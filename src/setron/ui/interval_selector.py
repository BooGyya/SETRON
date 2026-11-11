"""저장일·주·월 간격을 고르는 공통 세그먼트 도구."""

from PySide6.QtCore import QSignalBlocker, QSize, Signal
from PySide6.QtWidgets import QAbstractSpinBox, QButtonGroup, QGridLayout, QLayout, QSizePolicy, QSpinBox, QWidget

from setron.ui.components import button, label
from setron.ui.observation_interval import INTERVALS
from setron.ui.theme import INK, LINE, PRIMARY, SECONDARY, font, set_widget_font


class IntervalSelector(QWidget):
    step_changed = Signal(object)

    def __init__(self, caption="재생 간격", parent=None, *, compact=False, custom_days=False):
        super().__init__(parent)
        self.compact = compact
        self.custom_days = custom_days
        self._segment_margin = 1 if compact else 3
        self.caption_label = label(caption, 14, medium=True, color=SECONDARY)
        self.segment = QWidget()
        self.segment.setObjectName("replayInterval")
        self.segment.setStyleSheet(f"QWidget#replayInterval {{background:#f3f5f8;border:1px solid {LINE};border-radius:10px;}}")
        self.segment.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        self._segments = QGridLayout(self.segment)
        self._segments.setContentsMargins(*([self._segment_margin] * 4))
        self._segments.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        action = "재생" if caption == "재생 간격" else "표시"
        for step, text in INTERVALS.items():
            if step is None and custom_days:
                text = "지정일마다"
            control = button(text)
            control.setCheckable(True)
            control.setChecked(step is None)
            control.setFixedSize(96 if step is None else 72, 30 if compact else 34)
            control.setAccessibleName(f"{caption} {text}")
            control.setToolTip(f"시작 날짜부터 지정한 일수 간격으로 {action}" if step is None and custom_days else
                               f"실제 저장된 날짜마다 {action}" if step is None else
                               f"시작 날짜부터 {text} 간격으로 {action} · 저장되지 않은 날짜는 그 전 마지막 관측으로 계산")
            if compact:
                control.setStyleSheet(f"QPushButton {{background:transparent;color:{SECONDARY};border:1px solid transparent;"
                                      "border-radius:8px;padding:0;min-height:28px;max-height:28px;}"
                                      "QPushButton:disabled {color:#b8bec9;}"
                                      f"QPushButton:checked {{background:{PRIMARY};color:white;border-color:{PRIMARY};}}"
                                      f"QPushButton:focus {{border-color:{PRIMARY};}}")
            else:
                control.setStyleSheet(f"QPushButton {{background:transparent;color:{SECONDARY};border:0;border-radius:8px;padding:0;"
                                      "min-height:34px;max-height:34px;}"
                                      f"QPushButton:checked {{background:white;color:{PRIMARY};border:1px solid {PRIMARY};}}"
                                      "QPushButton:disabled {color:#b8bec9;}"
                                      f"QPushButton:focus {{border:2px solid {PRIMARY};}}")
            control.clicked.connect(lambda checked=False, value=step: self._select_step(value))
            self.group.addButton(control)
            self.buttons[step] = control
        self.day_input = None
        if custom_days:
            self.day_input = QSpinBox(self.segment)
            self.day_input.setRange(1, 2_147_483_647)
            self.day_input.setValue(1)
            self.day_input.setSuffix("일")
            self.day_input.setKeyboardTracking(False)
            self.day_input.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.day_input.setAccessibleName(f"{caption} 일수")
            self.day_input.setToolTip("시작 날짜부터 적용할 일수 간격")
            set_widget_font(self.day_input, font(14))
            self.day_input.setStyleSheet(f"QSpinBox {{background:white;color:{INK};border:1px solid {LINE};"
                                         "border-radius:6px;padding:0 6px;}")
            self.day_input.setFixedHeight(30 if compact else 34)
            self.day_input.ensurePolished()
            self.day_input.setFixedWidth(max(self.day_input.sizeHint().width(),
                                            self.day_input.fontMetrics().horizontalAdvance("2147483647일") + 20))
            self.day_input.setVisible(True)
        self._body = QGridLayout(self)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setHorizontalSpacing(12)
        self._body.setVerticalSpacing(8)
        self._body.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._arrangement = None
        self._arrange(True, len(self._active_controls()))
        if custom_days:
            self.buttons[None].toggled.connect(self._sync_day_input)
            self.day_input.valueChanged.connect(self._days_changed)

    def minimumSizeHint(self):
        return QSize(0, self._body.minimumSize().height())

    def unwrapped_width(self):
        controls = self._active_controls()
        return (self.caption_label.sizeHint().width() + self._body.horizontalSpacing()
                + sum(control.width() for control in controls)
                + self._segments.horizontalSpacing() * (len(controls) - 1) + 2 * self._segment_margin)

    def set_step(self, step):
        if self.custom_days and type(step) is int:
            if not 1 <= step <= self.day_input.maximum():
                raise ValueError("일수 간격은 1일 이상이어야 합니다.")
            with QSignalBlocker(self), QSignalBlocker(self.day_input):
                self.day_input.setValue(step)
                self.buttons[None].setChecked(True)
                self._sync_day_input()
            return
        if step not in self.buttons:
            raise ValueError("지원하지 않는 관측 간격입니다.")
        with QSignalBlocker(self):
            self.buttons[step].setChecked(True)
            if self.custom_days:
                self._sync_day_input()

    def _active_controls(self):
        controls = []
        for step, control in self.buttons.items():
            controls.append(control)
            if step is None and self.custom_days and control.isChecked():
                controls.append(self.day_input)
        return controls

    def _select_step(self, step):
        if self.custom_days and step is None:
            self.step_changed.emit(self.day_input.value())
        else:
            self.step_changed.emit(step)

    def _days_changed(self, value):
        if self.buttons[None].isChecked():
            self.step_changed.emit(value)

    def _sync_day_input(self, *_):
        self.day_input.setVisible(self.buttons[None].isChecked())
        self._arrangement = None
        self._reflow()

    def _arrange(self, inline, columns):
        if self._arrangement == (inline, columns):
            return
        self._body.removeWidget(self.caption_label)
        self._body.removeWidget(self.segment)
        self._body.addWidget(self.caption_label, 0, 0)
        self._body.addWidget(self.segment, 0 if inline else 1, 1 if inline else 0)
        for column in range(3):
            self._body.setColumnStretch(column, int(column == (2 if inline else 1)))
        for control in (*self.buttons.values(), self.day_input):
            if control is None:
                continue
            self._segments.removeWidget(control)
        controls = self._active_controls()
        for index, control in enumerate(controls):
            self._segments.addWidget(control, index // columns, index % columns)
        if self.compact:
            rows = (len(controls) + columns - 1) // columns
            self.segment.setFixedHeight(rows * 30 + (rows - 1) * 2 + 4)
        self._arrangement = inline, columns
        self.updateGeometry()

    def _reflow(self):
        widths = [control.width() for control in self._active_controls()]
        inline = self.width() >= self.unwrapped_width()
        columns = len(widths)
        if not inline:
            while columns > 1:
                needed = max(sum(widths[start:start + columns]) + 2 * (len(widths[start:start + columns]) - 1) + 2 * self._segment_margin
                             for start in range(0, len(widths), columns))
                if needed <= self.width():
                    break
                columns -= 1
        self._arrange(inline, columns)

    def resizeEvent(self, event):
        self._reflow()
        super().resizeEvent(event)
