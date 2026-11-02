"""업그레이드 우선순위를 검증한다 (BE-017 완료 조건)."""

from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aging.schemas import DEFAULT_THRESHOLDS
from setron.features.item.service import ItemCatalog
from setron.features.priority.schemas import PriorityGap
from setron.features.priority.service import prioritize
from setron.features.risk.schemas import MappingStatus, RiskRule, RiskStatus, RuleType
from setron.features.risk.service import RiskAssessor
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

M_C4, M_O2, M_O3 = 3, 1, 2
F_C4, F_O2, F_O3 = 30, 10, 20
V1, V2, V3, V4, O2_V1, O3_V1 = 301, 302, 303, 304, 101, 201
BASE = date(2026, 10, 1)

FAMILIES = {
    F_C4: FamilyReleases(F_C4, [
        ReleaseVersion(V1, F_C4, "v1", 1, date(2023, 10, 1)),  # 3년
        ReleaseVersion(V2, F_C4, "v2", 2, date(2024, 10, 1)),  # 2년
        ReleaseVersion(V3, F_C4, "v3", 3, date(2025, 10, 1)),  # 1년
        ReleaseVersion(V4, F_C4, "v4", 4, None),               # 출시일 없음
    ]),
    F_O2: FamilyReleases(F_O2, [ReleaseVersion(O2_V1, F_O2, "o2-v1", 1, date(2023, 10, 1))]),
    F_O3: FamilyReleases(F_O3, [ReleaseVersion(O3_V1, F_O3, "o3-v1", 1, date(2026, 4, 1))]),
}


def direct(rule_id, release_id, family_id=F_C4, mapping=MappingStatus.VERIFIED):
    return RiskRule(rule_id, RuleType.DIRECT_VERSION, family_id, f"사유 {rule_id}",
                    release_version_id=release_id, mapping_status=mapping)


DIRECT_V2 = direct(1, V2)
DIRECT_V4 = direct(2, V4)


def assessor(*rules):
    return RiskAssessor(FAMILIES, ItemCatalog(FAMILIES, []), rules)


def c4(version):
    return PmVersion(None, M_C4, F_C4, version)


def equipment(eid, *pms, hpa=False):
    return EquipmentRecord(eid, f"EQ{eid:02d}", BASE, "화성", "16L", "M-C4", tuple(pms), is_hpa=hpa)


def codes(entries):
    return [e.record.equipment_code for e in entries]


def test_risk_equipment_comes_first_then_longer_elapsed_period():
    records = [equipment(1, c4("v3")), equipment(2, c4("v1")), equipment(3, c4("v2"))]

    result = prioritize(records, assessor(DIRECT_V2), FAMILIES, DEFAULT_THRESHOLDS)

    assert codes(result.ranked) == ["EQ03", "EQ02", "EQ01"]  # 위험 2년 → 위험 없음 3년 → 1년
    assert [e.rank for e in result.ranked] == [1, 2, 3]
    assert result.ranked[0].risk.reasons and not result.needs_check


def test_older_risk_equipment_goes_before_newer_risk_equipment():
    older_risk = direct(3, V1)
    records = [equipment(1, c4("v2")), equipment(2, c4("v1"))]

    result = prioritize(records, assessor(DIRECT_V2, older_risk), FAMILIES, DEFAULT_THRESHOLDS)

    assert codes(result.ranked) == ["EQ02", "EQ01"]


def test_mixed_equipment_uses_oldest_family_and_keeps_each_period():
    """명세 예: O2 3년·O3 6개월이면 대표값은 3년이다."""
    mixed = equipment(1, PmVersion("PM1", M_O3, F_O3, "o3-v1"), PmVersion("PM3", M_O2, F_O2, "o2-v1"))
    two_years = equipment(2, c4("v2"))

    result = prioritize([two_years, mixed], assessor(), FAMILIES, DEFAULT_THRESHOLDS)

    assert codes(result.ranked) == ["EQ01", "EQ02"]
    first = result.ranked[0]
    assert first.representative.pm.pm_name == "PM3"
    assert [p.age.elapsed_label for p in first.age.pms] == ["6개월", "3년"]
    assert not first.partial_age


def test_confirmed_risk_without_release_date_needs_check_and_keeps_reason():
    result = prioritize([equipment(1, c4("v4")), equipment(2, c4("v3"))], assessor(DIRECT_V4),
                        FAMILIES, DEFAULT_THRESHOLDS)

    assert codes(result.ranked) == ["EQ02"]
    entry = result.needs_check[0]
    assert entry.rank is None and entry.gaps == (PriorityGap.AGE_UNKNOWN,)
    assert entry.is_risk and entry.risk.reasons[0].rule.risk_reason == "사유 2"


def test_unknown_risk_or_missing_version_needs_check_without_rank():
    unresolved_o2 = direct(4, O2_V1, family_id=F_O2, mapping=MappingStatus.UNRESOLVED)
    records = [
        equipment(1, PmVersion(None, M_O2, F_O2, "o2-v1")),  # 위험 미확인, 기간은 있음
        equipment(2, c4(None)),                              # 버전 없음
        equipment(3, c4("v4")),                              # 위험 있음, 출시일 없음
    ]

    result = prioritize(records, assessor(DIRECT_V4, unresolved_o2), FAMILIES, DEFAULT_THRESHOLDS)

    assert result.ranked == ()
    assert codes(result.needs_check) == ["EQ03", "EQ01", "EQ02"]  # 위험 확인 설비를 위에 두고 순위는 없음
    assert result.needs_check[1].gaps == (PriorityGap.RISK_UNKNOWN,)
    assert PriorityGap.AGE_UNKNOWN in result.needs_check[2].gaps
    assert all(e.rank is None and e.messages for e in result.needs_check)


def test_mixed_equipment_with_unknown_period_is_ranked_with_partial_flag():
    record = equipment(1, PmVersion(None, M_O2, F_O2, "o2-v1"), c4("v4"))

    result = prioritize([record], assessor(), FAMILIES, DEFAULT_THRESHOLDS)

    assert result.ranked[0].partial_age
    assert result.ranked[0].representative.pm.family_id == F_O2


def test_hpa_is_excluded_and_counted():
    result = prioritize([equipment(1, c4("v1")), equipment(2, c4("v1"), hpa=True)], assessor(DIRECT_V2),
                        FAMILIES, DEFAULT_THRESHOLDS)

    assert codes(result.ranked) == ["EQ01"]
    assert result.hpa_excluded == 1


def test_without_rules_sorts_by_age_and_reports_missing_rules():
    result = prioritize([equipment(1, c4("v3")), equipment(2, c4("v1"))], assessor(), FAMILIES, DEFAULT_THRESHOLDS)

    assert codes(result.ranked) == ["EQ02", "EQ01"]
    assert all(e.risk.status is RiskStatus.NO_RISK for e in result.ranked)
    assert not result.has_rules


def test_invalid_threshold_setting_still_sorts_by_elapsed_period():
    result = prioritize([equipment(1, c4("v3")), equipment(2, c4("v1"))], assessor(), FAMILIES, None)

    assert codes(result.ranked) == ["EQ02", "EQ01"]


def test_same_risk_and_period_falls_back_to_equipment_code():
    result = prioritize([equipment(2, c4("v1")), equipment(1, c4("v1"))], assessor(), FAMILIES, DEFAULT_THRESHOLDS)

    assert codes(result.ranked) == ["EQ01", "EQ02"]


def test_release_after_query_date_needs_check():
    result = prioritize([equipment(1, c4("v3"))], assessor(), FAMILIES, DEFAULT_THRESHOLDS,
                        base_date=date(2025, 1, 1))

    assert result.needs_check[0].gaps == (PriorityGap.AGE_UNKNOWN,)
