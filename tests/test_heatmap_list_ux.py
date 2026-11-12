"""목록의 계층 표시·복사·열 제한을 실제 Qt 렌더링과 입력으로 검증한다."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

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
