"""Windows DB 준비의 원인·권한 안내를 실제 DB 접속 없이 검증한다."""

from dataclasses import replace

import pymysql
import pytest

from test_init_demo_dump import destructive, initializer, setup  # noqa: F401


GUIDE_TITLE = "계정·권한 확인"
IMPORT_RIGHTS = "SELECT, CREATE, DROP, INSERT, REFERENCES"
RESET_RIGHTS = "SELECT, DELETE"
RAW_SQL = "DROP DATABASE RAW_SQL_MUST_NOT_BE_PRINTED"


def configured_run(initializer, setup, monkeypatch):
    """대상 식별과 비밀번호 비공개 여부를 명확히 구분할 시험 설정."""
    _, _, _, config, _ = setup
    config = replace(config, username="qa_db_operator", password="PRIVATE_PASSWORD_MUST_NOT_LEAK")
    monkeypatch.setattr(initializer, "load_config", lambda: config)
    return config


def fail_action(initializer, monkeypatch, mode, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(initializer, "prepare" if mode == "import" else "reset_data", fail)


def assert_guide(message, config, mode):
    assert GUIDE_TITLE in message
    assert "SHOW GRANTS" in message
    assert config.database in message and config.username in message
    assert (IMPORT_RIGHTS if mode == "import" else RESET_RIGHTS) in message
    assert config.password not in message
    assert RAW_SQL not in message
    guide = message.split(GUIDE_TITLE, 1)[1]
    assert not any(line.lstrip().startswith(("1.", "2.", "3.")) for line in guide.splitlines())


def assert_no_database_work(setup):
    _, old, db, _, connections = setup
    assert db.rows == old
    assert not connections and not db.log and not destructive(db)


@pytest.mark.parametrize("mode", ["import", "reset"])
@pytest.mark.parametrize("code,meaning", [
    (1045, ("로그인", "인증", "비밀번호")),
    (1044, ("접근", "사용", "권한")),
    (1142, ("권한",)),
    (1143, ("권한",)),
    (1227, ("권한",)),
    (2002, ("연결", "접속")),
    (2003, ("연결", "접속")),
    (9999, ("MySQL",)),
])
def test_mysql_failure_identifies_code_and_rights_without_exposing_driver_message(
        initializer, setup, monkeypatch, mode, code, meaning):
    config = configured_run(initializer, setup, monkeypatch)
    error = pymysql.err.OperationalError(code, f"password={config.password}; {RAW_SQL}")
    fail_action(initializer, monkeypatch, mode, error)

    status, message = initializer.run(mode=mode)

    assert status == 1
    assert str(code) in message
    base_message = message.split(GUIDE_TITLE, 1)[0]
    assert any(word in base_message for word in meaning)
    assert_guide(message, config, mode)
    assert_no_database_work(setup)


@pytest.mark.parametrize("mode", ["import", "reset"])
@pytest.mark.parametrize("nested", [False, True], ids=["direct-cause", "nested-cause"])
def test_wrapped_mysql_failure_preserves_recovery_and_backup_details(
        initializer, setup, monkeypatch, tmp_path, mode, nested):
    config = configured_run(initializer, setup, monkeypatch)
    backup = tmp_path / "backups" / "chosen_db_recovery.sql"
    public_message = f"시연 데이터 적용에 실패했습니다. 기존 데이터를 자동 복원했습니다.\n백업: {backup}"

    def fail(*args, **kwargs):
        try:
            raise pymysql.err.OperationalError(1142, f"password={config.password}; {RAW_SQL}")
        except pymysql.MySQLError as cause:
            if not nested:
                raise initializer.SetupError(public_message) from cause
            try:
                raise initializer.SetupError("기존 DB의 SQL 백업·검증에 실패했습니다.") from cause
            except initializer.SetupError as intermediate:
                raise initializer.SetupError(public_message) from intermediate

    monkeypatch.setattr(initializer, "prepare" if mode == "import" else "reset_data", fail)

    status, message = initializer.run(mode=mode)

    assert status == 1
    assert public_message in message
    assert "1142" in message
    assert_guide(message, config, mode)
    assert_no_database_work(setup)


@pytest.mark.parametrize("mode", ["import", "reset"])
@pytest.mark.parametrize("error_type", [FileNotFoundError, PermissionError])
def test_file_failure_after_config_loading_keeps_a_safe_base_message_and_rights_guide(
        initializer, setup, monkeypatch, mode, error_type):
    config = configured_run(initializer, setup, monkeypatch)
    fail_action(initializer, monkeypatch, mode, error_type(f"{config.password}; {RAW_SQL}"))

    status, message = initializer.run(mode=mode)

    assert status == 1
    assert any(word in message for word in ("읽", "파일", "검증"))
    assert_guide(message, config, mode)
    assert_no_database_work(setup)


def test_bad_dump_preserves_its_format_error_and_never_connects(
        initializer, setup, monkeypatch, tmp_path):
    source, _, _, _, _ = setup
    config = configured_run(initializer, setup, monkeypatch)
    monkeypatch.setattr(initializer, "program_folder", lambda: tmp_path)
    source.write_text("This is not a SETRON SQL export.", encoding="utf-8")

    status, message = initializer.run(mode="import", confirm=lambda _: pytest.fail("잘못된 SQL은 확인 전에 중단"))

    assert status == 1
    assert "SETRON에서 내보낸 완전한 SQL 파일이 필요합니다." in message
    assert_guide(message, config, "import")
    assert_no_database_work(setup)


@pytest.mark.parametrize("mode", ["import", "reset"])
def test_missing_configuration_keeps_the_relevant_error_without_inventing_a_target(
        initializer, setup, monkeypatch, mode):
    def missing_config():
        raise initializer.SetupError("프로그램 폴더의 setron.env를 찾지 못했습니다.")

    monkeypatch.setattr(initializer, "load_config", missing_config)
    monkeypatch.setattr(initializer, "prepare", lambda *a, **kw: pytest.fail("설정 오류 뒤 작업 실행 금지"))
    monkeypatch.setattr(initializer, "reset_data", lambda *a, **kw: pytest.fail("설정 오류 뒤 작업 실행 금지"))

    status, message = initializer.run(mode=mode)

    assert status == 1
    assert "setron.env를 찾지 못했습니다." in message
    assert "chosen_db" not in message
    assert_no_database_work(setup)


@pytest.mark.parametrize("mode", ["import", "reset"])
def test_cancellation_names_the_selected_action_and_does_not_show_a_failure_guide(
        initializer, setup, monkeypatch, mode):
    configured_run(initializer, setup, monkeypatch)
    monkeypatch.setattr(initializer, "prepare" if mode == "import" else "reset_data",
                        lambda *a, **kw: initializer.SetupResult(False))

    status, message = initializer.run(mode=mode)

    assert status == 3 and message == "취소했습니다. DB는 변경하지 않았습니다."
    assert GUIDE_TITLE not in message
    assert_no_database_work(setup)


@pytest.mark.parametrize("mode", ["import", "reset"])
def test_success_keeps_the_backup_path_without_a_failure_guide(
        initializer, setup, monkeypatch, tmp_path, mode):
    config = configured_run(initializer, setup, monkeypatch)
    backup = tmp_path / "backups" / "chosen_db.sql"
    monkeypatch.setattr(initializer, "prepare" if mode == "import" else "reset_data",
                        lambda *a, **kw: initializer.SetupResult(True, backup))

    status, message = initializer.run(mode=mode)

    expected = "데이터 초기화 완료" if mode == "reset" else "시연 데이터 넣기 완료"
    assert status == 0 and message == f"{expected}\n백업 파일: {backup}"
    assert GUIDE_TITLE not in message and "SHOW GRANTS" not in message
    assert config.password not in message
    assert_no_database_work(setup)

    monkeypatch.setattr(initializer, "prepare" if mode == "import" else "reset_data",
                        lambda *a, **kw: initializer.SetupResult(True))
    assert initializer.run(mode=mode) == (0, expected)
    assert_no_database_work(setup)
