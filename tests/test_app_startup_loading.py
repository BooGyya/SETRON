"""첫 DB 읽기는 로딩 창 뒤에서 실행하고, 닫힌 시작 창은 다시 열지 않는다."""

from datetime import date
import os
from threading import Event, get_ident
from time import perf_counter
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton

import setron.ui.app as app_module


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    yield result


@pytest.fixture
def startup(app, monkeypatch):
    created, closed_ai = [], []
    main_thread = get_ident()

    class Window(QMainWindow):
        def __init__(self, source):
            super().__init__()
            assert get_ident() == main_thread
            self.setObjectName("testStartupActualWindow")
            self.setMinimumSize(960, 600)
            self.source = source
            self.notice = QLabel(self)
            self.db_reconnect_button = QPushButton(self)
            self.close_guard = lambda: True
            self.warnings, self.applied, self.dates = [], [], []
            self.query_panel = SimpleNamespace(set_as_of=self.dates.append,
                                               apply_draft=lambda: self.applied.append(True))

        def set_data_warning(self, message, *, reconnect=False):
            self.warnings.append((message, reconnect))

    def create(source, **kwargs):
        window = Window(source)
        created.append(window)
        return window

    monkeypatch.setattr(app_module, "create_window", create)
    monkeypatch.setattr(app_module, "ai_providers_from_env", lambda: SimpleNamespace(
        problem=None, extractor=None, client=None, close=lambda: closed_ai.append(True)))
    monkeypatch.setattr(app_module, "DemoExcel", lambda: None)
    for name in ("saver_from_env", "sql_exporter_from_env", "sql_importer_from_env", "gateway_from_env",
                 "data_exporter_from_env", "export_files_from_env"):
        monkeypatch.setattr(app_module, name, lambda: None)
    previous = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(True)
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(app.quit)
    watchdog.start(3000)
    yield SimpleNamespace(created=created, closed_ai=closed_ai, main_thread=main_thread)
    watchdog.stop()
    for window in app.topLevelWidgets():
        if window.objectName() in {"startupWindow", "testStartupActualWindow"}:
            window.close()
            window.deleteLater()
    app.processEvents()
    app.setQuitOnLastWindowClosed(previous)


def loading_window(app):
    return next(window for window in app.topLevelWidgets()
                if window.objectName() == "startupWindow" and window.isVisible())


def test_loading_window_precedes_worker_and_auto_close_starts_after_actual_window(app, startup, monkeypatch):
    release, started = Event(), Event()
    threads, inspected, dimensions = [], [], []
    source = SimpleNamespace(dates=(date(2026, 10, 21),), analysis_dates=(date(2026, 10, 21),))

    def load():
        threads.append(get_ident())
        started.set()
        assert release.wait(2)
        return source, None

    def inspect():
        window = loading_window(app)
        inspected.append(window.findChild(QLabel, "startupLoadingMessage").text())
        window.resize(960, 650)
        dimensions.append(window.size())
        assert started.is_set() and startup.created == []
        release.set()

    monkeypatch.setattr(app_module, "dashboard_source_from_env", load)
    QTimer.singleShot(80, inspect)
    begin = perf_counter()
    try:
        assert app_module.main(["--demo-excel", "--auto-close-ms", "40"]) == 0
    finally:
        release.set()
    assert inspected == ["자료를 불러오고 있습니다."]
    assert threads and threads[0] != startup.main_thread
    assert len(startup.created) == 1 and startup.created[0].source is source
    assert startup.created[0].size() == dimensions[0]
    assert startup.created[0].applied == [True] and startup.created[0].dates == list(source.dates)
    assert perf_counter() - begin >= 0.1 and startup.closed_ai == [True]


def test_closing_loading_window_ignores_late_worker_response(app, startup, monkeypatch):
    release = Event()
    observed = []

    def load():
        assert release.wait(2)
        return None, None

    def close():
        window = loading_window(app)
        observed.append(window)
        window.close()

    monkeypatch.setattr(app_module, "dashboard_source_from_env", load)
    QTimer.singleShot(30, close)
    try:
        assert app_module.main(["--demo-excel"]) == 0
        assert observed and startup.created == []
    finally:
        release.set()
        QThreadPool.globalInstance().waitForDone(2000)
        app.processEvents()
    assert startup.created == [] and startup.closed_ai == [True]


@pytest.mark.parametrize("unexpected_error", [False, True])
def test_initial_read_error_keeps_database_reconnect_and_window_geometry(app, startup, monkeypatch, unexpected_error):
    calls, retry_size = [], []
    source = SimpleNamespace(dates=(date(2026, 10, 21),), analysis_dates=(date(2026, 10, 21),))

    def load():
        calls.append(get_ident())
        if len(calls) == 1:
            if unexpected_error:
                raise RuntimeError("검증용 읽기 실패")
            return None, "검증용 DB 연결 실패"
        return source, None

    retry = QTimer()
    retry.setInterval(10)

    def reconnect():
        if not startup.created:
            return
        first = startup.created[0]
        if not first.isVisible():
            return
        retry.stop()
        assert first.warnings[-1][1]
        first.resize(960, 640)
        retry_size.append(first.size())
        first.db_reconnect_button.click()

    monkeypatch.setattr(app_module, "dashboard_source_from_env", load)
    retry.timeout.connect(reconnect)
    retry.start()
    try:
        assert app_module.main(["--demo-excel", "--auto-close-ms", "120"]) == 0
    finally:
        retry.stop()
    assert len(calls) == len(startup.created) == 2
    assert startup.created[1].source is source and startup.created[1].size() == retry_size[0]
    assert not startup.created[1].warnings[-1][1]
    assert all(thread != startup.main_thread for thread in calls)
