"""데이터 관리 (11-10 개편, 11-11 2차 수정): 설비현황 · 릴리즈노트 · 변경 이력 세 탭.

- 설비현황: 현황표(가져온 엑셀 시트를 그대로 보여 주고 칸을 고치거나 행을 지움) · 설비(설비군 추가) ·
  위치(Site 추가, DB 관리 설정에 저장) · 가져온 엑셀 삭제(데이터 가져오기에서 옮겨 온 기준일 단위 삭제 화면).
- 릴리즈노트: 계열 목록(개조형은 상위 계열 아래) · 버전 줄 아래 Item 줄이 붙은 엑셀형 표. 위험 지정은 아이템 매니저에서만 한다.
- 변경 이력: 수정 · 등록 · 삭제를 수정 일시 · 파일 · 시트·셀 · 수정 전 → 후로 보인다.

데이터를 고치거나 더하거나 지우면 [저장] 단계 없이 바로 DB에 반영하고(11-11 결정) ``saved``로 알려 다른 화면이 다시 읽는다.
되돌릴 수 없는 행 삭제만 한 번 묻는다. DB 읽기·저장은 모두 워커에서 한다.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QStackedWidget, QTabBar, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from setron.features.management.schemas import RecordEdit
from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, button, label
from setron.ui.equipment import ignore_width
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.theme import LINE, PRIMARY, SECONDARY

SAVING = QColor("#fff2c4")     # 저장 중인 칸
SAVED = QColor("#e3f4e8")      # 이번에 저장한 칸
HEADER = QColor("#eef1f8")     # 시트 머리글 행
MUTED = QColor("#5d6678")      # DB와 연결되지 않은 원본 값
DELETED = QColor("#a0a7b4")    # 지운 행
DANGER = "#ad344d"
TABLE_STYLE = "QTableWidget {font-size:14px;} QHeaderView::section {font-size:13px;padding:4px 6px;}"


def _col(index):
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _split(address):
    letters = "".join(c for c in address if c.isalpha()).upper()
    digits = "".join(c for c in address if c.isdigit())
    number = 0
    for c in letters:
        number = number * 26 + ord(c) - 64
    return number - 1, int(digits or 0)


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _table(headers, name):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAccessibleName(name)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(34)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.setStyleSheet(TABLE_STYLE)
    table.setMinimumHeight(320)
    return table


def _fill(table, rows, keys=None):
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(_text(value))
            item.setToolTip(_text(value))
            if keys is not None and c == 0:
                item.setData(Qt.ItemDataRole.UserRole, keys[r])
            table.setItem(r, c, item)


def _segment(names, name):
    """하위 탭: 하나만 고르는 버튼 묶음."""
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    group = QButtonGroup(host)
    group.setExclusive(True)
    buttons = []
    for i, text in enumerate(names):
        control = button(text)
        control.setCheckable(True)
        control.setChecked(i == 0)
        control.setAccessibleName(f"{name} {text}")
        control.setStyleSheet(f"QPushButton {{background:white;border:1px solid {LINE};border-radius:10px;padding:0 16px;"
                              "min-height:34px;max-height:34px;}"
                              f"QPushButton:checked {{background:#e8ebfc;border-color:#9aa8f5;color:{PRIMARY};}}")
        group.addButton(control, i)
        buttons.append(control)
        row.addWidget(control)
    row.addStretch()
    return host, group, buttons


class _Panel(QWidget):
    """읽기 워커 하나를 가진 하위 화면."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.request = ReadRequest(self)
        self.request.completed.connect(self._done)
        self._handler = None
        self.loaded = False
        self.saved_note = ""  # 저장 직후 다시 읽어도 '저장했습니다' 안내를 남긴다
        self.notice = label("", 13, color=SECONDARY, wrap=True)

    def run(self, work, handler):
        if self.page.gateway is None or self.request.busy:
            return False
        self._handler = handler
        return self.request.start(BackgroundRead(work))

    def _done(self, value, error):
        handler, self._handler = self._handler, None
        if handler is not None:
            handler(value, error)

    def say(self, text, *, error=False):
        self.notice.setText(text)
        self.notice.setStyleSheet(f"color:{DANGER};" if error else f"color:{SECONDARY};")

    def flush(self):
        """다른 저장이 끝난 뒤 밀린 수정을 저장한다 (칸 편집 패널만)."""

    def _sync(self):
        """저장 중에는 버튼을 잠근다."""


class _SheetTable(QTableWidget):
    """엑셀 시트 표. 열이 적으면 남는 폭을 열마다 나눠 표를 꽉 채운다 (시트가 왼쪽에 작게 몰리지 않게)."""

    def __init__(self):
        super().__init__(0, 0)
        self.natural = []

    def measure(self, texts):
        """열마다 가장 긴 글자 폭(머리글 행은 굵게)으로 기본 폭을 정한다. 아주 긴 값은 420px에서 자른다."""
        normal = QFontMetrics(self.font())
        bold_font = QFont(self.font())
        bold_font.setBold(True)
        bold = QFontMetrics(bold_font)
        widths = [72] * self.columnCount()
        for (column, header), text in texts.items():
            metrics = bold if header else normal
            widths[column] = max(widths[column], metrics.horizontalAdvance(text) + 30)
        self.natural = [min(w, 420) for w in widths]
        self.fit()

    def fit(self):
        if not self.natural:
            return
        spare = self.viewport().width() - sum(self.natural)
        extra = spare // len(self.natural) if spare > 0 else 0
        for column, width in enumerate(self.natural):
            self.setColumnWidth(column, width + extra)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()


# --- 설비현황 › 현황표 ---------------------------------------------------------------------
class SheetPanel(_Panel):
    """가져온 엑셀 시트. 칸을 고치면 그 칸만 바로 저장하고, 고른 설비의 행을 지운다 (11-11)."""

    def __init__(self, page):
        super().__init__(page)
        self.sheets, self.grid, self.originals, self.edits, self.saving_cells = (), None, {}, {}, {}
        self.items, self.first_row = {}, 0
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        card = Card(padding=20)
        body.addWidget(card)
        row = QHBoxLayout()
        self.sheet_combo = ComboBox()
        self.sheet_combo.setAccessibleName("현황표 선택 (기준일 · Site · 시트)")
        self.sheet_combo.setMinimumWidth(360)
        self.sheet_combo.currentIndexChanged.connect(self._sheet_changed)
        self.source = label("", 12, color=SECONDARY)
        self.delete_button = button("선택 행 삭제")
        self.delete_button.setAccessibleName("고른 설비의 현황표 행 삭제")
        self.delete_button.setToolTip("고른 칸이 속한 설비의 이 현황표 행(설비 현황·SW 버전)을 DB에서 지웁니다.")
        self.delete_button.clicked.connect(self.delete_selected)
        for widget in (self.sheet_combo, self.source):
            row.addWidget(widget)
        row.addStretch()
        row.addWidget(self.delete_button)
        card.body.addLayout(row)
        self.table = _SheetTable()
        self.table.setAccessibleName("현황표 · 엑셀 시트 그대로")
        self.table.setStyleSheet(TABLE_STYLE)
        self.table.setMinimumHeight(560)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.itemChanged.connect(self._cell_changed)
        self.table.itemSelectionChanged.connect(self._sync)
        card.body.addWidget(self.table)
        card.body.addWidget(label("가져온 엑셀 시트를 원래 열·행 번호 그대로 보여 줍니다. DB 값과 연결된 칸을 두 번 눌러 고치면 "
                                  "바로 저장되고 변경 이력에 남습니다 (저장 중 노란색 → 저장됨 초록색). 행을 지우려면 그 설비의 칸이나 "
                                  "왼쪽 행 번호를 고른 뒤 [선택 행 삭제]를 누릅니다. Site · 기준일은 바꿀 수 없습니다.",
                                  12, color=SECONDARY, wrap=True))
        card.body.addWidget(self.notice)
        self._sync()

    def reload(self):
        self.say("현황표 목록을 읽고 있습니다…")
        return self.run(lambda: self.page.gateway.data_admin("status_sheets"), self._sheets_loaded)

    def _sheets_loaded(self, sheets, error):
        if error is not None:
            self.say("현황표 목록을 읽지 못했습니다. DB 연결을 확인해 주세요.", error=True)
            return
        self.loaded = True
        self.sheets = tuple(sheets)
        previous = self.sheet_combo.currentData()
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        for sheet in self.sheets:
            self.sheet_combo.addItem(f"{sheet.label} · {sheet.equipment_count}대", sheet.batch_id)
        self.sheet_combo.blockSignals(False)
        if not self.sheets:
            self.say(self.saved_note + "저장된 현황표가 없습니다. 데이터 가져오기에서 먼저 저장해 주세요.")
            self.saved_note = ""
            self.source.setText("")
            self._show(None)
            return
        index = max(0, self.sheet_combo.findData(previous))
        self.sheet_combo.setCurrentIndex(index)
        self._sheet_changed(index)

    def _sheet_changed(self, *_):
        batch_id = self.sheet_combo.currentData()
        if batch_id is None:
            return
        self.say("시트를 읽고 있습니다…")
        self.run(lambda: self.page.gateway.data_admin("sheet_grid", batch_id), self._grid_loaded)

    def _grid_loaded(self, grid, error):
        if error is not None:
            self.say("시트를 읽지 못했습니다. 다시 시도해 주세요.", error=True)
            return
        self._show(grid)
        sheet = next((s for s in self.sheets if s.batch_id == grid.batch_id), None)
        self.source.setText(f"원본 파일: {grid.workbook or '파일 이름 없음'} · 시트 {grid.sheet}"
                            + (f" · 가져온 시각 {_text(sheet.imported_at)[:16]}" if sheet and sheet.imported_at else ""))
        note, self.saved_note = self.saved_note, ""
        self.say(note + f"고칠 수 있는 칸 {sum(b.editable for b in grid.bindings.values())}개 · 고치면 바로 저장됩니다.")

    def _show(self, grid):
        self.grid, self.edits, self.originals, self.saving_cells, self.items = grid, {}, {}, {}, {}
        self.table.blockSignals(True)
        self.table.clear()
        self.table.clearSpans()
        addresses = (*grid.cells, *grid.bindings, *grid.deleted) if grid is not None else ()
        if not any(a in grid.bindings or a in grid.deleted or _text(grid.cells.get(a)).strip() for a in addresses):
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.table.natural = []
            self.table.blockSignals(False)
            self._sync()
            return
        positions = [_split(a) for a in addresses]
        columns = max(c for c, _ in positions) + 1
        # 값이 하나도 없는 행(엑셀의 빈 줄)은 보이지 않는다. 행 번호는 엑셀 그대로 둔다.
        rows = sorted({r for (_, r), a in zip(positions, addresses)
                       if a in grid.bindings or a in grid.deleted or _text(grid.cells.get(a)).strip()})
        data_start = min((_split(a)[1] for a in grid.bindings), default=rows[-1] + 1)
        self.first_row = rows[0]
        self.table.setColumnCount(columns)
        self.table.setRowCount(len(rows))
        self.table.setHorizontalHeaderLabels([_col(c) for c in range(columns)])
        self.table.setVerticalHeaderLabels([str(r) for r in rows])
        bold = QFont(self.table.font())
        bold.setBold(True)
        texts = {}
        for index, r in enumerate(rows):
            header = r < data_start
            values = []
            for c in range(columns):
                address = f"{_col(c)}{r}"
                binding = grid.bindings.get(address)
                value = binding.current if binding else _text(grid.cells.get(address))
                values.append(value)
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, address)
                self.items[address] = item
                if binding is not None and binding.editable:
                    item.setToolTip(f"{binding.label} · 두 번 눌러 고치면 바로 저장")
                    self.originals[address] = value
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if address in grid.deleted:
                        font = QFont(self.table.font())
                        font.setStrikeOut(True)
                        item.setFont(font)
                        item.setForeground(QBrush(DELETED))
                        item.setToolTip(grid.deleted[address])
                    elif header:
                        item.setFont(bold)
                        item.setBackground(QBrush(HEADER))
                    elif binding is None:
                        item.setForeground(QBrush(MUTED))
                    else:
                        item.setToolTip(f"{binding.label} · 고칠 수 없음")
                self.table.setItem(index, c, item)
            # 제목처럼 첫 칸에만 값이 있는 머리글 줄은 한 줄로 펼쳐 긴 제목이 첫 열을 넓히지 않게 한다.
            if header and columns > 1 and values[0] and not any(values[1:]):
                self.table.setSpan(index, 0, 1, columns)
                continue
            for c, value in enumerate(values):
                if value:
                    key = (c, header)
                    texts[key] = max(texts.get(key, ""), value, key=len)
        self.table.measure(texts)
        self.table.blockSignals(False)
        self._sync()

    def _paint(self, address, state):
        """칸 상태: None(원래 값) · 'saving' · 'saved'."""
        item, binding = self.items.get(address), self.grid.bindings.get(address) if self.grid else None
        if item is None or binding is None:
            return
        self.table.blockSignals(True)
        if state is None:
            item.setBackground(QBrush())
            item.setToolTip(f"{binding.label} · 두 번 눌러 고치면 바로 저장")
        else:
            item.setBackground(QBrush(SAVING if state == "saving" else SAVED))
            before, after = self.saving_cells.get(address, (self.originals.get(address), item.text()))
            item.setToolTip(f"{binding.label}: {before or '(빈칸)'} → {after or '(빈칸)'}"
                            + (" · 저장 중" if state == "saving" else " · 저장됨"))
        self.table.blockSignals(False)

    def _cell_changed(self, item):
        address = item.data(Qt.ItemDataRole.UserRole)
        if address not in self.originals:
            return
        value, original = item.text().strip(), self.originals[address]
        if value == original:
            self.edits.pop(address, None)
            if address not in self.saving_cells:
                self._paint(address, None)
            return
        self.edits[address] = value
        self.saving_cells[address] = (original, value)
        self._paint(address, "saving")
        self.flush()

    def flush(self):
        if not self.edits or self.grid is None or self.page.saving:
            return False
        batch, grid = dict(self.edits), self.grid
        self.edits.clear()
        started = self.page.save_changes(self.changes(batch), self, done=lambda _: self._stored(grid, batch),
                                         failed=lambda _: self._rejected(grid, batch))
        if not started:
            self.edits.update(batch)
        return started

    def _stored(self, grid, batch):
        if grid is not self.grid:
            return
        for address, value in batch.items():
            self.originals[address] = value  # 이제 DB 값
            if address not in self.edits:
                self._paint(address, "saved")
        self.say(f"저장했습니다 · {', '.join(grid.bindings[a].label for a in batch)} · 변경 이력에 기록했습니다.")

    def _rejected(self, grid, batch):
        """저장하지 못한 칸은 DB 값(원래 값)으로 되돌린다."""
        if grid is not self.grid:
            return
        self.table.blockSignals(True)
        for address in batch:
            self.edits.pop(address, None)
            self.saving_cells.pop(address, None)
            if address in self.items:
                self.items[address].setText(self.originals[address])
        self.table.blockSignals(False)
        for address in batch:
            self._paint(address, None)

    def selected_rows(self):
        """고른 칸이 속한 설비 현황 {snapshot_id: 호기}."""
        if self.grid is None:
            return {}
        chosen = {}
        for item in self.table.selectedItems():
            address = item.data(Qt.ItemDataRole.UserRole)
            sid = self.grid.owners.get(address)
            if sid is not None:
                chosen.setdefault(sid, self.grid.bindings[address].label.split(" · ")[0])
        return chosen

    def _sync(self):
        busy = self.page.saving
        self.delete_button.setEnabled(bool(self.selected_rows()) and not busy)
        self.sheet_combo.setEnabled(not busy)

    def changes(self, edits=None):
        """고친 칸을 기록별 변경으로 묶는다. SW 버전을 바꾸면 릴리즈 연결은 비워 다시 판정한다."""
        grouped = defaultdict(dict)
        for address, value in (self.edits if edits is None else edits).items():
            binding = self.grid.bindings[address]
            key = (binding.table, tuple(sorted(binding.key.items())))
            grouped[key][binding.field] = value or None
            if binding.field == "version_raw":
                grouped[key]["release_version_id"] = None
        return [(table, dict(key), changes) for (table, key), changes in grouped.items()]

    def delete_selected(self):
        chosen = self.selected_rows()
        if not chosen or self.grid is None or self.page.saving:
            return False
        codes = list(chosen.values())
        sheet = next((s for s in self.sheets if s.batch_id == self.grid.batch_id), None)
        where = sheet.label if sheet else self.grid.sheet
        shown = ", ".join(codes[:8]) + (f" 외 {len(codes) - 8}대" if len(codes) > 8 else "")
        if QMessageBox.question(self, "현황표 행 삭제",
                                f"{where}에서 설비 {len(codes)}대의 행을 DB에서 지울까요?\n\n{shown}\n\n"
                                "그 설비의 이 기준일 현황·SW 버전이 지워지고 되돌릴 수 없습니다. 다른 기준일 자료와 "
                                "가져온 엑셀 원본 칸은 남고, 원본 칸은 '삭제됨'으로 보입니다.") != QMessageBox.StandardButton.Yes:
            return False
        batch_id, ids = self.grid.batch_id, tuple(chosen)
        return self.page.change(lambda: self.page.gateway.data_admin("delete_rows", batch_id, ids), self,
                                message="행을 지우고 있습니다…", done=lambda count: self._deleted(count, codes))

    def _deleted(self, count, codes):
        self.saved_note = f"설비 {count}대의 행을 지웠습니다 ({', '.join(codes[:4])}{' …' if len(codes) > 4 else ''}). "
        self.reload()


# --- 설비현황 › 설비 ------------------------------------------------------------------------
class AddGroupDialog(QDialog):
    def __init__(self, parent, groups):
        super().__init__(parent)
        self.setWindowTitle("설비군 추가")
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("C7")
        self.category = ComboBox()
        for value in ("POLY", "OXIDE", "KCTC"):
            self.category.addItem(value, value)
        self.parent_combo = ComboBox()
        self.parent_combo.addItem("없음 (기본형)", None)
        for group in groups:
            if group.parent_id is None and group.category in ("POLY", "OXIDE", "KCTC"):
                self.parent_combo.addItem(f"{group.name} 의 개조형", group.group_id)
        self.error = label("", 12, color=DANGER, wrap=True)
        form.addRow("설비군 이름", self.name)
        form.addRow("분류", self.category)
        form.addRow("상위 설비군", self.parent_combo)
        form.addRow(self.error)
        actions = QHBoxLayout()
        cancel, ok = button("취소"), button("추가", primary=True)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._accept)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(ok)
        form.addRow(actions)
        self.groups = groups

    def _accept(self):
        if not self.name.text().strip():
            self.error.setText("설비군 이름을 입력해 주세요.")
            return
        parent = self.parent_combo.currentData()
        if parent is not None:
            category = next(g.category for g in self.groups if g.group_id == parent)
            if category != self.category.currentData():
                self.error.setText(f"개조형은 상위 설비군과 같은 분류({category})여야 합니다.")
                return
        self.accept()

    def values(self):
        return self.name.text().strip(), self.category.currentData(), self.parent_combo.currentData()


class EquipmentPanel(_Panel):
    """왼쪽: 설비군 (개조형도 한 줄씩, 상위 설비군 바로 아래) · 오른쪽: 설비 (모델 · Site · Line)."""

    CATEGORY_ORDER = {"POLY": 0, "OXIDE": 1, "KCTC": 2, "HPA": 3}

    def __init__(self, page):
        super().__init__(page)
        self.groups, self.equipment, self.ordered = (), (), ()
        body = QHBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        left, right = Card(padding=20), Card(padding=20)
        body.addWidget(left, 2)
        body.addWidget(right, 3)
        head = QHBoxLayout()
        head.addWidget(label("설비군", 20, medium=True))
        head.addStretch()
        self.add_group_button = button("＋ 설비군 추가", primary=True)
        self.add_group_button.clicked.connect(self.add_group)
        head.addWidget(self.add_group_button)
        left.body.addLayout(head)
        self.group_table = _table(("분류", "설비군", "상위 설비군", "설비"), "설비군 목록")
        self.group_table.setMinimumHeight(420)
        for column in (0, 2, 3):
            self.group_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.group_table.itemSelectionChanged.connect(self._render_equipment)
        left.body.addWidget(self.group_table)
        left.body.addWidget(label("설비군을 추가하면 DB에 저장되고, 기본형은 대시보드·히트맵 설비군 선택지에 새로 보이며 "
                                  "개조형은 상위 설비군에 함께 묶여 보입니다.", 12, color=SECONDARY, wrap=True))
        left.body.addWidget(self.notice)
        rhead = QHBoxLayout()
        self.equipment_title = label("설비 목록", 20, medium=True)
        rhead.addWidget(self.equipment_title)
        rhead.addStretch()
        right.body.addLayout(rhead)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("설비 ID 검색")
        self.site_filter, self.line_filter = ComboBox(), ComboBox()
        for widget in (self.search, self.site_filter, self.line_filter):
            filters.addWidget(widget)
        self.search.textChanged.connect(self._render_equipment)
        self.site_filter.currentIndexChanged.connect(self._render_equipment)
        self.line_filter.currentIndexChanged.connect(self._render_equipment)
        right.body.addLayout(filters)
        self.equipment_table = _table(("설비 ID", "모델", "Site", "Line", "설치 기록", "최근 기준일"), "설비 목록")
        self.equipment_table.setMinimumHeight(420)
        right.body.addWidget(self.equipment_table)

    def reload(self):
        self.say("설비 목록을 읽고 있습니다…")
        return self.run(lambda: self.page.gateway.data_admin("equipment_overview"), self._loaded)

    def _loaded(self, value, error):
        if error is not None:
            self.say("설비 목록을 읽지 못했습니다.", error=True)
            return
        self.loaded = True
        self.groups, self.equipment = value
        note, self.saved_note = self.saved_note, ""
        self.say(note)
        children = defaultdict(list)
        for g in self.groups:
            if g.parent_id is not None:
                children[g.parent_id].append(g)
        bases = sorted((g for g in self.groups if g.parent_id is None),
                       key=lambda g: (self.CATEGORY_ORDER.get(g.category, 9), g.name))
        known = {g.group_id for g in bases}
        ordered = []
        for base in bases:
            ordered.append(base)
            ordered.extend(sorted(children[base.group_id], key=lambda g: g.name))
        ordered.extend(g for g in self.groups if g.parent_id is not None and g.parent_id not in known)
        self.ordered = tuple(ordered)
        _fill(self.group_table, [(g.category, g.name if g.parent_id is None else f"   ↳ {g.name}",
                                  g.parent_name or "—", f"{g.equipment_count}대") for g in self.ordered],
              [g.group_id for g in self.ordered])
        for combo, values, title in ((self.site_filter, sorted({e.site for e in self.equipment if e.site}), "Site 전체"),
                                     (self.line_filter, sorted({e.line for e in self.equipment if e.line}), "Line 전체")):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(title, None)
            for v in values:
                combo.addItem(v, v)
            combo.setCurrentIndex(max(0, combo.findData(current)))
            combo.blockSignals(False)
        self._render_equipment()

    def selected_group(self):
        rows = self.group_table.selectionModel().selectedRows() if self.group_table.selectionModel() else []
        if not rows:
            return None
        return self.group_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _render_equipment(self, *_):
        gid = self.selected_group()
        text = self.search.text().strip().casefold()
        site, line = self.site_filter.currentData(), self.line_filter.currentData()
        rows = [e for e in self.equipment if (gid is None or gid in e.group_ids)
                and (not text or text in e.code.casefold()) and (site is None or e.site == site)
                and (line is None or e.line == line)]
        name = next((g.name for g in self.groups if g.group_id == gid), None)
        self.equipment_title.setText(f"설비 목록 · {name + ' · ' if name else ''}{len(rows)}대")
        _fill(self.equipment_table, [(e.code, e.model_name or "모델 미확인",
                                      (e.site or "?") if e.snapshot_count else "관측 없음",
                                      (e.line or "미지정") if e.snapshot_count else "", f"{e.snapshot_count}건", e.base_date)
                                     for e in rows], [e.equipment_id for e in rows])

    def add_group(self):
        if self.page.gateway is None or self.page.saving:
            return False
        dialog = AddGroupDialog(self, self.groups)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        name, category, parent = dialog.values()
        return self.page.change(lambda: self.page.gateway.data_admin("create_group", name, category, parent), self,
                                message=f"설비군 '{name}'을 등록하고 있습니다…", done=self._group_added)

    def _group_added(self, group):
        parent = next((g.name for g in self.groups if g.group_id == group.parent_id), None)
        self.saved_note = (f"설비군 '{group.name}'을 등록했습니다 · "
                           + (f"대시보드·히트맵에서 '{parent}'에 함께 묶여 보입니다." if parent
                              else "대시보드·히트맵 설비군 선택지에 보입니다."))
        self.page.group_added.emit(group)
        self.reload()

    def _sync(self):
        self.add_group_button.setEnabled(not self.page.saving)


# --- 설비현황 › 위치 ------------------------------------------------------------------------
class LocationPanel(_Panel):
    """Site → Fab → Line 트리. Site는 관측에 나온 Site와 데이터 관리에서 등록한 Site(DB 관리 설정)를 함께 보인다."""

    def __init__(self, page):
        super().__init__(page)
        self.equipment, self.registered = (), ()
        body = QHBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        left, right = Card(padding=20), Card(padding=20)
        body.addWidget(left, 2)
        body.addWidget(right, 3)
        head = QHBoxLayout()
        head.addWidget(label("위치", 20, medium=True))
        head.addStretch()
        self.add_site_button = button("＋ Site 추가", primary=True)
        self.add_site_button.clicked.connect(self.add_site)
        head.addWidget(self.add_site_button)
        left.body.addLayout(head)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAccessibleName("위치 트리 (Site → Fab → Line)")
        self.tree.setMinimumHeight(360)
        self.tree.setStyleSheet("QTreeWidget {font-size:14px;} QTreeWidget::item {min-height:30px;}")
        self.tree.itemSelectionChanged.connect(self._render_detail)
        left.body.addWidget(self.tree)
        left.body.addWidget(label("Site를 추가하면 DB에 저장되고 대시보드·히트맵 Site 선택지에 바로 보입니다. "
                                  "설비가 어느 Site에 있는지는 가져온 현황표로 정해집니다.", 12, color=SECONDARY, wrap=True))
        left.body.addWidget(self.notice)
        self.detail_title = label("위치를 고르세요", 20, medium=True)
        right.body.addWidget(self.detail_title)
        self.detail_info = label("", 13, color=SECONDARY, wrap=True)
        right.body.addWidget(self.detail_info)
        right.body.addWidget(label("소속 설비", 16, medium=True))
        self.equipment_table = _table(("설비 ID", "설비군", "모델", "최근 기준일"), "위치 소속 설비")
        self.equipment_table.setMinimumHeight(400)
        right.body.addWidget(self.equipment_table)

    def reload(self):
        self.say("위치를 읽고 있습니다…")
        gateway = self.page.gateway
        return self.run(lambda: (gateway.data_admin("equipment_overview")[1], gateway.data_admin("sites")), self._loaded)

    def _loaded(self, value, error):
        if error is not None:
            self.say("위치를 읽지 못했습니다.", error=True)
            return
        self.loaded = True
        self.equipment, self.registered = value[0], tuple(value[1])
        note, self.saved_note = self.saved_note, ""
        self.say(note)
        self._render_tree()

    def _render_tree(self):
        self.tree.clear()
        sites = defaultdict(lambda: defaultdict(int))
        for e in self.equipment:
            if e.snapshot_count:  # 관측이 모두 지워진 설비는 위치가 없다
                sites[e.site or "Site 미확인"][e.line or "Line 미지정"] += 1
        for site in self.registered:
            sites.setdefault(site, defaultdict(int))
        for site in sorted(sites):
            empty = not sites[site]
            node = QTreeWidgetItem([site + ("  (등록 · 설비 없음)" if empty else "")])
            node.setData(0, Qt.ItemDataRole.UserRole, (site, None))
            fab = QTreeWidgetItem(["Fab 미지정"])
            fab.setData(0, Qt.ItemDataRole.UserRole, (site, None))
            node.addChild(fab)
            for line, count in sorted(sites[site].items()):
                leaf = QTreeWidgetItem([f"{line}  · {count}대"])
                leaf.setData(0, Qt.ItemDataRole.UserRole, (site, line))
                fab.addChild(leaf)
            self.tree.addTopLevelItem(node)
            node.setExpanded(True)
            fab.setExpanded(True)

    def _render_detail(self):
        items = self.tree.selectedItems()
        if not items:
            return
        site, line = items[0].data(0, Qt.ItemDataRole.UserRole)
        rows = [e for e in self.equipment if e.snapshot_count and (e.site or "Site 미확인") == site
                and (line is None or (e.line or "Line 미지정") == line)]
        self.detail_title.setText(f"{site} · {line}" if line else site)
        self.detail_info.setText(f"Site {site} · Fab 미지정 · Line {line or '전체'} · 설비 {len(rows)}대"
                                 + (" · 데이터 관리에서 등록한 Site" if site in self.registered else ""))
        _fill(self.equipment_table, [(e.code, e.group_names, e.model_name, e.base_date) for e in rows])

    def add_site(self):
        if self.page.gateway is None or self.page.saving:
            return False
        name, ok = QInputDialog.getText(self, "Site 추가", "Site 이름")
        name = (name or "").strip()
        if not ok or not name:
            return False
        known = {e.site.casefold() for e in self.equipment if e.site} | {s.casefold() for s in self.registered}
        if name.casefold() in known:
            self.say(f"'{name}'은 이미 있는 Site입니다.", error=True)
            return False
        return self.page.change(lambda: self.page.gateway.data_admin("add_site", name), self,
                                message=f"Site '{name}'을 등록하고 있습니다…", done=self._site_added)

    def _site_added(self, name):
        self.saved_note = f"Site '{name}'을 등록했습니다 · 대시보드·히트맵 Site 선택지에 보입니다."
        self.reload()

    def _sync(self):
        self.add_site_button.setEnabled(not self.page.saving)


# --- 릴리즈노트 ------------------------------------------------------------------------------
RELEASE_COLUMNS = ("Version", "Date", "No", "Content (Item)", "적용 모델")
RELEASE_FIELDS = {0: ("release_version", "version_raw"), 1: ("release_version", "released_at"),
                  2: ("item", "item_no"), 3: ("item", "content"), 4: ("release_version_item", "target_group_ids")}


class ReleasePanel(_Panel):
    """릴리즈노트 엑셀형 표. 칸을 고치면 바로 저장한다. 위험 지정은 아이템 매니저에서 한다 (11-11)."""

    def __init__(self, page):
        super().__init__(page)
        self.families, self.rows, self.groups, self.edits, self.originals = (), (), {}, {}, {}
        self.family_id, self.variant = None, None
        body = QHBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        left, middle = Card(padding=16), Card(padding=20)
        left.setFixedWidth(240)
        body.addWidget(left)
        body.addWidget(middle, 1)
        left.body.addWidget(label("계열", 20, medium=True))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAccessibleName("릴리즈노트 계열 (개조형은 상위 계열 아래)")
        self.tree.setMinimumHeight(420)
        self.tree.setStyleSheet("QTreeWidget {font-size:14px;} QTreeWidget::item {min-height:28px;}")
        self.tree.itemSelectionChanged.connect(self._family_changed)
        left.body.addWidget(self.tree)
        head = QHBoxLayout()
        self.family_title = label("계열을 고르세요", 20, medium=True)
        self.family_info = label("", 12, color=SECONDARY)
        head.addWidget(self.family_title)
        head.addWidget(self.family_info)
        head.addStretch()
        middle.body.addLayout(head)
        self.table = QTableWidget(0, len(RELEASE_COLUMNS))
        self.table.setHorizontalHeaderLabels(RELEASE_COLUMNS)
        self.table.setAccessibleName("릴리즈노트 · 버전 줄 아래 Item 줄")
        self.table.setStyleSheet(TABLE_STYLE)
        self.table.setMinimumHeight(520)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._cell_changed)
        self.table.cellClicked.connect(self._toggle_version)
        middle.body.addWidget(self.table)
        middle.body.addWidget(label("엑셀 시트처럼 보여 주고, 칸을 두 번 눌러 고치면 바로 저장됩니다. 버전 칸(▼/▶)을 누르면 그 버전의 "
                                    "Item 줄을 접거나 펼칩니다. 날짜는 YYYY-MM-DD, 적용 모델은 설비군 이름을 쉼표로 적습니다. "
                                    "위험 지정은 아이템 매니저에서 합니다.", 12, color=SECONDARY, wrap=True))
        middle.body.addWidget(self.notice)

    def reload(self):
        self.say("릴리즈노트 계열을 읽고 있습니다…")
        return self.run(lambda: (self.page.gateway.data_admin("release_families"),
                                 self.page.gateway.data_admin("equipment_overview")[0]), self._families_loaded)

    def _families_loaded(self, value, error):
        if error is not None:
            self.say("릴리즈노트를 읽지 못했습니다.", error=True)
            return
        self.loaded = True
        self.families, groups = value
        self.groups = {g.name.casefold(): g.group_id for g in groups}
        self.tree.blockSignals(True)
        self.tree.clear()
        for family in self.families:
            node = QTreeWidgetItem([f"{family.name}  {family.version_count}"])
            node.setData(0, Qt.ItemDataRole.UserRole, (family.family_id, None))
            for gid, name in family.variants:
                child = QTreeWidgetItem([f"↳ {name}  (개조)"])
                child.setData(0, Qt.ItemDataRole.UserRole, (family.family_id, gid))
                node.addChild(child)
            self.tree.addTopLevelItem(node)
            node.setExpanded(True)
        self.tree.blockSignals(False)
        self.say("")
        if self.family_id is not None:
            self._load_rows(self.family_id)

    def _family_changed(self):
        items = self.tree.selectedItems()
        if not items:
            return
        family_id, variant = items[0].data(0, Qt.ItemDataRole.UserRole)
        self.variant = variant
        self._load_rows(family_id)

    def _load_rows(self, family_id):
        self.family_id = family_id
        self.say("버전과 Item을 읽고 있습니다…")
        self.run(lambda: self.page.gateway.data_admin("release_rows", family_id), self._rows_loaded)

    def _rows_loaded(self, rows, error):
        if error is not None:
            self.say("버전과 Item을 읽지 못했습니다.", error=True)
            return
        self.rows, self.edits, self.originals = tuple(rows), {}, {}
        note, self.saved_note = self.saved_note, ""
        family = next((f for f in self.families if f.family_id == self.family_id), None)
        variant = next((n for g, n in (family.variants if family else ()) if g == self.variant), None)
        self.family_title.setText((family.name if family else "계열") + (f" · {variant}" if variant else ""))
        versions = len({r.release_version_id for r in self.rows})
        self.family_info.setText(f"버전 {versions} · Item {sum(r.item_id is not None for r in self.rows)}"
                                 + (f" · {variant} 적용 Item 굵게" if variant else ""))
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        self.table.setVerticalHeaderLabels([str(r.row_number or "") for r in self.rows])
        for i, row in enumerate(self.rows):
            values = (row.version_raw if row.first_of_version else "", row.released_at if row.first_of_version else "",
                      row.item_no, row.content, row.applied_models)
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                editable = (c in (0, 1) and row.first_of_version) or (c in (2, 3, 4) and row.item_id is not None)
                if not editable:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                else:
                    self.originals[(i, c)] = value
                if self.variant is not None and self.variant in row.target_group_ids:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(i, c, item)
            if row.first_of_version:
                self.table.item(i, 0).setText("▼ " + row.version_raw)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.blockSignals(False)
        self.say(note)

    def _toggle_version(self, row, column):
        if column != 0 or not self.rows or not self.rows[row].first_of_version or self.table.state() == QAbstractItemView.State.EditingState:
            return
        vid = self.rows[row].release_version_id
        hidden = not any(self.table.isRowHidden(i) for i, r in enumerate(self.rows) if r.release_version_id == vid and i != row)
        self.table.blockSignals(True)
        for i, r in enumerate(self.rows):
            if r.release_version_id == vid and i != row:
                self.table.setRowHidden(i, hidden)
        item = self.table.item(row, 0)
        if (row, 0) not in self.edits:
            item.setText(("▶ " if hidden else "▼ ") + self.originals[(row, 0)])
        self.table.blockSignals(False)

    def _set_cell(self, key, text, background=None, tip=None):
        item = self.table.item(*key)
        self.table.blockSignals(True)
        item.setText(("▼ " + text) if key[1] == 0 else text)
        item.setBackground(QBrush(background) if background is not None else QBrush())
        item.setToolTip(tip or text)
        self.table.blockSignals(False)

    def _cell_changed(self, item):
        key = (item.row(), item.column())
        if key not in self.originals:
            return
        value = item.text().strip()
        if key[1] == 0:
            value = value.removeprefix("▼ ").removeprefix("▶ ").strip()
        original = self.originals[key]
        if value == original:
            self.edits.pop(key, None)
            self._set_cell(key, original)
            return
        try:
            self.changes({key: value})
        except ValueError as error:
            self.say(f"{error}. 날짜는 YYYY-MM-DD, 적용 모델은 설비군 이름으로 적어 주세요.", error=True)
            self._set_cell(key, original)
            return
        self.edits[key] = value
        self._set_cell(key, value, SAVING, f"{original or '(빈칸)'} → {value or '(빈칸)'} · 저장 중")
        self.flush()

    def flush(self):
        if not self.edits or self.page.saving:
            return False
        batch, rows = dict(self.edits), self.rows
        self.edits.clear()
        started = self.page.save_changes(self.changes(batch), self, done=lambda _: self._stored(rows, batch),
                                         failed=lambda _: self._rejected(rows, batch))
        if not started:
            self.edits.update(batch)
        return started

    def _stored(self, rows, batch):
        if rows is not self.rows:
            return
        for key, value in batch.items():
            before, self.originals[key] = self.originals[key], value
            if key not in self.edits:
                self._set_cell(key, value, SAVED, f"{before or '(빈칸)'} → {value or '(빈칸)'} · 저장됨")

    def _rejected(self, rows, batch):
        if rows is not self.rows:
            return
        for key in batch:
            self.edits.pop(key, None)
            self._set_cell(key, self.originals[key])

    def changes(self, edits=None):
        grouped = defaultdict(dict)
        for (r, c), value in (self.edits if edits is None else edits).items():
            row = self.rows[r]
            table, name = RELEASE_FIELDS[c]
            if table == "release_version":
                key = {"release_version_id": row.release_version_id}
                if name == "released_at":
                    try:
                        value = date.fromisoformat(value) if value else None
                    except ValueError:
                        raise ValueError(f"날짜 형식이 아닙니다: {value}") from None
            elif table == "item":
                key = {"item_id": row.item_id}
            else:
                key = {"release_version_id": row.release_version_id, "item_id": row.item_id}
                names = [n.strip() for n in value.split(",") if n.strip()]
                unknown = [n for n in names if n.casefold() not in self.groups]
                if unknown:
                    raise ValueError(f"모르는 설비군: {', '.join(unknown)}")
                grouped[(table, tuple(sorted(key.items())))].update(
                    target_scope="LISTED" if names else "UNKNOWN",
                    target_group_ids=[self.groups[n.casefold()] for n in names] if names else None)
                continue
            grouped[(table, tuple(sorted(key.items())))][name] = value or None
        return [(table, dict(key), changes) for (table, key), changes in grouped.items()]


# --- 변경 이력 -------------------------------------------------------------------------------
class HistoryPanel(_Panel):
    """수정 · 등록 · 삭제 기록. 수정 일시 · 수정 파일 · 시트·셀 · 수정 전 → 후를 한 줄에 다 보인다 (11-11)."""

    PERIODS = (("최근 7일", 7), ("최근 30일", 30), ("전체", None))
    KINDS = (("대상 전체", None), ("설비 현황", ("equipment", "equipment_snapshot", "equipment_version", "equipment_pm")),
             ("릴리즈노트", ("release_version", "item", "release_version_item")), ("설비군", ("equipment_group",)),
             ("Site", ("site",)), ("위험 지정", ("risk_rule",)))
    COLUMNS = ("수정 일시", "구분", "수정 파일", "시트 · 셀", "대상", "항목", "수정 전", "수정 후")

    def __init__(self, page):
        super().__init__(page)
        self.entries, self._shown = (), []
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        card = Card(padding=20)
        body.addWidget(card)
        filters = QHBoxLayout()
        self.period, self.kind = ComboBox(), ComboBox()
        for text, value in self.PERIODS:
            self.period.addItem(text, value)
        for text, value in self.KINDS:
            self.kind.addItem(text, value)
        self.period.setCurrentIndex(2)
        self.period.currentIndexChanged.connect(self._render)
        self.kind.currentIndexChanged.connect(self._render)
        filters.addWidget(self.period)
        filters.addWidget(self.kind)
        self.count = label("", 13, color=SECONDARY)
        filters.addWidget(self.count)
        filters.addStretch()
        refresh = button("새로 읽기")
        refresh.clicked.connect(self.reload)
        filters.addWidget(refresh)
        card.body.addLayout(filters)
        self.table = _table(self.COLUMNS, "변경 이력 (수정 일시 · 파일 · 셀 · 수정 전후)")
        self.table.setMinimumHeight(560)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        for column in range(len(self.COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents
                                        if column in (0, 1, 3, 5) else QHeaderView.ResizeMode.Stretch)
        card.body.addWidget(self.table)
        card.body.addWidget(label("수정 전 값은 회색, 수정 후 값은 노란 칸입니다. 칸에 마우스를 올리면 전체 내용이 보입니다.",
                                  12, color=SECONDARY, wrap=True))
        card.body.addWidget(self.notice)

    def reload(self):
        self.say("변경 이력을 읽고 있습니다…")
        return self.run(lambda: self.page.gateway.data_admin("history"), self._loaded)

    def _loaded(self, entries, error):
        if error is not None:
            self.say("변경 이력을 읽지 못했습니다.", error=True)
            return
        self.loaded = True
        self.entries = tuple(entries)
        self.say("" if self.entries else "아직 데이터 관리에서 고친 기록이 없습니다.")
        self._render()

    def visible(self):
        days, tables = self.period.currentData(), self.kind.currentData()
        since = datetime.now() - timedelta(days=days) if days else None
        return [e for e in self.entries if (since is None or (e.at is not None and e.at >= since))
                and (tables is None or e.table in tables)]

    def _render(self, *_):
        rows = self.visible()
        self._shown = rows
        _fill(self.table, [(_text(e.at)[:16].replace("T", " ") if e.at else "", e.kind,
                            e.workbook or ("파일 이름 없음" if e.sheet else "—"),
                            " · ".join(v for v in (e.sheet, e.cell) if v) or "—", e.target, e.field, e.before or "—",
                            e.after) for e in rows], list(range(len(rows))))
        for r, entry in enumerate(rows):
            before, after = self.table.item(r, 6), self.table.item(r, 7)
            before.setForeground(QBrush(MUTED))
            after.setBackground(QBrush(SAVING))
            if entry.kind == "삭제":
                after.setForeground(QBrush(QColor(DANGER)))
            font = after.font()
            font.setBold(True)
            after.setFont(font)
        self.count.setText(f"{len(rows)}건")


# --- 화면 ------------------------------------------------------------------------------------
class DataManagementPage(QWidget):
    """설비현황 · 릴리즈노트 · 변경 이력. 고치기·더하기·지우기는 바로 DB에 반영하고 끝나면 saved로 알린다."""

    save_started = Signal()
    saved = Signal(object)
    save_failed = Signal()
    group_added = Signal(object)

    def __init__(self, gateway, *, context_provider=lambda: None):
        super().__init__()
        self.gateway, self.context_provider = gateway, context_provider
        self.saving, self.deletion = False, None
        self.save_request = ReadRequest(self)
        self.save_request.completed.connect(self._saved)
        self._job = None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        self.tabs = QTabBar()
        for name in ("설비현황", "릴리즈노트", "변경 이력"):
            self.tabs.addTab(name)
        self.tabs.setExpanding(False)
        self.tabs.setAccessibleName("데이터 관리 화면 선택")
        self.tabs.setStyleSheet("QTabBar::tab {background:transparent;border:0;padding:10px 24px;color:#5d6678;font-size:16px;}"
                                f"QTabBar::tab:selected {{color:{PRIMARY};border-bottom:3px solid {PRIMARY};font-weight:bold;}}")
        body.addWidget(self.tabs)
        self.unavailable = label("DB에 연결되지 않아 데이터 관리를 쓸 수 없습니다. setron.env의 DB 설정을 확인해 주세요.",
                                 14, color=DANGER, wrap=True)
        self.unavailable.setVisible(gateway is None)
        body.addWidget(self.unavailable)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        # 설비현황
        status = QWidget()
        status_body = QVBoxLayout(status)
        status_body.setContentsMargins(0, 0, 0, 0)
        status_body.setSpacing(16)
        sub, self.status_group, self.status_buttons = _segment(("현황표", "설비", "위치", "가져온 엑셀 삭제"), "설비현황")
        self.status_buttons[3].setToolTip("가져온 엑셀(기준일 · Site 단위)을 통째로 지웁니다. 한 행씩 지우기는 현황표에서 합니다.")
        status_body.addWidget(sub)
        self.status_stack = QStackedWidget()
        self.sheet_panel, self.equipment_panel, self.location_panel = SheetPanel(self), EquipmentPanel(self), LocationPanel(self)
        # 삭제 화면은 따로 스크롤 영역에 넣지 않는다. 창 전체 스크롤 안에 스크롤 영역을 또 두면 크기 맞춤이 끝없이 반복된다.
        self.delete_host = QWidget()
        self.delete_layout = QVBoxLayout(self.delete_host)
        self.delete_layout.setContentsMargins(0, 0, 0, 0)
        self.delete_placeholder = label("가져온 엑셀 삭제를 쓸 수 없습니다 (DB 미연결).", 14, color=SECONDARY)
        self.delete_layout.addWidget(self.delete_placeholder)
        for widget in (self.sheet_panel, self.equipment_panel, self.location_panel, self.delete_host):
            self.status_stack.addWidget(widget)
        status_body.addWidget(self.status_stack, 1)
        self.status_group.idClicked.connect(self.select_status)
        self.release_panel, self.history_panel = ReleasePanel(self), HistoryPanel(self)
        for widget in (status, self.release_panel, self.history_panel):
            self.stack.addWidget(widget)
        self.tabs.currentChanged.connect(self.select_tab)
        # 여러 줄 안내가 첫 줄만 보이지 않게, 줄바꿈 라벨은 폭을 무시하고 높이를 내용에 맞춘다 (다른 화면과 같은 방식).
        ignore_width(*(w for w in self.findChildren(QLabel) if w.wordWrap()))

    @property
    def panels(self):
        return (self.sheet_panel, self.equipment_panel, self.location_panel, self.release_panel, self.history_panel)

    # 탭 이동 -----------------------------------------------------------------------------------
    def enter(self):
        """화면에 들어올 때 지금 탭을 처음 한 번 읽는다."""
        self._ensure_loaded()

    def select_tab(self, index):
        if self.tabs.currentIndex() != index:
            self.tabs.setCurrentIndex(index)
            return
        self.stack.setCurrentIndex(index)
        self._ensure_loaded()

    def select_status(self, index):
        self.status_buttons[index].setChecked(True)
        self.status_stack.setCurrentIndex(index)
        self._ensure_loaded()

    def _ensure_loaded(self):
        if self.gateway is None:
            return
        tab = self.stack.currentIndex()
        if tab == 0:
            index = self.status_stack.currentIndex()
            panel = (self.sheet_panel, self.equipment_panel, self.location_panel, None)[index]
            if panel is not None and not panel.loaded:
                panel.reload()
            if index == 3 and self.deletion is not None and getattr(self.deletion, "catalog", None) is None:
                self.deletion.reload()
        elif tab == 1 and not self.release_panel.loaded:
            self.release_panel.reload()
        elif tab == 2 and not self.history_panel.loaded:
            self.history_panel.reload()

    def attach_deletion(self, widget):
        """데이터 가져오기에 있던 저장 자료 삭제 화면을 이 화면의 '가져온 엑셀 삭제'로 옮긴다 (객체·연결은 그대로)."""
        self.deletion = widget
        self.delete_layout.removeWidget(self.delete_placeholder)
        self.delete_placeholder.deleteLater()
        self.delete_layout.addWidget(widget)

    def invalidate(self, *_):
        """다른 화면에서 저장·삭제·가져오기를 하면 다음에 볼 때 다시 읽는다."""
        for panel in self.panels:
            panel.loaded = False
        if self.isVisible():
            self._ensure_loaded()

    # 바로 반영 ----------------------------------------------------------------------------------
    def save_changes(self, changes, owner, *, done=None, failed=None):
        """[(table, key, {field: value})]를 저장 직전 검토 지문으로 묶어 관리 저장한다 (워커)."""
        if not changes:
            return False
        gateway, context, requested = self.gateway, deepcopy(self.context_provider()), list(changes)

        def work():
            # 저장 직전의 검토 지문(revision)으로 묶는다. 그 사이 다른 곳에서 바뀌었으면 저장이 거부된다.
            edits = tuple(RecordEdit(table, key, gateway.get(table, key).revision, values) for table, key, values in requested)
            return gateway.save(edits, context) if context is not None else gateway.save(edits)

        return self.change(work, owner, done=done, failed=failed)

    def change(self, work, owner, *, message="저장하고 있습니다…", done=None, failed=None):
        """DB를 바꾸는 작업 하나를 워커에서 실행한다. 한 번에 하나만 하고, 끝나면 saved로 다른 화면에 알린다."""
        if self.gateway is None or self.saving:
            return False
        self.saving, self._job = True, (owner, done, failed)
        owner.say(message)
        self._sync_saving()
        self.save_started.emit()
        started = self.save_request.start(BackgroundRead(work))
        if not started:
            self.saving, self._job = False, None
            self._sync_saving()
        return started

    def _saved(self, result, error):
        owner, done, failed = self._job
        self.saving, self._job = False, None
        self._sync_saving()
        if error is not None:
            message = str(error) if isinstance(error, (ValueError, LookupError)) else "DB 저장에 실패했습니다. 연결을 확인해 주세요."
            owner.say(f"저장하지 못했습니다: {message}", error=True)
            if failed is not None:
                failed(error)
            self.save_failed.emit()
        else:
            owner.say("저장했습니다 · 변경 이력에 기록했습니다.")
            for panel in self.panels:
                if panel is not owner:
                    panel.loaded = False  # 다른 탭은 다음에 볼 때 새로 읽는다
            if done is not None:
                done(result)
            # 앱은 saved를 받아 조회 선택지(설비군·Site)와 대시보드·히트맵을 DB에서 다시 읽는다.
            self.saved.emit(result if hasattr(result, "records") else SimpleNamespace(records=(), refresh=None, value=result))
        for panel in (self.sheet_panel, self.release_panel):
            panel.flush()  # 저장 중에 고친 칸

    def _sync_saving(self):
        for panel in self.panels:
            panel._sync()
