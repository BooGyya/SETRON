"""Windows 배치의 콘솔 승인·취소 경로를 Tk/실제 DB 없이 검증한다."""

import builtins
from copy import deepcopy
import sys

import pytest

from setron.features.storage.validation import TABLE_NAMES
from test_init_demo_dump import FakeConnection, destructive, initializer, setup  # noqa: F401


@pytest.fixture(autouse=True)
def console_without_tk_or_keyboard(initializer, monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "tkinter" or name.startswith("tkinter."):
            pytest.fail("--console 경로는 tkinter를 import하면 안 됩니다.")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(initializer, "_discard_pending_console_input", lambda: None)


def answer(monkeypatch, value):
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(builtins, "input", read)
    return prompts


def scripted_answers(monkeypatch, values):
    pending, prompts = iter(values), []

    def read(prompt):
        prompts.append(prompt)
        value = next(pending)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(builtins, "input", read)
    return prompts


@pytest.mark.parametrize("value,approved", [
    ("", False), ("n", False), ("N", False), ("no", False), ("unknown", False), ("yep", False),
    ("y", True), ("yes", True), (" YES ", True), ("예", True),
])
def test_console_confirmation_requires_an_explicit_yes(initializer, monkeypatch, capsys, value, approved):
    prompts = answer(monkeypatch, value)
    assert initializer._console_confirm("대상: 시험 DB") is approved
    assert "대상: 시험 DB" in capsys.readouterr().out
    assert len(prompts) == 1 and "기본 N" in prompts[0]


@pytest.mark.parametrize("failure", [EOFError(), KeyboardInterrupt()], ids=["EOF", "interrupt"])
def test_console_confirmation_cancels_when_input_closes(initializer, monkeypatch, failure):
    answer(monkeypatch, failure)
    assert initializer._console_confirm("대상: 시험 DB") is False


def configure_main(initializer, monkeypatch, tmp_path, config, mode):
    monkeypatch.setattr(initializer, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer, "load_config", lambda: config)
    monkeypatch.setattr(sys, "argv", ["SETRON_DB_INIT.exe", mode, "--console"])


@pytest.mark.parametrize("mode", ["--import-demo", "--reset-data"])
def test_main_console_yes_preserves_a_verified_backup_before_replacement_or_reset(
        initializer, setup, tmp_path, monkeypatch, capsys, mode):
    source, old, db, config, connections = setup
    assert isinstance(db, FakeConnection)
    expected = initializer.read_dataset(source)
    objects = deepcopy(db.objects)
    if mode == "--reset-data":
        source.unlink()  # 초기화는 시연 SQL이 없어도 실행한다.
    configure_main(initializer, monkeypatch, tmp_path, config, mode)
    answer(monkeypatch, "yes")
    checked = []

    def backup_before_mutation():
        files = list((tmp_path / "backups").glob("*.sql"))
        assert len(files) == 1 and initializer.read_dataset(files[0]) == old
        checked.append(files[0])

    db.backup_guard = backup_before_mutation
    assert initializer.main() == 0
    assert checked and connections
    backup, = (tmp_path / "backups").glob("*.sql")
    assert initializer.read_dataset(backup) == old
    if mode == "--import-demo":
        assert db.rows == expected and destructive(db)
    else:
        assert db.objects == objects and all(not rows for rows in db.rows.values())
        assert destructive(db) == [f"DELETE FROM `{table}`" for table in reversed(TABLE_NAMES)]
    output = capsys.readouterr().out
    assert f"{config.host}:{config.port}" in output and config.database in output
    assert "완료" in output and "검증:" in output and "백업" in output
    assert config.password not in output


@pytest.mark.parametrize("mode", ["--import-demo", "--reset-data"])
@pytest.mark.parametrize("value", ["n", EOFError()], ids=["no", "EOF"])
def test_main_console_no_or_eof_never_backs_up_or_changes_rows(
        initializer, setup, tmp_path, monkeypatch, capsys, mode, value):
    _, old, db, config, _ = setup
    configure_main(initializer, monkeypatch, tmp_path, config, mode)
    answer(monkeypatch, value)
    assert initializer.main() == 3
    assert db.rows == old and not destructive(db)
    assert not (tmp_path / "backups").exists()
    output = capsys.readouterr().out
    assert config.database in output and "취소" in output
    assert config.password not in output


@pytest.mark.parametrize("mode", [None, "--import-demo", "--reset-data"])
def test_quiet_and_console_are_rejected_before_config_or_database(
        initializer, setup, monkeypatch, mode):
    _, old, db, _, connections = setup
    monkeypatch.setattr(initializer, "load_config", lambda: pytest.fail("잘못된 옵션에서 설정을 읽으면 안 됩니다."))
    monkeypatch.setattr(builtins, "input", lambda _: pytest.fail("잘못된 옵션에서 확인을 물으면 안 됩니다."))
    options = [mode] if mode else []
    monkeypatch.setattr(sys, "argv", ["SETRON_DB_INIT.exe", *options, "--quiet", "--console"])
    assert initializer.main() == 2
    assert not connections and not db.log and db.rows == old


@pytest.mark.parametrize("options", [[], ["--console"]], ids=["no-arguments", "console-only"])
@pytest.mark.parametrize("choice", ["1", "2"], ids=["import", "reset"])
def test_default_exe_menu_runs_the_selected_action_after_explicit_approval(
        initializer, setup, tmp_path, monkeypatch, capsys, options, choice):
    source, old, db, config, connections = setup
    expected = initializer.read_dataset(source)
    objects = deepcopy(db.objects)
    if choice == "2":
        source.unlink()
    monkeypatch.setattr(initializer, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer, "load_config", lambda: config)
    monkeypatch.setattr(sys, "argv", ["SETRON_DB_INIT.exe", *options])
    prompts = scripted_answers(monkeypatch, [choice, "y", ""])
    checked = []

    def backup_before_mutation():
        backup, = (tmp_path / "backups").glob("*.sql")
        assert initializer.read_dataset(backup) == old
        checked.append(backup)

    db.backup_guard = backup_before_mutation
    assert initializer.main() == 0
    assert len(prompts) == 3 and checked and connections
    backup, = (tmp_path / "backups").glob("*.sql")
    assert initializer.read_dataset(backup) == old
    if choice == "1":
        assert db.rows == expected
    else:
        assert db.objects == objects and all(not rows for rows in db.rows.values())
        assert destructive(db) == [f"DELETE FROM `{table}`" for table in reversed(TABLE_NAMES)]
    output = capsys.readouterr().out
    assert all(token in output for token in ("1.", "2.", "3.", config.database, "완료", "검증:"))
    assert "SETRON DB 관리 · Windows MySQL\n설정: 실행 파일 옆 setron.env" in output
    assert "1. 시연 데이터 넣기\n2. 데이터 초기화 (모든 데이터 삭제 · 테이블 유지)\n3. 종료" in output
    assert config.password not in output


@pytest.mark.parametrize("options", [[], ["--console"]], ids=["no-arguments", "console-only"])
@pytest.mark.parametrize("value", ["3", EOFError(), KeyboardInterrupt()], ids=["exit", "EOF", "interrupt"])
def test_default_menu_exit_or_closed_input_never_reads_config_or_connects(
        initializer, setup, monkeypatch, options, value):
    _, old, db, _, connections = setup
    monkeypatch.setattr(initializer, "load_config", lambda: pytest.fail("메뉴 종료 전에 설정을 읽으면 안 됩니다."))
    monkeypatch.setattr(sys, "argv", ["SETRON_DB_INIT.exe", *options])
    prompts = scripted_answers(monkeypatch, [value])
    assert initializer.main() == 0
    assert len(prompts) == 1 and not connections and not db.log and db.rows == old


def test_default_menu_invalid_choice_reprompts_without_touching_database(initializer, setup, monkeypatch):
    _, old, db, _, connections = setup
    monkeypatch.setattr(initializer, "load_config", lambda: pytest.fail("올바른 작업 선택 전에는 설정을 읽으면 안 됩니다."))
    monkeypatch.setattr(sys, "argv", ["SETRON_DB_INIT.exe"])
    prompts = scripted_answers(monkeypatch, ["9", "unknown", "", "3"])
    assert initializer.main() == 0
    assert len(prompts) == 4 and not connections and not db.log and db.rows == old


@pytest.mark.parametrize("failure", [EOFError(), KeyboardInterrupt()], ids=["EOF", "interrupt"])
def test_completed_menu_action_closes_safely_when_the_final_input_is_unavailable(
        initializer, setup, tmp_path, monkeypatch, failure):
    source, old, db, config, _ = setup
    expected = initializer.read_dataset(source)
    monkeypatch.setattr(initializer, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer, "load_config", lambda: config)
    monkeypatch.setattr(sys, "argv", ["SETRON_DB_INIT.exe"])
    prompts = scripted_answers(monkeypatch, ["1", "yes", failure])
    assert initializer.main() == 0
    assert len(prompts) == 3 and db.rows == expected
    backup, = (tmp_path / "backups").glob("*.sql")
    assert initializer.read_dataset(backup) == old


def test_explicit_validation_with_console_keeps_its_noninteractive_no_database_path(
        initializer, setup, tmp_path, monkeypatch):
    _, old, db, _, connections = setup
    monkeypatch.setattr(initializer, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer, "load_config", lambda: pytest.fail("SQL 검증은 설정을 읽으면 안 됩니다."))
    monkeypatch.setattr(builtins, "input", lambda _: pytest.fail("SQL 검증은 메뉴나 확인을 물으면 안 됩니다."))
    monkeypatch.setattr(sys, "argv", ["SETRON_DB_INIT.exe", "--validate-dump", "--console"])
    assert initializer.main() == 0
    assert not connections and not db.log and db.rows == old
