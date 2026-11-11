"""VER5 Item 분석·관리. 원본/판정 조회를 조합하고 명시적 위험 등록은 기존 관리 경계에 위임한다."""

from dataclasses import replace

from PySide6.QtCore import QRectF, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit, QMenu, QPushButton, QSizePolicy, QSpacerItem,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget,
)

from setron.features.item.schemas import APPLICATION_LABELS, Application, ItemState
from setron.ui.appearance import ui_color
from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, DisclosureArrow, StatusBadge, button, filter_checkbox, label
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.item_data import (
    BASIS_LABELS, ItemEquipmentSelection, ItemManagerData, selected_application,
)
from setron.ui.item_distribution import ItemDistribution
from setron.ui.item_analysis import ItemSearchError, answer_items
from setron.ui.item_answer_card import ItemAnswerCard
from setron.ui.item_risk_dialog import _ClampLabel, warning_icon
from setron.ui.item_risk_page import ItemRiskPage
from setron.ui.item_risk_verdicts import ItemRuleVerdictDialog, category, repeats_item
from setron.ui.query_panel import _WrapRow
from setron.ui.theme import ACCENT_SOFT, ASSETS, INK, LINE, PRIMARY, SECONDARY, TONES, font, icon, set_widget_font
from setron.ui.versions import _fit
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.version_data import family_display_name
from setron.ui.source_notice import source_description


def _text(text="", size=13):
    widget = label(text, size, color=SECONDARY if size == 13 else None, wrap=True)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


class _ItemResultButton(QPushButton):
    """Keep the title and disclosure icon in fixed slots in all interaction states."""

    rule_requested = Signal(int)

    def __init__(self):
        """행 위젯은 한 번만 만들어 목록을 다시 그릴 때마다 set_content로 내용만 바꿔 재사용한다."""
        super().__init__()
        self.setCheckable(True)
        self.setAutoDefault(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.keyboard_focus = False  # 키보드(Tab)로 포커스했을 때만 포커스 링을 그린다(focus-visible).
        self.setStyleSheet(
            "QPushButton {background:white;border:0;border-top:1px solid #e5eaf3;"
            "border-bottom:1px solid transparent;border-left:3px solid transparent;border-radius:0;padding:0;min-height:46px;max-height:46px;}"
            "QPushButton:hover {background:#f5f6f8;}"
            "QPushButton:checked {background:#edf0fd;}"
            "QPushButton:checked:hover {background:#e4e9fb;}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(17, 0, 16, 0)
        row.setSpacing(16)
        self.caption = label("", 15, medium=True, color=INK)
        self.caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.arrow = DisclosureArrow()
        # 같은 제목이 여러 릴리즈에 나올 때 구분하는 작은 보조 텍스트. 고정 슬롯이라 제목 말줄임이 먼저 줄어든다.
        self.version = ""
        self.version_slot_width = 0
        self.display_content = ""
        self.version_label = label("", 15, medium=True, color=INK)
        self.version_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        for widget in (self.caption, self.version_label, self.arrow):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.version_label)
        row.addWidget(self.caption, 1)
        # 부모가 없는 위젯에 setVisible(True)를 하면 빈 최상위 창이 잠깐 뜨므로, 보이기는 레이아웃에 넣은 뒤에 정한다.
        self.version_label.hide()
        self._badge = button("")
        self._badge.setIcon(warning_icon())
        self._badge.setStyleSheet(f"QPushButton {{background:{TONES['warning'][0]};color:{TONES['warning'][1]};border:1px solid {TONES['warning'][2]};border-radius:4px;padding:2px 8px;min-height:23px;max-height:23px;}}")
        self._single_rule = None
        self._badge.clicked.connect(self._badge_clicked)
        row.addWidget(self._badge)
        self._badge.hide()
        row.addWidget(self.arrow)
        self.rule_badge = None  # 위험 기준이 있는 행에서만 배지 위젯을 가리킨다.
        self.toggled.connect(self.arrow.set_expanded)

    def _badge_clicked(self):
        if self._single_rule is not None:
            self.rule_requested.emit(self._single_rule)

    def set_content(self, content, rules, version="", version_tip="", *, display_content=None,
                    show_version=True, version_width=None):
        self.setAccessibleName(content)
        self.display_content = content if display_content is None else display_content
        # 긴 제목은 한 줄로 줄이고 전체 제목을 툴팁으로 보여 준다. 릴리즈 정보도 같은 툴팁 하나에 합친다.
        self.setToolTip(content + (f"\n{version_tip}" if version_tip else ""))
        self.setAccessibleDescription(f"포함 릴리즈 {version}" if version else "")
        self.version = version
        metrics = self.version_label.fontMetrics()
        self.version_slot_width = (min(168, metrics.horizontalAdvance(version) + 2) if version else 0) if version_width is None else version_width
        self.version_label.setFixedWidth(self.version_slot_width)
        self.version_label.setText(metrics.elidedText(version, Qt.TextElideMode.ElideMiddle, self.version_slot_width) if show_version else "")
        self.version_label.setVisible(self.version_slot_width > 0)
        self._single_rule = rules[0].risk_rule_id if len(rules) == 1 else None
        self._badge.setMenu(None)
        if rules:
            self._badge.setText(f"위험 기준 {len(rules)}건 ›")
            if len(rules) > 1:
                menu = QMenu(self._badge)
                for rule in rules:
                    action = menu.addAction(rule.display_name or rule.risk_reason)
                    action.triggered.connect(lambda checked=False, rid=rule.risk_rule_id: self.rule_requested.emit(rid))
                self._badge.setMenu(menu)
            names = "\n".join(rule.display_name or rule.risk_reason for rule in rules)
            # 부모 행과 배지가 같은 기준명을 각자 툴팁으로 갖고 있으면 배지 위에서 두 번 뜬다.
            # 행 툴팁은 Item 내용만, 기준명은 배지 툴팁 하나로만 보여 준다.
            self.setAccessibleDescription((f"포함 릴리즈 {version}. " if version else "") + f"등록된 위험 기준 {len(rules)}건. {names}")
            self._badge.setToolTip(names)
        self._badge.setVisible(bool(rules))
        self.rule_badge = self._badge if rules else None
        self.setChecked(False)
        self._elide()

    def _mark_keyboard_focus(self, enabled):
        if self.keyboard_focus != enabled:
            self.keyboard_focus = enabled
            self.update()

    def focusInEvent(self, event):
        self._mark_keyboard_focus(event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                                                     Qt.FocusReason.ShortcutFocusReason))
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._mark_keyboard_focus(False)
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        self._mark_keyboard_focus(False)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.keyboard_focus and self.hasFocus():
            # 점선 테두리 대신 둥근 모서리의 실선 링. 스타일시트 테두리를 건드리지 않아 레이아웃이 밀리지 않는다.
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(PRIMARY), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 6, 6)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        layout = self.layout()
        margins = layout.contentsMargins()
        badge_width = self._badge.sizeHint().width() if self.rule_badge else 0
        version_width = self.version_slot_width
        gaps = 1 + bool(self.rule_badge) + bool(version_width)
        available = max(0, self.width() - margins.left() - margins.right() - self.arrow.width()
                        - badge_width - version_width - gaps * layout.spacing() - 3)
        self.caption.setText(self.caption.fontMetrics().elidedText(self.display_content, Qt.TextElideMode.ElideRight, available))


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


class _PageBar(QWidget):
    """« ‹ 1 2 3 4 5 … 54 › » 형태의 번호 페이지 이동. 번호 슬롯은 최대 7개라 좁은 창에서도 한 줄에 들어간다.

    '…'를 누르면 해당 방향으로 10페이지 이동한다. 입력 칸이나 팝업 없이 54쪽 같은 긴 목록에서도
    몇 번 눌러 가까이 갈 수 있고, 키보드 포커스·접근성 흐름이 번호 버튼과 같다.
    """

    page_requested = Signal(int)  # 0부터 시작하는 페이지
    slots = 7
    jump = 10

    def __init__(self):
        super().__init__()
        self.current, self.total = 0, 1
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.first = self._make("«", "첫 페이지", lambda: self.page_requested.emit(0))
        self.previous = self._make("‹", "이전 페이지", lambda: self.page_requested.emit(self.current - 1))
        self.numbers = [self._make("", "", None) for _ in range(self.slots)]
        self.next = self._make("›", "다음 페이지", lambda: self.page_requested.emit(self.current + 1))
        self.last = self._make("»", "마지막 페이지", lambda: self.page_requested.emit(self.total - 1))
        for widget in (self.first, self.previous, *self.numbers, self.next, self.last):
            row.addWidget(widget)
        for slot in self.numbers:
            slot.clicked.connect(lambda checked=False, s=slot: self.page_requested.emit(s.target))
            slot.hide()
        self.setStyleSheet(
            f"QPushButton#pageButton {{background:transparent;border:1px solid transparent;border-radius:8px;color:{INK};"
            "padding:0;min-width:34px;max-width:60px;min-height:34px;max-height:34px;}"
            "QPushButton#pageButton:hover {background:#f5f6f8;}"
            f"QPushButton#pageButton[current=\"true\"] {{background:{ACCENT_SOFT};border-color:{ACCENT_SOFT};color:{PRIMARY};}}"
            "QPushButton#pageButton:disabled {color:#a0a8b8;}"
            f"QPushButton#pageButton:focus {{border:2px solid {PRIMARY};}}"
        )

    def _make(self, text, name, handler):
        widget = QPushButton(text)
        widget.setObjectName("pageButton")
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        set_widget_font(widget, font(14, medium=True))
        widget.setAccessibleName(name)
        widget.setToolTip(name)
        widget.target = 0
        if handler is not None:
            widget.clicked.connect(handler)
        return widget

    def window_tokens(self):
        """표시할 번호 목록. 정수는 1부터 시작하는 페이지, 'L'/'R'은 왼쪽/오른쪽 말줄임."""
        total, page = self.total, self.current + 1
        if total <= self.slots:
            return list(range(1, total + 1))
        if page <= 4:
            return [1, 2, 3, 4, 5, "R", total]
        if page >= total - 3:
            return [1, "L", *range(total - 4, total + 1)]
        return [1, "L", page - 1, page, page + 1, "R", total]

    def set_state(self, current, total):
        self.current, self.total = current, max(1, total)
        tokens = self.window_tokens()
        self.setVisible(self.total > 1)
        for slot, token in zip(self.numbers, tokens + [None] * (self.slots - len(tokens))):
            slot.setVisible(token is not None)
            if token is None:
                continue
            is_gap = token in ("L", "R")
            if is_gap:
                slot.target = max(1, self.current - self.jump) if token == "L" else min(self.total - 2, self.current + self.jump)
                slot.setText("…")
                name = f"{self.jump}페이지 {'앞' if token == 'L' else '뒤'}로 이동"
                slot.setAccessibleName(name)
                slot.setToolTip(name)
            else:
                slot.target = token - 1
                slot.setText(str(token))
                slot.setAccessibleName(f"{token}페이지로 이동")
                slot.setToolTip(f"{token}페이지로 이동" + (" (현재)" if token - 1 == self.current else ""))
            current_slot = not is_gap and token - 1 == self.current
            slot.setProperty("current", current_slot)
            slot.setAccessibleDescription("현재 페이지" if current_slot else "")
            slot.style().unpolish(slot)
            slot.style().polish(slot)
        self.first.setEnabled(self.current > 0)
        self.previous.setEnabled(self.current > 0)
        self.next.setEnabled(self.current + 1 < self.total)
        self.last.setEnabled(self.current + 1 < self.total)


class _FamilyItemDelegate(QStyledItemDelegate):
    """계열 항목을 같은 위치와 글꼴로 그리며, 자료가 없는 항목의 글자색을 구분한다."""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget).adjusted(2, 0, -2, 0)
        text = opt.fontMetrics.elidedText(opt.text, opt.textElideMode, max(0, rect.width()))
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        brush = index.data(Qt.ItemDataRole.ForegroundRole)
        selected = opt.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver)
        color = brush.color().name() if brush is not None else (PRIMARY if selected else INK)
        painter.save()
        painter.setPen(ui_color(color))
        painter.setFont(opt.font)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)
        painter.restore()


class _Toolbar(QWidget):
    """양쪽 도구를 한 줄에 두고, 폭이 모자라면 오른쪽 도구를 아래 줄로 내린다."""

    gap = 16
    search_minimum = 200

    def __init__(self, leading, trailing, *, wrap_trailing=False):
        super().__init__()
        self.leading, self.trailing, self.stacked = leading, trailing, None
        self.wrap_trailing = wrap_trailing
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(self.gap)
        self.grid.setVerticalSpacing(12)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._arrange(False)

    def minimumSizeHint(self):
        # 한 줄 폭을 부모 최소 폭으로 강제하면 줄바꿈이 일어나지 않는다.
        return QSize(0, self.grid.minimumSize().height())

    def _arrange(self, stacked):
        if self.stacked == stacked:
            return
        self.grid.removeWidget(self.leading)
        self.grid.removeWidget(self.trailing)
        self.grid.setColumnStretch(0, 0)
        self.grid.setColumnStretch(1, 0)
        if stacked:
            self.grid.addWidget(self.leading, 0, 0, 1, 2)
            if self.wrap_trailing:
                self.grid.addWidget(self.trailing, 1, 0, 1, 2)
            else:
                self.grid.addWidget(self.trailing, 1, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
        else:
            self.grid.addWidget(self.leading, 0, 0)
            if self.wrap_trailing:
                self.grid.addWidget(self.trailing, 0, 1)
            else:
                self.grid.addWidget(self.trailing, 0, 1, Qt.AlignmentFlag.AlignRight)
            self.grid.setColumnStretch(1, 1)
        self.stacked = stacked
        self.updateGeometry()

    def resizeEvent(self, event):
        def natural_width(widget):
            if widget.isHidden():
                return 0
            if isinstance(widget, _WrapRow):
                visible = [w for w in widget.widgets if not w.isHidden()]
                return (sum(max(w.minimumWidth(), w.sizeHint().width()) for w in visible)
                        + widget.grid.horizontalSpacing() * max(0, len(visible) - 1))
            return max(widget.minimumWidth(), widget.sizeHint().width())

        trailing_width = natural_width(self.trailing) if self.wrap_trailing else max(self.trailing.width(), self.search_minimum)
        leading_width = natural_width(self.leading)
        stacked = not self.trailing.isHidden() and event.size().width() < leading_width + self.gap + trailing_width
        self.grid.setColumnMinimumWidth(0, 0 if stacked else leading_width)
        self._arrange(stacked)
        super().resizeEvent(event)


class _ContentWrapRow(_WrapRow):
    """각 도구의 실제 폭을 기준으로 읽을 수 있는 줄 수를 고른다."""

    def resizeEvent(self, event):
        widths = [max(widget.minimumWidth(), widget.sizeHint().width()) for widget in self.widgets]
        for columns in range(len(widths), 0, -1):
            needed = max(sum(widths[start:start + columns]) + self.grid.horizontalSpacing() * (len(widths[start:start + columns]) - 1)
                         for start in range(0, len(widths), columns))
            if needed <= event.size().width() or columns == 1:
                self._arrange(columns)
                break
        QWidget.resizeEvent(self, event)


class _RightWrapRow(_ContentWrapRow):
    """상태 배지를 읽는 순서대로 두되 각 줄의 오른쪽 끝을 맞춘다."""

    def _arrange(self, columns):
        if self.columns == columns:
            return
        super()._arrange(columns)
        self.grid.setColumnStretch(columns, 0)
        self.grid.setColumnStretch(0, 1)
        for index, widget in enumerate(self.widgets):
            self.grid.removeWidget(widget)
            row_start = index // columns * columns
            row_count = min(columns, len(self.widgets) - row_start)
            self.grid.addWidget(widget, index // columns, columns - row_count + index % columns + 1)

class ItemsPage(QWidget):
    """메모리 또는 명시적 BackgroundRead 공급자를 사용하는 Item 조회 화면."""

    mode_changed = Signal(str)
    equipment_selected = Signal(object)
    selection_invalidated = Signal()
    answer_navigation = Signal(str, object)
    page_size = 12
    SEARCH_PLACEHOLDER = "기능명·증상 검색"

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
        self.verdict_dialog = None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)
        self.family_id = None
        self.family_item_counts = {}
        self.status = StatusBadge("Item 자료 확인 중", "info")
        self.notice = _text()
        self.status_host = QWidget()
        status_row = QHBoxLayout(self.status_host)
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(12)
        status_row.addWidget(self.status, alignment=Qt.AlignmentFlag.AlignVCenter)
        status_row.addWidget(self.notice, 1)
        body.addWidget(self.status_host)
        self.list_view, self.detail_view = QWidget(), QWidget()
        listing, detail = QVBoxLayout(self.list_view), QVBoxLayout(self.detail_view)
        for layout in (listing, detail):
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(16)
        detail.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        # SW 계열은 드롭다운으로 고른다. 계열 수가 늘어도 툴바 높이가 변하지 않는다.
        self.family_combo = ComboBox()
        set_widget_font(self.family_combo, font(14))
        set_widget_font(self.family_combo.view(), font(14))
        self.family_combo.setAccessibleName("SW 계열")
        self.family_combo.setProperty("queryField", True)
        self.family_combo.setFixedSize(280, 38)
        self.family_combo.setMaxVisibleItems(10)
        self.family_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        caret = (ASSETS / "caret-down.svg").as_posix()
        self.family_combo.setStyleSheet("QComboBox {padding-right:28px;} QComboBox::drop-down {border:0;width:28px;}"
                                        f"QComboBox::down-arrow {{image:url({caret});width:12px;height:12px;}}")
        self.family_combo.setItemDelegate(_FamilyItemDelegate(self.family_combo))
        self.family_combo.currentIndexChanged.connect(self._family_combo_changed)
        # 돋보기 또는 Enter로 검색 분석을 실행한다.
        self.search = QLineEdit()
        self.search.setObjectName("itemSearchBar")
        self.search.setAccessibleName("Item 검색")
        self.search.setPlaceholderText(self.SEARCH_PLACEHOLDER)
        self.search.setFixedSize(280, 38)
        set_widget_font(self.search, font(14))
        self.search.setStyleSheet(f"QLineEdit {{background:white;border:1px solid {LINE};border-radius:10px;padding:0 10px;}}"
                                  f"QLineEdit:focus {{border:2px solid {PRIMARY};}}")
        self.ai_search = self.search.addAction(icon("b2c35.svg"), QLineEdit.ActionPosition.LeadingPosition)
        self.ai_search.setText("검색")
        self.ai_search.setToolTip("관련 Item을 찾고 저장된 릴리즈·설비 관측으로 적용 현황을 계산합니다.")
        self.ai_search.triggered.connect(self.search_answer)
        self.search.returnPressed.connect(self.search_answer)
        self.setFocusProxy(self.search)
        self.search.setClearButtonEnabled(True)
        self.answer_card = ItemAnswerCard()
        self.answer_card.navigate_requested.connect(self.answer_navigation)
        self.answer_toggle = button("검색 분석 보기")
        self.answer_toggle.setCheckable(True)
        self.answer_toggle.toggled.connect(self.answer_card.setVisible)
        self.answer_toggle.hide()
        listing.addWidget(self.answer_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        listing.addWidget(self.answer_card)
        card = Card(padding=24)
        self.results_card = card
        card.body.setContentsMargins(24, 20, 24, 20)
        card.body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        card.setMaximumHeight(16777215)
        self.count = label("검색 결과", 16, medium=True)
        set_widget_font(self.count, font(18, medium=True))
        self.count.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.registered_only = filter_checkbox("위험 등록된 Item")
        self.registered_only.toggled.connect(self._registered_filter_changed)
        self.query_tools = _RightWrapRow([self.registered_only, self.family_combo, self.search], 0)
        self.query_tools.grid.setSpacing(12)
        self.toolbar = _Toolbar(self.count, self.query_tools, wrap_trailing=True)
        self.toolbar.setObjectName("itemToolbar")
        card.body.addWidget(self.toolbar)
        setTabOrder = QWidget.setTabOrder
        setTabOrder(self.registered_only, self.family_combo)
        setTabOrder(self.family_combo, self.search)
        # 선택한 계열에 릴리즈노트가 없으면 결과 자리에 이 안내 한 블록만 두고 검색·필터는 끈다.
        self.family_notice = QFrame()
        self.family_notice.setObjectName("familyNotice")
        self.family_notice.setStyleSheet("QFrame#familyNotice {background:#f7f9fc;border:1px solid #e5eaf3;border-radius:8px;}")
        notice_column = QVBoxLayout(self.family_notice)
        notice_column.setContentsMargins(18, 16, 18, 16)
        notice_column.setSpacing(4)
        self.family_notice_title = label("", 15, medium=True, wrap=True)
        self.family_notice_hint = label("데이터 가져오기에서 릴리즈노트를 가져오면 여기에 표시됩니다.", 13, color=SECONDARY, wrap=True)
        notice_column.addWidget(self.family_notice_title)
        notice_column.addWidget(self.family_notice_hint)
        self.family_notice.hide()
        card.body.addWidget(self.family_notice)
        self.result_host = QWidget()
        self.result_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.result_rows = QVBoxLayout(self.result_host)
        self.result_rows.setContentsMargins(0, 0, 0, 0)
        self.result_rows.setSpacing(0)
        self.result_rows.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.page_filler = QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        self.result_rows.addSpacerItem(self.page_filler)
        card.body.addWidget(self.result_host)
        self.result_buttons = {}
        self.row_pool = []
        self.empty = _text()
        card.body.addWidget(self.empty)
        controls = QHBoxLayout()
        self.refresh = button("다시 불러오기")
        self.page_total = 1
        self.page_bar = _PageBar()
        self.page_bar.page_requested.connect(self.go_to_page)
        # 기존 이름 유지: 이전/다음/처음/끝 버튼.
        self.first_page, self.previous, self.next, self.last_page = (
            self.page_bar.first, self.page_bar.previous, self.page_bar.next, self.page_bar.last)
        self.refresh.clicked.connect(self.reload)
        controls.addWidget(self.page_bar)
        controls.addStretch()
        controls.addWidget(self.refresh)
        card.body.addLayout(controls)
        listing.addWidget(card)
        self.listing = listing
        listing.addWidget(self.detail_view)
        self.back = button("검색 결과로 돌아가기")
        self.back.clicked.connect(self.back_to_list)

        card = Card()
        card.body.setContentsMargins(24, 20, 24, 20)
        self.content_rules = _text("", 12)
        card.body.setSpacing(8)
        self.full_detail = card
        heading = QHBoxLayout()
        self.title = _text("", 17)
        heading.addWidget(self.title, 1)
        heading.addWidget(self.back)
        card.body.addLayout(heading)
        card.body.addWidget(self.content_rules)
        self.inline_box = QFrame()
        self.inline_box.setObjectName("inlineItemFacts")
        self.inline_box.setStyleSheet("QFrame#inlineItemFacts {background:#f7f9fc;border:0;}")
        inline = QVBoxLayout(self.inline_box)
        inline.setContentsMargins(18, 16, 16, 14)
        inline.setSpacing(12)
        inline.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        tones = {Application.APPLIED: "success", Application.NOT_APPLIED: "danger", Application.UNKNOWN: "warning"}
        self.summary_badges = {state: StatusBadge("", tones.get(state, "neutral")) for state in APPLICATION_LABELS}
        self.hpa_badge = StatusBadge("")
        self.summary = _RightWrapRow([*self.summary_badges.values(), self.hpa_badge], 96)
        self.summary.grid.setSpacing(6)
        # 등록된 위험 기준은 펼침 영역 맨 위의 강조 박스에 둔다(옅은 주의색 배경 + 왼쪽 포인트 라인).
        self.rules_box = QFrame()
        self.rules_box.setObjectName("registeredRules")
        self.rules_box.setStyleSheet(f"QFrame#registeredRules {{background:#fff9e6;border:0;border-left:3px solid {TONES['warning'][2]};border-radius:4px;}}")
        rules_row = QHBoxLayout(self.rules_box)
        rules_row.setContentsMargins(12, 8, 12, 8)
        rules_row.setSpacing(8)
        self.rules_chip = StatusBadge("", "warning")
        self.inline_rules = _ClampLabel(1)
        set_widget_font(self.inline_rules, font(13, medium=True))
        # 기준 종류 칩(환경안전 필수 등)은 이름 앞에 두고, 이름은 Item 제목과 다를 때만 오른쪽에 보인다.
        self.rules_kinds = QWidget()
        self.rules_kinds.setStyleSheet("background:transparent;")
        self.rules_kinds_row = QHBoxLayout(self.rules_kinds)
        self.rules_kinds_row.setContentsMargins(0, 0, 0, 0)
        self.rules_kinds_row.setSpacing(6)
        rules_row.addWidget(self.rules_chip)
        rules_row.addWidget(self.rules_kinds)
        rules_row.addWidget(self.inline_rules, 1)
        rules_row.addStretch(0)
        self.rules_box.hide()
        inline.addWidget(self.rules_box)
        detail.addWidget(self.inline_box)
        self.register_rule = button("위험 기준 등록")
        self.register_rule.clicked.connect(lambda: self.manager.open_add(item_id=self.selected_id, family_id=self.family_id))
        detail.addWidget(self.full_detail)
        self.application_details = QWidget()
        application_body = QVBoxLayout(self.application_details)
        application_body.setContentsMargins(0, 0, 0, 0)
        application_body.setSpacing(8)
        application_body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.full_detail.body.addWidget(self.application_details)
        self.application_heading = label("설비별 적용 현황", 16, medium=True)
        # 자료가 없을 때는 필터·집계 대신 이 안내 한 블록만 보인다.
        self.no_data_notice = QFrame()
        self.no_data_notice.setObjectName("noDataNotice")
        self.no_data_notice.setStyleSheet("QFrame#noDataNotice {background:#f7f9fc;border:1px solid #e5eaf3;border-radius:8px;}")
        notice = QVBoxLayout(self.no_data_notice)
        notice.setContentsMargins(16, 14, 16, 14)
        notice.setSpacing(4)
        self.no_data_title = label("적용 현황을 계산할 수 없습니다", 14, medium=True, wrap=True)
        self.no_data_reason = label("", 12, color=SECONDARY, wrap=True)
        notice.addWidget(self.no_data_title)
        notice.addWidget(self.no_data_reason)
        application_body.addWidget(self.no_data_notice)
        self.state_filter = ComboBox()
        self.state_filter.setAccessibleName("Item 설비 적용 상태")
        self.state_filter.setProperty("queryField", True)
        self.state_filter.setFixedHeight(32)
        self.state_filter.addItem("전체 상태", None)
        for state, name in APPLICATION_LABELS.items():
            self.state_filter.addItem(name, state)
        self.site_filter = ComboBox()
        self.site_filter.setAccessibleName("Item 적용 Site")
        self.site_filter.setProperty("queryField", True)
        self.site_filter.setFixedHeight(32)
        self.site_filter.setMinimumWidth(0)
        self.site_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.site_filter.addItem("전체 Site", None)
        # 설비 상세 복귀에서 참조하는 보기 상태는 유지하되, 화면에서는 설비 목록을 기본으로 제공한다.
        self.distribution_view = ComboBox(self.application_details)
        self.distribution_view.setAccessibleName("Item 적용 보기")
        self.distribution_view.addItem("설비 목록", "list")
        self.distribution_view.addItem("Site · 설비 히트맵", "heatmap")
        self.distribution_view.hide()
        for control in (self.site_filter, self.state_filter):
            set_widget_font(control, font(14))
            set_widget_font(control.view(), font(14))
            control.setStyleSheet("QComboBox {padding-right:28px;} QComboBox::drop-down {border:0;width:28px;}"
                                 f"QComboBox::down-arrow {{image:url({caret});width:12px;height:12px;}}")
            control.currentTextChanged.connect(control.setToolTip)
        self.filter_row = _ContentWrapRow([self.application_heading, self.site_filter, self.state_filter], 184)
        self.application_toolbar = _Toolbar(self.filter_row, self.summary, wrap_trailing=True)
        application_body.insertWidget(0, self.application_toolbar)
        self.distribution = ItemDistribution()
        application_body.addWidget(self.distribution)
        self.distribution.scope_selected.connect(self.select_distribution_scope)
        self.distribution.equipment_selected.connect(self.open_equipment)
        self.equipment_model = ReadTableModel(("설비", "Site", "Line", "모델", "적용 상태", "버전", "비고"))
        self.equipment_table = read_table(self.equipment_model, "Item 설비별 적용 현황", (180, 90, 90, 120, 100, 200, 310))
        _style_raw_table(self.equipment_table)
        self.equipment_table.clicked.connect(lambda index: self.open_equipment_row(index.row()))
        self.equipment_table.activated.connect(lambda index: self.open_equipment_row(index.row()))
        application_body.addWidget(self.equipment_table)
        self.equipment_empty = _text()
        application_body.addWidget(self.equipment_empty)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.register_rule)
        application_body.addLayout(actions)
        body.addWidget(self.list_view)
        # 위험 기준 관리 탭은 없앴다. ItemRiskPage는 화면에 붙이지 않고 저장 경계(gateway)·등록 팝업 호출만 맡는다.
        self.manager = ItemRiskPage()
        self.manager.setParent(self)
        self.manager.hide()
        self.search.textChanged.connect(self._filter_changed)
        self.state_filter.currentIndexChanged.connect(self._scope_changed)
        self.site_filter.currentIndexChanged.connect(self._scope_changed)
        self.distribution_view.currentIndexChanged.connect(self.render_equipment)
        self.reload()

    def _set_mode(self, mode):
        self.mode = mode
        self.detail_view.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Minimum if mode == "detail" else QSizePolicy.Policy.Ignored)
        self._place_detail()
        self.detail_view.setVisible(mode == "detail")
        self.mode_changed.emit(mode)

    def open_registered_rule(self, rule_id):
        """'위험 기준 N건' 배지: 그 Item의 위험 기준 판정 팝업을 연다. Item 상세와 '전체 적용 현황'은 따로 유지한다."""
        rule = next((r for r in self.data.risk_rules if r.risk_rule_id == rule_id), None) if self.data else None
        if rule is None or rule.item_id is None:
            return False
        self.verdict_dialog = ItemRuleVerdictDialog(self.window(), self.data, rule.item_id, rule_id)
        self.verdict_dialog.exec()
        return True

    def refresh_after_save(self, *, preserve_detail=False):
        """저장 전 조회 응답과 상세 선택을 폐기하고 현재 Item 자료를 읽는다."""
        if preserve_detail and self.mode == "detail":
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
        self._invalidate_selection()
        self.selected_id, self.equipment = None, ()
        self.title.clear()
        self.equipment_model.set_rows(())
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
        self.count.setText("검색 결과 확인 중")
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
            if entry and self.family_combo.findData(family) >= 0:
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
        self.notice.setText(text)
        self.status.setText("저장 완료" if tone == "success" else "저장 결과 확인")
        self.status.set_tone(tone)
        self.status_host.show()

    def render_families(self):
        """전달된 SW 계열만 드롭다운 항목으로 만든다. 자료가 없으면 안내 한 줄만 두고 비활성화한다."""
        families = {}
        for pair in (self.data.versions.pairs if self.data else ()):
            families.setdefault(pair.family_id, pair.family_name)
        # 이미 불러온 릴리즈·Item 연결만 한 번 훑어 계열별 Item 수를 얻는다(추가 DB 조회 없음).
        self.family_item_counts = self._family_item_counts()
        if self.data is not None and self.family_id not in families:
            ordered = sorted(families)
            self.family_id = next((fid for fid in ordered if self.family_item_counts.get(fid, 0)),
                                  next(iter(ordered), None))
        combo = self.family_combo
        with QSignalBlocker(combo):
            combo.clear()
            for family_id in sorted(families):
                combo.addItem(family_display_name(families[family_id]), family_id)
                index = combo.count() - 1
                empty = self.family_item_counts.get(family_id, 0) == 0
                combo.setItemData(index, f"SW 계열 ID {family_id} · " + ("연결된 릴리즈노트 Item 없음" if empty
                                                                      else "이 계열의 릴리즈 노트 Item 보기"),
                                  Qt.ItemDataRole.ToolTipRole)
                if empty:  # 자료 없는 계열은 연하게. 선택은 가능하다.
                    combo.setItemData(index, QBrush(QColor(SECONDARY)), Qt.ItemDataRole.ForegroundRole)
            if families:
                combo.setCurrentIndex(combo.findData(self.family_id))
            else:
                combo.addItem("전달된 SW 계열 없음" if self.data else "조회 결과 미확인", None)
                combo.setCurrentIndex(0)
        combo.setEnabled(bool(families))
        combo.setToolTip("" if families else combo.currentText())
        # 가장 긴 계열 이름이 목록에서 잘리지 않게 한다. 항목이 많으면 목록은 최대 높이 안에서 스크롤된다.
        widest = max((combo.fontMetrics().horizontalAdvance(combo.itemText(i)) for i in range(combo.count())), default=0)
        combo.view().setMinimumWidth(max(combo.width(), widest + 48))

    def _family_item_counts(self):
        """계열별 연결 Item 수. 메모리의 릴리즈·Item 연결만 사용한다."""
        if not self.data:
            return {}
        family_of = {e.release.release_version_id: e.release.family_id for e in self.data.versions.releases}
        linked = {}
        for row in self.data.versions.version_items:
            family = family_of.get(row.release_version_id)
            if family is not None:
                linked.setdefault(family, set()).add(row.item_id)
        return {pair.family_id: len(linked.get(pair.family_id, ())) for pair in self.data.versions.pairs}

    def _included_releases(self):
        """선택 계열의 포함 릴리즈를 한 번 모아 표시와 정렬에 함께 사용한다."""
        if not self.data:
            return {}
        key = (id(self.data), self.family_id)
        if getattr(self, "_release_cache_key", None) != key:
            releases = {e.release.release_version_id: e.release for e in self.data.versions.releases
                        if e.release.family_id == self.family_id}
            found, fallback = {}, {}
            for row in self.data.versions.version_items:
                release = releases.get(row.release_version_id)
                if release is not None:
                    (found if row.state is ItemState.APPLIED else fallback).setdefault(row.item_id, {})[release.release_version_id] = release
            self._release_cache_key = key
            self._release_cache = {item_id: sorted((found.get(item_id) or fallback[item_id]).values(),
                                                   key=lambda r: (r.version_order is None, r.version_order or 0, r.release_version_id))
                                   for item_id in {*found, *fallback}}
            self._linked_versions = {item_id: {r.version_raw for r in (*found.get(item_id, {}).values(), *fallback.get(item_id, {}).values())}
                                     for item_id in {*found, *fallback}}
        return self._release_cache

    def _release_caption(self, item_id):
        """선택한 계열에서 Item이 속한 릴리즈를 (대표 버전 '외 N', 전체 목록 툴팁)으로 만든다.

        이미 불러온 release_version_item·release_version 연결만 쓴다. 대표는 확인된 순서가 가장 빠른(최초 포함) 릴리즈다.
        """
        releases = self._included_releases().get(item_id, ())
        if not releases:
            return "", ""
        first = releases[0]
        text = first.version_raw + (f" 외 {len(releases) - 1}" if len(releases) > 1 else "")
        lines = [f"{r.version_raw} · 출시일 {r.released_at or '미확인'}" for r in releases[:8]]
        if len(releases) > 8:
            lines.append(f"… 외 {len(releases) - 8}개")
        # 행 오른쪽 버전은 '최초 포함 버전'이 아니라 이 Item이 들어 있는 릴리즈다. 툴팁 첫 줄에 그 뜻을 밝힌다.
        return text, (f"포함 릴리즈: {text}\n" + "\n".join(lines)) if len(releases) > 1 else f"포함 릴리즈: {lines[0]}"

    def _family_combo_changed(self, index):
        family_id = self.family_combo.itemData(index)
        if family_id is not None and family_id != self.family_id:
            self.select_family(family_id)

    def select_family(self, family_id):
        """선택한 SW 계열의 릴리즈 노트 Item만 표시한다."""
        self.family_id = family_id
        index = self.family_combo.findData(family_id)
        if index >= 0 and index != self.family_combo.currentIndex():
            with QSignalBlocker(self.family_combo):
                self.family_combo.setCurrentIndex(index)
        self._filter_changed()

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

    def open_item_id(self, item_id):
        entry = next((e for e in self.data.versions.items if e.item.item_id == item_id), None) if self.data else None
        return self._open_entry(entry) if entry else False

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

    def _registered_filter_changed(self, *_):
        """체크박스는 검색 분석 결과는 두고 목록만 다시 거른다."""
        self._invalidate_selection()
        self.page_index, self.selected_id = 0, None
        self._set_mode("list")
        self.render_list()

    def render_list(self):
        """전달된 Item 목록과 조회 결과 미확인을 구분해 표시한다."""
        query = self.search.text().strip()
        family_name = next((family_display_name(p.family_name) for p in self.data.versions.pairs if p.family_id == self.family_id), "") if self.data else ""
        found = ()
        if self.data and self.family_id is not None:
            if self.answer is None:
                found = self.data.search(query, family_id=self.family_id)
            else:
                matched = set(self.answer.item_ids)
                found = tuple(e for e in self.data.search(family_id=self.family_id) if e.item.item_id in matched)
        rules_by_item = {}
        for rule in self.data.risk_rules if self.data else ():
            if rule.is_enabled and rule.family_id == self.family_id and rule.item_id is not None:
                rules_by_item.setdefault(rule.item_id, []).append(rule)
        registered_only = self.registered_only.isChecked()
        if registered_only:
            found = tuple(e for e in found if e.item.item_id in rules_by_item)
        releases = self._included_releases()

        def latest_included(entry):
            included = releases.get(entry.item.item_id, ())
            order = included[0].version_order if included else None
            return order is None, -order if order is not None else 0

        # 같은 대표 버전은 원래 Item 순서를 보존하고, 순서를 모르는 버전은 마지막에 둔다.
        found = tuple(sorted(found, key=latest_included))
        pages = max(1, (len(found) + self.page_size - 1) // self.page_size)
        self.page_index = max(0, min(self.page_index, pages - 1))
        self.visible_entries = found[self.page_index * self.page_size:(self.page_index + 1) * self.page_size]
        # 행 위젯은 풀에서 재사용한다(생성·스타일 계산 비용이 행당 약 7ms였다). 남는 행은 숨긴다.
        self.result_buttons.clear()
        release_captions = [self._release_caption(entry.item.item_id) for entry in self.visible_entries]
        version_width = None
        previous_release = object()
        for row, entry in enumerate(self.visible_entries):
            if row == len(self.row_pool):
                control = _ItemResultButton()
                control.rule_requested.connect(self.open_registered_rule)
                control.clicked.connect(lambda checked=False, c=control: self.toggle_result(c.row))
                self.row_pool.append(control)
                self.result_rows.insertWidget(self.result_rows.count() - 1, control)
            control = self.row_pool[row]
            control.row = row
            version, version_tip = release_captions[row]
            if version_width is None:
                metrics = control.version_label.fontMetrics()
                version_width = min(168, max((metrics.horizontalAdvance(text) + 2 for text, _ in release_captions if text), default=0))
            included = releases.get(entry.item.item_id, ())
            representative = included[0].release_version_id if included else None
            show_version = representative != previous_release
            previous_release = representative
            content = entry.item.content
            caption = content
            for raw in self._linked_versions.get(entry.item.item_id, ()):
                suffix = f"({raw})"
                if content.rstrip().endswith(suffix):
                    caption = content.rstrip()[:-len(suffix)].rstrip() or content
                    break
            control.set_content(content, rules_by_item.get(entry.item.item_id, ()), version, version_tip,
                                display_content=caption, show_version=show_version, version_width=version_width)
            control.show()
            self.result_buttons[entry.item.item_id] = control
        for control in self.row_pool[len(self.visible_entries):]:
            control.hide()
            control.setChecked(False)
        row_height = self.row_pool[0].sizeHint().height() if self.row_pool else 0
        # 짧은 마지막 페이지의 여백만 예약한다. 상세 높이는 실제 레이아웃에서 그대로 더한다.
        missing_rows = self.page_size - len(self.visible_entries) if pages > 1 else 0
        self.page_filler.changeSize(0, missing_rows * row_height,
                                    QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        self.result_rows.invalidate()
        self.result_host.setVisible(bool(found))
        self._place_detail()
        no_release = bool(self.data and self.family_id is not None and family_name
                          and not any(e.release.family_id == self.family_id for e in self.data.versions.releases))
        self.family_notice.setVisible(no_release)
        self.family_notice_title.setText(f"{family_name}에 연결된 릴리즈노트 아이템이 없습니다.")
        for control in (self.search, self.ai_search, self.registered_only):
            control.setEnabled(not no_release)
        self.empty.setVisible(not found and not no_release)
        self.empty.setText("자료를 불러오지 못했습니다. 다시 불러오기를 눌러주세요." if self.data is None else
                           "위험 기준이 등록된 아이템이 없습니다." if registered_only and not query else
                           "검색어와 일치하는 아이템이 없습니다." if query else
                           "선택한 계열에 연결된 아이템이 없습니다." if family_name else "등록된 계열이 없습니다.")
        self.count.setText(f"검색 결과 {len(found)}건")
        self.page_total = pages
        if self.data is None:
            self.count.setText("검색 결과 미확인")
        self.page_bar.set_state(self.page_index, pages)
        self.refresh.setVisible(self.data is None)

    def go_to_page(self, index):
        """0부터 시작하는 페이지로 이동한다. 범위를 벗어난 값은 처음·끝으로 보정한다."""
        index = max(0, min(index, self.page_total - 1))
        if index != self.page_index:
            self.change_page(index - self.page_index)

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
        detail_parent = self.inline_box if inline else self.full_detail
        if self.application_details.parentWidget() is not detail_parent:
            detail_parent.layout().addWidget(self.application_details)
        self.inline_box.setVisible(inline)
        self.full_detail.setVisible(not inline)
        self.application_details.setVisible(self.mode == "detail")

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

    def _open_entry(self, entry, *, scoped=False):
        self.scoped_detail = scoped
        self._invalidate_selection()
        item = entry.item
        self.selected_id = item.item_id
        self.title.setText(item.content)
        self.back.setText("현황 접기")
        catalog = self.data.catalog()
        releases = catalog.item_versions(item.item_id)
        rules = [r for r in self.data.risk_rules if r.item_id == item.item_id]
        register_text = "위험 기준 추가" if any(r.is_enabled and r.family_id == self.family_id for r in rules) else "위험 기준 등록"
        self.register_rule.setText(register_text)
        inclusion = next((f for f in catalog.overview(item, ()).families if f.family_id == self.family_id), None)
        self.register_rule.setEnabled(self.manager.gateway is not None and inclusion is not None)
        rule_names = [n for n in dict.fromkeys(r.display_name or r.risk_reason for r in rules) if not repeats_item(n, item.content)]
        # 제목과 같은 문구를 되풀이하지 않도록 위험 기준 이름은 제목과 다를 때만 별도 보조 줄에 둔다.
        self.content_rules.setVisible(bool(rule_names))
        self.content_rules.setText("위험 기준 · " + ", ".join(rule_names))
        # 연결이 전혀 없는 Item은 다른 계열의 대상 외라는 판정을 확대하지 않는다.
        connected = self.data.observations_connected and bool(releases)
        records = self.data.records
        if scoped:
            records = tuple(replace(r, pms=tuple(pm for pm in r.pms if pm.family_id == self.family_id))
                            for r in records if any(pm.family_id == self.family_id for pm in r.pms))
        summary = catalog.summarize(item, records) if connected else None
        self.equipment = summary.equipment if summary else ()
        enabled_rules = [r for r in rules if r.is_enabled and r.family_id == self.family_id]
        self.rules_box.setVisible(bool(enabled_rules))
        if enabled_rules:
            self.rules_chip.setText(f"위험 기준 {len(enabled_rules)}건 등록됨")
            distinct = [n for n in dict.fromkeys(r.display_name or r.risk_reason for r in enabled_rules)
                        if not repeats_item(n, item.content)]
            self.inline_rules.setVisible(bool(distinct))
            self.inline_rules.set_full_text(" · ".join(distinct))
            while self.rules_kinds_row.count():
                self.rules_kinds_row.takeAt(0).widget().deleteLater()
            for kind in dict.fromkeys(category(r) for r in enabled_rules):
                self.rules_kinds_row.addWidget(StatusBadge(kind, "info"))
            if any(r.memo.strip() for r in enabled_rules):
                self.rules_kinds_row.addWidget(StatusBadge("메모 있음"))
        if summary:
            for state, count in summary.counts().items():
                self.summary_badges[state].setText(f"{APPLICATION_LABELS[state]} {count}대")
            self.hpa_badge.setText(f"HPA 제외 {summary.hpa_excluded}대")
            self.summary.minimum_width = max(badge.sizeHint().width() for badge in self.summary.widgets)
        self.summary.setVisible(summary is not None)
        self.no_data_notice.setVisible(summary is None)
        self.no_data_reason.setText(
            "설비 관측 자료가 없어 설비별 적용 여부를 판단할 수 없습니다. 위험 기준 등록은 그대로 할 수 있습니다."
            if not self.data.observations_connected else
            "이 Item과 연결된 릴리즈가 없어 설비별 적용 여부를 판단할 수 없습니다. 위험 기준 등록은 그대로 할 수 있습니다.")
        self.site_filter.setVisible(summary is not None)
        self.state_filter.setVisible(summary is not None)
        with QSignalBlocker(self.state_filter):
            self.state_filter.setCurrentIndex(self.state_filter.findData(Application.NOT_APPLIED) if scoped and self.search.text().strip() else 0)
        with QSignalBlocker(self.site_filter):
            self.site_filter.clear()
            self.site_filter.addItem("전체 Site", None)
            for site in sorted({e.record.site for e in self.equipment}):
                self.site_filter.addItem(site, site)
        self.state_filter.setEnabled(summary is not None)
        self.site_filter.setEnabled(summary is not None)
        for control, minimum_width in ((self.site_filter, 144), (self.state_filter, 152)):
            control.setMinimumWidth(0)
            control.setMaximumWidth(16777215)
            control.ensurePolished()
            text_width = max((control.fontMetrics().horizontalAdvance(control.itemText(i))
                              for i in range(control.count())), default=0)
            # QSS의 오른쪽 padding(28px)과 드롭다운 영역(28px)은 각각 공간을 차지한다.
            required = text_width + 12 + 28 + 28 + 8
            hint = max(control.sizeHint().width(), control.minimumSizeHint().width())
            control.setFixedWidth(max(minimum_width, required, hint))
        self.render_equipment()
        self._set_mode("detail")
        _fit(self.equipment_table)
        control = self.result_buttons.get(self.selected_id)
        if control and not self.inline_box.isHidden():
            control.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.back.setFocus()
        return True

    def render_equipment(self, *_):
        state = self._application_filter()
        site = self.site_filter.currentData()
        rows = [e for e in self.equipment if (state is None or e.application == state)
                and (site is None or e.record.site == site)]
        heatmap = self.distribution_view.currentData() == "heatmap"
        self.distribution.set_results(self.equipment if heatmap else (), rows if heatmap else (), self.selected_equipment_id)
        self.distribution.set_scope(site, state)
        self.distribution.setVisible(heatmap)
        self.equipment_model.set_rows(tuple((e.record.equipment_code, e.record.site, e.record.line or "미지정",
            e.record.model_name, e.label,
            "\n".join(f"{p.pm.pm_name or '일반 SW'} · {p.pm.version_raw or '버전 미확인'}" for p in e.pms)
            or "버전 미확인",
            "\n".join(f"{p.pm.pm_name or '일반 SW'} · "
                      f"{APPLICATION_LABELS[p.application]} ({BASIS_LABELS[p.basis]})" for p in e.pms)
            or "PM/SW 관측 없음 · 업무 규칙상 대상 외") for e in rows), tuple(e.record.equipment_id for e in rows))
        if self.selected_equipment_id in self.equipment_model.ids:
            self.equipment_table.setCurrentIndex(self.equipment_model.index(self.equipment_model.ids.index(self.selected_equipment_id), 0))
        self.equipment_table.setVisible(bool(rows) and not heatmap)
        available = self.state_filter.isEnabled()
        self.equipment_empty.setVisible(available and not rows)
        self.equipment_empty.setText("현재 조건에 해당하는 설비가 없습니다.")
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
