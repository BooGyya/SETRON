"""설비 위험 표의 선택 기준 버전과 판정 원문·조회 시점 메타데이터를 검증한다."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QWidget

from setron.features.aggregation.schemas import PmVersion
from setron.features.item.service import ItemCatalog
from setron.features.risk.schemas import FindingBasis, MappingStatus, RiskFinding, RiskRule, RiskStatus, RuleType
from setron.features.risk.service import RiskAssessor
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.equipment_data import EquipmentDetail, equipment_data
from setron.ui.equipment_drawer import EquipmentDrawer
from setron.ui.equipment_risk_table import RiskCriteriaTable
from setron.ui.query_state import QueryState
from setron.ui.replay_data import ReplayRequest, load_replay, replay_detail

from test_ui_heatmap import DAY, app, source  # noqa: F401


@pytest.fixture
def own(app):
    widgets = []

    def show(widget):
        widgets.append(widget)
        widget.show()
        QTest.qWait(10)
        return widget

    yield show
    for widget in reversed(widgets):
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def rule(kind=RuleType.DIRECT_VERSION, **changes):
    return replace(RiskRule(7, kind, 10, "이전 사유", release_version_id=22,
                            mapping_status=MappingStatus.VERIFIED,
                            display_name="기준 <원문> & 이름", memo="첫 설명\n둘째 <설명>"), **changes)


def finding(value, *, status=RiskStatus.RISK, basis=FindingBasis.DIRECT_VERSION, pm="PM1"):
    observed = ReleaseVersion(11, 10, "관측 버전", 1)
    return RiskFinding(value, status, basis, PmVersion(pm, 1, 10, observed.version_raw, 11), observed)


@pytest.mark.parametrize("kind,basis,caption", [
    (RuleType.DIRECT_VERSION, FindingBasis.DIRECT_VERSION, "버전 일치"),
    (RuleType.VERSION_BELOW, FindingBasis.VERSION_BELOW, "버전 미만"),
    (RuleType.VERSION_ABOVE, FindingBasis.VERSION_ABOVE, "버전 초과"),
])
def test_reference_uses_rule_selected_release_and_preserves_name_memo_raw(own, kind, basis, caption):
    value = rule(kind)
    table = own(RiskCriteriaTable((finding(value, basis=basis),),
                                  {(10, 22): "  기준_v2_n4  ", (20, 22): "다른 계열 버전"}))
    assert table.rows == ((value.display_name, "  기준_v2_n4  ", value.memo, caption),)
    assert "관측 버전" not in table.rows[0] and not table.unknown_rows
    cells = table.table.findChildren(QLabel)
    assert all(cell.textFormat() == Qt.TextFormat.PlainText and cell.wordWrap() for cell in cells)
    assert all(cell.alignment() == Qt.AlignmentFlag.AlignCenter for cell in cells)
    assert value.memo in [cell.text() for cell in cells]


def test_many_pm_matches_deduplicate_by_rule_id_while_unknown_evidence_stays_separate(own):
    first = rule()
    same_name = replace(first, risk_rule_id=8, release_version_id=23)
    unresolved = replace(first, risk_rule_id=9, display_name="미확인 전용 기준")
    findings = (finding(first), finding(first, pm="PM2"), finding(same_name),
                finding(first, status=RiskStatus.UNKNOWN, basis=FindingBasis.VERSION_ORDER_UNKNOWN),
                finding(first, status=RiskStatus.UNKNOWN, basis=FindingBasis.VERSION_ORDER_UNKNOWN, pm="PM3"),
                finding(unresolved, status=RiskStatus.UNKNOWN, basis=FindingBasis.MAPPING_UNRESOLVED))
    table = own(RiskCriteriaTable(findings, {(10, 22): "기준2", (10, 23): "기준3"}))
    assert table.rows == ((first.display_name, "기준2", first.memo, "버전 일치"),
                          (same_name.display_name, "기준3", same_name.memo, "버전 일치"))
    assert table.unknown_rows == ((first.display_name, findings[3].message),
                                  (unresolved.display_name, findings[-1].message))
    assert table.unknown_notice.isVisible() and "미확인" in table.unknown_notice.text()
    assert unresolved.display_name not in [row[0] for row in table.rows]


def test_unknown_only_never_creates_matched_rule_row_or_uses_observed_version(own):
    value = rule(RuleType.VERSION_BELOW)
    unknown = finding(value, status=RiskStatus.UNKNOWN, basis=FindingBasis.VERSION_UNKNOWN)
    table = own(RiskCriteriaTable((unknown,), {(10, 22): "기준2"}))
    assert table.rows == () and table.table.isHidden()
    assert table.unknown_rows == ((value.display_name, unknown.message),)
    assert table.unknown_notice.isVisible()


def test_unlinked_reference_remains_unknown_even_when_observed_release_is_known(own):
    table = own(RiskCriteriaTable((finding(rule()),), {(10, 11): "관측 버전", (20, 22): "다른 계열"}))
    assert table.rows[0][1] == "미확인"


@pytest.mark.parametrize("kind,end,reference,caption", [
    (RuleType.SAFETY_REQUIRED, None, "별도 기준 버전 없음", "필수 Item 미적용"),
    (RuleType.DEFECT_ITEM, 23, "시작 시작 원문 (포함)\n해제 해제 원문 (제외)", "버전 범위 내 결함 Item 포함"),
    (RuleType.DEFECT_ITEM, None, "시작 시작 원문 (포함)\n해제 버전 없음", "버전 범위 내 결함 Item 포함"),
])
def test_legacy_rule_reference_preserves_safety_and_start_end_semantics(own, kind, end, reference, caption):
    value = rule(kind, item_id=5, start_release_version_id=22, end_release_version_id=end, memo="")
    table = own(RiskCriteriaTable((finding(value),), {(10, 22): "시작 원문", (10, 23): "해제 원문"}))
    assert table.rows == ((value.display_name, reference, "—", caption),)


@pytest.fixture
def referenced_source(source):
    original = source.families[1001]
    source.families[1001] = FamilyReleases(1001, (*original.releases, ReleaseVersion(10013, 1001, "목표 N v3", 3)))
    source.targets[101, 1001] = 10013
    selected = RiskRule(7, RuleType.VERSION_BELOW, 1001, "선택 기준", release_version_id=10012,
                        mapping_status=MappingStatus.VERIFIED, memo="기준 버전 확인")
    assessor = RiskAssessor(source.families, ItemCatalog(source.families, ()), (selected,))
    source.assessor = assessor
    source.risk_evaluator = lambda record: assessor.equipment_risk(record).status
    return source


def test_equipment_details_share_frozen_reference_map_and_keep_goal_and_observation_distinct(own, referenced_source):
    source = referenced_source
    query = QueryState(source.catalog).apply()
    data = equipment_data(source.snapshot(query), source)
    first = next(detail for detail in data.details if detail.record.equipment_id == 1)
    assert all(detail.risk_reference_versions is first.risk_reference_versions for detail in data.details)
    assert first.record.pms[0].version_raw == "v99"
    assert first.status.families[0].pms[0].comparison.target.version_raw == "목표 N v3"
    assert first.whole.findings[0].release.version_raw == "v99"
    table = own(RiskCriteriaTable(first.whole.findings, first.risk_reference_versions))
    assert table.rows == (("선택 기준", "v2", "기준 버전 확인", "버전 미만"),)
    family = source.families[1001]
    source.families[1001] = FamilyReleases(1001, tuple(
        replace(release, version_raw="나중에 바뀐 원문") if release.release_version_id == 10012 else release
        for release in family.releases))
    assert first.risk_reference_versions[1001, 10012] == "v2"


def test_legacy_four_argument_detail_constructor_keeps_independent_empty_lookup(referenced_source):
    source = referenced_source
    value = equipment_data(source.snapshot(QueryState(source.catalog).apply()), source).details[0]
    first = EquipmentDetail(value.selected, value.whole, value.record, value.status)
    second = EquipmentDetail(value.selected, value.whole, value.record, value.status)
    first.risk_reference_versions[10, 22] = "한 상세의 값"
    assert second.risk_reference_versions == {}


def test_replay_reference_lookup_uses_frozen_source_after_live_catalog_changes(own, referenced_source):
    source = referenced_source
    query = QueryState(source.catalog).apply()
    replay = load_replay(source, ReplayRequest(DAY, DAY, query))
    source.families[1001] = FamilyReleases(1001, tuple(
        replace(release, version_raw="live 변경 원문") if release.release_version_id == 10012 else release
        for release in source.families[1001].releases))
    detail, rows, _ = replay_detail(replay, replay.frames[0], 1)
    assert detail.risk_reference_versions[1001, 10012] == "v2"
    assert rows[0][2] == "v99" and rows[0][3] == "목표 N v3"
    table = own(RiskCriteriaTable(detail.whole.findings, detail.risk_reference_versions))
    assert table.rows[0][1] == "v2"


def test_hpa_does_not_display_risk_rows_even_with_unexpected_findings(own, source):
    data = equipment_data(source.snapshot(QueryState(source.catalog).apply()), source)
    excluded = next(detail for detail in data.details if detail.record.is_hpa)
    detail = replace(excluded, whole=replace(excluded.whole, risk=RiskStatus.RISK,
                                             findings=(finding(rule()),)))
    host = own(QWidget())
    host.resize(960, 640)
    drawer = EquipmentDrawer(host)
    drawer.open_detail(None, detail, ())
    assert drawer.risk_table is None
    assert "분석에서 제외" in drawer.risk_reason.text()
    assert drawer.risk_status_badge.tone == "neutral"
    drawer.close_panel(restore_focus=False)
