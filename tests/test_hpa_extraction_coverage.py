"""BE-024: AI 응답의 HPA 행 누락과 빈 필수값의 원본 위치를 합성 자료로 검증한다."""

import json

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.provenance import validate_save_request_sources
from setron.features.excel_import.schemas import ImportBlockedError
from setron.features.excel_import.service import build_save_request, draft_from_review, edit_value, validate
from setron.features.extraction.locations import record_locations
from setron.features.extraction.service import HPA_FILLED, extract_sheet_data, recheck_hpa_coverage
from setron.infrastructure.ai_client import ExampleChatClient
from setron.infrastructure.excel_reader import MergedRange, SheetData


# 빠진 HPA 위치는 하나하나 찾아낸다. 호기 · 버전이 원본에 있으면 그 칸 값으로 채우고(HPA_AUTO_FILLED, 11/12 멘토 피드백),
# 빈 칸 · 겹침 · 잘못 분류는 지금처럼 저장을 막는다(HPA_COVERAGE_MISSING).
GAP = ("HPA_COVERAGE_MISSING", HPA_FILLED)

FIELDS = ("base_date", "site", "group_name", "model_name", "family_name", "equipment_code", "version_raw", "pm_name")


def table(*records):
    """확정 머리글과 원본 오프셋을 가진 합성 현황표를 만든다.

    param
    - records: 표에 넣을 시험 레코드.

    return
    - 머리글·시험값·오프셋을 가진 원본 시트.
    """
    return SheetData("HPA_검증.xlsx", "증강현황", 5, 3, [list(FIELDS), *[list(row) for row in records]])


def record(code, version="vTEST.1", *, hpa=True, pm=None):
    """일반 설비 또는 HPA의 저장 필수값을 포함한 시험 행을 만든다.

    param
    - code, version: 합성 호기와 버전. None이면 필수 공란.
    - hpa, pm: HPA 여부와 명시할 PM 위치.

    return
    - 고정 필드 순서의 시험값 튜플.
    """
    return ("2026-11-03", "시험사업장", "HPA" if hpa else "Oxide", "HPA" if hpa else "M-O2",
            "HPA" if hpa else "O2", code, version, pm)


def row_at(sheet, index, *, omit_sources=()):
    """해당 원본 데이터 행과 같은 내용·출처의 모의 응답을 만든다.

    param
    - sheet, index: 원본 시트와 0부터 시작하는 표 인덱스.
    - omit_sources: 모의 AI가 출처를 빠뜨린 필드 목록.

    return
    - 현재 행의 원문과 셀 주소를 가진 모의 응답 객체.
    """
    return {"values": dict(zip(FIELDS, sheet.values[index])),
            "sources": {field: sheet.address_of(index, column) for column, field in enumerate(FIELDS)
                        if field not in omit_sources}}


def review(sheet, *rows):
    """실제 AI·DB 없이 추출 서비스의 원본 대조를 실행한다.

    param
    - sheet, rows: 원본 시트와 모의 AI가 반환할 추출 행.

    return
    - 정상 응답의 원본 대조·검토 결과.
    """
    client = ExampleChatClient([json.dumps({"rows": list(rows)}, ensure_ascii=False)])
    result = extract_sheet_data(client, ExtractKind.EQUIPMENT_STATUS, sheet)
    assert result.ok
    return result.data


def test_regular_rows_do_not_hide_a_missing_hpa_row():
    """일반 행의 정상 응답만으로 원본 HPA 누락을 숨길 수 없는지 확인한다."""
    sheet = table(record("TEST-O2", hpa=False), record(None, None))
    result = review(sheet, row_at(sheet, 1))
    assert any(issue.code == "HPA_COVERAGE_MISSING" and issue.blocks_save for issue in result.issues)


def oriented_sheet(values, transposed, *, merged=()):
    """같은 합성 표를 가로 또는 세로로 배치하고 실제 셀 주소를 유지한다."""
    if transposed:
        values = [list(column) for column in zip(*values)]
        merged = tuple(MergedRange(area.left, area.top, area.right, area.bottom) for area in merged)
    return SheetData("HPA_구분행_검증.xlsx", "증강현황", 1, 1, values, list(merged))


def oriented_row(sheet, values, index, transposed, *, fields=FIELDS, start=0, omit_sources=()):
    """표 방향·병렬 구역에 맞는 실제 원문 출처의 AI 응답을 만든다."""
    return {"values": dict(zip(fields, values)),
            "sources": {field: sheet.address_of(start + column, index) if transposed
                        else sheet.address_of(index, start + column)
                        for column, field in enumerate(fields) if field not in omit_sources}}


@pytest.mark.parametrize("transposed", [False, True], ids=["빈행", "빈열"])
@pytest.mark.parametrize("missing", ["equipment_code", "version_raw", "metadata", "all"])
def test_separator_does_not_hide_hpa_with_missing_required_values(transposed, missing):
    """빈 구분 행·열 뒤의 HPA를 누락해도 정상 행만 저장할 수 없어야 한다."""
    normal, hpa = record("TEST-O2", hpa=False), list(record("TEST-HPA"))
    for field in ("base_date", "site", "equipment_code", "version_raw"):
        if missing == field or missing == "all" or missing == "metadata" and field in {"base_date", "site"}:
            hpa[FIELDS.index(field)] = None
    sheet = oriented_sheet([list(FIELDS), list(normal), [None] * len(FIELDS), hpa], transposed)
    result = review(sheet, oriented_row(sheet, normal, 1, transposed))
    draft = draft_from_review(result)
    if missing == "metadata":       # 호기 · 버전은 원본에 있다 → 채우고, 비어 있는 기준일 · 사이트가 저장을 막는다
        assert any(issue.code == HPA_FILLED for issue in result.issues)
    else:
        assert any(issue.code == "HPA_COVERAGE_MISSING" and issue.blocks_save for issue in result.issues)
    assert any(issue.blocks_save for issue in validate(draft))
    with pytest.raises(ImportBlockedError):
        build_save_request(draft, "HPA-구분행-누락")


@pytest.mark.parametrize("transposed", [False, True], ids=["빈행", "빈열"])
def test_complete_hpa_after_separator_keeps_exact_values_and_can_be_saved(transposed):
    """구분 행·열이 있어도 빠짐없이 반환한 정상 HPA는 원문 그대로 저장한다."""
    normal, hpa = record("TEST-O2", hpa=False), record("TEST-HPA", "vHPA.2_d5 ")
    sheet = oriented_sheet([list(FIELDS), list(normal), [None] * len(FIELDS), list(hpa)], transposed)
    result = review(sheet, oriented_row(sheet, normal, 1, transposed), oriented_row(sheet, hpa, 3, transposed))
    assert not any(issue.code and issue.code.startswith("HPA_COVERAGE") for issue in result.issues)
    request = build_save_request(draft_from_review(result), "HPA-구분행-정상")
    validate_save_request_sources(request)
    assert [row.values["equipment_code"] for row in request.rows] == ["TEST-O2", "TEST-HPA"]
    assert request.rows[1].values["version_raw"] == "vHPA.2_d5 "
    assert request.rows[1].values["production_area"] is None


@pytest.mark.parametrize("transposed", [False, True], ids=["빈행", "빈열"])
def test_blank_hpa_after_separator_requires_explicit_correction_and_preserves_original(transposed):
    """구분 뒤 공란의 실제 주소를 복원하고 사용자 보완·재검증 후에만 저장한다."""
    normal, hpa = record("TEST-O2", hpa=False), list(record("TEST-HPA", None))
    hpa[0] = hpa[1] = None
    sheet = oriented_sheet([list(FIELDS), list(normal), [None] * len(FIELDS), hpa], transposed)
    result = review(sheet, oriented_row(sheet, normal, 1, transposed),
                    oriented_row(sheet, hpa, 3, transposed, omit_sources=("version_raw",)))
    draft = draft_from_review(result)
    address = sheet.address_of(FIELDS.index("version_raw"), 3) if transposed else sheet.address_of(3, FIELDS.index("version_raw"))
    assert draft.row(2).sources["version_raw"] == address
    with pytest.raises(ImportBlockedError):
        build_save_request(draft, "HPA-구분행-공란")
    for field, value in (("base_date", "2026-11-03"), ("site", "시험사업장"), ("version_raw", "vUSER")):
        draft = edit_value(draft, 2, field, value)
    request = build_save_request(draft, "HPA-구분행-보완")
    validate_save_request_sources(request)
    assert request.rows[1].values["version_raw"] == "vUSER"
    assert request.raw_data["cells"][address] is None


@pytest.mark.parametrize("transposed", [False, True], ids=["빈행", "빈열"])
@pytest.mark.parametrize("complete", [False, True], ids=["HPA누락", "모두반환"])
def test_parallel_tables_keep_both_hpa_records_after_separator(transposed, complete):
    """병렬 표에서도 구분 행·열 뒤의 두 HPA 위치를 서로 독립적으로 검사한다."""
    normal = record("TEST-O2", hpa=False)
    left, right = record("TEST-HPA-L"), record("TEST-HPA-R")
    sheet = oriented_sheet([list(FIELDS) * 2, list(normal) * 2, [None] * (len(FIELDS) * 2),
                            list(left) + list(right)], transposed)
    rows = [oriented_row(sheet, normal, 1, transposed)]
    if complete:
        rows.extend((oriented_row(sheet, left, 3, transposed),
                     oriented_row(sheet, right, 3, transposed, start=len(FIELDS))))
    result = review(sheet, *rows)
    missing = [issue for issue in result.issues if issue.code in GAP]
    assert len(missing) == (0 if complete else 2) and not any(issue.blocks_save for issue in missing)


@pytest.mark.parametrize("transposed", [False, True], ids=["가로", "전치"])
def test_reordered_new_header_after_separator_does_not_create_old_table_records(transposed):
    """열 순서가 바뀐 새 머리글에서 이전 표 경계를 끝내고 새 표만 검사한다."""
    normal, hpa = record("TEST-O2", hpa=False), dict(zip(FIELDS, record("TEST-HPA")))
    order = tuple(reversed(FIELDS))
    sheet = oriented_sheet([list(FIELDS), list(normal), [None] * len(FIELDS), list(order),
                            [hpa[field] for field in order]], transposed)
    rows = (oriented_row(sheet, normal, 1, transposed),
            oriented_row(sheet, [hpa[field] for field in order], 4, transposed, fields=order))
    result = review(sheet, *rows)
    assert len([location for location in record_locations(sheet) if location.is_hpa]) == 1
    assert not any(issue.code and issue.code.startswith("HPA_COVERAGE") for issue in result.issues)
    omitted = review(sheet, rows[0])
    assert len([issue for issue in omitted.issues if issue.code in GAP]) == 1


@pytest.mark.parametrize("transposed", [False, True], ids=["빈행", "빈열"])
def test_explicit_new_section_stops_inheriting_hpa_title_after_separator(transposed):
    """새 구역 제목이 있으면 앞 HPA 표의 분류를 다른 구역까지 늘리지 않는다."""
    sheet = oriented_sheet([["HPA", None], ["equipment_code", "version_raw"], ["TEST-HPA", "v1"],
                            [None, None], ["Oxide", None], ["TEST-O2", "v2"]], transposed,
                           merged=(MergedRange(1, 1, 1, 2), MergedRange(5, 1, 5, 2)))
    assert len([location for location in record_locations(sheet) if location.is_hpa]) == 1


@pytest.mark.parametrize("transposed", [False, True], ids=["빈행", "빈열"])
@pytest.mark.parametrize("complete", [False, True], ids=["두번째HPA누락", "모두반환"])
@pytest.mark.parametrize("version", [None, "vHPA.2"], ids=["SW공란", "SW있음"])
def test_hpa_section_keeps_records_across_separator_until_a_real_boundary(transposed, complete, version):
    """개별 분류 칸이 없는 HPA 구역에서도 빈 구분 뒤의 데이터 누락을 막는다."""
    names = ("base_date", "site", "equipment_code", "version_raw")
    first = ("2026-11-03", "시험사업장", "TEST-HPA-1", "v1")
    second = (None if version is None else "2026-11-03", None if version is None else "시험사업장", "TEST-HPA-2", version)
    sheet = oriented_sheet([["HPA", None, None, None], list(names), list(first), [None] * 4, list(second)],
                           transposed, merged=(MergedRange(1, 1, 1, 4),))
    rows = []
    for values, index in ((first, 2), (second, 4))[:2 if complete else 1]:
        row = oriented_row(sheet, values, index, transposed, fields=names,
                           omit_sources=("version_raw",) if values[-1] is None else ())
        row["values"].update({field: "HPA" for field in ("model_name", "group_name", "family_name")})
        row["sources"].update({field: "A1" for field in ("model_name", "group_name", "family_name")})
        rows.append(row)
    result = review(sheet, *rows)
    draft = draft_from_review(result)
    missing = [issue for issue in result.issues if issue.code in GAP]
    assert len(missing) == (0 if complete else 1)
    if version is None:                     # SW 버전이 빈 HPA는 채우지 않는다 → 저장을 막는다
        with pytest.raises(ImportBlockedError):
            build_save_request(draft, "HPA-구역-구분행")
    else:
        request = build_save_request(draft, "HPA-구역-구분행-정상")
        validate_save_request_sources(request)
        assert [row.values["equipment_code"] for row in request.rows] == ["TEST-HPA-1", "TEST-HPA-2"]
    if complete and version is None:
        address = sheet.address_of(3, 4) if transposed else sheet.address_of(4, 3)
        assert draft.row(2).sources["version_raw"] == address
        corrected = draft
        for field, value in (("base_date", "2026-11-03"), ("site", "시험사업장"), ("version_raw", "vUSER")):
            corrected = edit_value(corrected, 2, field, value)
        request = build_save_request(corrected, "HPA-구역-구분행-보완")
        validate_save_request_sources(request)
        assert request.raw_data["cells"][address] is None


@pytest.mark.parametrize("transposed", [False, True], ids=["빈행", "빈열"])
def test_hpa_section_row_with_only_own_metadata_still_requires_code_and_sw(transposed):
    """날짜·사업장이 직접 적힌 HPA 행은 호기·SW가 모두 비어도 누락 검사한다."""
    names = ("base_date", "site", "equipment_code", "version_raw")
    first = ("2026-11-03", "시험사업장", "TEST-HPA-1", "v1")
    broken = ("2026-11-03", "시험사업장", None, None)
    sheet = oriented_sheet([["HPA", None, None, None], list(names), list(first), [None] * 4, list(broken)],
                           transposed, merged=(MergedRange(1, 1, 1, 4),))
    rows = []
    for values, index in ((first, 2), (broken, 4)):
        row = oriented_row(sheet, values, index, transposed, fields=names,
                           omit_sources=("equipment_code", "version_raw") if index == 4 else ())
        row["values"].update({field: "HPA" for field in ("model_name", "group_name", "family_name")})
        row["sources"].update({field: "A1" for field in ("model_name", "group_name", "family_name")})
        rows.append(row)
    omitted = review(sheet, rows[0])
    assert len([issue for issue in omitted.issues if issue.code in GAP]) == 1
    with pytest.raises(ImportBlockedError):
        build_save_request(draft_from_review(omitted), "HPA-메타데이터만-누락")
    draft = draft_from_review(review(sheet, *rows))
    with pytest.raises(ImportBlockedError):
        build_save_request(draft, "HPA-메타데이터만-공란")
    for field, value in (("equipment_code", "TEST-HPA-2"), ("version_raw", "vUSER")):
        draft = edit_value(draft, 2, field, value)
    request = build_save_request(draft, "HPA-메타데이터만-보완")
    validate_save_request_sources(request)
    assert request.rows[1].values["equipment_code"] == "TEST-HPA-2"


@pytest.mark.parametrize("transposed", [False, True], ids=["가로", "전치"])
def test_inherited_merged_metadata_does_not_create_an_empty_hpa_record(transposed):
    """병합 날짜·사업장만 보이는 완전 빈 행·열을 별개 HPA로 만들지 않는다."""
    names = ("base_date", "site", "equipment_code", "version_raw")
    sheet = oriented_sheet([["HPA", None, None, None], list(names),
                            ["2026-11-03", "시험사업장", "TEST-HPA-1", "v1"], [None] * 4,
                            [None, None, "TEST-HPA-2", "v2"]], transposed,
                           merged=(MergedRange(1, 1, 1, 4), MergedRange(3, 1, 5, 1), MergedRange(3, 2, 5, 2)))
    assert len([location for location in record_locations(sheet) if location.is_hpa]) == 2


@pytest.mark.parametrize("transposed", [False, True], ids=["가로", "전치"])
def test_mixed_metadata_sources_do_not_guess_a_blank_hpa_record(transposed):
    """서로 다른 HPA의 날짜·사업장 주소를 합쳐 빈 호기·SW 위치를 고르지 않는다."""
    names = ("base_date", "site", "equipment_code", "version_raw")
    values = ("2026-11-03", "시험사업장", None, None)
    sheet = oriented_sheet([["HPA", None, None, None], list(names), list(values), list(values)], transposed,
                           merged=(MergedRange(1, 1, 1, 4),))
    row = oriented_row(sheet, values, 2, transposed, fields=names, omit_sources=("equipment_code", "version_raw"))
    row["sources"]["site"] = sheet.address_of(1, 3) if transposed else sheet.address_of(3, 1)
    row["values"].update({field: "HPA" for field in ("model_name", "group_name", "family_name")})
    row["sources"].update({field: "A1" for field in ("model_name", "group_name", "family_name")})
    result = review(sheet, row)
    assert "equipment_code" not in result.rows[0].sources and "version_raw" not in result.rows[0].sources
    assert len([issue for issue in result.issues if issue.code == "HPA_COVERAGE_MISSING"]) == 2


@pytest.mark.parametrize("transposed", [False, True], ids=["빈행", "빈열"])
@pytest.mark.parametrize("complete", [False, True], ids=["HPA누락", "HPA반환"])
def test_blank_between_hpa_title_and_headers_keeps_the_section_identifiable(transposed, complete):
    """HPA 단독 제목과 머리글 사이의 빈 구분도 누락을 숨기지 못하게 한다."""
    normal = record("TEST-O2", hpa=False)
    width = len(FIELDS)
    values = [list(FIELDS), list(normal), [None] * width, ["HPA", *([None] * (width - 1))],
              [None] * width, ["equipment_code", "version_raw", *([None] * (width - 2))],
              ["TEST-HPA", None, *([None] * (width - 2))]]
    sheet = oriented_sheet(values, transposed)
    rows = [oriented_row(sheet, normal, 1, transposed)]
    if complete:
        row = oriented_row(sheet, ("TEST-HPA", None), 6, transposed,
                           fields=("equipment_code", "version_raw"), omit_sources=("version_raw",))
        row["values"].update({field: "HPA" for field in ("model_name", "group_name", "family_name")})
        title = "D1" if transposed else "A4"
        row["sources"].update({field: title for field in ("model_name", "group_name", "family_name")})
        rows.append(row)
    result = review(sheet, *rows)
    missing = [issue for issue in result.issues if issue.code == "HPA_COVERAGE_MISSING"]
    assert len(missing) == (0 if complete else 1)
    with pytest.raises(ImportBlockedError):
        build_save_request(draft_from_review(result), "HPA-제목-빈구분")
    if complete:
        assert result.rows[1].sources["version_raw"].address == ("G2" if transposed else "B7")


@pytest.mark.parametrize("transposed", [False, True], ids=["빈행", "빈열"])
def test_titles_and_annotations_after_separator_do_not_create_hpa_records(transposed):
    """구분 뒤 범례·전체 병합 제목을 설비로 만들지 않고 다음 표의 HPA만 검사한다."""
    normal, hpa = record("TEST-O2", hpa=False), record("TEST-HPA")
    note = [None] * len(FIELDS)
    note[FIELDS.index("model_name")] = "HPA"
    note[FIELDS.index("equipment_code")] = "범례: 분석 제외"
    sheet = oriented_sheet([list(FIELDS), list(normal), [None] * len(FIELDS), note,
                            ["HPA", *([None] * (len(FIELDS) - 1))], list(FIELDS), list(hpa)], transposed,
                           merged=(MergedRange(5, 1, 5, len(FIELDS)),))
    result = review(sheet, oriented_row(sheet, normal, 1, transposed), oriented_row(sheet, hpa, 6, transposed))
    assert len([location for location in record_locations(sheet) if location.is_hpa]) == 1
    assert not any(issue.code and issue.code.startswith("HPA_COVERAGE") for issue in result.issues)


@pytest.mark.parametrize("transposed", [False, True], ids=["빈행", "빈열"])
def test_merged_classifier_only_does_not_hide_blank_hpa_identifiers_after_separator(transposed):
    """분류 칸만 병합된 HPA는 표 전체 제목과 구분해 빈 호기·SW를 검사한다."""
    normal = record("TEST-O2", hpa=False)
    broken = [None] * len(FIELDS)
    broken[FIELDS.index("group_name")] = "HPA"
    sheet = oriented_sheet([list(FIELDS), list(normal), [None] * len(FIELDS), broken], transposed,
                           merged=(MergedRange(4, 3, 4, 5),))
    result = review(sheet, oriented_row(sheet, normal, 1, transposed))
    assert len([issue for issue in result.issues if issue.code == "HPA_COVERAGE_MISSING"]) == 1
    with pytest.raises(ImportBlockedError):
        build_save_request(draft_from_review(result), "HPA-병합분류-공란")


def test_blank_required_sources_are_restored_from_unique_headers_without_filling_values():
    """빈 호기·SW의 주소만 확정 머리글 교차점에서 복원하는지 확인한다."""
    sheet = table(record(None, None))
    result = review(sheet, row_at(sheet, 1, omit_sources=("equipment_code", "version_raw")))
    row = result.rows[0]
    assert row.values["equipment_code"] is None and row.values["version_raw"] is None
    assert row.sources["equipment_code"].address == "H6" and row.sources["equipment_code"].value is None
    assert row.sources["version_raw"].address == "I6" and row.sources["version_raw"].value is None
    assert result.raw_data[sheet.sheet_name]["H6"] is None


def test_duplicating_one_hpa_response_does_not_cover_another_hpa_row():
    """한 HPA 출처를 중복 반환해 다른 HPA 누락을 숨길 수 없는지 확인한다."""
    sheet = table(record("TEST-HPA-1"), record("TEST-HPA-2"))
    first = row_at(sheet, 1)
    result = review(sheet, first, first)
    assert any(issue.code in GAP and issue.address in {"E7", "F7", "G7"} for issue in result.issues)
    assert "TEST-HPA-2" in [row.values["equipment_code"] for row in result.rows]     # 빠진 쪽은 원본 칸으로 채움


def test_response_order_does_not_change_hpa_coverage():
    """응답 행 순서를 바꿔도 같은 원본 레코드가 확인되는지 검증한다."""
    sheet = table(record("TEST-HPA-1"), record("TEST-HPA-2"))
    result = review(sheet, row_at(sheet, 2), row_at(sheet, 1))
    assert not any(issue.code and issue.code.startswith("HPA_COVERAGE") for issue in result.issues)


def test_transposed_headers_restore_blank_column_intersections():
    """세로 머리글 표에서도 빈 필수값의 실제 교차 주소를 복원하는지 확인한다."""
    values = record(None, None)
    sheet = SheetData("HPA_검증.xlsx", "세로배치", 3, 4,
                      [[field, value] for field, value in zip(FIELDS, values)])
    payload = {"values": dict(zip(FIELDS, values)),
               "sources": {field: sheet.address_of(index, 1) for index, field in enumerate(FIELDS)
                           if field not in {"equipment_code", "version_raw"}}}
    result = review(sheet, payload)
    assert result.rows[0].sources["equipment_code"].address == "E8"
    assert result.rows[0].sources["version_raw"].address == "E9"
    assert not any(issue.code == "HPA_COVERAGE_MISSING" for issue in result.issues)


def test_shuffled_header_order_uses_field_positions_instead_of_fixed_columns():
    """열 순서가 달라도 확정 머리글의 위치를 사용하는지 확인한다."""
    order = ("version_raw", "model_name", "family_name", "equipment_code", "site", "base_date", "group_name")
    values = dict(zip(FIELDS, record(None, None)))
    sheet = SheetData("HPA_검증.xlsx", "열순서변경", 1, 1, [list(order), [values[field] for field in order]])
    payload = {"values": values, "sources": {field: sheet.address_of(1, index) for index, field in enumerate(order)
                                             if field not in {"equipment_code", "version_raw"}}}
    result = review(sheet, payload)
    assert result.rows[0].sources["equipment_code"].address == "D2"
    assert result.rows[0].sources["version_raw"].address == "A2"


@pytest.mark.parametrize("complete", [False, True], ids=["오른쪽 누락", "양쪽 보존"])
def test_parallel_tables_have_separate_hpa_locations(complete):
    """병렬 표의 같은 행 번호를 서로 다른 HPA 위치로 구분하는지 확인한다.

    param
    - complete: 오른쪽 표의 HPA 응답도 포함할지 여부.
    """
    names = ("model_name", "equipment_code", "version_raw")
    sheet = SheetData("HPA_검증.xlsx", "병렬", 1, 1,
                      [list(names) * 2, ["HPA", "TEST-HPA-1", "v1", "HPA", "TEST-HPA-2", "v2"]])
    first = {"values": dict(zip(names, sheet.values[1][:3])),
             "sources": {field: sheet.address_of(1, index) for index, field in enumerate(names)}}
    second = {"values": dict(zip(names, sheet.values[1][3:])),
              "sources": {field: sheet.address_of(1, index + 3) for index, field in enumerate(names)}}
    result = review(sheet, first, *([second] if complete else []))
    missing = [issue for issue in result.issues if issue.code in GAP]
    assert not missing if complete else len(missing) == 1 and missing[0].address == "D2"


@pytest.mark.parametrize("complete", [False, True], ids=["병합 아래 두번째 누락", "병합 아래 두행 보존"])
def test_merged_hpa_classifier_does_not_merge_distinct_equipment_or_pm_rows(complete):
    """같은 병합 모델 셀 아래의 서로 다른 호기·PM 위치를 보존하는지 확인한다.

    param
    - complete: 두번째 PM을 반환할지, 첫번째 PM을 중복 반환할지 여부.
    """
    names = ("model_name", "equipment_code", "version_raw", "pm_name")
    sheet = SheetData("HPA_검증.xlsx", "병합", 1, 1,
                      [list(names), ["HPA", "TEST-HPA", "v1", "PM1"], [None, "TEST-HPA", "v2", "PM2"]],
                      [MergedRange(2, 1, 3, 1)])
    rows = [{"values": dict(zip(names, ("HPA", "TEST-HPA", f"v{i}", f"PM{i}"))),
             "sources": {"model_name": "A2", "equipment_code": f"B{i + 1}", "version_raw": f"C{i + 1}",
                         "pm_name": f"D{i + 1}"}} for i in (1, 2)]
    result = review(sheet, rows[0], *([rows[1]] if complete else [rows[0]]))
    missing = [issue for issue in result.issues if issue.code == "HPA_COVERAGE_MISSING"]
    assert not missing if complete else len(missing) == 1 and "PM2" in missing[0].reason
    assert result.raw_data[sheet.sheet_name]["A3"] is None


@pytest.mark.parametrize("complete", [False, True], ids=["같은 PM 중복", "두 PM 보존"])
def test_explicit_compound_pm_list_requires_each_named_position(complete):
    """한 셀에 명시된 복수 PM을 각각 추출했는지 확인한다.

    param
    - complete: 두 PM을 모두 반환할지, 같은 PM을 중복 반환할지 여부.
    """
    sheet = SheetData("HPA_검증.xlsx", "PM목록", 1, 1,
                      [["모델", "호기", "SW", "PM"], ["HPA", "TEST-HPA", "v1", "PM1, 2"]])
    rows = [{"values": {"model_name": "HPA", "equipment_code": "TEST-HPA", "version_raw": "v1", "pm_name": name},
             "sources": {"model_name": "A2", "equipment_code": "B2", "version_raw": "C2", "pm_name": "D2"}}
            for name in ("PM1", "2" if complete else "PM1")]
    result = review(sheet, *rows)
    missing = [issue for issue in result.issues if issue.code == "HPA_COVERAGE_MISSING"]
    assert not missing if complete else len(missing) == 1 and "PM2" in missing[0].reason


def test_sources_from_two_records_cannot_cover_one_hpa_record():
    """서로 다른 레코드의 호기·SW 출처를 섞어 정상 처리할 수 없는지 확인한다."""
    sheet = table(record("TEST-HPA-1", "v1"), record("TEST-HPA-2", "v2"))
    mixed = row_at(sheet, 1)
    mixed["values"]["version_raw"] = "v2"
    mixed["sources"]["version_raw"] = sheet.address_of(2, FIELDS.index("version_raw"))
    result = review(sheet, mixed)
    assert len([issue for issue in result.issues if issue.code == "HPA_COVERAGE_MISSING"]) == 2


def test_hpa_title_and_annotation_do_not_become_equipment_records():
    """HPA 제목·범례를 누락 설비로 오인하지 않는지 확인한다."""
    sheet = table(record("TEST-O2", hpa=False))
    sheet.values.insert(0, ["HPA", *([None] * (len(FIELDS) - 1))])
    sheet.values.append(["HPA", "범례: 분석 제외", *([None] * (len(FIELDS) - 2))])
    result = review(sheet, row_at(sheet, 2))
    assert not any(issue.code and issue.code.startswith("HPA_COVERAGE") for issue in result.issues)


def test_hpa_section_title_requires_each_following_record():
    """명시적 HPA 구역 제목 아래의 여러 레코드를 각각 확인하는지 검증한다."""
    names = ("equipment_code", "version_raw")
    sheet = SheetData("HPA_검증.xlsx", "HPA구역", 1, 1,
                      [["HPA", None], list(names), ["TEST-HPA-1", "v1"], ["TEST-HPA-2", "v2"]],
                      [MergedRange(1, 1, 1, 2)])
    first = {"values": {"model_name": "HPA", "equipment_code": "TEST-HPA-1", "version_raw": "v1"},
             "sources": {"model_name": "A1", "equipment_code": "A3", "version_raw": "B3"}}
    result = review(sheet, first)
    assert len([issue for issue in result.issues if issue.code in GAP]) == 1


def test_unstructured_hpa_data_is_blocked_without_guessing_blank_coordinates():
    """머리글 없는 HPA 데이터의 위치를 추측해 완료 처리하지 않는지 확인한다."""
    sheet = SheetData("HPA_검증.xlsx", "비정형", 1, 1, [["HPA", "TEST-HPA", "v1"]])
    result = review(sheet)
    assert any(issue.code == "HPA_COVERAGE_UNRESOLVED" and issue.blocks_save for issue in result.issues)


def test_ambiguous_merged_rows_do_not_guess_which_blank_sw_to_use():
    """PM 대응이 빠진 병합 표에서 빈 SW 주소를 임의로 선택하지 않는지 확인한다."""
    names = ("model_name", "equipment_code", "version_raw", "pm_name")
    sheet = SheetData("HPA_검증.xlsx", "모호한PM", 1, 1,
                      [list(names), ["HPA", "TEST-HPA", None, "PM1"], [None, "TEST-HPA", None, "PM2"]],
                      [MergedRange(2, 1, 3, 1), MergedRange(2, 2, 3, 2)])
    response = {"values": {"model_name": "HPA", "equipment_code": "TEST-HPA", "version_raw": None},
                "sources": {"model_name": "A2", "equipment_code": "B2"}}
    result = review(sheet, response)
    assert "version_raw" not in result.rows[0].sources
    assert any(issue.code == "REQUIRED_SOURCE_UNRESOLVED" and issue.field == "version_raw"
               and issue.address is None and issue.blocks_save for issue in result.issues)


def test_missing_metadata_can_be_completed_explicitly_without_inventing_a_source_cell():
    """원본에 없는 기준일·사업장은 가짜 셀 주소 없이 직접 보완할 수 있게 하는지 확인한다."""
    sheet = SheetData("HPA_검증.xlsx", "기준일없음", 1, 1,
                      [["모델", "호기", "SW"], ["HPA", "TEST-HPA", "v1"]])
    response = {"values": {"model_name": "HPA", "equipment_code": "TEST-HPA", "version_raw": "v1"},
                "sources": {"model_name": "A2", "equipment_code": "B2", "version_raw": "C2"}}
    result = review(sheet, response)
    assert "base_date" not in result.rows[0].sources
    assert not any(issue.code == "REQUIRED_SOURCE_UNRESOLVED" and issue.field in {"base_date", "site"}
                   for issue in result.issues)


def test_manual_source_confirmation_rechecks_all_hpa_slots_without_changing_values():
    """사용자 주소 확정 후 누락을 다시 계산하고 값과 원본을 보존하는지 확인한다."""
    sheet = table(record(None, None), record(None, None))
    first, second = row_at(sheet, 1), row_at(sheet, 2)
    first["sources"]["equipment_code"] = "H7"
    second["sources"]["equipment_code"] = "H6"
    pairs = [(row["values"], row["sources"]) for row in (first, second)]
    assert len(recheck_hpa_coverage(sheet, pairs)) == 2
    first["sources"]["equipment_code"] = "H6"
    remaining = recheck_hpa_coverage(sheet, pairs)
    assert len(remaining) == 1 and remaining[0].address in {"E7", "F7", "G7"}
    second["sources"]["equipment_code"] = "H7"
    assert recheck_hpa_coverage(sheet, pairs) == ()
    assert all(row["values"]["equipment_code"] is None and row["values"]["version_raw"] is None
               for row in (first, second))
    assert sheet.values[1][5:7] == [None, None] and sheet.values[2][5:7] == [None, None]


def test_manual_duplicate_or_out_of_bounds_sources_cannot_clear_another_record():
    """사용자가 지정한 주소도 중복·범위 밖이면 다른 레코드의 누락을 해제하지 않는지 확인한다."""
    sheet = table(record("TEST-HPA-1"), record("TEST-HPA-2"))
    first = row_at(sheet, 1)
    assert len(recheck_hpa_coverage(sheet, [(first["values"], first["sources"])] * 2)) == 1
    addresses = {field: "ZZ9999" for field in first["sources"]}
    assert len(recheck_hpa_coverage(sheet, [(first["values"], addresses)])) == 2


def test_duplicate_sw_headers_require_manual_source_confirmation():
    """중복 SW 머리글에서 사용자가 주소를 확정할 때까지 위치를 미확인으로 두는지 확인한다."""
    sheet = SheetData("HPA_검증.xlsx", "중복머리글", 1, 1,
                      [["모델", "호기", "SW", "Version"], ["HPA", "TEST-HPA", None, None]])
    payload = {"values": {"model_name": "HPA", "equipment_code": "TEST-HPA", "version_raw": None},
               "sources": {"model_name": "A2", "equipment_code": "B2"}}
    result = review(sheet, payload)
    assert "version_raw" not in result.rows[0].sources
    assert any(issue.code == "REQUIRED_SOURCE_UNRESOLVED" and issue.field == "version_raw"
               and issue.blocks_save for issue in result.issues)
    assert not any(issue.code == "HPA_COVERAGE_MISSING" for issue in result.issues)
    payload["sources"]["version_raw"] = "D2"
    assert recheck_hpa_coverage(sheet, [(payload["values"], payload["sources"])]) == ()


@pytest.mark.parametrize("separator", [False, True], ids=["연속행", "빈구분행"])
def test_educational_s5_headers_keep_five_hpa_records_and_each_blank_sw_position(separator):
    """교육용 S5의 분류·설비호기·Version 머리글과 병합 제목 배치를 합성 값으로 검증한다."""
    headers = ["No", "Bay", "분류", "설비호기", "PM", "Version", "CTC IP", "개조", "Check", "ESA", "APE"]
    values = [["HPA", *([None] * 10)], headers,
              *[[number, None, "HPA", f"TEST-HPA-{number}", *([None] * 7)] for number in range(1, 6)]]
    if separator:
        values.insert(3, [None] * len(headers))
    excel_rows = [number + 2 + (1 if separator and number > 1 else 0) for number in range(1, 6)]
    sheet = SheetData("합성_S5.xlsx", "S5", 1, 24, values, [MergedRange(1, 24, 1, 34)])
    rows = [{"values": {"group_name": "HPA", "model_name": "HPA", "family_name": "HPA",
                        "equipment_code": f"TEST-HPA-{number}", "version_raw": None},
             "sources": {"group_name": f"Z{excel_row}", "model_name": f"Z{excel_row}",
                         "family_name": f"Z{excel_row}", "equipment_code": f"AA{excel_row}"}}
            for number, excel_row in enumerate(excel_rows, 1)]
    result = review(sheet, *rows)
    assert not any(issue.code and issue.code.startswith("HPA_COVERAGE") for issue in result.issues)
    assert [row.sources["version_raw"].address for row in result.rows] == [f"AC{number}" for number in excel_rows]
    assert all(row.sources["version_raw"].value is None and row.values["version_raw"] is None for row in result.rows)
    omitted = review(sheet, *rows[:-1])
    assert len([issue for issue in omitted.issues if issue.code == "HPA_COVERAGE_MISSING" and issue.address == f"Z{excel_rows[-1]}"]) == 1


def test_normal_classifier_sources_cannot_relabel_hpa_identifiers_as_normal_equipment():
    """일반 행의 분류와 HPA 행의 호기·SW를 합쳐 HPA 누락을 숨길 수 없는지 확인한다."""
    sheet = table(record("TEST-O2", "vO2", hpa=False), record("TEST-HPA", "vHPA"))
    normal, hpa = row_at(sheet, 1), row_at(sheet, 2)
    mixed = row_at(sheet, 1)
    for field in ("equipment_code", "version_raw", "pm_name"):
        mixed["values"][field] = hpa["values"][field]
        mixed["sources"][field] = hpa["sources"][field]
    result = review(sheet, normal, mixed)
    assert any(issue.code == "HPA_COVERAGE_MISSING" and issue.blocks_save for issue in result.issues)
    assert recheck_hpa_coverage(sheet, [(mixed["values"], mixed["sources"])])


@pytest.mark.parametrize("field,value", [("group_name", "Oxide"), ("model_name", "M-O2"), ("family_name", "O2")])
def test_manual_classifier_value_change_cannot_clear_original_hpa_coverage(field, value):
    """원본 HPA 분류 주소를 유지한 값 변경만으로 HPA를 일반 설비로 바꿀 수 없는지 확인한다.

    param
    - field, value: 원본 HPA 대신 지정한 일반 설비 분류·모델·계열.
    """
    sheet = table(record("TEST-HPA", "vHPA"))
    payload = row_at(sheet, 1)
    payload["values"][field] = value
    assert recheck_hpa_coverage(sheet, [(payload["values"], payload["sources"])])


def test_hpa_classifier_values_with_other_record_sources_do_not_cover_hpa():
    """현재 분류값이 HPA라도 다른 레코드의 분류 출처를 사용하면 누락을 해제하지 않는지 확인한다."""
    sheet = table(record("TEST-O2", "vO2", hpa=False), record("TEST-HPA", "vHPA"))
    normal, hpa = row_at(sheet, 1), row_at(sheet, 2)
    for field in ("group_name", "model_name", "family_name"):
        hpa["sources"][field] = normal["sources"][field]
    assert recheck_hpa_coverage(sheet, [(hpa["values"], hpa["sources"])])


def test_shared_hpa_section_title_remains_valid_with_distinct_identifier_sources():
    """동일 HPA 표의 병합 구역 제목은 공유하되 각 호기·SW 출처로 레코드를 구분하는지 확인한다."""
    sheet = table(record("TEST-HPA-1"), record("TEST-HPA-2"))
    sheet.values.insert(0, ["HPA", *([None] * (len(FIELDS) - 1))])
    sheet.merged_ranges.append(MergedRange(5, 3, 5, 10))
    rows = [row_at(sheet, index) for index in (2, 3)]
    for row in rows:
        for field in ("group_name", "model_name", "family_name"):
            row["sources"][field] = "C5"
    result = review(sheet, *rows)
    assert not any(issue.code and issue.code.startswith("HPA_COVERAGE") for issue in result.issues)
    assert recheck_hpa_coverage(sheet, [(row["values"], row["sources"]) for row in rows]) == ()


def test_transposed_classifier_mix_does_not_cover_hpa_column():
    """전치 표에서도 일반 분류와 다른 HPA 열의 식별값을 합쳐 누락을 숨길 수 없는지 확인한다."""
    normal, hpa = record("TEST-O2", "vO2", hpa=False), record("TEST-HPA", "vHPA")
    sheet = SheetData("HPA_검증.xlsx", "전치분류혼합", 1, 1,
                      [[field, left, right] for field, left, right in zip(FIELDS, normal, hpa)])
    values = dict(zip(FIELDS, normal))
    sources = {field: sheet.address_of(index, 1) for index, field in enumerate(FIELDS)}
    for field in ("equipment_code", "version_raw", "pm_name"):
        index = FIELDS.index(field)
        values[field] = hpa[index]
        sources[field] = sheet.address_of(index, 2)
    assert recheck_hpa_coverage(sheet, [(values, sources)])
