"""AI 접속 설정이 있을 때 화면 공급자가 실제 AI 클라이언트를 쓰는지 검증한다 (BE-005, Jira S15P31B106-75)."""

import json

from setron.features.ai.schemas import AIStatus, ExtractKind
from setron.infrastructure.ai_client import AIConfigError, ExampleChatClient
from setron.ui.ai_connection import ai_providers_from_env

SETTINGS = {"SETRON_AI_BASE_URL": "http://ai.local/v1", "SETRON_AI_API_KEY": "k", "SETRON_AI_MODEL": "m"}


def test_without_settings_screens_keep_example_replies():
    providers = ai_providers_from_env({})

    assert not providers.connected
    assert (providers.extractor, providers.parser, providers.analyzer, providers.problem) == (None, None, None, None)


def test_invalid_settings_fall_back_with_reason():
    def broken(values):
        raise AIConfigError("SETRON_AI_MODEL 값이 필요합니다.")

    providers = ai_providers_from_env(SETTINGS, client_factory=broken)

    assert not providers.connected and "SETRON_AI_MODEL" in providers.problem


def test_providers_share_one_client():
    extract = json.dumps({"rows": [{"values": {"version_raw": "v1"}, "sources": {"version_raw": "A1"}}],
                          "review_notes": []})
    query = json.dumps({"conditions": {}, "unresolved": []})
    client = ExampleChatClient([extract, query], source=ResponseSource.REAL) if "source" in \
        ExampleChatClient.__init__.__code__.co_varnames else ExampleChatClient([extract, query])
    providers = ai_providers_from_env(SETTINGS, client_factory=lambda values: client)

    extracted = providers.extractor(ExtractKind.RELEASE_NOTE, "Release", [("A1", "v1")])
    providers.parser("평택 O2 현황")

    assert providers.connected and providers.client is client
    assert extracted.status is AIStatus.OK and extracted.data.rows[0].values["version_raw"] == "v1"
    assert len(client.calls) == 2
