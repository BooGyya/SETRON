"""기준일을 선택해도 결과는 유지하며, 조회 버튼으로 그날 이전 최신 관측을 조회한다."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from datetime import date, timedelta

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QTableView

from setron.ui.app import create_window
from setron.ui.dashboard_data import NoObservationBefore
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.query_state import AppliedQuery, FabOption, LineOption, QuerySelection, QueryState
from setron.ui.theme import install_theme


def applied(selection):
    return AppliedQuery(selection, 1, 0, "")


def test_snapshot_uses_latest_observation_on_or_before_as_of():
    source = demo_dashboard_source()
    assert source.snapshot(applied(QuerySelection())).base_date == date(2026, 10, 21)
    past = source.snapshot(applied(QuerySelection(as_of=date(2026, 8, 30))))
    assert past.base_date == date(2026, 8, 21)
    assert all(point.day <= date(2026, 8, 21) for point in past.observations)
    assert source.snapshot(applied(QuerySelection(as_of=date(2026, 7, 21)))).base_date == date(2026, 7, 21)
    with pytest.raises(NoObservationBefore, match="2026-01-01 이전"):
        source.snapshot(applied(QuerySelection(as_of=date(2026, 1, 1))))


def test_state_treats_today_or_future_as_latest_and_reset_clears_date():
    state = QueryState(demo_dashboard_source().catalog)
    state.set_as_of(date.today() + timedelta(days=3))
    assert state.draft.as_of is None
    state.set_as_of(date(2026, 8, 1))
    assert state.draft.as_of == date(2026, 8, 1) and "기준일 2026-08-01" in state.describe(state.draft)
    state.reset_draft()
    assert state.draft == QuerySelection()
    with pytest.raises(ValueError):
        state.set_draft(QuerySelection(as_of="2026-08-01"))


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    install_theme(app)
    result = create_window()
    result.resize(1600, 900)
    result.show()
    QTest.qWait(20)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def test_date_field_is_first_on_first_row_and_reset_returns_today(window):
    panel = window.query_panel
    applied = QSignalSpy(window.query_applied)
    today = date.today()
    assert panel.date_edit.date().toPython() == today
    assert panel.date_edit.mapTo(panel.card, panel.date_edit.rect().topLeft()).y() == \
        panel.combos["site_key"].mapTo(panel.card, panel.combos["site_key"].rect().topLeft()).y()
    assert panel.date_edit.mapTo(panel.card, panel.date_edit.rect().topLeft()).x() < \
        panel.combos["site_key"].mapTo(panel.card, panel.combos["site_key"].rect().topLeft()).x()
    panel.date_edit.setDate(QDate(2026, 8, 30))
    assert window.query_state.draft.as_of == date(2026, 8, 30)
    assert window.query_state.applied is None and applied.count() == 0
    panel.search_button.click()
    assert window.query_state.applied.selection.as_of == window.query_state.draft.as_of == date(2026, 8, 30)
    assert applied.count() == 1
    previous = window.query_state.applied
    panel.reset_button.click()  # 조건 초기화는 기준 날짜도 오늘(최신 관측)로 되돌린다
    assert window.query_state.draft.as_of is None and panel.date_edit.date().toPython() == today
    assert window.query_state.applied is previous and applied.count() == 1
    panel.search_button.click()
    assert applied.count() == 2 and window.query_state.applied.selection == QuerySelection()


def test_search_icon_applies_the_selected_conditions(window):
    panel = window.query_panel
    search = panel.search_button
    reset = panel.reset_button
    assert search.isVisible() and search.text() == "" and not search.icon().isNull()
    assert search.size() == reset.size() and search.iconSize() == reset.iconSize()
    assert search.y() == reset.y() and search.x() > reset.geometry().right()
    panel.set_as_of(date(2026, 8, 30))
    applied = QSignalSpy(window.query_applied)

    QTest.mouseClick(search, Qt.MouseButton.LeftButton)

    assert applied.count() == 1
    assert window.query_state.applied.selection == panel.state.draft
    assert window.pages["dashboard"].snapshot.base_date == date(2026, 8, 21)


@pytest.mark.parametrize("field", ["site_key", "fab_key", "line_key", "group_key"])
def test_filter_checkboxes_and_selection_complete_preserve_both_results_until_search(window, field):
    panel = window.query_panel
    dashboard, heatmap = window.pages["dashboard"], window.pages["heatmap"]
    source = dashboard.source
    site = source.catalog.sites[0].key
    source.catalog = replace(source.catalog, fabs=(FabOption("test-fab", "테스트 Fab", site),),
                             lines=(LineOption("test-line", "테스트 Line", site, "test-fab"),))
    window.set_query_catalog(source.catalog)
    previous = panel.apply_draft()
    snapshot = dashboard.snapshot
    applied = QSignalSpy(window.query_applied)
    panel.combos[field].click()
    popup = panel.filter_popup
    popup.all_button.click()
    key = next(iter(popup.checks))
    popup.checks[key].click()
    assert getattr(panel.state.draft, field) == key
    assert popup.isVisible() and applied.count() == 0 and panel.state.applied is previous
    assert dashboard.snapshot is heatmap.snapshot is snapshot
    popup.confirm_button.click()
    assert panel.filter_popup is None and applied.count() == 0
    assert dashboard.snapshot is heatmap.snapshot is snapshot
    window.navigate("heatmap")
    window.navigate("dashboard")
    assert dashboard.snapshot is heatmap.snapshot is snapshot and applied.count() == 0
    panel.search_button.click()
    assert applied.count() == 1 and panel.state.applied.selection == panel.state.draft
    assert dashboard.snapshot is heatmap.snapshot and dashboard.snapshot is not snapshot
    assert dashboard.snapshot.query is panel.state.applied


def test_calendar_day_selection_waits_for_search_to_update_dashboard_and_heatmap(window):
    panel = window.query_panel
    panel.set_as_of(date(2026, 10, 21))
    previous = panel.apply_draft()
    previous_snapshot = window.pages["dashboard"].snapshot
    applied = QSignalSpy(window.query_applied)
    text = panel.date_edit.findChild(QLineEdit)
    QTest.mouseClick(text, Qt.MouseButton.LeftButton, pos=text.rect().center())
    calendar = panel.date_edit.calendarWidget()
    assert calendar.isVisible()
    QTest.mouseClick(calendar.previous_button, Qt.MouseButton.LeftButton)
    calendar.month_combo.setCurrentIndex(7)
    assert (calendar.yearShown(), calendar.monthShown()) == (2026, 8)
    assert calendar.selectedDate() == QDate(2026, 10, 21)
    assert window.query_state.applied is previous and applied.count() == 0

    grid = calendar.findChild(QTableView, "qt_calendar_calendarview")
    model = grid.model()
    day = next(index for row in range(1, model.rowCount()) for column in range(model.columnCount())
               if (index := model.index(row, column)).data() in (25, "25"))
    QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=grid.visualRect(day).center())
    assert not calendar.isVisible() and applied.count() == 0
    assert window.query_state.draft.as_of == date(2026, 8, 25)
    assert window.query_state.applied is previous
    assert window.pages["dashboard"].snapshot is window.pages["heatmap"].snapshot is previous_snapshot
    panel.search_button.click()
    assert applied.count() == 1
    assert window.query_state.applied.selection.as_of == date(2026, 8, 25)
    dashboard = window.pages["dashboard"]
    assert dashboard.snapshot is window.pages["heatmap"].snapshot
    assert dashboard.snapshot.base_date == date(2026, 8, 21)
    snapshot = dashboard.snapshot
    panel.date_edit.setDate(QDate(2026, 1, 1))
    assert applied.count() == 1 and dashboard.snapshot is window.pages["heatmap"].snapshot is snapshot
    panel.search_button.click()
    assert applied.count() == 2
    assert dashboard.snapshot is None and "2026-01-01 이전" in dashboard.message.text()
    assert "2026-01-01 이전" in window.pages["heatmap"].message.text()
