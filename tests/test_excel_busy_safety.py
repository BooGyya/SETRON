"""실제 설치된 COM 재시도 루프를 유한하게 제한하고 읽기 전 크기를 검사한다."""

import ast
from concurrent.futures import ThreadPoolExecutor
from importlib.util import find_spec
from pathlib import Path
from threading import Event
import sys
import types
from types import SimpleNamespace
import warnings

import pytest

from setron.infrastructure import excel_reader
from setron.infrastructure.excel_reader import ExcelBusyError, ExcelSheetTooLargeError
from setron.ui import import_tasks
from test_excel_reader import FakeUsedRange, fake_book, use_fake_excel


class BusyComError(Exception):
    hresult = -2147418111


@pytest.fixture
def com_backend(monkeypatch):
    # 패키지를 import하면 등록되는 전역 Excel 정리는 실행하지 않는다. 설치된
    # 실제 재시도 클래스만 독립 네임스페이스에서 읽어 busy 응답을 재현한다.
    specification = find_spec("xlwings")
    if specification is None:
        pytest.skip("설치된 xlwings가 필요합니다.")
    path = Path(specification.origin).parent / "_xlwindows.py"
    source = ast.parse(path.read_text(encoding="utf-8"))
    definition = next(node for node in source.body
                      if isinstance(node, ast.ClassDef) and node.name == "COMRetryMethodWrapper")
    backend = types.ModuleType("isolated_xlwings_retry")
    backend.__dict__.update(N_COM_ATTEMPTS=0, types=types,
        pywintypes=SimpleNamespace(com_error=BusyComError),
        CDispatch=type("CDispatch", (), {}), CoClassBaseClass=type("CoClassBaseClass", (), {}),
        DispatchBaseClass=type("DispatchBaseClass", (), {}))
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), "exec"), backend.__dict__)
    calls = []
    monkeypatch.setattr(import_tasks, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(
        CoInitialize=lambda: calls.append("init"), CoUninitialize=lambda: calls.append("uninit")))
    monkeypatch.setattr(import_tasks, "_com_backend", lambda: backend)
    return backend, calls


def test_busy_com_retry_finishes_with_actionable_error_and_restores_configuration(com_backend):
    backend, initialized = com_backend
    attempts = []

    def rejected():
        attempts.append(True)
        raise BusyComError("private COM diagnostic")

    with pytest.raises(ExcelBusyError, match="셀 편집이나 대화상자를 마친 뒤") as caught:
        import_tasks.excel_task(backend.COMRetryMethodWrapper(rejected), com=True)

    assert len(attempts) == import_tasks.MAX_COM_ATTEMPTS
    assert backend.N_COM_ATTEMPTS == 0 and initialized == ["init", "uninit"]
    assert "private COM diagnostic" not in str(caught.value)


def test_transient_busy_response_can_recover_within_the_retry_budget(com_backend):
    backend, initialized = com_backend
    attempts = []

    def ready_after_two_rejections():
        attempts.append(True)
        if len(attempts) <= 2:
            raise BusyComError()
        return "read data"

    assert import_tasks.excel_task(backend.COMRetryMethodWrapper(ready_after_two_rejections), com=True) == "read data"
    assert len(attempts) == 3 and backend.N_COM_ATTEMPTS == 0
    assert initialized == ["init", "uninit"]


def test_existing_finite_retry_budget_is_respected_and_other_errors_remain_distinct(com_backend):
    backend, _ = com_backend
    backend.N_COM_ATTEMPTS = 2
    error = OSError("RPC server unavailable")

    def failed_connection():
        assert backend.N_COM_ATTEMPTS == 2
        raise error

    with pytest.raises(OSError) as caught:
        import_tasks.excel_task(failed_connection, com=True)
    assert caught.value is error and backend.N_COM_ATTEMPTS == 2


def test_simultaneous_com_tasks_do_not_restore_each_others_retry_settings(com_backend):
    backend, _ = com_backend
    first_started, second_entered, release = Event(), Event(), Event()

    def first():
        first_started.set()
        assert release.wait(3)
        assert backend.N_COM_ATTEMPTS == import_tasks.MAX_COM_ATTEMPTS

    def second():
        second_entered.set()
        assert backend.N_COM_ATTEMPTS == import_tasks.MAX_COM_ATTEMPTS

    with ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(import_tasks.excel_task, first, com=True)
        assert first_started.wait(2)
        two = executor.submit(import_tasks.excel_task, second, com=True)
        try:
            assert not second_entered.wait(.05)
        finally:
            release.set()
        one.result(timeout=3)
        two.result(timeout=3)
    assert backend.N_COM_ATTEMPTS == 0


def test_wrapped_busy_cause_is_detected_without_reclassifying_general_failures():
    busy = BusyComError()
    wrapped = RuntimeError("read failed")
    wrapped.__cause__ = busy
    assert excel_reader.is_excel_busy_error(wrapped)
    assert not excel_reader.is_excel_busy_error(OSError("RPC server unavailable"))
    assert isinstance(excel_reader._connection_error(wrapped), ExcelBusyError)


@pytest.mark.parametrize("scode,expected", [(-2146777998, True), (-2146827284, False)])
def test_dispatch_error_is_busy_only_with_the_specific_excel_busy_code(scode, expected):
    error = Exception(-2147352567, "automation error", (None, None, None, None, None, scode), None)
    error.hresult = -2147352567
    assert excel_reader.is_excel_busy_error(error) is expected


def test_partial_discovery_keeps_ready_workbook_and_warns_that_another_excel_is_busy(monkeypatch):
    class BusyApp:
        @property
        def books(self):
            raise BusyComError()

    ready = SimpleNamespace(pid=1001, books=[fake_book("ready.xlsx")])
    monkeypatch.setattr(excel_reader, "xw", SimpleNamespace(apps=[BusyApp(), ready]))
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always")
        books = excel_reader.list_open_workbooks()
    assert [book.name for book in books] == ["ready.xlsx"]
    assert any("셀 편집이나 대화상자를 마친 뒤" in str(notice.message) for notice in notices)


def test_used_range_limit_is_checked_before_reading_values(monkeypatch):
    monkeypatch.setattr(excel_reader, "MAX_USED_CELLS", 6)
    used = FakeUsedRange([["x"] * 4] * 2)
    used.options = lambda **_: pytest.fail("너무 큰 사용 영역의 값을 읽으면 안 됩니다.")
    use_fake_excel(monkeypatch, [[fake_book("large.xlsx", (SimpleNamespace(name="data", used_range=used),))]])

    with pytest.raises(ExcelSheetTooLargeError, match="6셀을 넘습니다"):
        excel_reader.read_sheet("large.xlsx", "data", app_pid=1000, full_name="C:/data/large.xlsx")


def test_used_range_at_the_limit_retains_all_values_and_original_coordinates(monkeypatch):
    monkeypatch.setattr(excel_reader, "MAX_USED_CELLS", 6)
    values = [["a", "b", "c"], [1, 2, 3]]
    used = FakeUsedRange(values, row=5, col=3)
    use_fake_excel(monkeypatch, [[fake_book("normal.xlsx", (SimpleNamespace(name="data", used_range=used),))]])

    sheet = excel_reader.read_sheet("normal.xlsx", "data", app_pid=1000, full_name="C:/data/normal.xlsx")

    assert sheet.values == values and sheet.address_of(0, 0) == "C5" and sheet.address_of(1, 2) == "E6"


def test_busy_error_while_reading_cell_values_has_specific_guidance(monkeypatch):
    class BusyValues(FakeUsedRange):
        @property
        def value(self):
            raise BusyComError()

    used = BusyValues([["original"]])
    use_fake_excel(monkeypatch, [[fake_book("busy.xlsx", (SimpleNamespace(name="data", used_range=used),))]])
    with pytest.raises(ExcelBusyError, match="셀 편집이나 대화상자를 마친 뒤"):
        excel_reader.read_sheet("busy.xlsx", "data", app_pid=1000, full_name="C:/data/busy.xlsx")
