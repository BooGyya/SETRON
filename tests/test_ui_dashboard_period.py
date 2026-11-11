"""공통 기준 날짜의 기간 선택과 추이 간격·필터 상태를 함께 검증한다."""

from datetime import timedelta
from itertools import combinations

from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QTableView

from setron.ui.app import create_window
from setron.ui.query_state import QuerySelection
from test_ui_dashboard import DAY1, DAY2, app, source, window  # noqa: F401


def _set_period(page, start, end):
    page.trend_start.setDate(QDate(start.year, start.month, start.day))
    page.trend_end.setDate(QDate(end.year, end.month, end.day))
    QTest.qWait(20)


def _visible_group_names(page):
    return {control.text() for control in page.series_widgets if control.isVisible()}


def test_daily_range_updates_points_and_chips_only_in_trend_view(window, source):
    source.records = tuple(record for record in source.records
                           if not (record.equipment_id == 5 and record.base_date == DAY2))
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    assert not page.trend_filters.isVisible()
    page.tabs.setCurrentIndex(3)
    QTest.qWait(20)

    assert page.trend_filters.isVisible()
    assert page.trend_step == 1 and page.trend_interval.day_value == 1
    assert page.trend_interval.day_dialog is None
    assert page.trend_start.date().toPython() == DAY1
    assert page.trend_end.date().toPython() == DAY2
    assert "Inline O2" in _visible_group_names(page)
    _set_period(page, DAY2, DAY2)

    assert [point.day for point in page.trend_snapshot.observations] == [DAY2]
    assert _visible_group_names(page) == {"O2", "O3"}
    assert {series.name() for series in page.chart.chart().series()} == {"O2", "O3"}
    assert all(point.x() == 0 for series in page.chart.chart().series() for point in series.points())
    page.tabs.setCurrentIndex(1)
    assert not page.trend_filters.isVisible()
    page.tabs.setCurrentIndex(3)
    assert page.trend_start.date().toPython() == page.trend_end.date().toPython() == DAY2


def test_invalid_trend_period_hides_chart_then_recovers_when_valid(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    page.tabs.setCurrentIndex(3)
    _set_period(page, DAY2, DAY1)

    assert page.trend_notice.isVisible() and page.trend_notice.text()
    assert not page.chart.isVisible()
    _set_period(page, DAY1, DAY2)

    assert not page.trend_notice.isVisible()
    assert page.chart.isVisible() and page.chart.chart().series()
    assert [point.day for point in page.trend_snapshot.observations] == [
        DAY1 + timedelta(days=offset) for offset in range((DAY2 - DAY1).days + 1)
    ]


def test_interval_reuses_loaded_points_and_reports_actual_observation_day(window, source, monkeypatch):
    page = window.pages["dashboard"]
    window.query_panel.set_draft(QuerySelection(as_of=DAY2, site_key="s1", group_key="o3"))
    window.query_panel.apply_draft()
    page.tabs.setCurrentIndex(3)
    snapshot = page.snapshot
    metrics = {name: metric.value.text() for name, metric in page.metrics.items()}
    start, end = DAY1 + timedelta(days=1), DAY2 - timedelta(days=1)
    _set_period(page, start, end)

    def unexpected_read(_query):
        raise AssertionError("재생 간격 변경은 DB 자료를 다시 조회하지 않아야 합니다.")

    monkeypatch.setattr(source, "snapshot", unexpected_read)
    page.trend_interval.buttons["week"].click()
    QTest.qWait(20)

    expected_days = [start + timedelta(days=7 * index) for index in range(5)]
    assert expected_days[-1] == end
    assert page.trend_step == "week"
    trend, = page.trend_snapshot.trends
    assert [point.day for point in trend.points] == expected_days
    assert all(point.source_day == DAY1 and point.summary.rate == 1 for point in trend.points)
    series, = page.chart.chart().series()
    assert series.name() == "O3" and [point.y() for point in series.points()] == [100] * 5
    series.hovered.emit(series.points()[0], True)
    tooltip = page.chart._active_tooltip[1]
    assert DAY1.isoformat() in tooltip or DAY1.strftime("%Y.%m.%d") in tooltip
    page.data_toggle.setChecked(True)
    table = page.data_table
    assert [table.horizontalHeaderItem(index).text() for index in range(2)] == ["기준일", "마지막 관측일"]
    assert table.rowCount() == len(expected_days)
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == [str(day) for day in expected_days]
    assert all(table.item(row, 1).text() == str(DAY1) for row in range(table.rowCount()))
    assert page.snapshot is snapshot
    assert {name: metric.value.text() for name, metric in page.metrics.items()} == metrics


def test_global_query_limits_period_and_keeps_bar_scope_when_switching_views(window):
    page = window.pages["dashboard"]
    selection = QuerySelection(as_of=DAY1, site_key="s1", fab_key="f1", line_key="l1", group_key="o3")
    window.query_panel.set_draft(selection)
    window.query_panel.apply_draft()
    snapshot = page.snapshot
    page.tabs.setCurrentIndex(3)
    _set_period(page, DAY1, DAY2)

    assert page.trend_end.date().toPython() == DAY1
    assert all(point.day <= DAY1 for point in page.trend_snapshot.observations)
    assert _visible_group_names(page) == {"O3"}
    assert page.trend_snapshot.equipment_ids == (1,)
    assert page.snapshot is snapshot and window.query_state.applied.selection == selection
    page.tabs.setCurrentIndex(2)
    assert not page.trend_filters.isVisible()
    bars = page.chart.chart().series()[0].barSets()
    assert [bar.label() for bar in bars] == ["O3"] and bars[0].at(0) == 100


def test_calendar_confirmation_and_global_reset_restore_full_daily_period(app, source):
    window = create_window(source)
    window.resize(1600, 960)
    window.show()
    try:
        panel = window.query_panel
        panel.set_draft(QuerySelection(as_of=DAY2, site_key="s1", group_key="o3"))
        panel.apply_draft()
        page = window.pages["dashboard"]
        page.tabs.setCurrentIndex(3)
        window.scroll.ensureWidgetVisible(panel.range_button)
        QTest.qWait(20)
        applied = QSignalSpy(window.query_applied)

        assert panel.range_button.parentWidget() is panel.original_date_field
        assert panel.range_button.isVisible() and not panel.date_edit.isVisible()
        assert not page.trend_start.isVisible() and not page.trend_end.isVisible()
        QTest.mouseClick(panel.range_button, Qt.MouseButton.LeftButton)
        dialog = panel.range_button.dialog
        calendar = dialog.calendar
        assert dialog.isVisible() and calendar.isVisible()
        grid = calendar.findChild(QTableView, "qt_calendar_calendarview")
        model = grid.model()
        day = next(index for row in range(1, model.rowCount()) for column in range(model.columnCount())
                   if (index := model.index(row, column)).data() in (21, "21"))
        QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=grid.visualRect(day).center())
        assert applied.count() == 0
        QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=grid.visualRect(day).center())
        QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)

        assert not dialog.isVisible() and applied.count() == 1
        assert window.query_state.applied.selection.as_of == DAY1
        assert page.trend_start.date().toPython() == page.trend_end.date().toPython() == DAY1
        assert [point.day for point in page.trend_snapshot.observations] == [DAY1]
        page.tabs.setCurrentIndex(2)
        assert panel.date_edit.isVisible() and not panel.range_button.isVisible()
        assert panel.date_edit.date().toPython() == DAY1
        page.tabs.setCurrentIndex(3)
        assert panel.range_button.isVisible() and not panel.date_edit.isVisible()
        window.navigate("heatmap")
        assert panel.date_edit.isVisible() and not panel.range_button.isVisible()
        window.navigate("dashboard")
        assert panel.range_button.isVisible() and not panel.date_edit.isVisible()
        page.trend_interval.buttons["week"].click()
        assert page.trend_step == "week"
        panel.reset_button.click()

        assert applied.count() == 2 and window.query_state.draft == QuerySelection()
        assert page.trend_step == 1 and page.trend_interval.buttons[None].isChecked()
        assert page.trend_interval.day_value == 1
        assert page.trend_start.date().toPython() == DAY1
        assert page.trend_end.date().toPython() == DAY2
        assert (panel.range_button.start, panel.range_button.end) == (DAY1, DAY2)
        assert [point.day for point in page.trend_snapshot.observations] == [
            DAY1 + timedelta(days=offset) for offset in range((DAY2 - DAY1).days + 1)
        ]
        assert page.chart.isVisible() and not page.trend_notice.isVisible()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_narrow_period_controls_and_series_labels_fit_without_overlap(window):
    window.resize(960, 760)
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    page.tabs.setCurrentIndex(3)
    page.trend_interval.buttons["week"].click()
    QTest.qWait(50)

    visible_series = [control for control in page.series_widgets if control.isVisible()]
    assert visible_series
    for control in visible_series:
        assert control.height() >= max(32, control.fontMetrics().height() + 8)
        assert page.series_grid.rect().contains(control.geometry())
    interval = page.trend_interval
    assert interval.rect().contains(interval.segment.geometry())
    controls = tuple(interval.buttons.values())
    assert all(control.isVisible() and interval.segment.rect().contains(control.geometry()) for control in controls)
    assert all(not left.geometry().intersects(right.geometry()) for left, right in combinations(controls, 2))
    assert window.scroll.horizontalScrollBar().maximum() == 0


def test_bulk_chart_selection_only_changes_visible_series_without_querying(window, source, monkeypatch):
    panel, page = window.query_panel, window.pages["dashboard"]
    panel.set_draft(QuerySelection(as_of=DAY2, site_key="s1"))
    panel.apply_draft()
    snapshot = page.snapshot
    applied = window.query_state.applied

    def unexpected_read(_query):
        raise AssertionError("범례 전체 선택은 자료를 다시 조회하지 않아야 합니다.")

    monkeypatch.setattr(source, "snapshot", unexpected_read)
    for mode in (2, 3):
        page.tabs.setCurrentIndex(mode)
        QTest.qWait(10)
        toggle = page.toggle_all_button
        assert toggle.isVisible() and toggle.text() == "전체 해제" and toggle.isChecked()
        assert page.series_tools.layout().count() == 1
        visible = [control for control in page.series_widgets if control.isVisible()]
        assert visible
        toggle.click()
        assert all(not control.isChecked() for control in visible)
        assert toggle.text() == "전체 선택" and not toggle.isChecked()
        assert "inline-o2" in page.visible_groups
        visible[0].click()
        assert visible[0].isChecked() and not all(control.isChecked() for control in visible)
        assert toggle.text() == "전체 선택" and not toggle.isChecked()
        toggle.click()
        assert all(control.isChecked() for control in visible)
        assert toggle.text() == "전체 해제" and toggle.isChecked()
        assert page.snapshot is snapshot and window.query_state.applied is applied

    visible[0].click()
    page.tabs.setCurrentIndex(2)
    assert page.toggle_all_button.text() == "전체 선택"
    page.toggle_all_button.click()
    assert page.toggle_all_button.text() == "전체 해제"
    page.tabs.setCurrentIndex(1)
    assert not page.toggle_all_button.isVisible()
