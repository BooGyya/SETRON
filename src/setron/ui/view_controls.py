"""보기 전환과 간격 선택에 쓰는 공통 세그먼트·일괄 선택 버튼."""

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QPropertyAnimation, QRect, QSize, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QButtonGroup, QGridLayout, QSizePolicy, QWidget

from setron.ui.appearance import readable_color, set_color_style
from setron.ui.components import button
from setron.ui.theme import LINE, font, set_widget_font


class SegmentedControl(QWidget):
    """버튼을 하나의 표면에 배치한다. 보기 탭은 QTabBar와 같은 선택 API를 쓴다."""

    currentChanged = Signal(int)

    def __init__(self, parent=None, *, compact=True):
        super().__init__(parent)
        self.compact = compact
        self.margin = 2
        self.buttons = []
        self._columns = None
        self.setObjectName("segmentedControl")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#segmentedControl {{background:#f3f5f8;border:1px solid {LINE};border-radius:10px;}}")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(*([self.margin] * 4))
        self.grid.setSpacing(2)
        self.selection_indicator = QWidget(self)
        self.selection_indicator.setObjectName("segmentSelection")
        self.selection_indicator.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.selection_indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        set_color_style(self.selection_indicator, lambda colors: (
            f"QWidget#segmentSelection {{background:{colors['primary']};border:0;border-radius:8px;}}"
            f"QWidget#segmentSelection:disabled {{background:{colors['selected']};}}"
        ))
        self.selection_indicator.hide()
        self.slide_animation = QPropertyAnimation(self.selection_indicator, b"geometry", self)
        self.slide_animation.setDuration(180)
        self.slide_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._selection_sync_pending = False
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.group.idToggled.connect(self._selection_changed)

    def addTab(self, text):
        control = button(text)
        control.setCheckable(True)
        control.setFixedHeight(30 if self.compact else 34)
        control.setMinimumWidth(max(72, control.fontMetrics().horizontalAdvance(text) + 24))
        control.setAccessibleName(text)
        set_color_style(control, lambda colors: (
            f"QPushButton {{background:transparent;color:{colors['muted']};border:1px solid transparent;"
            "border-radius:8px;padding:0 12px;min-height:28px;}"
            f"QPushButton:checked {{background:transparent;color:{readable_color('#ffffff', colors['primary'])};border-color:transparent;}}"
            f"QPushButton:focus {{border-color:{colors['accent']};}}"
            f"QPushButton:disabled {{color:{colors['muted']};}}"
        ))
        index = len(self.buttons)
        self.buttons.append(control)
        self.group.addButton(control, index)
        control.installEventFilter(self)
        self.set_columns(len(self.buttons))
        if index == 0:
            control.setChecked(True)
        return index

    def set_columns(self, columns):
        columns = max(1, columns)
        for control in self.buttons:
            self.grid.removeWidget(control)
        for index, control in enumerate(self.buttons):
            self.grid.addWidget(control, index // columns, index % columns)
        self._columns = columns
        self.updateGeometry()
        self._queue_selection_sync()

    def _selection_changed(self, index, checked):
        if checked:
            self._sync_selection(animate=True)
            self.currentChanged.emit(index)

    def _sync_selection(self, *, animate=False):
        index = self.currentIndex()
        if not 0 <= index < self.count():
            self.slide_animation.stop()
            self.selection_indicator.hide()
            return
        self.grid.activate()
        target = self.tabRect(index)
        if target.isEmpty():
            return
        if (self.slide_animation.state() == QAbstractAnimation.State.Running
                and self.slide_animation.endValue() == target):
            return
        self.slide_animation.stop()
        moving = (animate and self.isVisible() and self.selection_indicator.isVisible()
                  and self.selection_indicator.geometry() != target)
        self.selection_indicator.lower()
        if moving:
            self.slide_animation.setStartValue(self.selection_indicator.geometry())
            self.slide_animation.setEndValue(target)
            self.slide_animation.start()
        else:
            self.selection_indicator.setGeometry(target)
        self.selection_indicator.setVisible(self.isVisible())

    def _queue_selection_sync(self):
        if not self._selection_sync_pending:
            self._selection_sync_pending = True
            QTimer.singleShot(0, self, self._sync_selection_layout)

    def _sync_selection_layout(self):
        self._selection_sync_pending = False
        self._sync_selection()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and hasattr(self, "slide_animation"):
            self._queue_selection_sync()
        return result

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_selection()

    def hideEvent(self, event):
        self.slide_animation.stop()
        self.selection_indicator.hide()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._queue_selection_sync()

    def count(self):
        return len(self.buttons)

    def tabText(self, index):
        return self.buttons[index].text()

    def tabRect(self, index):
        return self.buttons[index].geometry() if 0 <= index < self.count() else QRect()

    def currentIndex(self):
        return self.group.checkedId()

    def setCurrentIndex(self, index):
        if 0 <= index < self.count():
            self.buttons[index].setChecked(True)

    def setExpanding(self, _value):
        pass

    def setDrawBase(self, _value):
        pass

    def setUsesScrollButtons(self, _value):
        pass

    def setElideMode(self, _value):
        pass

    def minimumSizeHint(self):
        return QSize(0, self.grid.minimumSize().height())

    def eventFilter(self, watched, event):
        if watched in self.buttons and event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self._queue_selection_sync()
        if watched in self.buttons and event.type() == QEvent.Type.KeyPress:
            if self._navigate(self.buttons.index(watched), event.key()):
                return True
        if watched in self.buttons and event.type() == QEvent.Type.Wheel:
            self.wheelEvent(event)
            return True
        return super().eventFilter(watched, event)

    def _navigate(self, index, key):
        if not self.buttons or key not in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End):
            return False
        target = (0 if key == Qt.Key.Key_Home else self.count() - 1 if key == Qt.Key.Key_End
                  else (index + (1 if key == Qt.Key.Key_Right else -1)) % self.count())
        if self.buttons[target].isEnabled():
            self.buttons[target].click()
            self.buttons[target].setFocus()
        return True

    def keyPressEvent(self, event):
        if self._navigate(self.currentIndex(), event.key()):
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea) and parent.verticalScrollBar().maximum() > 0:
                QApplication.sendEvent(parent.verticalScrollBar(), event)
                return
            parent = parent.parentWidget()
        event.ignore()


def bulk_selection_button(text):
    """화면과 팝업에서 전체 선택 동작을 같은 검정 버튼으로 표시한다."""
    control = button(text)
    control.setFixedHeight(34)
    control.setMinimumWidth(88)
    set_color_style(control, lambda _colors: (
        "QPushButton {background:#000000;color:#ffffff;border:1px solid #000000;"
        "border-radius:8px;padding:0 12px;min-height:32px;}"
        "QPushButton:hover {background:#242424;border-color:#242424;}"
        "QPushButton:focus {border:2px solid #7b8ff5;}"
        "QPushButton:disabled {background:#4b4b4b;color:#ffffff;border-color:#4b4b4b;}"
    ))
    return control


def bulk_selection_toggle_button():
    """대시보드와 리플레이의 설비군 일괄 선택 토글."""
    control = bulk_selection_button("전체 해제")
    control.setCheckable(True)
    control.setMinimumWidth(96)
    set_widget_font(control, font(13, medium=True))
    set_bulk_selection_state(control, True)
    return control


def set_bulk_selection_state(control, all_selected):
    control.setChecked(all_selected)
    control.setText("전체 해제" if all_selected else "전체 선택")
    control.setAccessibleName("설비군 " + control.text())
