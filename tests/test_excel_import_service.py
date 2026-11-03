"""C-1~C-3 업무 로직: 원본 보존, 수정값 분리, 저장 전 검사(S15P31B106-39), 저장 요청과 실패 처리."""

import json
from datetime import datetime

import pytest

from setron.features.ai.schemas import AIResult, AIStatus, AITask, ExtractedRow, ExtractKind, ExtractResult, ReviewNote
from setron.features.excel_import.schemas import DuplicateSaveError, ImportBlockedError, IssueLevel
from setron.features.excel_import.service import (
    MemoryImportSaver, ReferenceData, build_save_request, cell_text, describe_save_error, draft_from_extract,
    edit_value, extract_cells, has_errors, revert_row, set_excluded, snapshot_sheet, validate, value_at,
)
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.ai_client import ResponseSource
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseConnectionError, DatabaseSaveError
from setron.infrastructure.excel_reader import MergedRange, SheetData

HEAD = ["기준일", "Site", "호기", "PM", "모델", "SW 계열", "SW Ver", "구분"]


def sheet(*rows, merged=()):
    return SheetData("현황.xlsx", "Memory", 1, 1, [list(HEAD), *[list(r) for r in rows]], list(merged))


def eq_row(r, **values):
    """r행 값과 같은 셀 주소를 출처로 하는 설비 현황 추출 행."""
    columns = dict(base_date="A", site="B", equipment_code="C", pm_name="D", model_name="E",
                   family_name="F", version_raw="G", production_area="H")
    base = dict(base_date="2026-10-21", site="화성", equipment_code="ETCH-001", pm_name="PM1",
                model_name="M-C4", family_name="C4", version_raw="v4.56.6.43")
    base.update(values)
    return ExtractedRow({k: v for k, v in base.items()}, {k: f"{columns[k]}{r}" for k, v in base.items() if v is not None})


def draft_for(data, rows, *, kind=ExtractKind.EQUIPMENT_STATUS, notes=(), source=ResponseSource.EXAMPLE):
    snapshot = snapshot_sheet(data)
    result = AIResult(AITask.EXCEL_EXTRACT, AIStatus.OK, "ok", ExtractResult(kind, snapshot.sheet_name, tuple(rows), notes), source)
    return draft_from_extract(data.workbook_name, snapshot, result)


LINE2 = [datetime(2026, 10, 21), "화성", "ETCH-001", "PM1", "M-C4", "C4", "v4.56.6.43", None]
LINE3 = [datetime(2026, 10, 21), "화성", "ETCH-001", "PM2", "M-C4", "C4", "v4.56.6.42", None]


def levels(issues, level):
    return [i for i in issues if i.level is level]


def test_snapshot_preserves_raw_values_addresses_and_merged_ranges():
    data = SheetData("a.xlsx", "S", 3, 2, [["병합", None, 1.0], [None, datetime(2026, 1, 2), " 원문 "]],
                     [MergedRange(3, 2, 4, 2)])
    snapshot = snapshot_sheet(data)
    assert snapshot.cells == {"B3": "병합", "C3": None, "D3": 1.0, "B4": None, "C4": "2026-01-02", "D4": " 원문 "}
    assert snapshot.merged == ("B3:B4",)
    assert value_at(snapshot, "B4") == "병합"  # 병합 범위 안 칸은 왼쪽 위 값
    assert extract_cells(snapshot) == [("B3", "병합"), ("D3", "1"), ("C4", "2026-01-02"), ("D4", " 원문 ")]
    assert cell_text(1.5) == "1.5" and cell_text(datetime(2026, 1, 2, 9, 30)) == "2026-01-02 09:30:00"
    json.dumps(snapshot.cells)  # raw_data로 그대로 저장할 수 있어야 한다.


def test_failed_or_other_sheet_result_is_not_a_draft():
    snapshot = snapshot_sheet(sheet(LINE2))
    with pytest.raises(ValueError):
        draft_from_extract("x", snapshot, AIResult(AITask.EXCEL_EXTRACT, AIStatus.FAILED, "실패"))
    other = AIResult(AITask.EXCEL_EXTRACT, AIStatus.OK, "ok", ExtractResult(ExtractKind.EQUIPMENT_STATUS, "Other", ()))
    with pytest.raises(ValueError):
        draft_from_extract("x", snapshot, other)


def test_edit_is_kept_apart_from_ai_original_and_can_be_reverted():
    draft = draft_for(sheet(LINE2), [eq_row(2)])
    edited = edit_value(draft, 1, "site", "평택")
    row = edited.row(1)
    assert row.value("site") == "평택" and row.original["site"] == "화성" and row.edited_fields == ("site",)
    assert edit_value(edited, 1, "site", "화성").row(1).edits == {}  # 원본과 같으면 수정 기록 없음
    assert revert_row(edited, 1).row(1).value("site") == "화성"
    assert edit_value(draft, 1, "pm_name", "  ").row(1).value("pm_name") is None
    with pytest.raises(ValueError):
        edit_value(draft, 1, "content", "릴리즈 항목")
    assert draft.row(1).edits == {}  # 이전 초안은 바뀌지 않는다.


@pytest.mark.parametrize("model", ["M-C4", "HPA"], ids=["일반 설비", "HPA도 같음"])
def test_blank_required_value_blocks_whole_batch(model):
    family = "HPA" if model == "HPA" else "C4"
    normal, missing = list(LINE2), list(LINE3)
    for original in (normal, missing):
        original[4:6] = [model, family]
    missing[6] = None
    blank_row = eq_row(3, pm_name="PM2", model_name=model, family_name=family, version_raw=None)
    blank_row = ExtractedRow(blank_row.values, {**blank_row.sources, "version_raw": "G3"})
    draft = draft_for(sheet(normal, missing), [eq_row(2, model_name=model, family_name=family), blank_row])
    issues = validate(draft)
    errors = levels(issues, IssueLevel.ERROR)
    assert [(e.row_id, e.field) for e in errors] == [(2, "version_raw")]
    with pytest.raises(ImportBlockedError):
        build_save_request(draft, "r1")


def test_optional_production_area_blank_is_allowed_and_values_are_checked():
    draft = draft_for(sheet(LINE2), [eq_row(2)])
    assert not has_errors(validate(draft))
    memory = edit_value(draft, 1, "production_area", "Memory")
    assert build_save_request(memory, "r").rows[0].values["production_area"] == "MEMORY"
    wrong = edit_value(draft, 1, "production_area", "메모리")
    assert [e.field for e in levels(validate(wrong), IssueLevel.ERROR)] == ["production_area"]


@pytest.mark.parametrize("field,value", [("base_date", "10/21"), ("equipment_code", "E" * 51), ("version_raw", "v1\nv2")],
                         ids=["날짜 형식", "길이 초과", "버전 줄바꿈"])
def test_format_errors_block(field, value):
    draft = edit_value(draft_for(sheet(LINE2), [eq_row(2)]), 1, field, value)
    assert [e.field for e in levels(validate(draft), IssueLevel.ERROR)] == [field]


def test_ai_value_is_checked_against_original_cell():
    """AI 값의 원본 대조와 수정 후 목록형 셀 출처 보존을 검증한다."""
    data = sheet(LINE2)
    draft = draft_for(data, [eq_row(2, site="평택")])
    errors = levels(validate(draft), IssueLevel.ERROR)
    assert any(i.field == "site" and i.code == "SOURCE_MISMATCH" and "원본 B2: 화성" in i.message for i in errors)
    with pytest.raises(ImportBlockedError):
        build_save_request(draft, "unreviewed-source-mismatch")
    # 사용자가 고친 값은 원본과 다를 수 있다. 원래 셀 위치는 그대로 남는다.
    fixed = edit_value(draft, 1, "site", "평택시")
    assert not any(i.field == "site" for i in validate(fixed))
    assert build_save_request(fixed, "r").rows[0].source_reference["site"] == {"sheet": "Memory", "cells": ["B2"]}
    outside = draft_for(data, [ExtractedRow(eq_row(2).values, {**eq_row(2).sources, "pm_name": "Z99"})])
    assert any(i.field == "pm_name" and i.blocks_save and i.code == "SOURCE_OUTSIDE"
               and "범위 밖" in i.message for i in validate(outside))
    no_source = draft_for(data, [ExtractedRow(eq_row(2).values, {})])
    assert len([i for i in validate(no_source) if "원본 셀 위치가 없습니다" in i.message]) == 7


def test_merged_cell_source_uses_top_left_value():
    data = SheetData("a.xlsx", "Memory", 1, 1, [["화성", None], ["E-1", "v1"]], [MergedRange(1, 1, 1, 2)])
    row = ExtractedRow({"site": "화성", "equipment_code": "E-1"}, {"site": "B1", "equipment_code": "A2"})
    issues = validate(draft_for(data, [row]))
    assert not any(i.field in ("site", "equipment_code") and i.level in (IssueLevel.ERROR, IssueLevel.REVIEW) for i in issues)


def test_same_pm_conflict_blocks_but_exact_duplicate_merges_with_sources():
    """동일 PM 상충은 차단하고 정확한 중복의 원본 위치는 목록으로 함께 보존한다."""
    data = sheet(LINE2, LINE2, LINE3)
    conflicting_line = list(LINE2)
    conflicting_line[6] = "v4.56.6.42"
    conflict = draft_for(sheet(LINE2, conflicting_line), [eq_row(2), eq_row(3, version_raw="v4.56.6.42")])
    errors = levels(validate(conflict), IssueLevel.ERROR)
    assert {e.row_id for e in errors} == {1, 2} and all("같은 PM" in e.message for e in errors)
    duplicate = draft_for(data, [eq_row(2), eq_row(3)])
    issues = validate(duplicate)
    assert not has_errors(issues) and any(i.row_id == 2 and "정확히 같은 행" in i.message for i in issues)
    request = build_save_request(duplicate, "r")
    assert len(request.rows) == 1 and request.rows[0].row_ids == (1, 2)
    assert request.rows[0].source_reference["version_raw"]["cells"] == ["G2", "G3"]


def test_different_pm_version_is_non_blocking_exception():
    draft = draft_for(sheet(LINE2, LINE3), [eq_row(2), eq_row(3, pm_name="PM2", version_raw="v4.56.6.42")])
    issues = validate(draft)
    assert not has_errors(issues)
    assert len([i for i in issues if i.level is IssueLevel.INFO and "PM 불일치" in i.message]) == 2


def test_same_equipment_and_date_must_share_site_and_model():
    conflicting_site = list(LINE3)
    conflicting_site[1] = "평택"
    draft = draft_for(sheet(LINE2, conflicting_site), [eq_row(2), eq_row(3, pm_name="PM2", site="평택", version_raw="v4.56.6.42")])
    errors = levels(validate(draft), IssueLevel.ERROR)
    assert {e.row_id for e in errors} == {1, 2} and all(e.code == "SITE_CONFLICT" for e in errors)


def test_release_note_rules():
    data = SheetData("r.xlsx", "Release", 1, 1, [["Version", "Date", "No", "Content"], ["v1", "2026-09-01", "1", "A"]], [])
    rows = [ExtractedRow({"version_raw": "v1", "release_date": "2026-09-01", "item_no": "1", "content": "A"}),
            ExtractedRow({"version_raw": "v1", "release_date": "2026-09-02", "item_no": "1", "content": "B"}),
            ExtractedRow({"version_raw": "v2", "item_no": "x"}),
            ExtractedRow({"version_raw": "v3", "item_no": "3"})]
    issues = validate(draft_for(data, rows, kind=ExtractKind.RELEASE_NOTE))
    messages = {(i.row_id, i.field): i.message for i in levels(issues, IssueLevel.ERROR)}
    assert "출시일" in messages[(1, "release_date")] and "내용" in messages[(2, "content")]
    assert (3, "item_no") in messages and (4, "content") in messages
    assert all(i.field != "base_date" for i in issues)  # 설비 현황 필수값을 요구하지 않는다.


def test_reference_data_flags_unknown_equipment_and_version_without_blocking():
    draft = draft_for(sheet(LINE2), [eq_row(2)])
    assert any("기준 자료 미연결" in i.message for i in validate(draft))
    reference = ReferenceData(frozenset({"ETCH-999"}), {"C4": frozenset({"v4.56.6.42"})})
    issues = validate(draft, reference)
    assert not has_errors(issues)
    assert {i.field for i in levels(issues, IssueLevel.REVIEW) if "등록되지 않은" in i.message} == {"equipment_code", "version_raw"}


def test_save_request_keeps_raw_sheet_without_file_name_and_excludes_rows():
    draft = draft_for(sheet(LINE2, LINE3), [eq_row(2), eq_row(3, pm_name="PM2", version_raw="v4.56.6.42")],
                      notes=(ReviewNote("기준일 확인", "base_date", "A2"),))
    draft = set_excluded(edit_value(draft, 1, "model_name", "M-C4 Plus"), 2, True)
    request = build_save_request(draft, "req")
    assert "현황.xlsx" not in json.dumps(request.raw_data, ensure_ascii=False)
    assert request.workbook_name == "현황.xlsx" and request.raw_data["sheet"] == "Memory"
    assert request.raw_data["cells"]["G3"] == "v4.56.6.42"  # 제외한 행의 원본 셀도 보존
    assert [r.row_ids for r in request.rows] == [(1,)]
    assert request.rows[0].edited_fields == ("model_name",) and request.rows[0].values["base_date"] == "2026-10-21"
    assert request.review_count >= 1 and request.ai_source is ResponseSource.EXAMPLE
    nothing = set_excluded(draft, 1, True)
    assert any("저장할 행이 없습니다" in i.message for i in levels(validate(nothing), IssueLevel.ERROR))


def test_memory_saver_rejects_duplicate_request_and_replays_failures():
    request = build_save_request(draft_for(sheet(LINE2), [eq_row(2)]), "same")
    saver = MemoryImportSaver(failures=[DatabaseSaveError("저장 실패")])
    with pytest.raises(DatabaseSaveError):
        saver.save(request)
    assert saver.saved == []
    receipt = saver.save(request)
    assert receipt.batch_id == 1 and receipt.row_count == 1
    with pytest.raises(DuplicateSaveError):
        saver.save(request)
    assert len(saver.saved) == 1


@pytest.mark.parametrize("error,state", [
    (DatabaseCommitUnknownError("저장 결과를 확인할 수 없습니다."), "unknown"),
    (DuplicateSaveError("x"), "duplicate"),
    (DatabaseSaveError("저장 묶음을 취소했습니다."), "failed"),
    (DatabaseConnectionError("DB에 연결하지 못했습니다."), "failed"),
    (StorageValidationError("equipment.equipment_code: 길이"), "failed"),
])
def test_save_errors_map_to_screen_states(error, state):
    assert describe_save_error(error)[0] == state


def test_unexpected_error_hides_internal_details():
    state, message = describe_save_error(RuntimeError("password=secret"))
    assert state == "failed" and "secret" not in message
