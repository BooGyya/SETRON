"""6번: 한 목록·한 상세 화면. 조회/저장/정정/이력 기능을 새로 시작하지 않는다."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLayout, QLineEdit,
    QTableView, QVBoxLayout, QWidget, QSizePolicy,
)

from setron.features.aggregation.service import summarize
from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import CompareStatus
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.dashboard import MetricCard
from setron.ui.dashboard_data import count_value, family_name, missing_sw_count, model_name, upgrade_caption
from setron.ui.equipment_data import equipment_data, filtered_tiles, validate_scope
from setron.ui.heatmap_data import EquipmentSelection, GoalState, compare_tone
from setron.ui.target_data import target_map
from setron.ui.item_data import BASIS_LABELS, item_evidence_rows, selected_application
from setron.features.item.schemas import APPLICATION_LABELS
from setron.ui.theme import ACCENT_SOFT, INK, LINE, PRIMARY, SECONDARY, TONES, font, icon, svg_size



# 색은 히트맵과 같은 규칙(heatmap_data.compare_tone/version_tone): 초록 N 이상 · 노랑 N-1/확인 필요 ·
# 빨강 N-2 이하(임시 기준) · 회색 분석 제외. 아래는 판정 요약 글자색만 정한다.
TONE_INK = {"success": "#246b4a", "warning": "#b88000", "danger": "#c23345", "neutral": "#5e6b7a"}


def icon_label(asset):
    """원본 SVG를 루트 크기 그대로 표시한다."""
    result = QLabel()
    result.setPixmap(icon(asset).pixmap(svg_size(asset)))
    result.setFixedSize(svg_size(asset))
    result.setProperty("assetFile", asset)
    return result


def ignore_width(*widgets):
    """긴 원문 라벨이 본문 최소 폭을 늘려 가로 스크롤을 만들지 않게 한다."""
    for widget in widgets:
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        widget.setMinimumWidth(0)


class FlowGrid(QWidget):
    """폭에 맞춰 열 수를 바꾸는 카드 묶음. 표시 배치만 담당한다."""

    def __init__(self, min_width, *, spacing=8, max_columns=12, fill=True):
        super().__init__()
        self.min_width, self.max_columns, self.fill = min_width, max_columns, fill
        self.items, self.columns = [], 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(spacing)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def set_widgets(self, widgets):
        for widget in self.items:
            self.grid.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self.items, self.columns = list(widgets), 0
        self._arrange(self._fit(self.width()))

    def _fit(self, width):
        limit = min(self.max_columns, len(self.items)) if self.fill else self.max_columns
        return max(1, min(max(1, limit), (width + self.grid.horizontalSpacing()) // (self.min_width + self.grid.horizontalSpacing())))

    def minimumSizeHint(self):
        return QSize(0, self.grid.minimumSize().height())

    def _arrange(self, columns):
        if columns == self.columns:
            return
        for widget in self.items:
            self.grid.removeWidget(widget)
        for index in range(max(columns, self.columns, 1)):
            self.grid.setColumnStretch(index, int(index < columns))
        for index, widget in enumerate(self.items):
            self.grid.addWidget(widget, index // columns, index % columns)
            widget.show()
        self.columns = columns
        self.updateGeometry()

    def resizeEvent(self, event):
        self._arrange(self._fit(event.size().width()))
        super().resizeEvent(event)


def priority_text(entry, thresholds):
    """설비 상세의 업그레이드 우선순위(BE-017)·버전 경과(BE-016) 줄. 순위와 노후도는 별개로 보인다."""
    if entry is None:
        return "업그레이드 우선순위: 분석 제외"
    rank = f"{entry.rank}위" if entry.rank else "확인 필요 (" + " · ".join(entry.messages) + ")"
    oldest = entry.representative
    if thresholds is None:
        age = "기간 기준 설정 오류 · 관리 화면에서 확인"
    elif oldest is None or oldest.age.elapsed_label is None:
        age = "출시일 미확인"
    else:
        age = (f"{oldest.pm.pm_name or '공통 SW'} {oldest.pm.version_raw} · {oldest.age.elapsed_label} · {oldest.age.label}"
               f" (기준: {thresholds.description})")
    steps = f" · 목표 N보다 {entry.steps_behind}단계 낮음" if entry.steps_behind else ""
    return f"업그레이드 우선순위: {rank}{steps}\n가장 오래된 버전 경과: {age}"

class ReadTableModel(QAbstractTableModel):
    """원문은 Qt DisplayRole의 일반 텍스트로만 표시한다. 호기 ID는 행 데이터와 함께 보관한다."""

    def __init__(self, headings, *, numeric_columns=()):
        super().__init__()
        self.headings, self.rows, self.ids, self.tones = headings, (), (), ()
        self.numeric_columns = numeric_columns

    def set_rows(self, rows, ids=(), tones=()):
        self.beginResetModel()
        self.rows, self.ids, self.tones = tuple(rows), tuple(ids), tuple(tones)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headings)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headings[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        text = self.rows[index.row()][index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole):
            return text
        if role == Qt.ItemDataRole.UserRole and self.ids:
            return self.ids[index.row()]
        if role == Qt.ItemDataRole.FontRole:
            return font(14 if self.ids and index.column() == 0 else 13,
                        medium=bool(self.ids) and index.column() == 0, numeric=index.column() in self.numeric_columns)
        if role == Qt.ItemDataRole.ForegroundRole and self.tones and index.column() == 4:
            return QColor(TONES[self.tones[index.row()]][1])
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        return None


class ReadTable(QTableView):
    def __init__(self, widths):
        super().__init__()
        self.base_widths = widths

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.viewport().width()
        ratio = width / sum(self.base_widths) if width >= 900 else 1
        for index, original in enumerate(self.base_widths):
            self.setColumnWidth(index, int(original * ratio))
        if self.model() is not None:
            self.resizeRowsToContents()


def read_table(model, name, widths):
    table = ReadTable(widths)
    table.setModel(model)
    table.setAccessibleName(name)
    table.setMinimumWidth(0)
    table.setMinimumHeight(260)
    table.setFont(font(13))
    table.setWordWrap(True)
    table.setTextElideMode(Qt.TextElideMode.ElideNone)
    table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
    table.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
    table.verticalHeader().hide()
    table.verticalHeader().setMinimumSectionSize(56)
    table.horizontalHeader().setMinimumHeight(40)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    for index, width in enumerate(widths):
        table.setColumnWidth(index, width)
    table.setStyleSheet("QTableView {background:white;border:0;gridline-color:#e9eef6;selection-background-color:#edf0fd;selection-color:#18243c;}"
                        "QHeaderView::section {background:#f7f9fc;color:#5b6880;border:0;padding:8px;text-align:left;}"
                        "QTableView::item {padding:8px;border-bottom:1px solid #e9eef6;}"
                        f"QTableView:focus {{border:1px solid {PRIMARY};}}")
    table.setShowGrid(False)
    return table


def fit_rows(table):
    table.resizeRowsToContents()
    height = sum(table.rowHeight(i) for i in range(table.model().rowCount()))
    table.setFixedHeight(max(260, min(800, height + table.horizontalHeader().height() + 28)))


class EquipmentPage(QWidget):
    mode_changed = Signal(str)
    back_requested = Signal(str)

    def __init__(self, state, source):
        super().__init__()
        self.state, self.source = state, source
        self.snapshot, self.data = None, None
        self.scope_ids, self.visible_tiles = (), ()
        self.scope_label, self.origin, self.detail_back = "적용 범위 전체", "heatmap", "list"
        self.page_index, self.page_size, self.selected_id = 0, 12, None
        self.mode = "list"
        self.item_request, self._item_restore = None, None
        self._wide = None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)
        self.result_status = StatusBadge("조회 적용 전 · 예시 데이터", "info")
        body.addWidget(self.result_status)
        self.context = label("공통 조건에서 조회 적용을 눌러 주세요.", 13, color=SECONDARY, wrap=True)
        body.addWidget(self.context)
        self.list_view = QWidget()
        self.primary = QGridLayout(self.list_view)
        self.primary.setContentsMargins(0, 0, 0, 0)
        self.primary.setSpacing(24)
        self.list_card = Card()
        self.list_card.body.addWidget(label("설비 전체 목록", 20, medium=True))
        toolbar = QHBoxLayout()
        self.all_button = button("범위 전체")
        self.all_button.clicked.connect(lambda: self.risk_button.setChecked(False))
        self.risk_button = button("후보만")
        self.risk_button.setCheckable(True)
        self.risk_button.setProperty("dashboardRisk", True)
        self.risk_button.setStyleSheet("QPushButton:checked {border:2px solid #c9485e;}")
        self.risk_button.toggled.connect(self._filter_changed)
        toolbar.addWidget(self.all_button)
        toolbar.addWidget(self.risk_button)
        toolbar.addStretch()
        self.global_button = button("적용 범위 전체로")
        self.global_button.clicked.connect(lambda: self.open_all(self.origin))
        toolbar.addWidget(self.global_button)
        self.list_card.body.addLayout(toolbar)
        self.search = QLineEdit()
        self.search.setAccessibleName("설비 목록 검색")
        self.search.setPlaceholderText("설비명·위치·모델·버전 원문 검색")
        self.search.setStyleSheet("QLineEdit {background:#f7f9fc;border:1px solid #e5eaf3;border-radius:10px;padding:10px;}")
        self.search.textChanged.connect(self._filter_changed)
        self.goal_filter = QComboBox()
        self.goal_filter.setAccessibleName("목표 상태 필터")
        self.goal_filter.setProperty("queryField", True)
        self.goal_filter.setFixedHeight(38)
        self.goal_filter.addItem("목표 상태 전체", None)
        for goal in GoalState:
            self.goal_filter.addItem(goal.value, goal)
        self.goal_filter.currentIndexChanged.connect(self._filter_changed)
        self.sort = QComboBox()
        self.sort.setAccessibleName("설비 목록 정렬")
        self.sort.setProperty("queryField", True)
        self.sort.setFixedHeight(38)
        for caption, value in (("설비명 ↑", "code"), ("설비명 ↓", "code-desc"), ("Site · 설비명", "site"),
                               ("업그레이드 우선순위", "priority")):
            self.sort.addItem(caption, value)
        self.sort.currentIndexChanged.connect(self._filter_changed)
        self.filters = QGridLayout()
        self.filters.addWidget(self.search, 0, 0, 1, 2)
        self.filters.addWidget(self.goal_filter, 1, 0)
        self.filters.addWidget(self.sort, 1, 1)
        self.filters.setColumnStretch(0, 1)
        self.filters.setColumnStretch(1, 1)
        self.list_card.body.addLayout(self.filters)
        self.count = label("조회 적용 전", 13, color=SECONDARY, wrap=True)
        self.list_card.body.addWidget(self.count)
        self.model = ReadTableModel(("설비명", "Site / 원본 구역\nFab · Line", "모델 · 계열", "설치 버전", "선택 SW N 판정", "설비 위험 판정 / 근거"), numeric_columns=(3,))
        self.table = read_table(self.model, "선택 범위 설비 목록", (110, 164, 170, 216, 140, 220))
        self.table.activated.connect(lambda index: self.open_row(index.row()))
        self.table.clicked.connect(lambda index: self.open_row(index.row()))
        self.list_card.body.addWidget(self.table)
        self.list_card.body.addWidget(label("좁은 창에서는 표를 좌우로 스크롤할 수 있습니다. 행 선택 또는 Enter → 설비 상세", 12, color=SECONDARY, wrap=True))
        self.message = label("조회 적용 전입니다.", color=SECONDARY, wrap=True)
        self.list_card.body.addWidget(self.message)
        paging = QHBoxLayout()
        self.previous = button("이전")
        self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next = button("다음")
        self.next.clicked.connect(lambda: self.change_page(1))
        self.page_caption = label("0 / 0", 13, color=SECONDARY)
        paging.addWidget(self.previous)
        paging.addWidget(self.page_caption)
        paging.addWidget(self.next)
        paging.addStretch()
        paging.addWidget(label("12대씩 · 물리 설비 기준", 12, color=SECONDARY))
        self.list_card.body.addLayout(paging)
        self.return_button = button("이전 조회로 돌아가기")
        self.return_button.clicked.connect(lambda: self.back_requested.emit(self.origin))
        self.list_card.body.addWidget(self.return_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.summary = QWidget()
        summary_body = QVBoxLayout(self.summary)
        summary_body.setContentsMargins(0, 0, 0, 0)
        summary_body.setSpacing(12)
        summary_body.addWidget(label("목록 전달 범위 전체 요약", 13, color=SECONDARY, wrap=True))
        self.metrics = {}
        for key, title, asset, tone in (("risk", "안전·결함 후보", "60dbd.svg", "risk"),
                                       ("rate", "선택 SW 목표 달성률", "bbc12.svg", "achievement"),
                                       ("total", "물리 설비", "32ac4.svg", "neutral"),
                                       ("upgrade", "업그레이드 필요", "e53b6.svg", "neutral")):
            card = MetricCard(title, asset, tone=tone)
            summary_body.addWidget(card)
            self.metrics[key] = card
        summary_body.addStretch()
        body.addWidget(self.list_view)
        self._build_detail()
        actions = QHBoxLayout()
        self.back_button = button("이전 조회로 돌아가기")
        self.back_button.clicked.connect(self._back)
        self.list_button = button("적용 범위 전체 목록")
        self.list_button.clicked.connect(lambda: self.open_all(self.origin))
        self.history_button = button("선택 설비 관측 이력")
        self.history_button.setEnabled(False)
        self.history_button.setToolTip("관측 이력 화면은 후속 작업입니다.")
        actions.setSpacing(10)
        for control in (self.back_button, self.list_button, self.history_button):
            control.setMinimumWidth(0)
            control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            control.setFixedHeight(40)
            actions.addWidget(control, 1)
        self.detail_card.body.addLayout(actions)
        body.addWidget(self.detail_card)
        self._reflow(False)
        self._set_mode("list")
        self._render_list()

    # ----- 설비 상세 (VER4 사이드 패널 구성: 헤더 · 위치 띠 · 판정 요약 · 관측된 SW · 판정·원본 근거 · 동작) -----

    def _build_detail(self):
        self.detail_card = Card()
        self.detail_card.body.setSpacing(14)
        heading = QHBoxLayout()
        heading.setSpacing(10)
        self.detail_title = label("설비 상세", 24, medium=True, wrap=True)
        heading.addWidget(self.detail_title, 1)
        self.detail_goal_badge = StatusBadge("판정 전", "neutral")
        self.detail_risk_badge = StatusBadge("위험 판정 전", "neutral")
        heading.addWidget(self.detail_goal_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        heading.addWidget(self.detail_risk_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self.detail_card.body.addLayout(heading)
        self.detail_location = label("", 12, color=SECONDARY, wrap=True)
        self.detail_card.body.addWidget(self.detail_location)
        # 위치 띠: Site · Fab · Line · 원본 구역 · 실제 관측일
        self.meta_strip = QFrame()
        self.meta_strip.setObjectName("detailMeta")
        self.meta_strip.setStyleSheet("QFrame#detailMeta {background:white;border:1px solid #dee0e5;border-radius:12px;}")
        strip_body = QVBoxLayout(self.meta_strip)
        strip_body.setContentsMargins(12, 12, 12, 12)
        self.meta_values = {}
        cells = []
        for key, asset, title in (("site", "88d90.svg", "Site"), ("fab", "d865c.svg", "Fab"), ("line", "f50ce.svg", "Line"),
                                  ("area", "c9350.svg", "원본 구역"), ("date", "962ae.svg", "실제 관측일")):
            cell, value = self._field(asset, title, 6)
            self.meta_values[key] = value
            cells.append(cell)
        meta_grid = FlowGrid(120, spacing=12)
        meta_grid.set_widgets(cells)
        strip_body.addWidget(meta_grid)
        self.detail_card.body.addWidget(self.meta_strip)
        # 관측 범위 안내: 물리 설비 1대 · SW n건 · 판정 · 우선순위 원문
        notice = QFrame()
        notice.setObjectName("detailNotice")
        notice.setStyleSheet(f"QFrame#detailNotice {{background:{ACCENT_SOFT};border-radius:10px;}}")
        notice_body = QVBoxLayout(notice)
        notice_body.setContentsMargins(14, 12, 14, 12)
        self.detail_summary = label("", 13, wrap=True)
        notice_body.addWidget(self.detail_summary)
        self.detail_card.body.addWidget(notice)
        self.detail_card.body.addWidget(label("판정 요약", 16, medium=True))
        self.judgement = {}
        cards = []
        for key, asset, title in (("goal", "ef142.svg", "등록 SW 판정"), ("risk", "ac064.svg", "위험 판정"),
                                  ("complete", "d5571.svg", "목록 완전성")):
            card = QFrame()
            card.setObjectName("judgementCard")
            card.setStyleSheet("QFrame#judgementCard {background:white;border:1px solid #dee0e5;border-radius:12px;}")
            card_body = QVBoxLayout(card)
            card_body.setContentsMargins(12, 14, 12, 14)
            card_body.setSpacing(6)
            head = QHBoxLayout()
            head.setSpacing(6)
            head.addWidget(icon_label(asset))
            head.addWidget(label(title, 12, medium=True, color=SECONDARY))
            head.addStretch()
            card_body.addLayout(head)
            value = label("—", 18, medium=True, wrap=True)
            caption = label("", 11, color=SECONDARY, wrap=True)
            ignore_width(value, caption)
            card_body.addWidget(value)
            card_body.addWidget(caption)
            card_body.addStretch()
            self.judgement[key] = (value, caption)
            cards.append(card)
        judgement_grid = FlowGrid(170, spacing=8)
        judgement_grid.set_widgets(cards)
        self.detail_card.body.addWidget(judgement_grid)
        sw_heading = QHBoxLayout()
        sw_heading.setSpacing(12)
        self.sw_title = label("관측된 SW", 16, medium=True)
        sw_heading.addWidget(self.sw_title)
        sw_heading.addStretch()
        for color, text in (("#c23345", "목표 미달"), ("#208457", "목표 달성"), ("#b88000", "확인 필요")):
            dot = QFrame()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background:{color};border-radius:4px;")
            sw_heading.addWidget(dot)
            sw_heading.addWidget(label(text, 11, color=SECONDARY))
        self.detail_card.body.addLayout(sw_heading)
        self.sw_cards = FlowGrid(380, spacing=12)
        self.detail_card.body.addWidget(self.sw_cards)
        self.scope_note = label("", 12, color=SECONDARY, wrap=True)
        self.detail_card.body.addWidget(self.scope_note)
        # 판정 · 원본 근거
        evidence_card = QFrame()
        evidence_card.setObjectName("evidenceCard")
        evidence_card.setStyleSheet("QFrame#evidenceCard {background:white;border:1px solid #dee0e5;border-radius:12px;}")
        evidence_body = QVBoxLayout(evidence_card)
        evidence_body.setContentsMargins(16, 14, 16, 14)
        evidence_body.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(icon_label("02346.svg"))
        head.addWidget(label("판정 · 원본 근거", 15, medium=True))
        head.addStretch()
        evidence_body.addLayout(head)
        self.evidence_rows = QVBoxLayout()
        self.evidence_rows.setSpacing(8)
        evidence_body.addLayout(self.evidence_rows)
        self.evidence = label("", 12, color=SECONDARY, wrap=True)
        evidence_body.addWidget(self.evidence)
        self.detail_card.body.addWidget(evidence_card)
        self.table_title = label("SW 관측 원문 표 · 표시 문자열 그대로", 13, medium=True, color=SECONDARY)
        self.detail_card.body.addWidget(self.table_title)
        self.detail_model = ReadTableModel(("모듈 / SW 관측", "설비군 · 계열", "설치 버전 원문", "목표 N", "N 비교", "비교 근거"), numeric_columns=(2, 3))
        self.detail_table = read_table(self.detail_model, "선택 설비 전체 SW 관측", (165, 190, 230, 200, 130, 255))
        self.detail_card.body.addWidget(self.detail_table)
        for widget in (self.detail_title, self.detail_location, self.detail_summary, self.scope_note, self.evidence, self.context):
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.item_evidence_model = ReadTableModel(("근거 대상", "확인된 내용", "출처 / 저장 상태", "원본 위치 / 모델 범위"))
        self.item_evidence_table = read_table(self.item_evidence_model, "선택 Item 근거와 원본 출처", (180, 330, 340, 300))
        self.item_evidence_table.hide()
        self.detail_card.body.addWidget(self.item_evidence_table)
        note = label("모듈 수·필수 SW 목록은 미확정입니다. PM 관측 개수나 목표 달성만으로 설비 전체 구성이 완전하다고 단정하지 않습니다.",
                     12, color=SECONDARY, wrap=True)
        ignore_width(note)
        self.detail_card.body.addWidget(note)

    @staticmethod
    def _field(asset, title, spacing=8):
        cell = QWidget()
        row = QHBoxLayout(cell)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(spacing)
        row.addWidget(icon_label(asset), 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label(title, 11, color=SECONDARY))
        value = label("—", 13, medium=True, wrap=True)
        ignore_width(value)
        text.addWidget(value)
        row.addLayout(text, 1)
        return cell, value

    def _set_meta(self, values):
        for key, value in values.items():
            known = value not in (None, "")
            self.meta_values[key].setText(str(value) if known else ("미지정" if key in ("fab", "line") else "미확인"))
            self.meta_values[key].setStyleSheet(f"color:{INK if known else SECONDARY};")

    def _set_judgement(self, values):
        for key, (text, caption, color) in values.items():
            value_label, caption_label = self.judgement[key]
            value_label.setText(text)
            value_label.setStyleSheet(f"color:{color};")
            caption_label.setText(caption)

    def _set_evidence_rows(self, rows):
        self.evidence_values = tuple(rows)
        while self.evidence_rows.count():
            widget = self.evidence_rows.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        for asset, title, value in self.evidence_values:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(14)
            tag = QFrame()
            tag.setObjectName("evidenceLabel")
            tag.setStyleSheet("QFrame#evidenceLabel {background:#f4f5f8;border-radius:6px;}")
            tag.setFixedWidth(190)
            tag_row = QHBoxLayout(tag)
            tag_row.setContentsMargins(10, 6, 10, 6)
            tag_row.setSpacing(8)
            tag_row.addWidget(icon_label(asset))
            tag_text = label(title, 12, medium=True, wrap=True)
            ignore_width(tag_text)
            tag_row.addWidget(tag_text, 1)
            line.addWidget(tag, 0, Qt.AlignmentFlag.AlignTop)
            text = label(value, 12, wrap=True, color=INK if value not in ("미연결", "미확인") else SECONDARY)
            ignore_width(text)
            line.addWidget(text, 1)
            self.evidence_rows.addWidget(row)

    SW_TONES = {
        "danger": ("rgba(194,51,69,0.35)", "#faeff0", "#f6e2e5", "#c23345"),
        "success": ("rgba(32,132,87,0.35)", "#eef7f1", "#d4ebdf", "#246b4a"),
        "warning": ("rgba(184,128,0,0.35)", "#fdf6e3", "#fbe5a3", "#856009"),
        "neutral": ("#dee0e5", "#f4f5f8", "#e9ecf2", SECONDARY),
    }

    def _render_sw_cards(self, tones=()):
        """detail_model의 행을 그대로 카드로 표시한다. 표와 같은 문자열만 쓴다."""
        headings = self.detail_model.headings
        cards = []
        for index, row in enumerate(self.detail_model.rows):
            border, header_bg, pill_bg, ink = self.SW_TONES[tones[index] if index < len(tones) else "neutral"]
            card = QFrame()
            card.setObjectName("swCard")
            card.setStyleSheet(f"QFrame#swCard {{background:white;border:2px solid {border};border-radius:12px;}}")
            card_body = QVBoxLayout(card)
            card_body.setContentsMargins(0, 0, 0, 0)
            card_body.setSpacing(0)
            header = QFrame()
            header.setObjectName("swHeader")
            header.setStyleSheet(f"QFrame#swHeader {{background:{header_bg};border:none;border-top-left-radius:10px;"
                                 "border-top-right-radius:10px;}")
            header_row = QHBoxLayout(header)
            header_row.setContentsMargins(14, 10, 14, 10)
            header_row.setSpacing(10)
            chip = label(row[0], 13, medium=True)
            chip.setStyleSheet("background:#f4f5f8;border-radius:6px;padding:4px 8px;")
            chip.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            header_row.addWidget(chip)
            title = label(row[1], 14, medium=True, wrap=True)
            ignore_width(title)
            header_row.addWidget(title, 1)
            pill = label(row[4], 12, medium=True)
            pill.setStyleSheet(f"background:{pill_bg};color:{ink};border-radius:12px;padding:4px 10px;")
            header_row.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
            card_body.addWidget(header)
            content = QVBoxLayout()
            content.setContentsMargins(14, 12, 14, 12)
            content.setSpacing(10)
            fields = QGridLayout()
            fields.setHorizontalSpacing(12)
            fields.setVerticalSpacing(10)
            for position, (asset, column) in enumerate((("50857.svg", 2), ("2c52c.svg", 3), ("ba1bd.svg", 1))):
                cell, value = self._field(asset, headings[column])
                value.setText(row[column])
                fields.addWidget(cell, position // 2, position % 2)
            fields.setColumnStretch(0, 1)
            fields.setColumnStretch(1, 1)
            content.addLayout(fields)
            reason = QFrame()
            reason.setObjectName("swReason")
            reason.setStyleSheet(f"QFrame#swReason {{background:{header_bg};border:none;border-radius:8px;}}")
            reason_row = QHBoxLayout(reason)
            reason_row.setContentsMargins(14, 10, 14, 10)
            reason_row.setSpacing(14)
            reason_row.addWidget(label(headings[5], 13, medium=True, color=ink), 0, Qt.AlignmentFlag.AlignTop)
            reason_text = label(row[5], 12, wrap=True)
            ignore_width(reason_text)
            reason_row.addWidget(reason_text, 1)
            content.addWidget(reason)
            card_body.addLayout(content)
            cards.append(card)
        self.sw_cards.set_widgets(cards)
        self.sw_title.setText(f"관측된 SW ({len(cards)}건)")
        self.table_title.setVisible(bool(cards))

    def _reset_detail_extras(self):
        self.detail_goal_badge.setText("판정 전")
        self.detail_goal_badge.set_tone("neutral")
        self.detail_risk_badge.setText("위험 판정 전")
        self.detail_risk_badge.set_tone("neutral")
        self._set_meta({key: None for key in self.meta_values})
        self._set_judgement({key: ("—", "", INK) for key in self.judgement})
        self._set_evidence_rows(())
        self._render_sw_cards()

    def _reflow(self, wide):
        if self._wide is wide:
            return
        self.primary.removeWidget(self.list_card)
        self.primary.removeWidget(self.summary)
        self.summary.setMinimumWidth(0)
        self.summary.setMaximumWidth(288 if wide else 16777215)
        if wide:
            self.summary.setMinimumWidth(288)
        self.primary.addWidget(self.list_card, 0, 0)
        self.primary.addWidget(self.summary, 0 if wide else 1, 1 if wide else 0,
                               alignment=Qt.AlignmentFlag.AlignTop)
        self.primary.setColumnStretch(0, 1)
        self._wide = wide

    def resizeEvent(self, event):
        self._reflow(event.size().width() >= 1160)
        super().resizeEvent(event)

    def _set_mode(self, mode):
        self.mode = mode
        self.list_view.setVisible(mode == "list")
        self.detail_card.setVisible(mode == "detail")
        self.mode_changed.emit(mode)
        self.updateGeometry()

    def clear(self, text="선택지 변경 · 재조회 필요", *_):
        self.close_item_detail()
        self.snapshot, self.data, self.scope_ids, self.selected_id = None, None, (), None
        self.detail_model.set_rows(())
        self.detail_title.setText("설비 상세")
        self.detail_location.clear()
        self.detail_summary.clear()
        self.scope_note.clear()
        self.evidence.clear()
        self._reset_detail_extras()
        self._set_mode("list")
        self.result_status.setText(text if isinstance(text, str) else "선택지 변경 · 재조회 필요")
        self.result_status.set_tone("warning")
        self.context.setText("표시할 조회 결과가 없습니다. 조회 적용으로 다시 확인해 주세요.")
        for card in self.metrics.values():
            card.value.setText("—")
            card.caption.setText("결과 없음")
        self._render_list()

    def accept_result(self, query, snapshot):
        if not self.state.is_current(query) or snapshot.query is not query or self.source.catalog != self.state.catalog:
            return False
        try:
            data = equipment_data(snapshot, self.source)
        except (ValueError, RuntimeError):
            self.clear("설비 결과 표시 실패 · 재조회 필요")
            return False
        self.snapshot, self.data = snapshot, data
        self.result_status.setText(f"{getattr(self.source, 'result_label', '예시 결과')} · 조회 적용됨")
        self.result_status.set_tone("info")
        self.selected_id = None
        self.open_all(self.origin)
        return True

    def _current(self):
        return self.snapshot is not None and self.state.is_current(self.snapshot.query)

    def _reset_filters(self, risk_only=False):
        for control in (self.search, self.goal_filter, self.sort, self.risk_button):
            control.blockSignals(True)
        self.search.clear()
        self.goal_filter.setCurrentIndex(0)
        self.sort.setCurrentIndex(0)
        self.risk_button.setChecked(risk_only)
        for control in (self.search, self.goal_filter, self.sort, self.risk_button):
            control.blockSignals(False)
        self.page_index = 0

    def open_all(self, origin="heatmap"):
        if not self._current():
            return False
        self.close_item_detail()
        self.origin = origin if origin in ("dashboard", "heatmap") else self.origin
        self.scope_ids, self.scope_label = self.data.equipment_ids, "적용 범위 전체"
        self._reset_filters()
        self._update_scope()
        self._set_mode("list")
        return True

    def open_scope(self, request, origin="dashboard"):
        if not self._current() or request.query is not self.snapshot.query or request.base_date != self.snapshot.base_date:
            return False
        if not validate_scope(self.data, request.equipment_ids):
            return False
        if not set(request.equipment_ids) <= set(self.snapshot.equipment_ids):
            return False  # 별도 HPA 조회 자료로 대시보드의 전달 범위를 넓히지 않는다.
        if request.risk_only and not set(request.equipment_ids) <= set(self.snapshot.risky_ids):
            return False
        self.origin = origin if origin in ("dashboard", "heatmap") else "dashboard"
        self.scope_ids = request.equipment_ids
        self.scope_label = request.label + (" · 후보 전달 범위" if request.risk_only else " · 전달 범위 전체")
        self._reset_filters(request.risk_only)
        self._update_scope()
        self._set_mode("list")
        return True

    def _update_scope(self):
        query = self.snapshot.query
        self.context.setText(f"적용 #{query.revision} · 일반 분석 기준일 {self.snapshot.base_date} · {query.description}\n"
                             f"현재 목록: {self.scope_label} · {len(self.scope_ids)}대 · 검색/후보 필터는 표시만 변경\n"
                             "HPA는 조건에 맞는 최신 자료와 기존 전달 범위의 관측을 조회하며 실제 관측일을 표시합니다.\n"
                             + self.snapshot.source_label)
        ids = set(self.scope_ids)
        records = tuple(r for r in self.snapshot.selected_records if r.equipment_id in ids)
        summary = summarize(records, self.source.families, target_map(self.snapshot.target_values))
        tiles = tuple(d.selected for d in self.data.details if d.selected.equipment_id in ids)
        candidates = sum(t.risk is RiskStatus.RISK for t in tiles)
        unknown = sum(t.risk is RiskStatus.UNKNOWN and t.goal is not GoalState.EXCLUDED for t in tiles)
        hpa_count = sum(t.goal is GoalState.EXCLUDED for t in tiles)
        values = {"total": (f"{len(ids)}대", f"분석 {summary.equipment_count}대 · HPA 제외 {hpa_count}대"),
                  "risk": (count_value(candidates, unknown, "미확인"), f"목표 달성과 별도 · 위험 미확인 {unknown}대"),
                  "rate": ("계산 불가" if summary.rate is None else f"{summary.rate:.1%}",
                           f"{summary.compliant}/{summary.compliant + summary.below}건 · 판단 불가 {summary.undetermined}건 · SW 미등록 {missing_sw_count(summary)}대\n단위: 설비·계열 건 · 판단 불가는 분모 제외"),
                  "upgrade": (count_value(summary.upgrade_needed, summary.undetermined, "판단 불가"),
                              upgrade_caption("전달 범위 내 N 미만인 물리 설비 · 중복 제거", summary.undetermined))}
        for key, (value, caption) in values.items():
            self.metrics[key].value.setText(value)
            self.metrics[key].caption.setText(caption)
        if summary.rate == 1 and (not summary.complete or missing_sw_count(summary)):
            self.metrics["rate"].caption.setText(self.metrics["rate"].caption.text() + " · 확인 필요")
        self.all_button.setText(f"범위 전체 {len(ids)}")
        self.risk_button.setText(f"후보만 {candidates}")
        self._render_list()

    def _filter_changed(self, *_):
        self.page_index = 0
        self._render_list()

    def change_page(self, delta):
        last = max(0, (len(self.visible_tiles) - 1) // self.page_size)
        self.page_index = max(0, min(last, self.page_index + delta))
        self._render_list()

    def _render_list(self):
        current = self._current()
        for control in (self.search, self.goal_filter, self.sort, self.risk_button, self.all_button, self.global_button):
            control.setEnabled(current)
        goal = self.goal_filter.currentData()
        self.visible_tiles = filtered_tiles(self.data, self.scope_ids, text=self.search.text(), risk_only=self.risk_button.isChecked(),
                                            goal=GoalState(goal) if goal is not None else None,
                                            sort=self.sort.currentData()) if current else ()
        total = len(self.visible_tiles)
        self.page_index = min(self.page_index, max(0, (total - 1) // self.page_size))
        start = self.page_index * self.page_size
        rows, tones = [], []
        page = self.visible_tiles[start:start + self.page_size]
        for tile in page:
            versions = "\n".join(tile.raw_versions or tuple(v for f in tile.families for v in f.versions)) or ("분석 제외" if tile.goal is GoalState.EXCLUDED else "SW 미등록")
            names = tile.model_name or "\n".join(f.title for f in tile.families) or "—"
            location = tile.location + (f" · 실제 관측일 {tile.observation_date}" if tile.goal is GoalState.EXCLUDED else "")
            code = tile.code
            if self.sort.currentData() == "priority":
                entry = self.data.priority.get(tile.equipment_id)
                code = (f"{entry.rank}위 · " if entry is not None and entry.rank else "확인 필요 · ") + code
            rows.append((code, location.replace(" · ", "\n"), names, versions,
                         tile.goal.value + ("\n복수 SW" if tile.multi_sw else "") + ("\nPM 불일치" if tile.pm_mismatch else ""),
                         tile.risk_label + "\n세부 근거 미연결"))
            tones.append("success" if tile.goal is GoalState.MET else "warning" if tile.goal is GoalState.BELOW else "neutral")
        self.model.set_rows(rows, tuple(t.equipment_id for t in page), tones)
        fit_rows(self.table)
        self.table.setVisible(bool(page))
        self.message.setVisible(not page)
        self.message.setText("표시 조건에 맞는 설비가 없습니다. 검색·목표·후보 필터를 확인해 주세요." if current else "조회 결과가 없습니다. 위에서 조회 적용을 눌러 주세요.")
        self.count.setText(f"결과 {total}대 · {start + 1 if total else 0}–{start + len(page)} 표시 / 전달 범위 {len(self.scope_ids)}대" if current else "표시할 조회 결과가 없습니다.")
        self.page_caption.setText(f"{self.page_index + 1 if total else 0} / {(total + self.page_size - 1) // self.page_size}")
        self.previous.setEnabled(current and self.page_index > 0)
        self.next.setEnabled(current and start + self.page_size < total)

    def open_row(self, row):
        if not self._current() or not 0 <= row < len(self.model.ids):
            return False
        equipment_id = self.model.ids[row]
        detail = next(d for d in self.data.details if d.record.equipment_id == equipment_id)
        request = EquipmentSelection(self.snapshot.query, self.snapshot.base_date, equipment_id, detail.record.base_date)
        return self.open_detail(request, origin="list")

    def open_detail(self, request, origin="heatmap"):
        if not self._current() or request.query is not self.snapshot.query or request.base_date != self.snapshot.base_date:
            return False
        detail = next((d for d in self.data.details if d.record.equipment_id == request.equipment_id), None)
        if detail is None or (request.observation_date is not None and request.observation_date != detail.record.base_date):
            return False
        self.close_item_detail()
        self.detail_back = origin if origin in ("dashboard", "heatmap", "list") else "list"
        if origin != "list":
            self.origin = origin if origin in ("dashboard", "heatmap") else self.origin
        self.selected_id = request.equipment_id
        self.detail_title.setText(detail.record.equipment_code + " · 설비 상세")
        self.detail_location.setText(detail.whole.location + f" · 실제 관측일 {detail.record.base_date}")
        self.detail_summary.setText(f"물리 설비 1대 · 전체 SW 관측 {len(detail.record.pms)}건 · 모듈 수 미확정\n"
                                    f"선택 SW 판정: {detail.selected.goal.value} / 설비 전체 등록 SW 판정: {detail.whole.goal.value}\n"
                                    f"설비 위험: {detail.whole.risk_label} · 목표 상태와 별도\n"
                                    + priority_text(self.data.priority.get(detail.record.equipment_id), self.data.thresholds))
        self.scope_note.setText("관측된 SW 카드와 원문 표는 선택 설비의 전체 SW 관측입니다. 현재 설비군 밖의 SW도 표시하지만 대시보드·목록의 적용 범위를 넓히지는 않습니다.\n"
                               f"일반 분석 기준일 {self.snapshot.base_date} · 현재 적용: {self.snapshot.query.description}"
                               + ("\nHPA 원본 조회는 공통 분석에 포함하지 않습니다." if detail.record.is_hpa else ""))
        models = {mid: g.label for g in self.source.catalog.groups for mid in self.source.bindings[g.key]}
        rows, tones = [], []
        if detail.status:
            for family in detail.status.families:
                for item in family.pms:
                    pm, comparison = item.pm, item.comparison
                    rows.append((pm.pm_name or "모듈 미확정 · 공통 SW",
                                 f"{models[pm.group_id]} · 계열 {family_name(self.source, family.family_id)}",
                                 pm.version_raw if pm.version_raw is not None else "미입력",
                                 comparison.target.version_raw if comparison.target else "목표 미확인", comparison.label,
                                 comparison.message or "확인된 릴리즈 순서로 비교"))
                    tones.append(compare_tone(comparison.status, comparison.steps_below))
        elif detail.record.is_hpa:
            rows = [(pm.pm_name or "모듈 미확정 · 공통 SW",
                     f"{models.get(pm.group_id, detail.record.model_name)} · 계열 {family_name(self.source, pm.family_id)}",
                     pm.version_raw if pm.version_raw is not None else "미입력", "—", "분석 제외", "HPA는 N 비교에서 제외")
                    for pm in detail.record.pms]
        self.detail_model.set_rows(rows)
        fit_rows(self.detail_table)
        self.detail_table.setVisible(bool(rows))
        self._render_sw_cards(tones)
        self._show_detail_extras(detail, rows)
        extra = "HPA는 분석 제외입니다." if detail.record.is_hpa else "SW 관측이 없습니다." if not rows else "PM 배치 메모로 안전 Item 미적용을 추정하지 않습니다."
        self.evidence.setText(f"{extra}\n위험 근거: 판정 결과만 연결됨 · 규칙·필수 Item·릴리즈 문서·원본 위치 미연결.\n"
                               f"관측 출처: {self.snapshot.source_label} · 실제 관측일 {detail.record.base_date} · 원본 셀 연결은 확인 필요")
        self._set_mode("detail")
        return True

    def _show_detail_extras(self, detail, rows):
        whole, record = detail.whole, detail.record
        self.detail_goal_badge.setText(f"설비 전체 {whole.goal.value}")
        self.detail_goal_badge.set_tone(whole.version_tone)
        self.detail_risk_badge.setText(whole.risk_label)
        self.detail_risk_badge.set_tone("danger" if whole.risk is RiskStatus.RISK and whole.goal is not GoalState.EXCLUDED
                                        else "warning" if whole.risk is RiskStatus.UNKNOWN and whole.goal is not GoalState.EXCLUDED
                                        else "neutral")
        self._set_meta({"site": whole.site_label, "fab": whole.fab_label, "line": whole.line_label,
                        "area": whole.area, "date": record.base_date})
        counts = [f.status for f in whole.families]
        if whole.goal is GoalState.EXCLUDED:
            goal = ("분석 제외", "HPA는 목표 N 비교에서 제외", "#5e6b7a")
        else:
            goal = (whole.goal.value,
                    f"계열 기준 달성 {counts.count(CompareStatus.AT_OR_ABOVE)} / 미달 {counts.count(CompareStatus.BELOW)}"
                    f" / 확인 필요 {counts.count(CompareStatus.UNDETERMINED)} · 선택 SW {detail.selected.goal.value}",
                    TONE_INK[whole.version_tone])
        risk_color = ("#c23345" if whole.risk is RiskStatus.RISK else "#b88000" if whole.risk is RiskStatus.UNKNOWN else "#5e6b7a")
        self._set_judgement({
            "goal": goal,
            "risk": (whole.risk_label, "판정 결과만 연결됨 · 규칙·근거 문서 미연결 · 목표 판정과 별개",
                     "#5e6b7a" if whole.goal is GoalState.EXCLUDED else risk_color),
            "complete": ("미확정", "모듈 · 필수 SW 목록 확인 필요", "#5e6b7a"),
        })
        targets = " / ".join(dict.fromkeys(row[3] for row in rows if row[3] != "—")) or "미확인"
        self._set_evidence_rows((
            ("11605.svg", "실제 관측일", f"{record.base_date} (일반 분석 기준일 {self.snapshot.base_date})"),
            ("f5960.svg", "원본 모델", record.model_name or "미확인"),
            ("8cc7b.svg", "비교 목표 N", "HPA 비교 제외" if record.is_hpa else targets),
            ("f8090.svg", "위치", whole.location),
            ("3c247.svg", "판정 규칙", "미연결"),
            ("a2d12.svg", "필수 아이템명", "미연결"),
            ("ac176.svg", "릴리즈 문서 · 아이템 위치", "미연결"),
            ("f5960.svg", "관측 출처", self.snapshot.source_label),
        ))

    def _show_item_extras(self, request, application):
        record = application.record
        self.detail_goal_badge.setText("N 비교 안 함")
        self.detail_goal_badge.set_tone("neutral")
        self.detail_risk_badge.setText("위험 판정 미연결")
        self.detail_risk_badge.set_tone("neutral")
        self._set_meta({"site": record.site, "fab": None, "line": None, "area": None, "date": record.base_date})
        self._set_judgement({
            "goal": (application.label, f"선택 Item ID {request.item_id} · 목표 N 비교는 이 보기에서 수행하지 않음", INK),
            "risk": ("미연결", "Item 적용만으로 안전 여부를 추정하지 않음", "#5e6b7a"),
            "complete": ("미확정", "모듈 · 필수 SW 목록 확인 필요", "#5e6b7a"),
        })
        self._set_evidence_rows((
            ("11605.svg", "기준일", str(record.base_date)),
            ("f5960.svg", "원본 모델", record.model_name or "미확인"),
            ("f8090.svg", "위치", record.site or "미확인"),
            ("3c247.svg", "판정 규칙", "미연결"),
            ("ac176.svg", "원본 출처", "아래 Item 근거 표 참조"),
        ))

    def open_item_detail(self, request, *, is_current):
        """같은 상세 위젯으로 Item 관측을 표시한다. A 스냅샷·조회 조건은 변경하지 않는다."""
        if not is_current(request):
            return False
        application = selected_application(request)
        if application is None:
            return False
        self.close_item_detail()
        self._item_restore = (self.result_status.text(), self.result_status.tone, self.context.text(),
                              self.detail_model.headings, self.selected_id, self.detail_back, self.mode)
        self.item_request = request
        self.selected_id, self.detail_back = request.equipment_id, "items"
        self.result_status.setText("Item 관측 자료 · 읽기 전용")
        self.result_status.set_tone("info")
        self.context.setText(request.data.observation_label)
        record = application.record
        self.detail_title.setText(record.equipment_code + " · 설비 상세")
        self.detail_location.setText(f"{record.site} · {record.model_name} · 설비 ID {record.equipment_id} · 기준일 {record.base_date}")
        self.detail_summary.setText(f"선택 Item ID {request.item_id} · {application.label} · 전체 SW 관측 {len(record.pms)}건")
        self.scope_note.setText(f"Item 표시 범위: {request.site if request.site is not None else '전체 Site'} · "
                               f"{APPLICATION_LABELS[request.state] if request.state else '전체 상태'}\n"
                               "Item 화면의 같은 기준일 관측입니다. 공통 조회 범위·목표 N은 변경하지 않으며 이 보기에서는 N 비교를 수행하지 않습니다.")
        self.detail_model.headings = ("모듈 / SW 관측", "모델 · 계열", "설치 버전 원문", "대응 릴리즈", "Item 적용", "적용 근거")
        self.detail_model.set_rows(tuple((p.pm.pm_name or "공통 SW",
            f"{model_name(self.source, p.pm.group_id)} · 계열 {family_name(self.source, p.pm.family_id)}",
            p.pm.version_raw if p.pm.version_raw is not None else "미입력",
            f"{p.release.version_raw} · ID {p.release.release_version_id}" if p.release else "대응 미확인 / 대상 외",
            APPLICATION_LABELS[p.application], BASIS_LABELS[p.basis]) for p in application.pms))
        fit_rows(self.detail_table)
        self.detail_table.setVisible(bool(application.pms))
        self._render_sw_cards()
        self._show_item_extras(request, application)
        self.evidence.setText("Item 적용 근거는 아래 저장 행·원본 출처를 확인해 주세요. 저장 행이 없는 버전은 미확인입니다.\n"
                              "위험 규칙·필수 Item·위험 해제 판정은 미연결이며 Item 적용만으로 안전 여부를 추정하지 않습니다.\n"
                              "원본 문서/셀 열기는 미연결입니다." + ("\nPM/SW 관측 없음 · 업무 규칙상 대상 외" if not application.pms else ""))
        self.item_evidence_model.set_rows(item_evidence_rows(request, application))
        fit_rows(self.item_evidence_table)
        self.item_evidence_table.show()
        self.list_button.setEnabled(False)
        self.back_button.setText("Item 적용 현황으로 돌아가기")
        self._set_mode("detail")
        return True

    def close_item_detail(self):
        if self.item_request is None:
            return
        text, tone, context, headings, selected, detail_back, previous_mode = self._item_restore
        self.item_request, self._item_restore = None, None
        self.selected_id, self.detail_back = selected, detail_back
        self.result_status.setText(text)
        self.result_status.set_tone(tone)
        self.context.setText(context)
        self.detail_model.headings = headings
        self.detail_model.set_rows(())
        self.item_evidence_model.set_rows(())
        self.item_evidence_table.hide()
        for widget in (self.detail_title, self.detail_location, self.detail_summary, self.scope_note, self.evidence):
            widget.clear()
        self._reset_detail_extras()
        self.list_button.setEnabled(True)
        self.back_button.setText("이전 조회로 돌아가기")
        if previous_mode == "detail" and self._current() and selected is not None:
            self.open_detail(EquipmentSelection(self.snapshot.query, self.snapshot.base_date, selected), origin=detail_back)
        else:
            self._set_mode("list")

    def _back(self):
        if self.detail_back == "list":
            self._set_mode("list")
        else:
            self.back_requested.emit(self.detail_back)
