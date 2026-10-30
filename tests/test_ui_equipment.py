"""6번 목록/상세: 정확한 전달 범위·검색/페이지·원문·전체 판정·복귀·무효화."""

from dataclasses import replace
from datetime import date
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.app import create_window
from setron.ui.dashboard_data import DashboardDrilldown, DashboardSource, EquipmentLocation, GroupBinding
from setron.ui.equipment_data import equipment_data, filtered_tiles, validate_scope
from setron.ui.heatmap_data import EquipmentSelection, GoalState
from setron.ui.main_window import MainWindow
from setron.ui.query_state import UNASSIGNED, FabOption, GroupOption, LineOption, QueryCatalog, QuerySelection, QueryState, SiteOption
from setron.ui.theme import install_theme, svg_size


DAY = date(2026, 10, 21)


@pytest.fixture
def source():
    catalog = QueryCatalog(sites=(SiteOption("s1", "테스트 A"), SiteOption("s2", "테스트 B")),
                           fabs=(FabOption("f1", "Fab A", "s1"), FabOption("empty", "빈 Fab", "s2")),
                           lines=(LineOption("l1", "Line A", "s1", "f1"), LineOption("l2", "Fab 미지정 Line", "s1", None)),
                           groups=(GroupOption("o2", "O2", "OXIDE"), GroupOption("o3", "O3", "OXIDE")))
    families = {fid: FamilyReleases(fid, (ReleaseVersion(fid * 10 + 1, fid, "v99", 1),
                                        ReleaseVersion(fid * 10 + 2, fid, "v2", 2))) for fid in (1001, 1002)}
    records, locations = [], {}
    for eid in range(1, 19):
        pms = ((PmVersion("PM1", 101, 1001, "v99"), PmVersion("PM2", 101, 1001, "v2"),
                PmVersion("PM3", 102, 1002, "v2")) if eid == 1 else
               () if eid in (4, 5) else (PmVersion(None, 101, 1001, None if eid == 3 else "v2"),))
        records.append(EquipmentRecord(eid, f"TEST-{eid:03}", DAY, "정규 위치가 아닌 원본 Site", "원본 Line", "TEST", pms, is_hpa=eid == 5))
        locations[eid] = EquipmentLocation("s1", "P1", "f1", "l1") if eid == 1 else (
            EquipmentLocation("s1", "P1", None, "l2") if eid == 2 else EquipmentLocation("s1" if eid < 10 else "s2"))
    return DashboardSource(catalog=catalog, bindings=(GroupBinding("o2", (101,)), GroupBinding("o3", (102,))),
                           records=tuple(records), locations=locations, families=families,
                           targets={(101, 1001): 10012, (102, 1002): 10022},
                           risks={eid: RiskStatus.RISK if eid in (1, 2) else RiskStatus.UNKNOWN if eid == 3 else RiskStatus.NO_RISK
                                  for eid in range(1, 19)}, source_label="합성 예시 · 테스트 전용")


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


@pytest.fixture
def window(app, source):
    result = create_window(source)
    result.show()
    QTest.qWait(20)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def open_list(window):
    query = window.query_panel.apply_draft()
    window.header.actions["equipment_list"].click()
    return window.pages["equipment"], query


def test_read_data_keeps_selected_and_whole_equipment_separate(source):
    state = QueryState(source.catalog)
    state.set_draft(QuerySelection(group_key="o3"))
    snapshot = source.snapshot(state.apply())
    data = equipment_data(snapshot, source)
    assert data.snapshot is snapshot and len(data.details) == 1
    detail = data.details[0]
    assert detail.selected.goal is GoalState.MET
    assert detail.whole.goal is GoalState.BELOW
    assert len(detail.record.pms) == 3 and len(detail.status.families) == 2
    assert snapshot.summary.compliant == 1 and snapshot.summary.below == 0
    assert detail.whole.risk is RiskStatus.RISK


def test_filters_are_literal_case_insensitive_and_never_expand_ids(source):
    state = QueryState(source.catalog)
    data = equipment_data(source.snapshot(state.apply()), source)
    assert [t.equipment_id for t in filtered_tiles(data, (1, 2, 3), text="test-002")] == [2]
    assert [t.equipment_id for t in filtered_tiles(data, (1, 2, 3), text="v99")] == [1]
    assert filtered_tiles(data, (1, 2), text=".*") == ()
    assert [t.equipment_id for t in filtered_tiles(data, (1, 2, 3), risk_only=True)] == [1, 2]
    assert [t.equipment_id for t in filtered_tiles(data, (1, 2, 3), goal=GoalState.UNKNOWN)] == [3]
    assert [t.equipment_id for t in filtered_tiles(data, (1, 2, 3), sort="code-desc")] == [3, 2, 1]
    assert not validate_scope(data, (1, 1)) and not validate_scope(data, (999,))
    with pytest.raises(ValueError):
        filtered_tiles(data, (999,))


def test_missing_same_day_record_is_not_filled_from_history(source):
    state = QueryState(source.catalog)
    snapshot = source.snapshot(state.apply())
    source.records = tuple(replace(record, base_date=date(2026, 9, 21)) if record.equipment_id == 1 else record
                           for record in source.records)
    with pytest.raises(ValueError, match="같은 기준일"):
        equipment_data(snapshot, source)


def test_initial_state_disables_list_and_does_not_auto_apply(window):
    page = window.pages["equipment"]
    assert window.query_state.applied is None and page.snapshot is None
    assert not window.header.actions["equipment_list"].isEnabled()
    assert not page.search.isEnabled() and page.model.rowCount() == 0


def test_header_opens_applied_cohort_without_new_query_or_menu(window, monkeypatch):
    source = window.pages["equipment"].source
    calls, original = [], source.snapshot
    def counted(query):
        calls.append(query)
        return original(query)
    monkeypatch.setattr(source, "snapshot", counted)
    page, query = open_list(window)
    assert calls == [query]
    assert page.snapshot is window.pages["dashboard"].snapshot is window.pages["heatmap"].snapshot
    assert page.scope_ids == tuple(range(1, 19))
    assert window.route == "equipment" and window.header.title.text() == "설비 목록"
    assert window.nav["heatmap"].isChecked() and len(window.nav) == 10
    assert page.model.rowCount() == 12 and page.metrics["total"].value.text() == "18대"


@pytest.mark.parametrize("risk_only,ids", [(False, (1, 2, 3, 4, 5, 6, 7, 8, 9)), (True, (1, 2))])
def test_dashboard_cell_and_candidate_deliver_different_exact_cohorts(window, risk_only, ids):
    window.query_panel.apply_draft()
    dashboard = window.pages["dashboard"]
    row = next(index for index, row in enumerate(dashboard.snapshot.rows, 1) if row.position == ("s1", None))
    # 원본 구역 전체 행이 아니라 Site 전체에 해당하는 정확한 목록을 명시적으로 전달
    request = DashboardDrilldown(dashboard.snapshot.query, DAY, "테스트 A", ids, risk_only)
    dashboard.drilldown_requested.emit(request)
    page = window.pages["equipment"]
    assert window.route == "equipment" and page.scope_ids == ids
    assert tuple(t.equipment_id for t in page.visible_tiles) == ids
    assert page.metrics["total"].value.text() == f"{len(ids)}대"
    assert page.risk_button.isChecked() is risk_only
    assert row > 0


def test_actual_dashboard_general_cell_is_not_replaced_by_risk_only(window):
    window.query_panel.apply_draft()
    dashboard = window.pages["dashboard"]
    row = next(index for index, row in enumerate(dashboard.snapshot.rows, 1) if row.position == ("s1", "P1"))
    column = len(dashboard.snapshot.rows[row - 1].cells)
    dashboard.matrix_buttons[row, column].click()
    page = window.pages["equipment"]
    assert page.scope_ids == (1, 2) and not page.risk_button.isChecked()
    page.return_button.click()
    assert window.route == "dashboard"
    dashboard.risk_buttons[row, column].click()
    assert page.scope_ids == (1, 2) and page.risk_button.isChecked()


def test_pagination_search_goal_sort_and_candidate_filters_only_change_display(window):
    page, query = open_list(window)
    snapshot = page.snapshot
    assert page.model.ids == tuple(range(1, 13))
    page.next.click()
    assert page.page_index == 1 and page.model.ids == tuple(range(13, 19))
    assert "13–18" in page.count.text() and not page.next.isEnabled()
    page.search.setText("test-003")
    assert page.page_index == 0 and page.model.ids == (3,)
    page.search.clear()
    page.goal_filter.setCurrentIndex(page.goal_filter.findData(GoalState.UNKNOWN))
    assert page.model.ids == (3, 4)
    page.goal_filter.setCurrentIndex(0)
    page.sort.setCurrentIndex(1)
    assert page.model.ids[0] == 18
    page.risk_button.click()
    assert page.model.ids == (2, 1)
    page.all_button.click()
    assert len(page.visible_tiles) == 18
    assert page.snapshot is snapshot and window.query_state.applied is query
    assert page.metrics["total"].value.text() == "18대"


def test_scope_cannot_be_silently_widened_by_search_or_tabs(window):
    page, query = open_list(window)
    request = DashboardDrilldown(query, DAY, "한 호기 범위", (2,), True)
    assert page.open_scope(request)
    page.all_button.click()
    page.search.setText("TEST-001")
    assert page.model.ids == () and page.scope_ids == (2,)
    page.global_button.click()
    assert page.scope_ids == tuple(range(1, 19)) and page.search.text() == ""


def test_keyboard_row_opens_detail_and_back_keeps_filters_page_and_ids(window):
    page, _ = open_list(window)
    page.sort.setCurrentIndex(1)
    page.next.click()
    previous = page.model.ids
    index = page.model.index(2, 0)
    page.table.setCurrentIndex(index)
    page.table.setFocus()
    QTest.keyClick(page.table, Qt.Key.Key_Return)
    assert page.mode == "detail" and page.selected_id == previous[2]
    assert window.query_panel.isHidden() and window.header.title.text() == "설비 상세"
    page.back_button.click()
    assert page.mode == "list" and not window.query_panel.isHidden()
    assert page.page_index == 1 and page.model.ids == previous and page.sort.currentIndex() == 1


def test_heatmap_direct_detail_and_back_preserve_view_and_selection(window):
    window.query_panel.apply_draft()
    window.navigate("heatmap")
    heatmap = window.pages["heatmap"]
    heatmap.view_buttons.button(1).click()
    heatmap.tile_buttons[1].click()
    page = window.pages["equipment"]
    assert window.route == "equipment" and page.mode == "detail" and page.selected_id == 1
    assert page.detail_model.rowCount() == 3
    assert "위험 후보" in page.detail_summary.text() and "규칙·필수 Item·릴리즈 문서·원본 위치 미연결" in page.evidence.text()
    page.back_button.click()
    assert window.route == "heatmap" and heatmap.view_buttons.checkedId() == 1
    assert heatmap.selected_id == 1 and heatmap.tile_buttons[1].isChecked()


def test_detail_does_not_call_subset_goal_whole_equipment_success(window):
    window.query_panel.set_draft(QuerySelection(group_key="o3"))
    page, query = open_list(window)
    page.open_detail(EquipmentSelection(query, DAY, 1), origin="list")
    assert "선택 SW 판정: 목표 달성 / 설비 전체 등록 SW 판정: 목표 미달" in page.detail_summary.text()
    assert page.detail_model.rowCount() == 3 and "현재 설비군 밖의 SW도 표시" in page.scope_note.text()
    assert page.metrics["rate"].value.text() == "100.0%"  # 선택 SW 지표는 그대로
    assert page.snapshot.query is query and page.scope_ids == (1,)


@pytest.mark.parametrize("eid,goal", [(1, GoalState.BELOW), (2, GoalState.MET), (3, GoalState.UNKNOWN),
                                      (4, GoalState.UNKNOWN), (5, GoalState.EXCLUDED)])
def test_representative_states_use_one_reusable_detail(window, eid, goal):
    page, query = open_list(window)
    assert page.open_detail(EquipmentSelection(query, DAY, eid))
    assert goal.value in page.detail_summary.text()
    assert window.pages.keys() == {"dashboard", "heatmap", "equipment"}
    if eid == 3:
        assert page.detail_model.rows[0][2] == "미입력" and "버전 값이 비어" in page.detail_model.rows[0][5]
    if eid == 4:
        assert not page.detail_model.rows and "SW 관측이 없습니다" in page.evidence.text()
    if eid == 5:
        assert not page.detail_model.rows and "HPA는 분석 제외" in page.evidence.text()


def test_draft_changes_do_not_change_detail_but_reapply_does(window):
    page, query = open_list(window)
    page.open_detail(EquipmentSelection(query, DAY, 1))
    snapshot = page.snapshot
    window.query_panel.select("site_key", "s2")
    assert page.snapshot is snapshot and page.selected_id == 1 and page.mode == "detail"
    window.query_panel.apply_draft()
    assert page.snapshot is not snapshot and page.selected_id is None and page.mode == "list"
    assert page.detail_model.rows == () and page.detail_summary.text() == ""
    assert not page.open_detail(EquipmentSelection(query, DAY, 1))


def test_invalid_scope_date_ids_and_old_query_are_rejected(window):
    page, query = open_list(window)
    assert not page.open_scope(DashboardDrilldown(query, DAY, "잘못된 범위", (999,)))
    assert not page.open_scope(DashboardDrilldown(query, DAY, "중복", (1, 1)))
    assert not page.open_scope(DashboardDrilldown(query, DAY, "잘못된 위험", (3,), True))
    assert not page.open_detail(EquipmentSelection(query, date(2026, 9, 21), 1))
    assert not page.open_detail(EquipmentSelection(query, DAY, 999))
    old = page.snapshot
    window.query_panel.apply_draft()
    assert not page.accept_result(query, old)
    assert not page.open_scope(DashboardDrilldown(query, DAY, "이전 요청", (1,)))


@pytest.mark.parametrize("selection,ids", [(QuerySelection(fab_key="f1"), (1,)), (QuerySelection(line_key="l2"), (2,)),
                                        (QuerySelection(fab_key=UNASSIGNED, line_key="l2"), (2,)),
                                        (QuerySelection(fab_key="empty"), ())])
def test_canonical_fab_line_are_not_inferred_from_raw_area(window, selection, ids):
    window.query_panel.set_draft(selection)
    page, _ = open_list(window)
    assert page.scope_ids == ids
    assert page.metrics["total"].value.text() == f"{len(ids)}대"
    if ids == (1,):
        assert "Fab Fab A" in page.model.rows[0][1] and "Line Line A" in page.model.rows[0][1]
    if not ids:
        assert page.model.rowCount() == 0 and page.metrics["rate"].value.text() == "계산 불가"


def test_catalog_replacement_clears_list_detail_and_header_action(window):
    page, query = open_list(window)
    old = page.snapshot
    page.open_detail(EquipmentSelection(query, DAY, 1))
    window.set_query_catalog(page.source.catalog)
    assert page.snapshot is None and page.data is None and page.selected_id is None
    assert page.model.rowCount() == page.detail_model.rowCount() == 0
    assert not page.accept_result(query, old) and not window.header.actions["equipment_list"].isEnabled()


def test_provider_failure_clears_and_retry_rebuilds_list(window, monkeypatch):
    page, _ = open_list(window)
    original = page.source.snapshot
    def fail(query):
        raise RuntimeError("실패 예시")
    monkeypatch.setattr(page.source, "snapshot", fail)
    window.query_panel.apply_draft()
    assert page.snapshot is None and page.model.rowCount() == 0 and "실패" in page.result_status.text()
    assert not window.header.actions["equipment_list"].isEnabled()
    monkeypatch.setattr(page.source, "snapshot", original)
    window.query_panel.apply_draft()
    assert page.model.rowCount() == 12 and window.header.actions["equipment_list"].isEnabled()


def test_search_empty_and_candidate_empty_do_not_change_shared_totals(window):
    page, _ = open_list(window)
    page.search.setText("없는 호기")
    assert page.model.rowCount() == 0 and "맞는 설비가 없습니다" in page.message.text()
    assert page.metrics["total"].value.text() == "18대"
    page.search.clear()
    page.goal_filter.setCurrentIndex(page.goal_filter.findData(GoalState.EXCLUDED))
    page.risk_button.click()
    assert page.model.rowCount() == 0 and not page.next.isEnabled()


def test_raw_version_plain_text_not_replaced_and_all_pm_rows_remain(window, source, monkeypatch):
    raw = "v90.1.0.41(H) + Hotfix <검토 중> " * 8
    monkeypatch.setattr(source, "records", tuple(replace(r, pms=tuple(replace(pm, version_raw=raw) if pm.pm_name == "PM1" else pm
                                                                   for pm in r.pms)) for r in source.records))
    page, query = open_list(window)
    page.open_detail(EquipmentSelection(query, DAY, 1))
    assert page.detail_model.rows[0][2] == raw and page.detail_model.rowCount() == 3
    index = page.detail_model.index(0, 2)
    assert page.detail_model.data(index, Qt.ItemDataRole.ToolTipRole) == raw
    assert "릴리즈 노트에 없는" in page.detail_model.rows[0][5]


def test_auxiliary_route_keeps_parent_navigation_and_rejects_invalid_registration(app):
    window = MainWindow()
    window.register_page("heatmap", QWidget())
    window.register_auxiliary_page("sub", QWidget(), parent_key="heatmap", title="하위 화면")
    assert window.navigate("sub") and window.header.title.text() == "하위 화면"
    assert window.nav["heatmap"].isChecked() and sum(n.isChecked() for n in window.nav.values()) == 1
    with pytest.raises(ValueError):
        window.register_auxiliary_page("sub", QWidget(), parent_key="heatmap", title="중복")
    with pytest.raises(ValueError):
        window.register_auxiliary_page("other", QWidget(), parent_key="unknown", title="미등록")
    window.close()
    window.deleteLater()


def test_return_to_heatmap_restores_origin_scroll_after_layout(window):
    window.resize(960, 600)
    window.query_panel.apply_draft()
    window.navigate("heatmap")
    heatmap = window.pages["heatmap"]
    heatmap.view_buttons.button(1).click()
    QTest.qWait(100)
    window.scroll.verticalScrollBar().setValue(350)
    before = window.scroll.verticalScrollBar().value()
    assert before == 350
    heatmap.tile_buttons[1].click()
    window.pages["equipment"].back_button.click()
    QTest.qWait(100)
    assert window.route == "heatmap" and window.scroll.verticalScrollBar().value() == before


def test_hpa_raw_observation_is_visible_without_comparison(window, source, monkeypatch):
    records = tuple(replace(r, pms=(PmVersion(None, 101, 1001, "HPA 원문"),)) if r.is_hpa else r for r in source.records)
    monkeypatch.setattr(source, "records", records)
    page, query = open_list(window)
    page.open_detail(EquipmentSelection(query, DAY, 5))
    assert page.detail_model.rows[0][2:5] == ("HPA 원문", "—", "분석 제외")
    assert "HPA는 N 비교에서 제외" in page.detail_model.rows[0][5]


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
@pytest.mark.parametrize("mode", ["list", "detail"])
def test_responsive_layout_scrolls_tables_not_main_content(window, size, mode):
    page, query = open_list(window)
    if mode == "detail":
        page.open_detail(EquipmentSelection(query, DAY, 1), origin="list")
    window.resize(*size)
    QTest.qWait(100)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    current = page.detail_card if mode == "detail" else page.list_card
    point = current.mapTo(page, QPoint(0, 0))
    assert point.x() + current.width() <= page.width()
    table = page.detail_table if mode == "detail" else page.table
    if size[0] == 960:
        assert table.horizontalScrollBar().maximum() > 0
    for card in page.metrics.values():
        assert card.picture.pixmap().deviceIndependentSize().toSize() == svg_size(card.picture.property("assetFile"))
