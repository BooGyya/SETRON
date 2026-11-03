"""시연·확인용 DB를 빈 상태로 다시 만든다: 관리 테이블 삭제 → 스키마 → 시연용 기준 자료.

대상 DB의 **모든 SETRON 자료가 지워진다.** 실수를 막기 위해 대상 DB 이름을 ``--confirm``으로 한 번 더 받는다.
관리 테이블(13개) 외의 테이블과 DB 자체는 건드리지 않는다.

    uv run --locked --env-file .env python database/reset_demo_db.py --confirm <SETRON_DB_NAME 값>
    uv run --locked --env-file .env.windows python database/reset_demo_db.py --confirm setron   # 설치형 MySQL

옵션
- ``--prefix``: 접속 환경변수 접두사 (기본 ``SETRON_DB_``, 테스트 DB는 ``SETRON_TEST_DB_``)
- ``--no-seed``: 시연용 기준 자료(`seed_demo_catalog.sql`) 없이 스키마만 만든다
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql
from pymysql.constants import CLIENT

from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import DatabaseConfig, DatabaseConfigError

HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "schema_mysql5619.sql"
SEED = HERE / "seed_demo_catalog.sql"


def reset(config: DatabaseConfig, *, seed: bool = True) -> dict[str, int]:
    """관리 테이블을 지우고 스키마(와 시드)를 다시 적용한다. 테이블별 행 수를 돌려준다."""
    connection = pymysql.connect(host=config.host, port=config.port, user=config.username, password=config.password,
                                 database=config.database, charset="utf8mb4", connect_timeout=config.connect_timeout,
                                 client_flag=CLIENT.MULTI_STATEMENTS)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW TABLES")
            existing = {row[0] for row in cursor.fetchall()} & set(TABLE_NAMES)
            if existing:
                # 외래키 순서와 무관하게 지우기 위해 이 연결에서만 검사를 끈다.
                cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
                cursor.execute("DROP TABLE " + ", ".join(f"`{t}`" for t in sorted(existing)))
                cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            for path in (SCHEMA, SEED) if seed else (SCHEMA,):
                cursor.execute(path.read_text(encoding="utf-8"))
                while cursor.nextset():
                    pass
            connection.commit()
            counts = {}
            for table in TABLE_NAMES:
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                counts[table] = cursor.fetchone()[0]
            return counts
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm", required=True, help="지울 대상 DB 이름 (접속 설정의 DB 이름과 같아야 함)")
    parser.add_argument("--prefix", default="SETRON_DB_", help="접속 환경변수 접두사")
    parser.add_argument("--no-seed", action="store_true", help="시연용 기준 자료 없이 스키마만 만든다")
    args = parser.parse_args(argv)
    try:
        config = DatabaseConfig.from_env(prefix=args.prefix)
    except DatabaseConfigError as error:
        print(f"접속 설정을 확인해 주세요: {error}", file=sys.stderr)
        return 2
    if args.confirm != config.database:
        print(f"--confirm 값이 접속 설정의 DB 이름({config.database})과 다릅니다. 아무것도 지우지 않았습니다.",
              file=sys.stderr)
        return 2
    try:
        counts = reset(config, seed=not args.no_seed)
    except pymysql.MySQLError as error:
        print(f"DB 초기화에 실패했습니다: {error}", file=sys.stderr)
        return 1
    print(f"{config.host}:{config.port}/{config.database} 초기화 완료 · 관리 테이블 {len(counts)}개")
    print(" · ".join(f"{table} {count}행" for table, count in counts.items() if count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
