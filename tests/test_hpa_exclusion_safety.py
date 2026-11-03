"""HPA의 분류 추출·편집 상태와 무관하게 필수 오류가 행 제외로 사라지지 않는다."""

from copy import deepcopy

import pytest

from setron.features.excel_import.provenance import validate_save_request_sources
from setron.features.excel_import.schemas import ImportBlockedError
from setron.features.excel_import.service import (
    build_save_request, draft_from_review, edit_value, has_errors, set_excluded, validate,
)

from test_hpa_extraction_coverage import FIELDS, record, review, row_at, table


CLASSIFIERS = ("group_name", "model_name", "family_name")


def incomplete_hpa(*, evidence="family", missing="version_raw"):
    """실제 원본과 AI 누락을 분리한 일반 정상 행·HPA 오류 행의 초안을 만든다."""
    normal, hpa = list(record("EXCLUDE-NORMAL", "v1", hpa=False)), list(record("EXCLUDE-HPA", "vHPA"))
    normal[1] = hpa[1] = "화성"
    hpa[FIELDS.index(missing)] = None
    if evidence == "raw-family":
        hpa[FIELDS.index("group_name")] = hpa[FIELDS.index("model_name")] = None
    sheet = table(normal, hpa)
    first, second = row_at(sheet, 1), row_at(sheet, 2)
    omitted = CLASSIFIERS if evidence == "none" else CLASSIFIERS[:2] if evidence == "family" else ()
    for field in omitted:
        second["values"][field] = None
        second["sources"].pop(field)
    return draft_from_review(review(sheet, first, second))


@pytest.mark.parametrize("evidence", ["raw-family", "family", "none"])
@pytest.mark.parametrize("missing", ["equipment_code", "version_raw"])
def test_excluded_hpa_missing_classifiers_holds_the_entire_request(evidence, missing):
    """계열만 추출되거나 분류가 전부 빠져도 실제 HPA의 필수 공란은 전체 저장을 보류한다."""
    source = incomplete_hpa(evidence=evidence, missing=missing)
    original_cells = deepcopy(source.sheet.cells)
    excluded = set_excluded(source, 2, True)
    problems = [issue for issue in validate(excluded) if issue.blocks_save and issue.field == missing]

    assert problems
    assert all(issue.workbook_name == source.workbook_name and issue.sheet_name == source.sheet.sheet_name
               and issue.excel_position == (7, 8 if missing == "equipment_code" else 9) for issue in problems)
    with pytest.raises(ImportBlockedError):
        build_save_request(excluded, "excluded-hpa")
    assert source.sheet.cells == excluded.sheet.cells == original_cells


@pytest.mark.parametrize("missing", ["equipment_code", "version_raw"])
def test_clearing_every_classifier_does_not_erase_confirmed_original_hpa(missing):
    """현재 모델·설비군·계열을 모두 지워도 보관된 원본 HPA 필수 오류를 유지한다."""
    source = incomplete_hpa(evidence="none", missing=missing)
    for name in CLASSIFIERS:
        source = edit_value(source, 2, name, None)
    excluded = set_excluded(source, 2, True)

    assert all(excluded.row(2).value(name) is None for name in CLASSIFIERS)
    assert any(issue.blocks_save and issue.field == missing for issue in validate(excluded))
    with pytest.raises(ImportBlockedError):
        build_save_request(excluded, "cleared-source-hpa")


@pytest.mark.parametrize("evidence", ["raw-family", "family", "none"])
@pytest.mark.parametrize("missing", ["equipment_code", "version_raw"])
def test_reincluded_hpa_can_be_explicitly_completed_without_changing_original_cells(evidence, missing):
    """제외를 취소하고 원본 확인값을 보완·재검증하면 정상 행과 HPA를 함께 요청한다."""
    source = incomplete_hpa(evidence=evidence, missing=missing)
    original_cells = deepcopy(source.sheet.cells)
    completed = set_excluded(set_excluded(source, 2, True), 2, False)
    for name in CLASSIFIERS:
        if completed.row(2).value(name) is None:
            completed = edit_value(completed, 2, name, "HPA")
    completed = edit_value(completed, 2, missing, "EXCLUDE-HPA" if missing == "equipment_code" else "vHPA.USER")
    assert not has_errors(validate(completed))
    request = build_save_request(completed, "completed-hpa")
    validate_save_request_sources(request)

    assert len(request.rows) == 2 and request.raw_data["cells"] == original_cells
    assert request.raw_data["cells"]["H7" if missing == "equipment_code" else "I7"] is None
    assert source.sheet.cells == original_cells


def test_unrelated_hpa_title_does_not_block_excluding_an_invalid_normal_row():
    """다른 구역의 HPA 제목만으로 일반 설비 오류 행의 명시적 제외를 차단하지 않는다."""
    sheet = table(record("GOOD-NORMAL", "v1", hpa=False), record("EXCLUDED-NORMAL", None, hpa=False))
    sheet.values.insert(0, ["HPA", *([None] * (len(FIELDS) - 1))])
    source = draft_from_review(review(sheet, row_at(sheet, 2), row_at(sheet, 3)))
    excluded = set_excluded(source, 2, True)

    assert not has_errors(validate(excluded))
    request = build_save_request(excluded, "normal-exclusion")
    validate_save_request_sources(request)
    assert [row.values["equipment_code"] for row in request.rows] == ["GOOD-NORMAL"]


def test_valid_hpa_remains_explicitly_excludable():
    """필수값 오류가 없는 HPA의 명시적인 제외는 계속 허용한다."""
    sheet = table(record("GOOD-NORMAL", "v1", hpa=False), record("VALID-HPA", "vHPA"))
    source = draft_from_review(review(sheet, row_at(sheet, 1), row_at(sheet, 2)))
    excluded = set_excluded(source, 2, True)

    assert not has_errors(validate(excluded))
    request = build_save_request(excluded, "valid-hpa-exclusion")
    validate_save_request_sources(request)
    assert [row.values["equipment_code"] for row in request.rows] == ["GOOD-NORMAL"]
