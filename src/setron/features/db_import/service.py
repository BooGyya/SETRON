"""SETRON SQL 자료의 비교와 확인된 변경의 원자적 저장."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.exc import InvalidRequestError

from setron.features.storage.repository import StorageRepository
from setron.features.storage.validation import TABLE_NAMES, StorageValidationError
from setron.infrastructure.database import Database

from .dump import read_dump
from .merge import MergePlan, SqlImportError, build_plan, fingerprint
from .validation import validate_merge


class SqlImportService:
    def __init__(self, database_factory: Callable[[], Database]):
        self._database_factory = database_factory

    def preview(self, path: str | Path, mode: str) -> MergePlan:
        if mode not in {"add", "overwrite"}:
            raise SqlImportError("SQL 가져오기 방식을 선택해 주세요.")
        source = read_dump(path)
        database = self._database_factory()
        try:
            with database.transaction(isolation_level=_isolation(database)) as session:
                repository, target, next_ids = _read_target(session)
                name = database.engine.url.database if database.engine.dialect.name == "mysql" else None
                return build_plan(source, target, next_ids, repository, mode, _destination(database), name)
        except (StorageValidationError, UnicodeError) as error:
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
                validate_merge(target, plan.writes, repository)
                for write in plan.writes:
                    if write.action == "insert":
                        repository.insert(session, write.table, write.row)
                    elif write.action == "update":
                        repository.update(session, write.table, write.key, write.row)
                    else:
                        table = repository.table(write.table)
                        session.execute(table.delete().where(repository.key_filter(table, write.key)))
        except StorageValidationError as error:
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
