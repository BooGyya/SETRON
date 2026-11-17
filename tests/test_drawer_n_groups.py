"""QA Q-02: 설비 상세(사이드 패널·설비 목록)도 히트맵 카드와 같은 저장 N 그룹으로 판정한다."""

from dataclasses import replace
from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.version.groups import Slot, VersionGroups
from setron.features.version.schemas import CompareStatus, ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.app import create_window
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding
from setron.ui.equipment_data import equipment_data
from setron.ui.heatmap_data import EquipmentSelection, GoalState
from setron.ui.query_state import GroupOption, QueryCatalog, QueryState, SiteOption
from setron.ui.theme import install_theme

DAY = date(2026, 10, 21)


def source():
    """목표 N 하나는 v2, 설비는 v1 → 목표 하나로 보면 미달."""
    catalog = QueryCatalog(sites=(SiteOption("site", "시험 사업장"),), groups=(GroupOption("o2", "O2", "OXIDE"),))
    families = {10: FamilyReleases(10, (ReleaseVersion(101, 10, "v1", 1), ReleaseVersion(102, 10, "v2", 2)))}
    record = EquipmentRecord(1, "TEST-1", DAY, "시험 사업장", None, "M-O2", (PmVersion(None, 1, 10, "v1", 101),))
    return DashboardSource(catalog=catalog, bindings=(GroupBinding("o2", (1,)),), records=(record,),
                           locations={1: EquipmentLocation("site")}, families=families,
                           targets={(1, 10): 102}, risks={}, source_label="시험 자료")


def detail_of(data):
    snapshot = data.snapshot(QueryState(data.catalog).apply())
    return snapshot, equipment_data(snapshot, data).details[0]


def test_without_saved_group_detail_uses_single_target():
    _, detail = detail_of(source())

    assert detail.whole.goal is GoalState.BELOW
    assert detail.status.families[0].pms[0].comparison.target.version_raw == "v2"


def test_saved_group_is_used_by_detail_like_heatmap_tile():
    data = source()
    data.saved_groups = {(1, 10): VersionGroups({Slot.N: frozenset({101})})}   # v1을 N 칸에

    snapshot, detail = detail_of(data)

    assert detail.selected.goal is GoalState.MET            # 히트맵 카드 판정
    assert detail.whole.goal is GoalState.MET               # 상세 패널 판정 (예전에는 '정보 부족'·'목표 미달')
    comparison = detail.status.families[0].pms[0].comparison
    assert comparison.status is CompareStatus.AT_OR_ABOVE and comparison.target.version_raw == "v1"
    assert isinstance(snapshot.effective_targets[(1, 10)], VersionGroups)


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


def test_detail_opens_for_equipment_above_n(app):
    """N+1 이상(진초록) 설비도 상세가 열린다. 예전에는 글자색 표에 'strong'이 없어 KeyError로 열리지 않았다."""
    data = source()
    data.records = (replace(data.records[0], pms=(PmVersion(None, 1, 10, "v2", 102),)),)
    data.saved_groups = {(1, 10): VersionGroups({Slot.N: frozenset({101})})}   # v2는 N보다 한 칸 위 → N+1 이상
    window = create_window(data)
    try:
        query = window.query_panel.apply_draft()
        page = window.pages["equipment"]
        window.header.actions["equipment_list"].click()
        assert page.open_detail(EquipmentSelection(query, DAY, 1))
        assert page.judgement["goal"][0].text() == GoalState.MET.value
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
