"""설비 수·업그레이드 필요 수·N 이상 적용률 공통 집계를 검증한다 (BE-012 완료 조건)."""

from datetime import date

import pytest

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aggregation.service import summarize, summarize_by_date
from setron.features.version.schemas import CompareReason, CompareStatus, ReleaseVersion
from setron.features.version.service import FamilyReleases

DAY1 = date(2026, 10, 1)
DAY2 = date(2026, 10, 15)

# 모델(group_id)과 계열(family_id). 숫자는 테스트용이다.
M_O2, M_O3, M_C4, M_C4_PLUS, M_HPA = 1, 2, 3, 4, 9
F_O2, F_O3, F_C4 = 10, 20, 30

FAMILIES = {
    F_O2: FamilyReleases(F_O2, [ReleaseVersion(101, F_O2, "v2.1", 1), ReleaseVersion(102, F_O2, "v2.2", 2)]),
    F_O3: FamilyReleases(F_O3, [ReleaseVersion(201, F_O3, "v3.1", 1), ReleaseVersion(202, F_O3, "v3.2", 2)]),
    F_C4: FamilyReleases(F_C4, [
        ReleaseVersion(301, F_C4, "v4.56.6.42", 1),
        ReleaseVersion(302, F_C4, "v4.56.6.43", 2),
        ReleaseVersion(303, F_C4, "v4.56.6.44", 3),
    ]),
}
# 모델·계열별 목표 N. 같은 C4 계열이어도 원형 C4와 C4 Plus의 N을 따로 둔다.
TARGETS = {
    (M_O2, F_O2): 102,
    (M_O3, F_O3): 202,
    (M_C4, F_C4): 302,
    (M_C4_PLUS, F_C4): 303,
}


def pm(name, group_id, family_id, version):
    return PmVersion(name, group_id, family_id, version)


def equipment(eid, *pms, day=DAY1, line="P1D", hpa=False):
    return EquipmentRecord(eid, f"EQ{eid}", day, "화성", line, "model", tuple(pms), is_hpa=hpa)


def mixed_o2_below_o3_met(eid=1, day=DAY1):
    """명세 예시: PM1·2=O3(N 이상), PM3·4=O2(N 미만)."""
    return equipment(
        eid,
        pm("PM1", M_O3, F_O3, "v3.2"),
        pm("PM2", M_O3, F_O3, "v3.2"),
        pm("PM3", M_O2, F_O2, "v2.1"),
        pm("PM4", M_O2, F_O2, "v2.1"),
        day=day,
    )


def test_mixed_equipment_spec_example():
    """혼합 설비 O2 미달·O3 부합 → 실제 1대, 모델별 각 1대, 필요 1대(O2 1·O3 0), 적용률 50%."""
    result = summarize([mixed_o2_below_o3_met()], FAMILIES, TARGETS)

    assert result.equipment_count == 1
    assert result.upgrade_needed == 1
    models = {m.group_id: (m.equipment_count, m.upgrade_needed) for m in result.by_model}
    assert models == {M_O2: (1, 1), M_O3: (1, 0)}
    assert (result.compliant, result.below, result.undetermined) == (1, 1, 0)
    assert result.rate == 0.5


def test_same_family_pms_count_once_and_any_below_is_below():
    """같은 O2 계열의 PM1이 N 미만, PM2가 N 이상이면 O2 미달 1건이고 PM 버전 불일치로 표시한다."""
    record = equipment(1, pm("PM1", M_O2, F_O2, "v2.1"), pm("PM2", M_O2, F_O2, "v2.2"))

    result = summarize([record], FAMILIES, TARGETS)

    assert (result.compliant, result.below, result.undetermined) == (0, 1, 0)
    unit = result.equipment[0].families[0]
    assert unit.status is CompareStatus.BELOW
    assert unit.pm_mismatch
    assert [p.comparison.label for p in unit.pms] == ["N-1", "N 이상"]


def test_same_versions_on_all_pms_is_not_mismatch():
    result = summarize([mixed_o2_below_o3_met()], FAMILIES, TARGETS)

    assert not any(f.pm_mismatch for f in result.equipment[0].families)


@pytest.mark.parametrize(
    ("other_version", "expected"),
    [("v2.2", CompareStatus.UNDETERMINED), ("v2.1", CompareStatus.BELOW)],
    ids=["누락+N 이상 → 판단 불가", "누락+N 미만 → 미달"],
)
def test_missing_pm_version(other_version, expected):
    record = equipment(1, pm("PM1", M_O2, F_O2, None), pm("PM2", M_O2, F_O2, other_version))

    result = summarize([record], FAMILIES, TARGETS)

    assert result.equipment[0].families[0].status is expected


def test_undetermined_is_excluded_from_rate_and_blocks_complete():
    records = [
        equipment(1, pm(None, M_O2, F_O2, "v2.2")),
        equipment(2, pm(None, M_O2, F_O2, "v9.9")),  # 릴리즈 미등록 → 판단 불가
    ]

    result = summarize(records, FAMILIES, TARGETS)

    assert (result.compliant, result.below, result.undetermined) == (1, 0, 1)
    assert result.rate == 1.0
    assert not result.complete


def test_all_compliant_is_complete():
    result = summarize([equipment(1, pm(None, M_O2, F_O2, "v2.2"))], FAMILIES, TARGETS)

    assert result.rate == 1.0
    assert result.complete


def test_rate_is_none_when_nothing_is_determinable():
    result = summarize([equipment(1, pm(None, M_O2, F_O2, None))], FAMILIES, TARGETS)

    assert result.rate is None
    assert not result.complete


def test_hpa_is_excluded_from_all_counts():
    records = [
        equipment(1, pm(None, M_O2, F_O2, "v2.2")),
        equipment(2, pm(None, M_HPA, F_O2, "v2.1"), hpa=True),
    ]

    result = summarize(records, FAMILIES, TARGETS)

    assert result.equipment_count == 1
    assert result.upgrade_needed == 0
    assert result.hpa_excluded == 1
    assert {m.group_id for m in result.by_model} == {M_O2}
    assert (result.compliant, result.below) == (1, 0)


def test_equipment_below_in_several_families_is_needed_once():
    record = equipment(
        1, pm("PM1", M_O3, F_O3, "v3.1"), pm("PM2", M_O2, F_O2, "v2.1")
    )

    result = summarize([record], FAMILIES, TARGETS)

    assert result.upgrade_needed == 1
    assert result.below == 2


def test_each_model_uses_its_own_target_in_shared_family():
    """같은 C4 계열·같은 버전이어도 원형 C4(N=43)는 부합, C4 Plus(N=44)는 미달이다."""
    records = [
        equipment(1, pm(None, M_C4, F_C4, "v4.56.6.43")),
        equipment(2, pm(None, M_C4_PLUS, F_C4, "v4.56.6.43")),
    ]

    result = summarize(records, FAMILIES, TARGETS)

    statuses = [e.families[0].status for e in result.equipment]
    assert statuses == [CompareStatus.AT_OR_ABOVE, CompareStatus.BELOW]
    assert result.rate == 0.5


def test_missing_target_for_model_family_is_undetermined():
    result = summarize([equipment(1, pm(None, M_O2, F_O3, "v3.2"))], FAMILIES, TARGETS)

    comparison = result.equipment[0].families[0].pms[0].comparison
    assert comparison.status is CompareStatus.UNDETERMINED
    assert comparison.reason is CompareReason.TARGET_NOT_SET
    assert comparison.message == "이 계열의 목표 N이 지정되지 않았습니다."


def test_family_without_release_data_is_undetermined_with_its_own_reason():
    families = {**FAMILIES, 99: FamilyReleases(99, [])}

    result = summarize([equipment(1, pm(None, M_O2, 99, "v1")), equipment(2, pm(None, M_O2, 98, "v1"))],
                       families, {(M_O2, 99): 1})

    reasons = [e.families[0].pms[0].comparison.reason for e in result.equipment]
    assert reasons == [CompareReason.NO_RELEASE_DATA, CompareReason.NO_RELEASE_DATA]


def test_no_records_reports_no_data():
    result = summarize([], FAMILIES, TARGETS)

    assert not result.has_data
    assert result.equipment_count == 0
    assert result.rate is None


def test_by_family_counts_match_bar_and_line_values():
    """날짜별 추이: 막대(N 이상 대수)와 꺾은선(적용률)이 같은 계열 건수에서 나온다."""
    records = [
        mixed_o2_below_o3_met(1),
        equipment(2, pm(None, M_O2, F_O2, "v2.2")),
        equipment(3, pm(None, M_O3, F_O3, "v3.2")),
    ]

    result = summarize(records, FAMILIES, TARGETS)

    by_family = {f.family_id: (f.compliant, f.rate) for f in result.by_family}
    assert by_family == {F_O2: (1, 0.5), F_O3: (2, 1.0)}


def test_summarize_by_date_uses_only_records_of_each_date():
    records = [
        mixed_o2_below_o3_met(1, day=DAY1),
        equipment(2, pm(None, M_O2, F_O2, "v2.2"), day=DAY1),
        equipment(2, pm(None, M_O2, F_O2, "v2.2"), day=DAY2),
    ]

    result = summarize_by_date(records, FAMILIES, TARGETS)

    assert [s.base_date for s in result] == [DAY1, DAY2]
    assert [s.equipment_count for s in result] == [2, 1]
    assert result[1].upgrade_needed == 0


def test_summarize_accepts_latest_observation_per_equipment_across_dates():
    """QA Q-01: 설비별 최신 관측은 날짜가 섞일 수 있다. 집계 기준일은 가장 늦은 관측일."""
    result = summarize([mixed_o2_below_o3_met(1, DAY1), mixed_o2_below_o3_met(2, DAY2)], FAMILIES, TARGETS)

    assert result.equipment_count == 2 and result.base_date == max(DAY1, DAY2)


def test_summarize_rejects_same_equipment_from_two_dates():
    with pytest.raises(ValueError, match="두 번"):
        summarize([mixed_o2_below_o3_met(1, DAY1), mixed_o2_below_o3_met(1, DAY2)], FAMILIES, TARGETS)


def test_summarize_rejects_duplicated_equipment():
    with pytest.raises(ValueError, match="두 번"):
        summarize([mixed_o2_below_o3_met(1), mixed_o2_below_o3_met(1)], FAMILIES, TARGETS)
