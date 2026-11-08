"""VER5 9·16·56 보고서. 목록형 진입 → 생성 조건 → 미리보기·로컬 저장."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from setron.ui.chat_report_library import ChatReportLibrary
from setron.ui.components import button, label
from setron.ui.dashboard import DashboardGrid
from setron.ui.import_widgets import PeriodDateField
from setron.ui.query_panel import _WrapRow
from setron.ui.query_state import UNASSIGNED, AppliedQuery, QuerySelection, QueryState
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.report_data import FORMATS, REPORT_TYPES, ReportRequest, generate_report, scope_caption
from setron.ui.report_widgets import (
    Choices, GenerationDialog, ReportClock, ReportConfigPanels, ReportPreview, ReportSaveDialog,
    OptionalSection, SelectedReportType, checkbox, field, step_tabs,
)
from setron.ui.theme import PRIMARY, SECONDARY, icon, svg_size
from setron.ui.version_management_widgets import actions, card, combo, note, set_rows, table, text_field


class ReportsPage(QWidget):
    title_changed = Signal(str)

    def __init__(self, source, *, targets_provider=lambda: None, version_loader=None,
                 ai_provider=lambda: None, generation_allowed=lambda: True, settings=None, ai_settings=None):
        super().__init__()
        self.source, self.targets_provider = source, targets_provider
        self.version_loader, self.ai_provider, self.generation_allowed = version_loader, ai_provider, generation_allowed
        self.result, self.last_request, self.catalog = None, None, None
        self.recent = []
        self.generation_dialog = self.save_dialog = None
        self.request = ReadRequest(self)
        self.request.completed.connect(self._generated)
        self.item_request = ReadRequest(self)
        self.item_request.completed.connect(self._items_loaded)
        self.settings = settings if settings is not None else QSettings("SETRON", "Reports")
        self.ai_library = ChatReportLibrary(settings=ai_settings)
        self.ai_library.setParent(self)
        self.ai_library.hide()
        self.saved_entries = self._read_saved()
        self._applied = None
        self._ai_answer = None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        self.stack = QStackedWidget()
        body.addWidget(self.stack)
        self.landing = self._landing()
        self.stack.addWidget(self.landing)
        self.flow = QWidget()
        flow_body = QVBoxLayout(self.flow)
        flow_body.setContentsMargins(0, 0, 0, 0)
        flow_body.setSpacing(18)
        self.tabs = step_tabs()
        self.steps = QStackedWidget()
        self.config = self._config()
        self.preview = self._preview()
        self.steps.addWidget(self.config)
        self.steps.addWidget(self.preview)
        flow_body.addWidget(self.tabs)
        flow_body.addWidget(self.steps)
        self.tabs.currentChanged.connect(self.set_step)
        self.stack.addWidget(self.flow)
        self._refresh_choices()
        self._apply_conditions()
        self._render_library()
        self._kind_changed()
        self.enter()

    @property
    def saving(self):
        return bool(self.save_dialog and self.save_dialog.busy)

    def enter(self):
        """좌측 보고서 클릭은 이전 레이아웃 목록으로 진입. 생성 초안·결과는 유지한다."""
        self._show(self.stack, 0)
        self.title_changed.emit("보고서")
        self._render_library()

    @staticmethod
    def _show(stack, index):
        for i in range(stack.count()):
            stack.widget(i).setSizePolicy(QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred if i == index else QSizePolicy.Policy.Ignored)
        stack.setCurrentIndex(index)
        stack.updateGeometry()

    def _landing(self):
        page = QWidget()
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.clock = ReportClock()
        body.addWidget(self.clock)
        filters = card()
        self.landing_period = combo("기간", (("month", "최근 1개월"), ("quarter", "최근 3개월"), ("all", "전체 관측 기간")))
        self.landing_site = combo("Site", ((None, "전체 Site"), *((o.key, o.label) for o in self.source.catalog.sites)))
        self.landing_group = combo("Group", ((None, "전체"), *((o.key, o.label) for o in self.source.catalog.groups)))
        self.landing_format = combo("형식", tuple((key, title) for key, title, _ in FORMATS))
        def inline(caption, control):
            host = QWidget()
            box = QHBoxLayout(host)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(10)
            heading = label(caption, 13, color=SECONDARY)
            heading.setBuddy(control)
            box.addWidget(heading)
            box.addWidget(control, 1)
            return host
        filters.body.addWidget(_WrapRow([inline("생성 조건 · 기간", self.landing_period), inline("Site", self.landing_site),
                                        inline("Group", self.landing_group), inline("형식", self.landing_format)], 170, fields=True))
        body.addWidget(filters)
        cards = []
        self.create_buttons = {}
        for key, _, title, subtitle, asset in REPORT_TYPES:
            entry = card()
            entry.setMinimumWidth(0)
            entry.setMinimumHeight(230)
            entry.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            picture = label("")
            picture.setFixedSize(svg_size(f"reports-ver5/{asset}"))
            picture.setPixmap(icon(f"reports-ver5/{asset}").pixmap(picture.size()))
            picture.setProperty("assetFile", f"reports-ver5/{asset}")
            circle = QWidget()
            circle.setFixedSize(48, 48)
            circle.setStyleSheet("background:#e8efff;border-radius:24px;")
            centered = QVBoxLayout(circle)
            centered.setContentsMargins(0, 0, 0, 0)
            centered.addWidget(picture, 0, Qt.AlignmentFlag.AlignCenter)
            entry.body.addWidget(circle, 0, Qt.AlignmentFlag.AlignHCenter)
            heading = label(title, 15, medium=True, wrap=True)
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            entry.body.addWidget(heading)
            description = note(subtitle)
            description.setAlignment(Qt.AlignmentFlag.AlignCenter)
            entry.body.addWidget(description)
            entry.body.addStretch()
            create = button("생성")
            create.setAccessibleName(title + " 생성")
            create.clicked.connect(lambda checked=False, kind=key: self.start_flow(kind))
            entry.body.addWidget(create)
            self.create_buttons[key] = create
            cards.append(entry)
        grid = DashboardGrid(cards, minimum_width=165, maximum_columns=5, spacing=14)
        grid.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        body.addWidget(grid)
        recent = card("최근 생성된 보고서")
        recent.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.library_model, self.library_table = table(("보고서명", "유형", "조건", "생성자", "생성일시", "형식", "다운로드"), "최근 생성된 보고서",
                                                      (240, 130, 260, 90, 170, 90, 120))
        self.library_table.doubleClicked.connect(lambda index: self.open_saved(index.row()))
        recent.body.addWidget(self.library_table)
        self.library_note = note()
        recent.body.addWidget(self.library_note)
        self.open_button = button("선택 보고서 다운로드·열기")
        self.open_button.clicked.connect(lambda: self.open_saved(self.library_table.currentIndex().row()))
        # 실제 행별 다운로드 버튼을 사용한다. 키보드는 행 선택 후 Enter로 연다.
        self.open_button.hide()
        self.library_table.activated.connect(lambda index: self.open_saved(index.row()))
        body.addWidget(recent)
        body.addStretch()
        return page

    def _config(self):
        page = QWidget()
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        left = card("보고서 유형")
        self.kind = SelectedReportType(tuple((key, title) for key, title, *_ in REPORT_TYPES))
        left.body.addWidget(self.kind, 0, Qt.AlignmentFlag.AlignLeft)
        left.body.addWidget(label("조회 조건", 16, medium=True))
        self.site, self.fab, self.line, self.group = (combo(name) for name in ("Site", "Fab", "Line", "설비군"))
        dates = self.source.analysis_dates or self.source.dates
        latest = max(dates, default=date.today())
        self.base_date = self._date(latest, "관측 기준일")
        self.start = self._date(min(dates, default=latest), "기간 시작일")
        self.end = self._date(latest, "기간 종료일")
        period = QWidget()
        dates = QHBoxLayout(period)
        dates.setContentsMargins(0, 0, 0, 0)
        dates.setSpacing(6)
        dates.addWidget(self.start)
        dates.addWidget(label("~"))
        dates.addWidget(self.end)
        left.body.addWidget(_WrapRow([field("Site", self.site), field("Fab", self.fab), field("Line", self.line)], 140, fields=True))
        period_field = field("추이·변경 이력 기간", period)
        period_field.setMinimumWidth(280)
        left.body.addWidget(_WrapRow([field("관측 기준일", self.base_date), field("설비군", self.group),
                                     period_field], 160, fields=True))
        left.body.addWidget(note("Fab과 Line은 별개입니다. 원본 구역명으로 추정하지 않습니다."))
        self.apply_button, self.reset_button = button("조건 적용", primary=True), button("조건 초기화")
        self.apply_button.clicked.connect(self._apply_conditions)
        self.reset_button.clicked.connect(self.reset_conditions)
        left.body.addWidget(actions(self.apply_button, self.reset_button))
        self.scope = note()
        self.condition_status = note()
        left.body.addWidget(self.scope)
        left.body.addWidget(self.condition_status)
        left.body.addWidget(note(self.source.source_label))
        self.item = combo("조회할 Item")
        # 전체 Item 원문은 모델·팝업·툴팁에 보존하되, 그 길이를 카드의 최소 폭으로 사용하지 않는다.
        self.item.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.item.setMinimumContentsLength(18)
        self.item.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.item.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self.item.currentTextChanged.connect(self.item.setToolTip)
        self.item_field = field("조회할 Item *", self.item)
        self.item_note = note("Item 적용 여부는 목표 N 달성과 별도로 기록합니다.")
        item_section = QWidget()
        item_body = QVBoxLayout(item_section)
        item_body.setContentsMargins(0, 0, 0, 0)
        item_body.setSpacing(12)
        item_body.addWidget(self.item_field)
        item_body.addWidget(self.item_note)
        self.item_slot = OptionalSection(item_section)
        left.body.addWidget(self.item_slot)
        self.site.currentIndexChanged.connect(self._site_changed)
        self.fab.currentIndexChanged.connect(self._fab_changed)
        for control in (self.site, self.fab, self.line, self.group):
            control.currentIndexChanged.connect(self._dirty)
        for control in (self.base_date, self.start, self.end):
            control.textChanged.connect(self._dirty)
        right = card("출력 설정")
        self.output_body = right.body
        self.format = Choices(tuple((key, title) for key, title, _ in FORMATS))
        self.format.changed.connect(self._format_changed)
        self.format_label = text_field("선택한 형식", "Excel (.xlsx)", readonly=True)
        right.body.addWidget(self.format)
        right.body.addWidget(field("선택한 형식", self.format_label))
        right.body.addWidget(note("요약·조회 조건·분모·미확인/제외 수는 항상 포함"))
        self.options = {key: checkbox(title) for key, title in (("heatmap", "버전 히트맵"), ("distribution", "구성비·막대·트리맵"),
                        ("trend", "날짜별 적용률 추이"), ("evidence", "설비 목록·원문 근거"))}
        for control in self.options.values():
            right.body.addWidget(control)
        self.ai_fields = QWidget()
        ai_body = QVBoxLayout(self.ai_fields)
        ai_body.setContentsMargins(0, 0, 0, 0)
        self.ai_status = note("분석 결과 없음")
        self.ai_status.setFixedHeight(38)
        self.ai_status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        load_ai, example_ai = button("완료한 AI 응답 불러오기"), button("분석 결과 예시 불러오기")
        load_ai.clicked.connect(self.load_ai)
        example_ai.clicked.connect(self.example_ai)
        self.numeric_only = QCheckBox("AI 응답 없이 수치표만 생성")
        ai_body.addWidget(self.ai_status)
        ai_body.addWidget(load_ai)
        ai_body.addWidget(example_ai)
        ai_body.addWidget(self.numeric_only)
        ai_body.addWidget(note("예제 응답은 회사 AI 결과와 구분합니다. 보고서 생성은 AI를 다시 호출하지 않습니다."))
        self.ai_slot = OptionalSection(self.ai_fields)
        right.body.addWidget(self.ai_slot)
        self.filename = text_field("파일명", f"SETRON_보고서_{latest}")
        self.filename_field = field("파일명 · 확장자 자동", self.filename)
        right.body.addWidget(self.filename_field)
        self.generate_button = button("보고서 생성", primary=True)
        self.generate_button.clicked.connect(self.generate)
        right.body.addWidget(self.generate_button)
        self.generation_status = note("보고서를 생성하면 미리보기가 표시됩니다.")
        right.body.addWidget(self.generation_status)
        self.config_panels = ReportConfigPanels(left, right)
        body.addWidget(self.config_panels)
        self.recent_card = card("최근 생성한 보고서")
        self.recent_card.body.addWidget(note("생성 당시 조건·형식·내용으로 다시 열립니다. 최근 2개를 보관합니다."))
        self.recent_buttons = []
        for i in range(2):
            b = button("")
            b.clicked.connect(lambda checked=False, index=i: self.open_recent(index))
            b.hide()
            self.recent_buttons.append(b)
            self.recent_card.body.addWidget(b)
        body.addWidget(self.recent_card)
        self.demo_toggle = button("시연 옵션")
        self.demo_toggle.setCheckable(True)
        self.demo_fields = card()
        self.demo_fields.body.addWidget(note("UI 시연 전용 · 원본 자료와 저장된 N은 바꾸지 않습니다."))
        self.demo = combo("UI 시연 옵션", (("", "사용 안 함"), ("empty", "자료 없음 전환"), ("single", "관측 1장 전환"),
                          ("generation_failure", "생성 실패 전환"), ("save_failure", "저장 실패 전환")))
        self.demo_fields.body.addWidget(self.demo)
        self.demo_fields.hide()
        self.demo_toggle.toggled.connect(self.demo_fields.setVisible)
        body.addWidget(actions(self.demo_toggle))
        body.addWidget(self.demo_fields)
        body.addStretch()
        return page

    def _preview(self):
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(20)
        left = card("생성 결과")
        left.setMaximumWidth(310)
        left.setMinimumWidth(220)
        left.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.result_status = note("보고서를 생성하면 미리보기가 표시됩니다.")
        self.result_filename = text_field("생성된 파일", readonly=True)
        self.result_format = text_field("생성된 형식", readonly=True)
        self.result_scope = note()
        left.body.addWidget(self.result_status)
        left.body.addWidget(field("파일", self.result_filename))
        left.body.addWidget(field("형식", self.result_format))
        left.body.addWidget(self.result_scope)
        self.regenerate_button = button("같은 조건으로 재생성")
        self.retry_button = button("생성 재시도")
        self.save_button = button("파일 저장", primary=True)
        conditions = button("생성 조건으로")
        conditions.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        self.regenerate_button.clicked.connect(self.regenerate)
        self.retry_button.clicked.connect(self.retry)
        self.save_button.clicked.connect(self.open_save)
        left.body.addWidget(self.regenerate_button)
        left.body.addWidget(self.retry_button)
        left.body.addWidget(self.save_button)
        left.body.addWidget(conditions)
        self.file_status = note("저장 전 · 생성한 파일을 로컬에 저장할 수 있습니다.")
        left.body.addWidget(self.file_status)
        right = card()
        right.setMinimumWidth(0)
        right.setMinimumHeight(560)
        self.document = ReportPreview()
        right.body.addWidget(self.document)
        row.addWidget(left, 300, Qt.AlignmentFlag.AlignTop)
        row.addWidget(right, 1000)
        self._preview_actions(False)
        return page

    @staticmethod
    def _date(day, caption):
        control = PeriodDateField(str(day))
        control.setFixedHeight(38)
        control.setAccessibleName(caption)
        return control

    @staticmethod
    def _fill(control, entries, selected=None):
        control.blockSignals(True)
        control.clear()
        for key, caption in entries:
            control.addItem(caption, key)
        index = control.findData(selected)
        control.setCurrentIndex(max(0, index))
        control.blockSignals(False)

    def _refresh_choices(self):
        catalog = self.source.catalog
        self._fill(self.site, ((None, "전체 Site"), *((o.key, o.label) for o in catalog.sites)), self.site.currentData())
        self._fill(self.group, ((None, "설비군 전체"), *((o.key, o.label) for o in catalog.groups)), self.group.currentData())
        self._site_changed()

    def _site_changed(self):
        options = QueryState(self.source.catalog).available_fabs(QuerySelection(site_key=self.site.currentData()))
        self._fill(self.fab, ((None, "Fab 전체"), (UNASSIGNED, "Fab 미지정"), *((o.key, o.label) for o in options)), self.fab.currentData())
        self._fab_changed()

    def _fab_changed(self):
        options = QueryState(self.source.catalog).available_lines(QuerySelection(site_key=self.site.currentData(), fab_key=self.fab.currentData()))
        self._fill(self.line, ((None, "Line 전체"), (UNASSIGNED, "Line 미지정"), *((o.key, o.label) for o in options)), self.line.currentData())

    def _selection(self):
        day = date.fromisoformat(self.base_date.text())
        return QuerySelection(site_key=self.site.currentData(), fab_key=self.fab.currentData(),
                              line_key=self.line.currentData(), group_key=self.group.currentData(), as_of=day)

    def _dirty(self):
        if hasattr(self, "condition_status"):
            self.condition_status.setText("조건 변경됨 · 조건 적용 후 생성해 주세요.")

    def _apply_conditions(self):
        try:
            selection = self._selection()
            start, end = date.fromisoformat(self.start.text()), date.fromisoformat(self.end.text())
            if start > end:
                raise ValueError("시작일이 종료일보다 늦습니다.")
            QueryState(self.source.catalog).validate(selection)
            self._applied = (selection, start, end)
            self.scope.setText(scope_caption(self.source.catalog, selection))
            self.condition_status.setText("조건 적용됨")
            return True
        except ValueError as error:
            self.condition_status.setText("날짜·조건 확인 필요 · " + str(error))
            return False

    def reset_conditions(self):
        self.site.setCurrentIndex(0)
        self.fab.setCurrentIndex(0)
        self.line.setCurrentIndex(0)
        self.group.setCurrentIndex(0)
        dates = self.source.analysis_dates or self.source.dates
        latest = max(dates, default=date.today())
        self.base_date.setText(str(latest))
        self.start.setText(str(min(dates, default=latest)))
        self.end.setText(str(latest))
        self._apply_conditions()

    def start_flow(self, kind):
        self._refresh_choices()
        self.kind.set_value(kind)
        self._kind_changed()
        self.site.setCurrentIndex(max(0, self.site.findData(self.landing_site.currentData())))
        self.group.setCurrentIndex(max(0, self.group.findData(self.landing_group.currentData())))
        self.fab.setCurrentIndex(0)
        self.line.setCurrentIndex(0)
        self.format.set_value(self.landing_format.currentData())
        self._format_changed(self.format.value())
        dates = self.source.analysis_dates or self.source.dates
        latest = max(dates, default=date.today())
        period = self.landing_period.currentData()
        self.base_date.setText(str(latest))
        self.end.setText(str(latest))
        self.start.setText(str(min(dates, default=latest) if period == "all" else latest - timedelta(days=30 if period == "month" else 90)))
        self._apply_conditions()
        self._show(self.stack, 1)
        self.tabs.setCurrentIndex(0)
        self.set_step(0)
        if self.catalog is None and self.version_loader and not self.item_request.busy:
            self.item_request.start(BackgroundRead(self.version_loader))

    def set_step(self, index):
        self._show(self.steps, index)
        self.title_changed.emit("보고서" if index == 0 else "보고서 미리보기")

    def _kind_changed(self, *_):
        item = self.kind.value() == "item"
        self.item_slot.set_active(item)
        ai = self.kind.value() == "ai"
        self.ai_slot.set_active(ai)

    def _format_changed(self, value):
        self.format_label.setText(next(full for key, _, full in FORMATS if key == value))

    def _items_loaded(self, result, error):
        if error is not None:
            self.item_note.setText("Item 목록을 읽지 못했습니다. 보고서 메뉴에서 다시 진입해 주세요.")
            return
        self.catalog = result
        self._fill(self.item, ((None, "Item 선택"), *((e.item.item_id, f"Item {e.item.item_no or e.item.item_id} · {e.item.content}")
                                                   for e in result.items)), self.item.currentData())
        for i in range(self.item.count()):
            self.item.setItemData(i, self.item.itemText(i), Qt.ItemDataRole.ToolTipRole)
        self.item.setToolTip(self.item.currentText())

    def load_ai(self):
        self._ai_answer = deepcopy(self.ai_provider())
        self.ai_status.setText("완료한 AI 응답 불러옴 · " + self._ai_answer.request.question if self._ai_answer else "분석 결과 없음 · AI 분석에서 질문을 완료해 주세요.")
        self.ai_status.setToolTip(self.ai_status.text())
        if self.demo.currentData() == "ai":
            self.demo.setCurrentIndex(0)

    def example_ai(self):
        self._ai_answer = None
        if self.demo.findData("ai") < 0:
            self.demo.addItem("AI 응답 예제 · UI 시연", "ai")
        self.demo.setCurrentIndex(self.demo.findData("ai"))
        self.ai_status.setText("UI 시연 예제 선택됨 · 회사 AI 결과가 아닙니다.")
        self.ai_status.setToolTip(self.ai_status.text())

    def generate(self):
        if self.request.busy or self.saving:
            return
        try:
            selection = self._selection()
            current = (selection, date.fromisoformat(self.start.text()), date.fromisoformat(self.end.text()))
            if current != self._applied:
                raise ValueError("변경한 조회 조건을 먼저 적용해 주세요.")
            request = ReportRequest(self.kind.value(), AppliedQuery(selection, 1, 0, self.scope.text(),
                deepcopy(self.targets_provider())), selection.as_of, current[1], current[2], self.format.value(), self.filename.text().strip(),
                **{key: control.isChecked() for key, control in self.options.items()}, item_id=self.item.currentData(),
                ai_answer=deepcopy(self._ai_answer), numeric_only=self.numeric_only.isChecked(), demo=self.demo.currentData())
        except ValueError as error:
            self.generation_status.setText(str(error))
            return
        self._generate(request)

    def _generate(self, request):
        if self.request.busy or self.saving:
            return
        if not self.generation_allowed():
            self.generation_status.setText("다른 화면의 저장·조회가 완료된 뒤 생성해 주세요.")
            return
        self.last_request = request
        self.generate_button.setEnabled(False)
        self._preview_actions(False)
        self.generation_status.setText("보고서 생성 중")
        dialog = self.generation_dialog = GenerationDialog(self.window())
        dialog.cancelled.connect(self.cancel_generation)
        dialog.open()
        source, loader = self.source, self.version_loader
        def load():
            versions = loader() if request.kind == "item" and loader else None
            return generate_report(source, request, versions)
        self.request.start(BackgroundRead(load))

    def cancel_generation(self):
        self.request.cancel()
        self._close_generation()
        self.generate_button.setEnabled(True)
        self._preview_actions(self.result is not None)
        self.generation_status.setText("생성을 취소했습니다. 기존 결과는 유지했습니다.")

    def _close_generation(self):
        dialog, self.generation_dialog = self.generation_dialog, None
        if dialog is not None:
            dialog.accept()
            dialog.deleteLater()

    def _generated(self, result, error):
        self._close_generation()
        self.generate_button.setEnabled(True)
        self._show(self.stack, 1)
        self.tabs.setCurrentIndex(1)
        self.set_step(1)
        if error is not None:
            self.result_status.setText("생성 실패 · " + str(error) + ("\n이전 성공 결과를 유지했습니다." if self.result else ""))
            self.generation_status.setText("생성 실패 · " + str(error))
            self._preview_actions(self.result is not None, failed=True)
            return
        self.result = result
        self.recent = [result, *self.recent][:2]
        self._render_result(result)
        self._render_recent()
        self._render_library()

    def _preview_actions(self, completed, failed=False):
        self.save_button.setEnabled(completed)
        self.regenerate_button.setEnabled(self.last_request is not None or completed)
        self.retry_button.setVisible(failed)

    def _render_result(self, report):
        self.document.set_report(report)
        self.result_filename.setText(report.filename)
        self.result_format.setText(next(full for key, _, full in FORMATS if key == report.request.format))
        self.result_scope.setText(f"{report.scope}\n관측 기준일 {report.request.base_date}\n기간 {report.request.start} ~ {report.request.end}")
        self.result_status.setText("생성 완료" + (" · UI 시연" if report.request.demo else ""))
        self.generation_status.setText("생성 완료 · 미리보기에서 내용을 확인하세요.")
        self.file_status.setText("저장 전 · 생성 당시 조건·내용으로 저장합니다.")
        self._preview_actions(True)

    def regenerate(self):
        request = self.result.request if self.result else self.last_request
        if request:
            self._generate(request)

    def retry(self):
        if self.last_request:
            self._generate(replace(self.last_request, demo="" if self.last_request.demo == "generation_failure" else self.last_request.demo))

    def open_recent(self, index):
        if index < len(self.recent) and not self.request.busy and not self.saving:
            self.result = self.recent[index]
            self.last_request = self.result.request
            self._render_result(self.result)
            self._show(self.stack, 1)
            self.tabs.setCurrentIndex(1)
            self.set_step(1)

    def _render_recent(self):
        for i, b in enumerate(self.recent_buttons):
            b.setVisible(i < len(self.recent))
            if i < len(self.recent):
                report = self.recent[i]
                b.setText(f"{report.filename} · {report.created:%H:%M:%S} · 다시 열기")
                b.setToolTip(report.scope)

    def open_save(self):
        if self.result is None or self.request.busy or self.saving:
            return
        # 저장 실패 시연은 UI에서 끈 뒤 같은 결과를 유지한 채 저장을 재시도한다.
        result = self.result
        if result.request.demo == "save_failure" and self.demo.currentData() != "save_failure":
            result = replace(result, request=replace(result.request, demo=""))
        dialog = self.save_dialog = ReportSaveDialog(result, self.document.pictures(), self.window())
        dialog.saved.connect(lambda path, report=result: self._saved(path, report))
        def finished(*_):
            if self.save_dialog is dialog:
                self.save_dialog = None
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()

    def _saved(self, path, report):
        self.file_status.setText("저장 완료 · " + str(path))
        entry = {"path": str(path), "created": report.created.strftime("%Y-%m-%d %H:%M:%S"),
                 "kind": report.title, "scope": report.scope, "generation": report.created.isoformat()}
        self.saved_entries = [entry, *(e for e in self.saved_entries if e["path"] != str(path))]
        self.settings.setValue("files", json.dumps(self.saved_entries, ensure_ascii=False))
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            self.file_status.setText("파일은 저장됐지만 최근 보고서 목록 보관에 실패했습니다. " + str(path))
        self._render_library()

    def add(self, paths):
        """기존 AI 분석의 저장 신호는 원래 로컬 파일 목록과 함께 보존한다."""
        self.ai_library.add(paths)
        self._render_library()

    def _read_saved(self):
        try:
            values = json.loads(self.settings.value("files", "[]"))
            return [e for e in values if isinstance(e, dict) and isinstance(e.get("path"), str)
                    and isinstance(e.get("created"), str) and Path(e["path"]).suffix.lower() in {".xlsx", ".docx", ".pdf"}] if isinstance(values, list) else []
        except (ValueError, TypeError):
            return []

    def _render_library(self):
        known = {e["path"]: e for e in (*self.ai_library.entries, *self.saved_entries)}
        saved_generations = {e.get("generation") for e in self.saved_entries}
        pending = ({"report": r, "created": r.created.strftime("%Y-%m-%d %H:%M:%S"), "kind": r.title, "scope": r.scope}
                   for r in self.recent if r.created.isoformat() not in saved_generations)
        self.library_entries = sorted((*known.values(), *pending), key=lambda e: e["created"], reverse=True)
        rows = []
        for entry in self.library_entries:
            pending_report = entry.get("report")
            path = Path(entry["path"]) if not pending_report else None
            rows.append((pending_report.filename if pending_report else path.name, entry.get("kind", "AI 분석"),
                         entry.get("scope", "파일에서 확인"), "로컬 사용자", entry["created"],
                         pending_report.request.format.upper() if pending_report else path.suffix[1:].upper(),
                         "미리보기·저장" if pending_report else "다운로드·열기" if path.is_file() else "파일 없음"))
        set_rows(self.library_model, self.library_table, rows)
        for index, values in enumerate(rows):
            action = button(values[6])
            action.setAccessibleName(values[0] + " " + values[6])
            action.setStyleSheet(f"QPushButton {{border:0;background:transparent;color:{PRIMARY};padding:0 6px;}}"
                                f"QPushButton:focus {{border:1px solid {PRIMARY};border-radius:4px;}}")
            action.clicked.connect(lambda checked=False, row=index: self.open_saved(row))
            self.library_table.setIndexWidget(self.library_model.index(index, 6), action)
        self.library_note.setText("최근 생성된 보고서가 없습니다." if not rows else "이 PC에서 저장한 파일입니다. 파일 이동·삭제 시 열 수 없습니다.")
        self.open_button.setEnabled(bool(rows))

    def open_saved(self, row):
        if not 0 <= row < len(self.library_entries):
            self.library_note.setText("보고서를 먼저 선택해 주세요.")
            return False
        report = self.library_entries[row].get("report")
        if report is not None:
            self.open_recent(self.recent.index(report))
            return True
        path = Path(self.library_entries[row]["path"])
        if not path.is_file() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.library_note.setText("파일을 열지 못했습니다. 저장 위치를 확인해 주세요.")
            return False
        return True
