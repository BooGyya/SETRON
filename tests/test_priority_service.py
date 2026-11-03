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


TARGETS = {(M_C4, F_C4): V3, (M_O2, F_O2): O2_V1, (M_O3, F_O3): O3_V1}  # C4 기준 N = v3


def test_risk_equipment_comes_first_then_most_behind_target():
    """멘토링 3차: 위험 → 버전이 가장 낮은(N에서 가장 많이 뒤처진) 설비 → 나머지 낮은 순."""
    records = [equipment(1, c4("v3")), equipment(2, c4("v1")), equipment(3, c4("v2"))]

    result = prioritize(records, assessor(DIRECT_V2), FAMILIES, DEFAULT_THRESHOLDS, targets=TARGETS)

    assert codes(result.ranked) == ["EQ03", "EQ02", "EQ01"]  # 위험(N-1) → N-2 → N
    assert [e.steps_behind for e in result.ranked] == [1, 2, 0]
    assert [e.rank for e in result.ranked] == [1, 2, 3]
    assert result.ranked[0].risk.reasons and not result.needs_check


def test_more_behind_risk_equipment_goes_first_among_risks():
    records = [equipment(1, c4("v2")), equipment(2, c4("v1"))]

    result = prioritize(records, assessor(DIRECT_V2, direct(3, V1)), FAMILIES, DEFAULT_THRESHOLDS, targets=TARGETS)

    assert codes(result.ranked) == ["EQ02", "EQ01"]


def test_above_target_is_after_target():
    """N 위(음수 칸)는 N보다 뒤에 둔다."""
    records = [equipment(1, c4("v4")), equipment(2, c4("v3"))]

    result = prioritize(records, assessor(), FAMILIES, DEFAULT_THRESHOLDS, targets=TARGETS)

    assert codes(result.ranked) == ["EQ02", "EQ01"]
    assert [e.steps_behind for e in result.ranked] == [0, -1]


def test_without_target_falls_back_to_version_position():
    records = [equipment(1, c4("v3")), equipment(2, c4("v1")), equipment(3, c4("v2"))]

    result = prioritize(records, assessor(), FAMILIES, DEFAULT_THRESHOLDS)

    assert codes(result.ranked) == ["EQ02", "EQ03", "EQ01"]
    assert all(e.steps_behind is None for e in result.ranked)


def test_same_steps_and_position_are_ordered_by_older_release_date():
    """O2 v1(2023 출시)과 O3 v1(2026 출시)은 둘 다 N이고 첫 위치 → 출시일이 오래된 O2가 먼저."""
    records = [equipment(1, PmVersion(None, M_O3, F_O3, "o3-v1")), equipment(2, PmVersion(None, M_O2, F_O2, "o2-v1"))]

    result = prioritize(records, assessor(), FAMILIES, DEFAULT_THRESHOLDS, targets=TARGETS)

    assert codes(result.ranked) == ["EQ02", "EQ01"]


def test_mixed_equipment_uses_most_behind_pm_and_keeps_age_for_display():
    mixed = equipment(1, PmVersion("PM1", M_O3, F_O3, "o3-v1"), PmVersion("PM3", M_C4, F_C4, "v1"))
    one_behind = equipment(2, c4("v2"))

    result = prioritize([one_behind, mixed], assessor(), FAMILIES, DEFAULT_THRESHOLDS, targets=TARGETS)

    assert codes(result.ranked) == ["EQ01", "EQ02"]
    first = result.ranked[0]
    assert first.steps_behind == 2 and first.lowest.pm.pm_name in {"PM1", "PM3"}
    assert first.representative.pm.pm_name == "PM3"  # 노후도는 순위와 별도로 보여 준다
    assert [p.age.elapsed_label for p in first.age.pms] == ["6개월", "3년"]


def test_missing_release_date_is_still_ranked():
    """출시일은 마지막 정렬 기준이라 없어도 순위를 매긴다 (이전에는 확인 필요)."""
    result = prioritize([equipment(1, c4("v4")), equipment(2, c4("v3"))], assessor(DIRECT_V4),
                        FAMILIES, DEFAULT_THRESHOLDS, targets=TARGETS)

    assert codes(result.ranked) == ["EQ01", "EQ02"]
    assert result.ranked[0].is_risk and result.ranked[0].lowest.release.released_at is None
    assert not result.needs_check


def test_unknown_risk_or_unknown_version_position_needs_check_without_rank():
    unresolved_o2 = direct(4, O2_V1, family_id=F_O2, mapping=MappingStatus.UNRESOLVED)
    records = [
        equipment(1, PmVersion(None, M_O2, F_O2, "o2-v1")),  # 위험 미확인
        equipment(2, c4(None)),                              # 버전 없음
        equipment(3, c4("v9")),                              # 미등록 버전
    ]

    result = prioritize(records, assessor(DIRECT_V4, unresolved_o2), FAMILIES, DEFAULT_THRESHOLDS, targets=TARGETS)

    assert result.ranked == ()
    assert codes(result.needs_check) == ["EQ01", "EQ02", "EQ03"]
    assert result.needs_check[0].gaps == (PriorityGap.RISK_UNKNOWN,)
    assert PriorityGap.VERSION_UNKNOWN in result.needs_check[1].gaps
    assert PriorityGap.VERSION_UNKNOWN in result.needs_check[2].gaps
    assert all(e.rank is None and e.messages for e in result.needs_check)


def test_unordered_release_has_no_position():
    families = {F_C4: FamilyReleases(F_C4, [ReleaseVersion(V1, F_C4, "v1", None, date(2023, 10, 1))])}
    catalog = RiskAssessor(families, ItemCatalog(families, []), [])

    result = prioritize([equipment(1, c4("v1"))], catalog, families, DEFAULT_THRESHOLDS)

    assert result.needs_check[0].gaps == (PriorityGap.VERSION_UNKNOWN,)


def test_hpa_is_excluded_and_counted():
    result = prioritize([equipment(1, c4("v1")), equipment(2, c4("v1"), hpa=True)], assessor(DIRECT_V2),
                        FAMILIES, DEFAULT_THRESHOLDS, targets=TARGETS)

    assert codes(result.ranked) == ["EQ01"]
    assert result.hpa_excluded == 1


def test_without_rules_sorts_by_version_and_reports_missing_rules():
    result = prioritize([equipment(1, c4("v3")), equipment(2, c4("v1"))], assessor(), FAMILIES, DEFAULT_THRESHOLDS,
                        targets=TARGETS)

    assert codes(result.ranked) == ["EQ02", "EQ01"]
    assert all(e.risk.status is RiskStatus.NO_RISK for e in result.ranked)
    assert not result.has_rules
