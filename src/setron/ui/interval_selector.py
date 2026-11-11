"""저장일·주·월 간격을 고르는 공통 세그먼트 도구."""

from PySide6.QtCore import QSignalBlocker, QSize, Signal
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QLayout, QSizePolicy, QWidget

from setron.ui.components import button, label
from setron.ui.observation_interval import INTERVALS
from setron.ui.theme import LINE, PRIMARY, SECONDARY


class IntervalSelector(QWidget):
    step_changed = Signal(object)

    def __init__(self, caption="재생 간격", parent=None, *, compact=False):
        super().__init__(parent)
        self.compact = compact
        self._segment_margin = 2 if compact else 3
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
            control = button(text)
            control.setCheckable(True)
            control.setChecked(step is None)
            control.setFixedSize(96 if step is None else 72, 26 if compact else 34)
            control.setAccessibleName(f"{caption} {text}")
            control.setToolTip(f"실제 저장된 날짜마다 {action}" if step is None else
                               f"시작 날짜부터 {text} 간격으로 {action} · 저장되지 않은 날짜는 그 전 마지막 관측으로 계산")
            if compact:
                control.setStyleSheet(f"QPushButton {{background:transparent;color:{SECONDARY};border:1px solid transparent;"
                                      "border-radius:6px;padding:0;min-height:24px;max-height:24px;}"
                                      f"QPushButton:checked {{background:white;color:{PRIMARY};border-color:{PRIMARY};}}"
                                      "QPushButton:disabled {color:#b8bec9;}"
                                      f"QPushButton:focus {{border-color:{PRIMARY};}}")
            else:
                control.setStyleSheet(f"QPushButton {{background:transparent;color:{SECONDARY};border:0;border-radius:8px;padding:0;"
                                      "min-height:34px;max-height:34px;}"
                                      f"QPushButton:checked {{background:white;color:{PRIMARY};border:1px solid {PRIMARY};}}"
                                      "QPushButton:disabled {color:#b8bec9;}"
                                      f"QPushButton:focus {{border:2px solid {PRIMARY};}}")
            control.clicked.connect(lambda checked=False, value=step: self.step_changed.emit(value))
            self.group.addButton(control)
            self.buttons[step] = control
        self._body = QGridLayout(self)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setHorizontalSpacing(12)
        self._body.setVerticalSpacing(8)
        self._body.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._arrangement = None
        self._arrange(True, len(self.buttons))

    def minimumSizeHint(self):
        return QSize(0, self._body.minimumSize().height())

    def unwrapped_width(self):
        return (self.caption_label.sizeHint().width() + self._body.horizontalSpacing()
                + sum(control.width() for control in self.buttons.values())
                + self._segments.horizontalSpacing() * (len(self.buttons) - 1) + 2 * self._segment_margin)

    def set_step(self, step):
        if step not in self.buttons:
            raise ValueError("지원하지 않는 관측 간격입니다.")
        with QSignalBlocker(self):
            self.buttons[step].setChecked(True)

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
        for index, control in enumerate(self.buttons.values()):
            self._segments.addWidget(control, index // columns, index % columns)
        if self.compact:
            rows = (len(self.buttons) + columns - 1) // columns
            self.segment.setFixedHeight(rows * 26 + (rows - 1) * 2 + 8)
        self._arrangement = inline, columns
        self.updateGeometry()

    def resizeEvent(self, event):
        widths = [control.width() for control in self.buttons.values()]
        inline = event.size().width() >= self.unwrapped_width()
        columns = len(widths)
        if not inline:
            while columns > 1:
                needed = max(sum(widths[start:start + columns]) + 2 * (len(widths[start:start + columns]) - 1) + 2 * self._segment_margin
                             for start in range(0, len(widths), columns))
                if needed <= event.size().width():
                    break
                columns -= 1
        self._arrange(inline, columns)
        super().resizeEvent(event)
