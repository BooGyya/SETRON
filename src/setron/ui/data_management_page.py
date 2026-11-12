"""데이터 관리 (11-10 개편, 11-11 2·3차 수정): 설비현황 · 릴리즈노트 · 변경 이력 세 탭.

- 설비현황
  - 현황표: DB에 들어간 값을 가져오기 3단계와 같은 항목(호기 · Line · 설비군 · 모델명 · 메모리/파운드리 · SW 버전)으로
    보인다 (11-11 6차). 칸을 고친 뒤 [저장]으로 한꺼번에 저장하고, 행을 추가·삭제하며, [되돌리기]로 바로 앞 단계를
    되돌린다.
  - 설비: 설비군 추가 · 위치: Site 추가 뒤 빈 현황표 만들기 · 가져온 엑셀 삭제: 데이터 가져오기에서 옮겨 온 삭제 화면.
- 릴리즈노트: 계열 목록 · 버전 줄 아래 Item 줄이 붙은 엑셀형 표. 칸을 고치면 바로 저장하고, 버전·Item 줄을 추가한다.
  위험 지정은 아이템 매니저에서만 한다.
- 변경 이력: 왼쪽 목록(수정 일시 · 구분 · 파일 · 셀 · 수정 전 · 수정 후), 오른쪽 세부 정보. 탭에 들어올 때마다 다시 읽는다.

모든 표는 열 경계를 끌어 폭을 바꿀 수 있고, 바꾼 폭은 이 PC에 기억한다. DB 읽기·저장은 모두 워커에서 한다.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from PySide6.QtCore import QDate, QEvent, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QRadioButton, QScrollArea, QSizePolicy, QStackedWidget, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTabBar, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from shiboken6 import isValid

from setron.features.management.schemas import RecordEdit
from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, button, label
from setron.ui.theme import font, set_widget_font
from setron.ui.equipment import ignore_width
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.theme import ASSETS, LINE, PRIMARY, SECONDARY

SAVING = QColor("#fff2c4")     # 고쳤지만 아직 저장하지 않은 칸 · 저장 중인 칸
SAVED = QColor("#e3f4e8")      # 이번에 저장한 칸
NEW_ROW = QColor("#fdecef")    # 새 행의 아직 비어 있는 필수 칸
MUTED = QColor("#5d6678")      # DB와 연결되지 않은 원본 값
DELETE_ROW = QColor("#f8d7da")  # 저장하면 지울 행 (삭제 예정)
DANGER = "#ad344d"
TABLE_STYLE = "QTableWidget {font-size:14px;} QHeaderView::section {font-size:13px;padding:4px 6px;}"
COLUMN_KEY = "data_management/columns/"


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


def _date_picker(day=None, parent=None, *, height=38):
    """버전 리플레이 · 대시보드 기준날짜와 같은 날짜 칸과 달력 팝업 (11-12)."""
    from setron.ui.date_range import DatePickerEdit

    control = DatePickerEdit(parent, open_on_click=True)
    control.setProperty("queryField", True)
    icon = (ASSETS / "import-ver5/fdb91.svg").as_posix()
    control.setStyleSheet(f"QDateEdit::down-arrow {{image:url({icon});width:16px;height:16px;}}")
    control.setDisplayFormat("yyyy-MM-dd")
    control.setKeyboardTracking(False)
    if height:
        control.setFixedHeight(height)
    control.setDate(QDate(day.year, day.month, day.day) if isinstance(day, date) else QDate.currentDate())
    return control


def _longest_kept(order, rank):
    """``order`` 안에서 원래 순서(``rank``)를 지킨 가장 긴 부분 줄 (증가 부분 수열). 옮기지 않은 Item들이다."""
    tails, tail_index, previous = [], [], [None] * len(order)
    for index, item in enumerate(order):
        value = rank[item]
        place = bisect_left(tails, value)
        if place == len(tails):
            tails.append(value)
            tail_index.append(index)
        else:
            tails[place], tail_index[place] = value, index
        previous[index] = tail_index[place - 1] if place else None
    kept, index = [], tail_index[-1] if tail_index else None
    while index is not None:
        kept.append(order[index])
        index = previous[index]
    return kept[::-1]


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


def _history_buttons(panel, what):
    """되돌리기 · 다시 실행 아이콘 버튼 두 개와 단축키(Ctrl+Z · Ctrl+Y · Ctrl+Shift+Z)를 만든다 (11-12)."""
    buttons = []
    for asset, name, tip, slot in (
            ("undo.svg", "되돌리기", f"되돌리기 (Ctrl+Z) · 바로 앞 단계 하나를 되돌립니다 ({what})", panel.undo),
            ("redo.svg", "다시 실행", "다시 실행 (Ctrl+Y) · 되돌린 단계를 다시 적용합니다 (되돌린 저장은 같은 변경으로 다시 저장)",
             panel.redo)):
        control = button("", asset=asset)
        control.setText("")
        control.setAccessibleName(name)
        control.setToolTip(tip)
        control.setFixedWidth(44)
        control.clicked.connect(slot)
        buttons.append(control)
    for keys, slot in ((QKeySequence.StandardKey.Undo, panel.undo), (QKeySequence.StandardKey.Redo, panel.redo),
                       (QKeySequence("Ctrl+Shift+Z"), panel.redo)):
        shortcut = QShortcut(QKeySequence(keys), panel)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(slot)
    return buttons


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
        self.notice.hide()
        self.last_note = ""

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
        """오류만 화면에 보인다. 진행 · 결과 안내 문구는 지웠다 (11-12 요청). 마지막 안내는 ``last_note``에 남긴다."""
        self.last_note = text
        self.notice.setText(text if error else "")
        self.notice.setVisible(bool(error and text))
        self.notice.setStyleSheet(f"color:{DANGER};" if error else f"color:{SECONDARY};")

    def flush(self):
        """다른 저장이 끝난 뒤 밀린 수정을 저장한다 (바로 저장하는 패널만)."""

    def _sync(self):
        """저장 중에는 버튼을 잠근다."""


# --- 설비현황 › 현황표 ---------------------------------------------------------------------
# 대분류(POLY · OXIDE · KCTC)는 설비군의 값이라 설비군에서 자동으로 보이고, 모델명은 설비군을 따라간다 (11-12).
# 대시보드의 '분류' 필터(메모리/파운드리)와 헷갈리지 않게 '대분류'라고 부른다.
SHEET_COLUMNS = (("equipment_code", "설비 호기"), ("line", "Line"), ("category", "대분류"), ("group", "설비군"),
                 ("model_name", "모델명"), ("production_area", "메모리/파운드리"), ("version_raw", "SW 버전"), ("pm", "PM"))
COLUMN = {field: index for index, (field, _) in enumerate(SHEET_COLUMNS)}
AREA_LABELS = {"MEMORY": "메모리", "FOUNDRY": "파운드리", None: ""}
REQUIRED_NEW = ("equipment_code", "group", "production_area", "version_raw")  # 메모리/파운드리도 필수 (11-12: 비우면 임의로 정해졌다)
CATEGORY_CHOICES = ("POLY", "OXIDE", "KCTC")


SELECTION_TINT = QColor(60, 92, 233, 14)   # 고른 줄 위에 얹는 아주 옅은 파란 막 (칸 색이 그대로 보이게)
SELECTION_EDGE = QColor(60, 92, 233, 220)  # 고른 줄은 위아래 파란 선으로 표시


def _sheet_key(batch_id, base_date):
    """현황표 하나 = 입력 작업 · 기준일. 시연 seed는 입력 작업 하나에 기준일이 여럿이라 둘 다로 가른다 (11-12)."""
    return f"{batch_id}|{base_date or ''}"


def _parse_key(key):
    batch, _, day = str(key).partition("|")
    return int(batch), (date.fromisoformat(day) if day else None)


class _KeepColorsDelegate(QStyledItemDelegate):
    """고른 줄도 칸 색(분홍 필수 · 노랑 수정 · 빨강 삭제 예정)을 그대로 보이고, 그 위에 옅은 막과 위아래 선만 얹는다 (11-12).

    기본 선택 색은 칸 배경을 덮어서, 입력 중인 새 행에서 어느 칸을 채워야 하는지 보이지 않았다.
    """

    def paint(self, painter, option, index):
        if not option.state & QStyle.StateFlag.State_Selected:
            super().paint(painter, option, index)
            return
        plain = QStyleOptionViewItem(option)
        plain.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, plain, index)
        painter.save()
        painter.fillRect(option.rect, SELECTION_TINT)
        painter.setPen(QPen(SELECTION_EDGE, 2))
        rect = option.rect.adjusted(0, 1, 0, -1)
        painter.drawLine(rect.topLeft(), rect.topRight())
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.restore()


class _ChoiceDelegate(_KeepColorsDelegate):
    """칸을 고를 때 목록을 보인다. ``editable``이면 목록에 없는 값도 적을 수 있다."""

    def __init__(self, parent, options, *, editable=False, placeholder=""):
        super().__init__(parent)
        self.options, self.editable, self.placeholder = options, editable, placeholder

    def createEditor(self, parent, option, index):
        editor = ComboBox(parent)
        editor.setEditable(self.editable)
        editor.addItems(list(self.options()))
        if self.editable and self.placeholder:  # 고를 것이 없으면 새 이름을 적으라고 알린다
            editor.lineEdit().setPlaceholderText(self.placeholder)
        return editor

    def setEditorData(self, editor, index):
        text = index.data(Qt.ItemDataRole.EditRole) or ""
        found = editor.findText(text)
        if found >= 0:
            editor.setCurrentIndex(found)
        elif self.editable:
            editor.setEditText(text)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class _DateDelegate(_KeepColorsDelegate):
    """표의 날짜 칸을 고칠 때 리플레이와 같은 달력을 연다 (11-12). 값은 YYYY-MM-DD 글자로 돌려준다."""

    def createEditor(self, parent, option, index):
        editor = _date_picker(None, parent, height=0)
        # 칸을 열면 바로 달력이 뜬다(편집기가 화면에 놓인 뒤). 그 사이 편집기가 닫혀 지워졌으면 부르지 않는다.
        def open_when_shown(tries=10):
            if not isValid(editor):
                return
            if editor.isVisible():
                editor.open_calendar()
            elif tries:
                QTimer.singleShot(20, lambda: open_when_shown(tries - 1))

        QTimer.singleShot(0, open_when_shown)
        return editor

    def setEditorData(self, editor, index):
        text = (index.data(Qt.ItemDataRole.EditRole) or "").strip()
        try:
            day = date.fromisoformat(text)
        except ValueError:
            day = date.today()
        editor.setDate(QDate(day.year, day.month, day.day))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.date().toPython().isoformat(), Qt.ItemDataRole.EditRole)


# [?] 가이드: (제목, 언제, 설명, 버튼 동작, 버튼 글자)
GUIDE_STEPS = (
    ("빈 현황표 만들기", "그 Site · 기준일 현황표가 없을 때", "현황표 탭 > [＋ 빈 현황표]에서 Site · 기준일을 고릅니다. 새 Site면 이름을 "
     "적으면 함께 등록합니다. 메모리/파운드리를 꼭 고릅니다(새 행 · 새 라인의 기본값). 설비의 Site · 기준일은 행을 넣은 "
     "현황표가 정하므로, 꼭 그 Site의 현황표에 넣습니다.",
     "create", "빈 현황표 만들기"),
    ("행 추가 · 값 입력", "", "그 현황표를 고른 뒤 [＋ 행 추가]를 누르면 표 맨 아래에 새 줄(＋)이 생깁니다. 칸을 두 번 눌러 입력합니다. "
     "분홍 칸은 아직 비어 있는 필수 칸입니다.", "add", "행 추가"),
    ("저장 · 확인", "", "[저장]을 누르면 한꺼번에 DB에 들어가고 변경 이력에 남습니다. 처음 보는 라인은 그 줄의 메모리/파운드리로, 처음 보는 "
     "설비군은 대분류를 확인한 뒤 함께 등록합니다. 잘못 넣었으면 되돌리기 아이콘(Ctrl+Z)으로 마지막 저장을 되돌립니다.",
     "location", "위치 탭에서 확인"),
)
GUIDE_FIELDS = ("<table cellspacing='0' cellpadding='4' style='border-collapse:collapse'>"
                + "".join(f"<tr><td><b>{name}</b></td><td style='color:{'#ad344d' if need == '필수' else '#5d6678'}'>{need}</td>"
                          f"<td>{text}</td></tr>" for name, need, text in (
                              ("설비 호기", "필수", "설비 ID (예: CC4H101)"),
                              ("Line", "선택", "목록에서 고르거나 새 이름 (새 라인은 저장 때 등록)"),
                              ("대분류", "자동", "설비군에서 정해짐 · 새 설비군이면 POLY · OXIDE · KCTC 중 고름"),
                              ("설비군", "필수", "목록에서 고름 (없으면 저장 때 등록)"),
                              ("모델명", "자동", "설비군과 같음"),
                              ("메모리/파운드리", "필수", "라인을 고르면 그 라인 값 · 모르는 라인은 직접 고름"),
                              ("SW 버전", "필수", "설비군을 고르면 최신 버전이 자동으로 채워짐 · 실제 버전으로 고침")))
                + "</table>")
GUIDE_NOTE = ("위치 탭에서는 Site · 라인 이름을 표에서 바로 고치고(같은 라인 이름이면 합치기) 설비를 다른 라인으로 옮깁니다. "
              "설비 탭에서는 설비군 대분류 · (직접 추가한 설비군의) 이름을 표에서 고칩니다.")


class SheetPanel(_Panel):
    """현황표를 DB에 들어간 값 기준으로 보이고 고친다 (11-11 6차): 설비 × SW 한 줄, 가져오기 3단계와 같은 항목.

    칸 수정 · 행 추가 · 행 삭제 · 설비군 변경을 모아 두었다가 [저장]으로 한꺼번에 반영하고, [되돌리기]는 바로 앞 단계
    하나(또는 마지막 저장 전체)를 되돌린다. SW 계열은 보이지 않는다(VOC 1).
    """

    def __init__(self, page):
        super().__init__(page)
        self.sheets, self.data, self.view = (), None, []
        self.edits, self.new_rows, self.deletes, self.steps, self.sort = {}, [], {}, [], None
        self.redo_steps = []  # 되돌린 단계 (다시 실행용). 새로 고치면 쓸 수 없게 된다
        self.pending_batch, self._current, self._temp = None, None, 0
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
        self.undo_button, self.redo_button = _history_buttons(self, "저장 전 수정 · 새 행 · 삭제 표시, 또는 마지막 저장")
        self.add_button = button("＋ 행 추가")
        self.add_button.setToolTip("표 맨 아래에 새 행을 만듭니다. 설비 호기 · 설비군 · SW 버전을 적고 [저장]을 누릅니다.")
        self.delete_button = button("선택 행 삭제")
        self.delete_button.setAccessibleName("고른 설비의 현황표 행 삭제")
        self.delete_button.setToolTip("고른 줄의 설비를 '삭제 예정'으로 표시합니다. [저장]을 누르면 지워집니다.")
        self.save_button = button("저장", primary=True)
        self.save_button.setMinimumWidth(120)
        self.add_button.clicked.connect(self.add_row)
        self.delete_button.clicked.connect(self.delete_selected)
        self.save_button.clicked.connect(self.save)
        self.create_button = button("＋ 빈 현황표")
        self.create_button.setAccessibleName("빈 현황표 만들기")
        self.create_button.setToolTip("Site · 기준일을 골라 빈 현황표를 만듭니다. 그 Site의 설비는 그 현황표에서 [＋ 행 추가]로 넣습니다. "
                                      "목록에 없는 Site는 위치 탭에서 먼저 추가합니다.")
        self.create_button.clicked.connect(self.create_sheet)
        self.guide = None  # [?] 가이드 패널 (버튼은 설비현황 하위 탭 줄에 있다)
        row.addWidget(self.sheet_combo)
        row.addWidget(self.create_button)
        row.addStretch()
        for widget in (self.undo_button, self.redo_button, self.add_button, self.delete_button, self.save_button):
            row.addWidget(widget)
        card.body.addLayout(row)
        # 출처 줄 · 정렬 안내 문구는 화면에 두지 않는다 (11-12 요청: 설명 삭제). 값은 접근성 이름 · 툴팁에만 쓴다.
        self.source.setParent(self)
        self.source.hide()
        card.body.addWidget(self.notice)  # 오류 안내는 버튼 바로 아래에 (11-12: 표 아래에 있으면 보이지 않았다)
        sort_row = QHBoxLayout()
        self.sort_label = label("", 12, medium=True, color=PRIMARY)
        self.sort_label.setParent(self)
        self.sort_label.hide()
        self.sort_reset = button("기본 정렬로")
        self.sort_reset.setStyleSheet("QPushButton {padding:0 10px;min-height:26px;max-height:26px;font-size:12px;}")
        self.sort_reset.clicked.connect(lambda: self.sort_by(None))
        sort_row.addWidget(self.sort_reset)
        sort_row.addStretch()
        card.body.addLayout(sort_row)
        self.table = QTableWidget(0, len(SHEET_COLUMNS))
        self.table.setItemDelegate(_KeepColorsDelegate(self.table))  # 고른 줄도 필수 · 수정 · 삭제 색이 보이게
        self.table.setHorizontalHeaderLabels([name for _, name in SHEET_COLUMNS])
        self.table.setAccessibleName("현황표 · DB 값 기준")
        self.table.setStyleSheet(TABLE_STYLE)
        self.table.setMinimumHeight(560)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.setItemDelegateForColumn(COLUMN["line"], _ChoiceDelegate(
            self.table, self._line_choices, editable=True, placeholder="라인 이름을 적습니다 (처음 보는 라인은 저장 때 등록)"))
        self.table.setItemDelegateForColumn(COLUMN["category"], _ChoiceDelegate(self.table, lambda: CATEGORY_CHOICES))
        self.table.setItemDelegateForColumn(COLUMN["group"], _ChoiceDelegate(
            self.table, lambda: [g[1] for g in self.data.groups] if self.data else (), editable=True))
        self.table.setItemDelegateForColumn(COLUMN["model_name"], _ChoiceDelegate(
            self.table, lambda: [self._group_name(r.group_id) for r in self._selected_rows_data()][:1]))
        self.table.setItemDelegateForColumn(COLUMN["production_area"], _ChoiceDelegate(self.table, lambda: ("메모리", "파운드리", "")))
        self.table.itemChanged.connect(self._cell_changed)
        self.table.itemSelectionChanged.connect(self._sync)
        self.table.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.page.columns.attach(self.table, "sheet.db")
        card.body.addWidget(self.table)
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
        previous = self.sheet_combo.currentData()
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        for sheet in self.sheets:
            self.sheet_combo.addItem(sheet.label, _sheet_key(sheet.batch_id, sheet.base_date))
        if self.pending_batch is not None:  # 방금 만든 현황표(입력 작업 ID)를 고른다
            previous = next((_sheet_key(s.batch_id, s.base_date) for s in self.sheets if s.batch_id == self.pending_batch),
                            previous)
        self.pending_batch = None
        self.sheet_combo.setCurrentIndex(max(0, self.sheet_combo.findData(previous)))
        self.sheet_combo.blockSignals(False)
        self._current = self.sheet_combo.currentData()
        if not self.sheets:
            self.say(self.saved_note + "저장된 현황표가 없습니다. 데이터 가져오기에서 저장하거나 위치에서 빈 현황표를 만들어 주세요.")
            self.saved_note = ""
            self.source.setText("")
            self._show(None)
            return
        self._read(self._current)

    def open_batch(self, batch_id):
        """다른 탭에서 만든 현황표를 연다 (다음 읽기에서 고른다)."""
        self.pending_batch = batch_id
        self.loaded = False

    @property
    def pending(self):
        return bool(self.edits or self.new_rows or self.deletes)

    def _count(self):
        return len(self.edits) + len(self.new_rows) + len(self.deletes)

    def _sheet_changed(self, *_):
        key = self.sheet_combo.currentData()
        if key is None or key == self._current:
            return
        if not self._leave_ok():
            self.sheet_combo.blockSignals(True)
            self.sheet_combo.setCurrentIndex(max(0, self.sheet_combo.findData(self._current)))
            self.sheet_combo.blockSignals(False)
            return
        self._current = key
        self.steps, self.redo_steps = [], []
        self._read(key)

    def _leave_ok(self):
        """저장하지 않은 변경이 있으면 저장 · 버리기 · 취소를 묻는다. 계속 가도 되면 True."""
        if not self.pending:
            return True
        box = QMessageBox(QMessageBox.Icon.Question, "저장하지 않은 변경",
                          f"저장하지 않은 변경이 {self._count()}건 있습니다. 어떻게 할까요?", parent=self)
        save = box.addButton("저장", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("버리기", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is save:
            self.save()
            return False
        if box.clickedButton() is discard:
            self.edits, self.new_rows, self.deletes = {}, [], {}
            return True
        return False

    def _read(self, key):
        self.say("현황표를 읽고 있습니다…")
        batch_id, base_date = _parse_key(key)
        self.run(lambda: self.page.gateway.data_admin("sheet_rows", batch_id, base_date), self._rows_loaded)

    def _rows_loaded(self, data, error):
        if error is not None:
            self.say("현황표를 읽지 못했습니다. 다시 시도해 주세요.", error=True)
            return
        if self.data is None or (self.data.batch_id, self.data.base_date) != (data.batch_id, data.base_date):
            self.steps, self.sort, self.redo_steps = [], None, []
        self._show(data)
        sheet = next((s for s in self.sheets if s.batch_id == data.batch_id and s.base_date == data.base_date), None) \
            or next((s for s in self.sheets if s.batch_id == data.batch_id), None)
        when = _text(sheet.imported_at)[:16] if sheet and sheet.imported_at else ""
        if sheet is not None and sheet.origin == "직접 입력":  # 직접 만든 현황표는 원본 파일 · 시트가 없다
            self.source.setText(f"{data.site} · 기준일 {data.base_date or '모름'} · 데이터 관리에서 직접 만든 현황표"
                                + (f" · 만든 시각 {when}" if when else ""))
        else:
            self.source.setText(f"{data.site} · 기준일 {data.base_date or '모름'} · 원본 파일 {data.workbook or '이름 없음'} · 시트 "
                                f"{data.sheet}" + (f" · 가져온 시각 {when}" if when else ""))
        note, self.saved_note = self.saved_note, ""
        self.say(note + f"설비 {len({r.snapshot_id for r in data.rows})}대 · 바꾼 뒤 [저장]을 누릅니다.")

    def _show(self, data):
        self.data, self.edits, self.new_rows, self.deletes = data, {}, [], {}
        self._render()

    # 값 ------------------------------------------------------------------------------------
    @staticmethod
    def _key(row, field):
        """칸 수정이 기록되는 대상 (같은 설비의 여러 SW 줄이 같은 값을 공유한다)."""
        return {"equipment_code": ("code", row.equipment_id), "line": ("line", row.snapshot_id),
                "group": ("group", row.snapshot_id), "model_name": ("model", row.snapshot_id),
                "production_area": ("area", row.snapshot_id),
                "version_raw": ("version", row.version_id) if row.version_id else None}.get(field)

    @staticmethod
    def _original(row, field):
        return {"equipment_code": row.equipment_code, "line": row.line, "group": row.group_id,
                "model_name": row.model_name, "production_area": row.production_area, "version_raw": row.version_raw,
                "pm": row.pm_names}[field]

    def _group_name(self, gid):
        return next((name for g, name, _ in self.data.groups if g == gid), "") if self.data else ""

    def _group_category(self, gid):
        return next((category for g, _, category in self.data.groups if g == gid), "") if self.data else ""

    def _known_group(self, name):
        """이름(대소문자 무시)이 같은 등록 설비군 (ID, 이름, 대분류) 또는 None."""
        name = (name or "").strip().casefold()
        return next((g for g in self.data.groups if g[1].casefold() == name), None) if self.data and name else None

    def _value(self, row, field, *, edited=True):
        """화면에 보일 글자. ``edited``가 False면 저장 전 수정은 빼고 DB 값으로 (정렬용: 고치는 동안 행이 움직이지 않게)."""
        if field in ("category", "model_name"):
            gid = self.edits.get(("group", row.snapshot_id), row.group_id) if edited else row.group_id
            if field == "category":
                return self._group_category(gid)
            if edited and (("group", row.snapshot_id) in self.edits or ("model", row.snapshot_id) in self.edits):
                return self._group_name(gid)  # 모델명은 설비군을 따라간다
            return row.model_name or ""
        key = self._key(row, field)
        value = self.edits.get(key, self._original(row, field)) if key and edited else self._original(row, field)
        if field == "group":
            return value if isinstance(value, str) else self._group_name(value)
        if field == "production_area":
            return AREA_LABELS.get(value, "")
        return value or ""

    def _mismatched(self, row):
        """모델명이 다른 설비군을 가리키는 줄 (예전 편집으로 생긴 값). 모델명 칸에서 설비군 이름으로 맞출 수 있다.
        엑셀 원문 모델명('M-C4(DFB)' 등)이 같은 설비군을 가리키면 맞는 값이다 (서버 판단 ``model_mismatch``)."""
        return bool(getattr(row, "model_mismatch", False)) and ("model", row.snapshot_id) not in self.edits \
            and ("group", row.snapshot_id) not in self.edits

    def _editable(self, row, field):
        if field == "model_name":  # 모델명은 설비군을 따라간다. 설비군과 다른 줄만 설비군 이름으로 맞출 수 있다
            return row.snapshot_id not in self.deletes and self._mismatched(row)
        if row.snapshot_id in self.deletes or field in ("pm", "category"):
            return False  # 대분류는 설비군의 값
        if field == "group":
            return not row.has_pm
        if field == "version_raw":
            return row.version_id is not None and not row.has_pm
        return True

    # 표 그리기 --------------------------------------------------------------------------------
    def _sort_key(self, row):
        """DB 값으로 정렬한다. 저장 전 수정으로는 행이 움직이지 않고, 저장한 뒤 다시 읽을 때 자리를 찾는다."""
        if self.sort is not None:
            field = SHEET_COLUMNS[self.sort[0]][0]
            return [_natural(self._value(row, field, edited=False)), row.snapshot_id]
        return [_natural(self._value(row, f, edited=False)) for f in ("line", "group", "equipment_code")] + [row.snapshot_id]

    def _render(self):
        rows = sorted(self.data.rows, key=self._sort_key) if self.data else []
        if self.sort is not None and self.sort[1]:
            rows.reverse()
        self.view = [("row", r) for r in rows] + [("new", n) for n in self.new_rows]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.view))
        labels = [name + ((" ▼" if self.sort[1] else " ▲") if self.sort is not None and self.sort[0] == c else "")
                  for c, (_, name) in enumerate(SHEET_COLUMNS)]
        self.table.setHorizontalHeaderLabels(labels)
        self.table.setVerticalHeaderLabels([str(i) for i in range(1, len(rows) + 1)] + ["＋"] * len(self.new_rows))
        for r, (kind, entry) in enumerate(self.view):
            for c, (field, _) in enumerate(SHEET_COLUMNS):
                item = QTableWidgetItem()
                self.table.setItem(r, c, item)
                self._paint(r, c)
        self.table.setColumnHidden(COLUMN["pm"], not any(r.pm_names for r in rows))
        self.page.columns.fit(self.table)
        self.table.blockSignals(False)
        self._sync_sort()
        self._sync()

    def _paint(self, r, c):
        """칸 하나의 글자 · 색 · 안내를 지금 값과 저장 전 변경으로 칠한다."""
        kind, entry = self.view[r]
        field = SHEET_COLUMNS[c][0]
        item = self.table.item(r, c)
        blocked = self.table.signalsBlocked()
        self.table.blockSignals(True)
        item.setBackground(QBrush())
        item.setForeground(QBrush())
        if kind == "new":
            values = entry["values"]
            known = self._known_group(values.get("group"))
            value = values.get(field, "") if field not in ("pm", "category", "model_name") else ""
            if field == "category":
                value = known[2] if known else values.get("category", "")
            elif field == "model_name":
                value = known[1] if known else values.get("group", "")
            item.setText(AREA_LABELS.get(value, value) if field == "production_area" else value)
            fixed = field in ("pm", "model_name") or (field == "category" and (known is not None or not values.get("group")))
            needed = field in REQUIRED_NEW or (field == "category" and values.get("group") and known is None)
            if fixed:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip({"category": "대분류는 설비군에서 자동으로 정해집니다", "model_name": "모델명은 설비군과 같습니다"}
                                .get(field, ""))
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                item.setBackground(QBrush(NEW_ROW if needed and not value else SAVING))
                item.setToolTip(f"새 행 · {SHEET_COLUMNS[c][1]}" + (" (필수)" if needed else "") + " · 저장 전"
                                + (" · 새 설비군이라 POLY · OXIDE · KCTC 중에서 고릅니다" if field == "category" else "")
                                + (" · 새 라인이면 이 값으로 라인을 등록합니다" if field == "production_area" else "")
                                + (" · 라인의 메모리/파운드리로 자동 채움" if field == "production_area" and value
                                   and values.get("area_auto") and values.get("line") else "")
                                + (" · 설비군의 최신 버전으로 자동 채움 (실제 버전이 다르면 고칩니다)"
                                   if field == "version_raw" and values.get("version_auto") and value else ""))
        else:
            row = entry
            item.setText(self._value(row, field))
            editable = self._editable(row, field)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable if editable else item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            source = row.sources.get(field if field != "group" else "group_name")
            origin = f"원본 {source} · {row.source_values.get(field if field != 'group' else 'group_name') or '(빈칸)'}" \
                if source else "원본 셀 없음 (직접 입력 또는 가져오기 판정값)"
            key = self._key(row, field)
            if row.snapshot_id in self.deletes:
                item.setBackground(QBrush(DELETE_ROW))
                item.setForeground(QBrush(QColor(DANGER)))
                item.setToolTip("삭제 예정 · [저장]을 누르면 지워집니다. 되돌리기 아이콘(Ctrl+Z)으로 표시를 풉니다.")
            elif field == "model_name" and self._mismatched(row):
                item.setForeground(QBrush(QColor(DANGER)))
                item.setToolTip(f"모델명({row.model_name})이 설비군({self._group_name(row.group_id)})과 다릅니다 · "
                                "모델명 칸을 두 번 눌러 설비군 이름으로 맞추거나, 설비군 칸에서 맞는 설비군을 고릅니다")
            elif field in ("category", "model_name"):
                if ("group", row.snapshot_id) in self.edits or ("model", row.snapshot_id) in self.edits:
                    item.setBackground(QBrush(SAVING))
                item.setToolTip("대분류는 설비군에서 자동으로 정해집니다 (설비 탭에서 관리)" if field == "category"
                                else "모델명은 설비군을 따라갑니다 · 설비군 칸을 고칩니다")
            elif key is not None and key in self.edits:
                item.setBackground(QBrush(SAVING))
                before = self._original(row, field)
                before = self._group_name(before) if field == "group" else AREA_LABELS.get(before, before) \
                    if field == "production_area" else before
                item.setToolTip(f"{before or '(빈칸)'} → {item.text() or '(빈칸)'} · 저장 전 · {origin}")
            else:
                note = " · PM 설비는 버전 관리에서 고칩니다" if row.has_pm and field in ("group", "version_raw") else ""
                item.setToolTip(origin + note)
        self.table.blockSignals(blocked)

    # 정렬 ------------------------------------------------------------------------------------
    def _header_clicked(self, column):
        """열 제목을 누르면 그 열로 오름차순 → 내림차순 → 기본 정렬 순으로 바뀐다."""
        if self.data is None:
            return
        if self.sort is None or self.sort[0] != column:
            self.sort_by((column, False))
        elif not self.sort[1]:
            self.sort_by((column, True))
        else:
            self.sort_by(None)

    def sort_by(self, sort):
        self.sort = sort
        self._render()

    def _sync_sort(self):
        if self.sort is None:
            self.sort_label.setText("정렬: Line → 설비군 → 설비 호기 (기본) · 열 제목을 누르면 그 열로 정렬합니다")
        else:
            column, descending = self.sort
            self.sort_label.setText(f"정렬: {SHEET_COLUMNS[column][1]} {'내림차순' if descending else '오름차순'} · 한 번 더 누르면 바뀝니다")
        self.sort_reset.setVisible(self.sort is not None)

    # 칸 수정 ----------------------------------------------------------------------------------
    def _cell_changed(self, item):
        r, c = item.row(), item.column()
        if not (0 <= r < len(self.view)):
            return
        kind, entry = self.view[r]
        field, value = SHEET_COLUMNS[c][0], item.text().strip()
        if kind == "new":
            stored = {"메모리": "MEMORY", "파운드리": "FOUNDRY"}.get(value, "") if field == "production_area" else value
            values = entry["values"]
            if stored != values.get(field, ""):
                before = self._state()
                values[field] = stored or ""
                if field == "version_raw":
                    values["version_auto"] = False  # 직접 적은 버전은 설비군을 바꿔도 덮어쓰지 않는다
                elif field == "production_area":
                    values["area_auto"] = False     # 직접 고른 메모리/파운드리는 라인을 바꿔도 덮어쓰지 않는다
                elif field == "line":
                    self._fill_area(values)
                if field in ("group", "production_area", "line"):
                    self._fill_version(values)
                self.steps.append(("state", before))
            for other in range(len(SHEET_COLUMNS)):  # 대분류 · 모델명 · SW 버전 · 필수 표시가 따라 바뀐다
                self._paint(r, other)
            self._sync()
            return
        row = entry
        key = self._key(row, field)
        if key is None:
            return
        if field == "group":
            gid = next((g for g, name, _ in self.data.groups if name.casefold() == value.casefold()), None)
            if gid is None:
                self.say(f"설비군 '{value}'이(가) 없습니다. 설비 탭에서 먼저 추가하거나 목록에서 골라 주세요.", error=True)
                self._paint(r, c)
                return
            before = self._state()
            self._set_edit(key, gid, row.group_id)
            name = self._group_name(gid)
            # 설비군이 그대로면 모델명만 맞춘다 (모델명이 설비군과 달랐던 줄). 바뀌면 저장 때 서버가 모델명을 맞춘다
            self._set_edit(("model", row.snapshot_id), name if gid == row.group_id else row.model_name, row.model_name)
            if self._state() != before:
                self.steps.append(("state", before))
            self._repaint_all()
            return
        elif field == "model_name":  # 설비군과 다른 줄만: 설비군 이름으로 맞춘다
            name = self._group_name(self.edits.get(("group", row.snapshot_id), row.group_id))
            if value.casefold() != name.casefold():
                self.say(f"모델명은 설비군과 같아야 합니다 · '{name}'(으)로 맞추거나 설비군 칸을 고쳐 주세요.", error=True)
                self._paint(r, c)
                return
            value = name
        elif field == "production_area":
            value = {"메모리": "MEMORY", "파운드리": "FOUNDRY"}.get(value)
        elif field in ("equipment_code", "version_raw") and not value:
            self.say(f"{SHEET_COLUMNS[c][1]}은(는) 비울 수 없습니다.", error=True)
            self._paint(r, c)
            return
        previous = self.edits.get(key, self._original(row, field))
        if value == previous:
            self._paint(r, c)
            return
        self.steps.append(("cell", key, previous, field, row))
        self._set_edit(key, value, self._original(row, field))
        self._repaint_all()  # 같은 설비의 다른 SW 줄도 같은 값을 보인다

    def _fill_area(self, values):
        """새 행 메모리/파운드리를 고른 라인의 값으로 채운다 (그 라인을 알 때만, 직접 고른 값은 그대로)."""
        if not values.get("area_auto", True) and values.get("production_area"):
            return
        known = (getattr(self.data, "line_areas", {}) or {}).get((values.get("line") or "").strip())
        if known:
            values["production_area"], values["area_auto"] = known, True
        elif values.get("area_auto", True):
            values["production_area"] = self.data.default_area or ""  # 모르는 라인이면 현황표 기본값(없으면 빈칸 → 필수)

    def _fill_version(self, values):
        """새 행 SW 버전을 설비군 · 메모리/파운드리 계열의 최신 릴리즈 버전으로 채운다 (11-12).
        메모리/파운드리가 정해지기 전에는 채우지 않고, 직접 적은 버전은 그대로 둔다."""
        if not values.get("version_auto", True) and values.get("version_raw"):
            return
        known = self._known_group(values.get("group"))
        area = values.get("production_area")
        latest = (getattr(self.data, "latest_versions", {}) or {}).get((known[0], area)) if known and area else None
        values["version_raw"], values["version_auto"] = latest or "", True

    def _set_edit(self, key, value, original):
        if value == original:
            self.edits.pop(key, None)
        else:
            self.edits[key] = value

    def _repaint_all(self):
        for r in range(min(self.table.rowCount(), len(self.view))):
            for c in range(len(SHEET_COLUMNS)):
                if self.table.item(r, c) is not None:
                    self._paint(r, c)
        self._sync()

    # 행 추가 · 삭제 표시 --------------------------------------------------------------------------
    def add_row(self):
        if self.data is None or not self.data.site or self.page.saving:
            return False
        self._temp += 1
        self.new_rows.append({"temp": self._temp, "values": {
            "equipment_code": "", "line": "", "group": "", "category": "",
            "production_area": self.data.default_area or "", "area_auto": True, "version_raw": "", "version_auto": True}})
        self.steps.append(("new_row", self._temp))
        self._render()
        self.table.scrollToBottom()
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)
        self.say("새 행을 만들었습니다 · 설비 호기 · 설비군 · SW 버전(필수)을 적은 뒤 [저장]을 누릅니다.")
        return True

    def _selected_rows_data(self):
        """지금 고른(편집 중인) 칸의 설비 줄."""
        r = self.table.currentRow()
        return [self.view[r][1]] if 0 <= r < len(self.view) and self.view[r][0] == "row" else []

    def _selected_lines(self):
        rows = {i.row() for i in self.table.selectedItems()}
        return [self.view[r] for r in sorted(rows) if r < len(self.view)]

    def selected_rows(self):
        """고른 줄의 설비 현황 {snapshot_id: 호기} (이미 삭제 예정인 것은 뺀다)."""
        return {entry.snapshot_id: entry.equipment_code for kind, entry in self._selected_lines()
                if kind == "row" and entry.snapshot_id not in self.deletes}

    def delete_selected(self):
        chosen = self.selected_rows()
        news = [entry for kind, entry in self._selected_lines() if kind == "new"]
        if (not chosen and not news) or self.page.saving:
            return False
        if chosen:
            self.deletes.update(chosen)
            self.steps.append(("mark", tuple(chosen)))
        for new in news:
            self.new_rows.remove(new)
            self.steps = [s for s in self.steps if not (s[0] in ("new_row", "new") and s[1] == new["temp"])]
        self._render()
        self.say(f"설비 {len(chosen)}대를 삭제 예정으로 표시했습니다 · [저장]을 누르면 지워집니다." if chosen else "새 행을 지웠습니다.")
        return True

    # 저장 ------------------------------------------------------------------------------------
    def _edit_changes(self, edits):
        """칸 수정(설비군 제외)을 관리 저장 변경으로 바꾼다."""
        grouped = defaultdict(dict)
        for (kind, ident), value in edits.items():
            if kind == "code":
                grouped[("equipment", (("equipment_id", ident),))]["equipment_code"] = value
            elif kind in ("line", "model", "area"):
                name = {"line": "line", "model": "model_name", "area": "production_area"}[kind]
                grouped[("equipment_snapshot", (("snapshot_id", ident),))][name] = value or None
            elif kind == "version":
                grouped[("equipment_version", (("equipment_version_id", ident),))].update(
                    {"version_raw": value, "release_version_id": None})
        return [(table, dict(key), changes) for (table, key), changes in grouped.items()]

    def save(self):
        """수정 · 설비군 변경 · 새 행 · 삭제 표시를 한꺼번에 저장한다. 없는 설비군과 행 삭제만 한 번 묻는다."""
        if not self.pending or self.data is None or self.page.saving:
            return False
        incomplete = [n for n in self.new_rows if not all(n["values"].get(f) for f in REQUIRED_NEW)]
        if incomplete:
            self.say(f"새 행 {len(incomplete)}개의 필수 칸(설비 호기 · 설비군 · 메모리/파운드리 · SW 버전)을 채운 뒤 저장해 주세요.",
                     error=True)
            return False
        if not self._other_site_lines_ok():
            return False
        new_lines = self._new_line_areas()
        if new_lines is None:
            return False
        known = {name.casefold() for _, name, _ in self.data.groups}
        creates = []
        for name in dict.fromkeys(n["values"]["group"] for n in self.new_rows):
            if name.casefold() not in known:
                categories = {n["values"].get("category") for n in self.new_rows if n["values"]["group"] == name} - {""}
                if len(categories) != 1:
                    self.say(f"새 설비군 '{name}'의 대분류(POLY · OXIDE · KCTC)를 새 행의 대분류 칸에서 "
                             + ("하나로 맞춰 주세요." if categories else "골라 주세요."), error=True)
                    return False
                category = next(iter(categories))
                if QMessageBox.question(self, "새 설비군", f"설비군 '{name}'({category})이(가) 없습니다. 새 설비군으로 등록할까요?") \
                        != QMessageBox.StandardButton.Yes:
                    self.say(f"설비군 '{name}'을(를) 등록하지 않았습니다. 새 행의 설비군 칸을 고쳐 주세요.", error=True)
                    return False
                creates.append((name, category))
        if self.deletes and QMessageBox.question(
                self, "현황표 행 삭제", f"설비 {len(self.deletes)}대의 행({', '.join(list(self.deletes.values())[:6])}"
                f"{' …' if len(self.deletes) > 6 else ''})을 지우고 저장할까요?\n되돌리기 아이콘(Ctrl+Z)으로 저장 전으로 돌릴 수 있습니다.") \
                != QMessageBox.StandardButton.Yes:
            return False
        equipment_of = {r.equipment_id: r.snapshot_id for r in self.data.rows}
        live = {k: v for k, v in self.edits.items() if not (
            (k[0] in ("line", "model", "area", "group") and k[1] in self.deletes)
            or (k[0] == "version" and next((r.snapshot_id for r in self.data.rows if r.version_id == k[1]), None) in self.deletes)
            or (k[0] == "code" and equipment_of.get(k[1]) in self.deletes))}
        regroup = [(k[1], v) for k, v in live.items() if k[0] == "group"]
        edits = {k: v for k, v in live.items() if k[0] != "group"}
        olds = {k: self._original_of(k) for k in edits}
        adds = [({"equipment_code": n["values"]["equipment_code"], "line": n["values"]["line"],
                  "group_name": n["values"]["group"], "model_name": "",
                  "production_area": n["values"]["production_area"] or None, "version_raw": n["values"]["version_raw"]}, None)
                for n in self.new_rows]
        deletes = tuple(self.deletes)
        batch_id, base_date, gateway = self.data.batch_id, self.data.base_date, self.page.gateway
        save_edits = self.page.edit_work(self._edit_changes(edits)) if edits else None

        site, registers = self.data.site, [(line, fab) for line, fab in new_lines.items() if fab]

        def work():
            for line, fab in registers:  # 새 라인을 먼저 등록해야 새 행의 메모리/파운드리가 그 값으로 정해진다
                gateway.data_admin("add_line", site, line, fab)
            for name, category in creates:
                gateway.data_admin("create_group", name, category, None)
            saved = save_edits() if save_edits else None  # 검토 지문 충돌이 가장 잦으므로 칸 수정을 먼저 저장한다
            result = gateway.data_admin("apply_rows", batch_id, adds, deletes, regroup, base_date=base_date) \
                if (adds or deletes or regroup) \
                else {"added": [], "journal": None, "regroup": []}
            return SimpleNamespace(records=getattr(saved, "records", ()), refresh=getattr(saved, "refresh", None), rows=result)

        counts = (len(edits) + len(regroup), len(adds), len(deletes))
        registered = creates + [(f"라인 {line}", AREA_LABELS[fab]) for line, fab in registers]  # 저장 안내용
        rows = {r.snapshot_id: r for r in self.data.rows}
        replay = {"batch": (batch_id, base_date), "edits": dict(live), "new_rows": deepcopy(self.new_rows),
                  "deletes": {rows[sid].equipment_id: code for sid, code in self.deletes.items() if sid in rows}}
        return self.page.change(work, self, message="저장하고 있습니다…",
                                done=lambda result: self._saved_all(result, olds, counts, registered, replay))

    def other_site_lines(self):
        """저장할 Line 값 중 이 Site에는 없고 다른 Site에만 있는 라인 {라인: (Site, …)} — 다른 현황표에 넣는 실수 안내."""
        values = [n["values"].get("line", "") for n in self.new_rows]
        values += [v for (kind, sid), v in self.edits.items() if kind == "line" and sid not in self.deletes]
        own = {line.casefold() for line in self.data.lines}
        elsewhere = getattr(self.data, "elsewhere", {}) or {}
        return {v: elsewhere[v.casefold()] for v in dict.fromkeys(v.strip() for v in values if v and v.strip())
                if v.casefold() not in own and v.casefold() in elsewhere}

    def _line_choices(self):
        """Line 칸 목록: 이 Site의 라인 + 이번에 적은(아직 저장 안 한) 라인."""
        if self.data is None:
            return ()
        typed = [n["values"].get("line", "") for n in self.new_rows]
        typed += [v for (kind, _), v in self.edits.items() if kind == "line"]
        return tuple(dict.fromkeys([*self.data.lines, *sorted({v.strip() for v in typed if v and v.strip()}, key=_natural)]))

    def _is_new_line(self, line):
        line = (line or "").strip().casefold()
        elsewhere = getattr(self.data, "elsewhere", {}) or {}
        return bool(line) and self.data is not None and line not in {v.casefold() for v in self.data.lines} \
            and line not in elsewhere

    def unregistered_lines(self):
        """저장할 Line 값 중 이 Site에 없고 다른 Site에도 없는 처음 보는 라인 (저장 때 등록할지 묻는다)."""
        values = [n["values"].get("line", "") for n in self.new_rows]
        values += [v for (kind, sid), v in self.edits.items() if kind == "line" and sid not in self.deletes]
        own = {line.casefold() for line in self.data.lines}
        elsewhere = getattr(self.data, "elsewhere", {}) or {}
        return [v for v in dict.fromkeys(v.strip() for v in values if v and v.strip())
                if v.casefold() not in own and v.casefold() not in elsewhere]

    def _new_line_areas(self):
        """처음 보는 라인마다 그 줄의 메모리/파운드리로 새 라인을 등록한다 (11-12: 묻는 창 대신).

        return
        - {라인: MEMORY · FOUNDRY}. 메모리/파운드리를 모르거나 줄마다 다르면 안내하고 None.
        """
        result = {}
        for line in self.unregistered_lines():
            areas = {n["values"].get("production_area") for n in self.new_rows if n["values"].get("line", "").strip() == line}
            for (kind, sid), value in self.edits.items():
                if kind == "line" and value.strip() == line and sid not in self.deletes:
                    row = next(r for r in self.data.rows if r.snapshot_id == sid)
                    areas.add(self.edits.get(("area", sid), row.production_area))
            areas -= {None, ""}
            if len(areas) != 1:
                self.say(f"새 라인 '{line}'의 메모리/파운드리를 그 줄의 메모리/파운드리 칸에서 "
                         + ("하나로 맞춰 주세요." if areas else "골라 주세요 (저장할 때 새 라인으로 함께 등록합니다)."), error=True)
                return None
            result[line] = next(iter(areas))
        return result

    def _other_site_lines_ok(self):
        found = self.other_site_lines()
        if not found:
            return True
        site = self.data.site
        lines = "\n".join(f"· {line}: {', '.join(sites)} 라인" for line, sites in found.items())
        targets = ", ".join(dict.fromkeys(s for sites in found.values() for s in sites))
        if QMessageBox.question(self, "다른 Site의 라인", f"다음 라인은 이 Site({site})가 아니라 다른 Site에 있는 라인입니다.\n{lines}\n\n"
                                f"이 현황표는 {site}라 그대로 저장하면 {site} 설비로 저장됩니다. 그대로 저장할까요?\n"
                                f"{targets}에 넣으려면 [아니요]를 누르고 [＋ 빈 현황표]로 그 Site 현황표를 만들어 넣어 주세요.") \
                == QMessageBox.StandardButton.Yes:
            return True
        self.say(f"저장하지 않았습니다 · {targets}에 넣을 설비는 [＋ 빈 현황표]로 그 Site 현황표를 만들어 넣어 주세요.", error=True)
        return False

    def _original_of(self, key):
        for row in self.data.rows:
            for field, _ in SHEET_COLUMNS:
                if self._key(row, field) == key:
                    return self._original(row, field)
        return None

    def _saved_all(self, result, olds, counts, creates, replay=None):
        self.steps = [s for s in self.steps if s[0] == "saved"]
        self.steps.append(("saved", {"olds": olds, "added": list(result.rows["added"]), "journal": result.rows["journal"],
                                     "regroup": list(result.rows.get("regroup") or []), "replay": replay}))
        self.redo_steps = []
        self.edits, self.new_rows, self.deletes = {}, [], {}
        parts = [f"{n}{unit}" for n, unit in zip(counts, ("칸 수정", "행 추가", "행 삭제")) if n]
        self.saved_note = ("저장했습니다 · " + " · ".join(parts)
                           + (f" (새로 등록: {', '.join(m for m, _ in creates)})" if creates else "") + ". ")
        self.reload()

    # 되돌리기 ----------------------------------------------------------------------------------
    def undo(self):
        """바로 앞 단계 하나를 되돌린다."""
        if not self.steps or self.page.saving or self.data is None:
            return False
        kind, *rest = self.steps[-1]
        if kind != "saved":
            step, before = self.steps.pop(), self._state()
            if kind == "state":
                (state,) = rest
                self._restore(state)
                self.say("다시 실행한 변경을 되돌렸습니다.")
            elif kind == "cell":
                key, previous, field, row = rest
                self._set_edit(key, previous, self._original(row, field))
                self.say("수정 한 칸을 되돌렸습니다.")
            elif kind == "new":
                temp, field, previous = rest
                new = next((n for n in self.new_rows if n["temp"] == temp), None)
                if new is not None:
                    new["values"][field] = previous
            elif kind == "new_row":
                (temp,) = rest
                self.new_rows = [n for n in self.new_rows if n["temp"] != temp]
                self.steps = [s for s in self.steps if not (s[0] == "new" and s[1] == temp)]
                self.say("새 행을 지웠습니다.")
            elif kind == "mark":
                (sids,) = rest
                for sid in sids:
                    self.deletes.pop(sid, None)
                self.say("삭제 표시를 풀었습니다.")
            self.redo_steps.append(("pending", step, before, self._state()))
            self._render()
            return True
        if self.pending:
            self.say("저장하지 않은 변경을 먼저 저장하거나 되돌려 주세요.", error=True)
            return False
        (info,) = rest
        batch_id, gateway = self.data.batch_id, self.page.gateway
        save_back = self.page.edit_work(self._edit_changes(info["olds"])) if info["olds"] else None

        def work():
            if info["added"] or info["journal"] is not None or info["regroup"]:
                gateway.data_admin("undo_rows", batch_id, info["added"], info["journal"], info["regroup"])
            saved = save_back() if save_back else None
            return SimpleNamespace(records=getattr(saved, "records", ()), refresh=getattr(saved, "refresh", None))

        return self.page.change(work, self, message="마지막 저장을 되돌리고 있습니다…", done=lambda _: self._undone())

    def _undone(self):
        step = self.steps.pop()
        if step[1].get("replay"):
            self.redo_steps.append(("saved", step[1]["replay"]))
        self.saved_note = "마지막 저장을 되돌렸습니다 (수정 · 추가 · 삭제 모두). "
        self.reload()

    # 다시 실행 ---------------------------------------------------------------------------------
    def _state(self):
        """저장 전 변경 전체 (다시 실행 · 되돌리기용 복사본)."""
        return deepcopy((self.edits, self.new_rows, self.deletes, self._temp))

    def _restore(self, state):
        self.edits, self.new_rows, self.deletes, self._temp = deepcopy(state)

    def redo(self):
        """되돌린 단계 하나를 다시 적용한다. 되돌린 저장은 같은 변경으로 다시 저장한다 (지운 행은 설비로 다시 찾는다)."""
        if not self.redo_steps or self.page.saving or self.data is None or self.request.busy:
            return False
        kind, *rest = self.redo_steps[-1]
        if kind == "pending":
            step, before, after = rest
            if self._state() != after:
                self.redo_steps = []
                self.say("되돌린 뒤에 다른 내용을 고쳐서 다시 실행할 수 없습니다.", error=True)
                self._sync()
                return False
            self.redo_steps.pop()
            self._restore(before)
            self.steps.append(step)
            self._render()
            self.say("다시 실행했습니다.")
            return True
        (replay,) = rest
        if self.pending:
            self.say("저장하지 않은 변경을 먼저 저장하거나 되돌린 뒤 다시 실행해 주세요.", error=True)
            return False
        if replay["batch"] != (self.data.batch_id, self.data.base_date):
            self.redo_steps = []
            self.say("다른 현황표를 열어서 되돌린 저장을 다시 실행할 수 없습니다.", error=True)
            self._sync()
            return False
        self.redo_steps.pop()
        keys = {self._key(row, field) for row in self.data.rows for field, _ in SHEET_COLUMNS} - {None}
        snapshot_of = {row.equipment_id: row.snapshot_id for row in self.data.rows}
        before = self._state()
        self.edits = {k: v for k, v in replay["edits"].items() if k in keys}
        self.deletes = {snapshot_of[eid]: code for eid, code in replay["deletes"].items() if eid in snapshot_of}
        self.new_rows = []
        for new in replay["new_rows"]:
            self._temp += 1
            self.new_rows.append({**deepcopy(new), "temp": self._temp})
        self.steps.append(("state", before))
        self._render()
        self.say("되돌린 저장을 다시 적용하고 있습니다…")
        if not self.save():
            self.say("되돌린 저장을 다시 불러왔습니다 · 확인한 뒤 [저장]을 누르면 반영됩니다.")
        return True

    def _sync(self):
        busy = self.page.saving
        count = self._count()
        self.save_button.setText(f"저장 ({count}건)" if count else "저장")
        self.save_button.setEnabled(bool(count) and not busy)
        self.undo_button.setEnabled(bool(self.steps) and not busy)
        self.redo_button.setEnabled(bool(self.redo_steps) and not busy)
        self.add_button.setEnabled(self.data is not None and bool(self.data.site) and not busy)
        self.delete_button.setEnabled(bool(self.selected_rows() or any(k == "new" for k, _ in self._selected_lines()))
                                      and not busy)
        self.sheet_combo.setEnabled(not busy)
        self.create_button.setEnabled(self.page.gateway is not None and not busy)

    # 가이드 --------------------------------------------------------------------------------
    def attach_guide(self, parent):
        """[?]로 여는 '새 위치에 설비 넣기' 가이드. 변경 이력 세부 정보와 같은 패널(SideDrawer)을 쓴다."""
        from setron.ui.drawer import SideDrawer

        self.guide = SideDrawer(parent, object_name="sheetGuide", accessible_name="새 위치에 설비 넣기 가이드", panel_width=480)
        layout = self.guide.panel_layout
        layout.addLayout(self.guide.header)
        layout.addWidget(label("새 위치에 설비 넣기", 20, medium=True, wrap=True))
        layout.addWidget(label("새 Site · 라인 · 설비군도 현황표 탭 안에서 함께 등록됩니다. 단계의 버튼을 누르면 그 화면이나 동작으로 갑니다.",
                               13, color=SECONDARY, wrap=True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        steps = QVBoxLayout(host)
        steps.setContentsMargins(0, 0, 6, 0)
        steps.setSpacing(10)
        self.guide_buttons = {}
        for number, (title, when, text, action, caption) in enumerate(GUIDE_STEPS, 1):
            steps.addWidget(self._guide_step(number, title, when, text, action, caption))
        steps.addWidget(label(GUIDE_NOTE, 12, color=SECONDARY, wrap=True))
        warn = label("다른 Site의 현황표에 넣으면 그 Site 설비로 저장됩니다. 다른 Site에만 있는 라인을 적으면 [저장] 전에 알려 줍니다.",
                     12, color=DANGER, wrap=True)
        warn.setStyleSheet(f"color:{DANGER};background:#fdecef;border-radius:8px;padding:10px;")
        steps.addWidget(warn)
        steps.addStretch()
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        # 화면 전체에 거는 '줄바꿈 라벨 폭 무시'가 스크롤 안 라벨 높이를 한 줄로 자르지 않게 기본 크기 정책으로 둔다
        self._guide_labels = [w for w in self.guide.panel.findChildren(QLabel) if w.wordWrap()]
        for widget in self._guide_labels:
            widget.installEventFilter(self)  # 스크롤 안 줄바꿈 라벨은 폭이 바뀔 때 높이를 직접 맞춘다

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize and watched in getattr(self, "_guide_labels", ()):
            height = watched.heightForWidth(watched.width())
            if height > 0 and watched.minimumHeight() != height:
                watched.setMinimumHeight(height)
        return super().eventFilter(watched, event)

    def _guide_step(self, number, title, when, text, action, caption):
        box = QFrame()
        box.setObjectName("guideStep")
        box.setStyleSheet(f"QFrame#guideStep {{background:white;border:1px solid {LINE};border-radius:10px;}}")
        body = QVBoxLayout(box)
        body.setContentsMargins(14, 12, 14, 12)
        body.setSpacing(6)
        head = QHBoxLayout()
        badge = QLabel(str(number))
        badge.setFixedSize(24, 24)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(f"background:{PRIMARY};color:white;border-radius:12px;font-size:12px;font-weight:600;")
        head.addWidget(badge)
        head.addWidget(label(title, 15, medium=True))
        if when:
            head.addWidget(label(when, 12, color=SECONDARY))
        head.addStretch()
        body.addLayout(head)
        body.addWidget(label(text, 13, wrap=True))
        if action == "add":
            fields = QLabel(GUIDE_FIELDS)
            fields.setTextFormat(Qt.TextFormat.RichText)
            fields.setWordWrap(True)  # 표가 패널 폭을 넘지 않게 칸 안에서 줄을 바꾼다
            set_widget_font(fields, font(12))
            body.addWidget(fields)
        if caption:
            control = button(caption)
            control.setStyleSheet("QPushButton {padding:0 12px;min-height:30px;max-height:30px;font-size:13px;}")
            control.clicked.connect(lambda _=False, key=action: self.guide_go(key))
            self.guide_buttons[action] = control
            body.addWidget(control, 0, Qt.AlignmentFlag.AlignLeft)
        return box

    def show_guide(self):
        if self.guide is None:
            return False
        for widget in self._guide_labels:
            widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.guide.show_panel()
        return True

    def guide_go(self, key):
        """가이드 단계의 버튼: 가이드를 닫고 그 화면(이나 동작)으로 간다."""
        if self.guide is not None:
            self.guide.hide_panel(animated=False)
        if key in ("location", "equipment"):
            self.page.select_status(2 if key == "location" else 1)
            return True
        self.page.select_status(0)
        if key == "create":
            return self.create_sheet()
        if key == "add":
            if self.data is None or not self.data.site:
                self.say("먼저 그 Site의 현황표를 고르거나 [＋ 빈 현황표]로 만들어 주세요.", error=True)
                return False
            return self.add_row()
        return False

    # 빈 현황표 --------------------------------------------------------------------------------
    def create_sheet(self):
        """Site · 기준일을 골라 빈 현황표를 만들고 연다. Site 목록은 등록 Site와 현황표가 있는 Site."""
        if self.page.gateway is None or self.page.saving or self.request.busy or not self._leave_ok():
            return False
        return self.run(lambda: self.page.gateway.data_admin("sites"), self._sites_for_create)

    def _sites_for_create(self, registered, error):
        if error is not None:
            self.say("Site 목록을 읽지 못했습니다. DB 연결을 확인해 주세요.", error=True)
            return
        sites = sorted({*registered, *(s.site for s in self.sheets if s.site)}, key=_natural)
        dialog = CreateSheetDialog(self, sites, self.data.site if self.data is not None else None)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        site, base_date, area = dialog.values()
        same = next((s for s in sites if s.casefold() == site.casefold()), None)
        if same is None and QMessageBox.question(
                self, "새 Site", f"'{site}'은(는) 아직 없는 Site입니다. 새 Site로 등록하고 빈 현황표를 만들까요?") \
                != QMessageBox.StandardButton.Yes:
            return
        site, gateway = same or site, self.page.gateway

        def work():
            if same is None:  # 새 Site면 함께 등록한다 (위치 탭에 가지 않아도 된다, 11-12)
                gateway.data_admin("add_site", site)
            return gateway.data_admin("create_sheet", site, base_date, area)

        self.page.change(work, self, message="빈 현황표를 만들고 있습니다…",
                         done=lambda batch_id: self.page.open_sheet(
                             batch_id, (f"새 Site '{site}'을(를) 등록하고 " if same is None else f"{site} · ")
                             + f"{base_date} 빈 현황표를 만들었습니다 · [＋ 행 추가]로 설비를 넣습니다. "))


class CreateSheetDialog(QDialog):
    """빈 현황표 만들기: Site · 기준일."""

    def __init__(self, parent, sites, site=None):
        super().__init__(parent)
        self.setWindowTitle("빈 현황표 만들기")
        form = QFormLayout(self)
        self.site, self.base_date = ComboBox(), _date_picker(date.today())  # 리플레이와 같은 달력 (11-12)
        self.site.setEditable(True)  # 목록에 없는 이름을 적으면 새 Site로 등록한다
        for value in sites:
            self.site.addItem(value, value)
        self.site.setCurrentIndex(max(0, self.site.findData(site)))
        self.site.lineEdit().setPlaceholderText("Site를 고르거나 새 이름을 적습니다")
        self.site.setMinimumWidth(260)
        self.base_date.setAccessibleName("기준일")
        self.error = label("", 12, color=DANGER, wrap=True)
        area_row, self.areas = QHBoxLayout(), QButtonGroup(self)
        for key, text in (("MEMORY", "메모리"), ("FOUNDRY", "파운드리")):  # 필수: 새 행 · 새 라인의 기본값 (11-12)
            choice = QRadioButton(text)
            choice.setProperty("area", key)
            self.areas.addButton(choice)
            area_row.addWidget(choice)
        area_row.addStretch()
        form.addRow("Site", self.site)
        form.addRow("기준일", self.base_date)
        form.addRow("메모리/파운드리", area_row)
        form.addRow(label("목록에 없는 Site 이름을 적으면 새 Site로 함께 등록합니다. 메모리/파운드리는 이 현황표의 새 행과 처음 보는 "
                          "라인에 기본으로 쓰입니다(줄마다 바꿀 수 있음). 같은 Site · 기준일 현황표가 있으면 만들지 않습니다.",
                          12, color=SECONDARY, wrap=True))
        form.addRow(self.error)
        actions = QHBoxLayout()
        cancel, ok = button("취소"), button("만들기", primary=True)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._accept)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(ok)
        form.addRow(actions)

    def _accept(self):
        if not self.site.currentText().strip():
            self.error.setText("Site를 고르거나 새 이름을 적어 주세요.")
            return
        if self.areas.checkedButton() is None:
            self.error.setText("메모리/파운드리를 골라 주세요.")
            return
        self.accept()

    def values(self):
        area = self.areas.checkedButton().property("area") if self.areas.checkedButton() else None
        return self.site.currentText().strip(), self.base_date.date().toPython(), area


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


class _CategoryDelegate(_ChoiceDelegate):
    """대분류 칸: 묶음 첫 줄에만 글자가 보여도, 고칠 때는 그 줄의 실제 대분류(툴팁에 둔 값)에서 시작한다."""

    def setEditorData(self, editor, index):
        text = index.data(Qt.ItemDataRole.ToolTipRole) or index.data(Qt.ItemDataRole.EditRole) or ""
        found = editor.findText(text)
        if found >= 0:
            editor.setCurrentIndex(found)


class EquipmentPanel(_Panel):
    """왼쪽: 설비군 (분류는 묶음 첫 줄에만, 개조형도 한 줄씩) · 오른쪽: 설비 (모델 · Site · Line).

    11-12: 표에서 바로 고친다. 대분류 칸(기본형만, 개조형은 따라감)과 설비군 이름 칸(사용자가 추가한 설비군만)을 두 번
    눌러 고치고 [저장]으로 한꺼번에 반영한다. '독립 설비군으로 보기'는 없앴다.
    """

    CATEGORY_ORDER = {"POLY": 0, "OXIDE": 1, "KCTC": 2, "HPA": 3}

    def __init__(self, page):
        super().__init__(page)
        self.groups, self.equipment, self.ordered = (), (), ()
        self.pending, self.steps, self.redo_steps = {}, [], []  # pending: 설비군 ID → {"name", "category"}
        self._rendering = False
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
        actions = QHBoxLayout()
        self.delete_button = button("삭제")
        self.delete_button.clicked.connect(self.delete_group)
        self.undo_button, self.redo_button = _history_buttons(self, "저장 전 설비군 이름 · 대분류 수정")
        self.save_button = button("저장", primary=True)
        self.save_button.setMinimumWidth(96)
        self.save_button.clicked.connect(self.save)
        actions.addWidget(self.delete_button)
        actions.addStretch()
        for widget in (self.undo_button, self.redo_button, self.save_button):
            actions.addWidget(widget)
        left.body.addLayout(actions)
        left.body.addWidget(self.notice)
        self.group_table = _table(("분류", "설비군", "상위 설비군", "설비"), "설비군 목록")
        self.group_table.setItemDelegate(_KeepColorsDelegate(self.group_table))
        self.group_table.setItemDelegateForColumn(0, _CategoryDelegate(self.group_table, lambda: CATEGORY_CHOICES))
        self.group_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.group_table.setMinimumHeight(420)
        self.group_table.setShowGrid(False)
        self.group_table.setStyleSheet(TABLE_STYLE + "QTableWidget::item {border-bottom:1px solid #eceef3;}")
        self.page.columns.attach(self.group_table, "groups")
        self.group_table.itemSelectionChanged.connect(self._render_equipment)
        self.group_table.itemChanged.connect(self._cell_changed)
        left.body.addWidget(self.group_table)
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
        self._sync()

    def reload(self):
        return self.run(lambda: self.page.gateway.data_admin("equipment_overview"), self._loaded)

    def _loaded(self, value, error):
        if error is not None:
            self.say("설비 목록을 읽지 못했습니다.", error=True)
            return
        self.loaded = True
        self.groups, self.equipment = value
        known = {g.group_id for g in self.groups}
        self.pending = {gid: change for gid, change in self.pending.items() if gid in known}  # 저장 전 수정은 다시 읽어도 둔다
        self.say(self.saved_note)
        self.saved_note = ""
        self._render_groups()
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

    # 표 ------------------------------------------------------------------------------------
    def _value(self, group, field):
        """저장 전 수정을 반영한 값. 개조형의 대분류는 상위 설비군(저장 전 수정 포함)을 따른다."""
        if field == "category" and group.parent_id is not None:
            parent = next((g for g in self.groups if g.group_id == group.parent_id), None)
            return self._value(parent, "category") if parent is not None else group.category
        return self.pending.get(group.group_id, {}).get(field, group.name if field == "name" else group.category)

    def _render_groups(self):
        selected = self.selected_group()
        children = defaultdict(list)
        for g in self.groups:
            if g.parent_id is not None:
                children[g.parent_id].append(g)
        bases = sorted((g for g in self.groups if g.parent_id is None),
                       key=lambda g: (self.CATEGORY_ORDER.get(self._value(g, "category"), 9), self._value(g, "name")))
        known = {g.group_id for g in bases}
        ordered = []
        for base in bases:
            ordered.append(base)
            ordered.extend(sorted(children[base.group_id], key=lambda g: self._value(g, "name")))
        ordered.extend(g for g in self.groups if g.parent_id is not None and g.parent_id not in known)
        self.ordered = tuple(ordered)
        categories = [self._value(g, "category") for g in self.ordered]
        # 분류는 묶음의 첫 줄에만 쓴다 (11-11 요청)
        shown = [c if i == 0 or categories[i - 1] != c else "" for i, c in enumerate(categories)]
        self._rendering = True
        try:
            _fill(self.group_table, [(category, self._value(g, "name") if g.parent_id is None else f"   ↳ {self._value(g, 'name')}",
                                      self._parent_name(g) if g.parent_id else "—", f"{g.equipment_count}대")
                                     for category, g in zip(shown, self.ordered)], [g.group_id for g in self.ordered])
            bold = QFont(self.group_table.font())
            bold.setBold(True)
            for r, g in enumerate(self.ordered):
                change = self.pending.get(g.group_id, {})
                kind = self.group_table.item(r, 0)
                kind.setToolTip(categories[r])  # 고칠 때 시작 값 (_CategoryDelegate)
                if shown[r]:
                    kind.setFont(bold)
                name = self.group_table.item(r, 1)
                for c in range(self.group_table.columnCount()):
                    item = self.group_table.item(r, c)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if g.parent_id is None:
                    kind.setFlags(kind.flags() | Qt.ItemFlag.ItemIsEditable)
                else:
                    kind.setToolTip(f"{categories[r]} · 개조형은 상위 설비군의 대분류를 따라갑니다")
                if not g.builtin:
                    name.setFlags(name.flags() | Qt.ItemFlag.ItemIsEditable)
                    name.setToolTip(f"{self._value(g, 'name')} · 두 번 눌러 이름을 고칩니다")
                else:
                    name.setToolTip(f"{g.name} · 기본 설비군은 이름을 바꿀 수 없습니다")
                if "category" in change and g.parent_id is None:
                    kind.setBackground(QBrush(SAVING))
                    if not shown[r]:
                        kind.setText(categories[r])  # 바꾼 줄은 대분류를 보이게 한다
                if "name" in change:
                    name.setBackground(QBrush(SAVING))
        finally:
            self._rendering = False
        if selected is not None:
            row = next((r for r, g in enumerate(self.ordered) if g.group_id == selected), None)
            if row is not None:
                self.group_table.selectRow(row)
        self.page.columns.fit(self.group_table)
        self._sync()

    def _parent_name(self, group):
        parent = next((g for g in self.groups if g.group_id == group.parent_id), None)
        return self._value(parent, "name") if parent is not None else (group.parent_name or "—")

    def _cell_changed(self, item):
        if self._rendering or not (0 <= item.row() < len(self.ordered)) or item.column() not in (0, 1):
            return
        group = self.ordered[item.row()]
        if item.column() == 0:
            field, value = "category", item.text().strip()
            if group.parent_id is not None or value not in CATEGORY_CHOICES:
                self._render_groups()
                return
            original = group.category
        else:
            field, value = "name", item.text().strip().removeprefix("↳").strip()
            if group.builtin or not value:
                self.say("설비군 이름을 적어 주세요." if not value else f"'{group.name}'은(는) 기본 설비군이라 이름을 바꿀 수 없습니다.",
                         error=True)
                self._render_groups()
                return
            if any(self._value(g, "name").casefold() == value.casefold() for g in self.groups if g.group_id != group.group_id):
                self.say(f"'{value}'은(는) 이미 있는 설비군 이름입니다.", error=True)
                self._render_groups()
                return
            original = group.name
        if value == self._value(group, field):
            return
        self.steps.append(deepcopy(self.pending))
        self.redo_steps.clear()
        change = dict(self.pending.get(group.group_id, {}))
        if value == original:
            change.pop(field, None)
        else:
            change[field] = value
        if change:
            self.pending[group.group_id] = change
        else:
            self.pending.pop(group.group_id, None)
        self.say("")
        self._render_groups()

    def undo(self):
        if not self.steps or self.page.saving:
            return False
        self.redo_steps.append(deepcopy(self.pending))
        self.pending = self.steps.pop()
        self._render_groups()
        return True

    def redo(self):
        if not self.redo_steps or self.page.saving:
            return False
        self.steps.append(deepcopy(self.pending))
        self.pending = self.redo_steps.pop()
        self._render_groups()
        return True

    def save(self):
        if not self.pending or self.page.saving:
            return False
        changes = [(gid, change.get("name"), change.get("category")) for gid, change in self.pending.items()]
        return self.page.change(lambda: self.page.gateway.data_admin("update_groups", changes), self,
                                message="설비군을 저장하고 있습니다…", done=self._groups_saved)

    def _groups_saved(self, _count):
        self.pending, self.steps, self.redo_steps = {}, [], []
        self.reload()

    def selected_group(self):
        rows = self.group_table.selectionModel().selectedRows() if self.group_table.selectionModel() else []
        if not rows:
            return None
        item = self.group_table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _selected(self):
        gid = self.selected_group()
        return next((g for g in self.groups if g.group_id == gid), None)

    def _render_equipment(self, *_):
        gid = self.selected_group()
        text = self.search.text().strip().casefold()
        site, line = self.site_filter.currentData(), self.line_filter.currentData()
        rows = [e for e in self.equipment if (gid is None or gid in e.group_ids)
                and (not text or text in e.code.casefold()) and (site is None or e.site == site)
                and (line is None or e.line == line)]
        group = self._selected()
        name = self._value(group, "name") if group is not None else None
        self.equipment_title.setText(f"설비 목록 · {name + ' · ' if name else ''}{len(rows)}대")
        _fill(self.equipment_table, [(e.code, e.model_name or "모델 미확인",
                                      (e.site or "?") if e.snapshot_count else "관측 없음",
                                      (e.line or "미지정") if e.snapshot_count else "", f"{e.snapshot_count}건", e.base_date)
                                     for e in rows], [e.equipment_id for e in rows])
        self.page.columns.fit(self.equipment_table)
        self._sync()

    def add_group(self):
        if self.page.gateway is None or self.page.saving or self.pending:
            return False
        dialog = AddGroupDialog(self, self.groups)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        name, category, parent = dialog.values()
        return self.page.change(lambda: self.page.gateway.data_admin("create_group", name, category, parent), self,
                                message=f"설비군 '{name}'을 등록하고 있습니다…", done=self._group_added)

    def _group_added(self, group):
        self.page.group_added.emit(group)
        self.reload()

    def delete_group(self):
        group = self._selected()
        if group is None or self.page.saving or self.pending:
            return False
        if QMessageBox.question(self, "설비군 삭제", f"설비군 '{group.name}'을(를) 지울까요?\n함께 만든 빈 SW 계열도 지워집니다. "
                                "설비 · 릴리즈 노트에 쓰이는 설비군은 지울 수 없습니다.") != QMessageBox.StandardButton.Yes:
            return False
        return self.page.change(lambda: self.page.gateway.data_admin("delete_group", group.group_id), self,
                                message="설비군을 지우고 있습니다…", done=lambda _: self.reload())

    def _sync(self):
        busy = self.page.saving
        group = self._selected()
        # 저장 전 수정이 있으면 바로 저장하는 추가 · 삭제는 막는다 (먼저 저장하거나 되돌린다)
        self.add_group_button.setEnabled(not busy and not self.pending)
        self.delete_button.setEnabled(group is not None and not busy and not self.pending)
        self.save_button.setEnabled(bool(self.pending) and not busy)
        self.save_button.setText(f"저장 ({len(self.pending)})" if self.pending else "저장")
        self.undo_button.setEnabled(bool(self.steps) and not busy)
        self.redo_button.setEnabled(bool(self.redo_steps) and not busy)


class AddLineDialog(QDialog):
    """라인 추가: Site · 메모리/파운드리 · 라인 이름."""

    def __init__(self, parent, sites, site=None, fab=None):
        super().__init__(parent)
        self.setWindowTitle("라인 추가")
        form = QFormLayout(self)
        self.site, self.fab, self.name = ComboBox(), ComboBox(), QLineEdit()
        for value in sites:
            self.site.addItem(value, value)
        self.site.setCurrentIndex(max(0, self.site.findData(site)))
        for key, text in (("MEMORY", "메모리"), ("FOUNDRY", "파운드리")):
            self.fab.addItem(text, key)
        self.fab.setCurrentIndex(max(0, self.fab.findData(fab)))
        self.name.setPlaceholderText("P4H")
        self.error = label("", 12, color=DANGER, wrap=True)
        form.addRow("Site", self.site)
        form.addRow("메모리/파운드리", self.fab)
        form.addRow("라인 이름", self.name)
        form.addRow(self.error)
        actions = QHBoxLayout()
        cancel, ok = button("취소"), button("추가", primary=True)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._accept)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(ok)
        form.addRow(actions)

    def _accept(self):
        if not self.name.text().strip() or self.site.currentData() is None:
            self.error.setText("Site와 라인 이름을 입력해 주세요.")
            return
        self.accept()

    def values(self):
        return self.site.currentData(), self.name.text().strip(), self.fab.currentData()


# --- 설비현황 › 위치 ------------------------------------------------------------------------
FAB_LABELS = {"MEMORY": "메모리", "FOUNDRY": "파운드리", None: "메모리/파운드리 모름"}


class LocationPanel(_Panel):
    """Site → 메모리/파운드리 → 라인 트리 (11-11 6차) · 오른쪽: 고른 위치의 설비.

    11-12: 표에서 바로 고친다. 트리의 Site · 라인 이름을 두 번 눌러 바꾸고(같은 Site에 있는 라인 이름을 적으면 그 라인에
    합친다), 오른쪽 설비 표의 Line 칸에서 다른 라인을 고르면 그 설비를 옮긴다(여러 줄을 고르고 한 칸을 바꾸면 고른 설비
    모두). 바꾼 것은 [저장]으로 한꺼번에 반영하고, 모든 기준일의 설비 현황이 함께 바뀐다. 추가 · 삭제 · 메모리/파운드리
    변경은 바로 저장한다.
    """

    def __init__(self, page):
        super().__init__(page)
        self.equipment, self.registered, self.line_rows = (), (), ()
        self.ops, self.redo_ops = [], []  # 저장 전 변경 (화면에서 한 순서)
        self._rendering = False
        self._pending_select = None
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
        self.add_line_button = button("＋ 라인 추가")
        self.add_site_button.clicked.connect(self.add_site)
        self.add_line_button.clicked.connect(self.add_line)
        head.addWidget(self.add_site_button)
        head.addWidget(self.add_line_button)
        left.body.addLayout(head)
        actions = QHBoxLayout()
        self.fab_button, self.delete_button = button("메모리/파운드리 변경"), button("삭제")
        self.fab_button.clicked.connect(self.change_fab)
        self.delete_button.clicked.connect(self.delete)
        self.undo_button, self.redo_button = _history_buttons(self, "저장 전 이름 변경 · 합치기 · 설비 옮기기")
        self.save_button = button("저장", primary=True)
        self.save_button.setMinimumWidth(96)
        self.save_button.clicked.connect(self.save)
        for widget in (self.fab_button, self.delete_button):
            actions.addWidget(widget)
        actions.addStretch()
        for widget in (self.undo_button, self.redo_button, self.save_button):
            actions.addWidget(widget)
        left.body.addLayout(actions)
        left.body.addWidget(self.notice)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setAccessibleName("위치 트리 (Site → 메모리/파운드리 → 라인)")
        self.tree.setMinimumHeight(360)
        self.tree.setStyleSheet("QTreeWidget {font-size:14px;} QTreeWidget::item {min-height:30px;}")
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._render_detail)
        self.tree.itemChanged.connect(self._tree_changed)
        left.body.addWidget(self.tree)
        self.detail_title = label("위치를 고르세요", 20, medium=True)
        right.body.addWidget(self.detail_title)
        self.detail_info = label("", 13, color=SECONDARY, wrap=True)
        right.body.addWidget(self.detail_info)
        right.body.addWidget(label("소속 설비", 16, medium=True))
        self.equipment_table = _table(("설비 ID", "설비군", "모델", "Line", "최근 기준일"), "위치 소속 설비")
        self.equipment_table.setItemDelegate(_KeepColorsDelegate(self.equipment_table))
        self.equipment_table.setItemDelegateForColumn(3, _ChoiceDelegate(self.equipment_table, self._line_choices))
        self.equipment_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.equipment_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.equipment_table.setMinimumHeight(400)
        self.equipment_table.itemChanged.connect(self._line_changed)
        self.page.columns.attach(self.equipment_table, "location")
        right.body.addWidget(self.equipment_table)
        self._shown = []  # 오른쪽 표 줄 → 설비 (저장 전 변경 반영)
        self._sync()

    def reload(self):
        gateway = self.page.gateway
        return self.run(lambda: (gateway.data_admin("equipment_overview")[1], gateway.data_admin("sites"),
                                 gateway.data_admin("lines")), self._loaded)

    def _loaded(self, value, error):
        if error is not None:
            self.say("위치를 읽지 못했습니다.", error=True)
            return
        self.loaded = True
        self.equipment, self.registered, self.line_rows = value[0], tuple(value[1]), tuple(value[2])
        self.say(self.saved_note)
        self.saved_note = ""
        select, self._pending_select = self._pending_select, None
        self._render_tree()
        if select is not None:
            self._select(select)

    # 저장 전 변경을 반영한 모습 ---------------------------------------------------------------------
    def _view(self):
        """(설비 줄, 라인 줄, 등록 Site, 바뀐 위치 표시). 저장 전 변경(ops)을 원래 자료에 순서대로 얹는다."""
        equipment = {e.equipment_id: e for e in self.equipment}
        lines = {(r.site, r.line): r for r in self.line_rows}
        registered = list(self.registered)
        renamed, moved = set(), set()
        for op in self.ops:
            if op["kind"] == "site":
                old, new = op["old"], op["new"]
                equipment = {i: replace(e, site=new) if e.site == old else e for i, e in equipment.items()}
                lines = {(new if s == old else s, l): replace(r, site=new) if s == old else r for (s, l), r in lines.items()}
                registered = [new if s == old else s for s in registered]
                renamed = {(k[0], new if k[1] == old else k[1], *k[2:]) for k in renamed} | {("site", new)}
                moved = {(new if s == old else s, i) for s, i in moved}
            elif op["kind"] == "line":
                site, old, new = op["site"], op["old"], op["new"]
                equipment = {i: replace(e, line=new) if (e.site, e.line) == (site, old) else e for i, e in equipment.items()}
                row = lines.pop((site, old), None)
                if (site, new) not in lines and row is not None:
                    lines[(site, new)] = replace(row, line=new)
                renamed.add(("line", site, new))
            elif op["kind"] == "move":
                for eid, _ in op["equipment"]:
                    if eid in equipment:
                        equipment[eid] = replace(equipment[eid], line=op["to"])
                        moved.add((op["site"], eid))
        counts = Counter((e.site, e.line) for e in equipment.values() if e.snapshot_count and e.site and e.line)
        line_rows = tuple(sorted((replace(r, equipment_count=counts[key]) for key, r in lines.items()),
                                 key=lambda r: (r.site, r.fab or "", _natural(r.line))))
        return tuple(equipment.values()), line_rows, tuple(registered), renamed, moved

    def site_names(self):
        equipment, line_rows, registered, *_ = self._view()
        return sorted({e.site for e in equipment if e.site and e.snapshot_count} | set(registered) | {r.site for r in line_rows})

    def _render_tree(self):
        selected = self.selection()
        equipment, line_rows, registered, renamed, _ = self._view()
        sites = sorted({e.site for e in equipment if e.site and e.snapshot_count} | set(registered) | {r.site for r in line_rows})
        self._rendering = True
        try:
            self.tree.clear()
            for site in sites:
                lines = [r for r in line_rows if r.site == site]
                count = sum(r.equipment_count for r in lines)
                node = QTreeWidgetItem([site, f"{count}대" if count else "등록 · 설비 없음"])
                node.setData(0, Qt.ItemDataRole.UserRole, ("site", site))
                node.setFlags(node.flags() | Qt.ItemFlag.ItemIsEditable)
                node.setToolTip(0, f"{site} · 두 번 눌러 이름을 고칩니다")
                if ("site", site) in renamed:
                    node.setBackground(0, QBrush(SAVING))
                for fab in ("MEMORY", "FOUNDRY", None):
                    members = [r for r in lines if r.fab == fab]
                    if fab is None and not members:
                        continue
                    fab_node = QTreeWidgetItem([FAB_LABELS[fab], ""])
                    fab_node.setData(0, Qt.ItemDataRole.UserRole, ("fab", site, fab))
                    for row in members:
                        leaf = QTreeWidgetItem([row.line, f"{row.equipment_count}대" if row.equipment_count else
                                                ("등록" if row.registered else "")])
                        leaf.setData(0, Qt.ItemDataRole.UserRole, ("line", site, row.line, fab))
                        leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsEditable)
                        leaf.setToolTip(0, f"{site} · {row.line} · 두 번 눌러 이름을 고칩니다 "
                                           "(같은 Site에 있는 라인 이름을 적으면 그 라인에 합칩니다)")
                        if ("line", site, row.line) in renamed:
                            leaf.setBackground(0, QBrush(SAVING))
                        fab_node.addChild(leaf)
                    node.addChild(fab_node)
                self.tree.addTopLevelItem(node)
                node.setExpanded(True)  # 트리에 붙인 뒤에 펼쳐야 적용된다
                for i in range(node.childCount()):
                    node.child(i).setExpanded(True)
        finally:
            self._rendering = False
        if selected is not None:
            self._select(selected)
        self._render_detail()

    def _select(self, data):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item.data(0, Qt.ItemDataRole.UserRole)[:3] == data[:3]:
                self.tree.setCurrentItem(item)
                return
            stack.extend(item.child(i) for i in range(item.childCount()))

    def selection(self):
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def _line_choices(self):
        data = self.selection()
        if data is None:
            return ()
        _, line_rows, *_ = self._view()
        return sorted((r.line for r in line_rows if r.site == data[1]), key=_natural)

    def _render_detail(self):
        data = self.selection()
        self._sync()
        if data is None:
            self._shown = []
            self._rendering = True
            self.equipment_table.setRowCount(0)
            self._rendering = False
            return
        equipment, line_rows, registered, _, moved = self._view()
        kind, site = data[0], data[1]
        line = data[2] if kind == "line" else None
        fab = data[2] if kind == "fab" else data[3] if kind == "line" else None
        fab_lines = {r.line for r in line_rows if r.site == site and r.fab == fab}
        rows = sorted((e for e in equipment if e.snapshot_count and e.site == site
                       and (kind == "site" or (kind == "fab" and e.line in fab_lines) or (kind == "line" and e.line == line))),
                      key=lambda e: (_natural(e.line), e.code))
        self._shown = rows
        self.detail_title.setText(" · ".join(v for v in (site, FAB_LABELS[fab] if kind != "site" else None, line) if v))
        self.detail_info.setText(f"설비 {len(rows)}대")
        self._rendering = True
        try:
            _fill(self.equipment_table, [(e.code, e.group_names, e.model_name, e.line, e.base_date) for e in rows],
                  [e.equipment_id for e in rows])
            for r, e in enumerate(rows):
                for c in range(self.equipment_table.columnCount()):
                    item = self.equipment_table.item(r, c)
                    if c != 3:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                cell = self.equipment_table.item(r, 3)
                cell.setToolTip(f"{e.line} · 두 번 눌러 다른 라인을 고르면 옮깁니다 (여러 줄을 고르면 모두)")
                if (site, e.equipment_id) in moved:
                    cell.setBackground(QBrush(SAVING))
        finally:
            self._rendering = False
        self.page.columns.fit(self.equipment_table)

    # 표에서 고치기 (저장 전) --------------------------------------------------------------------
    def _push(self, op):
        self.ops.append(op)
        self.redo_ops.clear()
        self.say("")
        self._render_tree()

    def _tree_changed(self, item, column):
        if self._rendering or column != 0:
            return
        data, new = item.data(0, Qt.ItemDataRole.UserRole), item.text(0).strip()
        # 트리를 다시 그리면 방금 고친 항목이 지워지므로, 편집 신호가 끝난 뒤에 반영한다.
        QTimer.singleShot(0, lambda: self._tree_edited(data, new) if isValid(self) else None)

    def _tree_edited(self, data, new):
        if data is None or data[0] not in ("site", "line"):
            return
        old = data[1] if data[0] == "site" else data[2]
        if not new or new == old:
            self._render_tree()
            return
        if len(new) > 50:
            self.say("이름은 50자 이내로 적어 주세요.", error=True)
            self._render_tree()
            return
        if data[0] == "site":
            if new.casefold() != old.casefold() and new.casefold() in {s.casefold() for s in self.site_names()}:
                self.say(f"'{new}'은(는) 이미 있는 Site입니다. Site는 합칠 수 없습니다.", error=True)
                self._render_tree()
                return
            self._push({"kind": "site", "old": old, "new": new})
            self._select(("site", new))
            return
        site = data[1]
        _, line_rows, *_ = self._view()
        target = next((r for r in line_rows if r.site == site and r.line.casefold() == new.casefold() and r.line != old), None)
        if target is not None:
            count = next((r.equipment_count for r in line_rows if r.site == site and r.line == old), 0)
            if QMessageBox.question(self, "라인 합치기",
                                    f"{site}에 '{target.line}' 라인이 이미 있습니다.\n'{old}'의 설비 {count}대를 '{target.line}'에 "
                                    "합칠까요? 모든 기준일의 설비 현황이 옮겨지고, 메모리/파운드리는 "
                                    f"'{target.line}' 것을 따릅니다. [저장] 전에는 되돌릴 수 있습니다.") \
                    != QMessageBox.StandardButton.Yes:
                self._render_tree()
                return
            new = target.line
        self._push({"kind": "line", "site": site, "old": old, "new": new})
        self._select(("line", site, new))

    def _line_changed(self, item):
        if self._rendering or item.column() != 3 or not (0 <= item.row() < len(self._shown)):
            return
        to = item.text().strip()
        data = self.selection()
        if data is None:
            return
        site = data[1]
        if to not in self._line_choices():
            self.say(f"{site}에 없는 라인입니다. 목록에서 고르거나 왼쪽에서 라인을 먼저 추가해 주세요.", error=True)
            self._render_detail()
            return
        rows = {index.row() for index in self.equipment_table.selectionModel().selectedRows()}
        if item.row() not in rows:  # 고른 줄 밖의 칸을 고쳤으면 그 줄만
            rows = {item.row()}
        targets = [[self._shown[r].equipment_id, self._shown[r].line] for r in sorted(rows)
                   if r < len(self._shown) and self._shown[r].line != to]
        if not targets:
            self._render_detail()
            return
        self._push({"kind": "move", "site": site, "to": to, "equipment": targets})

    def undo(self):
        if not self.ops or self.page.saving:
            return False
        self.redo_ops.append(self.ops.pop())
        self._render_tree()
        return True

    def redo(self):
        if not self.redo_ops or self.page.saving:
            return False
        self.ops.append(self.redo_ops.pop())
        self._render_tree()
        return True

    def save(self):
        if not self.ops or self.page.saving:
            return False
        ops = deepcopy(self.ops)
        return self.page.change(lambda: self.page.gateway.data_admin("apply_locations", ops), self,
                                message="위치를 저장하고 있습니다…", done=self._locations_saved)

    def _locations_saved(self, _counts):
        self._pending_select = self.selection()
        self.ops, self.redo_ops = [], []
        self.reload()

    # 추가 · 지우기 · 메모리/파운드리 (바로 저장) ------------------------------------------------------
    def add_site(self):
        if self.page.gateway is None or self.page.saving or self.ops:
            return False
        name, ok = QInputDialog.getText(self, "Site 추가", "Site 이름")
        name = (name or "").strip()
        if not ok or not name:
            return False
        if name.casefold() in {s.casefold() for s in self.site_names()}:
            self.say(f"'{name}'은 이미 있는 Site입니다.", error=True)
            return False
        return self.page.change(lambda: self.page.gateway.data_admin("add_site", name), self,
                                message=f"Site '{name}'을 등록하고 있습니다…", done=self._site_added)

    def _site_added(self, name):
        self._pending_select = ("site", name)
        self.reload()
        if QMessageBox.question(self, "빈 현황표 만들기",
                                f"'{name}'의 빈 현황표를 만들까요?\n만들면 현황표에서 설비를 한 행씩 추가할 수 있습니다. "
                                "엑셀이 있으면 '아니요'를 누르고 데이터 가져오기로 넣으면 됩니다.") != QMessageBox.StandardButton.Yes:
            return
        self.create_sheet(name)

    def create_sheet(self, site):
        dialog = CreateSheetDialog(self, [site], site)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        _, base_date, area = dialog.values()
        return self.page.change(lambda: self.page.gateway.data_admin("create_sheet", site, base_date, area), self,
                                message="빈 현황표를 만들고 있습니다…",
                                done=lambda batch_id: self._sheet_created(batch_id, site, base_date))

    def _sheet_created(self, batch_id, site, base_date):
        """만들기만 하고 위치 탭에 머문다 (11-11). 현황표 탭에 가면 만든 현황표가 골라져 있다."""
        self.page.sheet_panel.open_batch(batch_id)
        self._changed("", ("site", site))

    def add_line(self):
        if self.page.gateway is None or self.page.saving or self.ops or not self.site_names():
            return False
        data = self.selection()
        dialog = AddLineDialog(self, self.site_names(), site=data[1] if data else None,
                               fab=data[2] if data and data[0] == "fab" else data[3] if data and data[0] == "line" else None)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        site, line, fab = dialog.values()
        return self.page.change(lambda: self.page.gateway.data_admin("add_line", site, line, fab), self,
                                message=f"라인 '{line}'을 등록하고 있습니다…",
                                done=lambda _: self._changed("", ("line", site, line, fab)))

    def change_fab(self):
        data = self.selection()
        if data is None or data[0] != "line" or self.page.saving or self.ops:
            return False
        site, line, fab = data[1], data[2], data[3]
        if fab is None:  # 모르는 라인은 어느 쪽인지 고른다
            choice, ok = QInputDialog.getItem(self, "메모리/파운드리 지정", f"{site} · {line}은(는) 어느 쪽 라인인가요?",
                                              ["메모리", "파운드리"], 0, False)
            if not ok:
                return False
            target = "FOUNDRY" if choice == "파운드리" else "MEMORY"
        else:
            target = "FOUNDRY" if fab == "MEMORY" else "MEMORY"
        count = next((r.equipment_count for r in self.line_rows if r.site == site and r.line == line), 0)
        if QMessageBox.question(self, "메모리/파운드리 변경",
                                f"{site} · {line}을(를) {FAB_LABELS[target]} 라인으로 바꿀까요?\n그 라인 설비 {count}대의 생산 영역도 "
                                f"{FAB_LABELS[target]}(으)로 바뀝니다. 설비 SW 계열(파운드리용 릴리즈 노트)은 바뀌지 않으니, 필요하면 "
                                "그 현황표를 다시 가져와 주세요.") != QMessageBox.StandardButton.Yes:
            return False
        return self.page.change(lambda: self.page.gateway.data_admin("set_line_fab", site, line, target), self,
                                message="메모리/파운드리를 바꾸고 있습니다…",
                                done=lambda _: self._changed("", ("line", site, line, target)))

    def delete(self):
        data = self.selection()
        if data is None or data[0] == "fab" or self.page.saving or self.ops:
            return False
        what = "Site" if data[0] == "site" else "라인"
        name = data[1] if data[0] == "site" else f"{data[1]} · {data[2]}"
        if QMessageBox.question(self, f"{what} 삭제", f"{what} '{name}'을(를) 지울까요?\n설비 현황이 있는 {what}은 지울 수 없습니다."
                                + ("\n그 Site에 등록한 라인도 함께 지워집니다." if data[0] == "site" else "")) \
                != QMessageBox.StandardButton.Yes:
            return False
        gateway = self.page.gateway
        work = (lambda: gateway.data_admin("delete_site", data[1])) if data[0] == "site" else (
            lambda: gateway.data_admin("delete_line", data[1], data[2]))
        return self.page.change(work, self, message=f"{what}을(를) 지우고 있습니다…", done=lambda _: self._changed("", None))

    def _changed(self, note, select):
        self.saved_note = note
        self._pending_select = select
        self.reload()

    def _sync(self):
        busy = self.page.saving
        data = self.selection()
        pending = bool(self.ops)
        # 저장 전 변경이 있으면 바로 저장하는 추가 · 삭제 · 메모리/파운드리 변경은 막는다 (먼저 저장하거나 되돌린다)
        self.add_site_button.setEnabled(not busy and not pending)
        self.add_line_button.setEnabled(not busy and not pending and bool(self.site_names()))
        self.delete_button.setEnabled(not busy and not pending and data is not None and data[0] in ("site", "line"))
        self.fab_button.setEnabled(not busy and not pending and data is not None and data[0] == "line")
        if data is not None and data[0] == "line":
            self.fab_button.setText({"MEMORY": "파운드리로 변경", "FOUNDRY": "메모리로 변경"}.get(data[3], "메모리/파운드리 지정"))
        else:
            self.fab_button.setText("메모리/파운드리 변경")
        self.save_button.setEnabled(pending and not busy)
        self.save_button.setText(f"저장 ({len(self.ops)})" if pending else "저장")
        self.undo_button.setEnabled(pending and not busy)
        self.redo_button.setEnabled(bool(self.redo_ops) and not busy)


# --- 릴리즈노트 ------------------------------------------------------------------------------
RELEASE_COLUMNS = ("Version", "Date", "No", "Content (Item)", "적용 모델")
RELEASE_FIELDS = {0: ("release_version", "version_raw"), 1: ("release_version", "released_at"),
                  2: ("item", "item_no"), 3: ("item", "content"), 4: ("release_version_item", "target_group_ids")}


class _ReleaseTable(QTableWidget):
    """Item 줄을 끌어 같은 버전 안에서 순서를 바꾸는 표. 놓으면 (원래 줄, 놓은 줄)을 알린다."""

    moved = Signal(int, int)

    def __init__(self, columns):
        super().__init__(0, columns)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)

    def dropEvent(self, event):
        target = self.indexAt(event.position().toPoint()).row()
        source = self.currentRow()
        event.setDropAction(Qt.DropAction.IgnoreAction)  # 칸을 옮기지 않고 순서 저장 뒤 다시 그린다
        event.accept()
        if source >= 0 and target >= 0 and source != target:
            self.moved.emit(source, target)


class ReleasePanel(_Panel):
    """릴리즈노트 엑셀형 표 (최신 버전이 위). 칸 수정 · 버전/Item 추가 · 삭제 · Item 순서를 모아 두었다가 [저장]으로
    한꺼번에 반영하고, [되돌리기]로 바로 앞 단계(또는 마지막 저장 전체)를 되돌린다 (11-11 5차). 위험 지정은 아이템 매니저에서 한다."""

    def __init__(self, page):
        super().__init__(page)
        self.families, self.rows, self.groups = (), (), {}
        self.family_id, self.variant, self.view, self.steps = None, None, [], []
        self.redo_steps = []  # 되돌린 단계 (다시 실행용)
        self._reset_pending()
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
        actions = QHBoxLayout()
        self.undo_button, self.redo_button = _history_buttons(self, "저장 전 수정 · 추가 · 삭제 표시 · 순서, 또는 마지막 저장")
        self.add_version_button = button("＋ 버전 추가")
        self.add_item_button = button("＋ Item 추가")
        self.add_item_button.setToolTip("고른 줄의 버전 맨 뒤에 새 Item을 추가합니다. 순서는 줄을 끌어서 바꿉니다.")
        self.delete_item_button = button("선택 Item 삭제")
        self.delete_version_button = button("선택 버전 삭제")
        self.save_button = button("저장", primary=True)
        self.save_button.setMinimumWidth(110)
        self.add_version_button.clicked.connect(self.add_version)
        self.add_item_button.clicked.connect(self.add_item)
        self.delete_item_button.clicked.connect(self.delete_item)
        self.delete_version_button.clicked.connect(self.delete_version)
        self.save_button.clicked.connect(self.save)
        actions.addStretch()
        for widget in (self.undo_button, self.redo_button, self.add_version_button, self.add_item_button, self.delete_item_button,
                       self.delete_version_button, self.save_button):
            actions.addWidget(widget)
        middle.body.addLayout(actions)
        self.table = _ReleaseTable(len(RELEASE_COLUMNS))
        self.table.setItemDelegate(_KeepColorsDelegate(self.table))
        self.table.setItemDelegateForColumn(1, _DateDelegate(self.table))  # Date: 리플레이와 같은 달력 (11-12)
        self.table.setHorizontalHeaderLabels(RELEASE_COLUMNS)
        self.table.setAccessibleName("릴리즈노트 · 버전 줄 아래 Item 줄")
        self.table.setStyleSheet(TABLE_STYLE)
        self.table.setMinimumHeight(520)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.itemChanged.connect(self._cell_changed)
        self.table.cellClicked.connect(self._toggle_version)
        self.table.itemSelectionChanged.connect(self._sync)
        self.table.moved.connect(self.move_item)
        # 'release.v2': 예전에 기억한 폭은 마지막 열(적용 모델)이 남는 폭을 다 가진 값이라 쓰지 않는다 (11-12)
        self.page.columns.attach(self.table, "release.v2")
        # 남는 폭은 마지막 '적용 모델'이 아니라 Content(Item)가 쓴다. Item 내용이 잘려 버전만 보이는 것처럼 보였다 (11-12)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(False)  # 긴 버전 이름(v0.3.0_DEMO_…)이 줄바꿈되어 '▼ …'만 보이지 않게 오른쪽을 줄인다
        middle.body.addWidget(self.table)
        middle.body.addWidget(self.notice)
        self._sync()

    def _reset_pending(self):
        # edits: ("v", 릴리즈 ID, 칸) · ("i", Item ID, "content") · ("l", 릴리즈 ID, Item ID) → 새 값
        self.edits, self.deleted, self.orders, self.new_versions, self.new_items = {}, set(), {}, [], []
        self.collapsed = set()
        self._temp = 0

    @property
    def pending(self):
        return bool(self.edits or self.deleted or self.orders or self.new_versions or self.new_items)

    def _count(self):
        return len(self.edits) + len(self.deleted) + len(self.orders) + len(self.new_versions) + len(self.new_items)

    # 읽기 -----------------------------------------------------------------------------------
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
        if family_id != self.family_id and not self._leave_ok():
            self._select_family(self.family_id)  # 고른 계열을 원래대로
            return
        if family_id != self.family_id:
            self.steps, self.redo_steps = [], []
        self.variant = variant
        self._load_rows(family_id)

    def _select_family(self, family_id):
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            node = self.tree.topLevelItem(i)
            if node.data(0, Qt.ItemDataRole.UserRole) == (family_id, None):
                self.tree.setCurrentItem(node)
        self.tree.blockSignals(False)

    def _leave_ok(self):
        """저장하지 않은 변경이 있으면 저장 · 버리기 · 취소를 묻는다."""
        if not self.pending:
            return True
        box = QMessageBox(QMessageBox.Icon.Question, "저장하지 않은 변경",
                          f"저장하지 않은 릴리즈노트 변경이 {self._count()}건 있습니다. 어떻게 할까요?", parent=self)
        save = box.addButton("저장", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("버리기", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is save:
            self.save()
            return False
        if box.clickedButton() is discard:
            self._reset_pending()
            return True
        return False

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
        self.rows = tuple(rows)
        self._reset_pending()
        note, self.saved_note = self.saved_note, ""
        family = next((f for f in self.families if f.family_id == self.family_id), None)
        variant = next((n for g, n in (family.variants if family else ()) if g == self.variant), None)
        self.family_title.setText((family.name if family else "계열") + (f" · {variant}" if variant else ""))
        versions = len({r.release_version_id for r in self.rows})
        self.family_info.setText(f"버전 {versions} · Item {sum(r.item_id is not None for r in self.rows)}"
                                 + (f" · {variant} 적용 Item 굵게" if variant else ""))
        self._render()
        self.say(note)

    # 화면 줄 만들기 ------------------------------------------------------------------------------
    def _versions(self):
        """[(버전 키, 버전 정보)] 화면 순서. 새 버전(최신)이 맨 위, 그 아래 DB 버전(이미 최신순)."""
        found = [(v["temp"], {"raw": v["version_raw"], "date": v["released_at"], "new": True}) for v in reversed(self.new_versions)]
        seen = set()
        for row in self.rows:
            if row.release_version_id not in seen:
                seen.add(row.release_version_id)
                found.append((row.release_version_id, {"raw": row.version_raw, "date": row.released_at, "new": False}))
        return found

    def _moved(self, version):
        """순서를 바꾼 버전에서 '옮긴' Item (11-12): 원래 순서를 그대로 지킨 가장 긴 줄(LIS)에 들지 않은 Item만.

        5번을 1번 자리로 옮기면 1~4번은 No만 밀릴 뿐 서로의 순서는 그대로라 5번만 고른다.
        """
        if version not in self.orders:
            return frozenset()
        stored = [r.item_id for r in self.rows if r.release_version_id == version and r.item_id is not None]
        original = stored + [i["temp"] for i in self.new_items if i["release"] == version]
        rank = {item: index for index, item in enumerate(original)}
        order = [item for item in self.orders[version] if item in rank]
        return frozenset(order) - frozenset(_longest_kept(order, rank))

    def _items(self, version):
        """버전의 Item 줄 키(DB Item ID 또는 새 Item temp) 화면 순서."""
        stored = [r.item_id for r in self.rows if r.release_version_id == version and r.item_id is not None]
        fresh = [i["temp"] for i in self.new_items if i["release"] == version]
        if version in self.orders:
            return list(self.orders[version])
        return stored + fresh

    def _render(self):
        """DB 줄과 저장 전 변경(수정 · 추가 · 삭제 표시 · 순서)으로 표를 다시 그린다."""
        by_item = {(r.release_version_id, r.item_id): r for r in self.rows if r.item_id is not None}
        new_items = {i["temp"]: i for i in self.new_items}
        self.view = []
        for version, info in self._versions():
            items = self._items(version)
            for index, key in enumerate(items or [None]):
                self.view.append({"version": version, "info": info, "item": key, "first": index == 0, "number": index + 1,
                                  "row": by_item.get((version, key)), "new_item": new_items.get(key)})
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.view))
        self.table.setVerticalHeaderLabels([str(i) for i in range(1, len(self.view) + 1)])  # 줄 번호는 1부터
        for r, line in enumerate(self.view):
            for c in range(len(RELEASE_COLUMNS)):
                item = QTableWidgetItem(self._display(line, c))
                editable = self._editable(line, c)
                if not editable:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._paint(item, line, c)
                self.table.setItem(r, c, item)
            self.table.setRowHidden(r, line["version"] in self.collapsed and not line["first"])
        self.page.columns.fit(self.table, cap=520)
        # 기억한 폭에서 '적용 모델'이 너무 넓어도 Content(Item)는 읽을 만큼 남긴다 (11-12)
        short = 280 - self.table.columnWidth(3)
        if short > 0:
            self.table.setColumnWidth(4, max(120, self.table.columnWidth(4) - short))
        self.table.blockSignals(False)
        self._sync()

    def _key(self, line, column):
        """칸 수정 키. 새 줄이면 None."""
        version, item = line["version"], line["item"]
        if column in (0, 1) and not line["info"]["new"]:
            return ("v", version, "version_raw" if column == 0 else "released_at")
        if column == 3 and line["row"] is not None:
            return ("i", item, "content")
        if column == 4 and line["row"] is not None:
            return ("l", version, item)
        return None

    def _original(self, line, column):
        row = line["row"]
        return {0: line["info"]["raw"], 1: line["info"]["date"], 3: row.content if row else "",
                4: row.applied_models if row else ""}.get(column, "")

    def _display(self, line, column):
        if column in (0, 1) and not line["first"]:
            return ""
        if column == 2:
            if line["item"] is None:
                return ""
            stored = line["row"].item_no if line["row"] else ""
            return str(line["number"]) if line["version"] in self.orders or line["new_item"] else stored
        new = line["new_item"]
        if column in (0, 1) and line["info"]["new"]:
            text = line["info"]["raw"] if column == 0 else (line["info"]["date"] or "")
        elif new is not None and column in (3, 4):
            text = new["content"] if column == 3 else (new["models"] or "전체")
        else:
            key = self._key(line, column)
            text = self.edits.get(key, self._original(line, column)) if key else self._original(line, column)
        if column == 0:
            return ("▶ " if line["version"] in self.collapsed else "▼ ") + text
        return text

    def _editable(self, line, column):
        if self._deleted(line):
            return False
        if column in (0, 1):
            return line["first"]
        if column in (3, 4):
            return line["item"] is not None
        return False

    def _deleted(self, line):
        return ("version", line["version"]) in self.deleted or ("item", line["version"], line["item"]) in self.deleted

    def _paint(self, item, line, column):
        text = item.text()
        item.setToolTip(text)
        if self._deleted(line):
            item.setBackground(QBrush(DELETE_ROW))
            item.setForeground(QBrush(QColor(DANGER)))
            item.setToolTip("삭제 예정 · [저장]을 누르면 지워집니다. 되돌리기 아이콘(Ctrl+Z)으로 표시를 풉니다.")
            return
        key = self._key(line, column)
        has_item = line["item"] is not None
        pending = ((line["info"]["new"] and ((column in (0, 1) and line["first"]) or (column in (2, 3, 4) and has_item)))
                   or (line["new_item"] is not None and column in (2, 3, 4))
                   or (key is not None and key in self.edits)
                   or (column == 2 and has_item and line["item"] in self._moved(line["version"])))
        if pending:
            item.setBackground(QBrush(SAVING))
            item.setToolTip(f"{text} · 저장 전")
        if self.variant is not None and line["row"] is not None and self.variant in line["row"].target_group_ids:
            font = item.font()
            font.setBold(True)
            item.setFont(font)

    def _toggle_version(self, row, column):
        if column == 1 and 0 <= row < len(self.view) and self.table.state() != QAbstractItemView.State.EditingState:
            item = self.table.item(row, 1)
            if item is not None and item.flags() & Qt.ItemFlag.ItemIsEditable:
                self.table.editItem(item)  # Date 칸은 한 번만 눌러도 달력이 열린다 (11-12)
            return
        if column != 0 or not (0 <= row < len(self.view)) or not self.view[row]["first"] \
                or self.table.state() == QAbstractItemView.State.EditingState:
            return
        version = self.view[row]["version"]
        self.collapsed ^= {version}
        self._render()

    # 칸 수정 ----------------------------------------------------------------------------------
    def _cell_changed(self, item):
        r, c = item.row(), item.column()
        if not (0 <= r < len(self.view)):
            return
        line = self.view[r]
        value = item.text().strip()
        if c == 0:
            value = value.removeprefix("▼ ").removeprefix("▶ ").strip()
        try:
            if c == 1 and value:
                date.fromisoformat(value)
            if c == 4:
                self._models(value)
        except ValueError as error:
            self.say(f"{error if c == 4 else '날짜 형식이 아닙니다: ' + value}. 날짜는 YYYY-MM-DD, 적용 모델은 설비군 이름으로 "
                     "적어 주세요.", error=True)
            self._repaint(r, c)
            return
        target = self._new_target(line, c)
        if target is not None:
            record, field = target
            previous = record[field] or ""
            if value != previous:
                self.steps.append(("new_edit", record, field, previous))
                record[field] = value
        else:
            key = self._key(line, c)
            if key is None:
                return
            previous = self.edits.get(key, self._original(line, c))
            if value == previous:
                return
            self.steps.append(("edit", key, previous))
            if value == self._original(line, c):
                self.edits.pop(key, None)
            else:
                self.edits[key] = value
        self._repaint(r, c)
        self._sync()

    def _new_target(self, line, column):
        """새 줄의 칸이면 (기록 dict, 칸 이름)."""
        if column in (0, 1) and line["info"]["new"]:
            record = next(v for v in self.new_versions if v["temp"] == line["version"])
            return record, "version_raw" if column == 0 else "released_at"
        if column in (3, 4) and line["new_item"] is not None:
            return line["new_item"], "content" if column == 3 else "models"
        return None

    def _repaint(self, r, c):
        """편집 신호 안에서는 칸 하나만 다시 칠한다."""
        self.table.blockSignals(True)
        item = self.table.item(r, c)
        item.setText(self._display(self.view[r], c))
        item.setBackground(QBrush())
        self._paint(item, self.view[r], c)
        self.table.blockSignals(False)

    def _models(self, text):
        names = [n.strip() for n in (text or "").split(",") if n.strip() and n.strip() != "전체"]
        unknown = [n for n in names if n.casefold() not in self.groups]
        if unknown:
            raise ValueError(f"모르는 설비군: {', '.join(unknown)}")
        return {"target_scope": "LISTED" if names else "ALL" if (text or "").strip() == "전체" else "UNKNOWN",
                "target_group_ids": [self.groups[n.casefold()] for n in names] if names else None}

    def selected_line(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.view[rows[0].row()] if rows and rows[0].row() < len(self.view) else None

    # 추가 · 삭제 표시 · 순서 ----------------------------------------------------------------------
    def _next_temp(self, prefix):
        self._temp += 1
        return f"{prefix}{self._temp}"

    def add_version(self):
        if self.family_id is None or self.page.saving:
            return False
        dialog = _VersionDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        version, day = dialog.values()
        if not version:
            self.say("버전명을 적어 주세요.", error=True)
            return False
        temp = self._next_temp("new-version-")
        self.new_versions.append({"temp": temp, "version_raw": version, "released_at": day or None})
        self.steps.append(("add_version", temp))
        self._render()
        self.say(f"버전 '{version}'을 맨 위에 추가했습니다 · [저장]을 누르면 반영됩니다.")
        return True

    def add_item(self):
        line = self.selected_line()
        if line is None or self.page.saving or self._deleted(line):
            return False
        dialog = _FormDialog(self, f"Item 추가 · {line['info']['raw']}", (("Item 내용", ""), ("적용 모델 (쉼표, 비우면 전체)", "")))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        content, models = dialog.values()
        try:
            self._models(models)
        except ValueError as error:
            self.say(f"{error}.", error=True)
            return False
        if not content:
            self.say("Item 내용을 적어 주세요.", error=True)
            return False
        version = line["version"]
        temp = self._next_temp("new-item-")
        before = list(self.orders[version]) if version in self.orders else None
        self.new_items.append({"temp": temp, "release": version, "content": content, "models": models})
        if before is not None:
            self.orders[version] = before + [temp]
        self.steps.append(("add_item", temp, version, before))
        self._render()
        self.say(f"{line['info']['raw']} 맨 뒤에 Item을 추가했습니다 · 순서는 끌어서 바꾸고 [저장]을 누릅니다.")
        return True

    def delete_item(self):
        line = self.selected_line()
        if line is None or line["item"] is None or self.page.saving or self._deleted(line):
            return False
        if line["new_item"] is not None:  # 저장 전 새 Item은 바로 없앤다
            self._drop_new_item(line["new_item"]["temp"])
            self._render()
            return True
        mark = ("item", line["version"], line["item"])
        self.deleted.add(mark)
        self.steps.append(("delete", mark))
        self._render()
        self.say("Item을 삭제 예정으로 표시했습니다 · [저장]을 누르면 지워집니다.")
        return True

    def delete_version(self):
        line = self.selected_line()
        if line is None or self.page.saving or self._deleted(line):
            return False
        version = line["version"]
        if line["info"]["new"]:  # 저장 전 새 버전은 그 Item과 함께 바로 없앤다
            self.new_versions = [v for v in self.new_versions if v["temp"] != version]
            for item in [i for i in self.new_items if i["release"] == version]:
                self._drop_new_item(item["temp"])
            self.orders.pop(version, None)
            self.steps = [s for s in self.steps if not (s[0] == "add_version" and s[1] == version)]
            self._render()
            return True
        mark = ("version", version)
        self.deleted.add(mark)
        self.steps.append(("delete", mark))
        self._render()
        self.say(f"버전 {line['info']['raw']}을 삭제 예정으로 표시했습니다 · [저장]을 누르면 그 Item과 함께 지워집니다.")
        return True

    def _drop_new_item(self, temp):
        self.new_items = [i for i in self.new_items if i["temp"] != temp]
        for version, order in list(self.orders.items()):
            if temp in order:
                self.orders[version] = [i for i in order if i != temp]
        self.steps = [s for s in self.steps if not (s[0] == "add_item" and s[1] == temp)
                      and not (s[0] == "new_edit" and s[1].get("temp") == temp)]

    def move_item(self, source, target):
        """Item 줄을 같은 버전 안의 다른 줄 자리로 옮긴다. [저장]을 누르면 순서(No)를 1부터 다시 매긴다."""
        if self.page.saving or not (0 <= source < len(self.view) and 0 <= target < len(self.view)):
            return False
        moving, place = self.view[source], self.view[target]
        if moving["item"] is None or moving["version"] != place["version"] or self._deleted(moving):
            self.say("Item 순서는 같은 버전 안에서만 바꿀 수 있습니다.", error=True)
            return False
        version = moving["version"]
        order = self._items(version)
        before = list(self.orders[version]) if version in self.orders else None
        position = order.index(place["item"]) if place["item"] is not None else 0
        order.remove(moving["item"])
        order.insert(position, moving["item"])
        self.orders[version] = order
        self.steps.append(("order", version, before))
        self._render()
        self.say("Item 순서를 바꿨습니다 · [저장]을 누르면 No가 1부터 다시 매겨집니다.")
        return True

    # 저장 · 되돌리기 ------------------------------------------------------------------------------
    def changes(self, edits=None):
        """칸 수정 키를 관리 저장 변경으로 바꾼다."""
        grouped = defaultdict(dict)
        for key, value in (self.edits if edits is None else edits).items():
            kind = key[0]
            if kind == "v":
                _, release, field = key
                grouped[("release_version", (("release_version_id", release),))][field] = (
                    date.fromisoformat(value) if field == "released_at" and value else value or None)
            elif kind == "i":
                grouped[("item", (("item_id", key[1]),))]["content"] = value or None
            else:
                _, release, item = key
                grouped[("release_version_item", (("item_id", item), ("release_version_id", release)))].update(self._models(value))
        return [(table, dict(key), changes) for (table, key), changes in grouped.items()]

    def _plan(self):
        deleted_versions = {mark[1] for mark in self.deleted if mark[0] == "version"}
        return {
            "add_versions": [{"temp": v["temp"], "version_raw": v["version_raw"], "released_at": v["released_at"]}
                             for v in self.new_versions],
            "add_items": [{"temp": i["temp"], "release": i["release"], "content": i["content"],
                           "group_ids": self._models(i["models"])["target_group_ids"]}
                          for i in self.new_items if i["release"] not in deleted_versions],
            "orders": {version: [i for i in order if ("item", version, i) not in self.deleted]
                       for version, order in self.orders.items() if version not in deleted_versions},
            "delete_items": sorted((mark[1], mark[2]) for mark in self.deleted
                                   if mark[0] == "item" and mark[1] not in deleted_versions),
            "delete_versions": sorted(deleted_versions),
        }

    def save(self):
        """수정 · 추가 · 삭제 · 순서를 한꺼번에 저장한다. 삭제가 있으면 한 번 묻는다."""
        if not self.pending or self.page.saving or self.family_id is None:
            return False
        if any(not v["version_raw"].strip() for v in self.new_versions) or any(not i["content"].strip() for i in self.new_items):
            self.say("새 버전의 버전명과 새 Item의 내용을 채운 뒤 저장해 주세요.", error=True)
            return False
        try:
            plan = self._plan()
            for version in self.new_versions:
                if version["released_at"]:
                    date.fromisoformat(version["released_at"])
        except ValueError as error:
            self.say(f"{error}. 고친 뒤 다시 저장해 주세요.", error=True)
            return False
        if self.deleted and QMessageBox.question(
                self, "릴리즈노트 삭제",
                f"버전 {len(plan['delete_versions'])}개 · Item {len(plan['delete_items'])}개를 지우고 저장할까요?\n"
                "지운 버전으로 정한 목표 N은 다시 정해야 하고, 위험 지정 연결은 '연결 미확인'이 됩니다. "
                "되돌리기 아이콘(Ctrl+Z)으로 저장 전으로 돌릴 수 있지만 위험 지정 연결은 되살아나지 않습니다.") != QMessageBox.StandardButton.Yes:
            return False
        deleted_versions = set(plan["delete_versions"])
        edits = {k: v for k, v in self.edits.items() if not (k[0] in ("v", "l") and k[1] in deleted_versions)
                 and not (k[0] == "i" and any(m[0] == "item" and m[2] == k[1] for m in self.deleted))
                 and not (k[0] == "l" and ("item", k[1], k[2]) in self.deleted)}
        olds = {k: self._original_of(k) for k in edits}
        family_id, gateway = self.family_id, self.page.gateway
        save_edits = self.page.edit_work(self.changes(edits)) if edits else None
        structural = any(plan[k] for k in plan)

        def work():
            saved = save_edits() if save_edits else None  # 검토 지문 충돌이 가장 잦으므로 칸 수정을 먼저 저장한다
            undo = gateway.data_admin("apply_release", family_id, plan) if structural else None
            return SimpleNamespace(records=getattr(saved, "records", ()), refresh=getattr(saved, "refresh", None), undo=undo)

        counts = (len(edits), len(plan["add_versions"]) + len(plan["add_items"]), len(plan["orders"]),
                  len(plan["delete_versions"]) + len(plan["delete_items"]))
        raws = {r.release_version_id: r.version_raw for r in self.rows}
        contents = {(r.release_version_id, r.item_id): r.content for r in self.rows if r.item_id is not None}
        replay = {"family": family_id, "state": self._state(),
                  "deleted": [("version", raws.get(m[1])) if m[0] == "version" else ("item", raws.get(m[1]), contents.get((m[1], m[2])))
                              for m in self.deleted]}
        return self.page.change(work, self, message="릴리즈노트를 저장하고 있습니다…",
                                done=lambda result: self._saved_all(result, olds, counts, replay))

    def _original_of(self, key):
        for line in self.view:
            for column in range(len(RELEASE_COLUMNS)):
                if self._key(line, column) == key:
                    return self._original(line, column)
        return ""

    def _saved_all(self, result, olds, counts, replay=None):
        self.steps = [s for s in self.steps if s[0] == "saved"]
        self.steps.append(("saved", {"undo": result.undo, "olds": olds, "replay": replay}))
        self.redo_steps = []
        parts = [f"{n}{unit}" for n, unit in zip(counts, ("칸 수정", "줄 추가", "버전 순서 변경", "줄 삭제")) if n]
        self.saved_note = "저장했습니다 · " + " · ".join(parts) + ". "
        self.reload()

    def undo(self):
        """바로 앞 단계 하나를 되돌린다 (저장 뒤에는 마지막 저장 전체)."""
        if not self.steps or self.page.saving:
            return False
        kind, *rest = self.steps[-1]
        if kind != "saved":
            step, before = self.steps.pop(), self._state()
            if kind == "state":
                (state,) = rest
                self._restore(state)
            elif kind == "edit":
                key, previous = rest
                line_value = self._original_of(key)
                if previous == line_value:
                    self.edits.pop(key, None)
                else:
                    self.edits[key] = previous
            elif kind == "new_edit":
                record, field, previous = rest
                record[field] = previous
            elif kind == "add_version":
                (temp,) = rest
                self.new_versions = [v for v in self.new_versions if v["temp"] != temp]
                for item in [i for i in self.new_items if i["release"] == temp]:
                    self._drop_new_item(item["temp"])
            elif kind == "add_item":
                temp, version, before = rest
                self._drop_new_item(temp)
                if before is not None:
                    self.orders[version] = before
            elif kind == "delete":
                (mark,) = rest
                self.deleted.discard(mark)
            elif kind == "order":
                version, before = rest
                if before is None:
                    self.orders.pop(version, None)
                else:
                    self.orders[version] = before
            self.redo_steps.append(("pending", step, before, self._state()))
            self._render()
            self.say("한 단계 되돌렸습니다.")
            return True
        if self.pending:
            self.say("저장하지 않은 변경을 먼저 저장하거나 되돌려 주세요.", error=True)
            return False
        (info,) = rest
        gateway = self.page.gateway
        save_back = self.page.edit_work(self.changes(info["olds"])) if info["olds"] else None

        def work():
            if info["undo"]:
                gateway.data_admin("undo_release", info["undo"])
            saved = save_back() if save_back else None
            return SimpleNamespace(records=getattr(saved, "records", ()), refresh=getattr(saved, "refresh", None))

        return self.page.change(work, self, message="마지막 저장을 되돌리고 있습니다…", done=lambda _: self._undone())

    def _undone(self):
        step = self.steps.pop()
        if step[1].get("replay"):
            self.redo_steps.append(("saved", step[1]["replay"]))
        self.saved_note = "마지막 저장을 되돌렸습니다. "
        self.reload()

    # 다시 실행 ---------------------------------------------------------------------------------
    def _state(self):
        """저장 전 변경 전체 (다시 실행 · 되돌리기용 복사본). 접힘 상태는 넣지 않는다."""
        return deepcopy((self.edits, self.deleted, self.orders, self.new_versions, self.new_items, self._temp))

    def _restore(self, state):
        self.edits, self.deleted, self.orders, self.new_versions, self.new_items, self._temp = deepcopy(state)

    def redo(self):
        """되돌린 단계 하나를 다시 적용한다. 되돌린 저장은 같은 변경으로 다시 저장한다 (지운 줄은 버전명 · 내용으로 다시 찾는다)."""
        if not self.redo_steps or self.page.saving or self.request.busy:
            return False
        kind, *rest = self.redo_steps[-1]
        if kind == "pending":
            step, before, after = rest
            if self._state() != after:
                self.redo_steps = []
                self.say("되돌린 뒤에 다른 내용을 고쳐서 다시 실행할 수 없습니다.", error=True)
                self._sync()
                return False
            self.redo_steps.pop()
            self._restore(before)
            self.steps.append(step)
            self._render()
            self.say("다시 실행했습니다.")
            return True
        (replay,) = rest
        if self.pending:
            self.say("저장하지 않은 변경을 먼저 저장하거나 되돌린 뒤 다시 실행해 주세요.", error=True)
            return False
        if replay["family"] != self.family_id:
            self.redo_steps = []
            self.say("다른 계열을 열어서 되돌린 저장을 다시 실행할 수 없습니다.", error=True)
            self._sync()
            return False
        self.redo_steps.pop()
        versions = {}
        for r in self.rows:
            versions.setdefault(r.version_raw, r.release_version_id)
        items = {(r.release_version_id, r.content): r.item_id for r in self.rows if r.item_id is not None}
        before = self._state()
        self._restore(replay["state"])
        self.deleted = set()
        for mark in replay["deleted"]:
            release = versions.get(mark[1])
            if release is None:
                continue
            if mark[0] == "version":
                self.deleted.add(("version", release))
            elif (release, mark[2]) in items:
                self.deleted.add(("item", release, items[(release, mark[2])]))
        self.steps.append(("state", before))
        self._render()
        self.say("되돌린 저장을 다시 적용하고 있습니다…")
        if not self.save():
            self.say("되돌린 저장을 다시 불러왔습니다 · 확인한 뒤 [저장]을 누르면 반영됩니다.")
        return True

    def _sync(self):
        busy = self.page.saving
        line = self.selected_line()
        live = line is not None and not self._deleted(line)
        count = self._count()
        self.save_button.setText(f"저장 ({count}건)" if count else "저장")
        self.save_button.setEnabled(bool(count) and not busy)
        self.undo_button.setEnabled(bool(self.steps) and not busy)
        self.redo_button.setEnabled(bool(self.redo_steps) and not busy)
        self.add_version_button.setEnabled(self.family_id is not None and not busy)
        self.add_item_button.setEnabled(live and not busy)
        self.delete_item_button.setEnabled(live and line["item"] is not None and not busy)
        self.delete_version_button.setEnabled(live and not busy)
        self.tree.setEnabled(not busy)


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


class _VersionDialog(QDialog):
    """버전 추가: 버전명 · 릴리즈 날짜(리플레이와 같은 달력, '날짜 모름'이면 비움) (11-12)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("버전 추가")
        form = QFormLayout(self)
        self.version = QLineEdit()
        self.version.setPlaceholderText("v4.60.1.7")
        self.version.setMinimumWidth(320)
        self.day = _date_picker(date.today())
        self.unknown = QCheckBox("날짜 모름")
        self.unknown.toggled.connect(self.day.setDisabled)
        day_row = QHBoxLayout()
        day_row.addWidget(self.day, 1)
        day_row.addWidget(self.unknown)
        form.addRow("버전명", self.version)
        form.addRow("릴리즈 날짜", day_row)
        actions = QHBoxLayout()
        cancel, ok = button("취소"), button("추가", primary=True)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(ok)
        form.addRow(actions)

    def values(self):
        day = "" if self.unknown.isChecked() else self.day.date().toPython().isoformat()
        return self.version.text().strip(), day


# --- 변경 이력 -------------------------------------------------------------------------------
class HistoryPanel(_Panel):
    """기본 열(수정 일시 · 구분 · 대상 · 항목 · 수정 전 · 수정 후)만 보이고, 줄을 누르면 오른쪽에서 세부 정보가 나온다 (11-11 4차).

    대시보드 설비 상세와 같은 오른쪽 패널(SideDrawer)을 쓴다. 탭에 들어올 때마다 DB에서 다시 읽는다.
    """

    PERIODS = (("최근 7일", 7), ("최근 30일", 30), ("전체", None))
    KINDS = (("대상 전체", None), ("설비 현황", ("equipment", "equipment_snapshot", "equipment_version", "equipment_pm")),
             ("릴리즈노트", ("release_version", "item", "release_version_item")), ("설비군", ("equipment_group",)),
             ("Site · 라인 · 현황표", ("site", "line", "status_sheet")), ("위험 지정", ("risk_rule",)))
    COLUMNS = ("수정 일시", "구분", "대상", "항목", "수정 전", "수정 후")

    def __init__(self, page):
        super().__init__(page)
        self.entries, self._shown, self.drawer = (), [], None
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
        card.body.addLayout(filters)
        self.table = _table(self.COLUMNS, "변경 이력 (수정 일시 · 대상 · 수정 전후)")
        self.table.setMinimumHeight(560)
        self.table.setWordWrap(False)
        self.page.columns.attach(self.table, "history")
        self.table.itemSelectionChanged.connect(self._open_detail)
        card.body.addWidget(self.table)
        card.body.addWidget(self.notice)

    def attach_drawer(self, parent):
        """대시보드처럼 창 오른쪽에서 나오는 세부 정보 패널을 만든다."""
        from setron.ui.drawer import SideDrawer

        self.drawer = SideDrawer(parent, object_name="historyDetail", accessible_name="변경 세부 정보", panel_width=420)
        layout = self.drawer.panel_layout
        layout.addLayout(self.drawer.header)
        self.detail_title = label("", 20, medium=True, wrap=True)
        self.detail = label("", 13, color=SECONDARY, wrap=True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.before_title, self.after_title = label("수정 전", 13, medium=True), label("수정 후", 13, medium=True)
        self.before_value, self.after_value = label("", 15, wrap=True), label("", 15, medium=True, wrap=True)
        self.before_value.setStyleSheet(f"color:{MUTED.name()};background:#f4f5f8;border-radius:8px;padding:10px;")
        self.after_value.setStyleSheet("background:#fff2c4;border-radius:8px;padding:10px;")
        for widget in (self.detail_title, self.detail, self.before_title, self.before_value, self.after_title, self.after_value):
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(widget)
        layout.addStretch()
        self.drawer.panel_hidden.connect(self.table.clearSelection)

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
        _fill(self.table, [(self._when(e.at), e.kind, e.target, e.field, e.before or "—", e.after) for e in rows],
              list(range(len(rows))))
        for r, entry in enumerate(rows):
            before, after = self.table.item(r, 4), self.table.item(r, 5)
            before.setForeground(QBrush(MUTED))
            after.setBackground(QBrush(SAVING))
            if entry.kind == "삭제":
                after.setForeground(QBrush(QColor(DANGER)))
            font = after.font()
            font.setBold(True)
            after.setFont(font)
        self.table.clearSelection()
        self.table.blockSignals(False)
        self.page.columns.fit(self.table, cap=300)
        self.count.setText(f"{len(rows)}건")

    @staticmethod
    def _when(at):
        """올해 기록은 'MM-DD HH:MM'으로 짧게 (전체 일시는 오른쪽 세부 정보에)."""
        if not at:
            return ""
        return at.strftime("%m-%d %H:%M") if at.year == datetime.now().year else at.strftime("%Y-%m-%d %H:%M")

    def selected_entry(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self._shown[rows[0].row()] if rows and rows[0].row() < len(self._shown) else None

    def _open_detail(self):
        entry = self.selected_entry()
        if entry is None or self.drawer is None:
            return
        self.detail_title.setText(f"{entry.target} · {entry.field}")
        self.detail.setText(f"구분  {entry.kind}\n수정 일시  {_text(entry.at)[:16].replace('T', ' ') or '—'}\n"
                            f"수정 파일  {entry.workbook or '—'}\n시트  {entry.sheet or '—'}\n셀  {entry.cell or '—'}")
        self.before_value.setText(entry.before or "(없음)")
        self.after_value.setText(entry.after or "(없음)")
        self.drawer.show_panel()


# --- 화면 ------------------------------------------------------------------------------------
class DataManagementPage(QWidget):
    """설비현황 · 릴리즈노트 · 변경 이력. DB를 바꾸는 작업은 끝나면 saved로 알려 다른 화면이 다시 읽는다."""

    save_started = Signal()
    saved = Signal(object)
    save_failed = Signal()
    group_added = Signal(object)

    def __init__(self, gateway, *, context_provider=lambda: None, column_store=None, drawer_parent=None):
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
        # 탭 줄은 탭 폭만큼만 둔다: 화면 끝까지 늘이면 아래 회색 줄이 '변경 이력' 뒤로도 이어진다 (11-12)
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        # 고른 탭은 굵은 글씨라 크기 계산보다 넓다. 모자라면 Qt가 ◀ ▶ 스크롤 버튼을 붙여서, 버튼을 끄고 여유 폭을 둔다 (11-12)
        self.tabs.setUsesScrollButtons(False)
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs.setMinimumWidth(self.tabs.sizeHint().width() + 48)
        self.tabs.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        tab_row.addWidget(self.tabs)
        tab_row.addStretch()
        body.addLayout(tab_row)
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
        # [?] '새 위치에 설비 넣기' 가이드: 설비현황의 어느 하위 탭에서도 보이게 하위 탭 줄 오른쪽에 둔다 (11-11)
        self.help_button = button("?")
        self.help_button.setAccessibleName("새 위치에 설비 넣기 가이드")
        self.help_button.setToolTip("새 Site · 라인을 만들고 설비 값까지 입력하는 순서를 봅니다.")
        self.help_button.setFixedSize(34, 34)
        self.help_button.setStyleSheet(f"QPushButton {{background:white;border:1px solid {LINE};border-radius:17px;padding:0;"
                                       "min-width:32px;max-width:32px;min-height:32px;max-height:32px;"
                                       f"color:{PRIMARY};font-size:16px;font-weight:600;}}"
                                       "QPushButton:hover {background:#e8ebfc;border-color:#9aa8f5;}")
        self.help_button.clicked.connect(self.sheet_panel.show_guide)
        sub.layout().addWidget(self.help_button)
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
        # 변경 이력 세부 정보: 대시보드처럼 창 오른쪽에서 나온다 (앱은 창 중앙 영역을, 단독 실행은 이 화면을 쓴다)
        self.history_panel.attach_drawer(drawer_parent if drawer_parent is not None else self)
        self.sheet_panel.attach_guide(drawer_parent if drawer_parent is not None else self)  # 현황표 [?] 가이드
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
        if index != 2 and self.history_panel.drawer is not None:
            self.history_panel.drawer.hide_panel(animated=False)
        if index != 0 and self.sheet_panel.guide is not None:
            self.sheet_panel.guide.hide_panel(animated=False)
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
        # 원래 들어 있던 가져오기 화면의 스크롤 영역에서 꺼낸다. 꺼내지 않으면 그 (숨은) 스크롤 영역이 계속 자기 폭으로
        # 크기를 맞춰서, 이 화면에서는 왼쪽 일부 폭만 쓰고 오른쪽이 비었다 (11-12).
        holder = widget.parentWidget().parentWidget() if widget.parentWidget() is not None else None
        if isinstance(holder, QScrollArea) and holder.widget() is widget:
            holder.takeWidget()
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
    def edit_work(self, changes):
        """[(table, key, {field: value})]를 저장 직전 검토 지문으로 묶어 관리 저장하는 워커 작업을 만든다."""
        gateway, context, requested = self.gateway, deepcopy(self.context_provider()), list(changes)

        def work():
            # 저장 직전의 검토 지문(revision)으로 묶는다. 그 사이 다른 곳에서 바뀌었으면 저장이 거부된다.
            edits = tuple(RecordEdit(table, key, gateway.get(table, key).revision, values) for table, key, values in requested)
            return gateway.save(edits, context) if context is not None else gateway.save(edits)

        return work

    def save_changes(self, changes, owner, *, done=None, failed=None):
        """칸 수정만 관리 저장한다 (워커)."""
        if not changes:
            return False
        return self.change(self.edit_work(changes), owner, done=done, failed=failed)

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
