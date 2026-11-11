"""한 달력의 기간 선택은 두 날짜를 고른 뒤 적용할 때만 확정한다."""

from datetime import date
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDate, QPoint, QRect, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from setron.ui.date_range import DateRangeButton
from setron.ui.theme import install_theme


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    return result


@pytest.fixture
def control(app):
    host = QWidget()
    host.resize(720, 520)
    layout = QVBoxLayout(host)
    result = DateRangeButton()
    result.set_range(date(2026, 8, 10), date(2026, 8, 20))
    layout.addWidget(result)
    layout.addStretch()
    host.show()
    QTest.qWait(10)
    yield result
    if result.dialog is not None:
        result.dialog.reject()
    host.close()
    host.deleteLater()
    app.processEvents()


def click_day(dialog, day):
    dialog.calendar.setCurrentPage(2026, 8)
    grid = dialog.calendar_view
    index = next(index for row in range(1, grid.model().rowCount()) for column in range(grid.model().columnCount())
                 if (index := grid.model().index(row, column)).data() in (day, str(day)))
    QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=grid.visualRect(index).center())


@pytest.mark.parametrize("days", ((12, 22), (22, 12)))
def test_two_calendar_clicks_apply_once_and_reverse_selection_is_reordered(control, days):
    changed = QSignalSpy(control.range_changed)
    assert control.height() == 38 and control.minimumWidth() == 240
    assert control.property("queryField") and not control.icon().isNull()
    control.click()
    dialog = control.dialog
    assert not dialog.calendar_scroll.horizontalScrollBar().isVisible()
    assert not dialog.calendar_scroll.verticalScrollBar().isVisible()
    assert dialog.calendar.range_start == QDate(2026, 8, 10)
    assert dialog.calendar.range_end == QDate(2026, 8, 20)
    click_day(dialog, days[0])
    assert dialog.awaiting_end and not dialog.apply_button.isEnabled()
    assert changed.count() == 0 and control.start == date(2026, 8, 10)
    dialog.accept()  # 완료되지 않은 기간은 직접 호출해도 확정할 수 없다.
    assert dialog.isVisible()
    click_day(dialog, days[1])
    assert not dialog.awaiting_end and dialog.apply_button.isEnabled()
    assert changed.count() == 0 and control.end == date(2026, 8, 20)
    assert dialog.calendar.range_start == QDate(2026, 8, 12)
    assert dialog.calendar.range_end == QDate(2026, 8, 22)
    dialog.apply_button.click()
    assert changed.count() == 1 and changed.at(0) == [date(2026, 8, 12), date(2026, 8, 22)]
    assert control.text() == "2026-08-12 ~ 2026-08-22"
    assert not dialog.isVisible()


@pytest.mark.parametrize("dismiss", ("cancel", "escape", "outside"))
def test_dismissing_pending_range_preserves_applied_dates(control, dismiss):
    changed = QSignalSpy(control.range_changed)
    control.click()
    dialog = control.dialog
    click_day(dialog, 12)
    click_day(dialog, 22)
    if dismiss == "cancel":
        dialog.cancel_button.click()
    elif dismiss == "escape":
        QTest.keyClick(dialog.calendar_view, Qt.Key.Key_Escape)
    else:
        host = control.parentWidget()
        QTest.mouseClick(host, Qt.MouseButton.LeftButton, pos=QPoint(host.width() - 10, host.height() - 10))
    assert not dialog.isVisible()
    assert changed.count() == 0
    assert (control.start, control.end) == (date(2026, 8, 10), date(2026, 8, 20))
    assert control.text() == "2026-08-10 ~ 2026-08-20"
    control.click()
    assert control.dialog.calendar.range_start == QDate(2026, 8, 10)
    assert control.dialog.calendar.range_end == QDate(2026, 8, 20)


def test_keyboard_can_select_both_dates_without_early_confirmation(control):
    changed = QSignalSpy(control.range_changed)
    control.click()
    dialog = control.dialog
    dialog.calendar.setSelectedDate(QDate(2026, 8, 12))
    QTest.keyClick(dialog.calendar_view, Qt.Key.Key_Return)
    assert dialog.awaiting_end and changed.count() == 0
    dialog.calendar.setSelectedDate(QDate(2026, 8, 22))
    QTest.keyClick(dialog.calendar_view, Qt.Key.Key_Space)
    assert dialog.apply_button.isEnabled() and changed.count() == 0
    QTest.keyClick(dialog.apply_button, Qt.Key.Key_Space)
    assert changed.count() == 1 and control.end == date(2026, 8, 22)


def test_programmatic_range_change_has_no_signal_and_optional_maximum_is_enforced(control):
    changed = QSignalSpy(control.range_changed)
    control.set_range(date(2026, 8, 11), date(2026, 8, 21), maximum=date(2026, 8, 25))
    assert changed.count() == 0
    control.click()
    dialog = control.dialog
    assert dialog.calendar.maximumDate() == QDate(2026, 8, 25)
    dialog.calendar.clicked.emit(QDate(2026, 8, 26))
    assert not dialog.awaiting_end and dialog.start == date(2026, 8, 11)
    assert changed.count() == 0


def test_popup_fits_small_available_screen(control, monkeypatch):
    available = QRect(50, 50, 300, 260)

    class Screen:
        def availableGeometry(self):
            return available

    monkeypatch.setattr(QApplication, "screenAt", lambda _position: Screen())
    control.click()
    dialog = control.dialog
    assert available.contains(dialog.geometry())
    assert dialog.apply_button.isVisible() and dialog.cancel_button.isVisible()
