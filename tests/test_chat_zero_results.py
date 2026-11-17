"""0건 검색은 빈 ID를 보존하고, 전체 범위 선택과 구분한다. 합성 자료만 사용한다."""
from dataclasses import replace

import pytest
from openpyxl import load_workbook

from setron.features.chat import answer, recognize
from setron.features.chat.export import query_export
from setron.features.chat.schemas import Intent
from setron.features.export.query_result import ExportRange, range_counts, save_query_result
from test_chat_questions import CATALOG, DATA, OCT, ask


@pytest.mark.parametrize("question,total", [
    ("화성 15L 목표 N 미만 설비 보여줘", 2),
    ("평택 P2 목표 N 미만 설비 보여줘", 2),
    ("화성 13L 목표 N 미만 설비 보여줘", 1),  # 목표 미지정은 미달이 아니며 HPA는 제외
    ("화성 16L 목표 달성한 설비 보여줘", 1),
    ("화성 15L 확인 필요 설비 보여줘", 2),
])
def test_zero_search_keeps_empty_ids_and_independent_export_ranges(question, total):
    result = ask(question)
    assert not result.issues and Intent.SEARCH in result.request.intents
    assert not next(t for t in result.tables if t.title == "조회 결과").rows
    assert result.equipment_ids == ()
    export = query_export(result, DATA)
    assert export.shown_ids == ()                  # None은 전체 범위이므로 빈 튜플과 구분해야 한다.
    assert range_counts(export) == {ExportRange.SHOWN: 0, ExportRange.ALL: total}


@pytest.mark.parametrize("intents", [(Intent.SEARCH, Intent.COMPARE), (Intent.COMPARE, Intent.SEARCH)])
def test_zero_search_in_combined_request_is_not_replaced_by_analysis_scope(intents):
    request = ask("화성 15L 목표 N 미만 설비 보여줘").request
    result = answer(replace(request, intents=intents), DATA)
    assert result.charts                         # 분석할 전체 자료는 있지만 검색 결과는 0건이다.
    assert not next(t for t in result.tables if t.title == "조회 결과").rows
    assert result.equipment_ids == ()
    assert range_counts(query_export(result, DATA))[ExportRange.SHOWN] == 0


@pytest.mark.parametrize("intents", [(Intent.COMPARE,), (Intent.CAUSE,), (Intent.PRIORITY,),
                                     (Intent.FORECAST,), (Intent.REPORT,), (Intent.VERSION_INFO,)])
def test_non_search_analysis_keeps_existing_scope_ids(intents):
    request = ask("화성 15L 현황 보여줘").request
    result = answer(replace(request, intents=intents, verdict=None), DATA)
    assert result.equipment_ids == (9, 10)
    export = query_export(result, DATA)
    assert export.shown_ids is None
    assert range_counts(export) == {ExportRange.SHOWN: 2, ExportRange.ALL: 2}


@pytest.mark.parametrize("scope,expected_codes", [
    (ExportRange.SHOWN, set()),
    (ExportRange.ALL, {"H-001", "H-002"}),
])
def test_zero_query_saved_excel_matches_selected_scope(tmp_path, scope, expected_codes):
    result = ask("화성 15L 목표 N 미만 설비 보여줘")
    path = tmp_path / f"{scope.name}.xlsx"
    assert save_query_result(query_export(result, DATA), path, scope=scope).saved
    book = load_workbook(path, read_only=True)
    try:
        assert book.sheetnames == ["조회 결과", "조회 조건"]
        assert book["조회 결과"]["A1"].value == "설비명"
        assert {row[0] for row in book["조회 결과"].iter_rows(min_row=2, values_only=True)} == expected_codes
        assert book["조회 결과"].max_row == 1 + len(expected_codes)
    finally:
        book.close()


def test_known_nonempty_search_ids_and_both_ranges_are_unchanged():
    result = ask("평택 O2 중 목표 N 미만인 설비만 보여줘")
    assert set(result.equipment_ids) == {1, 5}
    assert range_counts(query_export(result, DATA)) == {ExportRange.SHOWN: 2, ExportRange.ALL: 6}


@pytest.mark.parametrize("target_is_set", [True, False])
def test_exact_25_equipment_zero_result_does_not_leak_into_shown_export(tmp_path, target_is_set):
    template = next(r for r in DATA.records if r.equipment_id == 9 and r.base_date == OCT)
    records = tuple(replace(template, equipment_id=1000 + i, equipment_code=f"ZERO-{i:03d}") for i in range(25))
    data = replace(DATA, records=records, targets=DATA.targets if target_is_set else {})
    result = answer(recognize("화성 15L 목표 N 미만 설비 보여줘", CATALOG), data)
    assert "설비 25대 중 목표 N 미만은 0대" in result.summary
    assert result.equipment_ids == ()
    export = query_export(result, data)
    assert range_counts(export) == {ExportRange.SHOWN: 0, ExportRange.ALL: 25}
    if not target_is_set:
        assert any("목표 N이 정해지지 않은 계열" in notice for notice in result.notices)
    path = tmp_path / "zero-of-25.xlsx"
    assert save_query_result(export, path, scope=ExportRange.SHOWN).saved
    book = load_workbook(path, read_only=True)
    try:
        assert book["조회 결과"].max_row == 1
        assert not list(book["조회 결과"].iter_rows(min_row=2, values_only=True))
    finally:
        book.close()
