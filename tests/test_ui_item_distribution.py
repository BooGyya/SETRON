"""B-4: Item 관측 맥락을 보존하는 설비 정보 패널과 오래된 요청 거부."""

from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QLabel

from test_ui_items import app, data, window
from setron.features.item.schemas import Application
from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
from setron.ui.item_data import selected_application


def open_item(window):
    page = window.pages["items"]
    assert page.open_row(0)
    return page


def test_site_matrix_matches_counts_and_zero_cells_keep_empty_scope(window, data):
    page = window.pages["items"]
    records = tuple(replace(r, site="Site B") if r.equipment_id in (2, 4) else r for r in data.records)
    page.loader = lambda: replace(data, records=records)
    page.reload()
    page.open_row(0)
    page.distribution_view.setCurrentIndex(1)
    assert page.distribution.model.rows == (("Site A", "1대", "0대", "2대", "0대"),
                                            ("Site B", "0대", "1대", "0대", "1대"))
    assert set(page.distribution.tiles) == set(page.equipment_model.ids) == {1, 2, 3, 4, 5}
    assert all(tile.accessibleName() for tile in page.distribution.tiles.values())
    page.distribution.select_cell(page.distribution.model.index(1, 2))
    assert page.site_filter.currentData() == "Site B"
    assert page.state_filter.currentData() == Application.NOT_APPLIED
    assert set(page.distribution.tiles) == {2}
    assert page.equipment_model.ids == (2,)
    page.distribution.select_cell(page.distribution.model.index(1, 1))
    assert not page.equipment_model.ids and not page.distribution.tiles
    assert page.equipment_empty.isVisible()
    assert not page.open_equipment(1)


@pytest.mark.parametrize("scroll_position", [350, None])
def test_tile_keyboard_opens_side_panel_and_preserves_item_scope(window, app, scroll_position):
    page = open_item(window)
    page.site_filter.setCurrentIndex(1)
    page.state_filter.setCurrentIndex(page.state_filter.findData(Application.UNKNOWN))
    page.distribution_view.setCurrentIndex(1)
    QTest.qWait(30)
    window.scroll.verticalScrollBar().setValue(scroll_position if scroll_position is not None else window.scroll.verticalScrollBar().maximum())
    original_scroll = window.scroll.verticalScrollBar().value()
    QTest.keyClick(page.distribution.tiles[3], Qt.Key.Key_Space)
    drawer = window.equipment_drawer
    assert window.route == "items" and drawer.is_open
    assert drawer.detail.record.equipment_id == 3 and drawer.request.data is page.data
    assert drawer.info_values["관측일"].text() == "2026-01-02"
    assert drawer.application_badge.text() == "미확인"
    assert drawer.item_pm_rows[0][4] == "미확인"
    assert "저장 행 없음" in drawer.item_pm_rows[0][5]
    assert any("저장 행 없음" in r[2] for r in drawer.item_evidence_rows)
    assert any("C9" in r[3] for r in drawer.item_evidence_rows)
    assert not window.header.actions["equipment_list"].isEnabled()
    assert window.nav["items"].isChecked()
    assert not window.query_panel.isVisible()
    QTest.mouseClick(drawer.close_button, Qt.MouseButton.LeftButton)
    QTest.qWait(240)
    assert window.route == "items" and page.selected_id == 1
    assert page.site_filter.currentData() == "Site A"
    assert page.state_filter.currentData() == Application.UNKNOWN
    assert page.distribution_view.currentData() == "heatmap"
    assert page.selected_equipment_id == 3 and page.distribution.tiles[3].isChecked()
    scrollbar = window.scroll.verticalScrollBar()
    assert abs(scrollbar.value() - min(original_scroll, scrollbar.maximum())) <= 2
    assert drawer.request is None and not drawer.item_evidence_rows
    assert app.focusWidget() is page.distribution.tiles[3]


def test_table_enter_and_release_source_are_connected(window, data):
    page = window.pages["items"]
    from setron.ui.version_data import SourceReference
    versions = replace(data.versions, releases=(replace(data.versions.releases[0], sources=(
        SourceReference("버전", "release.xlsx", "R2", "릴리즈", "B8", 9),)), *data.versions.releases[1:]))
    page.loader = lambda: replace(data, versions=versions)
    page.reload()
    page.open_row(0)
    page.equipment_table.setCurrentIndex(page.equipment_model.index(0, 0))
    QTest.keyClick(page.equipment_table, Qt.Key.Key_Return)
    detail = window.equipment_drawer
    assert window.route == "items" and detail.is_open
    assert any("release.xlsx" in row[2] and "B8" in row[3] for row in detail.item_evidence_rows)
    assert any("모델 A" in row[3] for row in detail.item_evidence_rows)
    assert detail.risk_status_badge.text() == "해당 Item 위험 규칙 없음"


def test_table_click_keeps_expanded_item_filters_selection_and_scroll(window, app):
    page = open_item(window)
    assert not window.pages.is_loaded("equipment")
    page.site_filter.setCurrentIndex(1)
    table = page.equipment_table
    window.scroll.ensureWidgetVisible(table)
    app.processEvents()
    horizontal = table.horizontalScrollBar()
    horizontal.setValue(horizontal.maximum())
    index = page.equipment_model.index(0, 6)
    original_scroll = window.scroll.verticalScrollBar().value(), horizontal.value()
    history = tuple(window._route_history)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(index).center())
    drawer = window.equipment_drawer
    assert window.route == "items" and tuple(window._route_history) == history
    assert not window.pages.is_loaded("equipment")
    assert drawer.is_open and drawer.request.equipment_id == page.equipment_model.ids[0]
    assert page.detail_view.isVisible() and page.selected_id == 1 and page.site_filter.currentData() == "Site A"
    assert table.currentIndex() == index
    assert drawer.request.data is page.data and drawer.detail.record is page.data.records[0]
    assert drawer.info_values["관측일"].text() == str(page.data.records[0].base_date)
    assert (window.scroll.verticalScrollBar().value(), horizontal.value()) == original_scroll
    window.back_shortcut.trigger()
    QTest.qWait(240)
    assert window.route == "items" and page.mode == "detail" and page.detail_view.isVisible()
    assert table.currentIndex() == index and app.focusWidget() is table
    assert (window.scroll.verticalScrollBar().value(), horizontal.value()) == original_scroll


def test_item_panel_uses_item_observation_and_risk_instead_of_dashboard_goal(window, data):
    page = window.pages["items"]
    rule = RiskRule(1, RuleType.DIRECT_VERSION, 10, "선택 Item 위험 버전", item_id=1,
                    release_version_id=1, mapping_status=MappingStatus.VERIFIED)
    other = replace(rule, risk_rule_id=2, item_id=2, risk_reason="다른 Item 위험 버전")
    page.loader = lambda: replace(data, risk_rules=(rule, other))
    page.reload()
    page.open_row(0)
    page.open_equipment(1)
    drawer = window.equipment_drawer
    assert drawer.detail.record is data.records[0]
    assert drawer.software_cards[0].observation.current == "v99"
    assert drawer.software_cards[0].observation.target == "v99"
    assert drawer.risk_status_badge.text() == "위험" and drawer.risk_reasons is not None
    # 공통 패널은 요약 결과와 상세 근거를 나누고, 선택 Item의 근거만 이유 카드에 표시한다.
    assert drawer.risk_reasons.isVisibleTo(drawer.body_widget)
    assert "선택 Item 위험 버전" in drawer.risk_reasons.text()
    assert "다른 Item 위험 버전" not in drawer.risk_reasons.text()
    assert "선택 Item 위험 버전" not in drawer.risk_reason.text()
    assert drawer.body.indexOf(drawer.risk_reasons) == drawer.body.indexOf(drawer.risk_card) + 1
    assert drawer.goal_badge is None
    labels = {widget.text() for widget in drawer.body_widget.findChildren(QLabel) if widget.isVisibleTo(drawer.body_widget)}
    assert "대응 릴리즈" in labels and "목표 버전" not in labels
    assert window.query_state.applied is None and window.route == "items"


@pytest.mark.parametrize("change", ["query", "catalog", "navigation"])
def test_query_catalog_and_navigation_close_item_drawer_without_changing_item_scope(window, change):
    page = open_item(window)
    page.open_equipment(1)
    if change == "query":
        window.query_panel.apply_draft()
    elif change == "catalog":
        window.query_panel.catalog_changed.emit(window.query_state.catalog)
    else:
        window.navigate("dashboard")
    drawer = window.equipment_drawer
    assert not drawer.is_open and drawer.isHidden() and drawer.request is None
    assert page.selected_id == 1 and page.mode == "detail"
    assert not drawer.item_evidence_rows


@pytest.mark.parametrize("change", ["reload", "failure", "scope", "item", "back", "new_equipment"])
def test_old_request_is_rejected_and_invalidated_detail_removed(window, data, change):
    page = open_item(window)
    spy = QSignalSpy(page.equipment_selected)
    assert page.open_equipment(1)
    request = spy.at(0)[0]
    drawer = window.equipment_drawer
    if change == "reload":
        page.reload()
    elif change == "failure":
        page.loader = lambda: None
        page.reload()
    elif change == "scope":
        page.state_filter.setCurrentIndex(page.state_filter.findData(Application.UNKNOWN))
    elif change == "item":
        page.back_to_list()
        page.open_row(1)
    elif change == "back":
        page.back_to_list()
    else:
        drawer.close_button.click()
        page.open_equipment(2)
    assert not page.is_current_selection(request)
    current = drawer.request
    page.equipment_selected.emit(request)
    assert drawer.request is current
    if change != "new_equipment":
        assert drawer.request is None and not drawer.item_evidence_rows and not drawer.is_open
        assert window.route == "items"


def test_forged_identity_hpa_scope_and_disconnected_data_are_rejected(window, data):
    page = open_item(window)
    spy = QSignalSpy(page.equipment_selected)
    page.open_equipment(1)
    request = spy.at(0)[0]
    for invalid in (replace(request, equipment_id=999), replace(request, equipment_id=6),
                    replace(request, site="other"), replace(request, state=Application.UNKNOWN),
                    replace(request, item_id=999), replace(request, data=replace(data, records=(), observations_connected=False))):
        assert selected_application(invalid) is None
        assert not page.is_current_selection(invalid)
    assert not page.is_current_selection(replace(request, data=replace(data)))


def test_a_query_snapshot_filters_and_list_context_preserved(window):
    window.navigate("dashboard")
    window.query_panel.apply_draft()
    equipment = window.pages["equipment"]
    equipment.open_all("dashboard")
    equipment.search.setText("DEMO")
    window.navigate("equipment")
    equipment.open_row(0)
    snapshot, selection = equipment.snapshot, equipment.selected_id
    detail_rows, headings = equipment.detail_model.rows, equipment.detail_model.headings
    applied, draft, targets = window.query_state.applied, window.query_state.draft, window.query_state.target_draft
    window.navigate("items")
    page = open_item(window)
    page.open_equipment(1)
    assert equipment.snapshot is snapshot
    assert window.route == "items" and window.equipment_drawer.is_open
    window.equipment_drawer.close_button.click()
    assert window.query_state.applied is applied and window.query_state.draft == draft
    assert window.query_state.target_draft == targets
    assert equipment.snapshot is snapshot and equipment.search.text() == "DEMO"
    assert equipment.mode == "list" and equipment.selected_id == selection
    assert equipment.detail_model.rows == detail_rows and equipment.detail_model.headings == headings
    window.navigate("equipment")
    assert equipment.item_request is None and not equipment.item_evidence_table.isVisible()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
def test_heatmap_and_equipment_panel_responsive(window, size):
    page = open_item(window)
    page.distribution_view.setCurrentIndex(1)
    window.resize(*size)
    QTest.qWait(40)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    page.open_equipment(1)
    QTest.qWait(40)
    drawer = window.equipment_drawer
    assert window.route == "items" and drawer.is_open
    assert drawer.panel.width() <= window.centralWidget().width()
    assert drawer.scroll.horizontalScrollBar().maximum() == 0
    assert window.scroll.horizontalScrollBar().maximum() == 0
