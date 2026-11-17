"""모든 업무 화면에서 공유하는 연도 → 월 달력과 날짜 입력 칸."""

from PySide6.QtCore import QAbstractListModel, QDate, QEvent, QLocale, QModelIndex, Qt, Signal
from PySide6.QtGui import QFont, QTextCharFormat
from PySide6.QtWidgets import (
    QCalendarWidget, QComboBox, QDateEdit, QHBoxLayout, QStyle, QStyleOptionComboBox, QTableView, QToolButton, QWidget,
)

from setron.ui.appearance import manager, set_color_style, ui_color
from setron.ui.combo_box import ComboBox
from setron.ui.theme import ACCENT_SOFT, ASSETS, INK, LINE, PRIMARY, SECONDARY, SOFT


class _Years(QAbstractListModel):
    """최대 9999년을 항목 객체 수천 개 없이 스크롤 가능한 목록으로 제공한다."""

    def __init__(self, parent):
        super().__init__(parent)
        self.first, self.last = 1, 9999

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else max(0, self.last - self.first + 1)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < self.rowCount():
            return None
        year = self.first + index.row()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return f"{year:04d}년"
        if role == Qt.ItemDataRole.UserRole:
            return year
        return None

    def set_range(self, first, last):
        if (first, last) != (self.first, self.last):
            self.beginResetModel()
            self.first, self.last = first, last
            self.endResetModel()


class CalendarWidget(QCalendarWidget):
    """네이티브 날짜 표 위에 연도·월 드롭다운을 같은 순서로 배치한다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("날짜 선택 달력")
        self.setLocale(QLocale(QLocale.Language.Korean, QLocale.Country.SouthKorea))
        self.setFirstDayOfWeek(Qt.DayOfWeek.Sunday)
        self.setDateRange(QDate(1, 1, 1), QDate(9999, 12, 31))
        self.setNavigationBarVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)   # 주 번호(5·6·7…) 열 숨김
        self.header = QWidget(self)
        self.header.setObjectName("calendarHeader")
        row = QHBoxLayout(self.header)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)
        self.previous_button, self.next_button = QToolButton(), QToolButton()
        for control, arrow, caption in ((self.previous_button, Qt.ArrowType.LeftArrow, "이전 달"),
                                        (self.next_button, Qt.ArrowType.RightArrow, "다음 달")):
            control.setArrowType(arrow)
            control.setAccessibleName(caption)
            control.setToolTip(caption)
            control.setFixedSize(28, 32)
        self.year_combo, self.month_combo = ComboBox(), ComboBox()
        self.years = _Years(self)
        self.year_combo.setObjectName("calendarYear")
        self.month_combo.setObjectName("calendarMonth")
        self.year_combo.setAccessibleName("연도 선택 · 목록에서 스크롤")
        self.month_combo.setAccessibleName("월 선택")
        self.year_combo.setToolTip("연도를 선택하세요. 목록에서 스크롤하거나 숫자로 이동할 수 있습니다.")
        for control in (self.year_combo, self.month_combo):
            control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            control.setMaxVisibleItems(10)
            control.setFixedHeight(32)
            control.view().setUniformItemSizes(True)
            control.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.year_combo.setMinimumContentsLength(6)
        self.year_combo.setMinimumWidth(108)
        self.month_combo.setMinimumContentsLength(3)
        self.month_combo.setMinimumWidth(76)
        self.year_combo.setModel(self.years)
        for month in range(1, 13):
            self.month_combo.addItem(f"{month}월", month)
        for control in (self.previous_button, self.year_combo, self.month_combo, self.next_button):
            row.addWidget(control, 1 if control in (self.year_combo, self.month_combo) else 0)
        self.layout().insertWidget(0, self.header)
        arrow = (ASSETS / "import-ver5/ba8e3.svg").as_posix()
        self.setStyleSheet(
            f"QCalendarWidget {{background:white;color:{INK};border:1px solid {LINE};border-radius:10px;}}"
            f"QCalendarWidget QAbstractItemView {{selection-background-color:{ACCENT_SOFT};selection-color:{PRIMARY};}}"
        )
        self.header.setStyleSheet(
            "QWidget#calendarHeader {background:transparent;}"
            f"QWidget#calendarHeader QComboBox {{background:{SOFT};color:{INK};border:1px solid {LINE};"
            "border-radius:8px;padding:0 24px 0 8px;}"
            f"QWidget#calendarHeader QComboBox:hover {{border-color:{PRIMARY};}}"
            f"QWidget#calendarHeader QComboBox:focus {{border:2px solid {PRIMARY};}}"
            "QWidget#calendarHeader QComboBox::drop-down {border:0;width:22px;}"
            f"QWidget#calendarHeader QComboBox::down-arrow {{image:url({arrow});width:16px;height:16px;}}"
            f"QComboBox QAbstractItemView {{background:white;color:{INK};}}"
            f"QWidget#calendarHeader QToolButton {{background:{SOFT};color:{SECONDARY};border:1px solid {LINE};border-radius:8px;}}"
            f"QWidget#calendarHeader QToolButton:hover, QWidget#calendarHeader QToolButton:focus "
            f"{{background:{ACCENT_SOFT};border-color:{PRIMARY};}}"
            f"QWidget#calendarHeader QToolButton:disabled {{background:transparent;border-color:transparent;color:{SECONDARY};}}"
        )
        days = self.findChild(QTableView, "qt_calendar_calendarview")
        for view in (days, self.year_combo.view(), self.month_combo.view()):
            set_color_style(view, lambda colors: (
                f"selection-background-color:{colors['selected']};selection-color:{colors['accent']};"))
        appearance = manager()
        if appearance is not None:
            appearance.changed.connect(self._sync_day_colors)
        self._sync_day_colors()
        self.previous_button.clicked.connect(self.showPreviousMonth)
        self.next_button.clicked.connect(self.showNextMonth)
        self.year_combo.currentIndexChanged.connect(self._choose_page)
        self.month_combo.currentIndexChanged.connect(self._choose_page)
        self.currentPageChanged.connect(self._sync_header)
        self._sync_header()

    def _sync_day_colors(self):
        weekdays = QTextCharFormat()
        weekdays.setForeground(ui_color(SECONDARY))
        weekdays.setFontWeight(QFont.Weight.Medium)
        self.setHeaderTextFormat(weekdays)
        weekend = QTextCharFormat()
        weekend.setForeground(ui_color(PRIMARY))
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self.setWeekdayTextFormat(day, weekend)

    def minimumSizeHint(self):
        # Qt 기본 크기는 숨긴 네이티브 탐색 막대만 계산한다. 추가한 연/월 헤더 높이도 반영한다.
        size = super().minimumSizeHint()
        if hasattr(self, "header") and self.header.parentWidget() is self:
            header = self.header.sizeHint()
            size.setWidth(max(size.width(), header.width()))
            size.setHeight(size.height() + header.height())
        return size

    def sizeHint(self):
        return self.minimumSizeHint()

    def take_navigation_header(self):
        """기간 팝업이 연·월 도구를 날짜 필드 위에 배치할 때 기존 연결을 유지한다."""
        self.layout().removeWidget(self.header)
        self.header.setParent(None)
        self.updateGeometry()
        return self.header

    def _sync_header(self, *_):
        if not hasattr(self, "year_combo"):
            return
        controls = (self.year_combo, self.month_combo)
        blocked = [control.blockSignals(True) for control in controls]
        try:
            self.years.set_range(max(1, self.minimumDate().year()), min(9999, self.maximumDate().year()))
            year, month = self.yearShown(), self.monthShown()
            self.year_combo.setCurrentIndex(year - self.years.first)
            self.month_combo.setCurrentIndex(month - 1)
            for index in range(12):
                first = QDate(year, index + 1, 1)
                last = QDate(year, index + 1, first.daysInMonth())
                self.month_combo.model().item(index).setEnabled(first <= self.maximumDate() and last >= self.minimumDate())
            self.previous_button.setEnabled(QDate(year, month, 1) > self.minimumDate())
            first = QDate(year, month, 1)
            self.next_button.setEnabled(QDate(year, month, first.daysInMonth()) < self.maximumDate())
        finally:
            for control, previous in zip(controls, blocked):
                control.blockSignals(previous)

    def _choose_page(self, *_):
        year, month = self.year_combo.currentData(), self.month_combo.currentData()
        if year is None or month is None:
            return
        first = QDate(year, month, 1)
        if first < QDate(self.minimumDate().year(), self.minimumDate().month(), 1):
            year, month = self.minimumDate().year(), self.minimumDate().month()
        elif first > self.maximumDate():
            year, month = self.maximumDate().year(), self.maximumDate().month()
        self.setCurrentPage(year, month)
        self._sync_header()

    def showEvent(self, event):
        self._sync_header()
        super().showEvent(event)

    def setDateRange(self, minimum, maximum):
        super().setDateRange(minimum, maximum)
        self._sync_header()

    def setMinimumDate(self, minimum):
        super().setMinimumDate(minimum)
        self._sync_header()

    def setMaximumDate(self, maximum):
        super().setMaximumDate(maximum)
        self._sync_header()


class DateEdit(QDateEdit):
    """QDateEdit의 직접 입력은 보존하고 날짜 팝업은 공통 적용·취소 UI를 사용한다."""

    calendar_clicked = Signal(QDate)
    calendar_activated = Signal(QDate)

    def __init__(self, parent=None, *, open_on_click=False):
        super().__init__(parent)
        self.open_on_click = open_on_click
        self._calendar_widget = None
        self.dialog = None
        self.setCalendarPopup(True)
        self.setDisplayFormat("yyyy-MM-dd")
        if open_on_click:
            self.lineEdit().installEventFilter(self)

    def calendarWidget(self):
        """명시적인 QDateEdit 달력 API는 팝업과 별도의 네이티브 연결로 유지한다."""
        if self._calendar_widget is None:
            self.setCalendarWidget(CalendarWidget(self))
        return self._calendar_widget

    def setCalendarWidget(self, calendar):
        if self._calendar_widget is calendar:
            return
        super().setCalendarWidget(calendar)
        self._calendar_widget = calendar
        calendar.clicked.connect(self.calendar_clicked.emit)
        calendar.activated.connect(self.calendar_activated.emit)

    def is_calendar_visible(self):
        return (self.dialog is not None and self.dialog.isVisible()
                or self._calendar_widget is not None and self._calendar_widget.isVisible())

    def open_calendar(self):
        if not self.isEnabled() or self.isReadOnly() or not self.calendarPopup():
            return
        # date_range가 DateEdit를 기반으로 하므로 실제 팝업을 열 때 연결한다.
        from setron.ui.date_range import DateRangeDialog

        if self.dialog is not None:
            if self.dialog.isVisible():
                self.dialog.reject()
                return
            self.dialog.deleteLater()
        day = self.date().toPython()
        dialog = DateRangeDialog(day, day, minimum=self.minimumDate().toPython(),
                                 maximum=self.maximumDate().toPython(), single_day=True, parent=self)
        self.dialog = dialog
        dialog.accepted.connect(lambda: self._apply_day(dialog))
        dialog.open_at(self)

    def _apply_day(self, dialog):
        if dialog is not self.dialog:
            return
        day = QDate(dialog.end.year, dialog.end.month, dialog.end.day)
        if day != self.date():
            self.setDate(day)
        else:
            self.calendar_activated.emit(day)

    def _calendar_button_rect(self):
        option = QStyleOptionComboBox()
        option.initFrom(self)
        return self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                          QStyle.SubControl.SC_ComboBoxArrow, self)

    def _open_from_click(self, event):
        if (not self.open_on_click or event.button() != Qt.MouseButton.LeftButton or not self.calendarPopup()
                or not self.isEnabled() or self.isReadOnly()):
            return False
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.open_calendar()
        return True

    def mousePressEvent(self, event):
        if self._open_from_click(event):
            event.accept()
        else:
            if (event.button() == Qt.MouseButton.LeftButton and self.calendarPopup() and self.isEnabled() and not self.isReadOnly()
                    and self._calendar_button_rect().contains(event.position().toPoint())):
                self.open_calendar()
                event.accept()
                return
            super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if (self.calendarPopup() and not self.isReadOnly() and
                (event.key() == Qt.Key.Key_F4 or
                 event.key() == Qt.Key.Key_Down and event.modifiers() & Qt.KeyboardModifier.AltModifier)):
            self.open_calendar()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress and self._open_from_click(event):
            return True
        return super().eventFilter(watched, event)

    def hideEvent(self, event):
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.reject()
        super().hideEvent(event)
