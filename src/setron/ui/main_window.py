"""공통 창·사이드바·페이지 등록/이동과 공유 조회 조건을 제공한다."""

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLayout, QMessageBox, QMainWindow, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from setron.ui.components import EmptyState, PageHeader, StatusBadge, label
from setron.ui.query_panel import QueryPanel
from setron.ui.query_state import QueryCatalog, QueryState, preview_catalog
from setron.ui.theme import ASSETS, SECONDARY, font, icon, svg_size


@dataclass(frozen=True)
class Menu:
    key: str
    title: str
    normal_icon: str
    active_icon: str


MENUS = (
    Menu("dashboard", "대시보드", "7742b.svg", "26b8a.svg"),
    Menu("heatmap", "버전 히트맵", "0e6c6.svg", "f8ba2.svg"),
    Menu("items", "아이템 매니저", "7b6c5.svg", "77fb3.svg"),
    Menu("ai", "AI 분석", "a3b3c.svg", "6dc18.svg"),
    Menu("import", "데이터 가져오기", "30937.svg", "6cfa9.svg"),
    Menu("replay", "Version Replay", "e4697.svg", "1bd1e.svg"),
    Menu("versions", "버전 관리", "7b6c5.svg", "77fb3.svg"),
    Menu("history", "이력 조회", "d0b8c.svg", "04165.svg"),
    Menu("reports", "보고서", "b12e4.svg", "49591.svg"),
    Menu("settings", "기준 · 설정", "db27a.svg", "2543e.svg"),
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
        self.caption.setStyleSheet(f"color: {'white' if selected else SECONDARY if self.isEnabled() else '#8b95a8'};")

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
        self.header.action_requested.connect(self._header_action)
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
        side.setSpacing(6)
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
        divider.setStyleSheet("background:#e9eef6;")
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
        side.addWidget(label("SW 버전 현황 관리", 13, color=SECONDARY))
        saved = QVBoxLayout()
        saved.setSpacing(4)
        for i, menu in enumerate(MENUS):
            nav = NavButton(menu)
            nav.setEnabled(menu.key == "dashboard")
            nav.setToolTip(menu.title if nav.isEnabled() else menu.title + " · 준비 중")
            nav.clicked.connect(lambda checked=False, key=menu.key: self.navigate(key))
            self.nav[menu.key] = nav
            if i < 6:
                saved.addWidget(nav)
                if i == 5:
                    side.addLayout(saved)
            else:
                side.addWidget(nav)
        status = QFrame()
        status.setStyleSheet("QFrame {background:#f7f9fc;border-radius:10px;}")
        status_body = QVBoxLayout(status)
        status_body.setContentsMargins(12, 12, 12, 12)
        status_body.setSpacing(4)
        status_body.addWidget(label("업무 데이터 미연결", 12, medium=True, color=SECONDARY))
        status_body.addWidget(label("공통 UI 미리보기", 13, color=SECONDARY))
        side.addWidget(status)
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

    def navigate(self, key: str) -> bool:
        if key not in self._menus:
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
        self.header.title.setText(self._menus[key].title)
        for menu_key, nav in self.nav.items():
            nav.setChecked(menu_key == key)
        self.scroll.verticalScrollBar().setValue(0)
        self.page_changed.emit(key)
        return True

    def set_query_catalog(self, catalog: QueryCatalog) -> None:
        """검증된 선택지만 주입한다. 원본 구역명을 Fab/Line으로 추론하지 않는다."""
        self.query_panel.set_catalog(catalog)

    def _header_action(self, key: str) -> None:
        if key == "help":
            QMessageBox.information(
                self, "공통 UI 미리보기 안내",
                "공통 창·사이드바와 Site·Fab·Line·설비군 조회 조건을 제공합니다.\n\n"
                "• 업무 화면을 등록하면 해당 메뉴가 활성화됩니다.\n"
                "• 선택 변경·전체 조건으로 버튼은 선택 중인 조건만 바꿉니다.\n"
                "• 조회 적용을 눌러야 적용 조건과 요청 번호가 갱신됩니다.\n"
                "• 조건 적용은 데이터 조회 완료를 의미하지 않습니다.\n"
                "• 지표·목표 N·설비 데이터는 아직 연결하지 않았습니다.\n"
                "• 로그인이나 DB·Excel 연결을 시작하지 않습니다.",
            )
