"""AI 작업 요청을 만들고, 응답을 정해진 형식으로 해석한다 (명세 BE-005, FUN-056).

- 호출 실패·시간 초과·호출 제한 초과는 예외 대신 :class:`AIResult`의 상태와 안내 문구로 돌려준다.
- 회사 모델이 JSON 응답 모드를 지원하는지 모르므로, 응답 형식은 지시문으로 요청하고 본문에서 JSON을 꺼낸다.
- 결과가 잘못된 형식이면 일부만 쓰지 않고 전체를 INVALID_RESPONSE로 처리한다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from setron.features.ai.schemas import (
    EQUIPMENT_STATUS_FIELDS,
    RELEASE_NOTE_FIELDS,
    AIResult,
    AIStatus,
    AITask,
    AnalysisAnswer,
    ExtractedRow,
    ExtractKind,
    ExtractResult,
    QueryChoices,
    QueryConditions,
    ReviewNote,
    TargetView,
)
from setron.infrastructure.ai_client import (
    AIConnectionError,
    AIError,
    AIRateLimitError,
    AITimeoutError,
    ChatClient,
    ChatMessage,
)

MESSAGES = {
    AIConnectionError: "회사 AI 서버에 연결하지 못했습니다. 네트워크와 AI 서버 상태를 확인한 뒤 다시 시도하세요.",
    AITimeoutError: "AI 응답이 제한 시간 안에 오지 않았습니다. 잠시 뒤 다시 시도하세요.",
    AIRateLimitError: "AI 호출 제한을 넘었습니다. 잠시 뒤 다시 시도하세요.",
}
INVALID_MESSAGE = "AI 응답을 정해진 형식으로 해석하지 못했습니다. 다시 시도하세요."
OK_MESSAGE = "AI 응답을 받았습니다."

COMMON_RULES = (
    "반드시 JSON 객체 하나만 답한다. 설명 문장이나 코드 블록 표시를 붙이지 않는다.\n"
    "자료에 없는 값은 만들지 않는다. 모르는 값은 null로 두고 확인이 필요하다고 표시한다."
)

QUERY_PROMPT = (
    "너는 반도체 설비 SW 버전 현황 프로그램의 질문 해석기다.\n"
    "사용자 질문에서 조회 조건을 찾아 아래 형식으로 답한다. groups는 설비군(예: C4, O2, Inline Ox), "
    "models는 설비 모델명(예: M-C4), families는 버전 계열이다. Poly·Oxide·KCTC 같은 상위 분류는 "
    "해당 설비군을 모두 적는다. 선택지가 주어지지 않은 값은 질문에 적힌 표기 그대로 쓴다.\n"
    '{"sites": [], "lines": [], "groups": [], "models": [], "families": [], "versions": [], "items": [],\n'
    ' "base_date": "YYYY-MM-DD 또는 null", "target_view": "DASHBOARD|HEATMAP|ITEM_MANAGER|ANALYSIS 또는 null",\n'
    ' "unresolved": ["해석하지 못한 조건"]}\n'
    + COMMON_RULES
)

ANALYSIS_PROMPT = (
    "너는 반도체 설비 SW 버전 현황 분석가다. 함께 받은 자료만 근거로 질문에 답한다.\n"
    "자료로 확인한 내용은 evidence에, 추정은 assumptions에, 자료로 확인할 수 없는 내용은 unconfirmed에 나눠 적는다.\n"
    "자료의 숫자를 바꾸거나 새로 계산한 확률·완료 날짜를 만들지 않는다.\n"
    '{"answer": "답변", "evidence": [], "assumptions": [], "unconfirmed": []}\n'
    + COMMON_RULES
)

EXTRACT_PROMPT = (
    "너는 Excel 시트에서 값을 읽어 공통 항목으로 정리하는 추출기다.\n"
    "셀은 '주소: 값' 형식으로 주어진다. 항목마다 값을 읽은 셀 주소를 sources에 적는다.\n"
    "사용할 항목 이름: {fields}\n"
    '{{"rows": [{{"values": {{"항목": "값"}}, "sources": {{"항목": "셀 주소"}}}}],\n'
    ' "review_notes": [{{"reason": "확인이 필요한 이유", "field": "항목 또는 null", "address": "셀 주소 또는 null"}}]}}\n'
    + COMMON_RULES.replace("{", "{{").replace("}", "}}")
)

T = TypeVar("T")


class InvalidResponse(ValueError):
    """응답이 정해진 형식이 아니다."""


CHOICE_RULES = (
    "아래 선택지가 있는 항목은 선택지의 이름을 그대로 골라 쓴다. 질문의 다른 표현(예: 오투, 16라인, 지난달)도 "
    "가장 맞는 선택지로 바꾼다.\n"
    "선택지에 없는 조건은 지어내지 말고 unresolved에 적는다. 질문만으로 여러 선택지 중 하나를 고를 수 없으면 "
    "임의로 고르지 말고 unresolved에 '질문 표현 → 후보1 / 후보2 중 확인 필요' 형식으로 적는다.\n"
    "versions와 items는 선택지 없이 질문에 적힌 표기 그대로 쓴다.\n"
)


def parse_question(
    client: ChatClient, question: str, choices: QueryChoices | None = None
) -> AIResult[QueryConditions]:
    """사용자 질문에서 조회 조건을 찾는다 (BE-018, FUN-041).

    ``choices``를 주면 사업장·라인·분류·모델·계열·기준일을 그 선택지 안에서 고르게 한다.
    """
    prompt = QUERY_PROMPT if choices is None else f"{QUERY_PROMPT}\n{CHOICE_RULES}{_choice_lines(choices)}"
    messages = [ChatMessage("system", prompt), ChatMessage("user", question)]
    return _run(client, AITask.QUERY_PARSE, messages, _to_query_conditions)


def _choice_lines(choices: QueryChoices) -> str:
    fields = [("sites", "사업장"), ("lines", "라인"), ("groups", "설비군"), ("models", "모델명"),
              ("families", "버전 계열"), ("base_dates", "저장된 기준일(base_date)")]
    return "\n".join(
        f"{label} 선택지({key}): {json.dumps(list(getattr(choices, key)), ensure_ascii=False)}" for key, label in fields
    )


def analyze(client: ChatClient, question: str, facts: Mapping[str, Any]) -> AIResult[AnalysisAnswer]:
    """조회한 자료(``facts``)만 근거로 질문에 답한다 (BE-019, BE-020).

    ``facts``는 화면 집계와 같은 조건으로 조회한 값이어야 한다. JSON으로 바꿔 함께 보낸다.
    """
    content = f"질문: {question}\n자료(JSON): {json.dumps(facts, ensure_ascii=False, default=str)}"
    messages = [ChatMessage("system", ANALYSIS_PROMPT), ChatMessage("user", content)]
    return _run(client, AITask.ANALYSIS, messages, _to_analysis_answer)


def extract_sheet(
    client: ChatClient,
    kind: ExtractKind,
    sheet_name: str,
    cells: Sequence[tuple[str, Any]],
) -> AIResult[ExtractResult]:
    """시트의 셀 값에서 설비 현황이나 릴리즈 노트 항목을 추출한다 (BE-007 형식 초안).

    ``cells``는 ``(셀 주소, 값)`` 목록이다. 빈 셀은 빼고 넘긴다.
    """
    fields = EQUIPMENT_STATUS_FIELDS if kind is ExtractKind.EQUIPMENT_STATUS else RELEASE_NOTE_FIELDS
    prompt = EXTRACT_PROMPT.format(fields=", ".join(fields))
    body = "\n".join(f"{address}: {value}" for address, value in cells)
    messages = [ChatMessage("system", prompt), ChatMessage("user", f"시트: {sheet_name}\n{body}")]
    return _run(
        client,
        AITask.EXCEL_EXTRACT,
        messages,
        lambda obj: _to_extract_result(obj, kind, sheet_name, fields),
    )


def _run(
    client: ChatClient,
    task: AITask,
    messages: list[ChatMessage],
    convert: Callable[[dict[str, Any]], T],
) -> AIResult[T]:
    try:
        reply = client.complete(messages)
    except AIError as exc:
        status = AIStatus.RATE_LIMITED if isinstance(exc, AIRateLimitError) else AIStatus.FAILED
        message = next((m for cls, m in MESSAGES.items() if isinstance(exc, cls)), MESSAGES[AIConnectionError])
        return AIResult(task=task, status=status, message=message)

    try:
        data = convert(_load_json_object(reply.content))
    except InvalidResponse:
        return AIResult(
            task=task,
            status=AIStatus.INVALID_RESPONSE,
            message=INVALID_MESSAGE,
            source=reply.source,
            raw_content=reply.content,
        )
    return AIResult(
        task=task, status=AIStatus.OK, message=OK_MESSAGE, data=data, source=reply.source, raw_content=reply.content
    )


def _load_json_object(content: str) -> dict[str, Any]:
    """응답 본문에서 JSON 객체를 꺼낸다. 앞뒤 설명이나 ```json 표시가 붙어 와도 처리한다."""
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end < start:
        raise InvalidResponse("JSON 객체가 없습니다.")
    try:
        obj = json.loads(content[start : end + 1])
    except json.JSONDecodeError as exc:
        raise InvalidResponse(str(exc)) from exc
    if not isinstance(obj, dict):
        raise InvalidResponse("JSON 객체가 아닙니다.")
    return obj


def _strings(obj: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = obj.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InvalidResponse(f"{key}는 문자열 목록이어야 합니다.")
    return tuple(v.strip() for v in value if v.strip())


def _optional_string(obj: Mapping[str, Any], key: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidResponse(f"{key}는 문자열이거나 null이어야 합니다.")
    return value.strip() or None


def _string_map(value: Any, key: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvalidResponse(f"{key}는 객체여야 합니다.")
    return value


def _to_query_conditions(obj: dict[str, Any]) -> QueryConditions:
    view = _optional_string(obj, "target_view")
    try:
        target_view = TargetView(view) if view else None
    except ValueError as exc:
        raise InvalidResponse(f"알 수 없는 화면입니다: {view}") from exc
    return QueryConditions(
        sites=_strings(obj, "sites"),
        lines=_strings(obj, "lines"),
        groups=_strings(obj, "groups"),
        models=_strings(obj, "models"),
        families=_strings(obj, "families"),
        versions=_strings(obj, "versions"),
        items=_strings(obj, "items"),
        base_date=_optional_string(obj, "base_date"),
        target_view=target_view,
        unresolved=_strings(obj, "unresolved"),
    )


def _to_analysis_answer(obj: dict[str, Any]) -> AnalysisAnswer:
    answer = _optional_string(obj, "answer")
    if not answer:
        raise InvalidResponse("answer가 비어 있습니다.")
    return AnalysisAnswer(
        answer=answer,
        evidence=_strings(obj, "evidence"),
        assumptions=_strings(obj, "assumptions"),
        unconfirmed=_strings(obj, "unconfirmed"),
    )


def _to_extract_result(
    obj: dict[str, Any], kind: ExtractKind, sheet_name: str, fields: Sequence[str]
) -> ExtractResult:
    raw_rows = obj.get("rows")
    if not isinstance(raw_rows, list):
        raise InvalidResponse("rows는 목록이어야 합니다.")
    rows = []
    for raw in raw_rows:
        row = _string_map(raw, "rows의 행")
        values = _string_map(row.get("values"), "values")
        sources = _string_map(row.get("sources"), "sources")
        unknown = set(values) - set(fields)
        if unknown:
            raise InvalidResponse(f"정하지 않은 항목이 있습니다: {', '.join(sorted(unknown))}")
        rows.append(
            ExtractedRow(
                values={k: None if v is None else str(v) for k, v in values.items()},
                sources={k: str(v) for k, v in sources.items() if v},
            )
        )

    raw_notes = obj.get("review_notes") or []
    if not isinstance(raw_notes, list):
        raise InvalidResponse("review_notes는 목록이어야 합니다.")
    notes = []
    for raw in raw_notes:
        note = _string_map(raw, "review_notes의 항목")
        reason = _optional_string(note, "reason")
        if not reason:
            raise InvalidResponse("review_notes의 reason이 비어 있습니다.")
        notes.append(
            ReviewNote(reason=reason, field=_optional_string(note, "field"), address=_optional_string(note, "address"))
        )
    return ExtractResult(kind=kind, sheet_name=sheet_name, rows=tuple(rows), review_notes=tuple(notes))
