"""Docker EXE 기본 메뉴를 Docker 명령·실제 DB·사용자 키보드 접근 없이 검증한다."""

import builtins
from copy import deepcopy
import sys

import pytest

from setron.features.storage.validation import TABLE_NAMES
from test_init_demo_dump import FakeConnection, destructive
from test_init_docker_demo_dump import DockerRunner, initializer, setup  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_docker_entry(initializer, setup, tmp_path, monkeypatch):
    _, _, db, runner, _ = setup
    assert isinstance(db, FakeConnection) and isinstance(runner, DockerRunner)
    monkeypatch.setattr(initializer.shared, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer.subprocess, "run", runner)
    monkeypatch.setattr(initializer.shared, "load_config", lambda *args: pytest.fail("Docker 메뉴는 Windows 설정을 읽으면 안 됩니다."))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)


def answers(monkeypatch, values):
    pending, prompts = iter(values), []

    def read(prompt):
        prompts.append(prompt)
        value = next(pending)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(builtins, "input", read)
    return prompts


def assert_no_private_output(output, runner):
    assert runner.values["MYSQL_USER"].decode() not in output
    assert runner.values["MYSQL_PASSWORD"].decode() not in output
    assert runner.stderr.decode() not in output


@pytest.mark.parametrize("choice", ["1", "2"], ids=["import", "reset"])
def test_no_argument_menu_runs_selected_action_only_after_yes_and_verified_backup(
        initializer, setup, tmp_path, monkeypatch, capsys, choice):
    source, old, db, runner, connections = setup
    expected = initializer.shared.read_dataset(source)
    objects, columns = deepcopy(db.objects), deepcopy(db.columns)
    if choice == "2":
        source.unlink()
    prompts = answers(monkeypatch, [choice, "y", ""])
    checked = []

    def backup_before_mutation():
        backup, = (tmp_path / "backups").glob("*.sql")
        assert initializer.shared.read_dataset(backup) == old
        checked.append(backup)

    db.backup_guard = backup_before_mutation
    assert initializer.main([]) == 0
    assert len(prompts) == 3 and checked and runner.calls and connections
    assert prompts[:2] == ["선택 [1/2/3]: ", "선택 [Y/N]: "]
    backup, = (tmp_path / "backups").glob("*.sql")
    assert initializer.shared.read_dataset(backup) == old
    if choice == "1":
        assert db.rows == expected
    else:
        assert db.objects == objects and db.columns == columns and all(not rows for rows in db.rows.values())
        assert destructive(db) == [f"DELETE FROM `{table}`" for table in reversed(TABLE_NAMES)]
    output = capsys.readouterr().out
    assert all(token in output for token in ("1.", "2.", "3.", "setron_dev", "13306", "완료", "백업"))
    assert output.count("Docker 컨테이너:") == 1
    assert output.count("대상 DB:") == 1
    assert not any(token in output for token in ("선택한 작업:", "입력 자료:", "기본 컨테이너:", "대상은 실행 중인"))
    assert_no_private_output(output, runner)


@pytest.mark.parametrize("options", [[], ["--container", "chosen-docker-mysql"]], ids=["default", "custom"])
@pytest.mark.parametrize("value", ["3", EOFError(), KeyboardInterrupt()], ids=["exit", "EOF", "interrupt"])
def test_exit_or_closed_menu_input_never_inspects_docker_or_connects(
        initializer, setup, monkeypatch, options, value):
    _, old, db, runner, connections = setup
    prompts = answers(monkeypatch, [value])
    assert initializer.main(options) == 0
    assert len(prompts) == 1 and not runner.calls and not connections and not db.log and db.rows == old


def test_invalid_menu_choice_reprompts_without_accessing_docker(initializer, setup, monkeypatch):
    _, old, db, runner, connections = setup
    prompts = answers(monkeypatch, ["9", "unknown", "", "3"])
    assert initializer.main([]) == 0
    assert len(prompts) == 4 and not runner.calls and not connections and not db.log and db.rows == old


def test_first_insert_reports_target_once_without_asking_for_overwrite(
        initializer, setup, monkeypatch, capsys):
    source, _, db, runner, _ = setup
    db.objects.clear()
    db.rows = {table: () for table in TABLE_NAMES}
    prompts = answers(monkeypatch, ["1", ""])
    assert initializer.main([]) == 0
    assert len(prompts) == 2 and "선택 [Y/N]: " not in prompts
    assert db.rows == initializer.shared.read_dataset(source)
    output = capsys.readouterr().out
    assert output.count("Docker 컨테이너:") == output.count("대상 DB:") == output.count("백업 위치:") == 1
    assert "setron_dev" in output and "13306" in output
    assert_no_private_output(output, runner)


def test_container_only_option_opens_menu_and_forwards_the_chosen_container(
        initializer, setup, monkeypatch, capsys):
    source, _, db, runner, _ = setup
    expected = initializer.shared.read_dataset(source)
    prompts = answers(monkeypatch, ["1", "yes", ""])
    assert initializer.main(["--container", "chosen-docker-mysql"]) == 0
    assert len(prompts) == 3 and db.rows == expected
    assert runner.calls and all("chosen-docker-mysql" in call for call in runner.calls)
    output = capsys.readouterr().out
    assert "chosen-docker-mysql" in output
    assert_no_private_output(output, runner)


@pytest.mark.parametrize("choice", ["1", "2"], ids=["import", "reset"])
@pytest.mark.parametrize("value", ["n", EOFError()], ids=["no", "EOF"])
def test_declined_action_keeps_rows_and_does_not_create_a_backup(
        initializer, setup, tmp_path, monkeypatch, choice, value):
    _, old, db, _, _ = setup
    prompts = answers(monkeypatch, [choice, value, ""])
    assert initializer.main([]) == 3
    assert len(prompts) == 3 and db.rows == old and not destructive(db)
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("failure", [EOFError(), KeyboardInterrupt()], ids=["EOF", "interrupt"])
def test_completed_action_closes_safely_when_final_input_is_unavailable(initializer, setup, monkeypatch, failure):
    source, _, db, _, _ = setup
    expected = initializer.shared.read_dataset(source)
    prompts = answers(monkeypatch, ["1", "yes", failure])
    assert initializer.main([]) == 0
    assert len(prompts) == 3 and db.rows == expected


@pytest.mark.parametrize("options", [["--quiet"], ["--import-demo", "--quiet"], ["--reset-data", "--quiet"]])
def test_quiet_keeps_noninteractive_no_approval_behavior(initializer, setup, monkeypatch, capsys, options):
    _, old, db, runner, _ = setup
    monkeypatch.setattr(builtins, "input", lambda _: pytest.fail("quiet는 메뉴나 확인을 물으면 안 됩니다."))
    assert initializer.main(options) == 3
    assert db.rows == old and not destructive(db)
    assert_no_private_output(capsys.readouterr().out, runner)


def test_explicit_validation_keeps_noninteractive_no_docker_behavior(initializer, setup, monkeypatch):
    _, old, db, runner, connections = setup
    monkeypatch.setattr(builtins, "input", lambda _: pytest.fail("SQL 검증은 메뉴를 열면 안 됩니다."))
    assert initializer.main(["--validate-dump", "--container", "chosen-docker-mysql"]) == 0
    assert not runner.calls and not connections and not db.log and db.rows == old
