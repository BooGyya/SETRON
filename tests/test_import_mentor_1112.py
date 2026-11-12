"""11/12 멘토 피드백 — 데이터 가져오기 (합성 시트로 실제 양식 모양을 재현).

1. SW 계열 필요 없음 → 필수에서 빼고 모델명(번역표)으로 자동 결정
3. 실제 V2 현황표: AI가 HPA 행(X9 · Y9 · Z9 · AA9)을 빠뜨려 '원본 HPA 레코드…' 로 저장 불가
   → 호기 · 버전이 원본에 있으면 그 칸 값으로 채우고 안내 (빈 칸은 지금처럼 막음)
4. P3-S5 현황표가 대시보드 '미지정' → AI가 라인을 빠뜨림 → 블록 제목 칸 · 라인 모양 시트 이름으로 채움
5. 파운드리 릴리즈노트 일부 추출 · 모델명 · SW 계열 빨간색 → 노트 제목 칸에서 모델, 모델에서 계열
"""

from dataclasses import replace

import pytest

from setron.features.ai.schemas import AIResult, AIStatus, AITask, ExtractedRow, ExtractKind, ExtractResult
from setron.features.excel_import.provenance import validate_save_request_sources
from setron.features.excel_import.service import (
    LINE_FROM_SHEET, REQUIRED, build_save_request, draft_from_review, edit_value, validate,
)
from setron.features.extraction.service import HPA_FILLED, review_extraction_result
from setron.features.rule_extraction.service import rule_extract
from setron.infrastructure.excel_reader import SheetData

STATUS, RELEASE = ExtractKind.EQUIPMENT_STATUS, ExtractKind.RELEASE_NOTE
WIDTH = 27                                                   # A ~ AA


def grid(cells, rows=10):
    values = [[None] * WIDTH for _ in range(rows)]
    for address, value in cells.items():
        col = "".join(ch for ch in address if ch.isalpha())
        row = int("".join(ch for ch in address if ch.isdigit()))
        index = 0
        for ch in col:
            index = index * 26 + ord(ch) - 64
        values[row - 1][index - 1] = value
    return values


def v2_sheet(name="V2", title="V2\n( 2F )", hpa_version="v1.0.0"):
    """평택 V2 시트 모양: A1 블록 제목, Poly 묶음(B~G), X열부터 HPA 묶음(설비군 · 호기 · PM · 버전, No · Bay 없음)."""
    cells = {"A1": title, "B1": "Poly", "X1": "HPA",
             "B2": "No", "C2": "Bay", "D2": "설비군", "E2": "설비호기", "F2": "PM", "G2": "Version",
             "X2": "설비군", "Y2": "설비호기", "Z2": "PM", "AA2": "Version",
             "B3": 1, "D3": "M-C3", "E3": "ESPP17", "G3": "v3.52_F17",
             "B4": 2, "D4": "M-C4", "E4": "ESPGH35", "G4": "v4.57.2.2_f1",
             "X9": "HPA", "Y9": "ESHV09", "Z9": "PM1", "AA9": hpa_version}
    return SheetData("평택_실제.xlsx", name, 1, 1, grid(cells))


def drop(result, predicate=lambda values: False, fields=()):
    """AI가 행(predicate) 또는 항목(fields)을 빠뜨린 응답을 만든다."""
    rows = tuple(ExtractedRow({k: (None if k in fields else v) for k, v in r.values.items()},
                              {k: v for k, v in r.sources.items() if k not in fields})
                 for r in result.data.rows if not predicate(r.values))
    return replace(result, data=replace(result.data, rows=rows))


def prepared(sheet, response, kind=STATUS):
    draft = draft_from_review(review_extraction_result(kind, sheet, response).data)
    if kind is STATUS:
        for row in draft.rows:                               # 화면의 기준일 · 사이트 일괄 적용
            draft = edit_value(draft, row.row_id, "base_date", "2026-10-05")
            draft = edit_value(draft, row.row_id, "site", "평택")
    return draft


def errors(draft):
    return [issue for issue in validate(draft) if str(issue.level).endswith("ERROR")]


def saved(draft):
    request = build_save_request(draft, "11/12 멘토 피드백")
    validate_save_request_sources(request)
    return request


# --- 3. HPA ---------------------------------------------------------------------------------------------------
def test_missing_hpa_row_is_filled_from_its_source_cells_and_saves():
    sheet = v2_sheet()
    rule = rule_extract(STATUS, sheet)
    response = drop(rule, predicate=lambda values: values.get("model_name") == "HPA")      # AI가 HPA 행을 빠뜨림
    review = review_extraction_result(STATUS, sheet, response).data
    notes = [issue for issue in review.issues if issue.code == HPA_FILLED]
    assert len(notes) == 1 and not notes[0].blocks_save and "호기 Y9, SW 버전 AA9, PM Z9" in notes[0].reason
    hpa = next(row for row in review.rows if row.values.get("model_name") == "HPA")
    assert (hpa.values["equipment_code"], hpa.values["version_raw"], hpa.values["pm_name"], hpa.values["line"]) == \
        ("ESHV09", "v1.0.0", "PM1", "V2")
    draft = prepared(sheet, response)
    assert not errors(draft)
    assert "ESHV09" in [row.values["equipment_code"] for row in saved(draft).rows]


def test_missing_hpa_with_blank_version_still_blocks():
    """값을 짐작하지 않는다: SW 버전 칸이 빈 HPA는 채우지 않고 지금처럼 막는다."""
    sheet = v2_sheet(hpa_version=None)
    rule = rule_extract(STATUS, sheet)
    response = drop(rule, predicate=lambda values: values.get("model_name") == "HPA")
    review = review_extraction_result(STATUS, sheet, response).data
    assert not any(issue.code == HPA_FILLED for issue in review.issues)
    assert any(issue.code == "HPA_COVERAGE_MISSING" and issue.blocks_save for issue in review.issues)


# --- 4. 라인 ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name, title", [("P3-S5", "P3-S5\n( 3F )"), ("V2", "V2\n( 2F )")])
def test_missing_line_comes_from_the_block_title(name, title):
    sheet = v2_sheet(name, title)
    response = drop(rule_extract(STATUS, sheet), fields=("line",))                        # AI가 라인을 빠뜨림
    draft = prepared(sheet, response)
    assert {row.value("line") for row in draft.rows} == {name}
    assert not errors(draft) and len(saved(draft).rows) == 3


def ai_response(sheet_name, rows):
    data = ExtractResult(STATUS, sheet_name, tuple(ExtractedRow(values, sources) for values, sources in rows), ())
    return AIResult(AITask.EXCEL_EXTRACT, AIStatus.OK, "AI", data)


@pytest.mark.parametrize("sheet_name, expected", [("P3-S5", "P3-S5"), ("v2", "V2"), ("Memory", None), ("Sheet1", None)])
def test_line_shaped_sheet_name_fills_missing_line_without_a_block_title(sheet_name, expected):
    values = [["설비군", "설비호기", "Version"], ["M-C4", "ESPGL29", "v4.56.7.3_f1"]]
    sheet = SheetData("실제.xlsx", sheet_name, 1, 1, values)
    response = ai_response(sheet_name, [({"model_name": "M-C4", "equipment_code": "ESPGL29", "version_raw": "v4.56.7.3_f1"},
                                         {"model_name": "A2", "equipment_code": "B2", "version_raw": "C2"})])
    draft = draft_from_review(review_extraction_result(STATUS, sheet, response).data)
    assert draft.rows[0].value("line") == expected
    assert any(issue.code == LINE_FROM_SHEET for issue in draft.extraction_issues) == (expected is not None)


# --- 1. SW 계열 -------------------------------------------------------------------------------------------------
def test_sw_family_is_not_required_and_comes_from_the_model():
    assert "family_name" not in REQUIRED[STATUS]
    sheet = v2_sheet()
    draft = prepared(sheet, drop(rule_extract(STATUS, sheet), fields=("family_name",)))
    families = {row.value("model_name"): row.value("family_name") for row in draft.rows}
    assert families["M-C3"] == "C3" and families["M-C4"] == "C4"
    assert not errors(draft)                                          # '빈칸 자동 채우기'를 누르지 않아도


def test_user_entered_family_is_kept():
    sheet = v2_sheet()
    draft = prepared(sheet, rule_extract(STATUS, sheet))
    row = next(r for r in draft.rows if r.value("model_name") == "M-C4")
    draft = edit_value(draft, row.row_id, "family_name", "C4 (파운드리)")
    assert draft.row(row.row_id).value("family_name") == "C4 (파운드리)"


# --- 5. 릴리즈노트 --------------------------------------------------------------------------------------------
def note_sheet():
    """세로형 정기 Release List 모양 (교육용 M-O5 노트와 같은 머리글)."""
    values = [["P-PJT M-O5 SW. 정기 Release List", None, None, None, None],
              [None] * 5,
              ["버전명", "No", "항목", "JIRA", "Update내용"],
              ["v4.56.62.1_a1 (2026-01-08)", "1", "UI", "ETOJ-6532", "MES Recipe 승인 상태 확인 후 Process 실행"],
              [None, "2", "PMC", "ETOJ-5345", "FCS Recipe Step 전환 시 Parameter 검증"]]
    return SheetData("P_PJT_M-O5_실제.xlsx", "Release List", 1, 1, values)


def test_release_note_model_and_family_survive_an_ai_that_drops_them():
    sheet = note_sheet()
    rule = rule_extract(RELEASE, sheet)
    response = drop(rule, fields=("model_name", "family_name", "group_name"))
    draft = prepared(sheet, response, RELEASE)
    assert {(row.value("model_name"), row.value("family_name")) for row in draft.rows} == {("M-O5", "O5")}
    assert not errors(draft)
    request = saved(draft)
    assert len(request.rows) == 2 and request.rows[0].source_reference["model_name"]["cells"] == ["A1"]


# --- 확인용 V2(AI 추출)에서 드러난 저장 직전 Memory/Foundry 오류 ---------------------------------------------------------
@pytest.mark.parametrize("title, cited, value, saved_area", [
    ("V2 Foundry\n( 2F )", "A1", "Foundry", "FOUNDRY"),   # 제목 칸을 근거로 → 검토는 통과, 저장 직전 '원본 셀 값과 다릅니다'였음
    ("V2\n( 2F )", "B1", "FOUNDRY", None),                 # 관계없는 칸을 근거로 → AI 값은 쓰지 않음(저장 때 장소표로)
])
def test_ai_memory_foundry_follows_the_site_and_line(title, cited, value, saved_area):
    base = v2_sheet()
    values = [list(row) for row in base.values]
    values[0][0] = title
    sheet = SheetData(base.workbook_name, "V2", 1, 1, values)
    rule = rule_extract(STATUS, base)
    rows = tuple(ExtractedRow({**r.values, "production_area": value}, {**r.sources, "production_area": cited})
                 for r in rule.data.rows)
    draft = prepared(sheet, replace(rule, data=replace(rule.data, rows=rows)))
    assert not errors(draft)
    assert {row.values.get("production_area") for row in saved(draft).rows} == {saved_area}


def test_ai_memory_foundry_against_the_location_table_is_flagged_at_review():
    base = v2_sheet()
    values = [list(row) for row in base.values]
    values[0][0] = "V2 Memory\n( 2F )"
    sheet = SheetData(base.workbook_name, "V2", 1, 1, values)
    rule = rule_extract(STATUS, base)
    rows = tuple(ExtractedRow({**r.values, "production_area": "Memory"}, {**r.sources, "production_area": "A1"})
                 for r in rule.data.rows)
    draft = prepared(sheet, replace(rule, data=replace(rule.data, rows=rows)))
    messages = [issue.message for issue in errors(draft) if issue.field == "production_area"]
    assert messages and "라인 V2은(는) Foundry입니다" in messages[0]


def test_ai_split_of_block_title_into_site_and_line_is_rejoined():
    """AI가 블록 제목 'P3-S5 ( 3F )'를 사업장 'P3' + 라인 'S5'로 쪼개 읽으면, 수정 전에는 오류 없이 그대로 저장돼
    대시보드에서 '미지정'(멘토 피드백 4의 실제 원인)이었다. 라인은 'P3-S5'로, 사업장은 일괄 적용 값으로."""
    sheet = v2_sheet("P3-S5", "P3-S5\n( 3F )")
    rule = rule_extract(STATUS, sheet)
    rows = tuple(ExtractedRow({**r.values, "site": "P3", "line": "S5"}, {**r.sources, "site": "A1", "line": "A1"})
                 for r in rule.data.rows)
    review = review_extraction_result(STATUS, sheet, replace(rule, data=replace(rule.data, rows=rows))).data
    assert {(row.values.get("site"), row.values.get("line")) for row in review.rows} == {(None, "P3-S5")}
    draft = prepared(sheet, replace(rule, data=replace(rule.data, rows=rows)))
    assert {(row.values["site"], row.values["line"]) for row in saved(draft).rows} == {("평택", "P3-S5")}


def test_real_site_names_are_kept():
    sheet = v2_sheet("P3-S5", "P3-S5\n( 3F )")
    rule = rule_extract(STATUS, sheet)
    rows = tuple(ExtractedRow({**r.values, "site": None, "line": "S5"}, {**r.sources, "line": "A1"}) for r in rule.data.rows)
    review = review_extraction_result(STATUS, sheet, replace(rule, data=replace(rule.data, rows=rows))).data
    assert {row.values.get("line") for row in review.rows} == {"P3-S5"}
