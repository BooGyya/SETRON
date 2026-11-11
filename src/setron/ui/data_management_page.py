"""데이터 관리 (11-10 개편): 설비현황 · 릴리즈노트 · 변경 이력 세 탭.

- 설비현황: 현황표(가져온 엑셀 시트를 그대로 보여 주고 칸을 직접 고침) · 설비(설비군 추가) · 위치(Site 추가는 화면 표시용)
  · 저장 자료 삭제(데이터 가져오기에서 옮겨 온 기존 삭제 화면).
- 릴리즈노트: 계열 목록(개조형은 상위 계열 아래) · 버전 줄 아래 Item 줄이 붙은 엑셀형 표 · Item 위험 지정.
- 변경 이력: 관리 저장 기록을 수정 일시 · 파일 · 시트 · 셀 · 수정 전 → 후로 보인다.

수정은 바로 저장한다 (검토대기 없음, 11-10 결정). DB 읽기·저장은 모두 워커에서 한다.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QBrush, QColor
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

EDITED = QColor("#fff2c4")
MUTED = QColor("#8a93a6")
SITE_KEY = "data_management/extra_sites"


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
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.setMinimumHeight(260)
    return table


def _fill(table, rows, keys=None):
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(_text(value))
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
        self.notice.setStyleSheet("color:#ad344d;" if error else f"color:{SECONDARY};")


# --- 설비현황 › 현황표 ---------------------------------------------------------------------
class SheetPanel(_Panel):
    def __init__(self, page):
        super().__init__(page)
        self.sheets, self.grid, self.originals, self.edits = (), None, {}, {}
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
        self.revert_button = button("되돌리기")
        self.save_button = button("수정 저장", primary=True)
        self.save_button.setMinimumWidth(150)
        self.revert_button.clicked.connect(self.revert)
        self.save_button.clicked.connect(self.save)
        for widget in (self.sheet_combo, self.source):
            row.addWidget(widget)
        row.addStretch()
        row.addWidget(self.revert_button)
        row.addWidget(self.save_button)
        card.body.addLayout(row)
        self.table = QTableWidget(0, 0)
        self.table.setAccessibleName("현황표 · 엑셀 시트 그대로")
        self.table.setMinimumHeight(420)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.itemChanged.connect(self._cell_changed)
        card.body.addWidget(self.table)
        card.body.addWidget(label("가져온 엑셀 시트를 원래 열·행 그대로 보여 줍니다. DB 값과 연결된 칸만 고칠 수 있고, 고친 칸은 "
                                  "노란색(원래 값 → 새 값)입니다. [수정 저장]을 누르면 바로 반영되고 변경 이력에 남습니다. "
                                  "Site · 기준일은 바꿀 수 없습니다.", 12, color=SECONDARY, wrap=True))
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
            self.say("저장된 현황표가 없습니다. 데이터 가져오기에서 먼저 저장해 주세요.")
            self._show(None)
            return
        index = max(0, self.sheet_combo.findData(previous))
        self.sheet_combo.setCurrentIndex(index)
        self._sheet_changed(index)

    def _sheet_changed(self, *_):
        batch_id = self.sheet_combo.currentData()
        if batch_id is None:
            return
        if self.edits and not self._confirm_discard():
            return
        self.say("시트를 읽고 있습니다…")
        self.run(lambda: self.page.gateway.data_admin("sheet_grid", batch_id), self._grid_loaded)

    def _confirm_discard(self):
        return QMessageBox.question(self, "저장하지 않은 수정", f"저장하지 않은 수정 {len(self.edits)}칸을 버릴까요?") \
            == QMessageBox.StandardButton.Yes

    def _grid_loaded(self, grid, error):
        if error is not None:
            self.say("시트를 읽지 못했습니다. 다시 시도해 주세요.", error=True)
            return
        self._show(grid)
        sheet = next((s for s in self.sheets if s.batch_id == grid.batch_id), None)
        self.source.setText(f"원본 파일: {grid.workbook or '파일 이름 없음'} · 시트 {grid.sheet}"
                            + (f" · 가져온 시각 {_text(sheet.imported_at)[:16]}" if sheet and sheet.imported_at else ""))
        note, self.saved_note = self.saved_note, ""
        self.say(note + f"연결된 칸 {sum(b.editable for b in grid.bindings.values())}개를 고칠 수 있습니다.")

    def _show(self, grid):
        self.grid, self.edits, self.originals = grid, {}, {}
        self.table.blockSignals(True)
        self.table.clear()
        if grid is None or not grid.cells:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.table.blockSignals(False)
            self._sync()
            return
        positions = [_split(a) for a in grid.cells] + [_split(a) for a in grid.bindings]
        columns = max(c for c, _ in positions) + 1
        first, last = min(r for _, r in positions), max(r for _, r in positions)
        self.table.setColumnCount(columns)
        self.table.setRowCount(last - first + 1)
        self.table.setHorizontalHeaderLabels([_col(c) for c in range(columns)])
        self.table.setVerticalHeaderLabels([str(r) for r in range(first, last + 1)])
        self.first_row = first
        for r in range(first, last + 1):
            for c in range(columns):
                address = f"{_col(c)}{r}"
                binding = grid.bindings.get(address)
                value = binding.current if binding else _text(grid.cells.get(address))
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, address)
                if binding is None or not binding.editable:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if binding is None:
                        item.setForeground(QBrush(MUTED))
                else:
                    item.setToolTip(f"{binding.label} · 고칠 수 있음")
                    self.originals[address] = value
                self.table.setItem(r - first, c, item)
        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)
        self._sync()

    def _cell_changed(self, item):
        address = item.data(Qt.ItemDataRole.UserRole)
        if address not in self.originals:
            return
        value, original = item.text().strip(), self.originals[address]
        self.table.blockSignals(True)
        if value == original:
            self.edits.pop(address, None)
            item.setBackground(QBrush())
            item.setToolTip(f"{self.grid.bindings[address].label} · 고칠 수 있음")
        else:
            self.edits[address] = value
            item.setBackground(QBrush(EDITED))
            item.setToolTip(f"{self.grid.bindings[address].label}: {original or '(빈칸)'} → {value or '(빈칸)'}")
        self.table.blockSignals(False)
        self._sync()

    def revert(self):
        if self.grid is not None:
            self._show(self.grid)

    def _sync(self):
        count = len(self.edits)
        self.save_button.setText(f"수정 저장 ({count}칸)" if count else "수정 저장")
        self.save_button.setEnabled(bool(count) and not self.page.saving)
        self.revert_button.setEnabled(bool(count))

    def changes(self):
        """고친 칸을 기록별 변경으로 묶는다. SW 버전을 바꾸면 릴리즈 연결은 비워 다시 판정한다."""
        grouped = defaultdict(dict)
        for address, value in self.edits.items():
            binding = self.grid.bindings[address]
            key = (binding.table, tuple(sorted(binding.key.items())))
            grouped[key][binding.field] = value or None
            if binding.field == "version_raw":
                grouped[key]["release_version_id"] = None
        return [(table, dict(key), changes) for (table, key), changes in grouped.items()]

    def save(self):
        if not self.edits or self.grid is None:
            return False
        lines = [f"{self.grid.bindings[a].label}: {self.originals[a] or '(빈칸)'} → {v or '(빈칸)'}" for a, v in self.edits.items()]
        if QMessageBox.question(self, "현황표 수정 저장", "아래 수정을 바로 저장할까요?\n\n" + "\n".join(lines[:12])
                                + (f"\n… 외 {len(lines) - 12}칸" if len(lines) > 12 else "")) != QMessageBox.StandardButton.Yes:
            return False
        return self.page.save_changes(self.changes(), self, after=self.reload)


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
        self.error = label("", 12, color="#ad344d", wrap=True)
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
    def __init__(self, page):
        super().__init__(page)
        self.groups, self.equipment = (), ()
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
        self.group_table = _table(("분류", "설비군", "개조형", "설비"), "설비군 목록")
        self.group_table.itemSelectionChanged.connect(self._render_equipment)
        left.body.addWidget(self.group_table)
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
        self.equipment_table = _table(("설비 ID", "설비군 · 모델", "Site · Line", "설치 기록", "최근 기준일"), "설비 목록")
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
        self.say("")
        children = defaultdict(list)
        for g in self.groups:
            if g.parent_id is not None:
                children[g.parent_id].append(g.name)
        bases = [g for g in self.groups if g.parent_id is None]
        order = {"POLY": 0, "OXIDE": 1, "KCTC": 2, "HPA": 3}
        bases.sort(key=lambda g: (order.get(g.category, 9), g.name))
        _fill(self.group_table, [(g.category, g.name, " · ".join(children[g.group_id]) or "—",
                                  f"{g.equipment_count + sum(c.equipment_count for c in self.groups if c.parent_id == g.group_id)}대")
                                 for g in bases], [g.group_id for g in bases])
        for combo, values, title in ((self.site_filter, sorted({e.site for e in self.equipment if e.site}), "Site 전체"),
                                     (self.line_filter, sorted({e.line for e in self.equipment if e.line}), "Line 전체")):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(title, None)
            for v in values:
                combo.addItem(v, v)
            combo.blockSignals(False)
        self._render_equipment()

    def selected_group(self):
        rows = self.group_table.selectionModel().selectedRows() if self.group_table.selectionModel() else []
        if not rows:
            return None
        return self.group_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _render_equipment(self, *_):
        gid = self.selected_group()
        family = {gid} | {g.group_id for g in self.groups if g.parent_id == gid} if gid is not None else None
        text = self.search.text().strip().casefold()
        site, line = self.site_filter.currentData(), self.line_filter.currentData()
        rows = [e for e in self.equipment if (family is None or family & set(e.group_ids))
                and (not text or text in e.code.casefold()) and (site is None or e.site == site)
                and (line is None or e.line == line)]
        name = next((g.name for g in self.groups if g.group_id == gid), None)
        self.equipment_title.setText(f"설비 목록 · {name + ' · ' if name else ''}{len(rows)}대")
        _fill(self.equipment_table, [(e.code, f"{e.group_names} · {e.model_name or '모델 미확인'}",
                                      f"{e.site or '?'} · {e.line or '미지정'}" if e.snapshot_count else "관측 없음", f"{e.snapshot_count}건", e.base_date)
                                     for e in rows], [e.equipment_id for e in rows])

    def add_group(self):
        if self.page.gateway is None:
            return False
        dialog = AddGroupDialog(self, self.groups)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        name, category, parent = dialog.values()
        self.say(f"설비군 '{name}'을 등록하고 있습니다…")
        return self.run(lambda: self.page.gateway.data_admin("create_group", name, category, parent), self._group_added)

    def _group_added(self, group, error):
        if error is not None:
            self.say(f"설비군을 등록하지 못했습니다: {error}" if isinstance(error, ValueError)
                     else "설비군을 등록하지 못했습니다. DB 연결을 확인해 주세요.", error=True)
            return
        self.page.group_added.emit(group)
        self.reload()
        self.page.history_panel.loaded = False


# --- 설비현황 › 위치 ------------------------------------------------------------------------
class LocationPanel(_Panel):
    """Site → Fab → Line 트리. Site·Fab·Line 테이블이 없어 저장된 관측에서 만든다. Site 추가는 화면 표시용."""

    def __init__(self, page):
        super().__init__(page)
        self.equipment = ()
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
        self.tree.setMinimumHeight(320)
        self.tree.itemSelectionChanged.connect(self._render_detail)
        left.body.addWidget(self.tree)
        left.body.addWidget(label("Site 추가는 화면 표시용이며 DB에 저장하지 않습니다 (설비의 Site는 가져온 현황표로 정해집니다).",
                                  12, color=SECONDARY, wrap=True))
        self.detail_title = label("위치를 고르세요", 20, medium=True)
        right.body.addWidget(self.detail_title)
        self.detail_info = label("", 13, color=SECONDARY, wrap=True)
        right.body.addWidget(self.detail_info)
        right.body.addWidget(label("소속 설비", 16, medium=True))
        self.equipment_table = _table(("설비 ID", "설비군", "모델", "최근 기준일"), "위치 소속 설비")
        right.body.addWidget(self.equipment_table)
        right.body.addWidget(self.notice)

    def extra_sites(self):
        value = self.page.site_store.value(SITE_KEY, [])
        return [str(v) for v in (value if isinstance(value, list) else [value] if value else [])]

    def reload(self):
        self.say("위치를 읽고 있습니다…")
        return self.run(lambda: self.page.gateway.data_admin("equipment_overview"), self._loaded)

    def _loaded(self, value, error):
        if error is not None:
            self.say("위치를 읽지 못했습니다.", error=True)
            return
        self.loaded = True
        self.equipment = value[1]
        self.say("")
        self._render_tree()

    def _render_tree(self):
        self.tree.clear()
        sites = defaultdict(lambda: defaultdict(int))
        for e in self.equipment:
            if e.snapshot_count:  # 관측이 모두 지워진 설비는 위치가 없다
                sites[e.site or "Site 미확인"][e.line or "Line 미지정"] += 1
        for site in self.extra_sites():
            sites.setdefault(site, defaultdict(int))
        for site in sorted(sites):
            stored = site not in self.extra_sites() or any(e.site == site for e in self.equipment)
            node = QTreeWidgetItem([f"{site}" + ("" if stored else "  (표시 전용 · 설비 없음)")])
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
                                 + (" · 표시 전용 Site" if site in self.extra_sites() and not rows else ""))
        _fill(self.equipment_table, [(e.code, e.group_names, e.model_name, e.base_date) for e in rows])

    def add_site(self):
        name, ok = QInputDialog.getText(self, "Site 추가", "Site 이름 (화면 표시용)")
        name = (name or "").strip()
        if not ok or not name:
            return False
        known = {e.site for e in self.equipment} | set(self.extra_sites())
        if name in known:
            self.say(f"'{name}'은 이미 있는 Site입니다.", error=True)
            return False
        self.page.site_store.setValue(SITE_KEY, self.extra_sites() + [name])
        self.say(f"'{name}'을 위치 목록에 추가했습니다 (화면 표시용).")
        self._render_tree()
        return True


# --- 릴리즈노트 ------------------------------------------------------------------------------
RELEASE_COLUMNS = ("Version", "Date", "No", "Content (Item)", "적용 모델", "위험")
RELEASE_FIELDS = {0: ("release_version", "version_raw"), 1: ("release_version", "released_at"),
                  2: ("item", "item_no"), 3: ("item", "content"), 4: ("release_version_item", "target_group_ids")}


class ReleasePanel(_Panel):
    def __init__(self, page):
        super().__init__(page)
        self.families, self.rows, self.groups, self.edits, self.originals = (), (), {}, {}, {}
        self.family_id, self.variant = None, None
        body = QHBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        left, middle, right = Card(padding=16), Card(padding=20), Card(padding=20)
        left.setFixedWidth(220)
        right.setFixedWidth(320)
        body.addWidget(left)
        body.addWidget(middle, 1)
        body.addWidget(right)
        left.body.addWidget(label("계열", 20, medium=True))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAccessibleName("릴리즈노트 계열 (개조형은 상위 계열 아래)")
        self.tree.setMinimumHeight(360)
        self.tree.itemSelectionChanged.connect(self._family_changed)
        left.body.addWidget(self.tree)
        head = QHBoxLayout()
        self.family_title = label("계열을 고르세요", 20, medium=True)
        self.family_info = label("", 12, color=SECONDARY)
        self.release_save = button("수정 저장", primary=True)
        self.release_save.setMinimumWidth(150)
        self.release_save.clicked.connect(self.save)
        head.addWidget(self.family_title)
        head.addWidget(self.family_info)
        head.addStretch()
        head.addWidget(self.release_save)
        middle.body.addLayout(head)
        self.table = QTableWidget(0, len(RELEASE_COLUMNS))
        self.table.setHorizontalHeaderLabels(RELEASE_COLUMNS)
        self.table.setAccessibleName("릴리즈노트 · 버전 줄 아래 Item 줄")
        self.table.setMinimumHeight(420)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._cell_changed)
        self.table.itemSelectionChanged.connect(self._render_item)
        self.table.cellClicked.connect(self._toggle_version)
        middle.body.addWidget(self.table)
        middle.body.addWidget(label("엑셀 시트처럼 보여 주고 칸을 바로 고칩니다. 버전 칸(▼/▶)을 누르면 그 버전의 Item 줄을 접거나 펼칩니다. "
                                    "적용 모델은 설비군 이름을 쉼표로 적습니다.", 12, color=SECONDARY, wrap=True))
        middle.body.addWidget(self.notice)
        self.item_title = label("Item을 고르세요", 17, medium=True, wrap=True)
        self.item_info = label("", 13, color=SECONDARY, wrap=True)
        self.risk_button = button("위험 지정", primary=True)
        self.risk_button.clicked.connect(self.open_risk)
        right.body.addWidget(self.item_title)
        right.body.addWidget(self.item_info)
        right.body.addWidget(self.risk_button)
        right.body.addStretch()
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
        self.say("")
        if self.family_id is not None:
            self._load_rows(self.family_id)

    def _family_changed(self):
        items = self.tree.selectedItems()
        if not items:
            return
        family_id, variant = items[0].data(0, Qt.ItemDataRole.UserRole)
        if self.edits and family_id != self.family_id and QMessageBox.question(
                self, "저장하지 않은 수정", f"저장하지 않은 수정 {len(self.edits)}칸을 버릴까요?") != QMessageBox.StandardButton.Yes:
            return
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
            values = (("▼ " + row.version_raw) if row.first_of_version else "", row.released_at if row.first_of_version else "",
                      row.item_no, row.content, row.applied_models, row.risk or "일반")
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                editable = (c in (0, 1) and row.first_of_version) or (c in (2, 3, 4) and row.item_id is not None)
                if not editable:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                else:
                    self.originals[(i, c)] = row.version_raw if c == 0 else value
                    if c == 0:
                        item.setText(row.version_raw)
                if c == 5 and row.risk:
                    item.setForeground(QBrush(QColor("#b3263e")))
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
        self._sync()
        self._render_item()

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
            item.setText(("▶ " if hidden else "▼ ") + self.rows[row].version_raw)
        self.table.blockSignals(False)

    def _cell_changed(self, item):
        key = (item.row(), item.column())
        if key not in self.originals:
            return
        value = item.text().strip()
        if key[1] == 0:
            value = value.removeprefix("▼ ").removeprefix("▶ ").strip()
        self.table.blockSignals(True)
        if value == self.originals[key]:
            self.edits.pop(key, None)
            item.setBackground(QBrush())
        else:
            self.edits[key] = value
            item.setBackground(QBrush(EDITED))
            item.setToolTip(f"{self.originals[key] or '(빈칸)'} → {value or '(빈칸)'}")
        self.table.blockSignals(False)
        self._sync()

    def _sync(self):
        count = len(self.edits)
        self.release_save.setText(f"수정 저장 ({count}칸)" if count else "수정 저장")
        self.release_save.setEnabled(bool(count) and not self.page.saving)

    def changes(self):
        grouped = defaultdict(dict)
        for (r, c), value in self.edits.items():
            row = self.rows[r]
            table, field = RELEASE_FIELDS[c]
            if table == "release_version":
                key = {"release_version_id": row.release_version_id}
                if field == "released_at":
                    value = date.fromisoformat(value) if value else None
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
            grouped[(table, tuple(sorted(key.items())))][field] = value or None
        return [(table, dict(key), changes) for (table, key), changes in grouped.items()]

    def save(self):
        if not self.edits:
            return False
        try:
            changes = self.changes()
        except ValueError as error:
            self.say(f"{error}. 날짜는 YYYY-MM-DD, 적용 모델은 설비군 이름으로 적어 주세요.", error=True)
            return False
        if QMessageBox.question(self, "릴리즈노트 수정 저장", f"수정 {len(self.edits)}칸을 바로 저장할까요?") \
                != QMessageBox.StandardButton.Yes:
            return False
        return self.page.save_changes(changes, self, after=lambda: self._load_rows(self.family_id))

    def selected_row(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.rows[rows[0].row()] if rows and self.rows else None

    def _render_item(self):
        row = self.selected_row()
        self.risk_button.setEnabled(row is not None and row.item_id is not None and self.page.risk_opener is not None)
        if row is None or row.item_id is None:
            self.item_title.setText("Item을 고르세요")
            self.item_info.setText("")
            return
        self.item_title.setText(f"Item · {row.content or '(내용 없음)'}")
        self.item_info.setText(f"버전 {row.version_raw} · No {row.item_no or '?'}\n적용 모델 {row.applied_models}\n"
                               f"위험 {row.risk or '지정 없음'}\n\n위험 지정은 유형 · 영향 버전(시작~해제) · 근거를 입력하고 "
                               "영향을 확인한 뒤 저장합니다.")

    def open_risk(self):
        row = self.selected_row()
        if row is None or row.item_id is None or self.page.risk_opener is None:
            return False
        opened = self.page.risk_opener(self.family_id, row.item_id)
        self._load_rows(self.family_id)
        return opened


# --- 변경 이력 -------------------------------------------------------------------------------
class HistoryPanel(_Panel):
    PERIODS = (("최근 7일", 7), ("최근 30일", 30), ("전체", None))
    KINDS = (("대상 전체", None), ("설비 현황", ("equipment", "equipment_snapshot", "equipment_version", "equipment_pm")),
             ("릴리즈노트", ("release_version", "item", "release_version_item")), ("설비군", ("equipment_group",)),
             ("위험 지정", ("risk_rule",)))

    def __init__(self, page):
        super().__init__(page)
        self.entries = ()
        body = QHBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        left, right = Card(padding=20), Card(padding=20)
        right.setFixedWidth(420)
        body.addWidget(left, 1)
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
        filters.addStretch()
        refresh = button("새로 읽기")
        refresh.clicked.connect(self.reload)
        filters.addWidget(refresh)
        left.body.addLayout(filters)
        self.table = _table(("수정 일시", "수정 파일 · 시트 · 셀", "대상", "항목", "수정 전", "수정 후"), "변경 이력 (완료)")
        self.table.itemSelectionChanged.connect(self._render_detail)
        left.body.addWidget(self.table)
        left.body.addWidget(self.notice)
        self.detail_title = label("변경을 고르세요", 20, medium=True, wrap=True)
        self.detail = label("", 13, color=SECONDARY, wrap=True)
        self.before_after = _table(("항목", "수정 전", "수정 후"), "변경 전후")
        self.before_after.setMinimumHeight(90)
        self.before_after.setMaximumHeight(110)
        right.body.addWidget(self.detail_title)
        right.body.addWidget(self.detail)
        right.body.addWidget(self.before_after)
        right.body.addStretch()

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
        _fill(self.table, [(_text(e.at)[:16].replace("T", " ") if e.at else "", e.place, e.target, e.field, e.before, e.after)
                           for e in rows], list(range(len(rows))))
        self._shown = rows

    def _render_detail(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return
        entry = self._shown[rows[0].row()]
        self.detail_title.setText(f"{entry.target} · 변경 전후")
        self.detail.setText(f"수정 일시  {_text(entry.at)[:16].replace('T', ' ')}\n수정 파일  {entry.workbook or '파일 이름 없음'}\n"
                            f"시트 · 셀  {(entry.sheet or '—') + (' · ' + entry.cell if entry.cell else '')}\n종류  {entry.kind}")
        _fill(self.before_after, [(entry.field, entry.before, entry.after)])
        after = self.before_after.item(0, 2)
        if after is not None:
            after.setBackground(QBrush(EDITED))


# --- 화면 ------------------------------------------------------------------------------------
class DataManagementPage(QWidget):
    """설비현황 · 릴리즈노트 · 변경 이력. 저장은 관리 저장(gateway.save)을 쓰고 끝나면 saved로 알린다."""

    save_started = Signal()
    saved = Signal(object)
    save_failed = Signal()
    group_added = Signal(object)

    def __init__(self, gateway, *, context_provider=lambda: None, risk_opener=None, site_store=None):
        super().__init__()
        self.gateway, self.context_provider, self.risk_opener = gateway, context_provider, risk_opener
        self.site_store = site_store if site_store is not None else QSettings("SETRON", "SETRON")
        self.saving, self.deletion = False, None
        self.save_request = ReadRequest(self)
        self.save_request.completed.connect(self._saved)
        self._save_owner = None
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
                                 14, color="#ad344d", wrap=True)
        self.unavailable.setVisible(gateway is None)
        body.addWidget(self.unavailable)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        # 설비현황
        status = QWidget()
        status_body = QVBoxLayout(status)
        status_body.setContentsMargins(0, 0, 0, 0)
        status_body.setSpacing(16)
        sub, self.status_group, self.status_buttons = _segment(("현황표", "설비", "위치", "저장 자료 삭제"), "설비현황")
        status_body.addWidget(sub)
        self.status_stack = QStackedWidget()
        self.sheet_panel, self.equipment_panel, self.location_panel = SheetPanel(self), EquipmentPanel(self), LocationPanel(self)
        # 삭제 화면은 따로 스크롤 영역에 넣지 않는다. 창 전체 스크롤 안에 스크롤 영역을 또 두면 크기 맞춤이 끝없이 반복된다.
        self.delete_host = QWidget()
        self.delete_layout = QVBoxLayout(self.delete_host)
        self.delete_layout.setContentsMargins(0, 0, 0, 0)
        self.delete_placeholder = label("저장 자료 삭제를 쓸 수 없습니다 (DB 미연결).", 14, color=SECONDARY)
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
        """데이터 가져오기에 있던 저장 자료 삭제 화면을 이 화면의 '저장 자료 삭제'로 옮긴다 (객체·연결은 그대로)."""
        self.deletion = widget
        self.delete_layout.removeWidget(self.delete_placeholder)
        self.delete_placeholder.deleteLater()
        self.delete_layout.addWidget(widget)

    def invalidate(self, *_):
        """다른 화면에서 저장·삭제·가져오기를 하면 다음에 볼 때 다시 읽는다."""
        for panel in (self.sheet_panel, self.equipment_panel, self.location_panel, self.release_panel, self.history_panel):
            panel.loaded = False
        if self.isVisible():
            self._ensure_loaded()

    # 저장 ---------------------------------------------------------------------------------------
    def save_changes(self, changes, owner, *, after=None):
        """[(table, key, {field: value})]를 최신 검토 지문으로 묶어 한 번에 저장한다 (워커)."""
        if self.gateway is None or self.saving or not changes:
            return False
        gateway, context, requested = self.gateway, deepcopy(self.context_provider()), list(changes)

        def work():
            # 저장 직전의 검토 지문(revision)으로 묶는다. 그 사이 다른 곳에서 바뀌었으면 저장이 거부된다.
            edits = tuple(RecordEdit(table, key, gateway.get(table, key).revision, values) for table, key, values in requested)
            return gateway.save(edits, context) if context is not None else gateway.save(edits)

        self.saving, self._save_owner, self._after_save = True, owner, after
        owner.say("저장하고 있습니다…")
        self._sync_saving()
        self.save_started.emit()
        started = self.save_request.start(BackgroundRead(work))
        if not started:
            self.saving = False
            self._sync_saving()
        return started

    def _saved(self, result, error):
        owner, after = self._save_owner, self._after_save
        self.saving, self._save_owner, self._after_save = False, None, None
        self._sync_saving()
        if error is not None:
            message = str(error) if isinstance(error, ValueError) else "DB 저장에 실패했습니다. 연결을 확인해 주세요."
            owner.say(f"저장하지 못했습니다: {message}", error=True)
            self.save_failed.emit()
            return
        owner.say("저장했습니다. 변경 이력에 기록했습니다.")
        owner.saved_note = "저장했습니다 · 변경 이력에 기록했습니다. "
        self.history_panel.loaded = False
        self.saved.emit(result)
        if after is not None:
            after()

    def _sync_saving(self):
        self.sheet_panel._sync()
        self.release_panel._sync()
