"""VER5 / 6 Version Replay. 한 화면의 날짜·재생 상태만 바꾸는 읽기 전용 UI."""

from dataclasses import replace

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QProgressBar, QSizePolicy, QVBoxLayout, QWidget

from setron.features.excel_import.service import parse_date
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.dashboard_charts import series_color
from setron.ui.equipment import ignore_width
from setron.ui.equipment_drawer import EquipmentDrawer
from setron.ui.heatmap_data import EquipmentSelection
from setron.ui.import_widgets import PeriodDateField, import_combo_style
from setron.ui.query_panel import _WrapRow
from setron.ui.query_state import UNASSIGNED, QuerySelection, QueryState
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.replay_data import ReplayData, ReplayRequest, load_replay, replay_detail
from setron.ui.replay_widgets import ReplayChart, ReplayHeatmap
from setron.ui.theme import INK, LINE, PRIMARY, SECONDARY
from setron.ui.version_filter_dialog import VersionFilterDialog
from setron.ui.versions import _field


class ReplayPage(QWidget):
    def __init__(self, source, *, loader=None, targets_provider=lambda: None):
        super().__init__()
        self.source, self.loader, self.targets_provider = source, loader, targets_provider
        self.state = QueryState(source.catalog)  # 공통 조회 초안·적용 N과 독립적인 조회 상태.
        self.data, self.pending_request = None, None
        self.index, self.epoch, self.dirty, self._filling = 0, 0, True, False
        self.version_dialog, self.drawer = None, None
        self.read_request = ReadRequest(self)
        self.read_request.completed.connect(self._loaded)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.advance)
        self.date_buttons, self.legend_buttons = [], {}
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.filters = Card(padding=20)
        self.filters.body.setSpacing(12)
        self.filters.setStyleSheet(f"QFrame#card {{background:white;border:1px solid {LINE};border-radius:8px;}}")
        self.start, self.end = PeriodDateField(), PeriodDateField()
        days = source.analysis_dates or source.dates
        if days:
            self.start.setText(str(days[0]))
            self.end.setText(str(days[-1]))
        for control, name in ((self.start, "Replay 시작 날짜"), (self.end, "Replay 종료 날짜")):
            control.setAccessibleName(name)
            control.setFixedHeight(38)
            control.setFixedWidth(160)
        period = QWidget()
        period.setFixedWidth(346)
        period_row = QHBoxLayout(period)
        period_row.setContentsMargins(0, 0, 0, 0)
        period_row.setSpacing(8)
        period_row.addWidget(_field("시작 날짜", self.start))
        separator = label("~", 14, color=SECONDARY)
        separator.setFixedWidth(10)
        period_row.addWidget(separator, 0, Qt.AlignmentFlag.AlignBottom)
        period_row.addWidget(_field("종료 날짜", self.end))
        self.combos, fields = {}, [period]
        for key, title in (("site_key", "Site"), ("fab_key", "Fab"), ("line_key", "Line"), ("group_key", "설비군")):
            control = QComboBox()
            control.setAccessibleName("Replay " + title)
            control.setProperty("queryField", True)
            control.setFixedHeight(38)
            control.setMinimumWidth(0)
            control.setStyleSheet(import_combo_style())
            control.currentIndexChanged.connect(lambda _, field=key: self._select(field))
            self.combos[key] = control
            fields.append(_field(title, control))
        tools = QWidget()
        tools.setFixedWidth(150)
        tools_row = QHBoxLayout(tools)
        tools_row.setContentsMargins(0, 0, 0, 0)
        tools_row.setSpacing(12)
        self.reset_button = button("", asset="replay-ver5/65ae3.svg")
        self.reset_button.setProperty("assetFile", "replay-ver5/65ae3.svg")
        self.reset_button.setAccessibleName("Replay 조건 초기화")
        self.reset_button.setToolTip("기간과 조회 조건 초기화 · 공통 목표 N은 변경하지 않습니다")
        self.reset_button.setFixedSize(48, 38)
        self.reset_button.setStyleSheet("QPushButton {background:#e8ecfc;border:1px solid #a8b8f7;border-radius:10px;padding:0;}")
        self.apply_button = button("조회 적용", primary=True)
        self.apply_button.setStyleSheet("QPushButton {padding:0;min-height:36px;max-height:36px;}"
                                       "QPushButton:focus {min-height:34px;max-height:34px;}")
        self.apply_button.setFixedSize(90, 38)
        tools_row.addWidget(self.reset_button)
        tools_row.addWidget(self.apply_button)
        fields.append(tools)
        self.filters.body.addWidget(_WrapRow(fields, 106, fields=True))
        self.version_button = button("버전(N) 기준 입력")
        self.version_button.setStyleSheet("QPushButton {padding:0;min-height:38px;max-height:38px;}"
                                         "QPushButton:focus {min-height:36px;max-height:36px;}")
        self.version_button.setFixedSize(170, 40)
        self.version_button.setAccessibleName("Replay 버전 다중 선택 조회")
        self.version_button.setToolTip("조회할 버전을 여러 개 선택합니다. 목표 N은 변경하지 않습니다.")
        self.filters.body.addWidget(self.version_button, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(self.filters)
        self.applied_row = QWidget()
        applied = QHBoxLayout(self.applied_row)
        applied.setContentsMargins(0, 0, 0, 0)
        applied.setSpacing(12)
        self.badge = StatusBadge("조회 적용 전", "info")
        self.context = label("기간과 조회 조건을 선택해 주세요.", 13, color=SECONDARY, wrap=True)
        ignore_width(self.context)
        applied.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        applied.addWidget(self.context, 1)
        body.addWidget(self.applied_row)
        self.message = label("", 13, color=SECONDARY, wrap=True)
        self.message.hide()
        ignore_width(self.message)
        body.addWidget(self.message)
        self.content = Card(padding=20)
        self.content.setStyleSheet("QFrame#card {background:white;border:0;border-radius:12px;}")
        self.content.body.setSpacing(12)
        self.content.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.snapshot_title = label("관측 시점", 20, medium=True)
        self.content.body.addWidget(self.snapshot_title)
        self.content.body.addWidget(label("선택 설비군 기준 집계", 13, color=SECONDARY))
        self.timeline = QWidget()
        self.timeline_body = QVBoxLayout(self.timeline)
        self.timeline_body.setContentsMargins(0, 0, 0, 0)
        self.content.body.addWidget(self.timeline)
        playback = []
        self.play_button, self.pause_button, self.restart_button = button("재생", primary=True), button("일시정지"), button("처음부터")
        for control in (self.play_button, self.pause_button, self.restart_button):
            control.setStyleSheet("QPushButton {padding:0;min-height:38px;max-height:38px;}"
                                 "QPushButton:focus {min-height:36px;max-height:36px;}")
            control.setFixedSize(136, 40)
            playback.append(control)
        self.content.body.addWidget(_WrapRow(playback, 136))
        self.chart_card = Card(padding=20)
        self.chart_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.chart_card.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chart_card.setStyleSheet("QFrame#card {background:white;border:0;border-radius:12px;}")
        self.chart_card.body.setSpacing(12)
        self.chart_card.body.addWidget(label("설비군별 목표 적용 추이", 20, medium=True))
        self.chart_context = label("전체 설비 · 기본 목표 N", 14, color=SECONDARY, wrap=True)
        self.chart_card.body.addWidget(self.chart_context)
        self.legend_host = QWidget()
        self.legend_body = QVBoxLayout(self.legend_host)
        self.legend_body.setContentsMargins(0, 0, 0, 0)
        self.chart_card.body.addWidget(self.legend_host)
        self.chart = ReplayChart()
        self.chart.date_selected.connect(self.select_date)
        self.chart_card.body.addWidget(self.chart)
        self.content.body.addWidget(self.chart_card)
        self.playback_status = label("일시정지 · 0 / 0", 15, medium=True)
        self.content.body.addWidget(self.playback_status)
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
        self.heatmap_title = label("선택 시점의 설비 Heatmap", 16, medium=True)
        self.content.body.addWidget(self.heatmap_title)
        description = label("재생하면 날짜 칩 순서대로 타일 색이 바뀝니다 · 버전 히트맵과 같은 설비군 → Line → 설비 묶음",
                            12, color=SECONDARY, wrap=True)
        ignore_width(description)
        self.content.body.addWidget(description)
        legend = []
        for color, caption in (("#1e9e63", "목표 달성"), ("#d6ad00", "목표 미달"),
                               ("#999ea8", "확인 필요"), ("#d63a3a", "위험 후보 우선")):
            legend.append(label("●  " + caption, 12, color=color))
        legend.append(label("버전 미확인", 11, color=SECONDARY))
        self.content.body.addWidget(_WrapRow(legend, 90))
        self.content.body.addWidget(label("설비군 → Line → 설비", 16, medium=True))
        group_policy = label("Site·Fab은 조회 조건으로 유지 · Line 미확정 값은 원본 구역으로 구분합니다. "
                             "복수 SW는 여러 설비군에 표시되며 전체 물리 대수는 중복하지 않습니다.",
                             11, color=SECONDARY, wrap=True)
        ignore_width(group_policy)
        self.content.body.addWidget(group_policy)
        self.heatmap = ReplayHeatmap()
        self.heatmap.equipment_selected.connect(self.open_equipment)
        self.content.body.addWidget(self.heatmap)
        body.addWidget(self.content)
        self.start.textChanged.connect(self._changed)
        self.end.textChanged.connect(self._changed)
        self.reset_button.clicked.connect(self.reset_filters)
        self.apply_button.clicked.connect(self.apply_query)
        self.version_button.clicked.connect(self.open_versions)
        self.play_button.clicked.connect(self.play)
        self.pause_button.clicked.connect(self.pause)
        self.restart_button.clicked.connect(self.restart)
        self._fill_choices()
        self._sync_controls()

    @property
    def busy(self):
        return self.read_request.busy

    @property
    def frame(self):
        return self.data.frames[self.index] if self.data and self.data.frames else None

    def _fill_choices(self):
        self._filling = True
        try:
            for key, options in (("site_key", self.state.catalog.sites), ("fab_key", self.state.available_fabs()),
                                 ("line_key", self.state.available_lines()), ("group_key", self.state.catalog.groups)):
                combo = self.combos[key]
                combo.clear()
                combo.addItem("전체", None)
                if key in ("fab_key", "line_key"):
                    combo.addItem("미지정", UNASSIGNED)
                for option in options:
                    combo.addItem(option.label, option.key)
                value = getattr(self.state.draft, key)
                combo.setCurrentIndex(max(0, combo.findData(value)))
        finally:
            self._filling = False

    def _select(self, key):
        if self._filling:
            return
        self.state.select(key, self.combos[key].currentData())
        self._fill_choices()
        self._changed()

    def _changed(self, *_):
        if self._filling:
            return
        self.pause()
        self.epoch += 1
        self.dirty = True
        self.badge.setText("재적용 필요" if self.data else "조회 적용 전")
        self._sync_controls()

    def _sync_controls(self):
        ready = self.data is not None and not self.dirty and not self.busy
        for control in (self.start, self.end, *self.combos.values(), self.reset_button, self.version_button, self.apply_button):
            control.setEnabled(not self.busy)
        self.apply_button.setText("조회 중" if self.busy else "조회 적용")
        self.play_button.setEnabled(ready and self.data.can_replay and not self.timer.isActive())
        self.pause_button.setEnabled(ready and self.timer.isActive())
        self.restart_button.setEnabled(ready and bool(self.data.frames))
        for control in self.date_buttons:
            control.setEnabled(ready)
        self.heatmap.setEnabled(ready)
        self.chart.setEnabled(ready)

    def _notice(self, text, *, error=False):
        self.message.setText(text)
        self.message.setStyleSheet("color:#ad344d;" if error else f"color:{SECONDARY};")
        self.message.setVisible(bool(text))

    def enter(self):
        if self.state.catalog != self.source.catalog:
            self.state.replace_catalog(self.source.catalog)
            self._fill_choices()
            self.invalidate()
        if self.data is None and not self.busy:
            self.apply_query()

    def apply_query(self):
        if self.busy:
            return False
        self.pause()
        try:
            start, end = parse_date(self.start.text()), parse_date(self.end.text())
            self.state.set_target_draft(self.targets_provider())
            query = self.state.apply()
            request = ReplayRequest(start, end, query)
        except (ValueError, TypeError):
            self._notice("시작·종료 날짜를 YYYY-MM-DD로 입력하고 시작일이 종료일보다 늦지 않은지 확인해 주세요.", error=True)
            return False
        self.epoch += 1
        self.pending_request = request
        self.data, self.index = None, 0
        self.dirty = False
        self._clear_display()
        self.badge.setText("조회 중")
        self._notice("저장된 관측 자료를 읽고 있습니다…")
        work = (lambda: self.loader(request)) if self.loader is not None else (lambda: load_replay(self.source, request))
        started = self.read_request.start(BackgroundRead(work))
        self._sync_controls()
        return started

    def _loaded(self, data, error):
        if error is not None or not isinstance(data, ReplayData) or data.request != self.pending_request:
            self.data = None
            self.badge.setText("조회 실패")
            self._notice("관측 자료를 읽지 못했습니다. 날짜와 조건을 확인한 뒤 다시 조회해 주세요.", error=True)
            self._sync_controls()
            return
        self.data, self.index, self.dirty = data, max(0, len(data.frames) - 1), False
        self.epoch += 1
        self.badge.setText("적용됨")
        selection = data.request.query.selection
        scope = self.state.describe(replace(selection, as_of=data.request.end))
        scope = scope.replace(f" · 기준일 {data.request.end}", "")
        n = len(data.frames[0].snapshot.target_values) if data.frames else len(data.source.targets)
        self.context.setText(f"기간 {data.request.start} ~ {data.request.end} · 저장된 기준일 {len(data.frames)}개 · "
                             f"{scope} · 목표 N {n}개 · 위험 판정 기준 고정")
        self.context.setToolTip(data.source.source_label)
        self.chart_context.setText((next((g.label for g in data.source.catalog.groups
                                         if g.key == data.request.query.selection.group_key), "전체 설비")) + " · 조회 적용 시 목표 N 고정")
        self._make_dates()
        self._make_legend()
        self._draw_chart()
        self._render_frame()
        self._notice("기간 안에 저장된 관측일이 없습니다." if not data.frames else
                     "재생하려면 조건에 맞는 실제 관측일이 두 개 이상 필요합니다." if not data.can_replay else "")
        self._sync_controls()

    @staticmethod
    def _empty_layout(layout):
        while layout.count():
            widget = layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()

    def _make_dates(self):
        self._empty_layout(self.timeline_body)
        self.date_buttons = []
        for index, frame in enumerate(self.data.frames):
            caption = frame.day.strftime("%m.%d" if self.data.frames[0].day.year == self.data.frames[-1].day.year else "%Y.%m.%d")
            control = button(caption)
            control.setCheckable(True)
            control.setFixedSize(136, 40)
            control.setAccessibleName(f"{frame.day} 관측 선택")
            control.clicked.connect(lambda checked=False, i=index: self.select_date(i))
            self.date_buttons.append(control)
        self.timeline_body.addWidget(_WrapRow(self.date_buttons, 136) if self.date_buttons else QWidget())

    def _make_legend(self):
        previous = {key: b.isChecked() for key, b in self.legend_buttons.items()}
        self._empty_layout(self.legend_body)
        self.legend_buttons = {}
        for group in self.data.source.catalog.groups:
            if self.data.request.query.selection.group_key not in (None, group.key):
                continue
            control = button(group.label)
            control.setCheckable(True)
            control.setChecked(previous.get(group.key, True))
            control.setFixedHeight(36)
            control.setMinimumWidth(120)
            color = series_color(group.key)
            control.setStyleSheet(f"QPushButton {{background:white;color:{SECONDARY};border:1px solid {LINE};border-radius:10px;padding:0 14px;}}"
                                 "QPushButton {min-height:34px;max-height:34px;}"
                                 f"QPushButton:checked {{color:{INK};border:2px solid {color};min-height:32px;max-height:32px;}}"
                                 "QPushButton:focus {background:#f7f9fc;}")
            control.toggled.connect(self._draw_chart)
            self.legend_buttons[group.key] = control
        self.legend_body.addWidget(_WrapRow(list(self.legend_buttons.values()), 120) if self.legend_buttons else QWidget())

    def _draw_chart(self, *_):
        if self.data is not None:
            self.chart.show_data(self.data, {key for key, control in self.legend_buttons.items() if control.isChecked()})

    def _clear_display(self):
        self._empty_layout(self.timeline_body)
        self.date_buttons = []
        self.chart.clear()
        self.heatmap.clear()
        self.snapshot_title.setText("관측 시점")
        self.playback_status.setText("일시정지 · 0 / 0")
        self.achievement.setText("등록 SW 목표 달성 —")
        self.progress.setValue(0)
        self.heatmap_title.setText("선택 시점의 설비 Heatmap")

    def _render_frame(self):
        if self.drawer is not None:
            self.drawer.close_panel(restore_focus=False)
        frame = self.frame
        if frame is None:
            return
        self.snapshot_title.setText(f"{frame.day} 관측 스냅샷")
        self.heatmap_title.setText(f"선택 시점({frame.day})의 설비 Heatmap")
        self.playback_status.setText(f"{'재생 중' if self.timer.isActive() else '일시정지'} · {self.index + 1} / {len(self.data.frames)}")
        summary = frame.snapshot.summary
        decided = summary.compliant + summary.below
        value = f"{summary.rate:.1%}" if summary.rate is not None else "계산 불가"
        self.achievement.setText(f"등록 SW 목표 달성 {summary.compliant} / {decided}건 · {value}")
        self.achievement.setToolTip(f"분석 {summary.equipment_count}대 · HPA {summary.hpa_excluded}대 제외 · "
                                    f"판단 불가 {summary.undetermined}건은 달성률 분모 제외")
        self.progress.setValue(round(summary.rate * 1000) if summary.rate is not None else 0)
        self.progress.setAccessibleDescription(value)
        for i, control in enumerate(self.date_buttons):
            control.setChecked(i == self.index)
            control.setStyleSheet(f"QPushButton {{background:{'#e8ebfc' if i == self.index else '#f9fafb'};"
                                 f"color:{PRIMARY if i == self.index else INK};border:1px solid {PRIMARY if i == self.index else LINE};"
                                 "border-radius:10px;padding:0;min-height:38px;max-height:38px;}"
                                 f"QPushButton:focus {{border:2px solid {PRIMARY};min-height:36px;max-height:36px;}}")
        self.heatmap.show_frame(self.data, frame)

    def select_date(self, index):
        if self.data is None or self.dirty or not 0 <= index < len(self.data.frames):
            return False
        self.pause()
        self.index = index
        if self.drawer is not None:
            self.drawer.close_panel(restore_focus=False)
        self._render_frame()
        self._sync_controls()
        return True

    def play(self):
        if self.data is None or self.dirty or self.busy or not self.data.can_replay:
            return False
        if self.index == len(self.data.frames) - 1:
            self.index = 0
        self.timer.start()
        self._render_frame()
        self._sync_controls()
        return True

    def pause(self):
        self.timer.stop()
        if self.data is not None and self.data.frames:
            self.playback_status.setText(f"일시정지 · {self.index + 1} / {len(self.data.frames)}")
        self._sync_controls()

    def restart(self):
        if self.data is None or self.dirty or not self.data.frames:
            return False
        self.index = 0
        self._render_frame()
        return True

    def advance(self):
        if not self.timer.isActive() or self.data is None or self.dirty:
            return
        if self.index < len(self.data.frames) - 1:
            self.index += 1
            self._render_frame()
        if self.index == len(self.data.frames) - 1:
            self.pause()

    def reset_filters(self):
        if self.busy:
            return False
        self._filling = True
        days = self.source.analysis_dates or self.source.dates
        self.start.setText(str(days[0]) if days else "")
        self.end.setText(str(days[-1]) if days else "")
        self.state.set_draft(QuerySelection())
        self._fill_choices()
        self._changed()
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
                                     is_current=lambda: self.epoch == epoch, parent=self.window())
        self.version_dialog = dialog
        def done(result):
            if result == QDialog.DialogCode.Accepted and self.epoch == epoch:
                self.state.set_draft(replace(self.state.draft, versions=dialog.result_filter))
                self._changed()
            self.version_dialog = None
            dialog.deleteLater()
        dialog.finished.connect(done)
        dialog.open()

    def open_equipment(self, equipment_id):
        if self.data is None or self.frame is None or self.dirty:
            return False
        self.pause()
        try:
            detail, rows, evidence = replay_detail(self.data, self.frame, equipment_id)
        except (ValueError, StopIteration):
            return False
        if self.drawer is None:
            self.drawer = EquipmentDrawer(self.window())
        request = EquipmentSelection(self.frame.snapshot.query, self.frame.day, equipment_id, self.frame.day)
        self.drawer.open_detail(request, detail, rows, evidence, self.data.source.source_label,
                                return_focus=self.heatmap.tile_buttons.get(equipment_id), edit_available=False)
        return True

    def invalidate(self, *_):
        self.pause()
        self.read_request.cancel()
        self.epoch += 1
        self.data, self.pending_request, self.dirty = None, None, True
        self._clear_display()
        self.badge.setText("재조회 필요")
        self._notice("저장 자료가 변경되었습니다. 조회 적용으로 다시 확인해 주세요.")
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
        if self.drawer is not None:
            self.drawer.close_panel(restore_focus=False)
        super().hideEvent(event)
