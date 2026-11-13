"""가져오기 기준일·삭제 시작일/종료일의 왼쪽 입력과 커서 없는 표시. 실제 Excel·DB는 호출하지 않는다."""

import pytest
from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from setron.ui.import_delete import ImportDeletePage
from setron.ui.import_demo import DemoExcel
from setron.ui.import_page import ImportPage
from setron.ui.import_widgets import DateField, PeriodDateField
from test_ui_import_ver5 import own  # noqa: F401


def _import_page(own):
    page = own(ImportPage(excel=DemoExcel()))
    page.resize(1100, 1200)
    page.jump_to_step(2)
    return page


@pytest.mark.parametrize("initial", ["", "2026-10-05"])
@pytest.mark.parametrize("click_x", [30, 100, 145])
def test_any_text_click_starts_typing_from_first_digit_without_clearing_date(own, initial, click_x):
    page = _import_page(own)
    page.resize(1100, 1200)
    field = page.common_date
    field.setText(initial)
    QTest.qWait(10)
    QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(click_x, field.height() // 2))
    # 삭제 시작일·종료일은 숨긴 호환용 입력이다. 화면의 기준일과 기간 버튼을 검증한다.
    assert field.dialog.isVisible()
    field.dialog.reject()
    field.setFocus()
    assert field.hasFocus() and field.cursorPosition() == 0
    assert field.text() == initial  # 클릭만으로 이미 입력된 날짜를 버리지 않는다.
    QTest.keyClicks(field, "20261108")
    assert field.text() == "2026-11-08" and field.hasAcceptableInput()


def test_cursor_position_and_blink_do_not_change_rendered_date(own):
    field = own(PeriodDateField("2026-11-08"))
    field.setFixedSize(190, 42)
    field.setFocus()
    field.setCursorPosition(0)
    QTest.qWait(30)
    first = field.grab().toImage()
    for position in (2, 6, 8):
        field.setCursorPosition(position)
        QTest.qWait(30)
        assert field.grab().toImage() == first
    QTest.qWait(550)
    assert field.grab().toImage() == first
    field.selectAll()
    QTest.qWait(20)
    assert field.grab().toImage() != first  # 블록 커서만 없애며 실제 텍스트 선택 표시는 유지한다.
    field.deselect()
    assert field.text() == "2026-11-08"


def test_keyboard_selection_paste_undo_and_calendar_keep_native_behavior(own):
    field = own(PeriodDateField())
    field.setFixedSize(190, 42)
    field.setFocus()
    clipboard = QApplication.clipboard()
    original = clipboard.text()
    try:
        clipboard.setText("20261108")
        field.paste()
        assert field.text() == "2026-11-08"
        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(10)
        assert field.hasSelectedText()
        QTest.keyClicks(field, "20241231")
        assert field.text() == "2024-12-31"
        field.undo()
        assert field.text() != "2024-12-31"
        field.setText("2026-11-08")
        field.setCursorPosition(0)
        QTest.keyClick(field, Qt.Key.Key_Right)
        assert field.cursorPosition() == 1  # 키보드로 원하는 자리로 이동하는 동작은 강제 초기화하지 않는다.
        calendar_button = field.findChild(QToolButton)
        QTest.mouseClick(calendar_button, Qt.MouseButton.LeftButton)
        assert field.dialog.isVisible()
        calendar = field.dialog.calendar
        calendar.clicked.emit(QDate(2026, 9, 21))
        assert field.text() == "2026-11-08"
        field.dialog.apply_button.click()
        assert field.text() == "2026-09-21"
        field.setReadOnly(True)
        QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(70, 20))
        QTest.keyClicks(field, "20241108")
        assert field.text() == "2026-09-21"
    finally:
        clipboard.setText(original)


def test_cursor_hiding_is_limited_to_requested_date_fields(own):
    page = own(ImportDeletePage())
    assert isinstance(page.start, PeriodDateField) and isinstance(page.end, PeriodDateField)
    assert isinstance(_import_page(own).common_date, PeriodDateField)
    ordinary = own(DateField())
    assert not isinstance(ordinary, PeriodDateField)


def test_import_common_date_hides_block_cursor_and_keeps_selection_and_calendar(own):
    page = _import_page(own)
    field = page.common_date
    field.setText("2026-11-08")
    QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(80, field.height() // 2))
    QTest.qWait(20)
    field.dialog.reject()
    field.setFocus()
    assert field.hasFocus() and field.cursorPosition() == 0
    first = field.grab().toImage()
    field.setCursorPosition(6)
    QTest.qWait(20)
    assert field.grab().toImage() == first
    field.selectAll()
    QTest.qWait(20)
    assert field.grab().toImage() != first
    field.deselect()
    field.open_calendar()
    calendar = field.dialog.calendar
    calendar.clicked.emit(QDate(2026, 10, 5))
    assert field.text() == "2026-11-08"
    field.dialog.apply_button.click()
    assert field.text() == "2026-10-05"
