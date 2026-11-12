"""데이터로 그리는 QtCharts. Figma의 예시 SVG 그래프를 고정 이미지로 사용하지 않는다."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from html import escape

from PySide6.QtCharts import QBarCategoryAxis, QBarSeries, QBarSet, QCategoryAxis, QChart, QChartView, QLineSeries, QPieSeries, QScatterSeries, QValueAxis
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QCursor, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QGraphicsSimpleTextItem, QToolTip

from setron.features.aggregation.schemas import RateCount, Summary
from setron.features.version.schemas import CompareStatus
from setron.ui.appearance import chart_font, chart_text_font, manager, ui_color
from setron.ui.appearance_data import Appearance
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
            "padding:6px 12px;min-height:20px;max-height:20px;}"
            f"QPushButton:checked:enabled {{background:{fill};border-color:{color};}}"
            f"QPushButton:disabled {{color:#8b95a8;background:{SOFT};border-color:{LINE};}}"
            f"QPushButton:focus {{border-color:{PRIMARY};}}")


@dataclass(frozen=True)
class BarPoint:
    equipment_ids: tuple[int, ...]
    counts: RateCount


def bar_plot(snapshot):
    """구역·설비군별 기존 N 판정을 모아 적용률과 실제 설비 범위를 함께 유지한다."""
    areas = [(row.label, set(row.cells[-1].equipment_ids)) for row in snapshot.rows
             if row.position is not None and row.cells[-1].summary.equipment_count]
    groups = {}
    for group in snapshot.groups:
        points = []
        for _, ids in areas:
            equipment = tuple(e for e in group.summary.equipment if e.record.equipment_id in ids)
            statuses = [family.status for e in equipment for family in e.families]
            counts = RateCount(compliant=statuses.count(CompareStatus.AT_OR_ABOVE),
                               below=statuses.count(CompareStatus.BELOW),
                               undetermined=statuses.count(CompareStatus.UNDETERMINED))
            points.append(BarPoint(tuple(e.record.equipment_id for e in equipment), counts))
        if any(point.equipment_ids for point in points):
            groups[group.group_keys[0]] = group.label, tuple(points)
    return tuple(label for label, _ in areas), groups


def bar_tooltip(title, point):
    counts = point.counts
    if counts.rate is None:
        title += "\n목표 N 이상 적용률 계산 불가"
    else:
        title += f"\n목표 N 이상 {counts.compliant}/{counts.compliant + counts.below}건 ({counts.rate:.1%})"
    if counts.undetermined:
        title += f"\n판단 불가 {counts.undetermined}건"
    return equipment_count_tooltip(title, point.equipment_ids)


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


def trend_tooltip(day, label, summary, *, missing_equipment=0, source_day=None):
    decided = summary.compliant + summary.below
    title = f"{day:%Y.%m.%d} · {label}\n목표 달성 {summary.compliant} / {decided}건 ({summary.rate:.1%})"
    if source_day is not None and source_day != day:
        title += f"\n마지막 관측일 {source_day:%Y.%m.%d}"
    if missing_equipment:
        title += f"\n미관측 {missing_equipment}대"
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
        self.setAccessibleDescription("")
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
        self.setAccessibleDescription("표시할 차트 자료가 없습니다.")

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
        controller = manager()
        colors = controller.tokens if controller else Appearance().colors()
        keys = ("composition_met", "composition_below", "composition_unknown")
        for title, count, key, ids in zip(("N 이상", "N 미만", "판단 불가"), counts, keys,
                                            composition_cohorts(snapshot.summary)):
            if count:
                part = series.append(f"{title} {count}건 ({count / total:.1%})", count)
                part.setProperty("dashboardCompositionColor", key)
                part.setColor(QColor(colors[key]))
                part.setPen(QPen(Qt.PenStyle.NoPen))
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
        self.setAccessibleDescription(
            f"원형 그래프 · 달성률은 판단 가능 {decided}건 기준 · 구성비는 판단 불가 포함 전체 {total}건 기준 · "
            + " · ".join(f"{name} {value}건" for name, value in zip(("N 이상", "N 미만", "판단 불가"), counts)))

    def bars(self, plot, visible_groups):
        chart = self._chart()
        areas, groups = plot
        series = QBarSeries()
        shown = {}
        for key, (label, points) in groups.items():
            if key not in visible_groups:
                continue
            group_set = QBarSet(label)
            group_set.setColor(ui_color(series_color(key)))
            chart_font(group_set, "labelFont", font(10))
            # QBarSet의 빈 값 자리만 0으로 두고, 계산 불가는 별도 '—' 표시로 구분한다.
            group_set.append([point.counts.rate * 100 if point.counts.rate is not None else 0 for point in points])
            series.append(group_set)
            shown[group_set] = label, points
        descriptions = [
            f"{areas[index]} {label}: N 이상 {point.counts.compliant}건, N 미만 {point.counts.below}건, "
            f"판단 불가 {point.counts.undetermined}건"
            for label, points in shown.values() for index, point in enumerate(points) if point.equipment_ids]
        self.setAccessibleDescription("막대 그래프 · " + (" · ".join(descriptions) if descriptions else "표시한 설비군이 없습니다."))
        if not shown:
            return
        series.setLabelsVisible(False)
        chart.addSeries(series)
        x = QBarCategoryAxis()
        x.append(list(areas))
        x.setGridLineVisible(False)
        y = QValueAxis()
        y.setRange(0, 100)
        y.setLabelFormat("%.0f%%")
        y.setTitleText("목표 N 이상 적용률 (%)")
        chart_font(y, "titleFont", font(12))
        y.setTickCount(5)
        for axis, alignment in ((x, Qt.AlignmentFlag.AlignBottom), (y, Qt.AlignmentFlag.AlignLeft)):
            self._axis(axis)
            chart.addAxis(axis, alignment)
            series.attachAxis(axis)
        def hovered(entering, index, bar_set):
            target = bar_set, index
            if not entering:
                self._hover_tooltip(chart, target, "")
            elif bar_set in shown and 0 <= index < len(areas):
                label, points = shown[bar_set]
                if points[index].equipment_ids:
                    title = f"{areas[index]} / {label}"
                    self._hover_tooltip(chart, target, bar_tooltip(title, points[index]))
        series.hovered.connect(hovered)
        baseline_labels = []
        for group_index, (label, points) in enumerate(shown.values()):
            for area_index, point in enumerate(points):
                if not point.equipment_ids or point.counts.rate not in (None, 0):
                    continue
                title = f"{areas[area_index]} / {label}"
                marker = QGraphicsSimpleTextItem("—" if point.counts.rate is None else "0%", chart)
                marker.setToolTip(bar_tooltip(title, point))
                marker.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                chart_text_font(marker, font(10))
                marker.setBrush(QBrush(ui_color(SECONDARY)))
                offset = ((group_index + 0.5) / len(shown) - 0.5) * series.barWidth()
                baseline_labels.append((marker, area_index + offset))

        def place_baseline_labels(_rect=None):
            for marker, value in baseline_labels:
                position = chart.mapToPosition(QPointF(value, 0), series)
                bounds = marker.boundingRect()
                marker.setPos(position.x() - bounds.width() / 2, position.y() - bounds.height() - 3)

        chart._setron_place_labels = place_baseline_labels
        chart.plotAreaChanged.connect(place_baseline_labels)
        place_baseline_labels()

    def _trend_hover(self, chart, series, group, segment, position, hovered):
        if not hovered:
            self._hover_tooltip(chart, series, "")
            return
        # 선분 위에서는 그 선분을 실제로 구성하는 관측일 중 가장 가까운 값을 보여준다.
        index, _ = min(segment, key=lambda item: abs(item[0] - position.x()))
        point = group.points[index]
        text = trend_tooltip(point.day, group.label, point.summary, missing_equipment=point.missing_equipment,
                             source_day=point.source_day)
        self._hover_tooltip(chart, series, text)

    def trend(self, snapshot, visible_groups):
        chart = self._chart()
        dates = [point.day for point in snapshot.observations]
        # 좌표는 모든 관측일의 원래 인덱스를 유지하고 눈금만 화면 폭에 맞춰 줄인다.
        x = QCategoryAxis()
        x.setLabelsPosition(QCategoryAxis.AxisLabelsPosition.AxisLabelsPositionOnValue)
        x.setTruncateLabels(False)
        x.setRange(-0.5, max(0.5, len(dates) - 0.5))
        x.setGridLineVisible(False)
        pattern = "%Y-%m-%d" if dates and dates[0].year != dates[-1].year else "%m-%d"
        date_labels = tuple(day.strftime(pattern) for day in dates)
        measured_widths = {}
        ticks = None
        def place_dates(rect):
            nonlocal ticks
            # 축 눈금은 plot 밖에도 그려지므로 양 끝 날짜가 차트 가장자리에서 잘리지 않게 한다.
            # Qt 축 텍스트 문서의 안쪽 여백 8px도 실제 글꼴 폭과 함께 확보한다.
            key = x.labelsFont().toString()
            if key not in measured_widths:
                metrics = QFontMetricsF(x.labelsFont())
                measured_widths[key] = max((metrics.horizontalAdvance(text) for text in date_labels), default=0) + 8
            label_width = measured_widths[key]
            edge = label_width / 2 + 4
            width = max(1, rect.width())
            count = min(len(dates), max(2, int(max(0, width - 2 * edge) // max(85, label_width + 16)) + 1))
            indices = (() if not dates else (0,) if len(dates) == 1 else
                       tuple(round(i * (len(dates) - 1) / (count - 1)) for i in range(count)))
            ratio = min(0.45, edge / width)
            padding = max(0.5, (len(dates) - 1) * ratio / (1 - 2 * ratio)) if len(dates) > 1 else 0.5
            state = indices, round(padding, 5)
            if ticks == state:
                return
            ticks = state
            for label in x.categoriesLabels():
                x.remove(label)
            x.setStartValue(-padding)
            for index in indices:
                x.append(date_labels[index], index)
            x.setRange(-padding, max(0, len(dates) - 1) + padding)

        chart.plotAreaChanged.connect(place_dates)
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
                rate = point.summary.rate
                # 관측된 설비로 계산 가능한 값은 연결하고, 계산할 값이 없는 날짜에서만 끊는다.
                if rate is not None:
                    current.append((i, rate * 100))
                    continue
                if current:
                    segments.append(current)
                    current = []
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
        place_dates(chart.plotArea())
        descriptions = []
        for group in snapshot.trends:
            if (group.group_key not in visible_groups or not group.points
                    or not any(point.summary.rate is not None for point in group.points)):
                continue
            point = group.points[-1]
            counts = point.summary
            rate = "계산 불가" if counts.rate is None else f"{counts.rate:.1%}"
            descriptions.append(f"{group.label} 마지막 기준일 {point.day}: 달성률 {rate}, N 이상 {counts.compliant}건, "
                                f"N 미만 {counts.below}건, 판단 불가 {counts.undetermined}건")
        period = f"{dates[0]} ~ {dates[-1]} · " if dates else ""
        self.setAccessibleDescription("꺾은선 그래프 · " + period + (
            " · ".join(descriptions) if descriptions else "표시한 설비군이 없습니다."))

    def leaveEvent(self, event):
        self._hide_hover_tooltip()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._donut_mode:
            self.chart().legend().setAlignment(Qt.AlignmentFlag.AlignRight if event.size().width() >= 800 else Qt.AlignmentFlag.AlignBottom)
