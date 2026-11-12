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
from setron.features.db_export.service import _header
from setron.features.db_import.dump import (DUMP_COLUMNS, DumpFormatError, _read_file,
    columns_from_definition, supported_columns, values_for_columns, with_legacy_jira)
from setron.features.storage.validation import TABLE_NAMES, decode_record, encode_record
from setron.infrastructure.database import DatabaseConfig, DatabaseConfigError
from setron.infrastructure.env_file import load_env_file

TITLE = "SETRON 시연 DB 준비"
HERE = Path(__file__).resolve().parent
DUMP = HERE / "dataset.dump"
SCHEMA = HERE / "schema_mysql5619.sql"
_ENDING = ("SET FOREIGN_KEY_CHECKS = @OLD_FOREIGN_KEY_CHECKS;\n"
           "SET SESSION SQL_MODE = @OLD_SQL_MODE;\n"
           "SET character_set_client = @OLD_CHARACTER_SET_CLIENT;\n"
           "SET character_set_results = @OLD_CHARACTER_SET_RESULTS;\n"
           "SET collation_connection = @OLD_COLLATION_CONNECTION;\n")


class SetupError(ValueError):
    """화면에 표시할 오류. 비밀번호나 원문 SQL은 포함하지 않는다."""


@dataclass(frozen=True)
class SetupResult:
    applied: bool
    backup: Path | None = None


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
    except Exception:
        connection.rollback()
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


def _apply(connection, cursor, rows, definitions):
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
    connection.commit()


def prepare(config: DatabaseConfig, *, confirm: Callable[[str], bool] | None = None,
            folder: Path | None = None, source: Path | None = None) -> SetupResult:
    """검증 → 대상 확인 → 사용자 확인 → 백업 검증 → 초기화 → 행 수 확인."""
    folder = Path(folder) if folder is not None else program_folder()
    rows = read_dataset(source or dump_path(folder))
    definitions = schema_statements()
    _check_references(rows, definitions)
    if not is_loopback(config.host):
        raise SetupError("이 도구는 이 PC에 설치된 Windows MySQL에서만 시연 DB를 준비합니다.")
    lock_name = "setron-init:" + sha256(config.database.casefold().encode()).hexdigest()[:32]
    with pymysql.connect(host=config.host, port=config.port, user=config.username,
                         password=config.password, charset="utf8mb4", connect_timeout=config.connect_timeout) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT @@version_compile_os")
        if not cursor.fetchone()[0].lower().startswith("win"):
            raise SetupError("Windows에 설치된 MySQL에 접속해 주세요. 설정 파일의 포트를 확인하세요.")
        cursor.execute("SELECT GET_LOCK(%s, 5)", (lock_name,))
        if cursor.fetchone() != (1,):
            raise SetupError("시연 DB 준비 작업이 이미 실행 중입니다. 잠시 후 다시 시도해 주세요.")
        try:
            try:
                connection.select_db(config.database)
            except pymysql.err.OperationalError as error:
                if error.args[0] != 1049:
                    raise
                identifier = "`" + config.database.replace("`", "``") + "`"
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {identifier} DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                connection.select_db(config.database)
            objects = _objects(cursor)
            _check_objects(cursor, config, objects)
            backup = None
            old_rows, old_definitions = None, None
            if objects:
                message = (f"대상: {config.host}:{config.port} / DB: {config.database}\n\n"
                           "기존 데이터가 있습니다. 덮어쓰시겠습니까?\n"
                           "관리 테이블 13개의 데이터가 모두 교체됩니다.\n"
                           "진행하면 먼저 프로그램 폴더의 backups에 기존 DB를 SQL로 백업합니다.")
                if confirm is None or not confirm(message):
                    return SetupResult(False)
                try:
                    backup, old_rows, old_definitions = _backup(connection, cursor, folder, config)
                except Exception as error:
                    raise SetupError("기존 DB의 SQL 백업·검증에 실패했습니다. 기존 테이블과 데이터를 변경하지 않았습니다.") from error
            try:
                if objects:
                    _drop(cursor)
                _apply(connection, cursor, rows, definitions)
            except Exception as error:
                try:
                    connection.rollback()
                    _drop(cursor)
                    if old_rows is not None:
                        _apply(connection, cursor, old_rows, old_definitions)
                    else:
                        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
                    restored = "기존 데이터를 자동 복원했습니다." if backup else "이번에 만든 관리 테이블을 정리했습니다."
                except Exception:
                    restored = "자동 복원을 완료하지 못했습니다. 백업 SQL로 복원해 주세요." if backup else "관리 테이블 준비 상태를 확인해 주세요."
                suffix = f"\n백업: {backup}" if backup else ""
                raise SetupError(f"시연 데이터 적용에 실패했습니다. {restored}{suffix}") from error
            return SetupResult(True, backup)
        finally:
            try:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
            except pymysql.MySQLError:
                pass


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


def _reset_rows(connection, cursor):
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
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def reset_data(config: DatabaseConfig, *, confirm: Callable[[str], bool] | None = None,
               folder: Path | None = None) -> SetupResult:
    """덤프 없이 관리 테이블을 검사하고 확인·백업을 마친 뒤 데이터만 지운다."""
    folder = Path(folder) if folder is not None else program_folder()
    schema_statements()  # 신뢰하는 관리 테이블 목록과 번들 스키마 상태를 확인한다.
    if not is_loopback(config.host):
        raise SetupError("이 도구는 이 PC에 설치된 Windows MySQL에서만 데이터를 초기화합니다.")
    lock_name = "setron-init:" + sha256(config.database.casefold().encode()).hexdigest()[:32]
    with pymysql.connect(host=config.host, port=config.port, user=config.username,
                         password=config.password, charset="utf8mb4", connect_timeout=config.connect_timeout) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT @@version_compile_os")
        if not cursor.fetchone()[0].lower().startswith("win"):
            raise SetupError("Windows에 설치된 MySQL에 접속해 주세요. 설정 파일의 포트를 확인하세요.")
        cursor.execute("SELECT GET_LOCK(%s, 5)", (lock_name,))
        if cursor.fetchone() != (1,):
            raise SetupError("DB 준비 또는 초기화 작업이 이미 실행 중입니다. 잠시 후 다시 시도해 주세요.")
        try:
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
            message = (f"대상: {config.host}:{config.port} / DB: {config.database}\n\n"
                       "저장된 데이터를 모두 초기화하시겠습니까?\n"
                       "설비·현황·릴리즈·Item·위험 지정·설정 등 관리 테이블 13개의 모든 행이 삭제됩니다.\n"
                       "테이블 구조는 유지하며 시연 데이터는 넣지 않습니다.\n"
                       "진행하면 먼저 프로그램 폴더의 backups에 기존 DB를 SQL로 백업합니다.")
            if confirm is None or not confirm(message):
                return SetupResult(False)
            try:
                backup, _, _ = _backup(connection, cursor, folder, config)
            except Exception as error:
                raise SetupError("기존 DB의 SQL 백업·검증에 실패했습니다. 기존 테이블과 데이터를 변경하지 않았습니다.") from error
            try:
                _reset_rows(connection, cursor)
            except Exception as error:
                raise SetupError("데이터 초기화에 실패했습니다. 삭제 트랜잭션의 취소를 요청했습니다.\n"
                                 f"기존 데이터 백업: {backup}") from error
            return SetupResult(True, backup)
        finally:
            try:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
            except pymysql.MySQLError:
                pass


def load_config(folder: Path | None = None) -> DatabaseConfig:
    path = (folder or program_folder()) / "setron.env"
    if not path.is_file():
        raise SetupError("프로그램 폴더의 setron.env를 찾지 못했습니다.\nSETRON.exe 및 setron.env와 같은 폴더에서 실행해 주세요.")
    values = os.environ.copy()
    load_env_file(path, values)
    return DatabaseConfig.from_env(values)


def run(*, confirm: Callable[[str], bool] | None = None, mode: str = "import") -> tuple[int, str]:
    try:
        if mode not in {"import", "reset"}:
            return 2, "지원하지 않는 DB 작업입니다."
        result = (reset_data if mode == "reset" else prepare)(load_config(), confirm=confirm)
        if not result.applied:
            return 3, "초기화를 취소했습니다. 기존 테이블과 데이터는 변경하지 않았습니다."
        suffix = f"\n기존 DB 백업: {result.backup}" if result.backup else ""
    except (SetupError, DumpFormatError) as error:
        return 1, str(error)
    except DatabaseConfigError:
        return 1, "DB 연결 설정을 확인할 수 없습니다. setron.env를 확인해 주세요."
    except (OSError, UnicodeError, ValueError):
        return 1, "DB 설정·SQL 데이터·관리 스키마를 읽거나 검증하지 못했습니다. 기존 데이터를 삭제하지 않았습니다."
    except pymysql.MySQLError as error:
        code = error.args[0]
        if code in (2002, 2003):
            return 1, "이 PC의 MySQL에 연결할 수 없습니다. MySQL을 시작한 뒤 다시 실행해 주세요."
        if code in (1044, 1045, 1142):
            return 1, "DB 계정 또는 권한을 확인해 주세요. 테이블 조회·생성·삭제·입력 권한이 필요합니다."
        return 1, f"시연 DB 준비를 중단했습니다. MySQL 오류 코드: {code}"
    if mode == "reset":
        return 0, f"데이터 초기화가 끝났습니다. 관리 테이블 13개의 구조를 유지하고 모든 행이 0개인지 확인했습니다.{suffix}"
    return 0, f"시연 DB 준비가 끝났습니다. 관리 테이블 13개와 SQL의 행 수를 확인했습니다.\nSETRON.exe를 실행해 주세요.{suffix}"


def main() -> int:
    options = sys.argv[1:]
    allowed = {"--import-demo", "--reset-data", "--quiet", "--validate-dump"}
    actions = {"--import-demo", "--reset-data", "--validate-dump"}.intersection(options)
    if any(option not in allowed for option in options) or len(options) != len(set(options)) or len(actions) > 1:
        print("--import-demo, --reset-data, --validate-dump 중 하나를 선택하세요. --quiet를 함께 사용할 수 있습니다.")
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
    mode = "reset" if "--reset-data" in options else "import"
    if "--quiet" in options:
        code, message = run(mode=mode)  # callback이 없으므로 기존 DB를 덮어쓰거나 비우지 않는다.
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
