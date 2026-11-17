"""시트별 위치·생산 영역 조건과 한 파일의 원자적 맞춤 저장을 검증한다."""

from contextlib import closing
from dataclasses import replace
from zipfile import ZipFile

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill

from setron.features.export import management, template as renderer, template_workbook as exporter
from setron.features.export.template import TemplateColumn, TemplateConfig, TemplateRow, inspect_template
from setron.features.export.template_workbook import (
    SheetExportConfig, WorkbookTemplateConfig, preview_workbook_template,
    read_workbook_records, save_workbook_template_export, validate_workbook_config,
)
from test_release_revision import all_rows, observation, release, revision_storage  # noqa: F401


@pytest.fixture
def workbook_config(tmp_path):
    path = tmp_path / "여러_시트_양식.xlsx"
    book = Workbook()
    row_sheet = book.active
    row_sheet.title = "13L"
    row_sheet.append(("고객 설비", "고객 버전", "메모", "계산"))
    for row in range(2, 5):
        row_sheet.cell(row, 1, "오래된 설비")
        row_sheet.cell(row, 2, "오래된 버전")
    row_sheet["A2"].fill = PatternFill("solid", fgColor="00FFAA")
    row_sheet["C2"] = "사용자 메모"
    row_sheet["D2"] = "=LEN(A2)"
    row_sheet["A5"] = "영역 밖"
    column_sheet = book.create_sheet("Foundry")
    column_sheet["A1"] = "고객 Item"
    column_sheet["A2"] = "고객 내용"
    for column in range(2, 5):
        column_sheet.cell(1, column, "오래된 번호")
        column_sheet.cell(2, column, "오래된 내용")
    column_sheet["B3"] = "=LEN(B2)"
    column_sheet["E1"] = "영역 밖"
    book.create_sheet("안내")["A1"] = "원본 안내문"
    book.save(path)
    book.close()
    with ZipFile(path, "a") as archive:
        archive.writestr("custom-preserved/content.bin", b"unknown part")
        archive.comment = b"workbook-comment"
    fingerprint = inspect_template(path)["fingerprint"]
    first = TemplateConfig(path, "13L", "equipment", 1, 2, 4, "rows", (
        TemplateColumn(1, "설비 호기"), TemplateColumn(2, "SW 버전(원문)")), fingerprint)
    second = TemplateConfig(path, "Foundry", "release", 1, 2, 4, "rows", (), fingerprint,
                            orientation="columns", header_column=1, start_column=2, end_column=4,
                            rows=(TemplateRow(1, "Item 번호"), TemplateRow(2, "내용")))
    return WorkbookTemplateConfig(path, (SheetExportConfig(first, line="13L"),
                                         SheetExportConfig(second, production_area="FOUNDRY")))


@pytest.fixture
def workbook_records():
    empty = {"설비현황표": (), "릴리즈노트": ()}
    return {
        "13L": {**empty, "설비현황표": (
            {"설비 호기": "EQ-13L", "SW 버전(원문)": "v1", "사업장": "화성"},)},
        "Foundry": {**empty, "릴리즈노트": (
            {"Item 번호": "7", "내용": "수정 사항"},
            {"Item 번호": "8", "내용": "=문자 그대로"})},
    }


def package(path):
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def use_records(monkeypatch, values):
    monkeypatch.setattr(exporter, "read_workbook_records", lambda *_args, **_kwargs: values)


def test_mixed_orientation_single_file_loads_source_once_and_preserves_other_parts(
        workbook_config, workbook_records, monkeypatch, tmp_path):
    use_records(monkeypatch, workbook_records)
    calls = []
    original = renderer.load_template

    def load_once(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(renderer, "load_template", load_once)
    before = workbook_config.path.read_bytes()
    output = tmp_path / "완성.xlsx"
    result = save_workbook_template_export(object(), output, workbook_config)
    assert result.saved and len(calls) == 1
    assert workbook_config.path.read_bytes() == before
    with closing(load_workbook(output)) as book:
        assert book["13L"]["A2"].value == "EQ-13L"
        assert book["13L"]["A3"].value is None
        assert book["13L"]["A5"].value == "영역 밖"
        assert book["13L"]["C2"].value == "사용자 메모"
        assert book["13L"]["D2"].value == "=LEN(A2)"
        assert book["13L"]["A2"].fill.fgColor.rgb == "0000FFAA"
        assert book["Foundry"]["B1"].value == "7"
        assert book["Foundry"]["C2"].value == "=문자 그대로"
        assert book["Foundry"]["C2"].data_type == "s"
        assert book["Foundry"]["D2"].value is None
        assert book["Foundry"]["B3"].value == "=LEN(B2)"
        assert book["Foundry"]["E1"].value == "영역 밖"
    old, saved = package(workbook_config.path), package(output)
    changed = {"xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml", "xl/workbook.xml"}
    assert old.keys() == saved.keys()
    assert all(saved[name] == raw for name, raw in old.items() if name not in changed)
    with ZipFile(output) as archive:
        assert archive.comment == b"workbook-comment"


def test_empty_sheet_clears_only_mapped_input_and_reports_notice(
        workbook_config, workbook_records, monkeypatch, tmp_path):
    workbook_records["Foundry"]["릴리즈노트"] = ()
    preview = preview_workbook_template(workbook_records, workbook_config)
    assert preview["empty_sheets"] == ("Foundry",)
    assert preview["sheets"][1]["action"] == "clear"
    assert "입력 영역을 비웁니다" in preview["messages"][0]
    use_records(monkeypatch, workbook_records)
    output = tmp_path / "빈시트.xlsx"
    result = save_workbook_template_export(object(), output, workbook_config)
    assert result.saved and "Foundry" in result.message and "비웠습니다" in result.message
    with closing(load_workbook(output)) as book:
        assert all(book["Foundry"].cell(row, column).value is None
                   for row in (1, 2) for column in (2, 3, 4))
        assert book["Foundry"]["A1"].value == "고객 Item"
        assert book["Foundry"]["B3"].value == "=LEN(B2)"
        assert book["Foundry"]["E1"].value == "영역 밖"


def test_all_empty_sheets_can_be_cleared(workbook_config, workbook_records, monkeypatch, tmp_path):
    for records in workbook_records.values():
        records["설비현황표"] = records["릴리즈노트"] = ()
    use_records(monkeypatch, workbook_records)
    output = tmp_path / "모두빈시트.xlsx"
    assert save_workbook_template_export(object(), output, workbook_config).saved
    assert preview_workbook_template(workbook_records, workbook_config)["record_count"] == 0
    with closing(load_workbook(output)) as book:
        assert book["13L"]["A2"].value is None and book["Foundry"]["B1"].value is None


def test_empty_keep_preserves_whole_sheet_xml_exactly(
        workbook_config, workbook_records, monkeypatch, tmp_path):
    workbook_records["Foundry"]["릴리즈노트"] = ()
    use_records(monkeypatch, workbook_records)
    output = tmp_path / "빈시트유지.xlsx"
    config = replace(workbook_config, empty_policy="keep")
    assert save_workbook_template_export(object(), output, config).saved
    assert package(output)["xl/worksheets/sheet2.xml"] == package(config.path)["xl/worksheets/sheet2.xml"]


@pytest.mark.parametrize("failure", ["empty", "formula", "capacity", "merge"])
def test_any_later_sheet_failure_preserves_previous_output_and_source(
        workbook_config, workbook_records, monkeypatch, tmp_path, failure):
    config = workbook_config
    if failure == "empty":
        workbook_records["Foundry"]["릴리즈노트"] = ()
        config = replace(config, empty_policy="error")
    elif failure == "capacity":
        second = replace(config.sheets[1].template, end_column=2)
        config = replace(config, sheets=(config.sheets[0], replace(config.sheets[1], template=second)))
    else:
        with closing(load_workbook(config.path)) as book:
            if failure == "formula":
                book["Foundry"]["B1"] = "=1+1"
            else:
                book["Foundry"].merge_cells("A1:B1")
            book.save(config.path)
        fingerprint = inspect_template(config.path)["fingerprint"]
        config = replace(config, sheets=tuple(replace(settings, template=replace(
            settings.template, fingerprint=fingerprint)) for settings in config.sheets))
        # 자료가 없어도 수식·병합 영역을 덮는 매핑은 먼저 거절한다.
        workbook_records["Foundry"]["릴리즈노트"] = ()
    use_records(monkeypatch, workbook_records)
    source = config.path.read_bytes()
    output = tmp_path / "기존.xlsx"
    output.write_bytes(b"existing output")
    result = save_workbook_template_export(object(), output, config)
    assert not result.saved and output.read_bytes() == b"existing output"
    assert config.path.read_bytes() == source
    assert not list(tmp_path.glob(".기존.xlsx.*"))


def test_replace_failure_preserves_output_and_removes_temporary(
        workbook_config, workbook_records, monkeypatch, tmp_path):
    use_records(monkeypatch, workbook_records)
    output = tmp_path / "교체.xlsx"
    output.write_bytes(b"existing output")

    def denied(*_args):
        raise PermissionError("locked")

    monkeypatch.setattr(renderer.os, "replace", denied)
    assert not save_workbook_template_export(object(), output, workbook_config).saved
    assert output.read_bytes() == b"existing output"
    assert not list(tmp_path.glob(".교체.xlsx.*"))


def test_source_duplicate_fingerprint_and_missing_scope_cannot_be_published(
        workbook_config, workbook_records, monkeypatch, tmp_path):
    use_records(monkeypatch, workbook_records)
    before = workbook_config.path.read_bytes()
    assert not save_workbook_template_export(object(), workbook_config.path, workbook_config).saved
    assert workbook_config.path.read_bytes() == before
    with pytest.raises(ValueError, match="같은 시트"):
        validate_workbook_config(replace(workbook_config, sheets=(workbook_config.sheets[0],) * 2))
    changed = replace(workbook_config.sheets[1], template=replace(workbook_config.sheets[1].template,
                                                               fingerprint="different"))
    with pytest.raises(ValueError, match="검토 시점"):
        validate_workbook_config(replace(workbook_config, sheets=(workbook_config.sheets[0], changed)))
    with pytest.raises(ValueError, match="다시 읽은"):
        preview_workbook_template({"13L": workbook_records["13L"]}, workbook_config)


def test_aliased_internal_sheet_parts_are_rejected(workbook_config, workbook_records):
    source = package(workbook_config.path)
    # 두 시트 이름이 같은 OOXML part를 가리키면 마지막 결과가 앞 결과를 덮을 수 있다.
    name = "xl/_rels/workbook.xml.rels"
    source[name] = source[name].replace(b"/xl/worksheets/sheet2.xml", b"/xl/worksheets/sheet1.xml")
    with ZipFile(workbook_config.path, "w") as archive:
        for part, raw in source.items():
            archive.writestr(part, raw)
    fingerprint = inspect_template(workbook_config.path)["fingerprint"]
    config = replace(workbook_config, sheets=tuple(replace(settings, template=replace(
        settings.template, fingerprint=fingerprint)) for settings in workbook_config.sheets))
    with pytest.raises(ValueError, match="같은 내부 영역"):
        preview_workbook_template(workbook_records, config)


@pytest.fixture
def scoped_storage(revision_storage):
    return populate_scoped_storage(revision_storage)


def populate_scoped_storage(storage):
    first = release(storage, raw="v1")
    future = release(storage, raw="v2", order=2)
    other = release(storage, family=2, raw="other-v1")
    locations = (("EQ-A", 1, 1, first, "화성", "13L", "MEMORY"),
                 ("EQ-B", 2, 3, other, "화성", "13L-2", "FOUNDRY"),
                 ("EQ-C", 2, 3, other, "평택", "13L", "FOUNDRY"))
    for code, family, group, linked, site, line, area in locations:
        sid, _ = observation(storage, code=code, versions=((family, group, "v1", linked),))
        with storage.transaction() as tx:
            tx.update("equipment_snapshot", {"snapshot_id": sid},
                      {"site": site, "line": line, "production_area": area})
    release_batch = next(row["batch_id"] for row in all_rows(storage, "release_version")
                         if row["release_version_id"] == future)
    with storage.transaction() as tx:
        tx.update("import_batch", {"batch_id": release_batch},
                  {"source_type": "EXCEL", "raw_data": {"노트": {"A1": "v2"}}})
        tx.insert("item", {"content": "미연결 Item", "batch_id": release_batch, "edited_fields": []})
    return storage, release_batch


def test_scopes_share_snapshot_match_exact_locations_and_limit_release_families(scoped_storage, monkeypatch):
    storage, _ = scoped_storage
    reads = []
    original = management.read_management_data

    def read_once(*args):
        reads.append(1)
        return original(*args)

    monkeypatch.setattr(management, "read_management_data", read_once)
    result = management.read_management_records_by_scope(storage, {
        "대상": {"site": "화성", "line": "13L"},
        "다른사업장": {"site": "평택", "line": "13L"},
        "소문자": {"site": "화성", "line": "13l"},
        "없는라인": {"line": "13"},
    })
    assert len(reads) == 1
    assert [row["설비 호기"] for row in result["대상"]["설비현황표"]] == ["EQ-A"]
    assert {row["버전(원문)"] for row in result["대상"]["릴리즈노트"]} == {"v1", "v2"}
    assert {row["_family_id"] for row in result["대상"]["릴리즈노트"]} == {1}
    assert [row["설비 호기"] for row in result["다른사업장"]["설비현황표"]] == ["EQ-C"]
    for key in ("소문자", "없는라인"):
        assert not result[key]["설비현황표"] and not result[key]["릴리즈노트"]


def test_release_only_batch_uses_all_current_equipment_for_scope_and_choices(scoped_storage):
    storage, release_batch = scoped_storage
    result = management.read_management_records_by_scope(storage, {
        "릴리즈": {"site": "화성", "line": "13L"},
        "무관": {"site": "평택", "line": "13L"},
    }, batch_ids=(release_batch,))
    assert result["릴리즈"]["설비현황표"] == ()
    assert [row["버전(원문)"] for row in result["릴리즈"]["릴리즈노트"]] == ["v2"]
    assert not result["무관"]["릴리즈노트"]
    choices = result["릴리즈"]["_scope_choices"]
    assert choices["sites"] == ("평택", "화성")
    assert choices["lines"] == ("13L", "13L-2")
    assert choices["lines_by_site"]["평택"] == ("13L",)
    assert set(choices["production_areas"]) >= {"MEMORY", "FOUNDRY"}


def test_sheet_and_global_production_areas_intersect_never_broaden(scoped_storage):
    storage, _ = scoped_storage
    result = management.read_management_records_by_scope(storage, {
        "일치": {"production_area": "MEMORY"},
        "반대": {"production_area": "FOUNDRY"},
        "전체": {},
    }, production_area="MEMORY")
    assert {row["설비 호기"] for row in result["일치"]["설비현황표"]} == {"EQ-A"}
    assert result["전체"]["설비현황표"] == result["일치"]["설비현황표"]
    assert result["반대"]["설비현황표"] == result["반대"]["릴리즈노트"] == ()
    assert result["반대"]["_scope"]["conflicting_area"] is True


def test_wrapper_passes_explicit_scopes_and_never_guesses_sheet_names(
        workbook_config, scoped_storage, monkeypatch):
    storage, _ = scoped_storage
    calls = []
    original = exporter.read_management_records_by_scope

    def read(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(exporter, "read_management_records_by_scope", read)
    config = replace(workbook_config, sheets=(
        replace(workbook_config.sheets[0], line=None, site="평택"),
        replace(workbook_config.sheets[1], production_area="MEMORY")))
    result = read_workbook_records(storage, config)
    assert calls == [{"13L": {"site": "평택", "line": None, "production_area": None},
                      "Foundry": {"site": None, "line": None, "production_area": "MEMORY"}}]
    assert [row["설비 호기"] for row in result["13L"]["설비현황표"]] == ["EQ-C"]
    assert [row["설비 호기"] for row in result["Foundry"]["설비현황표"]] == ["EQ-A"]


@pytest.mark.parametrize("field,value", [("site", ""), ("site", "  "), ("line", ""), ("line", "\t ")])
def test_blank_location_is_rejected_instead_of_falling_back_to_all(
        workbook_config, scoped_storage, field, value):
    settings = replace(workbook_config.sheets[0], **{field: value})
    config = replace(workbook_config, sheets=(settings,))
    with pytest.raises(ValueError, match="명시적으로"):
        validate_workbook_config(config)
    with pytest.raises(ValueError, match="명시적으로"):
        management.read_management_records_by_scope(scoped_storage[0], {"대상": {field: value}})
