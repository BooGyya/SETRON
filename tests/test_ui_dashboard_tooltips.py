"""대시보드 차트의 실제 집계 범위와 설비 툴팁이 일치하는지 확인한다."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtTest import QTest
import pytest

from setron.ui import dashboard_charts
from setron.ui.dashboard_charts import DashboardChart, bar_plot, equipment_count_tooltip, equipment_tooltip
from test_ui_dashboard import DAY1, DAY2, app, applied, source


@pytest.fixture
def chart_tooltip(app, monkeypatch):
    class Tooltip:
        value = ""

        def showText(self, _position, text, _widget):
            self.value = text

        def text(self):
            return self.value

        def hideText(self):
            self.value = ""

    tooltip = Tooltip()
    monkeypatch.setattr(dashboard_charts, "QToolTip", tooltip)
    view = DashboardChart()
    view.resize(850, 380)
    view.show()
    yield view, tooltip
    view.close()
    view.deleteLater()
    app.processEvents()


def test_equipment_tooltip_deduplicates_and_bounds_names_and_escapes_plain_text():
    text = equipment_tooltip("A < B\n1 & 2", (1, 1, *range(2, 15)), {i: f"<TEST-{i}>" for i in range(1, 15)})
    assert "A &lt; B<br>1 &amp; 2<br>설비 14대" in text
    assert text.count("&lt;TEST-1&gt;") == 1
    assert "&lt;TEST-12&gt;<br>외 2대" in text
    assert "TEST-13" not in text


def test_chart_tooltip_counts_unique_equipment_without_listing_names():
    assert equipment_count_tooltip("A < B\n3건 (60.0%)", (1, 1, 2)) == "<qt>A &lt; B<br>3건 (60.0%)<br>설비 2대</qt>"


def test_composition_shows_aggregates_and_bar_uses_clicked_cohort(chart_tooltip, source):
    view, tooltip = chart_tooltip
    data = source.snapshot(applied(source))
    view.composition(data)
    compliant, below, unknown = view.chart().series()[0].slices()
    compliant.hovered.emit(True)
    assert tooltip.value == "<qt>N 이상 3건 (60.0%)<br>설비 3대</qt>"
    below.hovered.emit(True)
    compliant.hovered.emit(False)
    assert tooltip.value == "<qt>N 미만 1건 (20.0%)<br>설비 1대</qt>"
    unknown.hovered.emit(True)
    assert tooltip.value == "<qt>판단 불가 1건 (20.0%)<br>설비 1대</qt>"

    areas, groups = plot = bar_plot(data)
    requests = []
    view.bars(plot, lambda label, ids: requests.append((label, ids)), {"o2"})
    assert tooltip.value == ""
    series = view.chart().series()[0]
    bar = series.barSets()[0]
    label, cohorts = groups["o2"]
    index = cohorts.index((1, 2))
    zero_index = cohorts.index(())
    series.hovered.emit(True, zero_index, bar)
    assert tooltip.value == ""
    series.hovered.emit(True, index, bar)
    unknown.hovered.emit(False)
    assert tooltip.value == f"<qt>{areas[index]} / {label}<br>설비 2대</qt>"
    series.clicked.emit(index, bar)
    assert requests == [(f"{areas[index]} / {label}", (1, 2))]
    series.hovered.emit(False, index, bar)
    assert tooltip.value == ""


def test_native_line_segment_hover_uses_nearest_days_summary(chart_tooltip, source):
    view, tooltip = chart_tooltip
    data = source.snapshot(applied(source))
    view.trend(data, {"o3"})
    QTest.qWait(30)
    chart = view.chart()
    series = chart.series()[0]
    assert len(series.points()) == 2

    def move(value):
        position = chart.mapToScene(chart.mapToPosition(value, series))
        QTest.mouseMove(view.viewport(), view.mapFromScene(position))
        QTest.qWait(10)

    move(QPointF(0.25, 100))
    assert tooltip.value == f"<qt>{DAY1:%Y.%m.%d} · O3<br>목표 달성 1 / 1건 (100.0%)<br>설비 1대</qt>"
    move(QPointF(0.75, 100))
    assert tooltip.value == f"<qt>{DAY2:%Y.%m.%d} · O3<br>목표 달성 1 / 1건 (100.0%)<br>설비 1대</qt>"
    move(QPointF(0.75, 25))
    assert tooltip.value == ""


def test_scatter_hover_counts_only_the_determined_equipment(chart_tooltip, source):
    view, tooltip = chart_tooltip
    view.trend(source.snapshot(applied(source)), {"o2"})
    series = view.chart().series()[0]
    assert len(series.points()) == 1
    series.hovered.emit(series.points()[0], True)
    assert tooltip.value == f"<qt>{DAY2:%Y.%m.%d} · O2<br>목표 달성 1 / 2건 (50.0%)<br>설비 2대</qt>"
