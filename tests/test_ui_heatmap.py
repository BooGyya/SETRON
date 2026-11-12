"""5번 작업: 공통 결과·한 설비 한 타일·독립 판정·표시 전환·후속 선택 계약."""

from dataclasses import replace
from datetime import date
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QModelIndex, QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import CompareStatus, ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.app import create_window
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding, RiskRuleState
from setron.ui.heatmap import RISK_FILL, RISK_LINE, TIER_FILL, TileButton
from setron.ui.heatmap_data import GoalState, equipment_tiles
from setron.ui.heatmap_grouping import GROUPING_MODES
from setron.ui.query_state import UNASSIGNED, FabOption, GroupOption, LineOption, QueryCatalog, QuerySelection, QueryState, SiteOption
from setron.ui.theme import TONES, install_theme


DAY = date(2026, 10, 21)


def tree_indexes(model, parent=QModelIndex()):
    for row in range(model.rowCount(parent)):
        index = model.index(row, 0, parent)
        yield index
        yield from tree_indexes(model, index)


def equipment_indexes(model, equipment_id):
    return tuple(index for index in tree_indexes(model)
                 if index.data(Qt.ItemDataRole.UserRole) == equipment_id)


@pytest.fixture
def source():
    catalog = QueryCatalog(sites=(SiteOption("s1", "테스트 A"), SiteOption("s2", "테스트 B")),
                           fabs=(FabOption("f1", "Fab A", "s1"), FabOption("f2", "빈 Fab", "s2")),
                           lines=(LineOption("l1", "Line A", "s1", "f1"), LineOption("l2", "Line B", "s1", None)),
                           groups=(GroupOption("o2", "O2", "OXIDE"), GroupOption("o3", "O3", "OXIDE")))
    families = {fid: FamilyReleases(fid, (ReleaseVersion(fid * 10 + 1, fid, "v99", 1),
                                        ReleaseVersion(fid * 10 + 2, fid, "v2", 2))) for fid in (1001, 1002)}
    # 문자열의 대소가 아니라 저장된 릴리즈 순서로 N-1을 계산해야 한다.
    records = tuple(EquipmentRecord(eid, f"TEST-{eid}", DAY, "임의 원본 Site", "원본 Line", "TEST", pms, is_hpa=eid == 4)
                    for eid, pms in (
                        (1, (PmVersion("PM1", 101, 1001, "v99"), PmVersion("PM2", 101, 1001, "v2"),
                             PmVersion("PM3", 102, 1002, "v2"))),
                        (2, (PmVersion(None, 101, 1001, "v2"),)),
                        (3, (PmVersion(None, 101, 1001, None),)), (4, ()), (5, ())))
    return DashboardSource(catalog=catalog, bindings=(GroupBinding("o2", (101,)), GroupBinding("o3", (102,))),
                           records=records, locations={1: EquipmentLocation("s1", "P1", "f1", "l1"),
                                                      2: EquipmentLocation("s1", "P1", None, "l2"),
                                                      3: EquipmentLocation("s2"), 4: EquipmentLocation("s2"),
                                                      5: EquipmentLocation("s1")}, families=families,
                           targets={(101, 1001): 10012, (102, 1002): 10022},
                           risks={1: RiskStatus.RISK, 2: RiskStatus.RISK}, source_label="합성 예시 · 테스트 전용")


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


@pytest.fixture
def window(app, source):
    result = create_window(source)
    result.show()
    result.navigate("heatmap")
    # 범위 검증은 HPA까지 모두 보이는 상태에서 한다. 기본값(HPA 숨김)은 별도 테스트로 확인한다.
    result.pages["heatmap"].hpa_button.setChecked(True)
    QTest.qWait(30)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def test_one_tile_per_physical_equipment_preserves_all_pm_and_comparison(source):
    state = QueryState(source.catalog)
    result = source.snapshot(state.apply())
    tiles = equipment_tiles(result, source)
    assert [t.equipment_id for t in tiles] == list(result.equipment_ids) == [1, 2, 3, 4, 5]
    mixed = tiles[0]
    assert mixed.multi_sw and mixed.pm_mismatch and len(mixed.families) == 2
    assert mixed.goal is GoalState.BELOW and mixed.risk is RiskStatus.RISK
    assert len(mixed.families[0].versions) == 2
    assert "PM1 · v99 · N-1" in mixed.description
    assert "PM2 · v2 · N 이상" in mixed.description
    assert "PM3 · v2 · N 이상" in mixed.description
    assert "Fab Fab A · Line Line A · 원본 구역 P1" in mixed.location
    assert "목표 N: v2" in mixed.description


def test_goal_risk_unknown_hpa_and_unregistered_are_separate(source):
    state = QueryState(source.catalog)
    tiles = equipment_tiles(source.snapshot(state.apply()), source)
    assert tiles[1].goal is GoalState.MET and tiles[1].risk is RiskStatus.RISK
    assert tiles[2].goal is GoalState.UNKNOWN and tiles[2].risk is RiskStatus.UNKNOWN
    assert "미입력" in tiles[2].description and "버전 값이 비어" in tiles[2].description
    assert tiles[3].goal is GoalState.EXCLUDED and "위험 분석 제외" in tiles[3].description
    assert tiles[4].goal is GoalState.UNKNOWN and "SW 미등록" in tiles[4].description
    assert "위험 미확인" in tiles[4].description and "후보 아님" not in tiles[4].description


@pytest.mark.parametrize("selection,ids", [
    (QuerySelection(site_key="s1"), (1, 2, 5)), (QuerySelection(fab_key="f1"), (1,)),
    (QuerySelection(line_key="l2"), (2,)), (QuerySelection(fab_key=UNASSIGNED, line_key="l2"), (2,)),
    (QuerySelection(line_key=UNASSIGNED), (3, 4, 5)), (QuerySelection(fab_key="f2"), ()),
    (QuerySelection(group_key="o3"), (1,)),
])
def test_tiles_use_exact_shared_canonical_scope_not_raw_location(window, selection, ids):
    window.query_panel.set_draft(selection)
    window.query_panel.apply_draft()
    heatmap, dashboard = window.pages["heatmap"], window.pages["dashboard"]
    assert heatmap.snapshot is dashboard.snapshot
    assert set(heatmap.tile_buttons) == set(ids)
    assert ids == dashboard.snapshot.equipment_ids
    if selection.group_key == "o3":
        tile = heatmap.tiles[0]
        assert tile.goal is GoalState.MET and tile.risk is RiskStatus.RISK
        assert len(tile.families) == 1 and not tile.multi_sw


def test_start_draft_edits_and_apply_use_one_shared_result(window, monkeypatch):
    heatmap, dashboard = window.pages["heatmap"], window.pages["dashboard"]
    assert heatmap.snapshot is None and heatmap.tile_buttons == {}
    assert not heatmap.risk_button.isEnabled()
    original = dashboard.source.snapshot
    calls = []
    def counted(query):
        calls.append(query)
        return original(query)
    monkeypatch.setattr(dashboard.source, "snapshot", counted)
    query = window.query_panel.apply_draft()
    assert calls == [query]
    assert dashboard.snapshot is heatmap.snapshot
    visible_texts = [widget.text() for widget in heatmap.findChildren(QLabel) if widget.isVisible()]
    assert "적용됨" not in visible_texts and "설비군 → Line → 설비" not in visible_texts
    assert not any(text.startswith(("적용 기준 ", "Site·Fab은 조회 조건으로 유지")) for text in visible_texts)
    assert [heatmap.metrics[key].value.text() for key in ("analysis", "met", "below", "unknown")] == [
        "4대", "1대", "1대", "2대",
    ]
    assert tuple(heatmap.metrics) == ("analysis", "met", "below", "unknown")
    assert all(card.caption is None and card.title.alignment() == Qt.AlignmentFlag.AlignCenter
               and card.value.alignment() == Qt.AlignmentFlag.AlignCenter for card in heatmap.metrics.values())
    assert len(heatmap.summary.widgets) == 4
    previous = heatmap.snapshot
    window.query_panel.select("site_key", "s2")
    assert heatmap.snapshot is previous and heatmap.snapshot.query is query
    window.query_panel.apply_draft()
    assert len(calls) == 2 and heatmap.snapshot is not previous
    assert tuple(heatmap.tile_buttons) == (3, 4)


def test_mode_toggle_and_risk_filter_change_display_only_and_can_return(window):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    before = page.snapshot
    QTest.mouseClick(page.view_toggle, Qt.MouseButton.LeftButton)
    assert not page.view_toggle.isChecked() and page.view_toggle.text() == "상세보기"
    QTest.mouseClick(page.view_toggle, Qt.MouseButton.LeftButton)
    assert page.view_toggle.isChecked() and page.view_toggle.text() == "간략히 보기"
    assert "PM1 · v99 · N-1" in page.tile_buttons[1].accessibleName()
    page.risk_button.click()
    assert tuple(page.tile_buttons) == (1, 2)
    assert "표시 2대 / 선택 범위 5대" in page.view_count.text()
    assert page.metrics["analysis"].value.text() == "4대" and page.snapshot is before
    page.risk_button.click()
    assert set(page.tile_buttons) == {1, 2, 3, 4, 5}
    assert window.query_state.applied is before.query


def test_candidate_filter_empty_message_does_not_hide_unknown_as_safe(window):
    window.query_panel.select("site_key", "s2")
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    page.risk_button.click()
    assert not page.risk_button.isEnabled()
    assert page.risk_button.text() == "위험 후보"
    assert page.risk_button.toolTip() == "위험 미확인 1대"
    assert tuple(page.tile_buttons) == (3, 4)


@pytest.mark.parametrize("state,risks,disabled_reason", [
    (RiskRuleState.UNREGISTERED, {}, "위험 규칙 미등록"),
    (RiskRuleState.ALL_DISABLED, {}, "위험 규칙 모두 비활성"),
    (RiskRuleState.ACTIVE, {}, "위험 미확인 4대"),
    (RiskRuleState.ACTIVE, {1: RiskStatus.NO_RISK}, "확인된 후보 0대 · 미확인 3대"),
    (RiskRuleState.ACTIVE, dict.fromkeys((1, 2, 3, 5), RiskStatus.NO_RISK), "위험 후보 0대"),
])
def test_risk_filter_after_requery(window, source, state, risks, disabled_reason):
    page = window.pages["heatmap"]
    window.query_panel.apply_draft()
    page.risk_button.click()
    assert page.risk_button.isChecked()
    page.layout_buttons.button(1).click()
    source.risk_rule_state = state
    source.risks = risks
    window.query_panel.apply_draft()
    assert page.snapshot.risk_rule_state is state
    assert page.risk_button.text() == "위험 후보"
    assert page.risk_button.toolTip() == page.risk_button.accessibleDescription() == disabled_reason
    assert not page.risk_button.isEnabled() and not page.risk_button.isChecked()
    assert set(page.visible_ids) == {1, 2, 3, 4, 5}


def test_keyboard_selection_emits_identity_and_opens_connected_detail(window):
    query = window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    spy = QSignalSpy(page.equipment_selected)
    tile = page.tile_buttons[1]
    tile.setFocus()
    QTest.keyClick(tile, Qt.Key.Key_Space)
    request = spy.at(0)[0]
    assert request.query is query and request.base_date == DAY and request.equipment_id == 1
    assert tile.isChecked() and "설비 상세" in page.selection_notice.text()
    assert window.pages.keys() == {"dashboard", "heatmap", "equipment", "versions", "data", "items", "import", "ai", "replay",
                                   "reports", "settings", "export"}
    assert window.route == "heatmap" and window.pages["equipment"].mode == "detail"
    assert window.equipment_drawer.isVisible()
    page.view_toggle.setChecked(True)
    assert page.tile_buttons[1].isChecked()
    page.tile_buttons[2].click()
    assert page.selected_id == 2 and not page.tile_buttons[1].isChecked()
    window.query_panel.apply_draft()
    assert page.selected_id is None and not any(t.isChecked() for t in page.tile_buttons.values())


def test_tiles_show_risk_border_and_tooltip_with_matching_filter_checks(window):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    # 위험 후보는 굵은 빨간 테두리로 표시하고 판정 설명은 툴팁으로 확인한다.
    style = page.tile_buttons[2].styleSheet()
    assert f"2px solid {RISK_LINE}" in style and RISK_FILL not in style
    assert TIER_FILL[page.tile_buttons[2].data.tier] in style
    assert page.tile_buttons[2].data.risk is RiskStatus.RISK and page.tile_buttons[3].data.risk is RiskStatus.UNKNOWN
    assert "위험 후보" in page.tile_buttons[2].toolTip()
    assert page.tile_buttons[2].data.goal is GoalState.MET
    assert page.risk_button.text() == page.risk_button.accessibleName() == "위험 후보"
    assert page.below_filter.text() == page.below_filter.accessibleName() == "목표 미달"
    assert page.risk_button.icon().isNull()
    assert page.risk_button.styleSheet() == page.below_filter.styleSheet()
    assert "checkbox-tick.svg" in page.risk_button.styleSheet()
    tile = page.tile_buttons[1]
    assert tile.version_label.text() == "v99 / v2"
    assert set(tile.findChildren(QLabel)) == {tile.code_label, tile.version_label}
    assert all(text.alignment() == Qt.AlignmentFlag.AlignCenter for text in (tile.code_label, tile.version_label))


@pytest.mark.parametrize("view", [0, 1])
@pytest.mark.parametrize("layout", [0, 1])
def test_no_question_mark_decoration_in_any_tile_view(window, view, layout):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    page.view_toggle.setChecked(bool(view))
    page.layout_buttons.button(layout).click()
    tiles = page.findChildren(TileButton)
    assert tiles
    assert all(child.text() != "?" for tile in tiles for child in tile.findChildren(QLabel))
    assert not any(child.text().startswith("? ") for child in page.findChildren(QLabel))
    unknown = next(tile for tile in tiles if tile.data.equipment_id == 3)
    assert "위험 미확인" in unknown.toolTip() and "정보 부족" in unknown.toolTip()


def test_hpa_is_hidden_until_special_equipment_toggle(window):
    page = window.pages["heatmap"]
    page.hpa_button.setChecked(False)
    window.query_panel.apply_draft()
    hpa = {t.equipment_id for t in page.tiles if t.goal is GoalState.EXCLUDED}
    assert hpa and not hpa & set(page.tile_buttons)
    page.hpa_button.setChecked(True)
    assert hpa <= set(page.tile_buttons)
    QApplication.processEvents()
    captions = [caption.text() for caption in page.tile_host.findChildren(QLabel) if caption.isVisibleTo(page.tile_host)]
    assert "HPA · 분석 제외" in captions
    assert not any(caption.startswith("HPA /") for caption in captions)


def test_empty_scope_has_no_stale_tiles_or_fake_success(window):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    window.query_panel.select("fab_key", "f2")
    window.query_panel.apply_draft()
    assert page.tile_buttons == {}
    assert all(card.value.text() == "0대" for card in page.metrics.values())
    assert "조건에 맞는 설비 없음" in page.result_status.text()


def test_failure_and_retry_clear_both_pages(window, monkeypatch):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    source = page.source
    original = source.snapshot
    def fail(query):
        raise RuntimeError("테스트 실패")
    monkeypatch.setattr(source, "snapshot", fail)
    window.query_panel.apply_draft()
    assert page.snapshot is None and window.pages["dashboard"].snapshot is None
    assert page.tile_buttons == {}
    assert all(card.value.text() == "—" for card in page.metrics.values())
    assert "실패" in page.result_status.text()
    monkeypatch.setattr(source, "snapshot", original)
    window.query_panel.apply_draft()
    assert len(page.tile_buttons) == 5


def test_stale_result_and_catalog_generation_rejected(window):
    old = window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    previous = page.snapshot
    window.query_panel.apply_draft()
    current = page.snapshot
    assert not page.accept_result(old, previous) and page.snapshot is current
    window.set_query_catalog(page.source.catalog)
    assert page.snapshot is None and page.tile_buttons == {}
    assert not page.accept_result(current.query, current)
    assert "재조회 필요" in page.result_status.text()


def test_mode_result_and_selection_survive_menu_navigation(window):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    page.view_toggle.setChecked(True)
    page.tile_buttons[1].click()
    previous = page.snapshot
    window.navigate("dashboard")
    window.navigate("heatmap")
    assert page.snapshot is previous and page.selected_id == 1
    assert page.view_toggle.isChecked() and page.tile_buttons[1].isChecked()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
@pytest.mark.parametrize("mode", [0, 1])
def test_responsive_tiles_have_no_main_horizontal_overflow(window, size, mode):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    page.view_toggle.setChecked(bool(mode))
    window.resize(*size)
    QTest.qWait(100)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert all((tile.width(), tile.height()) == (140, 80 if mode else 40)
               for tile in page.tile_occurrences)
    assert all(grid.columns == 1 for grid in page.tile_grids)
    assert page.tile_body.count() == len(page.group_cards)
    for index, card in enumerate(page.group_cards):
        assert page.tile_body.itemAt(index).widget() is card
        assert card.x() == 0 and card.width() == page.tile_host.width()
        scroll = card.scroll
        point = scroll.mapTo(page, QPoint(0, 0))
        assert 0 <= point.x() < point.x() + scroll.width() <= page.width()
        assert scroll.verticalScrollBar().maximum() == 0
        assert scroll.widget().height() <= scroll.viewport().height()
        if scroll.widget().width() > scroll.viewport().width():
            assert scroll.horizontalScrollBar().maximum() > 0
        if index:
            previous = page.group_cards[index - 1]
            assert card.y() >= previous.y() + previous.height() + page.tile_body.spacing()


def test_horizontal_filters_and_equal_display_button_heights_at_wide_and_narrow_sizes(window):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    viewport = window.scroll.viewport()
    hpa, view, card_mode = page.hpa_button, page.view_toggle, page.layout_buttons.button(0)
    assert hpa.text() == "HPA 표시"
    page.search.setText("TEST-2")
    assert page.visible_ids == (2,)
    page.below_filter.click()
    assert page.search.text() == "TEST-2" and page.below_filter.isVisible()
    assert not page.visible_ids
    page.search.clear()
    assert page.visible_ids == (1,)

    for size in ((1600, 960), (960, 600), (1600, 960)):
        window.resize(*size)
        QTest.qWait(45)
        view_right = view.mapTo(viewport, QPoint(view.width(), view.height() // 2))
        card_left = card_mode.mapTo(viewport, QPoint(0, card_mode.height() // 2))
        assert 0 < card_left.x() - view_right.x() <= 16
        assert abs(card_left.y() - view_right.y()) <= 1
        assert hpa.geometry().right() < view.x() and hpa.y() == view.y()
        controls = (page.search, page.below_filter, page.risk_button, hpa, view)
        layouts = page.layout_buttons.buttons()
        assert all(control.height() == 38 for control in (page.search, hpa, view))
        assert card_mode.parentWidget().height() == 38 and all(control.height() == 30 for control in layouts)
        assert view.height() == card_mode.parentWidget().height()
        for control in (*controls, *layouts):
            left = control.mapTo(viewport, QPoint(0, 0)).x()
            assert control.isVisible() and 0 <= left < left + control.width() <= viewport.width()
            assert 13 <= control.font().pixelSize() <= 14
        assert page.search.width() >= page.search.minimumWidth()
        if page._control_rows == 1:
            assert page.search.width() + page.filter_bar.width() + page.display_bar.width() + 2 * page.controls.horizontalSpacing() == page.controls_host.width()
        assert window.scroll.horizontalScrollBar().maximum() == 0
        assert page.risk_button.y() == page.below_filter.y()
        assert page.risk_button.geometry().right() < page.below_filter.x()

    page.below_filter.click()
    page.layout_buttons.button(1).click()
    assert page.list_table.isVisible() and set(page.visible_ids) == {1, 2, 3, 4, 5}
    model = page.list_model
    assert model.headings[2] == "모델"
    projections = {(index.siblingAtColumn(2).data(), index.siblingAtColumn(3).data())
                   for index in equipment_indexes(model, 1)}
    assert projections == {("O2", "v99\nv2"), ("O3", "v2")}
    assert model.equipment_index(2).siblingAtColumn(3).data() == "v2"
    assert model.equipment_index(4).siblingAtColumn(3).data() == "분석 제외"
    unknown = model.equipment_index(3).siblingAtColumn(4)
    assert unknown.data(Qt.ItemDataRole.DisplayRole) == "정보 부족"
    assert "버전 값이 비어 있습니다." in unknown.data(Qt.ItemDataRole.ToolTipRole)
    for column in range(model.columnCount()):
        expected_alignment = (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                              if column == 0 else Qt.AlignmentFlag.AlignCenter)
        assert model.equipment_index(1).siblingAtColumn(column).data(Qt.ItemDataRole.TextAlignmentRole) == expected_alignment
    assert model.index(0, 0).data(model.GROUP_ROLE)
    assert model.index(0, 0).data(Qt.ItemDataRole.TextAlignmentRole) == Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


def test_compact_list_hides_detail_columns_and_keeps_row_selection(window):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    page.layout_buttons.button(1).click()
    table, model = page.list_table, page.list_model
    assert page.list_tools.isVisible()
    row = model.ids.index(1)
    index = model.equipment_index(1)
    detailed = model.rows[row]
    assert detailed[1] == "테스트 A\nP1" and detailed[5] == "위험 후보"
    assert "세부 근거 미연결" in index.siblingAtColumn(5).data(Qt.ItemDataRole.ToolTipRole)
    assert [col for col in range(model.columnCount()) if table.isColumnHidden(col)] == [1]
    QApplication.processEvents()
    detailed_height = table.visualRect(index).height()

    page.view_toggle.click()
    assert [model.headings[col] for col in range(model.columnCount()) if not table.isColumnHidden(col)] == [
        "설비명", "버전", "목표 달성",
    ]
    assert model.rows[row][1] == "테스트 A / P1" and model.rows[row][5] == "위험 후보"
    assert model.equipment_index(1).siblingAtColumn(3).data() == "v99 / v2"
    QApplication.processEvents()
    assert table.visualRect(index).height() < detailed_height
    spy = QSignalSpy(page.equipment_selected)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(index).center())
    assert spy.count() == 1 and spy.at(0)[0].equipment_id == page.selected_id == 1
    window.equipment_drawer.close_panel(restore_focus=False)

    for size in ((960, 600), (1600, 960)):
        window.resize(*size)
        QTest.qWait(45)
        assert window.scroll.horizontalScrollBar().maximum() == 0
        assert table.width() == page.controls_host.width()
        assert table.header().viewport().width() == table.viewport().width()
        assert table.header().sectionViewportPosition(3) == table.columnWidth(0)
        assert table.header().sectionViewportPosition(4) == table.columnWidth(0) + table.columnWidth(3)
        assert table.columnWidth(3) <= 200 and table.columnWidth(4) <= 140
        if table.header().length() > table.viewport().width():
            assert table.horizontalScrollBar().maximum() > 0
        else:
            assert table.horizontalScrollBar().maximum() == 0
        if size[0] == 1600:
            assert table.header().length() < table.viewport().width()
        assert table.selectionModel().selectedRows(0)[0].data(Qt.ItemDataRole.UserRole) == 1

    table.setFocus()
    QTest.keyClick(table, Qt.Key.Key_Return)
    assert spy.count() == 2 and spy.at(1)[0].equipment_id == page.selected_id == 1
    window.equipment_drawer.close_panel(restore_focus=False)
    compact_height = table.visualRect(index).height()
    page.view_toggle.click()
    QApplication.processEvents()
    assert [col for col in range(model.columnCount()) if table.isColumnHidden(col)] == [1]
    assert model.rows[row] == detailed and table.visualRect(index).height() > compact_height
    assert table.selectionModel().selectedRows(0)[0].data(Qt.ItemDataRole.UserRole) == 1
    assert table.header().defaultAlignment() == Qt.AlignmentFlag.AlignCenter


def test_default_demo_two_modes_and_catalog_mismatch(app):
    window = create_window()
    window.show()
    window.navigate("heatmap")
    window.pages["heatmap"].hpa_button.setChecked(True)
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    assert page.grouping_mode == "site"
    assert len(page.tile_buttons) == len(page.snapshot.equipment_ids) == 63
    for mode in (0, 1):
        page.view_toggle.setChecked(bool(mode))
        QTest.qWait(60)
        assert all(tile.height() >= tile.minimumSizeHint().height() for tile in page.tile_occurrences)
        assert all((tile.width(), tile.height()) == (140, 80 if mode else 40) for tile in page.tile_occurrences)
        assert all(grid.height() >= grid.minimumSizeHint().height() for grid in page.tile_grids)
        assert all(grid.rect().contains(tile.geometry()) for grid in page.tile_grids for tile in grid.tiles)
    assert page.tiles[0].multi_sw
    window.set_query_catalog(replace(page.source.catalog, sites=(*page.source.catalog.sites, SiteOption("extra", "새 Site"))))
    window.query_panel.apply_draft()
    assert page.snapshot is None and "실패" in page.result_status.text()
    window.close()
    window.deleteLater()


def test_long_raw_version_is_elided_in_fixed_tile_and_preserved_in_tooltip_and_list(window, source, monkeypatch):
    raw = "v90.1.0.41(H) · Hotfix_Etch_2026 + Safety Fix <검토 중> " * 4
    records = tuple(replace(record, pms=tuple(replace(pm, version_raw=raw) if pm.pm_name == "PM1" else pm
                                             for pm in record.pms)) for record in source.records)
    monkeypatch.setattr(source, "records", records)
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    page.view_toggle.setChecked(True)
    window.resize(960, 600)
    QTest.qWait(100)
    tile = page.tile_buttons[1]
    assert raw in tile.accessibleName() and "<검토 중>" in tile.toolTip()
    assert (tile.width(), tile.height()) == (140, 80)
    assert raw not in tile.version_label.text() and "판단 불가" not in tile.version_label.text()
    assert "…" in tile.version_label.text() and not tile.version_label.wordWrap()
    assert page.tiles[0].families[0].versions[0] == f"PM1 · {raw} · 판단 불가"
    assert window.scroll.horizontalScrollBar().maximum() == 0
    for child in tile.findChildren(QLabel):
        assert child.textFormat() is Qt.TextFormat.PlainText
        point = child.mapTo(tile, QPoint(0, 0))
        assert point.x() + child.width() <= tile.width()
        assert point.y() + child.height() <= tile.height()
        if child.wordWrap():
            assert child.heightForWidth(child.width()) <= child.height()
    page.layout_buttons.button(1).click()
    assert page.list_model.equipment_index(1).siblingAtColumn(3).data() == f"{raw}\nv2"


@pytest.mark.parametrize("mode", [key for key, _ in GROUPING_MODES])
def test_each_grouping_supports_tree_projection_filter_changes_and_compact_columns(window, mode):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    snapshot = page.snapshot
    expected_ids = {1, 2, 3, 4, 5}
    if mode == "subline":
        lines = {1: "P1F", 2: "P1D", 3: "P4H", 4: "P4D", 5: "S3"}
        page.present_snapshot(snapshot, tiles=tuple(replace(tile, line_label=lines[tile.equipment_id])
                                                    for tile in page.tiles))
        expected_ids.remove(5)  # 상위 라인만 있는 설비는 세부라인 보기에 섞이지 않는다.
    page.set_grouping(mode)
    page.layout_buttons.button(1).click()
    table, model = page.list_table, page.list_model
    assert model.rowCount() and model.index(0, 0).data(model.GROUP_ROLE)
    assert set(model.ids) == expected_ids
    projections = {(index.siblingAtColumn(2).data(), index.siblingAtColumn(3).data())
                   for index in equipment_indexes(model, 1)}
    assert projections == {("O2", "v99\nv2"), ("O3", "v2")}
    first = model.index(0, 0)
    table.setExpanded(first, False)
    page._render_tiles()
    assert not table.isExpanded(model.index(0, 0))
    for text, ids in (("TEST-1", {1}), ("TEST-2", {2}), ("", expected_ids)):
        page.search.setText(text)
        assert set(model.ids) == ids
        assert page.snapshot is snapshot
    page.view_toggle.setChecked(False)
    assert [column for column in range(model.columnCount()) if not table.isColumnHidden(column)] == [0, 3, 4]
    page.layout_buttons.button(0).click()
    assert page.list_tools.isHidden()
    assert set(page.tile_buttons) == expected_ids
    assert all((tile.width(), tile.height()) == (140, 40) for tile in page.tile_occurrences)


def test_subline_without_detail_lines_clears_previous_cards_and_list_and_explains_empty_scope(window):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    snapshot = page.snapshot
    assert page.tile_buttons
    page.set_grouping("subline")
    for layout in (0, 1):
        page.layout_buttons.button(layout).click()
        assert page.visible_ids == () and not page.tile_buttons and not page.group_cards
        if layout:
            assert page.list_model.rowCount() == 0
        else:
            assert not page.tile_occurrences
        assert page.message.isVisible() and page.message.text() == "표시할 세부라인이 없습니다."
        assert page.list_table.isHidden() and page.snapshot is snapshot
    page.set_grouping("line")
    assert set(page.list_model.ids) == {1, 2, 3, 4, 5}
    assert page.message.isHidden() and page.snapshot is snapshot


def test_same_structure_preserves_cards_cells_and_scroll_while_only_changed_tiles_are_restyled(window, source):
    query = window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    body, cards, controls = page.tile_body, tuple(page.group_cards), tuple(page.tile_occurrences)
    positions = tuple(card.scroll.horizontalScrollBar().value() for card in cards)
    source.risks = {**source.risks, 1: RiskStatus.NO_RISK}
    updated = source.snapshot(query)
    assert page.accept_result(query, updated)
    assert page.tile_body is body and tuple(page.group_cards) == cards and tuple(page.tile_occurrences) == controls
    assert tuple(card.scroll.horizontalScrollBar().value() for card in cards) == positions
    assert all(card.parentWidget() is page.tile_host for card in cards)
    assert page.rebuilt_tiles == 0
    assert page.restyled_tiles == sum(tile.data.equipment_id == 1 for tile in controls)
    assert all(tile.data.risk is RiskStatus.NO_RISK for tile in controls if tile.data.equipment_id == 1)
    assert page.accept_result(query, updated)
    assert page.rebuilt_tiles == page.restyled_tiles == 0
    assert tuple(page.group_cards) == cards and tuple(page.tile_occurrences) == controls


@pytest.mark.parametrize("status,steps,tone", [
    (CompareStatus.AT_OR_ABOVE, None, "success"),
    (CompareStatus.BELOW, 1, "warning"),
    (CompareStatus.BELOW, None, "warning"),
    (CompareStatus.BELOW, 2, "danger"),
    (CompareStatus.BELOW, 5, "danger"),
    (CompareStatus.UNDETERMINED, None, "neutral"),
], ids=["N 이상 초록", "N-1 노랑", "단계 모름은 많이 낮음으로 단정하지 않음", "N-2 빨강", "N-5 빨강", "판단 불가 회색"])
def test_version_gap_colors_follow_requirement(status, steps, tone):
    """요구사항 3.2: 기준 버전 적용은 초록, 많이 낮으면 빨강. 임계값은 확정 전 임시 기준(N-2 이하)."""
    from setron.ui.heatmap_data import FAR_BELOW_STEPS, compare_tone

    assert FAR_BELOW_STEPS == 2
    assert compare_tone(status, steps) == tone



def test_tiles_use_five_tiers_from_shared_tier_of():
    """목표 이상은 녹색, 미달은 분홍 계열의 두 단계로 구분한다."""
    from setron.features.version.groups import Tier
    from setron.ui.heatmap_data import EquipmentTile, GoalState as Goal, TIER_TONES, worst_tier
    from setron.features.risk.schemas import RiskStatus

    assert worst_tier([Tier.ABOVE, Tier.ONE_BELOW, Tier.UNKNOWN]) is Tier.ONE_BELOW
    assert worst_tier([Tier.UNKNOWN]) is Tier.UNKNOWN and worst_tier([]) is Tier.UNKNOWN
    assert TIER_TONES == {Tier.ABOVE: "strong", Tier.AT: "success", Tier.ONE_BELOW: "warning", Tier.TWO_BELOW: "danger",
                          Tier.UNKNOWN: "neutral"}
    tile = lambda goal, tier: EquipmentTile(1, "E", "s", "화성", "loc", goal, RiskStatus.NO_RISK, (), False, False,  # noqa: E731
                                            tier=tier)
    assert tile(Goal.MET, Tier.ABOVE).version_tone == "strong" and tile(Goal.MET, Tier.AT).version_tone == "success"
    assert tile(Goal.BELOW, Tier.TWO_BELOW).version_tone == "danger" and tile(Goal.UNKNOWN, Tier.UNKNOWN).version_tone == "neutral"
    assert tile(Goal.EXCLUDED, Tier.ABOVE).version_tone == "neutral"
    assert len(set(TIER_FILL.values())) == 5
    mild, stronger = (QColor(TIER_FILL[tier]) for tier in (Tier.ONE_BELOW, Tier.TWO_BELOW))
    assert mild.red() > mild.green() and stronger.red() > stronger.green()
    assert mild.lightness() > stronger.lightness()
