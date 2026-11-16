"""데이터 가져오기 검토 표의 '여러 칸 한 번에 바꾸기' 화면 도구 (최종 멘토링 11-16).

- 표에서 여러 칸을 고른 뒤 값을 입력하면 고른 칸이 모두 바뀐다 (Excel처럼 드래그 · Shift · Ctrl로 고른다).
- Ctrl+V: Excel에서 복사한 열 · 표를 고른 칸부터 붙여 넣는다. 값 하나를 여러 칸에 붙이면 고른 칸 모두에 같은 값.
- Ctrl+C: 고른 칸을 복사한다. Delete: 고른 칸을 비운다.
- [찾아 바꾸기]: 한 항목에서 찾을 글자를 바꿀 글자로 바꾼다 (예: 설비 호기 'ESPG' → 'ESPH').

계산(변경 목록)은 ``features.excel_import.bulk_edit``가 하고, 이 파일은 키 입력 · 창만 맡는다.
"""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QApplication, QCheckBox, QFormLayout, QLineEdit

from setron.ui.combo_box import ComboBox
from setron.ui.components import label
from setron.ui.import_dialogs import ImportDialog
from setron.ui.import_widgets import import_combo_style
from setron.ui.theme import SECONDARY, TONES


class BulkEditKeys(QObject):
    """검토 표의 Ctrl+V · Ctrl+C · Delete를 화면 함수로 넘긴다. 칸 편집 중(입력칸이 열린 동안)에는 손대지 않는다."""

    def __init__(self, table, *, paste, copy, clear):
        super().__init__(table)
        self.table, self.paste, self.copy, self.clear = table, paste, copy, clear
        table.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.table and event.type() == QEvent.Type.KeyPress \
                and self.table.state() != QAbstractItemView.State.EditingState:
            if event.matches(QKeySequence.StandardKey.Paste):
                return bool(self.paste()) or True
            if event.matches(QKeySequence.StandardKey.Copy):
                return bool(self.copy()) or True
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and event.modifiers() == Qt.KeyboardModifier.NoModifier:
                return bool(self.clear()) or True
        return False


def clipboard_text():
    return QApplication.clipboard().text()


def set_clipboard_text(text):
    QApplication.clipboard().setText(text)


class FindReplaceDialog(ImportDialog):
    """한 항목에서 찾을 글자를 바꿀 글자로 바꾼다. [바꾸기]를 누르면 ``values()``로 입력값을 돌려준다."""

    def __init__(self, fields, parent=None, *, selected_rows=0):
        super().__init__("찾아 바꾸기", parent)
        self.body.addWidget(label("고른 항목에서 찾을 글자를 바꿀 글자로 한 번에 바꿉니다. 원본 값은 그대로 두고 "
                                  "수정값으로 기록되며, 제외한 행은 바꾸지 않습니다.", 13, color=SECONDARY, wrap=True))
        form = QFormLayout()
        form.setSpacing(10)
        self.field = ComboBox()
        self.field.setStyleSheet(import_combo_style())
        self.field.setAccessibleName("바꿀 항목")
        for name, text in fields:
            self.field.addItem(text, name)
        self.find = QLineEdit()
        self.find.setPlaceholderText("예) C4(DFB)")
        self.find.setAccessibleName("찾을 글자")
        self.replacement = QLineEdit()
        self.replacement.setPlaceholderText("예) M-C4 DFB (비우면 찾은 글자를 지웁니다)")
        self.replacement.setAccessibleName("바꿀 글자")
        self.whole = QCheckBox("칸 전체가 같을 때만 바꾸기")
        self.whole.setToolTip("끄면 칸 안의 일부 글자도 바꿉니다 (예: 'ESPG' → 'ESPH'이면 ESPG12 → ESPH12).")
        self.only_selected = QCheckBox(f"고른 행({selected_rows}개)에서만 바꾸기")
        self.only_selected.setVisible(selected_rows > 1)
        form.addRow("항목", self.field)
        form.addRow("찾을 글자", self.find)
        form.addRow("바꿀 글자", self.replacement)
        self.body.addLayout(form)
        self.body.addWidget(self.whole)
        self.body.addWidget(self.only_selected)
        self.error = label("", 12, color=TONES["danger"][1], wrap=True)
        self.body.addWidget(self.error)
        self.add_action("취소", self.reject)
        self.add_action("바꾸기", self._accept, primary=True, default=True)
        self.finish_layout()

    def _accept(self):
        if not self.find.text() and not self.whole.isChecked():
            self.error.setText("찾을 글자를 입력해 주세요. 빈 칸을 채우려면 '칸 전체가 같을 때만 바꾸기'를 켜 주세요.")
            return
        self.accept()

    def values(self):
        return (self.field.currentData(), self.find.text(), self.replacement.text(), self.whole.isChecked(),
                self.only_selected.isVisible() and self.only_selected.isChecked())
