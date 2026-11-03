"""C-1~C-3 데이터 가져오기. 열린 Excel 선택·원본 미리보기 → AI 추출 검토·수정 → 확인 후 저장.

Excel 읽기·AI 추출·저장은 주입받은 객체에 요청한다. 화면은 SQL을 실행하지 않고 원본 파일을 바꾸지 않는다.
메뉴에 들어와도 Excel에 바로 연결하지 않고, 사용자가 불러오기를 눌렀을 때만 연결한다.
"""

from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
import uuid
import warnings

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox, QGridLayout, QHBoxLayout, QLineEdit, QMessageBox, QSizePolicy, QTableView, QVBoxLayout, QWidget,
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
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.import_demo import example_extractor
from setron.ui.import_extraction import LiveSheetExtractor, extract_for_review
from setron.features.excel_import.review_locations import assign_source, refresh_hpa_coverage
from setron.features.extraction.schemas import ExtractionReview
from setron.ui.query_panel import _WrapRow
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.release_import_review import choose_release_reviews
from setron.ui.theme import PRIMARY, SECONDARY, font
from setron.ui.versions import _field, _fit

SOURCE_LABELS = {
    ResponseSource.EXAMPLE: "예제 응답(EXAMPLE) · 실제 AI 결과 아님 · 시트 내용과 무관한 고정 응답",
    ResponseSource.REAL: "실제 AI API 응답(REAL)",
    None: "AI 응답 출처 미확인",
}


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

    def set_snapshot(self, snapshot):
        self.beginResetModel()
        self.snapshot, self.highlight = snapshot, None
        self.endResetModel()

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
        if role == Qt.ItemDataRole.BackgroundRole and address == self.highlight:
            return QColor("#fbe5a3")
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

    def __init__(self, excel=None, extractor=None, saver=None, *, reference=None, confirm=None,
                 refresh_context_provider=None, release_review_chooser=None, sheet_extractor=None):
        super().__init__()
        self.excel = excel if excel is not None else excel_reader
        self.extractor = extractor
        self.sheet_extractor = sheet_extractor or LiveSheetExtractor()
        self.saver = saver or MemoryImportSaver("메모리 저장소 · DB 미연결 · 조회 화면 자료에 반영되지 않음")
        self.reference = reference
        self.confirm = confirm or self._ask
        self.refresh_context_provider = refresh_context_provider
        self.release_review_chooser = release_review_chooser or choose_release_reviews
        self.workbooks, self.snapshot, self.workbook_name = (), None, None
        self.workbook_id = None
        self.draft, self.issues, self.selected_row_id = None, (), None
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
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)
        self.status = StatusBadge("Excel 미연결", "info")
        body.addWidget(self.status)
        self.notice = label("열린 통합문서 불러오기를 누르면 실행 중인 Excel에 연결합니다.", 13, color=SECONDARY, wrap=True)
        self.notice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body.addWidget(self.notice)

        select = Card()
        select.body.addWidget(label("1. Excel 통합문서 · 시트 선택", 20, medium=True))
        select.body.addWidget(label("사용자가 실행해 둔 데스크톱 Excel에서 값을 읽습니다. 파일 업로드·파일 선택은 지원하지 않으며 "
                                    "원본 파일을 수정하거나 복사하지 않습니다.", 13, color=SECONDARY, wrap=True))
        self.workbook_combo, self.sheet_combo, self.kind_combo = QComboBox(), QComboBox(), QComboBox()
        for widget, name in ((self.workbook_combo, "통합문서 선택"), (self.sheet_combo, "시트 선택"),
                             (self.kind_combo, "가져올 자료 종류")):
            widget.setAccessibleName(name)
            widget.setProperty("queryField", True)
            widget.setFixedHeight(38)
            widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            widget.setMinimumContentsLength(10)
        for kind in ExtractKind:
            self.kind_combo.addItem(KIND_LABELS[kind], kind.value)
        self.list_button = button("열린 통합문서 불러오기")
        self.read_button = button("시트 읽기", primary=True)
        select.body.addWidget(_WrapRow([_field("통합문서", self.workbook_combo), _field("시트", self.sheet_combo),
                                       _field("자료 종류", self.kind_combo)], 230, fields=True))
        select.body.addWidget(_WrapRow([self.list_button, self.read_button], 200))
        body.addWidget(select)

        preview = Card()
        preview.body.addWidget(label("2. 원본 미리보기", 20, medium=True))
        self.preview_info = label("시트를 읽으면 원본 셀 값과 주소가 표시됩니다.", 13, color=SECONDARY, wrap=True)
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
        self.grid.setStyleSheet("QTableView {background:white;gridline-color:#e9eef6;border:1px solid #e9eef6;"
                                "selection-background-color:#edf0fd;selection-color:#18243c;}"
                                "QHeaderView::section {background:#f7f9fc;color:#5b6880;border:0;padding:4px;}"
                                f"QTableView:focus {{border:1px solid {PRIMARY};}}")
        self.grid.clicked.connect(self._show_cell)
        preview.body.addWidget(self.grid)
        self.cell_label = label("", 13, color=SECONDARY, wrap=True)
        self.cell_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        preview.body.addWidget(self.cell_label)
        body.addWidget(preview)

        review = Card()
        review.body.addWidget(label("3. AI 추출 결과 검토 · 수정", 20, medium=True))
        self.extract_button = button("AI로 항목 추출", primary=True)
        self.source_badge = StatusBadge("AI 추출 전", "neutral")
        actions = QHBoxLayout()
        actions.addWidget(self.extract_button)
        actions.addWidget(self.source_badge)
        actions.addStretch()
        review.body.addLayout(actions)
        review.body.addWidget(label("AI 추출값은 원본과 따로 보관합니다. 수정한 값은 ‘수정됨’으로 표시되고 원본 값·셀 위치는 바뀌지 않습니다. "
                                    "AI 결과는 검토 없이 저장하지 않습니다.", 13, color=SECONDARY, wrap=True))
        # 시트 공통 정보: 교육용 현황표에는 기준일·사업장 셀이 없어 한 번 입력해 모든 행에 적용한다 (1차 점검 6번).
        common = QWidget()
        common_layout = QVBoxLayout(common)
        common_layout.setContentsMargins(0, 0, 0, 0)
        common_layout.setSpacing(6)
        common_layout.addWidget(label("시트 공통 정보 · 설비 현황표", 16, medium=True))
        common_layout.addWidget(label("기준일·사업장이 시트에 없으면 여기 입력하고 ‘모든 행에 적용’을 누르세요. 비어 있는 행만 채우고, "
                                      "메모리/파운드리를 비우면 라인에 따라 기본값(장소표)을 씁니다.", 13, color=SECONDARY, wrap=True))
        self.common_date, self.common_site = QLineEdit(), QLineEdit()
        self.common_date.setPlaceholderText("기준일 YYYY-MM-DD")
        self.common_site.setPlaceholderText("사업장 (예: 화성, 평택)")
        self.common_area = QComboBox()
        for text, value in (("메모리/파운드리 · 라인 기준 자동", ""), ("MEMORY", "MEMORY"), ("FOUNDRY", "FOUNDRY")):
            self.common_area.addItem(text, value)
        for widget, name in ((self.common_date, "공통 기준일"), (self.common_site, "공통 사업장"), (self.common_area, "공통 메모리/파운드리")):
            widget.setAccessibleName(name)
            widget.setProperty("queryField", True)
            widget.setFixedHeight(38)
        self.common_apply_button = button("모든 행에 적용")
        self.common_overwrite = QCheckBox("이미 있는 값도 덮어쓰기")
        common_layout.addWidget(_WrapRow([_field("기준일", self.common_date), _field("사업장", self.common_site),
                                          _field("메모리/파운드리", self.common_area)], 230, fields=True))
        common_layout.addWidget(_WrapRow([self.common_apply_button, self.common_overwrite], 220))
        self.common_box = common
        review.body.addWidget(common)
        self.row_model = ReadTableModel(("행", "상태"))
        self.row_table = read_table(self.row_model, "추출 행 목록", (70, 150))
        self.row_table.clicked.connect(lambda index: self.select_row(self.row_model.ids[index.row()]))
        self.row_table.activated.connect(lambda index: self.select_row(self.row_model.ids[index.row()]))
        review.body.addWidget(self.row_table)
        self.row_empty = label("", color=SECONDARY, wrap=True)
        review.body.addWidget(self.row_empty)
        self.editor = QWidget()
        editor = QVBoxLayout(self.editor)
        editor.setContentsMargins(0, 0, 0, 0)
        editor.setSpacing(12)
        self.editor_title = label("", 16, medium=True)
        editor.addWidget(self.editor_title)
        self.editor_grid = QGridLayout()
        self.editor_grid.setHorizontalSpacing(12)
        self.editor_grid.setVerticalSpacing(6)
        self.editor_grid.setColumnStretch(1, 1)
        editor.addLayout(self.editor_grid)
        self.inputs, self.originals, self.source_inputs = {}, {}, {}
        self.apply_edit_button = button("수정 반영", primary=True)
        self.revert_button = button("원래 값으로 되돌리기")
        self.exclude_button = button("이 행 제외")
        self.apply_edit_button.clicked.connect(self.apply_edits)
        self.revert_button.clicked.connect(self.revert_selected)
        self.exclude_button.clicked.connect(self.toggle_excluded)
        editor.addWidget(_WrapRow([self.apply_edit_button, self.revert_button, self.exclude_button], 190))
        review.body.addWidget(self.editor)
        review.body.addWidget(label("검토 항목", 16, medium=True))
        self.issue_model = ReadTableModel(("구분", "추출 행", "항목", "셀", "내용", "파일", "시트", "Excel 행", "Excel 열"))
        self.issue_table = read_table(self.issue_model, "검토 항목", (90, 70, 130, 100, 420, 200, 150, 85, 85))
        self.issue_table.clicked.connect(lambda index: self.open_issue(index.row()))
        self.issue_table.activated.connect(lambda index: self.open_issue(index.row()))
        review.body.addWidget(self.issue_table)
        body.addWidget(review)

        save = Card()
        save.body.addWidget(label("4. 저장", 20, medium=True))
        self.save_summary = label("", 13, color=SECONDARY, wrap=True)
        save.body.addWidget(self.save_summary)
        self.save_button = button("검토 완료 · 저장", primary=True)
        self.candidate_button = button("번역표 후보 적용")
        self.candidate_button.setToolTip("원문 모델 표기를 데이터 파트 번역표의 등록 모델로 바꾸고 빈 설비군·계열·UI를 채웁니다. "
                                         "적용한 값은 사용자 수정으로 기록됩니다.")
        self.revalidate_button = button("다시 검사")
        self.requery_button = button("대시보드로 이동")
        self.requery_button.setToolTip("이동만 합니다. 조회 결과를 갱신하려면 조회 적용을 다시 눌러 주세요.")
        save.body.addWidget(_WrapRow([self.save_button, self.candidate_button, self.revalidate_button,
                                      self.requery_button], 200))
        self.result_label = label("", 13, wrap=True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        save.body.addWidget(self.result_label)
        body.addWidget(save)

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
        self._render()

    # --- 상태 표시 ---------------------------------------------------------

    def _set_status(self, text, tone, notice=None):
        self.status.setText(text)
        self.status.set_tone(tone)
        if notice is not None:
            self.notice.setText(notice)

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
        return "AI 원본 대조 추출" if self.extractor is not None else "설정된 AI API · 원본 대조 추출"

    @property
    def busy(self):
        return self.state in ("reading", "extracting", "validating", "saving")

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
        rows, ids = [], []
        if draft is not None:
            headings = ("행", "상태", *(FIELD_LABELS[f] for f in draft.fields))
            if self.row_model.headings != headings:
                self.row_model.headings = headings
                for index, width in enumerate((70, 150, *(130 for _ in draft.fields))):
                    self.row_table.setColumnWidth(index, width)
                self.row_table.base_widths = (70, 150, *(130 for _ in draft.fields))
            for row in draft.rows:
                counts = [sum(i.row_id == row.row_id and i.level is level for i in self.issues)
                          for level in (IssueLevel.ERROR, IssueLevel.REVIEW)]
                state = "제외" if row.excluded else " · ".join(
                    part for part in (f"오류 {counts[0]}" if counts[0] else "", f"확인 {counts[1]}" if counts[1] else "",
                                      "수정됨" if row.edits else "") if part) or "정상"
                rows.append((str(row.row_id), state, *(self._cell(row, f) for f in draft.fields)))
                ids.append(row.row_id)
        self.row_model.set_rows(rows, ids)
        _fit(self.row_table)
        self.row_table.setVisible(bool(rows))
        self.row_empty.setVisible(not rows)
        self.row_empty.setText("시트를 읽은 뒤 AI로 항목 추출을 눌러 주세요." if draft is None else "추출된 행이 없습니다.")
        if self.selected_row_id in ids:
            self.row_table.selectRow(ids.index(self.selected_row_id))
        self.editor.setVisible(draft is not None and self.selected_row_id in ids)
        self.common_box.setVisible(self.kind is ExtractKind.EQUIPMENT_STATUS)
        for widget in (self.common_date, self.common_site, self.common_area, self.common_apply_button, self.common_overwrite):
            widget.setEnabled(draft is not None and not self.busy and not self.locked)
        self.issue_model.set_rows(tuple((ISSUE_LABELS[i.level], str(i.row_id) if i.row_id else "전체",
                                         FIELD_LABELS.get(i.field, "-") if i.field else "-", i.address or "위치 확인 필요", i.message,
                                         i.workbook_name or (draft.workbook_name if draft else "-"),
                                         i.sheet_name or (draft.sheet.sheet_name if draft else "-"),
                                         str(i.excel_position[0]) if i.excel_position else "미확인",
                                         str(i.excel_position[1]) if i.excel_position else "미확인")
                                        for i in self.issues))
        _fit(self.issue_table)
        editable = draft is not None and self.selected_row_id in ids and not self.busy and not self.locked
        for field in self.inputs.values():
            field.setReadOnly(self.busy or self.locked)
        for name, field in self.source_inputs.items():
            row = draft.row(self.selected_row_id) if draft and self.selected_row_id is not None else None
            field.setReadOnly(self.busy or self.locked or bool(row and row.sources.get(name)))
        for widget in (self.apply_edit_button, self.revert_button, self.exclude_button):
            widget.setEnabled(editable)
        self._render_save()

    @staticmethod
    def _cell(row, name):
        value = row.value(name)
        text = value if value is not None else "(빈 값)"
        return f"{text}\n(수정됨 · AI: {row.original.get(name) or '빈 값'})" if name in row.edits else text

    def _render_save(self):
        errors = sum(i.level is IssueLevel.ERROR for i in self.issues)
        reviews = sum(i.level is IssueLevel.REVIEW for i in self.issues)
        self.save_button.setEnabled(self.draft is not None and not errors and not self.busy and not self.locked)
        self.revalidate_button.setEnabled(self.draft is not None and not self.busy and not self.locked)
        candidates = model_candidate_rows(self.draft) if self.draft is not None else {}
        self.candidate_button.setText(f"번역표 후보 적용 ({len(candidates)}행)" if candidates else "번역표 후보 적용")
        self.candidate_button.setEnabled(bool(candidates) and not self.busy and not self.locked)
        self.requery_button.setVisible(self.state == "saved")
        if self.draft is None:
            self.save_summary.setText("AI 추출 결과를 검토한 뒤 저장할 수 있습니다.")
        else:
            active = len(self.draft.active_rows)
            excluded = len(self.draft.rows) - active
            self.save_summary.setText(
                f"저장 대상 {active}행 · 제외 {excluded}행 · 오류 {errors}건 · 확인 필요 {reviews}건\n"
                + ("필수값 오류가 남으면 묶음 전체를 저장하지 않습니다. 값과 원본 위치를 보완하고 다시 검사해 주세요." if errors else
                   f"저장 위치: {self.saver.target_label}"))

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
        self.review_request.cancel()
        self._review_context = None
        self.extraction_request.cancel()
        self._extraction_context = None
        self.release_request.cancel()
        self._release_context = None
        self.generation += 1
        self.draft, self.issues, self.selected_row_id = None, (), None
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
        if snapshot is None:
            self.preview_info.setText("표시할 원본이 없습니다.")
            return
        for merged in snapshot.merged:
            (top, left), (bottom, right) = (self.grid_model.position(part) for part in merged.split(":"))
            if 0 <= top < self.grid_model.rowCount() and 0 <= left < self.grid_model.columnCount():
                self.grid.setSpan(top, left, bottom - top + 1, right - left + 1)
        end = cell_address(snapshot.first_row + max(snapshot.row_count, 1) - 1, snapshot.first_col + max(snapshot.col_count, 1) - 1)
        start = cell_address(snapshot.first_row, snapshot.first_col)
        self.preview_info.setText(
            f"{self.workbook_name} · {snapshot.sheet_name} · 사용 영역 {start}:{end} · 값이 있는 셀 {len(snapshot.cells)}개"
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
        if self.draft is not None and self.draft.kind is not self.kind and not self.locked:
            self._reset_draft()
            self._set_status("자료 종류 변경 · 다시 추출 필요", "warning", "이전 추출 결과는 다른 종류라 지웠습니다.")
        self._render()

    def extract(self):
        if self.busy or self.snapshot is None or self.locked:
            return False
        self._reset_draft()
        self.state = "extracting"
        count = sum(value is not None and bool(cell_text(value).strip()) for value in self.snapshot.cells.values())
        self._set_status("AI 추출 중", "info", f"{self.snapshot.sheet_name}의 값이 있는 셀 {count}개를 보냅니다.")
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
        extractor, sheet_extractor = self.extractor, self.sheet_extractor
        self.extraction_request.start(BackgroundRead(lambda: extract_for_review(
            kind, workbook, captured, extractor=extractor, sheet_extractor=sheet_extractor)))

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
        self.source_badge.setText(SOURCE_LABELS.get(draft.ai_source, SOURCE_LABELS[None]))
        self.source_badge.set_tone("warning" if draft.ai_source is not ResponseSource.REAL else "info")
        self._changed(initial=True)
        if draft.rows:
            self.select_row(draft.rows[0].row_id)

    def _changed(self, *, initial=False):
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
                  "AI 추출 결과입니다. 행을 선택해 값을 확인·수정하고 검토 항목을 처리해 주세요." if initial else
                  "현재 검토값의 검사를 마쳤습니다. 변경 내용과 확인 항목을 검토해 주세요.")
        self._set_status("검토 필요 · 오류 있음" if errors else "검토 중 · 저장 가능", "warning" if errors else "info",
                         notice)
        self._render()

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
        """화면을 떠난 뒤 도착한 추출 결과를 폐기하고 재추출을 안내한다."""
        if self.state != "extracting":
            return
        self.extraction_request.cancel()
        self._extraction_context = None
        self.state = "idle"
        self._set_status("AI 추출 취소", "warning", "화면을 떠나 추출 결과 수신을 취소했습니다. 다시 추출해 주세요.")
        self._render()

    def hideEvent(self, event):
        self._cancel_extraction()
        self._cancel_review()
        super().hideEvent(event)

    def closeEvent(self, event):
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
        for line, name in enumerate(self.draft.fields):
            title = label(FIELD_LABELS[name], 13, medium=True)
            field = QLineEdit(row.value(name) or "")
            field.setAccessibleName(f"{row_id}행 {FIELD_LABELS[name]}")
            field.setFixedHeight(34)
            field.setMinimumWidth(0)
            field.setReadOnly(self.busy or self.locked)
            field.setStyleSheet("QLineEdit {background:#f7f9fc;border:1px solid #e5eaf3;border-radius:8px;padding:4px 8px;}")
            title.setBuddy(field)
            address = row.sources.get(name)
            original = label(f"AI 원본: {row.original.get(name) or '빈 값'} · 셀 {address or '위치 미확인'}"
                             + (" · 수정됨" if name in row.edits else ""), 12, color=SECONDARY, wrap=True)
            original.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.editor_grid.addWidget(title, line * 2, 0)
            self.editor_grid.addWidget(field, line * 2, 1)
            self.editor_grid.addWidget(original, line * 2 + 1, 1)
            self.inputs[name], self.originals[name] = field, original
            if name in {"equipment_code", "version_raw"}:
                source_field = QLineEdit(address or "")
                source_field.setAccessibleName(f"{row_id}행 {FIELD_LABELS[name]} 원본 셀")
                source_field.setPlaceholderText("원본 셀 주소 · 예: C12")
                source_field.setToolTip("위치를 확인할 수 없을 때 원본에서 확인한 셀을 지정하세요. 원본 값은 바꾸지 않습니다.")
                source_field.setReadOnly(self.busy or self.locked or bool(address))
                self.editor_grid.addWidget(source_field, line * 2, 2)
                self.source_inputs[name] = source_field
        self.editor_title.setText(f"{row_id}행{' (제외됨)' if row.excluded else ''} 값 확인 · 수정")
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
        request = build_save_request(self.draft, self.request_id, self._validation_reference())
        merged = len(self.draft.active_rows) - len(request.rows)
        text = (f"통합문서: {request.workbook_name}\n시트: {request.raw_data['sheet']}\n"
                f"자료 종류: {KIND_LABELS[request.kind]}\n저장 행: {len(request.rows)}행"
                + (f" (정확한 중복 {merged}행 합침)" if merged else "")
                + f"\n확인 필요 항목: {request.review_count}건\nAI 응답: {SOURCE_LABELS.get(request.ai_source, SOURCE_LABELS[None])}\n"
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
