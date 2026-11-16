"""사용자가 고른 양식의 지정 셀만 안전하게 채우고 원본 패키지를 보존한다."""

from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill

from setron.features.export import template as exporter
from setron.features.export.template import (
    TemplateColumn, TemplateConfig, inspect_template, inspect_template_sheet,
    preview_template, save_template_export,
)
from setron.features.export.management import read_management_records
from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.schemas import ItemLinkInput
from setron.features.release.service import ReleaseService
from test_management_db_source import connected
from test_release_revision import all_rows, release, revision_storage  # noqa: F401


@pytest.fixture
def template_path(tmp_path):
    path = tmp_path / "사업장_양식.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "사업장 양식"
    sheet.merge_cells("A1:D1")
    sheet["A1"] = "사업장 사용자 양식"
    for column, value in enumerate(("호기", "PM", "SW 버전", "공정", "사용자 메모", "계산", "기준일"), 1):
        sheet.cell(3, column, value)
    for row in range(4, 9):
        for column in range(1, 5):
            sheet.cell(row, column, f"old-{row}-{column}")
    sheet["A4"].fill = PatternFill("solid", fgColor="00FFAA")
    sheet["G4"].number_format = "yyyy-mm-dd"
    sheet["E4"] = "보존할 사용자 메모"
    sheet["F4"] = "=LEN(A4)"
    sheet["A9"] = "영역 밖 데이터"
    sheet.column_dimensions["A"].width = 24
    sheet.row_dimensions[4].height = 31
    book.create_sheet("보관")["A1"] = "수정하지 않는 다른 시트"
    book.save(path)
    book.close()
    with ZipFile(path, "a") as archive:
        archive.writestr("custom-preserved/content.bin", b"unknown part must survive")
        archive.comment = b"archive-comment"
    return path


@pytest.fixture
def records():
    base = {"기준일": date(2026, 11, 16), "모델명(원문)": "M-O2", "공정": "ETCH-A"}
    equipment = (
        {**base, "설비 호기": "EQ-A", "사업장": "화성", "PM": "PM1", "SW 버전(원문)": "v1"},
        {**base, "설비 호기": "EQ-A", "사업장": "화성", "PM": "PM2", "SW 버전(원문)": "v2"},
        {**base, "설비 호기": "EQ-B", "사업장": "평택", "PM": "PM1", "SW 버전(원문)": "v3"},
    )
    item = {"관리 모델": "M-O2", "UI": "", "생산 영역": "MEMORY", "Item 번호": "7",
            "내용": "수정 내용", "Jira 코드": "TEAM-7"}
    releases = ({**item, "버전(원문)": "v1", "적용 상태": "적용"},
                {**item, "버전(원문)": "v2", "적용 상태": "미적용"})
    return {"설비현황표": equipment, "릴리즈노트": releases}


def configuration(path, **changes):
    config = TemplateConfig(path, "사업장 양식", "equipment", 3, 4, 8, "rows", (
        TemplateColumn(1, "설비 호기"), TemplateColumn(2, "PM"),
        TemplateColumn(3, "SW 버전(원문)"), TemplateColumn(4, "공정"),
    ), inspect_template(path)["fingerprint"])
    return replace(config, **changes)


def package(path):
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_inspection_recommends_aliases_and_keeps_manual_columns(template_path):
    summary = inspect_template(template_path)
    first = summary["sheets"][0]
    assert first["name"] == "사업장 양식" and first["header_row"] == 3
    details = inspect_template_sheet(template_path, first["name"], 3)
    assert details["fingerprint"] == summary["fingerprint"]
    assert [column["suggested_field"] for column in details["columns"][:4]] == [
        "설비 호기", "PM", "SW 버전(원문)", "공정",
    ]
    assert details["columns"][4]["suggested_field"] is None
    assert "F4" in details["formula_cells"]


def test_version_header_candidates_outrank_release_date_metadata_rows(tmp_path):
    path = tmp_path / "version-columns.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Memory"
    sheet["A1"] = "SW Release List"
    for column, value in enumerate(("버전", "v1.0", "v1.1", "v1.2"), 1):
        sheet.cell(3, column, value)
    for column, value in enumerate(("출시일", "2026.01.01", "2026.02.01", "2026.03.01"), 1):
        sheet.cell(4, column, value)
    book.save(path)
    book.close()
    assert inspect_template(path)["sheets"][0]["header_row"] == 3


def test_rows_fill_clear_stale_values_and_preserve_formula_styles_and_unknown_parts(
        template_path, records, tmp_path, monkeypatch):
    before = template_path.read_bytes()
    config = configuration(template_path, columns=(*configuration(template_path).columns,
                                                 TemplateColumn(7, "기준일")))
    monkeypatch.setattr(exporter, "read_management_records", lambda *_args, **_kwargs: records)
    preview = preview_template(records, config)
    assert preview["row_count"] == 3 and preview["range"] == "A4:G6"
    assert preview["site_counts"] == {"화성": 2, "평택": 1}
    output = tmp_path / "filled.xlsx"

    result = save_template_export(object(), output, config)

    assert result.saved and template_path.read_bytes() == before
    with closing(load_workbook(output)) as book:
        sheet = book["사업장 양식"]
        assert (sheet["A4"].value, sheet["B4"].value, sheet["C4"].value) == ("EQ-A", "PM1", "v1")
        assert sheet["A6"].value == "EQ-B"
        assert sheet["A7"].value is None and sheet["D8"].value is None
        assert sheet["A9"].value == "영역 밖 데이터"
        assert sheet["E4"].value == "보존할 사용자 메모" and sheet["F4"].value == "=LEN(A4)"
        assert sheet["A4"].fill.fgColor.rgb == "0000FFAA"
        assert sheet.column_dimensions["A"].width == 24 and sheet.row_dimensions[4].height == 31
        assert sheet["G4"].value.date() == date(2026, 11, 16)
        assert sheet["G5"].value.date() == date(2026, 11, 16)
        assert sheet["G5"].number_format == "yyyy-mm-dd"
        assert "A1:D1" in {str(value) for value in sheet.merged_cells.ranges}
        assert book["보관"]["A1"].value == "수정하지 않는 다른 시트"
    original, copied = package(template_path), package(output)
    assert original.keys() == copied.keys()
    assert all(copied[name] == data for name, data in original.items()
               if name not in {"xl/worksheets/sheet1.xml", "xl/workbook.xml"})
    with ZipFile(output) as archive:
        assert archive.comment == b"archive-comment"


def test_equipment_columns_use_exact_pm_and_site_filter(template_path, records):
    config = configuration(template_path, mode="columns", site="화성", columns=(
        TemplateColumn(1, "설비 호기"), TemplateColumn(2, "SW 버전(원문)", "PM1"),
        TemplateColumn(3, "SW 버전(원문)", "PM2"), TemplateColumn(4, "SW 버전(원문)", "PM3"),
    ))

    preview = preview_template(records, config)

    assert preview["row_count"] == 1 and preview["rows"] == (("EQ-A", "v1", "v2", ""),)
    assert preview["selected_site"] == "화성"
    assert preview_template(records, replace(config, site="평택"))["rows"] == (("EQ-B", "v3", "", ""),)


def test_release_columns_group_items_and_use_exact_version(template_path, records, tmp_path, monkeypatch):
    config = configuration(template_path, kind="release", mode="columns", columns=(
        TemplateColumn(1, "Item 번호"), TemplateColumn(2, "내용"),
        TemplateColumn(3, "적용 상태", "v1"), TemplateColumn(4, "적용 상태", "v2"),
    ))
    monkeypatch.setattr(exporter, "read_management_records", lambda *_args, **_kwargs: records)

    assert preview_template(records, config)["rows"] == (("7", "수정 내용", "적용", "미적용"),)
    output = tmp_path / "release.xlsx"
    assert save_template_export(object(), output, config).saved
    with closing(load_workbook(output)) as book:
        assert [book["사업장 양식"].cell(4, column).value for column in range(1, 5)] == [
            "7", "수정 내용", "적용", "미적용",
        ]


def test_identical_release_items_in_one_version_are_not_collapsed(template_path, records):
    records["릴리즈노트"] = (*records["릴리즈노트"], dict(records["릴리즈노트"][0]))
    config = configuration(template_path, kind="release", mode="columns", columns=(TemplateColumn(1, "내용"),))
    with pytest.raises(ValueError, match="구분할 수 없습니다"):
        preview_template(records, config)


def independent_lists(records):
    first, second = records["릴리즈노트"]
    return {**records, "릴리즈노트": (
        {**first, "내용": "같은 내용", "Item 번호": "1", "_item_id": 1, "_family_id": 1},
        {**second, "내용": "두 번째 버전 내용", "Item 번호": "3", "_item_id": 3, "_family_id": 1},
        {**first, "내용": "같은 내용", "Item 번호": "2", "_item_id": 2, "_family_id": 1},
    )}


def list_configuration(template_path, **changes):
    return configuration(template_path, kind="release", mode="lists", columns=(
        TemplateColumn(1, "관리 모델"), TemplateColumn(2, "내용", "v1"), TemplateColumn(3, "내용", "v2"),
    ), **changes)


def test_independent_release_lists_pack_versions_without_item_alignment_or_deduplication(
        template_path, records, tmp_path, monkeypatch):
    records = independent_lists(records)
    config = list_configuration(template_path)
    preview = preview_template(records, config)
    assert preview["row_count"] == 2
    assert preview["rows"] == (("M-O2", "같은 내용", "두 번째 버전 내용"), ("M-O2", "같은 내용", ""))
    monkeypatch.setattr(exporter, "read_management_records", lambda *_args, **_kwargs: records)
    output = tmp_path / "independent-lists.xlsx"
    assert save_template_export(object(), output, config).saved
    with closing(load_workbook(output)) as book:
        sheet = book["사업장 양식"]
        assert (sheet["B4"].value, sheet["B5"].value) == ("같은 내용", "같은 내용")
        assert sheet["C4"].value == "두 번째 버전 내용" and sheet["C5"].value is None
        assert sheet["B6"].value is None and sheet["C8"].value is None
        assert sheet["F4"].value == "=LEN(A4)"


def test_independent_release_list_common_columns_require_one_unambiguous_value(template_path, records):
    records = independent_lists(records)
    config = configuration(template_path, kind="release", mode="lists", columns=(
        TemplateColumn(1, "Item 번호"), TemplateColumn(2, "내용", "v1"),
    ))
    with pytest.raises(ValueError, match="버전을 지정"):
        preview_template(records, config)


def test_independent_release_lists_reject_one_version_shared_by_multiple_families(template_path, records):
    records = independent_lists(records)
    records["릴리즈노트"] += ({**records["릴리즈노트"][0], "_family_id": 2, "_item_id": 9},)
    with pytest.raises(ValueError, match="여러 계열"):
        preview_template(records, list_configuration(template_path))


def test_independent_release_lists_reject_capacity_and_equipment_kind(template_path, records):
    with pytest.raises(ValueError, match="마지막 행"):
        preview_template(independent_lists(records), list_configuration(template_path, end_row=4))
    with pytest.raises(ValueError, match="릴리즈노트"):
        preview_template(records, configuration(template_path, mode="lists"))


def test_independent_release_list_qualifiers_preserve_version_case_and_spacing(template_path, records):
    source = records["릴리즈노트"]
    records["릴리즈노트"] = (source[0], {**source[0], "버전(원문)": "v1 ", "내용": "공백 버전"},
                             {**source[0], "버전(원문)": "V1", "내용": "대문자 버전"})
    config = configuration(template_path, kind="release", mode="lists", columns=(
        TemplateColumn(1, "내용", "v1"), TemplateColumn(2, "내용", "v1 "), TemplateColumn(3, "내용", "V1"),
    ))
    assert preview_template(records, config)["rows"] == (("수정 내용", "공백 버전", "대문자 버전"),)


@pytest.mark.parametrize("shared", [False, True], ids=["distinct-identical-items", "shared-one-item"])
def test_release_wide_rows_preserve_database_item_identity(template_path, revision_storage, shared):
    storage = revision_storage
    _, original_item, _, _ = connected(storage)
    second_release = release(storage, raw="v2", order=2)
    target_item = original_item
    if not shared:
        item = all_rows(storage, "item")[0]
        with storage.transaction() as tx:
            target_item = tx.insert("item", {"item_no": item["item_no"], "content": item["content"],
                                             "jira_code": item.get("jira_code"), "batch_id": item["batch_id"]})["item_id"]
    ReleaseService(storage).save_item_links(second_release, (
        ItemLinkInput(item_id=target_item, item_state=ItemState.APPLIED, target_scope=TargetScope.ALL),
    ))
    records = read_management_records(storage)
    assert all("_item_id" in row and "_family_id" in row and "_release_version_id" in row
               for row in records["릴리즈노트"])
    config = configuration(template_path, kind="release", mode="columns", columns=(
        TemplateColumn(1, "Item 번호"), TemplateColumn(2, "내용"),
        TemplateColumn(3, "적용 상태", "v1"), TemplateColumn(4, "적용 상태", "v2"),
    ))
    preview = preview_template(records, config)
    assert preview["row_count"] == (1 if shared else 2)
    if shared:
        assert preview["rows"][0][2:] == ("미적용", "적용")
    else:
        assert {row[2:] for row in preview["rows"]} == {("미적용", ""), ("", "적용")}


@pytest.mark.parametrize("changes", [
    {"mode": "columns", "columns": (TemplateColumn(1, "SW 버전(원문)"),)},
    {"end_row": 4},
    {"columns": ()},
    {"columns": (TemplateColumn(1, "설비 호기"), TemplateColumn(1, "PM"))},
    {"kind": "release", "site": "화성"},
], ids=["ambiguous-common-value", "capacity", "empty-mapping", "duplicate-column", "invalid-site-kind"])
def test_invalid_mapping_or_capacity_is_rejected(template_path, records, changes):
    with pytest.raises(ValueError):
        preview_template(records, configuration(template_path, **changes))


def test_formula_mapping_is_rejected_even_outside_written_data_rows(template_path, records):
    with pytest.raises(ValueError, match="수식"):
        preview_template(records, configuration(template_path, columns=(TemplateColumn(6, "공정"),)))


@pytest.mark.parametrize("formula_type", ["array", "shared"])
def test_formula_master_outside_data_area_still_protects_its_result_range(
        template_path, records, tmp_path, monkeypatch, formula_type):
    parts = package(template_path)
    worksheet = exporter.etree.fromstring(parts["xl/worksheets/sheet1.xml"])
    formula = worksheet.xpath("//*[local-name()='c'][@r='F4']/*[local-name()='f']")[0]
    formula.set("t", formula_type)
    formula.set("ref", "F4:F6")
    parts["xl/worksheets/sheet1.xml"] = exporter.etree.tostring(worksheet)
    with ZipFile(template_path, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    config = configuration(template_path, start_row=5, end_row=6, site="화성",
                           columns=(TemplateColumn(6, "공정"),))
    with pytest.raises(ValueError, match="수식의 결과 범위"):
        preview_template(records, config)
    monkeypatch.setattr(exporter, "read_management_records", lambda *_args, **_kwargs: records)
    output = tmp_path / "preserved.xlsx"
    output.write_bytes(b"original")
    result = save_template_export(object(), output, config)
    assert not result.saved and "수식의 결과 범위" in result.message
    assert output.read_bytes() == b"original"


def test_merged_anchor_conflicts_and_outside_area_are_rejected(template_path, records):
    book = load_workbook(template_path)
    book["사업장 양식"].merge_cells("A4:B4")
    book.save(template_path)
    book.close()
    with pytest.raises(ValueError, match="서로 다른"):
        preview_template(records, configuration(template_path))
    with pytest.raises(ValueError, match="영역 밖"):
        preview_template(records, configuration(template_path, columns=(TemplateColumn(2, "설비 호기"),)))
    matching = configuration(template_path, columns=(TemplateColumn(1, "설비 호기"), TemplateColumn(2, "설비 호기")))
    assert preview_template(records, matching)["row_count"] == 3


def test_merged_cell_cannot_cross_into_footer_beyond_selected_region(template_path, records):
    book = load_workbook(template_path)
    book["사업장 양식"].merge_cells("A7:A9")
    book.save(template_path)
    book.close()
    with pytest.raises(ValueError, match="마지막 행"):
        preview_template(records, configuration(template_path))


@pytest.mark.parametrize("content", ["a" * 40_000, "bad\0text"], ids=["long", "control"])
def test_unrepresentable_values_do_not_publish_output(template_path, records, tmp_path, monkeypatch, content):
    records["설비현황표"] = ({**records["설비현황표"][0], "공정": content},)
    monkeypatch.setattr(exporter, "read_management_records", lambda *_args, **_kwargs: records)
    output = tmp_path / "preserved.xlsx"
    output.write_bytes(b"original")

    result = save_template_export(object(), output, configuration(template_path))

    assert not result.saved and output.read_bytes() == b"original"
    assert not list(tmp_path.glob(".preserved.xlsx.*"))


def test_changed_template_and_same_output_path_are_rejected(template_path, records, tmp_path, monkeypatch):
    config = configuration(template_path)
    monkeypatch.setattr(exporter, "read_management_records", lambda *_args, **_kwargs: records)
    source = template_path.read_bytes()
    assert not save_template_export(object(), template_path, config).saved
    assert template_path.read_bytes() == source
    with ZipFile(template_path, "a") as archive:
        archive.writestr("changed.txt", b"changed")
    result = save_template_export(object(), tmp_path / "output.xlsx", config)
    assert not result.saved and "바뀌었습니다" in result.message
    assert not (tmp_path / "output.xlsx").exists()


@pytest.mark.parametrize("failure", ["write", "replace"])
def test_write_failure_preserves_output_and_template(template_path, records, tmp_path, monkeypatch, failure):
    config = configuration(template_path)
    original_template = template_path.read_bytes()
    monkeypatch.setattr(exporter, "read_management_records", lambda *_args, **_kwargs: records)
    output = tmp_path / "preserved.xlsx"
    output.write_bytes(b"original")
    original_write, writes = ZipFile.writestr, []

    def fail_write(archive, *args, **kwargs):
        original_write(archive, *args, **kwargs)
        writes.append(True)
        if len(writes) == 2:
            raise OSError("simulated interrupted write")

    def fail_replace(*_):
        raise PermissionError("simulated locked file")

    if failure == "write":
        monkeypatch.setattr(ZipFile, "writestr", fail_write)
    else:
        monkeypatch.setattr(exporter.os, "replace", fail_replace)
    result = save_template_export(object(), output, config)
    assert not result.saved and output.read_bytes() == b"original"
    assert template_path.read_bytes() == original_template
    assert not list(tmp_path.glob(".preserved.xlsx.*"))


def test_no_matching_site_does_not_erase_existing_output(template_path, records, tmp_path, monkeypatch):
    monkeypatch.setattr(exporter, "read_management_records", lambda *_args, **_kwargs: records)
    output = tmp_path / "preserved.xlsx"
    output.write_bytes(b"original")
    result = save_template_export(object(), output, configuration(template_path, site="없는 사업장"))
    assert not result.saved and "자료가 없습니다" in result.message
    assert output.read_bytes() == b"original"
    with pytest.raises(ValueError, match="자료가 없습니다"):
        preview_template(records, configuration(template_path, site="없는 사업장"))


def test_missing_calculation_settings_are_added_without_changing_formulas(template_path, records, tmp_path, monkeypatch):
    parts = package(template_path)
    workbook = exporter.etree.fromstring(parts["xl/workbook.xml"])
    for node in tuple(workbook):
        if exporter.etree.QName(node).localname == "calcPr":
            workbook.remove(node)
    parts["xl/workbook.xml"] = exporter.etree.tostring(workbook)
    with ZipFile(template_path, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    config = configuration(template_path)
    monkeypatch.setattr(exporter, "read_management_records", lambda *_args, **_kwargs: records)
    output = tmp_path / "recalculate.xlsx"
    assert save_template_export(object(), output, config).saved
    with closing(load_workbook(output)) as book:
        assert book.calculation.calcMode == "auto"
        assert book.calculation.fullCalcOnLoad and book.calculation.forceFullCalc
        assert book["사업장 양식"]["F4"].value == "=LEN(A4)"
