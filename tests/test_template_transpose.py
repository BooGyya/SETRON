"""항목명이 세로인 양식을 원본 배치 그대로 채우며 입력 영역을 보호한다."""

from contextlib import closing
from dataclasses import replace
from datetime import date
from zipfile import ZipFile

import pytest
from lxml import etree
from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill
from openpyxl.worksheet.formula import ArrayFormula

from setron.features.export import template as exporter
from setron.features.export.template import (
    TemplateColumn, TemplateConfig, TemplateRow, inspect_template, inspect_template_sheet, load_template,
    prepare_template_records, preview_template, render_template_sheet, save_template_export,
    write_template_package,
)


@pytest.fixture
def template_path(tmp_path):
    path = tmp_path / "세로_양식.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "세로 양식"
    sheet.merge_cells("A1:E1")
    sheet["A1"] = "사용자 설비현황표"
    for row, value in enumerate(("항목", "설비번호", "PM", "SW 버전", "공정", "기준일", "사용자 메모", "계산"), 3):
        sheet.cell(row, 2, value)
    for row in range(4, 8):
        for column in range(3, 6):
            sheet.cell(row, column, f"old-{row}-{column}")
    sheet["C4"].fill = PatternFill("solid", fgColor="00FFAA")
    sheet["C8"].number_format = "yyyy-mm-dd"
    sheet["C9"] = "보존할 메모"
    sheet["C10"] = "=LEN(C4)"
    sheet["F4"] = "입력 영역 밖"
    sheet["A4"] = "왼쪽 설명"
    sheet.column_dimensions["C"].width = 24
    sheet.row_dimensions[4].height = 31
    book.create_sheet("보관")["A1"] = "다른 시트 보존"
    book.save(path)
    book.close()
    with ZipFile(path, "a") as archive:
        archive.writestr("custom-preserved/content.bin", b"unmapped-part")
        archive.comment = b"comment-preserved"
    return path


@pytest.fixture
def records():
    base = {"기준일": date(2026, 11, 16), "모델명(원문)": "M-O2", "공정": "ETCH-A"}
    item = {"관리 모델": "M-O2", "UI": "", "생산 영역": "MEMORY", "Item 번호": "7",
            "내용": "=원문 내용\n한글😀", "Jira 코드": "TEAM-7"}
    return {
        "설비현황표": (
            {**base, "설비 호기": "EQ-A", "사업장": "화성", "PM": "PM1", "SW 버전(원문)": "v1"},
            {**base, "설비 호기": "EQ-A", "사업장": "화성", "PM": "PM2", "SW 버전(원문)": "v2"},
            {**base, "설비 호기": "EQ-B", "사업장": "평택", "PM": "PM1", "SW 버전(원문)": "v3"},
        ),
        "릴리즈노트": (
            {**item, "버전(원문)": "v1", "적용 상태": "적용"},
            {**item, "버전(원문)": "v2", "적용 상태": "미적용"},
        ),
    }


def configuration(path, **changes):
    value = TemplateConfig(
        path, "세로 양식", "equipment", 1, 2, 2, "rows", (), inspect_template(path)["fingerprint"],
        orientation="columns", header_column=2, start_column=3, end_column=5,
        rows=(TemplateRow(4, "설비 호기"), TemplateRow(5, "PM"), TemplateRow(6, "SW 버전(원문)"),
              TemplateRow(7, "공정"), TemplateRow(8, "기준일")),
    )
    return replace(value, **changes)


def save(records, config, path, monkeypatch):
    monkeypatch.setattr(exporter, "read_management_records", lambda *_args, **_kwargs: records)
    return save_template_export(object(), path, config)


def test_vertical_inspection_recommends_row_fields_and_header_column(template_path):
    summary = inspect_template(template_path)
    assert summary["sheets"][0]["header_column"] == 2
    metadata = inspect_template_sheet(template_path, "세로 양식", orientation="columns", header_column=2)
    assert metadata["orientation"] == "columns" and metadata["columns"] == []
    field = next(row for row in metadata["rows"] if row["row"] == 4)
    assert field == {"row": 4, "label": "4", "title": "설비번호", "suggested_field": "설비 호기", "qualifier": ""}
    assert next(row for row in metadata["rows"] if row["row"] == 6)["suggested_field"] == "SW 버전(원문)"


def test_vertical_merged_parent_recommends_pm_qualifier(tmp_path):
    path = tmp_path / "pm-label.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.merge_cells("A3:A4")
    sheet["A3"], sheet["B3"], sheet["B4"] = "PM1", "SW 버전", "공정"
    book.save(path)
    book.close()
    metadata = inspect_template_sheet(path, "Sheet", orientation="columns", header_column=2)
    row = next(row for row in metadata["rows"] if row["row"] == 3)
    assert (row["title"], row["suggested_field"], row["qualifier"]) == ("PM1 / SW 버전", "SW 버전(원문)", "PM1")


def test_transpose_preview_and_output_preserve_package_styles_and_unmapped_cells(
        template_path, records, tmp_path, monkeypatch):
    before = template_path.read_bytes()
    config = configuration(template_path)
    preview = preview_template(records, config)
    assert preview["row_count"] == preview["record_count"] == 3
    assert preview["columns"] == ("C", "D", "E") and preview["range"] == "C4:E8"
    assert preview["rows"][0] == ("EQ-A", "EQ-A", "EQ-B")
    assert preview["rows"][2] == ("v1", "v2", "v3")
    assert preview["row_labels"][0] == "4 · 설비 호기"
    output = tmp_path / "filled.xlsx"
    assert save(records, config, output, monkeypatch).saved
    assert template_path.read_bytes() == before
    with closing(load_workbook(output)) as book:
        sheet = book["세로 양식"]
        assert [sheet.cell(4, column).value for column in range(3, 6)] == ["EQ-A", "EQ-A", "EQ-B"]
        assert [sheet.cell(5, column).value for column in range(3, 6)] == ["PM1", "PM2", "PM1"]
        assert sheet["C8"].value.date() == sheet["D8"].value.date() == date(2026, 11, 16)
        assert sheet["C4"].fill.fgColor.rgb == "0000FFAA"
        assert sheet.row_dimensions[4].height == 31
        assert sheet.column_dimensions["C"].width == sheet.column_dimensions["D"].width == 24
        assert sheet["A4"].value == "왼쪽 설명" and sheet["F4"].value == "입력 영역 밖"
        assert sheet["C9"].value == "보존할 메모" and sheet["C10"].value == "=LEN(C4)"
        assert str(sheet.merged_cells) == "A1:E1" and book["보관"]["A1"].value == "다른 시트 보존"
    with ZipFile(output) as archive:
        assert archive.read("custom-preserved/content.bin") == b"unmapped-part"
        assert archive.comment == b"comment-preserved"


def test_transpose_site_filter_clears_old_values_only_in_mapped_rows(template_path, records, tmp_path, monkeypatch):
    config = configuration(template_path, site="화성")
    output = tmp_path / "hwaseong.xlsx"
    assert save(records, config, output, monkeypatch).saved
    with closing(load_workbook(output)) as book:
        sheet = book["세로 양식"]
        assert sheet["C4"].value == sheet["D4"].value == "EQ-A"
        assert sheet["E4"].value is None and sheet["E7"].value is None
        assert sheet["C9"].value == "보존할 메모"


def test_pm_column_grain_can_be_written_to_field_rows(template_path, records):
    config = configuration(template_path, mode="columns", rows=(
        TemplateRow(4, "설비 호기"), TemplateRow(6, "SW 버전(원문)", "PM1"),
        TemplateRow(7, "SW 버전(원문)", "PM2")))
    preview = preview_template(records, config)
    assert preview["record_count"] == 2 and preview["columns"] == ("C", "D")
    assert preview["rows"] == (("EQ-A", "EQ-B"), ("v1", "v3"), ("v2", ""))


@pytest.mark.parametrize("mode", ["rows", "columns", "lists"])
def test_release_record_grains_work_after_transpose(template_path, records, tmp_path, monkeypatch, mode):
    if mode == "rows":
        mappings = (TemplateRow(4, "버전(원문)"), TemplateRow(5, "Item 번호"), TemplateRow(6, "내용"))
        expected = (("v1", "v2"), ("7", "7"), ("=원문 내용\n한글😀", "=원문 내용\n한글😀"))
    else:
        mappings = (TemplateRow(4, "내용", "v1"), TemplateRow(5, "내용", "v2"))
        expected = (("=원문 내용\n한글😀",), ("=원문 내용\n한글😀",))
    config = configuration(template_path, kind="release", mode=mode, rows=mappings)
    assert preview_template(records, config)["rows"] == expected
    output = tmp_path / f"release-{mode}.xlsx"
    assert save(records, config, output, monkeypatch).saved
    with closing(load_workbook(output)) as book:
        literal_cell = book["세로 양식"]["C6" if mode == "rows" else "C4"]
        assert literal_cell.value == "=원문 내용\n한글😀" and literal_cell.data_type == "s"


def test_independent_lists_pack_horizontally_without_sparse_alignment(template_path, records):
    records["릴리즈노트"] += ({**records["릴리즈노트"][1], "Item 번호": "8", "내용": "두 번째 내용"},)
    config = configuration(template_path, kind="release", mode="lists", rows=(
        TemplateRow(4, "내용", "v1"), TemplateRow(5, "내용", "v2")))
    assert preview_template(records, config)["rows"] == (
        ("=원문 내용\n한글😀", ""), ("=원문 내용\n한글😀", "두 번째 내용"))


@pytest.mark.parametrize("change, message", [
    ({"end_column": 3}, "마지막 열"),
    ({"header_column": 3}, "항목명 열"),
    ({"orientation": "diagonal"}, "방향"),
    ({"rows": ()}, "한 행 이상"),
    ({"rows": (TemplateRow(4, "설비 호기"), TemplateRow(4, "공정"))}, "같은 양식 행"),
    ({"rows": (TemplateRow(1_048_577, "설비 호기"),)}, "연결"),
    ({"rows": (TemplateColumn(4, "설비 호기"),)}, "방향"),
    ({"rows": ({"row": 4, "field": "설비 호기"},)}, "방향"),
])
def test_transpose_validates_active_axis_without_using_legacy_row_bounds(template_path, records, change, message):
    with pytest.raises(ValueError, match=message):
        preview_template(records, configuration(template_path, **change))
    assert preview_template(records, configuration(template_path, header_row=-1, start_row=-1, end_row=-1))["row_count"] == 3


@pytest.mark.parametrize("merged", ["C4:F4", "B4:C4"])
def test_transpose_rejects_merged_cells_crossing_record_axis_or_header(template_path, records, merged):
    with closing(load_workbook(template_path)) as book:
        book["세로 양식"].merge_cells(merged)
        book.save(template_path)
    with pytest.raises(ValueError, match="병합"):
        preview_template(records, configuration(template_path))


def test_transpose_rejects_conflicting_fields_in_one_merged_cell(template_path, records):
    with closing(load_workbook(template_path)) as book:
        book["세로 양식"].merge_cells("C4:C5")
        book.save(template_path)
    with pytest.raises(ValueError, match="같은 병합 셀"):
        preview_template(records, configuration(template_path))


@pytest.mark.parametrize("empty", [False, True])
def test_transpose_protects_array_formula_results_even_if_master_is_outside_area(
        template_path, records, empty):
    with closing(load_workbook(template_path)) as book:
        book["세로 양식"]["A6"] = ArrayFormula(ref="A6:E6", text="=COLUMN(A6:E6)")
        book.save(template_path)
    config = configuration(template_path)
    if empty:
        records = {"설비현황표": (), "릴리즈노트": ()}
    with pytest.raises(ValueError, match="배열·공유 수식"):
        prepare_template_records(records, config, allow_empty=empty)


def test_transpose_protects_shared_formula_range_with_master_outside_mapped_rows(template_path, records):
    with ZipFile(template_path) as archive:
        entries = tuple((entry, archive.read(entry.filename)) for entry in archive.infolist())
    with ZipFile(template_path, "w") as archive:
        for entry, raw in entries:
            if entry.filename == "xl/worksheets/sheet1.xml":
                root = etree.fromstring(raw)
                namespace = {"s": root.nsmap[None]}
                cell = root.find("s:sheetData/s:row[@r='4']/s:c[@r='A4']", namespace)
                formula = etree.SubElement(cell, f"{{{root.nsmap[None]}}}f", t="shared", si="0", ref="A4:E4")
                formula.text = "COLUMN(A4)"
                raw = etree.tostring(root)
            archive.writestr(entry, raw)
    with pytest.raises(ValueError, match="배열·공유 수식"):
        preview_template(records, configuration(template_path))


def test_transpose_empty_clear_accepts_merge_wholly_inside_mapped_input(template_path, tmp_path):
    with closing(load_workbook(template_path)) as book:
        book["세로 양식"].merge_cells("C4:D4")
        book.save(template_path)
    config = configuration(template_path)
    package, sheet, rows, clear, assignments = prepare_template_records(
        {"설비현황표": (), "릴리즈노트": ()}, config, allow_empty=True)
    output = tmp_path / "empty-merged.xlsx"
    write_template_package(package, output, {package.sheets[config.sheet_name]: render_template_sheet(
        package, sheet, config, clear, assignments)})
    with closing(load_workbook(output)) as book:
        assert "C4:D4" in book["세로 양식"].merged_cells
        assert book["세로 양식"]["C4"].value is None
        assert book["세로 양식"]["C9"].value == "보존할 메모"


def test_transpose_clear_empty_preserves_unmapped_formula_and_works_with_shared_package(
        template_path, tmp_path):
    config = configuration(template_path)
    records = {"설비현황표": (), "릴리즈노트": ()}
    with pytest.raises(ValueError, match="현재 저장 자료"):
        preview_template(records, config)
    package = load_template(template_path, config.fingerprint)
    template, sheet, rows, clear, assignments = prepare_template_records(
        records, config, allow_empty=True, template=package)
    assert template is package and not rows and not assignments
    preview = preview_template(records, config, allow_empty=True, template=package)
    assert preview["row_count"] == 0 and preview["range"] == "C4:E8"
    output = tmp_path / "cleared.xlsx"
    write_template_package(template, output, {template.sheets[config.sheet_name]: render_template_sheet(
        template, sheet, config, clear, assignments)})
    with closing(load_workbook(output)) as book:
        sheet = book["세로 양식"]
        assert sheet["C4"].value is None and sheet["E7"].value is None
        assert sheet["C9"].value == "보존할 메모" and sheet["C10"].value == "=LEN(C4)"


def test_transpose_rejects_direct_formula_mapping_without_touching_existing_output(
        template_path, records, tmp_path, monkeypatch):
    config = configuration(template_path, rows=(TemplateRow(10, "설비 호기"),))
    output = tmp_path / "existing.xlsx"
    output.write_bytes(b"keep-existing")
    result = save(records, config, output, monkeypatch)
    assert not result.saved and "수식" in result.message
    assert output.read_bytes() == b"keep-existing"


def test_transpose_rejects_changed_template_and_same_source_destination(template_path, records, monkeypatch):
    config = configuration(template_path)
    original = template_path.read_bytes()
    assert not save(records, config, template_path, monkeypatch).saved
    assert template_path.read_bytes() == original
    with closing(load_workbook(template_path)) as book:
        book["보관"]["B1"] = "changed"
        book.save(template_path)
    with pytest.raises(ValueError, match="바뀌었습니다"):
        preview_template(records, config)
