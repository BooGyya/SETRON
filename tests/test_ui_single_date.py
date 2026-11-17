"""단일 날짜를 쓰는 실제 화면들이 같은 적용·취소 팝업을 공유한다."""

from datetime import date

import pytest
from PySide6.QtCore import QDate, QPoint, QRect
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from setron.ui.appearance import manager
from setron.ui.appearance_data import Appearance
from setron.ui.calendar_widgets import DateEdit
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.data_management_page import _date_picker
from setron.ui.date_range import DateRangeDialog
from setron.ui.import_demo import DemoExcel
from setron.ui.import_page import ImportPage
from setron.ui.import_widgets import DateField
from setron.ui.management_page import ValueEditor
from setron.ui.query_panel import QueryPanel
from setron.ui.query_state import QueryState, preview_catalog
from setron.ui.version_demo import demo_version_catalog
from setron.ui.version_management import VersionManagementPage
from setron.ui.version_management_forms import ReleaseForm
from test_ui_import_ver5 import own  # noqa: F401


@pytest.mark.parametrize("consumer", ["query", "management", "data", "import", "release", "installed", "plain"])
def test_visible_single_date_consumers_confirm_once_and_cancel_without_editing(own, consumer):
    if consumer == "query":
        field = own(QueryPanel(QueryState(preview_catalog()))).date_edit
    elif consumer == "management":
        field = own(ValueEditor("release_version", "released_at", date(2026, 8, 10), {})).editor
    elif consumer == "data":
        field = own(_date_picker(date(2026, 8, 10)))
    elif consumer == "import":
        page = own(ImportPage(excel=DemoExcel()))
        page.resize(1100, 1000)
        page.jump_to_step(2)
        field = page.common_date
    elif consumer == "release":
        field = own(ReleaseForm()).released
    elif consumer == "installed":
        field = own(VersionManagementPage(demo_version_catalog, demo_dashboard_source())).installed_date
    else:
        field = own(DateField())
    initial, chosen = QDate(2026, 8, 10), QDate(2026, 8, 20)
    if isinstance(field, DateEdit):
        field.setDate(initial)
        read = field.date
        changed = QSignalSpy(field.dateChanged)
        expected = chosen
    else:
        field.setText(initial.toString("yyyy-MM-dd"))
        read = field.text
        changed = QSignalSpy(field.textChanged)
        expected = chosen.toString("yyyy-MM-dd")
    previous = read()
    field.open_calendar()
    dialog = field.dialog
    assert isinstance(dialog, DateRangeDialog) and dialog.single_day
    assert tuple(dialog.endpoint_buttons) == ("start",)
    assert dialog.layout().itemAt(0).widget() is dialog.calendar.header
    assert dialog.calendar.comfortable and dialog.endpoint_buttons["start"].isChecked()
    dialog.calendar.clicked.emit(chosen)
    assert read() == previous and changed.count() == 0
    dialog.cancel_button.click()
    assert read() == previous and changed.count() == 0
    field.open_calendar()
    dialog = field.dialog
    dialog.calendar.clicked.emit(chosen)
    dialog.apply_button.click()
    assert read() == expected and changed.count() == 1 and not dialog.isVisible()


def test_single_date_limits_and_same_day_confirmation_are_preserved(own):
    field = own(DateEdit())
    field.setDateRange(QDate(2026, 8, 12), QDate(2026, 8, 20))
    field.setDate(QDate(2026, 8, 15))
    changed, confirmed = QSignalSpy(field.dateChanged), QSignalSpy(field.calendar_activated)
    field.open_calendar()
    dialog = field.dialog
    assert dialog.calendar.minimumDate() == field.minimumDate()
    assert dialog.calendar.maximumDate() == field.maximumDate()
    dialog.calendar.clicked.emit(QDate(2026, 8, 10))
    assert dialog.start == dialog.end == date(2026, 8, 15)
    dialog.apply_button.click()
    assert changed.count() == 0 and confirmed.count() == 1
    field.setReadOnly(True)
    field.open_calendar()
    assert not field.is_calendar_visible()
    field.setReadOnly(False)
    field.setCalendarPopup(False)
    field.open_calendar()
    assert not field.is_calendar_visible()


@pytest.mark.parametrize("initial", ["", "2026-02-30"])
def test_single_date_open_and_cancel_preserve_blank_or_invalid_input(own, initial):
    field = own(DateField(initial))
    field.open_calendar()
    assert field.dialog.calendar.selectedDate() == QDate.currentDate()
    assert field.text() == initial
    field.dialog.reject()
    assert field.text() == initial


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("font_size", ["small", "normal", "large"])
def test_single_date_layout_fits_normal_and_tiny_screens(own, monkeypatch, mode, font_size):
    field = own(DateField("2026-08-10"))
    theme, previous = manager(), manager().appearance
    try:
        theme.apply(Appearance(mode=mode, font_size=font_size))
        field.open_calendar()
        dialog = field.dialog
        QTest.qWait(20)
        card = dialog.endpoint_buttons["start"]
        assert card.value.font().pixelSize() > card.caption.font().pixelSize()
        assert card.rect().contains(card.value.geometry())
        grid = dialog.calendar_view
        last = grid.model().index(grid.model().rowCount() - 1, grid.model().columnCount() - 1)
        assert grid.visualRect(last).bottom() < grid.viewport().height()
        dialog.reject()

        available = QRect(50, 50, 300, 260)

        class Screen:
            def availableGeometry(self):
                return available

        monkeypatch.setattr(QApplication, "screenAt", lambda _point: Screen())
        field.open_calendar()
        dialog = field.dialog
        QTest.qWait(20)
        assert available.contains(dialog.geometry())
        for control in (dialog.apply_button, dialog.cancel_button, dialog.endpoint_buttons["start"],
                        dialog.calendar.previous_button, dialog.calendar.year_combo,
                        dialog.calendar.month_combo, dialog.calendar.next_button):
            assert dialog.rect().contains(control.rect().translated(control.mapTo(dialog, QPoint())))
        dialog.reject()
    finally:
        theme.apply(previous)
