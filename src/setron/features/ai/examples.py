"""실제 회사 AI 대신 쓸 예제 응답 (명세 BE-005).

정상 응답은 :mod:`setron.features.ai.service`가 요청하는 형식을 따른다. 값은 형식을 보여주기 위한 예시이며
실제 설비·버전 대응을 뜻하지 않는다. 실패 응답은 호출 실패·시간 초과·호출 제한 초과를 재현한다.
"""

from __future__ import annotations

import json

from setron.features.ai.schemas import AITask
from setron.infrastructure.ai_client import (
    AIConnectionError,
    AIRateLimitError,
    AITimeoutError,
    ExampleChatClient,
    ExampleFailure,
    ExampleStep,
)

QUERY_REPLY = json.dumps(
    {
        "sites": ["화성"],
        "lines": ["P1D"],
        "groups": ["C4"],
        "models": [],
        "families": [],
        "versions": ["v4.56.6.43"],
        "items": [],
        "base_date": None,
        "target_view": "DASHBOARD",
        "unresolved": [],
    },
    ensure_ascii=False,
)

ANALYSIS_REPLY = json.dumps(
    {
        "answer": "P1D 라인 C4 설비는 41·42·43 버전으로 나뉘어 있습니다.",
        "evidence": ["기준일 2026-10-21 현황에서 41 버전 2대, 42 버전 3대, 43 버전 5대"],
        "assumptions": ["43 버전 패치에 하드웨어 변경이 필요해 적용이 늦어졌을 수 있습니다."],
        "unconfirmed": ["설비별 실제 패치 일정"],
    },
    ensure_ascii=False,
)

EXTRACT_REPLY = json.dumps(
    {
        "rows": [
            {
                "values": {
                    "site": "화성",
                    "line": "P1D",
                    "model_name": "M-C4",
                    "equipment_code": "ETCH-001",
                    "family_name": "C4",
                    "version_raw": "v4.56.6.43",
                },
                "sources": {"site": "A1", "line": "B5", "equipment_code": "C5", "version_raw": "F5"},
            }
        ],
        "review_notes": [{"reason": "기준 날짜를 시트에서 찾지 못했습니다.", "field": "base_date", "address": None}],
    },
    ensure_ascii=False,
)

NORMAL_REPLIES = {
    AITask.QUERY_PARSE: QUERY_REPLY,
    AITask.ANALYSIS: ANALYSIS_REPLY,
    AITask.EXCEL_EXTRACT: EXTRACT_REPLY,
}

FAILURES = {
    "connection": ExampleFailure(AIConnectionError("예제: 연결 실패")),
    "timeout": ExampleFailure(AITimeoutError("예제: 시간 초과")),
    "rate_limit": ExampleFailure(AIRateLimitError("예제: 호출 제한 초과")),
}


def example_client(task: AITask, *failures: str) -> ExampleChatClient:
    """``failures``에 적은 실패를 차례로 낸 뒤 ``task``의 정상 응답을 돌려주는 예제 클라이언트.

    예: ``example_client(AITask.QUERY_PARSE, "rate_limit")``는 첫 호출에 제한 초과, 두 번째 호출에 정상 응답.
    """
    steps: list[ExampleStep] = [FAILURES[name] for name in failures]
    steps.append(NORMAL_REPLIES[task])
    return ExampleChatClient(steps)
