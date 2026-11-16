"""AI 분석 대화 — 질문 모음 (요청 인식 + 답 만들기). AI 없이 규칙과 DB 집계만으로 같은 답이 나와야 한다.

시연 자료와 같은 모양: 평택(P1·P2·P3)·화성(15L·16L·13L), 기준일 2026-09-21 · 2026-10-21, O2·C3 통합UI·O3(목표 미지정)·HPA.
"""

from datetime import date

import pytest

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.chat import ChatData, Intent, answer, recognize
from setron.features.chat.schemas import GroupBy, Verdict
from setron.features.query.schemas import FamilyChoice, GroupChoice, QueryCatalog
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

SEP, OCT = date(2026, 9, 21), date(2026, 10, 21)
O2, C3, O3, HPA = 1, 2, 3, 4                      # 계열 ID
G_O2, G_C3, G_O3, G_HPA = 11, 12, 13, 14          # 모델 ID
REL = {
    O2: [ReleaseVersion(101, O2, "v90.1.0.41", 10), ReleaseVersion(102, O2, "v90.1.0.43", 20)],
    C3: [ReleaseVersion(201, C3, "v3.52_13", 10, date(2026, 2, 2)), ReleaseVersion(202, C3, "v3.52_20", 20, date(2026, 6, 1))],
    O3: [ReleaseVersion(301, O3, "v4.49.62.77_d10", 10)],
}
FAMILIES = {fid: FamilyReleases(fid, rels) for fid, rels in REL.items()}
TARGETS = {(G_O2, O2): 102, (G_C3, C3): 202}       # O3는 목표 미지정
CATALOG = QueryCatalog(
    sites=("평택", "화성"), lines=("P1", "P2", "P3", "13L", "15L", "16L"),
    groups=(GroupChoice(G_O2, "O2", "M-O2", "OXIDE"), GroupChoice(G_C3, "C3", "M-C3", "POLY"),
            GroupChoice(G_O3, "O3", "M-O3", "OXIDE")),
    families=(FamilyChoice(O2, "O2", G_O2), FamilyChoice(C3, "C3 통합UI", G_C3), FamilyChoice(O3, "O3", G_O3)),
    dates=(SEP, OCT), releases=FAMILIES)
MODEL = {O2: (G_O2, "M-O2"), C3: (G_C3, "M-C3"), O3: (G_O3, "M-O3"), HPA: (G_HPA, "HPA")}
#        (ID, 호기, 사업장, 라인, 계열, 9월 버전, 10월 버전)
EQUIPMENT = [
    (1, "DM-009", "평택", "P1", O2, "v90.1.0.41", "v90.1.0.41"),
    (2, "DM-010", "평택", "P1", O2, "v90.1.0.41", "v90.1.0.43"),
    (3, "DM-020", "평택", "P2", O2, "v90.1.0.43", None),
    (4, "DM-021", "평택", "P2", O2, "v90.1.0.43", "v90.1.0.43"),
    (5, "DM-029", "평택", "P3", O2, "v90.1.0.41", "v90.1.0.41"),
    (6, "DM-027", "평택", "P3", C3, "v3.52_13", "v3.52_13"),
    (7, "DM-030", "평택", "P3", O3, "v4.49.62.77_d10", "v4.49.62.77_d10"),
    (8, "DM-031", "평택", "P3", O2, "v90.1.0.43", "v90.1.0.43"),
    (9, "H-001", "화성", "15L", O2, "v90.1.0.43", "v90.1.0.43"),
    (10, "H-002", "화성", "15L", C3, "v3.52_20", "v3.52_20"),
    (11, "H-003", "화성", "16L", C3, "v3.52_20", "v3.52_13"),
    (12, "H-004", "화성", "13L", O3, "v4.49.62.77_d10", "v4.49.62.77_d10"),
    (13, "H-005", "화성", "13L", HPA, None, None),
]


def _records():
    found = []
    for eid, code, site, line, fid, sep, oct_ in EQUIPMENT:
        gid, model = MODEL[fid]
        for day, raw in ((SEP, sep), (OCT, oct_)):
            pms = () if fid == HPA else (PmVersion(None, gid, fid, raw),)
            found.append(EquipmentRecord(eid, code, day, site, line, model, pms, is_hpa=fid == HPA))
    return tuple(found)


DATA = ChatData(_records(), CATALOG, TARGETS, {O2: "O2", C3: "C3 통합UI", O3: "O3"})


def ask(question):
    return answer(recognize(question, CATALOG), DATA)


def codes(result, title="조회 결과"):
    table = next(t for t in result.tables if t.title == title)
    column = table.columns.index("설비명")
    return [row[column] for row in table.rows]


# --- 와이어프레임 질문 ------------------------------------------------------------------
def test_wireframe_search_pyeongtaek_o2_below_target():
    result = ask("평택 O2 중 목표 N 미만인 설비만 보여줘")
    assert result.request.intents == (Intent.SEARCH,) and result.request.verdict is Verdict.BELOW
    assert sorted(codes(result)) == ["DM-009", "DM-029"]
    assert "평택 M-O2 설비 6대 중 목표 N 미만은 2대입니다." in result.summary and "P1 1대 · P3 1대" in result.summary
    assert "Site = 평택" in result.understood and "설비군 = M-O2" in result.understood and "기준일 2026-10-21" in result.understood


def test_wireframe_lowest_area_full_analysis():
    result = ask("목표 N 달성률이 가장 낮은 원본 구역을 분석해줘")
    assert result.request.intents == (Intent.COMPARE, Intent.CAUSE, Intent.PRIORITY, Intent.FORECAST)
    assert "가장 낮은 원본 구역은 16L(0.0%, 판단 가능 1대 중 0대)" in result.summary
    bar = next(c for c in result.charts if c.title.endswith("목표 N 달성률"))
    assert bar.labels[bar.highlight[0]] == "16L"
    assert result.cause and "16L의 C3 통합UI 설비 1대가 v3.52_13에 머물러 있습니다" in result.cause
    assert codes(result, "업그레이드 우선순위 추천") == ["H-003"]
    line = next(c for c in result.charts if "달성률 추이" in c.title)   # 진척이 없으면 '(진척이 없어 통일 예측 불가)' (11/16)
    assert line.title.startswith("16L C3 통합UI") and [name for name, _ in line.series][1] == "전체 평균"


# --- 목록·대수 --------------------------------------------------------------------------
@pytest.mark.parametrize("question, expected", [
    ("평택 P3 목표 N 미만 설비 보여줘", ["DM-027", "DM-029"]),
    ("확인 필요 설비 목록 뽑아줘", ["DM-020", "DM-030", "H-004"]),
    ("목표 달성한 설비 몇 대야?", ["DM-010", "DM-021", "DM-031", "H-001", "H-002"]),
    ("P1, P3 중 O2 설비 보여줘", ["DM-009", "DM-029", "DM-010", "DM-031"]),
    ("v90.1.0.41 쓰는 설비 보여줘", ["DM-009", "DM-029"]),
    ("2026-09-21 기준 평택 P1 미달 설비", ["DM-009", "DM-010"]),
    ("9월 평택 P1 미달 설비 알려줘", ["DM-009", "DM-010"]),
    ("M-O2 현황 보여줘", ["DM-009", "DM-029", "DM-020", "DM-010", "DM-021", "DM-031", "H-001"]),
])
def test_search_questions(question, expected):
    result = ask(question)
    assert Intent.SEARCH in result.request.intents
    assert sorted(codes(result)) == sorted(expected)


def test_memory_area_and_count_question():
    result = ask("화성 메모리 O3 설비 몇 대야?")
    assert result.request.areas == ("MEMORY",)
    assert codes(result) == ["H-004"] and "화성 Memory M-O3 설비는 1대입니다." in result.summary


def test_hpa_is_excluded_and_reported():
    result = ask("화성 13L 현황 보여줘")
    assert codes(result) == ["H-004"] and "HPA 1대는 분석에서 제외했습니다." in result.notices


def test_target_not_set_is_flagged():
    result = ask("O3 현황 보여줘")
    assert any("목표 N이 정해지지 않은 계열" in n for n in result.notices)
    assert all(row[-1].startswith("확인 필요") for row in next(t for t in result.tables).rows)


# --- 비교·변화 --------------------------------------------------------------------------
def test_compare_two_lines():
    result = ask("15L과 16L 달성률 비교해줘")
    assert result.request.intents == (Intent.COMPARE,)
    bar = result.charts[0]
    assert bar.labels == ("15L", "16L") and bar.values == (100.0, 0.0)


def test_compare_sites_when_two_sites_asked():
    result = ask("평택이랑 화성 중 어디가 달성률 낮아?")
    assert result.charts[0].labels == ("평택", "화성")
    assert "가장 낮은 사업장은 평택(50.0%" in result.summary


def test_compare_by_site_keyword():
    assert ask("사업장별 달성률 보여줘").charts[0].labels == ("평택", "화성")


def test_highest_area():
    assert "가장 높은 원본 구역은 15L·P2" in ask("달성률이 가장 높은 구역은?").summary


def test_change_since_last_month():
    result = ask("지난달보다 달성률이 떨어진 라인은?")
    assert result.request.intents == (Intent.CHANGE,)
    assert "2026-09-21보다 달성률이 떨어진 원본 구역은 P2(100.0% → 100.0%)" not in result.summary
    assert "16L(100.0% → 0.0%)" in result.summary and "P2" not in result.summary.split("입니다")[0]
    changed = codes(result, "버전이 바뀐 설비")
    assert changed == ["DM-010", "DM-020", "H-003"]


def test_change_rose():
    assert "P1(0.0% → 50.0%)" in ask("지난달보다 달성률이 올라간 라인은?").summary


# --- 원인·우선순위·예측 -------------------------------------------------------------------
def test_cause_for_one_line():
    result = ask("P3 달성률이 왜 낮아?")
    assert Intent.CAUSE in result.request.intents
    assert "P3의 O2 설비 1대가 v90.1.0.41에 머물러 있습니다" in result.cause
    assert "목표 N 미지정" in result.cause and not result.cause_is_ai


def test_cause_only_when_asked_specifically():
    result = ask("16L 원인 분석해줘")
    assert result.request.intents == (Intent.CAUSE,)


def test_cause_uses_ai_text_only_when_numbers_match():
    from setron.features.ai.schemas import AIResult, AIStatus, AITask, AnalysisAnswer
    from setron.features.analysis.schemas import AnalysisReport

    from setron.infrastructure.ai_client import ResponseSource

    def explainer(question, data):
        return AnalysisReport(AIResult(AITask.ANALYSIS, AIStatus.OK, "", AnalysisAnswer("16L은 C3 통합UI 1대가 미달입니다."),
                                       ResponseSource.REAL), {})

    def wrong(question, data):
        return AnalysisReport(AIResult(AITask.ANALYSIS, AIStatus.OK, "", AnalysisAnswer("99대가 미달입니다."),
                                       ResponseSource.REAL), {}, ("99",))

    def example(question, data):
        return AnalysisReport(AIResult(AITask.ANALYSIS, AIStatus.OK, "", AnalysisAnswer("예제 고정 설명"),
                                       ResponseSource.EXAMPLE), {})

    question = recognize("16L 원인 분석해줘", CATALOG)
    good = answer(question, ChatData(DATA.records, CATALOG, TARGETS, DATA.family_names, explainer=explainer))
    assert good.cause_is_ai and good.cause == "16L은 C3 통합UI 1대가 미달입니다."
    bad = answer(question, ChatData(DATA.records, CATALOG, TARGETS, DATA.family_names, explainer=wrong))
    assert not bad.cause_is_ai and any("자료에 없는 숫자" in n for n in bad.notices)
    fixed = answer(question, ChatData(DATA.records, CATALOG, TARGETS, DATA.family_names, explainer=example))
    assert not fixed.cause_is_ai and "예제 고정 설명" not in fixed.cause          # 예제 응답은 원인으로 쓰지 않는다


def test_priority_without_risk_rules_is_marked():
    result = ask("어디부터 업그레이드해야 해?")
    assert result.request.intents == (Intent.PRIORITY,)
    ranked = codes(result, "업그레이드 우선순위 추천")
    assert set(ranked[:4]) == {"DM-009", "DM-029", "DM-027", "H-003"}
    table = next(t for t in result.tables if t.title == "업그레이드 우선순위 추천")
    unset = [row for row in table.rows if row[1] in ("DM-030", "H-004")]
    assert unset and all(row[4].startswith("목표 N 미지정") and row[5] == "확인 필요" for row in unset)
    assert not {"DM-010", "DM-021", "DM-031", "H-001", "H-002"} & set(ranked)      # 목표 이상은 빼고
    assert any("위험 기준이 없어" in n for n in result.notices)
    assert "달성률은" in result.summary


def test_forecast_is_reference():
    result = ask("C3 통합UI는 언제 통일될까?")
    assert result.request.intents == (Intent.FORECAST,)
    assert "C3 통합UI" in result.summary and any("참고" in n for n in result.notices)
    assert result.charts and result.charts[0].series


def test_forecast_compare_line_is_not_a_copy_of_the_family_line():
    """'C3 통합UI는 언제?'는 범위의 설비가 C3뿐이라 범위 전체 선이 C3 선과 같다 → 모든 설비로 센다 (11-13)."""
    (name, family), (other, whole) = ask("C3 통합UI는 언제 통일될까?").charts[0].series
    assert other == "전체 평균"
    assert whole != tuple(p for p in family if not p.projected)
    # 모든 일반 설비: 9월 6/10, 10월 5/9 (HPA · 목표 없는 O3 · 10월 버전 없는 DM-020은 판단 불가라 뺀다)
    assert [p.rate for p in whole] == [60.0, 55.6]


def _family_forecast(status, *, below=0, undetermined=0, reasons=(), completion=None):
    from setron.features.forecast.schemas import FamilyForecast
    return FamilyForecast(C3, SEP, OCT, 2, 1, 2, below, undetermined, status, completion_date=completion, reasons=reasons)


@pytest.mark.parametrize("status, kwargs, expected", [
    ("COMPLETE", {}, "C3 통합UI는 이미 모든 설비가 목표 N 이상입니다."),
    ("NOT_CONFIRMED", {"undetermined": 2, "reasons": ("UNDETERMINED_LEFT",)},
     "C3 통합UI는 목표 N 미만 설비는 없지만 판단 불가 2대가 남아 통일됐다고 단정할 수 없습니다."),
    ("ESTIMATED", {"below": 3, "completion": date(2027, 1, 19)},
     "현재 속도라면 C3 통합UI 미달 3대가 모두 목표 N에 이르는 시점은 약 2027-01로 예상됩니다 (참고)."),
    ("NOT_ESTIMABLE", {"below": 3, "reasons": ("NO_PROGRESS",)},
     "C3 통합UI는 이 기간에 목표 N 이상 설비가 늘지 않아 통일 시점을 예측할 수 없습니다."),
    ("NOT_ESTIMABLE", {"below": 3, "reasons": ("SINGLE_DATE",)},
     "C3 통합UI는 통일 시점을 예측할 수 없습니다. 이 계열의 현황이 한 날짜뿐이라 예측할 수 없습니다. "
     "날짜가 다른 현황표가 2장 이상 필요합니다."),
])
def test_forecast_sentence_follows_status(status, kwargs, expected):
    """완료(미달 0대)를 '0대가 ○월에 통일'처럼 예측 문장으로 쓰지 않는다 (11-13 멘토 피드백)."""
    from setron.features.chat.answers import _forecast_text
    from setron.features.forecast.schemas import ForecastReason, ForecastStatus
    kwargs = {**kwargs, "reasons": tuple(ForecastReason(r) for r in kwargs.get("reasons", ()))}
    assert _forecast_text("C3 통합UI", _family_forecast(ForecastStatus(status), **kwargs)) == expected


# --- 버전·보고서 ------------------------------------------------------------------------
def test_latest_version_info():
    result = ask("C3 통합UI 최신 버전은?")
    assert Intent.VERSION_INFO in result.request.intents
    assert "C3 통합UI 최신 버전은 v3.52_20(2026-06-01 출시)이며 이 버전을 쓰는 설비는 1대입니다." in result.summary


def test_report_card():
    result = ask("이번 달 표준화 보고서 만들어줘")
    assert result.request.intents == (Intent.REPORT,)
    assert result.report and result.report.title == "SETRON 표준화 보고서 2026-10"
    assert "2026년 10월 표준화 보고서" in result.summary


# --- 이해 못 한 조건·말 ------------------------------------------------------------------
@pytest.mark.parametrize("question, word", [("중국 사업장 O2 현황 알려줘", "중국"), ("O7 설비 몇 대야?", "O7"),
                                            ("평택 P9 미달 설비 보여줘", "P9"), ("화성 18L 현황", "18L"),
                                            ("v9.9.9 쓰는 설비 보여줘", "v9.9.9"), ("3월 평택 현황", "3월")])
def test_unknown_names_are_shown_not_dropped(question, word):
    result = ask(question)
    assert any(word in issue for issue in result.issues)
    # 말한 조건을 못 쓰면 더 넓은 범위로 답하지 않고, 못 찾은 것과 그 종류의 선택지만 짧게 알려 준다 (11/11)
    assert not result.tables and not result.charts
    assert f"'{word}'" in result.summary or result.summary.startswith(f"{word} 자료는 아직 없어요")
    expected = {"중국": "사업장: 평택·화성", "O7": "있는 모델:", "P9": "있는 라인:", "18L": "있는 라인:",
                "v9.9.9": "등록된 릴리즈 버전에서 찾지 못했어요", "3월": "가장 최근은 2026-10-21"}[word]
    assert expected in result.summary


@pytest.mark.parametrize("question", ["안녕", "고마워"])
def test_smalltalk_gets_suggestions(question):
    result = ask(question)
    assert not result.request.understood and result.suggestions


# --- 요청 인식 표현 다양성 ----------------------------------------------------------------
@pytest.mark.parametrize("question, intents", [
    ("평택 미달 설비 리스트", (Intent.SEARCH,)),
    ("화성 O2 몇대 있어", (Intent.SEARCH,)),
    ("라인별 적용률 알려줘", (Intent.COMPARE,)),
    ("표준화율 제일 낮은 데가 어디야", (Intent.COMPARE,)),
    ("전월 대비 변화 보여줘", (Intent.CHANGE,)),
    ("위험 후보 우선순위대로 정리해줘", (Intent.PRIORITY,)),
    ("O2 통일 시점 예측해줘", (Intent.FORECAST,)),
    ("P3가 낮은 이유랑 먼저 올릴 설비 알려줘", (Intent.CAUSE, Intent.PRIORITY)),
    ("리포트 뽑아줘", (Intent.REPORT,)),
])
def test_intent_phrasings(question, intents):
    assert recognize(question, CATALOG).intents == intents


def test_group_by_detection():
    assert recognize("구역별 달성률", CATALOG).group_by is GroupBy.LINE
    assert recognize("사업장별 달성률", CATALOG).group_by is GroupBy.SITE


def test_korean_particles_follow_pronunciation():
    from setron.features.chat.answers import _josa
    assert [_josa(w, "은/는") for w in ("15L", "C3 통합UI", "O2", "16L C3 통합UI", "평택", "확인 필요")] == \
        ["15L은", "C3 통합UI는", "O2는", "16L C3 통합UI는", "평택은", "확인 필요는"]
    assert "16L C3 통합UI는 이 기간에" in ask("목표 N 달성률이 가장 낮은 원본 구역을 분석해줘").summary


# --- 이어지는 질문 ------------------------------------------------------------------------
def follow(first, then):
    previous = recognize(first, CATALOG)
    return answer(recognize(then, CATALOG, previous=previous), DATA)


def test_follow_up_narrows_previous_conditions():
    result = follow("평택 O2 중 목표 N 미만인 설비만 보여줘", "그중에 P3만")
    assert result.request.follow_up and result.understood.startswith("앞 질문 이어서")
    assert codes(result) == ["DM-029"] and "Site = 평택" in result.understood and "원본 구역 = P3" in result.understood


def test_follow_up_switching_site_drops_previous_lines():
    result = follow("평택 P3 목표 N 미만 설비 보여줘", "그럼 화성은?")
    assert result.request.conditions.sites == ("화성",) and result.request.conditions.lines == ()
    assert sorted(codes(result)) == ["H-003"]


def test_follow_up_can_change_the_request_kind():
    result = follow("목표 N 달성률이 가장 낮은 원본 구역을 분석해줘", "그럼 어디부터 업그레이드해?")
    assert result.request.follow_up and result.request.intents == (Intent.PRIORITY,)


def test_new_full_question_does_not_inherit():
    previous = recognize("평택 P3 목표 N 미만 설비 보여줘", CATALOG)
    fresh = recognize("15L과 16L 달성률 비교해줘", CATALOG, previous=previous)
    assert not fresh.follow_up and fresh.conditions.sites == ()


def test_short_condition_only_question_follows_previous():
    result = follow("평택 O2 중 목표 N 미만인 설비만 보여줘", "P1만")
    assert result.request.follow_up and codes(result) == ["DM-009"]


# --- 요약 다듬기 --------------------------------------------------------------------------
class _Client:
    def __init__(self, text, source=None):
        from setron.infrastructure.ai_client import ResponseSource
        self.text, self.source = text, source or ResponseSource.REAL

    def complete(self, messages):
        from setron.infrastructure.ai_client import ChatReply
        return ChatReply(self.text, self.source)


def test_polish_keeps_numbers_or_falls_back():
    from setron.features.chat import polish
    from setron.infrastructure.ai_client import ResponseSource
    base = ask("평택 O2 중 목표 N 미만인 설비만 보여줘")
    good = polish(base, _Client("평택 M-O2 설비 6대 가운데 목표 N에 못 미친 설비는 2대로, P1 1대 · P3 1대입니다."))
    assert good.polished and "가운데" in good.summary
    for wrong in ("평택 M-O2 설비 6대 중 3대가 미달입니다. (P1 1대 · P3 1대)",       # 숫자 바뀜
                  "평택 M-O2 설비 중 미달은 2대입니다.",                              # 숫자 빠짐
                  ""):
        assert polish(base, _Client(wrong)) == base
    assert polish(base, _Client(good.summary, ResponseSource.EXAMPLE)) == base     # 예제 응답은 쓰지 않음
    assert polish(base, None) == base


def test_priority_puts_dashboard_risk_first():
    from setron.features.risk.schemas import RiskStatus
    data = ChatData(DATA.records, CATALOG, TARGETS, DATA.family_names, risks={11: RiskStatus.RISK})
    result = answer(recognize("어디부터 업그레이드해야 해?", CATALOG), data)
    table = next(t for t in result.tables if t.title == "업그레이드 우선순위 추천")
    assert table.rows[0][1] == "H-003" and table.rows[0][5] == "높음" and table.rows[0][4].startswith("위험 후보")
    assert not any("위험 기준이 없어" in n for n in result.notices)


def test_records_without_lines_compare_by_site_and_say_unknown_area():
    from dataclasses import replace as dc_replace
    from setron.features.chat import ChatData
    records = tuple(dc_replace(r, line=None) for r in DATA.records)
    data = ChatData(records, CATALOG, TARGETS, DATA.family_names)
    result = answer(recognize("달성률이 가장 낮은 원본 구역은?", CATALOG), data)
    assert result.charts[0].labels == ("평택", "화성") and "-" not in result.summary


def test_line_spread_is_skipped_when_records_have_no_lines():
    from dataclasses import replace as dc_replace
    from setron.features.chat import ChatData
    data = ChatData(tuple(dc_replace(r, line=None) for r in DATA.records), CATALOG, TARGETS, DATA.family_names)
    result = answer(recognize("평택 목표 N 미만 설비 보여줘", CATALOG), data)
    assert result.summary.endswith("입니다.") and "(-" not in result.summary


# --- 11/09: 기준일까지 설비별 최신 관측 (대시보드 current_records와 같은 규칙, QA Q-01) ---------------------------
def _hwaseong_only_in_september():
    """평택은 9·10월, 화성은 9월에만 현황표를 넣은 경우 (사업장별로 다른 날 들어오는 실제 상황)."""
    from setron.features.chat import ChatData
    records = tuple(r for r in DATA.records if not (r.site == "화성" and r.base_date == OCT))
    return ChatData(records, CATALOG, TARGETS, DATA.family_names)


def test_latest_observation_per_equipment_is_used_like_the_dashboard():
    from setron.features.chat.answers import latest_until
    data = _hwaseong_only_in_september()
    result = answer(recognize("화성 현황 보여줘", CATALOG), data)
    assert result.base_date == OCT                                         # 기준일은 최신 관측일
    assert sorted(codes(result)) == ["H-001", "H-002", "H-003", "H-004"]   # 9월 관측으로 화성 설비가 그대로 보임
    assert "HPA 1대는 분석에서 제외했습니다." in result.notices
    current = latest_until(data.records, OCT)
    assert len(current) == 13 and {r.base_date for r in current if r.site == "화성"} == {SEP}


def test_site_compare_includes_sites_saved_on_earlier_dates():
    result = answer(recognize("평택이랑 화성 중 어디가 달성률 낮아?", CATALOG), _hwaseong_only_in_september())
    assert result.charts[0].labels == ("평택", "화성")
    # 화성 9월: 15L 2대 달성 · 16L H-003 v3.52_20 달성 (9월엔 아직 내려가지 않음) → 화성 100%
    assert "가장 낮은 사업장은 평택(50.0%" in result.summary
