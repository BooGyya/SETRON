"""삭제 전 차단·중단 복구·결과 미확인을 DB 연결 없이 검증한다."""
from copy import deepcopy
from dataclasses import replace
import importlib
import json
from pathlib import Path

import pymysql
import pytest

from test_init_demo_dump import destructive, initializer, setup  # noqa: F401


@pytest.fixture
def safety(initializer):
    return importlib.import_module("demo_db_safety")


def grant(rights, scope="`chosen_db`.*"):
    return f"GRANT {', '.join(rights)} ON {scope} TO 'demo'@'localhost' IDENTIFIED BY PASSWORD 'PRIVATE_HASH'"


@pytest.mark.parametrize("mode,missing", [
    ("import", "SELECT"), ("import", "CREATE"), ("import", "DROP"),
    ("import", "INSERT"), ("import", "REFERENCES"), ("import", "TRIGGER"),
    ("reset", "SELECT"), ("reset", "DELETE"), ("reset", "TRIGGER"),
])
def test_missing_privilege_blocks_before_confirmation_backup_or_mutation(
        initializer, safety, setup, tmp_path, mode, missing):
    _, original, db, config, _ = setup
    db.grants = (grant(sorted(safety._RIGHTS[mode] - {missing})),)
    action = initializer.prepare if mode == "import" else initializer.reset_data
    with pytest.raises(initializer.SetupError, match=missing) as error:
        action(config, folder=tmp_path, confirm=lambda _: pytest.fail("권한 부족은 확인 전에 차단"))
    assert "PRIVATE_HASH" not in str(error.value)
    assert db.rows == original and not destructive(db)
    assert not (tmp_path / "backups").exists()
    assert not list(tmp_path.glob("*.pending.json"))


def test_per_table_create_does_not_allow_creating_a_missing_database(initializer, safety, setup, tmp_path):
    _, original, db, config, _ = setup
    db.grants = tuple(grant(sorted(safety._RIGHTS["import"]), f"`chosen_db`.`{table}`") for table in db.objects)
    db.missing, db.objects = True, {}
    with pytest.raises(initializer.SetupError, match="DB 생성"):
        initializer.prepare(config, folder=tmp_path)
    assert db.rows == original and not destructive(db)
    assert not any(sql.startswith("CREATE DATABASE") for sql, _ in db.log)


@pytest.mark.parametrize("grants,allowed", [
    (["GRANT ALL PRIVILEGES ON *.* TO 'u'@'localhost'"], True),
    (["GRANT ALL PRIVILEGES ON `chosen\\_db`.* TO 'u'@'localhost'"], True),
    (["GRANT ALL PRIVILEGES ON `chosen%`.* TO 'u'@'localhost'"], True),
    (["GRANT ALL PRIVILEGES ON `chosenXdb`.* TO 'u'@'localhost'"], False),
    (["GRANT SELECT (`equipment_id`), INSERT (`equipment_id`) ON *.* TO 'u'@'localhost'"], False),
    (["GRANT USAGE ON *.* TO 'u'@'localhost'"], False),
    (["GRANT ALL PRIVILEGES ON *.* TO 'u'@'localhost'", "REVOKE INSERT ON `chosen_db`.* FROM 'u'@'localhost'"], False),
    (["GRANT ALL PRIVILEGES ON *.* TO 'u'@'localhost'", "GRANT PROXY ON ''@'' TO 'u'@'localhost'"], True),
])
def test_effective_rights_never_use_column_grants_or_ignore_restrictions(safety, setup, grants, allowed):
    _, _, db, _, _ = setup
    db.grants = tuple(grants)
    if allowed:
        safety.check_privileges(db.cursor(), "chosen_db", "import", tuple(db.objects))
    else:
        with pytest.raises(safety.SafetyError):
            safety.check_privileges(db.cursor(), "chosen_db", "import", tuple(db.objects))


def test_table_database_name_is_literal_and_overlapping_db_patterns_are_not_unioned(safety, setup):
    _, _, db, _, _ = setup
    db.grants = (grant(sorted(safety._RIGHTS["import"]), "`chosen_db`.`equipment`"),)
    with pytest.raises(safety.SafetyError):
        safety.check_privileges(db.cursor(), "chosenXdb", "import", ("equipment",))
    db.grants = (grant(["SELECT", "CREATE", "DROP"], "`chosen%`.*"),
                 grant(["INSERT", "REFERENCES", "TRIGGER"], "`chosen_db`.*"))
    with pytest.raises(safety.SafetyError):
        safety.check_privileges(db.cursor(), "chosen_db", "import", ("equipment",))


@pytest.mark.parametrize("mode", ["import", "reset"])
def test_pending_operation_blocks_even_partial_database_before_connect(
        initializer, safety, setup, tmp_path, mode):
    source, _, db, config, connections = setup
    backup, _, _ = initializer._backup(db, db.cursor(), tmp_path, config)
    operation = safety.begin_operation(tmp_path, config, mode, backup)
    db.objects.pop("equipment")
    source.write_text("invalid SQL must not hide recovery instructions", encoding="utf-8")
    db.log.clear()
    action = initializer.prepare if mode == "import" else initializer.reset_data
    with pytest.raises(initializer.SetupError, match="이전 DB 작업") as error:
        action(config, folder=tmp_path)
    assert str(backup) in str(error.value) and str(operation.path) in str(error.value)
    assert "확인을 마친 뒤에만" in str(error.value)
    assert not connections and not db.log


def test_journal_contains_no_credentials_and_identifies_port_and_database(safety, setup, tmp_path):
    _, _, _, config, _ = setup
    operation = safety.begin_operation(tmp_path, config, "reset", None)
    contents = operation.path.read_text(encoding="utf-8")
    assert config.password not in contents and config.username not in contents
    assert safety.journal_path(tmp_path, replace(config, host="localhost")) == operation.path
    assert safety.journal_path(tmp_path, replace(config, port=config.port + 1)) != operation.path
    assert safety.journal_path(tmp_path, replace(config, database="another")) != operation.path


def test_failed_atomic_status_update_preserves_original_backup_record(safety, setup, tmp_path, monkeypatch):
    _, _, _, config, _ = setup
    operation = safety.begin_operation(tmp_path, config, "import", tmp_path / "backups" / "original.sql")
    original = operation.path.read_bytes()
    def fail_replace(*args):
        raise PermissionError("simulated file lock")
    monkeypatch.setattr(safety.os, "replace", fail_replace)
    operation.uncertain()
    assert operation.path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))
    with pytest.raises(safety.SafetyError, match="original.sql"):
        safety.assert_no_pending(tmp_path, config)


def test_journal_delete_failure_does_not_turn_success_into_failure(
        initializer, safety, setup, tmp_path, monkeypatch):
    source, _, db, config, _ = setup
    pending = safety.journal_path(tmp_path, config)
    real_unlink = Path.unlink
    def deny_journal_unlink(path, *args, **kwargs):
        if path == pending:
            raise PermissionError("simulated journal cleanup failure")
        return real_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", deny_journal_unlink)
    result = initializer.prepare(config, folder=tmp_path, confirm=lambda _: True)
    assert result.applied and "작업은 완료" in result.warning
    assert db.rows == initializer.read_dataset(source)
    assert json.loads(pending.read_text(encoding="utf-8"))["status"] == "completed"
    safety.assert_no_pending(tmp_path, config)


@pytest.mark.parametrize("mode", ["import", "reset"])
def test_ctrl_c_during_mutation_restores_original_and_removes_pending(
        initializer, safety, setup, tmp_path, mode):
    _, original, db, config, _ = setup
    execute = db._cursor.execute
    interrupted = False
    sql_to_interrupt = ("DROP TABLE IF EXISTS `equipment`" if mode == "import" else "DELETE FROM `equipment`")
    def interrupt_once(sql, params=None):
        nonlocal interrupted
        value = execute(sql, params)
        if sql == sql_to_interrupt and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
        return value
    db._cursor.execute = interrupt_once
    action = initializer.prepare if mode == "import" else initializer.reset_data
    with pytest.raises(initializer.SetupError):
        action(config, folder=tmp_path, confirm=lambda _: True)
    assert interrupted and db.rows == original
    assert set(db.objects) == set(original)
    assert not safety.journal_path(tmp_path, config).exists()
    backup, = (tmp_path / "backups").glob("*.sql")
    assert initializer.read_dataset(backup) == original


@pytest.mark.parametrize("mode", ["import", "reset"])
def test_lost_commit_reply_retains_pending_and_never_automatically_replaces_data(
        initializer, safety, setup, tmp_path, mode):
    source, original, db, config, _ = setup
    real_commit = db.commit
    commits = 0
    def commit_then_lose_reply():
        nonlocal commits
        commits += 1
        real_commit()
        if commits == 2:  # first commit is the read-only verified backup
            raise pymysql.err.OperationalError(2013, "PRIVATE_CONNECTION_TEXT")
    db.commit = commit_then_lose_reply
    action = initializer.prepare if mode == "import" else initializer.reset_data
    with pytest.raises(initializer.SetupError, match="최종 저장 결과를 확인할 수 없습니다") as error:
        action(config, folder=tmp_path, confirm=lambda _: True)
    assert "PRIVATE_CONNECTION_TEXT" not in str(error.value)
    record = json.loads(safety.journal_path(tmp_path, config).read_text(encoding="utf-8"))
    assert record["status"] == "outcome_unknown"
    assert initializer.read_dataset(Path(record["backup"])) == original
    assert commits == 2
    if mode == "import":
        assert db.rows == initializer.read_dataset(source)
        assert len([sql for sql in destructive(db) if sql.startswith("DROP")]) == 13
    else:
        assert all(not rows for rows in db.rows.values())
    with pytest.raises(initializer.SetupError, match="이전 DB 작업"):
        action(config, folder=tmp_path)


def test_rollback_failure_keeps_recovery_record(initializer, safety, setup, tmp_path):
    _, original, db, config, _ = setup
    db.fail_delete = "equipment_group"
    def fail_rollback():
        raise pymysql.err.OperationalError(2013, "simulated lost rollback response")
    db.rollback = fail_rollback
    with pytest.raises(initializer.SetupError, match="結果|결과"):
        initializer.reset_data(config, folder=tmp_path, confirm=lambda _: True)
    record = json.loads(safety.journal_path(tmp_path, config).read_text(encoding="utf-8"))
    assert record["status"] == "outcome_unknown"
    assert initializer.read_dataset(Path(record["backup"])) == original


def test_finite_network_and_lock_waits_are_applied(initializer, setup, tmp_path):
    _, _, db, config, connections = setup
    initializer.prepare(config, folder=tmp_path, confirm=lambda _: False)
    assert connections[0]["read_timeout"] == initializer.QUERY_WAIT_SECONDS
    assert connections[0]["write_timeout"] == initializer.QUERY_WAIT_SECONDS
    assert ("SET SESSION lock_wait_timeout = %s", (initializer.QUERY_WAIT_SECONDS,)) in db.log
    assert ("SET SESSION innodb_lock_wait_timeout = %s", (initializer.QUERY_WAIT_SECONDS,)) in db.log


@pytest.mark.parametrize("missing", [False, True], ids=["empty-database", "new-database"])
def test_startup_reports_actual_target_once_and_environment_override_without_password(
        initializer, setup, tmp_path, monkeypatch, missing):
    _, _, db, config, _ = setup
    db.objects = {}
    db.missing = missing
    monkeypatch.setattr(initializer, "load_config", lambda: config)
    monkeypatch.setattr(initializer, "program_folder", lambda: tmp_path)
    monkeypatch.setenv("SETRON_DB_NAME", config.database)
    progress = []
    status, _ = initializer.run(progress=progress.append)
    assert status == 0
    targets = [value for value in progress if value.startswith("대상 DB:")]
    assert len(targets) == 1 and config.database in targets[0] and str(config.port) in targets[0]
    assert any("환경변수 우선" in value for value in progress)
    assert all(config.password not in value for value in progress)


def test_invalid_setting_keeps_the_exact_field_name(initializer, monkeypatch):
    def invalid():
        raise initializer.DatabaseConfigError("SETRON_DB_PORT는 정수여야 합니다.")
    monkeypatch.setattr(initializer, "load_config", invalid)
    status, message = initializer.run()
    assert status == 1 and "SETRON_DB_PORT는 정수" in message
    assert "따옴표" in message
