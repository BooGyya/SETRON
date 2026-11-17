"""대시보드 집계 히트맵의 머리글과 위치 열을 고정하는 네 영역 표시."""

from PySide6.QtCore import QCoreApplication, QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import QFrame, QGridLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from setron.ui.appearance import manager


class DashboardMatrix(QWidget):
    LOCATION_WIDTH = 150
    MINIMUM_COLUMN_WIDTH = 128
    MAXIMUM_HEIGHT = 620

    def __init__(self):
        super().__init__()
        self.setAccessibleName("대시보드 집계 히트맵 · 설비군 머리글과 사업장·라인 고정")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.body = self._area("대시보드 히트맵 값")
        self.header = self._area("고정 설비군 머리글")
        self.row_header = self._area("고정 사업장·라인 열")
        for area in (self.header, self.row_header):
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.corner = QWidget()
        self.corner_body = QVBoxLayout(self.corner)
        self.corner_body.setContentsMargins(0, 0, 0, 0)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(6)
        self.grid.addWidget(self.corner, 0, 0)
        self.grid.addWidget(self.header, 0, 1)
        self.grid.addWidget(self.row_header, 1, 0)
        self.grid.addWidget(self.body, 1, 1)
        self.grid.setColumnStretch(1, 1)
        self.grid.setRowStretch(1, 1)
        self._natural_height = None
        self._measure_timer, self._sync_timer = QTimer(self), QTimer(self)
        for timer in (self._measure_timer, self._sync_timer):
            timer.setSingleShot(True)
        self._measure_timer.timeout.connect(self._measure)
        self._sync_timer.timeout.connect(self._sync_viewports)
        self.body.viewport().installEventFilter(self)
        for widget in (self.header.viewport(), self.row_header.viewport(), self.corner):
            widget.installEventFilter(self)
        self.header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.row_header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.body.horizontalScrollBar().valueChanged.connect(self.header.horizontalScrollBar().setValue)
        self.body.verticalScrollBar().valueChanged.connect(self.row_header.verticalScrollBar().setValue)
        for bar in (self.body.horizontalScrollBar(), self.body.verticalScrollBar()):
            bar.rangeChanged.connect(self._queue_sync)
        current = manager()
        if current is not None:
            current.changed.connect(self._queue_measure)

    @staticmethod
    def _area(name):
        area = QScrollArea()
        area.setAccessibleName(name)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidgetResizable(True)
        area.setMinimumSize(0, 0)
        area.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        return area

    def minimumSizeHint(self):
        return QSize(0, self.minimumHeight())

    def widget(self):
        return self.body.widget()

    def viewport(self):
        return self.body.viewport()

    def horizontalScrollBar(self):
        return self.body.horizontalScrollBar()

    def verticalScrollBar(self):
        return self.body.verticalScrollBar()

    def ensureWidgetVisible(self, widget, xmargin=50, ymargin=50):
        self.body.ensureWidgetVisible(widget, xmargin, ymargin)

    def set_matrix(self, content, headings, locations, corner):
        for area, widget in ((self.body, content), (self.header, headings), (self.row_header, locations)):
            old = area.takeWidget()
            if old is not None:
                old.hide()
                old.deleteLater()
            area.setWidget(widget)
        while self.corner_body.count():
            old = self.corner_body.takeAt(0).widget()
            old.hide()
            old.deleteLater()
        self.corner_body.addWidget(corner)
        self._measure()
        self.body.horizontalScrollBar().setValue(0)
        self.body.verticalScrollBar().setValue(0)

    def _queue_measure(self, *_):
        self._measure_timer.start(0)

    def _queue_sync(self, *_):
        if not self._sync_timer.isActive():
            self._sync_timer.start(0)

    def _measure(self):
        if self.body.widget() is None:
            return
        body, header, locations = (area.widget() for area in (self.body, self.header, self.row_header))
        values, headings, positions = body.layout(), header.layout(), locations.layout()
        columns = headings.columnCount()
        widths = []
        for column in range(columns):
            heading = headings.itemAtPosition(0, column).widget()
            width = max(self.MINIMUM_COLUMN_WIDTH, heading.fontMetrics().horizontalAdvance(heading.text()) + 24)
            for row in range(values.rowCount()):
                item = values.itemAtPosition(row, column)
                if item is None or item.widget().objectName() == "dashboardMatrixSiteDivider":
                    continue
                width = max(width, item.widget().layout().minimumSize().width())
            widths.append(width)
            for grid in (values, headings):
                grid.setColumnMinimumWidth(column, width)
                grid.setColumnStretch(column, 1)
        natural_width = sum(widths) + values.horizontalSpacing() * max(0, columns - 1)
        for widget in (body, header):
            widget.setMinimumWidth(natural_width)
        self.corner.setFixedWidth(self.LOCATION_WIDTH)
        self.row_header.setFixedWidth(self.LOCATION_WIDTH)
        locations.setFixedWidth(self.LOCATION_WIDTH)
        natural_height = 0
        for row in range(values.rowCount()):
            location = positions.itemAtPosition(row, 0).widget()
            location.setFixedWidth(self.LOCATION_WIDTH)
            widgets = [location, *(values.itemAtPosition(row, column).widget() for column in range(columns)
                                  if values.itemAtPosition(row, column) is not None)]
            if location.objectName() == "dashboardMatrixSiteDivider":
                height = 1
            else:
                height = max(54, *(widget.heightForWidth(self.LOCATION_WIDTH) if widget.hasHeightForWidth()
                                   else widget.sizeHint().height() for widget in widgets))
            for widget in widgets:
                widget.setFixedHeight(height)
            values.setRowMinimumHeight(row, height)
            positions.setRowMinimumHeight(row, height)
            natural_height += height
        natural_height += values.verticalSpacing() * max(0, values.rowCount() - 1)
        for widget in (body, locations):
            widget.setMinimumHeight(natural_height)
        header_height = max(40, *(headings.itemAtPosition(0, column).widget().sizeHint().height()
                                 for column in range(columns)))
        self.header.setFixedHeight(header_height)
        self.corner.setFixedHeight(header_height)
        self._natural_height = header_height + self.grid.verticalSpacing() + natural_height
        self.setFixedHeight(min(self.MAXIMUM_HEIGHT, self._natural_height + self.body.horizontalScrollBar().sizeHint().height()))
        self._queue_sync()

    def _sync_viewports(self):
        # 머리글에는 본문의 세로 스크롤바 폭을, 위치 열에는 가로 스크롤바 높이를 남긴다.
        right = max(0, self.body.width() - self.body.viewport().width())
        bottom = max(0, self.body.height() - self.body.viewport().height())
        self.header.setViewportMargins(0, 0, right, 0)
        self.row_header.setViewportMargins(0, 0, 0, bottom)
        self.header.horizontalScrollBar().setValue(self.body.horizontalScrollBar().value())
        self.row_header.verticalScrollBar().setValue(self.body.verticalScrollBar().value())
        if self._natural_height is not None:
            horizontal = self.body.horizontalScrollBar()
            extra = horizontal.sizeHint().height() if horizontal.maximum() > 0 else 0
            height = min(self.MAXIMUM_HEIGHT, self._natural_height + extra)
            if self.height() != height:
                self.setFixedHeight(height)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and watched in (self.header.viewport(), self.row_header.viewport(), self.corner):
            QCoreApplication.sendEvent(self.body.viewport(), event)
            return True
        if watched is self.body.viewport() and event.type() == QEvent.Type.Resize:
            self._queue_sync()
        return super().eventFilter(watched, event)
