"""AI 분석 답 카드: 참고 문장은 '판단 근거 N건'으로 접고, 못 알아들은 질문 안내는 빨간색 (11/11)."""

from dataclasses import replace

from PySide6.QtWidgets import QLabel

from setron.features.chat.classify import OUT_OF_SCOPE_MESSAGE, OUT_OF_SCOPE_TITLE
from setron.features.chat.schemas import ChatAnswer, ChatRequest
from setron.ui.chat_page import AnswerCard
from test_ui_chat_ver5 import sample, ui  # noqa: F401  (ui는 fixture)


def test_notices_fold_into_one_basis_toggle_closed_by_default(ui):
    answer = replace(sample(), notices=("질문을 ‘업그레이드 우선순위’(으)로 이해했어요.",
                                        "목표 N이 정해지지 않은 계열이 있어 그 설비는 '확인 필요'로 셉니다."))
    card = ui(AnswerCard(answer, lambda _: None))
    assert card.basis_toggle.text() == "판단 근거 2건" and not card.basis_body.isVisibleTo(card)
    assert not any(w.objectName() == "chatNote" and w.text() in answer.notices for w in card.findChildren(QLabel))
    card.basis_toggle.click()
    lines = [w.text() for w in card.basis_body.findChildren(QLabel)]
    assert card.basis_body.isVisibleTo(card) and lines == list(answer.notices)
    card.basis_toggle.click()
    assert not card.basis_body.isVisibleTo(card)


def test_no_notices_means_no_toggle(ui):
    card = ui(AnswerCard(replace(sample(), notices=()), lambda _: None))
    assert card.basis_toggle is None and card.basis_body is None


def test_unrecognized_and_out_of_scope_banners_are_red(ui):
    request = ChatRequest(question="점심 뭐 먹지?", intents=(), understood=False)
    unknown = ui(AnswerCard(ChatAnswer(request, "요청을 찾지 못함", "분석할 내용을 찾지 못했어요."), lambda _: None))
    outside = ui(AnswerCard(ChatAnswer(request, OUT_OF_SCOPE_TITLE, OUT_OF_SCOPE_MESSAGE, title=OUT_OF_SCOPE_TITLE),
                            lambda _: None))
    red = lambda card: [w.text() for w in card.findChildren(QLabel) if w.objectName() == "chatIssue"]
    assert any(text.startswith("조건 인식 실패") for text in red(unknown))
    assert any(text.startswith("답할 수 없는 질문") for text in red(outside))
    assert not any("조건 인식 실패" in text for text in red(outside))


def test_single_chart_uses_full_width_and_special_banners(ui):
    """그래프 하나는 전체 폭 (줄 배치의 '마지막 칸은 오른쪽에 작게' 규칙 때문에 찌그러지던 문제, 11/11).
    확인 질문 · 지원하지 않는 요청은 '자료 없음' 대신 맞는 안내."""
    from setron.features.chat import answer, recognize
    from setron.features.chat.answers import UNSUPPORTED_TITLE
    from setron.features.chat.classify import CLARIFY_TITLE
    from setron.ui.query_panel import _WrapRow
    from test_chat_questions import CATALOG, DATA
    forecast = answer(recognize("C3 통합UI는 언제 통일될까?", CATALOG), DATA)
    assert len(forecast.charts) == 1
    card = ui(AnswerCard(forecast, lambda _: None))
    assert not card.findChildren(_WrapRow) and card.charts[0].isVisibleTo(card)
    request = ChatRequest(question="언제야?", intents=(), understood=True)
    stale = lambda card: [w.text() for w in card.findChildren(QLabel) if w.objectName() == "chatStale"]
    clarify = ui(AnswerCard(ChatAnswer(request, CLARIFY_TITLE, "어느 쪽인가요?", title=CLARIFY_TITLE), lambda _: None))
    unsupported = ui(AnswerCard(ChatAnswer(request, "주간 보고서", "주간 보고서는 아직 만들 수 없어요.",
                                           title=UNSUPPORTED_TITLE), lambda _: None))
    assert any(t.startswith("확인 질문") for t in stale(clarify))
    assert any(t.startswith("지원하지 않는 요청") for t in stale(unsupported))


def test_period_answer_buttons_navigate_and_guides_hide_the_import_button(ui):
    from setron.features.chat import answer, recognize
    from test_chat_questions import CATALOG, DATA
    routes = []
    period = ui(AnswerCard(answer(recognize("9월부터 10월 보고서는 만들 수 있어?", CATALOG), DATA), lambda _: None,
                           None, None, None, routes.append))
    assert [b.text() for b in period.action_buttons] == ["버전 표준화 리포트 만들기", "설비별 변경 이력 리포트 만들기"]
    period.action_buttons[1].click()
    assert routes == ["reports:change:2026-09-21:2026-10-21"] and period.import_button is None
    weekly = ui(AnswerCard(answer(recognize("이번 주 보고서 만들어줘", CATALOG), DATA), lambda _: None,
                           None, None, None, routes.append))
    assert weekly.import_button is None                                   # 기능이 없어서지 자료가 없어서가 아니다
    missing = ui(AnswerCard(answer(recognize("11월 보고서 만들어줘", CATALOG), DATA), lambda _: None,
                            None, None, None, routes.append))
    assert missing.import_button is not None                              # 자료가 정말 없을 때만


def test_ai_route_opens_report_menu_with_kind_and_period(ui):
    from datetime import date
    from setron.ui.app import create_window
    window = ui(create_window(chat_enabled=True))
    window.pages["ai"].navigate_requested.emit("reports:change:2026-09-21:2026-10-21")
    reports = window.pages["reports"]
    assert reports.kind == "change"
    assert reports.start.date().toPython() == date(2026, 9, 21) and reports.end.date().toPython() == date(2026, 10, 21)
