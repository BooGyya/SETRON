"""로컬 Windows DB에 시연 자료를 넣거나 테이블 구조를 유지하며 데이터를 지운다.

파일은 데이터로만 읽고 CREATE DATABASE/USE/DDL은 실행하지 않는다.
설정된 DB와 프로그램에 포함한 관리 스키마만 사용한다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from io import StringIO
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

import pymysql
from pymysql.converters import escape_item

from init_local_db import is_loopback
from demo_db_safety import (SafetyError, CommitOutcomeUnknown, assert_no_pending,
                            begin_operation, check_privileges, journal_path, recovery_message)
from setron.features.db_export.service import _header
from setron.features.db_import.dump import (DUMP_COLUMNS, DumpFormatError, _read_file,
    columns_from_definition, supported_columns, values_for_columns, with_legacy_jira)
from setron.features.storage.validation import TABLE_NAMES, decode_record, encode_record
from setron.infrastructure.database import DatabaseConfig, DatabaseConfigError
from setron.infrastructure.env_file import load_env_file

TITLE = "SETRON 시연 DB 준비"
LOCK_WAIT_SECONDS = 5
QUERY_WAIT_SECONDS = 30
HERE = Path(__file__).resolve().parent
DUMP = HERE / "dataset.dump"
SCHEMA = HERE / "schema_mysql5619.sql"
_ENDING = ("SET FOREIGN_KEY_CHECKS = @OLD_FOREIGN_KEY_CHECKS;\n"
           "SET SESSION SQL_MODE = @OLD_SQL_MODE;\n"
           "SET character_set_client = @OLD_CHARACTER_SET_CLIENT;\n"
           "SET character_set_results = @OLD_CHARACTER_SET_RESULTS;\n"
           "SET collation_connection = @OLD_COLLATION_CONNECTION;\n")


SetupError = SafetyError


@dataclass(frozen=True)
class SetupResult:
    applied: bool
    backup: Path | None = None
    warning: str = ""


def program_folder() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()


def dump_path(folder: Path) -> Path:
    """잘못된 외부 파일이 있어도 번들로 조용히 바꾸지 않는다."""
    external = folder / "SETRON_DB.sql"
    if external.exists():
        return external
    if DUMP.is_file():
        return DUMP
    source = HERE / "SETRON_DB.sql"
    return source if source.is_file() else HERE / "SETRON_시연_DB_초기화.sql"


def read_dataset(path: Path) -> dict[str, tuple[dict, ...]]:
    """엄격한 앱 파서와 저장 검증을 DB 변경 전에 실행한다."""
    sql = path.read_text(encoding="utf-8-sig")
    prefix, marker, body = sql.partition("-- SETRON DB 내보내기\n")
    if not marker:
        raise DumpFormatError("SETRON에서 내보낸 완전한 SQL 파일이 필요합니다.")
    # 예전 초기화 파일의 알려진 머리말은 읽되 실행하지 않는다.
    preamble = "\n".join(line for line in prefix.splitlines() if line.strip() and not line.startswith("--"))
    if preamble and not re.fullmatch(
            r"CREATE DATABASE IF NOT EXISTS setron\s+DEFAULT CHARACTER SET utf8mb4\s+"
            r"COLLATE utf8mb4_unicode_ci;\s*USE setron;", preamble):
        raise DumpFormatError("SQL 파일의 머리말에 지원하지 않는 명령이 있습니다.")
    rows = _read_file(StringIO(marker + body))
    for table, values in rows.items():
        seen = set()
        key = ("release_version_id", "item_id") if table == "release_version_item" else (DUMP_COLUMNS[table][0],)
        for row in values:
            encode_record(table, decode_record(table, row))
            identity = tuple(row[field] for field in key)
            if identity in seen or any(value is None for value in identity):
                raise SetupError(f"{table}의 기본 키가 없거나 중복되었습니다.")
            seen.add(identity)
    return rows


def schema_statements() -> tuple[str, ...]:
    """입력 파일의 DDL 대신 프로그램에 포함한 스키마만 사용한다."""
    sql = SCHEMA.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^CREATE TABLE ([a-z_]+) \(\n.*?^\) ENGINE=.*?;[ \t]*$", sql, re.M | re.S))
    if tuple(match[1] for match in matches) != TABLE_NAMES:
        raise SetupError("프로그램에 포함된 관리 스키마를 확인할 수 없습니다.")
    return tuple(match[0] for match in matches)


def _check_references(rows, statements):
    rows, _, _ = _projected_values(rows, statements)
    for table, statement in zip(TABLE_NAMES, statements, strict=True):
        for local, parent, remote in re.findall(
                r"FOREIGN KEY \(([a-z_]+)\)\s+REFERENCES ([a-z_]+) \(([a-z_]+)\)", statement):
            parent_values = {row[remote] for row in rows[parent]}
            if any(row[local] is not None and row[local] not in parent_values for row in rows[table]):
                raise SetupError(f"{table}.{local}이 SQL 파일에 없는 자료를 참조합니다.")


def _objects(cursor) -> dict[str, str]:
    cursor.execute("SHOW FULL TABLES")
    return dict(cursor.fetchall())


def _check_objects(cursor, config, objects):
    if objects and (set(objects) != set(TABLE_NAMES) or any(kind != "BASE TABLE" for kind in objects.values())):
        raise SetupError("대상 DB에 관리 테이블 13개 이외의 테이블·뷰가 있거나 관리 테이블이 일부만 있습니다. 아무것도 변경하지 않았습니다.")
    for catalog, schema_column in (("TRIGGERS", "TRIGGER_SCHEMA"), ("ROUTINES", "ROUTINE_SCHEMA"), ("EVENTS", "EVENT_SCHEMA")):
        cursor.execute(f"SELECT COUNT(*) FROM information_schema.{catalog} WHERE {schema_column} = %s", (config.database,))
        if cursor.fetchone()[0]:
            raise SetupError("대상 DB에 지원하지 않는 트리거·프로시저·이벤트가 있습니다. 아무것도 변경하지 않았습니다.")
    cursor.execute("SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE "
                   "WHERE REFERENCED_TABLE_SCHEMA IS NOT NULL AND "
                   "((TABLE_SCHEMA = %s AND REFERENCED_TABLE_SCHEMA <> %s) OR "
                   "(REFERENCED_TABLE_SCHEMA = %s AND TABLE_SCHEMA <> %s))", (config.database,) * 4)
    if cursor.fetchone()[0]:
        raise SetupError("다른 DB와 연결된 외래키가 있어 초기화를 중단했습니다. 아무것도 변경하지 않았습니다.")


def _literal(value):
    return "_binary X'" + bytes(value).hex() + "'" if isinstance(value, (bytes, bytearray)) else escape_item(value, "utf8mb4")


def _backup(connection, cursor, folder, config):
    """같은 연결의 일관된 스냅샷을 SQL로 보존하고 다시 읽어 검증한다."""
    directory = folder / "backups"
    directory.mkdir(exist_ok=True)
    name = re.sub(r"[^\w.-]", "_", config.database)
    path = directory / f"{name}_{datetime.now():%Y%m%d_%H%M%S_%f}_{uuid4().hex[:8]}.sql"
    definitions, originals = [], {}
    try:
        cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cursor.execute("SET SESSION SQL_MODE = 'NO_AUTO_VALUE_ON_ZERO'")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]
        counts = {}
        for table in TABLE_NAMES:
            cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
            counts[table] = cursor.fetchone()[0]
        with path.open("x", encoding="utf-8", newline="\n") as file:
            file.write(_header(datetime.now(), version, config.database, len(TABLE_NAMES), sum(counts.values())))
            for table in TABLE_NAMES:
                cursor.execute(f"SHOW CREATE TABLE `{table}`")
                create = cursor.fetchone()[1]
                definitions.append(create)
                file.write(f"\n-- 테이블 {table} · {counts[table]}행\n{create};\n")
                columns = columns_from_definition(table, create)
                quoted = ", ".join(f"`{field}`" for field in columns)
                cursor.execute(f"SELECT {quoted} FROM `{table}` ORDER BY 1")
                values = cursor.fetchall()
                originals[table] = tuple(dict(zip(columns, row, strict=True)) for row in values)
                for offset in range(0, len(values), 100):
                    file.write(f"INSERT INTO `{table}` ({quoted}) VALUES\n")
                    file.write(",\n".join("(" + ", ".join(_literal(value) for value in row) + ")"
                                          for row in values[offset:offset + 100]) + ";\n")
            file.write("\n" + _ENDING)
            file.flush()
            os.fsync(file.fileno())
        backed_up = read_dataset(path)
        normalized = {table: tuple({field: value.isoformat(sep=" ") if isinstance(value, datetime)
                                   else value.isoformat() if isinstance(value, date) else value
                                   for field, value in row.items()} for row in values)
                      for table, values in originals.items()}
        if backed_up != normalized:
            raise SetupError("백업 SQL의 값이 원본 DB와 일치하지 않습니다.")
        connection.commit()
        return path, backed_up, tuple(definitions)
    except (Exception, KeyboardInterrupt):
        try:
            connection.rollback()
        finally:
            path.unlink(missing_ok=True)
        raise


def _drop(cursor):
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    for table in reversed(TABLE_NAMES):
        cursor.execute(f"DROP TABLE IF EXISTS `{table}`")


def _projected_values(rows, definitions):
    columns_by_table = {table: columns_from_definition(table, definition)
                        for table, definition in zip(TABLE_NAMES, definitions, strict=True)}
    rows = with_legacy_jira(rows, columns_by_table["item"])
    # 대상 DDL과 데이터가 맞는지 DDL 실행 전에 검사한다. non-NULL Jira를 구형 구조에서 버리지 않는다.
    values = {table: [values_for_columns(table, row, columns_by_table[table]) for row in rows[table]] for table in TABLE_NAMES}
    for table, definition in zip(TABLE_NAMES, definitions, strict=True):
        if table != "item":
            continue
        limits = {field: int(length) for field, length in re.findall(
            r"(?:`)?(jira_key|jira_code)(?:`)?\s+VARCHAR\((\d+)\)", definition, re.I)}
        for projected in values[table]:
            for field, value in zip(columns_by_table[table], projected, strict=True):
                if field in limits and value is not None and (not isinstance(value, str) or len(value) > limits[field]):
                    raise SetupError(f"item.{field}의 값을 대상 열에 손실 없이 저장할 수 없습니다.")
    return rows, columns_by_table, values


def _commit(connection, *, guarded=False):
    try:
        connection.commit()
    except (pymysql.MySQLError, OSError, KeyboardInterrupt) as error:
        if guarded:
            raise CommitOutcomeUnknown("최종 저장 응답을 받지 못해 DB 반영 결과를 확인할 수 없습니다.") from error
        raise


def _apply(connection, cursor, rows, definitions, *, guarded_commit=False):
    rows, columns_by_table, values = _projected_values(rows, definitions)
    cursor.execute("SET SESSION SQL_MODE = 'STRICT_TRANS_TABLES,NO_AUTO_VALUE_ON_ZERO'")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    for definition in definitions:
        cursor.execute(definition)
    for table in TABLE_NAMES:
        columns = columns_by_table[table]
        sql = f"INSERT INTO `{table}` (" + ", ".join(f"`{field}`" for field in columns) + ") VALUES (" + ", ".join(["%s"] * len(columns)) + ")"
        for offset in range(0, len(rows[table]), 100):
            cursor.executemany(sql, values[table][offset:offset + 100])
    if set(_objects(cursor)) != set(TABLE_NAMES):
        raise SetupError("관리 테이블 13개를 모두 준비하지 못했습니다.")
    for table in TABLE_NAMES:
        cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
        if cursor.fetchone()[0] != len(rows[table]):
            raise SetupError(f"{table}의 적용 행 수가 SQL 파일과 다릅니다.")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
    _commit(connection, guarded=guarded_commit)


def _connect(config):
    return pymysql.connect(host=config.host, port=config.port, user=config.username,
                           password=config.password, charset="utf8mb4", connect_timeout=config.connect_timeout,
                           read_timeout=QUERY_WAIT_SECONDS, write_timeout=QUERY_WAIT_SECONDS)


def _start_session(cursor):
    cursor.execute("SELECT @@version_compile_os")
    if not cursor.fetchone()[0].lower().startswith("win"):
        raise SetupError("Windows에 설치된 MySQL에 접속해 주세요. 설정 파일의 포트를 확인하세요.")
    cursor.execute("SET SESSION lock_wait_timeout = %s", (QUERY_WAIT_SECONDS,))
    cursor.execute("SET SESSION innodb_lock_wait_timeout = %s", (QUERY_WAIT_SECONDS,))


def _progress(callback, text):
    if callback is not None:
        try:
            callback(text)
        except (OSError, RuntimeError):
            pass


def _release_lock(cursor, lock_name):
    try:
        cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
    except (pymysql.MySQLError, KeyboardInterrupt):
        pass


def _unknown_result(operation, backup, error):
    operation.uncertain()
    suffix = f"\n백업: {backup}" if backup else ""
    return SetupError("최종 저장 결과를 확인할 수 없습니다. 이미 반영되었을 수 있으므로 다시 실행하거나 자동 복원하지 않습니다.\n"
                      "연결을 복구한 뒤 관리자에게 대상 DB 확인을 요청하세요."
                      f"{suffix}\n작업 기록: {operation.path}")


def prepare(config: DatabaseConfig, *, confirm: Callable[[str], bool] | None = None,
            folder: Path | None = None, source: Path | None = None,
            progress: Callable[[str], None] | None = None) -> SetupResult:
    """검증·권한 확인·백업·작업 기록을 마친 뒤에만 테이블을 교체한다."""
    folder = Path(folder) if folder is not None else program_folder()
    if not is_loopback(config.host):
        raise SetupError("이 도구는 이 PC에 설치된 Windows MySQL에서만 시연 DB를 준비합니다.")
    assert_no_pending(folder, config)
    rows = read_dataset(source or dump_path(folder))
    definitions = schema_statements()
    _check_references(rows, definitions)
    lock_name = "setron-init:" + sha256(config.database.casefold().encode()).hexdigest()[:32]
    _progress(progress, "DB 연결·권한을 확인하는 중…")
    with _connect(config) as connection, connection.cursor() as cursor:
        _start_session(cursor)
        cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, LOCK_WAIT_SECONDS))
        if cursor.fetchone() != (1,):
            raise SetupError("시연 DB 준비 작업이 이미 실행 중입니다. 잠시 후 다시 시도해 주세요.")
        operation = None
        try:
            # 잠금을 기다리는 동안 다른 실행이 중단됐을 수도 있어 다시 확인한다.
            assert_no_pending(folder, config)
            check_privileges(cursor, config.database, "import", TABLE_NAMES)
            try:
                connection.select_db(config.database)
            except pymysql.err.OperationalError as error:
                if error.args[0] != 1049:
                    raise
                check_privileges(cursor, config.database, "import", TABLE_NAMES, create_database=True)
                operation = begin_operation(folder, config, "import", None)
                identifier = "`" + config.database.replace("`", "``") + "`"
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {identifier} DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                connection.select_db(config.database)
            objects = _objects(cursor)
            _check_objects(cursor, config, objects)
            backup = None
            old_rows, old_definitions = None, None
            if objects:
                message = ("[시연 데이터 삽입]\n"
                           f"대상: {config.host}:{config.port} / DB: {config.database}\n"
                           "관리 테이블 13개의 데이터를 모두 교체합니다.\n"
                           "앱과 다른 DB 도구의 저장 작업을 먼저 종료하세요.\n"
                           f"백업 폴더: {folder / 'backups'}\n\n"
                           "기존 데이터를 덮어쓰시겠습니까?")
                if confirm is None or not confirm(message):
                    return SetupResult(False)
                _progress(progress, "기존 데이터를 백업·검증하는 중…")
                try:
                    backup, old_rows, old_definitions = _backup(connection, cursor, folder, config)
                except (Exception, KeyboardInterrupt) as error:
                    raise SetupError("기존 DB의 SQL 백업·검증에 실패하거나 중단됐습니다. 기존 테이블과 데이터를 변경하지 않았습니다.") from error
            operation = operation or begin_operation(folder, config, "import", backup)
            _progress(progress, "시연 데이터를 적용하는 중… 창을 닫지 마세요.")
            try:
                if objects:
                    _drop(cursor)
                _apply(connection, cursor, rows, definitions, guarded_commit=True)
            except CommitOutcomeUnknown as error:
                raise _unknown_result(operation, backup, error) from error
            except (Exception, KeyboardInterrupt) as error:
                try:
                    connection.rollback()
                    _drop(cursor)
                    if old_rows is not None:
                        _apply(connection, cursor, old_rows, old_definitions, guarded_commit=True)
                    else:
                        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
                    restored = "기존 데이터를 자동 복원했습니다." if backup else "이번에 만든 관리 테이블을 정리했습니다."
                    warning = operation.complete()
                    if warning:
                        restored += "\n" + warning
                except (Exception, KeyboardInterrupt):
                    operation.uncertain()
                    restored = ("자동 복원을 완료하지 못했습니다. 백업 SQL로 복원해 주세요." if backup
                                else "관리 테이블 준비 상태를 확인해 주세요.")
                    restored += f"\n작업 기록: {operation.path}"
                suffix = f"\n백업: {backup}" if backup else ""
                action = "시연 데이터 적용이 중단되었습니다." if isinstance(error, KeyboardInterrupt) else "시연 데이터 적용에 실패했습니다."
                raise SetupError(f"{action} {restored}{suffix}") from error
            return SetupResult(True, backup, operation.complete())
        except (pymysql.MySQLError, OSError, KeyboardInterrupt) as error:
            if operation is not None:
                operation.uncertain()
                raise SetupError("DB 준비 중 연결·파일 오류 또는 중단이 발생했습니다. DB 상태를 확인해 주세요.\n"
                                 f"작업 기록: {operation.path}") from error
            raise
        finally:
            _release_lock(cursor, lock_name)


def _check_reset_tables(cursor, config):
    """DELETE 전체 취소가 가능한 InnoDB 관리 테이블과 지원 컬럼만 허용한다."""
    cursor.execute("SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s", (config.database,))
    engines = dict(cursor.fetchall())
    if set(engines) != set(TABLE_NAMES) or any(str(engine).upper() != "INNODB" for engine in engines.values()):
        raise SetupError("데이터 초기화는 관리 테이블 13개가 모두 InnoDB인 경우에만 가능합니다. 아무것도 변경하지 않았습니다.")
    for table in TABLE_NAMES:
        cursor.execute(f"SHOW COLUMNS FROM `{table}`")
        if not supported_columns(table, tuple(row[0] for row in cursor.fetchall())):
            raise SetupError(f"{table}의 구조가 지원하는 관리 스키마와 다릅니다. 아무것도 변경하지 않았습니다.")


def _reset_rows(connection, cursor, *, guarded_commit=False):
    """테이블은 그대로 두고 명시적 단일 트랜잭션으로 모든 행을 삭제한다."""
    cursor.execute("SELECT @@SESSION.FOREIGN_KEY_CHECKS")
    foreign_key_checks = cursor.fetchone()[0]
    if foreign_key_checks not in (0, 1):
        raise SetupError("외래키 검사 설정을 확인할 수 없어 데이터 초기화를 중단했습니다.")
    # 백업이 자체 트랜잭션을 commit했으므로 여기서 삭제 트랜잭션을 새로 연다.
    connection.begin()
    try:
        try:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in reversed(TABLE_NAMES):
                cursor.execute(f"DELETE FROM `{table}`")
            if set(_objects(cursor)) != set(TABLE_NAMES):
                raise SetupError("데이터 초기화 중 관리 테이블 상태가 바뀌었습니다.")
            for table in TABLE_NAMES:
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                if cursor.fetchone()[0] != 0:
                    raise SetupError(f"{table}에 데이터가 남아 있어 이번 초기화를 취소합니다.")
        finally:
            # 세션 설정 복구까지 성공해야 삭제 내용을 commit한다.
            cursor.execute("SET FOREIGN_KEY_CHECKS = %s", (foreign_key_checks,))
        _commit(connection, guarded=guarded_commit)
    except CommitOutcomeUnknown:
        raise
    except (Exception, KeyboardInterrupt):
        try:
            connection.rollback()
        except (Exception, KeyboardInterrupt) as error:
            if guarded_commit:
                raise CommitOutcomeUnknown("데이터 초기화 취소 결과를 확인할 수 없습니다.") from error
            raise
        raise


def reset_data(config: DatabaseConfig, *, confirm: Callable[[str], bool] | None = None,
               folder: Path | None = None, progress: Callable[[str], None] | None = None) -> SetupResult:
    """덤프 없이 관리 테이블을 검사하고 확인·백업을 마친 뒤 데이터만 지운다."""
    folder = Path(folder) if folder is not None else program_folder()
    if not is_loopback(config.host):
        raise SetupError("이 도구는 이 PC에 설치된 Windows MySQL에서만 데이터를 초기화합니다.")
    assert_no_pending(folder, config)
    schema_statements()  # 신뢰하는 관리 테이블 목록과 번들 스키마 상태를 확인한다.
    lock_name = "setron-init:" + sha256(config.database.casefold().encode()).hexdigest()[:32]
    _progress(progress, "DB 연결·권한을 확인하는 중…")
    with _connect(config) as connection, connection.cursor() as cursor:
        _start_session(cursor)
        cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, LOCK_WAIT_SECONDS))
        if cursor.fetchone() != (1,):
            raise SetupError("DB 준비 또는 초기화 작업이 이미 실행 중입니다. 잠시 후 다시 시도해 주세요.")
        try:
            assert_no_pending(folder, config)
            check_privileges(cursor, config.database, "reset", TABLE_NAMES)
            try:
                connection.select_db(config.database)
            except pymysql.err.OperationalError as error:
                if error.args[0] != 1049:
                    raise
                raise SetupError("설정된 DB가 없어 데이터 초기화를 중단했습니다. DB나 테이블을 새로 만들지 않았습니다.") from error
            objects = _objects(cursor)
            _check_objects(cursor, config, objects)
            if set(objects) != set(TABLE_NAMES):
                raise SetupError("관리 테이블 13개가 없어 데이터 초기화를 중단했습니다. 아무것도 변경하지 않았습니다.")
            _check_reset_tables(cursor, config)
            message = ("[데이터 초기화]\n"
                       f"대상: {config.host}:{config.port} / DB: {config.database}\n"
                       "관리 테이블 13개의 모든 데이터를 삭제합니다.\n"
                       "테이블 구조는 유지합니다.\n"
                       "앱과 다른 DB 도구의 저장 작업을 먼저 종료하세요.\n"
                       f"백업 폴더: {folder / 'backups'}\n\n"
                       "모든 데이터를 삭제하시겠습니까?")
            if confirm is None or not confirm(message):
                return SetupResult(False)
            _progress(progress, "기존 데이터를 백업·검증하는 중…")
            try:
                backup, _, _ = _backup(connection, cursor, folder, config)
            except (Exception, KeyboardInterrupt) as error:
                raise SetupError("기존 DB의 SQL 백업·검증에 실패하거나 중단됐습니다. 기존 테이블과 데이터를 변경하지 않았습니다.") from error
            operation = begin_operation(folder, config, "reset", backup)
            _progress(progress, "데이터를 초기화하는 중… 창을 닫지 마세요.")
            try:
                _reset_rows(connection, cursor, guarded_commit=True)
            except CommitOutcomeUnknown as error:
                raise _unknown_result(operation, backup, error) from error
            except (Exception, KeyboardInterrupt) as error:
                warning = operation.complete()
                suffix = f"\n{warning}" if warning else ""
                raise SetupError("데이터 초기화에 실패했습니다. 삭제 트랜잭션의 취소를 요청했습니다.\n"
                                 f"기존 데이터 백업: {backup}{suffix}") from error
            return SetupResult(True, backup, operation.complete())
        finally:
            _release_lock(cursor, lock_name)


def load_config(folder: Path | None = None) -> DatabaseConfig:
    path = (folder or program_folder()) / "setron.env"
    if not path.is_file():
        raise SetupError("프로그램 폴더의 setron.env를 찾지 못했습니다.\nSETRON.exe 및 setron.env와 같은 폴더에서 실행해 주세요.")
    values = os.environ.copy()
    load_env_file(path, values)
    return DatabaseConfig.from_env(values)


def _permission_guidance(config: DatabaseConfig | None, mode: str) -> str:
    """계정 권한은 자동 변경하지 않고 해당 DB에 한정한 확인·설정 방법을 안내한다."""
    target = (f"대상: {config.host}:{config.port} / DB: {config.database} / 계정: {config.username}"
              if config is not None else "설정: 실행 파일 옆 setron.env")
    permissions = "SELECT, DELETE, TRIGGER" if mode == "reset" else "SELECT, CREATE, DROP, INSERT, REFERENCES, TRIGGER"
    return ("\n\n[계정·권한 확인]\n"
            f"{target}\n"
            "setron.env의 계정·비밀번호·포트를 확인하세요.\n"
            "비밀번호 앞뒤에 공백이 있으면 전체를 따옴표로 감싸세요.\n"
            "새 MySQL에서는 계정을 먼저 생성하세요.\n"
            "관리자 확인: SHOW GRANTS FOR '계정명'@'접속호스트';\n"
            f"필요 권한: {permissions}\n"
            "부족한 권한은 대상 DB에만 GRANT로 설정하세요.")


def _mysql_failure(error: BaseException) -> str:
    """감싼 DB 예외에서도 코드만 읽는다. 원문 SQL·비밀번호가 있는 오류 원문은 출력하지 않는다."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, pymysql.MySQLError):
            code = error.args[0] if error.args and isinstance(error.args[0], int) else None
            reason = {
                1045: "MySQL 로그인에 실패했습니다. 계정·비밀번호와 접속 호스트를 확인하세요.",
                1044: "지정한 DB에 접근할 권한이 없습니다. 대상 DB의 계정 권한을 확인하세요.",
                1142: "이 작업의 MySQL 명령 실행 권한이 부족합니다.",
                1143: "이 작업의 MySQL 컬럼 접근 권한이 부족합니다.",
                1227: "이 작업에 필요한 MySQL 권한이 부족합니다.",
                1205: "다른 작업의 잠금 대기 시간이 초과됐습니다. 앱과 다른 DB 도구의 작업을 종료한 뒤 다시 확인하세요.",
                1213: "다른 DB 작업과 충돌했습니다. 표시된 백업·복원 결과를 확인하세요.",
                1049: "지정한 DB가 없습니다. setron.env의 DB 이름과 최초 DB 준비 상태를 확인하세요.",
                2002: "이 PC의 MySQL에 연결할 수 없습니다. MySQL 서비스와 포트를 확인하세요.",
                2003: "이 PC의 MySQL에 연결할 수 없습니다. MySQL 서비스와 포트를 확인하세요.",
                2006: "MySQL 연결이 끊겼습니다. 서버 상태와 표시된 백업·복원 결과를 확인하세요.",
                2013: "MySQL 응답 도중 연결이 끊겼습니다. 서버 상태와 표시된 백업·복원 결과를 확인하세요.",
            }.get(code, "MySQL 작업에 실패했습니다. 서버 상태와 계정 권한을 확인하세요.")
            return f"MySQL 오류 코드: {code if code is not None else '확인 불가'}\n{reason}"
        error = error.__cause__ if error.__cause__ is not None else error.__context__
    return ""


def run(*, confirm: Callable[[str], bool] | None = None, mode: str = "import",
        progress: Callable[[str], None] | None = None) -> tuple[int, str]:
    config = None

    def failed(message: str, error: BaseException) -> tuple[int, str]:
        detail = _mysql_failure(error)
        return 1, message + ("\n" + detail if detail else "") + _permission_guidance(config, mode)

    try:
        if mode not in {"import", "reset"}:
            return 2, "지원하지 않는 DB 작업입니다."
        config = load_config()
        _progress(progress, f"대상: {config.host}:{config.port} / DB: {config.database} / 계정: {config.username}")
        overridden = [key.removeprefix("SETRON_DB_") for key in os.environ if key.startswith("SETRON_DB_")]
        if overridden:
            _progress(progress, "Windows 환경변수 우선 적용: " + ", ".join(sorted(overridden)))
        _progress(progress, "앱과 다른 DB 도구의 저장 작업을 종료한 상태에서 진행하세요.")
        options = {"confirm": confirm}
        if progress is not None:
            options["progress"] = progress
        result = (reset_data if mode == "reset" else prepare)(config, **options)
        if not result.applied:
            cancelled = "데이터 초기화를 취소했습니다." if mode == "reset" else "시연 데이터 삽입을 취소했습니다."
            return 3, cancelled + "\nDB를 변경하지 않았습니다."
        suffix = f"\n백업: {result.backup}" if result.backup else ""
        if result.warning:
            suffix += "\n" + result.warning
    except (SetupError, DumpFormatError) as error:
        return failed(str(error), error)
    except DatabaseConfigError as error:
        return failed(f"DB 연결 설정을 확인할 수 없습니다.\n{error}\nsetron.env를 확인해 주세요.", error)
    except KeyboardInterrupt as error:
        return failed("사용자가 작업을 중단했습니다. 다시 실행하여 미완료 작업 기록과 DB 상태를 확인하세요.", error)
    except (OSError, UnicodeError, ValueError) as error:
        return failed("DB 설정·SQL 데이터·관리 스키마를 읽거나 검증하지 못했습니다. 기존 데이터를 삭제하지 않았습니다.", error)
    except pymysql.MySQLError as error:
        action = "데이터 초기화" if mode == "reset" else "시연 데이터 삽입"
        return failed(f"{action} 작업을 중단했습니다.", error)
    if mode == "reset":
        return 0, f"데이터 초기화 완료\n검증: 테이블 13개 유지 · 모든 데이터 0행{suffix}"
    return 0, f"시연 데이터 삽입 완료\n검증: 테이블 13개 · SQL 행 수 일치{suffix}"


def _configure_console_output():
    """동결 EXE도 배치의 UTF-8 코드 페이지와 같은 인코딩으로 안내한다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                pass


def _discard_pending_console_input():
    """choice 메뉴에서 남은 Enter가 확인 질문을 자동 취소하지 않도록 비운다."""
    if os.name == "nt" and sys.stdin is not None and sys.stdin.isatty():
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getwch()


def _console_confirm(message: str) -> bool:
    _discard_pending_console_input()
    print(message, flush=True)
    try:
        answer = input("진행: Y / 취소: Enter·N [기본 N]: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().casefold() in {"y", "yes", "예"}


def _console_menu() -> int:
    print("SETRON DB 관리 · Windows MySQL")
    print("설정: 실행 파일 옆 setron.env\n")
    print("1. 시연 데이터 넣기")
    print("2. 데이터 초기화 (모든 데이터 삭제 · 테이블 유지)")
    print("3. 종료\n")
    while True:
        try:
            choice = input("선택 [1/2/3] 후 Enter: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            choice = "3"
        if choice == "3":
            print("종료: DB를 변경하지 않았습니다.")
            return 0
        if choice in {"1", "2"}:
            break
        print("1, 2, 3 중 하나를 입력하세요.")
    mode = "import" if choice == "1" else "reset"
    code, message = run(confirm=_console_confirm, mode=mode, progress=lambda text: print(text, flush=True))
    print(message)
    try:
        input("Enter 키를 누르면 창을 닫습니다: ")
    except (EOFError, KeyboardInterrupt):
        print()
    return code


def main() -> int:
    _configure_console_output()
    options = sys.argv[1:]
    allowed = {"--import-demo", "--reset-data", "--quiet", "--console", "--validate-dump"}
    actions = {"--import-demo", "--reset-data", "--validate-dump"}.intersection(options)
    if (any(option not in allowed for option in options) or len(options) != len(set(options)) or len(actions) > 1
            or {"--console", "--quiet"}.issubset(options)):
        print("--import-demo, --reset-data, --validate-dump 중 하나를 선택하세요. --console 또는 --quiet를 함께 사용할 수 있습니다.")
        return 2
    if "--validate-dump" in options:
        try:
            path = dump_path(program_folder())
            rows = read_dataset(path)
            _check_references(rows, schema_statements())
        except (OSError, UnicodeError, ValueError):
            print("SQL 파일의 형식·행·참조를 검증하지 못했습니다. DB에는 접속하지 않았습니다.")
            return 1
        print(f"SQL 검증 완료: {path}\n관리 테이블 {len(rows)}개 · 설비 {len(rows['equipment'])}대 · "
              f"관측 {len(rows['equipment_snapshot'])}개 · 전체 {sum(map(len, rows.values()))}행\nDB에는 접속하지 않았습니다.")
        return 0
    if not actions and "--quiet" not in options:
        return _console_menu()
    mode = "reset" if "--reset-data" in options else "import"
    if "--quiet" in options:
        code, message = run(mode=mode)  # callback이 없으므로 기존 DB를 덮어쓰거나 비우지 않는다.
        print(message)
        return code
    if "--console" in options:
        code, message = run(confirm=_console_confirm, mode=mode, progress=lambda text: print(text, flush=True))
        print(message)
        return code
    import tkinter as tk
    from tkinter import messagebox

    window = tk.Tk()
    title = "SETRON 데이터 초기화" if mode == "reset" else TITLE
    window.title(title)
    window.resizable(False, False)
    tk.Label(window, text="데이터 초기화를 준비하는 중입니다…" if mode == "reset" else "시연 DB를 준비하는 중입니다…", padx=32, pady=24).pack()
    window.update()

    def confirm(message):
        return messagebox.askyesno(title, message, default=messagebox.NO, icon=messagebox.WARNING, parent=window)

    code, message = run(confirm=confirm, mode=mode)
    window.withdraw()
    (messagebox.showinfo if code in (0, 3) else messagebox.showerror)(title, message, parent=window)
    window.destroy()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
