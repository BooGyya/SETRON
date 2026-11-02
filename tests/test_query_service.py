"""질문 조건 검사와 자료 고르기를 검증한다 (BE-018 완료 조건)."""

from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aggregation.service import summarize
from setron.features.ai.schemas import QueryConditions, TargetView
from setron.features.ai.service import parse_question
from setron.features.item.schemas import Item
from setron.features.query.schemas import (
    FamilyChoice,
    GroupChoice,
    IssueKind,
    QueryCatalog,
)
from setron.features.query.service import answer_query, resolve_conditions, select_records
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.infrastructure.ai_client import ExampleChatClient

M_O2, M_O3, M_C4, M_HPA = 1, 2, 3, 9
F_O2_MEM, F_O2_FDY, F_O3, F_C4 = 10, 11, 20, 30
DAY1, DAY2 = date(2026, 9, 21), date(2026, 10, 21)

RELEASES = {
    F_O2_MEM: FamilyReleases(F_O2_MEM, [ReleaseVersion(101, F_O2_MEM, "v2.1", 1),
                                        ReleaseVersion(103, F_O2_MEM, "v2.3", 2)]),
    F_O2_FDY: FamilyReleases(F_O2_FDY, [ReleaseVersion(113, F_O2_FDY, "v2.3", 1)]),
    F_O3: FamilyReleases(F_O3, [ReleaseVersion(201, F_O3, "v3.1", 1)]),
    F_C4: FamilyReleases(F_C4, [ReleaseVersion(301, F_C4, "v4.56.6.43", 1)]),
}
CATALOG = QueryCatalog(
    sites=("평택", "화성"),
    lines=("P1", "16L"),
    groups=(GroupChoice(M_O2, "O2", "M-O2", "OXIDE"), GroupChoice(M_O3, "O3", "M-O3", "OXIDE"),
            GroupChoice(M_C4, "C4", "M-C4", "POLY"), GroupChoice(M_HPA, "HPA", "HPA", "ETC")),
    families=(FamilyChoice(F_O2_MEM, "O2 Memory", M_O2), FamilyChoice(F_O2_FDY, "O2 Foundry", M_O2),
              FamilyChoice(F_O3, "O3", M_O3), FamilyChoice(F_C4, "C4", M_C4)),
    dates=(DAY1, DAY2),
    releases=RELEASES,
    items=(Item(1, 1, "Recipe 편집 미저장 경고"), Item(2, 2, "Recipe 승인 상태 확인"),
           Item(3, 3, "환경안전 Interlock 추가")),
)
TARGETS = {(M_O2, F_O2_MEM): 103, (M_O3, F_O3): 201, (M_C4, F_C4): 301}


def equipment(eid, site, line, *pms, day=DAY2, hpa=False):
    return EquipmentRecord(eid, f"DM-{eid:03d}", day, site, line, "model", tuple(pms), is_hpa=hpa)


RECORDS = (
    equipment(1, "평택", "P1", PmVersion(None, M_O2, F_O2_MEM, "v2.3")),
    equipment(2, "화성", "16L", PmVersion("PM1", M_O2, F_O2_MEM, "v2.1"), PmVersion("PM2", M_O3, F_O3, "v3.1")),
    equipment(3, "화성", "16L", PmVersion(None, M_C4, F_C4, "v4.56.6.43")),
    equipment(4, "평택", "P1", PmVersion(None, M_HPA, F_O2_MEM, "v2.3"), hpa=True),
    equipment(1, "평택", "P1", PmVersion(None, M_O2, F_O2_MEM, "v2.1"), day=DAY1),
)


def resolve(**conditions):
    return resolve_conditions(QueryConditions(**conditions), CATALOG)


def codes(records):
    return [(r.equipment_code, r.base_date) for r in records]


# --- AI에 주는 선택지 -------------------------------------------------------------

def test_choices_exclude_analysis_excluded_models_and_list_stored_dates():
    choices = CATALOG.choices()

    assert choices.groups == ("O2", "O3", "C4")
    assert choices.models == ("M-O2", "M-O3", "M-C4")
    assert choices.families == ("O2 Memory", "O2 Foundry", "O3", "C4")
    assert choices.base_dates == ("2026-09-21", "2026-10-21")


def test_parse_question_sends_choices_in_system_prompt():
    client = ExampleChatClient(['{"sites": ["화성"]}'])

    parse_question(client, "화성 오투 현황", CATALOG.choices())

    system = client.calls[0][0].content
    assert '"O2 Memory", "O2 Foundry"' in system
    assert "임의로 고르지 말고 unresolved" in system


# --- 조건 확정 -------------------------------------------------------------------

def test_values_in_choices_are_resolved_ignoring_case_and_spaces():
    query = resolve(sites=[" 화성 "], lines=["16l"], groups=["c4"], families=["o2 memory"],
                    target_view=TargetView.HEATMAP)

    assert query.ready
    assert (query.sites, query.lines) == (("화성",), ("16L",))
    assert (query.group_ids, query.family_ids) == ((M_C4,), (F_O2_MEM,))
    assert query.target_view is TargetView.HEATMAP


def test_missing_base_date_uses_latest_stored_date():
    assert resolve().base_date == DAY2


def test_value_not_in_choices_is_reported_with_candidates_and_blocks_navigation():
    query = resolve(lines=["Fab 3"], target_view=TargetView.DASHBOARD)

    issue = query.issues[0]
    assert (issue.field, issue.kind, issue.candidates) == ("lines", IssueKind.NOT_IN_CHOICES, ("P1", "16L"))
    assert not query.ready and query.target_view is None


def test_group_label_and_model_name_point_to_same_group():
    """화면 설비군 이름(C4)과 모델명(M-C4)은 같은 설비군 조건이 된다."""
    assert resolve(groups=["C4"], models=["M-C4"]).group_ids == (M_C4,)
    assert resolve(groups=["O2"], models=["M-O3"]).group_ids == (M_O2, M_O3)


def test_hpa_group_is_not_a_choice():
    assert resolve(groups=["HPA"]).issues[0].kind is IssueKind.NOT_IN_CHOICES


def test_ai_unresolved_condition_needs_user_check():
    query = resolve(unresolved=["오투 → O2 Memory / O2 Foundry 중 확인 필요"])

    assert query.issues[0].kind is IssueKind.UNRESOLVED
    assert not query.ready


def test_base_date_must_be_stored_and_valid():
    not_stored = resolve(base_date="2026-08-21").issues[0]
    invalid = resolve(base_date="지난달").issues[0]

    assert (not_stored.kind, not_stored.candidates) == (IssueKind.DATE_NOT_STORED, ("2026-09-21", "2026-10-21"))
    assert invalid.kind is IssueKind.INVALID_DATE


def test_version_is_found_inside_requested_family():
    assert resolve(families=["O2 Memory"], versions=["v2.3"]).release_ids == (103,)


def test_same_version_in_two_families_is_ambiguous_without_family():
    issue = resolve(versions=["v2.3"]).issues[0]

    assert issue.kind is IssueKind.AMBIGUOUS
    assert issue.candidates == ("O2 Memory v2.3", "O2 Foundry v2.3")


def test_version_with_different_notation_is_not_linked_but_suggested():
    issue = resolve(families=["C4"], versions=["V4.56.6.43"]).issues[0]

    assert (issue.kind, issue.candidates) == (IssueKind.NOT_FOUND, ("v4.56.6.43",))


def test_item_is_resolved_only_when_one_item_matches():
    assert resolve(items=["Interlock"]).item_ids == (3,)
    ambiguous = resolve(items=["Recipe"]).issues[0]
    missing = resolve(items=["없는 기능"]).issues[0]

    assert (ambiguous.kind, len(ambiguous.candidates)) == (IssueKind.AMBIGUOUS, 2)
    assert missing.kind is IssueKind.NOT_FOUND


# --- 자료 고르기 -----------------------------------------------------------------

def test_records_are_filtered_by_date_site_and_line():
    assert codes(select_records(RECORDS, resolve(sites=["화성"]), CATALOG)) == [("DM-002", DAY2), ("DM-003", DAY2)]
    assert codes(select_records(RECORDS, resolve(base_date="2026-09-21"), CATALOG)) == [("DM-001", DAY1)]


def test_family_condition_keeps_only_matching_pms_of_mixed_equipment():
    selected = select_records(RECORDS, resolve(families=["O3"]), CATALOG)

    assert codes(selected) == [("DM-002", DAY2)]
    assert [pm.pm_name for pm in selected[0].pms] == ["PM2"]


def test_group_and_version_conditions_filter_pms():
    c4 = select_records(RECORDS, resolve(groups=["C4"]), CATALOG)
    o3 = select_records(RECORDS, resolve(models=["M-O3"]), CATALOG)
    old_o2 = select_records(RECORDS, resolve(families=["O2 Memory"], versions=["v2.1"]), CATALOG)

    assert codes(c4) == [("DM-003", DAY2)]
    assert [pm.pm_name for pm in o3[0].pms] == ["PM2"]
    assert codes(old_o2) == [("DM-002", DAY2)]


def test_answer_uses_common_aggregation_with_same_conditions():
    query = resolve(families=["O2 Memory"], target_view=TargetView.DASHBOARD)

    answer = answer_query(query, RECORDS, CATALOG, TARGETS)

    expected = summarize(select_records(RECORDS, query, CATALOG), RELEASES, TARGETS)
    assert answer.summary == expected
    assert (answer.summary.compliant, answer.summary.below, answer.summary.hpa_excluded) == (1, 1, 1)
    assert answer.query is query  # 화면 이동에 같은 조건을 넘긴다


def test_answer_is_not_queried_while_conditions_need_check():
    answer = answer_query(resolve(lines=["Fab 3"]), RECORDS, CATALOG, TARGETS)

    assert answer.summary is None
    assert answer.query.issues
