"""Jira 저장 칸을 jira_code로 이관한다. 기존 값과 명시적 NULL은 원본으로 덮지 않는다.

DDL은 하나의 ALTER로 실행하며, 두 이름이 함께 있으면 모든 값이 NULL까지 같은지 먼저 검사한다.
Jira 칸이 전혀 없던 DB만 최초 추가 직전에 원본 후보를 읽어 한 번 채운다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import logging

from sqlalchemy import text

from setron.features.item.jira import join_codes
from setron.features.storage.validation import StorageValidationError

LOG = logging.getLogger(__name__)
COLUMN_SQL = ("ALTER TABLE item ADD COLUMN jira_code VARCHAR(200) NULL "
              "COMMENT 'Item별 Jira 코드(여러 개면 쉼표); 원본에 없으면 NULL'")


class JiraMigrationError(StorageValidationError):
    """값을 잃을 수 있어 자동 이관을 실행하지 않은 상태."""


@dataclass(frozen=True)
class _Migration:
    available: bool
    changed: bool = False
    added: bool = False


def _columns(connection):
    rows = connection.execute(text(
        "SELECT COLUMN_NAME AS name, CHARACTER_MAXIMUM_LENGTH AS max_length, "
        "CHARACTER_SET_NAME AS charset, COLLATION_NAME AS collation "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'item' AND COLUMN_NAME IN ('jira_code', 'jira_key')")).mappings()
    return {row["name"]: dict(row) for row in rows}


def has_jira_column(connection) -> bool:
    """새 canonical 칸이 있는가. 이전 jira_key만 있으면 이관 대상이다."""
    return "jira_code" in _columns(connection)


def _quote(value):
    return "`" + str(value).replace("`", "``") + "`"


def _definition(column):
    result = "VARCHAR(200)"
    if column.get("charset"):
        result += " CHARACTER SET " + _quote(column["charset"])
    if column.get("collation"):
        result += " COLLATE " + _quote(column["collation"])
    return result + " NULL DEFAULT NULL"


def _index_changes(connection, columns, *, rename, drop_key, widen):
    rows = connection.execute(text(
        "SELECT INDEX_NAME AS index_name, COLUMN_NAME AS column_name, NON_UNIQUE AS non_unique, "
        "SEQ_IN_INDEX AS sequence, SUB_PART AS sub_part, INDEX_TYPE AS index_type "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'item' ORDER BY INDEX_NAME, SEQ_IN_INDEX")).mappings()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["index_name"]].append(dict(row))
    drops, additions = [], []
    for name, parts in grouped.items():
        transformed, needs_rebuild = [], False
        for part in parts:
            old = part["column_name"]
            target = "jira_code" if old == "jira_key" and (rename or drop_key) else old
            prefix = part.get("sub_part")
            if old == "jira_key" and drop_key:
                needs_rebuild = True
            if target == "jira_code" and (widen or old == "jira_key" and drop_key):
                if prefix is None:
                    # 기존 VARCHAR50 full index는 50 prefix로 유지: utf8mb4/5.6의 767byte를 넘지 않는다.
                    prefix = min(int(columns.get(old, {}).get("max_length") or 191), 191)
                    needs_rebuild = True
            fragment = _quote(target) + ("(" + str(prefix) + ")" if prefix is not None else "")
            if target not in [column for column, _ in transformed]:
                transformed.append((target, fragment))
        if not needs_rebuild:
            continue
        if name == "PRIMARY" or any(part.get("index_type", "BTREE") != "BTREE" for part in parts):
            raise JiraMigrationError("Jira 칸의 사용자 정의 인덱스는 자동 이관할 수 없습니다. 스키마를 확인해 주세요.")
        drops.append("DROP INDEX " + _quote(name))
        kind = "UNIQUE " if not parts[0]["non_unique"] else ""
        additions.append("ADD " + kind + "INDEX " + _quote(name) + " (" +
                         ", ".join(fragment for _, fragment in transformed) + ")")
    return drops, additions


def _ensure(engine):
    try:
        with engine.begin() as connection:
            columns = _columns(connection)
            if not columns:
                connection.execute(text(COLUMN_SQL))
                return _Migration(True, True, True)
            both = "jira_code" in columns and "jira_key" in columns
            if both:
                conflicts = connection.execute(text(
                    "SELECT COUNT(*) FROM item WHERE NOT "
                    "(CAST(jira_code AS BINARY) <=> CAST(jira_key AS BINARY))")).scalar()
                if conflicts:
                    raise JiraMigrationError("jira_code와 jira_key 값이 다른 Item이 있어 이관하지 않았습니다. NULL을 포함한 값을 먼저 확인해 주세요.")
            rename = "jira_code" not in columns
            source = columns["jira_key" if rename else "jira_code"]
            length = source.get("max_length")
            widen = length is not None and int(length) < 200
            if not rename and not both and not widen:
                return _Migration(True)
            drops, additions = _index_changes(connection, columns, rename=rename, drop_key=both, widen=widen)
            changes = []
            if rename:
                changes.append("CHANGE COLUMN jira_key jira_code " + _definition(source) + " AFTER edited_fields")
            elif widen:
                changes.append("MODIFY COLUMN jira_code " + _definition(source))
            if both:
                changes.append("DROP COLUMN jira_key")
            connection.execute(text("ALTER TABLE item " + ", ".join((*drops, *changes, *additions))))
            return _Migration(True, True)
    except JiraMigrationError:
        raise
    except Exception:  # noqa: BLE001 — 권한이 없어도 기존 칸으로 읽을 수 있다. 접속 정보는 기록하지 않는다.
        LOG.warning("Item Jira 칸을 이관하지 못했습니다. DB 권한과 스키마를 확인해 주세요.")
        return _Migration(False)


def ensure_jira_column(engine) -> bool:
    """canonical 칸을 준비한다. 성공 DDL 뒤 기존 StorageService는 재반사가 필요하다."""
    return _ensure(engine).available


def missing_jira(items, original_rows) -> dict[int, str]:
    """Jira 칸이 전혀 없던 Item의 최초 원본 후보. 저장된 NULL/빈 문자열은 후보가 아니다."""
    from setron.features.item.original import JIRA_FOUND, jira_by_item

    ids = [item["item_id"] for item in items if "jira_code" not in item and "jira_key" not in item]
    found = {}
    for item_id, jira in jira_by_item(original_rows, ids).items():
        if jira.status == JIRA_FOUND and (joined := join_codes(jira.codes)):
            found[item_id] = joined
    return found


def _refresh(storage):
    from setron.features.storage.repository import StorageRepository

    with storage.database.transaction() as session:
        storage.repository = StorageRepository(session.connection())


def fill_missing_jira(storage, *, candidates=None) -> int:
    """최초 추가 때 확보한 후보만 채운다. 일반 재호출은 기존 NULL을 재생성하지 않는다."""
    if not candidates:
        return 0
    with storage.transaction() as tx:
        count = 0
        for item_id, codes in candidates.items():
            item = tx.get("item", {"item_id": item_id})
            if item.get("jira_code") is not None or any(
                    name in (item.get("edited_fields") or ()) for name in ("jira_code", "jira_key")):
                continue
            tx.update("item", {"item_id": item_id}, {"jira_code": codes})
            count += 1
    return count


def upgrade(storage, engine) -> int:
    """값/NULL 보존 이관 → reflection 갱신. Jira 칸이 없던 DB만 최초 원본 채우기."""
    from setron.features.equipment.repository import EquipmentRepository
    from setron.features.item.original import original_item_rows

    candidates = {}
    try:
        with engine.begin() as connection:
            existed = bool(_columns(connection))
        if not existed:
            with storage.transaction() as tx:
                items = [dict(item) for item in EquipmentRepository().rows(tx, "item")]
                legacy = [{name: value for name, value in item.items() if name not in {"jira_code", "jira_key"}}
                          for item in items]
                candidates = missing_jira(legacy, original_item_rows(tx, items))
    except ValueError as error:
        raise JiraMigrationError(str(error)) from error
    except Exception:
        LOG.warning("Item Jira 최초 이관 자료를 읽지 못했습니다. 기존 자료는 변경하지 않았습니다.")
        return 0
    state = _ensure(engine)
    if not state.available:
        return 0
    if state.changed:
        _refresh(storage)
    return fill_missing_jira(storage, candidates=candidates) if state.added else 0
