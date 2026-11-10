"""보고서 생성 진행창과 문서에 포함할 차트."""

from PySide6.QtCharts import QBarCategoryAxis, QBarSet, QChart, QChartView, QLineSeries, QPieSeries, QStackedBarSeries, QValueAxis
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget

from setron.ui.appearance import chart_font, readable_ink, ui_color
from setron.ui.chat_export import chart_png
from setron.ui.import_dialogs import ImportBusyBar, ImportDialog
from setron.ui.theme import INK, PRIMARY, font

COLORS = ("#3c5ce9", "#293b66", "#9ca3af")


class GenerationDialog(ImportDialog):
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__("보고서를 생성하고 있습니다", parent)
        self.add_message("선택한 기간과 조건으로 보고서를 준비합니다.")
        self.body.addWidget(ImportBusyBar())
        self.add_action("생성 취소", self.reject, default=True)
        self.finish_layout()

    def reject(self):
        self.cancelled.emit()
        super().reject()


def report_pictures(report):
    """화면 배치와 관계없이 같은 크기의 차트를 문서용 PNG로 캡처한다."""
    pictures = []
    for index, section in enumerate(report.sections):
        if not section.chart or not section.table or not section.table.rows:
            continue
        kinds = ("bar", "pie", "treemap") if section.chart == "distribution" else ("trend",)
        for kind in kinds:
            chart = TreeMap(section.table) if kind == "treemap" else data_chart(section.table, kind)
            try:
                pictures.append((index, chart_png(chart, (680, chart.height()))))
            finally:
                chart.deleteLater()
    return tuple(pictures)


def data_chart(table, kind):
    chart = QChart()
    chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
    chart.setBackgroundRoundness(0)
    chart_font(chart.legend(), "font", font(12))
    chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
    view = QChartView(chart)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    view.setMinimumWidth(0)
    view.setFixedHeight(280)
    view.setAccessibleName(table.title + " · 같은 수치표 참조")
    if kind == "pie":
        series = QPieSeries()
        chart.setTitle("상태 구성비 (설비·계열 건)")
        for index, title in enumerate(table.columns[1:], 1):
            value = sum(row[index] for row in table.rows)
            if value:
                part = series.append(f"{title} {value}건", value)
                part.setColor(ui_color(COLORS[index - 1]))
        chart.addSeries(series)
        return view
    x, y = QBarCategoryAxis(), QValueAxis()
    x.append([str(row[0]) for row in table.rows])
    y.setLabelFormat("%.0f%%" if kind == "trend" else "%d")
    series_list = []
    if kind == "trend":
        chart.setTitle("날짜별 적용률 추이")
        y.setRange(0, 100)
        # 미확인 분모의 날짜에서 선을 끊는다. 점 하나는 점만 보인다.
        current = None
        for index, row in enumerate(table.rows):
            if row[4] is None:
                current = None
                continue
            if current is None:
                current = QLineSeries()
                current.setName("N 이상 적용률")
                current.setPen(QPen(ui_color(PRIMARY), 2))
                current.setPointsVisible(True)
                series_list.append(current)
            current.append(index, float(row[4]) * 100)
        chart.legend().hide()
    else:
        chart.setTitle("계열별 구성 (설비·계열 건)")
        y.setRange(0, max(1, max(sum(row[1:]) for row in table.rows)))
        series = QStackedBarSeries()
        for i, title in enumerate(table.columns[1:], 1):
            values = QBarSet(title)
            values.setColor(ui_color(COLORS[i - 1]))
            values.append([row[i] for row in table.rows])
            series.append(values)
        series_list.append(series)
    chart.addAxis(x, Qt.AlignmentFlag.AlignBottom)
    chart.addAxis(y, Qt.AlignmentFlag.AlignLeft)
    for series in series_list:
        chart.addSeries(series)
        series.attachAxis(x)
        series.attachAxis(y)
    return view


class TreeMap(QWidget):
    """면적=건수인 계열별 스트립 트리맵. 원문 수치표가 키보드 대체 수단이다."""

    def __init__(self, table):
        super().__init__()
        self.table = table
        self.setMinimumWidth(0)
        self.setFixedHeight(210)
        self.setAccessibleName("트리맵 · 면적은 설비·계열 건수에 비례")
        self.setAccessibleDescription(" · ".join(str(row) for row in table.rows))

    def paintEvent(self, event):
        painter = QPainter(self)
        surface = ui_color("white")
        painter.fillRect(self.rect(), surface)
        total = sum(sum(row[1:]) for row in self.table.rows)
        if not total:
            painter.setPen(readable_ink(ui_color(INK), surface))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "자료 없음")
            return
        x = 0.0
        for row in self.table.rows:
            count = sum(row[1:])
            if not count:
                continue
            width = self.width() * count / total
            y = 0.0
            for index, value in enumerate(row[1:]):
                if not value:
                    continue
                height = self.height() * value / count
                rect = QRectF(x, y, width, height)
                fill = ui_color(COLORS[index])
                painter.fillRect(rect, fill)
                painter.setPen(QPen(surface, 1))
                painter.drawRect(rect)
                if width > 100 and height > 38:
                    painter.setPen(readable_ink(ui_color(INK), fill))
                    painter.drawText(rect.adjusted(8, 4, -8, -4), Qt.AlignmentFlag.AlignCenter,
                                     f"{row[0]} · {self.table.columns[index + 1]} {value}건")
                y += height
            x += width
