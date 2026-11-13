"""자료형·접속 설정만 읽는 시작 경로는 실제 AI·Excel SDK를 로드하지 않는다."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from setron.infrastructure import ai_client


def test_schemas_and_unused_configured_client_do_not_import_external_sdks():
    source = """
import sys
from setron.features.ai.schemas import AIStatus
from setron.features.excel_import.schemas import ImportComparison
from setron.infrastructure.excel_reader import SheetData
from setron.infrastructure.ai_client import AIConfig, OpenAICompatibleChatClient, ResponseSource
client = OpenAICompatibleChatClient(AIConfig('https://example.test/v1', 'test-key', 'test-model'))
client.close()
assert 'openai' not in sys.modules
assert 'xlwings' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", source], check=True, capture_output=True, text=True, timeout=15)


def test_sdk_client_is_created_once_on_first_request_and_is_not_reopened_after_close(monkeypatch):
    created, calls, closed = [], [], []

    def complete(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(model="test-model", choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content="답변"))])

    sdk_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=complete)),
        close=lambda: closed.append(True),
    )

    def create(**kwargs):
        created.append(kwargs)
        return sdk_client

    monkeypatch.setattr(ai_client, "openai", SimpleNamespace(OpenAI=create))
    client = ai_client.OpenAICompatibleChatClient(
        ai_client.AIConfig("https://example.test/v1", "test-key", "test-model"))
    assert created == []
    for _ in range(2):
        assert client.complete([ai_client.ChatMessage("user", "질문")]).content == "답변"
    assert len(created) == 1 and len(calls) == 2
    client.close()
    client.close()
    assert closed == [True]
    with pytest.raises(ai_client.AIConnectionError):
        client.complete([ai_client.ChatMessage("user", "질문")])
    assert len(created) == 1
