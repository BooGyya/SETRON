"""SQLAlchemy와 SQLite로 DB 연결과 트랜잭션 처리를 검증한다."""

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import registry

from setron.infrastructure import database as database_module
from setron.infrastructure.database import (
    Database,
    DatabaseCommitUnknownError,
    DatabaseConfig,
    DatabaseConfigError,
    DatabaseConnectionError,
    DatabaseSaveError,
    create_mysql_engine,
)


@pytest.fixture
def env_values():
    return {
        "SETRON_DB_HOST": "127.0.0.1",
        "SETRON_DB_PORT": "3306",
        "SETRON_DB_NAME": "setron_test",
        "SETRON_DB_USER": "tester",
        "SETRON_DB_PASSWORD": " p@ss:/?#% word ",
    }


def test_environment_config_preserves_password_and_builds_mysql_url(env_values):
    config = DatabaseConfig.from_env(env_values)
    engine = create_mysql_engine(config)
    try:
        assert engine.url.drivername == "mysql+pymysql"
        assert engine.url.database == "setron_test"
        assert engine.url.password == env_values["SETRON_DB_PASSWORD"]
        assert engine.url.query["charset"] == "utf8mb4"
        assert config.password not in repr(config)
        assert config.password not in str(engine.url)
    finally:
        engine.dispose()


def test_missing_configuration_lists_variable_names_only():
    with pytest.raises(DatabaseConfigError, match="SETRON_DB_HOST"):
        DatabaseConfig.from_env({})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("HOST", " "),
        ("NAME", ""),
        ("USER", "\t"),
        ("PORT", "not-a-port"),
        ("PORT", "0"),
        ("PORT", "65536"),
        ("CONNECT_TIMEOUT", "0"),
        ("CONNECT_TIMEOUT", "1.5"),
    ],
)
def test_invalid_configuration_is_rejected(env_values, key, value):
    env_values["SETRON_DB_" + key] = value
    with pytest.raises(DatabaseConfigError):
        DatabaseConfig.from_env(env_values)


def test_password_must_be_explicit_but_may_be_empty(env_values):
    env_values["SETRON_DB_PASSWORD"] = ""
    assert DatabaseConfig.from_env(env_values).password == ""
    del env_values["SETRON_DB_PASSWORD"]
    with pytest.raises(DatabaseConfigError, match="SETRON_DB_PASSWORD"):
        DatabaseConfig.from_env(env_values)


@pytest.fixture
def database_and_table(tmp_path):
    # 임시 SQLite 파일을 만들어 저장 결과를 별도 연결에서 다시 조회한다.
    engine = create_engine("sqlite:///" + (tmp_path / "database.db").as_posix())
    metadata = MetaData()
    records = Table(
        "records",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("value", String(50), nullable=False),
    )
    metadata.create_all(engine)
    database = Database(engine)
    try:
        yield database, records
    finally:
        database.dispose()


def stored_rows(database, records):
    with database.engine.connect() as connection:
        return connection.execute(select(records).order_by(records.c.id)).all()


def test_successful_batch_is_committed(database_and_table):
    database, records = database_and_table
    database.check_connection()

    with database.transaction() as session:
        session.execute(records.insert(), {"id": 1, "value": "설비 A"})
        session.execute(records.insert(), {"id": 2, "value": "설비 B"})

    assert stored_rows(database, records) == [(1, "설비 A"), (2, "설비 B")]
    with database.transaction() as session:
        assert session.scalars(select(records.c.value).order_by(records.c.id)).all() == [
            "설비 A", "설비 B"
        ]


def test_database_error_rolls_back_insert_update_and_delete(database_and_table):
    database, records = database_and_table
    with database.transaction() as session:
        session.execute(records.insert(), [{"id": 1, "value": "기존 A"}, {"id": 2, "value": "기존 B"}])

    with pytest.raises(DatabaseSaveError) as caught:
        with database.transaction() as session:
            session.execute(records.update().where(records.c.id == 1).values(value="수정 A"))
            session.execute(records.delete().where(records.c.id == 2))
            session.execute(records.insert(), {"id": 3, "value": "추가 C"})
            session.execute(records.insert(), {"id": 1, "value": "중복 오류"})

    assert not isinstance(caught.value, DatabaseConnectionError)
    assert stored_rows(database, records) == [(1, "기존 A"), (2, "기존 B")]

    # 새 세션으로 다음 작업을 실행해 이전 실패의 영향이 남아 있지 않은지 확인한다.
    with database.transaction() as session:
        session.execute(records.insert(), {"id": 3, "value": "재시도 C"})
    assert stored_rows(database, records)[-1] == (3, "재시도 C")


def test_orm_flush_failure_at_commit_cancels_previous_changes(database_and_table):
    database, records = database_and_table
    mappings = registry()

    class Record:
        pass

    mappings.map_imperatively(Record, records)
    try:
        with database.transaction() as session:
            session.execute(records.insert(), {"id": 1, "value": "기존값"})

        with pytest.raises(DatabaseSaveError):
            with database.transaction() as session:
                session.execute(records.update().where(records.c.id == 1).values(value="수정값"))
                # commit 직전의 flush에서 INSERT가 실행되면서 중복 오류가 발생한다.
                session.add(Record(id=1, value="중복값"))

        assert stored_rows(database, records) == [(1, "기존값")]
    finally:
        mappings.dispose()


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_business_error_is_preserved_and_cancels_batch(database_and_table, error_type):
    database, records = database_and_table
    with pytest.raises(error_type, match="검증 실패"):
        with database.transaction() as session:
            session.execute(records.insert(), {"id": 1, "value": "취소할 자료"})
            raise error_type("검증 실패")
    assert stored_rows(database, records) == []


def test_disconnect_during_work_is_a_save_failure(database_and_table):
    database, records = database_and_table
    with pytest.raises(DatabaseSaveError) as caught:
        with database.transaction() as session:
            session.execute(records.insert(), {"id": 1, "value": "취소할 자료"})
            raise OperationalError(None, None, RuntimeError("연결 끊김"), connection_invalidated=True)

    assert not isinstance(caught.value, (DatabaseConnectionError, DatabaseCommitUnknownError))
    assert stored_rows(database, records) == []


@pytest.mark.parametrize("connection_invalidated", [False, True])
def test_commit_error_is_handled_and_session_is_released(database_and_table, connection_invalidated):
    database, records = database_and_table

    def fail_commit(connection):
        raise OperationalError(
            None, None, RuntimeError("commit 실패"), connection_invalidated=connection_invalidated
        )

    event.listen(database.engine, "commit", fail_commit)
    expected = DatabaseCommitUnknownError if connection_invalidated else DatabaseSaveError
    try:
        with pytest.raises(expected):
            with database.transaction() as session:
                session.execute(records.insert(), {"id": 1, "value": "미완료"})
    finally:
        event.remove(database.engine, "commit", fail_commit)

    assert stored_rows(database, records) == []
    with database.transaction() as session:
        session.execute(records.insert(), {"id": 1, "value": "새 작업"})


def test_connection_error_occurs_before_business_code_runs(tmp_path):
    # DB 파일의 상위 폴더가 없으면 연결에 실패한다.
    engine = create_engine("sqlite:///" + (tmp_path / "missing" / "db.sqlite").as_posix())
    database = Database(engine)
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


def test_cli_reports_missing_config_without_a_traceback(monkeypatch, capsys):
    def missing_config():
        raise DatabaseConfigError("DB 접속 환경변수가 없습니다: SETRON_DB_HOST")

    monkeypatch.setattr(Database, "from_env", missing_config)
    assert database_module.main() == 1
    assert "SETRON_DB_HOST" in capsys.readouterr().err


def test_driver_authentication_error_is_reported_as_connection_failure():
    def fail_authentication():
        raise RuntimeError("인증에 필요한 암호화 모듈을 사용할 수 없습니다.")

    engine = create_engine("mysql+pymysql://", creator=fail_authentication)
    database = Database(engine)
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
