"""Windows 로컬 MySQL에 데이터 없이 SETRON 관리 테이블만 준비한다.

배포 시 빌더가 만든 CREATE TABLE 전용 스키마를 사용한다. 원본의 INSERT는 허용하지 않는다.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
import re
import sys

import pymysql

from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import DatabaseConfig, DatabaseConfigError
from setron.infrastructure.env_file import load_env_file


HERE = Path(__file__).resolve().parent


def schema_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "database" / "schema_mysql5619.sql"
    return HERE / "schema_mysql5619.sql"


def default_env() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).with_name("setron.env")
    return HERE.parent / "dist" / "SETRON" / "setron.env"


def _split_sql(sql: str) -> tuple[str, ...]:
    """문자열 안의 세미콜론은 보존하며 실행 가능한 주석·여분의 SQL을 숨기지 않는다."""
    statements, current = [], []
    position, quote = 0, None
    while position < len(sql):
        char = sql[position]
        following = sql[position:position + 2]
        if quote:
            current.append(char)
            if char == "\\" and position + 1 < len(sql):
                position += 1
                current.append(sql[position])
            elif char == quote:
                if position + 1 < len(sql) and sql[position + 1] == quote:
                    position += 1
                    current.append(quote)
                else:
                    quote = None
        elif char in "'\"`":
            quote = char
            current.append(char)
        elif char == "#" or (following == "--" and (position + 2 == len(sql) or sql[position + 2].isspace())):
            ending = sql.find("\n", position)
            position = len(sql) if ending < 0 else ending
            current.append("\n")
            continue
        elif following == "/*":
            if sql[position:position + 3] in {"/*!", "/*+"}:
                raise ValueError("실행 가능한 주석이 있는 스키마는 사용할 수 없습니다.")
            ending = sql.find("*/", position + 2)
            if ending < 0:
                raise ValueError("스키마의 주석이 끝나지 않았습니다.")
            position = ending + 2
            current.append(" ")
            continue
        elif char == ";":
            if statement := "".join(current).strip():
                statements.append(statement)
            current = []
        else:
            current.append(char)
        position += 1
    if quote:
        raise ValueError("스키마의 문자열이 끝나지 않았습니다.")
    if "".join(current).strip():
        raise ValueError("스키마 SQL은 세미콜론으로 끝나야 합니다.")
    return tuple(statements)


def schema_statements(sql: str) -> tuple[str, ...]:
    """정해진 13개 CREATE TABLE만 허용한다. 데이터·삭제·교체 SQL은 실행하지 않는다."""
    creates, names = [], []
    for statement in _split_sql(sql):
        if re.fullmatch(r"SET\s+NAMES\s+utf8mb4\s+COLLATE\s+utf8mb4_unicode_ci", statement, re.I):
            continue  # 연결의 charset 설정으로 충분하므로 실행하지 않는다.
        match = re.fullmatch(
            r"CREATE\s+TABLE\s+([a-z_]+)\s*\(.*\)\s+ENGINE\s*=\s*InnoDB\s+"
            r"DEFAULT\s+CHARSET\s*=\s*utf8mb4\s+COLLATE\s*=\s*utf8mb4_unicode_ci"
            r"(?:\s+COMMENT\s*=\s*'(?:[^'\\]|\\.|'')*')?", statement, re.S | re.I)
        if match is None:
            raise ValueError("스키마에는 관리 테이블 CREATE TABLE만 포함할 수 있습니다. 데이터·삭제 명령은 허용하지 않습니다.")
        names.append(match[1])
        creates.append(statement)
    if tuple(names) != TABLE_NAMES:
        raise ValueError("관리 테이블 13개의 이름과 생성 순서가 올바르지 않습니다.")
    return tuple(creates)


def read_schema(path: Path | None = None) -> tuple[str, ...]:
    return schema_statements((schema_path() if path is None else path).read_text(encoding="utf-8-sig"))


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _confirm(config: DatabaseConfig) -> bool:
    print(f"대상 DB: {config.host}:{config.port}/{config.database} | 계정: {config.username}")
    print("관리 테이블 13개를 생성합니다. 데이터는 추가하지 않습니다.")
    try:
        return input("생성할까요? [y/N]: ").strip().casefold() in {"y", "yes", "예"}
    except (EOFError, KeyboardInterrupt):
        return False


def initialize(config: DatabaseConfig, *, confirm=None, statements=None) -> str:
    """준비 완료·신규 생성·취소를 반환한다. 기존 관리 테이블은 읽기만 한다."""
    if not is_loopback(config.host):
        raise ValueError("SETRON_DB_HOST에 로컬 주소(127.0.0.1, ::1 또는 localhost)를 입력해 주세요.")
    statements = read_schema() if statements is None else schema_statements(";\n".join(statements) + ";")
    lock_name = "setron-init:" + sha256(config.database.casefold().encode()).hexdigest()[:32]
    with pymysql.connect(
        host=config.host, port=config.port, user=config.username, password=config.password,
        database=config.database, charset="utf8mb4", connect_timeout=config.connect_timeout,
        init_command="SET SESSION sql_mode = CONCAT_WS(',', @@SESSION.sql_mode, 'STRICT_TRANS_TABLES')",
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@version_compile_os")
            if not cursor.fetchone()[0].lower().startswith("win"):
                raise ValueError("Windows에 설치된 로컬 MySQL에 접속해 주세요. 설정 파일의 포트를 확인하세요.")
            cursor.execute("SELECT GET_LOCK(%s, 5)", (lock_name,))
            if cursor.fetchone()[0] != 1:
                raise ValueError("DB 준비 작업이 이미 실행 중이거나 잠금을 가져올 수 없습니다.")
            cursor.execute("SHOW FULL TABLES")
            objects = {name.lower(): kind for name, kind in cursor.fetchall() if name.lower() in TABLE_NAMES}
            if objects:
                if set(objects) != set(TABLE_NAMES) or any(kind != "BASE TABLE" for kind in objects.values()):
                    raise ValueError("관리 테이블이 일부만 있거나 같은 이름의 뷰가 있습니다. 아무것도 변경하지 않았습니다.")
                return "ready"
            if not (confirm or _confirm)(config):
                return "cancelled"
            completed = 0
            try:
                for statement in statements:
                    cursor.execute(statement)
                    completed += 1
            except pymysql.MySQLError as error:
                raise ValueError(
                    f"테이블 생성 중 오류가 발생했습니다 (MySQL 코드: {error.args[0]}, 완료: {completed}/13개). "
                    "MySQL 테이블 생성은 자동 확정되어 일부 구조가 남을 수 있습니다. "
                    "자동으로 삭제하거나 재설정하지 않았습니다. 대상 DB의 테이블과 권한을 확인해 주세요.") from error
    return "created"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=default_env(), help="접속 설정 파일")
    parser.add_argument("--validate-schema", action="store_true", help="DB 접속 없이 포함된 스키마만 검사")
    args = parser.parse_args(argv)
    try:
        statements = read_schema()
        if args.validate_schema:
            print("스키마 검증 완료: CREATE TABLE 13개, 데이터 명령 없음")
            return 0
        values: dict[str, str] = {}
        load_env_file(args.env_file, values)
        config = DatabaseConfig.from_env(values)
        status = initialize(config, statements=statements)
    except (DatabaseConfigError, ValueError, OSError) as error:
        print(f"DB 준비 실패: {error}", file=sys.stderr)
        return 2
    except pymysql.MySQLError as error:
        print(f"DB 준비 실패 (MySQL 코드: {error.args[0]}). 접속 설정과 DB 권한을 확인해 주세요.", file=sys.stderr)
        return 1
    if status == "cancelled":
        print("DB 준비를 취소했습니다. 테이블과 데이터는 변경하지 않았습니다.")
        return 3
    result = "관리 테이블 13개 생성 완료" if status == "created" else "이미 준비된 DB입니다. 기존 테이블과 데이터를 유지했습니다."
    print(f"{config.host}:{config.port}/{config.database} | {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
