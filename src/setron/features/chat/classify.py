"""규칙이 못 알아들은 질문만 AI에게 '요청 종류 · 조건'을 분류해 달라고 한다 (11/11 A+B 중 B).

AI는 분류만 한다. 8가지 요청 종류 중 하나와 선택지(사업장 · 라인 · 모델 · 계열)에 있는 이름만 고르게 하고,
답·숫자는 지금처럼 DB 계산으로 만든다. AI 답은 JSON으로 받아 검사한다.

- 종류가 목록에 없으면 버린다. 이름이 선택지에 없으면 버린다(버전처럼 생긴 값은 규칙이 다시 확인).
- 통과한 결과는 규칙이 다시 읽을 수 있는 짧은 질문(예: '평택 P3 업그레이드 우선순위')으로 바꿔 ``recognize``에 넣는다.
  그래서 조건 해석·검증은 규칙 한 곳에서만 일어난다.
- AI가 '범위 밖'이라고 하면 SETRON 자료에 대한 질문만 답할 수 있다고 안내한다.
- AI가 없거나 실패하면 None — 지금처럼 '분석할 내용을 찾지 못했어요'로 답한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from setron.infrastructure.ai_client import ChatMessage, ResponseSource

from .schemas import Intent, Verdict

OUT_OF_SCOPE = "OUT_OF_SCOPE"
OUT_OF_SCOPE_TITLE = "요청 범위 밖"
OUT_OF_SCOPE_MESSAGE = ("SETRON 자료(설비 SW 버전 현황 · 릴리즈 · 목표 N)에 대한 질문에만 답할 수 있어요. "
                        "아래 같은 질문을 해 보세요.")

# 규칙이 확실히 알아듣는 표현으로 바꾼다 (recognize의 단어 표와 맞춤).
INTENT_PHRASE = {
    Intent.SEARCH: "현황 보여줘",
    Intent.COMPARE: "달성률 비교",
    Intent.CHANGE: "지난달 대비 변화",
    Intent.CAUSE: "원인 분석",
    Intent.PRIORITY: "업그레이드 우선순위",
    Intent.FORECAST: "버전 통일 시점 예측",
    Intent.VERSION_INFO: "최신 버전",
    Intent.REPORT: "보고서 만들어줘",
}
VERDICT_PHRASE = {Verdict.BELOW: "목표 N 미만", Verdict.ACHIEVED: "목표 N 이상", Verdict.CHECK: "확인 필요"}

_DESCRIPTIONS = (
    ("SEARCH", "설비 현황 · 목록 · 대수 · 상태"),
    ("COMPARE", "사업장 · 라인 · 모델끼리 달성률 비교, 가장 낮거나 높은 곳"),
    ("CHANGE", "이전 기준일 대비 변화, 나아졌는지 · 나빠졌는지"),
    ("CAUSE", "미달 · 판단 불가의 원인, 왜"),
    ("PRIORITY", "업그레이드 우선순위, 무엇부터"),
    ("FORECAST", "버전 통일 시점 · 완료 예상"),
    ("VERSION_INFO", "최신 버전 · 버전 목록 · 출시일"),
    ("REPORT", "보고서 만들기"),
    (OUT_OF_SCOPE, "설비 SW 버전 현황과 관계없는 질문 (날씨, 잡담, 일반 지식, 자료에 없는 사람 · 일정 정보)"),
)
_VERSION = re.compile(r"^[vV]\d[\w.\-+]*$")
_MAX_NAMES = 60               # 선택지가 너무 길면 앞쪽만 (토큰 절약)


@dataclass(frozen=True)
class Classified:
    """AI 분류 결과. ``question``은 규칙이 다시 읽을 짧은 질문, ``out_of_scope``면 범위 밖."""

    question: str = ""
    out_of_scope: bool = False


def _choices(catalog) -> dict[str, list[str]]:
    groups = []
    for group in getattr(catalog, "analysis_groups", ()) or ():
        groups.extend(name for name in (group.name, getattr(group, "model_name", None)) if name)
    return {
        "sites": list(dict.fromkeys(catalog.sites))[:_MAX_NAMES],
        "lines": list(dict.fromkeys(catalog.lines))[:_MAX_NAMES],
        "models": list(dict.fromkeys(groups))[:_MAX_NAMES],
        "families": list(dict.fromkeys(f.name for f in catalog.families))[:_MAX_NAMES],
    }


def _prompt(catalog) -> str:
    choices = _choices(catalog)
    kinds = "\n".join(f"- {name}: {text}" for name, text in _DESCRIPTIONS)
    return (
        "너는 반도체 설비 SW 버전 현황 프로그램의 질문 분류기다. 질문에 답하지 말고 분류만 하라.\n"
        f"요청 종류(intent)는 아래 중 정확히 하나:\n{kinds}\n"
        "names에는 질문이 가리키는 이름을 아래 선택지에서 글자 그대로만 고른다. 선택지에 없으면 넣지 않는다. "
        "버전(v로 시작)은 질문에 적힌 그대로 넣어도 된다.\n"
        f"선택지: {json.dumps(choices, ensure_ascii=False)}\n"
        "verdict는 질문이 목표 N 미만 설비만 원하면 BELOW, 목표 이상만이면 ACHIEVED, 확인 필요만이면 CHECK, 아니면 null.\n"
        '출력은 JSON 한 줄만: {"intent": "...", "names": [...], "verdict": null}'
    )


def _parse(text: str) -> dict | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.S)
    try:
        value = json.loads(match.group(0) if match else text)
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, dict) else None


def classify(client, question: str, catalog) -> Classified | None:
    """규칙이 못 알아들은 질문 하나를 AI로 분류한다.

    param
    - client: 회사 AI 클라이언트(``ChatClient``). None이면 None.
    - question: 사용자 질문.
    - catalog: ``query.schemas.QueryCatalog`` (고를 수 있는 이름).

    return
    - ``Classified`` (다시 읽을 짧은 질문 또는 범위 밖) / AI가 없거나 실패·형식 오류면 None.
    """
    if client is None or not question.strip():
        return None
    try:
        reply = client.complete([ChatMessage("system", _prompt(catalog)), ChatMessage("user", question.strip())])
    except Exception:
        return None
    if getattr(reply, "source", ResponseSource.REAL) is not ResponseSource.REAL:
        return None                                       # 예제 응답은 질문과 무관하다
    value = _parse(getattr(reply, "content", ""))
    if value is None:
        return None
    kind = str(value.get("intent") or "").strip().upper()
    if kind == OUT_OF_SCOPE:
        return Classified(out_of_scope=True)
    intent = next((i for i in INTENT_PHRASE if i.name == kind), None)
    if intent is None:
        return None
    allowed = {name for names in _choices(catalog).values() for name in names}
    raw = value.get("names") if isinstance(value.get("names"), list) else []
    names = [str(n).strip() for n in raw if str(n).strip() in allowed or _VERSION.match(str(n).strip())]
    verdict = next((v for v in VERDICT_PHRASE if v.name == str(value.get("verdict") or "").strip().upper()), None)
    parts = [*dict.fromkeys(names)]
    if verdict is not None and intent is Intent.SEARCH:
        parts.append(VERDICT_PHRASE[verdict])
    parts.append(INTENT_PHRASE[intent])
    return Classified(question=" ".join(parts))
