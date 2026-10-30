"""공통 UI만 검증한다. 업무 화면·예시 설비·DB·Excel은 만들거나 호출하지 않는다."""

import ast
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWidget

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


def test_original_assets_and_fonts_are_usable(window):
    assert {"Inter", "IBM Plex Sans KR"} <= set(QFontDatabase.families())
    for asset in ASSETS.glob("*.svg"):
        assert asset.stat().st_size > 0
        renderer = QSvgRenderer(str(asset))
        assert renderer.isValid(), asset.name
        assert not renderer.defaultSize().isEmpty(), asset.name
    logo = window.findChild(QLabel, "semesLogo")
    assert not logo.pixmap().isNull()
    assert (logo.width(), logo.height()) == (80, 19)
    for menu in MENUS:
        nav = window.nav[menu.key]
        assert nav.height() == 46
        assert (nav.picture.width(), nav.picture.height()) == (18, 18)
        assert nav.accessibleName() == menu.title
        rendered_asset = nav.picture.property("assetFile")
        assert nav.picture.pixmap().deviceIndependentSize().toSize() == svg_size(rendered_asset)
        assert (ASSETS / menu.normal_icon).is_file()
        assert (ASSETS / menu.active_icon).is_file()


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
    assert window.nav["import"].picture.property("assetFile") == "6cfa9.svg"
    assert window.nav["dashboard"].picture.property("assetFile") == "7742b.svg"
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
    assert header._compact is (header.width() < 1100)
    if size[1] == 600:
        assert window.sidebar_scroll.verticalScrollBar().maximum() > 0


def test_help_works_but_unconnected_actions_are_disabled(window, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args))
    for key in ("equipment_list", "export", "settings"):
        assert not window.header.actions[key].isEnabled()
    QTest.mouseClick(window.header.actions["help"], Qt.MouseButton.LeftButton)
    assert len(messages) == 1
    assert "공통 창" in messages[0][2]


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
