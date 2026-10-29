"""위험 설비 판정을 검증한다 (BE-015 완료 조건)."""

from datetime import date

import pytest

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.item.schemas import ItemState, TargetScope, VersionItem
from setron.features.item.service import ItemCatalog
from setron.features.risk.schemas import (
    FindingBasis,
    MappingStatus,
    RiskRule,
    RiskStatus,
    RuleType,
)
from setron.features.risk.service import RiskAssessor, validate_rule
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

M_C4, M_C4_PLUS, M_O2 = 3, 4, 1
F_C4, F_O2 = 30, 10
V1, V2, V3, V4, V_HOTFIX = 301, 302, 303, 304, 305
O2_V1 = 101

ITEM_DEFECT, ITEM_SAFETY, ITEM_PLUS_SAFETY, ITEM_SCOPE_UNKNOWN, ITEM_O2 = 1, 2, 3, 4, 5


def make_families(*extra_c4):
    return {
        F_C4: FamilyReleases(F_C4, [
            ReleaseVersion(V1, F_C4, "v1", 1),
            ReleaseVersion(V2, F_C4, "v2", 2),
            ReleaseVersion(V3, F_C4, "v3", 3),
            ReleaseVersion(V4, F_C4, "v4", 4),
            *extra_c4,
        ]),
        F_O2: FamilyReleases(F_O2, [ReleaseVersion(O2_V1, F_O2, "o2-v1", 1)]),
    }


ROWS = [
    VersionItem(V1, ITEM_DEFECT, ItemState.APPLIED, TargetScope.ALL),        # 결함 Item: v1부터 포함
    VersionItem(V3, ITEM_SAFETY, ItemState.APPLIED, TargetScope.ALL),        # 안전 Item: v3에서 추가
    VersionItem(V3, ITEM_PLUS_SAFETY, ItemState.APPLIED, TargetScope.LISTED, (M_C4_PLUS,)),
    VersionItem(V3, ITEM_SCOPE_UNKNOWN, ItemState.APPLIED, TargetScope.UNKNOWN),
    VersionItem(O2_V1, ITEM_O2, ItemState.APPLIED, TargetScope.ALL),
]


def rule(rule_id, rule_type, **kwargs):
    kwargs.setdefault("family_id", F_C4)
    kwargs.setdefault("mapping_status", MappingStatus.VERIFIED)
    return RiskRule(rule_id, rule_type, risk_reason=f"사유 {rule_id}", **kwargs)


DEFECT_V1_TO_V3 = rule(1, RuleType.DEFECT_ITEM, item_id=ITEM_DEFECT,
                       start_release_version_id=V1, end_release_version_id=V3)
SAFETY = rule(2, RuleType.SAFETY_REQUIRED, item_id=ITEM_SAFETY)
DIRECT_V2 = rule(3, RuleType.DIRECT_VERSION, release_version_id=V2)


def assessor(*rules, families=None):
    families = families or make_families()
    return RiskAssessor(families, ItemCatalog(families, ROWS), rules)


def pm(version, group_id=M_C4, family_id=F_C4, name=None):
    return PmVersion(name, group_id, family_id, version)


def equipment(eid, *pms, site="화성", hpa=False):
    return EquipmentRecord(eid, f"EQ{eid}", date(2026, 10, 1), site, "P1D", "M-C4", tuple(pms), is_hpa=hpa)


def statuses(findings):
    return [(f.status, f.basis) for f in findings]


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("v1", [(RiskStatus.RISK, FindingBasis.DEFECT_IN_RANGE)]),
        ("v2", [(RiskStatus.RISK, FindingBasis.DEFECT_IN_RANGE)]),
        ("v3", []),
        ("v4", []),
    ],
    ids=["시작 버전 v1 포함", "v2 위험", "해제 버전 v3부터 제외", "v4 제외"],
)
def test_defect_risk_includes_start_and_excludes_fixed_version(version, expected):
    """명세 예시: v1에서 발생해 v3에서 수정된 결함은 v1·v2만 위험이다."""
    assert statuses(assessor(DEFECT_V1_TO_V3).pm_findings(pm(version))) == expected


def test_defect_without_end_version_stays_risky():
    open_defect = rule(1, RuleType.DEFECT_ITEM, item_id=ITEM_DEFECT, start_release_version_id=V1)

    assert statuses(assessor(open_defect).pm_findings(pm("v4"))) == [(RiskStatus.RISK, FindingBasis.DEFECT_IN_RANGE)]


def test_defect_without_start_version_is_unknown_inside_and_clear_after_end():
    no_start = rule(1, RuleType.DEFECT_ITEM, item_id=ITEM_DEFECT, end_release_version_id=V3)
    risk = assessor(no_start)

    assert statuses(risk.pm_findings(pm("v2"))) == [(RiskStatus.UNKNOWN, FindingBasis.DEFECT_RANGE_UNKNOWN)]
    assert risk.pm_findings(pm("v3")) == ()


def test_defect_is_not_risk_when_item_not_yet_included():
    """범위가 v1부터여도 Item이 v3에서야 추가됐다면 v1·v2에는 결함 Item이 없다."""
    defect = rule(1, RuleType.DEFECT_ITEM, item_id=ITEM_SAFETY, start_release_version_id=V1)

    assert assessor(defect).pm_findings(pm("v2")) == ()
    assert statuses(assessor(defect).pm_findings(pm("v3"))) == [(RiskStatus.RISK, FindingBasis.DEFECT_IN_RANGE)]


def test_defect_with_unknown_version_order_is_unknown():
    families = make_families(ReleaseVersion(V_HOTFIX, F_C4, "v2a1", None))

    result = assessor(DEFECT_V1_TO_V3, families=families).pm_findings(pm("v2a1"))

    assert statuses(result) == [(RiskStatus.UNKNOWN, FindingBasis.DEFECT_RANGE_UNKNOWN)]


@pytest.mark.parametrize(
    ("version", "expected"),
    [("v2", [(RiskStatus.RISK, FindingBasis.SAFETY_NOT_APPLIED)]), ("v3", []), ("v4", [])],
    ids=["안전 Item 추가 전 구버전 → 위험", "추가된 버전", "후속 버전 누적 포함"],
)
def test_safety_item_not_applied_is_risk(version, expected):
    assert statuses(assessor(SAFETY).pm_findings(pm(version))) == expected


@pytest.mark.parametrize(
    ("safety_rule", "pm_args"),
    [
        (rule(2, RuleType.SAFETY_REQUIRED, item_id=ITEM_SCOPE_UNKNOWN), ("v2",)),
        (rule(2, RuleType.SAFETY_REQUIRED, item_id=ITEM_O2), ("v2",)),
        (SAFETY, ("v9",)),
        (SAFETY, (None,)),
    ],
    ids=["적용 모델 범위 미확인", "이 계열 릴리즈에 없는 안전 Item", "릴리즈 미등록 버전", "버전 누락"],
)
def test_safety_without_enough_data_is_unknown_not_safe(safety_rule, pm_args):
    result = assessor(safety_rule).pm_findings(pm(*pm_args))

    assert [f.status for f in result] == [RiskStatus.UNKNOWN]


def test_safety_item_for_other_model_is_not_risk():
    plus_only = rule(2, RuleType.SAFETY_REQUIRED, item_id=ITEM_PLUS_SAFETY)

    assert assessor(plus_only).pm_findings(pm("v2", M_C4)) == ()
    assert [f.status for f in assessor(plus_only).pm_findings(pm("v2", M_C4_PLUS))] == [RiskStatus.RISK]


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("v2", [(RiskStatus.RISK, FindingBasis.DIRECT_VERSION)]),
        ("v3", []),
        ("v9", [(RiskStatus.UNKNOWN, FindingBasis.VERSION_UNKNOWN)]),
    ],
    ids=["지정 버전", "다른 버전", "미등록 버전"],
)
def test_direct_version(version, expected):
    assert statuses(assessor(DIRECT_V2).pm_findings(pm(version))) == expected


def test_rule_for_specific_model_does_not_apply_to_others():
    plus_direct = rule(3, RuleType.DIRECT_VERSION, release_version_id=V2, group_id=M_C4_PLUS)

    assert assessor(plus_direct).pm_findings(pm("v2", M_C4)) == ()
    assert len(assessor(plus_direct).pm_findings(pm("v2", M_C4_PLUS))) == 1


def test_rule_of_other_family_is_not_applied():
    assert assessor(DIRECT_V2).pm_findings(pm("o2-v1", M_O2, F_O2)) == ()


def test_disabled_rule_is_ignored():
    disabled = rule(3, RuleType.DIRECT_VERSION, release_version_id=V2, is_enabled=False)

    assert assessor(disabled).pm_findings(pm("v2")) == ()


def test_unresolved_rule_makes_family_unknown():
    unresolved = rule(3, RuleType.DIRECT_VERSION, release_version_id=V2, mapping_status=MappingStatus.UNRESOLVED)

    result = assessor(unresolved).pm_findings(pm("v4"))

    assert statuses(result) == [(RiskStatus.UNKNOWN, FindingBasis.MAPPING_UNRESOLVED)]


def test_equipment_with_several_reasons_is_one_risky_equipment():
    record = equipment(1, pm("v2", name="PM1"), pm("v2", name="PM2"))
    risk = assessor(DEFECT_V1_TO_V3, SAFETY, DIRECT_V2)

    result = risk.equipment_risk(record)
    summary = risk.summarize([record])

    assert result.status is RiskStatus.RISK
    assert len(result.reasons) == 6  # PM 2개 × 사유 3개
    assert summary.risky == 1


def test_risk_takes_precedence_over_unknown():
    record = equipment(1, pm("v2", name="PM1"), pm(None, name="PM2"))

    result = assessor(DIRECT_V2).equipment_risk(record)

    assert result.status is RiskStatus.RISK
    assert {f.status for f in result.findings} == {RiskStatus.RISK, RiskStatus.UNKNOWN}


def test_equipment_without_findings_is_no_risk():
    assert assessor(DIRECT_V2).equipment_risk(equipment(1, pm("v4"))).status is RiskStatus.NO_RISK


def test_summary_counts_by_site_and_excludes_hpa():
    records = [
        equipment(1, pm("v2"), site="화성"),
        equipment(2, pm("v4"), site="화성"),
        equipment(3, pm(None), site="평택"),
        equipment(4, pm("v2"), site="평택", hpa=True),
    ]

    summary = assessor(DIRECT_V2).summarize(records)

    assert summary.sites == ("평택", "화성")
    assert summary.counts("화성") == {RiskStatus.RISK: 1, RiskStatus.NO_RISK: 1, RiskStatus.UNKNOWN: 0}
    assert summary.counts("평택")[RiskStatus.UNKNOWN] == 1
    assert summary.hpa_excluded == 1
    assert summary.has_rules


def test_summary_without_rules_marks_missing_criteria():
    summary = assessor().summarize([equipment(1, pm("v2"))])

    assert summary.counts()[RiskStatus.NO_RISK] == 1
    assert not summary.has_rules


def test_release_findings_show_version_risks():
    risk = assessor(DEFECT_V1_TO_V3, SAFETY, DIRECT_V2)
    v2 = make_families()[F_C4].get(V2)
    v4 = make_families()[F_C4].get(V4)

    assert {f.basis for f in risk.release_findings(v2)} == {
        FindingBasis.DEFECT_IN_RANGE, FindingBasis.SAFETY_NOT_APPLIED, FindingBasis.DIRECT_VERSION,
    }
    assert risk.release_findings(v4) == ()


@pytest.mark.parametrize(
    ("bad_rule", "message"),
    [
        (rule(1, RuleType.DEFECT_ITEM), "대상 Item"),
        (rule(1, RuleType.DIRECT_VERSION), "버전을 선택"),
        (rule(1, RuleType.DIRECT_VERSION, release_version_id=O2_V1), "다른 계열"),
        (rule(1, RuleType.DEFECT_ITEM, item_id=ITEM_DEFECT,
              start_release_version_id=V3, end_release_version_id=V2), "해제 버전은 시작 버전보다"),
        (rule(1, RuleType.DIRECT_VERSION, family_id=999, release_version_id=V2), "릴리즈 정보가 없습니다"),
    ],
    ids=["Item 없음", "버전 없음", "다른 계열 버전", "해제가 시작보다 앞", "없는 계열"],
)
def test_validate_rule_reports_problems(bad_rule, message):
    problems = validate_rule(bad_rule, make_families())

    assert any(message in p for p in problems)


def test_validate_rule_accepts_valid_rule():
    assert validate_rule(DEFECT_V1_TO_V3, make_families()) == ()
