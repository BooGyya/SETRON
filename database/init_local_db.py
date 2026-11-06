"""Windows에 설치된 로컬 MySQL에 SETRON 스키마와 시연용 목업 데이터를 적용한다."""

from __future__ import annotations

import argparse
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
import sys

import pymysql
from pymysql.constants import CLIENT

from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import DatabaseConfig, DatabaseConfigError
from setron.infrastructure.env_file import load_env_file

HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "schema_mysql5619.sql"
SEED = HERE / "seed.sql"
DEFAULT_ENV = HERE.parent / ".env.windows"


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def execute_sql(cursor: pymysql.cursors.Cursor, sql: str) -> None:
    cursor.execute(sql)
    while cursor.nextset():
        pass


def execute_seed(cursor: pymysql.cursors.Cursor, sql: str) -> None:
    """생성된 seed.sql을 작은 묶음으로 실행하고 커밋 전에 적용 여부를 검사한다."""
    parts = [part.strip() for part in sql.split(";\n\n")]
    status_query = "SELECT @seed_install AS installed, @seed_done AS already_installed"
    if parts[-2:] != [status_query, "COMMIT;"] or parts.count("START TRANSACTION") != 1:
        raise ValueError("seed.sql의 트랜잭션 또는 완료 확인 구문이 올바르지 않습니다.")

    # 트랜잭션은 호출자가 관리한다. 생성기는 데이터 문자열을 hex로 쓰므로 구분자가 값과 섞이지 않는다.
    statements = [part for part in parts[:-2] if part != "START TRANSACTION"]
    for offset in range(0, len(statements), 50):
        execute_sql(cursor, ";\n\n".join(statements[offset:offset + 50]))

    cursor.execute(status_query)
    if cursor.fetchone() not in ((1, 0), (0, 1)):
        raise ValueError("기존 호기·릴리즈·설정과 목업 시드가 충돌합니다. 이번 시드 적용을 취소했습니다.")


def initialize(config: DatabaseConfig, *, seed: bool = True) -> bool:
    """필요한 경우 테이블을 만든 뒤 목업 데이터를 추가하고 테이블 생성 여부를 반환한다."""
    if not is_loopback(config.host):
        raise ValueError("SETRON_DB_HOST에 로컬 주소(127.0.0.1, ::1 또는 localhost)를 입력해 주세요.")

    schema_sql = SCHEMA.read_text(encoding="utf-8")
    seed_sql = SEED.read_text(encoding="utf-8") if seed else None
    lock_name = "setron-init:" + sha256(config.database.casefold().encode()).hexdigest()[:32]
    expected = set(TABLE_NAMES)

    with pymysql.connect(
        host=config.host, port=config.port, user=config.username, password=config.password,
        database=config.database, charset="utf8mb4", connect_timeout=config.connect_timeout,
        client_flag=CLIENT.MULTI_STATEMENTS,
        init_command="SET SESSION sql_mode = CONCAT_WS(',', @@SESSION.sql_mode, 'STRICT_TRANS_TABLES')",
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@version_compile_os")
            if not cursor.fetchone()[0].lower().startswith("win"):
                raise ValueError("Windows에 설치된 로컬 MySQL에 접속해 주세요. 설정 파일의 포트를 확인하세요.")

            # 연결이 끝나면 잠금이 해제된다. 스키마 생성과 시드 적용을 한 번에 한 작업만 수행한다.
            cursor.execute("SELECT GET_LOCK(%s, 5)", (lock_name,))
            if cursor.fetchone()[0] != 1:
                raise ValueError("DB 준비 작업이 이미 실행 중이거나 잠금을 가져올 수 없습니다.")

            cursor.execute("SHOW FULL TABLES")
            objects = {name.lower(): kind for name, kind in cursor.fetchall() if name.lower() in expected}
            if objects and (set(objects) != expected or any(kind != "BASE TABLE" for kind in objects.values())):
                raise ValueError("관리 테이블이 일부만 있거나 같은 이름의 뷰가 있습니다. 대상 DB의 스키마를 확인해 주세요.")

            if not objects:
                execute_sql(cursor, schema_sql)
                connection.commit()
            if seed_sql is not None:
                connection.begin()
                execute_seed(cursor, seed_sql)
                connection.commit()

    return not objects


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV, help="접속 설정 파일 (기본값: .env.windows)")
    parser.add_argument("--no-seed", action="store_true", help="스키마만 준비하고 시연용 목업 데이터는 생략")
    args = parser.parse_args(argv)

    if not args.env_file.is_file():
        parser.error(f"접속 설정 파일이 없습니다: {args.env_file} (.env.windows.example 참고)")

    try:
        values: dict[str, str] = {}
        load_env_file(args.env_file, values)
        config = DatabaseConfig.from_env(values)
        created = initialize(config, seed=not args.no_seed)
    except (DatabaseConfigError, ValueError, OSError) as error:
        print(f"DB 준비 실패: {error}", file=sys.stderr)
        return 2
    except pymysql.MySQLError as error:
        print(f"DB 준비 실패 (MySQL 오류 코드: {error.args[0]}). 접속 설정, DB 권한, 테이블 상태를 확인해 주세요.",
              file=sys.stderr)
        return 1

    schema_status = f"관리 테이블 {len(TABLE_NAMES)}개 {'생성' if created else '확인'}"
    seed_status = "시연용 목업 데이터 적용 확인" if not args.no_seed else "스키마 준비 완료"
    print(f"{config.host}:{config.port}/{config.database} | {schema_status} | {seed_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
