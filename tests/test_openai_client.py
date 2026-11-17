"""실제 SDK와 모의 HTTP 전송으로 GMS·사내 API 전환을 검증한다. 외부 API는 호출하지 않는다."""

from dataclasses import replace
import json

import httpx2
import pytest

from setron.features.ai.schemas import AIStatus, ExtractKind
from setron.features.ai.service import analyze, extract_sheet, parse_question
from setron.infrastructure.ai_client import (
    AIConfig, AIConfigError, AIConnectionError, AIRateLimitError, AIResponseError, AITimeoutError,
    ChatMessage, OpenAICompatibleChatClient, ResponseSource,
)


def settings(**changes):
    values = {"SETRON_AI_BASE_URL": "https://gms.example.test/gmsapi/api.openai.com/v1",
              "SETRON_AI_API_KEY": "test-secret-key", "SETRON_AI_MODEL": "test-model"}
    values.update(changes)
    return values


def completion(content="시험 답변", *, finish_reason="stop", **message_changes):
    return {"id": "test-completion", "object": "chat.completion", "created": 0, "model": "server-test-model",
            "choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": content, **message_changes}}]}


def client_with(handler, environ=None):
    return OpenAICompatibleChatClient.from_env(
        settings() if environ is None else environ,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def test_config_reads_process_environment_and_hides_key(monkeypatch):
    for name, value in settings().items():
        monkeypatch.setenv(name, value)
    # 팀 .env에 SETRON_AI_TIMEOUT(180)이 있으면 기본값 60 검사가 깨지므로 지운다.
    monkeypatch.delenv("SETRON_AI_TIMEOUT", raising=False)
    config = AIConfig.from_env()
    assert config.base_url == settings()["SETRON_AI_BASE_URL"]
    assert config.model == "test-model" and config.timeout == 60
    assert config.api_key == "test-secret-key" and "test-secret-key" not in repr(config)


@pytest.mark.parametrize("name", ["SETRON_AI_BASE_URL", "SETRON_AI_API_KEY", "SETRON_AI_MODEL"])
@pytest.mark.parametrize("missing", [True, False])
def test_required_config_has_no_openai_environment_fallback(monkeypatch, name, missing):
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unrelated.example.test/v1")
    environ = settings()
    if missing:
        del environ[name]
    else:
        environ[name] = "   "
    with pytest.raises(AIConfigError) as error:
        AIConfig.from_env(environ)
    assert name in str(error.value)
    assert "test-secret-key" not in str(error.value) and "unrelated-secret" not in str(error.value)


@pytest.mark.parametrize("value", ["", "invalid", "0", "-1", "nan", "inf", "-inf"])
def test_invalid_timeout_is_rejected_before_sdk_creation(value):
    with pytest.raises(AIConfigError, match="SETRON_AI_TIMEOUT"):
        OpenAICompatibleChatClient.from_env(settings(SETRON_AI_TIMEOUT=value))


@pytest.mark.parametrize("url", ["ai.example.test/v1", "ftp://ai.example.test/v1", "https:///v1",
    "https://user:secret@ai.example.test/v1", "https://ai.example.test/v1?key=secret",
    "https://ai.example.test/v1#fragment", "http://ai.example.test:bad/v1", "http://ai.example.test:0/v1",
    "http://ai.example.test:65536/v1", "http://ai. example.test/v1", "http://ai.example.test/\nv1"])
def test_invalid_base_url_is_rejected_without_echoing_value(url):
    with pytest.raises(AIConfigError, match="SETRON_AI_BASE_URL") as error:
        AIConfig.from_env(settings(SETRON_AI_BASE_URL=url))
    assert url not in str(error.value) and "test-secret-key" not in str(error.value)


@pytest.mark.parametrize("key", ["key\nvalue", "key value", " key", "key\rvalue", "key\tvalue", "한글키", "key\x7fvalue"])
def test_invalid_header_key_is_rejected_without_echoing_value(key):
    with pytest.raises(AIConfigError, match="SETRON_AI_API_KEY") as error:
        AIConfig.from_env(settings(SETRON_AI_API_KEY=key))
    assert key not in str(error.value)


@pytest.mark.parametrize("timeout", [True, None, "60", float("nan"), float("inf"), 0])
def test_direct_config_validates_timeout_too(timeout):
    with pytest.raises(AIConfigError):
        replace(AIConfig.from_env(settings()), timeout=timeout)


@pytest.mark.parametrize("base_url,key,model", [
    ("https://gms.example.test/gmsapi/api.openai.com/v1", "test-gms-key", "gpt-4.1"),
    ("http://company.example.test:8000/v1/", "EMPTY", "gpt-4"),
])
def test_same_chatclient_switches_url_auth_and_model_by_environment(base_url, key, model):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(200, json=completion(" 시험 원문 답변 \n"))

    environ = settings(SETRON_AI_BASE_URL=base_url, SETRON_AI_API_KEY=key,
                       SETRON_AI_MODEL=model, SETRON_AI_TIMEOUT="12.5")
    with client_with(handler, environ) as client:
        assert calls == []  # 생성만으로 서버 접속·모델 조회를 하지 않는다.
        reply = client.complete([ChatMessage("system", "지시문"), ChatMessage("user", "질문")])
    assert len(calls) == 1
    request = calls[0]
    assert str(request.url) == base_url.rstrip("/") + "/chat/completions"
    assert request.method == "POST" and request.headers["authorization"] == "Bearer " + key
    assert json.loads(request.content) == {"model": model, "messages": [
        {"role": "system", "content": "지시문"}, {"role": "user", "content": "질문"}]}
    assert request.extensions["timeout"]["read"] == 12.5
    assert reply.content == " 시험 원문 답변 \n" and reply.source is ResponseSource.REAL
    assert reply.model == "server-test-model"


@pytest.mark.parametrize("status,error_type", [(400, AIConnectionError), (401, AIConnectionError),
    (403, AIConnectionError), (404, AIConnectionError), (429, AIRateLimitError),
    (500, AIConnectionError), (503, AIConnectionError)])
def test_http_errors_map_to_existing_errors_without_retry_or_secret_echo(status, error_type):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(status, json={"error": {"message": "private test-secret-key", "type": "test"}},
                               headers={"retry-after": "1"})

    with client_with(handler) as client:
        with pytest.raises(error_type) as error:
            client.complete([ChatMessage("user", "시험")])
    assert len(calls) == 1 and "test-secret-key" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("transport_error,error_type", [
    (httpx2.ConnectError, AIConnectionError), (httpx2.ConnectTimeout, AITimeoutError),
    (httpx2.ReadTimeout, AITimeoutError),
])
def test_transport_errors_are_mapped_without_automatic_retry(transport_error, error_type):
    calls = []

    def handler(request):
        calls.append(request)
        raise transport_error("private test-secret-key", request=request)

    with client_with(handler) as client:
        with pytest.raises(error_type) as error:
            client.complete([ChatMessage("user", "시험")])
    assert len(calls) == 1 and "test-secret-key" not in str(error.value)


@pytest.mark.parametrize("payload", [
    {}, [], {"choices": None}, {"choices": []}, {"choices": [None]},
    {"choices": [{"finish_reason": "stop", "message": {"content": 123}}]},
    completion(None), completion(""), completion("  "), completion("partial", finish_reason="length"),
    completion("partial", finish_reason="content_filter"), completion("text", refusal="refused"),
    completion("text", finish_reason="tool_calls"), completion("text", tool_calls=[{"id": "test"}]),
    completion("text", function_call={"name": "test", "arguments": "{}"}),
])
def test_incomplete_or_malformed_response_is_not_accepted(payload):
    with client_with(lambda request: httpx2.Response(200, json=payload)) as client:
        with pytest.raises(AIResponseError):
            client.complete([ChatMessage("user", "시험")])


@pytest.mark.parametrize("response", [
    httpx2.Response(200, text="not json", headers={"content-type": "application/json"}),
    httpx2.Response(200, html="<h1>gateway</h1>"),
])
def test_non_json_http_response_becomes_invalid_response_result(response):
    with client_with(lambda request: response) as client:
        result = parse_question(client, "시험")
    assert result.status is AIStatus.INVALID_RESPONSE and result.data is None


@pytest.mark.parametrize("status,expected", [(401, AIStatus.FAILED), (429, AIStatus.RATE_LIMITED), (503, AIStatus.FAILED)])
def test_existing_ai_service_receives_expected_failure_status(status, expected):
    with client_with(lambda request: httpx2.Response(status, json={"error": {"message": "test"}})) as client:
        result = parse_question(client, "시험")
    assert result.status is expected and result.data is None


def test_stopped_text_with_invalid_json_uses_existing_parser_and_marks_real_response():
    with client_with(lambda request: httpx2.Response(200, json=completion("not json"))) as client:
        result = parse_question(client, "시험")
    assert result.status is AIStatus.INVALID_RESPONSE and result.source is ResponseSource.REAL
    assert result.raw_content == "not json"


@pytest.mark.parametrize("task,payload", [
    ("query", {"sites": ["시험사업장"], "unresolved": []}),
    ("analysis", {"answer": "시험 분석", "evidence": ["시험 근거"]}),
    ("extraction", {"rows": [{"values": {"equipment_code": "TEST-001"}, "sources": {"equipment_code": "A1"}}]}),
])
def test_sdk_client_plugs_into_all_existing_ai_operations(task, payload):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(200, json=completion(json.dumps(payload, ensure_ascii=False)))

    with client_with(handler) as client:
        if task == "query":
            result = parse_question(client, "시험사업장 현황")
            assert result.data.sites == ("시험사업장",)
        elif task == "analysis":
            result = analyze(client, "시험 질문", {"count": 1})
            assert result.data.answer == "시험 분석"
        else:
            result = extract_sheet(client, ExtractKind.EQUIPMENT_STATUS, "시험", [("A1", "TEST-001")])
            assert result.data.rows[0].sources["equipment_code"] == "A1"
    assert result.ok and result.source is ResponseSource.REAL and len(calls) == 1


def test_sdk_client_plugs_into_be007_sheetdata_review():
    from setron.features.extraction.service import extract_sheet_data
    from setron.infrastructure.excel_reader import SheetData

    payload = {"rows": [{"values": {"equipment_code": "TEST-001", "version_raw": "vTEST"},
                         "sources": {"equipment_code": "A2", "version_raw": "B2"}}]}
    sheet = SheetData("합성.xlsx", "시험", 1, 1, [["호기", "Version"], ["TEST-001", "vTEST"]])
    with client_with(lambda request: httpx2.Response(200, json=completion(json.dumps(payload)))) as client:
        result = extract_sheet_data(client, ExtractKind.EQUIPMENT_STATUS, sheet)
    assert result.ok and result.source is ResponseSource.REAL
    assert result.data.rows[0].sources["version_raw"].address == "B2"
    assert result.data.rows[0].values["version_raw"] == "vTEST"
    assert result.data.needs_review  # 모의 자료의 누락된 필수값을 임의로 채우지 않는다.


def test_context_manager_closes_http_connections_after_work_failure():
    transport_client = httpx2.Client(transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=completion())))
    with pytest.raises(RuntimeError, match="work failed"):
        with OpenAICompatibleChatClient(AIConfig.from_env(settings()), http_client=transport_client):
            raise RuntimeError("work failed")
    assert transport_client.is_closed
