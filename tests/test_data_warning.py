"""DB 접속 실패와 설정 없이 시작한 예시 모드의 공통 경고를 확인한다."""

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
