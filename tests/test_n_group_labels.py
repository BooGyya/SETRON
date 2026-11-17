"""N 그룹 공용 표기 함수(목표 N 문구, 설비·계열 색 단계)를 검증한다.

AI 자료·보고서·화면이 각자 문구와 색을 만들지 않고 ``version.groups.target_label``과
``FamilyStatus.tier``를 함께 쓰도록 옮긴 것이다.
"""

from setron.features.aggregation.schemas import FamilyStatus, PmComparison, PmVersion
from setron.features.aggregation.service import summarize
from setron.features.version.groups import Slot, Tier, VersionGroups, target_label
from setron.features.version.schemas import CompareStatus, VersionComparison
from test_analysis_service import F_O2, FAMILIES, M_O2, M_O3, F_O3, RECORDS, V21, V22, V23, V31

O2 = FAMILIES[F_O2]


def test_target_label_single_release():
    assert target_label(O2, V23) == "v2.3"
    assert target_label(O2, 999) == "릴리즈 미확인"
    assert target_label(None, V23) == "릴리즈 미확인"
    assert target_label(O2, None) == "릴리즈 미확인"


def test_target_label_group_lists_slots_newest_first_in_version_order():
    groups = VersionGroups({Slot.N: frozenset({V23, V22}), Slot.N_MINUS_2: frozenset({V21})})

    assert target_label(O2, groups) == "N 그룹 · N: v2.2, v2.3 · N-2: v2.1"


def test_target_label_group_marks_empty_n_and_missing_release():
    assert target_label(O2, VersionGroups({Slot.N_MINUS_1: frozenset({V22})})) == "N 그룹 · N 칸 비어 있음 (판단 불가)"
    assert target_label(O2, VersionGroups({Slot.N: frozenset({V23, 999})})) == "N 그룹 · N: v2.3, 릴리즈 미확인"
    assert target_label(None, VersionGroups({Slot.N: frozenset({V23})})) == "N 그룹 · N: 릴리즈 미확인"


def _tiers(targets):
    summary = summarize(RECORDS, FAMILIES, targets)
    return {(f.family_id, f.pms[0].pm.version_raw): f.tier for e in summary.equipment for f in e.families}


def test_family_tier_follows_group_slots():
    targets = {(M_O2, F_O2): VersionGroups({Slot.N: frozenset({V22}), Slot.N_MINUS_1: frozenset({V21})}),
               (M_O3, F_O3): V31}

    tiers = _tiers(targets)

    assert tiers[(F_O2, "v2.3")] is Tier.ABOVE
    assert tiers[(F_O2, "v2.2")] is Tier.AT
    assert tiers[(F_O2, "v2.1")] is Tier.ONE_BELOW
    assert tiers[(F_O3, "v3.1")] is Tier.AT


def test_family_tier_single_target_splits_same_and_newer():
    tiers = _tiers({(M_O2, F_O2): V22, (M_O3, F_O3): V31})

    assert tiers[(F_O2, "v2.3")] is Tier.ABOVE
    assert tiers[(F_O2, "v2.2")] is Tier.AT
    assert tiers[(F_O2, "v2.1")] is Tier.ONE_BELOW


def _status(status, *comparisons):
    pms = tuple(PmComparison(PmVersion(f"PM{i}", M_O2, F_O2, "v"), c) for i, c in enumerate(comparisons, 1))
    return FamilyStatus(1, F_O2, status, pms, pm_mismatch=False)


def test_family_tier_uses_lowest_pm_of_the_family_status():
    """미달이면 미달 PM 중 가장 낮은 단계. 판단 불가 PM은 미달 판정의 색을 바꾸지 않는다."""
    below1 = VersionComparison("v", None, None, CompareStatus.BELOW, 1, slot=-1)
    below2 = VersionComparison("v", None, None, CompareStatus.BELOW, 2, slot=-2)
    unknown = VersionComparison("v", None, None, CompareStatus.UNDETERMINED)

    assert _status(CompareStatus.BELOW, below1, below2, unknown).tier is Tier.TWO_BELOW
    assert _status(CompareStatus.BELOW, below1, unknown).tier is Tier.ONE_BELOW
    assert _status(CompareStatus.UNDETERMINED, unknown).tier is Tier.UNKNOWN
    assert _status(CompareStatus.AT_OR_ABOVE).tier is Tier.UNKNOWN  # PM이 없으면 칠할 근거가 없다
