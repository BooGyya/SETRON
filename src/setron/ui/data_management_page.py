"""데이터 관리 (11-10 개편, 11-11 2·3차 수정): 설비현황 · 릴리즈노트 · 변경 이력 세 탭.

- 설비현황
  - 현황표: 가져온 엑셀 시트를 그대로 보여 준다. 칸을 고친 뒤 [저장]으로 한꺼번에 저장하고, 행을 추가·삭제하며,
    [되돌리기]로 바로 앞 단계를 되돌린다. 데이터 행은 Line → 설비군 → 호기 순으로 정렬해 보인다.
  - 설비: 설비군 추가 · 위치: Site 추가 뒤 빈 현황표 만들기 · 가져온 엑셀 삭제: 데이터 가져오기에서 옮겨 온 삭제 화면.
- 릴리즈노트: 계열 목록 · 버전 줄 아래 Item 줄이 붙은 엑셀형 표. 칸을 고치면 바로 저장하고, 버전·Item 줄을 추가한다.
  위험 지정은 아이템 매니저에서만 한다.
- 변경 이력: 왼쪽 목록(수정 일시 · 구분 · 파일 · 셀 · 수정 전 · 수정 후), 오른쪽 세부 정보. 탭에 들어올 때마다 다시 읽는다.

모든 표는 열 경계를 끌어 폭을 바꿀 수 있고, 바꾼 폭은 이 PC에 기억한다. DB 읽기·저장은 모두 워커에서 한다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QStackedWidget, QTabBar, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from setron.features.management.schemas import RecordEdit
from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, button, label
from setron.ui.equipment import ignore_width
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.theme import LINE, PRIMARY, SECONDARY

SAVING = QColor("#fff2c4")     # 고쳤지만 아직 저장하지 않은 칸 · 저장 중인 칸
SAVED = QColor("#e3f4e8")      # 이번에 저장한 칸
HEADER = QColor("#eef1f8")     # 시트 머리글 행
NEW_ROW = QColor("#fdecef")    # 새 행의 아직 비어 있는 필수 칸
MUTED = QColor("#5d6678")      # DB와 연결되지 않은 원본 값
DELETED = QColor("#a0a7b4")    # 지운 행
DANGER = "#ad344d"
TABLE_STYLE = "QTableWidget {font-size:14px;} QHeaderView::section {font-size:13px;padding:4px 6px;}"
FIELD_NAMES = {"line": "Line", "model_name": "설비군", "equipment_code": "호기", "version_raw": "SW 버전"}
REQUIRED = ("equipment_code", "model_name", "version_raw")
COLUMN_KEY = "data_management/columns/"


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


def _natural(text):
    """'P10'이 'P9' 뒤에 오도록 숫자는 숫자로 비교한다."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part.casefold()) for part in re.split(r"(\d+)", text or "") if part]


class ColumnMemory:
    """표 열 폭: 끌어서 바꾸고, 사용자가 바꾼 폭은 이 PC 설정에 기억한다 (11-11)."""

    def __init__(self, store):
        self.store = store

    def attach(self, table, key):
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        table.setProperty("columnKey", key)
        header.sectionResized.connect(lambda *_: self._save(table))

    def _save(self, table):
        # 표를 채우며 자동으로 폭을 맞출 때가 아니라, 사람이 열 경계를 끌 때만 저장한다.
        if not QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            return
        widths = [table.horizontalHeader().sectionSize(c) for c in range(table.columnCount())]
        self.store.setValue(COLUMN_KEY + str(table.property("columnKey")), widths)

    def restore(self, table):
        value = self.store.value(COLUMN_KEY + str(table.property("columnKey")))
        if not isinstance(value, (list, tuple)) or len(value) != table.columnCount():
            return False
        try:
            widths = [int(w) for w in value]
        except (TypeError, ValueError):
            return False
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width)
        return True

    def fit(self, table, *, cap=360, spread=True, fixed=None):
        """기억한 폭이 없으면 내용 폭으로 맞추고, ``spread``면 남는 폭을 열마다 나눈다. ``fixed``: {열: 고정 폭}."""
        if self.restore(table):
            return
        table.resizeColumnsToContents()
        widths = [min(max(table.columnWidth(c) + 16, 64), cap) for c in range(table.columnCount())]
        for column, width in (fixed or {}).items():
            widths[column] = width
        spare = table.viewport().width() - sum(widths)
        extra = spare // len(widths) if spread and widths and spare > 0 else 0
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width + extra)


def _table(headers, name):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAccessibleName(name)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(34)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
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


def _ask_date(parent, title, text):
    """YYYY-MM-DD 날짜 하나를 묻는다. 취소하면 None."""
    value, ok = QInputDialog.getText(parent, title, text, text=date.today().isoformat())
    if not ok:
        return None
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        QMessageBox.warning(parent, title, "날짜는 YYYY-MM-DD로 적어 주세요.")
        return None


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
        """다른 저장이 끝난 뒤 밀린 수정을 저장한다 (바로 저장하는 패널만)."""

    def _sync(self):
        """저장 중에는 버튼을 잠근다."""


class _SheetTable(QTableWidget):
    """엑셀 시트 표. 열이 적으면 남는 폭을 열마다 나눠 표를 꽉 채운다. 사용자가 끌어 바꾼 폭은 그대로 둔다."""

    def __init__(self):
        super().__init__(0, 0)
        self.natural, self.remembered = [], False

    def measure(self, texts, memory):
        """열마다 가장 긴 글자 폭(머리글 행은 굵게)으로 기본 폭을 정한다. 아주 긴 값은 420px에서 자른다."""
        self.setProperty("columnKey", f"sheet{self.columnCount()}")
        self.remembered = memory.restore(self)
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
        if not self.natural or self.remembered:
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
    """가져온 엑셀 시트. 칸을 고치고 [저장]으로 한꺼번에 저장한다. 행 추가·삭제와 바로 앞 단계 되돌리기 (11-11)."""

    def __init__(self, page):
        super().__init__(page)
        self.sheets, self.grid, self.originals, self.edits, self.items = (), None, {}, {}, {}
        self.shown, self.steps, self.new_row, self.first_row = {}, [], None, 0
        self.pending_batch = None  # 다른 화면(위치)에서 만든 현황표를 열 때
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        card = Card(padding=20)
        body.addWidget(card)
        row = QHBoxLayout()
        self.sheet_combo = ComboBox()
        self.sheet_combo.setAccessibleName("현황표 선택 (기준일 · Site · 시트)")
        self.sheet_combo.setMinimumWidth(360)
        self.sheet_combo.currentIndexChanged.connect(self._sheet_changed)
        self.source = label("", 12, color=SECONDARY, wrap=True)
        self.undo_button = button("되돌리기")
        self.undo_button.setToolTip("바로 앞 단계 하나를 되돌립니다 (칸 수정 · 저장 · 행 추가 · 행 삭제).")
        self.add_button = button("＋ 행 추가")
        self.add_button.setToolTip("표 맨 아래에 새 행을 만듭니다. 호기 · 설비군 · SW 버전을 다 적으면 저장되고 정렬됩니다.")
        self.delete_button = button("선택 행 삭제")
        self.delete_button.setAccessibleName("고른 설비의 현황표 행 삭제")
        self.delete_button.setToolTip("고른 칸이 속한 설비의 이 현황표 행(설비 현황·SW 버전)을 DB에서 지웁니다.")
        self.save_button = button("저장", primary=True)
        self.save_button.setMinimumWidth(120)
        self.undo_button.clicked.connect(self.undo)
        self.add_button.clicked.connect(self.add_row)
        self.delete_button.clicked.connect(self.delete_selected)
        self.save_button.clicked.connect(self.save)
        row.addWidget(self.sheet_combo)
        row.addStretch()
        for widget in (self.undo_button, self.add_button, self.delete_button, self.save_button):
            row.addWidget(widget)
        card.body.addLayout(row)
        card.body.addWidget(self.source)  # 원본 파일 안내는 버튼 줄 아래 (긴 파일 이름이 화면 폭을 늘리지 않게)
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
        self.page.columns.attach(self.table, "sheet")
        card.body.addWidget(self.table)
        card.body.addWidget(label("가져온 엑셀 시트를 원래 열·행 번호로 보여 주고, 데이터 행은 Line → 설비군 → 호기 순으로 정렬합니다. "
                                  "DB 값과 연결된 칸을 두 번 눌러 고친 뒤 [저장]을 누르면 한꺼번에 저장되고 변경 이력에 남습니다 "
                                  "(고친 칸 노란색 → 저장됨 초록색). [＋ 행 추가]로 설비를 더하고, 행을 지우려면 그 설비의 칸이나 "
                                  "왼쪽 행 번호를 고른 뒤 [선택 행 삭제]를 누릅니다. Site · 기준일은 바꿀 수 없습니다.",
                                  12, color=SECONDARY, wrap=True))
        card.body.addWidget(self.notice)
        self._sync()

    # 읽기 -----------------------------------------------------------------------------------
    def reload(self):
        self.say("현황표 목록을 읽고 있습니다…")
        return self.run(lambda: self.page.gateway.data_admin("status_sheets"), self._sheets_loaded)

    def _sheets_loaded(self, sheets, error):
        if error is not None:
            self.say("현황표 목록을 읽지 못했습니다. DB 연결을 확인해 주세요.", error=True)
            return
        self.loaded = True
        self.sheets = tuple(sheets)
        previous = self.pending_batch if self.pending_batch is not None else self.sheet_combo.currentData()
        self.pending_batch = None
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        for sheet in self.sheets:
            self.sheet_combo.addItem(f"{sheet.label} · {sheet.equipment_count}대", sheet.batch_id)
        index = max(0, self.sheet_combo.findData(previous))
        self.sheet_combo.setCurrentIndex(index)
        self.sheet_combo.blockSignals(False)
        self._current = self.sheet_combo.currentData()
        if not self.sheets:
            self.say(self.saved_note + "저장된 현황표가 없습니다. 데이터 가져오기에서 저장하거나 위치에서 빈 현황표를 만들어 주세요.")
            self.saved_note = ""
            self.source.setText("")
            self._show(None)
            return
        self._read_grid(self._current)

    def open_batch(self, batch_id):
        """다른 탭에서 만든 현황표를 연다 (다음 읽기에서 고른다)."""
        self.pending_batch = batch_id
        self.loaded = False

    def _sheet_changed(self, *_):
        batch_id = self.sheet_combo.currentData()
        if batch_id is None or batch_id == getattr(self, "_current", None):
            return
        if not self._leave_ok():
            self.sheet_combo.blockSignals(True)
            self.sheet_combo.setCurrentIndex(max(0, self.sheet_combo.findData(self._current)))
            self.sheet_combo.blockSignals(False)
            return
        self._current = batch_id
        self.steps = []
        self._read_grid(batch_id)

    def _leave_ok(self):
        """저장하지 않은 수정이 있으면 저장 · 버리기 · 취소를 묻는다. 계속 가도 되면 True."""
        if not self.edits:
            return True
        box = QMessageBox(QMessageBox.Icon.Question, "저장하지 않은 수정",
                          f"저장하지 않은 수정이 {len(self.edits)}칸 있습니다. 어떻게 할까요?", parent=self)
        save = box.addButton("저장", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("버리기", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is save:
            self.save()
            return False  # 저장이 끝난 뒤 다시 고르게 한다
        if box.clickedButton() is discard:
            self.edits.clear()
            return True
        return False

    def _read_grid(self, batch_id):
        self.say("시트를 읽고 있습니다…")
        self.run(lambda: self.page.gateway.data_admin("sheet_grid", batch_id), self._grid_loaded)

    def _grid_loaded(self, grid, error):
        if error is not None:
            self.say("시트를 읽지 못했습니다. 다시 시도해 주세요.", error=True)
            return
        if self.grid is None or self.grid.batch_id != grid.batch_id:
            self.steps = []
        self._show(grid)
        sheet = next((s for s in self.sheets if s.batch_id == grid.batch_id), None)
        self.source.setText(f"원본 파일: {grid.workbook or '파일 이름 없음'} · 시트 {grid.sheet}"
                            + (f" · 가져온 시각 {_text(sheet.imported_at)[:16]}" if sheet and sheet.imported_at else ""))
        note, self.saved_note = self.saved_note, ""
        self.say(note + f"고칠 수 있는 칸 {sum(b.editable for b in grid.bindings.values())}개 · 고친 뒤 [저장]을 누릅니다.")

    # 표 그리기 --------------------------------------------------------------------------------
    def _value(self, grid, address):
        binding = grid.bindings.get(address)
        return binding.current if binding else _text(grid.cells.get(address))

    def _show(self, grid):
        self.grid, self.edits, self.originals, self.items, self.shown, self.new_row = grid, {}, {}, {}, {}, None
        self.table.blockSignals(True)
        self.table.clear()
        self.table.clearSpans()
        addresses = (*grid.cells, *grid.bindings, *grid.deleted) if grid is not None else ()
        filled = [a for a in addresses if a in grid.bindings or a in grid.deleted or _text(grid.cells.get(a)).strip()]
        if not filled:
            columns = len(grid.columns) + 1 if grid is not None and grid.columns else 0
            self.table.setRowCount(0)
            self.table.setColumnCount(max(columns, max((_split(a)[0] + 1 for a in addresses), default=0)))
            self.table.setHorizontalHeaderLabels([_col(c) for c in range(self.table.columnCount())])
            self.table.natural = []
            self.table.blockSignals(False)
            self._sync()
            return
        positions = [_split(a) for a in addresses]
        columns = max(c for c, _ in positions) + 1
        # 값이 하나도 없는 행(엑셀의 빈 줄)은 보이지 않는다. 행 번호는 엑셀 그대로 둔다.
        rows = sorted({_split(a)[1] for a in filled})
        data_start = min((_split(a)[1] for a in grid.bindings), default=rows[-1] + 1)
        header_rows = [r for r in rows if r < data_start]
        data_rows = [r for r in rows if r >= data_start]
        letters = {name: letter for letter, name in grid.columns.items()}
        if {"line", "model_name", "equipment_code"} & set(letters):
            data_rows.sort(key=lambda r: [_natural(self._value(grid, f"{letters[n]}{r}")) if n in letters else []
                                          for n in ("line", "model_name", "equipment_code")] + [r])
        rows = header_rows + data_rows
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
                value = self._value(grid, address)
                values.append(value)
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, address)
                self.items[address] = item
                self.shown[address] = value
                if binding is not None and binding.editable:
                    item.setToolTip(f"{binding.label} · 두 번 눌러 고친 뒤 [저장]")
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
        self.table.measure(texts, self.page.columns)
        self.table.blockSignals(False)
        self._sync()

    def _paint(self, address, state):
        """칸 상태: None(원래 값) · 'edited'(저장 전) · 'saving' · 'saved'."""
        item, binding = self.items.get(address), self.grid.bindings.get(address) if self.grid else None
        if item is None or binding is None:
            return
        self.table.blockSignals(True)
        if state is None:
            item.setBackground(QBrush())
            item.setToolTip(f"{binding.label} · 두 번 눌러 고친 뒤 [저장]")
        else:
            item.setBackground(QBrush(SAVED if state == "saved" else SAVING))
            note = {"edited": "저장 전", "saving": "저장 중", "saved": "저장됨"}[state]
            item.setToolTip(f"{binding.label}: {getattr(item, 'before', '') or '(빈칸)'} → {item.text() or '(빈칸)'} · {note}")
        self.table.blockSignals(False)

    def _set_text(self, address, text):
        self.table.blockSignals(True)
        self.items[address].setText(text)
        self.table.blockSignals(False)
        self.shown[address] = text

    # 칸 수정 · 저장 ---------------------------------------------------------------------------
    def _cell_changed(self, item):
        address = item.data(Qt.ItemDataRole.UserRole)
        if self.new_row is not None and address in self.new_row["cells"]:
            self._new_cell_changed(address, item)
            return
        if address not in self.originals:
            return
        value, previous = item.text().strip(), self.shown.get(address, "")
        if value == previous:
            return
        self.steps.append(("cell", address, previous))
        self._apply_edit(address, value)

    def _apply_edit(self, address, value):
        self.shown[address] = value
        if value == self.originals[address]:
            self.edits.pop(address, None)
            self._paint(address, None)
        else:
            self.edits[address] = value
            self.items[address].before = self.originals[address]
            self._paint(address, "edited")
        self._sync()

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

    def save(self):
        """고친 칸을 한꺼번에 저장한다 (확인 창 없음)."""
        if not self.edits or self.grid is None or self.page.saving:
            return False
        batch, grid = dict(self.edits), self.grid
        for address in batch:
            self._paint(address, "saving")
        return self.page.save_changes(self.changes(batch), self, done=lambda _: self._stored(grid, batch),
                                      failed=lambda _: self._sync())

    def _stored(self, grid, batch, *, undoing=False):
        if grid is not self.grid:
            return
        olds = {address: self.originals[address] for address in batch}
        for address, value in batch.items():
            self.originals[address] = value
            if self.edits.get(address) == value:
                self.edits.pop(address)
            self.items[address].before = olds[address]
            self._paint(address, "saved")
        if not undoing:
            # 저장한 칸 수정은 '저장' 한 단계로 묶는다 (되돌리면 저장 전 값으로 다시 저장).
            self.steps = [s for s in self.steps if not (s[0] == "cell" and s[1] in batch)]
            self.steps.append(("saved", olds))
        labels = [grid.bindings[a].label for a in batch]
        self.say(("되돌렸습니다" if undoing else f"{len(batch)}칸을 저장했습니다") + " · "
                 + ", ".join(labels[:4]) + (" …" if len(labels) > 4 else "") + " · 변경 이력에 기록했습니다.")
        self._sync()

    # 되돌리기 ----------------------------------------------------------------------------------
    def undo(self):
        """바로 앞 단계 하나를 되돌린다."""
        if not self.steps or self.page.saving or self.grid is None:
            return False
        kind, *rest = self.steps[-1]
        if kind == "cell":
            self.steps.pop()
            address, previous = rest
            self._set_text(address, previous)
            self._apply_edit(address, previous)
            self.say("수정 한 칸을 되돌렸습니다.")
            return True
        if kind == "new":
            self.steps.pop()
            address, previous = rest
            self._set_text(address, previous)
            self.new_row["values"][self.new_row["cells"][address]] = previous
            self._paint_new_row()
            return True
        if kind == "new_row":
            self.steps.pop()
            self._drop_new_row()
            self.say("새 행을 지웠습니다.")
            return True
        if self.edits or self.new_row is not None:
            self.say("저장하지 않은 수정이나 새 행을 먼저 저장하거나 되돌려 주세요.", error=True)
            return False
        grid = self.grid
        if kind == "saved":
            (olds,) = rest
            return self.page.save_changes(self.changes(olds), self,
                                          done=lambda _: (self.steps.pop(), self._restore_saved(grid, olds)))
        if kind == "delete":
            journal, codes = rest
            return self.page.change(lambda: self.page.gateway.data_admin("restore_rows", journal), self,
                                    message="지운 행을 되살리고 있습니다…",
                                    done=lambda _: self._after_undo(f"지운 행을 되살렸습니다 ({', '.join(codes[:4])})."))
        if kind == "add":
            sid, code = rest
            batch_id = grid.batch_id
            return self.page.change(lambda: self.page.gateway.data_admin("delete_rows", batch_id, (sid,), undo_add=True),
                                    self, message="추가한 행을 지우고 있습니다…",
                                    done=lambda _: self._after_undo(f"추가한 행({code})을 지웠습니다."))
        return False

    def _restore_saved(self, grid, olds):
        if grid is not self.grid:
            return
        for address, value in olds.items():
            self._set_text(address, value)
        self._stored(grid, olds, undoing=True)

    def _after_undo(self, note):
        self.steps.pop()
        self.saved_note = note + " "
        self.reload()

    # 행 추가 ----------------------------------------------------------------------------------
    def add_row(self):
        """표 맨 아래에 새 행을 만든다. 필수 칸(호기 · 설비군 · SW 버전)을 다 적으면 저장한다."""
        if self.grid is None or not self.grid.columns or self.new_row is not None or self.edits or self.page.saving:
            return False
        used = [_split(a)[1] for a in (*self.grid.cells, *self.grid.bindings, *self.grid.deleted)]
        number = max(used, default=1) + 1
        index = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(index)
        self.table.setVerticalHeaderItem(index, QTableWidgetItem(f"＋{number}"))
        cells = {}
        for c in range(self.table.columnCount()):
            letter = _col(c)
            address = f"{letter}{number}"
            item = QTableWidgetItem("")
            item.setData(Qt.ItemDataRole.UserRole, address)
            name = self.grid.columns.get(letter)
            if name is None:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            else:
                cells[address] = name
            self.items[address] = item
            self.table.setItem(index, c, item)
        self.table.blockSignals(False)
        self.new_row = {"number": number, "index": index, "cells": cells, "values": dict.fromkeys(cells.values(), "")}
        self.steps.append(("new_row",))
        self._paint_new_row()
        self.table.scrollToBottom()
        first = next((a for a, n in cells.items() if n == "equipment_code"), next(iter(cells), None))
        if first is not None:
            self.table.setCurrentItem(self.items[first])
        self.say("새 행: " + " · ".join(FIELD_NAMES[n] + ("(필수)" if n in REQUIRED else "") for n in cells.values())
                 + "을 적으면 저장됩니다.")
        self._sync()
        return True

    def _paint_new_row(self):
        self.table.blockSignals(True)
        for address, name in self.new_row["cells"].items():
            item = self.items[address]
            empty = not self.new_row["values"].get(name)
            item.setBackground(QBrush(NEW_ROW if empty and name in REQUIRED else SAVING))
            item.setToolTip(f"새 행 · {FIELD_NAMES[name]}" + (" (필수)" if name in REQUIRED else ""))
        self.table.blockSignals(False)

    def _drop_new_row(self):
        if self.new_row is None:
            return
        self.table.removeRow(self.new_row["index"])
        for address in self.new_row["cells"]:
            self.items.pop(address, None)
        self.new_row = None
        self.steps = [s for s in self.steps if s[0] not in ("new", "new_row")]
        self._sync()

    def _new_cell_changed(self, address, item):
        name, value = self.new_row["cells"][address], item.text().strip()
        previous = self.new_row["values"].get(name, "")
        if value == previous:
            return
        self.steps.append(("new", address, previous))
        self.new_row["values"][name] = value
        self._paint_new_row()
        if all(self.new_row["values"].get(n) for n in REQUIRED if n in self.new_row["values"]):
            self._commit_new_row()

    def _commit_new_row(self):
        values, number, batch_id = dict(self.new_row["values"]), self.new_row["number"], self.grid.batch_id
        model = values.get("model_name", "")
        gateway = self.page.gateway
        known = {name.casefold() for name in self.grid.groups}
        create = None
        if model.casefold() not in known:
            if QMessageBox.question(self, "새 설비군", f"설비군 '{model}'이(가) 없습니다. 새 설비군으로 등록할까요?") \
                    != QMessageBox.StandardButton.Yes:
                self.say(f"설비군 '{model}'을(를) 등록하지 않았습니다. 설비군 칸을 고쳐 주세요.", error=True)
                return False
            category, ok = QInputDialog.getItem(self, "새 설비군", f"'{model}'의 분류", ["POLY", "OXIDE", "KCTC"], 0, False)
            if not ok:
                return False
            create = (model, category)

        def work():
            if create is not None:
                gateway.data_admin("create_group", create[0], create[1], None)
            return gateway.data_admin("add_row", batch_id, values, number)

        return self.page.change(work, self, message="새 행을 저장하고 있습니다…",
                                done=lambda sid: self._row_added(sid, values, create))

    def _row_added(self, sid, values, create):
        self.steps = [s for s in self.steps if s[0] not in ("new", "new_row")]
        self.steps.append(("add", sid, values.get("equipment_code", "")))
        self.new_row = None
        self.saved_note = (f"'{values.get('equipment_code')}' 행을 추가했습니다"
                           + (f" (새 설비군 '{create[0]}' 등록)" if create else "") + " · 정렬했습니다. ")
        self.reload()

    # 행 삭제 ----------------------------------------------------------------------------------
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

    def delete_selected(self):
        chosen = self.selected_rows()
        if not chosen or self.grid is None or self.page.saving or self.edits or self.new_row is not None:
            return False
        codes = list(chosen.values())
        sheet = next((s for s in self.sheets if s.batch_id == self.grid.batch_id), None)
        where = sheet.label if sheet else self.grid.sheet
        shown = ", ".join(codes[:8]) + (f" 외 {len(codes) - 8}대" if len(codes) > 8 else "")
        if QMessageBox.question(self, "현황표 행 삭제",
                                f"{where}에서 설비 {len(codes)}대의 행을 DB에서 지울까요?\n\n{shown}\n\n"
                                "그 설비의 이 기준일 현황·SW 버전이 지워집니다. [되돌리기]로 바로 되살릴 수 있고, "
                                "가져온 엑셀 원본 칸은 '삭제됨'으로 보입니다.") != QMessageBox.StandardButton.Yes:
            return False
        batch_id, ids = self.grid.batch_id, tuple(chosen)
        return self.page.change(lambda: self.page.gateway.data_admin("delete_rows", batch_id, ids), self,
                                message="행을 지우고 있습니다…", done=lambda journal: self._deleted(journal, codes))

    def _deleted(self, journal, codes):
        self.steps.append(("delete", journal, codes))
        self.saved_note = f"설비 {len(codes)}대의 행을 지웠습니다 ({', '.join(codes[:4])}{' …' if len(codes) > 4 else ''}). "
        self.reload()

    def _sync(self):
        busy = self.page.saving
        clean = not self.edits and self.new_row is None
        self.save_button.setText(f"저장 ({len(self.edits)}칸)" if self.edits else "저장")
        self.save_button.setEnabled(bool(self.edits) and not busy)
        self.undo_button.setEnabled(bool(self.steps) and not busy)
        self.add_button.setEnabled(self.grid is not None and bool(self.grid.columns) and clean and not busy)
        self.add_button.setToolTip("표 맨 아래에 새 행을 만듭니다. 호기 · 설비군 · SW 버전을 다 적으면 저장되고 정렬됩니다."
                                   if self.grid is None or self.grid.columns else
                                   "이 시트는 열 구성을 알 수 없어 행을 추가할 수 없습니다. 데이터 가져오기로 넣어 주세요.")
        self.delete_button.setEnabled(bool(self.selected_rows()) and clean and not busy)
        self.sheet_combo.setEnabled(not busy and self.new_row is None)


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
    """왼쪽: 설비군 (분류는 묶음 첫 줄에만, 개조형도 한 줄씩) · 오른쪽: 설비 (모델 · Site · Line)."""

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
        self.group_table.setShowGrid(False)
        self.group_table.setStyleSheet(TABLE_STYLE + "QTableWidget::item {border-bottom:1px solid #eceef3;}")
        self.page.columns.attach(self.group_table, "groups")
        self.group_table.itemSelectionChanged.connect(self._render_equipment)
        left.body.addWidget(self.group_table)
        left.body.addWidget(label("설비군을 추가하면 DB에 저장되고, 기본형은 대시보드·히트맵 설비군 선택지에 새로 보이며 "
                                  "개조형은 상위 설비군에 함께 묶여 보입니다. 현황표에서 행을 추가할 때 없는 설비군을 적어도 "
                                  "그 자리에서 등록할 수 있습니다.", 12, color=SECONDARY, wrap=True))
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
        self.page.columns.attach(self.equipment_table, "equipment")
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
        # 분류는 묶음의 첫 줄에만 쓴다 (11-11 요청)
        shown = [g.category if i == 0 or self.ordered[i - 1].category != g.category else ""
                 for i, g in enumerate(self.ordered)]
        _fill(self.group_table, [(category, g.name if g.parent_id is None else f"   ↳ {g.name}",
                                  g.parent_name or "—", f"{g.equipment_count}대") for category, g in zip(shown, self.ordered)],
              [g.group_id for g in self.ordered])
        bold = QFont(self.group_table.font())
        bold.setBold(True)
        for r, category in enumerate(shown):
            if category:
                self.group_table.item(r, 0).setFont(bold)
            self.group_table.item(r, 0).setToolTip(self.ordered[r].category)
        self.page.columns.fit(self.group_table)
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
        self.page.columns.fit(self.equipment_table)

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
    """Site → Fab → Line 트리. 관측에 나온 Site와 데이터 관리에서 등록한 Site(DB 관리 설정)를 함께 보인다.

    Site를 추가하면 그 Site의 빈 현황표(기준일)를 만들지 이어서 묻고, 만들면 현황표 탭에서 연다 (11-11 확정 흐름).
    """

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
        left.body.addWidget(label("Site를 추가하면 DB에 저장되고 대시보드·히트맵 Site 선택지에 바로 보입니다. 이어서 그 Site의 "
                                  "빈 현황표를 만들면 현황표에서 설비를 한 행씩 추가할 수 있습니다.", 12, color=SECONDARY, wrap=True))
        left.body.addWidget(self.notice)
        self.detail_title = label("위치를 고르세요", 20, medium=True)
        right.body.addWidget(self.detail_title)
        self.detail_info = label("", 13, color=SECONDARY, wrap=True)
        right.body.addWidget(self.detail_info)
        right.body.addWidget(label("소속 설비", 16, medium=True))
        self.equipment_table = _table(("설비 ID", "설비군", "모델", "최근 기준일"), "위치 소속 설비")
        self.equipment_table.setMinimumHeight(400)
        self.page.columns.attach(self.equipment_table, "location")
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
        self.page.columns.fit(self.equipment_table)

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
        self.saved_note = f"Site '{name}'을 등록했습니다 · 대시보드·히트맵 Site 선택지에 보입니다. "
        self.reload()
        if QMessageBox.question(self, "빈 현황표 만들기",
                                f"'{name}'의 빈 현황표를 만들까요?\n만들면 현황표에서 설비를 한 행씩 추가할 수 있습니다. "
                                "엑셀이 있으면 '아니요'를 누르고 데이터 가져오기로 넣으면 됩니다.") != QMessageBox.StandardButton.Yes:
            return
        self.create_sheet(name)

    def create_sheet(self, site):
        base_date = _ask_date(self, "빈 현황표 만들기", f"'{site}' 현황표의 기준일 (YYYY-MM-DD)")
        if base_date is None:
            return False
        return self.page.change(lambda: self.page.gateway.data_admin("create_sheet", site, base_date), self,
                                message="빈 현황표를 만들고 있습니다…",
                                done=lambda batch_id: self.page.open_sheet(batch_id, f"{site} · {base_date} 빈 현황표를 만들었습니다. "))

    def _sync(self):
        self.add_site_button.setEnabled(not self.page.saving)


# --- 릴리즈노트 ------------------------------------------------------------------------------
RELEASE_COLUMNS = ("Version", "Date", "No", "Content (Item)", "적용 모델")
RELEASE_FIELDS = {0: ("release_version", "version_raw"), 1: ("release_version", "released_at"),
                  2: ("item", "item_no"), 3: ("item", "content"), 4: ("release_version_item", "target_group_ids")}


class ReleasePanel(_Panel):
    """릴리즈노트 엑셀형 표. 칸을 고치면 바로 저장하고, 버전·Item 줄을 추가한다. 위험 지정은 아이템 매니저에서 한다."""

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
        self.add_version_button = button("＋ 버전 추가")
        self.add_item_button = button("＋ Item 추가")
        self.add_item_button.setToolTip("고른 줄의 버전에 새 Item을 추가합니다.")
        self.add_version_button.clicked.connect(self.add_version)
        self.add_item_button.clicked.connect(self.add_item)
        head.addWidget(self.family_title)
        head.addWidget(self.family_info)
        head.addStretch()
        head.addWidget(self.add_version_button)
        head.addWidget(self.add_item_button)
        middle.body.addLayout(head)
        self.table = QTableWidget(0, len(RELEASE_COLUMNS))
        self.table.setHorizontalHeaderLabels(RELEASE_COLUMNS)
        self.table.setAccessibleName("릴리즈노트 · 버전 줄 아래 Item 줄")
        self.table.setStyleSheet(TABLE_STYLE)
        self.table.setMinimumHeight(520)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._cell_changed)
        self.table.cellClicked.connect(self._toggle_version)
        self.table.itemSelectionChanged.connect(self._sync)
        self.page.columns.attach(self.table, "release")
        middle.body.addWidget(self.table)
        middle.body.addWidget(label("엑셀 시트처럼 보여 주고, 칸을 두 번 눌러 고치면 바로 저장됩니다. 버전 칸(▼/▶)을 누르면 그 버전의 "
                                    "Item 줄을 접거나 펼칩니다. 날짜는 YYYY-MM-DD, 적용 모델은 설비군 이름을 쉼표로 적습니다. "
                                    "새 버전은 순서 맨 끝에 들어갑니다. 위험 지정은 아이템 매니저에서 합니다.", 12, color=SECONDARY, wrap=True))
        middle.body.addWidget(self.notice)
        self._sync()

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
        note, self.saved_note = self.saved_note, ""
        self.say(note)
        if self.family_id is not None:
            self._load_rows(self.family_id, note)
        self._sync()

    def _family_changed(self):
        items = self.tree.selectedItems()
        if not items:
            return
        family_id, variant = items[0].data(0, Qt.ItemDataRole.UserRole)
        self.variant = variant
        self._load_rows(family_id)

    def _load_rows(self, family_id, note=""):
        self.family_id = family_id
        self.saved_note = note
        self.say("버전과 Item을 읽고 있습니다…")
        self.run(lambda: self.page.gateway.data_admin("release_rows", family_id), self._rows_loaded)
        self._sync()

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
        self.page.columns.fit(self.table, cap=520)
        self.table.blockSignals(False)
        self.say(note)
        self._sync()

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
                grouped[(table, tuple(sorted(key.items())))].update(self._models(value))
                continue
            grouped[(table, tuple(sorted(key.items())))][name] = value or None
        return [(table, dict(key), changes) for (table, key), changes in grouped.items()]

    def _models(self, text):
        names = [n.strip() for n in (text or "").split(",") if n.strip() and n.strip() != "전체"]
        unknown = [n for n in names if n.casefold() not in self.groups]
        if unknown:
            raise ValueError(f"모르는 설비군: {', '.join(unknown)}")
        return {"target_scope": "LISTED" if names else "ALL" if (text or "").strip() == "전체" else "UNKNOWN",
                "target_group_ids": [self.groups[n.casefold()] for n in names] if names else None}

    def selected_row(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.rows[rows[0].row()] if rows and self.rows else None

    def add_version(self):
        if self.family_id is None or self.page.saving:
            return False
        dialog = _FormDialog(self, "버전 추가", (("버전명", "v4.60.1.7"), ("릴리즈 날짜 (선택, YYYY-MM-DD)", "")))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        version, day = dialog.values()
        try:
            released = date.fromisoformat(day) if day else None
        except ValueError:
            self.say("날짜는 YYYY-MM-DD로 적어 주세요.", error=True)
            return False
        if not version:
            self.say("버전명을 적어 주세요.", error=True)
            return False
        family_id = self.family_id
        return self.page.change(lambda: self.page.gateway.data_admin("add_release_version", family_id, version, released),
                                self, message=f"버전 '{version}'을 추가하고 있습니다…",
                                done=lambda _: self._added(f"버전 '{version}'을 추가했습니다 (순서 맨 끝). "))

    def add_item(self):
        row = self.selected_row()
        if row is None or self.page.saving:
            return False
        dialog = _FormDialog(self, f"Item 추가 · {row.version_raw}",
                             (("No (선택, 숫자)", ""), ("Item 내용", ""), ("적용 모델 (쉼표, 비우면 전체)", "")))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        number, content, models = dialog.values()
        try:
            item_no = int(number) if number else None
            ids = self._models(models)["target_group_ids"]
        except ValueError as error:
            self.say(f"{error if 'invalid literal' not in str(error) else 'No는 숫자로 적어 주세요'}.", error=True)
            return False
        if not content:
            self.say("Item 내용을 적어 주세요.", error=True)
            return False
        release_id = row.release_version_id
        return self.page.change(lambda: self.page.gateway.data_admin("add_release_item", release_id, content, item_no, ids),
                                self, message="Item을 추가하고 있습니다…",
                                done=lambda _: self._added(f"{row.version_raw}에 Item을 추가했습니다. "))

    def _added(self, note):
        self.saved_note = note  # 계열 목록(버전 수)과 표를 함께 다시 읽는다
        self.reload()

    def _sync(self):
        busy = self.page.saving
        self.add_version_button.setEnabled(self.family_id is not None and not busy)
        self.add_item_button.setEnabled(self.selected_row() is not None and not busy)


class _FormDialog(QDialog):
    """글자 칸 몇 개를 받는 작은 대화상자."""

    def __init__(self, parent, title, fields):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        self.fields = []
        for caption, placeholder in fields:
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setMinimumWidth(320)
            form.addRow(caption, edit)
            self.fields.append(edit)
        actions = QHBoxLayout()
        cancel, ok = button("취소"), button("추가", primary=True)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(ok)
        form.addRow(actions)

    def values(self):
        return tuple(edit.text().strip() for edit in self.fields)


# --- 변경 이력 -------------------------------------------------------------------------------
class HistoryPanel(_Panel):
    """왼쪽 목록(수정 일시 · 구분 · 파일 · 셀 · 대상 · 항목 · 수정 전 · 수정 후) · 오른쪽 세부 정보 (11-11).

    탭에 들어올 때마다 DB에서 다시 읽으므로 [새로 읽기] 버튼은 두지 않는다.
    """

    PERIODS = (("최근 7일", 7), ("최근 30일", 30), ("전체", None))
    KINDS = (("대상 전체", None), ("설비 현황", ("equipment", "equipment_snapshot", "equipment_version", "equipment_pm")),
             ("릴리즈노트", ("release_version", "item", "release_version_item")), ("설비군", ("equipment_group",)),
             ("Site · 현황표", ("site", "status_sheet")), ("위험 지정", ("risk_rule",)))
    COLUMNS = ("수정 일시", "구분", "수정 파일", "시트 · 셀", "대상", "항목", "수정 전", "수정 후")

    def __init__(self, page):
        super().__init__(page)
        self.entries, self._shown = (), []
        body = QHBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        card, right = Card(padding=20), Card(padding=20)
        right.setFixedWidth(320)
        body.addWidget(card, 1)
        body.addWidget(right)
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
        card.body.addLayout(filters)
        self.table = _table(self.COLUMNS, "변경 이력 (수정 일시 · 파일 · 셀 · 수정 전후)")
        self.table.setMinimumHeight(560)
        self.table.setWordWrap(False)
        self.page.columns.attach(self.table, "history")
        # '시트 · 셀'은 오른쪽 세부 정보에 보이므로 목록에서는 좁게 둔다 (열 경계를 끌면 다시 넓어진다)
        self.table.itemSelectionChanged.connect(self._render_detail)
        card.body.addWidget(self.table)
        card.body.addWidget(label("수정 전 값은 회색, 수정 후 값은 노란 칸입니다. 줄을 누르면 오른쪽에 세부 정보가 보이고, "
                                  "열 경계를 끌어 폭을 바꿀 수 있습니다.", 12, color=SECONDARY, wrap=True))
        card.body.addWidget(self.notice)
        self.detail_title = label("변경을 고르세요", 20, medium=True, wrap=True)
        self.detail = label("", 13, color=SECONDARY, wrap=True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.before_title, self.after_title = label("수정 전", 13, medium=True), label("수정 후", 13, medium=True)
        self.before_value, self.after_value = label("", 15, wrap=True), label("", 15, medium=True, wrap=True)
        self.before_value.setStyleSheet(f"color:{MUTED.name()};background:#f4f5f8;border-radius:8px;padding:10px;")
        self.after_value.setStyleSheet("background:#fff2c4;border-radius:8px;padding:10px;")
        for widget in (self.detail_title, self.detail, self.before_title, self.before_value, self.after_title, self.after_value):
            right.body.addWidget(widget)
        right.body.addStretch()
        self._render_detail()

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
        self.table.blockSignals(True)
        _fill(self.table, [(self._when(e.at), e.kind,
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
        self.table.blockSignals(False)
        self.page.columns.fit(self.table, cap=130, spread=False, fixed={1: 64, 3: 80})
        self.count.setText(f"{len(rows)}건")
        self._render_detail()

    @staticmethod
    def _when(at):
        """올해 기록은 'MM-DD HH:MM'으로 짧게 (전체 일시는 오른쪽 세부 정보에)."""
        if not at:
            return ""
        return at.strftime("%m-%d %H:%M") if at.year == datetime.now().year else at.strftime("%Y-%m-%d %H:%M")

    def _render_detail(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        entry = self._shown[rows[0].row()] if rows and rows[0].row() < len(self._shown) else None
        for widget in (self.before_title, self.before_value, self.after_title, self.after_value):
            widget.setVisible(entry is not None)
        if entry is None:
            self.detail_title.setText("변경을 고르세요")
            self.detail.setText("왼쪽 목록에서 줄을 누르면 수정 일시 · 파일 · 셀과 수정 전 → 후가 여기에 보입니다.")
            return
        self.detail_title.setText(f"{entry.target} · {entry.field}")
        self.detail.setText(f"구분  {entry.kind}\n수정 일시  {_text(entry.at)[:16].replace('T', ' ') or '—'}\n"
                            f"수정 파일  {entry.workbook or '—'}\n시트  {entry.sheet or '—'}\n셀  {entry.cell or '—'}")
        self.before_value.setText(entry.before or "(없음)")
        self.after_value.setText(entry.after or "(없음)")


# --- 화면 ------------------------------------------------------------------------------------
class DataManagementPage(QWidget):
    """설비현황 · 릴리즈노트 · 변경 이력. DB를 바꾸는 작업은 끝나면 saved로 알려 다른 화면이 다시 읽는다."""

    save_started = Signal()
    saved = Signal(object)
    save_failed = Signal()
    group_added = Signal(object)

    def __init__(self, gateway, *, context_provider=lambda: None, column_store=None):
        super().__init__()
        self.gateway, self.context_provider = gateway, context_provider
        self.columns = ColumnMemory(column_store if column_store is not None else QSettings("SETRON", "SETRON"))
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
        self.status_buttons[3].setToolTip("가져온 엑셀(기준일 · Site 단위)을 골라 통째로 지웁니다. 한 행씩 지우기는 현황표에서 합니다.")
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
        """화면에 들어올 때 지금 탭을 읽는다 (변경 이력은 들어올 때마다 다시 읽는다)."""
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

    def open_sheet(self, batch_id, note=""):
        """다른 탭에서 만든 현황표를 현황표 탭에서 연다."""
        self.sheet_panel.open_batch(batch_id)
        self.sheet_panel.saved_note = note
        self.select_tab(0)
        self.select_status(0)

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
        elif tab == 2 and not self.history_panel.request.busy:
            self.history_panel.reload()

    def attach_deletion(self, widget):
        """데이터 가져오기에 있던 저장 자료 삭제 화면을 이 화면의 '가져온 엑셀 삭제'로 옮긴다 (객체·연결은 그대로)."""
        self.deletion = widget
        self.delete_layout.removeWidget(self.delete_placeholder)
        self.delete_placeholder.deleteLater()
        self.delete_layout.addWidget(widget)
        table = getattr(widget, "table", None)
        if isinstance(table, QTableWidget):
            self.columns.attach(table, "delete")
            # 목록이 바뀔 때마다 기억한 폭(없으면 내용 폭)으로 맞춘다
            render = widget._render

            def render_and_fit():
                render()
                self.columns.fit(table, spread=False, fixed={0: 44})  # 0열은 체크 칸

            widget._render = render_and_fit  # 목록을 다 그린 뒤 기억한 폭(없으면 내용 폭)으로 맞춘다
            render_and_fit()

    def invalidate(self, *_):
        """다른 화면에서 저장·삭제·가져오기를 하면 다음에 볼 때 다시 읽는다."""
        for panel in self.panels:
            panel.loaded = False
        if self.isVisible():
            self._ensure_loaded()

    # DB 반영 ----------------------------------------------------------------------------------
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
        for panel in self.panels:
            panel.flush()  # 저장 중에 고친 칸 (바로 저장하는 패널만)

    def _sync_saving(self):
        for panel in self.panels:
            panel._sync()
