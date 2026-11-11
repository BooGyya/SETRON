"""AI 분석 범용 집계 (11/11 멘토링: 라인 × 설비군의 최신화율 · 파편화).

테스트 자료(test_chat_questions)로 손 계산한 값:
- 평택 O2: P1 = .41·.43 (지수 0.5), P2 = .43 + 버전 없음 (지수 0), P3 = O2 .41·.43 + C3 1 + O3 1 → (2×0.5 + 0 + 0)/4 = 0.25
- C3 통합UI(3대: P3 v3.52_13 · 15L v3.52_20 · 16L v3.52_13): v3.52_20 기준 → 1/3, v3.52_13 기준 → 3/3
"""

from setron.features.aggregation.service import summarize
from setron.features.chat import Intent, answer, recognize
from setron.features.chat.answers import latest_until
from test_chat_phrasing import FakeClient, Service
from test_chat_questions import CATALOG, DATA, OCT


def ask(question):
    return answer(recognize(question, CATALOG), DATA)


def test_most_fragmented_line_in_pyeongtaek():
    result = ask("평택에서 가장 파편화된 라인은?")
    assert result.request.intents == (Intent.AGGREGATE,) and result.request.aggregate == ("fragmentation", "line", "")
    ranking = result.tables[0]
    assert [(r[1], r[3]) for r in ranking.rows] == [("평택 P1", "0.50"), ("평택 P3", "0.25"), ("평택 P2", "0.00")]
    assert result.summary.startswith("평택에서 가장 파편화된 라인은(는) 평택 P1이에요. O2 설비 2대가 2가지 버전으로")
    assert any("버전이 비어 있는 설비 1대" in n for n in result.notices)          # 모르는 버전을 한 종류로 세지 않음
    assert result.charts and result.charts[0].values[0] == 50.0


def test_version_kinds_and_counts_per_version():
    kinds = ask("평택 O2 라인별 버전 몇 가지야?")
    assert kinds.request.aggregate[0] == "versions" and kinds.summary.endswith("(O2 2가지).")
    counts = ask("평택 O2 버전별로 몇 대씩이야?")
    assert counts.tables[0].rows == (("O2 v90.1.0.43", "3대", "50.0%"), ("O2 v90.1.0.41", "2대", "33.3%"),
                                     ("O2 버전 없음", "1대", "16.7%"))


def test_baseline_version_freshness_rate():
    result = ask("C3의 v3.52_20을 기준 버전으로 봤을 때 최신화율은?")
    assert result.request.aggregate == ("baseline", "", "v3.52_20")
    assert result.summary.startswith("C3 통합UI v3.52_20을(를) 기준으로 보면 M-C3 최신화율은 33.3%예요 (기준 이상 1대 · 미만 2대")
    assert result.tables[0].rows == (("평택 P3", "1", "0", "1", "0", "0.0%"), ("화성 15L", "1", "1", "0", "0", "100.0%"),
                                     ("화성 16L", "1", "0", "1", "0", "0.0%"))
    equal = ask("C3 v3.52_13 기준으로 라인별 최신화율")
    assert "최신화율은 100.0%예요" in equal.summary and equal.summary.endswith("라인별로 모두 같아요.")


def test_unknown_baseline_version_is_honest():
    result = ask("C3의 V1.2.1.4를 M 버전이라고 했을 때 최신화율은?")
    assert result.summary == "C3 통합UI에 'V1.2.1.4' 버전은 등록돼 있지 않아 기준으로 쓸 수 없어요. 최근 버전 — C3 통합UI: v3.52_20, v3.52_13."
    assert not result.tables and result.request.conditions.lines == ()       # 'V1'을 라인으로 읽지 않음


def test_baseline_at_saved_target_matches_dashboard_numbers():
    """질문의 기준 버전을 저장된 목표 N(C3 = v3.52_20)과 같게 주면 대시보드 집계와 숫자가 같아야 한다."""
    on_base = [r for r in latest_until(DATA.records, OCT) if r.model_name == "M-C3" and not r.is_hpa]
    dashboard = summarize(on_base, DATA.catalog.releases, DATA.targets)
    result = ask("C3 v3.52_20 기준 최신화율")
    assert f"최신화율은 {dashboard.rate * 100:.1f}%예요" in result.summary
    assert f"(기준 이상 {dashboard.compliant}대 · 미만 {dashboard.below}대" in result.summary


def test_version_with_korean_particle_is_found():
    result = ask("v3.52_20을 쓰는 설비 보여줘")
    assert result.request.conditions.versions == ("v3.52_20",) and result.tables[0].rows[0][0] == "H-002"


def test_ai_plans_aggregate_with_any_phrasing():
    fragment = Service(FakeClient({"tool": "aggregate", "measure": "fragmentation", "by": "line", "names": ["평택"]}))
    assert fragment.ask("평택 쪽 라인들 버전 제일 엉망인 데?").tables[0].rows[0][1] == "평택 P1"
    baseline = Service(FakeClient({"tool": "aggregate", "measure": "baseline", "baseline": "v3.52_20", "by": "line",
                                   "names": ["C3"]}))
    assert "최신화율은 33.3%예요" in baseline.ask("C3 v3.52_20 기준으로 라인마다 얼마나 올라왔어?").summary
    counts = Service(FakeClient({"tool": "aggregate", "measure": "count", "by": "version", "names": ["평택", "M-O2"]}))
    assert counts.ask("평택 O2 버전 분포 좀").tables[0].rows[0] == ("O2 v90.1.0.43", "3대", "50.0%")
    unknown = Service(FakeClient({"tool": "aggregate", "measure": "baseline", "baseline": "V1.2.1.4", "names": ["C3"]}))
    assert "등록돼 있지 않아" in unknown.ask("C3의 V1.2.1.4가 M 버전이라고 했을 때 최신화율").summary


def test_clear_aggregate_questions_follow_rules_even_if_ai_plans_otherwise():
    """'버전 + 기준 + 최신화율'·'파편화'는 규칙이 정확하다. AI가 달성률 비교로 계획해도 기준 버전을 무시하지 않는다."""
    wrong = FakeClient({"tool": "compare", "names": ["C3"]})
    service = Service(wrong)
    unknown = service.ask("C3의 V1.2.1.4를 M 버전이라고 했을 때 최신화율은?")
    assert unknown.summary.startswith("C3 통합UI에 'V1.2.1.4' 버전은 등록돼 있지 않아")
    known = service.ask("C3의 v3.52_20을 기준 버전으로 봤을 때 최신화율은?")
    assert "최신화율은 33.3%예요" in known.summary
    assert service.ask("평택에서 가장 파편화된 라인은?").tables[0].rows[0][1] == "평택 P1"
    assert [m for m in wrong.calls if "질문 분류기" in m[0].content] == []        # AI 계획을 부르지 않음


def test_aggregate_question_after_another_answer_is_not_a_follow_up():
    """집계 답 다음에 '평택에서 가장 파편화된 라인은?'을 물으면 앞 질문의 조건(C3)을 이어받지 않는다 (11/11 시연 DB).
    '그중에 P3만'처럼 진짜 이어 묻기는 앞 집계(무엇을 셀지 · 기준 버전)를 그대로 이어받는다."""
    first = recognize("C3의 V1.2.1.4를 M 버전이라고 했을 때 최신화율은?", CATALOG)
    second = recognize("평택에서 가장 파편화된 라인은?", CATALOG, previous=first)
    assert not second.follow_up and second.aggregate == ("fragmentation", "line", "")
    assert second.conditions.groups == () and second.conditions.sites == ("평택",)
    assert answer(second, DATA).tables[0].rows[0][1] == "평택 P1"
    base = recognize("C3의 v3.52_20을 기준 버전으로 봤을 때 최신화율은?", CATALOG)
    narrowed = recognize("그중에 화성만", CATALOG, previous=base)
    assert narrowed.follow_up and narrowed.aggregate == ("baseline", "", "v3.52_20")
    assert answer(narrowed, DATA).summary.startswith("C3 통합UI v3.52_20을(를) 기준으로 보면 화성 M-C3 최신화율은 50.0%예요")


def test_version_text_is_not_read_as_a_line_name():
    """시연 DB에는 V1 · V2 라인이 있다. 'V1.2.1.4'의 V1을 V1 라인으로 읽으면 'C3 중 V1 라인' → 0대가 된다 (11/11)."""
    from dataclasses import replace as swap
    catalog = swap(CATALOG, lines=(*CATALOG.lines, "V1", "V2"))
    request = recognize("C3의 V1.2.1.4를 M 버전이라고 했을 때 최신화율은?", catalog)
    assert request.conditions.lines == () and request.aggregate == ("baseline", "", "V1.2.1.4")
    assert answer(request, DATA).summary.startswith("C3 통합UI에 'V1.2.1.4' 버전은 등록돼 있지 않아")
    assert recognize("평택 V1 라인 현황", catalog).conditions.lines == ("V1",)        # 진짜 라인 이름은 그대로


def test_line_named_like_a_version_is_only_a_line():
    """시연 DB의 V2 라인: '평택 V2 라인 현황'의 V2를 버전으로도 읽어 '등록된 버전에서 V2를 못 찾았어요'가 나오던 문제."""
    from dataclasses import replace as swap
    catalog = swap(CATALOG, lines=(*CATALOG.lines, "V1", "V2"))
    request = recognize("평택 V2 라인 현황 보여줘", catalog)
    assert request.conditions.lines == ("V2",) and request.conditions.unresolved == () and request.conditions.versions == ()
    assert recognize("V2.0.1 쓰는 설비", catalog).conditions.lines == ()            # 점이 이어지면 버전
