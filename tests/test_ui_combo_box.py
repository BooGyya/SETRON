"""전 화면 공통 드롭다운의 앵커·긴 목록·Qt 기본 선택 계약."""

import ast
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QComboBox, QScrollArea, QStyle, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from setron.ui.app import create_window
from setron.ui.combo_box import ComboBox
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.data_management_page import _ChoiceDelegate
from setron.ui.import_widgets import import_combo_style
from test_ui_import_ver5 import own  # noqa: F401


def below(combo):
    popup = combo.view().window()
    anchor = combo.mapToGlobal(QPoint(0, combo.height() + combo.popup_gap))
    assert popup.isVisible() and popup.y() == anchor.y()
    assert popup.y() > combo.mapToGlobal(combo.rect().bottomLeft()).y()
    assert popup.geometry().bottom() <= combo.screen().availableGeometry().bottom()


@pytest.mark.parametrize("selected", [-1, 0, 1, 2])
def test_list_opens_below_the_field_for_any_selection(own, selected):
    combo = own(ComboBox())
    combo.resize(220, 38)
    combo.move(120, 100)
    combo.addItems(["최근 1개월", "최근 3개월", "전체 관측 기간"])
    combo.setStyleSheet(import_combo_style())
    combo.setCurrentIndex(selected)
    changed = QSignalSpy(combo.currentIndexChanged)
    combo.showPopup()
    QTest.qWait(20)
    below(combo)
    assert combo.currentIndex() == selected and changed.count() == 0
    assert not combo.style().styleHint(QStyle.StyleHint.SH_ComboBox_Popup)
    QTest.keyClick(combo.view(), Qt.Key.Key_Escape)
    assert not combo.view().isVisible() and combo.currentIndex() == selected


def test_long_list_keeps_selection_visible_and_scrollable(own):
    combo = own(ComboBox())
    combo.resize(220, 38)
    combo.move(120, 100)
    for i in range(300):
        combo.addItem(f"후보 {i}", i)
    combo.setCurrentIndex(299)
    combo.showPopup()
    QTest.qWait(20)
    below(combo)
    view = combo.view()
    assert view.verticalScrollBar().maximum() > 0
    assert view.viewport().rect().intersects(view.visualRect(view.currentIndex()))
    assert view.window().height() < combo.screen().availableGeometry().height() / 2 + 1
    combo.hidePopup()


def test_last_field_can_open_downward_and_restores_page_after_closing(own):
    area = QScrollArea()
    area.setWidgetResizable(True)
    content = QWidget()
    body = QVBoxLayout(content)
    body.addStretch()
    combo = ComboBox()
    combo.addItems([f"선택지 {i}" for i in range(80)])
    combo.setFixedHeight(38)
    body.addWidget(combo)
    area.setWidget(content)
    available = QApplication.primaryScreen().availableGeometry()
    area.setGeometry(available.left() + 60, available.bottom() - 260, 340, 250)
    own(area)
    QTest.qWait(20)
    minimum, position = content.minimumHeight(), area.verticalScrollBar().value()
    margins = body.contentsMargins()
    original = combo.mapToGlobal(QPoint())
    combo.showPopup()
    QTest.qWait(20)
    below(combo)
    assert combo.view().window().height() >= combo.fontMetrics().height() * 2
    assert combo.mapToGlobal(QPoint()).y() < original.y()
    combo.hidePopup()
    QTest.qWait(20)
    assert content.minimumHeight() == minimum and area.verticalScrollBar().value() == position
    assert body.contentsMargins() == margins and combo.mapToGlobal(QPoint()) == original


def test_keyboard_mouse_and_disabled_candidates_keep_native_selection_contract(own):
    combo = own(ComboBox())
    combo.addItems(["첫 번째", "사용 불가", "세 번째"])
    combo.model().item(1).setEnabled(False)
    combo.resize(220, 38)
    combo.move(100, 80)
    changed, activated = QSignalSpy(combo.currentIndexChanged), QSignalSpy(combo.activated)
    combo.showPopup()
    QTest.qWait(20)
    QTest.keyClick(combo.view(), Qt.Key.Key_Down)
    QTest.keyClick(combo.view(), Qt.Key.Key_Return)
    assert combo.currentIndex() == 2 and changed.count() == 1 and activated.count() == 1
    combo.showPopup()
    QTest.qWait(20)
    index = combo.model().index(0, 0)
    point = combo.view().visualRect(index).center()
    QTest.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert combo.currentIndex() == 0 and changed.count() == 2 and activated.count() == 2
    assert not combo.view().isVisible()


def test_editable_site_does_not_lose_direct_input_when_cancelled(own):
    combo = own(ComboBox())
    combo.addItems(["화성", "평택"])
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.setEditText("신규 Site")
    changed = QSignalSpy(combo.currentTextChanged)
    combo.showPopup()
    QTest.qWait(20)
    below(combo)
    QTest.keyClick(combo.view(), Qt.Key.Key_Escape)
    assert combo.currentText() == "신규 Site" and combo.count() == 2 and changed.count() == 0


def test_empty_or_disabled_fields_do_not_open(own):
    combo = own(ComboBox())
    combo.showPopup()
    assert not combo.view().isVisible()
    combo.addItem("선택지")
    combo.setEnabled(False)
    combo.showPopup()
    assert not combo.view().isVisible()


@pytest.mark.parametrize("editable", [False, True])
def test_data_management_delegate_uses_shared_popup_and_commits_selection_or_input(own, editable):
    table = own(QTableWidget(1, 1))
    table.setGeometry(80, 80, 420, 180)
    table.setItem(0, 0, QTableWidgetItem("P2"))
    delegate = _ChoiceDelegate(table, lambda: ("P1", "P2"), editable=editable)
    table.setItemDelegateForColumn(0, delegate)
    table.setCurrentCell(0, 0)
    table.editItem(table.item(0, 0))
    QTest.qWait(10)
    editor = table.findChild(ComboBox)
    assert editor is not None and editor.parentWidget() is table.viewport()
    assert editor.isEditable() is editable and editor.currentText() == "P2"
    committed = QSignalSpy(delegate.commitData)
    editor.showPopup()
    QTest.qWait(20)
    below(editor)
    if editable:
        editor.hidePopup()
        editor.lineEdit().selectAll()
        QTest.keyClicks(editor.lineEdit(), "P5")
        expected = "P5"
    else:
        QTest.keyClick(editor.view(), Qt.Key.Key_Home)
        QTest.keyClick(editor.view(), Qt.Key.Key_Return)
        expected = "P1"
    if table.item(0, 0).text() != expected:
        QTest.keyClick(editor, Qt.Key.Key_Return)
    QTest.qWait(20)
    assert committed.count() >= 1 and table.item(0, 0).text() == expected


def test_no_screen_constructs_an_unshared_native_combobox():
    root = Path(__file__).resolve().parents[1] / "src/setron/ui"
    for path in root.glob("*.py"):
        if path.name == "combo_box.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = (node for node in ast.walk(tree) if isinstance(node, ast.Call))
        assert not any((isinstance(node.func, ast.Name) and node.func.id == "QComboBox")
                       or (isinstance(node.func, ast.Attribute) and node.func.attr == "QComboBox")
                       for node in calls), path.name


def test_all_application_comboboxes_use_the_shared_component(own):
    window = own(create_window(demo_dashboard_source()))
    # 시작 시에는 공통 셸만 만들므로 실제 화면을 구성한 뒤 그 안의 입력기를 검사한다.
    for key in tuple(window.pages):
        window.pages[key].ensurePolished()
    combos = window.findChildren(QComboBox)
    assert combos
    assert all(isinstance(combo, ComboBox) for combo in combos)


def test_long_scrolled_page_keeps_field_visible_and_restores_on_external_hide(own):
    area = QScrollArea()
    area.setWidgetResizable(True)
    content = QWidget()
    body = QVBoxLayout(content)
    body.addSpacing(1000)
    combo = ComboBox()
    combo.addItems([f"선택지 {i}" for i in range(100)])
    combo.setFixedHeight(38)
    body.addWidget(combo)
    area.setWidget(content)
    available = QApplication.primaryScreen().availableGeometry()
    area.setGeometry(available.left() + 50, available.bottom() - 260, 340, 250)
    own(area)
    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
    QTest.qWait(20)
    margins, position = body.contentsMargins(), area.verticalScrollBar().value()
    combo.showPopup()
    QTest.qWait(20)
    below(combo)
    assert combo.mapTo(area.viewport(), QPoint()).y() >= 0
    assert combo.mapTo(area.viewport(), combo.rect().bottomLeft()).y() < area.viewport().height()
    combo.view().window().hide()
    QTest.qWait(20)
    assert body.contentsMargins() == margins and area.verticalScrollBar().value() == position
