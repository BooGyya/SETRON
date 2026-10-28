"""회사 AI API(OpenAI 호환 Chat Completions) 호출 계층.

실제 접속 주소·모델·인증·호출 제한은 아직 받지 못했다 (명세 FUN-056, BE-005).
그때까지는 :class:`ExampleChatClient`가 미리 정한 정상·실패 응답을 돌려준다.
실제 클라이언트는 정보를 받은 뒤 :class:`ChatClient` 형식에 맞춰 추가한다.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class AIError(Exception):
    """AI 호출이 실패했다."""


class AIConnectionError(AIError):
    """AI 서버에 연결하지 못했거나 서버가 오류를 돌려줬다."""


class AITimeoutError(AIError):
    """AI 서버가 제한 시간 안에 응답하지 않았다."""


class AIRateLimitError(AIError):
    """호출 제한을 넘었다. 잠시 뒤 다시 시도해야 한다."""


class ResponseSource(str, Enum):
    """응답을 어디서 받았는지. 예제 응답 시험과 실제 API 시험의 결과를 구분하는 데 쓴다."""

    EXAMPLE = "EXAMPLE"
    REAL = "REAL"


@dataclass(frozen=True)
class ChatMessage:
    """Chat Completions의 메시지 한 건. ``role``은 ``system``·``user``·``assistant`` 중 하나다."""

    role: str
    content: str


@dataclass(frozen=True)
class ChatReply:
    """AI가 돌려준 답변 본문."""

    content: str
    source: ResponseSource
    model: str | None = None


class ChatClient(Protocol):
    """AI 호출 방법. 예제 클라이언트와 실제 클라이언트가 같은 형식을 따른다.

    실패하면 :class:`AIError`의 하위 오류를 낸다.
    """

    def complete(self, messages: Sequence[ChatMessage]) -> ChatReply: ...


@dataclass(frozen=True)
class ExampleFailure:
    """예제 클라이언트가 재현할 실패 하나. ``error``는 낼 오류 객체다."""

    error: AIError


ExampleStep = str | ExampleFailure


class ExampleChatClient:
    """미리 정한 응답을 순서대로 돌려주는 예제 클라이언트.

    ``steps``의 문자열은 정상 응답 본문이고, :class:`ExampleFailure`는 그 차례에 낼 오류다.
    준비한 응답을 다 쓰면 :class:`AIConnectionError`를 낸다. 받은 메시지는 ``calls``에 남는다.
    """

    def __init__(self, steps: Iterable[ExampleStep], model: str = "example") -> None:
        self._steps: deque[ExampleStep] = deque(steps)
        self._model = model
        self.calls: list[list[ChatMessage]] = []

    def complete(self, messages: Sequence[ChatMessage]) -> ChatReply:
        self.calls.append(list(messages))
        if not self._steps:
            raise AIConnectionError("준비한 예제 응답을 모두 사용했습니다.")
        step = self._steps.popleft()
        if isinstance(step, ExampleFailure):
            raise step.error
        return ChatReply(content=step, source=ResponseSource.EXAMPLE, model=self._model)
