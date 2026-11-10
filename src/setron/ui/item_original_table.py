"""원본 문서마다 실제 열을 유지하는 읽기 표. 누적 판정 결과를 원본 행으로 위장하지 않는다."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget

from setron.ui.appearance import ui_color
from setron.ui.combo_box import ComboBox
from setron.ui.components import label
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.theme import INK, SECONDARY, paint_font


def show_item_source(parent, data, item_id):
    entry = next((e for e in data.versions.items if e.item.item_id == item_id), None)
    if entry is None:
        return
    dialog = QDialog(parent)
    dialog.setWindowTitle("아이템 원본 근거")
    layout = QVBoxLayout(dialog)
    layout.addWidget(label(entry.item.content, 15, medium=True, wrap=True))
    table = ItemOriginalTable()
    layout.addWidget(table)
    rows = tuple(r for r in data.versions.original_rows if r.item_id == item_id)
    table.set_rows(rows, (item_id,))
    for ref in entry.sources:
        layout.addWidget(label(" · ".join(str(v) for v in (ref.document, ref.sheet, ref.cells) if v), wrap=True))
    if not rows:
        layout.addWidget(label("보관된 원본 행이 없습니다. 위 내용은 저장된 관리값입니다.", wrap=True))
    close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    close.rejected.connect(dialog.reject)
    layout.addWidget(close)
    dialog.resize(720, 360)
    dialog.exec()


def _column_key(text):
    return "".join(text.split()).casefold()


_C4_CENTERED = frozenset(("no", "항목", "remark", "담당", "normalc4", "dfb", "dex",
                          "우선순위", "비활성화가능", "비활성화가능여부", "hw연관", "고객사연관"))
_C4_MARKERS = frozenset(("normalc4", "dfb", "dex", "hw연관", "고객사연관"))
_C4_DISABLE = frozenset(("비활성화가능", "비활성화가능여부"))


class EvidenceDelegate(QStyledItemDelegate):
    """원본 근거 강조를 QSS와 무관하게 그린다. 긴 원문은 표의 셀 높이에 맞춰 줄바꿈한다."""
    def paint(self, painter, option, index):
        model = index.model()
        highlighted = bool(model.ids and model.ids[index.row()] in model.highlighted)
        foreground = index.data(Qt.ItemDataRole.ForegroundRole)
        if not highlighted and foreground is None:
            return super().paint(painter, option, index)
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(option.rect, ui_color("#edf0fd" if selected else "#fff6d2" if highlighted else "white"))
        painter.setPen(ui_color("#dee0e5"))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.setPen(foreground if foreground is not None else ui_color(INK))
        painter.setFont(paint_font(12, medium=True) if highlighted else index.data(Qt.ItemDataRole.FontRole))
        painter.drawText(option.rect.adjusted(12, 6, -12, -6),
                         index.data(Qt.ItemDataRole.TextAlignmentRole) | Qt.TextFlag.TextWordWrap,
                         str(index.data() or ""))
        painter.restore()


class OriginalModel(ReadTableModel):
    def __init__(self):
        super().__init__(())
        self.highlighted = frozenset()

    def _c4_column(self, column):
        # C4 원본 양식만 꾸민다. O5 등 다른 문서의 X/불가는 재해석하지 않는다.
        if any(_column_key(h) == "normalc4" for h in self.headings):
            return _column_key(self.headings[column])
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.headings)
                and role == Qt.ItemDataRole.TextAlignmentRole and self._c4_column(section) in _C4_CENTERED):
            return Qt.AlignmentFlag.AlignCenter
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid():
            column = self._c4_column(index.column())
            if role == Qt.ItemDataRole.TextAlignmentRole and column in _C4_CENTERED:
                return Qt.AlignmentFlag.AlignCenter
            if role == Qt.ItemDataRole.ForegroundRole:
                value = self.rows[index.row()][index.column()].strip()
                if (column in _C4_MARKERS and value.casefold() == "x"
                        or column in _C4_DISABLE and value == "불가"):
                    return ui_color("#c23345")
            if role == Qt.ItemDataRole.BackgroundRole and self.ids and self.ids[index.row()] in self.highlighted:
                return ui_color("#fff6d2")
        return super().data(index, role)


class ItemOriginalTable(QWidget):
    item_selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.rows = ()
        self.highlighted = frozenset()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.documents = ComboBox()
        self.documents.setAccessibleName("릴리즈노트 원본 문서·시트")
        self.documents.setProperty("queryField", True)
        self.documents.setFixedHeight(40)
        self.documents.currentIndexChanged.connect(self.render)
        layout.addWidget(self.documents)
        self.position = label("", 12, color=SECONDARY, wrap=True)
        layout.addWidget(self.position)
        self.model = OriginalModel()
        self.table = read_table(self.model, "릴리즈노트 원본 전체 열", ())
        self.table.setItemDelegate(EvidenceDelegate(self.table))
        self.table.clicked.connect(self.activate)
        self.table.activated.connect(self.activate)
        layout.addWidget(self.table)
        self.hide()

    def set_rows(self, rows, highlighted=()):
        self.rows, self.highlighted = tuple(rows), frozenset(highlighted)
        previous = self.documents.currentData()
        self.documents.blockSignals(True)
        self.documents.clear()
        # 서로 다른 원본 열 구조는 별도 선택지로 유지한다.
        keys = tuple(dict.fromkeys((r.batch_id, r.sheet, r.headers) for r in self.rows))
        for key in keys:
            self.documents.addItem(f"입력 작업 {key[0]} · {key[1]} · {len(key[2])}개 원본 열", key)
        self.documents.setCurrentIndex(max(0, self.documents.findData(previous)))
        self.documents.blockSignals(False)
        self.render()
        self.setVisible(bool(keys))

    def render(self, *_):
        key = self.documents.currentData()
        rows = [r for r in self.rows if (r.batch_id, r.sheet, r.headers) == key]
        self.model.beginResetModel()
        self.model.headings = key[2] if key else ()
        self.model.rows = tuple(r.values for r in rows)
        self.model.ids = tuple(r.item_id for r in rows)
        self.model.highlighted = self.highlighted
        self.model.endResetModel()
        self.table.base_widths = tuple(330 if any(w in h.casefold() for w in ("내용", "update")) else 135
                                       for h in self.model.headings)
        self.table.resizeColumnsToContents()
        for i, width in enumerate(self.table.base_widths):
            self.table.setColumnWidth(i, width)
        self.table.resizeRowsToContents()
        self.table.setFixedHeight(max(100, min(620, 44 + sum(self.table.rowHeight(i) for i in range(len(rows))))))
        self.position.setText("원본 행 " + ", ".join(str(r.row) for r in rows)
                              + " · 저장된 원문 그대로 표시 · 노란색: 검색 근거" if rows else "")

    def activate(self, index):
        if index.isValid() and 0 <= index.row() < len(self.model.ids):
            self.item_selected.emit(self.model.ids[index.row()])
