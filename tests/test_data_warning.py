"""DB 접속 실패와 설정 없이 시작한 예시 모드의 공통 경고를 확인한다."""

from threading import get_ident

import pytest
from PySide6.QtWidgets import QApplication

from setron.ui import app as startup
from setron.ui.ai_connection import AIProviders
from setron.ui.app import create_window, startup_warning
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.main_window import MENUS, MainWindow
from setron.ui.theme import install_theme

from test_ui_management import wait_for


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


def test_startup_warning_distinguishes_db_failure_from_demo_mode():
    assert startup_warning("연결 실패", "AI 접속 설정 오류") == (
        "DB에 연결하지 못했습니다. MySQL 실행 상태와 접속 설정을 확인해주세요."
    )
    demo = startup_warning(None, None, demo_without_db=True)
    assert demo == "DB 접속 설정이 없어 합성 예시를 표시합니다. setron.env를 확인해주세요."
    assert startup_warning(None, "AI 접속 설정 오류") == "AI 예제 응답 · AI 접속 설정 오류"
    assert startup_warning(None, None) == ""


def test_warning_stays_visible_on_every_menu_while_notice_is_hidden(app):
    window = create_window()
    try:
        window.show()
        window.set_data_warning(startup_warning("DB 연결 실패", None))
        for menu in MENUS:
            if menu.key not in window.pages:
                continue
            window.navigate(menu.key)
            app.processEvents()
            assert window.data_warning.isVisible(), menu.key
        window.navigate("dashboard")
        app.processEvents()
        assert not window.notice.isVisible()          # 공통 안내 칸은 대시보드에서 숨김 (그래서 경고를 따로 둠)
        window.set_data_warning("")
        assert not window.data_warning.isVisible()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_startup_reconnect_retries_then_shows_the_connected_source(app, monkeypatch):
    connected_source = demo_dashboard_source()
    attempts = []

    def load_source():
        attempts.append(get_ident())
        return (None, "연결 실패") if len(attempts) < 3 else (connected_source, None)

    def visible_windows():
        return [widget for widget in app.topLevelWidgets()
                if isinstance(widget, MainWindow) and widget.isVisible()]

    def exercise_reconnect(_app):
        first, = visible_windows()
        try:
            retry = first.db_reconnect_button
            assert retry.isVisible() and retry.isEnabled()
            retry.click()
            assert retry.text() == "DB 연결 중…" and not retry.isEnabled()
            retry.click()
            wait_for(app, retry.isEnabled)
            assert len(attempts) == 2
            assert "다시 연결하지 못했습니다" in first.data_warning.text()

            retry.click()
            wait_for(app, lambda: any(window is not first for window in visible_windows()))
            current, = visible_windows()
            assert not current.data_warning.isVisible() and not current.db_reconnect_button.isVisible()
            dashboard = current.pages["dashboard"]
            assert dashboard.source is connected_source
            wait_for(app, lambda: dashboard.snapshot is not None)
            assert len(attempts) == 3
            assert attempts[0] == get_ident() and all(thread != get_ident() for thread in attempts[1:])
        finally:
            for window in visible_windows():
                window.close()
                window.deleteLater()
            app.processEvents()
        return 0

    monkeypatch.setattr(startup, "dashboard_source_from_env", load_source)
    monkeypatch.setattr(startup, "ai_providers_from_env", AIProviders)
    monkeypatch.delenv("SETRON_DB_NAME", raising=False)
    monkeypatch.setattr(QApplication, "exec", exercise_reconnect)
    assert startup.main([]) == 0
