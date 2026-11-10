"""대시보드·버전 히트맵 미리보기와 스크롤 배치."""

from PySide6.QtCore import QEvent, QPoint, QSize, QTimer, Qt
from PySide6.QtWidgets import QButtonGroup, QSizePolicy, QVBoxLayout, QWidget

from setron.ui.appearance import readable_color, refresh_widget_font, surface_edge
from setron.ui.appearance_data import HEATMAP_TIERS
from setron.ui.components import Card, button, label
from setron.ui.dashboard import DashboardGrid, dashboard_cell_colors
from setron.ui.heatmap import heatmap_cell_colors


def _sample_tile(title, detail):
    tile = Card(padding=12)
    tile.body.setSpacing(4)
    tile.body.addWidget(label(title, 14, medium=True, wrap=True))
    tile.body.addWidget(label(detail, 12, wrap=True))
    return tile


class AppearancePreview(QWidget):
    def __init__(self):
        super().__init__()
        layout = self._page(self)
        layout.addWidget(label("미리보기 화면 선택", 14, medium=True))
        group = QButtonGroup(self)
        self.switch_buttons = {}
        for key, title in (("dashboard", "대시보드"), ("heatmap", "버전 히트맵")):
            choice = button(title)
            choice.setCheckable(True)
            choice.setProperty("settingsOption", True)
            choice.clicked.connect(lambda checked=False, k=key: self.show_page(k))
            group.addButton(choice)
            self.switch_buttons[key] = choice
        layout.addWidget(DashboardGrid(list(self.switch_buttons.values()), minimum_width=128, maximum_columns=2, spacing=8))
        self.sample = Card(padding=20)
        self.title = label("", 18, medium=True, wrap=True)
        self.sample.body.addWidget(self.title)
        layout.addWidget(self.sample)

        self.dashboard_page = QWidget()
        dashboard_body = self._page(self.dashboard_page)
        self.metric_card = Card(padding=16)
        self.metric_card.body.setSpacing(6)
        self.metric_card.body.addWidget(label("목표 N 이상 달성률", 14, medium=True))
        self.value = label("60.0 %", 46, medium=True)
        self.metric_card.body.addWidget(self.value)
        self.metric_card.body.addWidget(label("48 / 80건 · 예시 데이터", 12, wrap=True))
        dashboard_body.addWidget(self.metric_card)
        dashboard_body.addWidget(label("구역별 달성률", 14, medium=True))
        self.dashboard_tiles = {
            "met": _sample_tile("100%", "목표 달성"),
            "below": _sample_tile("60%", "목표 미달"),
            "dashboard_unknown": _sample_tile("정보 부족", "확인 필요"),
            "risk": _sample_tile("후보 1대", "안전·결함 위험 후보"),
        }
        dashboard_body.addWidget(DashboardGrid(list(self.dashboard_tiles.values()), minimum_width=118,
                                               maximum_columns=2, spacing=8))
        self.sample.body.addWidget(self.dashboard_page)

        self.heatmap_page = QWidget()
        heatmap_body = self._page(self.heatmap_page)
        heatmap_body.addWidget(label("버전 비교 단계 · 예시 데이터", 14, medium=True, wrap=True))
        self.heatmap_tiles = {key: _sample_tile(f"DM-{number:03}", title)
                              for number, (key, title) in enumerate(HEATMAP_TIERS.values(), 1)}
        self.heatmap_risk = _sample_tile("DM-006", "N · 위험 후보")
        heatmap_body.addWidget(DashboardGrid([*self.heatmap_tiles.values(), self.heatmap_risk],
                                             minimum_width=118, maximum_columns=2, spacing=8))
        self.sample.body.addWidget(self.heatmap_page)
        self.show_page("dashboard")
        for child in (self.sample, *self.sample.findChildren(QWidget)):
            child.setProperty("themePreview", True)

    def show_page(self, key):
        self.switch_buttons[key].setChecked(True)
        self.title.setText("대시보드 미리보기" if key == "dashboard" else "버전 히트맵 미리보기")
        self.dashboard_page.setVisible(key == "dashboard")
        self.heatmap_page.setVisible(key == "heatmap")

    @staticmethod
    def _page(widget):
        body = QVBoxLayout(widget)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        return body

    @staticmethod
    def _paint_card(card, background, ink, border, *, radius=8, width=1):
        card.setStyleSheet(f"QFrame#card {{background:{background};border:{width}px solid {border};"
                           f"border-radius:{radius}px;}} QLabel {{color:{ink};}}")

    def render(self, colors, font_scale):
        surface, ink, line = colors["surface"], colors["ink"], colors["line"]
        selected = colors["selected"]
        self._paint_card(self.sample, surface, readable_color(ink, surface),
                         surface_edge(surface, colors["canvas"], line), radius=16)
        self._paint_card(self.metric_card, selected, readable_color(colors["muted"], selected),
                         surface_edge(selected, surface, line), radius=12)
        self.value.setStyleSheet(f"color:{readable_color(colors['rate'], selected)};")
        for key, tile in self.dashboard_tiles.items():
            background, foreground = dashboard_cell_colors(colors, key)
            self._paint_card(tile, background, foreground, surface_edge(background, surface, line))
        for key, tile in self.heatmap_tiles.items():
            background, foreground, border = heatmap_cell_colors(colors, key)
            self._paint_card(tile, background, foreground, border, radius=4)
        background, foreground, border = heatmap_cell_colors(colors, "heat_at", risk=True)
        self._paint_card(self.heatmap_risk, background, foreground, border, radius=4, width=2)
        for child in self.sample.findChildren(QWidget):
            child.setProperty("setronPreviewFontScale", font_scale)
            refresh_widget_font(child)


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
