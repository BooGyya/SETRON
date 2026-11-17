# -*- coding: utf-8 -*-
"""AI 호출부 — 이 파일 밖에서는 어느 서버·어느 모델인지 모른다.

설정(.env 또는 환경변수):
  SVHM_LLM_BASE_URL   개발: https://gms.ssafy.io/gmsapi/api.openai.com/v1   회사: http://<내부IP>:<port>/v1
  SVHM_LLM_API_KEY    개발: GMS 키                                            회사: EMPTY
  SVHM_LLM_MODEL      개발: gpt-5                                             회사: gpt-oss-120b (확인 필요)
  SVHM_LLM_PARAM_STYLE  openai5 (temperature 없음, max_completion_tokens) | classic (temperature, max_tokens)  — 회사 vLLM 은 classic
  SVHM_LLM_JSON_MODE  response_format 지원 여부: auto | on | off   (auto: 한 번 실패하면 off 로 내려감)
  SVHM_LLM_MOCK       1 이면 서버 호출 없이 mock_responder 가 답함 (테스트·오프라인)
  SVHM_LLM_LOG        호출 로그(jsonl) 경로. 기본 logs/llm_calls.jsonl

사용:
  client = LLMClient()                       # 설정 읽음
  data = client.complete_json(system, user, schema=MyPydanticModel)   # dict → 검증된 모델
"""
from __future__ import annotations

import hashlib, json, os, re, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Type

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except Exception:
    pass


class LLMError(Exception):
    pass


@dataclass
class LLMSettings:
    base_url: str = os.getenv("SVHM_LLM_BASE_URL", "https://gms.ssafy.io/gmsapi/api.openai.com/v1")
    api_key: str = os.getenv("SVHM_LLM_API_KEY") or os.getenv("GMS_KEY") or os.getenv("OPENAI_API_KEY") or ""
    model: str = os.getenv("SVHM_LLM_MODEL", "gpt-5")
    param_style: str = os.getenv("SVHM_LLM_PARAM_STYLE", "openai5")
    json_mode: str = os.getenv("SVHM_LLM_JSON_MODE", "auto")
    mock: bool = os.getenv("SVHM_LLM_MOCK", "0") == "1"
    log_path: str = os.getenv("SVHM_LLM_LOG", "logs/llm_calls.jsonl")
    timeout: float = float(os.getenv("SVHM_LLM_TIMEOUT", "60"))
    max_output_tokens: int = int(os.getenv("SVHM_LLM_MAX_TOKENS", "2048"))
    temperature: float = float(os.getenv("SVHM_LLM_TEMPERATURE", "0.2"))

    def describe(self) -> str:
        key = (self.api_key[:4] + "…") if self.api_key else "(없음)"
        return f"model={self.model} base_url={self.base_url} key={key} style={self.param_style} json={self.json_mode} mock={self.mock}"


def strip_code_fence(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def extract_json(text: str) -> Any:
    """응답에서 JSON 객체만 뽑는다. 앞뒤 설명이 섞여도 첫 '{'~마지막 '}' 를 시도."""
    t = strip_code_fence(text)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", t, flags=re.S)
    if m:
        return json.loads(m.group(0))
    raise LLMError("응답에서 JSON 을 찾지 못함: " + t[:200])


class LLMClient:
    def __init__(self, settings: Optional[LLMSettings] = None, mock_responder: Optional[Callable[[str, str], dict]] = None):
        self.s = settings or LLMSettings()
        self.mock_responder = mock_responder
        self._client = None
        self._json_mode_disabled = False

    # ------------------------------------------------------------------ 내부
    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            if not self.s.api_key:
                raise LLMError(".env 에 SVHM_LLM_API_KEY(또는 GMS_KEY) 가 없습니다")
            self._client = OpenAI(api_key=self.s.api_key, base_url=self.s.base_url, timeout=self.s.timeout)
        return self._client

    def _log(self, record: dict):
        try:
            p = Path(self.s.log_path); p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _call(self, system: str, user: str, want_json: bool) -> str:
        kwargs: dict[str, Any] = {"model": self.s.model,
                                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.s.param_style == "classic":
            kwargs["max_tokens"] = self.s.max_output_tokens
            kwargs["temperature"] = self.s.temperature
        else:                                   # gpt-5 계열: temperature 미지원, max_completion_tokens
            kwargs["max_completion_tokens"] = self.s.max_output_tokens
        use_json = want_json and self.s.json_mode != "off" and not self._json_mode_disabled
        if use_json:
            kwargs["response_format"] = {"type": "json_object"}
        t0 = time.time()
        try:
            resp = self._get_client().chat.completions.create(**kwargs)
        except Exception as e:
            msg = str(e)
            if use_json and self.s.json_mode == "auto" and ("response_format" in msg or "json_object" in msg):
                self._json_mode_disabled = True      # 회사 서버가 JSON 모드를 모르면 끄고 재시도
                return self._call(system, user, want_json)
            raise LLMError(f"LLM 호출 실패: {msg}")
        content = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        self._log({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "model": self.s.model, "latency_s": round(time.time() - t0, 2),
                   "prompt_sha": hashlib.sha1((system + user).encode()).hexdigest()[:10],
                   "tokens": {"in": getattr(usage, "prompt_tokens", None), "out": getattr(usage, "completion_tokens", None)} if usage else None,
                   "json_mode": use_json, "chars_out": len(content)})
        return content

    # ------------------------------------------------------------------ 공개
    def complete_text(self, system: str, user: str) -> str:
        if self.s.mock:
            r = self.mock_responder(system, user) if self.mock_responder else {}
            return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        return self._call(system, user, want_json=False)

    def complete_json(self, system: str, user: str, schema: Optional[Type] = None, retries: int = 1) -> Any:
        """JSON 응답을 받고(선택) pydantic 스키마로 검증. 실패하면 오류 내용을 붙여 한 번 더."""
        if self.s.mock:
            data = self.mock_responder(system, user) if self.mock_responder else {}
            return schema.model_validate(data) if schema else data
        last_err = None
        user_msg = user
        for attempt in range(retries + 1):
            raw = self._call(system, user_msg, want_json=True)
            try:
                data = extract_json(raw)
                return schema.model_validate(data) if schema else data
            except Exception as e:
                last_err = e
                user_msg = user + f"\n\n[이전 응답이 형식에 맞지 않았습니다: {str(e)[:300]}]\n반드시 규칙대로 JSON 만 출력하세요."
        raise LLMError(f"JSON 응답 검증 실패 ({retries + 1}회): {last_err}")
