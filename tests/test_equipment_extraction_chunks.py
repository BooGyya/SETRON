"""큰 병렬 설비 현황표를 원본 행 구간으로 나눠 추출하고 전체 원본으로 검증한다."""

from copy import deepcopy
import json
import re

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.extraction import service
from setron.infrastructure.ai_client import AITimeoutError, ChatReply, ResponseSource
from setron.infrastructure.excel_reader import MergedRange, SheetData


@pytest.fixture
def sheet(monkeypatch):
    monkeypatch.setattr(service, "EQUIPMENT_CHUNK_CELLS", 24)
    return SheetData("병렬_시험.xlsx", "P1", 5, 2,
        [["모델", "설비호기", "Version", None, "모델", "설비호기", "Version"],
         *[["M-O2", f"TEST-O2-{i}", f"v{i}", None, "M-C4", f"TEST-C4-{i}", f"c{i}"]
           for i in range(12)]])


class Client:
    def __init__(self, sheet, *, fail_at=None, wrong_row=False):
        self.sheet, self.calls = sheet, []
        self.fail_at, self.wrong_row = fail_at, wrong_row

    def complete(self, messages):
        self.calls.append(messages)
        if len(self.calls) == self.fail_at:
            raise AITimeoutError("시험 시간 초과")
        bounds = re.search(r"추출 대상 행: ([0-9]+)~([0-9]+)행", messages[-1].content)
        first, last = tuple(map(int, bounds.groups())) if bounds else (6, 17)
        if self.wrong_row:
            first, last = 17, 17
        rows = []
        for index in range(1, self.sheet.row_count):
            row_number = self.sheet.first_row + index
            if not first <= row_number <= last:
                continue
            for offset in (0, 4):
                values, sources = {}, {}
                for name, col in (("model_name", offset), ("equipment_code", offset + 1), ("version_raw", offset + 2)):
                    address = self.sheet.address_of(index, col)
                    for area in self.sheet.merged_ranges:
                        if area.contains(row_number, self.sheet.first_col + col):
                            address = service.cell_address(area.top, area.left)
                    values[name] = self.sheet.values[int(re.search(r"[0-9]+$", address)[0]) - self.sheet.first_row][col]
                    sources[name] = address
                rows.append(dict(values=values, sources=sources))
        return ChatReply(json.dumps(dict(rows=rows)), ResponseSource.EXAMPLE)


def test_parallel_tables_are_partitioned_without_losing_original_context(sheet):
    original, updates = deepcopy(sheet), []
    client = Client(sheet)
    result = service.extract_sheet_data(client, ExtractKind.EQUIPMENT_STATUS, sheet, progress=updates.append)
    assert result.ok and len(client.calls) == 4
    assert len(result.data.rows) == 24
    assert {row.values["equipment_code"] for row in result.data.rows} == {
        f"TEST-{model}-{i}" for model in ("O2", "C4") for i in range(12)}
    assert sheet == original and result.data.raw_data["P1"]["H17"] == "c11"
    assert [p.completed for p in updates if p.stage == "extract"] == [0, 1, 2, 3, 4]
    assert updates[-1].stage == "review"
    for call in client.calls:
        assert "B5: 모델" in call[-1].content and "H17: c11" in call[-1].content
        assert "모든 병렬 표" in call[-1].content and "content의 원본 셀" not in call[-1].content


def test_shared_merged_version_uses_the_current_equipment_row(sheet):
    sheet.merged_ranges.append(MergedRange(6, 4, 17, 4))
    for row in sheet.values[2:]:
        row[2] = None
    client = Client(sheet)
    result = service.extract_sheet_data(client, ExtractKind.EQUIPMENT_STATUS, sheet)
    assert result.ok and len(result.data.rows) == 24
    assert all(row.sources["version_raw"].address == "D6" for row in result.data.rows[::2])


@pytest.mark.parametrize("failure", ["timeout", "outside"])
def test_failed_chunk_cannot_return_partial_data(sheet, failure):
    client = Client(sheet, fail_at=2 if failure == "timeout" else None, wrong_row=failure == "outside")
    result = service.extract_sheet_data(client, ExtractKind.EQUIPMENT_STATUS, sheet)
    assert not result.ok and result.data is None
    assert len(client.calls) == (2 if failure == "timeout" else 1)
    assert ("추출 실패" if failure == "timeout" else "행 밖의 설비 항목") in result.message


def test_unconfirmed_layout_keeps_single_full_sheet_request(sheet):
    sheet.values[0] = ["미확인 머리글"] * 7
    client = Client(sheet)
    result = service.extract_sheet_data(client, ExtractKind.EQUIPMENT_STATUS, sheet)
    assert result.ok and len(client.calls) == 1
    assert "추출 대상 행:" not in client.calls[0][-1].content


def test_omitted_equipment_is_a_blocking_issue_at_its_original_cell(sheet):
    client = Client(sheet)
    complete = client.complete

    def omit(messages):
        response = complete(messages)
        data = json.loads(response.content)
        data["rows"] = [row for row in data["rows"] if row["sources"]["equipment_code"] != "C10"]
        return ChatReply(json.dumps(data), ResponseSource.EXAMPLE)

    client.complete = omit
    result = service.extract_sheet_data(client, ExtractKind.EQUIPMENT_STATUS, sheet)
    assert result.ok and len(result.data.rows) == 23
    missing = [issue for issue in result.data.issues if issue.code == "EQUIPMENT_COVERAGE_MISSING"]
    assert len(missing) == 1 and missing[0].address == "C10" and missing[0].blocks_save


def test_repeated_headers_and_merged_titles_are_not_equipment_records(sheet):
    sheet.values.insert(5, list(sheet.values[0]))
    sheet.values.append(["Poly", None, None, None, "Oxide", None, None])
    sheet.merged_ranges.extend([MergedRange(19, 2, 19, 4), MergedRange(19, 6, 19, 8)])
    issues = service.equipment_coverage_issues(sheet, ())
    assert len(issues) == 24
    assert not {"C10", "G10", "B19", "F19"} & {issue.address for issue in issues}


def test_duplicate_source_does_not_cover_missing_equipment_but_verified_completion_does(sheet):
    result = service.extract_sheet_data(Client(sheet), ExtractKind.EQUIPMENT_STATUS, sheet)
    records = [(row.values, {name: source.address for name, source in row.sources.items()})
               for row in result.data.rows]
    missing = next(record for record in records if record[1]["equipment_code"] == "C10")
    incomplete = [record for record in records if record is not missing]
    incomplete.append(incomplete[0])
    issues = service.recheck_hpa_coverage(sheet, incomplete)
    assert [issue.address for issue in issues if issue.code == "EQUIPMENT_COVERAGE_MISSING"] == ["C10"]
    assert not service.recheck_hpa_coverage(sheet, [*incomplete, missing])
