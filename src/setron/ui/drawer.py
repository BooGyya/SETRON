"""현재 화면을 덮는 오른쪽 패널의 공통 동작."""

from PySide6.QtCore import QEasingCurve, QEvent, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from setron.ui.components import button
from setron.ui.theme import CANVAS


class SideDrawer(QWidget):
    panel_hidden = Signal()

    def __init__(self, parent, *, object_name, accessible_name, panel_width=560):
        super().__init__(parent)
        self.setObjectName(object_name + "Overlay")
        self.setAccessibleName(accessible_name)
        self.panel_width = panel_width
        self._is_open = False
        self._slide_progress = 0.0

        self.backdrop = QPushButton(self)
        self.backdrop.setAccessibleName(accessible_name + " 닫기")
        self.backdrop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.backdrop.setStyleSheet("QPushButton {background:rgba(20,32,51,0.09);border:0;border-radius:0;padding:0;}")
        self.backdrop.clicked.connect(self.close_panel)

        self.panel = QFrame(self)
        self.panel.setObjectName(object_name + "Panel")
        self.panel.setStyleSheet(f"QFrame#{self.panel.objectName()} {{background:{CANVAS};border:1px solid #d5d7da;}}")
        self.panel.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.panel.setAccessibleName(accessible_name)
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(16)
        shadow.setOffset(-2, 0)
        shadow.setColor(QColor(20, 32, 51, 54))
        self.panel.setGraphicsEffect(shadow)
        self.panel_layout = QVBoxLayout(self.panel)
        self.panel_layout.setContentsMargins(24, 16, 24, 24)
        self.panel_layout.setSpacing(14)

        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        self.close_button = self._header_button("529be.svg", accessible_name + " 접기", "접기 (Esc)")
        self.close_button.clicked.connect(self.close_panel)
        self.header.addWidget(self.close_button)
        self.header.addStretch()

        self.escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape.activated.connect(self.close_panel)
        self._slide = QVariantAnimation(self)
        self._slide.setDuration(200)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.valueChanged.connect(self._set_slide_progress)
        self._slide.finished.connect(self._finish_slide)
        parent.installEventFilter(self)
        self.hide()

    def _header_button(self, asset, name, tooltip=None):
        control = button("", asset=asset)
        control.setText("")
        control.setAccessibleName(name)
        control.setToolTip(tooltip or name)
        control.setFixedSize(32, 32)
        control.setStyleSheet("QPushButton {padding:0;border:2px solid transparent;background:transparent;}"
                              "QPushButton:hover {background:#e9edf4;}"
                              "QPushButton:focus {border-color:#3c5ce9;}")
        return control

    @property
    def is_open(self):
        return self._is_open

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._position()
        return super().eventFilter(watched, event)

    def _position(self):
        self.setGeometry(self.parentWidget().rect())
        self.backdrop.setGeometry(self.rect())
        width = min(self.panel_width, self.width())
        self.panel.setGeometry(self.width() - round(width * self._slide_progress), 0, width, self.height())

    def _set_slide_progress(self, progress):
        self._slide_progress = progress
        self._position()

    def _finish_slide(self):
        if not self._is_open and not self.isHidden():
            self.hide()
            self.panel_hidden.emit()

    def _animate_to(self, progress):
        self._slide.stop()
        if self._slide_progress == progress:
            self._finish_slide()
            return
        self._slide.setStartValue(self._slide_progress)
        self._slide.setEndValue(progress)
        self._slide.start()

    def show_panel(self):
        was_open = self._is_open
        self._is_open = True
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._position()
        self.show()
        self.raise_()
        if not was_open:
            self._animate_to(1.0)
        self.panel.setFocus(Qt.FocusReason.OtherFocusReason)

    def hide_panel(self, *, animated=True):
        if animated and not self._is_open:
            return
        self._is_open = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if animated:
            self._animate_to(0.0)
        else:
            self._slide.stop()
            self._set_slide_progress(0.0)
            self._finish_slide()

    def close_panel(self):
        self.hide_panel()

    def focusNextPrevChild(self, forward):
        # Tab이 가려진 화면으로 빠져나가지 않게 패널 내부를 순환한다.
        controls = [w for w in self.panel.findChildren(QWidget)
                    if w.isVisibleTo(self.panel) and w.isEnabled() and w.focusPolicy() & Qt.FocusPolicy.TabFocus]
        if not self._is_open or not controls:
            return super().focusNextPrevChild(forward)
        focus = QApplication.focusWidget()
        index = controls.index(focus) if focus in controls else (-1 if forward else 0)
        reason = Qt.FocusReason.TabFocusReason if forward else Qt.FocusReason.BacktabFocusReason
        controls[(index + (1 if forward else -1)) % len(controls)].setFocus(reason)
        return True
