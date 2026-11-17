"""VER5 변경 이력·보관 작업·선택 작업 출력. 전체 현황 스냅샷과 구분한다."""

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QFileDialog, QVBoxLayout, QWidget

from setron.ui.components import button, label
from setron.ui.import_dialogs import ImportDialog, ImportProgressDialog
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.version_management_data import archive_comparison
from setron.ui.version_management_widgets import actions, card, columns, combo, note, set_rows, table, tabs, text_field
from setron.ui.versions import _field


FIELD_LABELS = {"version_raw": "버전 원문", "released_at": "릴리즈일", "version_order": "릴리즈 순서",
                "order_basis": "순서 확인 근거", "applicability": "확인된 적용 대상", "content": "변경 내용",
                "item_no": "Item 번호", "item_state": "포함 상태", "target_scope": "모델 적용 범위",
                "target_group_ids": "적용 모델", "category": "항목·분류", "jira": "JIRA", "remark": "비고",
                "code": "설비호기", "site": "Site", "fab": "Fab", "line": "Line", "geometry": "형상"}


def shown(value):
    if value is None or value == "":
        return "미기재"
    return str(value)


def change_rows(changes):
    return tuple((change.category, change.target, FIELD_LABELS.get(key, key), shown(old), shown(new), change.reason)
                 for change in changes for key, old, new in change.differences)


def save_archive_excel(path, archive, *, include_sources=True):
    """확정한 선택 작업의 변경·근거만 출력한다. DB 조회·SQL 덤프를 호출하지 않는다."""
    from openpyxl import Workbook
    workbook = Workbook()
    summary = workbook.active
    summary.title = "선택 작업"
    summary.append(("보관 작업", archive.title))
    summary.append(("생성 일시", archive.created_at))
    summary.append(("출력 범위", "선택 작업의 변경 묶음 · 전체 현황 스냅샷 아님"))
    changes = workbook.create_sheet("변경 전후")
    changes.append(("종류", "대상", "항목", "이전 값", "변경 값", "사유"))
    for row in change_rows(archive.changes):
        changes.append(row)
    if include_sources:
        sources = workbook.create_sheet("입력 근거")
        sources.append(("대상", "원본·입력 근거"))
        for change in archive.changes:
            sources.append((change.target, "\n".join(map(str, change.sources)) or "직접 입력 · 원본 파일 없음"))
    for sheet in workbook:
        sheet.freeze_panes = "A2"
        for row in sheet:
            for cell in row:
                if cell.value is not None:
                    # 원문이 수식으로 실행되지 않도록 모든 출력값을 문자열로 저장한다.
                    cell.value = str(cell.value)
                    cell.data_type = "s"
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(65, max(18, max(len(str(c.value or "")) for c in column) + 2))
    workbook.save(path)
    return Path(path)


class ReviewPanel(QWidget):
    archive_created = Signal()
    history_requested = Signal()

    def __init__(self, workspace):
        super().__init__()
        self.workspace, self.reviewed = workspace, None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(label("변경 검토 · 보관 작업 만들기", 20, medium=True))
        left, right = card(), card("보관할 범위 확인")
        self.empty = note()
        left.body.addWidget(self.empty)
        self.model, self.table = table(("종류", "대상", "항목", "이전 값", "변경 값", "사유"), "검토할 변경 묶음", (100, 150, 120, 150, 150, 180))
        left.body.addWidget(self.table)
        right.body.addWidget(note("저장된 입력 작업의 변경 묶음을 읽기 전용으로 남깁니다. 목표 N·실제 설비 SW·전체 현황 스냅샷은 변경하지 않습니다."))
        self.check = button("변경 내용 확인", primary=True)
        self.confirmed = QCheckBox("보관할 변경 범위를 확인했습니다")
        self.create = button("보관 작업 만들기 · 작업본", primary=True)
        self.history = button("보관 이력 확인")
        self.message = note("작업본·보관 이력은 현재 앱 세션에만 유지됩니다.")
        self.check.clicked.connect(self.review)
        self.confirmed.toggled.connect(self.sync)
        self.create.clicked.connect(self.make_archive)
        self.history.clicked.connect(self.history_requested)
        for widget in (self.check, self.confirmed, self.create, self.message, self.history):
            right.body.addWidget(widget)
        left.setMinimumHeight(400)
        body.addWidget(columns(left, right))
        self.refresh()

    def refresh(self):
        self.reviewed = None
        self.confirmed.setChecked(False)
        changes = tuple(self.workspace.pending.values())
        set_rows(self.model, self.table, change_rows(changes))
        self.table.setVisible(bool(changes))
        self.empty.setText(f"미확정 변경 {len(changes)}건 · 입력 화면에서 변경분을 확인하고 작업본을 저장해 주세요." if changes else
                           "미확정 변경 없음\n저장한 변경을 보관할 수 있습니다. 설비 정정·Excel 검토 또는 작업본 저장부터 시작하세요.")
        self.check.setEnabled(bool(self.workspace.history))
        self.sync()

    def review(self):
        self.reviewed = self.workspace.generation, deepcopy(self.workspace.history)
        set_rows(self.model, self.table, change_rows(self.reviewed[1]))
        self.table.show()
        self.empty.setText(f"보관할 저장 변경 {len(self.reviewed[1])}건 · 전체 현황 스냅샷 아님")
        self.confirmed.setChecked(False)
        self.sync()

    def sync(self, *_):
        self.create.setEnabled(self.reviewed is not None and self.confirmed.isChecked()
                               and self.reviewed[0] == self.workspace.generation)

    def make_archive(self):
        if self.reviewed is None or self.reviewed[0] != self.workspace.generation:
            self.message.setText("변경 내용이 달라졌습니다. 다시 확인해 주세요.")
            self.sync()
            return False
        try:
            archive = self.workspace.archive(self.reviewed[1], confirmed=self.confirmed.isChecked())
        except ValueError as error:
            self.message.setText(str(error))
            return False
        self.message.setText(archive.title + "을 만들었습니다. 현재 앱 세션의 읽기 전용 작업본입니다.")
        self.confirmed.setChecked(False)
        self.reviewed = None
        self.sync()
        self.archive_created.emit()
        return True


class ArchiveExportDialog(ImportDialog):
    export_requested = Signal(str, bool)

    def __init__(self, archive, parent=None):
        super().__init__("선택 보관 작업 Excel", parent)
        self.archive = deepcopy(archive)
        self.body.addWidget(_field("출력 범위 · 생성 시 고정", text_field("선택 작업 출력 범위", f"{archive.title} · {len(archive.changes)}건 · 전체 현황 아님", readonly=True)))
        self.filename = text_field("파일명", archive.title.replace(" ", "_") + ".xlsx")
        self.body.addWidget(_field("파일명", self.filename))
        self.sources = QCheckBox("원문·처리 근거 포함")
        self.sources.setChecked(True)
        self.body.addWidget(self.sources)
        self.message = note("선택한 보관 작업만 출력합니다. DB 전체 SQL·가져오기·병합·복원은 별도 절차입니다.")
        self.body.addWidget(self.message)
        self.add_action("취소·닫기", self.reject, default=True)
        self.generate = self.add_action("파일 생성", self.choose_path, primary=True)
        self.finish_layout()

    def choose_path(self):
        filename = self.filename.text().strip()
        if not filename or Path(filename).name != filename or any(c in filename for c in '<>:"/\\|?*'):
            self.message.setText("경로를 제외한 올바른 파일명을 입력해 주세요.")
            return
        if not filename.lower().endswith(".xlsx"):
            filename += ".xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "선택 보관 작업 저장", filename, "Excel (*.xlsx)")
        if path:
            self.export_requested.emit(path, self.sources.isChecked())
            self.accept()


class HistoryPanel(QWidget):
    sql_requested = Signal()

    def __init__(self, workspace):
        super().__init__()
        self.workspace, self.archive_index, self.history_index = workspace, 0, None
        self.read_request = ReadRequest(self)
        self.read_request.completed.connect(self._exported)
        self.progress = ImportProgressDialog(self)
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        self.title = label("변경 이력", 20, medium=True)
        body.addWidget(self.title)
        self.history_button = button("개별 변경 이력")
        self.archive_button = button("보관 이력·원본")
        self.export_button = button("선택 작업 Excel", primary=True)
        self.sql_button = button("DB 전체 SQL · 별도")
        self.history_button.clicked.connect(lambda: self.show_mode(False))
        self.archive_button.clicked.connect(lambda: self.show_mode(True))
        self.export_button.clicked.connect(self.open_export)
        self.sql_button.clicked.connect(self.sql_requested)
        body.addWidget(actions(self.history_button, self.archive_button, self.export_button, self.sql_button, None))
        self.list_card, self.detail_card = card("최근 변경"), card()
        self.kind = combo("변경 이력 종류", ((None, "전체"), ("릴리즈", "릴리즈"), ("Item", "Item"), ("설비 기록", "설비 기록"), ("설비 정보", "설비 정보"), ("Excel", "Excel")))
        self.kind.currentIndexChanged.connect(self.refresh)
        self.kind_tabs = tabs(("전체", "릴리즈", "Item", "설비 기록", "설비 정보", "Excel"), "변경 이력 종류")
        self.kind_tabs.currentChanged.connect(self.kind.setCurrentIndex)
        self.kind.currentIndexChanged.connect(self.kind_tabs.setCurrentIndex)
        self.kind.hide()
        self.list_card.body.addWidget(self.kind_tabs)
        self.list_model, self.list_table = table(("변경·보관 작업", "처리 일시"), "변경·보관 작업 목록", (210, 160))
        self.list_card.body.addWidget(self.list_table)
        self.empty = note()
        self.list_card.body.addWidget(self.empty)
        self.list_table.clicked.connect(lambda index: self.select(index.row()))
        self.list_table.activated.connect(lambda index: self.select(index.row()))
        self.selected = note("목록에서 변경 이력을 선택하세요.")
        self.detail_card.body.addWidget(self.selected)
        self.archive_tabs = tabs(("내용", "비교", "근거"), "보관 작업 상세 탭")
        self.archive_tabs.currentChanged.connect(self.render)
        self.detail_card.body.addWidget(self.archive_tabs)
        self.compare_choice = combo("비교 작업 A")
        self.compare_choice.currentIndexChanged.connect(self.render)
        self.detail_card.body.addWidget(self.compare_choice)
        self.detail_model, self.detail_table = table(("종류", "대상", "항목", "이전 값", "변경 값", "사유"), "변경·보관 작업 상세", (90, 140, 120, 160, 160, 200))
        self.detail_card.body.addWidget(self.detail_table)
        self.evidence = note()
        self.detail_card.body.addWidget(self.evidence)
        self.detail_card.setMinimumHeight(500)
        body.addWidget(columns(self.list_card, self.detail_card))
        self.message = note()
        body.addWidget(self.message)
        self.archived = False
        self.export_dialog = None
        self.show_mode(False)

    def show_mode(self, archived):
        self.archived = archived
        self.title.setText("보관 이력 · 작업별 변경 묶음" if archived else "변경 이력")
        self.archive_tabs.setVisible(archived)
        self.export_button.setVisible(archived)
        self.sql_button.setVisible(archived)
        self.kind.setVisible(not archived)
        self.kind.hide()
        self.kind_tabs.setVisible(not archived)
        self.refresh()

    def refresh(self, *_):
        if self.archived:
            self.entries = self.workspace.archives
            rows = tuple((a.title, a.created_at) for a in self.entries)
        else:
            selected = self.kind.currentData()
            self.entries = tuple(c for c in reversed(self.workspace.history) if selected is None or c.category == selected)
            rows = tuple((c.category + " · " + c.target, c.recorded_at) for c in self.entries)
        set_rows(self.list_model, self.list_table, rows)
        self.list_table.setVisible(bool(rows))
        self.empty.setText("" if rows else "보관 작업이 없습니다." if self.archived else "저장한 변경 이력이 없습니다. 릴리즈·Item 수정 또는 설비 기록 정정 후 확인하세요.")
        self.compare_choice.blockSignals(True)
        self.compare_choice.clear()
        for i, a in enumerate(self.workspace.archives):
            self.compare_choice.addItem(a.title, i)
        self.compare_choice.blockSignals(False)
        self.index = min(getattr(self, "index", 0), max(0, len(self.entries)-1))
        self.render()

    def select(self, index):
        if 0 <= index < len(self.entries):
            self.index = index
            self.render()

    def render(self, *_):
        self.compare_choice.setVisible(self.archived and self.archive_tabs.currentIndex() == 1)
        self.export_button.setEnabled(self.archived and bool(self.entries) and not self.read_request.busy)
        if not self.entries:
            self.selected.setText("목록에서 변경 이력을 선택하세요.")
            set_rows(self.detail_model, self.detail_table, ())
            self.evidence.clear()
            self.detail_table.hide()
            return
        self.detail_table.show()
        entry = self.entries[self.index]
        changes = entry.changes if self.archived else (entry,)
        self.selected.setText((entry.title + " · " + entry.created_at if self.archived else entry.target + " · " + entry.recorded_at)
                              + "\n" + ("DB 저장 완료" if all(c.persisted for c in changes) else "현재 앱 세션의 작업본 · DB 저장과 별도"))
        self.detail_model.headings = ("종류", "대상", "항목", "이전 값", "변경 값", "사유")
        rows = change_rows(changes)
        self.evidence.setText("이 작업에 포함된 변경과 입력 근거만 표시합니다. 전체 현황 스냅샷이 아닙니다.")
        if self.archived and self.archive_tabs.currentIndex() == 1:
            first = self.compare_choice.currentData()
            rows = tuple((*row, "선택 작업 A ↔ B") for row in archive_comparison(self.workspace.archives[first], entry)) if first is not None else ()
            self.evidence.setText("작업 A와 B에 포함된 변경 묶음만 비교합니다." if rows else "선택한 작업 사이에 다른 변경값이 없습니다.")
        elif self.archived and self.archive_tabs.currentIndex() == 2:
            self.detail_model.headings = ("종류", "대상", "근거 구분", "원본·입력 근거", "사유", "처리 일시")
            rows = tuple((c.category, c.target, "입력 근거", "\n".join(map(str, c.sources)) or "직접 입력 · 원본 파일 없음", c.reason, c.recorded_at) for c in changes)
        set_rows(self.detail_model, self.detail_table, rows)

    def open_export(self):
        if not self.archived or not self.entries or self.read_request.busy:
            return
        archive = deepcopy(self.entries[self.index])
        dialog = ArchiveExportDialog(archive, self)
        self.export_dialog = dialog
        dialog.export_requested.connect(lambda path, sources: self.export(path, archive, sources))
        dialog.open()

    def export(self, path, archive, sources=True):
        if self.read_request.busy:
            return False
        frozen = deepcopy(archive)
        self.progress.set_operation("export", "선택 작업 Excel 생성 중", "선택한 변경 묶음과 입력 근거를 출력하고 있습니다.")
        self.export_button.setEnabled(False)
        return self.read_request.start(BackgroundRead(lambda: save_archive_excel(path, frozen, include_sources=sources)))

    def _exported(self, path, error):
        self.progress.set_operation(None)
        self.message.setText("파일을 생성하지 못했습니다. 저장 위치와 파일 사용 상태를 확인해 주세요." if error else f"선택 보관 작업 Excel을 저장했습니다: {path}")
        self.render()
