"""대시보드의 좁은 화면 가독성, 키보드 수치 조회와 날짜축 데이터 보존 회귀."""

from dataclasses import replace
from datetime import date, timedelta

import pytest
from PySide6.QtCharts import QCategoryAxis
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QGraphicsSimpleTextItem, QGraphicsTextItem, QLabel

from setron.ui.dashboard_charts import DashboardChart
from setron.ui.dashboard_data import trend_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.appearance_data import Appearance
from test_ui_dashboard import app, applied, source, window  # noqa: F401


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
def test_dense_trend_retains_every_point_and_readable_endpoint_dates(app, size):
    source = demo_dashboard_source()
    data = source.snapshot(applied(source))
    data = trend_window(data, min(source.analysis_dates), max(source.analysis_dates), 1)
    chart = DashboardChart()
    chart.resize(size[0] - 310, 350)
    chart.show()
    try:
        chart.trend(data, {group.group_key for group in data.trends})
        QTest.qWait(60)
        dates = [point.day for point in data.observations]
        axis, = chart.chart().axes(Qt.Orientation.Horizontal)
        assert isinstance(axis, QCategoryAxis)
        labels = axis.categoriesLabels()
        assert labels[0] == dates[0].strftime("%m-%d")
        assert labels[-1] == dates[-1].strftime("%m-%d")
        assert 2 <= len(labels) < len(dates)
        assert not axis.labelsTruncated()
        for group in data.trends:
            actual = sorted((point.x(), point.y()) for series in chart.chart().series()
                            if series.name() == group.label for point in series.points())
            expected = [(index, point.summary.rate * 100) for index, point in enumerate(group.points)
                        if point.summary.rate is not None]
            assert actual == expected
    finally:
        chart.close()
        chart.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("font_size", ["small", "normal", "large"])
@pytest.mark.parametrize("count", [0, 1, 90])
def test_trend_end_dates_fit_actual_chart_bounds_across_year_and_font_sizes(app, font_size, count):
    current = app.appearance_manager
    previous = current.appearance
    current.apply(Appearance(font_size=font_size))
    source = demo_dashboard_source()
    data = source.snapshot(applied(source))
    points = tuple(replace(data.observations[-1], day=date(2026, 12, 1) + timedelta(days=i)) for i in range(count))
    group = replace(data.trends[0], points=points)
    data = replace(data, observations=points, trends=(group,))
    chart = DashboardChart()
    chart.show()
    try:
        chart.trend(data, {group.group_key})
        for width in (1280, 650, 420):
            chart.resize(width, 350)
            QTest.qWait(40)
            axis, = chart.chart().axes(Qt.Orientation.Horizontal)
            labels = axis.categoriesLabels()
            assert not axis.labelsTruncated()
            if not points:
                assert labels == [] and not chart.chart().series()
                continue
            pattern = "%Y-%m-%d" if count > 1 else "%m-%d"
            assert labels[0] == points[0].day.strftime(pattern)
            assert labels[-1] == points[-1].day.strftime(pattern)
            bounds = [item.sceneBoundingRect() for item in chart.chart().scene().items()
                      if isinstance(item, QGraphicsTextItem) and item.toPlainText() in labels]
            assert len(bounds) == len(labels)
            assert all(chart.chart().sceneBoundingRect().contains(rect) for rect in bounds)
            ordered = sorted(bounds, key=lambda rect: rect.left())
            assert all(left.right() + 4 <= right.left() for left, right in zip(ordered, ordered[1:]))
            actual = sorted(point.x() for series in chart.chart().series() for point in series.points())
            assert actual == list(range(count))
    finally:
        chart.close()
        chart.deleteLater()
        current.apply(previous)
        app.processEvents()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
def test_pie_labels_fit_inside_hole_and_legend_stays_in_card(window, size):
    window.query_panel.apply_draft()
    page = window.pages["dashboard"]
    page.tabs.setCurrentIndex(1)
    window.resize(*size)
    QTest.qWait(70)
    plot = page.chart.chart().plotArea()
    series, = page.chart.chart().series()
    diameter = min(plot.width(), plot.height()) * series.holeSize()
    text_bounds = [item.sceneBoundingRect() for item in page.chart.chart().childItems()
                   if isinstance(item, QGraphicsSimpleTextItem)]
    assert page.chart.width() >= 350
    assert all(bounds.width() < diameter for bounds in text_bounds)
    assert max(bounds.bottom() for bounds in text_bounds) - min(bounds.top() for bounds in text_bounds) < diameter
    legend = page.composition_legend.rect().translated(page.composition_legend.mapTo(page.content, QPoint()))
    assert page.content.rect().contains(legend)
    if size[0] == 960:
        graph = page.chart.rect().translated(page.chart.mapTo(page.content, QPoint()))
        assert graph.bottom() < legend.top()


def test_numeric_table_is_reachable_by_keyboard_and_matches_each_view(window):
    window.query_panel.apply_draft()
    page = window.pages["dashboard"]
    for mode in (1, 2, 3):
        page.tabs.setCurrentIndex(mode)
        QTest.qWait(20)
        assert page.data_toggle.isVisible() and page.data_toggle.isEnabled()
        page.data_toggle.setFocus()
        QTest.keyClick(page.data_toggle, Qt.Key.Key_Space)
        assert page.data_table.isVisible() and page.data_table.rowCount() > 0
        if mode == 1:
            assert [page.data_table.item(row, 1).text() for row in range(3)] == ["3", "1", "1"]
            assert page.data_table.item(0, 2).text() == "60.0%"
        QTest.keyClick(page.data_toggle, Qt.Key.Key_Space)
        assert not page.data_table.isVisible()


def test_accessible_chart_description_follows_new_query_and_view(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    page.tabs.setCurrentIndex(1)
    assert "N 이상 3건" in page.chart.accessibleDescription()
    notes = [widget.text() for widget in page.composition_legend.findChildren(QLabel)]
    assert any("판단 가능 4건 기준" in text and "전체 5건 기준" in text for text in notes)
    page.tabs.setCurrentIndex(2)
    window.query_panel.select("site_key", "s2")
    window.query_panel.apply_draft()
    assert "막대 그래프" in page.chart.accessibleDescription()
    assert "N 이상 1건" in page.chart.accessibleDescription()
    assert "N 이상 3건" not in page.chart.accessibleDescription()
    page.tabs.setCurrentIndex(3)
    assert "꺾은선 그래프" in page.chart.accessibleDescription()
    assert "Inline O2" in page.chart.accessibleDescription()
    page.toggle_all_button.click()
    assert "표시한 설비군이 없습니다" in page.chart.accessibleDescription()
    page.chart.clear()
    assert page.chart.accessibleDescription() == "표시할 차트 자료가 없습니다."


def test_compact_metrics_preserve_one_row_and_restore_wide_density(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    for size, compact in (((960, 600), True), ((1600, 960), False), ((1100, 800), True)):
        window.resize(*size)
        QTest.qWait(60)
        cards = tuple(page.metrics.values())
        assert len({card.mapTo(page, QPoint()).y() for card in cards}) == 1
        assert all(card.height() == 64 if compact else card.height() > 64 for card in cards)
        assert all(card.value.font().pixelSize() == (22 if compact else 30) for card in cards)
        assert window.scroll.widget().width() == window.scroll.viewport().width()
