"""실제 회사 AI 없이 예제 응답으로 AI 호출 흐름을 검증한다 (BE-005 완료 조건)."""

import pytest

from setron.features.ai.examples import example_client
from setron.features.ai.schemas import AIStatus, AITask, ExtractKind, TargetView
from setron.features.ai.service import analyze, extract_sheet, parse_question
from setron.infrastructure.ai_client import (
    AIConnectionError,
    ExampleChatClient,
    ExampleFailure,
    ResponseSource,
)


def test_parse_question_returns_conditions_from_example_reply():
    client = example_client(AITask.QUERY_PARSE)

    result = parse_question(client, "화성 P1D 라인 C4 설비의 43 버전 적용 현황 보여줘")

    assert result.ok
    assert result.data.sites == ("화성",)
    assert result.data.lines == ("P1D",)
    assert result.data.versions == ("v4.56.6.43",)
    assert result.data.target_view is TargetView.DASHBOARD
    assert result.source is ResponseSource.EXAMPLE
    system, user = client.calls[0]
    assert system.role == "system" and "JSON" in system.content
    assert user.content == "화성 P1D 라인 C4 설비의 43 버전 적용 현황 보여줘"


def test_analyze_sends_facts_and_separates_evidence_from_assumptions():
    client = example_client(AITask.ANALYSIS)

    result = analyze(client, "P1D C4 버전이 왜 나뉘었어?", {"versions": {"41": 2, "42": 3, "43": 5}})

    assert result.ok
    assert result.data.evidence and result.data.assumptions and result.data.unconfirmed
    assert '"43": 5' in client.calls[0][1].content


def test_extract_sheet_keeps_cell_addresses_and_review_notes():
    client = example_client(AITask.EXCEL_EXTRACT)

    result = extract_sheet(client, ExtractKind.EQUIPMENT_STATUS, "Memory", [("A1", "화성"), ("C5", "ETCH-001")])

    assert result.ok
    row = result.data.rows[0]
    assert row.values["equipment_code"] == "ETCH-001"
    assert row.sources["equipment_code"] == "C5"
    assert result.data.review_notes[0].field == "base_date"
    assert "A1: 화성" in client.calls[0][1].content


@pytest.mark.parametrize(
    ("failure", "status", "message"),
    [
        ("connection", AIStatus.FAILED, "연결하지 못했습니다"),
        ("timeout", AIStatus.FAILED, "제한 시간"),
        ("rate_limit", AIStatus.RATE_LIMITED, "호출 제한"),
    ],
)
def test_failures_become_result_status_and_retry_succeeds(failure, status, message):
    client = example_client(AITask.QUERY_PARSE, failure)

    first = parse_question(client, "질문")
    second = parse_question(client, "질문")

    assert first.status is status
    assert message in first.message
    assert first.data is None
    assert second.ok


@pytest.mark.parametrize(
    "content",
    [
        "죄송하지만 답할 수 없습니다.",
        '{"sites": "화성"}',
        '{"target_view": "MAP"}',
        "[1, 2]",
        '{"sites": ["화성"',
    ],
    ids=["JSON 없음", "목록 아님", "모르는 화면", "객체 아님", "깨진 JSON"],
)
def test_wrong_format_is_invalid_response(content):
    result = parse_question(ExampleChatClient([content]), "질문")

    assert result.status is AIStatus.INVALID_RESPONSE
    assert result.data is None
    assert result.raw_content == content


def test_json_wrapped_in_code_block_is_accepted():
    content = '답변입니다.\n```json\n{"sites": ["평택"], "unresolved": ["3층"]}\n```'

    result = parse_question(ExampleChatClient([content]), "평택 3층 설비")

    assert result.ok
    assert result.data.sites == ("평택",)
    assert result.data.unresolved == ("3층",)


def test_analysis_without_answer_is_invalid():
    result = analyze(ExampleChatClient(['{"answer": "", "evidence": []}']), "질문", {})

    assert result.status is AIStatus.INVALID_RESPONSE


def test_extract_with_unknown_field_is_invalid():
    content = '{"rows": [{"values": {"serial": "123"}}]}'

    result = extract_sheet(ExampleChatClient([content]), ExtractKind.EQUIPMENT_STATUS, "Memory", [])

    assert result.status is AIStatus.INVALID_RESPONSE


def test_example_client_runs_out_of_replies():
    client = ExampleChatClient([ExampleFailure(AIConnectionError("x"))])

    assert parse_question(client, "질문").status is AIStatus.FAILED
    assert parse_question(client, "질문").status is AIStatus.FAILED
