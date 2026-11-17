"""기준일과 기간이 같은 달력 팝업을 공유하고 적용할 때만 확정한다."""

from datetime import date

from PySide6.QtCore import QDate, QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLayout, QPushButton, QScrollArea,
    QSizePolicy, QStyle, QStyleOptionButton, QTableView, QVBoxLayout, QWidget,
)

from setron.ui.appearance import set_color_style, ui_color
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
    def __init__(self, *, comfortable=False):
        super().__init__()
        self.comfortable = comfortable
        self._day_view = self.findChild(QTableView, "qt_calendar_calendarview")

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        if getattr(self, "comfortable", False):
            size.setWidth(max(350, size.width()))
            header_height = self.header.sizeHint().height() if self.header.parentWidget() is self else 0
            size.setHeight(max(size.height(), header_height + 7 * max(30, self.fontMetrics().height() + 10)))
        return size

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
        if self.comfortable:
            # 연한 기간 띠는 이어 그리고, 양 끝 날짜만 별도의 둥근 셀로 강조한다.
            band = rect.adjusted(0, 3, 0, -3)
            if endpoint:
                band.setLeft(rect.center().x() if day == start else rect.left())
                band.setRight(rect.center().x() if day == end else rect.right())
            painter.setBrush(ui_color(ACCENT_SOFT))
            painter.drawRect(band)
        painter.setBrush(ui_color(PRIMARY if endpoint else ACCENT_SOFT))
        if not self.comfortable or endpoint:
            inset = 3 if self.comfortable else 1
            radius = (6 if self.comfortable else 4) if endpoint else 0
            painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), radius, radius)
        painter.setPen(ui_color("white" if endpoint else INK))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(day.day()))
        if self.comfortable and day == self.selectedDate() and self._day_view.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(ui_color("white" if endpoint else PRIMARY), 1))
            painter.drawRoundedRect(rect.adjusted(6, 6, -6, -6), 4, 4)
        painter.restore()


class _RangeEndpointButton(QPushButton):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("rangeEndpoint")
        self.setCheckable(True)
        self.setAccessibleName(f"{title} 선택")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        body = QVBoxLayout(self)
        body.setContentsMargins(12, 9, 12, 9)
        body.setSpacing(4)
        self.caption = label(title, 11, medium=True)
        self.value = label("", 17, medium=True, numeric=True)
        for child in (self.caption, self.value):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            body.addWidget(child)
        set_color_style(self, lambda colors: (
            f"QPushButton#rangeEndpoint {{background:{colors['soft']};border:1px solid {colors['line']};"
            "border-radius:10px;padding:0;}"
            f"QPushButton#rangeEndpoint:hover {{background:{colors['surface']};border-color:{colors['accent']};}}"
            f"QPushButton#rangeEndpoint:checked {{background:{colors['selected']};border:2px solid {colors['accent']};}}"
            f"QPushButton#rangeEndpoint:focus {{border-color:{colors['accent']};}}"))
        set_color_style(self.value, lambda colors: f"color:{colors['ink']};")
        self.toggled.connect(self._sync_caption)
        self._sync_caption()

    def _sync_caption(self, *_):
        set_color_style(self.caption, lambda colors: f"color:{colors['accent'] if self.isChecked() else colors['muted']};")

    def set_day(self, day):
        self.value.setText(f"{day:%Y-%m-%d}")
        self.setAccessibleDescription(self.value.text())

    def sizeHint(self):
        return self.layout().sizeHint() + QSize(4, 4) if self.layout() is not None else super().sizeHint()

    def minimumSizeHint(self):
        return self.sizeHint()


class DateRangeDialog(QDialog):
    def __init__(self, start, end, *, maximum=None, minimum=None, single_day=False, parent=None):
        _validate(start, end, maximum)
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("dateRangeDialog")
        title = "날짜 선택" if single_day else "조회 기간 선택"
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.setStyleSheet(f"QDialog#dateRangeDialog {{background:white;border:1px solid {LINE};border-radius:10px;}}")
        self.start, self.end, self.maximum = start, end, maximum
        self.single_day = single_day
        self.active_endpoint = "start"
        body = QVBoxLayout(self)
        body.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        body.setContentsMargins(16, 16, 16, 16)
        body.setSpacing(12)
        self.caption = label("", 12, color=SECONDARY, wrap=True)
        body.addWidget(self.caption)
        self.range_label = label("", 13, medium=True, color=PRIMARY, wrap=True)
        self.range_label.setAccessibleName("선택 중인 기준 날짜" if single_day else "선택 중인 조회 기간")
        body.addWidget(self.range_label)
        self.range_label.hide()
        self.endpoint_buttons = {}
        endpoints = QHBoxLayout()
        endpoints.setSpacing(10)
        self._endpoint_row = endpoints
        fields = (("start", "선택 날짜"),) if single_day else (("start", "시작 날짜"), ("end", "종료 날짜"))
        for endpoint, title in fields:
            control = _RangeEndpointButton(title)
            control.clicked.connect(lambda _checked=False, name=endpoint: self._select_endpoint(name))
            self.endpoint_buttons[endpoint] = control
            endpoints.addWidget(control, 1)
        body.addLayout(endpoints)
        self.calendar = _RangeCalendar(comfortable=True)
        self.calendar.setObjectName("dateRangeCalendar")
        self.calendar.setStyleSheet(self.calendar.styleSheet() +
            "QCalendarWidget#dateRangeCalendar {border:0;border-radius:0;}")
        self.calendar.header.layout().setContentsMargins(0, 0, 0, 0)
        for control in (self.calendar.previous_button, self.calendar.next_button):
            control.setFixedSize(32, 36)
        for control in (self.calendar.year_combo, self.calendar.month_combo):
            control.setFixedHeight(36)
        body.insertWidget(0, self.calendar.take_navigation_header())
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
        if single_day:
            self.calendar.activated.connect(self._choose_day)
        self.calendar_view = self.calendar.findChild(QTableView, "qt_calendar_calendarview")
        self.calendar_view.installEventFilter(self)
        divider = QFrame()
        divider.setFixedHeight(1)
        set_color_style(divider, lambda colors: f"background:{colors['line']};border:0;")
        body.addWidget(divider)
        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addStretch()
        self.cancel_button, self.apply_button = button("취소"), button("적용", primary=True)
        for control in (self.cancel_button, self.apply_button):
            control.setAutoDefault(False)
            control.setFixedHeight(40)
            control.setMinimumWidth(82)
            footer.addWidget(control)
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self.accept)
        body.addLayout(footer)
        self._render_range()
        self._select_endpoint("start", focus_calendar=False)

    def _render_range(self):
        self.calendar.set_range(_qdate(self.start), _qdate(self.end))
        self.range_label.setText(f"{self.start:%Y-%m-%d}" if self.single_day else
                                 f"{self.start:%Y-%m-%d} ~ {self.end:%Y-%m-%d}")
        valid = self._valid_range()
        self.apply_button.setEnabled(valid)
        for endpoint, control in self.endpoint_buttons.items():
            control.set_day(self.start if endpoint == "start" else self.end)
        self.caption.setText("시작 날짜는 종료 날짜와 같거나 앞서야 합니다." if not valid else "")
        self.caption.setVisible(not valid)

    def _valid_range(self):
        return (self.start <= self.end
                and self.calendar.minimumDate() <= _qdate(self.start)
                and _qdate(self.end) <= self.calendar.maximumDate())

    def _select_endpoint(self, endpoint, *, focus_calendar=True, reveal_date=True):
        self.active_endpoint = endpoint
        for name, control in self.endpoint_buttons.items():
            control.setChecked(name == endpoint)
        if reveal_date:
            chosen = self.start if endpoint == "start" else self.end
            self.calendar.setSelectedDate(_qdate(chosen))
            self.calendar.setCurrentPage(chosen.year, chosen.month)
        if focus_calendar:
            self.calendar_view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _choose_day(self, value):
        chosen = value.toPython()
        if not self.calendar.minimumDate() <= value <= self.calendar.maximumDate():
            return
        if self.single_day:
            self.start = self.end = chosen
        elif self.active_endpoint == "start":
            self.start = chosen
        else:
            self.end = chosen
        self._render_range()
        # 시작 날짜만 바꿔도 바로 적용할 수 있고, 계속 고르면 종료 날짜를 편집한다.
        if not self.single_day and self.active_endpoint == "start":
            self._select_endpoint("end", reveal_date=False)

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
        if self._valid_range():
            super().accept()

    def open_at(self, anchor):
        location = anchor.mapToGlobal(QPoint(0, anchor.height()))
        screen = QApplication.screenAt(location) or anchor.screen()
        bounds = screen.availableGeometry().adjusted(8, 8, -8, -8)
        if bounds.width() < 350 or bounds.height() < 420:
            self.layout().setContentsMargins(8, 8, 8, 8)
            self.layout().setSpacing(6)
            self._endpoint_row.setSpacing(6)
            self.calendar.header.layout().setSpacing(6)
            self.calendar_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
            for control in self.endpoint_buttons.values():
                control.layout().setContentsMargins(6, 6, 6, 6)
                control.layout().setSpacing(2)
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
    """조회용 날짜 칸은 텍스트를 눌러도 공통 단일 날짜 팝업을 연다."""

    def __init__(self, parent=None, *, open_on_click=True):
        super().__init__(parent, open_on_click=open_on_click)


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
