"""기준일과 기간이 같은 달력 팝업을 공유하고 적용할 때만 확정한다."""

from datetime import date

from PySide6.QtCore import QDate, QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLayout, QPushButton, QScrollArea,
    QStyle, QStyleOptionButton, QTableView, QVBoxLayout, QWidget,
)

from setron.ui.appearance import ui_color
from setron.ui.calendar_widgets import CalendarWidget, DateEdit
from setron.ui.components import button, label
from setron.ui.theme import ACCENT_SOFT, INK, LINE, PRIMARY, SECONDARY, SOFT, font, icon, set_widget_font


def _qdate(value):
    return QDate(value.year, value.month, value.day)


def _validate(start, end, maximum):
    if type(start) is not date or type(end) is not date or start > end:
        raise ValueError("시작 날짜와 종료 날짜를 확인해 주세요.")
    if maximum is not None and (type(maximum) is not date or end > maximum):
        raise ValueError("선택할 수 있는 마지막 날짜를 확인해 주세요.")


class _RangeCalendar(CalendarWidget):
    def set_range(self, start, end):
        self.range_start, self.range_end = start, end
        self.updateCells()

    def paintCell(self, painter, rect, day):
        start, end = getattr(self, "range_start", None), getattr(self, "range_end", None)
        selected = start is not None and (day == start if end is None else start <= day <= end)
        if not selected:
            super().paintCell(painter, rect, day)
            return
        endpoint = day == start or day == end
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ui_color(PRIMARY if endpoint else ACCENT_SOFT))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 4 if endpoint else 0, 4 if endpoint else 0)
        painter.setPen(ui_color("white" if endpoint else INK))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(day.day()))
        painter.restore()


class DateRangeDialog(QDialog):
    def __init__(self, start, end, *, maximum=None, minimum=None, single_day=False, parent=None):
        _validate(start, end, maximum)
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("dateRangeDialog")
        title = "기준 날짜 선택" if single_day else "조회 기간 선택"
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.setStyleSheet(f"QDialog#dateRangeDialog {{background:white;border:1px solid {LINE};border-radius:10px;}}")
        self.start, self.end, self.maximum = start, end, maximum
        self.single_day = single_day
        self.awaiting_end = False
        body = QVBoxLayout(self)
        body.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(8)
        body.addWidget(label("기준 날짜 선택" if single_day else "기간 선택", 15, medium=True))
        self.caption = label("조회할 기준 날짜를 선택해 주세요." if single_day else
                             "시작 날짜와 종료 날짜를 차례로 선택해 주세요.", 12, color=SECONDARY, wrap=True)
        body.addWidget(self.caption)
        self.range_label = label("", 13, medium=True, color=PRIMARY, wrap=True)
        self.range_label.setAccessibleName("선택 중인 기준 날짜" if single_day else "선택 중인 조회 기간")
        body.addWidget(self.range_label)
        self.calendar = _RangeCalendar()
        self.calendar.setAccessibleName("기준 날짜 달력" if single_day else "조회 기간 달력 · 시작 날짜와 종료 날짜 선택")
        if minimum is not None:
            self.calendar.setMinimumDate(_qdate(minimum))
        if maximum is not None:
            self.calendar.setMaximumDate(_qdate(maximum))
        self.calendar.setSelectedDate(_qdate(start))
        self.calendar.setCurrentPage(start.year, start.month)
        self.calendar.setMinimumSize(self.calendar.minimumSizeHint())
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.calendar)
        self.calendar_scroll = scroll
        body.addWidget(scroll, 1)
        self.calendar.clicked.connect(self._choose_day)
        self.calendar_view = self.calendar.findChild(QTableView, "qt_calendar_calendarview")
        self.calendar_view.installEventFilter(self)
        footer = QHBoxLayout()
        footer.addStretch()
        self.cancel_button, self.apply_button = button("취소"), button("적용", primary=True)
        for control in (self.cancel_button, self.apply_button):
            control.setAutoDefault(False)
            control.setFixedHeight(36)
            footer.addWidget(control)
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self.accept)
        body.addLayout(footer)
        self._render_range()

    def _render_range(self):
        self.calendar.set_range(_qdate(self.start), _qdate(self.end) if self.end is not None else None)
        self.range_label.setText(f"{self.start:%Y-%m-%d}" if self.single_day else
                                 f"{self.start:%Y-%m-%d} ~ " + (f"{self.end:%Y-%m-%d}" if self.end else "종료 날짜"))
        self.apply_button.setEnabled(not self.awaiting_end)

    def _choose_day(self, value):
        chosen = value.toPython()
        if not self.calendar.minimumDate() <= value <= self.calendar.maximumDate():
            return
        if self.single_day:
            self.start = self.end = chosen
        elif self.awaiting_end:
            self.start, self.end = sorted((self.start, chosen))
            self.awaiting_end = False
            self.caption.setText("선택한 기간을 확인한 뒤 적용해 주세요.")
        else:
            self.start, self.end, self.awaiting_end = chosen, None, True
            self.caption.setText("종료 날짜를 선택해 주세요.")
        self._render_range()

    def eventFilter(self, watched, event):
        if (self.isVisible() and event.type() == QEvent.Type.MouseButtonPress
                and isinstance(watched, QWidget) and watched is not self and not self.isAncestorOf(watched)
                and not self.frameGeometry().contains(event.globalPosition().toPoint())):
            self.reject()
            anchor = self.parentWidget()
            return watched is anchor or (anchor is not None and anchor.isAncestorOf(watched))
        if (watched is self.calendar_view and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)):
            self._choose_day(self.calendar.selectedDate())
            return True
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        QApplication.instance().installEventFilter(self)
        super().showEvent(event)

    def hideEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)

    def accept(self):
        if not self.awaiting_end and self.end is not None:
            super().accept()

    def open_at(self, anchor):
        location = anchor.mapToGlobal(QPoint(0, anchor.height()))
        screen = QApplication.screenAt(location) or anchor.screen()
        bounds = screen.availableGeometry().adjusted(8, 8, -8, -8)
        # 보통 화면에서는 달력 전체를 표시하고, 화면이 작을 때만 달력 영역을 스크롤한다.
        self.calendar_scroll.setMinimumSize(self.calendar.minimumSizeHint() + QSize(2, 2))
        natural = self.sizeHint().expandedTo(QSize(340, 0))
        self.calendar_scroll.setMinimumSize(0, 0)
        width, height = min(natural.width(), bounds.width()), min(natural.height(), bounds.height())
        self.resize(width, height)
        x = max(bounds.left(), min(location.x(), bounds.right() - self.width() + 1))
        y = location.y()
        if y + self.height() > bounds.bottom() + 1:
            y = anchor.mapToGlobal(QPoint(0, 0)).y() - self.height()
        y = max(bounds.top(), min(y, bounds.bottom() - self.height() + 1))
        self.move(x, y)
        self.show()
        self.calendar_view.setFocus(Qt.FocusReason.PopupFocusReason)


class DatePickerEdit(DateEdit):
    """단일 기준일도 기간 선택과 동일한 팝업에서 적용·취소한다."""

    def __init__(self, parent=None, *, open_on_click=True):
        super().__init__(parent, open_on_click=open_on_click)
        self.dialog = None

    def is_calendar_visible(self):
        return self.dialog is not None and self.dialog.isVisible()

    def open_calendar(self):
        if not self.isEnabled() or self.isReadOnly() or not self.calendarPopup():
            return
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
        day = _qdate(dialog.end)
        if day != self.date():
            self.setDate(day)
        else:
            self.calendar_activated.emit(day)

    def _open_from_click(self, event):
        if (event.button() != Qt.MouseButton.LeftButton or not self.isEnabled()
                or self.isReadOnly() or not self.calendarPopup()):
            return False
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.open_calendar()
        return True

    def keyPressEvent(self, event):
        if (event.key() == Qt.Key.Key_F4 or
                event.key() == Qt.Key.Key_Down and event.modifiers() & Qt.KeyboardModifier.AltModifier):
            self.open_calendar()
            event.accept()
        else:
            super().keyPressEvent(event)

    def hideEvent(self, event):
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.reject()
        super().hideEvent(event)


class DateRangeButton(QPushButton):
    range_changed = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dateRangeButton")
        self.setProperty("queryField", True)
        self.setAccessibleName("조회 기간 선택")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(240)
        self.setFixedHeight(38)
        self.setAutoDefault(False)
        set_widget_font(self, font(14))
        self.setIcon(icon("import-ver5/fdb91.svg"))
        self.setIconSize(QSize(16, 16))
        self.setStyleSheet(f"QPushButton#dateRangeButton {{background:{SOFT};color:{INK};border:1px solid {LINE};"
                          "border-radius:10px;padding:0 10px;min-height:36px;max-height:36px;}"
                          f"QPushButton#dateRangeButton:hover {{border-color:{PRIMARY};}}"
                          f"QPushButton#dateRangeButton:focus {{border:2px solid {PRIMARY};}}")
        self.dialog = None
        self.set_range(date.today(), date.today())
        self.clicked.connect(self.open_range)

    def set_range(self, start, end, maximum=None):
        _validate(start, end, maximum)
        self.start, self.end, self.maximum = start, end, maximum
        text = f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d}"
        self.setText(text)
        self.setToolTip(text + " · 시작 날짜와 종료 날짜를 선택합니다.")
        self.setAccessibleDescription(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text, option.icon = "", QIcon()
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
        painter.setPen(ui_color(INK if self.isEnabled() else SECONDARY))
        painter.drawText(self.rect().adjusted(10, 0, -34, 0), Qt.AlignmentFlag.AlignCenter, self.text())
        self.icon().paint(painter, self.width() - 26, (self.height() - 16) // 2, 16, 16)

    def open_range(self):
        if self.dialog is not None:
            if self.dialog.isVisible():
                self.dialog.reject()
                return
            self.dialog.deleteLater()
        dialog = DateRangeDialog(self.start, self.end, maximum=self.maximum, parent=self)
        self.dialog = dialog
        dialog.accepted.connect(lambda: self._apply_range(dialog))
        dialog.open_at(self)

    def _apply_range(self, dialog):
        self.set_range(dialog.start, dialog.end, self.maximum)
        self.range_changed.emit(self.start, self.end)
