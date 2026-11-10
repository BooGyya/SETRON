"""기존 내보내기 작업을 선택하는 독립 화면."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from setron.ui.components import Card, button, label
from setron.ui.theme import SECONDARY


EXPORT_OPTIONS = (
    ("sql", "DB 전체 SQL", "데이터베이스 전체를 복원 가능한 SQL 파일로 저장합니다."),
    ("summary", "관리값 요약 Excel", "현재 관리값을 요약한 Excel 통합문서를 저장합니다."),
    ("management", "관리 자료 XLSX", "현재 관리값과 보관 원본을 Excel 통합문서로 저장합니다."),
    ("status", "현재 조회 현황 XLSX", "대시보드에 마지막으로 적용한 조회 조건으로 설비 현황을 저장합니다."),
)


class DataExportPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.buttons, self.details = {}, {}
        for key, title, description in EXPORT_OPTIONS:
            card = Card(padding=18)
            row = QHBoxLayout()
            row.setSpacing(16)
            captions = QVBoxLayout()
            captions.setSpacing(5)
            captions.addWidget(label(title, 16, medium=True, wrap=True))
            detail = label(description, 13, color=SECONDARY, wrap=True)
            captions.addWidget(detail)
            row.addLayout(captions, 1)
            action = button("내보내기", primary=True)
            action.setAccessibleName(f"{title} 내보내기")
            action.setEnabled(False)
            row.addWidget(action, alignment=Qt.AlignmentFlag.AlignVCenter)
            card.body.addLayout(row)
            layout.addWidget(card)
            self.buttons[key], self.details[key] = action, detail

    def set_availability(self, reasons, *, busy=""):
        for key, _, description in EXPORT_OPTIONS:
            reason = reasons[key] or busy
            self.buttons[key].setEnabled(not reason)
            self.details[key].setText(f"{description} {reason}" if reason else description)
