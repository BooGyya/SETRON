"""인쇄용 그래프와 설비군 → 라인 → 호기 히트맵 PNG. 화면 테마와 무관하게 흰 종이에 그린다."""

from dataclasses import replace
from math import ceil

from PySide6.QtCharts import QChart, QChartView, QDateTimeAxis, QLineSeries, QValueAxis
from PySide6.QtCore import QByteArray, QBuffer, QDateTime, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen

from setron.features.risk.schemas import RiskStatus
from setron.features.version.groups import TIER_LABELS, Tier
from setron.ui.chat_export import chart_png


PAPER_INK = "#142033"
TIER_COLORS = {Tier.ABOVE: "#A9DBBD", Tier.AT: "#DDF4E5", Tier.ONE_BELOW: "#FFF0B7",
               Tier.TWO_BELOW: "#F8C5CE", Tier.UNKNOWN: "#ECEEF1"}
LINE_COLORS = ("#1D4ED8", "#C2410C", "#15803D", "#7E22CE", "#0E7490", "#BE123C")
FIGURE_WIDTH, ROWS_PER_COLUMN, COLUMNS_PER_PAGE = 1200, 12, 6


def paper_font(size=24, bold=False):
    font = QFont("IBM Plex Sans KR")
    font.setPixelSize(size)
    font.setBold(bold)
    return font


def _png(image):
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("보고서 그림을 만들지 못했습니다.")
    return bytes(data.data())


def heatmap_images(panel):
    """긴 라인은 옆 열로 이어 쓰고 여섯 열마다 새 그림을 만든다. 셀을 잘라 버리지 않는다."""
    columns = []
    tiers = tuple(TIER_COLORS)
    for line, cells in panel.lines:
        cells = sorted(cells, key=lambda c: (tiers.index(c.tier), c.code))
        pages = max(1, ceil(len(cells) / ROWS_PER_COLUMN))
        for part in range(pages):
            title = line + (f" ({part + 1}/{pages})" if pages > 1 else "")
            columns.append((title, cells[part * ROWS_PER_COLUMN:(part + 1) * ROWS_PER_COLUMN]))
    return tuple(_heatmap_page(panel, columns[i:i + COLUMNS_PER_PAGE], i // COLUMNS_PER_PAGE + 1,
                               ceil(len(columns) / COLUMNS_PER_PAGE))
                 for i in range(0, len(columns), COLUMNS_PER_PAGE))


def _heatmap_page(panel, columns, page, pages):
    rows = max((len(cells) for _, cells in columns), default=0)
    header_height = max(45, max((len(line.splitlines()) for line, _ in columns), default=1) * 27 + 12)
    height = 167 + header_height + 58 * rows
    image = QImage(FIGURE_WIDTH, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(PAPER_INK))
    painter.setFont(paper_font(25, True))
    suffix = f" · {page}/{pages}" if pages > 1 else ""
    painter.drawText(QRectF(20, 5, 1160, 43), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                     (panel.heading or f"{panel.area} · {panel.site} · {panel.group}") + suffix)
    painter.setFont(paper_font(19))
    for i, (tier, fill) in enumerate(TIER_COLORS.items()):
        x = 20 + i * 200
        painter.fillRect(QRectF(x, 56, 24, 24), QColor(fill))
        painter.drawText(QRectF(x + 32, 50, 163, 36), Qt.AlignmentFlag.AlignVCenter, TIER_LABELS[tier])
    painter.setPen(QPen(QColor("#C53B53"), 3))
    painter.drawRect(QRectF(1025, 56, 24, 24))
    painter.setPen(QColor(PAPER_INK))
    painter.drawText(QRectF(1055, 50, 125, 36), Qt.AlignmentFlag.AlignVCenter, "위험 후보")
    left, top, width = 20, 100, 1160
    painter.fillRect(QRectF(left, top, width, 49), QColor("#EEF1F5"))
    painter.setPen(QPen(QColor("#566173"), 2))
    painter.drawRect(QRectF(left, top, width, 49))
    painter.setFont(paper_font(28, True))
    painter.drawText(QRectF(left, top, width, 49), Qt.AlignmentFlag.AlignCenter, panel.group)
    col_width = width / max(1, len(columns))
    for index, (line, cells) in enumerate(columns):
        x, y = left + index * col_width, top + 49
        painter.setPen(QPen(QColor("#566173"), 2))
        painter.drawRect(QRectF(x, y, col_width, header_height))
        painter.setFont(paper_font(21 if "\n" in line else 23, True))
        painter.drawText(QRectF(x + 4, y, col_width - 8, header_height), Qt.AlignmentFlag.AlignCenter, line)
        for r, cell in enumerate(cells):
            rect = QRectF(x + 1, y + header_height + r * 58 + 1, col_width - 2, 56)
            painter.fillRect(rect, QColor(TIER_COLORS[cell.tier]))
            risk = cell.risk is RiskStatus.RISK
            painter.setPen(QPen(QColor("#C53B53" if risk else "#7C8795"), 4 if risk else 1))
            painter.drawRect(rect)
            painter.setPen(QColor(PAPER_INK))
            size = 25
            while size > 16 and QFontMetrics(paper_font(size, True)).horizontalAdvance(cell.code) > rect.width() - 14:
                size -= 1
            painter.setFont(paper_font(size, True))
            text_rect = rect.adjusted(7, 0, -7, -20 if risk else 0)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, cell.code)
            if risk:
                painter.setFont(paper_font(17, True))
                painter.setPen(QColor("#9F243C"))
                painter.drawText(rect.adjusted(4, 33, -4, -2), Qt.AlignmentFlag.AlignCenter, "! 위험 후보")
    painter.end()
    return _png(image)


def line_trend_images(trend):
    """라인 수를 제한하지 않고, 문서 한 그림의 범례만 여섯 개씩 나눈다."""
    result = []
    for start in range(0, len(trend.lines), 6):
        part = replace(trend, lines=trend.lines[start:start + 6], rates=trend.rates[start:start + 6])
        view = line_trend_chart(part)
        try:
            result.append(chart_png(view, (FIGURE_WIDTH, 590)))
        finally:
            view.deleteLater()
    return tuple(result)


def line_trend_chart(trend):
    chart = QChart()
    chart.setBackgroundBrush(QColor("white"))
    chart.setBackgroundRoundness(0)
    chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
    chart.setTitle(f"{trend.area} · {trend.site} · {trend.group}")
    chart.setTitleFont(paper_font(27, True))
    chart.setTitleBrush(QColor(PAPER_INK))
    chart.legend().setFont(paper_font(23))
    chart.legend().setLabelColor(QColor(PAPER_INK))
    chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
    x, y = QDateTimeAxis(), QValueAxis()
    x.setFormat("yy-MM-dd")
    x.setTickCount(min(7, max(2, len(trend.days))))
    y.setRange(0, 100)
    y.setLabelFormat("%.0f%%")
    for axis in (x, y):
        axis.setLabelsFont(paper_font(23))
        axis.setLabelsColor(QColor(PAPER_INK))
        axis.setGridLineColor(QColor("#E5E7EB"))
    chart.addAxis(x, Qt.AlignmentFlag.AlignBottom)
    chart.addAxis(y, Qt.AlignmentFlag.AlignLeft)
    stamps = [QDateTime.fromString(d.isoformat(), "yyyy-MM-dd") for d in trend.days]
    if stamps:
        x.setRange(stamps[0].addDays(-1) if len(stamps) == 1 else stamps[0],
                   stamps[-1].addDays(1) if len(stamps) == 1 else stamps[-1])
    for number, (line, rates) in enumerate(zip(trend.lines, trend.rates)):
        series, labelled = None, False
        for stamp, rate in zip(stamps, rates):
            if rate is None:
                series = None
                continue
            if series is None:
                series = QLineSeries()
                series.setName(line)
                series.setPen(QPen(QColor(LINE_COLORS[number % len(LINE_COLORS)]), 4))
                series.setPointsVisible(True)
                chart.addSeries(series)
                series.attachAxis(x)
                series.attachAxis(y)
                if labelled:
                    for marker in chart.legend().markers(series):
                        marker.setVisible(False)
                labelled = True
            series.append(stamp.toMSecsSinceEpoch(), rate * 100)
        if not labelled:
            series = QLineSeries()
            series.setName(line + " (판정 불가)")
            series.setPen(QPen(QColor(LINE_COLORS[number % len(LINE_COLORS)]), 4))
            chart.addSeries(series)
    view = QChartView(chart)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    return view
