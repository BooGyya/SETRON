"""AI 분석 조회 결과 Excel 내보내기를 검증한다 (BE-021, 화면 10-1)."""

from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aggregation.service import summarize
from setron.features.export.query_result import (ALL_COLUMNS, CONDITION_SHEET, RESULT_SHEET, ExportRange,
                                                 QueryColumn, QueryResultExport, build_query_result,
                                                 default_filename, range_counts, save_query_result, source_labels)
from setron.features.export.schemas import TargetRow
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from test_management_service import observe
from test_release_revision import release, revision_storage  # noqa: F401

M_O2, M_O3 = 1, 2
F_O2, F_O3 = 10, 20
DAY = date(2026, 10, 21)
FAMILIES = {
    F_O2: FamilyReleases(F_O2, [ReleaseVersion(101, F_O2, "v2.1", 1), ReleaseVersion(102, F_O2, "v2.2", 2)]),
    F_O3: FamilyReleases(F_O3, [ReleaseVersion(201, F_O3, "v3.1", 1)]),
}
TARGETS = {(M_O2, F_O2): 102, (M_O3, F_O3): 201}
NAMES = {F_O2: "O2", F_O3: "O3"}


def record(eid, line, *pms, hpa=False):
    return EquipmentRecord(eid, f"DM-{eid:03d}", DAY, "평택", line, "M-O2", tuple(pms), is_hpa=hpa)


def o2(version):
    return PmVersion(None, M_O2, F_O2, version)


RECORDS = (
    record(3, "P2", o2("v2.1")),                                    # 미달
    record(1, "P1", o2("v2.2")),                                    # 달성
    record(2, "P1", o2(None)),                                      # 확인 필요
    record(4, "P3", o2("v2.1"), PmVersion(None, M_O3, F_O3, "v3.1")),  # 혼합: O2 미달, O3 달성
    record(9, "P1", o2("v2.2"), hpa=True),
)


def export(**changes):
    values = dict(summary=summarize(RECORDS, FAMILIES, TARGETS), question="평택 O2 중 목표 N 미만인 설비만 보여줘",
                  conditions=(("Site", "평택"), ("설비군", "M-O2"), ("판정", "목표 N 미만")), shown_ids=(3, 2, 4),
                  family_names=NAMES, sources={(3, F_O2): "P2 · G5 · 입력 작업 7"},
                  created_at=datetime(2026, 11, 6, 9, 30))
    values.update(changes)
    return QueryResultExport(**values)


def table(sheet):
    return [list(row) for row in sheet.iter_rows(values_only=True)]


def condition_values(sheet):
    """조회 조건 시트의 위쪽 항목·값 줄 (빈 줄 전까지)."""
    rows = []
    for row in table(sheet)[1:]:
        if row[0] is None:
            break
        rows.append((row[0], row[1]))
    return dict(rows)


def target_table(sheet):
    rows = table(sheet)
    start = next(i for i, row in enumerate(rows) if row[0] == "모델·계열별 목표 N")
    return [row[:3] for row in rows[start + 2:] if row[0] is not None]


def test_range_counts_follow_shown_equipment_and_exclude_hpa():
    assert range_counts(export()) == {ExportRange.SHOWN: 3, ExportRange.ALL: 4}
    assert range_counts(export(shown_ids=None)) == {ExportRange.SHOWN: 4, ExportRange.ALL: 4}


def test_shown_range_writes_one_row_per_equipment_and_family_sorted_by_location():
    book = build_query_result(export())

    rows = table(book[RESULT_SHEET])
    assert rows[0] == [c.value for c in ALL_COLUMNS]
    assert [r[0] for r in rows[1:]] == ["DM-002", "DM-003", "DM-004", "DM-004"]  # P1, P2, P3 순, 혼합은 2행
    assert rows[2] == ["DM-003", "평택 · P2", "O2 v2.1", "v2.2", "N-1", "P2 · G5 · 입력 작업 7"]
    assert rows[1][2] == "O2 (버전 없음)" and rows[1][4] == "판단 불가" and rows[1][5] == "출처 없음"
    assert {r[2].split()[0] for r in rows[3:]} == {"O2", "O3"}


def test_all_range_includes_compliant_equipment_but_not_hpa():
    rows = table(build_query_result(export(), scope=ExportRange.ALL)[RESULT_SHEET])

    assert [r[0] for r in rows[1:]] == ["DM-001", "DM-002", "DM-003", "DM-004", "DM-004"]


def test_chosen_columns_keep_screen_order_and_scope_stays_without_recognized_conditions():
    """요청 인식 조건을 꺼도 조회 조건 시트는 남기고 질문 줄만 뺀다 (BE-021: 범위·기준일·N 확인)."""
    book = build_query_result(export(), columns=(QueryColumn.JUDGMENT, QueryColumn.EQUIPMENT), include_conditions=False)

    assert book.sheetnames == [RESULT_SHEET, CONDITION_SHEET]
    assert table(book[RESULT_SHEET])[0] == ["설비명", "판정"]
    values = condition_values(book[CONDITION_SHEET])
    assert "질문" not in values and "요청 인식" not in values
    assert values["조회 범위 · Site"] == "평택" and values["기준일"] == "2026-10-21"
    assert target_table(book[CONDITION_SHEET])
    with pytest.raises(ValueError, match="열을 하나 이상"):
        build_query_result(export(), columns=())


def test_condition_sheet_records_question_conditions_range_and_time():
    values = condition_values(build_query_result(export())[CONDITION_SHEET])

    assert values["질문"] == "평택 O2 중 목표 N 미만인 설비만 보여줘"
    assert values["요청 인식"].startswith("아래 조회 범위는 AI가")
    assert values["조회 범위 · Site"] == "평택" and values["조회 범위 · 판정"] == "목표 N 미만"
    assert values["기준일"] == "2026-10-21"
    assert values["내보낸 범위"] == "조회 결과 3대 (4행 · 설비 1대 × 계열 1개)"
    assert values["같은 조건의 전체 설비"] == "4대 · HPA 1대 제외"
    assert values["생성 시각"] == "2026-11-06 09:30"


def test_text_that_looks_like_a_formula_stays_literal(tmp_path):
    path = tmp_path / "결과.xlsx"
    assert save_query_result(export(question="=SUM(A1)", shown_ids=None), path).saved

    saved = load_workbook(path)
    try:
        cell = saved[CONDITION_SHEET]["B2"]
        assert cell.value == "=SUM(A1)" and cell.data_type == "s"
    finally:
        saved.close()


def test_save_rejects_wrong_extension_and_empty_columns(tmp_path):
    assert not save_query_result(export(), tmp_path / "결과.csv").saved
    result = save_query_result(export(), tmp_path / "결과.xlsx", columns=())
    assert not result.saved and "열을 하나 이상" in result.message
    assert not (tmp_path / "결과.xlsx").exists()


def test_default_filename_uses_conditions_and_base_date():
    assert default_filename(export()) == "AI조회_평택_M-O2_목표N미만_2026-10-21.xlsx"
    assert default_filename(export(conditions=(("모델", 'C4/"DFB"?'),))) == "AI조회_C4DFB_2026-10-21.xlsx"


def test_source_labels_use_stored_sheet_cell_and_batch(revision_storage):  # noqa: F811
    snapshot = observe(revision_storage, release(revision_storage, raw="v1", order=1))

    labels = source_labels((snapshot,))

    batch = snapshot.software[0].batch_id
    assert labels == {(snapshot.equipment_id, 1): f"현황 · E1 · 입력 작업 {batch}"}


def test_target_table_uses_given_targets_or_collects_them_from_results():
    """모델·계열별 목표 N 표: 넘겨받은 목표를 그대로 쓰고, 없으면 판정에 쓰인 목표를 모은다."""
    given = export(targets=(TargetRow("M-O2", "O2", "v2.2"), TargetRow("M-O5", "O5", None)))
    assert target_table(build_query_result(given)[CONDITION_SHEET]) == [
        ["M-O2", "O2", "v2.2"], ["M-O5", "O5", "미지정 (판단 불가)"]]

    assert target_table(build_query_result(export())[CONDITION_SHEET]) == [
        ["M-O2", "O2", "v2.2"], ["M-O2", "O3", "v3.1"]]
