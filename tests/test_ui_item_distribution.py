"""B-4: Site·설비 분포, 저장 근거와 기존 설비 상세 왕복 및 오래된 요청 거부."""

from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest

from test_ui_items import app, data, window
from setron.features.item.schemas import Application
from setron.ui.item_data import selected_application


def open_item(window):
    page = window.pages["items"]
    assert page.open_row(0)
    page.more_button.click()
    return page


def test_site_matrix_matches_counts_and_zero_cells_keep_empty_scope(window, data):
    page = window.pages["items"]
    records = tuple(replace(r, site="Site B") if r.equipment_id in (2, 4) else r for r in data.records)
    page.loader = lambda: replace(data, records=records)
    page.reload()
    page.open_row(0)
    page.more_button.click()
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
    assert "0대" in page.scope_count.text()
    assert not page.open_equipment(1)


@pytest.mark.parametrize("scroll_position", [350, None])
def test_tile_keyboard_reuses_equipment_page_and_preserves_item_scope(window, scroll_position):
    page = open_item(window)
    page.site_filter.setCurrentIndex(1)
    page.state_filter.setCurrentIndex(page.state_filter.findData(Application.UNKNOWN))
    page.distribution_view.setCurrentIndex(1)
    QTest.qWait(30)
    window.scroll.verticalScrollBar().setValue(scroll_position if scroll_position is not None else window.scroll.verticalScrollBar().maximum())
    original_scroll = window.scroll.verticalScrollBar().value()
    equipment = window.pages["equipment"]
    QTest.keyClick(page.distribution.tiles[3], Qt.Key.Key_Space)
    assert window.route == "equipment" and window.pages["equipment"] is equipment
    assert equipment.mode == "detail" and equipment.selected_id == 3
    assert equipment.item_request.data is page.data
    assert "2026-01-02" in equipment.detail_location.text()
    assert "미확인" in equipment.detail_summary.text()
    assert equipment.detail_model.rows[0][4] == "미확인"
    assert "저장 행 없음" in equipment.detail_model.rows[0][5]
    assert any("저장 행 없음" in r[2] for r in equipment.item_evidence_model.rows)
    assert any("C9" in r[3] for r in equipment.item_evidence_model.rows)
    assert not equipment.list_button.isEnabled()
    assert not window.header.actions["equipment_list"].isEnabled()
    assert window.nav["items"].isChecked()
    assert not window.query_panel.isVisible()
    QTest.mouseClick(equipment.back_button, Qt.MouseButton.LeftButton)
    QTest.qWait(60)
    assert window.route == "items" and page.selected_id == 1
    assert page.site_filter.currentData() == "Site A"
    assert page.state_filter.currentData() == Application.UNKNOWN
    assert page.distribution_view.currentData() == "heatmap"
    assert page.selected_equipment_id == 3 and page.distribution.tiles[3].isChecked()
    scrollbar = window.scroll.verticalScrollBar()
    assert abs(scrollbar.value() - min(original_scroll, scrollbar.maximum())) <= 2
    assert equipment.item_request is None and not equipment.item_evidence_model.rows


def test_table_enter_and_release_source_are_connected(window, data):
    page = window.pages["items"]
    from setron.ui.version_data import SourceReference
    versions = replace(data.versions, releases=(replace(data.versions.releases[0], sources=(
        SourceReference("버전", "release.xlsx", "R2", "릴리즈", "B8", 9),)), *data.versions.releases[1:]))
    page.loader = lambda: replace(data, versions=versions)
    page.reload()
    page.open_row(0)
    page.more_button.click()
    page.equipment_table.setCurrentIndex(page.equipment_model.index(0, 0))
    QTest.keyClick(page.equipment_table, Qt.Key.Key_Return)
    detail = window.pages["equipment"]
    assert window.route == "equipment"
    assert any("release.xlsx" in row[2] and "B8" in row[3] for row in detail.item_evidence_model.rows)
    assert any("모델 A" in row[3] for row in detail.item_evidence_model.rows)
    assert detail.detail_risk_badge.text() == "해당 Item 위험 규칙 없음"
    assert "원본 셀" in detail.evidence.text()


@pytest.mark.parametrize("change", ["reload", "failure", "scope", "item", "back", "new_equipment"])
def test_old_request_is_rejected_and_invalidated_detail_removed(window, data, change):
    page = open_item(window)
    spy = QSignalSpy(page.equipment_selected)
    assert page.open_equipment(1)
    request = spy.at(0)[0]
    equipment = window.pages["equipment"]
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
        equipment.back_button.click()
        page.open_equipment(2)
    assert not page.is_current_selection(request)
    assert not equipment.open_item_detail(request, is_current=page.is_current_selection)
    if change != "new_equipment":
        assert equipment.item_request is None and not equipment.item_evidence_model.rows
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


def test_a_query_snapshot_filters_and_detail_restored(window):
    window.navigate("dashboard")
    window.query_panel.apply_draft()
    equipment = window.pages["equipment"]
    equipment.open_all("dashboard")
    equipment.search.setText("DEMO")
    equipment.open_row(0)
    snapshot, selection = equipment.snapshot, equipment.selected_id
    detail_rows, headings = equipment.detail_model.rows, equipment.detail_model.headings
    applied, draft, targets = window.query_state.applied, window.query_state.draft, window.query_state.target_draft
    window.navigate("items")
    page = open_item(window)
    page.open_equipment(1)
    assert equipment.snapshot is snapshot
    equipment.back_button.click()
    assert window.query_state.applied is applied and window.query_state.draft == draft
    assert window.query_state.target_draft == targets
    assert equipment.snapshot is snapshot and equipment.search.text() == "DEMO"
    assert equipment.mode == "detail" and equipment.selected_id == selection
    assert equipment.detail_model.rows == detail_rows and equipment.detail_model.headings == headings
    window.navigate("equipment")
    assert equipment.item_request is None and not equipment.item_evidence_table.isVisible()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
def test_heatmap_and_reused_detail_responsive(window, size):
    page = open_item(window)
    page.distribution_view.setCurrentIndex(1)
    window.resize(*size)
    QTest.qWait(40)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    page.open_equipment(1)
    QTest.qWait(40)
    assert window.scroll.horizontalScrollBar().maximum() == 0
