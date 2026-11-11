"""공통 UI만 검증한다. 업무 화면·예시 설비·DB·Excel은 만들거나 호출하지 않는다."""

import ast
import hashlib
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontDatabase, QRegion
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QLineEdit, QVBoxLayout, QWidget

from setron.ui.app import main
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.main_window import MENUS, MainWindow
from setron.ui.theme import ASSETS, TONES, install_theme, svg_size


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    install_theme(instance)
    yield instance


@pytest.fixture
def window(app):
    result = MainWindow()
    result.show()
    QTest.qWait(20)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def test_shell_starts_without_business_pages(window):
    assert window.pages == {}
    assert window.route == "dashboard"
    assert window.stack.currentWidget() is window.placeholder
    assert window.header.title.text() == "대시보드"
    assert window.nav["dashboard"].isChecked()
    assert window.nav["dashboard"].isEnabled()
    assert all(not nav.isEnabled() for key, nav in window.nav.items() if key != "dashboard")
    assert "공통 조회 조건" in window.notice.text()
    assert not hasattr(window, "overview")
    assert not hasattr(window, "filters")


@pytest.mark.parametrize("size", [16, 24])
def test_window_title_uses_blue_tile_s_at_native_icon_sizes(window, size):
    assert window.windowTitle() == "SETRON"
    image = window.windowIcon().pixmap(size, size).toImage()
    assert (image.width(), image.height()) == (size, size)
    assert image.pixelColor(0, 0).alpha() == 0
    colors = {image.pixelColor(x, y).name() for y in range(size) for x in range(size)
              if image.pixelColor(x, y).alpha() == 255}
    assert {"#064786", "#ffffff"} <= colors


def test_sidebar_removes_standalone_history_menu(window):
    assert "history" not in window.nav
    assert not any(menu.key == "history" for menu in MENUS)
    assert not any(nav.accessibleName() == "이력 조회" for nav in window.nav.values())
    assert window.findChild(QWidget, "nav_history") is None
    assert {"versions", "management", "reports", "settings"} <= window.nav.keys()
    with pytest.raises(ValueError, match="알 수 없는"):
        window.navigate("history")


@pytest.mark.parametrize("route,title", [("replay", "버전 리플레이"), ("export", "데이터 내보내기"),
                                         ("data", "데이터 관리")])
def test_sidebar_route_uses_same_label_in_header(window, route, title):
    window.register_page(route, QWidget())
    nav = window.nav[route]
    assert nav.caption.text() == nav.accessibleName() == title
    QTest.mouseClick(nav, Qt.MouseButton.LeftButton)
    assert window.route == route and window.header.title.text() == title


def test_original_assets_and_fonts_are_usable(window):
    assert {"Inter", "IBM Plex Sans KR"} <= set(QFontDatabase.families())
    for asset in ASSETS.glob("*.svg"):
        assert asset.stat().st_size > 0
        renderer = QSvgRenderer(str(asset))
        assert renderer.isValid(), asset.name
        assert not renderer.defaultSize().isEmpty(), asset.name
    logo = window.findChild(QLabel, "setronLogo")
    assert not logo.pixmap().isNull()
    assert (logo.width(), logo.height()) == (128, 36)
    for menu in MENUS:
        nav = window.nav[menu.key]
        assert nav.height() == 46
        assert (nav.picture.width(), nav.picture.height()) == (18, 18)
        assert nav.accessibleName() == menu.title
        rendered_asset = nav.picture.property("assetFile")
        assert nav.picture.pixmap().deviceIndependentSize().toSize() == svg_size(rendered_asset)
        assert (ASSETS / menu.normal_icon).is_file()
        assert (ASSETS / menu.active_icon).is_file()


def test_sidebar_uses_latest_logo_with_matching_white_dark_render_and_no_backplate(window, app):
    from setron.ui.appearance import manager
    from setron.ui.appearance_data import Appearance
    logo = window.findChild(QLabel, "setronLogo")
    assert logo.property("brandAsset") == "setron-logo.png"
    assert logo.accessibleName() == "SETRON 로고 · SEMES Tracking Monitoring"
    # 승인한 자간 v2 PNG를 재인코딩하지 않고 패키지 자산으로 보관한다.
    assert hashlib.sha256((ASSETS / "setron-logo.png").read_bytes()).hexdigest() == (
        "02e1541111ebe082ff268427c03671b7307d327c2406ec5872967d746ae4451a")
    current, previous = manager(), manager().appearance
    try:
        current.apply(Appearance(mode="light"))
        app.processEvents()
        geometry = logo.geometry()
        menu_y = window.nav["dashboard"].y()
        original = logo.pixmap().toImage()
        painted_width = QRegion(logo.pixmap().mask()).boundingRect().width()
        assert logo.property("displayedBrandAsset") == "setron-logo.png"
        assert logo.property("brandMode") == "light"
        assert logo.pixmap().deviceIndependentSize().toSize() == logo.size()
        assert original.hasAlphaChannel()
        assert original.pixelColor(0, 0).alpha() == 0
        current.apply(Appearance(mode="dark"))
        app.processEvents()
        assert logo.property("displayedBrandAsset") == "setron-logo.png"
        assert logo.property("brandMode") == "dark"
        assert not logo.pixmap().isNull()
        assert logo.pixmap().toImage() != original
        white = logo.pixmap().toImage()
        for y in range(white.height()):
            for x in range(white.width()):
                pixel = white.pixelColor(x, y)
                assert pixel.alpha() == original.pixelColor(x, y).alpha()
                if pixel.alpha():
                    assert (pixel.red(), pixel.green(), pixel.blue()) == (255, 255, 255)
        assert logo.pixmap().deviceIndependentSize().toSize() == logo.size()
        assert abs(QRegion(logo.pixmap().mask()).boundingRect().width() - painted_width) <= 2 * logo.devicePixelRatioF()
        assert logo.pixmap().toImage().pixelColor(0, 0).alpha() == 0
        assert logo.geometry() == geometry
        assert window.nav["dashboard"].y() == menu_y
        assert "background" not in logo.styleSheet()
        current.apply(Appearance(mode="light"))
        assert logo.property("displayedBrandAsset") == "setron-logo.png"
        assert logo.pixmap().toImage() == original
        assert "background" not in logo.styleSheet()
    finally:
        current.apply(previous)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_sidebar_brand_respects_theme_at_startup(app, mode):
    from setron.ui.appearance import manager
    from setron.ui.appearance_data import Appearance
    current, previous = manager(), manager().appearance
    result = None
    try:
        current.apply(Appearance(mode=mode))
        result = MainWindow()
        result.show()
        app.processEvents()
        logo = result.findChild(QLabel, "setronLogo")
        assert logo.property("displayedBrandAsset") == "setron-logo.png"
        assert logo.property("brandMode") == mode
        assert (logo.width(), logo.height()) == (128, 36)
        assert abs(2 * logo.x() + logo.width() - logo.parentWidget().width()) <= 1
        painted = QRegion(logo.pixmap().mask()).boundingRect()
        assert abs(2 * painted.x() + painted.width() - logo.pixmap().width()) <= 2 * logo.devicePixelRatioF()
        assert logo.pixmap().deviceIndependentSize().toSize() == logo.size()
        assert "background" not in logo.styleSheet()
    finally:
        if result is not None:
            result.close()
            result.deleteLater()
        current.apply(previous)
        app.processEvents()


def test_sidebar_brand_header_and_menu_group_order(window):
    sidebar = window.findChild(QWidget, "sidebarContent")
    logos = [widget for widget in sidebar.findChildren(QLabel) if widget.property("brandAsset")]
    assert len(logos) == 1 and logos[0].objectName() == "setronLogo"
    assert window.findChild(QLabel, "semesLogo") is None
    assert not any(widget.text() in {"SETRON", "SEMES"} for widget in sidebar.findChildren(QLabel))
    assert abs(2 * logos[0].x() + logos[0].width() - sidebar.width()) <= 1
    assert logos[0].geometry().bottom() < window.nav["dashboard"].geometry().top()
    assert window.findChild(QLabel, "serviceDescription") is None
    assert not sidebar.findChildren(QLabel, "navGroup")
    menus = sorted((nav for nav in window.nav.values() if not nav.isHidden()), key=lambda nav: nav.y())
    assert [nav.caption.text() for nav in menus] == [
        "대시보드", "버전 히트맵", "버전 리플레이", "아이템 매니저", "AI분석",
        "데이터 가져오기", "데이터 내보내기", "데이터 관리", "보고서", "설정",
    ]
    dividers = sidebar.findChildren(QFrame, "sidebarDivider")
    assert len(dividers) == 2
    for divider, left, right in zip(dividers, ("ai", "data"), ("import", "reports")):
        assert window.nav[left].geometry().bottom() < divider.y() < window.nav[right].y()


def test_registration_enables_menu_and_updates_header(window, app):
    page = Card()
    page.body.addWidget(label("담당 화면 테스트"))
    window.register_page("import", page)
    spy = QSignalSpy(window.page_changed)
    QTest.mouseClick(window.nav["import"], Qt.MouseButton.LeftButton)
    app.processEvents()
    assert window.route == "import"
    assert window.stack.currentWidget() is page
    assert window.header.title.text() == "데이터 가져오기"
    assert window.nav["import"].isChecked()
    assert not window.nav["dashboard"].isChecked()
    assert window.nav["import"].picture.property("assetFile") == "ver5/active-download.svg"
    assert window.nav["dashboard"].picture.property("assetFile") == "ver5/dashboard.svg"
    assert spy.count() == 1 and spy.at(0) == ["import"]


def test_default_dashboard_can_be_registered_once(window):
    page = QWidget()
    window.register_page("dashboard", page)
    assert window.stack.currentWidget() is page
    with pytest.raises(ValueError, match="이미 등록"):
        window.register_page("dashboard", QWidget())
    with pytest.raises(ValueError):
        window.register_page("heatmap", page)


def test_invalid_or_unregistered_routes_do_not_change_selection(window):
    assert window.navigate("heatmap") is False
    assert window.route == "dashboard"
    with pytest.raises(ValueError, match="알 수 없는"):
        window.navigate("not-a-menu")
    with pytest.raises(ValueError):
        window.register_page("not-a-menu", QWidget())
    with pytest.raises(ValueError):
        window.register_page("import", None)
    assert window.pages == {}


@pytest.mark.parametrize("key", [menu.key for menu in MENUS])
def test_every_menu_has_a_working_registered_route(window, key):
    page = QWidget()
    window.register_page(key, page)
    assert window.navigate(key)
    assert window.stack.currentWidget() is page
    assert sum(nav.isChecked() for nav in window.nav.values()) == 1
    assert window.nav[key].picture.property("assetFile") == window.nav[key].menu.active_icon


def test_keyboard_navigation_and_page_state_are_preserved(window, app):
    page = QWidget()
    edit = QLineEdit()
    QVBoxLayout(page).addWidget(edit)
    edit.setText("담당 화면의 임시 입력")
    window.register_page("heatmap", page)
    window.nav["heatmap"].setFocus()
    QTest.keyClick(window.nav["heatmap"], Qt.Key.Key_Space)
    app.processEvents()
    assert window.route == "heatmap"
    window.navigate("dashboard")
    window.navigate("heatmap")
    assert edit.text() == "담당 화면의 임시 입력"


def test_back_retraces_distinct_pages_and_restores_scroll(window):
    tall = QWidget()
    tall.setMinimumHeight(2200)
    window.register_page("heatmap", tall)
    window.register_page("settings", QWidget())
    assert not window.header.back.isEnabled()
    assert not window.navigate("import")
    window.navigate("heatmap")
    window.navigate("heatmap")
    QTest.qWait(20)
    window.scroll.verticalScrollBar().setValue(400)
    window.navigate("settings")
    window.header.toolbar.hide()
    assert window.header.back.isHidden() and window.back_shortcut.isEnabled()
    assert all(window.header.actions[key].isHidden() for key in ("export", "settings"))

    window.back_shortcut.trigger()
    QTest.qWait(20)
    assert window.route == "heatmap" and window.nav["heatmap"].isChecked()
    assert window.scroll.verticalScrollBar().value() == 400
    window.back_shortcut.trigger()
    assert window.route == "dashboard" and not window.header.back.isEnabled()


def test_tall_hidden_page_does_not_stretch_short_page(window, app):
    baseline = window.scroll.verticalScrollBar().maximum()
    tall = QWidget()
    tall.setMinimumHeight(2200)
    window.register_page("heatmap", tall)
    window.navigate("heatmap")
    QTest.qWait(20)
    assert window.scroll.verticalScrollBar().maximum() > 0
    window.navigate("dashboard")
    QTest.qWait(20)
    assert window.placeholder.height() < 500
    assert window.scroll.verticalScrollBar().maximum() == baseline


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
def test_responsive_header_and_scroll_boundaries(window, app, size):
    window.resize(*size)
    QTest.qWait(30)
    header = window.header
    right = header.toolbar.mapTo(header, QPoint(0, 0)).x() + header.toolbar.width()
    assert right <= header.width()
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert window.sidebar_scroll.parentWidget().width() == 224
    logo = window.findChild(QLabel, "setronLogo")
    logo_right = logo.mapTo(window.sidebar_scroll.viewport(), QPoint(logo.width(), 0)).x()
    assert logo_right <= window.sidebar_scroll.viewport().width()
    assert abs(2 * logo.x() + logo.width() - logo.parentWidget().width()) <= 1
    assert header._compact is (header.width() < 1100)
    if size[1] == 600:
        assert window.sidebar_scroll.verticalScrollBar().maximum() > 0


def test_unconnected_actions_are_disabled_and_help_is_removed(window):
    assert set(window.header.actions) == {"equipment_list", "export", "settings"}
    for key in ("equipment_list", "export", "settings"):
        assert not window.header.actions[key].isEnabled()
    assert not hasattr(window, "connection_label")


def test_common_components_have_no_implicit_domain_state(app):
    badge = StatusBadge("연결 전", "info")
    for tone in TONES:
        badge.set_tone(tone)
        assert badge.text() == "연결 전"
    with pytest.raises(ValueError):
        badge.set_tone("unknown-tone")
    assert label("<b>원문</b>").textFormat() == Qt.TextFormat.PlainText
    assert button("확인", primary=True).property("primary") is True


def test_negative_auto_close_is_rejected_before_startup():
    with pytest.raises(SystemExit) as result:
        main(["--auto-close-ms", "-1"])
    assert result.value.code == 2


def test_common_shell_does_not_import_business_or_infrastructure():
    ui_dir = Path(__file__).parents[1] / "src" / "setron" / "ui"
    for filename in ("app.py", "main_window.py", "components.py", "theme.py", "query_state.py", "query_panel.py"):
        tree = ast.parse((ui_dir / filename).read_text(encoding="utf-8"))
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
        assert not any(m.startswith(("setron.features", "setron.infrastructure")) for m in modules), filename
