"""AI 원인·현황 설명 자료와 숫자 검사를 검증한다 (BE-019 완료 조건)."""

import json
from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aggregation.service import summarize, summarize_by_date
from setron.features.ai.examples import example_client
from setron.features.ai.schemas import AIStatus, AITask, AnalysisAnswer
from setron.features.analysis.schemas import AnalysisInput
from setron.features.analysis.service import build_facts, check_numbers, explain
from setron.features.item.schemas import Item, ItemState, TargetScope, VersionItem
from setron.features.item.service import ItemCatalog
from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
from setron.features.risk.service import RiskAssessor
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.infrastructure.ai_client import ExampleChatClient

M_O2, M_O3, M_HPA = 1, 2, 9
F_O2, F_O3 = 10, 20
V21, V22, V23, V31 = 101, 102, 103, 201
DAY1, DAY2 = date(2026, 9, 21), date(2026, 10, 21)

FAMILIES = {
    F_O2: FamilyReleases(F_O2, [ReleaseVersion(V21, F_O2, "v2.1", 1), ReleaseVersion(V22, F_O2, "v2.2", 2),
                                ReleaseVersion(V23, F_O2, "v2.3", 3)]),
    F_O3: FamilyReleases(F_O3, [ReleaseVersion(V31, F_O3, "v3.1", 1)]),
}
TARGETS = {(M_O2, F_O2): V23, (M_O3, F_O3): V31}
NAMES = {F_O2: "O2", F_O3: "O3"}
ITEMS = (Item(1, 1, "Recipe 편집 미저장 경고"), Item(2, 2, "환경안전 Interlock 추가"), Item(3, 3, "알람 조건 수정"))


def stored(item_id, states):
    codes = {"A": ItemState.APPLIED, "N": ItemState.NOT_APPLIED}
    return [VersionItem(r, item_id, codes[c], TargetScope.ALL) for r, c in zip((V21, V22, V23), states)]


CATALOG = ItemCatalog(FAMILIES, [*stored(1, "AAA"), *stored(2, "NNA"), *stored(3, "NAA")])
DIRECT_V21 = RiskRule(1, RuleType.DIRECT_VERSION, F_O2, "가상 결함 버전", release_version_id=V21,
                      mapping_status=MappingStatus.VERIFIED)


def equipment(eid, site, *pms, day=DAY2, hpa=False):
    return EquipmentRecord(eid, f"DM-{eid:03d}", day, site, "L1", "model", tuple(pms), is_hpa=hpa)


def o2(version, name=None):
    return PmVersion(name, M_O2, F_O2, version)


RECORDS = (
    equipment(1, "평택", o2("v2.3")),
    equipment(2, "평택", o2("v2.1")),
    equipment(3, "평택", o2(None)),
    equipment(4, "화성", o2("v2.2", "PM1"), PmVersion("PM2", M_O3, F_O3, "v3.1")),
    equipment(5, "화성", o2("v2.1")),
    equipment(6, "화성", o2("v2.3"), hpa=True),
)


def data(**overrides):
    values = dict(records=RECORDS, families=FAMILIES, targets=TARGETS, family_names=NAMES,
                  scope=(("Site", "전체"),), assessor=RiskAssessor(FAMILIES, CATALOG, [DIRECT_V21]),
                  item_catalog=CATALOG, items=ITEMS)
    values.update(overrides)
    return AnalysisInput(**values)


FACTS = build_facts(data())


# --- 자료 ------------------------------------------------------------------------

def test_overall_numbers_match_common_aggregation():
    summary = summarize(RECORDS, FAMILIES, TARGETS)
    overall = FACTS["전체 현황"]

    assert overall["설비 수(HPA 제외)"] == summary.equipment_count == 5
    assert (overall["N 이상"], overall["N 미만"], overall["판단 불가"]) == (2, 3, 1)
    assert overall["N 이상 적용률(%)"] == round(summary.rate * 100, 1) == 40.0
    assert overall["HPA 제외 설비"] == 1


def test_site_numbers_match_aggregation_run_per_site():
    hwaseong = next(s for s in FACTS["사업장별 현황"] if s["사업장"] == "화성")
    expected = summarize([r for r in RECORDS if r.site == "화성"], FAMILIES, TARGETS)

    assert hwaseong["N 이상"] == expected.compliant == 1
    assert hwaseong["N 미만"] == expected.below == 2
    assert [f["계열"] for f in hwaseong["계열별"]] == ["O2", "O3"]


def test_conditions_include_each_family_target():
    assert FACTS["조회 조건"]["계열별 목표 N"] == [{"계열": "O2", "목표 N": "v2.3"}, {"계열": "O3", "목표 N": "v3.1"}]
    assert FACTS["조회 조건"]["기준일"] == "2026-10-21"


def test_version_distribution_counts_equipment_per_installed_version():
    o2 = FACTS["버전 분포"][0]["버전별 설비 수"]

    assert {(v["설치 버전"], v["판정"]): v["설비 수"] for v in o2} == {
        ("(버전 없음)", "판단 불가"): 1, ("v2.1", "N 미만"): 2, ("v2.2", "N 미만"): 1, ("v2.3", "N 이상"): 1,
    }


def test_version_distribution_splits_below_by_tier():
    """목표가 릴리즈 하나(v2.3)여도 한 단계 아래는 N-1, 두 단계 아래는 N-2 이하로 나눠 보낸다."""
    o2 = FACTS["버전 분포"][0]

    assert {v["설치 버전"]: v["단계"] for v in o2["버전별 설비 수"]} == {
        "(버전 없음)": "비교 불가", "v2.1": "N-2 이하", "v2.2": "N-1", "v2.3": "N"}
    assert o2["단계별 설비 수"] == {"N+1 이상": 0, "N": 1, "N-1": 1, "N-2 이하": 2, "비교 불가": 1}


def test_counts_include_all_five_tiers_and_rule_text():
    total = FACTS["전체 현황"]["단계별 건수(설비×계열)"]

    assert list(total) == ["N+1 이상", "N", "N-1", "N-2 이하", "비교 불가"]
    assert sum(total.values()) == FACTS["전체 현황"]["N 이상"] + FACTS["전체 현황"]["N 미만"] + FACTS["전체 현황"]["판단 불가"]
    assert all("단계별 건수(설비×계열)" in site for site in FACTS["사업장별 현황"])
    assert "N-2 이하" in FACTS["조회 조건"]["단계 기준"]


def test_risks_are_grouped_by_site_and_reason_without_treating_unknown_as_safe():
    risks = FACTS["위험"]

    assert risks["사업장별"] == [{"사업장": "평택", "위험 설비": 1, "미확인 설비": 1},
                              {"사업장": "화성", "위험 설비": 1, "미확인 설비": 0}]
    assert risks["사유별"] == [{"사유": "가상 결함 버전", "설비": ["DM-002", "DM-005"]}]


def test_gap_evidence_lists_items_in_target_but_not_in_installed_version():
    gaps = {g["설치 버전"]: g for g in FACTS["통일 지연 근거"]}

    assert gaps["v2.1"]["설비 수"] == 2
    assert [i["내용"] for i in gaps["v2.1"]["목표 N에만 포함된 Item"]] == ["환경안전 Interlock 추가", "알람 조건 수정"]
    assert [i["내용"] for i in gaps["v2.2"]["목표 N에만 포함된 Item"]] == ["환경안전 Interlock 추가"]
    assert gaps["v2.2"]["목표 N에만 포함된 Item"][0]["근거"] == "item_id 2"


def test_missing_inputs_are_marked_instead_of_guessed():
    facts = build_facts(data(assessor=None, item_catalog=None))

    assert facts["위험"] == {"상태": "미계산"}
    assert facts["통일 지연 근거"] == []
    assert "날짜별 추이" not in facts


def test_trend_is_added_when_two_dates_exist():
    earlier = (equipment(1, "평택", o2("v2.2"), day=DAY1),)
    facts = build_facts(data(trend=summarize_by_date(earlier + RECORDS, FAMILIES, TARGETS)))

    assert [t["기준일"] for t in facts["날짜별 추이"]] == ["2026-09-21", "2026-10-21"]


def test_facts_can_be_sent_as_json():
    assert json.loads(json.dumps(FACTS, ensure_ascii=False)) == FACTS


# --- 숫자 검사 ---------------------------------------------------------------------

def answer(text, *evidence):
    return AnalysisAnswer(text, evidence=evidence)


def test_numbers_from_facts_pass():
    reply = answer("2026-10-21 기준 설비 5대 중 적용률은 40.0%입니다.", "평택 위험 설비 1대, 화성 N 미만 2건")

    assert check_numbers(reply, FACTS) == ()


def test_numbers_not_in_facts_are_reported_once():
    reply = answer("적용률은 75%이고 위험 설비는 7대입니다.", "75%는 전월보다 높음")

    assert check_numbers(reply, FACTS) == ("75", "7")


def test_version_notation_and_zero_one_are_not_checked():
    reply = answer("v2.1·v4.56.6.43_DFB·4.56.6.42 버전 설비는 0대 또는 1대입니다.")

    assert check_numbers(reply, FACTS) == ()


# --- 설명 요청 ---------------------------------------------------------------------

def test_explain_sends_facts_and_reports_unmatched_numbers():
    reply = {"answer": "평택 위험 설비는 1대, 전체 적용률은 55%입니다.", "evidence": [], "assumptions": [],
             "unconfirmed": []}
    client = ExampleChatClient([json.dumps(reply, ensure_ascii=False)])

    report = explain(client, "사업장별 위험 설비 알려줘", data())

    assert report.result.ok
    assert report.unmatched_numbers == ("55",) and not report.numbers_match
    assert '"통일 지연 근거"' in client.calls[0][1].content


def test_explain_failure_keeps_facts_and_skips_number_check():
    report = explain(example_client(AITask.ANALYSIS, "timeout"), "질문", data())

    assert report.result.status is AIStatus.FAILED
    assert report.unmatched_numbers == ()
    assert report.facts == FACTS
