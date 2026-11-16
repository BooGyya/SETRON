"""실제 CMD에서 Windows BAT가 EXE의 메뉴·입출력·종료 코드를 그대로 전달하는지 확인한다."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows BAT integration")


@pytest.fixture(scope="module")
def compiled_helper(tmp_path_factory):
    compiler = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.is_file():
        pytest.skip("C# fixture compiler unavailable")
    folder = tmp_path_factory.mktemp("windows_menu_fixture")
    source = folder / "Fixture.cs"
    source.write_text(
        """using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
class Fixture {
    static int Main(string[] args) {
        Console.InputEncoding = new UTF8Encoding(false);
        Console.OutputEncoding = new UTF8Encoding(false);
        var trace = new List<string>();
        trace.Add(Assembly.GetExecutingAssembly().Location);
        trace.Add(Environment.CurrentDirectory);
        trace.AddRange(args);
        File.WriteAllLines(Environment.GetEnvironmentVariable("WINDOWS_MENU_TRACE"), trace, new UTF8Encoding(false));
        Console.WriteLine("WINDOWS_MENU_FIXTURE");
        Console.WriteLine("1. 시연 데이터 넣기 / 2. 데이터 초기화 / 3. 종료");
        Console.Write("선택: ");
        string choice = Console.ReadLine();
        Console.WriteLine("선택값: " + (choice ?? "<EOF>"));
        int status;
        if (!Int32.TryParse(Environment.GetEnvironmentVariable("WINDOWS_MENU_STATUS"), out status))
            status = choice == "3" ? 0 : choice == "1" ? 1 : choice == "2" ? 3 : 2;
        Console.WriteLine("실행 결과: " + status);
        return status;
    }
}
""", encoding="utf-8-sig")
    exe = folder / "SETRON_DB_INIT.exe"
    subprocess.run([str(compiler), "/nologo", "/target:exe", "/codepage:65001", f"/out:{exe}", str(source)],
                   capture_output=True, check=True, timeout=30)
    return exe


@pytest.fixture
def menu(tmp_path):
    folder = tmp_path / "윈도우 배포 폴더"
    folder.mkdir()
    bat = folder / "prepare_demo_db.bat"
    shutil.copyfile(ROOT / bat.name, bat)
    return bat


@pytest.fixture
def helper(menu, compiled_helper):
    exe = menu.parent / "SETRON_DB_INIT.exe"
    shutil.copyfile(compiled_helper, exe)
    return exe


def invoke(program, tmp_path, *, answer="", arguments=(), status=None):
    cwd = tmp_path / "다른 실행 위치"
    cwd.mkdir(exist_ok=True)
    trace = tmp_path / ("bat_trace.txt" if program.suffix.lower() == ".bat" else "exe_trace.txt")
    env = dict(os.environ, WINDOWS_MENU_TRACE=str(trace))
    env.pop("WINDOWS_MENU_STATUS", None)
    if status is not None:
        env["WINDOWS_MENU_STATUS"] = str(status)
    if program.suffix.lower() == ".bat":
        # /s /c의 바깥 따옴표와 실행 경로·각 인자의 따옴표를 각각 보존한다.
        command = subprocess.list2cmdline([str(program), *arguments])
        command = f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c "{command}"'
    else:
        command = [str(program), *arguments]
    result = subprocess.run(command, input=answer.encode("utf-8"), cwd=cwd, env=env,
                            capture_output=True, timeout=15)
    recorded = trace.read_text(encoding="utf-8-sig").splitlines() if trace.exists() else None
    return result, recorded


@pytest.mark.parametrize("choice,expected", [("1", 1), ("2", 3), ("3", 0), ("잘못된 선택", 2)])
def test_bat_and_direct_exe_have_identical_menu_input_and_result(menu, helper, tmp_path, choice, expected):
    direct, direct_trace = invoke(helper, tmp_path, answer=choice + "\r\n")
    wrapped, wrapped_trace = invoke(menu, tmp_path, answer=choice + "\r\n")
    assert direct.returncode == wrapped.returncode == expected
    assert wrapped.stdout == direct.stdout
    assert wrapped.stderr == direct.stderr == b""
    assert wrapped.stdout.decode("utf-8").count("WINDOWS_MENU_FIXTURE") == 1
    assert direct_trace[2:] == wrapped_trace[2:] == []
    assert Path(wrapped_trace[0]) == helper
    assert Path(wrapped_trace[1]) == menu.parent


@pytest.mark.parametrize("status", [0, 1, 2, 3])
def test_bat_preserves_every_helper_exit_status_without_extra_output(menu, helper, tmp_path, status):
    direct, _ = invoke(helper, tmp_path, answer="3\r\n", status=status)
    wrapped, _ = invoke(menu, tmp_path, answer="3\r\n", status=status)
    assert wrapped.returncode == direct.returncode == status
    assert (wrapped.stdout, wrapped.stderr) == (direct.stdout, direct.stderr)


def test_explicit_arguments_and_quoted_korean_path_are_forwarded_unchanged(menu, helper, tmp_path):
    arguments = ("--validate-dump", "--config", str(tmp_path / "설정 자료" / "접속 설정.env"),
                 "--label", "두 단어", "--reset-data")
    direct, direct_trace = invoke(helper, tmp_path, answer="3\r\n", arguments=arguments)
    wrapped, wrapped_trace = invoke(menu, tmp_path, answer="3\r\n", arguments=arguments)
    assert wrapped.returncode == direct.returncode == 0
    assert wrapped_trace[2:] == direct_trace[2:] == list(arguments)
    assert (wrapped.stdout, wrapped.stderr) == (direct.stdout, direct.stderr)


def test_repository_dist_helper_takes_priority_over_beside_bat_copy(menu, helper, compiled_helper, tmp_path):
    nested = menu.parent / "dist" / "SETRON" / helper.name
    nested.parent.mkdir(parents=True)
    shutil.copyfile(compiled_helper, nested)
    result, trace = invoke(menu, tmp_path, answer="3\r\n")
    assert result.returncode == 0
    assert Path(trace[0]) == nested


def test_beside_bat_fallback_does_not_use_helper_from_caller_directory(menu, helper, compiled_helper, tmp_path):
    caller = tmp_path / "다른 실행 위치"
    caller.mkdir()
    shutil.copyfile(compiled_helper, caller / helper.name)
    result, trace = invoke(menu, tmp_path, answer="3\r\n")
    assert result.returncode == 0
    assert Path(trace[0]) == helper


def test_missing_helper_reports_one_distinct_diagnostic_and_never_runs_menu(menu, tmp_path):
    result, trace = invoke(menu, tmp_path, answer="\r\n")
    assert result.returncode == 1
    text = result.stdout.decode("utf-8")
    assert text.count("SETRON_DB_INIT.exe를 찾지 못했습니다.") == 1
    assert "WINDOWS_MENU_FIXTURE" not in text
    assert trace is None
    assert result.stderr == b""
