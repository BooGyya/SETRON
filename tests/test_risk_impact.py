"""위험 규칙 저장 전 영향 미리보기를 검증한다 (BE-015, 화면 17·19·50 "영향 미리보기")."""

from dataclasses import replace
from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord
from setron.features.item.service import ItemCatalog
from setron.features.management import ManagementService
from setron.features.risk.impact import DRAFT_RULE_ID, preview_rule_impact
from setron.features.risk.schemas import FindingBasis, MappingStatus, RiskStatus, RuleType
from test_management_service import observe
from test_release_revision import all_rows, release, revision_storage  # noqa: F401
from test_risk_service import (DIRECT_V2, F_C4, ITEM_DEFECT, ROWS, V1, V2, V3, make_families, pm, rule)

DAY = date(2026, 10, 21)
DEFECT_V1_TO_V3 = rule(7, RuleType.DEFECT_ITEM, item_id=ITEM_DEFECT,
                       start_release_version_id=V1, end_release_version_id=V3)


def record(eid, version, site="화성", hpa=False):
    return EquipmentRecord(eid, f"EQ-{eid:02d}", DAY, site, "16L", "M-C4", (pm(version),), is_hpa=hpa)


def preview(draft, rules=(), records=(), **kwargs):
    families = make_families()
    return preview_rule_impact(draft, families, ItemCatalog(families, ROWS), rules, records, **kwargs)


def test_defect_rule_lists_releases_in_range_and_marks_equipment_without_saving():
    """시작 버전은 포함하고 해제 버전부터 뺀다. 범위 밖 설비는 영향 목록에 넣지 않는다."""
    result = preview(DEFECT_V1_TO_V3, records=(record(1, "v2"), record(2, "v4"), record(3, None)), base_date=DAY)

    assert result.saveable and result.rule.risk_rule_id == DRAFT_RULE_ID and result.base_date == DAY
    assert [(r.release.version_raw, r.status) for r in result.releases] == [
        ("v1", RiskStatus.RISK), ("v2", RiskStatus.RISK)]
    impacts = {e.record.equipment_code: e for e in result.equipment}
    assert set(impacts) == {"EQ-01", "EQ-03"}
    assert impacts["EQ-01"].status is RiskStatus.RISK
    assert (impacts["EQ-01"].before, impacts["EQ-01"].after) == (RiskStatus.NO_RISK, RiskStatus.RISK)
    assert impacts["EQ-03"].status is RiskStatus.UNKNOWN
    assert impacts["EQ-03"].findings[0].basis is FindingBasis.VERSION_UNKNOWN
    assert [e.record.equipment_code for e in result.equipment] == ["EQ-01", "EQ-03"]  # 위험이 먼저
    assert (result.count(RiskStatus.RISK), result.count(RiskStatus.UNKNOWN), result.newly_risky) == (1, 1, 1)


def test_equipment_already_risky_by_saved_rule_is_counted_separately():
    """이미 다른 규칙으로 위험인 설비는 새 위험으로 세지 않는다."""
    result = preview(DEFECT_V1_TO_V3, rules=(DIRECT_V2,), records=(record(1, "v2"), record(2, "v1")))

    impacts = {e.record.equipment_code: e for e in result.equipment}
    assert impacts["EQ-01"].before is RiskStatus.RISK and not impacts["EQ-01"].changed
    assert impacts["EQ-02"].changed
    assert (result.newly_risky, result.already_risky) == (1, 1)


def test_hpa_is_excluded_and_sites_are_counted():
    result = preview(DEFECT_V1_TO_V3, records=(
        record(1, "v1", site="평택"), record(2, "v2"), record(3, "v1"), record(4, "v1", hpa=True)))

    assert result.hpa_excluded == 1 and len(result.equipment) == 3
    assert {site: dict(counts) for site, counts in result.by_site().items()} == {
        "평택": {RiskStatus.RISK: 1}, "화성": {RiskStatus.RISK: 2}}
    assert result.count(RiskStatus.RISK, site="화성") == 2


def test_invalid_draft_reports_problems_and_skips_impact():
    backwards = replace(DEFECT_V1_TO_V3, start_release_version_id=V3, end_release_version_id=V1)

    result = preview(backwards, records=(record(1, "v2"),), problems=("DB 연결 확인 필요", "DB 연결 확인 필요"))

    assert not result.saveable
    assert result.problems == ("DB 연결 확인 필요", "해제 버전은 시작 버전보다 뒤의 버전이어야 합니다.")
    assert result.releases == () and result.equipment == ()


def test_disabled_draft_has_no_impact():
    result = preview(replace(DIRECT_V2, is_enabled=False), records=(record(1, "v2"),))

    assert result.saveable and result.releases == () and result.equipment == ()


def test_unresolved_mapping_is_flagged_and_marks_family_unknown():
    result = preview(rule(8, RuleType.DIRECT_VERSION, release_version_id=V2, mapping_status=MappingStatus.UNRESOLVED),
                     records=(record(1, "v1"),))

    assert result.mapping_unresolved
    assert result.equipment[0].status is RiskStatus.UNKNOWN
    assert all(r.status is RiskStatus.UNKNOWN and r.release.family_id == F_C4 for r in result.releases)


def test_management_preview_reads_latest_observation_and_writes_nothing(revision_storage):  # noqa: F811
    storage = revision_storage
    v1 = release(storage, raw="v1", order=1)
    observe(storage, v1, day=date(2026, 9, 1), code="EQ-OLD")
    snapshot = observe(storage, v1, day=date(2026, 10, 1), code="EQ-NEW")
    before = {name: all_rows(storage, name) for name in ("risk_rule", "import_batch", "equipment_version")}

    result = ManagementService(storage).preview_risk_rule({
        "rule_type": "DIRECT_VERSION", "family_id": 1, "release_version_id": v1,
        "risk_reason": "미리보기", "mapping_status": "VERIFIED"})

    assert result.saveable and result.base_date == date(2026, 10, 1)
    assert [e.record.equipment_id for e in result.equipment] == [snapshot.equipment_id]
    assert result.equipment[0].status is RiskStatus.RISK and result.newly_risky == 1
    assert [r.release.release_version_id for r in result.releases] == [v1]
    assert {name: all_rows(storage, name) for name in before} == before


def test_management_preview_reports_wrong_family_release_without_saving(revision_storage):  # noqa: F811
    storage = revision_storage
    other = release(storage, raw="c1", order=1, family=2)

    result = ManagementService(storage).preview_risk_rule({
        "rule_type": "DIRECT_VERSION", "family_id": 1, "release_version_id": other, "risk_reason": "다른 계열"})

    assert not result.saveable and any("같은 계열" in p for p in result.problems)
    assert all_rows(storage, "risk_rule") == []
