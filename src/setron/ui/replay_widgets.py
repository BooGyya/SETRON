"""고정된 재생 자료의 분류별 추이와 버전 히트맵 공통 콘텐츠를 연결한다."""

from functools import partial

from PySide6.QtCharts import QBarCategoryAxis, QLineSeries, QScatterSeries, QValueAxis
from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QProgressBar, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget

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
        self.setObjectName("replayViewControls")
        self.setStyleSheet("QScrollArea#replayViewControls {background:transparent;border:0;}")
        self.viewport().setAutoFillBackground(False)
        self.tabs, self.grouping, self.interval = tabs, grouping, interval
        self._refreshing = False
        self._overflow = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setAccessibleName("리플레이 보기·분류·데이터 간격")
        self.row = QWidget()
        self.row.setObjectName("replayViewControlsRow")
        self.row.setStyleSheet("QWidget#replayViewControlsRow {background:transparent;}")
        self.row_body = QHBoxLayout(self.row)
        self.row_body.setContentsMargins(0, 0, 0, 0)
        self.row_body.setSpacing(12)
        for control in (tabs, grouping, interval):
            self.row_body.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        self.row_body.addStretch()
        self.setWidget(self.row)
        self.row.setAutoFillBackground(False)  # QScrollArea.setWidget가 켠 배경 채우기를 해제한다.
        self.scroll_left, self.scroll_right = button("‹"), button("›")
        for control, direction, name in ((self.scroll_left, -1, "이전 도구 보기"),
                                         (self.scroll_right, 1, "다음 도구 보기")):
            control.setParent(self)
            control.setAccessibleName(name)
            control.setToolTip(f"{name} · 보기, 분류, 데이터 간격을 좌우로 이동")
            control.setStyleSheet(f"QPushButton {{background:white;color:{PRIMARY};border:1px solid {LINE};"
                                 "border-radius:7px;padding:0;min-height:0;}"
                                 "QPushButton:disabled {color:#b8c0cd;background:#f8f9fc;}")
            set_widget_font(control, font(20, medium=True))
            control.setAutoRepeat(True)
            control.clicked.connect(lambda checked=False, step=direction: self._scroll_tools(step))
            control.hide()
        self.horizontalScrollBar().rangeChanged.connect(self.refresh)
        self.horizontalScrollBar().valueChanged.connect(self._sync_scroll_buttons)
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
            self._overflow = self.row.minimumWidth() > self.contentsRect().width()
            self.setViewportMargins(32 if self._overflow else 0, 0, 32 if self._overflow else 0, 0)
            scrollbar = self.horizontalScrollBar()
            extra = scrollbar.sizeHint().height() if self._overflow else 0
            self.setFixedHeight(height + extra)
            self.scroll_left.setGeometry(0, 0, 28, height)
            self.scroll_right.setGeometry(max(0, self.width() - 28), 0, 28, height)
            self._sync_scroll_buttons()
        finally:
            self._refreshing = False

    def _scroll_tools(self, direction):
        scrollbar = self.horizontalScrollBar()
        scrollbar.setValue(scrollbar.value() + direction * max(160, self.viewport().width() * 2 // 3))

    def _sync_scroll_buttons(self, *_):
        scrollbar = self.horizontalScrollBar()
        for control, enabled in ((self.scroll_left, scrollbar.value() > scrollbar.minimum()),
                                 (self.scroll_right, scrollbar.value() < scrollbar.maximum())):
            control.setVisible(self._overflow)
            control.setEnabled(enabled)
            if self._overflow:
                control.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh()


class ReplayPlaybackControls(QFrame):
    """보기와 스크롤 위치에 관계없이 공유하는 재생 도구 모음."""

    speed_changed = Signal(float)
    frame_dragged = Signal(int)  # 재생 위치 막대를 끌거나 눌러 고른 프레임 번호
    SPEEDS = (0.5, 1, 2, 4)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("replayPlaybackControls")
        self.setAccessibleName("리플레이 재생 도구")
        self.setStyleSheet(f"QFrame#replayPlaybackControls {{background:white;border:1px solid {LINE};border-radius:14px;}}")
        body = QVBoxLayout(self)
        body.setContentsMargins(10, 6, 10, 6)
        body.setSpacing(4)  # 재생 위치 막대를 더해도 떠 있는 도구 높이를 110px 안으로 (11-13)
        context_row = QHBoxLayout()
        context_row.setSpacing(14)
        context = QVBoxLayout()
        context.setSpacing(0)
        date_row = QHBoxLayout()
        date_row.setSpacing(8)
        self.date_label = label("재생일 —", 12, medium=True)
        self.date_label.setAccessibleName("현재 재생일")
        self.frame_label = label("0 / 0", 11, color=SECONDARY)
        self.frame_label.setAccessibleName("재생 프레임")
        date_row.addWidget(self.date_label)
        date_row.addWidget(self.frame_label)
        date_row.addStretch()
        context.addLayout(date_row)
        self.observation_label = label("", 11, color=SECONDARY)
        self.observation_label.setAccessibleName("관측 기준일")
        self.observation_label.setMinimumHeight(self.observation_label.fontMetrics().height())
        context.addWidget(self.observation_label)
        context_row.addLayout(context, 1)
        rate = QVBoxLayout()
        rate.setSpacing(4)
        rate_row = QHBoxLayout()
        rate_row.setSpacing(10)
        self.scope_label = label("전체 범위", 11, color=SECONDARY)
        self.scope_label.setAccessibleName("적용률 집계 범위")
        rate_row.addWidget(self.scope_label)
        self.rate_label = label("적용률 —", 12, medium=True)
        self.rate_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        rate_row.addWidget(self.rate_label)
        rate.addLayout(rate_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("현재 재생일의 등록 SW 적용률")
        self.progress.setStyleSheet("QProgressBar {background:#e8ebfc;border:0;border-radius:2px;min-height:4px;max-height:4px;}"
                                   f"QProgressBar::chunk {{background:{PRIMARY};border-radius:2px;}}")
        self.progress.setFixedHeight(4)
        rate.addWidget(self.progress)
        context_row.addLayout(rate)
        body.addLayout(context_row)
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
        for control in self.speed_tabs.buttons:
            control.setFixedWidth(54)
        self.speed_tabs.setCurrentIndex(1)
        self.speed_tabs.currentChanged.connect(lambda index: self.speed_changed.emit(self.SPEEDS[index]))
        row.addWidget(self.speed_tabs)
        # 재생 위치 막대: 실행/일시정지 아래에서 끌어 원하는 시점으로 옮긴다 (11-13 요청). 끄는 동안 그 시점을 바로 보인다.
        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setAccessibleName("재생 위치")
        self.position.setToolTip("끌거나 눌러 재생 시점을 옮깁니다. 옮기면 재생을 멈춥니다.")
        self.position.setRange(0, 0)
        self.position.setPageStep(1)
        self.position.setTracking(True)
        self.position.setCursor(Qt.CursorShape.PointingHandCursor)
        self.position.setStyleSheet(
            "QSlider {min-height:12px;max-height:12px;}"
            "QSlider::groove:horizontal {background:#e8ebfc;border:0;border-radius:3px;height:6px;}"
            f"QSlider::sub-page:horizontal {{background:{PRIMARY};border-radius:3px;height:6px;}}"
            f"QSlider::handle:horizontal {{background:white;border:2px solid {PRIMARY};width:10px;height:10px;"
            "margin:-2px 0;border-radius:7px;}"
            "QSlider::groove:horizontal:disabled {background:#f1f2f5;}"
            "QSlider::sub-page:horizontal:disabled {background:#d5d7da;}"
            "QSlider::handle:horizontal:disabled {border-color:#d5d7da;}")
        self.position.valueChanged.connect(self.frame_dragged.emit)
        self.position.setFixedHeight(12)
        body.addWidget(self.position)

    def set_context(self, day, observed_day, index, total, *, selected_groups=False):
        """프레임 자료만 받아 두 보기에서 같은 재생 문맥을 표시한다."""
        self.date_label.setText(f"재생일 {day:%Y-%m-%d}")
        self.frame_label.setText(f"{index + 1} / {total}")
        self.frame_label.setAccessibleDescription(f"전체 {total}개 중 {index + 1}번째 프레임")
        self.position.blockSignals(True)  # 재생으로 바뀐 위치는 다시 '옮기기'로 보내지 않는다
        self.position.setRange(0, max(0, total - 1))
        self.position.setValue(index)
        self.position.blockSignals(False)
        self.position.setAccessibleDescription(f"{day:%Y-%m-%d} · 전체 {total}개 중 {index + 1}번째")
        carried = day != observed_day
        self.observation_label.setText(f"관측 기준 {observed_day:%Y-%m-%d} · 이전 자료" if carried else "실제 저장일과 같은 시점")
        self.observation_label.setToolTip("재생일 이전 마지막 저장 자료를 이어서 표시합니다. 설비별 관측일은 상세에서 확인할 수 있습니다."
                                         if carried else "해당 재생일에 저장된 자료입니다. 설비별 관측일은 상세에서 확인할 수 있습니다.")
        self.scope_label.setText("선택 설비군" if selected_groups else "전체 범위")
        self.scope_label.setToolTip("선택한 설비군의 등록 SW를 집계합니다." if selected_groups else
                                    "조회 조건 전체의 등록 SW를 집계합니다. 사업장·분류·라인·세부라인 범례는 추이 선의 표시만 바꿉니다.")

    def clear_context(self):
        self.date_label.setText("재생일 —")
        self.frame_label.setText("0 / 0")
        self.frame_label.setAccessibleDescription("")
        self.position.blockSignals(True)
        self.position.setRange(0, 0)
        self.position.blockSignals(False)
        self.position.setAccessibleDescription("")
        self.observation_label.setText("")
        self.observation_label.setToolTip("")
        self.scope_label.setText("전체 범위")
        self.scope_label.setToolTip("")

    def fit_width(self, available):
        """본문 폭 안에 고정하고, 내용의 높이만 바뀌면 하단 여백도 따라간다."""
        # 날짜·백분율의 자릿수가 바뀌어도 재생 도구가 매 시점 좌우로 움직이지 않는다.
        date_width = (self.date_label.fontMetrics().horizontalAdvance("재생일 2000-12-31")
                      + self.frame_label.fontMetrics().horizontalAdvance("999 / 999") + 8)
        observed_width = self.observation_label.fontMetrics().horizontalAdvance("관측 기준 2000-12-31 · 이전 자료")
        rate_width = (self.scope_label.fontMetrics().horizontalAdvance("선택 설비군")
                      + self.rate_label.fontMetrics().horizontalAdvance("적용률 계산 불가") + 10)
        preferred = max(self.layout().sizeHint().width(), max(date_width, observed_width) + rate_width + 36)
        self.setFixedWidth(min(max(1, available), preferred))
        self.adjustSize()


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
        self._display_key = None
        self._segment_slots, self._current_slots = [], {}
        self._current_marker = None
        self._values_description = ""

    def clear(self):
        super().clear()
        self.values, self.days = {}, ()
        self.current = None
        self.trends = ()
        self.available_trends = ()
        self._trend_key = self._trend_data = None
        self._trend_frames = ()
        self._display_key = None
        self._segment_slots, self._current_slots = [], {}
        self._current_marker = None
        self._values_description = ""
        self.setAccessibleDescription("")

    def _hover(self, chart, series, label, days, summaries, points, position, hovered):
        text = ""
        if hovered and points:
            index, _ = min(points, key=lambda point: abs(point[0] - position.x()))
            text = trend_tooltip(days[index], label, summaries[index])
        self._hover_tooltip(chart, series, text)

    def show_data(self, data, visible_groups, current=None, frames=None, *, grouping="group", hidden_series=()):
        """공통 분류 기준별 꺾은선. 설비군 모드는 선택한 모델만 표시한다.

        current(재생 중인 날짜 번호)까지는 진한 실선, 이후는 흐린 점선, 현재 날짜에는 세로선과 큰 점을 둔다.
        실제 시점마다 점을 찍고, 점을 누르면 그 날짜로 가고 마우스를 올리면 대시보드와 같은 집계를 보인다.
        frames를 주지 않으면 실제 저장 날짜(data.frames)를 쓴다. current가 None이면 마지막 시점을 현재로 본다.
        hidden_series는 집계 후 표시할 선만 숨긴다. available_trends는 숨긴 선도 포함해 범례에서 다시 켤 수 있다.
        같은 조회·간격·표시 선에서는 차트 객체를 유지하고 현재 시점의 좌표만 갱신한다.
        """
        frames = tuple(data.frames if frames is None else frames)
        visible_groups = None if visible_groups is None else frozenset(visible_groups)
        cache_key = (id(data), tuple(id(frame) for frame in frames), grouping,
                     visible_groups if grouping == "group" else None)
        if cache_key != self._trend_key:
            self.available_trends = replay_trends(data, frames, visible_groups, grouping=grouping)
            self._trend_key, self._trend_data = cache_key, data
            self._trend_frames = frames
        hidden_series = frozenset(hidden_series)
        self.trends = tuple(trend for trend in self.available_trends if trend.key not in hidden_series)
        display_key = cache_key, tuple(trend.key for trend in self.trends)
        if display_key != self._display_key:
            self._build_chart(frames, grouping)
            self._display_key = display_key
        if not frames:
            return
        current = len(frames) - 1 if current is None else max(0, min(current, len(frames) - 1))
        if current == self.current:
            return
        self._move_current(current)

    def _build_chart(self, frames, grouping):
        """관측·분류·표시 대상이 바뀔 때만 축과 단절 구간별 시리즈를 만든다."""
        chart = self._chart()
        self.values, self.days, self.current = {}, (), None
        self._segment_slots, self._current_slots = [], {}
        self._current_marker, self._values_description = None, ""
        self.setAccessibleDescription("")
        self.setAccessibleName(f"{dict(GROUPING_MODES).get(grouping, '분류')}별 목표 적용 추이 · 저장된 관측일의 달성률")
        if not frames:
            return
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

        def slot(series, name, trend):
            add(series)
            series.setVisible(False)
            points = []
            series.clicked.connect(lambda point: self.date_selected.emit(round(point.x())))
            # 이 목록은 시리즈 객체와 함께 보존하며 현재 시점이 움직일 때 내용을 바꾼다.
            series.hovered.connect(partial(self._hover, chart, series, trend.label, days, trend.summaries, points))
            return series, name, points

        # 현재 날짜 세로선. 선보다 먼저 넣어 선 아래에 깔린다.
        if self.trends:
            marker = QLineSeries()
            marker.setName(CURRENT_MARKER)
            marker.setPen(QPen(ui_color(SECONDARY), 1.5, Qt.PenStyle.DashLine))
            add(marker)
            self._current_marker = marker
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

            for points in segments:
                parts = []
                for name, pen_color, width, style, size in (
                        (trend.label, color, 2.5, Qt.PenStyle.SolidLine, 7),
                        (f"{trend.label}{FUTURE_SUFFIX}", faded, 1.5, Qt.PenStyle.DashLine, 6)):
                    line_slot = None
                    if len(points) > 1:
                        line = QLineSeries()
                        line.setPen(QPen(pen_color, width, style))
                        line_slot = slot(line, name, trend)
                    dots = QScatterSeries()
                    dots.setColor(pen_color)
                    dots.setBorderColor(pen_color)
                    dots.setMarkerSize(size)
                    parts.append((line_slot, slot(dots, name + POINT_SUFFIX, trend)))
                self._segment_slots.append((tuple(points), *parts))
            dot = QScatterSeries()
            dot.setColor(color)
            dot.setBorderColor(ui_color("white"))
            dot.setMarkerSize(12)
            self._current_slots[trend.key] = slot(dot, f"{trend.label}{CURRENT_SUFFIX}", trend)
        self._values_description = " · ".join(
            f"{trend.label}: " + ", ".join(f"{day} {'확인 필요' if value is None else f'{value:.1%}'}"
                                     for day, value in zip(days, values))
            for trend in self.trends for values in (self.values[trend.key],))

    @staticmethod
    def _replace_slot(slot, points):
        if slot is None:
            return
        series, name, current_points = slot
        if current_points == points:
            return
        current_points[:] = points
        series.replace([QPointF(x, y) for x, y in points])
        series.setName(name if points else "")
        series.setVisible(bool(points))

    def _move_current(self, current):
        """이미 존재하는 선·점의 좌표만 바꿔 재생 중 전체 차트 교체를 피한다."""
        self._hide_hover_tooltip()
        self.current = current
        if self._current_marker is not None:
            self._current_marker.replace([QPointF(current, 0), QPointF(current, 100)])
        for points, past_slots, future_slots in self._segment_slots:
            past = [point for point in points if point[0] <= current]
            future = [point for point in points if point[0] >= current]
            # 현재 날짜 점은 실선 쪽에 있으므로 그 점만 남은 미래 구간은 숨긴다.
            if len(future) == 1 and future[0][0] == current:
                future = []
            for part, (line, dots) in ((past, past_slots), (future, future_slots)):
                self._replace_slot(line, part if len(part) > 1 else [])
                self._replace_slot(dots, part)
        for key, slot in self._current_slots.items():
            rate = self.values[key][current]
            self._replace_slot(slot, [(current, rate * 100)] if rate is not None else [])
        self.setAccessibleDescription(f"현재 {self.days[current]} · " + self._values_description)


class ReplayHeatmap(QWidget):
    """버전 히트맵과 같은 콘텐츠를 고정된 재생 스냅샷에 연결한다."""

    equipment_selected = Signal(int, object)
    controls_ready = Signal(object)

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
        tile = getattr(self.view, "selection_control", None)
        if tile is None:
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
            self.controls_ready.emit(self.view.controls_host)
        self.selected_tile = None
        self.view.present_snapshot(frame.snapshot, source=data.source, tiles=frame.tiles,
                                   selected_groups=selected)
