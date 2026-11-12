"""이미 만든 DB에 Item Jira 칸을 더하고, 비어 있는 Jira를 원본 보관에서 한 번 채운다 (11/12).

앱이 DB 자료를 처음 읽을 때(:func:`setron.ui.database_source.dashboard_source_from_env`) 부른다.
- 칸 추가: ``item.jira_code``가 없으면 ``ALTER TABLE``로 맨 끝에 더한다. 새 DB는 스키마 SQL에 이미 있다.
- 한 번 채우기: Jira가 빈 Item만, 그 Item의 원본 행(변경 내용 칸의 행)에서 아이템 매니저와 같은 규칙으로 읽은 코드를
  넣는다. 이미 값이 있으면 건드리지 않고, 원본 행이 없거나 코드가 없으면 비워 둔다 → 여러 번 돌아도 결과가 같다.
- 실패해도 앱 시작을 막지 않는다(권한 없음 · 연결 문제). 그때 아이템 매니저는 지금처럼 원본 행을 읽어 보여 준다.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from setron.features.item.jira import join_codes

LOG = logging.getLogger(__name__)
COLUMN_SQL = ("ALTER TABLE item ADD COLUMN jira_code VARCHAR(200) NULL "
              "COMMENT 'Item별 Jira 코드(여러 개면 쉼표); 원본에 없으면 NULL'")


def has_jira_column(connection) -> bool:
    row = connection.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'item' AND COLUMN_NAME = 'jira_code'")).scalar()
    return bool(row)


def ensure_jira_column(engine) -> bool:
    """칸이 있으면 True. 없으면 더하고 True, 더하지 못하면 False."""
    try:
        with engine.begin() as connection:
            if has_jira_column(connection):
                return True
            connection.execute(text(COLUMN_SQL))
            LOG.info("item.jira_code 칸을 더했습니다.")
            return True
    except Exception as error:                      # noqa: BLE001 — 앱 시작을 막지 않는다
        LOG.warning("item.jira_code 칸을 더하지 못했습니다: %s", error)
        return False


def missing_jira(items, original_rows) -> dict[int, str]:
    """Jira가 빈 Item → 원본 행에서 읽은 코드. 아이템 매니저의 :func:`item_jira`와 같은 규칙."""
    from setron.ui.item_original import JIRA_FOUND, jira_by_item

    empty = [item["item_id"] for item in items if not (item.get("jira_code") or "").strip()]
    found = {}
    for item_id, jira in jira_by_item(original_rows, empty).items():
        if jira.status == JIRA_FOUND and (joined := join_codes(jira.codes)):
            found[item_id] = joined
    return found


def fill_missing_jira(storage) -> int:
    """비어 있는 Item Jira를 원본 보관에서 채운다. 채운 Item 수."""
    from setron.features.equipment.repository import EquipmentRepository
    from setron.ui.item_original import original_item_rows

    try:
        with storage.transaction() as tx:
            items = [dict(item) for item in EquipmentRepository().rows(tx, "item")]
            if not items or "jira_code" not in items[0]:
                return 0
            found = missing_jira(items, original_item_rows(tx, items))
            for item_id, codes in found.items():
                tx.update("item", {"item_id": item_id}, {"jira_code": codes})
        if found:
            LOG.info("Item Jira %d건을 원본 보관에서 채웠습니다.", len(found))
        return len(found)
    except Exception as error:                      # noqa: BLE001 — 앱 시작을 막지 않는다
        LOG.warning("Item Jira를 채우지 못했습니다: %s", error)
        return 0


def upgrade(storage, engine) -> int:
    """칸 추가 → 한 번 채우기. 채운 Item 수(칸을 못 더했으면 0)."""
    return fill_missing_jira(storage) if ensure_jira_column(engine) else 0
