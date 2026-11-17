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
    """DB 설정 검증에 사용할 환경변수 예시를 제공한다.

    return
    - 공백과 특수문자를 포함한 테스트 비밀번호가 담긴 환경변수 사전.
    """
    return {
        "SETRON_DB_HOST": "127.0.0.1",
        "SETRON_DB_PORT": "3306",
        "SETRON_DB_NAME": "setron_test",
        "SETRON_DB_USER": "tester",
        "SETRON_DB_PASSWORD": " p@ss:/?#% word ",
    }


def test_environment_config_preserves_password_and_builds_mysql_url(env_values):
    """비밀번호 원문을 보존하고 출력에서는 숨기는 MySQL URL 구성을 검증한다.

    param
    - env_values: DB 접속 설정으로 사용할 환경변수 예시.
    """
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
    """필수 설정 누락 시 오류 메시지에 필요한 환경변수 이름이 표시되는지 검증한다."""
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
    """비어 있거나 허용 범위를 벗어난 DB 접속 설정이 거부되는지 검증한다.

    param
    - env_values: 변경 전의 유효한 환경변수 예시.
    - key: SETRON_DB_ 뒤에 붙일 검사 대상 설정 이름.
    - value: 해당 설정에 넣을 잘못된 값.
    """
    env_values["SETRON_DB_" + key] = value
    with pytest.raises(DatabaseConfigError):
        DatabaseConfig.from_env(env_values)


def test_password_must_be_explicit_but_may_be_empty(env_values):
    """빈 비밀번호는 허용하되 비밀번호 환경변수 자체의 누락은 거부하는지 검증한다.

    param
    - env_values: 비밀번호 설정을 변경할 환경변수 예시.
    """
    env_values["SETRON_DB_PASSWORD"] = ""
    assert DatabaseConfig.from_env(env_values).password == ""
    del env_values["SETRON_DB_PASSWORD"]
    with pytest.raises(DatabaseConfigError, match="SETRON_DB_PASSWORD"):
        DatabaseConfig.from_env(env_values)


@pytest.fixture
def database_and_table(tmp_path):
    """트랜잭션 검증용 SQLite DB와 레코드 테이블을 준비하고 사용 후 연결을 정리한다.

    param
    - tmp_path: pytest가 제공하는 테스트별 임시 디렉터리.

    return
    - 테스트 중 사용할 Database와 records 테이블의 튜플.
    """
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
    """별도 DB 연결에서 저장된 레코드를 기본키 순서로 조회한다.

    param
    - database: 조회할 DB 연결 관리자.
    - records: 조회 대상 레코드 테이블.

    return
    - 기본키 오름차순으로 정렬한 저장 행 목록.
    """
    with database.engine.connect() as connection:
        return connection.execute(select(records).order_by(records.c.id)).all()


def test_successful_batch_is_committed(database_and_table):
    """한 트랜잭션의 모든 삽입 결과가 확정되어 다른 연결에서도 조회되는지 검증한다.

    param
    - database_and_table: SQLite DB 연결 관리자와 레코드 테이블.
    """
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
    """DB 오류가 발생하면 같은 트랜잭션의 추가·수정·삭제가 모두 취소되는지 검증한다.

    param
    - database_and_table: SQLite DB 연결 관리자와 레코드 테이블.
    """
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
    """최종 저장 직전 ORM flush가 실패하면 앞선 수정도 취소되는지 검증한다.

    param
    - database_and_table: SQLite DB 연결 관리자와 ORM 매핑에 사용할 레코드 테이블.
    """
    database, records = database_and_table
    mappings = registry()

    class Record:
        """ORM flush 검증용 레코드."""

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
    """업무 코드의 예외를 그대로 전달하면서 같은 트랜잭션의 변경을 취소하는지 검증한다.

    param
    - database_and_table: SQLite DB 연결 관리자와 레코드 테이블.
    - error_type: 업무 코드에서 발생시킬 예외 클래스.
    """
    database, records = database_and_table
    with pytest.raises(error_type, match="검증 실패"):
        with database.transaction() as session:
            session.execute(records.insert(), {"id": 1, "value": "취소할 자료"})
            raise error_type("검증 실패")
    assert stored_rows(database, records) == []


def test_disconnect_during_work_is_a_save_failure(database_and_table):
    """작업 도중의 연결 단절이 최종 저장 결과 미확인 오류와 구분되는지 검증한다.

    param
    - database_and_table: SQLite DB 연결 관리자와 레코드 테이블.
    """
    database, records = database_and_table
    with pytest.raises(DatabaseSaveError) as caught:
        with database.transaction() as session:
            session.execute(records.insert(), {"id": 1, "value": "취소할 자료"})
            raise OperationalError(None, None, RuntimeError("연결 끊김"), connection_invalidated=True)

    assert not isinstance(caught.value, (DatabaseConnectionError, DatabaseCommitUnknownError))
    assert stored_rows(database, records) == []


@pytest.mark.parametrize("connection_invalidated", [False, True])
def test_commit_error_is_handled_and_session_is_released(database_and_table, connection_invalidated):
    """최종 저장 오류를 연결 상태에 따라 구분하고 다음 세션을 사용할 수 있는지 검증한다.

    param
    - database_and_table: SQLite DB 연결 관리자와 레코드 테이블.
    - connection_invalidated: True이면 연결 단절을 동반한 최종 저장 오류를 발생시킨다.
    """
    database, records = database_and_table

    def fail_commit(connection):
        """commit 이벤트에서 검사 대상 DB 오류를 발생시킨다.

        param
        - connection: commit을 시도하는 SQLAlchemy 연결.

        raises
        - OperationalError: 테스트에서 지정한 연결 상태의 저장 실패를 재현하는 경우.
        """
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
    """최초 접속 실패 시 트랜잭션 내부의 업무 코드가 실행되지 않는지 검증한다.

    param
    - tmp_path: 접속할 수 없는 SQLite 경로를 구성할 임시 디렉터리.
    """
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
    """DB 점검 명령이 설정 오류를 종료 코드와 표준 오류 메시지로 전달하는지 검증한다.

    param
    - monkeypatch: DB 설정 로더를 대체할 pytest 도구.
    - capsys: 명령 실행 중 표준 출력을 수집할 pytest 도구.
    """
    def missing_config():
        """필수 환경변수가 없는 상황을 재현한다.

        raises
        - DatabaseConfigError: 테스트용 설정 누락 오류를 발생시키는 경우.
        """
        raise DatabaseConfigError("DB 접속 환경변수가 없습니다: SETRON_DB_HOST")

    monkeypatch.setattr(Database, "from_env", missing_config)
    assert database_module.main() == 1
    assert "SETRON_DB_HOST" in capsys.readouterr().err


def test_driver_authentication_error_is_reported_as_connection_failure():
    """드라이버 인증 준비 오류가 업무 시작 전 접속 오류로 전달되는지 검증한다."""
    def fail_authentication():
        """드라이버에서 인증용 모듈을 사용할 수 없는 상황을 재현한다.

        raises
        - RuntimeError: 테스트용 인증 준비 오류를 발생시키는 경우.
        """
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
