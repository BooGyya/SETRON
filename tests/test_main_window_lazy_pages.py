"""지연 페이지는 최초 사용 때만 만들며 화면 이동·종료 경계를 보존한다."""

import os
from threading import Thread

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThread
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QWidget

from setron.ui.main_window import MainWindow
from setron.ui.theme import install_theme


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    return result


@pytest.fixture
def window(app):
    result = MainWindow()
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def test_registration_and_nonresolving_inspection_do_not_create_pages(window):
    calls = []
    window.register_lazy_page("reports", lambda: calls.append("reports") or QWidget(), uses_query=False)
    window.register_page("heatmap", QWidget())
    assert set(window.pages) == {"reports", "heatmap"}
    assert window.nav["reports"].isEnabled()
    assert not window.pages.is_loaded("reports")
    assert window.pages.peek("reports") is window.peek_page("reports") is None
    assert window.pages.peek("reports", "pending") == "pending"
    assert list(window.pages.loaded_items()) == [("heatmap", window.pages["heatmap"])]
    assert len(tuple(window.pages.items())) == len(tuple(window.pages.values())) == 2
    assert window.pages.get("unknown") is None
    window.close_guard = lambda: window.peek_page("reports") is None
    assert window.close()
    assert calls == []


def test_direct_access_creates_once_in_gui_thread_and_replaces_only_its_slot(window):
    calls = []

    class Page(QWidget):
        def set_playback_host(self, viewport):
            self.viewport = viewport

    def factory():
        calls.append(QThread.currentThread())
        return Page()

    window.register_lazy_page("replay", factory, uses_query=False)
    window.register_page("heatmap", QWidget())
    count = window.stack.count()
    pending = dict.__getitem__(window.pages, "replay")
    position = next(index for index in range(count) if window.stack.widget(index) is pending)
    page = window.pages["replay"]
    assert window.pages.get("replay") is page and window.pages["replay"] is page
    assert window.pages.peek("replay") is page and window.pages.is_loaded("replay")
    assert calls == [window.thread()]
    assert page.parentWidget() is window.stack and page.viewport is window.scroll.viewport()
    assert window.stack.count() == count and window.stack.widget(position) is page
    assert window.route == "dashboard" and window.stack.currentWidget() is window.placeholder
    assert window.navigate("replay") and window.stack.currentWidget() is page
    assert window.navigate("heatmap") and window.navigate("replay")
    assert calls == [window.thread()]


def test_navigation_resolves_before_route_change_and_emits_one_page_event(window):
    page = QWidget()
    window.register_lazy_page("items", lambda: page, uses_query=True)
    spy = QSignalSpy(window.page_changed)
    assert window.navigate("items")
    assert window.pages["items"] is page and window.stack.currentWidget() is page
    assert window.route == "items" and window.header.title.text() == "아이템 매니저"
    assert window.nav["items"].isChecked() and not window.query_panel.isHidden()
    assert spy.count() == 1 and spy.at(0) == ["items"]


def test_failed_or_recursive_factory_keeps_route_and_can_be_retried(window):
    attempts = []

    def factory():
        attempts.append(None)
        if len(attempts) == 1:
            return window.pages["settings"]
        return QWidget()

    window.register_lazy_page("settings", factory, uses_query=False)
    with pytest.raises(RuntimeError, match="다시 요청"):
        window.navigate("settings")
    assert window.route == "dashboard" and window._route_history == []
    assert not window.pages.is_loaded("settings") and window.peek_page("settings") is None
    assert window.stack.currentWidget() is window.placeholder
    assert window.navigate("settings")
    assert len(attempts) == 2 and window.pages.is_loaded("settings")


def test_factory_never_runs_from_worker_thread(window):
    calls, errors = [], []
    window.register_lazy_page("reports", lambda: calls.append(None) or QWidget())

    def read():
        try:
            window.pages["reports"]
        except RuntimeError as error:
            errors.append(str(error))

    task = Thread(target=read)
    task.start()
    task.join(timeout=2)
    assert not task.is_alive() and len(errors) == 1 and "GUI" in errors[0]
    assert not calls and not window.pages.is_loaded("reports")
    assert isinstance(window.pages["reports"], QWidget) and calls == [None]


def test_lazy_registration_reuses_normal_validation_and_current_route_behavior(window):
    for key, factory, uses_query in (("unknown", QWidget, None), ("reports", None, None),
                                     ("reports", QWidget, "yes")):
        with pytest.raises(ValueError):
            window.register_lazy_page(key, factory, uses_query=uses_query)
    assert not window.pages
    page = QWidget()
    window.register_lazy_page("dashboard", lambda: page)
    assert window.pages["dashboard"] is page and window.stack.currentWidget() is page
    with pytest.raises(ValueError, match="이미 등록"):
        window.register_lazy_page("dashboard", QWidget)
    window.register_lazy_page("reports", lambda: page)
    with pytest.raises(ValueError, match="별도의"):
        window.pages["reports"]
    assert not window.pages.is_loaded("reports")


def test_lazy_auxiliary_page_does_not_build_parent_and_preserves_parent_navigation(window):
    calls = []
    window.register_lazy_page("heatmap", lambda: calls.append("heatmap") or QWidget())
    window.register_lazy_auxiliary_page("equipment", lambda: calls.append("equipment") or QWidget(),
                                       parent_key="heatmap", title="설비 목록")
    assert set(window.pages) == {"heatmap", "equipment"} and calls == []
    assert window.peek_page("equipment") is None and "equipment" not in window.nav
    assert window.navigate("equipment")
    assert calls == ["equipment"] and not window.pages.is_loaded("heatmap")
    assert window.stack.currentWidget() is window.pages["equipment"]
    assert window.header.title.text() == "설비 목록" and window.nav["heatmap"].isChecked()
    assert not window.query_panel.isHidden() and window.header.back.isEnabled()
    assert window.navigate("heatmap") and calls == ["equipment", "heatmap"]


@pytest.mark.parametrize("key,parent,title,uses_query", [
    ("reports", "heatmap", "설비 목록", True), ("equipment", "missing", "설비 목록", True),
    ("equipment", "heatmap", "", True), ("equipment", "heatmap", "설비 목록", None),
])
def test_lazy_auxiliary_validation_leaves_registration_unchanged(window, key, parent, title, uses_query):
    window.register_lazy_page("heatmap", QWidget)
    with pytest.raises(ValueError):
        window.register_lazy_auxiliary_page(key, QWidget, parent_key=parent, title=title, uses_query=uses_query)
    assert set(window.pages) == {"heatmap"} and not window.pages.is_loaded("heatmap")
