"""SETRON SQL 자료의 비교와 확인된 변경의 원자적 저장."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.exc import InvalidRequestError

from setron.features.storage.repository import StorageRepository
from setron.features.storage.validation import TABLE_NAMES, StorageValidationError
from setron.infrastructure.database import Database
from setron.features.peer_sync import PeerSyncError

from .dump import read_dump
from .merge import MergePlan, SqlImportError, build_plan, fingerprint
from .validation import validate_merge


class SqlImportService:
    def __init__(self, database_factory: Callable[[], Database], *, peer_id: str | None = None):
        self._database_factory = database_factory
        self._peer_id = peer_id

    def preview(self, path: str | Path, mode: str) -> MergePlan:
        if mode not in {"add", "overwrite", "review"}:
            raise SqlImportError("SQL 가져오기 방식을 선택해 주세요.")
        source = read_dump(path)
        database = self._database_factory()
        try:
            with database.transaction(isolation_level=_isolation(database)) as session:
                repository, target, next_ids = _read_target(session)
                name = database.engine.url.database if database.engine.dialect.name == "mysql" else None
                return build_plan(source, target, next_ids, repository, mode, _destination(database), name)
        except (StorageValidationError, UnicodeError, PeerSyncError) as error:
            raise SqlImportError(str(error)) from None
        finally:
            database.dispose()

    def resolve(self, plan: MergePlan, choices: dict[str, str]) -> MergePlan:
        """파일을 다시 읽지 않고 검토한 원본과 최신 대상 지문으로 선택을 재검증한다."""
        if plan.mode != "review" or plan._source is None:
            raise SqlImportError("값 비교를 먼저 실행해 주세요.")
        if not isinstance(choices, dict) or any(value not in {"CURRENT", "INCOMING", "AUTO"} for value in choices.values()):
            raise SqlImportError("비교 항목의 적용 값을 선택해 주세요.")
        database = self._database_factory()
        try:
            if plan.destination != _destination(database):
                raise SqlImportError("대상 DB 설정이 바뀌었습니다. 변경 사항을 다시 확인해 주세요.")
            with database.transaction(isolation_level=_isolation(database)) as session:
                repository, target, next_ids = _read_target(session)
                if plan.revision != fingerprint(target, next_ids):
                    raise SqlImportError("대상 DB가 미리보기 이후 바뀌었습니다. 변경 사항을 다시 확인해 주세요.")
                name = database.engine.url.database if database.engine.dialect.name == "mysql" else None
                selected = dict(plan.choices)
                known = {difference.id for difference in plan.differences}
                if set(choices) - known:
                    raise SqlImportError("검토 목록에 없는 적용 선택입니다. 변경 사항을 다시 검토해 주세요.")
                for identifier, choice in choices.items():
                    if choice == "AUTO":
                        selected.pop(identifier, None)
                    else:
                        selected[identifier] = choice
                return build_plan(plan._source, target, next_ids, repository, "review", _destination(database), name,
                                  choices=selected)
        except (StorageValidationError, UnicodeError, PeerSyncError) as error:
            raise SqlImportError(str(error)) from None
        finally:
            database.dispose()

    def apply(self, plan: MergePlan) -> MergePlan:
        database = self._database_factory()
        try:
            if plan.destination != _destination(database):
                raise SqlImportError("대상 DB 설정이 바뀌었습니다. 변경 사항을 다시 확인해 주세요.")
            with database.transaction(isolation_level=_isolation(database)) as session:
                repository, target, next_ids = _read_target(session, lock=True)
                if plan.revision != fingerprint(target, next_ids):
                    raise SqlImportError("대상 DB가 미리보기 이후 바뀌었습니다. 변경 사항을 다시 확인해 주세요.")
                if plan.mode == "review":
                    if plan._source is None:
                        raise SqlImportError("값 비교를 다시 실행해 주세요.")
                    name = database.engine.url.database if database.engine.dialect.name == "mysql" else None
                    checked = build_plan(plan._source, target, next_ids, repository, "review", _destination(database), name,
                                         choices=plan.choices)
                    if checked.unresolved:
                        raise SqlImportError("선택하지 않은 비교 항목이 있습니다. 모든 항목의 적용 값을 선택해 주세요.")
                    if checked.writes != plan.writes or checked.differences != plan.differences:
                        raise SqlImportError("검토한 적용 계획이 바뀌었습니다. 선택 내용을 다시 검토해 주세요.")
                    plan = checked
                validate_merge(target, plan.writes, repository)
                from setron.features.peer_sync import read_state, reconcile
                sync = plan.mode == "review" or bool(plan._source and read_state(plan._source).entities)
                before_state = reconcile(session, repository, rows=target, peer_id=self._peer_id) if sync else None
                for write in plan.writes:
                    if write.action == "insert":
                        repository.insert(session, write.table, write.row)
                    elif write.action == "update":
                        repository.update(session, write.table, write.key, write.row)
                    else:
                        table = repository.table(write.table)
                        session.execute(table.delete().where(repository.key_filter(table, write.key)))
                if sync:
                    from setron.features.peer_sync import key_token
                    receipts = {entity["uuid"]: plan._receipts[entity["table"]][key_token(entity["key"])]
                                for entity in before_state.entities.values()
                                if entity["key"] is not None and entity["table"] in plan._receipts
                                and key_token(entity["key"]) in plan._receipts[entity["table"]]}
                    addresses = {(entity["table"], key_token(entity["key"])): entity["uuid"]
                                 for entity in before_state.entities.values() if entity["key"] is not None}
                    fields = {}
                    for difference in plan.differences:
                        identifier = addresses.get((difference.table, key_token(difference.key)))
                        if identifier is not None and difference.choice is not None:
                            fields.setdefault(identifier, {})[difference.id] = difference.choice
                    reconcile(session, repository, peer_id=self._peer_id, incoming_rows=plan._source,
                              id_maps=plan.id_maps, choices={"values": plan.choices, "entities": receipts, "fields": fields})
        except (StorageValidationError, PeerSyncError) as error:
            raise SqlImportError(str(error)) from None
        finally:
            database.dispose()
        return plan


def _isolation(database):
    # InnoDB의 전체 PK 범위 잠금은 미리보기 재검사 후 다른 연결의 INSERT도 막는다.
    return "REPEATABLE READ" if database.engine.dialect.name == "mysql" else None


def _destination(database):
    return database.engine.url.render_as_string(hide_password=True)


def _read_target(session, *, lock=False):
    connection = session.connection()
    try:
        repository = StorageRepository(connection)
    except InvalidRequestError as error:
        raise SqlImportError("대상 DB에 SETRON 관리 테이블을 먼저 설치해 주세요.") from error
    rows = {}
    for name in TABLE_NAMES:
        table = repository.table(name)
        statement = select(table).order_by(*table.primary_key.columns)
        if lock:
            statement = statement.with_for_update()
        rows[name] = tuple(dict(row) for row in session.execute(statement).mappings())
    next_ids = {}
    if connection.dialect.name == "mysql":
        # 삭제된 ID에 새 행을 할당하면 보관된 위험·현황 근거가 다른 행을 가리킬 수 있다.
        statement = text("SELECT TABLE_NAME, AUTO_INCREMENT FROM information_schema.TABLES "
                         "WHERE TABLE_SCHEMA = DATABASE() AND AUTO_INCREMENT IS NOT NULL")
        next_ids = {name: int(value) for name, value in session.execute(statement) if name in TABLE_NAMES}
    return repository, rows, next_ids
