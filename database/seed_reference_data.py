"""기존 업무 자료를 보존하면서 가져오기에 필요한 모델·계열 기준정보만 추가한다.

    uv run --locked python database/seed_reference_data.py --env-file .env

스키마는 미리 적용되어 있어야 한다. 같은 명령을 다시 실행해도 기준정보를 중복 등록하지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql
from pymysql.constants import CLIENT

from setron.infrastructure.database import DatabaseConfig, DatabaseConfigError
from setron.infrastructure.env_file import load_env_file

SEED = Path(__file__).with_name("seed_demo_catalog.sql")
TABLES = ("equipment_group", "version_family")


def seed_reference_data(config: DatabaseConfig) -> dict[str, int]:
    """기준정보를 한 트랜잭션으로 추가하고 이번에 추가한 행 수를 반환한다."""
    connection = pymysql.connect(
        host=config.host, port=config.port, user=config.username, password=config.password,
        database=config.database, charset="utf8mb4", connect_timeout=config.connect_timeout,
        client_flag=CLIENT.MULTI_STATEMENTS, autocommit=False,
    )
    try:
        with connection.cursor() as cursor:
            before = {}
            for table in TABLES:
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                before[table] = cursor.fetchone()[0]
            cursor.execute(SEED.read_text(encoding="utf-8"))
            while cursor.nextset():
                pass
            added = {}
            for table in TABLES:
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                added[table] = cursor.fetchone()[0] - before[table]
        connection.commit()
        return added
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", type=Path, help="접속 설정 파일 (예: .env, .env.windows)")
    parser.add_argument("--prefix", default="SETRON_DB_", help="접속 환경변수 접두사")
    args = parser.parse_args(argv)
    if args.env_file is not None:
        if not args.env_file.is_file():
            parser.error("지정한 접속 설정 파일이 없습니다.")
        load_env_file(args.env_file)
    try:
        config = DatabaseConfig.from_env(prefix=args.prefix)
        added = seed_reference_data(config)
    except DatabaseConfigError as error:
        print(f"접속 설정을 확인해 주세요: {error}", file=sys.stderr)
        return 2
    except pymysql.MySQLError:
        print("기준정보 추가에 실패했습니다. DB 연결·스키마·권한을 확인해 주세요.", file=sys.stderr)
        return 1
    print(f"{config.host}:{config.port}/{config.database} 기준정보 준비 완료 · "
          f"모델 {added['equipment_group']}개 · 계열 {added['version_family']}개 추가")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
