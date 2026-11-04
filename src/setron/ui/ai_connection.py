"""회사 AI 접속 설정(`SETRON_AI_*`)이 있으면 화면들이 실제 AI를 쓰도록 공급자를 만든다 (BE-005, Jira S15P31B106-75).

설정이 없으면 각 화면의 기본값인 예제 응답을 그대로 쓴다. 설정이 잘못되면 예제 응답으로 시작하고 이유를 알린다.
클라이언트 하나를 가져오기·AI 분석 화면이 함께 쓰며, 앱을 닫을 때 정리한다.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial

from setron.features.ai.service import analyze, extract_sheet, parse_question
from setron.features.extraction.service import extract_sheet_data
from setron.infrastructure.ai_client import AIConfigError, OpenAICompatibleChatClient


@dataclass(frozen=True)
class AIProviders:
    """화면에 넘길 실제 AI 공급자. 설정이 없으면 모두 None(예제 응답)이다.

    - ``extractor(kind, sheet_name, cells)``: 가져오기 화면의 AI 추출.
    - ``sheet_extractor(kind, sheet)``: 원본 범위·병합 위치를 보존하는 실제 가져오기 추출.
    - ``parser(question)``·``analyzer(question, facts)``: AI 분석 화면의 질문 해석·분석.
    - ``problem``: 설정이 잘못돼 예제 응답으로 시작한 이유.
    """

    client: OpenAICompatibleChatClient | None = None
    extractor: Callable | None = None
    parser: Callable | None = None
    analyzer: Callable | None = None
    problem: str | None = None
    sheet_extractor: Callable | None = None

    @property
    def connected(self) -> bool:
        return self.client is not None

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


def ai_providers_from_env(environ: Mapping[str, str] | None = None, *, client_factory=None) -> AIProviders:
    """``SETRON_AI_BASE_URL``이 있으면 실제 AI 공급자를 만든다. 만들기만 하고 AI를 호출하지 않는다."""
    values = os.environ if environ is None else environ
    if not values.get("SETRON_AI_BASE_URL"):
        return AIProviders()
    factory = client_factory or OpenAICompatibleChatClient.from_env
    try:
        client = factory(values)
    except AIConfigError as error:
        return AIProviders(problem=f"AI 접속 설정을 확인해 주세요. 예제 응답으로 시작했습니다: {error}")
    return AIProviders(
        client=client,
        extractor=lambda kind, sheet_name, cells: extract_sheet(client, kind, sheet_name, cells),
        sheet_extractor=partial(extract_sheet_data, client, max_workers=2),
        parser=lambda question: parse_question(client, question),
        analyzer=lambda question, facts: analyze(client, question, facts),
    )
