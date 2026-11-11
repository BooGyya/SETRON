"""저장일·주·월 간격을 고르는 공통 세그먼트 도구."""

from PySide6.QtCore import QSignalBlocker, QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QInputDialog, QLayout, QSizePolicy, QWidget

from setron.ui.components import button, label
from setron.ui.observation_interval import INTERVALS
from setron.ui.theme import LINE, PRIMARY, SECONDARY


class IntervalSelector(QWidget):
    step_changed = Signal(object)

    def __init__(self, caption="재생 간격", parent=None, *, compact=False, custom_days=False):
        super().__init__(parent)
        self.compact = compact
        self.custom_days = custom_days
        self.day_value = 1
        self._current_step = 1 if custom_days else None
        self.day_dialog = None
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
        self._action = action
        for step, text in INTERVALS.items():
            if step is None and custom_days:
                text = "지정일마다"
            control = button(text)
            control.setCheckable(True)
            control.setChecked(step is None)
            control.setFixedSize(96 if step is None else 72, 30 if compact else 34)
            control.setAccessibleName(f"{caption} {text}")
            control.setToolTip(f"시작 날짜부터 {self.day_value}일 간격으로 {action}" if step is None and custom_days else
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
        self._body = QGridLayout(self)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setHorizontalSpacing(12)
        self._body.setVerticalSpacing(8)
        self._body.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._arrangement = None
        self._arrange(True, len(self._active_controls()))

    def minimumSizeHint(self):
        return QSize(0, self._body.minimumSize().height())

    def unwrapped_width(self):
        controls = self._active_controls()
        return (self.caption_label.sizeHint().width() + self._body.horizontalSpacing()
                + sum(control.width() for control in controls)
                + self._segments.horizontalSpacing() * (len(controls) - 1) + 2 * self._segment_margin)

    def set_step(self, step):
        selected = step
        if self.custom_days and type(step) is int:
            if not 1 <= step <= 2_147_483_647:
                raise ValueError("일수 간격은 1일 이상이어야 합니다.")
            self.day_value, selected = step, None
        elif self.custom_days and step is None:
            step = self.day_value
        elif step not in self.buttons:
            raise ValueError("지원하지 않는 관측 간격입니다.")
        with QSignalBlocker(self):
            self.buttons[selected].setChecked(True)
            self._current_step = step
            if self.custom_days:
                self.buttons[None].setToolTip(f"시작 날짜부터 {self.day_value}일 간격으로 {self._action}")

    def _active_controls(self):
        return list(self.buttons.values())

    def _select_step(self, step):
        if self.custom_days and step is None:
            self.set_step(self._current_step)
            self._open_day_dialog()
        else:
            self.set_step(step)
            self.step_changed.emit(step)

    def _open_day_dialog(self):
        if self.day_dialog is not None:
            if self.day_dialog.isVisible():
                self.day_dialog.raise_()
                self.day_dialog.activateWindow()
                return
            self.day_dialog.deleteLater()
        dialog = QInputDialog(self.window())
        dialog.setWindowTitle("일수 간격 입력")
        dialog.setLabelText("표시할 간격을 일수로 입력하세요.")
        dialog.setInputMode(QInputDialog.InputMode.IntInput)
        dialog.setIntRange(1, 2_147_483_647)
        dialog.setIntValue(self.day_value)
        dialog.setOkButtonText("적용")
        dialog.setCancelButtonText("취소")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.accepted.connect(lambda: self._apply_day_dialog(dialog))
        self.day_dialog = dialog
        dialog.open()

    def _apply_day_dialog(self, dialog):
        if dialog is not self.day_dialog:
            return
        value = dialog.intValue()
        self.set_step(value)
        self.step_changed.emit(value)

    def _arrange(self, inline, columns):
        if self._arrangement == (inline, columns):
            return
        self._body.removeWidget(self.caption_label)
        self._body.removeWidget(self.segment)
        self._body.addWidget(self.caption_label, 0, 0)
        self._body.addWidget(self.segment, 0 if inline else 1, 1 if inline else 0)
        for column in range(3):
            self._body.setColumnStretch(column, int(column == (2 if inline else 1)))
        for control in self.buttons.values():
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
