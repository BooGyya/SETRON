"""GMS·사내 OpenAI 호환 API와 모의 응답을 같은 ChatClient로 제공한다."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json
import math
import os
from typing import Protocol
from urllib.parse import urlsplit

import openai


class AIError(Exception):
    """AI 호출이 실패했다."""


class AIConfigError(AIError):
    """필수 AI 환경변수가 없거나 접속 설정이 잘못됐다."""


class AIResponseError(AIError):
    """AI 서버가 완전한 텍스트 응답을 반환하지 않았다."""


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
class AIConfig:
    """AI 접속 설정. API 키는 repr에서 제외하고 접속 대상은 명시적으로 지정한다."""

    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout: float = 60.0

    def __post_init__(self) -> None:
        for name in ("base_url", "api_key", "model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise AIConfigError(f"SETRON_AI_{name.upper()} 값이 필요합니다.")
        if any(not 33 <= ord(char) <= 126 for char in self.api_key):
            raise AIConfigError("SETRON_AI_API_KEY는 공백·제어 문자 없는 ASCII 문자열이어야 합니다.")
        try:
            url = urlsplit(self.base_url)
            valid_url = (url.scheme in {"http", "https"} and bool(url.hostname)
                         and url.username is None and url.password is None
                         and not url.query and not url.fragment
                         and (url.port is None or 1 <= url.port <= 65535)
                         and not any(char.isspace() or ord(char) < 32 for char in self.base_url))
        except ValueError:
            valid_url = False
        if not valid_url:
            raise AIConfigError("SETRON_AI_BASE_URL은 인증정보·쿼리·프래그먼트 없는 HTTP(S) 기본 주소여야 합니다.")
        if (isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float))
                or not math.isfinite(self.timeout) or self.timeout <= 0):
            raise AIConfigError("SETRON_AI_TIMEOUT은 0보다 큰 유한한 초 단위 숫자여야 합니다.")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AIConfig:
        """SETRON_AI_BASE_URL·API_KEY·MODEL과 선택 TIMEOUT을 읽는다.

        .env 파일은 실행 시 uv --env-file로 불러온다. 다른 OPENAI_* 환경변수나
        기본 OpenAI 주소로 대체하지 않으며, 오류에 키·설정값을 출력하지 않는다.
        """
        values = os.environ if environ is None else environ
        required = ("SETRON_AI_BASE_URL", "SETRON_AI_API_KEY", "SETRON_AI_MODEL")
        missing = [name for name in required if name not in values]
        if missing:
            raise AIConfigError("AI 접속 환경변수가 없습니다: " + ", ".join(missing))
        try:
            timeout = float(values.get("SETRON_AI_TIMEOUT", "60"))
        except (TypeError, ValueError):
            raise AIConfigError("SETRON_AI_TIMEOUT은 초 단위 숫자여야 합니다.") from None
        return cls(base_url=values["SETRON_AI_BASE_URL"].strip(), api_key=values["SETRON_AI_API_KEY"],
                   model=values["SETRON_AI_MODEL"].strip(), timeout=timeout)


class OpenAICompatibleChatClient:
    """OpenAI SDK의 Chat Completions 호출을 기존 ChatClient 계약에 연결한다.

    GMS·사내 API는 AIConfig만 바꿔 사용한다. SDK 자동 재시도는 하지 않는다.
    http_client에는 시험용 전송 계층 등을 주입할 수 있으며 이 객체가 함께 닫는다.
    앱의 작업 수명 동안 재사용하고 종료 시 close() 또는 with 문으로 정리한다.
    """

    def __init__(self, config: AIConfig, *, http_client=None) -> None:
        self.config = config
        self._client = openai.OpenAI(base_url=config.base_url, api_key=config.api_key,
                                     timeout=config.timeout, max_retries=0, http_client=http_client)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, *, http_client=None) -> OpenAICompatibleChatClient:
        """환경변수를 검증하고 클라이언트를 생성한다. 생성만으로 API를 호출하지 않는다."""
        return cls(AIConfig.from_env(environ), http_client=http_client)

    def complete(self, messages: Sequence[ChatMessage]) -> ChatReply:
        """문자열 메시지를 한 번 전송하고 완전한 텍스트 응답만 반환한다.

        서버별 지원 여부가 확인되지 않은 JSON 모드·도구·추론 옵션은 전송하지 않는다.
        SDK 오류의 본문·헤더는 사용자 오류에 포함하지 않는다.
        """
        try:
            response = self._client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": message.role, "content": message.content} for message in messages],
            )
        except openai.RateLimitError:
            raise AIRateLimitError("AI 호출 제한을 넘었습니다.") from None
        except openai.APITimeoutError:
            raise AITimeoutError("AI 응답 제한 시간을 넘었습니다.") from None
        except (openai.APIResponseValidationError, json.JSONDecodeError):
            raise AIResponseError("AI 응답 형식이 올바르지 않습니다.") from None
        except openai.APIError:
            raise AIConnectionError("AI 서버 연결 또는 요청 처리에 실패했습니다.") from None

        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            raise AIResponseError("AI 응답에 답변이 없습니다.")
        choice = choices[0]
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        if (getattr(choice, "finish_reason", None) != "stop"
                or getattr(message, "refusal", None) or getattr(message, "tool_calls", None)
                or getattr(message, "function_call", None)
                or not isinstance(content, str) or not content.strip()):
            raise AIResponseError("AI 응답이 비어 있거나 완전한 텍스트 답변이 아닙니다.")
        model = getattr(response, "model", None)
        return ChatReply(content, ResponseSource.REAL, model if isinstance(model, str) else self.config.model)

    def close(self) -> None:
        """SDK와 HTTP 연결을 정리한다."""
        self._client.close()

    def __enter__(self) -> OpenAICompatibleChatClient:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


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
