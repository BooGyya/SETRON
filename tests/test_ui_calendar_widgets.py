"""공통 달력과 Replay 필터 정렬의 프론트 검증. 실제 DB·Excel·AI를 사용하지 않는다."""

import ast
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QDate, QPoint, QPointF, Qt
from PySide6.QtGui import QPalette, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QTableView

from setron.ui.appearance import manager
from setron.ui.appearance_data import Appearance
from setron.ui.calendar_widgets import CalendarWidget, DateEdit
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.import_delete import ImportDeletePage
from setron.ui.import_demo import DemoExcel
from setron.ui.import_page import ImportPage
from setron.ui.import_widgets import DateField, PeriodDateField
from setron.ui.management_page import ValueEditor
from setron.ui.query_panel import QueryPanel
from setron.ui.query_state import QueryState, preview_catalog
from setron.ui.replay_page import ReplayPage
from test_ui_import_ver5 import own  # noqa: F401


def test_year_precedes_month_and_both_use_native_dropdowns(own):
    calendar = own(CalendarWidget())
    calendar.setSelectedDate(QDate(2026, 10, 5))
    calendar.resize(340, 300)
    QTest.qWait(20)
    assert calendar.year_combo.x() < calendar.month_combo.x()
    assert calendar.year_combo.currentText() == "2026년"
    assert calendar.month_combo.currentText() == "10월"
    assert not calendar.isNavigationBarVisible()
    assert calendar.years.rowCount() == 9999
    assert calendar.year_combo.currentData() == calendar.yearShown() == 2026
    assert calendar.month_combo.currentData() == calendar.monthShown() == 10
    assert calendar.locale().language() == calendar.locale().Language.Korean


def test_year_dropdown_scrolls_without_committing_another_date(own):
    calendar = own(CalendarWidget())
    calendar.setSelectedDate(QDate(2026, 10, 5))
    calendar.year_combo.showPopup()
    QTest.qWait(30)
    view = calendar.year_combo.view()
    bar = view.verticalScrollBar()
    assert view.isVisible() and bar.maximum() > 100
    assert view.window().height() < 500  # 화면 전체 높이로 열지 않고 제한된 목록 안에서 스크롤.
    before = bar.value()
    point = view.viewport().rect().center()
    wheel = QWheelEvent(QPointF(point), QPointF(view.viewport().mapToGlobal(point)), QPoint(), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
    QApplication.sendEvent(view.viewport(), wheel)
    QTest.qWait(20)
    assert bar.value() > before
    assert calendar.selectedDate() == QDate(2026, 10, 5)
    calendar.year_combo.hidePopup()


def test_calendar_days_and_dropdowns_use_readable_colors_after_theme_changes(own):
    calendar = own(CalendarWidget())
    selected = QDate(2026, 10, 5)
    calendar.setSelectedDate(selected)
    days = calendar.findChild(QTableView, "qt_calendar_calendarview")
    theme = manager()
    original = theme.appearance
    roles = QPalette.ColorRole
    try:
        for mode in ("light", "dark", "light"):
            theme.apply(Appearance(mode=mode))
            assert days.palette().color(roles.Base).name() == theme.tokens["surface"]
            assert days.palette().color(roles.Text).name() == theme.tokens["ink"]
            assert days.palette().color(roles.Highlight).name() == theme.tokens["selected"]
            assert days.palette().color(roles.HighlightedText).name() == theme.tokens["accent"]
            assert calendar.headerTextFormat().foreground().color().name() == theme.tokens["muted"]
            assert all(calendar.weekdayTextFormat(day).foreground().color().name() == theme.tokens["accent"]
                       for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday))
            assert calendar.selectedDate() == selected and days.currentIndex().data() == selected.day()
            for combo in (calendar.year_combo, calendar.month_combo):
                combo.showPopup()
                QTest.qWait(20)
                view = combo.view()
                assert view.isVisible()
                assert view.palette().color(roles.Base).name() == theme.tokens["surface"]
                assert view.palette().color(roles.Text).name() == theme.tokens["ink"]
                assert view.palette().color(roles.Highlight).name() == theme.tokens["selected"]
                assert view.palette().color(roles.HighlightedText).name() == theme.tokens["ink"]
                combo.hidePopup()
    finally:
        theme.apply(original)


def test_year_and_month_keyboard_selection_only_change_the_view_page(own):
    calendar = own(CalendarWidget())
    original = QDate(2024, 2, 29)
    calendar.setSelectedDate(original)
    calendar.year_combo.setFocus()
    QTest.keyClick(calendar.year_combo, Qt.Key.Key_Down)
    assert calendar.yearShown() == 2025 and calendar.monthShown() == 2
    calendar.month_combo.setFocus()
    QTest.keyClick(calendar.month_combo, Qt.Key.Key_Down)
    assert calendar.monthShown() == 3
    assert calendar.selectedDate() == original


def test_previous_next_month_sync_header_across_year_boundary(own):
    calendar = own(CalendarWidget())
    calendar.setSelectedDate(QDate(2026, 12, 31))
    calendar.next_button.click()
    assert calendar.year_combo.currentData() == 2027 and calendar.month_combo.currentData() == 1
    calendar.previous_button.click()
    assert calendar.year_combo.currentData() == 2026 and calendar.month_combo.currentData() == 12
    assert calendar.selectedDate() == QDate(2026, 12, 31)


def test_year_can_be_typed_with_native_combo_keyboard_search(own):
    calendar = own(CalendarWidget())
    calendar.setSelectedDate(QDate(2026, 10, 5))
    calendar.year_combo.setFocus()
    QTest.keyClicks(calendar.year_combo, "1999", delay=10)
    assert calendar.yearShown() == 1999
    assert calendar.selectedDate() == QDate(2026, 10, 5)


def test_popup_size_reserves_all_six_weeks_below_custom_header(own):
    field = own(PeriodDateField("2026-05-21"))
    field.open_calendar()
    calendar = field.calendar_menu.actions()[0].defaultWidget()
    QTest.qWait(30)
    assert calendar.height() >= calendar.header.height() + 7 * calendar.fontMetrics().height()
    from PySide6.QtWidgets import QAbstractItemView
    grid = next(view for view in calendar.findChildren(QAbstractItemView)
                if view not in (calendar.year_combo.view(), calendar.month_combo.view()))
    # 마지막 주 행까지 전체 셀의 하단이 실제 표 뷰포트 안에 있어야 한다.
    last = grid.model().index(grid.model().rowCount() - 1, grid.model().columnCount() - 1)
    assert grid.visualRect(last).bottom() < grid.viewport().height()
    field.calendar_menu.close()


def test_range_limits_years_months_and_navigation_without_changing_selection(own):
    calendar = own(CalendarWidget())
    calendar.setDateRange(QDate(2024, 6, 15), QDate(2026, 9, 20))
    calendar.setSelectedDate(QDate(2025, 2, 1))
    calendar.year_combo.setCurrentIndex(0)
    assert (calendar.yearShown(), calendar.monthShown()) == (2024, 6)
    assert not calendar.previous_button.isEnabled()
    assert not calendar.month_combo.model().item(4).isEnabled()
    calendar.year_combo.setCurrentIndex(2)
    calendar.month_combo.setCurrentIndex(11)
    assert (calendar.yearShown(), calendar.monthShown()) == (2026, 9)
    assert not calendar.next_button.isEnabled()
    assert calendar.years.rowCount() == 3
    assert calendar.selectedDate() == QDate(2025, 2, 1)


@pytest.mark.parametrize("field_type", [DateField, PeriodDateField])
def test_date_fields_reuse_common_calendar_and_only_commit_day_selection(own, field_type):
    field = own(field_type("2026-10-05"))
    field.setFixedSize(190, 42)
    field.open_calendar()
    calendar = field.calendar_menu.actions()[0].defaultWidget()
    assert isinstance(calendar, CalendarWidget)
    calendar.year_combo.setCurrentIndex(2024 - calendar.years.first)
    calendar.month_combo.setCurrentIndex(1)
    assert field.text() == "2026-10-05"
    QTest.keyClick(calendar, Qt.Key.Key_Escape)
    assert field.text() == "2026-10-05"
    field.calendar_menu.close()
    field.open_calendar()
    assert field.calendar_menu.actions()[0].defaultWidget() is calendar
    assert calendar.yearShown() == 2026 and calendar.monthShown() == 10
    calendar.activated.emit(QDate(2024, 2, 29))
    assert field.text() == "2024-02-29" and not field.calendar_menu.isVisible()
    field.selectAll()
    QTest.keyClicks(field, "20261108")
    assert field.text() == "2026-11-08" and field.hasAcceptableInput()


def test_native_date_edit_keeps_input_and_popup_date_contract(own):
    edit = own(DateEdit())
    edit.setDateRange(QDate(1000, 1, 1), QDate(9999, 12, 31))
    edit.setDate(QDate(2026, 10, 5))
    calendar = edit.calendarWidget()
    assert isinstance(calendar, CalendarWidget) and edit.calendarPopup()
    calendar._sync_header()
    assert calendar.years.first == 1000 and calendar.years.last == 9999
    calendar.year_combo.setCurrentIndex(2024 - calendar.years.first)
    assert edit.date() == QDate(2026, 10, 5)
    calendar.setSelectedDate(QDate(2024, 2, 29))
    assert edit.date() == QDate(2024, 2, 29)
    assert edit.displayFormat() == "yyyy-MM-dd"


def test_common_query_and_management_release_date_use_same_calendar(own):
    panel = own(QueryPanel(QueryState(preview_catalog())))
    assert isinstance(panel.date_edit, DateEdit)
    assert isinstance(panel.date_edit.calendarWidget(), CalendarWidget)
    editor = own(ValueEditor("release_version", "released_at", date(2024, 2, 29), {}))
    assert isinstance(editor.editor, DateEdit)
    assert isinstance(editor.editor.calendarWidget(), CalendarWidget)
    assert editor.value() == date(2024, 2, 29)
    assert editor.editor.minimumDate() == QDate(1000, 1, 1)
    editor.empty.setChecked(True)
    assert editor.value() is None and not editor.editor.isEnabled()


def test_clicking_the_query_date_text_opens_calendar_until_day_confirmation(own):
    panel = own(QueryPanel(QueryState(preview_catalog())))
    panel.set_as_of(date(2026, 10, 5))
    applied = panel.apply_draft()
    edit = panel.date_edit
    text = edit.findChild(QLineEdit)
    QTest.mouseClick(text, Qt.MouseButton.LeftButton, pos=text.rect().center())
    calendar = edit.calendarWidget()
    assert calendar.isVisible()
    assert panel.state.applied is applied

    chosen = QDate(2026, 10, 21)
    calendar.setSelectedDate(chosen)
    assert panel.state.applied is applied
    calendar.activated.emit(chosen)
    assert panel.state.applied.selection.as_of == panel.state.draft.as_of == chosen.toPython()
    assert panel.state.applied.revision == applied.revision + 1
    assert not calendar.isVisible()


@pytest.mark.parametrize("screen", ["replay", "import", "delete"])
def test_each_screen_date_popup_uses_shared_calendar(own, screen):
    if screen == "replay":
        page = own(ReplayPage(demo_dashboard_source()))
        fields = (page.start, page.end)
    elif screen == "import":
        page = own(ImportPage(excel=DemoExcel()))
        fields = (page.common_date,)
    else:
        page = own(ImportDeletePage())
        fields = (page.start, page.end)
    for field in fields:
        field.open_calendar()
        calendar = field.calendar_menu.actions()[0].defaultWidget()
        assert isinstance(calendar, CalendarWidget)
        assert calendar.year_combo.currentText().endswith("년")
        assert calendar.month_combo.currentText().endswith("월")
        field.calendar_menu.close()


@pytest.mark.parametrize("width", [1320, 746, 620])
def test_replay_reset_and_apply_align_with_adjacent_input_fields(own, width):
    page = own(ReplayPage(demo_dashboard_source()))
    page.resize(width, 1200)
    QTest.qWait(50)
    positions = [control.mapTo(page.filters, QPoint()).y() for control in (
        page.combos["group_key"], page.reset_button, page.apply_button)]
    assert len(set(positions)) == 1
    assert page.reset_button.height() == page.apply_button.height() == 38
    assert page.reset_button.x() < page.apply_button.x()
    assert page.apply_button.mapTo(page.filters, page.apply_button.rect().bottomRight()).x() <= page.filters.width()
    page.apply_button.setFocus()
    QTest.qWait(10)
    assert page.reset_button.height() == page.apply_button.height() == 38


def test_no_screen_constructs_an_unshared_native_calendar():
    root = Path(__file__).resolve().parents[1] / "src/setron/ui"
    for path in root.glob("*.py"):
        if path.name == "calendar_widgets.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        raw = {"QCalendarWidget", "QDateEdit", "QDateTimeEdit"}
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        assert not any((isinstance(node.func, ast.Name) and node.func.id in raw)
                       or (isinstance(node.func, ast.Attribute) and node.func.attr in raw)
                       for node in calls), path.name
