"""테스트용 MySQL DB의 접속 설정이 있을 때 통합 테스트를 실행한다."""

import os
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, select

from setron.infrastructure.database import (
    Database,
    DatabaseConfig,
    DatabaseConnectionError,
    DatabaseSaveError,
    create_mysql_engine,
)


pytestmark = pytest.mark.skipif(
    not os.environ.get("SETRON_TEST_DB_NAME", "").strip(),
    reason="별도 MySQL 테스트 DB 접속 환경변수가 설정되지 않았습니다.",
)


@pytest.fixture
def mysql_database_and_table():
    """테스트 MySQL에 임시 테이블을 만들고 사용 후 해당 테이블과 연결을 정리한다.

    SETRON_TEST_DB_ 접속 설정을 사용하며 테이블 이름에 임의 식별자를 붙인다.

    return
    - 테스트 중 사용할 Database와 InnoDB 레코드 테이블의 튜플.
    """
    # 미리 생성한 테스트 DB에 전용 환경변수로 접속한다.
    database = Database.from_env(prefix="SETRON_TEST_DB_")
    metadata = MetaData()
    records = Table(
        "be001_transaction_" + uuid4().hex,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("value", String(50), nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    created = False
    try:
        database.check_connection()
        records.create(database.engine)
        created = True
        yield database, records
    finally:
        try:
            if created:
                # 이번 테스트에서 만든 테이블만 삭제한다.
                records.drop(database.engine)
        finally:
            database.dispose()


def test_mysql_commits_success_and_rolls_back_failed_batch(mysql_database_and_table):
    """MySQL에서 정상 저장은 확정되고 중복키 오류가 난 작업은 모두 취소되는지 검증한다.

    param
    - mysql_database_and_table: 테스트 MySQL 연결 관리자와 임시 레코드 테이블.
    """
    database, records = mysql_database_and_table
    with database.transaction() as session:
        session.execute(records.insert(), [{"id": 1, "value": "기존 A"}, {"id": 2, "value": "기존 B"}])

    with database.engine.connect() as connection:
        assert connection.execute(select(records).order_by(records.c.id)).all() == [
            (1, "기존 A"), (2, "기존 B")
        ]
    with pytest.raises(DatabaseSaveError):
        with database.transaction() as session:
            session.execute(records.update().where(records.c.id == 1).values(value="수정 A"))
            session.execute(records.delete().where(records.c.id == 2))
            session.execute(records.insert(), {"id": 3, "value": "추가 C"})
            session.execute(records.insert(), {"id": 1, "value": "중복 오류"})

    with database.engine.connect() as connection:
        assert connection.execute(select(records).order_by(records.c.id)).all() == [
            (1, "기존 A"), (2, "기존 B")
        ]


def test_mysql_authentication_failure_is_reported_before_work_starts():
    """잘못된 MySQL 비밀번호로 접속하면 업무 코드 실행 전에 실패하는지 검증한다."""
    config = DatabaseConfig.from_env(prefix="SETRON_TEST_DB_")
    wrong_password = "wrong-" + uuid4().hex
    database = Database(create_mysql_engine(replace(config, password=wrong_password)))
    entered = False
    try:
        with pytest.raises(DatabaseConnectionError):
            database.check_connection()
        with pytest.raises(DatabaseConnectionError):
            with database.transaction():
                entered = True
        assert entered is False
    finally:
        database.dispose()
