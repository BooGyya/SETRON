"""현재 선택 행과 무관하게 입력칸 아래에서 열리는 공통 Qt 드롭다운."""

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QProxyStyle, QScrollArea, QStyle
from shiboken6 import isValid


class _DropdownStyle(QProxyStyle):
    def __init__(self, parent):
        super().__init__("Fusion")
        self.setParent(parent)

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_ComboBox_Popup:
            return 0  # 선택 행에 맞춰 필드 위로 겹치는 메뉴 대신 스크롤 가능한 목록.
        return super().styleHint(hint, option, widget, returnData)


class ComboBox(QComboBox):
    """선택·검색·직접 입력은 Qt에 맡기고, 팝업의 앵커와 사용 공간만 통일한다."""

    popup_gap = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dropdown_style = _DropdownStyle(self)
        self.setStyle(self._dropdown_style)
        self._scroll_restore = None
        self.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def _preferred_height(self):
        row = max(0, self.currentIndex())
        height = max(self.view().sizeHintForRow(row), self.fontMetrics().height() + 8)
        return min(height * min(self.count(), self.maxVisibleItems()) + 12,
                   self.screen().availableGeometry().height() // 2)

    def _make_room(self):
        """화면 끝의 필드는 잠시 위로 스크롤해 아래쪽 선택 공간을 확보한다."""
        below = self.screen().availableGeometry().bottom() - self.mapToGlobal(QPoint(0, self.height())).y()
        missing = self._preferred_height() + self.popup_gap - below
        if missing <= 0:
            return
        area = self.parentWidget()
        while area is not None and not isinstance(area, QScrollArea):
            area = area.parentWidget()
        if area is None or area.widget() is None:
            return
        content, bar = area.widget(), area.verticalScrollBar()
        body = content.layout()
        self._scroll_restore = (body, body.contentsMargins() if body else None, bar, bar.value())
        target = bar.value() + missing
        if target > bar.maximum() and body is not None:
            # 페이지 마지막 필드에도 적용한다. 닫힐 때 원래 여백·스크롤을 복원한다.
            margins = body.contentsMargins()
            body.setContentsMargins(margins.left(), margins.top(), margins.right(),
                                    margins.bottom() + target - bar.maximum())
            body.activate()
        below = self.screen().availableGeometry().bottom() - self.mapToGlobal(QPoint(0, self.height())).y()
        bar.setValue(bar.value() + max(0, self._preferred_height() + self.popup_gap - below))

    def _restore_scroll(self):
        state, self._scroll_restore = self._scroll_restore, None
        if state is not None:
            body, margins, bar, value = state
            if body is not None and isValid(body):
                body.setContentsMargins(margins)
                body.activate()
            if isValid(bar):
                bar.setValue(value)

    def showPopup(self):
        if not self.count() or not self.isEnabled():
            return
        self.ensurePolished()
        self._restore_scroll()
        popup = self.view().window()
        popup.installEventFilter(self)
        self._make_room()
        super().showPopup()
        self._position_popup()
        # 스크롤바가 새로 생기며 수행하는 마지막 레이아웃까지 같은 앵커에 맞춘다.
        QTimer.singleShot(0, self._position_popup)

    def _position_popup(self):
        if not isValid(self):
            return
        popup = self.view().window()
        if not popup.isVisible():
            self._restore_scroll()
            return
        anchor = self.mapToGlobal(QPoint(0, self.height() + self.popup_gap))
        screen = self.screen().availableGeometry()
        height = max(1, min(popup.height(), self._preferred_height(), screen.bottom() - anchor.y() + 1))
        width = min(max(self.width(), popup.width()), screen.width())
        left = max(screen.left(), min(anchor.x(), screen.right() - width + 1))
        popup.setMinimumWidth(0)
        popup.setGeometry(left, anchor.y(), width, height)
        self.view().scrollTo(self.view().currentIndex(), QAbstractItemView.ScrollHint.EnsureVisible)

    def hidePopup(self):
        super().hidePopup()
        self._restore_scroll()

    def eventFilter(self, watched, event):
        # 바깥 클릭으로 Qt가 팝업을 직접 닫는 경우도 임시 스크롤 공간을 복원한다.
        if event.type() == QEvent.Type.Hide and watched is self.view().window():
            self._restore_scroll()
        return super().eventFilter(watched, event)
