"""QA Q-03: DB를 못 읽어 합성 예시 자료로 시작하면 모든 화면에 '실제 자료 아님' 경고가 계속 보인다."""

import pytest
from PySide6.QtWidgets import QApplication

from setron.ui.app import create_window, startup_warning
from setron.ui.main_window import MENUS
from setron.ui.theme import install_theme


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


def test_startup_warning_says_numbers_are_not_real():
    text = startup_warning("DB 자료를 읽지 못해 합성 예시로 시작했습니다: 연결 실패", None)
    assert text.startswith("실제 자료 아님") and "합성 예시" in text and "연결 실패" in text
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
