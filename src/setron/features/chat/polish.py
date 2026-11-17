"""요약 문장 다듬기. AI는 말투만 자연스럽게 바꾸고, 숫자·버전·설비명·날짜는 원문과 똑같아야 한다.

다듬은 문장에서 뽑은 숫자·식별자 목록이 원문과 하나라도 다르면(바뀜·빠짐·새로 생김) 원문을 그대로 쓴다.
AI가 없거나 실패해도 답은 그대로 나간다.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace

from setron.infrastructure.ai_client import ChatMessage, ResponseSource

from .schemas import ChatAnswer

PROMPT = (
    "너는 반도체 설비 SW 버전 현황 프로그램의 답변 문장을 다듬는다. 받은 문장을 같은 뜻으로 더 자연스러운 한국어로 바꿔라.\n"
    "규칙: 숫자·백분율·날짜·버전(v로 시작)·설비 호기·라인·사업장·모델 이름은 한 글자도 바꾸거나 빼지 말고 그대로 써라. "
    "새로운 숫자나 사실을 더하지 말라. 문장 수를 늘리지 말라. 다듬은 문장만 출력하라."
)
_TOKEN = re.compile(r"\d{4}-\d{2}-\d{2}|[vV]\d[\w.\-+]*|[A-Za-z]+[\w\-]*\d[\w\-]*|\d+(?:\.\d+)?%?|\d+[A-Za-z]+")


def facts(text: str) -> Counter:
    """문장에 들어 있는 숫자·날짜·버전·식별자 (같은지 비교용)."""
    return Counter(token.casefold() for token in _TOKEN.findall(text))


def polish(answer: ChatAnswer, client) -> ChatAnswer:
    """요약 문장을 AI로 다듬는다. 숫자·식별자가 원문과 완전히 같을 때만 바꾼다.

    param
    - answer: 엔진이 만든 답 묶음.
    - client: 회사 AI 클라이언트(``ChatClient``). None이면 그대로 돌려준다.

    return
    - 다듬은 답(``polished=True``) 또는 원래 답.
    """
    original = answer.summary.strip()
    if client is None or not original:
        return answer
    try:
        reply = client.complete([ChatMessage("system", PROMPT), ChatMessage("user", original)])
    except Exception:
        return answer
    if getattr(reply, "source", ResponseSource.REAL) is not ResponseSource.REAL:
        return answer                                   # 예제 응답은 질문과 무관하다
    text = (reply.content or "").strip().strip('"').strip()
    if not text or len(text) > len(original) * 2 or facts(text) != facts(original):
        return answer
    if text[:1] in "{[<`" or "```" in text:
        return answer                                   # JSON·코드처럼 생긴 답은 문장이 아니다 (숫자 없는 문장 검사 보완)
    return replace(answer, summary=text, polished=True)
