"""긴 릴리즈 노트의 구간 추출은 문맥·원본 좌표를 보존하고 일부 성공을 저장에 넘기지 않는다."""

import json
import re
from threading import Event, Lock

import pytest

from setron.features.ai.schemas import AIStatus, ExtractKind
from setron.features.extraction import service
from setron.features.extraction.normalization import normalize
from setron.features.extraction.schemas import CellSource
from setron.infrastructure.ai_client import AITimeoutError, ChatReply, ExampleChatClient, ExampleFailure, ResponseSource
from setron.infrastructure.excel_reader import MergedRange, SheetData


@pytest.fixture
def sheet(monkeypatch):
    monkeypatch.setattr(service, "RELEASE_CHUNK_ROWS", 4)
    return SheetData("검증.xlsx", "Memory", 5, 2, [
        ["M-O2 Release", None, None], ["Version", "No", "Description"],
        ["v1", 1, "변경 1"], [None, 2, "변경 2"], [None, 3, "변경 3"],
        ["v2", 1, "변경 4"],
    ], [MergedRange(7, 2, 9, 2)])


def reply(*positions):
    return json.dumps({"rows": [
        {"values": {"model_name": "M-O2", "family_name": "O2", "version_raw": version,
                    "item_no": str(number), "content": content},
         "sources": {"model_name": "B5", "family_name": "B5", "version_raw": version_cell,
                     "item_no": f"C{row}", "content": f"D{row}"}}
        for row, number, content, version, version_cell in positions
    ], "review_notes": []}, ensure_ascii=False)


FIRST = ((7, 1, "변경 1", "v1", "B7"), (8, 2, "변경 2", "v1", "B7"))
LAST = ((9, 3, "변경 3", "v1", "B7"), (10, 1, "변경 4", "v2", "B10"))


def test_chunks_keep_shared_headers_merged_versions_and_original_offsets(sheet):
    client = ExampleChatClient([reply(*FIRST), reply(*LAST)])
    result = service.extract_sheet_data(client, ExtractKind.RELEASE_NOTE, sheet)
    assert result.ok
    assert [row.values["content"] for row in result.data.rows] == [f"변경 {i}" for i in range(1, 5)]
    assert result.data.rows[2].sources["version_raw"].address == "B7"
    assert not result.data.issues
    assert result.data.raw_data["Memory"]["D9"] == "변경 3"
    first, second = (call[-1].content for call in client.calls)
    assert "5~8행" in first and "9~10행" in second
    assert all("B5: M-O2 Release" in content and "B7:B9" in content
               for content in (first, second))
    assert "B5:D8" in first and "B9:D10" in second
    assert "D10: 변경 4" not in first and "D7: 변경 1" not in second
    assert sheet.values[4][0] is None


def test_failed_later_chunk_discards_all_partial_rows_and_does_not_retry(sheet):
    client = ExampleChatClient([reply(*FIRST), ExampleFailure(AITimeoutError("검증용 시간 초과"))])
    result = service.extract_sheet_data(client, ExtractKind.RELEASE_NOTE, sheet)
    assert not result.ok and result.data is None
    assert "9~10행 추출 실패" in result.message
    assert len(client.calls) == 2


def test_out_of_range_content_is_rejected_instead_of_duplicate_import(sheet):
    client = ExampleChatClient([reply(*FIRST), reply(*FIRST)])
    result = service.extract_sheet_data(client, ExtractKind.RELEASE_NOTE, sheet)
    assert result.status is AIStatus.INVALID_RESPONSE and result.data is None
    assert "9~10행 밖" in result.message


def test_parallel_replies_are_bounded_and_merged_in_source_order(sheet):
    lock, second_done = Lock(), Event()
    completed, updates = [], []

    class Client:
        running = 0
        peak = 0

        def complete(self, messages):
            first = int(re.search(r"추출 대상 행: ([0-9]+)", messages[-1].content)[1])
            with lock:
                self.running += 1
                self.peak = max(self.peak, self.running)
            try:
                if first == 5:
                    assert second_done.wait(3)
                    return ChatReply(reply(*FIRST), ResponseSource.EXAMPLE)
                return ChatReply(reply(*LAST), ResponseSource.EXAMPLE)
            finally:
                with lock:
                    self.running -= 1
                    completed.append(first)
                if first != 5:
                    second_done.set()

    client = Client()
    result = service.extract_sheet_data(client, ExtractKind.RELEASE_NOTE, sheet,
                                        max_workers=2, progress=updates.append)
    assert result.ok and client.peak == 2 and completed == [9, 5]
    assert [row.values["content"] for row in result.data.rows] == [f"변경 {i}" for i in range(1, 5)]
    assert [(value.completed, value.total) for value in updates if value.stage == "extract"] == [(0, 2), (1, 2), (2, 2)]
    assert updates[-1].stage == "review"


def test_cancellation_stops_remaining_chunks(sheet):
    stop = Event()
    client = ExampleChatClient([reply(*FIRST), reply(*LAST)])

    def update(progress):
        if progress.completed == 1:
            stop.set()

    with pytest.raises(InterruptedError):
        service.extract_sheet_data(client, ExtractKind.RELEASE_NOTE, sheet, progress=update, cancelled=stop.is_set)
    assert len(client.calls) == 1


def test_parallel_cancel_does_not_launch_the_next_chunk(sheet):
    sheet.values.extend([[None, None, None] for _ in range(4)])
    started, release, finished, stop = Event(), Event(), Event(), Event()
    calls = []

    class Client:
        def complete(self, messages):
            first = int(re.search(r"추출 대상 행: ([0-9]+)", messages[-1].content)[1])
            calls.append(first)
            if first == 5:
                assert started.wait(3)
                return ChatReply(reply(*FIRST), ResponseSource.EXAMPLE)
            started.set()
            try:
                assert release.wait(3)
                return ChatReply(reply(*LAST), ResponseSource.EXAMPLE)
            finally:
                finished.set()

    def update(progress):
        if progress.completed:
            stop.set()

    try:
        with pytest.raises(InterruptedError):
            service.extract_sheet_data(Client(), ExtractKind.RELEASE_NOTE, sheet,
                                       max_workers=2, progress=update, cancelled=stop.is_set)
        assert sorted(calls) == [5, 9]
    finally:
        release.set()
        assert finished.wait(3)


def test_unknown_layout_preserves_full_context_instead_of_guessing_headers(sheet):
    sheet.values[1] = ["사용자 열 1", "사용자 열 2", "사용자 열 3"]
    client = ExampleChatClient([reply(*FIRST), reply(*LAST)])
    assert service.extract_sheet_data(client, ExtractKind.RELEASE_NOTE, sheet).ok
    assert all("B5:D10" in call[-1].content and "D10: 변경 4" in call[-1].content for call in client.calls)


@pytest.mark.parametrize("original, extracted, expected", [
    (7.0, "7.0", "7"), (7.0, "7", "7"),
    (7.5, "7.5", "7.5"), ("7.0", "7.0", "7.0"),
])
def test_only_proven_integral_excel_numbers_are_normalized(original, extracted, expected):
    values = {"item_no": extracted}
    result, _, _ = normalize(values, ExtractKind.RELEASE_NOTE, 1, {"item_no": CellSource("현황", "B3", original)})
    assert result["item_no"] == expected
    assert values["item_no"] == extracted
