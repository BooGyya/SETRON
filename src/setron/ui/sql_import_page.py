"""SETRON SQL 백업을 선택하고 대상 DB와 비교한 뒤 가져온다."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QBoxLayout, QDialog, QFileDialog, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from setron.features.db_import.dump import DumpFormatError
from setron.features.db_import.service import SqlImportError
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseError
from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.import_dialogs import ImportConfirmDialog
from setron.ui.import_widgets import import_combo_style
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.sql_import_review import SqlComparisonWidget
from setron.ui.theme import SECONDARY


class SqlImportPage(QWidget):
    saved = Signal(object)
    result_uncertain = Signal()
    busy_changed = Signal()

    def __init__(self, importer):
        super().__init__()
        self.setObjectName("canvas")
        self.importer = importer
        self.path = self.plan = None
        self._review_dirty = False
        self.preview_request, self.resolve_request, self.apply_request = (ReadRequest(self) for _ in range(3))
        self.preview_request.completed.connect(self._preview_completed)
        self.resolve_request.completed.connect(self._resolve_completed)
        self.apply_request.completed.connect(self._apply_completed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        card = Card()
        layout.addWidget(card)
        layout.addStretch()
        self.header_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.header_layout.setSpacing(12)
        self.title = label("SQL 데이터 가져오기", 18, medium=True, wrap=True)
        self.header_layout.addWidget(self.title, 1)
        self.status = StatusBadge("파일 선택 대기" if importer is not None else "DB 연결 필요")
        self.status.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.header_layout.addWidget(self.status, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        card.body.addLayout(self.header_layout)
        self.select_button = button("SQL 파일 선택")
        self.select_button.clicked.connect(self._choose_file)
        self.file_label = label("", color=SECONDARY, wrap=True)
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.file_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.file_layout.setSpacing(12)
        self.file_layout.addWidget(self.select_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.file_layout.addWidget(self.file_label, 1)
        card.body.addLayout(self.file_layout)
        mode_label = label("가져오기 방식", medium=True)
        self.mode_combo = ComboBox()
        self.mode_combo.setAccessibleName(mode_label.text())
        self.mode_combo.setProperty("queryField", True)
        self.mode_combo.setStyleSheet(import_combo_style())
        self.mode_combo.setMinimumHeight(40)
        self.mode_combo.addItem("비교하고 선택", "review")
        self.mode_combo.addItem("신규 항목만 추가", "add")
        self.mode_combo.addItem("같은 항목 덮어쓰기", "overwrite")
        self.mode_combo.currentIndexChanged.connect(self._selection_changed)
        mode_row = QHBoxLayout()
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch()
        card.body.addLayout(mode_row)
        self.details = label("" if importer is not None else "DB 접속 설정을 확인해 주세요.", wrap=True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.body.addWidget(self.details)
        self.comparison = SqlComparisonWidget()
        self.comparison.setVisible(False)
        self.comparison.choices_changed.connect(self._choices_changed)
        self.comparison.review_requested.connect(self._resolve_choices)
        card.body.addWidget(self.comparison)
        actions = QHBoxLayout()
        self.preview_button = button("변경 사항 검토")
        self.preview_button.clicked.connect(self._preview)
        actions.addWidget(self.preview_button)
        self.apply_button = button("가져오기", primary=True)
        self.apply_button.clicked.connect(self._confirm_apply)
        actions.addWidget(self.apply_button)
        actions.addStretch()
        card.body.addLayout(actions)
        self._render()

    @property
    def busy(self):
        return self.preview_request.busy or self.resolve_request.busy or self.apply_request.busy

    def _can_apply(self):
        return (not self.busy and self.plan is not None and self.plan.changes > 0
                and not getattr(self.plan, "unresolved", 0) and not self._review_dirty)

    def _render(self):
        enabled = self.importer is not None and not self.busy
        self.select_button.setEnabled(enabled)
        self.mode_combo.setEnabled(enabled)
        self.preview_button.setEnabled(enabled and self.path is not None)
        self.apply_button.setEnabled(self._can_apply())
        self.comparison.setEnabled(enabled and self.plan is not None)
        self.comparison.review_button.setEnabled(enabled and self.plan is not None and self._review_dirty
                                                  and not self.comparison.unresolved)
        self.file_label.setVisible(bool(self.file_label.text()))
        self.details.setVisible(bool(self.details.text()))
        self._reflow()

    def _reflow(self):
        available = self.width() - 48
        header_width = (self.title.fontMetrics().horizontalAdvance(self.title.text())
                        + self.status.sizeHint().width() + self.header_layout.spacing())
        self.header_layout.setDirection(QBoxLayout.Direction.TopToBottom if available < header_width
                                        else QBoxLayout.Direction.LeftToRight)
        self.file_layout.setDirection(QBoxLayout.Direction.TopToBottom if available < 520
                                      else QBoxLayout.Direction.LeftToRight)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def _show_status(self, status, tone, details=""):
        self.status.setText(status)
        self.status.set_tone(tone)
        self.details.setText(details)
        self._render()

    def _choose_file(self):
        if self.busy:
            return
        start = str(self.path.parent) if self.path is not None else ""
        selected, _ = QFileDialog.getOpenFileName(self, "SETRON SQL 파일 선택", start, "SQL 파일 (*.sql)")
        if not selected:
            return
        self.path = Path(selected)
        self.file_label.setText(str(self.path))
        self._selection_changed()

    def _selection_changed(self):
        if self.apply_request.busy:
            return
        self.preview_request.cancel()
        self.resolve_request.cancel()
        self.plan = None
        self._review_dirty = False
        self.comparison.set_differences(())
        self._show_status("검토 대기" if self.path is not None else "파일 선택 대기", "neutral")
        self.busy_changed.emit()

    def _preview(self):
        if self.busy or self.importer is None or self.path is None:
            return
        self.plan = None
        self._review_dirty = False
        self.comparison.set_differences(())
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
            self._review_dirty = False
            self.comparison.set_differences(getattr(plan, "differences", ()))
            status = ("적용할 값 선택 필요" if getattr(plan, "unresolved", 0) else
                      "검토 완료" if plan.changes else "가져올 변경분 없음")
            self._show_status(status, "info", plan.summary)
        self.busy_changed.emit()

    def _choices_changed(self):
        if self.plan is None or self.busy:
            return
        reviewed = {item.id: item.choice for item in getattr(self.plan, "differences", ())
                    if item.choice in {"CURRENT", "INCOMING"}}
        self._review_dirty = reviewed != self.comparison.choices
        if self._review_dirty:
            self._show_status("적용할 값 선택 필요" if self.comparison.unresolved else "선택 내용 검토 필요",
                              "info", self.plan.summary)
        else:
            status = ("적용할 값 선택 필요" if getattr(self.plan, "unresolved", 0) else
                      "검토 완료" if self.plan.changes else "가져올 변경분 없음")
            self._show_status(status, "info", self.plan.summary)

    def _resolve_choices(self):
        plan = self.plan
        if self.busy or plan is None or not self._review_dirty or self.comparison.unresolved:
            return
        choices = self.comparison.choices
        resolve = self.importer.resolve
        self.resolve_request.start(BackgroundRead(lambda: resolve(plan, choices)))
        self._show_status("선택 내용 검토 중", "info", "선택한 값과 연결 자료를 확인하고 있습니다.")
        self.busy_changed.emit()

    def _resolve_completed(self, plan, error):
        if error is not None:
            known = isinstance(error, (SqlImportError, DatabaseError))
            self._show_status("선택 검토 실패", "danger", str(error) if known else "선택한 값을 검토하지 못했습니다.")
        elif plan is None or not isinstance(getattr(plan, "summary", None), str):
            self._show_status("선택 검토 실패", "danger", "선택한 값을 검토하지 못했습니다. 변경 사항을 다시 확인해 주세요.")
        else:
            self.plan = plan
            self._review_dirty = False
            self.comparison.set_differences(getattr(plan, "differences", ()))
            status = ("적용할 값 선택 필요" if getattr(plan, "unresolved", 0) else
                      "검토 완료" if plan.changes else "가져올 변경분 없음")
            self._show_status(status, "info", plan.summary)
        self.busy_changed.emit()

    def _confirm_apply(self):
        plan = self.plan
        if not self._can_apply():
            return
        action, question = ("병합", "병합할까요?") if plan.target_rows else ("가져오기", "가져올까요?")
        choices = self.comparison.choices
        selected = (f"\n선택 · 현재값 유지 {sum(value == 'CURRENT' for value in choices.values()):,}건 · "
                    f"가져온값 사용 {sum(value == 'INCOMING' for value in choices.values()):,}건" if choices else "")
        dialog = ImportConfirmDialog(
            f"{self.path.name}\n가져오기 방식 · {self.mode_combo.currentText()}\n\n"
            f"{plan.summary}{selected}\n\n검토한 변경 사항을 대상 DB에 {question}",
            self, title=f"SQL 데이터 {action} 확인", action_text=action,
        )
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
        finally:
            dialog.deleteLater()
        self.plan = None
        self._review_dirty = False
        apply = self.importer.apply
        self.apply_request.start(BackgroundRead(lambda: apply(plan)))
        self._show_status(f"{action} 중", "info", "처리가 끝날 때까지 기다려 주세요.")
        self.busy_changed.emit()

    def _apply_completed(self, result, error):
        self.plan = None
        self._review_dirty = False
        known_error = isinstance(error, (SqlImportError, DatabaseError))
        if (isinstance(error, DatabaseCommitUnknownError)
                or error is not None and not known_error
                or error is None and not isinstance(getattr(result, "summary", None), str)):
            detail = (str(error) if isinstance(error, DatabaseCommitUnknownError) else
                      "SQL 저장 결과를 확인할 수 없습니다. 중복으로 가져오지 말고 DB 기록을 확인한 뒤 변경 사항을 다시 검토해 주세요.")
            self._show_status("가져오기 결과 확인 필요", "warning", detail)
            self.result_uncertain.emit()
        elif error is not None:
            self._show_status("가져오기 실패", "danger", str(error))
        else:
            self._show_status("가져오기 완료", "success", result.summary)
            self.saved.emit(result)
        self.busy_changed.emit()
