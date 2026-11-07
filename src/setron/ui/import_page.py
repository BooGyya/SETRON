"""C-1~C-3 데이터 가져오기. 열린 Excel 선택·원본 미리보기 → AI 추출 검토·수정 → 확인 후 저장.

Excel 읽기·AI 추출·저장은 주입받은 객체에 요청한다. 화면은 SQL을 실행하지 않고 원본 파일을 바꾸지 않는다.
메뉴에 들어와도 Excel에 바로 연결하지 않고, 사용자가 불러오기를 눌렀을 때만 연결한다.
"""

from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
import re
import uuid
import warnings

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QSizePolicy,
    QScrollArea, QStackedWidget, QStyle, QStyledItemDelegate, QTabBar, QTableView, QVBoxLayout, QWidget,
)

from setron.features.ai.schemas import ExtractKind
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.excel_import.reference import ReferenceData
from setron.features.excel_import.candidates import apply_model_candidates, candidate_notes, model_candidate_rows
from setron.features.excel_import.schemas import FIELD_LABELS, ISSUE_LABELS, KIND_LABELS, Issue, IssueLevel
from setron.features.excel_import.service import (
    build_save_request, cell_text, describe_save_error, draft_from_review, edit_value, parse_date,
    MemoryImportSaver, has_errors, parse_address, revert_row, set_excluded, snapshot_sheet, validate,
)
from setron.infrastructure import excel_reader
from setron.infrastructure.ai_client import AIConfigError, ResponseSource
from setron.infrastructure.excel_reader import ExcelDiscoveryWarning, ExcelReadError, cell_address
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.equipment import ReadTable, ReadTableModel, read_table
from setron.ui.import_demo import example_extractor
from setron.ui.import_extraction import LiveSheetExtractor, extract_for_review
from setron.features.excel_import.review_locations import assign_source, refresh_hpa_coverage
from setron.features.extraction.schemas import ExtractionReview
from setron.ui.query_panel import _WrapRow
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.release_import_review import choose_release_reviews
from setron.ui.import_delete import ImportDeletePage
from setron.ui.import_widgets import ComparisonCard, DateField, ImportComparison, ImportDataStateDialog, ImportSteps, import_combo_style, picture
from setron.ui.theme import ACCENT_SOFT, INK, LINE, PRIMARY, SECONDARY, SOFT, TONES, font
from setron.ui.versions import _field, _fit

SOURCE_LABELS = {
    ResponseSource.EXAMPLE: "예제 응답(EXAMPLE) · 실제 AI 결과 아님 · 시트 내용과 무관한 고정 응답",
    ResponseSource.REAL: "실제 AI API 응답(REAL)",
    ResponseSource.RULE: "규칙 추출(RULE) · 정해진 양식을 AI 없이 읽음 · 원본 대조 완료",
    None: "AI 응답 출처 미확인",
}


# 표시 전용 색·문구. 기존 검토 수준(IssueLevel)과 행 상태 문구에서만 고르고 새 판정을 만들지 않는다.
ISSUE_TONES = {IssueLevel.ERROR: "danger", IssueLevel.REVIEW: "warning", IssueLevel.INFO: "neutral"}
ISSUE_ACTIONS = {IssueLevel.ERROR: "수정 필요", IssueLevel.REVIEW: "확인", IssueLevel.INFO: "참고"}
LABEL_TONES = {ISSUE_LABELS[level]: tone for level, tone in ISSUE_TONES.items()}
DANGER_INK, WARNING_INK = TONES["danger"][1], TONES["warning"][1]
FIELD_LINE = "#dee0e5"
STEP_TEXT = "실행 중인 Excel 읽기 → 원본 미리보기 → AI 추출 · 검토 → 저장"


def _row_state_tone(text):
    """행 상태 문구(정상·오류 n·확인 n·수정됨·제외)를 배지 색으로 옮긴다."""
    if text == "제외":
        return "neutral"
    if text == "확인 완료":
        return "success"
    if "오류" in text:
        return "danger"
    if "확인" in text:
        return "warning"
    return "info" if "수정됨" in text else "success"


class _BadgeDelegate(QStyledItemDelegate):
    """셀 문구를 그대로 작은 배지로 그린다. 문구는 모델 값이며 접근성 텍스트도 그대로 유지한다."""

    def __init__(self, tone_for, parent=None):
        super().__init__(parent)
        self.tone_for = tone_for

    def paint(self, painter, option, index):
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        self.initStyleOption(option, index)
        option.text = ""
        style = option.widget.style() if option.widget is not None else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        if not text:
            return
        background, ink, border = TONES[self.tone_for(text)]
        badge_font = font(12, medium=True)
        metrics = QFontMetrics(badge_font)
        width = min(metrics.horizontalAdvance(text) + 16, option.rect.width() - 8)
        height = metrics.height() + 6
        rect = QRectF(option.rect.center().x() - width / 2 + 0.5, option.rect.center().y() - height / 2 + 0.5,
                      width, height)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(border)))
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setFont(badge_font)
        painter.setPen(QColor(ink))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                         metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(width) - 8))
        painter.restore()


class _ReviewRowModel(ReadTableModel):
    """추출 행 목록. 오류가 있는 값은 빨강, 사용자가 고친 값은 파랑으로만 구분한다."""

    def __init__(self, headings):
        super().__init__(headings)
        self.text_colors = {}

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid():
            if role == Qt.ItemDataRole.ForegroundRole and (index.row(), index.column()) in self.text_colors:
                return QColor(self.text_colors[index.row(), index.column()])
            if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == 0:
                return Qt.AlignmentFlag.AlignCenter
        return super().data(index, role)


class _IssueModel(ReadTableModel):
    """검토 항목 목록. 행 자료(rows)는 기존 9열 그대로이고, 표시 전용 ‘처리’ 열은 검토 수준에서만 만든다."""

    action_column = 9

    def __init__(self, headings):
        super().__init__(headings)
        self.levels = ()
        self.reviewed = set()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        column = index.column()
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (0, 1, 3, self.action_column):
            return Qt.AlignmentFlag.AlignCenter
        if column != self.action_column:
            return super().data(index, role)
        level = self.levels[index.row()] if index.row() < len(self.levels) else None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole):
            if index.row() in self.reviewed:
                return "확인 완료"
            return ISSUE_ACTIONS.get(level, "")
        if role == Qt.ItemDataRole.FontRole:
            return font(13, medium=True)
        if role == Qt.ItemDataRole.ForegroundRole:
            return QColor(DANGER_INK if level is IssueLevel.ERROR else INK)
        return None


class _ImportTable(ReadTable):
    """넓은 창에서는 열을 늘리고, 좁은 창에서는 표 안에서만 가로 스크롤한다(페이지 가로 스크롤 없음)."""

    def resizeEvent(self, event):
        QTableView.resizeEvent(self, event)
        width = self.viewport().width()
        ratio = max(1.0, width / max(1, sum(self.base_widths)))
        for index, original in enumerate(self.base_widths):
            self.setColumnWidth(index, int(original * ratio))
        if self.model() is not None:
            self.resizeRowsToContents()


def _import_table(model, name, widths):
    """``read_table``과 같은 읽기 전용 설정에 VER5 표 머리·행 간격을 적용한다."""
    table = _ImportTable(widths)
    table.setModel(model)
    table.setAccessibleName(name)
    table.setMinimumWidth(0)
    table.setMinimumHeight(140)
    table.setFont(font(13))
    table.setWordWrap(True)
    table.setTextElideMode(Qt.TextElideMode.ElideNone)
    table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
    table.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
    table.verticalHeader().hide()
    table.verticalHeader().setMinimumSectionSize(44)
    table.horizontalHeader().setMinimumHeight(38)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    for index, width in enumerate(widths):
        table.setColumnWidth(index, width)
    table.setShowGrid(False)
    table.setStyleSheet(
        f"QTableView {{background:white;border:1px solid {FIELD_LINE};border-radius:8px;"
        f"selection-background-color:#eef1fd;selection-color:{INK};}}"
        f"QHeaderView::section {{background:#f4f5f8;color:{SECONDARY};border:0;border-bottom:1px solid {FIELD_LINE};"
        "padding:6px 10px;font-size:12px;}"
        f"QTableView::item {{padding:6px 10px;border-bottom:1px solid #eceef1;}}"
        f"QTableView:focus {{border:1px solid {PRIMARY};}}")
    return table


def _section(title):
    """번호가 붙은 단계 카드. 공통 Card의 테두리 형태를 사용한다."""
    card = Card(padding=28)
    card.setProperty("outlined", True)
    card.body.setSpacing(14)
    card.body.addWidget(label(title, 20, medium=True))
    return card


def _workbook_key(book):
    """선택한 파일명·Excel 프로세스·전체 경로를 하나의 선택 키로 보존한다.

    param
    - book: BE-006에서 받은 WorkbookInfo.

    return
    - 동명 파일과 목록 순서 변경에 영향을 받지 않는 내부 선택 키.
    """
    return json.dumps([book.name, book.app_pid, book.full_name], ensure_ascii=False)


def saver_from_env():
    """``SETRON_DB_*`` 접속 설정이 있으면 DB 저장소, 없으면 None(화면 기본값인 메모리 저장소)을 돌려준다.

    DB 접속은 첫 저장 때 한다. 앱 시작만으로 DB에 연결하지 않는다.
    """
    return DatabaseImportSaver.from_env()


class SheetGridModel(QAbstractTableModel):
    """원본 셀을 Excel 열 문자·행 번호와 함께 일반 텍스트로 표시한다. 큰 시트는 앞부분만 표시한다."""

    max_rows, max_cols = 300, 40

    def __init__(self):
        super().__init__()
        self.snapshot, self.highlight = None, None
        self.marks = {}  # 셀 주소 → 검토 수준. 검토 항목의 셀만 표시하고 원본 값은 바꾸지 않는다.

    def set_snapshot(self, snapshot):
        self.beginResetModel()
        self.snapshot, self.highlight, self.marks = snapshot, None, {}
        self.endResetModel()

    def set_marks(self, marks):
        """검토 항목이 가리키는 셀을 수준별 배경으로 표시한다."""
        if marks == self.marks:
            return
        self.marks = marks
        if self.rowCount() and self.columnCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.snapshot is None else min(self.snapshot.row_count, self.max_rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.snapshot is None else min(self.snapshot.col_count, self.max_cols)

    @property
    def truncated(self):
        s = self.snapshot
        return s is not None and (s.row_count > self.max_rows or s.col_count > self.max_cols)

    def address(self, row, col):
        return cell_address(self.snapshot.first_row + row, self.snapshot.first_col + col)

    def position(self, address):
        row, col = parse_address(address)
        return row - self.snapshot.first_row, col - self.snapshot.first_col

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or self.snapshot is None:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return cell_address(1, self.snapshot.first_col + section)[:-1]
        return str(self.snapshot.first_row + section)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self.snapshot is None:
            return None
        address = self.address(index.row(), index.column())
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return cell_text(self.snapshot.cells.get(address))
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{address} {cell_text(self.snapshot.cells.get(address))}"
        if role == Qt.ItemDataRole.BackgroundRole:
            if address == self.highlight:
                return QColor("#fbe5a3")
            level = self.marks.get(address)
            return QColor(TONES["danger"][0]) if level is IssueLevel.ERROR else \
                QColor("#fdf3d6") if level is IssueLevel.REVIEW else None
        if role == Qt.ItemDataRole.ForegroundRole and self.marks.get(address) is IssueLevel.ERROR:
            return QColor(DANGER_INK)
        if role == Qt.ItemDataRole.FontRole:
            return font(12)
        return None


class ImportPage(QWidget):
    """``excel``은 ``list_open_workbooks()``·``read_sheet()``를 제공한다(기본 ``excel_reader`` 모듈).

    ``read_sheet``에는 선택 목록의 파일명·app_pid·full_name을 함께 전달한다.
    ``extractor(kind, sheet_name, cells)``는 :class:`AIResult`를, ``saver``는 :class:`ImportSaver`를 따른다.
    추출 공급자가 없으면 설정된 실제 AI를 사용한다. 기존 공급자 결과도 원본 검증을 거친다.
    저장 공급자가 None이면 메모리 저장소를 쓴다.
    """

    saved = Signal(object)  # SaveReceipt
    requery_requested = Signal()
    navigate_requested = Signal(str)
    date_selected = Signal(object)

    def __init__(self, excel=None, extractor=None, saver=None, *, reference=None, confirm=None,
                 refresh_context_provider=None, release_review_chooser=None, sheet_extractor=None, rule_extractor=None,
                 comparison_loader=None, delete_gateway=None):
        super().__init__()
        self.excel = excel if excel is not None else excel_reader
        self.extractor = extractor
        self.sheet_extractor = sheet_extractor or LiveSheetExtractor()
        self.rule_extractor = rule_extractor        # 정해진 양식은 AI 없이 규칙으로 먼저 읽는다 (features.rule_extraction)
        self.saver = saver or MemoryImportSaver("메모리 저장소 · DB 미연결 · 조회 화면 자료에 반영되지 않음")
        self.reference = reference
        self.confirm = confirm or self._ask
        self.refresh_context_provider = refresh_context_provider
        self.release_review_chooser = release_review_chooser or choose_release_reviews
        self.workbooks, self.snapshot, self.workbook_name = (), None, None
        self.workbook_id = None
        self.draft, self.issues, self.selected_row_id = None, (), None
        self.reviewed_issues = set()
        self.state, self.generation, self.request_id, self.receipt = "idle", 0, None, None
        self.review_request = ReadRequest(self)
        self.review_request.completed.connect(self._review_completed)
        self._review_context = None
        self.extraction_request = ReadRequest(self)
        self.extraction_request.completed.connect(self._extraction_completed)
        self._extraction_context = None
        self.release_request = ReadRequest(self)
        self.release_request.completed.connect(self._release_review_completed)
        self._release_context = None
        self._release_risk_decisions = set()
        self.comparison_loader = comparison_loader
        self.comparison_request = ReadRequest(self)
        self.comparison_request.completed.connect(self._comparison_completed)
        self._comparison_context = None
        self.available_dates = ()
        self._outer_viewport = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        tab_row = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.addTab("가져오기")
        self.tabs.addTab("저장된 데이터 삭제")
        self.tabs.setExpanding(False)
        self.tabs.setAccessibleName("데이터 가져오기 화면 선택")
        self.tabs.setStyleSheet("QTabBar::tab {background:transparent;border:0;border-radius:0;padding:8px 10px;color:#5b6880;}"
                               "QTabBar::tab:selected {color:#3c5ce9;border-bottom:2px solid #3c5ce9;}")
        tab_row.addWidget(self.tabs)
        tab_row.addStretch()
        self.data_state_button = button("데이터 상태 보기")
        self.data_state_button.clicked.connect(self.open_data_state)
        tab_row.addWidget(self.data_state_button)
        outer.addLayout(tab_row)
        self.workspace = QStackedWidget()
        self.import_scroll, self.delete_scroll = QScrollArea(), QScrollArea()
        for scroll in (self.import_scroll, self.delete_scroll):
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.import_content = QWidget()
        self.import_content.setObjectName("canvas")
        body = QVBoxLayout(self.import_content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        # 단계 안내 띠: 왼쪽은 고정 단계 문구, 오른쪽은 현재 상태 배지. 상세 안내는 바로 아래 줄에 둔다.
        self.steps = QFrame()
        self.steps.setObjectName("importSteps")
        steps = QHBoxLayout(self.steps)
        steps.setContentsMargins(16, 8, 10, 8)
        steps.setSpacing(12)
        self.steps_label = label(STEP_TEXT)  # 기존 안내 문자열은 접근성·테스트용으로 보존한다.
        self.steps_label.hide()
        self.progress_steps = ImportSteps()
        self.progress_steps.jump_requested.connect(self.jump_to_step)
        steps.addWidget(self.progress_steps, 1)
        self.status = StatusBadge("Excel 미연결", "info")
        steps.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(self.steps)
        self.saved_banner = QFrame()
        self.saved_banner.setStyleSheet("QFrame {background:#e9f4ee;border-radius:8px;}")
        banner = QHBoxLayout(self.saved_banner)
        banner.addWidget(picture("3ad3a.svg"))
        self.saved_caption = label("", 13, medium=True, color="#1f8c59", wrap=True)
        self.saved_caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        banner.addWidget(self.saved_caption, 1)
        for title, route in (("히트맵에서 보기", "heatmap"), ("대시보드에서 보기", "dashboard")):
            action = button(title)
            action.setToolTip("화면으로 이동합니다. 저장 자료를 조회하려면 조회 적용을 눌러 주세요.")
            action.clicked.connect(lambda checked=False, key=route: self.navigate_requested.emit(key))
            banner.addWidget(action)
        self.saved_banner.hide()
        body.addWidget(self.saved_banner)
        self.notice = label("열린 통합문서 불러오기를 누르면 실행 중인 Excel에 연결합니다.", 13, color=SECONDARY, wrap=True)
        self.notice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.notice.setContentsMargins(4, 0, 4, 0)
        body.addWidget(self.notice)

        select = _section("1. Excel 통합문서 · 시트 선택")
        self.step_cards = [select]
        select.body.addWidget(label("사용자가 실행해 둔 데스크톱 Excel에서 값을 읽습니다. 파일 업로드·파일 선택은 지원하지 않으며 "
                                    "원본 파일을 수정하거나 복사하지 않습니다.", 13, color=SECONDARY, wrap=True))
        self.workbook_combo, self.sheet_combo, self.kind_combo = QComboBox(), QComboBox(), QComboBox()
        for widget, name in ((self.workbook_combo, "통합문서 선택"), (self.sheet_combo, "시트 선택"),
                             (self.kind_combo, "가져올 자료 종류")):
            widget.setAccessibleName(name)
            widget.setProperty("queryField", True)
            widget.setFixedHeight(42)
            widget.setFont(font(14, medium=True))
            widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            widget.setMinimumContentsLength(10)
            widget.setStyleSheet(import_combo_style())
        for kind in ExtractKind:
            self.kind_combo.addItem(KIND_LABELS[kind], kind.value)
        self.list_button = button("열린 통합문서 불러오기")
        self.read_button = button("시트 읽기", primary=True)
        select.body.addWidget(_WrapRow([_field("통합문서", self.workbook_combo), _field("시트", self.sheet_combo),
                                       _field("자료 종류", self.kind_combo), QWidget()], 230, fields=True))
        for widget in (self.list_button, self.read_button):
            widget.setFixedHeight(40)
        select.body.addWidget(_WrapRow([self.list_button, self.read_button], 200))
        body.addWidget(select)

        preview = _section("2. 원본 미리보기")
        self.step_cards.append(preview)
        self.preview_info = label("시트를 읽으면 원본 셀 값과 주소가 표시됩니다.", 12, color=SECONDARY, wrap=True)
        preview.body.addWidget(self.preview_info)
        self.grid_model = SheetGridModel()
        self.grid = QTableView()
        self.grid.setModel(self.grid_model)
        self.grid.setAccessibleName("원본 시트 미리보기")
        self.grid.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.grid.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.grid.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.grid.setFixedHeight(300)
        self.grid.setMinimumWidth(0)
        self.grid.horizontalHeader().setDefaultSectionSize(120)
        self.grid.horizontalHeader().setFixedHeight(26)
        self.grid.verticalHeader().setDefaultSectionSize(28)
        self.grid.verticalHeader().setMinimumWidth(36)
        self.grid.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.grid.setStyleSheet(
            f"QTableView {{background:white;gridline-color:#e5e7eb;border:1px solid {FIELD_LINE};"
            f"selection-background-color:#edf0fd;selection-color:{INK};}}"
            f"QHeaderView::section {{background:#f4f5f8;color:{SECONDARY};border:0;border-right:1px solid #e5e7eb;"
            "border-bottom:1px solid #e5e7eb;padding:2px 4px;font-size:11px;}"
            "QTableCornerButton::section {background:#f4f5f8;border:0;border-right:1px solid #e5e7eb;"
            "border-bottom:1px solid #e5e7eb;}"
            f"QTableView:focus {{border:1px solid {PRIMARY};}}")
        self.grid.clicked.connect(self._show_cell)
        preview.body.addWidget(self.grid)
        self.cell_label = label("", 13, color=SECONDARY, wrap=True)
        self.cell_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        preview.body.addWidget(self.cell_label)
        body.addWidget(preview)

        review = _section("3. AI 추출 결과 검토 · 수정")
        self.step_cards.append(review)
        review.body.addWidget(label("AI 추출값은 원본과 따로 보관합니다. 수정한 값은 ‘수정됨’으로 표시되고 원본 값·셀 위치는 바뀌지 않습니다. "
                                    "AI 결과는 검토 없이 저장하지 않습니다.", 13, color=SECONDARY, wrap=True))
        self.extract_button = button("항목 추출 (규칙 → AI)" if self.rule_extractor is not None else "AI로 항목 추출", primary=True)
        self.extract_button.setFixedHeight(40)
        self.source_badge = StatusBadge("AI 추출 전", "neutral")
        actions = QHBoxLayout()
        actions.setSpacing(12)
        actions.addWidget(self.extract_button)
        actions.addWidget(self.source_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        actions.addStretch()
        review.body.addLayout(actions)
        # 시트 공통 정보: 교육용 현황표에는 기준일·사업장 셀이 없어 한 번 입력해 모든 행에 적용한다 (1차 점검 6번).
        common = QFrame()
        common.setObjectName("importCommon")
        common.setStyleSheet(f"QFrame#importCommon {{background:{SOFT};border:1px solid {FIELD_LINE};border-radius:10px;}}")
        common_layout = QVBoxLayout(common)
        common_layout.setContentsMargins(20, 16, 20, 16)
        common_layout.setSpacing(8)
        common_layout.addWidget(label("기준 날짜 · Site", 15, medium=True))
        common_layout.addWidget(label("기준일·사업장이 시트에 없으면 여기 입력하고 ‘모든 행에 적용’을 누르세요. 비어 있는 행만 채우고, "
                                      "메모리/파운드리를 비우면 라인에 따라 기본값(장소표)을 씁니다.", 13, color=SECONDARY, wrap=True))
        self.common_date, self.common_site = DateField(), QLineEdit()
        self.common_date.setPlaceholderText("기준일 YYYY-MM-DD")
        self.common_site.setPlaceholderText("사업장 (예: 화성, 평택)")
        self.common_site_combo = QComboBox()
        self.common_site_combo.setEditable(True)
        self.common_site_combo.setLineEdit(self.common_site)
        self.common_site_combo.setAccessibleName("가져올 자료의 Site 선택 또는 입력")
        self.common_site_combo.setProperty("queryField", True)
        self.common_site_combo.setFixedHeight(38)
        self.common_site_combo.setStyleSheet(import_combo_style())
        self.common_area = QComboBox()
        self.common_area.setStyleSheet(import_combo_style())
        for text, value in (("메모리/파운드리 · 라인 기준 자동", ""), ("MEMORY", "MEMORY"), ("FOUNDRY", "FOUNDRY")):
            self.common_area.addItem(text, value)
        for widget, name in ((self.common_date, "공통 기준일"), (self.common_site_combo, "공통 사업장"), (self.common_area, "공통 메모리/파운드리")):
            widget.setAccessibleName(name)
            widget.setProperty("queryField", True)
            widget.setFixedHeight(38)
        self.common_apply_button = button("모든 행에 적용")
        self.common_overwrite = QCheckBox("이미 있는 값도 덮어쓰기")
        common_layout.addWidget(_WrapRow([_field("기준일", self.common_date), _field("Site", self.common_site_combo),
                                          _field("메모리/파운드리", self.common_area), QWidget()], 230, fields=True))
        common_layout.addWidget(_WrapRow([self.common_apply_button, self.common_overwrite], 220))
        self.common_box = common
        select.body.insertWidget(3, common)
        self.release_context = QFrame()
        release_context = QVBoxLayout(self.release_context)
        release_context.setContentsMargins(0, 0, 0, 0)
        self.release_family = QComboBox()
        self.release_family.setEditable(True)
        self.release_family.setAccessibleName("릴리즈 노트 SW 계열 선택")
        self.release_family.setProperty("queryField", True)
        self.release_family.setStyleSheet(import_combo_style())
        self.release_family.setFixedHeight(40)
        self.family_apply_button = button("선택한 계열을 빈 행에 적용")
        self.family_apply_button.clicked.connect(self.apply_release_family)
        release_context.addWidget(_field("설비군 / SW 계열", self.release_family))
        release_context.addWidget(label("릴리즈 노트에는 기준 날짜·Site 대신 계열을 지정합니다. 모델·UI 범위는 추출 결과에서 따로 확인하세요.", 12, color=SECONDARY, wrap=True))
        release_context.addWidget(self.family_apply_button, 0, Qt.AlignmentFlag.AlignLeft)
        select.body.insertWidget(4, self.release_context)
        self.row_model = _ReviewRowModel(("행", "상태"))
        self.row_table = _import_table(self.row_model, "추출 행 목록", (64, 120))
        self.row_table.setItemDelegateForColumn(1, _BadgeDelegate(_row_state_tone, self.row_table))
        self.row_table.clicked.connect(lambda index: self.select_row(self.row_model.ids[index.row()]))
        self.row_table.activated.connect(lambda index: self.select_row(self.row_model.ids[index.row()]))
        review.body.addWidget(self.row_table)
        self.row_empty = label("", color=SECONDARY, wrap=True)
        review.body.addWidget(self.row_empty)
        # 선택 행 편집 패널: 값 입력 · 원본 셀 주소 상자 · ‘AI 원본’ 설명을 한 줄씩 묶는다.
        self.editor = QFrame()
        self.editor.setObjectName("importEditor")
        self.editor.setStyleSheet("QFrame#importEditor {background:#f4f5f8;border-radius:12px;}")
        editor = QVBoxLayout(self.editor)
        editor.setContentsMargins(20, 18, 20, 18)
        editor.setSpacing(12)
        self.editor_title = label("", 15, medium=True)
        editor.addWidget(self.editor_title)
        self.editor_grid = QGridLayout()
        self.editor_grid.setHorizontalSpacing(12)
        self.editor_grid.setVerticalSpacing(4)
        self.editor_grid.setColumnStretch(1, 1)
        self.editor_grid.setColumnMinimumWidth(0, 96)
        editor.addLayout(self.editor_grid)
        self.inputs, self.originals, self.source_inputs = {}, {}, {}
        self.apply_edit_button = button("수정 반영", primary=True)
        self.revert_button = button("원래 값으로 되돌리기")
        self.exclude_button = button("이 행 제외")
        self.suggestion_button = button("추천값 적용")
        self.suggestion_button.clicked.connect(self.apply_suggestion)
        self.suggestion_button.hide()
        self.apply_edit_button.clicked.connect(self.apply_edits)
        self.revert_button.clicked.connect(self.revert_selected)
        self.exclude_button.clicked.connect(self.toggle_excluded)
        for widget in (self.apply_edit_button, self.revert_button, self.exclude_button):
            widget.setFixedHeight(40)
        editor.addWidget(_WrapRow([self.suggestion_button, self.apply_edit_button, self.revert_button, self.exclude_button], 190))
        review.body.addWidget(self.editor)
        issue_head = QHBoxLayout()
        issue_head.addWidget(label("검토 항목", 15, medium=True), 1)
        self.review_ack_button = button("선택 항목 확인 완료")
        self.review_ack_button.setToolTip("오류는 수정해야 합니다. 확인 완료는 검토 표시이며 업무 판정이나 원본을 바꾸지 않습니다.")
        self.review_ack_button.clicked.connect(self.toggle_issue_reviewed)
        issue_head.addWidget(self.review_ack_button)
        review.body.addLayout(issue_head)
        # 모델 열 순서(구분~Excel 열)는 유지하고, 마지막 ‘처리’ 열만 화면에서 ‘내용’ 바로 뒤로 옮긴다.
        self.issue_model = _IssueModel(("구분", "추출 행", "항목", "셀", "내용", "파일", "시트", "Excel 행", "Excel 열", "처리"))
        self.issue_table = _import_table(self.issue_model, "검토 항목", (96, 70, 110, 80, 380, 180, 110, 72, 72, 100))
        self.issue_table.horizontalHeader().moveSection(_IssueModel.action_column, 5)
        self.issue_table.setItemDelegateForColumn(0, _BadgeDelegate(lambda text: LABEL_TONES.get(text, "neutral"),
                                                                    self.issue_table))
        self.issue_table.setItemDelegateForColumn(_IssueModel.action_column, _BadgeDelegate(
            lambda text: "success" if text == "확인 완료" else "danger" if text == "수정 필요" else "neutral", self.issue_table))
        self.issue_table.clicked.connect(self._activate_issue)
        self.issue_table.activated.connect(self._activate_issue)
        self.issue_table.selectionModel().currentChanged.connect(lambda *_: self._sync_review_action())
        review.body.addWidget(self.issue_table)
        body.addWidget(review)

        save = _section("4. 변경분 확인 · 저장")
        self.step_cards.append(save)
        self.save_summary = label("", 14, medium=True, wrap=True)
        save.body.addWidget(self.save_summary)
        self.save_note = label("", 13, color=SECONDARY, wrap=True)
        save.body.addWidget(self.save_note)
        self.comparison_card = ComparisonCard()
        save.body.addWidget(self.comparison_card)
        self.save_button = button("검토 완료 · 저장", primary=True)
        self.candidate_button = button("번역표 후보 적용")
        self.candidate_button.setToolTip("원문 모델 표기를 데이터 파트 번역표의 등록 모델로 바꾸고 빈 설비군·계열·UI를 채웁니다. "
                                         "적용한 값은 사용자 수정으로 기록됩니다.")
        self.revalidate_button = button("다시 검사")
        self.requery_button = button("대시보드로 이동")
        self.requery_button.setToolTip("이동만 합니다. 조회 결과를 갱신하려면 조회 적용을 다시 눌러 주세요.")
        for widget in (self.save_button, self.candidate_button, self.revalidate_button, self.requery_button):
            widget.setFixedHeight(40)
        save.body.addWidget(_WrapRow([self.save_button, self.candidate_button, self.revalidate_button,
                                      self.requery_button], 200))
        self.result_label = label("", 13, wrap=True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        save.body.addWidget(self.result_label)
        save.body.addWidget(label("번역표: 원본의 모델 표기를 데이터 파트 번역표의 등록 모델로 바꾸는 규칙입니다. "
                                  "적용한 값은 사용자 수정으로 기록됩니다.", 12, color="#8b95a8", wrap=True))
        body.addWidget(save)
        body.addStretch()
        self.import_scroll.setWidget(self.import_content)
        self.deletion = ImportDeletePage(delete_gateway)
        self.delete_scroll.setWidget(self.deletion)
        self.workspace.addWidget(self.import_scroll)
        self.workspace.addWidget(self.delete_scroll)
        outer.addWidget(self.workspace, 1)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.deletion.busy_changed.connect(self._sync_tabs)
        self.deletion.cancel_requested.connect(lambda: self.tabs.setCurrentIndex(0))

        self.list_button.clicked.connect(self.load_workbooks)
        self.read_button.clicked.connect(self.read_selected)
        self.extract_button.clicked.connect(self.extract)
        self.common_apply_button.clicked.connect(self.apply_common)
        self.save_button.clicked.connect(self.save)
        self.candidate_button.clicked.connect(self.apply_model_candidates)
        self.revalidate_button.clicked.connect(self.revalidate)
        self.requery_button.clicked.connect(self.requery_requested.emit)
        self.workbook_combo.currentIndexChanged.connect(self._fill_sheets)
        self.kind_combo.currentIndexChanged.connect(self._kind_changed)
        self._paint_steps(self.status.tone)
        self._render()

    def configure_choices(self, *, dates=(), families=(), sites=()):
        """이미 읽은 선택지만 설정한다. 여기서 Excel이나 DB를 조회하지 않는다."""
        self.available_dates = tuple(dates)
        current = self.release_family.currentText()
        self.release_family.clear()
        self.release_family.addItem("")
        self.release_family.addItems(sorted(set(families)))
        self.release_family.setCurrentText(current)
        current_site = self.common_site.text()
        self.common_site_combo.clear()
        self.common_site_combo.addItem("")
        self.common_site_combo.addItems(sorted(set(sites)))
        self.common_site_combo.setCurrentText(current_site)

    def jump_to_step(self, index):
        if 0 <= index < len(self.step_cards):
            self.import_scroll.verticalScrollBar().setValue(max(0, self.step_cards[index].y() - 12))

    def _sync_tabs(self):
        self.tabs.setEnabled(not self.busy and not self.deletion.busy)

    def _tab_changed(self, index):
        if self.busy or self.deletion.busy:
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(self.workspace.currentIndex())
            self.tabs.blockSignals(False)
            return
        self.workspace.setCurrentIndex(index)
        self.steps.setVisible(index == 0)
        if index == 1 and self.deletion.catalog is None:
            self.deletion.reload()

    def _fit_height(self):
        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if parent is None:
            return
        viewport = parent.viewport()
        if self._outer_viewport is not viewport:
            self._outer_viewport = viewport
            viewport.installEventFilter(self)
        top = self.mapTo(parent.widget(), QPoint()).y()
        height = max(360, viewport.height() - top - 28)
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)

    def eventFilter(self, watched, event):
        if watched is self._outer_viewport and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._fit_height)
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_height)

    def open_data_state(self):
        self.data_state_dialog = ImportDataStateDialog(self.available_dates, self)
        self.data_state_dialog.date_selected.connect(self.date_selected.emit)
        self.data_state_dialog.import_requested.connect(lambda: self.tabs.setCurrentIndex(0))
        self.data_state_dialog.exec()

    def apply_release_family(self):
        if self.draft is None or self.kind is not ExtractKind.RELEASE_NOTE or self.busy or self.locked:
            return False
        value = self.release_family.currentText().strip()
        if not value:
            return False
        draft = self.draft
        for row in draft.active_rows:
            if not row.value("family_name"):
                draft = edit_value(draft, row.row_id, "family_name", value)
        if draft == self.draft:
            return False
        self.draft = draft
        self._changed()
        if self.selected_row_id is not None:
            self.select_row(self.selected_row_id)
        return True

    def _refresh_comparison(self):
        self.comparison_request.cancel()
        self._comparison_context = None
        if self.draft is None or self.kind is not ExtractKind.EQUIPMENT_STATUS or self.comparison_loader is None:
            self.comparison_card.show_comparison(None)
            return
        self._comparison_context = (self.generation, self.draft)
        self.comparison_card.show_comparison(None, message="같은 범위의 직전 저장본과 비교하고 있습니다…")
        captured, loader = deepcopy(self.draft), self.comparison_loader
        self.comparison_request.start(BackgroundRead(lambda: loader(captured)))
        self._render()

    def _comparison_completed(self, result, error):
        context, self._comparison_context = self._comparison_context, None
        if context is None or context[0] != self.generation or context[1] is not self.draft:
            return
        if error is not None or result is not None and not isinstance(result, ImportComparison):
            self.comparison_card.show_comparison(None, message="비교 자료를 읽지 못했습니다. ‘다시 검사’로 재시도할 수 있습니다.")
        else:
            self.comparison_card.show_comparison(result)
        self._render()

    def apply_suggestion(self):
        """구분자 오타 후보만 사용자의 클릭으로 입력한다. 실제 반영·검증은 기존 경로를 따른다."""
        if self.busy or self.locked or not self.suggestion_button.property("suggestion"):
            return False
        self.inputs["version_raw"].setText(self.suggestion_button.property("suggestion"))
        self.inputs["version_raw"].setModified(True)
        return self.apply_edits()

    # --- 상태 표시 ---------------------------------------------------------

    def _set_status(self, text, tone, notice=None):
        self.status.setText(text)
        self.status.set_tone(tone)
        self._paint_steps(tone)
        if notice is not None:
            self.notice.setText(notice)

    def _paint_steps(self, tone):
        """단계 고정 띠의 공통 테두리. 실제 진행 상태는 단계별로만 표시한다."""
        self.steps.setStyleSheet("QFrame#importSteps {background:white;border:1px solid #dee0e5;border-radius:8px;}")

    @property
    def kind(self):
        return ExtractKind(self.kind_combo.currentData())

    @property
    def extraction_label(self):
        """화면에서 사용하는 추출 공급자를 구분해 안내한다.

        return
        - 실제 연결 또는 명시적으로 주입한 예제·시험 공급자 안내.
        """
        if self.extractor is example_extractor:
            return "AI 예제 응답"
        method = "AI 원본 대조 추출" if self.extractor is not None else "설정된 AI API · 원본 대조 추출"
        return f"정해진 양식은 규칙 추출 · 그 밖은 {method}" if self.rule_extractor is not None else method

    @property
    def busy(self):
        return self.state in ("reading", "extracting", "validating", "saving") or self.comparison_request.busy

    @property
    def locked(self):
        """저장했거나 저장 여부를 모르는 초안은 다시 저장·수정하지 않는다."""
        return self.state in ("saving", "saved", "unknown")

    def _render(self):
        draft = self.draft
        self.list_button.setEnabled(not self.busy)
        self.read_button.setEnabled(not self.busy and self.sheet_combo.currentIndex() >= 0)
        self.extract_button.setEnabled(not self.busy and self.snapshot is not None and not self.locked)
        for combo in (self.workbook_combo, self.sheet_combo, self.kind_combo):
            combo.setEnabled(not self.busy)
        rows, ids, colors = [], [], {}
        if draft is not None:
            equipment = draft.kind is ExtractKind.EQUIPMENT_STATUS
            headings = ("원본 행" if equipment else "행", "상태", *(FIELD_LABELS[f] for f in draft.fields),
                        *(("출처 셀",) if equipment else ()))
            if self.row_model.headings != headings:
                self.row_model.headings = headings
                widths = (80, 120, *(120 for _ in draft.fields), *((100,) if equipment else ()))
                for index, width in enumerate(widths):
                    self.row_table.setColumnWidth(index, width)
                self.row_table.base_widths = widths
            error_fields = {(i.row_id, i.field) for i in self.issues if i.level is IssueLevel.ERROR and i.field}
            origins = {}
            origin_counts = Counter()
            for row in draft.rows:
                address = row.sources.get("equipment_code") or row.sources.get("version_raw")
                match = re.fullmatch(r"\$?[A-Za-z]+\$?([1-9][0-9]*)", address or "")
                origins[row.row_id] = match[1] if match else None
                if match:
                    origin_counts[match[1]] += 1
            origin_seen = Counter()
            for line, row in enumerate(draft.rows):
                counts = [sum(i.row_id == row.row_id and i.level is level
                              and (level is IssueLevel.ERROR or self._issue_key(i) not in self.reviewed_issues) for i in self.issues)
                          for level in (IssueLevel.ERROR, IssueLevel.REVIEW)]
                checked = any(i.row_id == row.row_id and i.level is IssueLevel.REVIEW
                              and self._issue_key(i) in self.reviewed_issues for i in self.issues)
                state = "제외" if row.excluded else " · ".join(
                    part for part in (f"오류 {counts[0]}" if counts[0] else "", f"확인 {counts[1]}" if counts[1] else "",
                                      "수정됨" if row.edits else "") if part) or ("확인 완료" if checked else "정상")
                origin = origins[row.row_id]
                display_id = f"미확인 (추출 {row.row_id})" if equipment else str(row.row_id)
                if equipment and origin:
                    origin_seen[origin] += 1
                    suffix = chr(96 + origin_seen[origin]) if origin_seen[origin] <= 26 else str(origin_seen[origin])
                    display_id = origin + (f"-{suffix}" if origin_counts[origin] > 1 else "")
                rows.append((display_id, state, *(self._cell(row, f) for f in draft.fields),
                             *((row.sources.get("version_raw") or "미확인",) if equipment else ())))
                ids.append(row.row_id)
                for column, name in enumerate(draft.fields, start=2):
                    if (row.row_id, name) in error_fields:
                        colors[line, column] = DANGER_INK
                    elif name in row.edits:
                        colors[line, column] = PRIMARY
        self.row_model.text_colors = colors
        self.row_model.set_rows(rows, ids)
        _fit(self.row_table)
        self.row_table.setVisible(bool(rows))
        self.row_empty.setVisible(not rows)
        self.row_empty.setText(f"시트를 읽은 뒤 {self.extract_button.text()}을 눌러 주세요." if draft is None else "추출된 행이 없습니다.")
        if self.selected_row_id in ids:
            self.row_table.selectRow(ids.index(self.selected_row_id))
        self.editor.setVisible(draft is not None and self.selected_row_id in ids)
        self.common_box.setVisible(self.kind is ExtractKind.EQUIPMENT_STATUS)
        self.release_context.setVisible(self.kind is ExtractKind.RELEASE_NOTE)
        self.comparison_card.setVisible(self.kind is ExtractKind.EQUIPMENT_STATUS)
        for widget in (self.common_date, self.common_site, self.common_site_combo, self.common_area, self.common_overwrite, self.release_family):
            widget.setEnabled(not self.busy and not self.locked)
        for widget in (self.common_apply_button, self.family_apply_button):
            widget.setEnabled(draft is not None and not self.busy and not self.locked)
        self.issue_model.levels = tuple(i.level for i in self.issues)
        self.issue_model.reviewed = {index for index, issue in enumerate(self.issues) if self._issue_key(issue) in self.reviewed_issues}
        self.issue_model.set_rows(tuple((ISSUE_LABELS[i.level], str(i.row_id) if i.row_id else "전체",
                                         FIELD_LABELS.get(i.field, "-") if i.field else "-", i.address or "위치 확인 필요", i.message,
                                         i.workbook_name or (draft.workbook_name if draft else "-"),
                                         i.sheet_name or (draft.sheet.sheet_name if draft else "-"),
                                         str(i.excel_position[0]) if i.excel_position else "미확인",
                                         str(i.excel_position[1]) if i.excel_position else "미확인")
                                        for i in self.issues))
        _fit(self.issue_table)
        self._sync_review_action()
        self._mark_grid()
        editable = draft is not None and self.selected_row_id in ids and not self.busy and not self.locked
        for field in self.inputs.values():
            field.setReadOnly(self.busy or self.locked)
        for name, field in self.source_inputs.items():
            row = draft.row(self.selected_row_id) if draft and self.selected_row_id is not None else None
            field.setReadOnly(self.busy or self.locked or bool(row and row.sources.get(name)))
        for widget in (self.apply_edit_button, self.revert_button, self.exclude_button):
            widget.setEnabled(editable)
        # 읽기 전용 여부를 정한 뒤 스타일을 다시 적용해 잠긴 입력칸이 회색으로 보이게 한다.
        self._style_editor(draft.row(self.selected_row_id) if draft is not None and self.selected_row_id in ids else None)
        self._render_save()
        self._sync_tabs()
        stage = (4 if self.state == "saved" and self.receipt else 3 if self.state == "saving" else
                 3 if draft is not None and not has_errors(self.issues) and not self.busy else
                 2 if draft is not None or self.state in {"extracting", "validating"} else
                 1 if self.snapshot is not None else 0)
        self.progress_steps.set_stage(stage)
        self.saved_banner.setVisible(self.state == "saved" and self.receipt is not None)
        if self.receipt is not None:
            self.saved_caption.setText(f"저장 완료 · {self.workbook_name or ''} · "
                                       f"{self.receipt.row_count}건 · {self.receipt.target_label}")

    def _mark_grid(self):
        """검토 항목이 가리키는 원본 셀을 미리보기에 표시한다. 오류가 확인 필요보다 우선한다."""
        marks = {}
        if self.snapshot is not None:
            for issue in self.issues:
                if not issue.address or issue.level not in (IssueLevel.ERROR, IssueLevel.REVIEW):
                    continue
                try:
                    row, col = self.grid_model.position(issue.address.split(":")[0])
                except ValueError:
                    continue
                if 0 <= row < self.grid_model.rowCount() and 0 <= col < self.grid_model.columnCount():
                    address = self.grid_model.address(row, col)
                    if marks.get(address) is not IssueLevel.ERROR:
                        marks[address] = issue.level
        self.grid_model.set_marks(marks)

    def _style_editor(self, row):
        """선택 행 입력칸을 검토 상태에 맞춰 표시한다. 오류 칸은 빨간 테두리와 오류 문구, 수정한 칸은 파란 테두리.

        param
        - row: 현재 편집 중인 행. 없으면 None.
        """
        self.suggestion_button.hide()
        self.suggestion_button.setProperty("suggestion", None)
        if row is None:
            return
        raw = row.value("version_raw") or (row.original.get("version_raw") if "version_raw" not in row.edits else "") or ""
        if isinstance(raw, str) and re.fullmatch(r"v[0-9]+(?:\.[0-9]+)+,[0-9]+(?:_[A-Za-z0-9]+)?", raw):
            suggested = raw.replace(",", ".")
            self.suggestion_button.setText(f"추천값 {suggested} 적용")
            self.suggestion_button.setProperty("suggestion", suggested)
            self.suggestion_button.setToolTip("쉼표 구분자를 점으로 바꾸는 후보입니다. 원본과 대조한 뒤 선택하세요.")
            self.suggestion_button.setVisible(True)
            self.suggestion_button.setEnabled(not self.busy and not self.locked and not row.excluded)
        for name, field in self.inputs.items():
            messages = [i.message for i in self.issues
                        if i.row_id == row.row_id and i.field == name and i.level is IssueLevel.ERROR]
            border = TONES["danger"][2] if messages else PRIMARY if name in row.edits else FIELD_LINE
            ink = DANGER_INK if messages else INK
            field.setStyleSheet(
                f"QLineEdit {{background:white;color:{ink};border:1px solid {border};border-radius:8px;padding:0 12px;}}"
                f"QLineEdit:focus {{border:2px solid {PRIMARY};}}"
                f"QLineEdit[readOnly=\"true\"] {{background:{SOFT};}}")
            caption = self.originals.get(name)
            if caption is not None:
                base = caption.property("baseText") or ""
                caption.setText(base + "".join(f" · {message}" for message in messages))
                caption.setStyleSheet(f"color:{DANGER_INK if messages else SECONDARY};")

    @staticmethod
    def _cell(row, name):
        value = row.value(name)
        text = value if value is not None else "(빈 값)"
        return f"{text}\n(수정됨 · AI: {row.original.get(name) or '빈 값'})" if name in row.edits else text

    def _render_save(self):
        errors = sum(i.level is IssueLevel.ERROR for i in self.issues)
        reviews = sum(i.level is IssueLevel.REVIEW for i in self.issues)
        acknowledged = sum(i.level is IssueLevel.REVIEW and self._issue_key(i) in self.reviewed_issues for i in self.issues)
        self.save_button.setEnabled(self.draft is not None and not errors and not self.busy and not self.locked
                                    and not self.has_editor_changes())
        self.save_button.setText("저장됨" if self.state == "saved" and self.receipt else "검토 완료 · 저장")
        self.revalidate_button.setEnabled(self.draft is not None and not self.busy and not self.locked)
        candidates = model_candidate_rows(self.draft) if self.draft is not None else {}
        self.candidate_button.setText(f"번역표 후보 적용 ({len(candidates)}행)" if candidates else "번역표 후보 적용")
        self.candidate_button.setEnabled(bool(candidates) and not self.busy and not self.locked)
        self.requery_button.setVisible(self.state == "saved")
        if self.draft is None:
            self.save_summary.setText("AI 추출 결과를 검토한 뒤 저장할 수 있습니다.")
            self.save_note.setText("")
        else:
            active = len(self.draft.active_rows)
            excluded = len(self.draft.rows) - active
            if self.kind is ExtractKind.EQUIPMENT_STATUS:
                sources = [row.sources.get("equipment_code") for row in self.draft.active_rows]
                physical = len(set(sources)) if sources and all(sources) else None
                count = f"원본 {physical}행 (SW {active}건)" if physical is not None else f"추출 {active}건"
            else:
                count = f"{active}행"
            self.save_summary.setText(f"저장 대상 {count} · 제외 {excluded}추출 건 · 오류 {errors}건 · 확인 필요 {reviews - acknowledged}건"
                                      + (f" · 검토 확인 {acknowledged}건" if acknowledged else ""))
            self.save_note.setText(
                "필수값 오류가 남으면 묶음 전체를 저장하지 않습니다. 값과 원본 위치를 보완하고 다시 검사해 주세요." if errors else
                f"저장 위치: {self.saver.target_label}")
        self.save_note.setStyleSheet(f"color:{DANGER_INK if errors else SECONDARY};")
        self.save_note.setVisible(bool(self.save_note.text()))

    def apply_model_candidates(self):
        """번역표 후보를 확인창에서 승인한 경우에만 적용하고 다시 검증한다.

        return
        - 적용했으면 True. 후보가 없거나 취소하면 False.
        """
        if self.draft is None or self.busy or self.locked:
            return False
        rows = model_candidate_rows(self.draft)
        if not rows:
            return False
        notes = candidate_notes(self.draft)
        text = (f"{len(rows)}행의 모델 표기를 데이터 파트 번역표의 등록 모델로 바꾸고 빈 설비군·계열·UI를 채웁니다.\n"
                "원문은 그대로 남고 바뀐 값은 사용자 수정으로 기록됩니다."
                + ("\n\n확인할 점:\n" + "\n".join(f"- {note}" for note in notes) if notes else "")
                + "\n\n적용할까요?")
        if not self.confirm(text):
            return False
        self.draft, count = apply_model_candidates(self.draft)
        self._changed()
        self._set_status("번역표 후보 적용", "info", f"{count}행에 번역표 후보를 적용했습니다. 검토 항목을 확인해 주세요.")
        if self.selected_row_id is not None:
            self.select_row(self.selected_row_id)
        return True

    # --- C-1 Excel 선택·미리보기 ----------------------------------------------

    def load_workbooks(self):
        """열린 통합문서 목록을 갱신하고 파일명·프로세스·전체 경로로 기존 선택을 유지한다.

        return
        - 선택 가능한 통합문서가 있으면 True.
        """
        if self.busy:
            return False
        try:
            with warnings.catch_warnings(record=True) as notices:
                warnings.simplefilter("always", ExcelDiscoveryWarning)
                self.workbooks = tuple(self.excel.list_open_workbooks())
        except ExcelReadError as exc:
            self.workbooks = ()
            self._set_status("Excel 연결 실패", "danger", str(exc))
        except Exception:
            self.workbooks = ()
            self._set_status("Excel 연결 실패", "danger", "Excel에서 통합문서 목록을 읽지 못했습니다. Excel 상태를 확인하고 다시 시도하세요.")
        else:
            messages = [str(notice.message) for notice in notices
                        if issubclass(notice.category, ExcelDiscoveryWarning)]
            self._set_status(f"열린 통합문서 {len(self.workbooks)}개", "warning" if messages else "info",
                             "통합문서와 시트를 고른 뒤 시트 읽기를 눌러 주세요."
                             + ("\n" + "\n".join(messages) if messages else ""))
        previous = self.workbook_combo.currentData()
        self.workbook_combo.blockSignals(True)
        self.workbook_combo.clear()
        names = Counter(book.name for book in self.workbooks)
        for book in self.workbooks:
            text = f"{book.name} · Excel {book.app_pid} · {book.full_name}" if names[book.name] > 1 else book.name
            self.workbook_combo.addItem(text, _workbook_key(book))
            self.workbook_combo.setItemData(
                self.workbook_combo.count() - 1, f"{book.full_name}\nExcel {book.app_pid}", Qt.ItemDataRole.ToolTipRole,
            )
        index = self.workbook_combo.findData(previous)
        self.workbook_combo.setCurrentIndex(index if index >= 0 else 0 if self.workbooks else -1)
        self.workbook_combo.blockSignals(False)
        self._fill_sheets()
        return bool(self.workbooks)

    def _selected_workbook(self):
        """현재 선택 키와 일치하는 통합문서 정보를 찾는다.

        return
        - 선택 목록의 WorkbookInfo. 일치하는 선택이 없으면 None.
        """
        key = self.workbook_combo.currentData()
        return next((book for book in self.workbooks if _workbook_key(book) == key), None)

    def _fill_sheets(self, *_):
        """파일명뿐 아니라 선택한 Excel 창과 경로가 일치하는 시트 목록을 표시한다."""
        previous = self.sheet_combo.currentData()
        self.sheet_combo.clear()
        book = self._selected_workbook()
        for name in book.sheet_names if book else ():
            self.sheet_combo.addItem(name, name)
        index = self.sheet_combo.findData(previous)
        if index >= 0:
            self.sheet_combo.setCurrentIndex(index)
        self._render()

    def _reset_draft(self):
        self.comparison_request.cancel()
        self._comparison_context = None
        self.comparison_card.show_comparison(None)
        self.review_request.cancel()
        self._review_context = None
        self.extraction_request.cancel()
        self._extraction_context = None
        self.release_request.cancel()
        self._release_context = None
        self.generation += 1
        self.draft, self.issues, self.selected_row_id = None, (), None
        self.reviewed_issues.clear()
        self.state, self.request_id, self.receipt = "idle", None, None
        self.result_label.setText("")
        self.source_badge.setText("AI 추출 전")
        self.source_badge.set_tone("neutral")

    def read_selected(self):
        """선택 정보를 고정해 해당 통합문서의 시트 읽기를 예약한다.

        return
        - 선택이 있고 다른 작업 중이 아니어서 읽기를 예약하면 True.
        """
        workbook, sheet = self._selected_workbook(), self.sheet_combo.currentData()
        if self.busy or workbook is None or sheet is None:
            return False
        self._reset_draft()
        self.state = "reading"
        self._set_status("시트 읽는 중", "info", f"{workbook.name} · {sheet}")
        self._render()
        generation = self.generation
        QTimer.singleShot(0, lambda: self._read(generation, workbook, sheet))
        return True

    def _read(self, generation, workbook, sheet):
        """예약 당시 선택한 Excel 프로세스와 전체 파일명으로 읽는다.

        param
        - generation: 이전 화면 요청과 구분하는 세대 번호.
        - workbook: 선택 목록에서 고정한 WorkbookInfo.
        - sheet: 선택한 원본 시트 이름.
        """
        if generation != self.generation:
            return
        self.state = "idle"
        try:
            data = self.excel.read_sheet(workbook.name, sheet, app_pid=workbook.app_pid, full_name=workbook.full_name)
            snapshot = snapshot_sheet(data)
        except ExcelReadError as exc:
            self.snapshot = None
            self._set_status("시트 읽기 실패", "danger", str(exc))
        except Exception:
            self.snapshot = None
            self._set_status("시트 읽기 실패", "danger", "시트를 읽지 못했습니다. Excel에서 파일이 열려 있는지 확인하고 다시 시도하세요.")
        else:
            self.snapshot, self.workbook_name = snapshot, data.workbook_name
            self.workbook_id = _workbook_key(workbook)
            self._set_status("원본 미리보기 · 읽기 전용", "info",
                             f"{self.workbook_name} · {snapshot.sheet_name} · 읽은 시점의 값입니다. Excel에서 고치면 다시 읽어 주세요.")
        self._show_snapshot()
        self._render()

    def _show_snapshot(self):
        snapshot = self.snapshot
        self.grid_model.set_snapshot(snapshot)
        self.grid.clearSpans()
        self.cell_label.setText("")
        # 작은 시트는 빈 영역 없이, 큰 시트는 최대 300px 안에서 스크롤한다.
        self.grid.setFixedHeight(max(120, min(300, 26 + 28 * self.grid_model.rowCount() + 14)))
        if snapshot is None:
            self.preview_info.setText("표시할 원본이 없습니다.")
            return
        for merged in snapshot.merged:
            (top, left), (bottom, right) = (self.grid_model.position(part) for part in merged.split(":"))
            if 0 <= top < self.grid_model.rowCount() and 0 <= left < self.grid_model.columnCount():
                self.grid.setSpan(top, left, bottom - top + 1, right - left + 1)
        end = cell_address(snapshot.first_row + max(snapshot.row_count, 1) - 1, snapshot.first_col + max(snapshot.col_count, 1) - 1)
        start = cell_address(snapshot.first_row, snapshot.first_col)
        nonempty = sum(value is not None and bool(cell_text(value).strip()) for value in snapshot.cells.values())
        self.preview_info.setText(
            f"{self.workbook_name} · {snapshot.sheet_name} · 사용 영역 {start}:{end} · 값이 있는 셀 {nonempty}개"
            + (f" · 병합 {len(snapshot.merged)}개" if snapshot.merged else "")
            + (f"\n앞의 {self.grid_model.max_rows}행·{self.grid_model.max_cols}열만 표시합니다. AI 추출과 저장 원본에는 전체 셀을 사용합니다."
               if self.grid_model.truncated else ""))

    def _show_cell(self, index):
        address = self.grid_model.address(index.row(), index.column())
        value = self.grid_model.snapshot.cells.get(address)
        self.cell_label.setText(f"선택 셀 {address} · 원본값 {cell_text(value) if value is not None else '(빈 칸)'}")

    def highlight_cell(self, address):
        if self.snapshot is None:
            return False
        try:
            row, col = self.grid_model.position(address.split(":")[0])
        except ValueError:
            return False
        if not (0 <= row < self.grid_model.rowCount() and 0 <= col < self.grid_model.columnCount()):
            return False
        self.grid_model.highlight = self.grid_model.address(row, col)
        index = self.grid_model.index(row, col)
        self.grid_model.dataChanged.emit(self.grid_model.index(0, 0), self.grid_model.index(
            self.grid_model.rowCount() - 1, self.grid_model.columnCount() - 1))
        self.grid.scrollTo(index)
        self.grid.setCurrentIndex(index)
        self._show_cell(index)
        return True

    # --- C-2 추출 결과 검토·수정 -----------------------------------------------

    def _kind_changed(self, *_):
        if self.draft is not None and self.draft.kind is not self.kind and self.state == "unknown":
            self.kind_combo.blockSignals(True)
            self.kind_combo.setCurrentIndex(self.kind_combo.findData(self.draft.kind.value))
            self.kind_combo.blockSignals(False)
            self._set_status("저장 결과 확인 필요", "warning", "저장 결과가 확인되지 않아 자료 종류를 바꾸지 않았습니다.")
        elif self.draft is not None and self.draft.kind is not self.kind:
            self._reset_draft()
            self._set_status("자료 종류 변경 · 다시 추출 필요", "warning", "이전 추출 결과는 다른 종류라 지웠습니다.")
        self._render()

    def extract(self):
        if self.busy or self.snapshot is None or self.locked:
            return False
        self._reset_draft()
        self.state = "extracting"
        count = sum(value is not None and bool(cell_text(value).strip()) for value in self.snapshot.cells.values())
        self._set_status("추출 중", "info",
                         f"{self.snapshot.sheet_name}을(를) 정해진 양식이면 규칙으로 바로 읽고, 아니면 값이 있는 셀 {count}개를 AI에 보냅니다."
                         if self.rule_extractor is not None else f"{self.snapshot.sheet_name}의 값이 있는 셀 {count}개를 보냅니다.")
        self._render()
        generation, snapshot, workbook = self.generation, self.snapshot, self.workbook_name
        workbook_id = self.workbook_id
        kind = self.kind
        QTimer.singleShot(0, lambda: self._extract(generation, workbook, snapshot, kind, workbook_id))
        return True

    def _extract(self, generation, workbook, snapshot, kind, workbook_id=None):
        """화면을 참조하지 않는 워커에서 추출과 원본 검증을 시작한다.

        param
        - generation, workbook, snapshot, kind, workbook_id: 추출 예약 당시의 선택 정보.
        """
        if generation != self.generation or snapshot is not self.snapshot or self.state != "extracting" or kind is not self.kind:
            return
        self._extraction_context = (generation, workbook, snapshot, kind, workbook_id)
        captured = deepcopy(snapshot)
        extractor, sheet_extractor, rules = self.extractor, self.sheet_extractor, self.rule_extractor
        self.extraction_request.start(BackgroundRead(lambda: extract_for_review(
            kind, workbook, captured, extractor=extractor, sheet_extractor=sheet_extractor, rules=rules)))

    def _extraction_completed(self, result, error):
        """현재 파일·시트의 완료 결과만 화면에 반영한다.

        param
        - result, error: 원본 검증을 마친 추출 결과 또는 작업 예외.
        """
        context, self._extraction_context = self._extraction_context, None
        if context is None:
            return
        generation, workbook, snapshot, kind, workbook_id = context
        if generation != self.generation or snapshot is not self.snapshot or self.state != "extracting":
            return
        self.state = "idle"
        draft = None
        if error is None:
            try:
                if result.ok and isinstance(result.data, ExtractionReview):
                    if result.data.workbook_name != workbook or result.data.sheet_name != snapshot.sheet_name:
                        raise ValueError("추출 결과의 원본 파일·시트가 다릅니다.")
                    draft = draft_from_review(result.data, source=result.source, workbook_id=workbook_id)
                    draft = replace(draft, sheet=deepcopy(snapshot))
            except Exception:
                result, draft = None, None
        if draft is None:
            self._set_status("AI 추출 실패", "danger",
                             str(error) if isinstance(error, AIConfigError) else
                             result.message if result is not None else "AI 추출 결과를 처리하지 못했습니다. 다시 시도하세요.")
            self._render()
            return
        self.draft = draft
        # 사용자가 1단계에서 명시한 값만 빈 칸에 채운다. 원문/기존값은 자동 덮어쓰지 않는다.
        context = ({"base_date": self.common_date.text().strip(), "site": self.common_site.text().strip(),
                    "production_area": self.common_area.currentData() or ""} if kind is ExtractKind.EQUIPMENT_STATUS else
                   {"family_name": self.release_family.currentText().strip()})
        for row in draft.active_rows:
            for name, value in context.items():
                if value and not row.value(name):
                    self.draft = edit_value(self.draft, row.row_id, name, value)
        self.source_badge.setText(SOURCE_LABELS.get(draft.ai_source, SOURCE_LABELS[None]))
        self.source_badge.set_tone("info" if draft.ai_source in {ResponseSource.REAL, ResponseSource.RULE} else "warning")
        self._changed(initial=True)
        if draft.rows:
            self.select_row(draft.rows[0].row_id)

    def _changed(self, *, initial=False):
        self.reviewed_issues.clear()
        self.comparison_request.cancel()
        self._comparison_context = None
        self.comparison_card.show_comparison(None)
        self.draft = refresh_hpa_coverage(self.draft)
        # 내용이 바뀌면 새 저장 요청이다. 실패 후 그대로 재시도할 때만 같은 요청 번호를 쓴다.
        self.request_id = uuid.uuid4().hex
        if self.state == "failed":
            self.state = "idle"
            self.result_label.setText("")
        if isinstance(self.saver, DatabaseImportSaver):
            self.review_request.cancel()
            self._review_context = (self.generation, self.draft, initial)
            self.reference = None
            self.state = "validating"
            self.issues = (Issue(IssueLevel.ERROR, "기존 저장 자료를 검사하고 있습니다. 완료 후 저장할 수 있습니다.",
                                 code="REFERENCE_CHECK_PENDING"),)
            self._set_status("기존 저장 자료 검사 중", "info", "현재 검토값과 DB의 기존값을 확인하고 있습니다.")
            self._render()
            saver, draft = self.saver, deepcopy(self.draft)
            self.review_request.start(BackgroundRead(lambda: saver.load_reference((draft,))))
            return
        self._finish_review(initial=initial)

    def _review_completed(self, reference, error):
        """현재 초안의 조회 결과만 GUI 스레드에서 검토 항목에 반영한다."""
        context, self._review_context = self._review_context, None
        if context is None:
            return
        generation, draft, initial = context
        if generation != self.generation or draft is not self.draft or self.state != "validating":
            return
        if error is None and not isinstance(reference, ReferenceData):
            error = TypeError("검증 기준 자료 형식을 확인해야 합니다.")
        self.state = "idle"
        self.reference = reference if error is None else None
        self._finish_review(initial=initial, error=error)

    def _finish_review(self, *, initial=False, error=None):
        """DB 또는 메모리 기준 자료로 현재 검토값 전체를 검사한다."""
        self.issues = validate(self.draft, self._validation_reference())
        if error is not None:
            self.issues = (Issue(
                IssueLevel.ERROR,
                "기존 저장 자료를 읽지 못해 저장을 보류했습니다. DB 연결을 확인한 뒤 ‘다시 검사’를 눌러 주세요.",
                workbook_name=self.draft.workbook_name, sheet_name=self.draft.sheet.sheet_name,
                code="REFERENCE_LOAD_FAILED",
            ), *self.issues)
        errors = has_errors(self.issues)
        notice = ("기존 자료를 읽지 못했습니다. DB 연결을 확인한 뒤 ‘다시 검사’를 눌러 주세요." if error is not None else
                  ("규칙 추출 결과입니다(정해진 양식 · AI 미사용). " if self.draft is not None and self.draft.ai_source is ResponseSource.RULE
                   else "AI 추출 결과입니다. ") + "행을 선택해 값을 확인·수정하고 검토 항목을 처리해 주세요." if initial else
                  "현재 검토값의 검사를 마쳤습니다. 변경 내용과 확인 항목을 검토해 주세요.")
        self._set_status("검토 필요 · 오류 있음" if errors else "검토 중 · 저장 가능", "warning" if errors else "info",
                         notice)
        self._render()
        self._refresh_comparison()

    def revalidate(self):
        """반영된 검토값을 유지하며 최신 기존값 조회와 전체 검사를 다시 수행한다."""
        if self.draft is None or self.busy or self.locked:
            return False
        self._changed()
        return True

    def _cancel_review(self):
        """떠난 화면으로 늦은 응답을 전달하지 않고 현재 초안은 보존한다."""
        if self.state != "validating":
            return
        self.review_request.cancel()
        self.release_request.cancel()
        self._release_context = None
        self._review_context = None
        self.reference = None
        self.state = "idle"
        self.issues = (Issue(IssueLevel.ERROR, "기존 자료 검사를 취소했습니다. ‘다시 검사’ 후 저장해 주세요.",
                             code="REFERENCE_REVIEW_CANCELLED"),)
        self._set_status("기존 자료 검사 취소", "warning", "검토한 값은 유지됩니다. ‘다시 검사’를 눌러 주세요.")
        self._render()

    def _cancel_extraction(self):
        """앱을 닫을 때 진행 중인 추출 결과를 받지 않는다 (화면 이동·최소화로는 취소하지 않는다)."""
        if self.state != "extracting":
            return
        self.extraction_request.cancel()
        self._extraction_context = None
        self.state = "idle"
        self._set_status("AI 추출 취소", "warning", "화면을 떠나 추출 결과 수신을 취소했습니다. 다시 추출해 주세요.")
        self._render()

    def hideEvent(self, event):
        """화면이 숨겨져도 추출은 계속한다. 기존 자료 검사만 앱 안에서 다른 화면으로 옮길 때 다시 하게 한다.

        - 추출은 시트 읽기 때 떠 둔 원본 사본으로 하므로, 다른 화면에 다녀와도 결과가 바뀌지 않는다.
          돌아오면 결과가 보인다 (멘토 5차 전 확인: 추출 중 창을 바꾸면 취소되던 문제).
        - 창 최소화·다른 프로그램 전환처럼 운영체제가 보낸 숨김(spontaneous)은 화면을 떠난 것이 아니다.
        - 기존 자료 검사(DB 대조)는 다른 화면에서 저장이 바뀔 수 있어, 앱 안 이동일 때만 취소하고 다시 검사하게 한다.
        """
        if not event.spontaneous():
            comparison_pending = self.comparison_request.busy
            self.comparison_request.cancel()
            self._comparison_context = None
            if comparison_pending:
                self.comparison_card.show_comparison(
                    None, message="직전 저장본 비교를 취소했습니다. ‘다시 검사’로 다시 확인해 주세요.")
            self._cancel_review()
            self._render()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.comparison_request.cancel()
        self._comparison_context = None
        self._cancel_extraction()
        self._cancel_review()
        super().closeEvent(event)

    def select_row(self, row_id):
        if self.draft is None:
            return False
        try:
            row = self.draft.row(row_id)
        except LookupError:
            return False
        self.selected_row_id = row_id
        while self.editor_grid.count():
            widget = self.editor_grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self.inputs, self.originals, self.source_inputs = {}, {}, {}
        box_style = (f"background:white;color:{INK};border:1px solid {FIELD_LINE};border-radius:8px;padding:0 10px;")
        for line, name in enumerate(self.draft.fields):
            title = label(FIELD_LABELS[name], 13, medium=True, wrap=True)
            title.setContentsMargins(0, 10, 0, 0)
            field = QLineEdit(row.value(name) or "")
            field.setAccessibleName(f"{row_id}행 {FIELD_LABELS[name]}")
            field.setFixedHeight(40)
            field.setFont(font(14, medium=True))
            field.setProperty("reviewValue", row.value(name) or "")
            field.setMinimumWidth(0)
            field.setReadOnly(self.busy or self.locked)
            title.setBuddy(field)
            address = row.sources.get(name)
            text = (f"AI 원본: {row.original.get(name) or '빈 값'} · 셀 {address or '위치 미확인'}"
                    + (" · 수정됨" if name in row.edits else ""))
            original = label(text, 11, color=SECONDARY, wrap=True)
            original.setProperty("baseText", text)
            original.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.editor_grid.addWidget(title, line * 2, 0, Qt.AlignmentFlag.AlignTop)
            self.editor_grid.addWidget(field, line * 2, 1)
            self.editor_grid.addWidget(original, line * 2 + 1, 1, 1, 2)
            self.inputs[name], self.originals[name] = field, original
            field.textChanged.connect(self._render_save)
            if name in {"equipment_code", "version_raw"}:
                source_field = QLineEdit(address or "")
                source_field.setAccessibleName(f"{row_id}행 {FIELD_LABELS[name]} 원본 셀")
                source_field.setProperty("reviewValue", address or "")
                source_field.setPlaceholderText("셀 · 예: C12")
                source_field.setToolTip("위치를 확인할 수 없을 때 원본에서 확인한 셀을 지정하세요. 원본 값은 바꾸지 않습니다.")
                source_field.setReadOnly(self.busy or self.locked or bool(address))
                source_field.setStyleSheet(f"QLineEdit {{{box_style}}} QLineEdit:focus {{border:2px solid {PRIMARY};}}")
                self.source_inputs[name] = source_field
                source_field.textChanged.connect(self._render_save)
                cell_box = source_field
            else:
                # 읽기 전용 원본 셀 주소 상자. 주소가 없으면 ‘미확인’으로만 표시한다.
                cell_box = QLabel(address or "미확인")
                cell_box.setTextFormat(Qt.TextFormat.PlainText)
                cell_box.setAccessibleName(f"{row_id}행 {FIELD_LABELS[name]} 원본 셀 {address or '미확인'}")
                cell_box.setStyleSheet(box_style + ("" if address else f"color:{SECONDARY};"))
            cell_box.setFont(font(13, medium=True, numeric=True))
            cell_box.setFixedSize(96, 40)
            self.editor_grid.addWidget(cell_box, line * 2, 2)
        address = row.sources.get("equipment_code") if self.kind is ExtractKind.EQUIPMENT_STATUS else row.sources.get("content")
        origin = re.fullmatch(r"\$?[A-Za-z]+\$?([1-9][0-9]*)", address or "")
        title = f"원본 {origin[1]}행 · 추출 {row_id}" if origin else f"추출 {row_id}행 · 원본 행 미확인"
        self.editor_title.setText(f"{title}{' (제외됨)' if row.excluded else ''} 값 확인 · 수정")
        self.exclude_button.setText("이 행 다시 포함" if row.excluded else "이 행 제외")
        for widget in (self.apply_edit_button, self.revert_button, self.exclude_button):
            widget.setEnabled(not self.busy and not self.locked)
        self._render()
        return True

    def apply_common(self):
        """시트 공통 정보(기준일·사업장·메모리/파운드리)를 설비 현황 초안의 모든 행에 적용한다.

        빈 행만 채우고, ‘덮어쓰기’를 켜면 기존 값도 바꾼다. 원본 추출값은 수정값과 따로 보존된다.

        return
        - 적용했으면 True. 초안이 없거나 입력이 없거나 날짜 형식이 틀리면 False.
        """
        if self.draft is None or self.busy or self.locked or self.kind is not ExtractKind.EQUIPMENT_STATUS:
            return False
        values = {"base_date": self.common_date.text().strip(), "site": self.common_site.text().strip(),
                  "production_area": self.common_area.currentData() or ""}
        values = {name: text for name, text in values.items() if text}
        if not values:
            self._set_status("공통 정보 없음", "warning", "기준일·사업장·메모리/파운드리 중 하나 이상을 입력해 주세요.")
            return False
        if "base_date" in values:
            try:
                parse_date(values["base_date"])
            except ValueError:
                self._set_status("기준일 형식 확인", "warning", "기준일은 YYYY-MM-DD 형식으로 입력해 주세요.")
                return False
        draft, changed = self.draft, 0
        overwrite = self.common_overwrite.isChecked()
        for row in draft.rows:
            if row.excluded:
                continue
            for name, text in values.items():
                if overwrite or not (row.value(name) or "").strip():
                    draft = edit_value(draft, row.row_id, name, text)
                    changed += 1
        self.draft = draft
        self._changed()
        self._set_status("공통 정보 적용", "info", f"{len(values)}개 항목을 {changed}칸에 적용했습니다. 검토 항목을 다시 확인해 주세요.")
        if self.selected_row_id is not None:
            self.select_row(self.selected_row_id)
        return True

    def apply_edits(self):
        """사용자가 바꾸거나 직접 편집한 칸만 검토 초안에 반영한다.

        return
        - 현재 값 또는 미해결 추출 오류의 보완 기록이 바뀌면 True. 변경이 없으면 False.
        """
        if self.draft is None or self.busy or self.locked or self.selected_row_id is None:
            return False
        draft = self.draft
        row = draft.row(self.selected_row_id)
        for name, field in self.inputs.items():
            # 다른 칸의 수정만으로 자동 공란에 남은 추출 오류가 해제되지 않게 한다.
            if field.isModified() or field.text() != (row.value(name) or ""):
                draft = edit_value(draft, self.selected_row_id, name, field.text())
        try:
            for name, field in self.source_inputs.items():
                if field.text().strip() and field.text().strip() != (row.sources.get(name) or ""):
                    draft = assign_source(draft, self.selected_row_id, name, field.text().strip())
        except ValueError as error:
            self._set_status("원본 위치 확인 필요", "warning", str(error))
            return False
        if draft == self.draft:
            return False
        self.draft = draft
        self._changed()
        self.select_row(self.selected_row_id)
        return True

    def has_editor_changes(self):
        """아직 ‘수정 반영’을 누르지 않은 입력이 있으면 저장에서 누락되지 않게 막는다."""
        if self.draft is None or self.selected_row_id is None:
            return False
        # 비교 기준은 편집창을 열었을 때의 값이다. 이미 반영된 초안의 비동기 재검토를
        # 사용자 미반영 입력으로 오인하지 않는다.
        return (any(field.text() != (field.property("reviewValue") or "") for field in self.inputs.values())
                or any(field.text().strip() != (field.property("reviewValue") or "") for field in self.source_inputs.values()))

    def revert_selected(self):
        if self.draft is None or self.busy or self.locked or self.selected_row_id is None:
            return False
        self.draft = revert_row(self.draft, self.selected_row_id)
        self._changed()
        self.select_row(self.selected_row_id)
        return True

    def toggle_excluded(self):
        if self.draft is None or self.busy or self.locked or self.selected_row_id is None:
            return False
        row = self.draft.row(self.selected_row_id)
        self.draft = set_excluded(self.draft, row.row_id, not row.excluded)
        self._changed()
        self.select_row(row.row_id)
        return True

    def open_issue(self, index):
        if not 0 <= index < len(self.issues):
            return
        issue = self.issues[index]
        if issue.row_id is not None:
            self.select_row(issue.row_id)
            if issue.field in self.inputs:
                self.inputs[issue.field].setFocus()
        if issue.address:
            self.highlight_cell(issue.address)

    @staticmethod
    def _issue_key(issue):
        return issue.code, issue.row_id, issue.field, issue.address, issue.message

    def _sync_review_action(self):
        index = self.issue_table.currentIndex().row()
        issue = self.issues[index] if 0 <= index < len(self.issues) else None
        self.review_ack_button.setEnabled(issue is not None and issue.level is not IssueLevel.ERROR
                                           and not self.busy and not self.locked)
        self.review_ack_button.setText("확인 취소" if issue and self._issue_key(issue) in self.reviewed_issues else "선택 항목 확인 완료")

    def _activate_issue(self, index):
        row, column = index.row(), index.column()
        if column == _IssueModel.action_column and 0 <= row < len(self.issues) and self.issues[row].level is not IssueLevel.ERROR:
            self.toggle_issue_reviewed(row)
            return
        self.open_issue(row)
        if 0 <= row < len(self.issues):
            self.issue_table.setCurrentIndex(self.issue_model.index(row, column))

    def toggle_issue_reviewed(self, index=None):
        """검토 표시만 토글한다. ERROR를 지우거나 저장 계약의 review_count를 변경하지 않는다."""
        if self.busy or self.locked:
            return False
        if not isinstance(index, int) or isinstance(index, bool):
            index = self.issue_table.currentIndex().row()
        if not 0 <= index < len(self.issues) or self.issues[index].level is IssueLevel.ERROR:
            return False
        key = self._issue_key(self.issues[index])
        self.reviewed_issues.remove(key) if key in self.reviewed_issues else self.reviewed_issues.add(key)
        self._render()
        self.issue_table.setCurrentIndex(self.issue_model.index(index, _IssueModel.action_column))
        return True

    # --- C-3 저장 ----------------------------------------------------------

    def _validation_reference(self):
        """릴리즈의 기존 번호 매칭은 교체 검토에서 처리하고 나머지 검증 자료는 유지한다.

        return
        - 현재 초안의 형식·필수값·모델·계열 검증 자료.
        """
        if (self.draft is not None and self.draft.kind is ExtractKind.RELEASE_NOTE
                and isinstance(self.saver, DatabaseImportSaver) and self.reference is not None):
            return replace(self.reference, stored_releases=())
        return self.reference

    def _ask(self, text):
        answer = QMessageBox.question(self, "가져오기 저장 확인", text,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def save(self):
        if self.draft is None or self.busy or self.locked or has_errors(self.issues):
            return False
        if self.has_editor_changes():
            self._set_status("수정 반영 필요", "warning", "입력한 값을 ‘수정 반영’한 뒤 저장해 주세요.")
            return False
        request = build_save_request(self.draft, self.request_id, self._validation_reference())
        merged = len(self.draft.active_rows) - len(request.rows)
        text = (f"통합문서: {request.workbook_name}\n시트: {request.raw_data['sheet']}\n"
                f"자료 종류: {KIND_LABELS[request.kind]}\n저장 행: {len(request.rows)}행"
                + (f" (정확한 중복 {merged}행 합침)" if merged else "")
                + f"\n확인 필요 항목(원본 검토 안내): {request.review_count}건 · 화면 검토 확인: {sum(i.level is IssueLevel.REVIEW and self._issue_key(i) in self.reviewed_issues for i in self.issues)}건"
                + f"\nAI 응답: {SOURCE_LABELS.get(request.ai_source, SOURCE_LABELS[None])}\n"
                f"저장 위치: {self.saver.target_label}\n\n원본 셀 값과 위치는 그대로 보존하고, 수정한 값은 따로 기록합니다.\n"
                + ("\n이 시트가 해당 계열의 전체 누적 릴리즈 노트인지 확인해 주세요.\n"
                   "기존 자료 전체를 교체하며, 새 자료에 없는 버전·Item은 제외됩니다.\n"
                   "개조형·Item 적용 범위와 포함·제외·분기 내용을 검토한 전체 노트가 맞습니까?\n"
                   if request.kind is ExtractKind.RELEASE_NOTE and isinstance(self.saver, DatabaseImportSaver) else "")
                + "저장할까요?")
        if not self.confirm(text):
            self._set_status("저장 취소 · 검토 계속", "info", "저장하지 않았습니다.")
            return False
        if isinstance(self.saver, DatabaseImportSaver) and request.kind is ExtractKind.RELEASE_NOTE:
            try:
                context = self.refresh_context_provider() if self.refresh_context_provider else None
            except Exception:
                self._set_status("목표 기준 확인 실패", "danger", "현재 조회의 목표 N을 확인한 뒤 다시 저장해 주세요.")
                return False
            request = replace(request, release_note_confirmed=True, filename_confirmed=True,
                              refresh_context=deepcopy(context))
            self._release_risk_decisions = set()
            self._begin_release_review(request)
            return True
        self._start_save(request)
        return True

    def _begin_release_review(self, request):
        """DB에서 전체 교체 후보를 비동기로 읽고 현재 초안과 요청을 연결한다.

        param
        - request: 파일명과 전체 노트 확인을 마친 불변 저장 요청.
        """
        self.state = "validating"
        self._release_context = (self.generation, self.draft, request)
        self._set_status("릴리즈 교체 검토 중", "info", "기존 자료의 매칭·수정값·Item 범위·위험 복원을 확인합니다.")
        self._render()
        saver, snapshot = self.saver, deepcopy(request)
        self.release_request.start(BackgroundRead(lambda: saver.preview_release_import(snapshot, snapshot.release_reviews)))

    def _release_review_completed(self, plans, error):
        """현재 검토 세대만 수락하고 명시 선택 후 저장하거나 검토를 계속한다.

        param
        - plans: 계열별 교체 검토 결과.
        - error: 읽기 실패. 성공이면 None.
        """
        context, self._release_context = self._release_context, None
        if context is None:
            return
        generation, draft, request = context
        if generation != self.generation or draft is not self.draft or self.state != "validating":
            return
        self.state = "idle"
        if error is not None:
            self._set_status("릴리즈 교체 검토 실패", "danger", "최신 자료를 다시 검사한 뒤 저장해 주세요.")
            self._render()
            return
        if not isinstance(plans, tuple):
            self._set_status("릴리즈 교체 검토 실패", "danger", "교체 검토 결과의 형식을 확인한 뒤 다시 저장해 주세요.")
            self._render()
            return
        selected, again = self.release_review_chooser(self, plans, request.release_reviews,
                                                     risk_decisions=self._release_risk_decisions)
        if selected is None:
            self._set_status("교체 취소 · 검토 계속", "info", "DB를 변경하지 않았습니다. 검토값은 유지됩니다.")
            self._render()
            return
        request = replace(request, release_reviews=selected)
        if again:
            self._begin_release_review(request)
        else:
            self._start_save(request)

    def _start_save(self, request):
        """확인·교체 검토를 마친 요청을 기존 저장 경로로 전달한다.

        param
        - request: 이번 화면에서 명시적으로 검토한 SaveRequest.
        """
        self.state = "saving"
        self._set_status("저장 중", "info", "저장이 끝날 때까지 다시 저장할 수 없습니다.")
        self.result_label.setText("")
        self._render()
        for widget in (self.apply_edit_button, self.revert_button, self.exclude_button):
            widget.setEnabled(False)
        QTimer.singleShot(0, lambda: self._save(request))

    def _save(self, request):
        try:
            receipt = self.saver.save(request)
        except Exception as exc:
            state, message = describe_save_error(exc)
            if state == "unknown":
                self.state = "unknown"
                self._set_status("저장 결과 확인 필요", "warning", "다시 저장하지 마세요. 연결 복구 후 저장된 내용을 확인해 주세요.")
            elif state == "duplicate":
                self.state = "saved"
                self._set_status("이미 저장됨", "info", message)
            else:
                self.state = "failed"
                self._set_status("저장 실패", "danger", "내용을 확인한 뒤 다시 저장할 수 있습니다.")
            self.result_label.setText(message)
        else:
            self.state, self.receipt = "saved", receipt
            self._set_status("저장 완료", "success", f"{receipt.target_label} · 입력 작업 {receipt.batch_id}")
            self.result_label.setText(
                f"입력 작업 {receipt.batch_id} · {receipt.row_count}행 · {receipt.saved_at:%Y-%m-%d %H:%M:%S}\n"
                "조회 화면은 자동으로 바뀌지 않습니다. 저장한 자료를 반영하려면 대시보드에서 ‘조회 적용’을 다시 눌러 주세요.\n"
                "다른 시트를 가져오려면 시트를 다시 읽어 주세요.")
            self.saved.emit(receipt)
        if self.selected_row_id is not None:
            self.select_row(self.selected_row_id)
        else:
            self._render()
