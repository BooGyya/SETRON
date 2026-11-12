"""일반 분석 기준일과 분리한 HPA 최신 원본의 목록·검색·상세 경로를 검증한다."""

from dataclasses import replace
from datetime import date
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.app import create_window
from setron.ui.dashboard_data import DashboardDrilldown, DashboardSource, EquipmentLocation, GroupBinding
from setron.ui.database_source import DatabaseDashboardSource
from setron.ui.equipment import EquipmentPage
from setron.ui.equipment_data import equipment_data
from setron.ui.heatmap_data import EquipmentSelection, GoalState
from setron.ui.item_data import item_data_from_source
from setron.ui.query_state import UNASSIGNED, GroupOption, LineOption, QueryCatalog, QuerySelection, QueryState, SiteOption
from setron.ui.theme import install_theme
from setron.ui.version_data import catalog_from_dashboard


ANALYSIS, OTHER_HPA, LATEST = date(2026, 10, 1), date(2026, 10, 15), date(2026, 11, 1)


def record(equipment_id, day, *, hpa=True, version="vHPA.2_d5 ", line="L-new", site="시험 A"):
    """날짜·원문·분류가 명시된 합성 관측을 만든다.

    param
    - equipment_id, day: 시험 설비 ID와 실제 관측일.
    - hpa, version, line, site: 검증할 원본 분류·SW·구역·사업장.

    return
    - 실제 업무 자료와 무관한 EquipmentRecord.
    """
    return EquipmentRecord(equipment_id, f"TEST-{equipment_id}", day, site, line,
                           "HPA" if hpa else "M-O2",
                           (PmVersion(None, 9 if hpa else 1, 90 if hpa else 10, version),), is_hpa=hpa)


def source(records=None):
    """오래된 HPA 위치 매핑과 최신 원본을 함께 가진 공급자를 만든다.

    param
    - records: 기본 관측 목록 대신 사용할 합성 관측.

    return
    - 일반 설비의 목표 N과 위험을 확인할 수 있는 메모리 공급자.
    """
    records = tuple(records) if records is not None else (
        record(1, ANALYSIS, hpa=False, version="v2", line="L-old"),
        record(9, ANALYSIS, version="vHPA.old", line="L-old"), record(9, LATEST),
        record(10, OTHER_HPA, version="vHPA.other"))
    catalog = QueryCatalog(sites=(SiteOption("a", "시험 A"), SiteOption("b", "시험 B")),
                           lines=(LineOption("old", "원본과 다른 정규 Line", "a", None),),
                           groups=(GroupOption("o2", "O2", "OXIDE"),))
    families = {10: FamilyReleases(10, (ReleaseVersion(101, 10, "v1", 1), ReleaseVersion(102, 10, "v2", 2)))}
    return DashboardSource(catalog=catalog, bindings=(GroupBinding("o2", (1,)),), records=records,
        locations={r.equipment_id: EquipmentLocation("a", "L-old", None, "old") for r in records},
        families=families, targets={(1, 10): 102}, risks={r.equipment_id: RiskStatus.RISK for r in records},
        source_label="합성 HPA 최신 조회 시험")


@pytest.fixture(scope="module")
def app():
    """실제 QWidget 이벤트를 처리할 Qt 앱을 제공한다."""
    value = QApplication.instance() or QApplication([])
    install_theme(value)
    return value


@pytest.fixture
def windows(app):
    """시험 창을 닫아 다른 Qt 검사에 상태가 남지 않게 한다."""
    created = []
    yield created
    for window in created:
        window.close()
        window.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("reverse", [False, True], ids=["저장 순서", "역순"])
def test_header_list_opens_latest_hpa_search_and_actual_observation_detail(windows, reverse):
    """분석 기준일 뒤에 저장한 HPA를 전체 목록 버튼·버전 검색·Enter 상세로 조회한다."""
    data = source()
    if reverse:
        data = source(reversed(data.records))
    window = create_window(data)
    windows.append(window)
    window.show()
    query = window.query_panel.apply_draft()
    dashboard, page = window.pages["dashboard"], window.pages["equipment"]
    snapshot = dashboard.snapshot
    assert snapshot.base_date == ANALYSIS and snapshot.summary.equipment_count == 1
    assert snapshot.summary.rate == 1 and snapshot.risky_ids == (1,)
    assert tuple(p.day for p in snapshot.observations) == (ANALYSIS,)
    QTest.mouseClick(window.header.actions["equipment_list"], Qt.MouseButton.LeftButton)
    assert window.route == "equipment" and set(page.scope_ids) == {1, 9, 10}
    assert len(page.data.details) == 3
    row = next(row for row in page.model.rows if row[0] == "TEST-9")
    assert "vHPA.2_d5 " in row[3] and "vHPA.old" not in row[3]
    assert "L-new" in row[1] and str(LATEST) in row[1]
    page.search.setText("vHPA.2_d5")
    assert page.model.ids == (9,)
    page.table.setCurrentIndex(page.model.index(0, 0))
    QTest.keyClick(page.table, Qt.Key.Key_Return)
    assert page.mode == "detail" and page.selected_id == 9
    assert page.detail_model.rows[0][2:5] == ("vHPA.2_d5 ", "—", "분석 제외")
    assert page.observation_date.text() == f"관측일 {LATEST}"
    evidence = {title: value for _, title, value in page.evidence_values}
    assert evidence["실제 관측일"] == f"{LATEST} (일반 분석 기준일 {ANALYSIS})"
    assert page.snapshot is snapshot and page.snapshot.query is query


def test_detail_checks_query_date_and_actual_hpa_date_separately(windows):
    """공통 요청 날짜를 HPA 날짜로 바꾸거나 오래된 원본 날짜를 지정하면 거부한다."""
    window = create_window(source())
    windows.append(window)
    query = window.query_panel.apply_draft()
    page = window.pages["equipment"]
    assert page.open_detail(EquipmentSelection(query, ANALYSIS, 9, LATEST))
    assert not page.open_detail(EquipmentSelection(query, LATEST, 9, LATEST))
    assert not page.open_detail(EquipmentSelection(query, ANALYSIS, 9, ANALYSIS))
    assert page.open_detail(EquipmentSelection(query, ANALYSIS, 10, OTHER_HPA))
    assert page.observation_date.text() == f"관측일 {OTHER_HPA}"


def test_hpa_lookup_does_not_expand_dashboard_or_risk_drilldowns(windows):
    """전체 목록의 새 HPA를 기존 분석 전달 범위나 위험 후보에 자동 추가하지 않는다."""
    window = create_window(source())
    windows.append(window)
    query = window.query_panel.apply_draft()
    page = window.pages["equipment"]
    assert page.snapshot.equipment_ids == (1, 9)
    assert page.open_scope(DashboardDrilldown(query, ANALYSIS, "분석 범위", (1,)))
    assert page.scope_ids == (1,)
    assert not page.open_scope(DashboardDrilldown(query, ANALYSIS, "범위 확대", (1, 10)))
    assert not page.open_scope(DashboardDrilldown(query, ANALYSIS, "위험 위조", (9,), True))
    assert page.open_scope(DashboardDrilldown(query, ANALYSIS, "위험 후보", (1,), True))
    assert page.model.ids == (1,)
    heatmap = window.pages["heatmap"]
    heatmap.risk_button.setChecked(True)
    assert set(heatmap.tile_buttons) == {1}


def test_filtered_dashboard_scope_keeps_its_hpa_observation_when_latest_is_outside_line(windows):
    """다른 구역으로 옮긴 최신 HPA를 가져오지 않고 원래 전달한 날짜·호기 범위를 유지한다."""
    window = create_window(source())
    windows.append(window)
    window.query_panel.set_draft(QuerySelection(line_key="old"))
    query = window.query_panel.apply_draft()
    page = window.pages["equipment"]
    assert page.snapshot.equipment_ids == (1, 9)
    assert page.snapshot.hpa_lookup_records == ()
    assert set(page.data.equipment_ids) == {1, 9}
    assert page.open_scope(DashboardDrilldown(query, ANALYSIS, "기존 구역 HPA", (9,)))
    assert page.scope_ids == (9,) and page.model.ids == (9,)
    assert page.open_detail(EquipmentSelection(query, ANALYSIS, 9, ANALYSIS))
    assert page.detail_model.rows[0][2] == "vHPA.old"
    assert page.observation_date.text() == f"관측일 {ANALYSIS}"
    assert not page.open_detail(EquipmentSelection(query, ANALYSIS, 9, LATEST))


def test_snapshot_without_separate_lookup_fields_retains_same_day_hpa():
    """기존 형식의 스냅샷도 같은 날짜 HPA를 조회 범위에서 잃지 않는다."""
    data = source()
    snapshot = data.snapshot(QueryState(data.catalog).apply())
    legacy = replace(snapshot, hpa_lookup_records=(), hpa_lookup_locations=())
    found = equipment_data(legacy, data)
    hpa = next(detail for detail in found.details if detail.record.equipment_id == 9)
    assert found.equipment_ids == snapshot.equipment_ids
    assert hpa.record.base_date == ANALYSIS and hpa.record.pms[0].version_raw == "vHPA.old"


def test_hpa_only_source_lists_each_equipment_latest_date_without_common_analysis(windows):
    """서로 다른 날짜의 HPA만 있어도 묶어 분석하지 않고 각 원본을 조회한다."""
    data = source((record(9, ANALYSIS, version="vHPA.old"), record(9, LATEST),
                   record(10, OTHER_HPA, version="vHPA.other")))
    window = create_window(data)
    windows.append(window)
    query = window.query_panel.apply_draft()
    page = window.pages["equipment"]
    assert page.open_all() and set(page.scope_ids) == {9, 10}
    assert page.snapshot.summary.equipment_count == 0 and page.snapshot.summary.rate is None
    assert page.snapshot.observations == ()
    assert page.open_detail(EquipmentSelection(query, LATEST, 10, OTHER_HPA))
    assert page.observation_date.text() == f"관측일 {OTHER_HPA}"
    assert page.detail_model.rows[0][2] == "vHPA.other"


def test_latest_hpa_scope_uses_current_site_and_does_not_inherit_old_line_mapping():
    """최신 사업장·구역을 사용하고 이전 구역의 정규 Line으로 새 자료를 표시하지 않는다."""
    data = source((record(1, ANALYSIS, hpa=False), record(9, ANALYSIS, line="L-old"),
                   record(9, LATEST, line="L-new", site="시험 B")))
    state = QueryState(data.catalog)
    state.set_draft(QuerySelection(site_key="b"))
    assert tuple(r.equipment_id for r in data.snapshot(state.apply()).hpa_lookup_records) == (9,)
    state.set_draft(QuerySelection(site_key="a"))
    assert data.snapshot(state.apply()).hpa_lookup_records == ()
    state.set_draft(QuerySelection(line_key="old"))
    assert data.snapshot(state.apply()).hpa_lookup_records == ()
    state.set_draft(QuerySelection(line_key=UNASSIGNED))
    assert tuple(r.equipment_id for r in data.snapshot(state.apply()).hpa_lookup_records) == (9,)
    state.set_draft(QuerySelection(group_key="o2"))
    assert data.snapshot(state.apply()).hpa_lookup_records == ()


def test_hpa_lookup_is_bound_to_snapshot_when_source_changes(app):
    """새 조회 없이 source 원본·위치가 바뀌어도 캡처한 HPA를 다른 값으로 교체하지 않는다."""
    data = source()
    state = QueryState(data.catalog)
    query = state.apply()
    snapshot = data.snapshot(query)
    data.records = tuple(r for r in data.records if not r.is_hpa) + (record(9, date(2026, 12, 1), version="vHPA.later"),)
    data.locations = {1: data.locations[1]}
    page = EquipmentPage(state, data)
    try:
        assert page.accept_result(query, snapshot)
        assert page.open_detail(EquipmentSelection(query, ANALYSIS, 9, LATEST))
        assert page.detail_model.rows[0][2] == "vHPA.2_d5 "
        assert page.observation_date.text() == f"관측일 {LATEST}"
    finally:
        page.deleteLater()
        app.processEvents()


def test_mixed_hpa_history_is_an_explicit_snapshot_error():
    """일반/HPA가 같은 ID에 섞인 원문을 최신 HPA로 덮어써 표시하지 않는다."""
    data = source((record(9, ANALYSIS, hpa=False), record(9, LATEST)))
    with pytest.raises(ValueError, match="원문 분류를 검토"):
        data.snapshot(QueryState(data.catalog).apply())


def test_existing_mixed_history_explains_conflict_and_clears_previous_views(windows, app):
    """이미 잘못 저장된 분류 이력은 원문 검토를 안내하고 이전 숫자·목록·AI 자료를 쓰지 않는다."""
    original = source()
    current = [original]
    database = DatabaseDashboardSource(lambda: current[0])
    analysis_calls = []
    window = create_window(database, version_loader=lambda: catalog_from_dashboard(current[0]),
                           ai_analyzer=lambda *args: analysis_calls.append(args))
    windows.append(window)
    window.show()
    window.query_panel.apply_draft()
    dashboard, heatmap, equipment, ai = (window.pages[key]
                                        for key in ("dashboard", "heatmap", "equipment", "ai"))
    assert dashboard.snapshot is not None and dashboard.metrics["total"].value.text() == "1대"
    assert heatmap.tiles and equipment.data is not None
    assert window.header.actions["equipment_list"].isEnabled()

    changed_day = date(2026, 12, 1)
    current[0] = source((*original.records, record(9, changed_day, hpa=False)))
    window.query_panel.apply_draft()

    assert dashboard.snapshot is None
    assert all(metric.value.text() == "—" for metric in dashboard.metrics.values())
    assert "조회 보류" in dashboard.result_status.text()
    assert "원문 분류를 검토" in dashboard.message.text()
    assert f"TEST-9 · {LATEST.isoformat()} · HPA" in dashboard.message.text()
    assert f"TEST-9 · {changed_day.isoformat()} · 일반 설비" in dashboard.message.text()
    assert "설비 ID" not in dashboard.message.text()
    assert "TEST-1" not in dashboard.message.text()
    assert heatmap.snapshot is None and not heatmap.tiles and not heatmap.tile_buttons
    assert all(metric.value.text() == "—" for metric in heatmap.metrics.values())
    assert equipment.snapshot is None and equipment.data is None and not equipment.model.ids
    assert not window.header.actions["equipment_list"].isEnabled()
    assert ai.facts_provider() is None and ai._current_snapshot() is None
    assert "대시보드" in ai.facts_label.text() and "분석" in ai.facts_label.text()

    assert ai.analyze()
    for _ in range(300):
        app.processEvents()
        if ai.busy is None:
            break
        QTest.qWait(10)
    assert ai.busy is None and not analysis_calls
    assert "조회 실패" in ai.status.text()

    current[0] = original
    window.query_panel.apply_draft()
    assert dashboard.snapshot is not None and dashboard.metrics["total"].value.text() == "1대"
    assert heatmap.tiles and equipment.data is not None
    assert window.header.actions["equipment_list"].isEnabled()


def test_item_observations_keep_general_analysis_date_after_new_hpa_save():
    """HPA의 새 날짜가 Item 화면의 일반 설비 관측을 지우지 않는다."""
    data = source()
    result = item_data_from_source(catalog_from_dashboard(data), data)
    assert result.base_date == ANALYSIS
    assert any(r.equipment_id == 1 for r in result.records)
    assert all(r.base_date == ANALYSIS for r in result.records)
