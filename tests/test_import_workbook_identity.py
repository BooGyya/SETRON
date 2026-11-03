"""표시용 파일명이 같아도 통합문서 식별자로 사업장별 원본을 구분한다."""

from dataclasses import replace

import pytest

from setron.features.ai.schemas import AIResult, AIStatus, AITask, ExtractedRow, ExtractKind, ExtractResult
from setron.features.excel_import.schemas import ImportBlockedError
from setron.features.excel_import.service import (
    ImportValidationService, build_save_request, draft_from_extract, draft_from_review,
    edit_value, has_errors, validate_batch,
)
from setron.features.import_save.service import DatabaseImportSaver

from test_import_validation import draft, extraction_review, row
from test_snapshot_reimport import count, snapshot, storage  # noqa: F401


def workbook(code, site, identity, *, name="설비현황.xlsx", sheet="현황", version="v1"):
    return replace(draft(row(equipment_code=code, site=site, version_raw=version), book=name, sheet=sheet),
                   workbook_id=identity)


def test_same_file_names_with_distinct_workbook_ids_have_independent_site_checks():
    sources = (workbook("HS-1", "화성", "pid-1:C:/화성/설비현황.xlsx"),
               workbook("PT-1", "평택", "pid-2:C:/평택/설비현황.xlsx"))
    assert not has_errors(validate_batch(sources))


def test_one_workbook_id_still_blocks_mixed_sites_across_sheets():
    sources = (workbook("HS-1", "화성", "same-file", sheet="첫 시트"),
               workbook("PT-1", "평택", "same-file", name="표시명 변경.xlsx", sheet="다음 시트"))
    problems = [issue for issue in validate_batch(sources) if issue.code == "SITE_CONFLICT"]
    assert len(problems) == 2 and all(issue.blocks_save for issue in problems)
    assert {issue.workbook_id for issue in problems} == {"same-file"}


def test_legacy_callers_without_identity_keep_filename_grouping():
    sources = (workbook("HS-1", "화성", None), workbook("PT-1", "평택", None, sheet="다른 시트"))
    assert has_errors(validate_batch(sources))


def test_identity_value_cannot_collide_with_legacy_filename_group():
    sources = (workbook("HS-1", "화성", "설비현황.xlsx"), workbook("PT-1", "평택", None))
    assert not has_errors(validate_batch(sources))


def test_same_named_files_keep_their_own_identical_error_messages():
    sources = (workbook("HS-1", "화성", "file-a", version=None),
               workbook("HS-2", "화성", "file-b", version=None))
    problems = [issue for issue in validate_batch(sources)
                if issue.blocks_save and issue.field == "version_raw"]
    assert len(problems) == 2
    assert {issue.workbook_id for issue in problems} == {"file-a", "file-b"}


def test_review_edits_and_save_requests_preserve_identity_outside_raw_data():
    source = workbook("HS-1", "화성", "selected-excel-window")
    edited = edit_value(source, 1, "version_raw", "v2")
    request = build_save_request(edited, "edited")
    assert edited.workbook_id == request.workbook_id == source.workbook_id
    assert request.workbook_name == "설비현황.xlsx"
    assert source.workbook_id not in str(request.raw_data)
    assert "설비현황.xlsx" not in str(request.raw_data)
    assert "workbook_id" not in request.raw_data


def test_ai_extract_constructor_accepts_the_captured_workbook_identity():
    source = workbook("HS-1", "화성", None)
    result = AIResult(AITask.EXCEL_EXTRACT, AIStatus.OK, "정상",
                      ExtractResult(ExtractKind.EQUIPMENT_STATUS, source.sheet.sheet_name,
                                    (ExtractedRow(source.rows[0].original, source.rows[0].sources),)))
    created = draft_from_extract(source.workbook_name, source.sheet, result, workbook_id="captured-window")
    assert created.workbook_id == "captured-window"


def test_normalized_extract_constructor_accepts_workbook_identity():
    created = draft_from_review(extraction_review(), workbook_id="normalized-window")
    assert created.workbook_id == "normalized-window"


def test_prepare_and_save_boundary_preserve_distinct_same_named_workbooks(storage):
    sources = (workbook("HS-1", "화성", "file-a"), workbook("PT-1", "평택", "file-b"))
    requests = ImportValidationService(storage).prepare(sources, "different-books")
    assert tuple(request.workbook_id for request in requests) == ("file-a", "file-b")
    receipts = DatabaseImportSaver(storage).save_many(requests)
    assert len(receipts) == count(storage, "equipment") == 2
    assert snapshot(storage, "HS-1").site == "화성"
    assert snapshot(storage, "PT-1").site == "평택"
    with storage.transaction() as tx:
        originals = [tx.get("import_batch", {"batch_id": receipt.batch_id})["raw_data"] for receipt in receipts]
    assert all("file-a" not in str(raw) and "file-b" not in str(raw) for raw in originals)


def test_prepare_still_holds_a_real_same_workbook_site_conflict(storage):
    sources = (workbook("HS-1", "화성", "same-window"),
               workbook("PT-1", "평택", "same-window", sheet="다음 시트"))
    with pytest.raises(ImportBlockedError):
        ImportValidationService(storage).prepare(sources, "same-book-conflict")
    assert count(storage, "equipment") == 0
