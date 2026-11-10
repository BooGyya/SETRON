"""VER5 / 6 Version Replay. 한 화면의 날짜·재생 상태만 바꾸는 읽기 전용 UI."""

from PySide6.QtCore import QDate, QSignalBlocker, QTimer, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QHBoxLayout, QProgressBar, QTabBar, QVBoxLayout, QWidget,
)

from setron.ui.components import Card, button, label
from setron.ui.dashboard import TAB_STYLE
from setron.ui.dashboard_charts import series_button_style, series_color
from setron.ui.dashboard_data import information_gap_reasons
from setron.ui.equipment import ignore_width
from setron.ui.equipment_drawer import EquipmentDrawer
from setron.ui.heatmap_data import EquipmentSelection
from setron.ui.query_panel import QueryPanel, _WrapRow
from setron.ui.query_state import QueryState
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.replay_data import INTERVALS, ReplayData, ReplayRequest, can_replay, group_view, load_replay, replay_detail
from setron.ui.replay_widgets import ReplayChart, ReplayHeatmap
from setron.ui.theme import LINE, PRIMARY, SECONDARY, font, set_widget_font
from setron.ui.version_filter_dialog import VersionFilterDialog


class ReplayPage(QWidget):
    def __init__(self, source, *, loader=None, targets_provider=lambda: None):
        super().__init__()
        self.source, self.loader, self.targets_provider = source, loader, targets_provider
        self.state = QueryState(source.catalog)  # 공통 조회 초안·적용 N과 독립적인 조회 상태.
        self.data, self.pending_request = None, None
        self.index, self.epoch, self.dirty = 0, 0, True
        self.version_dialog, self.drawer = None, None
        self.read_request = ReadRequest(self)
        self.read_request.completed.connect(self._loaded)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.advance)
        self.legend_buttons, self.legend_tools, self.frames = {}, (), ()  # frames: 지금 재생 간격의 시점들
        self._legend_available = set()
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
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
        self.timeline = QWidget()
        interval_row = QHBoxLayout(self.timeline)
        interval_row.setContentsMargins(0, 0, 0, 0)
        interval_row.setSpacing(12)
        interval_row.addWidget(label("재생 간격", 14, medium=True, color=SECONDARY))
        segment = QWidget()
        segment.setObjectName("replayInterval")
        segment.setStyleSheet(f"QWidget#replayInterval {{background:#f3f5f8;border:1px solid {LINE};border-radius:10px;}}")
        segment_row = QHBoxLayout(segment)
        segment_row.setContentsMargins(3, 3, 3, 3)
        segment_row.setSpacing(2)
        self.interval_group = QButtonGroup(self)
        self.interval_group.setExclusive(True)
        self.interval_buttons, self.step = {}, None
        for step, caption in INTERVALS.items():
            control = button(caption)
            control.setCheckable(True)
            control.setChecked(step is None)
            control.setFixedSize(96 if step is None else 72, 34)
            control.setAccessibleName(f"재생 간격 {caption}")
            control.setToolTip("실제 저장된 날짜마다 재생" if step is None else
                               f"시작 날짜부터 {caption} 간격으로 재생 · 저장되지 않은 날짜는 그 전 마지막 관측으로 계산")
            control.setStyleSheet(f"QPushButton {{background:transparent;color:{SECONDARY};border:0;border-radius:8px;padding:0;"
                                  "min-height:34px;max-height:34px;}"
                                  f"QPushButton:checked {{background:white;color:{PRIMARY};border:1px solid {PRIMARY};}}"
                                  f"QPushButton:disabled {{color:#b8bec9;}}"
                                  f"QPushButton:focus {{border:2px solid {PRIMARY};}}")
            control.clicked.connect(lambda checked=False, s=step: self.set_interval(s))
            self.interval_group.addButton(control)
            self.interval_buttons[step] = control
            segment_row.addWidget(control)
        interval_row.addWidget(segment)
        interval_row.addStretch()
        self.content.body.addWidget(self.timeline)
        self.legend_host = QWidget()
        self.legend_body = QVBoxLayout(self.legend_host)
        self.legend_body.setContentsMargins(0, 0, 0, 0)
        self.content.body.addWidget(self.legend_host)
        # 두 보기에서 같은 재생 상태를 제어한다.
        playback = QWidget()
        playback_row = QHBoxLayout(playback)
        playback_row.setContentsMargins(0, 0, 0, 0)
        playback_row.setSpacing(8)
        playback_row.addStretch(1)
        # 앱 글꼴(IBM Plex Sans KR)에 있는 기호만 쓴다. ⏸·⏮는 글꼴에 없어 네모로 깨진다.
        self.play_button, self.pause_button, self.restart_button = button("▶", primary=True), button("‖"), button("↺")
        for control, name in ((self.play_button, "재생"), (self.pause_button, "일시정지"), (self.restart_button, "처음부터")):
            control.setAccessibleName(name)
            control.setToolTip(name)
            set_widget_font(control, font(16, medium=True))
            control.setStyleSheet("QPushButton {padding:0;min-height:38px;max-height:38px;}"
                                  "QPushButton:focus {min-height:36px;max-height:36px;}")
            control.setFixedSize(56, 40)
            playback_row.addWidget(control)
        playback_row.addStretch(1)
        self.content.body.addWidget(playback)
        self.achievement = label("등록 SW 목표 달성 —", 20, medium=True, wrap=True)
        self.content.body.addWidget(self.achievement)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFixedHeight(24)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("현재 관측일의 등록 SW 목표 달성률")
        self.progress.setStyleSheet(f"QProgressBar {{background:#e8ebfc;border:0;border-radius:0;}}"
                                   f"QProgressBar::chunk {{background:{PRIMARY};border-radius:0;}}")
        self.content.body.addWidget(self.progress)
        self.view_tabs = QTabBar()
        self.view_tabs.setAccessibleName("리플레이 보기")
        self.view_tabs.setDrawBase(False)
        self.view_tabs.setExpanding(False)
        self.view_tabs.setStyleSheet(TAB_STYLE)
        set_widget_font(self.view_tabs, font(14, medium=True))
        for caption in ("달성률 추이", "히트맵"):
            self.view_tabs.addTab(caption)
        self.content.body.addWidget(self.view_tabs, 0, Qt.AlignmentFlag.AlignLeft)
        self.chart = ReplayChart()
        self.chart.date_selected.connect(self.select_date)
        self.content.body.addWidget(self.chart)
        self.heatmap = ReplayHeatmap()
        self.heatmap.equipment_selected.connect(self.open_equipment)
        self.content.body.addWidget(self.heatmap)
        self.view_tabs.currentChanged.connect(self._show_view)
        self._show_view(self.view_tabs.currentIndex())
        body.addWidget(self.content)
        self.query_panel.confirmed.connect(self._confirm_filters)
        self.reset_button.clicked.connect(self.reset_filters)
        self.version_button.clicked.connect(self.open_versions)
        self.play_button.clicked.connect(self.play)
        self.pause_button.clicked.connect(self.pause)
        self.restart_button.clicked.connect(self.restart)
        self._sync_controls()

    @property
    def busy(self):
        return self.read_request.busy

    @property
    def frame(self):
        return self.frames[self.index] if self.data is not None and self.frames else None

    def selected_groups(self):
        """조회 범위에 설비가 있는 설비군을 모두 골랐으면 None(전체)."""
        chosen = {key for key in self._legend_available if self.legend_buttons[key].isChecked()}
        return None if chosen == self._legend_available else chosen

    def _show_view(self, index):
        self.chart.setVisible(index == 0)
        self.heatmap.setVisible(index == 1)
        if self.drawer is not None:
            self.drawer.close_panel(restore_focus=False)

    def _confirm_filters(self):
        if self.data is not None and not self.dirty:
            request = self.data.request
            if (self.start.date().toPython(), self.end.date().toPython(), self.state.draft) == (
                    request.start, request.end, request.query.selection):
                return
        self.apply_query()

    def _sync_controls(self):
        ready = self.data is not None and not self.dirty and not self.busy
        self.query_panel.card.setEnabled(not self.busy)
        self.play_button.setEnabled(ready and can_replay(self.frames) and not self.timer.isActive())
        self.pause_button.setEnabled(ready and self.timer.isActive())
        self.restart_button.setEnabled(ready and bool(self.frames))
        for step, control in self.interval_buttons.items():
            # 간격이 기간보다 길어 시점이 2개 미만이면 그 간격은 고를 수 없다. 저장일마다는 항상 고를 수 있다.
            control.setEnabled(ready and (step is None or can_replay(self.data.frames_for(step))))
        for key, control in self.legend_buttons.items():
            control.setEnabled(ready and key in self._legend_available)
        for control in self.legend_tools:
            control.setEnabled(ready and bool(self._legend_available))
        self.heatmap.setEnabled(ready)
        self.chart.setEnabled(ready)

    def _notice(self, text, *, error=False):
        self.message.setText(text)
        self.message.setStyleSheet("color:#ad344d;" if error else f"color:{SECONDARY};")
        self.message.setVisible(bool(text))

    def enter(self):
        if self.state.catalog != self.source.catalog:
            self.invalidate()
        if self.data is None and not self.busy:
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
            request = ReplayRequest(start, end, query)
        except (ValueError, TypeError):
            self.dirty = True
            self._notice("조회 조건과 기준 버전을 확인해 주세요.", error=True)
            self._sync_controls()
            return False
        self.epoch += 1
        self.pending_request = request
        self.data, self.index, self.frames = None, 0, ()
        self.dirty = False
        if self.drawer is not None:
            self.drawer.close_panel(restore_focus=False)
        self._clear_display()
        self._notice("저장된 관측 자료를 읽고 있습니다…")
        work = (lambda: self.loader(request)) if self.loader is not None else (lambda: load_replay(self.source, request))
        started = self.read_request.start(BackgroundRead(work))
        self._sync_controls()
        return started

    def _loaded(self, data, error):
        if self.pending_request is not None and self.pending_request.query.selection != self.state.draft:
            # 팝업에서 계속 선택할 수 있으므로 읽기가 끝나면 가장 최근 선택으로 다시 조회한다.
            self.apply_query()
            return
        if error is not None or not isinstance(data, ReplayData) or data.request != self.pending_request:
            self.data, self.frames = None, ()
            self._notice("관측 자료를 읽지 못했습니다. 날짜와 조건을 확인한 뒤 다시 조회해 주세요.", error=True)
            self._sync_controls()
            return
        self.data, self.dirty = data, False
        self.frames = data.frames_for(self.step)
        if self.step is not None and not can_replay(self.frames):
            self.step, self.frames = None, data.frames  # 새 기간에 맞지 않는 간격이면 저장일마다로 되돌린다
            self.interval_buttons[None].setChecked(True)
        self.index = max(0, len(self.frames) - 1)
        self.epoch += 1
        self._make_legend()
        self._render_frame()
        self._notice("기간 안에 저장된 관측일이 없습니다." if not data.frames else
                     "재생하려면 조건에 맞는 실제 관측일이 두 개 이상 필요합니다." if not data.can_replay else "")
        self._sync_controls()

    def set_interval(self, step):
        """재생 간격을 바꾼다 (요청 2-1). 다시 조회하지 않고 이미 읽은 자료로 시점만 다시 만든다."""
        if step not in self.interval_buttons:
            return False
        self.interval_buttons[step].setChecked(True)
        if self.data is None or self.dirty or self.busy or step == self.step:
            return False
        frames = self.data.frames_for(step)
        if step is not None and not can_replay(frames):
            self.interval_buttons[self.step].setChecked(True)
            return False
        self.timer.stop()
        self.step, self.frames, self.index = step, frames, max(0, len(frames) - 1)
        self._render_frame()
        self._sync_controls()
        return True

    @staticmethod
    def _empty_layout(layout):
        while layout.count():
            widget = layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()

    def _make_legend(self):
        """대시보드와 같은 색으로 설비군을 표시하고, 자료가 없는 설비군은 비활성화한다."""
        previous = {key: self.legend_buttons[key].isChecked() for key in self._legend_available}
        self._empty_layout(self.legend_body)
        self.legend_buttons = {}
        known = self.data.frames + ((self.data.seed,) if self.data.seed else ())
        self._legend_available = {cell.group_keys[0] for frame in known for cell in frame.snapshot.groups
                                  if len(cell.group_keys) == 1 and cell.equipment_ids}
        self.toggle_all_button = button("전체 해제")
        self.toggle_all_button.setCheckable(True)
        self.toggle_all_button.setMinimumWidth(96)
        set_widget_font(self.toggle_all_button, font(13, medium=True))
        self.toggle_all_button.setStyleSheet(series_button_style(PRIMARY))
        self.toggle_all_button.clicked.connect(self.toggle_all_groups)
        self.legend_tools = (self.toggle_all_button,)
        for group in self.data.source.catalog.groups:
            available = group.key in self._legend_available
            control = button(group.label)
            control.setCheckable(True)
            control.setChecked(available and previous.get(group.key, True))
            control.setEnabled(available)
            control.setMinimumWidth(120)
            set_widget_font(control, font(13, medium=True))
            control.setStyleSheet(series_button_style(series_color(group.key)))
            control.setAccessibleName(f"설비군 {group.label} 보기")
            control.toggled.connect(self._groups_changed)
            self.legend_buttons[group.key] = control
        widgets = [*self.legend_tools, *self.legend_buttons.values()] if self.legend_buttons else []
        self.legend_body.addWidget(_WrapRow(widgets, 120) if widgets else QWidget())
        self._update_bulk_toggle()

    def set_all_groups(self, checked):
        """설비군 버튼 전체 선택·해제. 화면은 한 번만 다시 그린다."""
        for key in self._legend_available:
            control = self.legend_buttons[key]
            blocked = control.blockSignals(True)
            control.setChecked(checked)
            control.blockSignals(blocked)
        self._groups_changed()

    def toggle_all_groups(self):
        """한 번 누르면 전체 선택, 모두 골라져 있을 때 누르면 전체 해제."""
        self.set_all_groups(self.selected_groups() is not None)

    def _groups_changed(self, *_):
        """고른 설비군이 바뀌면 그래프 선·달성률·히트맵을 같은 설비군으로 다시 그린다 (요청 2-2)."""
        self._update_bulk_toggle()
        if self.data is not None and not self.dirty:
            self._render_frame()

    def _update_bulk_toggle(self):
        if self.legend_tools:
            all_selected = bool(self._legend_available) and self.selected_groups() is None
            self.toggle_all_button.setChecked(all_selected)
            self.toggle_all_button.setText("전체 해제" if all_selected else "전체 선택")
            self.toggle_all_button.setAccessibleName("설비군 " + self.toggle_all_button.text())

    def _draw_chart(self, *_):
        if self.data is not None:
            groups = self.selected_groups()
            self.chart.show_data(self.data, self._legend_available if groups is None else groups,
                                 current=self.index, frames=self.frames)

    def _clear_display(self):
        self.chart.clear()
        self.heatmap.clear()
        self.achievement.setText("등록 SW 목표 달성 —")
        self.achievement.setToolTip("")  # 이전 조회의 대수 설명이 남지 않게 한다 (QA R-06).
        self.progress.setValue(0)
        self.progress.setAccessibleDescription("")

    def _render_frame(self):
        if self.drawer is not None:
            self.drawer.close_panel(restore_focus=False)
        # 히트맵과 함께 그래프도 현재 날짜까지 실선·세로선으로 다시 그린다 (재생 시 그래프도 움직임).
        self._draw_chart()
        frame = self.frame
        if frame is None:
            return
        groups = self.selected_groups()
        summary, observed_count, carried_count = group_view(self.data, frame, groups)
        if summary is None:
            self.achievement.setText("등록 SW 목표 달성 — · 설비군을 하나 이상 골라 주세요")
            self.achievement.setToolTip("")
            self.progress.setValue(0)
            self.progress.setAccessibleDescription("설비군 선택 없음")
        else:
            decided = summary.compliant + summary.below
            value = f"{summary.rate:.1%}" if summary.rate is not None else "계산 불가"
            self.achievement.setText(f"등록 SW 목표 달성 {summary.compliant} / {decided}건 · {value}")
            observed = f"이날 저장 {observed_count}대"
            if carried_count:
                observed += f" · 이전 관측 이어받음 {carried_count}대"
            if frame.at is not None and frame.at != frame.snapshot.base_date:
                observed += f" · 마지막 저장일 {frame.snapshot.base_date} 기준"
            hpa = f" · HPA {summary.hpa_excluded}대 제외" if groups is None else ""
            tooltip = (f"{frame.day} · 분석 {summary.equipment_count}대 ({observed}){hpa} · "
                       f"판단 불가 {summary.undetermined}건은 달성률 분모 제외")
            reasons = information_gap_reasons(summary)
            self.achievement.setToolTip(f"{tooltip}\n정보 부족 사유\n{reasons}" if reasons else tooltip)
            self.progress.setValue(round(summary.rate * 1000) if summary.rate is not None else 0)
            self.progress.setAccessibleDescription(value)
        self.heatmap.show_frame(self.data, frame, groups)

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

    def reset_filters(self):
        if self.busy:
            return False
        self._reset_period()
        self.query_panel.reset_draft()
        return self.apply_query()

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
                                     is_current=lambda: self.epoch == epoch, parent=self.window())
        self.version_dialog = dialog
        def done(result):
            if result == QDialog.DialogCode.Accepted and self.epoch == epoch:
                self.query_panel.set_version_filter(dialog.result_filter)
                self._confirm_filters()
            self.version_dialog = None
            dialog.deleteLater()
        dialog.finished.connect(done)
        dialog.open()

    def open_equipment(self, equipment_id):
        if self.data is None or self.frame is None or self.dirty:
            return False
        self.pause()
        try:
            detail, rows, _ = replay_detail(self.data, self.frame, equipment_id)
        except (ValueError, StopIteration):
            return False
        if self.drawer is None:
            self.drawer = EquipmentDrawer(self.window())
        snapshot = self.frame.snapshot
        request = EquipmentSelection(snapshot.query, snapshot.base_date, equipment_id, detail.record.base_date)
        self.drawer.open_detail(request, detail, rows,
                                return_focus=self.heatmap.tile_buttons.get(equipment_id), edit_available=False)
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
