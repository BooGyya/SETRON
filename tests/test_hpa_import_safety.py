"""HPA 필수 오류·원본 근거·직접 저장 경계의 우회를 막는다 (BE-024)."""

from dataclasses import replace
from datetime import date

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.equipment.validation import prepare_snapshot
from setron.features.excel_import.provenance import validate_save_request_sources
from setron.features.excel_import.schemas import ImportBlockedError, SaveRequest, SaveRow
from setron.features.excel_import.service import (
    build_save_request, draft_from_review, edit_value, has_errors, set_excluded, snapshot_sheet, validate,
)
from setron.features.extraction.schemas import ExtractionIssue, ExtractionReview
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.excel_reader import SheetData

from test_hpa_import import hpa_draft
from test_import_validation import draft, row


def test_legacy_unmatched_ai_version_blocks_until_user_explicitly_corrects_it():
    """레거시 AI 원문 불일치는 단순 안내가 아니며 수정 전 저장을 차단한다."""
    source = draft(row(model_name="HPA", group_name="HPA", family_name="HPA"))
    broken = replace(source, rows=(replace(source.row(1), original={**source.row(1).original, "version_raw": "vFAKE"}),))
    assert any(issue.code == "SOURCE_MISMATCH" and issue.blocks_save for issue in validate(broken))
    with pytest.raises(ImportBlockedError):
        build_save_request(broken, "unverified-ai")
    corrected = edit_value(broken, 1, "version_raw", "v1")
    request = build_save_request(corrected, "explicit-value")
    validate_save_request_sources(request)


def test_identifier_value_edit_does_not_clear_unknown_source_position():
    """호기·SW 값만 보완해도 미확정 원본 위치 오류를 해제하지 않는다."""
    source = hpa_draft(version=None)
    original_row = source.row(1)
    without_source = replace(source, rows=(replace(original_row, sources={name: address for name, address in original_row.sources.items()
                                                                       if name != "version_raw"}),))
    edited = edit_value(without_source, 1, "version_raw", "vHPA.USER")
    assert any(issue.code == "REQUIRED_SOURCE_UNRESOLVED" and issue.blocks_save for issue in validate(edited))
    with pytest.raises(ImportBlockedError):
        build_save_request(edited, "missing-position")
    positioned = replace(edited, rows=(replace(edited.row(1), sources={**edited.row(1).sources, "version_raw": "H6"}),))
    validate_save_request_sources(build_save_request(positioned, "confirmed-position"))


def test_read_blank_cells_remain_in_raw_snapshot_after_correction():
    """빈 셀도 실제 읽은 원본으로 보존해 사용자 보완 후 출처를 잃지 않는다."""
    sheet = SheetData("시험.xlsx", "현황", 5, 3, [["HPA", None]])
    snapshot = snapshot_sheet(sheet)
    assert snapshot.cells == {"C5": "HPA", "D5": None}


def test_user_supplied_date_does_not_require_an_invented_excel_address():
    """원본에 없는 기준일은 명시적 사용자 입력으로 보완할 수 있다."""
    source = hpa_draft()
    value = source.row(1)
    source = replace(source, rows=(replace(value, original={**value.original, "base_date": None},
        normalized={**value.normalized, "base_date": None},
        sources={name: address for name, address in value.sources.items() if name != "base_date"}),))
    completed = edit_value(source, 1, "base_date", "2026-11-03")
    validate_save_request_sources(build_save_request(completed, "user-date"))


def test_normalized_values_do_not_bypass_source_validation_before_db_connection():
    """정규값이라는 표시만 있는 위조 SW는 DB 연결 전에 차단한다."""
    request = build_save_request(hpa_draft(), "normalized-forgery")
    saved = request.rows[0]
    forged = replace(request, rows=(replace(saved, values={**saved.values, "version_raw": "vFAKE"}),))
    saver = DatabaseImportSaver(connect=lambda: pytest.fail("원본 검증 실패 전 DB에 연결하면 안 됩니다."))
    with pytest.raises(StorageValidationError, match="SW 버전 원문"):
        saver.save(forged)


def test_explicit_identifier_edit_still_requires_a_real_original_cell():
    """명시 수정 표시는 원본 값 차이를 허용하지만 없는 셀 주소까지 허용하지 않는다."""
    request = build_save_request(hpa_draft(), "edited-position")
    saved = request.rows[0]
    forged = replace(saved, edited_fields=("version_raw",),
                     source_reference={**saved.source_reference, "version_raw": {"sheet": "HPA 검증", "cells": ["Z999"]}})
    with pytest.raises(StorageValidationError, match="SW 버전 원문"):
        validate_save_request_sources(replace(request, rows=(forged,)))


def test_canonical_model_category_pm_and_date_have_original_provenance():
    """M5·분류·고정 PM·날짜의 확정 정규화와 선택 공란을 저장 경계에서도 허용한다."""
    values = dict(base_date="2026-11-03", site="화성", model_name="M-O5", group_name="OXIDE", family_name="O5",
                  equipment_code="TEST-M5", pm_name="PM2", version_raw="vTEST_n4", production_area=None)
    original = dict(A1="2026.11.03", B1="화성", C1="M5", D1="O5", E1="TEST-M5",
                    F1="PM1, 2는 O5", G1="vTEST_n4 (2026.10.01)")
    addresses = dict(base_date="A1", site="B1", model_name="C1", family_name="D1", equipment_code="E1", pm_name="F1", version_raw="G1")
    source = {name: {"sheet": "현황", "cells": [address]} for name, address in addresses.items()}
    request = SaveRequest("canonical", ExtractKind.EQUIPMENT_STATUS, "합성.xlsx", {"sheet": "현황", "cells": original},
                          (SaveRow((1,), values, source, ()),), 0, None)
    validate_save_request_sources(request)


@pytest.mark.parametrize("missing", ["equipment_code", "version_raw"])
def test_excluding_hpa_with_required_error_does_not_release_the_batch(missing):
    """필수 오류가 남은 HPA 행을 제외해도 정상 행만 먼저 저장할 수 없다."""
    values = dict(equipment_code="HPA-2", group_name="HPA", model_name="HPA", family_name="HPA")
    values[missing] = None
    bad = row(2, **values)
    source = set_excluded(draft(row(), bad), 2, True)
    assert has_errors(validate(source))
    with pytest.raises(ImportBlockedError):
        build_save_request(source, "excluded-invalid-hpa")


def test_valid_hpa_and_unrelated_normal_rows_can_still_be_excluded():
    """필수 오류가 없는 HPA 및 일반 비대상 행의 명시적 제외는 유지한다."""
    hpa = row(2, equipment_code="HPA-2", group_name="HPA", model_name="HPA", family_name="HPA")
    assert not has_errors(validate(set_excluded(draft(row(), hpa), 2, True)))
    assert not has_errors(validate(set_excluded(draft(row(), row(2, version_raw=None)), 2, True)))


def test_global_extraction_block_and_code_are_preserved_in_review():
    """행 ID가 없는 HPA 누락 오류도 검토 초안에서 저장 차단 상태와 코드가 유지된다."""
    review = ExtractionReview(ExtractKind.EQUIPMENT_STATUS, "시험.xlsx", "현황", (),
        (ExtractionIssue("HPA 누락", code="HPA_ROWS_UNRESOLVED", blocks_save=True),), {"현황": {"A1": "HPA"}})
    converted = draft_from_review(review)
    assert converted.extraction_issues[0].code == "HPA_ROWS_UNRESOLVED"
    assert converted.extraction_issues[0].blocks_save


def test_direct_snapshot_cannot_mix_hpa_and_analysis_enabled_models():
    """직접 저장 서비스도 일반 PM과 HPA PM의 혼합을 쓰기 전에 거부한다."""
    value = prepare_snapshot(SnapshotInput(equipment_code="TEST-MIXED", base_date=date(2026, 11, 3), site="화성",
        model_name="혼합", batch_id=1, software=(
            SoftwareInput(family_id=10, version_raw="v1", pms=(PmInput(pm_name="PM1", group_id=1),)),
            SoftwareInput(family_id=90, version_raw="vHPA", pms=(PmInput(pm_name="PM2", group_id=9),)))))

    class Transaction:
        def get(self, table, key):
            if table == "import_batch":
                return {"source_type": "MANUAL"}
            if table == "equipment_group":
                return {"category": "HPA" if key["group_id"] == 9 else "OXIDE",
                        "analysis_enabled": key["group_id"] != 9}
            pytest.fail("혼합 모델 오류를 발견한 뒤 추가 조회·쓰기를 하면 안 됩니다.")

    with pytest.raises(StorageValidationError, match="HPA"):
        EquipmentService(object())._validate_links(Transaction(), value)
