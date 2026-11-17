"""N 그룹 목표(멘토 5차, 팀 결정 11-05)에서 우선순위·AI 자료·Excel 표기·라인별 히트맵 색이 맞게 동작하는지 검증한다.

N 그룹 판정 자체는 ``test_version_groups.py``가 맡는다. 여기서는 그 판정을 쓰는 BE-017·019·021 쪽만 본다.
"""

from setron.features.aggregation.service import summarize
from setron.features.aging.schemas import DEFAULT_THRESHOLDS
from setron.features.analysis.service import build_facts
from setron.features.export.line_heatmap import line_heatmap_groups
from setron.features.export.schemas import StatusReport
from setron.features.export.service import _family_label, _target
from setron.features.item.service import ItemCatalog
from setron.features.priority.service import prioritize
from setron.features.risk.service import RiskAssessor
from setron.features.version.groups import Slot, Tier, VersionGroups
from test_analysis_service import F_O2, F_O3, FAMILIES, M_O2, M_O3, NAMES, RECORDS, V21, V22, V23, V31, data

# N = v2.3, N-1 = v2.2. v2.1은 어느 칸에도 없고 모든 칸보다 오래돼 N-2 이하다.
GROUPS = {(M_O2, F_O2): VersionGroups({Slot.N: frozenset({V23}), Slot.N_MINUS_1: frozenset({V22})}), (M_O3, F_O3): V31}


def o2_status(summary, version):
    return next(f for e in summary.equipment for f in e.families
                if f.family_id == F_O2 and f.pms[0].pm.version_raw == version)


def test_priority_counts_steps_by_group_slot():
    """N-1은 1칸, N-2 이하는 2칸, N은 0칸. 그룹 목표에서도 오류 없이 순위를 매긴다."""
    result = prioritize(RECORDS, RiskAssessor(FAMILIES, ItemCatalog(FAMILIES, []), []), FAMILIES, DEFAULT_THRESHOLDS,
                        targets=GROUPS)

    steps = {(p.pm.family_id, p.release.version_raw): p.steps_behind
             for e in result.ranked for p in e.positions}
    assert steps[(F_O2, "v2.3")] == 0 and steps[(F_O2, "v2.2")] == 1 and steps[(F_O2, "v2.1")] == 2
    assert steps[(F_O3, "v3.1")] == 0  # 릴리즈 하나인 기존 목표는 그대로


def test_priority_group_without_n_slot_has_no_steps():
    targets = {(M_O2, F_O2): VersionGroups({Slot.N_MINUS_1: frozenset({V22})}), (M_O3, F_O3): V31}

    result = prioritize(RECORDS, RiskAssessor(FAMILIES, ItemCatalog(FAMILIES, []), []), FAMILIES, DEFAULT_THRESHOLDS,
                        targets=targets)

    assert all(p.steps_behind is None for e in (*result.ranked, *result.needs_check)
               for p in e.positions if p.pm.family_id == F_O2)


def test_ai_facts_list_group_versions_by_slot():
    facts = build_facts(data(targets=GROUPS))

    targets = {t["계열"]: t["목표 N"] for t in facts["조회 조건"]["계열별 목표 N"]}
    assert targets == {"O2": "N 그룹 · N: v2.3 · N-1: v2.2", "O3": "v3.1"}


def test_ai_facts_mark_empty_n_slot_and_unknown_release():
    targets = {(M_O2, F_O2): VersionGroups({Slot.N_MINUS_1: frozenset({V22})}),
               (M_O3, F_O3): VersionGroups({Slot.N: frozenset({V31, 999})})}

    facts = build_facts(data(targets=targets))

    texts = {t["계열"]: t["목표 N"] for t in facts["조회 조건"]["계열별 목표 N"]}
    assert texts["O2"] == "N 그룹 · N 칸 비어 있음 (판단 불가)"
    assert texts["O3"] == "N 그룹 · N: v3.1, 릴리즈 미확인"


def test_ai_facts_distribution_uses_group_slots():
    """그룹에서 v2.1은 어느 칸에도 없고 가장 오래돼 N-2 이하, v2.2는 N-1 칸, v2.3은 N 칸이다."""
    o2 = build_facts(data(targets=GROUPS))["버전 분포"][0]

    assert {v["설치 버전"]: (v["판정"], v["단계"]) for v in o2["버전별 설비 수"]} == {
        "(버전 없음)": ("판단 불가", "비교 불가"), "v2.1": ("N 미만", "N-2 이하"),
        "v2.2": ("N 미만", "N-1"), "v2.3": ("N 이상", "N")}
    assert o2["단계별 설비 수"]["N-1"] == 1 and o2["단계별 설비 수"]["N-2 이하"] == 2


def test_export_labels_use_group_slots():
    summary = summarize(RECORDS, FAMILIES, GROUPS)

    assert _family_label(o2_status(summary, "v2.1")) == "N-2 이하"
    assert _family_label(o2_status(summary, "v2.2")) == "N-1"
    assert _target(o2_status(summary, "v2.2")) == "v2.3 (N 그룹 최신)"


def test_line_heatmap_colors_follow_group_tiers():
    """그룹 N보다 최신이면 진초록(N+1 이상), N 칸이면 연초록, N-1 노랑, N-2 이하 빨강."""
    targets = {(M_O2, F_O2): VersionGroups({Slot.N: frozenset({V22}), Slot.N_MINUS_1: frozenset({V21})}),
               (M_O3, F_O3): V31}
    groups = line_heatmap_groups(StatusReport(summary=summarize(RECORDS, FAMILIES, targets), family_names=NAMES))

    tiers = {c.tier for site in groups for family in site.families if family.family == "O2"
             for line in family.lines for c in line.cells}
    assert {Tier.ABOVE, Tier.AT, Tier.ONE_BELOW} <= tiers
