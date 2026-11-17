"""원본 문서마다 실제 열을 유지하는 읽기 표. 누적 판정 결과를 원본 행으로 위장하지 않는다."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget

from setron.ui.appearance import readable_ink, ui_color
from setron.ui.combo_box import ComboBox
from setron.ui.components import label
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.theme import INK, SECONDARY, font, paint_font, set_widget_font


def source_locations(refs):
    """출처 위치를 '문서 시트 셀 · 셀' 한 문장으로 묶는다. 같은 문서·시트의 셀은 한 줄에 모은다."""
    groups = {}
    for ref in refs:
        cells = groups.setdefault(" ".join(v for v in (ref.document, ref.sheet) if v), [])
        if ref.cells and ref.cells not in cells:
            cells.append(ref.cells)
    parts = [" ".join(v for v in (name, " · ".join(cells)) if v) for name, cells in groups.items()]
    parts = [p for p in parts if p]
    return "원본 위치: " + " / ".join(parts) if parts else ""


def show_item_source(parent, data, item_id):
    # PopupDialog는 item_risk_dialog에 있고 그쪽이 이 모듈을 가져오므로 순환을 피하려고 여기서 가져온다.
    from setron.ui.components import button
    from setron.ui.item_risk_dialog import PopupDialog, _ClampLabel
    entry = next((e for e in data.versions.items if e.item.item_id == item_id), None)
    if entry is None:
        return
    dialog = PopupDialog(parent, "아이템 원본 근거")
    body = dialog.body
    body.setSpacing(10)
    content = _ClampLabel(2)
    set_widget_font(content, font(15, medium=True))
    content.set_full_text(entry.item.content)
    body.addWidget(content)
    table = ItemOriginalTable()
    rows = tuple(r for r in data.versions.original_rows if r.item_id == item_id)
    table.set_rows(rows, (item_id,))
    body.addWidget(table)
    location = source_locations(entry.sources)
    if location:
        where = label(location, 12, color=SECONDARY, wrap=True)
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(where)
    if not rows:
        body.addWidget(label("보관된 원본 행이 없습니다. 위 내용은 저장된 관리값입니다.", 12, color=SECONDARY, wrap=True))
    footer = QHBoxLayout()
    footer.addStretch()
    close = button("닫기", primary=True)
    close.clicked.connect(dialog.accept)
    footer.addWidget(close)
    body.addLayout(footer)
    dialog.resize(900, 350 if rows else 240)
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
        fill = ui_color("#edf0fd" if selected else "#fff6d2" if highlighted else "white")
        painter.fillRect(option.rect, fill)
        painter.setPen(ui_color("#dee0e5"))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.setPen(readable_ink(foreground if foreground is not None else ui_color(INK), fill))
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
        self.documents.hide()
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
        # 입력 작업이 하나뿐이면 선택할 것이 없으니 콤보 대신 보조 텍스트 한 줄로 낮춘다.
        self.documents.setVisible(len(keys) > 1)
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
        self.table.base_widths = tuple(self._column_width(h) for h in self.model.headings)
        self.table.resizeColumnsToContents()
        for i, width in enumerate(self.table.base_widths):
            self.table.setColumnWidth(i, width)
        self.table.resizeRowsToContents()
        self.table.setFixedHeight(max(100, min(620, 44 + self.table.horizontalScrollBar().sizeHint().height() + sum(self.table.rowHeight(i) for i in range(len(rows))))))
        info = []
        if key and self.documents.isHidden():
            info.append(f"입력 작업 {key[0]} · {key[1]} · {len(key[2])}개 원본 열")
        if rows:
            info.append("원본 행 " + ", ".join(str(r.row) for r in rows))
            info.append("저장된 원문 그대로 · 노란색: 검색 근거")
        self.position.setText(" · ".join(info))
        self.table.horizontalScrollBar().setValue(0)

    @staticmethod
    def _column_width(heading):
        """핵심 열(버전명·No·항목·Update내용)이 가로 스크롤 없이 먼저 보이도록 앞쪽 열은 좁게, 내용 열은 넓게 잡는다."""
        key = _column_key(heading)
        if any(w in key for w in ("내용", "update")):
            return 330
        if key == "no":
            return 56
        if "버전" in key or key in ("항목", "version"):
            return 100
        return 110

    def activate(self, index):
        if index.isValid() and 0 <= index.row() < len(self.model.ids):
            self.item_selected.emit(self.model.ids[index.row()])
