"""Docker 시연 자료 적용의 대상 격리·확인·백업을 실제 DB 접속 없이 검사한다."""

from copy import deepcopy
from hashlib import sha256
import importlib
from pathlib import Path
import subprocess
import sys

import pymysql
import pytest

from setron.features.db_import.dump import DumpFormatError
from setron.features.storage.validation import TABLE_NAMES
from test_init_demo_dump import FakeConnection, FakeCursor, dataset, destructive


DATABASE = Path(__file__).resolve().parents[1] / "database"


@pytest.fixture(scope="module")
def initializer():
    sys.path.insert(0, str(DATABASE))
    try:
        return importlib.import_module("init_docker_demo_dump")
    finally:
        sys.path.remove(str(DATABASE))


class DockerRunner:
    """호스트 명령에 자격 증명을 넣지 않는 Docker CLI 응답 대역."""

    def __init__(self):
        self.calls = []
        self.running = b"true\n"
        self.hostname = b"docker-test-host\n"
        self.ports = b"127.0.0.1:13306\n"
        self.values = {"MYSQL_DATABASE": b"setron_dev", "MYSQL_USER": b"docker_fixture_account",
                       "MYSQL_PASSWORD": b" fixture-only-private-value "}
        self.fail = None
        self.stderr = b"fixture diagnostic which must not be displayed"

    def __call__(self, command, **kwargs):
        self.calls.append(tuple(command))
        action = command[1]
        variable = command[-1] if action == "exec" else None
        if self.fail in {action, variable} and self.fail is not None:
            return subprocess.CompletedProcess(command, 1, b"", self.stderr)
        if action == "inspect":
            output = self.hostname if "{{.Config.Hostname}}" in command else self.running
        elif action == "port":
            output = self.ports
        elif action == "exec" and variable in self.values:
            output = self.values[variable] + b"\n"
        else:
            return subprocess.CompletedProcess(command, 1, b"", self.stderr)
        return subprocess.CompletedProcess(command, 0, output, b"")


class SummaryCursor(FakeCursor):
    def execute(self, sql, params=None):
        if sql == "SELECT @@hostname":
            self.connection.log.append((sql, params))
            self.result = [(self.connection.hostname,)]
        elif sql.startswith("SELECT COUNT(DISTINCT `equipment_id`), COUNT(*) FROM `equipment_snapshot`"):
            self.connection.log.append((sql, params))
            rows = [row for row in self.connection.rows["equipment_snapshot"]
                    if (row["site"], row["line"]) == tuple(params)]
            self.result = [(len({row["equipment_id"] for row in rows}), len(rows))]
        else:
            super().execute(sql, params)


@pytest.fixture
def setup(initializer, tmp_path, monkeypatch):
    source = dataset(tmp_path / "SETRON_DB.sql")
    old = initializer.shared.read_dataset(dataset(tmp_path / "old.sql", code="OLD Docker😀'\\\n".encode()))
    db = FakeConnection(old, platform="Linux")
    db.hostname = "docker-test-host"
    db._cursor = SummaryCursor(db)
    runner = DockerRunner()
    connections = []

    def connect(**kwargs):
        connections.append(kwargs)
        return db

    monkeypatch.setattr(initializer.pymysql, "connect", connect)
    return source, old, db, runner, connections


def test_target_uses_only_verified_container_port_and_private_environment(initializer):
    runner = DockerRunner()
    target = initializer.docker_target(runner=runner)
    config = target.config
    assert target.container == "setron-mysql-dev-1"
    assert (config.host, config.port, config.database) == ("127.0.0.1", 13306, "setron_dev")
    assert sha256(config.password.encode()).digest() == sha256(runner.values["MYSQL_PASSWORD"]).digest()
    assert all(runner.values["MYSQL_PASSWORD"].decode() not in part for call in runner.calls for part in call)
    assert {call[-1] for call in runner.calls if call[1] == "exec"} == set(runner.values)
    assert all(call[:2] == ("docker", "exec") and "printenv" in call
               for call in runner.calls if call[-1] in runner.values)


@pytest.mark.parametrize("ports,host", [
    (b"[::1]:13306\n", "::1"),
    (b"127.0.0.1:13306\n[::1]:13306\n", "127.0.0.1"),
])
def test_verified_loopback_port_can_use_ipv6_and_shared_ipv4_mapping(initializer, ports, host):
    runner = DockerRunner()
    runner.ports = ports
    target = initializer.docker_target(runner=runner)
    assert (target.config.host, target.config.port) == (host, 13306)


@pytest.mark.parametrize("ports", [
    b"0.0.0.0:13306\n", b"[::]:13306\n", b"192.168.10.1:13306\n", b"localhost:13306\n",
    b"127.0.0.1:0\n", b"127.0.0.1:65536\n", b"127.0.0.1:not-a-port\n", b"",
    b"127.0.0.1:13306\n[::1]:13307\n",
])
def test_unbound_external_or_ambiguous_published_ports_are_rejected(initializer, ports):
    runner = DockerRunner()
    runner.ports = ports
    with pytest.raises(ValueError):
        initializer.docker_target(runner=runner)


@pytest.mark.parametrize("container", ["", "--other", "name; echo unsafe", "a/b", "x" * 129])
def test_invalid_container_names_are_rejected_before_cli_execution(initializer, container):
    runner = DockerRunner()
    with pytest.raises(ValueError):
        initializer.docker_target(container, runner=runner)
    assert not runner.calls


@pytest.mark.parametrize("failure", ["inspect", "port", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD"])
def test_docker_command_failures_hide_captured_diagnostics(initializer, failure):
    runner = DockerRunner()
    runner.fail = failure
    with pytest.raises(ValueError) as error:
        initializer.docker_target(runner=runner)
    assert runner.stderr.decode() not in str(error.value)
    assert runner.values["MYSQL_PASSWORD"].decode() not in str(error.value)


def test_stopped_container_is_not_started_automatically(initializer):
    runner = DockerRunner()
    runner.running = b"false\n"
    with pytest.raises(ValueError):
        initializer.docker_target(runner=runner)
    assert all(call[1] != "start" for call in runner.calls)


@pytest.mark.parametrize("variable,value", [
    ("MYSQL_DATABASE", b""), ("MYSQL_DATABASE", b"mysql"), ("MYSQL_DATABASE", b"information_schema"),
    ("MYSQL_DATABASE", b"performance_schema"), ("MYSQL_DATABASE", b"sys"), ("MYSQL_USER", b""),
])
def test_missing_target_names_or_system_databases_are_rejected(initializer, variable, value):
    runner = DockerRunner()
    runner.values[variable] = value
    with pytest.raises(ValueError):
        initializer.docker_target(runner=runner)


def test_external_invalid_sql_is_rejected_before_docker_or_database(initializer, setup, tmp_path):
    source, _, db, runner, connections = setup
    source.write_text("DROP DATABASE Windows;", encoding="utf-8")
    with pytest.raises((ValueError, DumpFormatError)):
        initializer.prepare(folder=tmp_path, runner=runner, confirm=lambda _: True)
    assert not runner.calls and not connections and not db.log


@pytest.mark.parametrize("confirm", [None, lambda _: False])
def test_cancelled_or_unconfirmed_replacement_keeps_all_data(initializer, setup, tmp_path, confirm):
    _, old, db, runner, connections = setup
    result = initializer.prepare(folder=tmp_path, runner=runner, confirm=confirm)
    assert not result.applied and db.rows == old
    assert not destructive(db) and not (tmp_path / "backups").exists()
    assert db.selected == ["setron_dev"]
    assert connections[0]["host"] == "127.0.0.1" and connections[0]["port"] == 13306
    assert any(sql.startswith("SELECT RELEASE_LOCK") for sql, _ in db.log)


def test_windows_server_at_published_port_is_rejected_before_database_selection(initializer, setup, tmp_path):
    _, old, db, runner, _ = setup
    db.platform = "Win64"
    with pytest.raises(ValueError):
        initializer.prepare(folder=tmp_path, runner=runner, confirm=lambda _: True)
    assert db.rows == old and not destructive(db) and not db.selected


@pytest.mark.parametrize("problem", ["partial", "extra", "view", "trigger", "routine", "event", "cross-db", "lock", "missing-db", "hostname"])
def test_wrong_database_objects_or_guards_abort_before_any_write(initializer, setup, tmp_path, problem):
    _, old, db, runner, _ = setup
    if problem == "partial":
        db.objects.pop("app_setting")
    elif problem == "extra":
        db.objects["unrelated"] = "BASE TABLE"
    elif problem == "view":
        db.objects["equipment"] = "VIEW"
    elif problem in {"trigger", "routine", "event", "cross-db"}:
        db.unsupported.add({"trigger": "TRIGGERS", "routine": "ROUTINES", "event": "EVENTS", "cross-db": "KEY_COLUMN_USAGE"}[problem])
    elif problem == "lock":
        db.lock = 0
    elif problem == "missing-db":
        db.missing = True
    elif problem == "hostname":
        db.hostname = "different-container-host"
    with pytest.raises((ValueError, pymysql.MySQLError)):
        initializer.prepare(folder=tmp_path, runner=runner, confirm=lambda _: pytest.fail("guard must precede confirmation"))
    assert db.rows == old and not destructive(db)
    assert not any(sql.startswith("CREATE DATABASE") for sql, _ in db.log)
    assert not (tmp_path / "backups").exists()


def test_backup_failure_never_deletes_existing_data(initializer, setup, tmp_path, monkeypatch):
    _, old, db, runner, _ = setup
    def fail(*args):
        raise OSError("fixture backup failure")
    monkeypatch.setattr(initializer.shared, "_backup", fail)
    with pytest.raises(ValueError):
        initializer.prepare(folder=tmp_path, runner=runner, confirm=lambda _: True)
    assert db.rows == old and not destructive(db)


def test_approved_import_has_valid_backup_before_drop_and_ignores_windows_env(initializer, setup, tmp_path, monkeypatch):
    source, old, db, runner, connections = setup
    (tmp_path / "setron.env").write_text("SETRON_DB_HOST=127.0.0.1\nSETRON_DB_PORT=3306\nSETRON_DB_NAME=setron\n", encoding="utf-8")
    monkeypatch.setattr(initializer.shared, "load_config", lambda *args: pytest.fail("Docker tool must never load Windows settings"))
    confirmations = []
    def guard():
        files = list((tmp_path / "backups").glob("*.sql"))
        assert len(files) == 1 and initializer.shared.read_dataset(files[0]) == old
    db.backup_guard = guard
    result = initializer.prepare(folder=tmp_path, runner=runner, confirm=lambda message: confirmations.append(message) or True)
    assert result.applied and result.backup.is_file()
    assert initializer.shared.read_dataset(result.backup) == old
    assert db.rows == initializer.shared.read_dataset(source)
    assert "13306" in confirmations[0] and "setron_dev" in confirmations[0]
    assert runner.values["MYSQL_USER"].decode() not in confirmations[0]
    assert runner.values["MYSQL_PASSWORD"].decode() not in confirmations[0]
    assert connections[0]["port"] != 3306 and db.selected == ["setron_dev"]
    drops = [sql for sql, _ in db.log if sql.startswith("DROP TABLE")]
    assert drops == [f"DROP TABLE IF EXISTS `{table}`" for table in reversed(TABLE_NAMES)]
    assert set(db.objects) == set(TABLE_NAMES)


def test_import_failure_restores_original_rows_and_retains_backup(initializer, setup, tmp_path):
    _, old, db, runner, _ = setup
    db.fail_create = 1
    with pytest.raises(ValueError) as error:
        initializer.prepare(folder=tmp_path, runner=runner, confirm=lambda _: True)
    assert db.rows == old and set(db.objects) == set(TABLE_NAMES)
    files = list((tmp_path / "backups").glob("*.sql"))
    assert len(files) == 1 and initializer.shared.read_dataset(files[0]) == old
    assert str(files[0]) in str(error.value)


def test_failed_automatic_restore_still_reports_readable_backup(initializer, setup, tmp_path):
    _, old, db, runner, _ = setup
    db.fail_create = 2
    with pytest.raises(ValueError) as error:
        initializer.prepare(folder=tmp_path, runner=runner, confirm=lambda _: True)
    files = list((tmp_path / "backups").glob("*.sql"))
    assert len(files) == 1 and initializer.shared.read_dataset(files[0]) == old
    assert str(files[0]) in str(error.value)


def test_validate_mode_never_calls_docker_connect_or_windows_configuration(initializer, setup, tmp_path, monkeypatch, capsys):
    _, _, db, _, connections = setup
    monkeypatch.setattr(initializer.shared, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer, "docker_target", lambda *args, **kwargs: pytest.fail("validation must not inspect Docker"))
    monkeypatch.setattr(initializer.shared, "load_config", lambda *args: pytest.fail("validation must not read Windows settings"))
    assert initializer.main(["--validate-dump"]) == 0
    assert not connections and not db.log
    assert "1" in capsys.readouterr().out


def test_quiet_never_approves_or_exposes_private_environment(initializer, setup, tmp_path, monkeypatch, capsys):
    _, old, db, runner, connections = setup
    monkeypatch.setattr(initializer.shared, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer.subprocess, "run", runner)
    assert initializer.main(["--quiet"]) == 3
    assert db.rows == old and not destructive(db)
    captured = capsys.readouterr().out
    assert runner.values["MYSQL_USER"].decode() not in captured
    assert runner.values["MYSQL_PASSWORD"].decode() not in captured
    assert connections[0]["port"] == 13306


def test_custom_container_option_is_forwarded_without_shell_interpolation(initializer, setup, tmp_path, monkeypatch):
    _, old, db, runner, _ = setup
    monkeypatch.setattr(initializer.shared, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer.subprocess, "run", runner)
    assert initializer.main(["--container", "chosen-docker-mysql", "--quiet"]) == 3
    assert db.rows == old and not destructive(db)
    assert runner.calls and all("chosen-docker-mysql" in call for call in runner.calls)


def test_console_confirmation_defaults_to_no(initializer, setup, tmp_path, monkeypatch, capsys):
    _, old, db, runner, _ = setup
    monkeypatch.setattr(initializer.shared, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer.subprocess, "run", runner)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert initializer.main(["--import-demo"]) == 3
    assert db.rows == old and not destructive(db)
    captured = capsys.readouterr().out
    assert runner.values["MYSQL_USER"].decode() not in captured
    assert runner.values["MYSQL_PASSWORD"].decode() not in captured


def test_unknown_cli_options_never_read_docker_or_connect(initializer, setup):
    _, _, db, runner, connections = setup
    with pytest.raises(SystemExit) as error:
        initializer.main(["--unknown"])
    assert error.value.code == 2
    assert not runner.calls and not connections and not db.log


def test_line_summary_counts_unique_equipment_and_preserves_site_scope(initializer):
    snapshots = (
        {"equipment_id": 1, "site": "평택", "line": "P1F"},
        {"equipment_id": 1, "site": "평택", "line": "P1F"},
        {"equipment_id": 2, "site": "평택", "line": "P1F"},
        {"equipment_id": 3, "site": "화성", "line": "P1F"},
        {"equipment_id": 4, "site": "평택", "line": "P4H"},
    )
    rows = {table: () for table in TABLE_NAMES}
    rows["equipment"] = tuple({"equipment_id": number} for number in range(1, 5))
    rows["equipment_snapshot"] = snapshots
    db = FakeConnection(rows, platform="Linux")
    summary = initializer._query_summary(SummaryCursor(db))
    assert summary == initializer._source_summary(rows)
    assert summary["lines"]["P1F"] == {"equipment": 2, "observations": 3}
    assert summary["lines"]["P4H"] == {"equipment": 1, "observations": 1}


def test_reset_backs_up_before_delete_and_preserves_structure_without_dump(initializer, setup, tmp_path, monkeypatch):
    _, old, db, runner, _ = setup
    objects, columns = deepcopy(db.objects), deepcopy(db.columns)
    monkeypatch.setattr(initializer.shared, "dump_path", lambda *args: pytest.fail("reset must not read demo SQL"))
    monkeypatch.setattr(initializer.shared, "load_config", lambda *args: pytest.fail("must not use Windows settings"))
    messages = []

    def guard():
        files = list((tmp_path / "backups").glob("*.sql"))
        assert len(files) == 1 and initializer.shared.read_dataset(files[0]) == old

    db.backup_guard = guard
    result = initializer.reset_data(folder=tmp_path, runner=runner,
                                    confirm=lambda message: messages.append(message) or True)
    assert result.applied and result.summary == {"tables": 13, "rows": 0}
    assert initializer.shared.read_dataset(result.backup) == old
    assert db.objects == objects and db.columns == columns
    assert all(not rows for rows in db.rows.values())
    assert destructive(db) == [f"DELETE FROM `{table}`" for table in reversed(TABLE_NAMES)]
    assert db.foreign_key_checks == 1 and db.transaction_rows is None
    assert db.selected == ["setron_dev"]
    assert "13306" in messages[0]
    assert "테이블 구조를 유지" in messages[0] and str(tmp_path / "backups") in messages[0]
    assert runner.values["MYSQL_PASSWORD"].decode() not in messages[0]
    assert any(sql.startswith("SELECT RELEASE_LOCK") for sql, _ in db.log)


@pytest.mark.parametrize("confirm", [None, lambda _: False])
def test_reset_cancel_has_no_backup_or_data_changes(initializer, setup, tmp_path, confirm):
    _, old, db, runner, _ = setup
    assert not initializer.reset_data(folder=tmp_path, runner=runner, confirm=confirm).applied
    assert db.rows == old and not destructive(db)
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("problem", ["empty", "partial", "extra", "view", "trigger", "engine", "columns", "windows", "hostname", "lock", "missing-db"])
def test_reset_rejects_unsupported_target_before_confirmation(initializer, setup, tmp_path, problem):
    _, old, db, runner, _ = setup
    if problem == "empty":
        db.objects.clear()
    elif problem == "partial":
        db.objects.pop("app_setting")
    elif problem == "extra":
        db.objects["unrelated"] = "BASE TABLE"
    elif problem == "view":
        db.objects["equipment"] = "VIEW"
    elif problem == "trigger":
        db.unsupported.add("TRIGGERS")
    elif problem == "engine":
        db.engines["equipment"] = "MyISAM"
    elif problem == "columns":
        db.columns["equipment"] = ("unknown_column",)
    elif problem == "windows":
        db.platform = "Win64"
    elif problem == "hostname":
        db.hostname = "different-container"
    elif problem == "lock":
        db.lock = 0
    elif problem == "missing-db":
        db.missing = True
    with pytest.raises((ValueError, pymysql.MySQLError)):
        initializer.reset_data(folder=tmp_path, runner=runner,
                               confirm=lambda _: pytest.fail("must verify target first"))
    assert db.rows == old and not destructive(db)
    assert not (tmp_path / "backups").exists()


def test_reset_backup_failure_never_deletes(initializer, setup, tmp_path, monkeypatch):
    _, old, db, runner, _ = setup

    def fail(*args):
        raise OSError("fixture backup failure")

    monkeypatch.setattr(initializer.shared, "_backup", fail)
    with pytest.raises(ValueError, match="백업"):
        initializer.reset_data(folder=tmp_path, runner=runner, confirm=lambda _: True)
    assert db.rows == old and not destructive(db)


@pytest.mark.parametrize("failure", ["delete", "verify"])
def test_failed_reset_rolls_back_rows_and_retains_verified_backup(initializer, setup, tmp_path, failure):
    _, old, db, runner, _ = setup
    if failure == "delete":
        db.fail_delete = "equipment"
    else:
        db.reset_bad_count = True
    with pytest.raises(ValueError) as error:
        initializer.reset_data(folder=tmp_path, runner=runner, confirm=lambda _: True)
    assert db.rows == old and db.foreign_key_checks == 1
    assert set(db.objects) == set(TABLE_NAMES)
    assert ("ROLLBACK", None) in db.log
    backup, = (tmp_path / "backups").glob("*.sql")
    assert initializer.shared.read_dataset(backup) == old and str(backup) in str(error.value)


@pytest.mark.parametrize("answer,code", [("", 3), ("N", 3), ("Y", 0)])
def test_reset_cli_uses_explicit_confirmation_and_custom_container(initializer, setup, tmp_path, monkeypatch, capsys, answer, code):
    _, old, db, runner, _ = setup
    monkeypatch.setattr(initializer.shared, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer.subprocess, "run", runner)
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    assert initializer.main(["--reset-data", "--container", "chosen-mysql"]) == code
    assert runner.calls and all("chosen-mysql" in call for call in runner.calls)
    if code == 0:
        assert all(not rows for rows in db.rows.values())
        assert "구조를 유지" in capsys.readouterr().out
    else:
        assert db.rows == old and not destructive(db)


def test_reset_quiet_is_cancelled_and_conflicting_modes_never_connect(initializer, setup, tmp_path, monkeypatch):
    _, old, db, runner, _ = setup
    monkeypatch.setattr(initializer.shared, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer.subprocess, "run", runner)
    with pytest.raises(SystemExit) as error:
        initializer.main(["--reset-data", "--import-demo"])
    assert error.value.code == 2 and not runner.calls and not db.log
    assert initializer.main(["--reset-data", "--quiet"]) == 3
    assert db.rows == old and not destructive(db)


def test_packager_normalizes_ascii_wrapper_to_crlf_without_connecting(initializer, setup, tmp_path, monkeypatch):
    source, _, _, _, connections = setup
    root = tmp_path / "builder-source"
    root.mkdir()
    wrapper = b"@echo off\nsetlocal DisableDelayedExpansion\necho sample\n"
    (root / "prepare_docker_demo_db.bat").write_bytes(wrapper)
    location = Path(__file__).resolve().parents[1] / "packaging" / "build_docker_demo_db.py"
    spec = importlib.util.spec_from_file_location("build_docker_demo_db_for_test", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "deployment"
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(sys, "argv", ["build_docker_demo_db.py", "--dump", str(source), "--output-dir", str(output)])
    commands = []

    def build(command, **kwargs):
        commands.append(command)
        dist = Path(command[command.index("--distpath") + 1])
        dist.mkdir()
        (dist / "SETRON_DOCKER_DB_INIT.exe").write_bytes(b"fixture-executable")

    monkeypatch.setattr(module.subprocess, "run", build)
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    module.main()
    assert (output / "prepare_docker_demo_db.bat").read_bytes() == wrapper.replace(b"\n", b"\r\n")
    assert (output / "SETRON_DB.sql").read_bytes() == source.read_bytes()
    assert {path.name for path in output.iterdir()} == {"SETRON_DOCKER_DB_INIT.exe", "prepare_docker_demo_db.bat", "SETRON_DB.sql"}
    assert "--console" in commands[0] and not connections
