"""AI 분석 질문 이해 보완 (11/11 A+B).

A: 사전에 없던 말투도 규칙으로 알아듣는다 (토큰 0).
B: 규칙이 못 알아들은 질문만 AI에게 '요청 종류·조건' 분류를 부탁한다. AI는 분류만, 숫자는 DB 계산.
"""

import json

import pytest

from setron.features.chat import Intent, answer, recognize
from setron.features.chat.classify import OUT_OF_SCOPE_MESSAGE, classify
from setron.features.chat.schemas import GroupBy
from setron.infrastructure.ai_client import ChatReply, ResponseSource
from setron.ui.chat_data import ChatService
from test_chat_questions import CATALOG, DATA


# --- A: 말투 넓히기 -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("question, intent", [
    ("C3 애들 상태 어때?", Intent.SEARCH),
    ("요즘 어때?", Intent.SEARCH),
    ("16L 괜찮아?", Intent.SEARCH),
    ("뭐부터 손봐야 돼?", Intent.PRIORITY),
    ("어떤 설비부터 고쳐야 해?", Intent.PRIORITY),
    ("지난번보다 나아졌어?", Intent.CHANGE),
    ("이거 다 올리려면 언제쯤 끝나?", Intent.FORECAST),
    ("업데이트 제일 안 된 데가 어디야?", Intent.COMPARE),
])
def test_everyday_phrasing_is_understood_by_rules(question, intent):
    request = recognize(question, CATALOG)
    assert request.understood and intent in request.intents


def test_two_models_compare_by_model_and_comparative_not_below_filter():
    request = recognize("O2랑 C3 중에 뭐가 더 안 좋아?", CATALOG)
    assert Intent.COMPARE in request.intents and request.group_by is GroupBy.MODEL
    assert "가장 낮은 모델은 M-C3" in answer(request, DATA).summary
    worst = recognize("업데이트 제일 안 된 데가 어디야?", CATALOG)
    assert worst.verdict is None                                   # '안 된'은 미달 목록이 아니라 비교 표현


# --- B: 못 알아들은 질문만 AI 분류 -----------------------------------------------------------------------------
class FakeClient:
    def __init__(self, reply=None, *, source=ResponseSource.REAL, error=None):
        self.reply, self.source, self.error, self.calls = reply, source, error, []

    def complete(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        content = self.reply if isinstance(self.reply, str) else json.dumps(self.reply, ensure_ascii=False)
        return ChatReply(content, self.source)


class Service(ChatService):
    def __init__(self, client):
        super().__init__(None, dict, client)

    def data(self):
        return DATA


def _classifier_calls(client):
    """요약 다듬기 호출은 빼고 분류 요청만 센다."""
    return [m for m in client.calls if "질문 분류기" in m[0].content]


def test_smalltalk_and_quick_questions_never_call_the_planner():
    """11/11부터 직접 입력한 질문은 AI가 먼저 계획한다. 인사말·바로 분석·예시·추천 질문만 AI 없이 규칙으로 즉시."""
    from setron.features.chat.answers import SUGGESTIONS
    client = FakeClient({"intent": "PRIORITY", "names": [], "verdict": None})
    service = Service(client)
    service.quick_questions.add("어디부터 업그레이드해야 해?")
    service.ask("안녕하세요")
    service.ask(SUGGESTIONS[0])
    service.ask("어디부터 업그레이드해야 해?")
    assert _classifier_calls(client) == []
    service.ask("C3 현황 보여줘")                                          # 직접 입력한 질문은 계획한다
    assert len(_classifier_calls(client)) == 1


def test_not_understood_question_is_classified_then_answered_from_db():
    client = FakeClient({"intent": "PRIORITY", "names": ["평택", "화성시청"], "verdict": None})
    result = Service(client).ask("거기 업글 1순위는?")                     # 규칙 사전에 없는 말
    assert len(_classifier_calls(client)) == 1
    assert Intent.PRIORITY in result.request.intents and result.request.question == "거기 업글 1순위는?"
    assert result.request.conditions.sites == ("평택",)                  # 선택지에 없는 '화성시청'은 버림
    assert "질문을 ‘평택 업그레이드 우선순위’(으)로 이해했어요." in result.notices
    assert "업그레이드가 가장 급한 설비는" in result.summary            # 숫자는 DB 계산 그대로


def test_out_of_scope_is_declined_politely():
    result = Service(FakeClient({"intent": "OUT_OF_SCOPE", "names": []})).ask("오늘 점심 뭐 먹지?")
    assert result.summary == OUT_OF_SCOPE_MESSAGE and not result.tables and result.suggestions
    assert result.title == "요청 범위 밖"                                 # 화면이 '답할 수 없는 질문'으로 구분


@pytest.mark.parametrize("client", [
    FakeClient(error=RuntimeError("AI 연결 실패")),
    FakeClient({"intent": "PRIORITY"}, source=ResponseSource.EXAMPLE),   # 예제 응답은 질문과 무관
    FakeClient({"intent": "WEATHER", "names": []}),                      # 목록에 없는 종류
    FakeClient("우선순위 질문 같아요"),                                     # JSON이 아님
])
def test_ai_failures_fall_back_to_the_usual_not_understood_answer(client):
    result = Service(client).ask("거기 업글 1순위는?")
    assert not result.request.understood and "분석할 내용을 찾지 못했어요" in result.summary


def test_no_client_means_rules_only():
    result = Service(None).ask("거기 업글 1순위는?")
    assert "분석할 내용을 찾지 못했어요" in result.summary
    assert classify(None, "아무 질문", CATALOG) is None


def test_classifier_prompt_lists_only_catalog_names_and_asks_for_json():
    client = FakeClient({"intent": "SEARCH", "names": ["M-O2"], "verdict": "BELOW"})
    guess = classify(client, "O2 쪽 덜 끝난 애들?", CATALOG)
    assert guess.question == "M-O2 목표 N 미만 현황 보여줘"
    system = client.calls[0][0].content
    assert "평택" in system and "JSON" in system and "답하지 말고 분류만" in system


def test_polish_never_rewrites_fixed_guidance_or_accepts_json():
    from setron.features.chat import polish
    client = FakeClient({"intent": "WEATHER"})
    guidance = Service(client).ask("거기 업글 1순위는?")
    assert "분석할 내용을 찾지 못했어요" in guidance.summary and not guidance.polished
    understood = answer(recognize("요즘 어때?", CATALOG), DATA)
    weird = FakeClient("{" + understood.summary + "}")                    # 숫자는 같아도 JSON처럼 생긴 답
    assert polish(understood, weird).summary == understood.summary
