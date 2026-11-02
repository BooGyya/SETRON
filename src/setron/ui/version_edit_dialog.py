"""B-2 공통 편집 흐름. 기본 항목은 비어 있고 실제 저장 어댑터는 연결하지 않는다."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QMessageBox, QScrollArea, QVBoxLayout, QWidget

from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.theme import CANVAS, SECONDARY
from setron.ui.version_edit_data import EditDraft, EditGateway, SaveOutcome


class VersionEditDialog(QDialog):
    """편집·검토·저장 상태를 분리한 모달 화면.

    ivar
    - draft: 현재 화면만 소유하는 초안.
    - gateway: 선택적 비동기 저장 경계. 기본값 None으로 저장 차단.
    - state: edit/review/saving/success/failed/unknown.
    - settled: 저장 결과를 GUI 스레드로 전달하는 내부 신호.
    """

    settled = Signal(int, object)

    def __init__(self, draft: EditDraft, *, title: str, gateway: EditGateway | None = None, parent=None):
        """명시적으로 전달된 항목만 렌더링한다.

        param
        - draft: 원본·시작값을 보존한 화면 초안.
        - title: 선택한 릴리즈의 표시 이름.
        - gateway: 테스트 등 외부에서 제공할 저장 경계. 기본 앱에서는 미연결.
        - parent: 기존 버전 화면.
        """
        super().__init__(parent)
        self.draft, self.gateway = draft, gateway
        self.state, self.reviewed, self.attempt = "edit", None, 0
        self.inputs, self.errors = {}, {}
        self.setWindowTitle("SETRON · 릴리즈 수정")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumSize(420, 400)
        self.resize(760, 640)
        self.setStyleSheet(f"QDialog {{background:{CANVAS};}}")
        body = QVBoxLayout(self)
        body.setContentsMargins(24, 24, 24, 24)
        body.addWidget(label("릴리즈 수정", 24, medium=True))
        body.addWidget(label(title, 16, wrap=True))
        self.status = StatusBadge("편집 항목 확인 대기", "info")
        body.addWidget(self.status)
        self.notice = label("", 13, color=SECONDARY, wrap=True)
        body.addWidget(self.notice)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        self.form = Card()
        self.form.body.addWidget(label("원본 · 현재 관리값 · 수정값", 18, medium=True))
        if not draft.fields:
            self.form.body.addWidget(label(
                "편집 가능한 항목이 아직 연결되지 않았습니다. 현재 릴리즈 정보는 변경되지 않습니다.", wrap=True))
        for field in draft.fields:
            self.form.body.addWidget(label(field.title, medium=True))
            original = "원본 미연결" if field.original is None else field.original or "(빈 값)"
            self.form.body.addWidget(label(f"원본: {original}\n현재 관리값: {field.current or '(빈 값)'}", 13, wrap=True))
            entry = QLineEdit(field.current)
            entry.setAccessibleName(field.title + " 수정값")
            entry.setMinimumHeight(38)
            entry.setMaxLength(2147483647)
            entry.textChanged.connect(lambda value, key=field.key: self._changed(key, value))
            self.inputs[field.key] = entry
            self.form.body.addWidget(entry)
            error = label("", 13, wrap=True)
            self.errors[field.key] = error
            self.form.body.addWidget(error)
        layout.addWidget(self.form)
        self.review = Card()
        self.review.body.addWidget(label("변경 내용 검토", 18, medium=True))
        self.review_model = ReadTableModel(("항목", "원본", "현재 관리값", "수정값"))
        self.review_table = read_table(self.review_model, "릴리즈 수정 검토", (120, 180, 180, 180))
        self.review.body.addWidget(self.review_table)
        layout.addWidget(self.review)
        layout.addStretch()
        scroll.setWidget(content)
        body.addWidget(scroll, 1)
        controls = QHBoxLayout()
        self.cancel, self.edit, self.check, self.save = (
            button("취소"), button("계속 수정"), button("변경 검토"), button("저장", primary=True))
        for action in (self.cancel, self.edit, self.check, self.save):
            action.setAutoDefault(False)
            controls.addWidget(action)
        body.addLayout(controls)
        self.cancel.clicked.connect(self.reject)
        self.edit.clicked.connect(self._edit)
        self.check.clicked.connect(self._review)
        self.save.clicked.connect(self._save)
        self.settled.connect(self._complete, Qt.ConnectionType.QueuedConnection)
        self._render()

    def _changed(self, key, value):
        """입력 변경 시 검토 스냅샷을 무효화한다.

        param
        - key, value: 명시된 항목 키와 새 입력값.
        """
        if self.state not in {"edit", "failed"}:
            return
        self.draft.values[key] = value
        self.reviewed = None
        self.state = "edit"
        self.errors[key].setText("")
        self._render()

    def _render(self):
        """상태별 버튼·입력 잠금과 안내를 함께 갱신한다."""
        editing = self.state in {"edit", "failed"}
        self.form.setVisible(editing)
        self.review.setVisible(not editing)
        self.check.setVisible(editing)
        self.check.setEnabled(bool(self.draft.request().changes))
        self.edit.setVisible(self.state == "review")
        self.save.setVisible(self.state in {"review", "saving"})
        self.save.setEnabled(self.state == "review" and self.gateway is not None)
        self.cancel.setEnabled(self.state != "saving")
        self.cancel.setText("닫기" if self.state in {"success", "unknown"} or not self.draft.fields else "취소")
        for entry in self.inputs.values():
            entry.setEnabled(editing)
        messages = {
            "edit": ("수정 초안" if self.draft.fields else "편집 항목 확인 대기", "info",
                     "변경한 값은 검토 후 저장합니다." if self.gateway else "저장 기능이 연결되지 않았습니다. 입력값은 DB나 파일에 저장되지 않습니다."),
            "review": ("변경 내용 검토", "info", "검토한 변경만 저장합니다." if self.gateway else "저장 기능 미연결 · 변경 내용을 검토할 수 있지만 저장할 수 없습니다."),
            "saving": ("저장 중", "info", "결과를 기다리는 동안 추가 저장과 닫기를 막습니다."),
            "failed": ("저장 실패", "danger", "입력값을 유지했습니다. 내용을 확인한 뒤 다시 검토해 주세요."),
            "success": ("저장 완료", "success", "저장 요청의 성공 응답을 받았습니다."),
            "unknown": ("저장 결과 미확인", "warning", "자동 재시도하지 않습니다. 저장된 내용을 확인한 후 새로 시작해 주세요."),
        }
        title, tone, message = messages[self.state]
        self.status.setText(title)
        self.status.set_tone(tone)
        self.status.setMinimumWidth(self.status.fontMetrics().horizontalAdvance(title) + 24)
        self.notice.setText(message)

    def _review(self):
        """검증이 통과한 변경만 불변 스냅샷으로 검토 화면에 표시한다."""
        if self.state not in {"edit", "failed"} or not self.draft.request().changes:
            return
        errors = self.draft.errors()
        for key, widget in self.errors.items():
            widget.setText(errors.get(key, ""))
        if errors:
            self.notice.setText("입력 오류를 확인해 주세요.")
            self.inputs[next(iter(errors))].setFocus()
            return
        self.reviewed = self.draft.request()
        fields = {f.key: f for f in self.draft.fields}
        self.review_model.set_rows(tuple((fields[key].title,
            fields[key].original if fields[key].original is not None else "원본 미연결", before, after)
            for key, before, after in self.reviewed.changes))
        self.review_table.resizeRowsToContents()
        self.state = "review"
        self._render()

    def _edit(self):
        """검토를 취소하고 입력을 유지한 채 초안으로 돌아간다."""
        if self.state == "review":
            self.state, self.reviewed = "edit", None
            self._render()

    def _save(self):
        """검토된 변경만 한 번 제출하고 완료 응답을 비동기로 받는다."""
        if self.state != "review" or self.gateway is None or self.reviewed != self.draft.request():
            return
        if self.draft.errors():
            self._edit()
            self._review()
            return
        self.state = "saving"
        self.attempt += 1
        attempt = self.attempt
        self._render()
        try:
            self.gateway.submit(self.reviewed, lambda result: self.settled.emit(attempt, result))
        except Exception:
            self.settled.emit(attempt, SaveOutcome("unknown"))

    def _complete(self, attempt, result):
        """현재 저장 요청의 첫 완료 응답만 적용한다.

        param
        - attempt: 제출 시점의 요청 번호. 오래된 응답은 무시한다.
        - result: 저장 결과. 잘못된 형식도 미확인으로 처리한다.
        """
        if self.state != "saving" or attempt != self.attempt:
            return
        self.state = result.status if isinstance(result, SaveOutcome) and result.status in {
            "success", "failed", "unknown"} else "unknown"
        self._render()

    def reject(self):
        """취소·Escape를 동일하게 처리하고 변경이 있으면 폐기를 확인한다."""
        if self.state == "saving":
            return
        if self.state == "success":
            super().accept()
            return
        if self.state not in {"success", "unknown"} and self.draft.request().changes:
            answer = QMessageBox.question(self, "수정 취소", "저장하지 않은 수정 내용을 버릴까요?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return
        self.draft.values = {f.key: f.current for f in self.draft.fields}
        self.reviewed = None
        super().reject()

    def closeEvent(self, event):
        """창 닫기에도 저장 중 차단과 초안 폐기 확인을 적용한다.

        param
        - event: Qt 창 닫기 이벤트.
        """
        event.ignore()
        self.reject()
