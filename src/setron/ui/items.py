"""VER5 Item 분석·관리. 원본/판정 조회를 조합하고 명시적 위험 등록은 기존 관리 경계에 위임한다."""

from dataclasses import replace
from html import escape

from PySide6.QtCore import QPoint, QRect, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QMenu, QPushButton, QSizePolicy,
    QStyle, QStyledItemDelegate, QTabBar, QVBoxLayout, QWidget,
)

from setron.infrastructure.excel_reader import open_source_cell
from setron.features.item.schemas import APPLICATION_LABELS, Application, Basis, ItemState
from setron.ui.appearance import ui_color
from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, DisclosureArrow, StatusBadge, button, label
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.item_data import (
    BASIS_LABELS, ItemEquipmentSelection, ItemManagerData, release_note_rows, selected_application,
)
from setron.ui.item_distribution import ItemDistribution
from setron.ui.item_analysis import ItemSearchError, answer_items
from setron.ui.item_answer_card import ItemAnswerCard
from setron.ui.item_original_table import EvidenceDelegate, ItemOriginalTable
from setron.ui.item_risk_page import ItemRiskPage
from setron.ui.query_panel import _WrapRow
from setron.ui.theme import ACCENT_SOFT, INK, PRIMARY, SECONDARY, font, set_widget_font, paint_font
from setron.ui.versions import VersionsPage, _fit
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.source_notice import source_description, observation_description


STORED_STATE_LABELS = {ItemState.APPLIED: "포함", ItemState.NOT_APPLIED: "미포함", ItemState.UNKNOWN: "미확인"}


def _text(text="", size=13):
    widget = label(text, size, color=SECONDARY if size == 13 else None, wrap=True)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


class _ItemResultButton(QPushButton):
    """Keep the title and disclosure icon in fixed slots in all interaction states."""

    rule_requested = Signal(int)

    def __init__(self, content, rules):
        super().__init__()
        self.setAccessibleName(content)
        self.setToolTip(content)
        self.setCheckable(True)
        self.setAutoDefault(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            "QPushButton {background:white;border:0;border-top:1px solid #e5eaf3;"
            "border-bottom:1px solid transparent;border-left:3px solid transparent;border-radius:0;padding:0;min-height:46px;max-height:46px;}"
            "QPushButton:hover {background:#f7f9fc;}"
            "QPushButton:checked {background:#edf0fd;}"
            'QPushButton[keyboardFocus="true"]:focus {border:1px dashed #66708a;border-left-width:3px;}'
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(17, 0, 16, 0)
        self.caption = label(content, 15, medium=True)
        self.caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        arrow = DisclosureArrow()
        for widget in (self.caption, arrow):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.caption, 1)
        self.rule_badge = None
        if rules:
            self.rule_badge = button(f"등록된 기준 {len(rules)}건 ›")
            self.rule_badge.setStyleSheet("QPushButton {background:#edf0fd;color:#3652ca;border:0;border-radius:4px;padding:2px 8px;min-height:23px;max-height:23px;}")
            if len(rules) == 1:
                self.rule_badge.clicked.connect(lambda: self.rule_requested.emit(rules[0].risk_rule_id))
            else:
                menu = QMenu(self.rule_badge)
                for rule in rules:
                    action = menu.addAction(rule.display_name or rule.risk_reason)
                    action.triggered.connect(lambda checked=False, rid=rule.risk_rule_id: self.rule_requested.emit(rid))
                self.rule_badge.setMenu(menu)
            names = "\n".join(rule.display_name or rule.risk_reason for rule in rules)
            self.setToolTip(f"{content}\n{names}")
            self.setAccessibleDescription(f"등록된 위험 기준 {len(rules)}건. {names}")
            self.rule_badge.setToolTip(names)
            row.addWidget(self.rule_badge)
        row.addWidget(arrow)
        self.toggled.connect(arrow.set_expanded)

    def _mark_keyboard_focus(self, enabled):
        if self.property("keyboardFocus") != enabled:
            self.setProperty("keyboardFocus", enabled)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def focusInEvent(self, event):
        self._mark_keyboard_focus(event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                                                     Qt.FocusReason.ShortcutFocusReason))
        super().focusInEvent(event)

    def mousePressEvent(self, event):
        self._mark_keyboard_focus(False)
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        badge_width = self.rule_badge.sizeHint().width() + self.layout().spacing() if self.rule_badge else 0
        available = max(0, self.width() - 64 - badge_width)
        self.caption.setText(self.caption.fontMetrics().elidedText(self.accessibleName(), Qt.TextElideMode.ElideRight, available))


def _style_raw_table(table):
    """Figma 원본 표 양식(#dee0e5 외곽선 · #f4f5f8 머리글 · 12px)을 공통 읽기 표에 덧입힌다."""
    table.setStyleSheet("QTableView {background:white;border:1px solid #dee0e5;border-radius:10px;"
                        "gridline-color:#dee0e5;selection-background-color:#edf0fd;selection-color:#142033;}"
                        "QHeaderView::section {background:#f4f5f8;color:#4a5466;border:0;"
                        "border-bottom:1px solid #dee0e5;padding:10px 12px;font-size:12px;font-weight:600;}"
                        "QTableView::item {padding:8px 12px;border-bottom:1px solid #dee0e5;}"
                        f"QTableView:focus {{border:1px solid {PRIMARY};}}")
    table.verticalHeader().setMinimumSectionSize(38)
    table.horizontalHeader().setMinimumHeight(38)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


class _FlowLayout(QLayout):
    """칩을 너비에 맞춰 줄바꿈한다. 좁은 창에서 가로 스크롤을 만들지 않는다."""

    def __init__(self, parent, spacing):
        super().__init__(parent)
        self.items, self.gap = [], spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect, *, apply):
        lines, current, x = [], [], rect.x()
        for item in self.items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint()
            if current and x + hint.width() > rect.right() + 1:
                lines.append(current)
                current, x = [], rect.x()
            current.append((item, x, hint))
            x += hint.width() + self.gap
        if current:
            lines.append(current)
        y = rect.y()
        for line in lines:
            height = max(hint.height() for _, _, hint in line)
            if apply:
                for item, left, hint in line:
                    item.setGeometry(QRect(QPoint(left, y + (height - hint.height()) // 2), hint))
            y += height + self.gap
        return max(0, y - self.gap - rect.y())


class _ReleaseNoteModel(ReadTableModel):
    """버전별 묶음의 첫 행에만 버전명을 그리도록 묶음 시작 행을 보관한다."""

    def __init__(self, headings):
        super().__init__(headings)
        self.group_starts, self.dates = frozenset(), ()
        self.highlighted = frozenset()

    def set_groups(self, starts, dates):
        self.group_starts, self.dates = frozenset(starts), tuple(dates)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.TextAlignmentRole and orientation == Qt.Orientation.Horizontal:
            return Qt.AlignmentFlag.AlignCenter if section in (1, 3) else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.FontRole:
            return paint_font(12, medium=index.column() == 3)
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in (1, 3):
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 4:
            return ui_color(SECONDARY)
        return super().data(index, role)


class _VersionDelegate(QStyledItemDelegate):
    """버전명(굵게)과 출시일(보조)을 Figma 원본처럼 두 줄로 표시한다. 같은 버전의 후속 행은 비운다."""

    def paint(self, painter, option, index):
        model = index.model()
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, ui_color("#edf0fd"))
        elif model.ids and model.ids[index.row()] in model.highlighted:
            painter.fillRect(option.rect, ui_color("#fff6d2"))
        painter.setPen(ui_color("#dee0e5"))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        if index.row() in model.group_starts:
            rect = option.rect.adjusted(12, 9, -8, -9)
            painter.setPen(ui_color(INK))
            painter.setFont(paint_font(13, medium=True))
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, index.data())
            painter.setPen(ui_color(SECONDARY))
            painter.setFont(paint_font(11))
            painter.drawText(rect.adjusted(0, 19, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                             model.dates[index.row()])
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(150, 56 if index.row() in index.model().group_starts else 38)


def _chip(text):
    chip = QPushButton(text)
    chip.setCheckable(True)
    chip.setCursor(Qt.CursorShape.PointingHandCursor)
    set_widget_font(chip, font(14, medium=True))
    chip.setObjectName("familyChip")
    chip.setFixedHeight(36)
    chip.setStyleSheet("QPushButton#familyChip {background:transparent;border:1px solid transparent;border-radius:10px;"
                       f"padding:0 12px;min-height:34px;max-height:34px;color:{INK};}}"
                       "QPushButton#familyChip:hover {background:#f5f6f8;}"
                       f"QPushButton#familyChip:checked {{background:{ACCENT_SOFT};border-color:{ACCENT_SOFT};color:{PRIMARY};}}"
                       f"QPushButton#familyChip:focus {{border:2px solid {PRIMARY};}}")
    return chip


class ItemsPage(QWidget):
    """메모리 또는 명시적 BackgroundRead 공급자를 사용하는 Item 조회 화면."""

    mode_changed = Signal(str)
    equipment_selected = Signal(object)
    selection_invalidated = Signal()
    answer_navigation = Signal(str, object)
    page_size = 12

    def __init__(self, loader, *, ai_client=None):
        """기존 Item 화면에 읽기 요청 조정자를 연결한다.

        param
        - loader: ItemManagerData 메모리 공급자 또는 워커용 BackgroundRead.
        """
        super().__init__()
        self.loader, self.data = loader, None
        self.ai_client = ai_client
        self.save_feedback = None
        self.last_saved_rule_id = None
        self.restore_detail = None
        self.scoped_detail = False
        self.answer = None
        self.answer_request = ReadRequest(self)
        self.answer_request.completed.connect(self._answer_loaded)
        self.read_request = ReadRequest(self)
        self.read_request.completed.connect(self._loaded)
        self.mode, self.page_index, self.selected_id = "list", 0, None
        self.visible_entries, self.equipment = (), ()
        self.revision, self.selected_equipment_id = 0, None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)
        self.tabs = QTabBar()
        self.tabs.addTab("아이템 조회")
        self.tabs.addTab("위험 기준 관리")
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(True)
        self.tabs.setAccessibleName("아이템 매니저 탭")
        self.tabs.setStyleSheet("QTabBar::tab {background:transparent;color:#66708a;padding:10px 4px;margin-right:24px;"
                               "border:0;border-radius:0;border-bottom:2px solid transparent;font-size:15px;}"
                               f"QTabBar::tab:selected {{background:transparent;color:{PRIMARY};border:0;border-bottom:2px solid {PRIMARY};font-weight:600;}}")
        body.addWidget(self.tabs)
        self.family_id, self.release_rows = None, ()
        self.status = StatusBadge("Item 자료 확인 중", "info")
        self.notice = _text()
        self.status_host = QWidget()
        status_row = QHBoxLayout(self.status_host)
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(12)
        status_row.addWidget(self.status, alignment=Qt.AlignmentFlag.AlignVCenter)
        status_row.addWidget(self.notice, 1)
        self.saved_rule_button = button("등록한 기준 보기")
        self.saved_rule_button.hide()
        self.saved_rule_button.clicked.connect(self.open_saved_rule)
        status_row.addWidget(self.saved_rule_button)
        body.addWidget(self.status_host)
        self.list_view, self.detail_view = QWidget(), QWidget()
        listing, detail = QVBoxLayout(self.list_view), QVBoxLayout(self.detail_view)
        for layout in (listing, detail):
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(16)
        # Figma 08 설비군 선택: 저장된 SW 계열만 칩으로 표시한다.
        card = Card(padding=16)
        card.setProperty("outlined", True)
        card.body.setContentsMargins(16, 12, 16, 12)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.family_title = label("SW 계열", 14, medium=True)
        top.addWidget(self.family_title)
        top.addStretch()
        self.family_host = QWidget()
        self.family_host.setAccessibleName("설비군 선택 항목")
        self.family_flow = _FlowLayout(self.family_host, 8)
        self.family_group = QButtonGroup(self)
        self.family_group.setExclusive(True)
        self.family_group.idClicked.connect(self.select_family)
        self.family_empty = label("", 13, color=SECONDARY, wrap=True)
        self.import_button = button("릴리즈노트 가져오기")
        self.import_button.setFixedHeight(40)
        self.import_button.setToolTip("데이터 가져오기 화면으로 이동합니다.")
        self.import_button.clicked.connect(self.open_import)
        top.addWidget(self.import_button, alignment=Qt.AlignmentFlag.AlignVCenter)
        card.body.addLayout(top)
        # 제목을 선택지와 같은 흐름에 넣지 않아, 줄바꿈 후에도 하나의 선택 영역으로 읽힌다.
        self.family_choices = QFrame()
        self.family_choices.setObjectName("itemFamilyChoices")
        self.family_choices.setStyleSheet("QFrame#itemFamilyChoices {background:transparent;border:0;}")
        choices = QVBoxLayout(self.family_choices)
        choices.setContentsMargins(0, 0, 0, 0)
        choices.addWidget(self.family_host)
        card.body.addWidget(self.family_choices)
        listing.addWidget(card)
        search_panel = card
        bar = QFrame()
        bar.setObjectName("itemSearchBar")
        bar.setStyleSheet(f"QFrame#itemSearchBar {{background:#f9fafb;border:1px solid #d5d7da;border-radius:8px;}}")
        row = QHBoxLayout(bar)
        row.setContentsMargins(18, 8, 8, 8)
        row.setSpacing(12)

        self.search = QLineEdit()
        self.search.setAccessibleName("Item 검색")
        self.search.setPlaceholderText("기능명이나 증상을 입력하세요. 예) Recipe Edit 경고")
        self.search.setFixedHeight(40)
        self.search.setMinimumWidth(0)
        set_widget_font(self.search, font(15))
        self.search.setStyleSheet("QLineEdit {background:transparent;border:0;padding:0;}")
        row.addWidget(self.search, 1)
        self.ai_search = button("검색", primary=True)
        self.ai_search.setFixedSize(80, 40)
        self.ai_search.setToolTip("관련 Item을 찾고 저장된 릴리즈·설비 관측으로 적용 현황을 계산합니다.")
        self.ai_search.clicked.connect(self.search_answer)
        self.search.returnPressed.connect(self.search_answer)
        row.addWidget(self.ai_search)
        search_panel.body.setSpacing(8)
        search_panel.body.addWidget(bar)
        self.setFocusProxy(self.search)
        bar.setFocusProxy(self.search)
        self.search.setClearButtonEnabled(True)
        # Figma 08 릴리즈 노트 원본: 저장된 열(버전·No·내용)과 저장 상태·모델 범위만 표시한다.
        card = Card(padding=24)
        card.body.setContentsMargins(24, 20, 24, 20)
        self.answer_card = ItemAnswerCard()
        self.answer_card.navigate_requested.connect(self.answer_navigation)
        self.answer_toggle = button("검색 분석 보기")
        self.answer_toggle.setCheckable(True)
        self.answer_toggle.toggled.connect(self.answer_card.setVisible)
        self.answer_toggle.hide()
        listing.addWidget(self.answer_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        listing.addWidget(self.answer_card)
        self.original_toggle = button("릴리즈노트 원본 보기")
        self.original_toggle.setCheckable(True)
        self.original_toggle.toggled.connect(card.setVisible)
        self.original_toggle.toggled.connect(lambda opened: self.original_toggle.setText("릴리즈노트 원본 접기" if opened else "릴리즈노트 원본 보기"))
        card.hide()
        original_card = card
        head = QHBoxLayout()
        head.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.release_title = label("릴리즈 노트 Item", 17, medium=True, wrap=True)
        self.release_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.release_subtitle = label("", 12, color=SECONDARY, wrap=True)
        self.release_subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        titles.addWidget(self.release_title)
        titles.addWidget(self.release_subtitle)
        head.addLayout(titles, 1)
        self.ai_notice = label("", 12, color=SECONDARY)
        self.ai_notice.hide()
        head.addWidget(self.ai_notice, alignment=Qt.AlignmentFlag.AlignTop)
        card.body.addLayout(head)
        self.original = ItemOriginalTable()
        self.original.item_selected.connect(self.open_item_id)
        _style_raw_table(self.original.table)
        card.body.addWidget(self.original)
        self.release_note_model = _ReleaseNoteModel(("버전명", "No", "Update 내용", "저장 상태", "모델 범위"))
        self.release_note_table = read_table(self.release_note_model, "릴리즈 노트 Item", (130, 48, 360, 90, 130))
        _style_raw_table(self.release_note_table)
        self.release_note_table.setItemDelegate(EvidenceDelegate(self.release_note_table))
        self.release_note_table.setItemDelegateForColumn(0, _VersionDelegate(self.release_note_table))
        self.release_note_table.clicked.connect(lambda index: self.open_release_row(index.row()))
        self.release_note_table.activated.connect(lambda index: self.open_release_row(index.row()))
        card.body.addWidget(self.release_note_table)
        self.release_note_empty = _text()
        card.body.addWidget(self.release_note_empty)
        card = Card(padding=24)
        self.results_card = card
        card.body.setContentsMargins(24, 20, 24, 20)
        self.count = label("검색 결과", 16, medium=True)
        set_widget_font(self.count, font(18, medium=True))
        card.body.addWidget(self.count)
        self.result_rows = QVBoxLayout()
        self.result_rows.setSpacing(0)
        card.body.addLayout(self.result_rows)
        self.result_buttons = {}
        self.empty = _text()
        card.body.addWidget(self.empty)
        controls = QHBoxLayout()
        self.previous, self.next, self.refresh = button("이전"), button("다음"), button("다시 불러오기")
        self.page_label = label("", 13)
        self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next.clicked.connect(lambda: self.change_page(1))
        self.refresh.clicked.connect(self.reload)
        for widget in (self.previous, self.page_label, self.next):
            controls.addWidget(widget)
        controls.addStretch()
        controls.addWidget(self.refresh)
        card.body.addLayout(controls)
        listing.addWidget(card)
        self.listing = listing
        listing.addWidget(self.detail_view)
        listing.addWidget(self.original_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        listing.addWidget(original_card)
        self.back = button("검색 결과로 돌아가기")
        self.back.clicked.connect(self.back_to_list)

        card = Card()
        card.body.setContentsMargins(24, 20, 24, 20)
        self.title, self.content = _text("", 20), _text("", 14)
        set_widget_font(self.title, font(20, medium=True))
        card.body.setSpacing(8)
        detail_panel = self.full_detail = card
        heading = QHBoxLayout()
        heading.addWidget(self.title, 1)
        heading.addWidget(self.back)
        card.body.addLayout(heading)
        card.body.addWidget(self.content)
        self.inline_box = QFrame()
        self.inline_box.setObjectName("inlineItemFacts")
        self.inline_box.setStyleSheet("QFrame#inlineItemFacts {background:#f7f9fc;border:0;}")
        inline = QVBoxLayout(self.inline_box)
        inline.setContentsMargins(18, 14, 16, 14)
        inline.setSpacing(12)
        facts = QHBoxLayout()
        facts.setSpacing(32)
        self.first_value = label("", 20, medium=True)
        self.missing_value = label("", 20, medium=True)
        for name, value in (("최초 포함 버전", self.first_value), ("미적용 설비", self.missing_value)):
            metric = QVBoxLayout()
            metric.setSpacing(1)
            metric.addWidget(label(name, 12, color=SECONDARY))
            metric.addWidget(value)
            facts.addLayout(metric)
        self.inline_date = label("", 12, color=SECONDARY)
        metric.addWidget(self.inline_date)
        facts.setAlignment(Qt.AlignmentFlag.AlignTop)
        facts.addStretch()
        self.inline_equipment = label("", 14, wrap=True)
        self.inline_equipment.setTextFormat(Qt.TextFormat.RichText)
        self.inline_equipment.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.inline_equipment.setStyleSheet("border-top:1px solid #dbe1ed;padding-top:8px;")
        inline.addLayout(facts)
        inline.addWidget(self.inline_equipment)
        links = QHBoxLayout()
        self.more_button = button("전체 적용 현황")
        self.more_button.setCheckable(True)
        self.more_button.toggled.connect(card.setVisible)
        links.addStretch()
        links.addWidget(self.more_button)
        self.inline_register = button("위험 기준 등록")
        self.inline_register.setProperty("soft", True)
        self.inline_register.clicked.connect(lambda: self.manager.open_add(item_id=self.selected_id, family_id=self.family_id))
        links.addWidget(self.inline_register)
        inline.addLayout(links)
        detail.addWidget(self.inline_box)
        self.source_button = button("버전 · 원본 근거")
        self.source_button.setCheckable(True)
        self.register_rule = button("위험 기준 등록")
        self.register_rule.clicked.connect(lambda: self.manager.open_add(item_id=self.selected_id, family_id=self.family_id))
        detail.addWidget(card)
        evidence = QWidget()
        evidence_body = QVBoxLayout(evidence)
        evidence_body.setContentsMargins(0, 0, 0, 0)
        self.detail_original = ItemOriginalTable()
        evidence_body.addWidget(self.detail_original)
        evidence.hide()
        self.source_button.toggled.connect(evidence.setVisible)
        self.release_model, self.release_table, self.release_note = self._section(
            evidence_body, "포함 릴리즈 · 모델 적용 범위", ("SW 계열", "버전 원문", "확인된 순서", "포함 상태", "모델 범위"),
            (180, 240, 200, 180, 280))
        self.source_model, self.source_table, self.source_note = self._section(
            evidence_body, "Item 출처 · 원본 위치", ("대상 / 필드", "문서 · 리비전", "시트 · 셀", "입력 작업"), (180, 380, 280, 180))
        self.source_table.activated.connect(self.open_source_cell)
        card = detail_panel
        line = QHBoxLayout()
        self.observation_note, self.summary = _text("", 12), _text("", 14)
        line.addWidget(label("설비별 적용 현황", 16, medium=True), 1)
        self.observation_note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        line.addWidget(self.observation_note, 1)
        card.body.addLayout(line)
        card.body.addWidget(self.summary)
        self.state_filter = ComboBox()
        self.state_filter.setAccessibleName("Item 설비 적용 상태")
        self.state_filter.setProperty("queryField", True)
        self.state_filter.setFixedHeight(38)
        self.state_filter.addItem("전체 상태", None)
        for state, name in APPLICATION_LABELS.items():
            self.state_filter.addItem(name, state)
        self.site_filter, self.distribution_view = ComboBox(), ComboBox()
        for widget, name in ((self.site_filter, "Item 적용 Site"), (self.distribution_view, "Item 적용 보기")):
            widget.setAccessibleName(name)
            widget.setProperty("queryField", True)
            widget.setFixedHeight(38)
            widget.setMinimumWidth(0)
            widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.site_filter.addItem("전체 Site", None)
        self.distribution_view.addItem("설비 목록", "list")
        self.distribution_view.addItem("Site · 설비 히트맵", "heatmap")
        card.body.addWidget(_WrapRow([self.site_filter, self.state_filter, self.distribution_view], 200, fields=True))
        self.distribution = ItemDistribution()
        card.body.addWidget(self.distribution)
        self.distribution.scope_selected.connect(self.select_distribution_scope)
        self.distribution.equipment_selected.connect(self.open_equipment)
        self.equipment_model = ReadTableModel(("설비 / ID", "Site", "모델", "적용 상태", "PM별 버전 · 판정 근거"))
        self.equipment_table = read_table(self.equipment_model, "Item 설비별 적용 현황", (200, 120, 180, 120, 470))
        _style_raw_table(self.equipment_table)
        self.equipment_table.clicked.connect(lambda index: self.open_equipment_row(index.row()))
        self.equipment_table.activated.connect(lambda index: self.open_equipment_row(index.row()))
        card.body.addWidget(self.equipment_table)
        self.equipment_empty = _text()
        self.scope_count = _text()
        card.body.addWidget(self.scope_count)
        card.body.addWidget(self.equipment_empty)
        actions = QHBoxLayout()
        actions.addWidget(self.source_button)
        actions.addStretch()
        actions.addWidget(self.register_rule)
        card.body.addLayout(actions)
        detail.addWidget(evidence)
        body.addWidget(self.list_view)
        self.manager = ItemRiskPage()
        _style_raw_table(self.manager.table)
        body.addWidget(self.manager)
        self.tabs.currentChanged.connect(lambda *_: self._set_mode(self.mode))
        self.search.textChanged.connect(self._filter_changed)
        self.state_filter.currentIndexChanged.connect(self._scope_changed)
        self.site_filter.currentIndexChanged.connect(self._scope_changed)
        self.distribution_view.currentIndexChanged.connect(self.render_equipment)
        self.reload()

    def _section(self, layout, title, headings, widths):
        card = Card()
        card.body.setContentsMargins(24, 20, 24, 20)
        card.body.addWidget(label(title, 17, medium=True))
        note = _text()
        model = ReadTableModel(headings)
        table = read_table(model, title, widths)
        _style_raw_table(table)
        card.body.addWidget(note)
        card.body.addWidget(table)
        layout.addWidget(card)
        return model, table, note

    def _set_mode(self, mode):
        self.mode = mode
        self._place_detail()
        analysis = self.tabs.currentIndex() == 0
        for view, visible in ((self.list_view, analysis), (self.detail_view, analysis and mode == "detail"),
                              (self.manager, not analysis)):
            view.setVisible(visible)
            view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred if visible else QSizePolicy.Policy.Ignored)
        self.mode_changed.emit(mode)

    def open_registered_rule(self, rule_id):
        self.tabs.setCurrentIndex(1)
        self.manager.select_rule(rule_id)

    def open_saved_rule(self):
        if self.last_saved_rule_id is not None:
            self.open_registered_rule(self.last_saved_rule_id)

    def refresh_after_save(self, *, preserve_detail=False):
        """저장 전 조회 응답과 상세 선택을 폐기하고 현재 Item 자료를 읽는다."""
        if preserve_detail and self.mode == "detail" and self.tabs.currentIndex() == 0:
            self.restore_detail = (self.selected_id, self.family_id, self.scoped_detail, self.site_filter.currentData(),
                                   self.state_filter.currentData(), self.page_index, self.answer)
        self.read_request.cancel()
        self.reload()

    def reload(self):
        """이전 상세 요청을 무효화하고 중복 실행 없이 자료를 요청한다."""
        if self.read_request.busy:
            return
        self._clear_answer()
        self.save_feedback = None
        self.saved_rule_button.hide()
        self._invalidate_selection()
        self.selected_id, self.equipment = None, ()
        self.title.clear()
        self.content.clear()
        for model in (self.release_model, self.source_model, self.equipment_model):
            model.set_rows(())
        self.data = None
        self.manager.set_data(None)
        self.search.setEnabled(False)
        self.render_families()
        self._filter_changed()
        self.refresh.setEnabled(False)
        self.status.setText("Item 자료 불러오는 중")
        self.status_host.show()
        self.status.set_tone("info")
        self.notice.setText("조회 중입니다. 완료될 때까지 기다려 주세요.")
        self.empty.setText("Item 자료 불러오는 중")
        self.count.setText("조회 중 · 결과 건수는 완료 후 표시합니다.")
        self.read_request.start(self.loader)

    def _loaded(self, data, error):
        """현재 요청 결과에 기존 목록·오류 표시를 적용한다.

        param
        - data: 조회 결과.
        - error: 공급자 실패 또는 None.
        """
        self.refresh.setEnabled(True)
        if error is not None or not isinstance(data, ItemManagerData):
            self.data = None
            self.status.setText("Item 자료 불러오기 실패")
            self.status.set_tone("danger")
            self.notice.setText("자료를 불러오지 못했습니다. 다시 불러오기로 재시도해 주세요. 이전 상세는 표시하지 않습니다.")
        else:
            self.data = data
            self.status.setText("Item 자료 연결됨")
            self.status.set_tone("info")
            self.notice.setText(source_description(data.versions.source_label))
        self.search.setEnabled(self.data is not None)
        self.ai_search.setEnabled(self.data is not None)
        self.manager.set_data(self.data)
        self.status_host.setVisible(self.data is None)
        if self.data is not None and self.save_feedback is not None:
            self.show_save_feedback(*self.save_feedback)
        self.render_families()
        self._filter_changed()
        restore, self.restore_detail = self.restore_detail, None
        if restore and self.data:
            item_id, family, scoped, site, state, page_index, previous_answer = restore
            entry = next((e for e in self.data.versions.items if e.item.item_id == item_id), None)
            if entry and self.family_group.button(family):
                self.select_family(family)
                if previous_answer and previous_answer.question == self.search.text():
                    release_ids = {e.release.release_version_id for e in data.versions.releases if e.release.family_id == family}
                    valid_ids = {r.item_id for r in data.versions.version_items if r.release_version_id in release_ids}
                    ids = tuple(i for i in previous_answer.item_ids if i in valid_ids)
                    self._answer_loaded(replace(previous_answer, data=data, item_ids=ids), None)
                self.page_index = page_index
                self.render_list()
                self._open_entry(entry, scoped=scoped)
                self.site_filter.setCurrentIndex(max(0, self.site_filter.findData(site)))
                self.state_filter.setCurrentIndex(max(0, self.state_filter.findData(state)))

    def show_save_feedback(self, text, tone):
        """저장 실패/미확인 안내가 후속 읽기 응답에 가려지지 않게 보존한다."""
        self.save_feedback = text, tone
        self.saved_rule_button.setVisible(tone == "success" and self.last_saved_rule_id is not None and self.tabs.currentIndex() == 0)
        self.notice.setText(text)
        self.status.setText("저장 완료" if tone == "success" else "저장 결과 확인")
        self.status.set_tone(tone)
        self.status_host.show()

    def render_families(self):
        """전달된 SW 계열만 칩으로 만든다. 자료가 없으면 칩을 만들지 않는다."""
        for chip in self.family_group.buttons():
            self.family_group.removeButton(chip)
        while self.family_flow.count():
            widget = self.family_flow.takeAt(0).widget()
            if widget is not None and widget is not self.family_empty:
                widget.hide()
                widget.deleteLater()
        families = {}
        for pair in (self.data.versions.pairs if self.data else ()):
            families.setdefault(pair.family_id, pair.family_name)
        if self.family_id not in families:
            self.family_id = next(iter(sorted(families)), None)
        for family_id in sorted(families):
            chip = _chip(families[family_id].replace("파운드리", "Foundry"))
            chip.setToolTip(f"SW 계열 ID {family_id} · 이 계열의 릴리즈 노트 Item 보기")
            chip.setChecked(family_id == self.family_id)
            self.family_group.addButton(chip, family_id)
            self.family_flow.addWidget(chip)
        if not families:
            self.family_empty.setText("전달된 SW 계열 없음" if self.data else "조회 결과 미확인")
            self.family_empty.show()
            self.family_flow.addWidget(self.family_empty)
        else:
            self.family_empty.hide()
        self.family_host.updateGeometry()

    def select_family(self, family_id):
        """선택한 SW 계열의 릴리즈 노트 Item만 표시한다."""
        self.family_id = family_id
        self._filter_changed()
        button_ = self.family_group.button(family_id)
        if button_ is not None:
            button_.setChecked(True)

    def _clear_answer(self):
        self.answer_request.cancel()
        self.answer = None
        self.answer_toggle.setChecked(False)
        self.answer_toggle.hide()
        self.answer_card.hide()
        self.ai_search.setEnabled(self.data is not None)

    def search_answer(self):
        if self.data is None or self.family_id is None or self.answer_request.busy:
            return False
        data, family, question, client = self.data, self.family_id, self.search.text(), self.ai_client
        if not question.strip():
            self.answer_card.show_message("검색할 질문을 입력해 주세요.")
            return False
        self.answer = None
        self.answer_card.show_message("관련 Item과 적용 현황을 확인하고 있습니다…", loading=True)
        self.ai_search.setEnabled(False)
        self.answer_request.start(BackgroundRead(lambda: answer_items(data, family, question, client)))
        return True

    def _answer_loaded(self, answer, error):
        self.ai_search.setEnabled(self.data is not None)
        if error is not None or answer is None:
            # 공급자 예외에는 URL/인증 정보가 있을 수 있어 그대로 표시하지 않는다.
            message = str(error) if isinstance(error, ItemSearchError) else "검색하지 못했습니다. 연결을 확인한 뒤 AI 검색으로 다시 시도해 주세요."
            self.answer_card.show_message(message)
            return
        if answer.data is not self.data or answer.family_id != self.family_id or answer.question != self.search.text():
            return
        if self.mode == "detail" and self.selected_id not in answer.item_ids:
            self._invalidate_selection()
            self.selected_id = None
            self._set_mode("list")
        self.answer = answer
        self.page_index = 0
        self.render_list()
        if len(answer.item_ids) == 1:
            self.answer_card.show_answer(answer)
            self.answer_toggle.show()
            self.answer_card.setVisible(self.answer_toggle.isChecked())
        else:
            self.answer_card.hide()
        self.render_release_notes()

    def open_item_id(self, item_id):
        entry = next((e for e in self.data.versions.items if e.item.item_id == item_id), None) if self.data else None
        return self._open_entry(entry) if entry else False

    def render_release_notes(self):
        """원본/관리 행만 표시한다. 누적 적용 판정과 달리 가상의 원본 행을 만들지 않는다."""
        data, family_id = self.data, self.family_id
        query = self.search.text().strip()
        ids = set(self.answer.item_ids) if self.answer is not None else ({e.item.item_id for e in data.search(query)} if data and query else None)
        self.release_rows = release_note_rows(data, family_id, ids) if data and family_id is not None else ()
        names = {p.family_id: p.family_name for p in data.versions.pairs} if data else {}
        name = names.get(family_id)
        self.search.setPlaceholderText(f"{name} 계열에 등록된 릴리즈노트가 없습니다" if name and data and not any(e.release.family_id == family_id for e in data.versions.releases) else "기능명이나 증상을 입력하세요. 예) Recipe Edit 경고")
        self.release_title.setText(f"{name} · 릴리즈 노트 Item" if name else "릴리즈 노트 Item")
        docs = ()
        if data and family_id is not None:
            docs = tuple(dict.fromkeys(s.document for e in data.versions.releases if e.release.family_id == family_id
                                       for s in e.sources if s.document))
        self.release_subtitle.setText(" · ".join(("릴리즈 노트 원본", *(docs or ("출처 문서 미확인",)),
                                                  "저장된 버전별 Item 행 기준 · JIRA·항목·비고 열 미연결")))
        rows, starts, dates, previous = [], [], [], None
        for index, note in enumerate(self.release_rows):
            release = note.release
            if release.release_version_id != previous:
                starts.append(index)
                previous = release.release_version_id
            dates.append(" · ".join(filter(None, (
                release.released_at.isoformat() if release.released_at else "출시일 미확인",
                "순서 미확인" if release.version_order is None else None))))
            if note.row is None:
                rows.append((release.version_raw, "", "저장된 Item 행 없음 · 미포함으로 판정하지 않습니다.", "미확인", ""))
            else:
                item = note.entry.item
                rows.append((release.version_raw, "번호 미확인" if item.item_no is None else str(item.item_no),
                             item.content, STORED_STATE_LABELS[note.row.state], self.data.scope_label(note.row)))
        self.release_note_model.set_groups(starts, dates)
        self.release_note_model.highlighted = frozenset(self.answer.item_ids) if self.answer else frozenset()
        self.release_note_model.set_rows(rows, tuple(n.entry.item.item_id if n.entry else None for n in self.release_rows))
        table = self.release_note_table
        table.resizeRowsToContents()
        height = sum(table.rowHeight(i) for i in range(len(rows)))
        table.setFixedHeight(max(120, min(720, height + table.horizontalHeader().height() + 2 * table.frameWidth() + 2)))
        table.setVisible(bool(rows))
        self.release_note_empty.setVisible(not rows)
        self.release_note_empty.setText(
            "자료 불러오기 실패 · 재시도해 주세요." if data is None else
            "전달된 SW 계열이 없습니다." if family_id is None else
            "검색어와 일치하는 이 계열의 저장 행이 없습니다." if query else
            "이 계열에 전달된 릴리즈가 없습니다. 미포함으로 판정하지 않습니다.")
        linked_ids = {r.item_id for r in data.versions.version_items
                      if r.release_version_id in {e.release.release_version_id for e in data.versions.releases
                                                 if e.release.family_id == family_id}} if data else set()
        originals = tuple(r for r in data.versions.original_rows if r.item_id in linked_ids
                          and (ids is None or r.item_id in ids)) if data else ()
        self.original.set_rows(originals, self.answer.item_ids if self.answer else ())
        if originals:
            table.hide()
            self.release_note_empty.hide()
            self.release_subtitle.setText("보관된 Excel 원본 · 문서별 열 구조 유지")
        else:
            self.release_subtitle.setText("원본 행 미연결 · 현재 저장된 버전·Item 관리값 표시")

    def open_release_row(self, row):
        """릴리즈 노트 행의 Item 상세를 연다. 저장 행이 없는 버전 행은 열지 않는다."""
        if not 0 <= row < len(self.release_rows) or self.release_rows[row].entry is None:
            return False
        return self._open_entry(self.release_rows[row].entry)

    def open_import(self):
        """기존 데이터 가져오기 화면으로 이동한다. 화면이 없으면 버튼을 비활성화한다."""
        window = self.window()
        moved = hasattr(window, "navigate") and getattr(window, "pages", {}).get("import") is not None \
            and window.navigate("import")
        if not moved:
            self.import_button.setEnabled(False)
            self.import_button.setToolTip("데이터 가져오기 화면이 연결되지 않았습니다.")
        return bool(moved)

    def hideEvent(self, event):
        """화면 이탈 시 진행 중 응답을 무효화한다.

        param
        - event: Qt 숨김 이벤트.
        """
        if self.answer_request.busy:
            self._clear_answer()
        if self.read_request.busy:
            self.read_request.cancel()
            self.refresh.setEnabled(True)
            self.status.setText("조회 취소")
            self.notice.setText("다시 불러오기를 눌러 조회해 주세요.")
            self.empty.setText("조회 취소 · 다시 불러오기 가능")
            self.count.setText("조회 취소 · 결과 미확인")
        super().hideEvent(event)

    def _filter_changed(self, *_):
        self._clear_answer()
        self._invalidate_selection()
        self.page_index, self.selected_id = 0, None
        self._set_mode("list")
        self.render_list()
        self.render_release_notes()

    def render_list(self):
        """전달된 Item 목록과 조회 결과 미확인을 구분해 표시한다."""
        query = self.search.text().strip()
        family_name = next((p.family_name for p in self.data.versions.pairs if p.family_id == self.family_id), "") if self.data else ""
        found = ()
        if self.data and self.family_id is not None:
            if self.answer is None:
                found = self.data.search(query, family_id=self.family_id)
            else:
                matched = set(self.answer.item_ids)
                found = tuple(e for e in self.data.search(family_id=self.family_id) if e.item.item_id in matched)
        pages = max(1, (len(found) + self.page_size - 1) // self.page_size)
        self.page_index = max(0, min(self.page_index, pages - 1))
        self.visible_entries = found[self.page_index * self.page_size:(self.page_index + 1) * self.page_size]
        for control in self.result_buttons.values():
            self.result_rows.removeWidget(control)
            control.hide()
            control.deleteLater()
        self.result_buttons.clear()
        rules_by_item = {}
        for rule in self.data.risk_rules if self.data else ():
            if rule.is_enabled and rule.family_id == self.family_id and rule.item_id is not None:
                rules_by_item.setdefault(rule.item_id, []).append(rule)
        for row, entry in enumerate(self.visible_entries):
            control = _ItemResultButton(entry.item.content, rules_by_item.get(entry.item.item_id, ()))
            control.rule_requested.connect(self.open_registered_rule)
            control.clicked.connect(lambda checked=False, i=row: self.toggle_result(i))
            self.result_buttons[entry.item.item_id] = control
            self.result_rows.addWidget(control)
        self._place_detail()
        self.empty.setVisible(not found)
        self.empty.setText("자료를 불러오지 못했습니다. 다시 불러오기를 눌러주세요." if self.data is None else
                           "검색어와 일치하는 아이템이 없습니다." if query else
                           "선택한 계열에 연결된 아이템이 없습니다." if family_name else "등록된 계열이 없습니다.")
        self.count.setText(f"검색 결과 {len(found)}건" if query else f"{family_name} 아이템 전체 {len(found)}건".strip())
        self.page_label.setText(f"{self.page_index + 1} / {pages}")
        if self.data is None:
            self.count.setText("조회 결과 미확인")
        for control in (self.previous, self.next, self.page_label):
            control.setVisible(pages > 1)
        self.refresh.setVisible(self.data is None)
        self.previous.setEnabled(self.page_index > 0)
        self.next.setEnabled(self.page_index + 1 < pages)

    def change_page(self, delta):
        self.page_index += delta
        self._invalidate_selection()
        self._set_mode("list")
        self.selected_id = None
        self.render_list()

    def toggle_result(self, row):
        if self.mode == "detail" and self.visible_entries[row].item.item_id == self.selected_id:
            self.back_to_list()
        else:
            self.open_row(row)

    def _place_detail(self):
        selected = self.result_buttons.get(self.selected_id)
        inline = self.mode == "detail" and selected is not None and not self.results_card.isHidden()
        self.result_rows.removeWidget(self.detail_view)
        self.listing.removeWidget(self.detail_view)
        if inline:
            self.result_rows.insertWidget(self.result_rows.indexOf(selected) + 1, self.detail_view)
        else:
            self.listing.insertWidget(self.listing.indexOf(self.results_card) + 1, self.detail_view)
        for item_id, control in self.result_buttons.items():
            active = inline and item_id == self.selected_id
            control.setChecked(active)
        self.inline_box.setVisible(inline)
        self.full_detail.setVisible(not inline or self.more_button.isChecked())

    def open_row(self, row):
        """선택한 Item의 출처와 명시된 관측 상태를 표시한다.

        param
        - row: 현재 목록의 행 번호.

        return
        - 상세 열기 성공이면 True, 유효한 행이 아니면 False.
        """
        if not 0 <= row < len(self.visible_entries):
            return False
        return self._open_entry(self.visible_entries[row], scoped=True)

    def open_source_cell(self, index):
        refs = getattr(self, "source_refs", ())
        if not index.isValid() or index.row() >= len(refs):
            return
        ref = refs[index.row()]
        if not ref.sheet or not ref.cells:
            self.source_note.setText("이 필드에는 저장된 시트·셀 위치가 없습니다.")
            return
        path, _ = QFileDialog.getOpenFileName(self, f"원본 Excel 선택 · {ref.sheet} · {ref.cells}", "",
                                              "Excel (*.xlsx *.xlsm *.xlsb *.xls)")
        if not path:
            return
        try:
            open_source_cell(path, ref.sheet, ref.cells)
        except Exception:
            self.source_note.setText(f"선택한 파일에서 {ref.sheet} · {ref.cells} 위치를 열 수 없습니다. 원본 파일을 확인하세요.")
        else:
            self.source_note.setText(f"Excel에서 {ref.sheet} · {ref.cells} 위치를 열었습니다.")

    def _open_entry(self, entry, *, scoped=False):
        self.scoped_detail = scoped
        self._invalidate_selection()
        item = entry.item
        self.selected_id = item.item_id
        self.title.setText(item.content)
        self.back.setText("현황 접기")
        self.more_button.setChecked(False)
        self.source_button.setChecked(False)
        catalog = self.data.catalog()
        names = {p.family_id: p.family_name for p in self.data.versions.pairs}
        releases = catalog.item_versions(item.item_id)
        self.release_model.set_rows(tuple((names[r.release.family_id], r.release.version_raw,
            self.data.versions.order_label(r.release),
            "미확인 · 저장 행 없음" if r.source is None else
            {ItemState.APPLIED: "포함", ItemState.NOT_APPLIED: "미포함", ItemState.UNKNOWN: "미확인"}[r.state],
            self.data.scope_label(r.source)) for r in releases))
        self.release_note.setText("동일 계열의 포함 버전 이상 누적 적용 · 순서·모델 범위 미확인은 추정하지 않습니다." if releases else
                                  "연결된 릴리즈가 없습니다. 포함 여부와 적용 범위를 확인할 수 없습니다.")
        self.release_table.setVisible(bool(releases))
        sources = []
        VersionsPage._append_sources(sources, f"Item {item.item_id}", entry.sources)
        self.source_model.set_rows(sources)
        self.source_refs = entry.sources
        self.detail_original.set_rows(tuple(r for r in self.data.versions.original_rows if r.item_id == item.item_id), (item.item_id,))
        self.source_note.setText("출처 행을 두 번 클릭하거나 Enter를 눌러 원본 Excel 파일의 셀을 확인하세요."
                                 if entry.sources else "저장된 원본 위치가 없습니다.")
        rules = [r for r in self.data.risk_rules if r.item_id == item.item_id]
        self.inline_register.setText("기준 추가" if any(r.is_enabled and r.family_id == self.family_id for r in rules) else "위험 기준 등록")
        inclusion = next((f for f in catalog.overview(item, ()).families if f.family_id == self.family_id), None)
        first = inclusion.first_included if inclusion else None
        self.register_rule.setEnabled(self.manager.gateway is not None and inclusion is not None)
        self.content.setText(f"{names.get(self.family_id, '계열 미확인')} · 최초 확인 포함 {first.version_raw if first else '미확인'}" + "".join(f"\n위험 규칙 {r.risk_rule_id} · {r.risk_reason}" for r in rules))
        # 연결이 전혀 없는 Item은 다른 계열의 대상 외라는 판정을 확대하지 않는다.
        connected = self.data.observations_connected and bool(releases)
        records = self.data.records
        if scoped:
            records = tuple(replace(r, pms=tuple(pm for pm in r.pms if pm.family_id == self.family_id))
                            for r in records if any(pm.family_id == self.family_id for pm in r.pms))
        summary = catalog.summarize(item, records) if connected else None
        self.equipment = summary.equipment if summary else ()
        self.inline_register.setEnabled(self.register_rule.isEnabled())
        missing = [e for e in self.equipment if e.application is Application.NOT_APPLIED]
        self.first_value.setText(first.version_raw if first else "미확인")
        self.inline_date.setText(f"{self.data.base_date} 관측" if self.data.observations_connected else "관측 미연결")
        self.missing_value.setText(f"{len(missing)}대" if summary else "미확인")
        lines = []
        for equipment in missing[:3]:
            record = equipment.record
            location = " · ".join(filter(None, (record.site, record.line)))
            lines.append(f"<b>{escape(record.equipment_code)}</b> &nbsp; {escape(location)}")
            reasons = {}
            for pm in equipment.pms:
                if pm.application is Application.NOT_APPLIED:
                    reasons.setdefault((pm.pm.version_raw, pm.basis), []).append(pm.pm.pm_name or "공통 SW")
            for (version, basis), pms in reasons.items():
                observed = f"{', '.join(pms)} · {version or '미확인 버전'} 사용"
                reason = (f"최초 포함 {first.version_raw} 이전" if basis is Basis.BEFORE_INCLUDED and first
                          else "이 버전에 미적용으로 기록돼 있습니다." if basis is Basis.STORED else BASIS_LABELS[basis])
                lines.append(escape(f"{observed} · {reason}"))
        if len(missing) > 3:
            lines.append(f"외 {len(missing) - 3}대 · 전체 적용 현황에서 확인하세요.")
        if summary and summary.counts()[Application.UNKNOWN]:
            lines.append(f"판정 불가 {summary.counts()[Application.UNKNOWN]}대")
        self.inline_equipment.setText("<br>".join(lines) if lines else
                                      "미적용으로 확인된 설비가 없습니다." if summary else "관측 자료 또는 릴리즈 연결이 필요합니다.")
        self.observation_note.setText(f"{self.data.base_date} 관측" if self.data.observations_connected else "설비 관측 자료 미연결")
        self.observation_note.setToolTip(observation_description(self.data))
        self.summary.setText(" · ".join(f"{APPLICATION_LABELS[s]} {n}대" for s, n in summary.counts().items())
                             + f" · HPA 제외 {summary.hpa_excluded}대" if summary else "적용 현황 미확인 · 릴리즈 연결 또는 설비 관측 자료가 필요합니다.")
        with QSignalBlocker(self.state_filter):
            self.state_filter.setCurrentIndex(self.state_filter.findData(Application.NOT_APPLIED) if scoped and self.search.text().strip() else 0)
        with QSignalBlocker(self.site_filter):
            self.site_filter.clear()
            self.site_filter.addItem("전체 Site", None)
            for site in sorted({e.record.site for e in self.equipment}):
                self.site_filter.addItem(site, site)
        self.state_filter.setEnabled(summary is not None)
        self.site_filter.setEnabled(summary is not None)
        self.render_equipment()
        self._set_mode("detail")
        for table in (self.release_table, self.source_table, self.equipment_table):
            _fit(table)
        control = self.result_buttons.get(self.selected_id)
        if control and control.isVisible():
            control.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.back.setFocus()
        return True

    def render_equipment(self, *_):
        state = self._application_filter()
        site = self.site_filter.currentData()
        rows = [e for e in self.equipment if (state is None or e.application == state)
                and (site is None or e.record.site == site)]
        family_names = {p.family_id: p.family_name for p in self.data.versions.pairs} if self.data else {}
        heatmap = self.distribution_view.currentData() == "heatmap"
        self.distribution.set_results(self.equipment if heatmap else (), rows if heatmap else (), self.selected_equipment_id)
        self.distribution.set_scope(site, state)
        self.distribution.setVisible(heatmap)
        self.equipment_model.set_rows(tuple((f"{e.record.equipment_code} · ID {e.record.equipment_id}",
            f"{e.record.site} · {e.record.line or '라인 미지정'}", e.record.model_name, e.label,
            "\n".join(f"{p.pm.pm_name or '일반 SW'} · 계열 {family_names.get(p.pm.family_id, p.pm.family_id)} · "
                      f"{p.pm.version_raw or '버전 미확인'} · "
                      f"{APPLICATION_LABELS[p.application]} ({BASIS_LABELS[p.basis]})" for p in e.pms)
            or "PM/SW 관측 없음 · 업무 규칙상 대상 외") for e in rows), tuple(e.record.equipment_id for e in rows))
        self.scope_count.setText(f"표시 범위: {site if site is not None else '전체 Site'} · "
                                 f"{APPLICATION_LABELS[state] if state else '전체 상태'} / {len(rows)}대 / 전체 {len(self.equipment)}대")
        if self.selected_equipment_id in self.equipment_model.ids:
            self.equipment_table.setCurrentIndex(self.equipment_model.index(self.equipment_model.ids.index(self.selected_equipment_id), 0))
        self.equipment_table.setVisible(bool(rows) and not heatmap)
        self.equipment_empty.setVisible(not rows)
        self.equipment_empty.setText("설비 관측 또는 릴리즈 연결을 확인해 주세요." if not self.state_filter.isEnabled() else
                                     "현재 조건에 해당하는 설비가 없습니다.")
        _fit(self.equipment_table)

    def _invalidate_selection(self):
        self.revision += 1
        self.selected_equipment_id = None
        self.selection_invalidated.emit()

    def _application_filter(self):
        value = self.state_filter.currentData()
        return Application(value) if value is not None else None

    def _scope_changed(self, *_):
        self._invalidate_selection()
        self.render_equipment()

    def select_distribution_scope(self, site, state):
        with QSignalBlocker(self.site_filter), QSignalBlocker(self.state_filter):
            self.site_filter.setCurrentIndex(self.site_filter.findData(site))
            self.state_filter.setCurrentIndex(self.state_filter.findData(state))
        self._scope_changed()

    def is_current_selection(self, request):
        return (isinstance(request, ItemEquipmentSelection) and self.mode == "detail"
                and request.data is self.data and request.revision == self.revision
                and request.item_id == self.selected_id and request.site == self.site_filter.currentData()
                and request.equipment_id == self.selected_equipment_id
                and request.state == self._application_filter() and selected_application(request) is not None)

    def open_equipment_row(self, row):
        if 0 <= row < len(self.equipment_model.ids):
            return self.open_equipment(self.equipment_model.ids[row])
        return False

    def open_equipment(self, equipment_id):
        if self.mode != "detail" or self.data is None:
            return False
        request = ItemEquipmentSelection(self.data, self.revision, self.selected_id, equipment_id,
                                         self.site_filter.currentData(), self._application_filter())
        if selected_application(request) is None:
            return False
        self.revision += 1
        self.selected_equipment_id = equipment_id
        request = ItemEquipmentSelection(self.data, self.revision, self.selected_id, equipment_id,
                                         self.site_filter.currentData(), self._application_filter())
        self.distribution.select_equipment(equipment_id)
        if equipment_id in self.equipment_model.ids:
            self.equipment_table.setCurrentIndex(self.equipment_model.index(self.equipment_model.ids.index(equipment_id), 0))
        self.equipment_selected.emit(request)
        return True

    def back_to_list(self):
        self._invalidate_selection()
        self._set_mode("list")
        if self.selected_id in self.result_buttons:
            self.result_buttons[self.selected_id].setFocus()
        elif self.selected_id in self.release_note_model.ids:
            self.release_note_table.setCurrentIndex(
                self.release_note_model.index(self.release_note_model.ids.index(self.selected_id), 0))
            self.release_note_table.setFocus()
