"""N 버전 그룹 판정 (features.version.groups) — 멘토 5차 요청 1번, 팀 결정 2026-11-05.

KCTC Inline O2 교육용 노트의 실제 버전 17개(순서 10~170)와 화성 설비 19대(_k 7·_j 6·_i 6)로 확인한다.
"""

from datetime import date

import pytest

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aggregation.service import summarize
from setron.features.version.groups import (
    GroupIssueCode, Slot, Tier, VersionGroups, check_groups, compare_with_groups, suggest_ladder, tier_of,
)
from setron.features.version.schemas import CompareReason, CompareStatus, ReleaseVersion
from setron.features.version.service import FamilyReleases, compare_version

FAMILY, MODEL = 7, 1
NAMES = ["v2296.1", "v2296.2", "v2296.3", "v2296.5", "v2296.6", "v2296.7", "v2296.8", "v2296.9", "v2296.9_a",
         "v2296.9_b", "v2296.9_c", "v2296.9_d", "v2296.9_e", "v2296.9_g", "v2296.9_i", "v2296.9_j", "v2296.9_k"]
RELEASES = [ReleaseVersion(i + 1, FAMILY, name, (i + 1) * 10) for i, name in enumerate(NAMES)]
ID = {r.version_raw: r.release_version_id for r in RELEASES}


def family(*extra):
    return FamilyReleases(FAMILY, [*RELEASES, *extra])


def groups(**slots):
    names = {"n_2": Slot.N_MINUS_2, "n_1": Slot.N_MINUS_1, "n": Slot.N, "n1": Slot.N_PLUS_1, "n2": Slot.N_PLUS_2}
    return VersionGroups({names[k]: frozenset(ID[v] for v in vs) for k, vs in slots.items()})


def tier(fam, grp, raw):
    return tier_of(compare_with_groups(fam, raw, grp))


def test_same_period_versions_grouped_as_n_are_all_achieved():
    """멘토 사례: 같은 시기 핫픽스 _i·_j·_k 를 N으로 묶으면 셋 다 N(연초록)·달성."""
    grp = groups(n=["v2296.9_i", "v2296.9_j", "v2296.9_k"])
    for raw in ("v2296.9_i", "v2296.9_j", "v2296.9_k"):
        result = compare_with_groups(family(), raw, grp)
        assert result.status is CompareStatus.AT_OR_ABOVE and result.slot == 0 and tier_of(result) is Tier.AT
    assert tier(family(), grp, "v2296.9_g") is Tier.ONE_BELOW          # 넣은 칸(N) 바로 아래 릴리즈 → N-1 (11/08 규칙 수정)


def test_explicit_slots_give_five_colors():
    grp = groups(n_2=["v2296.9_g"], n_1=["v2296.9_i"], n=["v2296.9_j"], n1=["v2296.9_k"])
    assert [tier(family(), grp, raw) for raw in ("v2296.9_e", "v2296.9_g", "v2296.9_i", "v2296.9_j", "v2296.9_k")] == \
        [Tier.TWO_BELOW, Tier.TWO_BELOW, Tier.ONE_BELOW, Tier.AT, Tier.ABOVE]
    below = compare_with_groups(family(), "v2296.9_i", grp)
    assert below.status is CompareStatus.BELOW and below.steps_below == 1 and below.slot == -1
    assert compare_with_groups(family(), "v2296.9_g", grp).steps_below == 2


def test_unassigned_version_follows_nearest_older_group():
    """N={_k}, N-2={_g} 이면 그 사이 _i·_j 는 바로 아래 칸(N-2)을 따른다 — 달성으로 잘못 세지 않는다."""
    grp = groups(n_2=["v2296.9_g"], n=["v2296.9_k"])
    assert tier(family(), grp, "v2296.9_i") is Tier.TWO_BELOW
    assert tier(family(), grp, "v2296.9_j") is Tier.TWO_BELOW
    grp = groups(n_1=["v2296.9_i"], n=["v2296.9_k"])
    assert tier(family(), grp, "v2296.9_j") is Tier.ONE_BELOW


def test_version_newer_than_every_group_is_above_n():
    newer = ReleaseVersion(99, FAMILY, "v2296.10", 180)
    assert tier(family(newer), groups(n=["v2296.9_k"]), "v2296.10") is Tier.ABOVE
    result = compare_with_groups(family(newer), "v2296.10", groups(n=["v2296.9_j"], n2=["v2296.9_k"]))
    assert result.slot == 2 and tier_of(result) is Tier.ABOVE


def test_one_version_cannot_be_in_two_slots():
    with pytest.raises(ValueError, match="한 칸에만"):
        VersionGroups({Slot.N: frozenset({1}), Slot.N_MINUS_1: frozenset({1})})


def test_empty_n_means_target_not_set():
    grp = groups(n_1=["v2296.9_j"])
    result = compare_with_groups(family(), "v2296.9_k", grp)
    assert result.status is CompareStatus.UNDETERMINED and result.reason is CompareReason.TARGET_NOT_SET
    assert [i.code for i in check_groups(family(), grp) if i.blocks] == [GroupIssueCode.N_EMPTY]


def test_crossed_order_is_warned_but_explicit_slots_are_kept():
    grp = groups(n_1=["v2296.9_k"], n=["v2296.9_e"])
    issues = check_groups(family(), grp)
    assert [i.code for i in issues] == [GroupIssueCode.ORDER_CROSSED] and not issues[0].blocks
    assert tier(family(), grp, "v2296.9_k") is Tier.ONE_BELOW and tier(family(), grp, "v2296.9_e") is Tier.AT


def test_missing_and_unordered_members_are_reported_and_not_used_as_anchors():
    unordered = ReleaseVersion(50, FAMILY, "v2296.9_h", None)
    grp = VersionGroups({Slot.N: frozenset({ID["v2296.9_k"], 404}), Slot.N_MINUS_1: frozenset({50})})
    codes = {i.code for i in check_groups(family(unordered), grp)}
    assert codes == {GroupIssueCode.MISSING_RELEASE, GroupIssueCode.ORDER_UNKNOWN}
    assert tier(family(unordered), grp, "v2296.9_h") is Tier.ONE_BELOW          # 넣은 칸은 그대로
    assert tier(family(unordered), grp, "v2296.9_j") is Tier.ONE_BELOW          # 기준점은 _k 하나뿐 → 바로 아래 _j는 N-1


def test_same_exceptions_as_single_target_comparison():
    grp = groups(n=["v2296.9_k"])
    unordered = ReleaseVersion(50, FAMILY, "v2296.9_h", None)
    assert compare_with_groups(family(), None, grp).reason is CompareReason.MISSING_VERSION
    assert compare_with_groups(family(), "v9999", grp).reason is CompareReason.NOT_REGISTERED
    assert compare_with_groups(family(unordered), "v2296.9_h", grp).reason is CompareReason.ORDER_UNKNOWN
    assert compare_with_groups(family(), "v2296.9_k", grp, release_version_id=404).reason is CompareReason.OTHER_FAMILY
    assert compare_with_groups(FamilyReleases(FAMILY, []), "v2296.9_k", grp).reason is CompareReason.NO_RELEASE_DATA


def test_storage_form_round_trip_and_single_target():
    grp = groups(n_2=["v2296.9_g"], n=["v2296.9_k", "v2296.9_j"])
    assert grp.to_dict() == {"N-2": [ID["v2296.9_g"]], "N": sorted([ID["v2296.9_j"], ID["v2296.9_k"]])}
    assert VersionGroups.from_dict(grp.to_dict()) == grp
    assert VersionGroups.single(5).to_dict() == {"N": [5]}
    with pytest.raises(ValueError, match="알 수 없는 N 칸"):
        VersionGroups.from_dict({"N+3": [1]})


def test_ladder_suggestion_fills_empty_slots_next_to_each_other():
    grp = suggest_ladder(family(), groups(n=["v2296.9_j"]))
    assert grp.to_dict() == {"N-2": [ID["v2296.9_g"]], "N-1": [ID["v2296.9_i"]], "N": [ID["v2296.9_j"]],
                             "N+1": [ID["v2296.9_k"]]}
    kept = suggest_ladder(family(), groups(n_1=["v2296.9_e"], n=["v2296.9_k"]))     # 사용자가 채운 칸은 그대로
    assert kept.members(Slot.N_MINUS_1) == {ID["v2296.9_e"]} and kept.members(Slot.N_MINUS_2) == {ID["v2296.9_d"]}


def test_ladder_suggestion_refreshes_outside_multiple_n_versions():
    fam = family()
    selected = ["v2296.9_e", "v2296.9_j"]
    previous = groups(n=selected, n1=["v2296.9_g"], n2=["v2296.9_k"])
    filled = suggest_ladder(fam, previous, replace_surrounding=True)
    assert filled == groups(n_2=["v2296.9_c"], n_1=["v2296.9_d"], n=selected, n1=["v2296.9_k"])
    assert tier(fam, filled, "v2296.9_g") is Tier.AT
    assert not check_groups(fam, filled)


def test_ladder_refresh_keeps_existing_slots_when_no_n_order_can_be_used():
    unordered = ReleaseVersion(50, FAMILY, "미확정 버전", None)
    previous = VersionGroups({Slot.N: {50, 404}, Slot.N_PLUS_1: {ID["v2296.9_k"]}})
    assert suggest_ladder(family(unordered), previous, replace_surrounding=True) == previous


def test_single_target_tiers_keep_existing_judgement():
    """그룹이 없는 계열은 기존 compare_version 그대로이고, 색만 같은 단계로 읽는다."""
    newer = ReleaseVersion(99, FAMILY, "v2296.10", 180)
    fam = family(newer)
    target = ID["v2296.9_k"]
    assert [tier_of(compare_version(fam, raw, target)) for raw in ("v2296.10", "v2296.9_k", "v2296.9_j", "v2296.9_i")] == \
        [Tier.ABOVE, Tier.AT, Tier.ONE_BELOW, Tier.TWO_BELOW]
    assert compare_version(fam, "v2296.9_j", target).slot is None


def _hwaseong_inline_o2():
    versions = ["v2296.9_k"] * 7 + ["v2296.9_j"] * 6 + ["v2296.9_i"] * 6
    return [EquipmentRecord(i, f"ESINO{i:02d}", date(2026, 10, 5), "화성", "15L", "Inline O2",
                            (PmVersion(None, MODEL, FAMILY, raw),)) for i, raw in enumerate(versions, 1)]


@pytest.mark.parametrize("target, compliant, below", [
    (ID["v2296.9_k"], 7, 12),                                                   # 기존 목표 1개: 36.8%
    (groups(n=["v2296.9_k"]), 7, 12),
    (groups(n=["v2296.9_i", "v2296.9_j", "v2296.9_k"]), 19, 0),                 # 같은 시기 묶음: 100%
    (groups(n_2=["v2296.9_i"], n_1=["v2296.9_j"], n=["v2296.9_k"]), 7, 12),
])
def test_dashboard_summary_uses_groups(target, compliant, below):
    summary = summarize(_hwaseong_inline_o2(), {FAMILY: family()}, {(MODEL, FAMILY): target})
    assert (summary.compliant, summary.below, summary.undetermined) == (compliant, below, 0)
    assert summary.upgrade_needed == below



def test_single_version_in_n_matches_single_target_tiers_for_every_release():
    """N 칸에 버전 하나만 넣으면 목표 하나 판정과 모든 릴리즈에서 색 단계가 같다 (11/08: _j가 N-2로 나오던 문제).

    넣은 칸보다 모두 오래되거나 최신인 버전은 가장 가까운 칸에서 릴리즈 몇 단계 떨어졌는지로 정한다.
    """
    fam = family()
    for target in RELEASES[1:-1]:
        grp = VersionGroups.single(target.release_version_id)
        for release in RELEASES:
            single = compare_version(fam, release.version_raw, target.release_version_id)
            grouped = compare_with_groups(fam, release.version_raw, grp)
            assert tier_of(grouped) is tier_of(single), (target.version_raw, release.version_raw)


def test_steps_below_lowest_slot_and_above_top_slot():
    names = [r.version_raw for r in RELEASES]
    k = names.index("v2296.9_k")
    grp = groups(n=["v2296.9_k"])
    assert tier(family(), grp, names[k - 1]) is Tier.ONE_BELOW and tier(family(), grp, names[k - 2]) is Tier.TWO_BELOW
    low = groups(n=["v2296.9_k"], n_1=[names[k - 1]])
    assert tier(family(), low, names[k - 2]) is Tier.TWO_BELOW                     # N-1 칸 바로 아래 → N-2
    if k + 1 < len(names):
        assert tier(family(), grp, names[k + 1]) is Tier.ABOVE
