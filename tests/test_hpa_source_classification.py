"""저장 요청의 HPA 분류 출처 교차와 검토값 변경 후 누락 재검사를 검증한다."""

from dataclasses import replace

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.provenance import validate_save_request_sources
from setron.features.excel_import.schemas import SaveRequest, SaveRow
from setron.features.excel_import.service import (
    build_save_request, draft_from_review, edit_value, has_errors, revert_row, set_excluded, snapshot_sheet, validate,
)
from setron.features.extraction.normalization import normalize
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.excel_reader import MergedRange, SheetData

from test_hpa_extraction_coverage import FIELDS, record, review, row_at, table


def request_for(sheet, *payloads, edited=()):
    """검토 서비스를 우회한 수동 SaveRequest도 실제 원본과 대조하도록 만든다."""
    snapshot = snapshot_sheet(sheet)
    rows = tuple(SaveRow((index,), normalize(payload["values"], ExtractKind.EQUIPMENT_STATUS, index, {})[0],
        {field: {"sheet": sheet.sheet_name, "cells": [address]} for field, address in payload["sources"].items()}, edited)
        for index, payload in enumerate(payloads, 1))
    return SaveRequest("source-classification", ExtractKind.EQUIPMENT_STATUS, sheet.workbook_name,
        {"sheet": sheet.sheet_name, "cells": snapshot.cells, "merged": list(snapshot.merged)}, rows, 0, None)


@pytest.mark.parametrize("separate_tables", [False, True], ids=["같은 표", "분리된 표"])
def test_normal_classification_cannot_use_an_hpa_identifier_record(separate_tables):
    """필드별 값이 모두 원본에 있어도 일반 분류와 HPA 호기·SW의 조합은 거부한다."""
    if separate_tables:
        sheet = SheetData("교차.xlsx", "고립표", 1, 1,
            [list(FIELDS), list(record("TEST-NORMAL", "vO2", hpa=False)), [None] * len(FIELDS),
             list(FIELDS), list(record("TEST-HPA", "vHPA"))])
        hpa_index = 4
    else:
        sheet = table(record("TEST-NORMAL", "vO2", hpa=False), record("TEST-HPA", "vHPA"))
        hpa_index = 2
    mixed, hpa = row_at(sheet, 1), row_at(sheet, hpa_index)
    for field in ("equipment_code", "version_raw", "pm_name"):
        mixed["values"][field] = hpa["values"][field]
        mixed["sources"][field] = hpa["sources"][field]
    with pytest.raises(StorageValidationError, match="HPA"):
        validate_save_request_sources(request_for(sheet, mixed))


def test_explicit_classifier_edit_does_not_reclassify_original_hpa_as_normal():
    """명시적 수정 목록도 원본 HPA를 일반 설비로 바꾸는 면제 사유로 쓰지 않는다."""
    sheet = table(record("TEST-HPA", "vHPA"))
    payload = row_at(sheet, 1)
    payload["values"].update(group_name="Oxide", model_name="M-O2", family_name="O2")
    with pytest.raises(StorageValidationError, match="HPA"):
        validate_save_request_sources(request_for(sheet, payload, edited=("group_name", "model_name", "family_name")))


def test_hpa_values_cannot_borrow_classification_sources_from_normal_record():
    """현재 분류가 HPA여도 다른 일반 레코드의 분류 출처를 지정하면 차단한다."""
    sheet = table(record("TEST-NORMAL", hpa=False), record("TEST-HPA"))
    normal, hpa = row_at(sheet, 1), row_at(sheet, 2)
    for field in ("group_name", "model_name", "family_name"):
        hpa["sources"][field] = normal["sources"][field]
    with pytest.raises(StorageValidationError, match="HPA"):
        validate_save_request_sources(request_for(sheet, hpa, edited=("group_name", "model_name", "family_name")))


@pytest.mark.parametrize("field", ["model_name", "pm_name"])
def test_one_valid_hpa_source_does_not_hide_an_extra_normal_record_source(field):
    """같은 필드의 정상 HPA 출처 하나가 다른 레코드의 추가 출처를 숨기지 못한다."""
    sheet = table(record("TEST-NORMAL", hpa=False, pm="PM2"), record("TEST-HPA", pm="PM1"))
    normal, hpa = row_at(sheet, 1), row_at(sheet, 2)
    request = request_for(sheet, hpa, edited=(field,))
    saved = request.rows[0]
    combined = {**saved.source_reference, field: {"sheet": sheet.sheet_name,
        "cells": [hpa["sources"][field], normal["sources"][field]]}}
    with pytest.raises(StorageValidationError, match="HPA"):
        validate_save_request_sources(replace(request, rows=(replace(saved, source_reference=combined),)))


def test_headerless_hpa_classification_source_cannot_be_hidden_by_omitting_its_value():
    """정형 표가 없어도 HPA 분류 출처를 가진 일반 행에서 해당 값만 빼서 우회하지 못한다."""
    sheet = SheetData("수동.xlsx", "비정형", 1, 1, [list(record("TEST-HPA")), list(record("TEST-NORMAL", hpa=False))])
    payload = row_at(sheet, 1)
    payload["sources"]["group_name"] = "C1"
    request = request_for(sheet, payload)
    saved = request.rows[0]
    values = {field: value for field, value in saved.values.items() if field != "group_name"}
    with pytest.raises(StorageValidationError, match="HPA"):
        validate_save_request_sources(replace(request, rows=(replace(saved, values=values),)))


def test_selected_normal_row_does_not_require_saving_every_valid_hpa_row():
    """정상 HPA를 저장 대상에서 제외해도 다른 정상 행의 원본 대조는 통과한다."""
    sheet = table(record("TEST-NORMAL", hpa=False), record("TEST-HPA"))
    validate_save_request_sources(request_for(sheet, row_at(sheet, 1)))
    validate_save_request_sources(request_for(sheet, row_at(sheet, 2)))


def test_hpa_title_above_normal_table_does_not_reclassify_its_records():
    """일반 모델이 명시된 표 위의 HPA 제목만으로 일반 행을 차단하지 않는다."""
    sheet = SheetData("제목.xlsx", "현황", 1, 1,
        [["HPA", *([None] * (len(FIELDS) - 1))], list(FIELDS), list(record("TEST-NORMAL", hpa=False))])
    validate_save_request_sources(request_for(sheet, row_at(sheet, 2)))


def test_shared_hpa_section_title_is_valid_provenance_for_each_record():
    """같은 HPA 표의 병합 제목은 모델·분류·계열의 공통 출처로 유지한다."""
    sheet = table(record("TEST-HPA-1"), record("TEST-HPA-2"))
    sheet.values.insert(0, ["HPA", *([None] * (len(FIELDS) - 1))])
    sheet.merged_ranges.append(MergedRange(5, 3, 5, 10))
    payloads = [row_at(sheet, index) for index in (2, 3)]
    for payload in payloads:
        for field in ("group_name", "model_name", "family_name"):
            payload["sources"][field] = "C5"
    validate_save_request_sources(request_for(sheet, *payloads))


def test_exact_duplicate_hpa_sources_are_checked_for_every_record():
    """정확히 같은 HPA 두 행을 합친 저장 요청은 두 원본 레코드의 출처를 모두 확인한다."""
    sheet = table(record("TEST-HPA"), record("TEST-HPA"))
    request = request_for(sheet, row_at(sheet, 1), row_at(sheet, 2))
    first, second = request.rows
    merged = replace(first, row_ids=(1, 2), source_reference={name: {"sheet": sheet.sheet_name,
        "cells": [*first.source_reference[name]["cells"], *second.source_reference[name]["cells"]]}
        for name in first.source_reference})
    validate_save_request_sources(replace(request, rows=(merged,)))


def test_vertically_merged_hpa_identifiers_keep_distinct_pm_records():
    """모델·호기·SW를 공유하는 병합 표에서도 PM1·PM2의 정상 출처를 구분한다."""
    sheet = table(record("TEST-HPA", "v1", pm="PM1"), record("TEST-HPA", "v1", pm="PM2"))
    shared = ("group_name", "model_name", "family_name", "equipment_code", "version_raw")
    for field in shared:
        column = FIELDS.index(field)
        sheet.values[2][column] = None
        absolute = sheet.first_col + column
        sheet.merged_ranges.append(MergedRange(6, absolute, 7, absolute))
    first, second = row_at(sheet, 1), row_at(sheet, 2)
    for field in shared:
        second["values"][field] = first["values"][field]
        second["sources"][field] = first["sources"][field]
    validate_save_request_sources(request_for(sheet, first, second))


def test_compound_pm_source_keeps_two_hpa_pm_rows_with_shared_identifiers():
    """한 PM1·2 셀을 공유한 두 응답을 현재 PM 위치별로 원본 레코드와 대조한다."""
    sheet = table(record("TEST-HPA", "v1", pm="PM1, 2"))
    first, second = row_at(sheet, 1), row_at(sheet, 1)
    first["values"]["pm_name"], second["values"]["pm_name"] = "PM1", "PM2"
    validate_save_request_sources(request_for(sheet, first, second))


def test_pm_edit_and_revert_refresh_native_hpa_coverage_without_ui_callbacks():
    """PM 값을 보완하면 전역 HPA 누락 오류를 다시 계산하고 되돌리면 다시 차단한다."""
    sheet = table(record("TEST-NORMAL", hpa=False), record("TEST-HPA", pm="PM1"))
    missing = row_at(sheet, 2)
    missing["values"]["pm_name"] = None
    source = draft_from_review(review(sheet, row_at(sheet, 1), missing))
    assert any(issue.code == "HPA_COVERAGE_MISSING" for issue in source.extraction_issues)
    completed = edit_value(source, 2, "pm_name", "PM1")
    assert not has_errors(validate(completed))
    validate_save_request_sources(build_save_request(completed, "pm-completed"))
    reverted = revert_row(completed, 2)
    assert any(issue.code == "HPA_COVERAGE_MISSING" for issue in reverted.extraction_issues)
    excluded = set_excluded(completed, 2, True)
    assert not has_errors(validate(excluded))
    validate_save_request_sources(build_save_request(excluded, "valid-hpa-excluded"))
