"""SETRON SQL 백업을 선택하고 대상 DB와 비교한 뒤 가져온다."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from setron.features.db_import.dump import DumpFormatError
from setron.features.db_import.service import SqlImportError
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseError
from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.import_dialogs import ImportConfirmDialog
from setron.ui.import_widgets import import_combo_style
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.theme import SECONDARY


class SqlImportPage(QWidget):
    saved = Signal(object)
    busy_changed = Signal()

    def __init__(self, importer):
        super().__init__()
        self.setObjectName("canvas")
        self.importer = importer
        self.path = self.plan = None
        self.preview_request, self.apply_request = ReadRequest(self), ReadRequest(self)
        self.preview_request.completed.connect(self._preview_completed)
        self.apply_request.completed.connect(self._apply_completed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        card = Card()
        layout.addWidget(card)
        layout.addStretch()
        card.body.addWidget(label("SQL 데이터 가져오기", 18, medium=True, wrap=True))
        card.body.addWidget(label(
            "SETRON의 ‘DB 전체 SQL’에서 내보낸 .sql 파일을 선택하고 대상 DB에 반영할 변경 사항을 검토합니다.",
            color=SECONDARY, wrap=True,
        ))
        self.file_label = label("선택한 파일이 없습니다.", color=SECONDARY, wrap=True)
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.body.addWidget(self.file_label)
        mode_label = label("가져오기 방식", medium=True)
        self.mode_combo = ComboBox()
        self.mode_combo.setAccessibleName(mode_label.text())
        self.mode_combo.setProperty("queryField", True)
        self.mode_combo.setStyleSheet(import_combo_style())
        self.mode_combo.addItem("신규 항목만 추가", "add")
        self.mode_combo.addItem("같은 항목 덮어쓰기", "overwrite")
        self.mode_combo.currentIndexChanged.connect(self._selection_changed)
        mode_row = QHBoxLayout()
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch()
        card.body.addLayout(mode_row)
        self.status = StatusBadge("파일 선택 대기" if importer is not None else "DB 연결 필요")
        card.body.addWidget(self.status)
        self.details = label("" if importer is not None else "DB 접속 설정을 확인해 주세요.", wrap=True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.body.addWidget(self.details)
        actions = QHBoxLayout()
        self.select_button = button("SQL 파일 선택")
        self.select_button.clicked.connect(self._choose_file)
        actions.addWidget(self.select_button)
        actions.addStretch()
        self.preview_button = button("변경 사항 검토")
        self.preview_button.clicked.connect(self._preview)
        actions.addWidget(self.preview_button)
        self.apply_button = button("가져오기", primary=True)
        self.apply_button.clicked.connect(self._confirm_apply)
        actions.addWidget(self.apply_button)
        card.body.addLayout(actions)
        self._render()

    @property
    def busy(self):
        return self.preview_request.busy or self.apply_request.busy

    def _render(self):
        enabled = self.importer is not None and not self.busy
        self.select_button.setEnabled(enabled)
        self.mode_combo.setEnabled(enabled)
        self.preview_button.setEnabled(enabled and self.path is not None)
        self.apply_button.setEnabled(not self.busy and self.plan is not None and self.plan.changes > 0)
        self.details.setVisible(bool(self.details.text()))

    def _show_status(self, status, tone, details=""):
        self.status.setText(status)
        self.status.set_tone(tone)
        self.details.setText(details)
        self._render()

    def _choose_file(self):
        start = str(self.path.parent) if self.path is not None else ""
        selected, _ = QFileDialog.getOpenFileName(self, "SETRON SQL 파일 선택", start, "SQL 파일 (*.sql)")
        if not selected:
            return
        self.path = Path(selected)
        self.file_label.setText(str(self.path))
        self._selection_changed()

    def _selection_changed(self):
        self.plan = None
        self._show_status("검토 대기" if self.path is not None else "파일 선택 대기", "neutral")

    def _preview(self):
        self.plan = None
        preview, path, mode = self.importer.preview, self.path, self.mode_combo.currentData()
        self.preview_request.start(BackgroundRead(lambda: preview(path, mode)))
        self._show_status("비교 중", "info", "SQL 파일을 읽고 대상 DB와 비교하고 있습니다.")
        self.busy_changed.emit()

    def _preview_completed(self, plan, error):
        if error is not None:
            known_error = isinstance(error, (DumpFormatError, SqlImportError, DatabaseError))
            detail = str(error) if known_error else "SQL 파일을 검토하지 못했습니다."
            self._show_status("검토 실패", "danger", detail)
        else:
            self.plan = plan
            self._show_status("검토 완료" if plan.changes else "가져올 변경분 없음", "info", plan.summary)
        self.busy_changed.emit()

    def _confirm_apply(self):
        plan = self.plan
        if self.busy or plan is None or not plan.changes:
            return
        action, question = ("병합", "병합할까요?") if plan.target_rows else ("가져오기", "가져올까요?")
        dialog = ImportConfirmDialog(
            f"{self.path.name}\n가져오기 방식 · {self.mode_combo.currentText()}\n\n"
            f"{plan.summary}\n\n검토한 변경 사항을 대상 DB에 {question}",
            self, title=f"SQL 데이터 {action} 확인", action_text=action,
        )
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
        finally:
            dialog.deleteLater()
        self.plan = None
        apply = self.importer.apply
        self.apply_request.start(BackgroundRead(lambda: apply(plan)))
        self._show_status(f"{action} 중", "info", "처리가 끝날 때까지 기다려 주세요.")
        self.busy_changed.emit()

    def _apply_completed(self, result, error):
        if isinstance(error, DatabaseCommitUnknownError):
            self._show_status("가져오기 결과 확인 필요", "warning", str(error))
        elif error is not None:
            known_error = isinstance(error, (SqlImportError, DatabaseError))
            detail = str(error) if known_error else "SQL 데이터를 저장하지 못했습니다."
            self._show_status("가져오기 실패", "danger", detail)
        else:
            self._show_status("가져오기 완료", "success", result.summary)
            self.saved.emit(result)
        self.busy_changed.emit()
