"""설정의 오른쪽 미리보기를 스크롤 범위가 늘어나지 않게 배치한다."""

from PySide6.QtCore import QEvent, QPoint, QSize, QTimer
from PySide6.QtWidgets import QSizePolicy, QWidget


class PreviewColumn(QWidget):
    def __init__(self, card, scroll_area, *, breakpoint):
        super().__init__()
        self.card, self.breakpoint = card, breakpoint
        self.viewport = scroll_area.viewport() if scroll_area is not None else None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._place)
        card.setParent(self)
        card.installEventFilter(self)
        card.show()
        if scroll_area is not None:
            scroll_area.verticalScrollBar().valueChanged.connect(self._place)
            scroll_area.verticalScrollBar().rangeChanged.connect(self.refresh)
            self.viewport.installEventFilter(self)

    def _card_height(self):
        return max(self.card.sizeHint().height(), self.card.heightForWidth(self.width()))

    def sizeHint(self):
        return QSize(self.card.sizeHint().width(), self._card_height())

    def minimumSizeHint(self):
        return QSize(0, self._card_height())

    def refresh(self, *_):
        self.updateGeometry()
        self._layout_timer.start(0)

    def _place(self, *_):
        if not self.isVisible():
            return
        height = min(self.height(), self._card_height())
        top = 0
        if self.viewport is not None and self.parentWidget().width() >= self.breakpoint:
            # 열 자체는 그대로 두고 카드를 뷰포트 상단까지 따라오게 한다.
            column_y = self.mapToGlobal(QPoint()).y()
            viewport_y = self.viewport.mapToGlobal(QPoint(0, 16)).y()
            top = min(max(0, viewport_y - column_y), self.height() - height)
        self.card.setGeometry(0, top, self.width(), height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh()

    def eventFilter(self, watched, event):
        if ((watched is self.card and event.type() == QEvent.Type.LayoutRequest)
                or (watched is self.viewport and event.type() == QEvent.Type.Resize)):
            self.refresh()
        return super().eventFilter(watched, event)
