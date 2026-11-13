"""VER5 / 6 Version Replay. 조회한 시점별 설비 현황과 상세를 보여 준다."""

from PySide6.QtCore import QDate, QEvent, QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QWidget,
)

from setron.ui.components import Card, button, label
from setron.ui.dashboard import DashboardGrid
from setron.ui.dashboard_charts import ScrollableChart, series_button_style, series_color
from setron.ui.dashboard_data import information_gap_reasons
from setron.ui.equipment import ignore_width
from setron.ui.equipment_drawer import EquipmentDrawer
from setron.ui.heatmap_data import EquipmentSelection
from setron.ui.heatmap_grouping import GROUPING_MODES
from setron.ui.interval_selector import IntervalSelector
from setron.ui.query_panel import QueryPanel
from setron.ui.query_state import QueryState
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.replay_data import ReplayData, ReplayRequest, can_replay, group_view, load_replay, replay_detail
from setron.ui.replay_widgets import ReplayChart, ReplayHeatmap, ReplayPlaybackControls, ReplayViewControls
from setron.ui.theme import SECONDARY, font, set_widget_font
from setron.ui.version_filter_dialog import VersionFilterDialog
from setron.ui.version_filter import version_group_options
from setron.ui.view_controls import SegmentedControl, bulk_selection_toggle_button, set_bulk_selection_state


class ReplayPage(QWidget):
    drawer_opened = Signal()

    def __init__(self, source, *, loader=None, targets_provider=lambda: None, drawer_parent=None):
        super().__init__()
        self.source, self.loader, self.targets_provider = source, loader, targets_provider
        self.state = QueryState(source.catalog)  # 공통 조회 초안·적용 N과 독립적인 조회 상태.
        self.data, self.pending_request = None, None
        self.index, self.epoch, self.dirty = 0, 0, True
        self.version_dialog = None
        self.drawer = (EquipmentDrawer(drawer_parent, object_name="replayEquipmentDrawer")
                       if drawer_parent is not None else None)
        self.read_request = ReadRequest(self)
        self.read_request.completed.connect(self._loaded)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.advance)
        self.playback_speed = 1.0
        self._seeking, self._seek_was_playing = False, False
        self._playback_host = self
        self.legend_buttons, self.legend_tools, self.frames = {}, (), ()  # frames: 지금 재생 간격의 시점들
        self._hidden_groups = set()
        self._hidden_trends = {}
        self.trend_legend_buttons, self.trend_legend_tools = {}, ()
        self.grouping_mode = "site"
        self.heatmap_controls = None
        self._trend_legend_key = None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 88)
        body.setSpacing(16)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.query_panel = QueryPanel(self.state, period=True)
        self.start, self.end = self.query_panel.start_date, self.query_panel.end_date
        self.reset_button = self.query_panel.reset_button
        self.version_button = self.query_panel.version_button
        self.version_button.setEnabled(True)
        self._reset_period()
        body.addWidget(self.query_panel)
        self.message = label("", 13, color=SECONDARY, wrap=True)
        self.message.hide()
        ignore_width(self.message)
        body.addWidget(self.message)
        self.content = Card(padding=20)
        self.content.setStyleSheet("QFrame#card {background:white;border:0;border-radius:12px;}")
        self.content.body.setSpacing(12)
        self.content.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        # 날짜 칩 대신 재생 간격 (요청 2-1). 하나만 고르는 세그먼트 버튼. 바꾸면 다시 조회하지 않고 시점만 다시 만든다.
        self.timeline = IntervalSelector(compact=True, custom_days=True, action="재생", show_caption=False)
        self.interval_group, self.interval_buttons, self.step = self.timeline.group, self.timeline.buttons, None
        self.timeline.set_saved_days()
        self.timeline.step_changed.connect(self.set_interval)
        self.grouping_tabs = SegmentedControl()
        self.grouping_tabs.setAccessibleName("리플레이 분류")
        for _, caption in GROUPING_MODES:
            self.grouping_tabs.addTab(caption)
        self.grouping_tabs.setCurrentIndex(next(i for i, (mode, _) in enumerate(GROUPING_MODES)
                                               if mode == self.grouping_mode))
        self.legend_host = QWidget()
        self.legend_body = QVBoxLayout(self.legend_host)
        self.legend_body.setContentsMargins(0, 0, 0, 0)
        self.content.body.addWidget(self.legend_host)
        self.trend_legend_host = QWidget()
        self.trend_legend_body = QVBoxLayout(self.trend_legend_host)
        self.trend_legend_body.setContentsMargins(0, 0, 0, 0)
        self.trend_legend_host.hide()
        self.content.body.addWidget(self.trend_legend_host)
        # 두 보기에서 같은 재생 상태를 제어한다.
        self.playback_controls = ReplayPlaybackControls(self)
        self.achievement = self.playback_controls.rate_label
        self.progress = self.playback_controls.progress
        self.play_button = self.playback_controls.play_button
        self.pause_button = self.playback_controls.pause_button
        self.restart_button = self.playback_controls.restart_button
        self.speed_tabs = self.playback_controls.speed_tabs
        self.position_slider = self.playback_controls.position_slider
        self.position_slider.sliderPressed.connect(self._begin_seek)
        self.position_slider.sliderMoved.connect(self._preview_seek)
        self.position_slider.sliderReleased.connect(self._finish_seek)
        self.position_slider.valueChanged.connect(self._seek_position)
        self.playback_controls.speed_changed.connect(self.set_playback_speed)
        self.speed_buttons = dict(zip(self.playback_controls.SPEEDS, self.speed_tabs.buttons))
        for speed, control in self.speed_buttons.items():
            control.setAccessibleName(f"재생 속도 {speed:g}배속")
            control.setToolTip(f"{speed:g}배속으로 재생")
        self.installEventFilter(self)
        if self.drawer is not None:
            self.drawer.installEventFilter(self)
        self.view_tabs = SegmentedControl()
        self.view_tabs.setAccessibleName("리플레이 보기")
        self.view_tabs.setDrawBase(False)
        self.view_tabs.setExpanding(False)
        set_widget_font(self.view_tabs, font(14, medium=True))
        for caption in ("달성률 추이", "히트맵"):
            self.view_tabs.addTab(caption)
        self.view_controls = ReplayViewControls(self.view_tabs, self.grouping_tabs, self.timeline)
        self.content.body.insertWidget(0, self.view_controls)
        self.chart = ReplayChart()
        self.chart.date_selected.connect(self.select_date)
        self.chart_row = ScrollableChart(self.chart)
        self.content.body.addWidget(self.chart_row)
        self.heatmap = ReplayHeatmap()
        self.heatmap.equipment_selected.connect(self.open_equipment)
        self.heatmap.controls_ready.connect(self._attach_heatmap_controls)
        self.content.body.addWidget(self.heatmap)
        self.grouping_tabs.currentChanged.connect(lambda index: self.set_grouping(GROUPING_MODES[index][0]))
        self.view_tabs.currentChanged.connect(self._show_view)
        self._show_view(self.view_tabs.currentIndex())
        body.addWidget(self.content)
        self.reset_button.clicked.connect(self.reset_filters)
        self.query_panel.search_button.clicked.connect(self.apply_query)
        self.version_button.clicked.connect(self.open_versions)
        self.play_button.clicked.connect(self.play)
        self.pause_button.clicked.connect(self.pause)
        self.restart_button.clicked.connect(self.restart)
        self._sync_controls()

    @property
    def busy(self):
        return self.read_request.busy

    def set_playback_host(self, host):
        """스크롤 문서 대신 화면 본문의 viewport에 재생 도구를 고정한다."""
        if host is self._playback_host:
            return
        self._playback_host.removeEventFilter(self)
        self._playback_host = host
        self.playback_controls.setParent(host)
        host.installEventFilter(self)
        self._position_playback()

    def _position_playback(self):
        if not hasattr(self, "playback_controls"):
            return
        host, controls = self._playback_host, self.playback_controls
        controls.fit_width(host.width() - 24)
        size = controls.size()
        margins = self.layout().contentsMargins()
        bottom = size.height() + 32
        if margins.bottom() != bottom:
            self.layout().setContentsMargins(margins.left(), margins.top(), margins.right(), bottom)
        controls.move(max(12, (host.width() - size.width()) // 2), max(12, host.height() - size.height() - 16))
        visible = self.isVisible() and not (self.drawer is not None and self.drawer.isVisible())
        controls.setVisible(visible)
        if visible:
            controls.raise_()

    def eventFilter(self, watched, event):
        if watched is self._playback_host and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._position_playback()
        elif watched is self.drawer and event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            QTimer.singleShot(0, self, self._position_playback)
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self._position_playback()

    def set_playback_speed(self, speed):
        if speed not in self.playback_controls.SPEEDS:
            return False
        self.playback_speed = float(speed)
        self.timer.setInterval(round(1000 / speed))
        with QSignalBlocker(self.speed_tabs):
            self.speed_tabs.setCurrentIndex(self.playback_controls.SPEEDS.index(speed))
        return True

    @property
    def frame(self):
        return self.frames[self.index] if self.data is not None and self.frames else None

    def selected_groups(self):
        """조회 범위에 설비가 있는 설비군을 모두 골랐으면 None(전체)."""
        chosen = {key for key, control in self.legend_buttons.items() if control.isChecked()}
        return None if len(chosen) == len(self.legend_buttons) else chosen

    def _show_view(self, index):
        self.chart.setVisible(index == 0)
        self.chart_row.setVisible(index == 0)
        self.heatmap.setVisible(index == 1)
        if self.heatmap_controls is not None:
            self.heatmap_controls.setVisible(index == 1)
        self.trend_legend_host.setVisible(index == 0 and self.grouping_mode != "group"
                                          and self.trend_legend_body.count() > 0)
        if self.drawer is not None:
            self.drawer.close_panel(restore_focus=False)
        if self.frame is not None:
            self._render_frame()

    def _attach_heatmap_controls(self, controls):
        """히트맵과 같은 검색·필터 도구를 리플레이의 첫 행에 한 번만 배치한다."""
        self.heatmap_controls = controls
        self.heatmap.view.content.body.removeWidget(controls)
        self.content.body.insertWidget(0, controls)
        controls.setVisible(self.view_tabs.currentIndex() == 1)
        controls.setEnabled(self.data is not None and not self.dirty and not self.busy)

    def set_grouping(self, mode):
        """추이와 히트맵에 같은 분류를 적용하며 조회 데이터는 그대로 사용한다."""
        if mode not in {key for key, _ in GROUPING_MODES}:
            return False
        self.grouping_mode = mode
        with QSignalBlocker(self.grouping_tabs):
            self.grouping_tabs.setCurrentIndex(next(i for i, (key, _) in enumerate(GROUPING_MODES) if key == mode))
        self.heatmap.set_grouping(mode)
        self.legend_host.setVisible(mode == "group" and bool(self.legend_buttons))
        self.trend_legend_host.setVisible(self.view_tabs.currentIndex() == 0 and mode != "group"
                                          and self.trend_legend_body.count() > 0)
        if self.frame is not None:
            self._render_frame()
        return True

    def _sync_controls(self):
        ready = self.data is not None and not self.dirty and not self.busy
        self.query_panel.card.setEnabled(not self.busy)
        self.play_button.setEnabled(ready and can_replay(self.frames) and not self.timer.isActive() and not self._seeking)
        self.pause_button.setEnabled(ready and self.timer.isActive())
        self.restart_button.setEnabled(ready and bool(self.frames) and not self._seeking)
        self.position_slider.setEnabled(ready and len(self.frames) > 1)
        self.speed_tabs.setEnabled(ready)
        for step, control in self.interval_buttons.items():
            # 간격이 기간보다 길어 시점이 2개 미만이면 그 간격은 고를 수 없다. 저장일마다는 항상 고를 수 있다.
            control.setEnabled(ready and (step is None or can_replay(self.data.frames_for(step))))
        for control in self.legend_buttons.values():
            control.setEnabled(ready)
        for control in self.legend_tools:
            control.setEnabled(ready)
        for control in (*self.trend_legend_buttons.values(), *self.trend_legend_tools):
            control.setEnabled(ready)
        self.view_tabs.setEnabled(not self.busy)
        self.grouping_tabs.setEnabled(ready)
        self.heatmap.setEnabled(ready)
        if self.heatmap_controls is not None:
            self.heatmap_controls.setEnabled(ready)
        self.chart.setEnabled(ready)
        self._position_playback()

    def _notice(self, text, *, error=False):
        self.message.setText(text)
        self.message.setStyleSheet("color:#ad344d;" if error else f"color:{SECONDARY};")
        self.message.setVisible(bool(text))

    def enter(self):
        if self.state.catalog != self.source.catalog:
            self.invalidate()
        if self.data is None and not self.busy and self.state.applied is None:
            self.apply_query()

    def apply_query(self):
        if self.busy:
            return False
        self.pause()
        start, end = self.start.date().toPython(), self.end.date().toPython()
        if start > end:
            self.dirty = True
            self._notice("시작 날짜는 종료 날짜와 같거나 앞서야 합니다.", error=True)
            self._sync_controls()
            return False
        try:
            self.state.set_target_draft(self.targets_provider())
            query = self.query_panel.apply_draft()
            if query is None:
                return False
            request = ReplayRequest(start, end, query)
        except (ValueError, TypeError):
            self.dirty = True
            self._notice("조회 조건과 기준 버전을 확인해 주세요.", error=True)
            self._sync_controls()
            return False
        self.epoch += 1
        self.pending_request = request
        # 이전 결과의 조작은 막고 새 결과가 도착할 때까지 화면은 유지한다.
        self.data, self.index, self.frames = None, 0, ()
        self.dirty = False
        if self.drawer is not None:
            self.drawer.close_panel(restore_focus=False)
        self._notice("")
        work = (lambda: self.loader(request)) if self.loader is not None else (lambda: load_replay(self.source, request))
        started = self.read_request.start(BackgroundRead(work))
        self._sync_controls()
        return started

    def _loaded(self, data, error):
        request, self.pending_request = self.pending_request, None
        if request is None:
            return
        if not self.state.is_current(request.query):
            self._sync_controls()
            return
        if error is not None or not isinstance(data, ReplayData) or data.request != request:
            self._clear_display()
            self._notice("관측 자료를 읽지 못했습니다. 날짜와 조건을 확인한 뒤 다시 조회해 주세요.", error=True)
            self._sync_controls()
            return
        self.data, self.dirty = data, False
        self.frames = data.frames_for(self.step)
        if self.step is not None and not can_replay(self.frames):
            self.step, self.frames = None, data.frames  # 새 기간에 맞지 않는 간격이면 저장일마다로 되돌린다
        self._sync_interval_selector()
        self.index = max(0, len(self.frames) - 1)
        self.epoch += 1
        if self.frames:
            self._make_legend()
            self._render_frame()
        else:
            self._clear_display()
        self._notice("기간 안에 저장된 관측일이 없습니다." if not data.frames else
                     "재생하려면 조건에 맞는 실제 관측일이 두 개 이상 필요합니다." if not data.can_replay else "")
        self._sync_controls()

    def _sync_interval_selector(self):
        if self.step is None:
            self.timeline.set_saved_days()
        else:
            self.timeline.set_step(self.step)

    def set_interval(self, step):
        """재생 간격을 바꾼다 (요청 2-1). 다시 조회하지 않고 이미 읽은 자료로 시점만 다시 만든다."""
        valid_step = step in self.interval_buttons or type(step) is int and step > 0
        if not valid_step or self.data is None or self.dirty or self.busy or step == self.step:
            self._sync_interval_selector()
            return False
        frames = self.data.frames_for(step)
        if step is not None and not can_replay(frames):
            self._sync_interval_selector()
            return False
        # 재생 중에 간격을 바꾸면 멈추지 않고, 보던 날짜(또는 그 전 가장 가까운 시점)부터 이어서 재생한다 (11-11 점검).
        playing, current = self.timer.isActive(), self.frame.day if self.frame is not None else None
        self.timer.stop()
        index = max(0, len(frames) - 1)
        if playing and current is not None:
            index = max((i for i, frame in enumerate(frames) if frame.day <= current), default=0)
        self.step, self.frames, self.index = step, frames, index
        self._sync_interval_selector()
        if playing and index < len(frames) - 1:
            self.timer.start()
        self._make_legend()
        self._render_frame()
        self._sync_controls()
        return True

    @staticmethod
    def _empty_layout(layout):
        while layout.count():
            widget = layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()

    def _clear_legend(self):
        self._empty_layout(self.legend_body)
        self.legend_buttons = {}
        self.legend_tools = ()
        self.legend_host.hide()
        self._empty_layout(self.trend_legend_body)
        self.trend_legend_buttons, self.trend_legend_tools = {}, ()
        self.trend_legend_host.hide()
        self._trend_legend_key = None

    def _make_legend(self):
        """조회 기간과 재생 간격에 따라 표시할 설비군 칩을 구성한다."""
        self._clear_legend()
        available = {cell.group_keys[0] for frame in self.frames for cell in frame.snapshot.groups
                     if len(cell.group_keys) == 1 and cell.equipment_ids}
        if not available:
            return
        self.toggle_all_button = bulk_selection_toggle_button()
        self.toggle_all_button.clicked.connect(self.toggle_all_groups)
        self.legend_tools = (self.toggle_all_button,)
        for group in self.data.source.catalog.groups:
            if group.key not in available:
                continue
            control = button(group.label)
            control.setCheckable(True)
            control.setChecked(group.key not in self._hidden_groups)
            set_widget_font(control, font(13, medium=True))
            control.setStyleSheet(series_button_style(series_color(group.key)))
            control.setAccessibleName(f"설비군 {group.label} 보기")
            control.toggled.connect(self._groups_changed)
            self.legend_buttons[group.key] = control
        widgets = (*self.legend_tools, *self.legend_buttons.values())
        self.legend_body.addWidget(DashboardGrid(widgets, minimum_width=96, maximum_columns=9, spacing=8))
        self._update_bulk_toggle()
        self.legend_host.setVisible(self.grouping_mode == "group")

    def set_all_groups(self, checked):
        """설비군 버튼 전체 선택·해제. 화면은 한 번만 다시 그린다."""
        if self.data is None or self.dirty:
            return
        for control in self.legend_buttons.values():
            blocked = control.blockSignals(True)
            control.setChecked(checked)
            control.blockSignals(blocked)
        self._groups_changed()

    def toggle_all_groups(self):
        """한 번 누르면 전체 선택, 모두 골라져 있을 때 누르면 전체 해제."""
        self.set_all_groups(self.selected_groups() is not None)

    def _groups_changed(self, *_):
        """고른 설비군이 바뀌면 그래프 선·달성률·히트맵을 같은 설비군으로 다시 그린다 (요청 2-2)."""
        if self.data is None or self.dirty:
            return
        self._hidden_groups.difference_update(self.legend_buttons)
        self._hidden_groups.update(key for key, control in self.legend_buttons.items() if not control.isChecked())
        self._update_bulk_toggle()
        self._render_frame()

    def _update_bulk_toggle(self):
        if self.legend_tools:
            set_bulk_selection_state(self.toggle_all_button, self.selected_groups() is None)

    def _draw_chart(self, *_):
        if self.data is not None:
            groups = self.selected_groups() if self.grouping_mode == "group" else None
            visible = self.legend_buttons.keys() if groups is None else groups
            self.chart.show_data(self.data, visible, current=self.index, frames=self.frames,
                                 grouping=self.grouping_mode,
                                 hidden_series=self._hidden_trends.get(self.grouping_mode, ()))
            self.chart_row.sync_axis()
            self._update_trend_legend()

    def _update_trend_legend(self):
        """숨긴 선도 범례에 남겨 위치 분류별 선택을 다시 바꿀 수 있다."""
        if self.grouping_mode == "group":
            self.trend_legend_host.hide()
            return
        trends = self.chart.available_trends
        key = (self.grouping_mode, tuple((trend.key, trend.label, trend.color_key) for trend in trends))
        if key != self._trend_legend_key:
            self._empty_layout(self.trend_legend_body)
            self.trend_legend_buttons, self.trend_legend_tools = {}, ()
            mode = self.grouping_mode
            hidden = self._hidden_trends.get(mode, ())
            if trends:
                toggle = bulk_selection_toggle_button()
                toggle.clicked.connect(self.toggle_all_trends)
                self.trend_legend_tools = (toggle,)
            for trend in trends:
                control = button(trend.label)
                control.setCheckable(True)
                control.setChecked(trend.key not in hidden)
                set_widget_font(control, font(13, medium=True))
                control.setStyleSheet(series_button_style(series_color(trend.color_key)))
                control.setAccessibleName(f"{dict(GROUPING_MODES)[mode]} {trend.label} 보기")
                control.toggled.connect(lambda checked, mode=mode: self._trend_series_changed(mode))
                self.trend_legend_buttons[trend.key] = control
            widgets = (*self.trend_legend_tools, *self.trend_legend_buttons.values())
            if widgets:
                self.trend_legend_body.addWidget(DashboardGrid(widgets, minimum_width=96, maximum_columns=9, spacing=8))
            self._trend_legend_key = key
        self._update_trend_bulk_toggle()
        self.trend_legend_host.setVisible(self.view_tabs.currentIndex() == 0 and bool(trends))

    def set_all_trends(self, checked):
        """위치 분류의 추이 선만 한 번에 켜거나 끈다."""
        if self.data is None or self.dirty or self.busy or self.grouping_mode == "group":
            return
        for control in self.trend_legend_buttons.values():
            with QSignalBlocker(control):
                control.setChecked(checked)
        self._trend_series_changed(self.grouping_mode)

    def toggle_all_trends(self):
        self.set_all_trends(not all(control.isChecked() for control in self.trend_legend_buttons.values()))

    def _trend_series_changed(self, mode):
        if self.data is None or self.dirty or self.busy or mode != self.grouping_mode:
            return
        hidden = self._hidden_trends.setdefault(mode, set())
        hidden.difference_update(self.trend_legend_buttons)
        hidden.update(key for key, control in self.trend_legend_buttons.items() if not control.isChecked())
        self._draw_chart()

    def _update_trend_bulk_toggle(self):
        if self.trend_legend_tools:
            toggle = self.trend_legend_tools[0]
            set_bulk_selection_state(toggle, all(control.isChecked() for control in self.trend_legend_buttons.values()))
            toggle.setAccessibleName(f"{dict(GROUPING_MODES)[self.grouping_mode]} " + toggle.text())

    def _clear_display(self):
        self._clear_legend()
        self.chart.clear()
        self.heatmap.clear()
        self.playback_controls.clear_context()
        self.achievement.setText("전체 적용률 —")
        self.achievement.setToolTip("")  # 이전 조회의 대수 설명이 남지 않게 한다 (QA R-06).
        self.progress.setAccessibleDescription("")
        self.progress.setToolTip("")

    def _render_frame(self):
        if self.drawer is not None:
            self.drawer.close_panel(restore_focus=False)
        frame = self.frame
        if frame is None:
            self.playback_controls.clear_context()
            return
        # 숨은 보기는 전환할 때 현재 시점을 표시한다. 재생 중에는 보이는 보기만 갱신한다.
        if self.view_tabs.currentIndex() == 0:
            self._draw_chart()
        groups = self.selected_groups() if self.grouping_mode == "group" else None
        self.playback_controls.set_context(frame.day, frame.snapshot.base_date, self.index, len(self.frames),
                                          selected_groups=groups is not None)
        summary, observed_count, carried_count = group_view(self.data, frame, groups)
        self.playback_controls.set_application_rate(summary.rate if summary is not None else None)
        if summary is None:
            self.achievement.setText("전체 적용률 —")
            self.achievement.setToolTip("설비군을 하나 이상 골라 주세요.")
            self.progress.setAccessibleDescription("설비군 선택 없음")
        else:
            decided = summary.compliant + summary.below
            value = f"{summary.rate:.1%}" if summary.rate is not None else "계산 불가"
            # 1주·1개월 간격 시점은 그 전 마지막 저장일 자료를 이어 쓴다. 화면이 몇 칸 그대로인 이유를 바로 보이게 한다.
            carried = (f" · {frame.snapshot.base_date:%m-%d} 저장 기준"
                       if frame.at is not None and frame.at != frame.snapshot.base_date else "")
            self.achievement.setText(f"전체 적용률 {value}")
            observed = f"이날 저장 {observed_count}대"
            if carried_count:
                observed += f" · 이전 관측 이어받음 {carried_count}대"
            if frame.at is not None and frame.at != frame.snapshot.base_date:
                observed += f" · 마지막 저장일 {frame.snapshot.base_date} 기준"
            hpa = f" · HPA {summary.hpa_excluded}대 제외" if groups is None else ""
            tooltip = (f"등록 SW 목표 달성 {summary.compliant} / {decided}건 · {value}{carried}\n"
                       f"{frame.day} · 분석 {summary.equipment_count}대 ({observed}){hpa} · "
                       f"판단 불가 {summary.undetermined}건은 달성률 분모 제외")
            reasons = information_gap_reasons(summary)
            self.achievement.setToolTip(f"{tooltip}\n정보 부족 사유\n{reasons}" if reasons else tooltip)
            self.progress.setAccessibleDescription(value)
        self.progress.setToolTip(self.achievement.toolTip())
        if self.view_tabs.currentIndex() == 1:
            self.heatmap.show_frame(self.data, frame, groups)
        self._position_playback()

    def select_date(self, index):
        if self.data is None or self.dirty or not 0 <= index < len(self.frames):
            return False
        self.pause()
        self.index = index
        if self.drawer is not None:
            self.drawer.close_panel(restore_focus=False)
        self._render_frame()
        self._sync_controls()
        return True

    def _begin_seek(self):
        self._seeking = True
        self._seek_was_playing = self.timer.isActive()
        self.timer.stop()
        self._sync_controls()

    def _preview_seek(self, index):
        if self._seeking and 0 <= index < len(self.frames):
            self.position_slider.setToolTip(f"재생 위치 {self.frames[index].day:%Y-%m-%d} · {index + 1} / {len(self.frames)}")

    def _finish_seek(self):
        if not self._seeking:
            return
        index, resume = self.position_slider.sliderPosition(), self._seek_was_playing
        self._seeking, self._seek_was_playing = False, False
        self._seek_position(index, resume=resume)

    def _seek_position(self, index, *, resume=None):
        # 드래그 중에는 프레임을 계산하지 않고 손잡이를 놓았을 때 한 번만 갱신한다.
        if self._seeking or self.data is None or self.dirty or self.busy or not 0 <= index < len(self.frames):
            return False
        playing = self.timer.isActive() if resume is None else resume
        if index != self.index and not self.select_date(index):
            return False
        if playing and index < len(self.frames) - 1:
            self.timer.start()
        elif index == len(self.frames) - 1:
            self.timer.stop()
        self._sync_controls()
        return True

    def play(self):
        if self.data is None or self.dirty or self.busy or not can_replay(self.frames):
            return False
        if self.index == len(self.frames) - 1:
            self.index = 0
        self.timer.start()
        self._render_frame()
        self._sync_controls()
        return True

    def pause(self):
        self.timer.stop()
        self._seeking, self._seek_was_playing = False, False
        self._sync_controls()

    def restart(self):
        if self.data is None or self.dirty or not self.frames:
            return False
        self.index = 0
        self._render_frame()
        return True

    def advance(self):
        if not self.timer.isActive() or self.data is None or self.dirty:
            return
        if self.index < len(self.frames) - 1:
            self.index += 1
            self._render_frame()
        if self.index == len(self.frames) - 1:
            self.pause()

    def _reset_period(self):
        days = self.source.analysis_dates or self.source.dates
        with QSignalBlocker(self.start), QSignalBlocker(self.end):
            self.start.setDate(QDate.fromString(str(days[0]), "yyyy-MM-dd") if days else QDate.currentDate())
            self.end.setDate(QDate.fromString(str(days[-1]), "yyyy-MM-dd") if days else QDate.currentDate())
        self.query_panel.sync_period_range()

    def reset_filters(self):
        if self.busy:
            return False
        self._reset_period()
        self.query_panel.reset_draft()
        return True

    def open_versions(self):
        if self.busy:
            return
        self.pause()
        if self.version_dialog is not None:
            self.version_dialog.raise_()
            return
        epoch = self.epoch
        options = self.data.options if self.data is not None else ()
        dialog = VersionFilterDialog(options, self.state.draft.versions,
                                     group_options=version_group_options(self.data.source if self.data is not None else self.source, options),
                                     is_current=lambda: self.epoch == epoch, parent=self.window())
        self.version_dialog = dialog
        def done(result):
            if result == QDialog.DialogCode.Accepted and self.epoch == epoch:
                self.query_panel.set_version_filter(dialog.result_filter)
            self.version_dialog = None
            dialog.deleteLater()
        dialog.finished.connect(done)
        dialog.open()

    def is_current_equipment_selection(self, request):
        frame = self.frame
        return (frame is not None and not self.dirty and request.query is frame.snapshot.query
                and request.base_date == frame.snapshot.base_date and request.equipment_id in self.heatmap.visible_ids)

    def open_equipment(self, equipment_id, tile=None):
        frame = self.frame
        if frame is None or self.dirty or equipment_id not in self.heatmap.visible_ids:
            return False
        if tile is None:
            tile = self.heatmap.tile_buttons.get(equipment_id, self.heatmap.list_table)
        elif tile is not self.heatmap.list_table and (tile not in self.heatmap.tile_occurrences
                                                     or tile.data.equipment_id != equipment_id):
            return False
        self.heatmap.select_equipment(equipment_id)
        self.pause()
        try:
            detail, rows, _ = replay_detail(self.data, frame, equipment_id)
        except (ValueError, StopIteration):
            return False
        if self.drawer is None:
            self.drawer = EquipmentDrawer(self.window(), object_name="replayEquipmentDrawer")
            self.drawer.installEventFilter(self)
        snapshot = frame.snapshot
        request = EquipmentSelection(snapshot.query, snapshot.base_date, equipment_id, detail.record.base_date)
        self.drawer.open_detail(request, detail, rows, return_focus=tile)
        self._position_playback()
        self.drawer_opened.emit()
        return True

    def invalidate(self, *_):
        self.pause()
        self.read_request.cancel()
        self.epoch += 1
        self.data, self.pending_request, self.dirty, self.frames = None, None, True, ()
        if self.state.catalog != self.source.catalog:
            self.query_panel.set_catalog(self.source.catalog)
        self._clear_display()
        self._notice("저장 자료가 변경되었습니다. 조회 조건을 다시 선택해 주세요.")
        if self.drawer is not None:
            self.drawer.close_panel(restore_focus=False)
        self._sync_controls()

    def hideEvent(self, event):
        self.playback_controls.hide()
        self.pause()
        if self.busy:
            self.read_request.cancel()
            self.pending_request = None
            self._sync_controls()
        if self.version_dialog is not None:
            self.version_dialog.reject()
        if self.query_panel.filter_popup is not None:
            self.query_panel.filter_popup.reject()
        if self.drawer is not None:
            self.drawer.close_panel(restore_focus=False)
        super().hideEvent(event)
