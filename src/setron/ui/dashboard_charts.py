"""데이터로 그리는 QtCharts. Figma의 예시 SVG 그래프를 고정 이미지로 사용하지 않는다."""

from collections.abc import Iterable, Mapping
from functools import partial
from html import escape

from PySide6.QtCharts import QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView, QLineSeries, QPieSeries, QScatterSeries, QValueAxis
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsSimpleTextItem, QToolTip

from setron.features.aggregation.schemas import Summary
from setron.features.version.schemas import CompareStatus
from setron.ui.appearance import chart_font, chart_text_font, ui_color
from setron.ui.dashboard_data import denominator_equipment_ids
from setron.ui.theme import INK, LINE, PRIMARY, SECONDARY, SOFT, font


SERIES_COLORS = {
    "c3": "#5068d9", "c4": "#8b67d1", "o2": "#26859d", "o3": "#d9803e", "o5": "#c15a95",
    "inline-ox": "#66748e", "inline-o2": "#2e946f", "cluster-ox": "#d2516b", "cluster-o2": "#6b7ccd",
}


def series_color(key, index=0):
    # 추가 설비군도 조회로 시리즈 순서가 바뀌었다고 범례/선의 색이 달라지면 안 된다.
    return SERIES_COLORS.get(key, tuple(SERIES_COLORS.values())[sum(map(ord, key)) % len(SERIES_COLORS)])


def series_button_style(color):
    fill = QColor.fromRgbF(*(0.4 + 0.6 * channel for channel in QColor(color).getRgbF()[:3])).name()
    return (f"QPushButton {{color:{INK};background:white;border:1px solid {color};border-radius:8px;"
            "padding:6px 12px;min-height:0;}"
            f"QPushButton:checked:enabled {{background:{fill};border-color:{color};}}"
            f"QPushButton:disabled {{color:#8b95a8;background:{SOFT};border-color:{LINE};}}"
            f"QPushButton:focus {{border-color:{PRIMARY};}}")


def trend_point_rate(point):
    """관측이 부족하거나 비교 분모가 없으면 차트에 찍을 값이 없다."""
    return None if point.missing_equipment else point.summary.rate


def bar_plot(snapshot):
    """원본 구역과 설비군별 막대가 실제로 세는 설비를 돌려준다."""
    areas = [(row.label, set(row.cells[-1].equipment_ids)) for row in snapshot.rows
             if row.position is not None and row.cells[-1].summary.equipment_count]
    groups = {}
    for group in snapshot.groups:
        cohorts = tuple(tuple(eid for eid in group.equipment_ids if eid in ids) for _, ids in areas)
        if any(cohorts):
            groups[group.group_keys[0]] = group.label, cohorts
    return tuple(label for label, _ in areas), groups


def equipment_tooltip(title: str, equipment_ids: Iterable[int], equipment_names: Mapping[int, str]) -> str:
    """한눈에 읽을 수 있도록 실제 대수와 앞의 12개 호기명을 표시한다."""
    ids = tuple(dict.fromkeys(equipment_ids))
    lines = [*title.splitlines(), f"설비 {len(ids)}대"]
    lines.extend(equipment_names.get(eid, f"설비 {eid}") for eid in ids[:12])
    if len(ids) > 12:
        lines.append(f"외 {len(ids) - 12}대")
    return "<qt>" + "<br>".join(map(escape, lines)) + "</qt>"


def equipment_count_tooltip(title: str, equipment_ids: Iterable[int]) -> str:
    """차트에 표시된 집계와 실제 설비 대수를 함께 표시한다."""
    lines = [*title.splitlines(), f"설비 {len(set(equipment_ids))}대"]
    return "<qt>" + "<br>".join(map(escape, lines)) + "</qt>"


def trend_tooltip(day, label, summary):
    decided = summary.compliant + summary.below
    title = f"{day:%Y.%m.%d} · {label}\n목표 달성 {summary.compliant} / {decided}건 ({summary.rate:.1%})"
    return equipment_count_tooltip(title, denominator_equipment_ids(summary))


def composition_cohorts(summary: Summary) -> tuple[tuple[int, ...], ...]:
    """N 이상, N 미만, 판단 불가에 각각 포함된 물리 설비."""
    cohorts = {status: [] for status in (CompareStatus.AT_OR_ABOVE, CompareStatus.BELOW, CompareStatus.UNDETERMINED)}
    for equipment in summary.equipment:
        for status in {family.status for family in equipment.families}:
            cohorts[status].append(equipment.record.equipment_id)
    return tuple(tuple(ids) for ids in cohorts.values())


class DashboardChart(QChartView):
    def __init__(self):
        super().__init__()
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(350)
        self.setMaximumHeight(390)
        self.setMinimumWidth(0)
        self.setBackgroundBrush(QBrush(ui_color("white")))
        self._donut_mode = False
        self._active_tooltip = None
        self.setAccessibleName("대시보드 차트 · 같은 수치는 수치표 보기에서 확인할 수 있습니다")

    def _chart(self):
        self._hide_hover_tooltip()
        chart = QChart()
        chart.setBackgroundBrush(QBrush(ui_color("white")))
        chart.setBackgroundRoundness(0)
        chart.legend().hide()
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        self._donut_mode = False
        previous = self.chart()
        self.setChart(chart)
        if previous:
            previous.deleteLater()
        return chart

    def clear(self):
        self._chart()

    def _hide_hover_tooltip(self):
        if self._active_tooltip is None:
            return
        _, text = self._active_tooltip
        self._active_tooltip = None
        if QToolTip.text() == text:
            QToolTip.hideText()

    def _hover_tooltip(self, chart, target, text):
        if chart is not self.chart():
            return
        if text:
            self._active_tooltip = target, text
            QToolTip.showText(QCursor.pos(), text, self)
        elif self._active_tooltip is not None and self._active_tooltip[0] == target:
            self._hide_hover_tooltip()

    @staticmethod
    def _axis(axis):
        chart_font(axis, "labelsFont", font(11))
        axis.setLabelsColor(ui_color(SECONDARY))
        axis.setLinePen(QPen(ui_color("#e9eef6")))
        axis.setGridLinePen(QPen(ui_color("#e9eef6")))

    def composition(self, snapshot):
        chart = self._chart()
        counts = (snapshot.summary.compliant, snapshot.summary.below, snapshot.summary.undetermined)
        total = sum(counts)
        series = QPieSeries()
        series.setPieSize(0.90)
        series.setHoleSize(0.65)
        for title, count, color, ids in zip(("N 이상", "N 미만", "판단 불가"), counts, (PRIMARY, "#fdf0b8", "#e5e7eb"),
                                            composition_cohorts(snapshot.summary)):
            if count:
                part = series.append(f"{title} {count}건 ({count / total:.1%})", count)
                part.setColor(ui_color(color))
                part.setBorderColor(ui_color(color))
                tooltip = equipment_count_tooltip(part.label(), ids)
                part.hovered.connect(lambda hovered, target=part, text=tooltip: self._hover_tooltip(chart, target, text if hovered else ""))
        chart.addSeries(series)
        self._donut_mode = True
        chart.legend().hide()  # 범례는 화면 오른쪽 상태 칩으로 표시한다.
        rate = snapshot.summary.rate
        center = QGraphicsSimpleTextItem("계산 불가" if rate is None else f"{rate:.1%}", chart)
        chart_text_font(center, font(34, medium=True, numeric=True))
        center.setBrush(QBrush(ui_color(INK)))
        decided = snapshot.summary.compliant + snapshot.summary.below
        caption = QGraphicsSimpleTextItem(f"{snapshot.summary.compliant} / {decided}건 목표 달성" if total else "자료 없음", chart)
        chart_text_font(caption, font(13))
        caption.setBrush(QBrush(ui_color(SECONDARY)))
        def place_center(rect):
            size, below = center.boundingRect(), caption.boundingRect()
            top = rect.center().y() - (size.height() + 6 + below.height()) / 2
            center.setPos(rect.center().x() - size.width() / 2, top)
            caption.setPos(rect.center().x() - below.width() / 2, top + size.height() + 6)

        chart._setron_place_labels = place_center
        chart.plotAreaChanged.connect(place_center)
        # 조회를 다시 적용하면 차트 영역이 이미 배치되어 변경 신호가 추가로 오지 않을 수 있다.
        place_center(chart.plotArea())
        self.setAccessibleDescription(" · ".join(f"{name} {value}건" for name, value in zip(("N 이상", "N 미만", "판단 불가"), counts)))

    def bars(self, plot, on_click, visible_groups):
        chart = self._chart()
        areas, groups = plot
        series = QBarSeries()
        shown = {}
        for key, (label, cohorts) in groups.items():
            if key not in visible_groups:
                continue
            group_set = QBarSet(label)
            group_set.setColor(ui_color(series_color(key)))
            chart_font(group_set, "labelFont", font(10))
            group_set.append([len(ids) for ids in cohorts])
            series.append(group_set)
            shown[group_set] = label, cohorts
        if not shown:
            return
        series.setLabelsVisible(False)
        chart.addSeries(series)
        x = QBarCategoryAxis()
        x.append(list(areas))
        x.setGridLineVisible(False)
        y = QValueAxis()
        maximum = max((group.at(i) for group in series.barSets() for i in range(group.count())), default=0)
        y.setRange(0, max(2, maximum + 1))
        y.setLabelFormat("%d")
        y.setTitleText("대수 (대)")
        chart_font(y, "titleFont", font(12))
        y.setTickCount(min(5, int(y.max()) + 1))
        for axis, alignment in ((x, Qt.AlignmentFlag.AlignBottom), (y, Qt.AlignmentFlag.AlignLeft)):
            self._axis(axis)
            chart.addAxis(axis, alignment)
            series.attachAxis(axis)
        def clicked(index, bar_set):
            if bar_set in shown and 0 <= index < len(areas):
                label, cohorts = shown[bar_set]
                if ids := cohorts[index]:
                    on_click(f"{areas[index]} / {label}", ids)
        series.clicked.connect(clicked)

        def hovered(entering, index, bar_set):
            target = bar_set, index
            if not entering:
                self._hover_tooltip(chart, target, "")
            elif bar_set in shown and 0 <= index < len(areas):
                label, cohorts = shown[bar_set]
                if ids := cohorts[index]:
                    title = f"{areas[index]} / {label}"
                    self._hover_tooltip(chart, target, equipment_count_tooltip(title, ids))
        series.hovered.connect(hovered)

    def _trend_hover(self, chart, series, group, segment, position, hovered):
        if not hovered:
            self._hover_tooltip(chart, series, "")
            return
        # 선분 위에서는 그 선분을 실제로 구성하는 관측일 중 가장 가까운 값을 보여준다.
        index, _ = min(segment, key=lambda item: abs(item[0] - position.x()))
        point = group.points[index]
        self._hover_tooltip(chart, series, trend_tooltip(point.day, group.label, point.summary))

    def trend(self, snapshot, visible_groups):
        chart = self._chart()
        dates = [point.day for point in snapshot.observations]
        x = QBarCategoryAxis()
        x.append([day.strftime("%m-%d") for day in dates])
        x.setGridLineVisible(False)
        y = QValueAxis()
        y.setRange(0, 100)
        y.setTickCount(5)
        y.setLabelFormat("%.0f%%")
        for axis, alignment in ((x, Qt.AlignmentFlag.AlignBottom), (y, Qt.AlignmentFlag.AlignLeft)):
            self._axis(axis)
            chart.addAxis(axis, alignment)
        for index, group in enumerate(snapshot.trends):
            if group.group_key not in visible_groups:
                continue
            segments, current = [], []
            for i, point in enumerate(group.points):
                rate = trend_point_rate(point)
                # 관측 부족/분모 없음은 선을 끊는다. 이전 값이나 0%로 채우지 않는다.
                if rate is None:
                    if current:
                        segments.append(current)
                    current = []
                else:
                    current.append((i, rate * 100))
            if current:
                segments.append(current)
            for segment in segments:
                series = QLineSeries() if len(segment) > 1 else QScatterSeries()
                series.setName(group.label)
                series.setColor(ui_color(series_color(group.group_key, index)))
                if isinstance(series, QLineSeries):
                    series.setPen(QPen(series.color(), 2.5))
                    series.setPointsVisible(True)
                else:
                    series.setMarkerSize(6)
                for px, py in segment:
                    series.append(px, py)
                chart.addSeries(series)
                series.attachAxis(x)
                series.attachAxis(y)
                series.hovered.connect(partial(self._trend_hover, chart, series, group, segment))

    def leaveEvent(self, event):
        self._hide_hover_tooltip()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._donut_mode:
            self.chart().legend().setAlignment(Qt.AlignmentFlag.AlignRight if event.size().width() >= 800 else Qt.AlignmentFlag.AlignBottom)
