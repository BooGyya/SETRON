"""고정된 재생 자료의 분류별 추이와 버전 히트맵 공통 콘텐츠를 연결한다."""

from functools import partial

from PySide6.QtCharts import QBarCategoryAxis, QLineSeries, QScatterSeries, QValueAxis
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QProgressBar, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from setron.ui.appearance import ui_color
from setron.ui.components import button, label
from setron.ui.dashboard_charts import DashboardChart, series_color, trend_tooltip
from setron.ui.heatmap import HeatmapPage
from setron.ui.heatmap_grouping import GROUPING_MODES
from setron.ui.replay_data import replay_trends
from setron.ui.theme import INK, LINE, PRIMARY, SECONDARY, font, set_widget_font
from setron.ui.view_controls import SegmentedControl

# 그래프 시리즈 이름. 범례는 숨겨져 있고, 테스트·접근성에서 선의 역할을 구분하는 데 쓴다.
CURRENT_MARKER = "현재 시점"
FUTURE_SUFFIX = " · 이후"
CURRENT_SUFFIX = " · 현재"
POINT_SUFFIX = " · 시점"  # 실제 시점마다 찍는 점. 누르기·마우스 올리기는 이 점에서 받는다.


class ReplayViewControls(QScrollArea):
    """보기·분류·간격을 한 행으로 유지하고 부족한 폭은 도구 안에서 스크롤한다."""

    def __init__(self, tabs, grouping, interval):
        super().__init__()
        self.tabs, self.grouping, self.interval = tabs, grouping, interval
        self._refreshing = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setAccessibleName("리플레이 보기·분류·데이터 간격")
        self.row = QWidget()
        self.row_body = QHBoxLayout(self.row)
        self.row_body.setContentsMargins(0, 0, 0, 0)
        self.row_body.setSpacing(12)
        for control in (tabs, grouping, interval):
            self.row_body.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        self.row_body.addStretch()
        self.setWidget(self.row)
        self.horizontalScrollBar().rangeChanged.connect(self.refresh)
        self.refresh()

    def minimumSizeHint(self):
        return QSize(0, self.height())

    def refresh(self, *_):
        if self._refreshing:
            return
        self._refreshing = True
        try:
            controls = self.tabs, self.grouping, self.interval
            widths = self.tabs.sizeHint().width(), self.grouping.sizeHint().width(), self.interval.unwrapped_width()
            height = max(control.sizeHint().height() for control in controls)
            for control, width in zip(controls, widths):
                control.setFixedSize(width, height)
            self.row.setMinimumWidth(sum(widths) + 2 * self.row_body.spacing())
            self.row.setFixedHeight(height)
            scrollbar = self.horizontalScrollBar()
            extra = scrollbar.sizeHint().height() if self.row.minimumWidth() > self.viewport().width() else 0
            self.setFixedHeight(height + extra)
        finally:
            self._refreshing = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh()


class ReplayPlaybackControls(QFrame):
    """보기와 스크롤 위치에 관계없이 공유하는 재생 도구 모음."""

    speed_changed = Signal(float)
    SPEEDS = (0.5, 1, 2, 4)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("replayPlaybackControls")
        self.setAccessibleName("리플레이 재생 도구")
        self.setStyleSheet(f"QFrame#replayPlaybackControls {{background:white;border:1px solid {LINE};border-radius:14px;}}")
        body = QVBoxLayout(self)
        body.setContentsMargins(12, 10, 12, 10)
        body.setSpacing(8)
        rate_row = QHBoxLayout()
        rate_row.setSpacing(10)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("현재 관측일의 등록 SW 적용률")
        self.progress.setStyleSheet("QProgressBar {background:#e8ebfc;border:0;border-radius:2px;min-height:4px;max-height:4px;}"
                                   f"QProgressBar::chunk {{background:{PRIMARY};border-radius:2px;}}")
        self.progress.setFixedHeight(4)
        rate_row.addWidget(self.progress, 1, Qt.AlignmentFlag.AlignVCenter)
        self.rate_label = label("적용률 —", 12, medium=True)
        self.rate_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        rate_row.addWidget(self.rate_label)
        body.addLayout(rate_row)
        row = QHBoxLayout()
        row.setSpacing(8)
        body.addLayout(row)
        style = (f"QPushButton {{background:white;color:{INK};border:1px solid {LINE};border-radius:8px;"
                 "padding:0;min-height:38px;max-height:38px;}"
                 f"QPushButton:hover, QPushButton:focus {{background:#e8ebfc;color:{PRIMARY};border-color:{PRIMARY};}}"
                 f"QPushButton:pressed {{background:{PRIMARY};color:white;border-color:{PRIMARY};}}"
                 "QPushButton:disabled {background:#f9fafb;color:#a9b1bf;border-color:#e4e7ed;}")
        self.play_button, self.pause_button, self.restart_button = button("▶"), button("‖"), button("↺")
        for control, name in ((self.play_button, "재생"), (self.pause_button, "일시정지"), (self.restart_button, "처음부터")):
            control.setAccessibleName(name)
            control.setToolTip(name)
            set_widget_font(control, font(16, medium=True))
            control.setStyleSheet(style)
            control.setFixedSize(44, 40)
            row.addWidget(control)
        divider = QFrame()
        divider.setFixedSize(1, 28)
        divider.setStyleSheet(f"background:{LINE};border:0;")
        row.addWidget(divider)
        self.speed_tabs = SegmentedControl()
        self.speed_tabs.setAccessibleName("재생 속도")
        for speed in self.SPEEDS:
            self.speed_tabs.addTab(f"{speed:g}×")
        self.speed_tabs.setCurrentIndex(1)
        self.speed_tabs.currentChanged.connect(lambda index: self.speed_changed.emit(self.SPEEDS[index]))
        row.addWidget(self.speed_tabs)


class ReplayChart(DashboardChart):
    date_selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(300)
        self.setMaximumHeight(300)
        self.setAccessibleName("설비군별 목표 적용 추이 · 저장된 관측일의 달성률")
        self.values, self.days = {}, ()
        self.current = None
        self.trends = ()
        self.available_trends = ()
        self._trend_key = self._trend_data = None
        self._trend_frames = ()

    def clear(self):
        super().clear()
        self.values, self.days = {}, ()
        self.current = None
        self.trends = ()
        self.available_trends = ()
        self._trend_key = self._trend_data = None
        self._trend_frames = ()
        self.setAccessibleDescription("")

    def _hover(self, chart, series, label, days, summaries, points, position, hovered):
        text = ""
        if hovered:
            index, _ = min(points, key=lambda point: abs(point[0] - position.x()))
            text = trend_tooltip(days[index], label, summaries[index])
        self._hover_tooltip(chart, series, text)

    def show_data(self, data, visible_groups, current=None, frames=None, *, grouping="group", hidden_series=()):
        """공통 분류 기준별 꺾은선. 설비군 모드는 선택한 모델만 표시한다.

        current(재생 중인 날짜 번호)까지는 진한 실선, 이후는 흐린 점선, 현재 날짜에는 세로선과 큰 점을 둔다.
        실제 시점마다 점을 찍고, 점을 누르면 그 날짜로 가고 마우스를 올리면 대시보드와 같은 집계를 보인다.
        frames를 주지 않으면 실제 저장 날짜(data.frames)를 쓴다. current가 None이면 마지막 시점을 현재로 본다.
        hidden_series는 집계 후 표시할 선만 숨긴다. available_trends는 숨긴 선도 포함해 범례에서 다시 켤 수 있다.
        """
        frames = tuple(data.frames if frames is None else frames)
        visible_groups = None if visible_groups is None else frozenset(visible_groups)
        chart = self._chart()
        self.values, self.days = {}, ()
        self.current = None
        self.setAccessibleDescription("")
        self.setAccessibleName(f"{dict(GROUPING_MODES).get(grouping, '분류')}별 목표 적용 추이 · 저장된 관측일의 달성률")
        cache_key = (id(data), tuple(id(frame) for frame in frames), grouping,
                     visible_groups if grouping == "group" else None)
        if cache_key != self._trend_key:
            self.available_trends = replay_trends(data, frames, visible_groups, grouping=grouping)
            self._trend_key, self._trend_data = cache_key, data
            self._trend_frames = frames
        hidden_series = frozenset(hidden_series)
        self.trends = tuple(trend for trend in self.available_trends if trend.key not in hidden_series)
        if not frames:
            return
        current = len(frames) - 1 if current is None else max(0, min(current, len(frames) - 1))
        self.current = current
        days = self.days = tuple(frame.day for frame in frames)
        x, y = QBarCategoryAxis(), QValueAxis()
        pattern = "%Y-%m-%d" if days[0].year != days[-1].year else "%m-%d"
        x.append([d.strftime(pattern) for d in days])
        x.setGridLineVisible(False)
        y.setRange(0, 100)
        y.setTickCount(5)
        y.setLabelFormat("%.0f%%")
        for axis, alignment in ((x, Qt.AlignmentFlag.AlignBottom), (y, Qt.AlignmentFlag.AlignLeft)):
            self._axis(axis)
            chart.addAxis(axis, alignment)

        def add(series):
            chart.addSeries(series)
            series.attachAxis(x)
            series.attachAxis(y)

        # 현재 날짜 세로선. 선보다 먼저 넣어 선 아래에 깔린다.
        if self.trends:
            marker = QLineSeries()
            marker.setName(CURRENT_MARKER)
            marker.setPen(QPen(ui_color(SECONDARY), 1.5, Qt.PenStyle.DashLine))
            marker.append(current, 0)
            marker.append(current, 100)
            add(marker)
        for trend in self.trends:
            values, segments, segment = [], [], []
            summaries = trend.summaries
            for i, summary in enumerate(summaries):
                rate = summary.rate if summary is not None else None
                values.append(rate)
                if rate is None:
                    if segment:
                        segments.append(segment)
                    segment = []
                else:
                    segment.append((i, rate * 100))
            if segment:
                segments.append(segment)
            self.values[trend.key] = tuple(values)
            color = ui_color(series_color(trend.color_key))
            faded = QColor(color)
            faded.setAlpha(90)

            def connect(series, points):
                series.clicked.connect(lambda point: self.date_selected.emit(round(point.x())))
                series.hovered.connect(partial(self._hover, chart, series, trend.label, days, summaries, points))

            for points in segments:
                past = [p for p in points if p[0] <= current]
                future = [p for p in points if p[0] >= current]
                # 현재 날짜 점은 실선 쪽에 이미 있으므로 이후 쪽이 그 점 하나뿐이면 그리지 않는다.
                if len(future) == 1 and future[0][0] == current:
                    future = []
                for part, name, pen_color, width, style, size in (
                        (past, trend.label, color, 2.5, Qt.PenStyle.SolidLine, 7),
                        (future, f"{trend.label}{FUTURE_SUFFIX}", faded, 1.5, Qt.PenStyle.DashLine, 6)):
                    if not part:
                        continue
                    if len(part) > 1:
                        line = QLineSeries()
                        line.setName(name)
                        line.setPen(QPen(pen_color, width, style))
                        for px, py in part:
                            line.append(px, py)
                        add(line)
                        connect(line, part)
                    dots = QScatterSeries()
                    dots.setName(name + POINT_SUFFIX)
                    dots.setColor(pen_color)
                    dots.setBorderColor(pen_color)
                    dots.setMarkerSize(size)
                    for px, py in part:
                        dots.append(px, py)
                    add(dots)
                    connect(dots, part)
            # 현재 날짜 점을 크게 표시한다. 판단 불가인 날은 점을 찍지 않는다(0%로 그리지 않음).
            if values[current] is not None:
                dot = QScatterSeries()
                dot.setName(f"{trend.label}{CURRENT_SUFFIX}")
                dot.setColor(color)
                dot.setBorderColor(ui_color("white"))
                dot.setMarkerSize(12)
                dot.append(current, values[current] * 100)
                add(dot)
                connect(dot, [(current, values[current] * 100)])
        self.setAccessibleDescription(f"현재 {days[current]} · " + " · ".join(
            f"{trend.label}: " + ", ".join(f"{day} {'확인 필요' if value is None else f'{value:.1%}'}"
                                     for day, value in zip(days, values))
            for trend in self.trends for values in (self.values[trend.key],)))


class ReplayHeatmap(QWidget):
    """버전 히트맵과 같은 콘텐츠를 고정된 재생 스냅샷에 연결한다."""

    equipment_selected = Signal(int, object)

    def __init__(self):
        super().__init__()
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        self.view = None
        self.grouping_mode = "site"
        self.selected_tile = None

    @property
    def legend(self):
        return self.view.legend if self.view is not None else None

    @property
    def list_table(self):
        return self.view.list_table if self.view is not None else None

    @property
    def tile_buttons(self):
        return self.view.tile_buttons if self.view is not None else {}

    @property
    def tile_occurrences(self):
        return self.view.tile_occurrences if self.view is not None else []

    @property
    def group_cards(self):
        return self.view.group_cards if self.view is not None else []

    @property
    def visible_ids(self):
        return self.view.visible_ids if self.view is not None else ()

    @property
    def rebuilt_tiles(self):
        return self.view.rebuilt_tiles if self.view is not None else 0

    @property
    def restyled_tiles(self):
        return self.view.restyled_tiles if self.view is not None else 0

    def clear(self):
        self.selected_tile = None
        if self.view is not None:
            self.view.clear_snapshot()

    def set_grouping(self, mode):
        if mode not in dict(GROUPING_MODES):
            raise ValueError("지원하지 않는 히트맵 그룹 기준입니다.")
        self.grouping_mode = mode
        if self.view is not None:
            self.view.set_grouping(mode)

    def select_equipment(self, equipment_id):
        if self.view is not None:
            self.view.select_equipment(equipment_id)
        self.selected_tile = self.tile_buttons.get(equipment_id)

    def select_tile(self, tile):
        equipment_id = (self.view.selected_id if tile is self.list_table and self.view is not None
                        else getattr(getattr(tile, "data", None), "equipment_id", None))
        self.select_equipment(equipment_id)

    def _open_equipment(self, selection):
        tile = (self.list_table if self.view.layout_buttons.checkedId() == 1
                else self.tile_buttons.get(selection.equipment_id))
        self.equipment_selected.emit(selection.equipment_id, tile)

    def show_frame(self, data, frame, selected=None):
        """전달된 시점만 표시한다. 필터·분류 변경으로 자료를 다시 읽지 않는다."""
        if self.view is None:
            self.view = HeatmapPage(None, data.source, detail_connected=True,
                                    show_summary=False, show_grouping=False)
            self.view.content.body.setContentsMargins(0, 0, 0, 0)
            self.view.set_grouping(self.grouping_mode)
            self.view.equipment_selected.connect(self._open_equipment)
            self.body.addWidget(self.view)
        self.selected_tile = None
        self.view.present_snapshot(frame.snapshot, source=data.source, tiles=frame.tiles,
                                   selected_groups=selected)
