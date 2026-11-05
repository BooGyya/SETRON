"""공통 창·사이드바·페이지 등록/이동과 공유 조회 조건을 제공한다."""

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLayout, QMainWindow, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from setron.ui.components import EmptyState, PageHeader, StatusBadge, label
from setron.ui.query_panel import QueryPanel
from setron.ui.query_state import QueryCatalog, QueryState, preview_catalog
from setron.ui.theme import ASSETS, INK, PRIMARY, SECONDARY, font, icon, svg_size


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
    Menu("replay", "Version Replay", *_ver5("replay")),
    Menu("versions", "버전 관리", *_ver5("versions")),
    # VER5 사이드바에 없는 기존 메뉴. 기능을 유지하고 데이터 관리 묶음에 둔다.
    Menu("management", "관리 데이터", *_ver5("management", "settings")),
    Menu("history", "이력 조회", *_ver5("history")),
    Menu("reports", "보고서", *_ver5("reports")),
    Menu("settings", "기준 · 설정", *_ver5("settings")),
)
# 첫 묶음(현황 분석)은 제목 없이 표시한다.
MENU_GROUPS = (
    (None, ("dashboard", "heatmap", "items", "ai", "import", "replay")),
    ("데이터 관리", ("versions", "management", "history")),
    ("운영 설정", ("reports", "settings")),
)


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
        self.picture.setPixmap(icon(asset).pixmap(svg_size(asset)))
        self.caption.setStyleSheet(f"color: {PRIMARY if selected else INK if self.isEnabled() else '#8b95a8'};")

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, "caption"):
            self._sync_visual()


class PageStack(QWidget):
    """위젯은 보존하되 현재 페이지만 배치해 숨겨진 페이지의 최소 높이를 격리한다."""

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
        content = QWidget()
        content.setObjectName("canvas")
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(28, 28, 28, 28)
        self.body.setSpacing(20)
        self.header = PageHeader()
        self.body.addWidget(self.header)
        self.notice = StatusBadge("작업 단위 3 · 공통 조회 조건 · 업무 데이터 미연결", "info")
        self.body.addWidget(self.notice)
        self.query_panel = QueryPanel(self.query_state)
        self.query_panel.applied.connect(self.query_applied.emit)
        self.body.addWidget(self.query_panel)
        self.stack = PageStack()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.placeholder = EmptyState(
            "공통 화면 준비가 완료되었습니다",
            "Site·Fab·Line·설비군을 선택하고 조회 적용으로 조건을 확정할 수 있습니다.\n"
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
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = label("")
        logo.setObjectName("semesLogo")
        logo.setFixedSize(80, 19)
        logo.setPixmap(QPixmap(str(ASSETS / "ff5f9.png")).scaled(
            80, 19, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(logo)
        divider = QFrame()
        divider.setFixedSize(1, 18)
        divider.setStyleSheet("background:#d5d7da;")
        brand.addWidget(divider)
        brand_name = label("SETRON", 18, numeric=True)
        brand_font = font(18, numeric=True)
        brand_font.setWeight(QFont.Weight.DemiBold)
        brand_name.setFont(brand_font)
        brand.addWidget(brand_name)
        brand.addStretch()
        signature = QWidget()
        signature.setFixedHeight(28)
        brand.setContentsMargins(0, 0, 0, 0)
        signature.setLayout(brand)
        side.addWidget(signature)
        side.addWidget(label("SW 버전 현황 관리", 13, color=INK))
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
        side.addStretch()
        self.sidebar_scroll.setWidget(content)
        outer.addWidget(self.sidebar_scroll)
        return sidebar

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

    def navigate(self, key: str) -> bool:
        if key not in self._menus and key not in self._page_parents:
            raise ValueError(f"알 수 없는 메뉴입니다: {key}")
        if key not in self.pages and key != "dashboard":
            return False
        current = self.pages.get(key, self.placeholder)
        for index in range(self.stack.count()):
            page = self.stack.widget(index)
            page.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Preferred if page is current else QSizePolicy.Policy.Ignored)
        self.stack.setCurrentWidget(current)
        self.stack.updateGeometry()
        self.route = key
        self.query_panel.setVisible(key in self._query_routes)
        menu_key = self._page_parents.get(key, key)
        self.header.title.setText(self._page_titles.get(key, self._menus[menu_key].title))
        for menu_key, nav in self.nav.items():
            nav.setChecked(menu_key == self._page_parents.get(key, key))
        self.scroll.verticalScrollBar().setValue(0)
        self.page_changed.emit(key)
        return True

    def set_query_catalog(self, catalog: QueryCatalog) -> None:
        """검증된 선택지만 주입한다. 원본 구역명을 Fab/Line으로 추론하지 않는다."""
        self.query_panel.set_catalog(catalog)

    def closeEvent(self, event):
        """DB 저장 결과를 확인하기 전에 앱을 닫아 결과 안내를 잃지 않게 한다."""
        guard = getattr(self, "close_guard", None)
        if guard is not None and not guard():
            self.notice.setText("관리 데이터 저장 중입니다. 저장 결과를 확인한 뒤 닫아 주세요.")
            event.ignore()
            return
        super().closeEvent(event)

