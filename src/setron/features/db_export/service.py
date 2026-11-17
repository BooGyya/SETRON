"""관리 DB 전체를 SQL 파일로 내보낸다 (BE-023, FUN-063).

관리 테이블의 구조와 누적 자료를 같은 InnoDB 스냅샷에서 읽는다. 빈 DB에서
직접 실행하거나 SETRON의 SQL 가져오기에서 읽을 수 있는 파일을 만든다.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TextIO

from pymysql.converters import escape_item
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from setron.features.export.schemas import ExportResult, ExportStatus
from setron.features.peer_sync import PeerSyncError, reconcile
from setron.features.storage.repository import StorageRepository
from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import Database, DatabaseConnectionError, DatabaseError

ROWS_PER_INSERT = 200
MAX_INSERT_BYTES = 1024 * 1024
SQL_MODE = "NO_AUTO_VALUE_ON_ZERO"


def export_database(database: Database, path: str | Path, *, now: Callable[[], datetime] = datetime.now,
                    peer_id: str | None = None) -> ExportResult:
    """관리 테이블 전체를 ``path``(.sql)에 저장하고 성공 또는 실패 원인을 돌려준다.

    고유한 임시파일을 같은 폴더에 작성한 뒤 교체하므로, 실패하면 기존 SQL 파일을
    보존한다. 폴더 선택과 기존 파일의 덮어쓰기 확인은 호출하는 화면에서 처리한다.
    """
    path = Path(path)
    if path.suffix.lower() != ".sql":
        return ExportResult(ExportStatus.FAILED, path, "파일 이름은 .sql로 끝나야 합니다.")
    temp = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                                prefix=f".{path.name}.", suffix=".part", delete=False) as file:
            temp = Path(file.name)
            with _snapshot(database, peer_id=peer_id) as connection:
                version = connection.exec_driver_sql("SELECT VERSION()").scalar()
                name = connection.exec_driver_sql("SELECT DATABASE()").scalar()
                counts = {table: connection.exec_driver_sql(f"SELECT COUNT(*) FROM `{table}` FOR UPDATE").scalar()
                          for table in TABLE_NAMES}
                total = sum(counts.values())
                file.write(_header(now(), version, name, len(counts), total))
                for table, count in counts.items():
                    create = connection.exec_driver_sql(f"SHOW CREATE TABLE `{table}`").one()[1]
                    file.write(f"\n-- 테이블 {table} · {count}행\n{create};\n")
                    _write_rows(connection, file, table)
                file.write("\nSET FOREIGN_KEY_CHECKS = @OLD_FOREIGN_KEY_CHECKS;\n"
                           "SET SESSION SQL_MODE = @OLD_SQL_MODE;\n"
                           "SET character_set_client = @OLD_CHARACTER_SET_CLIENT;\n"
                           "SET character_set_results = @OLD_CHARACTER_SET_RESULTS;\n"
                           "SET collation_connection = @OLD_COLLATION_CONNECTION;\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    except PermissionError:
        return ExportResult(ExportStatus.FAILED, path,
                            "파일을 저장할 수 없습니다. 같은 이름의 파일이 열려 있는지, 폴더 쓰기 권한이 있는지 확인해 주세요.")
    except (DatabaseError, SQLAlchemyError, PeerSyncError) as error:
        # SQLAlchemy의 원래 예외에는 쿼리와 연결 정보가 들어갈 수 있어 직접 표시하지 않는다.
        detail = str(error) if isinstance(error, (DatabaseError, PeerSyncError)) else "MySQL 연결 상태와 테이블 조회 권한을 확인해 주세요."
        return ExportResult(ExportStatus.FAILED, path, f"DB 자료를 읽지 못했습니다: {detail}")
    except OSError as error:
        return ExportResult(ExportStatus.FAILED, path, f"파일을 저장하지 못했습니다: {error.strerror or error}")
    finally:
        if temp is not None:
            _remove(temp)
    return ExportResult(ExportStatus.SAVED, path, f"테이블 {len(counts)}개 · {total}행")


@contextmanager
def _snapshot(database: Database, *, peer_id=None) -> Iterator[Connection]:
    """서버 기본 격리 수준과 무관한 동일 시점의 자료를 읽고 연결 설정을 복구한다."""
    try:
        connection = database.engine.connect()
    except RuntimeError as error:
        # 인증 라이브러리 오류도 공통 DB 연결 경로와 같은 실패 결과로 전달한다.
        raise DatabaseConnectionError("DB에 연결하지 못했습니다. MySQL 실행 상태와 접속 설정을 확인하세요.") from error
    with connection:
        connection = connection.execution_options(isolation_level="REPEATABLE READ")
        mode = None
        try:
            with connection.begin():
                mode = connection.exec_driver_sql("SELECT @@SESSION.sql_mode").scalar()
                # SHOW CREATE TABLE의 따옴표·주석도 파일의 SQL 모드와 같은 규칙으로 생성한다.
                connection.exec_driver_sql(f"SET SESSION sql_mode = '{SQL_MODE}'")
                connection.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT")
                # 같은 트랜잭션에서 업무 행을 잠그고 교환 이력과 함께 내보낸다.
                # export의 SELECT도 locking read여야 앞선 consistent snapshot과 섞이지 않는다.
                repository = StorageRepository(connection)
                reconcile(connection, repository, peer_id=peer_id)
                yield connection
        finally:
            if mode is not None and not connection.invalidated:
                connection.exec_driver_sql("SET SESSION sql_mode = %s", (mode,))


def _write_rows(connection: Connection, file: TextIO, table: str) -> None:
    """서버 커서와 크기를 제한한 INSERT로 누적 자료의 메모리 사용을 제한한다."""
    result = connection.exec_driver_sql(f"SELECT * FROM `{table}` ORDER BY 1 FOR UPDATE",
                                        execution_options={"stream_results": True})
    try:
        columns = ", ".join(f"`{column}`" for column in result.keys())
        prefix = f"INSERT INTO `{table}` ({columns}) VALUES\n"
        prefix_size = len(prefix.encode("utf-8")) + 2
        values = []
        size = prefix_size
        while rows := result.fetchmany(ROWS_PER_INSERT):
            for row in rows:
                value = "(" + ", ".join(escape_item(v, "utf8mb4") for v in row) + ")"
                value_size = len(value.encode("utf-8")) + 2
                if values and (len(values) >= ROWS_PER_INSERT or size + value_size > MAX_INSERT_BYTES):
                    file.write(prefix + ",\n".join(values) + ";\n")
                    values = []
                    size = prefix_size
                values.append(value)
                size += value_size
        if values:
            file.write(prefix + ",\n".join(values) + ";\n")
    finally:
        result.close()


def _header(created: datetime, version: str, name: str, tables: int, rows: int) -> str:
    # 서버에서 받은 이름이 주석의 다음 줄에 SQL을 만들지 않도록 한 줄로 제한한다.
    name, version = " ".join(name.splitlines()), " ".join(version.splitlines())
    return (
        "-- SETRON DB 내보내기\n"
        f"-- 생성 {created:%Y-%m-%d %H:%M:%S} · 원본 DB {name} · MySQL {version}\n"
        f"-- 테이블 {tables}개 · {rows}행\n"
        "--\n"
        "-- 빈 DB에서 실행하세요. 기존 테이블을 삭제하거나 자료를 합치지 않습니다.\n"
        "-- 실행 중에만 외래키 검사와 SQL·문자셋 설정을 변경하고 끝에서 복구합니다.\n\n"
        "SET @OLD_CHARACTER_SET_CLIENT = @@character_set_client;\n"
        "SET @OLD_CHARACTER_SET_RESULTS = @@character_set_results;\n"
        "SET @OLD_COLLATION_CONNECTION = @@collation_connection;\n"
        "SET NAMES utf8mb4;\n"
        "SET @OLD_SQL_MODE = @@SESSION.SQL_MODE;\n"
        f"SET SESSION SQL_MODE = '{SQL_MODE}';\n"
        "SET @OLD_FOREIGN_KEY_CHECKS = @@FOREIGN_KEY_CHECKS;\n"
        "SET FOREIGN_KEY_CHECKS = 0;\n"
    )


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
