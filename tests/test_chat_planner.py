"""AI가 먼저 계획하고 규칙이 검사·대체한다 (11/11). 숫자는 언제나 DB 계산.

11/11 시연 DB에서 이상하게 답하던 질문들:
- '가장 마지막 데이터가 언제야?' → 통일 예측으로 답함  (→ 자료 안내)
- '어떠어떠한 보고서 만들 수 있어?' → 10월 보고서를 바로 만듦  (→ 자료 안내: 만들 수 있는 보고서)
- '현재일 기준으로 일주일치 보고서' → 말없이 월간 보고서  (→ 지원하지 않음 + 대신할 것)
- '11월 보고서도 만들 수 있어?' → 자료 목록을 줄줄이  (→ 11월 자료 없음 + 가장 최근 + 10월 보고서 버튼)
"""

from datetime import date

from setron.features.chat import Intent, recognize
from setron.features.chat.answers import UNSUPPORTED_TITLE
from setron.features.chat.classify import CLARIFY_TITLE, plan
from test_chat_phrasing import FakeClient, Service, _classifier_calls
from test_chat_questions import CATALOG, OCT, SEP


def test_last_data_question_is_data_overview_not_forecast():
    result = Service(FakeClient({"tool": "data_overview"})).ask("가장 마지막 데이터가 언제야?")
    assert result.request.intents == (Intent.DATA_INFO,)
    assert result.summary.startswith(f"가장 최근 자료는 {OCT.isoformat()}이에요.")
    assert [t.title for t in result.tables] == ["저장된 기준일"]           # 날짜를 물으면 날짜만 (11/11)
    assert not result.charts                                               # 통일 예측 그래프가 아니다


def test_which_reports_question_lists_reports_instead_of_making_one():
    result = Service(FakeClient({"tool": "data_overview", "topic": "reports"})).ask("그럼 어떠어떠한 보고서 만들 수 있어?")
    assert result.report is None and result.title == "자료 안내"
    assert result.summary.startswith("만들 수 있는 보고서는 5가지예요.")      # 묻는 것에 바로 답한다 (11/11)
    assert [t.title for t in result.tables] == ["만들 수 있는 보고서"]
    reports = result.tables[0]
    assert reports.rows[0][0] == "월간 표준화 보고서" and "(9월~10월)" in reports.rows[0][2]
    assert result.actions == (("보고서 메뉴 열기", "reports"),)


def test_weekly_report_is_unsupported_not_silently_monthly():
    result = Service(FakeClient({"tool": "unsupported", "unsupported": "주간 보고서"})).ask(
        "현재일 기준으로 일주일치 보고서 만들 수 있어?")
    assert result.title == UNSUPPORTED_TITLE and result.report is None
    assert result.summary.startswith("주간 보고서는 아직 만들 수 없어요.")
    assert result.suggestions[0] == "10월 보고서 만들어줘"


def test_month_without_data_says_so_briefly_with_next_step():
    result = Service(FakeClient({"tool": "report", "date": "2026-11"})).ask("11월 보고서도 만들 수 있어?")
    assert result.summary.startswith("11월 자료는 아직 없어요.") and f"가장 최근은 {OCT.isoformat()}" in result.summary
    assert "라인" not in result.summary and result.suggestions[0] == "10월 보고서 만들어줘"
    assert result.request.understood                                       # 빨간 '조건 인식 실패'가 아니다


def test_month_with_data_makes_that_month_report():
    result = Service(FakeClient({"tool": "report", "date": "2026-09"})).ask("9월치로 보고서 하나 뽑아 줄래?")
    assert result.report is not None and result.report.title == "SETRON 표준화 보고서 2026-09"
    # '보고서 + 9월'이 분명하면 규칙이 바로 답한다 (AI 계획을 거치지 않음, 11/11)
    assert not any(n.startswith("질문을 ‘") for n in result.notices)


def test_ambiguous_question_gets_a_clarifying_question_with_options():
    options = ["C3 통합UI는 언제 통일될까?", "가장 최근 자료 알려줘"]
    result = Service(FakeClient({"tool": "clarify", "clarify": "통일 시점을 물으신 건가요, 자료 날짜를 물으신 건가요?",
                                 "options": options})).ask("언제야?")
    assert result.title == CLARIFY_TITLE and result.summary.endswith("물으신 건가요?")
    assert list(result.suggestions) == options and not result.tables


def test_follow_up_plan_keeps_previous_conditions():
    service = Service(FakeClient({"tool": "search", "names": ["평택"], "verdict": "BELOW"}))
    first = service.ask("평택 쪽 업데이트 안 된 장비 좀")
    assert first.request.conditions.sites == ("평택",)
    service.client = FakeClient({"tool": "search", "names": ["P3"], "follow_up": True})
    second = service.ask("거기서 P3만 볼래", previous=first.request)
    assert second.request.conditions.sites == ("평택",) and second.request.conditions.lines == ("P3",)


def test_ai_failure_falls_back_to_rules_which_also_handle_these_questions():
    failing = FakeClient(error=RuntimeError("AI 연결 실패"))
    last = Service(failing).ask("가장 마지막 데이터가 언제야?")
    assert last.request.intents == (Intent.DATA_INFO,) and not last.charts
    weekly = Service(failing).ask("현재일 기준으로 일주일치 보고서 만들 수 있어?")
    assert weekly.title == UNSUPPORTED_TITLE
    which = Service(None).ask("그럼 어떠어떠한 보고서 만들 수 있어?")
    assert which.request.intents == (Intent.DATA_INFO,)
    assert Service(None).ask("C3 통합UI는 언제 통일될까?").request.intents == (Intent.FORECAST,)


def test_plan_prompt_lists_dates_today_and_previous_and_rejects_unknown_tools():
    client = FakeClient({"tool": "dance"})
    previous = recognize("평택 현황 보여줘", CATALOG)
    assert plan(client, "아무 질문", CATALOG, previous=previous, today=date(2026, 11, 11)) is None   # 모르는 도구
    system = client.calls[0][0].content
    assert SEP.isoformat() in system and OCT.isoformat() in system and "오늘 날짜: 2026-11-11" in system
    assert "'평택 현황 보여줘'" in system and "질문 분류기" in system and "data_overview" in system



# --- 11/11: 여러 달 보고서는 보고서 메뉴로, '각각'은 달마다 버튼, 주간·일별만 지원하지 않음 -------------------------
from setron.features.chat import answer                                       # noqa: E402
from setron.features.chat.answers import PERIOD_REPORTS, PERIOD_TITLE          # noqa: E402
from test_chat_questions import DATA                                           # noqa: E402


def test_period_report_points_to_report_menu_with_prefilled_period():
    result = answer(recognize("9월부터 10월 보고서는 만들 수 있어?", CATALOG), DATA)
    assert result.title == PERIOD_TITLE and result.report is None
    assert result.actions == (("버전 표준화 리포트 만들기", f"reports:status:{SEP}:{OCT}"),
                              ("설비별 변경 이력 리포트 만들기", f"reports:change:{SEP}:{OCT}"))
    assert "보고서 메뉴에서 만들 수 있어요" in result.summary and result.suggestions == ("9월 보고서 만들어줘", "10월 보고서 만들어줘")


def test_each_month_gets_a_button_per_month_and_missing_months_are_honest():
    each = answer(recognize("9월이랑 10월 보고서 각각 만들어줘", CATALOG), DATA)
    assert each.suggestions == ("9월 보고서 만들어줘", "10월 보고서 만들어줘") and not each.actions
    quarter = answer(recognize("3분기 보고서 만들어줘", CATALOG), DATA)
    assert "7월~9월 중 자료가 있는 달은 9월뿐이에요" in quarter.summary
    empty = answer(recognize("1월부터 3월 보고서", CATALOG), DATA)
    assert empty.summary.startswith("1월~3월 자료는 아직 없어요.")


def test_integrated_report_is_available_through_the_report_menu():
    rules = answer(recognize("통합보고서는 만들 수 있어?", CATALOG), DATA)
    assert rules.title == PERIOD_TITLE and rules.summary.startswith("여러 달을 묶은 통합 보고서는 보고서 메뉴에서")
    # AI가 '지원하지 않음'으로 판단해도 여러 달 보고서는 있는 기능으로 바로잡는다
    planned = Service(FakeClient({"tool": "unsupported", "unsupported": "5월~10월 통합 보고서"})).ask("5월부터 10월 보고서는 만들수있어?")
    assert planned.title == PERIOD_TITLE and planned.actions
    ranged = Service(FakeClient({"tool": "report", "date_range": ["2026-09", "2026-10"]})).ask("9~10월치 묶어서")
    assert ranged.title == PERIOD_TITLE
    listed = Service(FakeClient({"tool": "report", "months": ["2026-09", "2026-10"], "each_month": True})).ask("9월 10월 각각")
    assert listed.suggestions == ("9월 보고서 만들어줘", "10월 보고서 만들어줘")


def test_weekly_is_unsupported_with_the_real_reason():
    result = answer(recognize("이번 주 보고서 만들어줘", CATALOG), DATA)
    assert result.title == UNSUPPORTED_TITLE and "한 달 간격으로 저장돼 그보다 짧은 기간의 자료가 없어요" in result.summary


def test_period_report_names_match_the_report_menu():
    from setron.ui.report_data import REPORT_TYPES
    titles = {key: title for key, _, title, *_ in REPORT_TYPES}
    assert all(titles.get(key) == name for key, name, _ in PERIOD_REPORTS)


def test_report_with_explicit_months_skips_the_planner():
    """'5월부터 10월까지 보고서 만들 수 있어?'를 AI가 자료 안내로 계획해도 규칙이 정확하므로 규칙을 따른다 (11/11)."""
    client = FakeClient({"tool": "data_overview"})                          # 잘못된 계획
    result = Service(client).ask("9월부터 10월까지 보고서 만들 수 있어?")
    assert result.title == PERIOD_TITLE and result.actions and _classifier_calls(client) == []
    assert Service(client).ask("9월 보고서 만들어줘").report is not None and _classifier_calls(client) == []


def test_answer_suggestions_are_answered_by_rules_but_clarify_options_are_not():
    client = FakeClient({"tool": "data_overview", "topic": "reports"})
    service = Service(client)
    first = service.ask("그럼 어떠어떠한 보고서 만들 수 있어?")
    assert len(_classifier_calls(client)) == 1
    follow = first.suggestions[-1]                                           # 답에 넣은 추천 질문
    service.client = FakeClient({"tool": "data_overview"})
    service.ask(follow)
    assert _classifier_calls(service.client) == []                         # 규칙으로 바로
    service.client = FakeClient({"tool": "clarify", "clarify": "어느 쪽인가요?", "options": ["둘 중 하나 질문"]})
    service.ask("언제야?")
    service.client = FakeClient({"tool": "search"})
    service.ask("둘 중 하나 질문")
    assert len(_classifier_calls(service.client)) == 1                     # 확인 질문 선택지는 AI가 다시 계획
