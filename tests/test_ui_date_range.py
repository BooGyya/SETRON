"""기간의 시작·종료 날짜를 각각 편집하고 적용할 때만 확정한다."""

from datetime import date
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDate, QPoint, QRect, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from setron.ui.appearance import manager
from setron.ui.appearance_data import Appearance
from setron.ui.date_range import DateRangeButton, DateRangeDialog
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


def test_two_calendar_clicks_apply_once_without_changing_the_control_early(control):
    changed = QSignalSpy(control.range_changed)
    assert control.height() == 38 and control.minimumWidth() == 240
    assert control.property("queryField") and not control.icon().isNull()
    control.click()
    dialog = control.dialog
    assert not dialog.calendar_scroll.horizontalScrollBar().isVisible()
    assert not dialog.calendar_scroll.verticalScrollBar().isVisible()
    assert dialog.calendar.range_start == QDate(2026, 8, 10)
    assert dialog.calendar.range_end == QDate(2026, 8, 20)
    assert dialog.endpoint_buttons["start"].isChecked()
    assert not any("차례로 선택" in item.text() for item in dialog.findChildren(QLabel))
    click_day(dialog, 12)
    assert dialog.endpoint_buttons["end"].isChecked() and dialog.apply_button.isEnabled()
    assert (dialog.start, dialog.end) == (date(2026, 8, 12), date(2026, 8, 20))
    assert changed.count() == 0 and control.start == date(2026, 8, 10)
    click_day(dialog, 22)
    assert dialog.apply_button.isEnabled()
    assert changed.count() == 0 and control.end == date(2026, 8, 20)
    assert dialog.calendar.range_start == QDate(2026, 8, 12)
    assert dialog.calendar.range_end == QDate(2026, 8, 22)
    dialog.apply_button.click()
    assert changed.count() == 1 and changed.at(0) == [date(2026, 8, 12), date(2026, 8, 22)]
    assert control.text() == "2026-08-12 ~ 2026-08-22"
    assert not dialog.isVisible()


def test_year_month_header_is_first_and_keeps_navigation_separate_from_date_edits(control):
    control.click()
    dialog = control.dialog
    calendar = dialog.calendar
    assert dialog.layout().itemAt(0).widget() is calendar.header
    assert calendar.header.parentWidget() is dialog
    assert not any(item.isVisibleTo(dialog) and item.text() == "기간 선택" for item in dialog.findChildren(QLabel))
    header = calendar.header.rect().translated(calendar.header.mapTo(dialog, QPoint()))
    endpoint = dialog.endpoint_buttons["start"].rect().translated(dialog.endpoint_buttons["start"].mapTo(dialog, QPoint()))
    grid = dialog.calendar_view.rect().translated(dialog.calendar_view.mapTo(dialog, QPoint()))
    assert header.bottom() < endpoint.top() < endpoint.bottom() < grid.top()
    dates = dialog.start, dialog.end
    calendar.year_combo.setCurrentIndex(2025 - calendar.years.first)
    calendar.month_combo.setFocus()
    QTest.keyClick(calendar.month_combo, Qt.Key.Key_Down)
    assert (calendar.yearShown(), calendar.monthShown()) == (2025, 9)
    calendar.next_button.click()
    assert calendar.monthShown() == 10 and (dialog.start, dialog.end) == dates
    dialog.endpoint_buttons["end"].click()
    assert calendar.selectedDate() == QDate(2026, 8, 20)


@pytest.mark.parametrize("endpoint, day, expected", [
    ("start", 12, (date(2026, 8, 12), date(2026, 8, 20))),
    ("end", 22, (date(2026, 8, 10), date(2026, 8, 22))),
])
def test_one_endpoint_can_be_selected_and_applied_without_reselecting_the_other(control, endpoint, day, expected):
    changed = QSignalSpy(control.range_changed)
    control.click()
    dialog = control.dialog
    dialog.endpoint_buttons[endpoint].click()
    assert dialog.endpoint_buttons[endpoint].isChecked()
    assert dialog.calendar.selectedDate() == QDate(2026, 8, 10 if endpoint == "start" else 20)
    click_day(dialog, day)
    assert dialog.apply_button.isEnabled() and (dialog.start, dialog.end) == expected
    assert changed.count() == 0
    dialog.apply_button.click()
    assert changed.count() == 1 and changed.at(0) == list(expected)
    assert (control.start, control.end) == expected
    control.click()
    assert (control.dialog.start, control.dialog.end) == expected


def test_start_selection_keeps_calendar_month_until_user_selects_end_field(control):
    control.set_range(date(2026, 5, 21), date(2026, 10, 21))
    control.click()
    dialog = control.dialog
    dialog.calendar.clicked.emit(QDate(2026, 5, 25))
    assert (dialog.calendar.yearShown(), dialog.calendar.monthShown()) == (2026, 5)
    assert dialog.endpoint_buttons["end"].isChecked() and dialog.end == date(2026, 10, 21)
    dialog.endpoint_buttons["end"].click()
    assert (dialog.calendar.yearShown(), dialog.calendar.monthShown()) == (2026, 10)
    assert dialog.calendar.selectedDate() == QDate(2026, 10, 21)
    dialog.calendar.clicked.emit(QDate(2026, 10, 15))
    dialog.apply_button.click()
    assert (control.start, control.end) == (date(2026, 5, 25), date(2026, 10, 15))


@pytest.mark.parametrize("endpoint, invalid, other, repaired, expected", [
    ("start", 22, "end", 25, (date(2026, 8, 22), date(2026, 8, 25))),
    ("end", 8, "start", 5, (date(2026, 8, 5), date(2026, 8, 8))),
])
def test_invalid_order_preserves_other_endpoint_until_user_repairs_it(control, endpoint, invalid, other, repaired, expected):
    changed = QSignalSpy(control.range_changed)
    control.click()
    dialog = control.dialog
    dialog.endpoint_buttons[endpoint].click()
    click_day(dialog, invalid)
    assert not dialog.apply_button.isEnabled()
    assert dialog.caption.isVisible() and "같거나 앞서야" in dialog.caption.text()
    assert getattr(dialog, other) == date(2026, 8, 20 if other == "end" else 10)
    dialog.accept()
    assert dialog.isVisible() and changed.count() == 0
    dialog.endpoint_buttons[other].click()
    click_day(dialog, repaired)
    assert dialog.apply_button.isEnabled() and not dialog.caption.isVisible()
    dialog.apply_button.click()
    assert changed.count() == 1 and changed.at(0) == list(expected)


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
    assert dialog.endpoint_buttons["end"].isChecked() and dialog.apply_button.isEnabled() and changed.count() == 0
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
    assert dialog.start == date(2026, 8, 11) and dialog.end == date(2026, 8, 21)
    assert changed.count() == 0


@pytest.mark.parametrize("font_size", ["normal", "large"])
def test_popup_fits_small_available_screen(control, monkeypatch, font_size):
    available = QRect(50, 50, 300, 260)

    class Screen:
        def availableGeometry(self):
            return available

    theme, previous = manager(), manager().appearance
    theme.apply(Appearance(font_size=font_size))
    try:
        monkeypatch.setattr(QApplication, "screenAt", lambda _position: Screen())
        control.click()
        dialog = control.dialog
        assert available.contains(dialog.geometry())
        assert dialog.apply_button.isVisible() and dialog.cancel_button.isVisible()
        for action in (dialog.apply_button, dialog.cancel_button):
            assert dialog.rect().contains(action.rect().translated(action.mapTo(dialog, QPoint())))
        for navigation in (dialog.calendar.previous_button, dialog.calendar.year_combo,
                           dialog.calendar.month_combo, dialog.calendar.next_button):
            assert dialog.rect().contains(navigation.rect().translated(navigation.mapTo(dialog, QPoint())))
        for endpoint in dialog.endpoint_buttons.values():
            bounds = endpoint.rect().translated(endpoint.mapTo(dialog, QPoint()))
            assert dialog.rect().contains(bounds)
            assert endpoint.value.width() >= endpoint.value.fontMetrics().horizontalAdvance(endpoint.value.text())
    finally:
        theme.apply(previous)


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("font_size", ["small", "normal", "large"])
def test_endpoint_cards_keep_date_hierarchy_and_fit_both_active_states(control, mode, font_size):
    theme, previous = manager(), manager().appearance
    theme.apply(Appearance(mode=mode, font_size=font_size))
    try:
        control.click()
        dialog = control.dialog
        for active in ("start", "end"):
            dialog.endpoint_buttons[active].click()
            QTest.qWait(20)
            assert dialog.endpoint_buttons[active].isChecked()
            assert dialog.layout().itemAt(0).widget() is dialog.calendar.header
            for endpoint in dialog.endpoint_buttons.values():
                assert endpoint.value.font().pixelSize() > endpoint.caption.font().pixelSize()
                assert endpoint.rect().contains(endpoint.caption.geometry())
                assert endpoint.rect().contains(endpoint.value.geometry())
                assert endpoint.value.height() >= endpoint.value.fontMetrics().height()
                assert endpoint.value.width() >= endpoint.value.fontMetrics().horizontalAdvance(endpoint.value.text())
            grid = dialog.calendar_view
            last = grid.model().index(grid.model().rowCount() - 1, grid.model().columnCount() - 1)
            assert grid.visualRect(last).bottom() < grid.viewport().height()
    finally:
        theme.apply(previous)


def test_single_day_picker_shares_polished_layout_and_confirmation(control):
    day = date(2026, 8, 10)
    dialog = DateRangeDialog(day, day, single_day=True, parent=control)
    try:
        dialog.open_at(control)
        assert tuple(dialog.endpoint_buttons) == ("start",) and dialog.calendar.comfortable
        assert dialog.layout().itemAt(0).widget() is dialog.calendar.header
        assert not dialog.range_label.isVisible() and not dialog.caption.isVisible()
        assert dialog.endpoint_buttons["start"].caption.text() == "선택 날짜"
        assert dialog.endpoint_buttons["start"].isChecked()
        assert dialog.calendar.previous_button.size() == dialog.calendar.next_button.size()
        assert dialog.calendar.previous_button.width() == 32
        dialog.calendar.clicked.emit(QDate(2026, 8, 12))
        assert dialog.start == dialog.end == date(2026, 8, 12)
        assert dialog.endpoint_buttons["start"].value.text() == "2026-08-12"
        assert dialog.apply_button.isEnabled()
    finally:
        dialog.reject()
        dialog.deleteLater()
