"""테마 관리자 아래에서도 확인창(QMessageBox)이 답을 돌려준다 (11/11 '새 대화' 멈춤).

QMessageBox.icon()은 그림(QIcon)이 아니라 종류(물음표·경고)를 돌려준다. 테마 관리자가 이를 그림으로 여겨
'Icon' object has no attribute 'isNull' 오류가 이어지고, 확인창이 Yes를 돌려주지 못했다.
버그가 다시 생겨도 테스트가 멈추지 않게 2초 뒤 확인창을 강제로 닫는다.
"""

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from setron.ui.appearance import install_appearance, manager
from setron.ui.appearance_data import Appearance
from setron.ui.chat_page import ChatPage
from test_chat_phrasing import Service
from test_ui_chat_ver5 import ui  # noqa: F401  (fixture)


class _Store:
    def appearance(self):
        return Appearance()

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _themed():
    app = QApplication.instance()
    if manager() is None:
        install_appearance(app, _Store())
    return app


def _answer_yes_then_give_up():
    def press():
        box = QApplication.activeModalWidget()
        if isinstance(box, QMessageBox):
            box.button(QMessageBox.StandardButton.Yes).click()

    def give_up():
        box = QApplication.activeModalWidget()
        if isinstance(box, QMessageBox):
            box.reject()
    QTimer.singleShot(200, press)
    QTimer.singleShot(2000, give_up)


def test_message_box_returns_yes_under_theme_manager(ui):
    _themed()
    _answer_yes_then_give_up()
    answer = QMessageBox.question(None, "새 대화", "지금 대화를 지우고 처음 화면으로 갈까요?")
    assert answer == QMessageBox.StandardButton.Yes


def test_new_conversation_with_real_confirm_returns_to_start(ui):
    _themed()
    page = ui(ChatPage(Service(None)))
    page.send("화성 15L 목표 N 미만 설비 보여줘")
    for _ in range(100):
        if page.pending is None and page.cards:
            break
        QTest.qWait(20)
    assert page.cards
    _answer_yes_then_give_up()
    page.new_button.click()                                  # 진짜 확인창이 뜬다
    QTest.qWait(100)
    assert not page.cards and page.empty.isVisible()
