"""대시보드의 좁은 화면 가독성과 날짜축 데이터 보존 회귀."""

from dataclasses import replace
from datetime import date, timedelta

import pytest
from PySide6.QtCharts import QCategoryAxis
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QGraphicsSimpleTextItem, QGraphicsTextItem, QLabel, QPushButton, QTableWidget

from setron.ui.dashboard_charts import DashboardChart
from setron.ui.dashboard import DashboardPage
from setron.ui.dashboard_data import trend_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.appearance_data import Appearance
from setron.ui.main_window import MainWindow
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


def test_numeric_table_and_toggle_are_removed_from_all_dashboard_views(window):
    window.query_panel.apply_draft()
    page = window.pages["dashboard"]
    for mode in (0, 1, 2, 3):
        page.tabs.setCurrentIndex(mode)
        QTest.qWait(20)
        assert not page.findChildren(QTableWidget)
        assert not any("수치표" in control.text() for control in page.findChildren(QPushButton))
        assert "수치표" not in page.chart.accessibleName()


def test_accessible_chart_description_follows_new_query_and_view(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    page.tabs.setCurrentIndex(1)
    assert "N 이상 3건" in page.chart.accessibleDescription()
    notes = [widget.text() for widget in page.composition_legend.findChildren(QLabel)]
    assert not any("판단 가능" in text or "판단 불가 포함 전체" in text for text in notes)
    assert "판단 가능 4건 기준" in page.chart.accessibleDescription()
    assert "전체 5건 기준" in page.chart.accessibleDescription()
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


@pytest.fixture
def dense_window(app):
    source = demo_dashboard_source()
    result = MainWindow(query_catalog=source.catalog)
    page = DashboardPage(result.query_state, source)
    result.register_page("dashboard", page)
    result.query_applied.connect(page.apply_query)
    result.query_panel.catalog_changed.connect(page.invalidate)
    result.show()
    result.query_panel.apply_draft()
    QTest.qWait(40)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("size", [(1600, 960), (960, 600)])
@pytest.mark.parametrize("mode", [2, 3])
def test_dense_graph_scroll_reaches_last_data_without_widening_page(dense_window, size, mode):
    window = dense_window
    page = window.pages["dashboard"]
    window.resize(*size)
    page.tabs.setCurrentIndex(mode)
    QTest.qWait(60)
    scroll = page.chart_row.scroll
    bar = scroll.horizontalScrollBar()
    assert bar.isVisible() and bar.maximum() > 0
    assert scroll.verticalScrollBar().maximum() == 0
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert window.scroll.widget().width() == window.scroll.viewport().width()

    chart = page.chart.chart()
    if mode == 2:
        axis, = chart.axes(Qt.Orientation.Horizontal)
        last = len(axis.categories()) - 1
        series, = chart.series()
        assert last >= 7
        assert all(values.count() == last + 1 for values in series.barSets())
        position = QPointF(last, series.barSets()[0].at(last))
    else:
        last = len(page.trend_snapshot.observations) - 1
        assert last > 100
        for group in page.trend_snapshot.trends:
            actual = sorted((point.x(), point.y()) for series in chart.series()
                            if series.name() == group.label for point in series.points())
            assert actual == [(index, point.summary.rate * 100) for index, point in enumerate(group.points)
                              if point.summary.rate is not None]
        series = next(series for series in chart.series() if series.points()[-1].x() == last)
        position = series.points()[-1]

    def last_point_in_viewport():
        local = page.chart.mapFromScene(chart.mapToScene(chart.mapToPosition(position, series)))
        return page.chart.mapTo(scroll.viewport(), local)

    bar.setValue(0)
    assert last_point_in_viewport().x() > scroll.viewport().width()
    bar.setValue(bar.maximum())
    QTest.qWait(20)
    assert scroll.viewport().rect().contains(last_point_in_viewport())


def test_graph_scroll_shrinks_for_filtered_sampled_empty_and_pie_views(dense_window):
    window = dense_window
    window.resize(1600, 960)
    page = window.pages["dashboard"]
    scroll = page.chart_row.scroll
    bar = scroll.horizontalScrollBar()
    page.tabs.setCurrentIndex(2)
    QTest.qWait(40)
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())

    window.query_panel.select("site_key", "giheung")
    window.query_panel.apply_draft()
    QTest.qWait(40)
    assert page.chart.chart().series() and bar.maximum() == 0

    page.tabs.setCurrentIndex(3)
    QTest.qWait(40)
    assert bar.maximum() > 0
    page.trend_interval.buttons["month"].click()
    QTest.qWait(40)
    assert page.chart.chart().series() and bar.maximum() == 0

    page.toggle_all_button.click()
    QTest.qWait(40)
    assert not page.chart.chart().series() and bar.maximum() == 0
    page.tabs.setCurrentIndex(1)
    QTest.qWait(40)
    assert page.chart.chart().series() and bar.maximum() == 0
    assert page.composition_legend.isVisible()
    assert 350 <= page.chart.width() <= 560
    assert window.scroll.horizontalScrollBar().maximum() == 0
