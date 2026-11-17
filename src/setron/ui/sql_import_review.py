"""SQL 병합의 기존값·가져온값과 적용할 값을 나란히 검토한다."""

from datetime import date, datetime
from html import escape

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QHeaderView, QLineEdit, QStyledItemDelegate, QTableView,
    QPlainTextEdit, QStyle, QStyleOptionComboBox, QVBoxLayout, QWidget,
)

from setron.ui.combo_box import ComboBox
from setron.ui.components import button, label
from setron.ui.import_widgets import import_combo_style
from setron.ui.theme import SECONDARY


CHOICES = (("선택하세요", None), ("현재값 유지", "CURRENT"), ("가져온값 사용", "INCOMING"))
FIELD_LABELS = {
    "line": "라인", "process_name": "공정", "site": "사업장", "model_name": "모델",
    "production_area": "Memory/Foundry", "structure_type": "구조", "geometry_type": "형상",
    "modification_type": "개조 구분", "group_name": "설비군", "category": "분류",
    "family_name": "SW 계열", "version_raw": "버전", "pm_name": "PM", "family": "SW 계열",
    "version": "버전", "group": "설비군", "pms": "PM", "software": "SW", "versions": "버전",
    "content": "내용", "item_no": "Item 번호", "jira_code": "Jira", "version_order": "릴리즈 순서",
    "released_at": "출시일", "release_date": "출시일", "item_state": "포함 상태",
    "target_scope": "적용 범위", "target_group_ids": "적용 모델", "risk_reason": "위험 사유",
    "is_enabled": "사용 여부", "analysis_enabled": "분석 여부", "setting_value": "설정값",
}


def value_text(value, depth=0):
    """원문은 보존하고 바이너리·날짜·묶음 값을 읽을 수 있는 일반 텍스트로 표시한다."""
    if value is None:
        return "(없음)"
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, dict):
        if not value:
            return "(없음)"
        return "\n".join(f"{FIELD_LABELS.get(str(key), str(key))}: {value_text(member, depth + 1)}"
                         for key, member in value.items())
    if isinstance(value, (list, tuple)):
        if not value:
            return "(없음)"
        return "\n".join(f"• {value_text(member, depth + 1)}" for member in value)
    return str(value) if str(value) else "(빈 값)"


class SqlDifferenceModel(QAbstractTableModel):
    choices_changed = Signal()
    headers = ("항목", "비교 항목", "현재 PC 값", "가져온 값", "선택", "적용할 값")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.differences = ()
        self.choices = {}

    def set_differences(self, differences):
        self.beginResetModel()
        self.differences = tuple(differences)
        self.choices = {item.id: item.choice for item in self.differences
                        if item.choice in {"CURRENT", "INCOMING"}}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.differences)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section] if orientation == Qt.Orientation.Horizontal else section + 1
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self.differences[index.row()]
        choice = self.choices.get(item.id)
        if role == Qt.ItemDataRole.EditRole and index.column() == 4:
            return choice
        if role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            return None
        field = FIELD_LABELS.get(item.field, item.field)
        if item.grouped:
            field += " (묶음)"
        chosen = item.current if choice == "CURRENT" else item.incoming if choice == "INCOMING" else "(선택 필요)"
        values = (item.label, field, value_text(item.current), value_text(item.incoming),
                  dict((value, title) for title, value in CHOICES).get(choice, "선택하세요"), value_text(chosen))
        text = str(values[index.column()])
        if role == Qt.ItemDataRole.ToolTipRole:
            return "<pre>" + escape(text) + "</pre>"
        return text.replace("\n", " · ")

    def flags(self, index):
        result = super().flags(index)
        return result | Qt.ItemFlag.ItemIsEditable if index.column() == 4 else result

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or index.column() != 4:
            return False
        if value not in {None, "CURRENT", "INCOMING"}:
            return False
        item = self.differences[index.row()]
        if self.choices.get(item.id) == value:
            return False
        if value is None:
            self.choices.pop(item.id, None)
        else:
            self.choices[item.id] = value
        self.dataChanged.emit(self.index(index.row(), 4), self.index(index.row(), 5))
        self.choices_changed.emit()
        return True

    def choose_all(self, choice):
        if choice not in {"CURRENT", "INCOMING"} or not self.differences:
            return
        self.choices = {item.id: choice for item in self.differences}
        self.dataChanged.emit(self.index(0, 4), self.index(len(self.differences) - 1, 5))
        self.choices_changed.emit()


class _ChoiceDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        box = QStyleOptionComboBox()
        box.rect = option.rect.adjusted(3, 4, -3, -4)
        box.state = option.state
        box.currentText = str(index.data() or "선택하세요")
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, box, painter, option.widget)
        style.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, box, painter, option.widget)

    def createEditor(self, parent, option, index):
        editor = ComboBox(parent)
        editor.setStyleSheet(import_combo_style())
        editor.setAccessibleName("적용할 값 선택")
        for title, choice in CHOICES:
            editor.addItem(title, choice)
        def commit():
            self.commitData.emit(editor)
            self.closeEditor.emit(editor)
        editor.activated.connect(lambda _: commit())
        return editor

    def setEditorData(self, editor, index):
        editor.setCurrentIndex(max(0, editor.findData(index.data(Qt.ItemDataRole.EditRole))))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)


class SqlComparisonWidget(QWidget):
    choices_changed = Signal()
    review_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)
        layout.addWidget(label("값 비교와 적용 선택", 15, medium=True))
        layout.addWidget(label("값이 다른 항목마다 현재값 유지 또는 가져온값 사용을 선택하세요. "
                               "PM·SW 등 연결된 자료는 한 묶음으로 적용합니다.", 12, color=SECONDARY, wrap=True))
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("항목 또는 값 검색")
        self.search.setAccessibleName("비교 항목 검색")
        self.keep_button = button("현재값 모두 유지")
        self.incoming_button = button("가져온값 모두 선택")
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.keep_button)
        toolbar.addWidget(self.incoming_button)
        layout.addLayout(toolbar)
        self.model = SqlDifferenceModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.table = QTableView()
        self.table.setAccessibleName("SQL 변경값 비교")
        self.table.setModel(self.proxy)
        self.table.setItemDelegateForColumn(4, _ChoiceDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.horizontalHeader().setMinimumSectionSize(90)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(220)
        self.table.setMaximumHeight(390)
        layout.addWidget(self.table)
        detail = QHBoxLayout()
        self.current_detail, self.incoming_detail, self.applied_detail = (QPlainTextEdit() for _ in range(3))
        for title, editor in (("현재 PC 값 전체", self.current_detail), ("가져온 값 전체", self.incoming_detail),
                              ("적용할 값 전체", self.applied_detail)):
            column = QVBoxLayout()
            column.addWidget(label(title, 12, color=SECONDARY))
            editor.setReadOnly(True)
            editor.setAccessibleName(title)
            editor.setMinimumHeight(80)
            editor.setMaximumHeight(140)
            column.addWidget(editor)
            detail.addLayout(column, 1)
        layout.addLayout(detail)
        footer = QHBoxLayout()
        self.count_label = label("", 12, wrap=True)
        self.review_button = button("선택 내용 검토")
        footer.addWidget(self.count_label, 1)
        footer.addWidget(self.review_button)
        layout.addLayout(footer)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.keep_button.clicked.connect(lambda: self.model.choose_all("CURRENT"))
        self.incoming_button.clicked.connect(lambda: self.model.choose_all("INCOMING"))
        self.review_button.clicked.connect(self.review_requested)
        self.model.choices_changed.connect(self._choices_changed)
        self.table.selectionModel().currentRowChanged.connect(self._show_detail)
        self.table.clicked.connect(lambda index: self.table.edit(index) if index.column() == 4 else None)

    @property
    def choices(self):
        return dict(self.model.choices)

    @property
    def unresolved(self):
        return len(self.model.differences) - len(self.model.choices)

    def set_differences(self, differences):
        self.search.clear()
        self.model.set_differences(differences)
        self._update_count()
        if self.proxy.rowCount():
            self.table.setCurrentIndex(self.proxy.index(0, 0))
        else:
            for editor in (self.current_detail, self.incoming_detail, self.applied_detail):
                editor.clear()
        self.setVisible(bool(self.model.differences))

    def _update_count(self):
        current = sum(choice == "CURRENT" for choice in self.model.choices.values())
        incoming = sum(choice == "INCOMING" for choice in self.model.choices.values())
        self.count_label.setText(f"전체 {len(self.model.differences):,}건 · 선택 필요 {self.unresolved:,}건 · "
                                f"현재값 유지 {current:,}건 · 가져온값 사용 {incoming:,}건")

    def _choices_changed(self):
        self._update_count()
        self._show_detail(self.table.currentIndex())
        self.choices_changed.emit()

    def _show_detail(self, current, previous=QModelIndex()):
        source = self.proxy.mapToSource(current)
        if not source.isValid():
            return
        item = self.model.differences[source.row()]
        choice = self.model.choices.get(item.id)
        self.current_detail.setPlainText(value_text(item.current))
        self.incoming_detail.setPlainText(value_text(item.incoming))
        self.applied_detail.setPlainText(value_text(item.current if choice == "CURRENT" else item.incoming)
                                         if choice else "적용할 값을 선택해 주세요.")
