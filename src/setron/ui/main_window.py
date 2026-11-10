"""공통 창·사이드바·페이지 등록/이동과 공유 조회 조건을 제공한다."""

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLayout, QMainWindow, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from setron.ui.components import EmptyState, PageHeader, StatusBadge, label
from setron.ui.query_panel import QueryPanel
from setron.ui.query_state import QueryCatalog, QueryState, preview_catalog
from setron.ui.theme import ASSETS, INK, PRIMARY, SECONDARY, icon, svg_size


@dataclass(frozen=True)
class Menu:
    key: str
    title: str
    normal_icon: str
    active_icon: str


def _ver5(key: str, owner: str | None = None) -> tuple[str, str]:
    # Figma VER5 사이드바 원본 아이콘: 기본은 글자색(#142033), 선택은 강조색(#3C5CE9)이다.
    owner = owner or key
    return f"ver5/{owner}.svg", f"ver5/active-{owner}.svg"


MENUS = (
    Menu("dashboard", "대시보드", *_ver5("dashboard")),
    Menu("heatmap", "버전 히트맵", *_ver5("heatmap")),
    Menu("items", "아이템 매니저", *_ver5("items")),
    Menu("ai", "AI 분석", *_ver5("ai")),
    Menu("import", "데이터 가져오기", *_ver5("import")),
    Menu("replay", "버전 리플레이", *_ver5("replay")),
    # 데이터 관리 (11-10 개편): 설비현황 · 릴리즈노트 · 변경 이력. 기존 버전 관리·관리 데이터 메뉴를 대신한다.
    Menu("data", "데이터 관리", *_ver5("versions")),
    Menu("versions", "버전 관리", *_ver5("versions")),
    # VER5 사이드바에 없는 기존 메뉴. 다른 화면의 내부 이동(레코드 열기 등)용으로 남기고 사이드바에는 숨긴다.
    Menu("management", "관리 데이터", *_ver5("management", "settings")),
    Menu("reports", "보고서", *_ver5("reports")),
    Menu("settings", "설정", *_ver5("settings")),
)
# 첫 묶음(현황 분석)은 제목 없이 표시한다.
MENU_GROUPS = (
    (None, ("dashboard", "heatmap", "items", "ai", "import", "replay")),
    ("데이터 관리", ("data",)),
    ("운영 설정", ("reports", "settings")),
)
# 사이드바에 보이지 않지만 등록·내부 이동은 되는 메뉴 (11-10 데이터 관리 개편 전 화면).
HIDDEN_MENUS = ("versions", "management")


class BrandLogo(QLabel):
    """밝게/어둡게 모두 같은 크기로 원본 로고를 표시한다."""

    def __init__(self):
        super().__init__()
        self.setObjectName("setronLogo")
        self.setAccessibleName("SETRON 로고 · SEMES Tracking Monitoring")
        self.setProperty("brandAsset", "setron-logo.png")
        self.setFixedSize(128, 36)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._source = QPixmap(str(ASSETS / self.property("brandAsset")))
        visible = QRegion(self._source.mask()).boundingRect()
        self._viewbox = (visible.adjusted(-1, -1, 1, 1).intersected(self._source.rect())
                         if not visible.isEmpty() else self._source.rect())
        from setron.ui.appearance import manager
        if controller := manager():
            controller.changed.connect(self._sync_brand)
        self._sync_brand()

    def _sync_brand(self):
        from setron.ui.appearance import manager
        controller = manager()
        dark = bool(controller and controller.dark)
        # PNG 자체는 바꾸지 않고 표시할 때만 투명 여백을 제외한다.
        ratio = self.devicePixelRatioF()
        picture = self._source.copy(self._viewbox).scaled(self.size() * ratio, Qt.AspectRatioMode.KeepAspectRatio,
                                                         Qt.TransformationMode.SmoothTransformation)
        if dark:
            # 같은 PNG의 알파를 유지해 자간·글자 형태를 바꾸지 않고 흰색으로 표시한다.
            tint = QPainter(picture)
            tint.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            tint.fillRect(picture.rect(), Qt.GlobalColor.white)
            tint.end()
        picture.setDevicePixelRatio(ratio)
        # 모드 전환 시에도 투명 슬롯 크기와 메뉴 위치를 고정한다.
        canvas = QPixmap(self.size() * ratio)
        canvas.setDevicePixelRatio(ratio)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawPixmap(round((self.width() - picture.deviceIndependentSize().width()) / 2),
                           round((self.height() - picture.deviceIndependentSize().height()) / 2), picture)
        painter.end()
        self.setPixmap(canvas)
        self.setProperty("displayedBrandAsset", self.property("brandAsset"))
        self.setProperty("brandMode", "dark" if dark else "light")

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.Type.Show, QEvent.Type.DevicePixelRatioChange) and hasattr(self, "_source"):
            self._sync_brand()
        return result


class NavButton(QPushButton):
    def __init__(self, menu: Menu):
        super().__init__()
        self.menu = menu
        self.setProperty("nav", True)
        self.setObjectName("nav_" + menu.key)
        self.setCheckable(True)
        self.setFixedHeight(46)
        self.setAccessibleName(menu.title)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 12, 0)
        row.setSpacing(12)
        self.picture = label("")
        self.picture.setFixedSize(18, 18)
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption = label(menu.title, 14, medium=True)
        for child in (self.picture, self.caption):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row.addWidget(child)
        row.addStretch()
        self.toggled.connect(self._sync_visual)
        self._sync_visual()

    def _sync_visual(self):
        selected = self.isChecked()
        asset = self.menu.active_icon if selected else self.menu.normal_icon
        self.picture.setProperty("assetFile", asset)
        from setron.ui.appearance import manager
        controller = manager()
        image = icon(asset)
        if controller:
            image = controller.themed_icon(image, svg_size(asset), controller.tokens["accent"] if selected else None)
        self.picture.setPixmap(image.pixmap(svg_size(asset)))
        ink = PRIMARY if selected else INK if self.isEnabled() else '#8b95a8'
        self.caption.setStyleSheet(f"color: {ink};")
        if controller:
            controller._widget(self.caption)

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, "caption"):
            self._sync_visual()


class PageStack(QWidget):
    """위젯은 보존하되 현재 페이지만 배치해 숨겨진 페이지의 최소 높이를 격리한다."""

    current_changed = Signal()

    def __init__(self):
        super().__init__()
        self._pages: list[QWidget] = []
        self._current: QWidget | None = None
        self._body = QVBoxLayout(self)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

    def addWidget(self, page: QWidget) -> None:
        if page in self._pages:
            raise ValueError("이미 등록된 페이지입니다.")
        page.setParent(self)
        page.hide()
        self._pages.append(page)

    def count(self) -> int:
        return len(self._pages)

    def widget(self, index: int) -> QWidget:
        return self._pages[index]

    def currentWidget(self) -> QWidget | None:
        return self._current

    def setCurrentWidget(self, page: QWidget) -> None:
        if page not in self._pages:
            raise ValueError("등록되지 않은 페이지입니다.")
        if self._current is page:
            return
        if self._current is not None:
            self._body.removeWidget(self._current)
            self._current.hide()
        self._current = page
        self._body.addWidget(page)
        page.show()
        self._body.invalidate()
        self.updateGeometry()
        self.current_changed.emit()


class _ContentCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("canvas")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self._preview_background = QColor()

    def set_preview_background(self, value):
        color = QColor(value) if value else QColor()
        if color != self._preview_background:
            self._preview_background = color
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._preview_background.isValid():
            painter = QPainter(self)
            painter.fillRect(event.rect(), self._preview_background)
            painter.end()


class MainWindow(QMainWindow):
    page_changed = Signal(str)
    query_applied = Signal(object)

    def __init__(self, *, query_catalog: QueryCatalog | None = None):
        super().__init__()
        self.setWindowTitle("SETRON · 공통 UI 미리보기")
        self.setMinimumSize(960, 600)
        self.resize(1440, 960)
        self.pages: dict[str, QWidget] = {}
        self.route = "dashboard"
        self._route_history: list[tuple[str, int]] = []
        self._navigation_id = 0
        self._page_back = None
        self._is_subpage = False
        self.nav: dict[str, NavButton] = {}
        self._menus = {menu.key: menu for menu in MENUS}
        self._page_parents: dict[str, str] = {}
        self._page_titles: dict[str, str] = {}
        self._query_routes = {"dashboard", "heatmap"}
        self.query_state = QueryState(query_catalog if query_catalog is not None else preview_catalog())
        root = QWidget()
        root.setObjectName("canvas")
        self.setCentralWidget(root)
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._sidebar())
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.canvas = content = _ContentCanvas()
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(28, 28, 28, 28)
        self.body.setSpacing(20)
        self.header = PageHeader()
        self.header.back.clicked.connect(self.go_back)
        self.back_shortcut = QAction("뒤로 가기", self)
        self.back_shortcut.setShortcuts(QKeySequence.StandardKey.Back)
        self.back_shortcut.triggered.connect(self.go_back)
        self.addAction(self.back_shortcut)
        self.body.addWidget(self.header)
        # 실제 업무 자료가 아닐 때(DB 미연결로 합성 예시 자료 등)의 경고. 공통 안내 칸(notice)과 달리
        # 화면별 숨김 규칙을 따르지 않고 모든 화면에 계속 보인다 (QA Q-03).
        self.data_warning = StatusBadge("", "danger")
        self.data_warning.setObjectName("dataWarning")
        self.data_warning.setWordWrap(True)
        self.data_warning.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.data_warning.hide()
        self.body.addWidget(self.data_warning)
        self.notice = StatusBadge("작업 단위 3 · 공통 조회 조건 · 업무 데이터 미연결", "info")
        self.body.addWidget(self.notice)
        self.query_panel = QueryPanel(self.query_state)
        self.query_panel.applied.connect(self.query_applied.emit)
        self.body.addWidget(self.query_panel)
        self.stack = PageStack()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.placeholder = EmptyState(
            "공통 화면 준비가 완료되었습니다",
            "Site·Fab·Line·설비군 선택을 완료하면 조회 조건이 반영됩니다.\n"
            "대시보드의 지표·그래프, 히트맵, 설비 목록·상세는 아직 연결하지 않았습니다.\n\n"
            "회색 메뉴는 아직 업무 화면이 연결되지 않아 사용할 수 없습니다.",
        )
        self.stack.addWidget(self.placeholder)
        self.body.addWidget(self.stack)
        self.body.addStretch()
        self.scroll.setWidget(content)
        row.addWidget(self.scroll, 1)
        self.navigate("dashboard")

    def _sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(224)
        outer = QVBoxLayout(sidebar)
        outer.setContentsMargins(0, 0, 0, 0)
        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("sidebarContent")
        side = QVBoxLayout(content)
        side.setContentsMargins(20, 20, 20, 20)
        side.setSpacing(10)
        side.addWidget(BrandLogo(), alignment=Qt.AlignmentFlag.AlignHCenter)
        description = label("SW 버전 현황 관리", 13, color=INK)
        description.setObjectName("serviceDescription")
        side.addWidget(description)
        for title, keys in MENU_GROUPS:
            group = QVBoxLayout()
            group.setSpacing(4)
            if title is not None:
                caption = label(title, 11, color=INK)
                caption.setObjectName("navGroup")
                side.addWidget(caption)
            for key in keys:
                menu = self._menus[key]
                nav = NavButton(menu)
                nav.setEnabled(menu.key == "dashboard")
                nav.setToolTip(menu.title if nav.isEnabled() else menu.title + " · 준비 중")
                nav.clicked.connect(lambda checked=False, key=menu.key: self.navigate(key))
                self.nav[menu.key] = nav
                group.addWidget(nav)
            side.addLayout(group)
        for key in HIDDEN_MENUS:
            nav = NavButton(self._menus[key])
            nav.setParent(content)
            nav.setEnabled(False)
            nav.hide()
            nav.clicked.connect(lambda checked=False, key=key: self.navigate(key))
            self.nav[key] = nav
        side.addStretch()
        self.sidebar_scroll.setWidget(content)
        outer.addWidget(self.sidebar_scroll)
        return sidebar

    def set_data_warning(self, text: str | None) -> None:
        """실제 자료가 아님을 알리는 경고를 모든 화면에 계속 띄운다. 빈 문구면 숨긴다 (QA Q-03)."""
        text = (text or "").strip()
        self.data_warning.setText(text)
        self.data_warning.setToolTip(text)
        self.data_warning.setVisible(bool(text))

    def register_page(self, key: str, widget: QWidget, *, uses_query: bool | None = None) -> None:
        """담당 QWidget을 알려진 메뉴에 한 번 등록한다. 등록한 위젯의 소유자는 이 창이다."""
        if key not in self._menus:
            raise ValueError(f"알 수 없는 메뉴입니다: {key}")
        if key in self.pages:
            raise ValueError(f"이미 등록된 메뉴입니다: {key}")
        if not isinstance(widget, QWidget) or widget in (self, self.placeholder) or widget in self.pages.values():
            raise ValueError("각 메뉴에 별도의 QWidget을 등록해 주세요.")
        if uses_query is not None and not isinstance(uses_query, bool):
            raise ValueError("uses_query는 bool 또는 None이어야 합니다.")
        if uses_query is True:
            self._query_routes.add(key)
        elif uses_query is False:
            self._query_routes.discard(key)
        self.pages[key] = widget
        self.stack.addWidget(widget)
        self.nav[key].setEnabled(True)
        self.nav[key].setToolTip(self._menus[key].title)
        if key == self.route:
            self.navigate(key)

    def register_auxiliary_page(self, key: str, widget: QWidget, *, parent_key: str, title: str, uses_query: bool = True) -> None:
        """목록/상세 같은 하위 화면은 새 사이드바 메뉴 없이 기존 메뉴 아래에 등록한다."""
        if not isinstance(key, str) or not key or key in self._menus or key in self.pages:
            raise ValueError("중복되지 않는 하위 화면 key가 필요합니다.")
        if parent_key not in self._menus or parent_key not in self.pages:
            raise ValueError("등록된 상위 메뉴가 필요합니다.")
        if not isinstance(title, str) or not title.strip() or not isinstance(uses_query, bool):
            raise ValueError("하위 화면 제목과 조회 패널 여부가 올바르지 않습니다.")
        if not isinstance(widget, QWidget) or widget in (self, self.placeholder) or widget in self.pages.values():
            raise ValueError("각 화면에 별도의 QWidget을 등록해 주세요.")
        self._page_parents[key], self._page_titles[key] = parent_key, title
        self.pages[key] = widget
        self.stack.addWidget(widget)
        if uses_query:
            self._query_routes.add(key)

    def navigate(self, key: str, *, record_history: bool = True) -> bool:
        if key not in self._menus and key not in self._page_parents:
            raise ValueError(f"알 수 없는 메뉴입니다: {key}")
        if key not in self.pages and key != "dashboard":
            return False
        if key != self.route:
            if record_history:
                self._route_history.append((self.route, self.scroll.verticalScrollBar().value()))
            self._page_back = None
            self._is_subpage = False
        self._navigation_id += 1
        current = self.pages.get(key, self.placeholder)
        for index in range(self.stack.count()):
            page = self.stack.widget(index)
            page.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Preferred if page is current else QSizePolicy.Policy.Ignored)
        self.stack.setCurrentWidget(current)
        self.stack.updateGeometry()
        self.route = key
        # 펼침 행이 스크롤 임계점을 지나도 목록의 가로 폭을 유지한다.
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn if key == "items"
                                               else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.query_panel.setVisible(key in self._query_routes)
        menu_key = self._page_parents.get(key, key)
        self.header.title.setText(self._page_titles.get(key, self._menus[menu_key].title))
        for menu_key, nav in self.nav.items():
            nav.setChecked(menu_key == self._page_parents.get(key, key))
        self.scroll.verticalScrollBar().setValue(0)
        self.page_changed.emit(key)
        self._sync_back()
        return True

    def set_page_back(self, action, *, subpage: bool = False) -> None:
        """현재 화면 안에서 먼저 되돌릴 패널 또는 상세가 있으면 연결한다."""
        self._page_back = action
        self._is_subpage = subpage
        self._sync_back()

    def _sync_back(self) -> None:
        enabled = self._page_back is not None or bool(self._route_history)
        subpage = self._is_subpage or self.route in self._page_parents
        self.header.back.setEnabled(enabled)
        self.header.back.setVisible(subpage and enabled)
        for key in ("export", "settings"):
            self.header.actions[key].setVisible(subpage)
        self.back_shortcut.setEnabled(enabled)

    def go_back(self) -> bool:
        if self._page_back is not None:
            self._page_back()
            return True
        return self.go_back_route()

    def go_back_route(self, expected: str | None = None) -> bool:
        if not self._route_history:
            return False
        key, offset = self._route_history[-1]
        if expected is not None and key != expected:
            return False
        self._route_history.pop()
        navigation_id = self._navigation_id + 1
        self.navigate(key, record_history=False)

        def restore_scroll():
            if self._navigation_id == navigation_id:
                self.scroll.verticalScrollBar().setValue(offset)

        QTimer.singleShot(0, self, lambda: QTimer.singleShot(0, self, restore_scroll))
        return True

    def set_query_catalog(self, catalog: QueryCatalog) -> None:
        """검증된 선택지만 주입한다. 원본 구역명을 Fab/Line으로 추론하지 않는다."""
        self.query_panel.set_catalog(catalog)

    def closeEvent(self, event):
        """DB 저장 결과를 확인하기 전에 앱을 닫아 결과 안내를 잃지 않게 한다."""
        guard = getattr(self, "close_guard", None)
        if guard is not None and not guard():
            self.notice.setText("데이터 저장·삭제 중입니다. 처리 결과를 확인한 뒤 닫아 주세요.")
            event.ignore()
            return
        super().closeEvent(event)

