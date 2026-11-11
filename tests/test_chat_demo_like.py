"""시연 DB와 같은 자료(설비 300대 · 기준일 6개 · 실제 이름)로 AI 분석을 점검한다 (11/11 자체 QA).

멘토 점검 목록:
- '평택에서 가장 파편화된 라인은?' · 'C3의 V1.2.1.4를 기준 버전으로 봤을 때 최신화율은?'
- 사업장 · 라인 · 설비군 조건이 집계에 반영되는지, 답의 숫자가 같은 조건의 DB 집계와 같은지
- 자료나 지원 규칙이 없는 질문의 안내
사람마다 말투가 달라도(띄어쓰기 · 영문 · 별칭 · 조사 · 이어 묻기) 같은 답이 나오는지 함께 본다.
"""

import re
from dataclasses import replace

import pytest

from demo_like_data import CATALOG, DATA
from setron.features.aggregation.service import summarize
from setron.features.chat import Intent, answer, recognize
from setron.features.chat.answers import PERIOD_TITLE, UNSUPPORTED_TITLE, latest_until

OCT = max(CATALOG.dates)


def ask(question, previous=None):
    request = recognize(question, CATALOG, previous=previous)
    return answer(request, DATA)


def current(**cond):
    rows = latest_until(DATA.records, OCT)
    rows = [r for r in rows if not r.is_hpa and all(getattr(r, key) == value for key, value in cond.items())]
    return rows


# --- 멘토 질문 ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("question", [
    "평택에서 가장 파편화된 라인은?", "평택 라인 중에 버전이 제일 흩어진 데가 어디야?", "평택 어느 라인이 제일 파편화됐어?",
])
def test_most_fragmented_line_in_pyeongtaek_any_phrasing(question):
    result = ask(question)
    assert result.request.aggregate == ("fragmentation", "line", "") and result.request.conditions.sites == ("평택",)
    # DFB는 C4와 따로 센다(11/12 멘토 피드백 2) → P1F의 대표는 O5(8대 · 8가지)
    assert result.tables[0].rows[0][1] == "평택 P1F" and "O5 설비 8대가 8가지 버전" in result.summary


@pytest.mark.parametrize("model, label", [("M-C4", "C4 (M-C4)"), ("M-C4 DFB", "C4 (M-C4 DFB)")])
def test_fragmentation_index_is_computed_within_a_family_and_group(model, label):
    """파편화 지수 = 같은 계열 · 같은 설비군에서 설비 두 대의 버전이 다를 확률. DFB는 C4 노트를 쓰지만 버전 줄기가
    달라 독립 설비군으로 따로 센다(11/12 멘토 피드백 2). 평택 P1F 손 계산과 같아야 한다."""
    from collections import Counter
    fid = next(f.family_id for f in CATALOG.families if f.name == "C4")
    versions = [p.version_raw for r in current(site="평택", line="P1F", model_name=model) for p in r.pms if p.family_id == fid]
    counts = Counter(versions)
    index = 1 - sum((n / len(versions)) ** 2 for n in counts.values())
    detail = next(t for t in ask("평택에서 가장 파편화된 라인은?").tables if t.title == "계열별 버전 분포")
    row = next(r for r in detail.rows if r[0] == "평택 P1F" and r[1] == label)
    assert row[2] == str(len(versions)) and row[3] == str(len(counts)) and row[6] == f"{index:.2f}"
    assert not any(r[1] == "C4" for r in detail.rows if r[0] == "평택 P1F")          # C4와 DFB를 섞은 줄은 없다


def test_unknown_baseline_version_lists_every_family_of_the_model():
    result = ask("C3의 V1.2.1.4를 기준 버전으로 봤을 때 최신화율은?")
    assert result.request.conditions.lines == ()                       # 'V1.2.1.4'의 V1은 V1 라인이 아니다
    assert result.summary.startswith("C3 구UI · C3 통합UI · C3 통합UI (파운드리)에 'V1.2.1.4' 버전은 등록돼 있지 않아")
    assert "C3 구UI: v1.1.1.6D_2" in result.summary and "C3 통합UI: v3.53_1" in result.summary


@pytest.mark.parametrize("question", [
    "C3 통합UI v3.52_22 기준으로 라인별 최신화율", "C3 통합UI를 v3.52_22 기준으로 보면 라인별 최신화율은?",
    "v3.52_22를 M 버전이라고 하면 C3 통합UI 라인별로 얼마나 올라왔어?",
])
def test_baseline_rate_any_phrasing(question):
    result = ask(question)
    assert result.request.aggregate == ("baseline", "line", "v3.52_22")
    assert "최신화율은 30.2%예요 (기준 이상 13대 · 미만 30대" in result.summary


def test_baseline_rate_at_saved_target_equals_dashboard_for_every_family():
    for (gid, fid), rid in sorted(DATA.targets.items()):
        model = next(g.model_name for g in CATALOG.groups if g.group_id == gid)
        release = CATALOG.releases[fid].get(rid)
        rows = [replace(r, pms=tuple(p for p in r.pms if p.family_id == fid))
                for r in current(model_name=model) if any(p.family_id == fid for p in r.pms)]
        dashboard = summarize(rows, CATALOG.releases, DATA.targets)
        result = ask(f"{model} {release.version_raw} 기준 최신화율")
        assert f"최신화율은 {dashboard.rate * 100:.1f}%예요 (기준 이상 {dashboard.compliant}대 · 미만 {dashboard.below}대" \
            in result.summary, (model, release.version_raw)


# --- 조건 반영 · 숫자 일치 -----------------------------------------------------------------------------------
@pytest.mark.parametrize("question, cond", [
    ("평택 설비 몇 대야?", dict(site="평택")), ("화성 15L 설비 몇 대야?", dict(site="화성", line="15L")),
    ("평택 P1F C4 설비 몇 대야?", dict(site="평택", line="P1F", model_name="M-C4")),
    ("평택P1F설비몇대", dict(site="평택", line="P1F")), ("pyeongtaek C4 status", dict(site="평택", model_name="M-C4")),
    ("화성 MC4 현황", dict(site="화성", model_name="M-C4")), ("화성 씨4 현황", dict(site="화성", model_name="M-C4")),
    ("평택 V2 설비 몇 대야?", dict(site="평택", line="V2")), ("화성 오투 현황", dict(site="화성", model_name="M-O2")),
])
def test_conditions_and_counts_match_direct_aggregation(question, cond):
    result = ask(question)
    rows = current(**cond)
    expected = summarize(rows, CATALOG.releases, DATA.targets)
    assert re.search(rf"설비는 {len(rows)}대", result.summary), result.summary
    assert f"미만 {expected.below}건" in result.summary


def test_region_inside_line_names_is_a_line_filter():
    result = ask("기흥 설비 몇 대야?")
    assert set(result.request.conditions.lines) == {"1L (기흥)", "5L (기흥)", "S1 (기흥)"}
    assert result.request.conditions.unresolved == ()


def test_line_rates_match_direct_aggregation():
    result = ask("평택 라인별 달성률 비교")
    rows = {row[0]: row for row in result.tables[0].rows}
    for line in sorted({r.line for r in current(site="평택") if r.line}):
        expected = summarize(current(site="평택", line=line), CATALOG.releases, DATA.targets)
        assert rows[line][2] == str(expected.compliant) and rows[line][3] == str(expected.below), line


def test_trend_by_date_and_relative_months():
    trend = ask("평택 C4 월별 달성률")
    assert trend.request.aggregate == ("achievement", "date", "") and trend.tables[0].title == "기준일별 목표 N 달성률"
    assert len(trend.tables[0].rows) == 6 and "달성률 추이: 2026-05-21" in trend.summary
    assert recognize("두 달 전 평택 달성률", CATALOG).conditions.base_date == "2026-08-21"
    assert recognize("지난달 평택 현황", CATALOG).intents == (Intent.SEARCH,)
    assert recognize("지난달 평택 현황", CATALOG).conditions.base_date == "2026-09-21"
    assert recognize("지난달보다 떨어진 라인", CATALOG).intents == (Intent.CHANGE,)


# --- 자료 · 지원 규칙이 없는 질문 -----------------------------------------------------------------------------
@pytest.mark.parametrize("question, expected", [
    ("2025년 10월 현황", "2025년 10월 자료는 아직 없어요"), ("2024년 현황", "2024년 자료는 아직 없어요"),
    ("평택 P9 라인 현황", "'P9'은(는) 지금 자료에 없는 라인이에요"), ("중국 공장 현황", "'중국'은(는) 지금 자료에서 찾지 못했어요"),
    ("설비 ESPA02 현황", "설비 ESPA02은(는) 저장된 현황에 없어요"), ("C4 DFB 평택 V2 라인 버전 몇 가지야?", "조건에 맞는 설비가 없어요"),
])
def test_missing_data_is_explained_briefly(question, expected):
    assert expected in ask(question).summary


@pytest.mark.parametrize("question, label", [
    ("담당자가 누구야?", "담당자 정보"), ("공정별 버전 분포 보여줘", "공정 정보"), ("엔지니어가 왜 업데이트 안 했어?", "담당자 정보"),
    ("위험한 설비 보여줘", "위험 후보 거르기"), ("주간 보고서 만들어줘", "주간 보고서"),
])
def test_unsupported_information_is_declined_honestly(question, label):
    result = ask(question)
    assert result.title == UNSUPPORTED_TITLE and result.request.unsupported == label


def test_off_topic_is_not_an_analysis_request_even_without_ai():
    for question in ("오늘 날씨 어때?", "SW 버전 관리 잘하는 법 알려줘", "점심 뭐 먹지"):
        assert not recognize(question, CATALOG).understood, question


def test_tier_words_fall_back_to_above_below_with_a_note():
    result = ask("화성 N-2 이하 설비 보여줘")
    assert result.request.verdict is not None and "목표 N 미만은" in result.summary
    assert any(n.startswith("N+1 · N-1 · N-2 같은 단계") for n in result.notices)


def test_equipment_code_questions():
    one = ask("DEMO-0121 현황")
    assert one.request.codes == ("DEMO-0121",) and "DEMO-0121 설비는 1대입니다" in one.summary
    assert recognize("ESOC6 현황", CATALOG).codes == ("ESOC6",)                   # 숫자 하나짜리 호기
    assert recognize("XMO3 현황", CATALOG).conditions.groups == ("XMO3",)        # 모델은 호기가 아니다


# --- 이어 묻기 -----------------------------------------------------------------------------------------------
def test_follow_up_chain_keeps_context_and_new_aggregate_questions_do_not():
    first = ask("평택 C4 목표 N 미만 설비 보여줘")
    hwaseong = ask("그럼 화성은?", first.request)
    assert hwaseong.request.conditions.sites == ("화성",) and hwaseong.request.conditions.groups == ("C4",)
    why = ask("왜 그래?", hwaseong.request)
    assert Intent.CAUSE in why.request.intents and why.request.conditions.sites == ("화성",)
    change = ask("지난달이랑 비교하면?", why.request)
    assert change.request.intents == (Intent.CHANGE,) and change.request.follow_up
    excel = ask("이 결과 Excel로 뽑아줘", change.request)
    assert excel.request.follow_up and excel.request.conditions.sites == ("화성",)
    fresh = ask("평택에서 가장 파편화된 라인은?", excel.request)
    assert not fresh.request.follow_up and fresh.request.conditions.groups == ()
    narrowed = ask("그중에 C4만", fresh.request)
    assert narrowed.request.follow_up and narrowed.request.aggregate == ("fragmentation", "line", "")
    assert "평택 M-C4에서 가장 파편화된 라인은(는) 평택 P1F" in narrowed.summary


def test_period_and_data_info_questions_still_work_on_demo_like_data():
    assert ask("5월부터 7월 보고서는 만들 수 있어?").title == PERIOD_TITLE
    info = ask("가장 마지막 데이터가 언제야?")
    assert info.request.intents == (Intent.DATA_INFO,) and "2026-10-21" in info.summary
    assert recognize("최근 자료 기준 평택 달성률", CATALOG).intents == (Intent.COMPARE,)
