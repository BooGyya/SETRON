"""목록의 계층 표시·복사·열 제한을 실제 Qt 렌더링과 입력으로 검증한다."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QStyleOptionViewItem, QVBoxLayout, QWidget

from setron.ui.combo_box import ComboBox
from setron.ui.heatmap import HeatmapPage
from setron.ui.heatmap_data import GoalState
from setron.ui.heatmap_list import HeatmapListModel, HeatmapTreeView
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.query_state import QueryState
from setron.ui.theme import INK, PRIMARY, SECONDARY, install_theme


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    return result


def groups(code="EQ-01", reason="후보 아님"):
    row = SimpleNamespace(equipment_id=1, code=code, reason=reason)
    node = SimpleNamespace(key=("group", "c3"), title="C3", group_name=None, children=(), tiles=(row,))
    for kind, title in (("line", "P1"), ("fab", "Memory"), ("site", "평택")):
        node = SimpleNamespace(key=(kind,), title=title, group_name=None, children=(node,), tiles=(row,))
    return (node,)


def values(row, _):
    return (row.code, "평택", "C3", "v1.2.3", "목표 달성", row.reason), "success", {}


@pytest.fixture
def table(app):
    model = HeatmapListModel(("설비명", "위치", "모델", "버전", "목표 달성", "위험 판정"), centered=True)
    view = HeatmapTreeView(model, "목록 UX 검증", (220, 150, 160, 160, 120, 150))
    view.resize(800, 600)
    view.set_groups(groups(), values)
    view.show()
    QTest.qWait(30)
    yield view
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def branch_pixels(view, index):
    rect = view.visualRect(index)
    center = QPoint(rect.left() - view.indentation() // 2, rect.center().y())
    image = view.viewport().grab().toImage()
    dark = {(x - center.x(), y - center.y())
            for x in range(center.x() - 5, center.x() + 6)
            for y in range(center.y() - 5, center.y() + 6)
            if max(image.pixelColor(x, y).getRgb()[:3]) < 150}
    return center, dark


def test_chevrons_are_visible_and_point_differently_after_mouse_collapse_and_keyboard_expand(table, app):
    index = table.model().index(0, 0)
    center, open_pixels = branch_pixels(table, index)
    assert table.isExpanded(index) and len(open_pixels) >= 10
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=center)
    app.processEvents()
    _, closed_pixels = branch_pixels(table, index)
    assert not table.isExpanded(index) and len(closed_pixels) >= 10 and closed_pixels != open_pixels
    table.setCurrentIndex(index)
    QTest.keyClick(table, Qt.Key.Key_Right)
    assert table.isExpanded(index)


def test_group_name_click_toggles_once_and_preserves_state_across_updates(table, app):
    index = table.model().group_index(("group", "c3"))
    collapsed, expanded = QSignalSpy(table.collapsed), QSignalSpy(table.expanded)
    name = table.visualRect(index).topLeft() + QPoint(12, table.visualRect(index).height() // 2)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=name)
    app.processEvents()
    assert not table.isExpanded(index) and collapsed.count() == 1
    assert table._expansion_depth is None
    table.set_groups(groups("UPDATED"), values)
    index = table.model().group_index(("group", "c3"))
    assert not table.isExpanded(index)
    arrow, _ = branch_pixels(table, index)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=arrow)
    app.processEvents()
    assert table.isExpanded(index) and expanded.count() == 1
    equipment = table.model().equipment_index(1)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(equipment).center())
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole) == 1
    assert table.isExpanded(index) and collapsed.count() == expanded.count() == 1


def test_expansion_geometry_waits_until_qt_finishes_and_batches_rapid_toggles(table, app, monkeypatch):
    app.processEvents()
    calls, fit_height = [], table.fit_height

    def measured_fit():
        calls.append(table.isExpanded(table.model().index(0, 0)))
        fit_height()

    monkeypatch.setattr(table, "fit_height", measured_fit)
    root = table.model().index(0, 0)
    for _ in range(20):
        table.setExpanded(root, not table.isExpanded(root))
    # 펼침/접힘 신호를 처리하는 도중에는 트리 높이 변경이나 강제 레이아웃을 하지 않는다.
    assert calls == []
    QTest.qWait(30)
    assert 1 <= len(calls) <= 2
    assert table.isExpanded(root)
    assert table.viewport().rect().intersects(table.visualRect(table.model().equipment_index(1)))


@pytest.mark.parametrize("arrow", [False, True], ids=["group-name", "arrow"])
def test_held_group_click_keeps_global_target_stable_until_release(app, arrow):
    model = HeatmapListModel(("설비명", "위치", "모델", "버전", "목표 달성", "위험 판정"))
    view = HeatmapTreeView(model, "누름과 놓음 사이 위치 보존", (220, 150, 160, 160, 120, 150))
    rows = tuple(SimpleNamespace(equipment_id=i, code=f"EQ-{i}", reason="후보 아님") for i in range(12))
    node = SimpleNamespace(key=("group", "c4"), title="C4", group_name="C4", children=(), tiles=rows)
    content, host = QWidget(), QScrollArea()
    body = QVBoxLayout(content)
    body.setContentsMargins(0, 0, 0, 0)
    heading = QLabel("목록 위의 화면 내용")
    heading.setFixedHeight(400)
    body.addWidget(heading)
    body.addWidget(view)
    body.addStretch()
    host.setWidgetResizable(True)
    host.setWidget(content)
    host.resize(800, 700)
    view.set_groups((node,), values)
    selected = []
    view.clicked.connect(lambda index: selected.append(index.data(Qt.ItemDataRole.UserRole)))
    host.show()
    QTest.qWait(40)
    try:
        scrollbar = host.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        QTest.qWait(20)
        index = model.group_index(("group", "c4"))
        rect = view.visualRect(index)
        position = QPoint(rect.left() - view.indentation() // 2 if arrow else rect.left() + 12, rect.center().y())
        pointer = view.viewport().mapToGlobal(position)
        assert host.viewport().rect().contains(host.viewport().mapFromGlobal(pointer))
        before = view.height(), view.viewport().mapToGlobal(QPoint()), scrollbar.value()
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=position)
        QTest.qWait(80)
        # 높이가 먼저 줄면 바깥 스크롤이 눌렀던 분류를 포인터 아래에서 옮긴다.
        assert (view.height(), view.viewport().mapToGlobal(QPoint()), scrollbar.value()) == before
        if not arrow:
            assert view.isExpanded(index)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=view.viewport().mapFromGlobal(pointer))
        QTest.qWait(40)
        assert not view.isExpanded(index) and not view._mouse_press_active
        assert view.height() < before[0] and scrollbar.value() < before[2]
        assert all(equipment_id is None for equipment_id in selected)
    finally:
        host.close()
        host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("cancel", ["outside", "hide", "rebuild", "ungrab", "deactivate"])
def test_canceled_group_press_cannot_toggle_replacement_or_leave_geometry_deferred(table, app, cancel):
    index = table.model().index(0, 0)
    rect = table.visualRect(index)
    position = QPoint(rect.left() + 12, rect.center().y())
    QTest.mousePress(table.viewport(), Qt.MouseButton.LeftButton, pos=position)
    table._schedule_geometry()
    QTest.qWait(20)
    if cancel == "hide":
        table.hide()
        table.show()
    elif cancel == "rebuild":
        nodes = groups("REPLACED")
        nodes[0].key = ("replacement-site",)
        table.set_groups(nodes, values)
    elif cancel == "ungrab":
        QCoreApplication.sendEvent(table.viewport(), QEvent(QEvent.Type.UngrabMouse))
    elif cancel == "deactivate":
        QCoreApplication.sendEvent(table, QEvent(QEvent.Type.WindowDeactivate))
    release = QPoint(-10, -10) if cancel == "outside" else position
    QTest.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, pos=release)
    QTest.qWait(30)
    assert not table._mouse_press_active and not table._geometry_pending
    assert table.isExpanded(table.model().index(0, 0))


def test_group_depths_use_neutral_colors_and_distinct_fonts(table):
    root = table.model().index(0, 0)
    child = table.model().index(0, 0, root)
    assert root.data(Qt.ItemDataRole.ForegroundRole).name() == INK
    assert child.data(Qt.ItemDataRole.ForegroundRole).name() == SECONDARY
    assert root.data(Qt.ItemDataRole.ForegroundRole).name() != PRIMARY
    assert root.data(Qt.ItemDataRole.FontRole).pixelSize() > child.data(Qt.ItemDataRole.FontRole).pixelSize()


def test_depth_commands_batch_expand_preserve_selection_and_manual_changes_across_updates(table):
    model = table.model()
    root, child = model.index(0, 0), model.index(0, 0, model.index(0, 0))
    table.select_equipment(1)
    table.set_expansion_depth(0)
    assert not table.isExpanded(root) and table.currentIndex().data(Qt.ItemDataRole.UserRole) == 1
    table.set_expansion_depth(2)
    assert table.isExpanded(root) and not table.isExpanded(child)
    table.set_groups(groups("CHANGED"), values)
    assert table.isExpanded(model.index(0, 0)) and not table.isExpanded(model.index(0, 0, model.index(0, 0)))
    table.set_expansion_depth(-1)
    assert all(table.isExpanded(model.indexFromItem(item)) for item in model._group_items.values())
    table.setExpanded(model.index(0, 0), False)
    assert table._expansion_depth is None
    table.set_groups(groups("CHANGED-AGAIN"), values)
    assert not table.isExpanded(model.index(0, 0))


def test_valid_long_name_is_capped_elided_and_copied_in_full_with_dense_wrapping_rows(table, app):
    long_name = "W" * 50
    table.set_groups(groups(long_name), values)
    index = table.model().equipment_index(1)
    assert table.columnWidth(0) <= max(220, min(360, round(table.viewport().width() * 0.42)))
    assert index.data(Qt.ItemDataRole.ToolTipRole) == long_name
    option = QStyleOptionViewItem()
    table.itemDelegate().initStyleOption(option, index)
    assert not option.features & QStyleOptionViewItem.ViewItemFeature.WrapText
    assert option.textElideMode == Qt.TextElideMode.ElideRight
    table.select_equipment(1)
    table.copy_name_action.trigger()
    assert QApplication.clipboard().text() == long_name
    plain_height = table.visualRect(index).height()
    assert 46 <= plain_height < 56
    table.set_groups(groups(long_name, "긴 위험 사유\n" * 8), values)
    app.processEvents()
    assert table.visualRect(table.model().equipment_index(1)).height() > plain_height
    table.setCurrentIndex(table.model().index(0, 0))
    assert not table.copy_name_action.isEnabled() and not table.copy_equipment_name()


def test_heatmap_list_tools_risk_tooltip_and_narrow_kpis_share_the_same_source(app):
    source = demo_dashboard_source()
    state = QueryState(source.catalog)
    snapshot = source.snapshot(state.apply())
    page = HeatmapPage(state, source, detail_connected=True)
    page.resize(850, 900)
    page.show()
    page.accept_result(snapshot.query, snapshot)
    QTest.qWait(60)
    try:
        assert page.list_tools.isHidden()
        assert isinstance(page.tree_depth, ComboBox) and page.tree_depth.property("queryField")
        assert page.summary.compact and page.summary.grid.rowCount() == 1
        page.layout_buttons.button(1).click()
        assert page.list_tools.isVisible()
        page.tree_depth.setCurrentIndex(page.tree_depth.findData(0))
        assert all(not page.list_table.isExpanded(page.list_model.indexFromItem(item))
                   for item in page.list_model._group_items.values())
        page.tree_depth.setCurrentIndex(page.tree_depth.findData(-1))
        normal = next(tile for tile in page.tiles if tile.risk.value == "NO_RISK" and tile.goal is not GoalState.EXCLUDED)
        index = page.list_model.equipment_index(normal.equipment_id)
        assert index.siblingAtColumn(5).data() == "후보 아님"
        assert "등록 규칙상 후보 아님" in index.siblingAtColumn(5).data(Qt.ItemDataRole.ToolTipRole)
        page.list_table.select_equipment(normal.equipment_id)
        assert page.copy_name.isEnabled()
        page.copy_name.click()
        assert QApplication.clipboard().text() == normal.code
        page.view_toggle.setChecked(False)
        assert [col for col in range(6) if not page.list_table.isColumnHidden(col)] == [0, 3, 4]
        page.layout_buttons.button(0).click()
        assert page.list_tools.isHidden() and page.snapshot is snapshot
    finally:
        page.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("width", [1350, 850, 650])
def test_list_navigation_wraps_without_overflow_or_empty_replay_row(app, width):
    source = demo_dashboard_source()
    state = QueryState(source.catalog)
    page = HeatmapPage(state, source, detail_connected=True)
    page.resize(width, 900)
    page.show()
    page.accept_result(state.apply(), source.snapshot(state.applied))
    QTest.qWait(50)
    try:
        page.layout_buttons.button(1).click()
        QTest.qWait(50)
        for control in (page.grouping_bar, page.list_tools):
            rect = control.geometry()
            assert page.navigation_host.rect().contains(rect)
        assert page._navigation_rows == (1 if width >= 850 else 2)
        page._show_grouping = False  # Replay의 외부 분류 전환 행과 같은 사용 계약.
        page._render_tiles()
        QTest.qWait(30)
        assert page.grouping_bar.isHidden() and page.list_tools.isVisible() and page._navigation_rows == 1
        page.layout_buttons.button(0).click()
        assert page.navigation_host.isHidden() and page.list_tools.isHidden()
    finally:
        page.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
