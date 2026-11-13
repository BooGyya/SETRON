"""개별 설비 선택은 공통 오른쪽 패널로 열고 원래 조회 화면을 보존한다."""

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.ui.dashboard_data import DashboardDrilldown
from setron.ui.equipment_drawer import EquipmentDrawer
from setron.ui.heatmap_data import EquipmentSelection

from test_ui_equipment import DAY, app, open_list, source, window  # noqa: F401
from test_ui_management import wait_for


def open_selected_row(window, *, input="keyboard"):
    page, query = open_list(window)
    page.search.setText("TEST-00")
    page.sort.setCurrentIndex(1)
    index = page.model.index(2, 3)
    page.table.setCurrentIndex(index)
    page.table.scrollTo(index)
    window.scroll.ensureWidgetVisible(page.table)
    page.table.setFocus()
    QApplication.processEvents()
    before = (page.snapshot, page.model.ids, page.page_index, page.search.text(), page.sort.currentIndex(),
              page.table.currentIndex(), page.table.horizontalScrollBar().value(),
              page.table.verticalScrollBar().value(), window.scroll.verticalScrollBar().value())
    if input == "mouse":
        QTest.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton, pos=page.table.visualRect(index).center())
    else:
        QTest.keyClick(page.table, Qt.Key.Key_Return)
    return page, query, before


@pytest.mark.parametrize("input", ["mouse", "keyboard"])
@pytest.mark.parametrize("close", ["button", "escape", "back", "backdrop"])
def test_equipment_list_uses_common_panel_and_preserves_list_context(window, app, input, close):
    window.resize(960, 600)
    page, query, before = open_selected_row(window, input=input)
    drawer = window.equipment_drawer
    assert isinstance(drawer, EquipmentDrawer) and drawer.is_open
    assert window.route == "equipment" and window.header.title.text() == "설비 목록"
    assert page.mode == "list" and page.list_view.isVisibleTo(page) and page.detail_card.isHidden()
    assert not window.query_panel.isHidden()
    assert drawer.request == EquipmentSelection(query, DAY, before[1][2], DAY)
    assert drawer.detail.record.equipment_id == before[1][2]
    assert drawer.title.text() == "설비 정보" and drawer.info_values["설비명"].text() == f"TEST-{before[1][2]:03}"
    assert drawer.parentWidget() is window.centralWidget()
    assert drawer.panel.width() == 560 and not drawer.scroll.horizontalScrollBar().maximum()

    if close == "escape":
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_Escape)
    elif close == "back":
        window.back_shortcut.trigger()
    elif close == "backdrop":
        drawer.backdrop.click()
    else:
        drawer.close_button.click()
    wait_for(app, drawer.isHidden)
    assert window.route == "equipment" and page.mode == "list"
    assert page.snapshot is before[0] and window.query_state.applied is query
    assert (page.model.ids, page.page_index, page.search.text(), page.sort.currentIndex()) == before[1:5]
    assert page.table.currentIndex() == before[5]
    assert (page.table.horizontalScrollBar().value(), page.table.verticalScrollBar().value(),
            window.scroll.verticalScrollBar().value()) == before[6:]
    assert app.focusWidget() is page.table


@pytest.mark.parametrize("change", ["query", "catalog", "navigation"])
def test_equipment_panel_closes_on_context_invalidation(window, change):
    page, query, _ = open_selected_row(window)
    drawer = window.equipment_drawer
    old_request = drawer.request
    if change == "query":
        window.query_panel.apply_draft()
    elif change == "catalog":
        window.set_query_catalog(window.query_state.catalog)
    else:
        window.navigate("heatmap")
    assert not drawer.is_open and drawer.isHidden() and drawer.request is None
    if change != "navigation":
        assert not page.open_detail(old_request, show_page=False)
    else:
        assert window.route == "heatmap" and window.query_state.applied is query


def test_heatmap_list_and_dashboard_detail_use_same_common_content(window, app):
    page, query = open_list(window)
    assert page.open_row(0)
    drawer = window.equipment_drawer
    expected_info = {name: label.text() for name, label in drawer.info_values.items()}
    expected_software = tuple(card.observation for card in drawer.software_cards)
    expected_risk = drawer.risk_status_badge.text(), drawer.risk_reason.text()
    drawer.close_button.click()
    wait_for(app, drawer.isHidden)

    window.navigate("heatmap")
    window.pages["heatmap"].tile_buttons[1].click()
    assert window.equipment_drawer is drawer and drawer.is_open
    assert {name: label.text() for name, label in drawer.info_values.items()} == expected_info
    assert tuple(card.observation for card in drawer.software_cards) == expected_software
    assert (drawer.risk_status_badge.text(), drawer.risk_reason.text()) == expected_risk
    drawer.close_button.click()
    wait_for(app, drawer.isHidden)
    assert window.go_back_route("equipment")
    assert page.mode == "list" and page.detail_card.isHidden() and page.list_view.isVisibleTo(page)

    window.navigate("dashboard")
    assert not drawer.is_open
    window.pages["dashboard"].drilldown_requested.emit(DashboardDrilldown(query, DAY, "선택 범위", (1, 2), False))
    cohort = window.equipment_list_drawer
    assert isinstance(cohort, EquipmentDrawer) and cohort.request is None
    assert page.open_row(0)
    assert cohort.is_open and cohort.request.equipment_id == 1 and window.route == "dashboard"
    assert {name: label.text() for name, label in cohort.info_values.items()} == expected_info
    assert tuple(card.observation for card in cohort.software_cards) == expected_software
    assert (cohort.risk_status_badge.text(), cohort.risk_reason.text()) == expected_risk
    cohort.back_button.click()
    assert cohort.is_open and cohort.request is None and page.scope_ids == (1, 2)


def test_list_selection_outside_its_route_cannot_open_a_panel(window):
    page, _ = open_list(window)
    window.navigate("heatmap")
    assert page.open_row(0)
    assert not window.equipment_drawer.is_open and not window.equipment_list_drawer.is_open
    assert window.route == "heatmap"


def test_heatmap_list_selection_closes_to_the_same_table_and_row(window, app):
    window.query_panel.apply_draft()
    window.navigate("heatmap")
    page = window.pages["heatmap"]
    page.layout_buttons.button(1).click()
    table = page.list_table
    model = table.model()
    pending = [QModelIndex()]
    leaf = None
    while pending and leaf is None:
        parent = pending.pop()
        for row in range(model.rowCount(parent)):
            index = model.index(row, 0, parent)
            if index.data(Qt.ItemDataRole.UserRole) is not None:
                leaf = index
                break
            pending.append(index)
    assert leaf is not None
    table.expandAll()
    table.setCurrentIndex(leaf)
    table.setFocus()
    QTest.keyClick(table, Qt.Key.Key_Return)
    drawer = window.equipment_drawer
    assert drawer.is_open and drawer.return_focus is table
    assert drawer.request.equipment_id == leaf.data(Qt.ItemDataRole.UserRole)
    drawer.close_button.click()
    wait_for(app, drawer.isHidden)
    assert window.route == "heatmap" and table.hasFocus() and table.currentIndex() == leaf
