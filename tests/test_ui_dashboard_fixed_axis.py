"""가로 스크롤 중 고정된 세로축과 실제 데이터 좌표의 정렬을 검증한다."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QFontMetricsF
from PySide6.QtTest import QTest

from setron.ui.replay_widgets import ReplayChart
from test_ui_dashboard import app  # noqa: F401
from test_ui_dashboard_ux import dense_window  # noqa: F401


@pytest.mark.parametrize("mode", [2, 3])
@pytest.mark.parametrize("font_size", ["small", "normal", "large"])
def test_fixed_axis_stays_visible_and_aligned_when_plot_scrolls_and_resizes(app, dense_window, mode, font_size):
    window = dense_window
    page = window.pages["dashboard"]
    page.tabs.setCurrentIndex(mode)
    appearance = app.appearance_manager
    previous = appearance.appearance
    appearance.apply(replace(previous, font_size=font_size, mode="dark" if font_size == "large" else "light"))
    try:
        for width in (960, 1600):
            window.resize(width, 960)
            QTest.qWait(60)
            fixed, scroll = page.chart_row.fixed_axis, page.chart_row.scroll
            chart = page.chart.chart()
            axis, = chart.axes(Qt.Orientation.Vertical)
            assert fixed.isVisible() and fixed.axis is axis
            assert not axis.labelsVisible() and not axis.isTitleVisible()
            assert 480 <= page.chart.height() <= 520
            assert scroll.horizontalScrollBar().maximum() > 0
            origin = fixed.mapTo(page.chart_row, QPoint())
            assert origin.x() < scroll.mapTo(page.chart_row, QPoint()).x()
            series = chart.series()[0]
            if mode == 2:
                last = series.barSets()[0].count() - 1
                last_point = QPointF(last, series.barSets()[0].at(last))
            else:
                last = len(page.trend_snapshot.observations) - 1
                series = next(series for series in chart.series() if series.points()[-1].x() == last)
                last_point = series.points()[-1]

            for position in (0, scroll.horizontalScrollBar().maximum()):
                scroll.horizontalScrollBar().setValue(position)
                QTest.qWait(10)
                assert fixed.mapTo(page.chart_row, QPoint()) == origin
                ticks = fixed.tick_positions()
                assert {label for label, _ in ticks} == {"0%", "25%", "50%", "75%", "100%"}
                metrics = QFontMetricsF(axis.labelsFont())
                for label, point in ticks:
                    bounds = metrics.boundingRect(label)
                    bounds.moveRight(point.x())
                    bounds.moveCenter(QPointF(bounds.center().x(), point.y()))
                    assert QRectF(fixed.rect()).contains(bounds)
                    value = float(label.removesuffix("%"))
                    plot_point = chart.mapToScene(chart.mapToPosition(QPointF(0, value), series))
                    local_point = page.chart.mapFromScene(plot_point)
                    expected_y = fixed.mapFromGlobal(page.chart.mapToGlobal(local_point)).y()
                    assert abs(point.y() - expected_y) <= 1.5
                if axis.titleText():
                    title = fixed.title_bounds()
                    assert not title.isEmpty() and QRectF(fixed.rect()).contains(title)
                assert window.scroll.horizontalScrollBar().maximum() == 0
                assert window.scroll.widget().width() == window.scroll.viewport().width()

            local_point = page.chart.mapFromScene(chart.mapToScene(chart.mapToPosition(last_point, series)))
            assert scroll.viewport().rect().contains(page.chart.mapTo(scroll.viewport(), local_point))
    finally:
        appearance.apply(previous)


def test_fixed_axis_hides_for_pie_empty_groups_and_heatmap_and_replay_keeps_tall_height(dense_window):
    page = dense_window.pages["dashboard"]
    page.tabs.setCurrentIndex(2)
    QTest.qWait(30)
    assert page.chart_row.fixed_axis.isVisible() and page.chart.height() >= 480
    page.tabs.setCurrentIndex(1)
    QTest.qWait(30)
    assert page.chart_row.fixed_axis.isHidden()
    assert 350 <= page.chart.height() <= 390
    page.tabs.setCurrentIndex(3)
    page.toggle_all_button.click()
    QTest.qWait(30)
    assert not page.chart.chart().series()
    assert page.chart_row.fixed_axis.isHidden() and page.chart_row.fixed_axis.axis is None
    page.tabs.setCurrentIndex(0)
    assert page.chart_row.isHidden()
    replay = ReplayChart()
    try:
        assert replay.minimumHeight() == 480 and replay.maximumHeight() == 520
    finally:
        replay.deleteLater()
