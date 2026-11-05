"""데이터로 그리는 QtCharts. Figma의 예시 SVG 그래프를 고정 이미지로 사용하지 않는다."""

from PySide6.QtCharts import QAbstractBarSeries, QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView, QLineSeries, QPieSeries, QScatterSeries, QValueAxis
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsSimpleTextItem

from setron.ui.theme import INK, PRIMARY, SECONDARY, font


SERIES_COLORS = {
    "c3": "#5068d9", "c4": "#8b67d1", "o2": "#26859d", "o3": "#d9803e", "o5": "#c15a95",
    "inline-ox": "#66748e", "inline-o2": "#2e946f", "cluster-ox": "#d2516b", "cluster-o2": "#6b7ccd",
}


def series_color(key, index=0):
    # 추가 설비군도 조회로 시리즈 순서가 바뀌었다고 범례/선의 색이 달라지면 안 된다.
    return SERIES_COLORS.get(key, tuple(SERIES_COLORS.values())[sum(map(ord, key)) % len(SERIES_COLORS)])


class DashboardChart(QChartView):
    def __init__(self):
        super().__init__()
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(350)
        self.setMaximumHeight(390)
        self.setMinimumWidth(0)
        self.setBackgroundBrush(QBrush(QColor("white")))
        self._donut_mode = False
        self.setAccessibleName("대시보드 차트 · 같은 수치는 수치표 보기에서 확인할 수 있습니다")

    def _chart(self):
        chart = QChart()
        chart.setBackgroundBrush(QBrush(QColor("white")))
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

    @staticmethod
    def _axis(axis):
        axis.setLabelsFont(font(11))
        axis.setLabelsColor(QColor(SECONDARY))
        axis.setLinePen(QPen(QColor("#e9eef6")))
        axis.setGridLinePen(QPen(QColor("#e9eef6")))

    def composition(self, snapshot):
        chart = self._chart()
        counts = (snapshot.summary.compliant, snapshot.summary.below, snapshot.summary.undetermined)
        total = sum(counts)
        series = QPieSeries()
        series.setPieSize(0.90)
        series.setHoleSize(0.65)
        for title, count, color in zip(("N 이상", "N 미만", "판단 불가"), counts, (PRIMARY, "#f6dc85", "#c9ced6")):
            if count:
                part = series.append(f"{title} {count}건 ({count / total:.1%})", count)
                part.setColor(QColor(color))
                part.setBorderColor(QColor(color))
        chart.addSeries(series)
        self._donut_mode = True
        chart.legend().show()
        chart.legend().setFont(font(13))
        chart.legend().setLabelColor(QColor(SECONDARY))
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignRight if self.width() >= 800 else Qt.AlignmentFlag.AlignBottom)
        center = QGraphicsSimpleTextItem(f"{total}건" if total else "자료 없음", chart)
        center.setFont(font(30, medium=True, numeric=True))
        center.setBrush(QBrush(QColor(INK)))
        def place_center(rect):
            size = center.boundingRect()
            center.setPos(rect.center().x() - size.width() / 2, rect.center().y() - size.height() / 2)
        chart.plotAreaChanged.connect(place_center)
        self.setAccessibleDescription(" · ".join(f"{name} {value}건" for name, value in zip(("N 이상", "N 미만", "판단 불가"), counts)))

    def bars(self, snapshot, on_click, visible_groups):
        chart = self._chart()
        rows = [row for row in snapshot.rows if row.position is not None and row.cells[-1].summary.equipment_count]
        series = QBarSeries()
        shown = []
        for index, group in enumerate(snapshot.groups):
            if group.group_keys[0] not in visible_groups:
                continue
            shown.append(group)
            group_set = QBarSet(group.label)
            group_set.setColor(QColor(series_color(group.group_keys[0], index)))
            group_set.setLabelFont(font(10))
            values = []
            for row in rows:
                # KCTC도 하위 4종을 분리한다. 셀 합산으로 물리 설비 수를 만들지 않는다.
                ids = set(group.equipment_ids)
                values.append(len(ids.intersection(row.cells[-1].equipment_ids)))
            group_set.append(values)
            series.append(group_set)
        series.setLabelsVisible(True)
        series.setLabelsFormat("@value")
        series.setLabelsPosition(QAbstractBarSeries.LabelsPosition.LabelsOutsideEnd)
        chart.addSeries(series)
        x = QBarCategoryAxis()
        x.append([row.label for row in rows])
        x.setGridLineVisible(False)
        y = QValueAxis()
        maximum = max((group.at(i) for group in series.barSets() for i in range(group.count())), default=0)
        y.setRange(0, max(2, maximum + 1))
        y.setLabelFormat("%d")
        y.setTitleText("대수 (대)")
        y.setTitleFont(font(12))
        y.setTickCount(min(5, int(y.max()) + 1))
        for axis, alignment in ((x, Qt.AlignmentFlag.AlignBottom), (y, Qt.AlignmentFlag.AlignLeft)):
            self._axis(axis)
            chart.addAxis(axis, alignment)
            series.attachAxis(axis)
        def clicked(index, bar_set):
            group = shown[series.barSets().index(bar_set)]
            row = rows[index]
            ids = tuple(eid for eid in group.equipment_ids if eid in row.cells[-1].equipment_ids)
            on_click(f"{row.label} / {group.label}", ids)
        series.clicked.connect(clicked)

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
                rate = point.summary.rate
                # 관측 부족/분모 없음은 선을 끊는다. 이전 값이나 0%로 채우지 않는다.
                if rate is None or point.missing_equipment:
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
                series.setColor(QColor(series_color(group.group_key, index)))
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._donut_mode:
            self.chart().legend().setAlignment(Qt.AlignmentFlag.AlignRight if event.size().width() >= 800 else Qt.AlignmentFlag.AlignBottom)
