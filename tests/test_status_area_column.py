"""설비 현황표의 Memory/Foundry 열을 규칙 추출로 읽는다 (11-17 시연 준비: 평택 시트의 Memory/Foundry가 비어 보임).

열이 있으면 그 값으로 검토 표의 Memory/Foundry 칸을 채우고, 없으면 지금처럼 비워 둔다(저장 때 장소표 · 등록 라인으로 정함).
"""

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.candidates import apply_model_candidates
from setron.features.excel_import.service import draft_from_review, edit_value, has_errors, validate
from setron.features.extraction.service import review_extraction_result
from setron.features.rule_extraction.service import rule_extract
from setron.infrastructure.excel_reader import SheetData

STATUS = ExtractKind.EQUIPMENT_STATUS


def sheet(header, rows):
    values = [["평택 설비현황표 · 기준일 2026-11-01 (시연용)"] + [None] * (len(header) - 1), header, *rows]
    return SheetData("평택.xlsx", "평택 2026-11-01", 1, 1, values)


def draft_of(source):
    result = rule_extract(STATUS, source)
    assert result is not None
    draft = draft_from_review(review_extraction_result(STATUS, source, result).data)
    for row in draft.rows:
        draft = edit_value(draft, row.row_id, "base_date", "2026-11-01")
        draft = edit_value(draft, row.row_id, "site", "평택")
    draft, _ = apply_model_candidates(draft)      # 화면의 [빈칸 자동 채우기]와 같은 순서
    return result, draft


@pytest.mark.parametrize("title", ["Memory/Foundry", "memory / foundry", "생산 영역"])
def test_area_column_fills_memory_foundry_with_its_source_cell(title):
    source = sheet(["No", "Line", title, "설비군", "설비호기", "Version"], [
        [1, "P1", "Memory", "M-C4", "EP4P101", "v4.60.1.5"],
        [2, "S5", "Foundry", "M-O3", "EP5S501", "v2.40.5"],
    ])
    result, draft = draft_of(source)
    assert [r.values["production_area"] for r in result.data.rows] == ["Memory", "Foundry"]
    assert [r.sources["production_area"] for r in result.data.rows] == ["C3", "C4"]   # 원본 셀
    assert [r.values["line"] for r in result.data.rows] == ["P1", "S5"]               # 다른 열은 그대로
    assert [r.value("production_area") for r in draft.rows] == ["MEMORY", "FOUNDRY"]
    assert not has_errors(validate(draft))


def test_without_the_column_it_stays_blank_as_before():
    source = sheet(["No", "Line", "설비군", "설비호기", "Version"], [[1, "P1", "M-C4", "EP4P101", "v4.60.1.5"]])
    result, draft = draft_of(source)
    assert result.data.rows[0].values.get("production_area") is None
    assert draft.rows[0].value("production_area") in (None, "")
