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
