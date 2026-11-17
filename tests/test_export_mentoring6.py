"""6차 멘토링: 사용자 Excel의 분류·라인·누락·재가져오기 검증. 운영 DB는 변경하지 않는다."""

from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import date

import pytest
from openpyxl import load_workbook

from setron.features.ai.schemas import ExtractKind
from setron.features.export.management import build_management_workbook, save_management_summary
from setron.features.extraction.service import review_extraction_result
from setron.features.excel_import.service import build_save_request, draft_from_review, validate
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.schemas import ItemLinkInput
from setron.features.release.service import ReleaseService
from setron.features.rule_extraction.service import rule_extract
from setron.infrastructure.excel_reader import SheetData
from test_management_db_source import connected
from test_release_revision import all_rows, observation, release, revision_storage  # noqa: F401


def configure_catalog(storage):
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 1}, {"group_name": "M-O2"})
        tx.update("equipment_group", {"group_id": 3}, {"group_name": "M-C3"})
        for group_id, model, parent in ((5, "M-C4", None), (6, "M-C4 DFB", 5)):
            tx.insert("equipment_group", {"group_id": group_id, "group_name": model, "category": "POLY",
                "parent_group_id": parent, "analysis_enabled": True})
        for family_id, model_id, name in ((3, 5, "C4"), (4, 1, "O2 (파운드리)"), (5, 4, "HPA")):
            tx.insert("version_family", {"family_id": family_id, "group_id": model_id,
                                         "family_name": name, "ui_type": None})
    return storage


@pytest.fixture
def source(revision_storage):
    storage = revision_storage
    connected(storage)
    configure_catalog(storage)
    specs = (
        ("P1F", "MEMORY", 1, 1, "M-O2", "v1"),
        ("P1D", "MEMORY", 3, 2, "M-C3", "v2.1"),
        ("P4H", "MEMORY", 6, 3, "M-C4 DFB", "v4.1_DFB "),
        ("P4D", "MEMORY", 5, 3, "M-C4", "v4.1"),
        ("P3-S5", "FOUNDRY", 1, 4, "M-O2", "v3.1"),
        ("V2", "FOUNDRY", 4, 5, "HPA", "v9.1"),
        ("NEW-LINE-77", None, 1, 1, "M-O2", "v1"),
    )
    for line, area, model_id, family_id, model, version in specs:
        snapshot_id, _ = observation(storage, code="TEST-" + line, versions=((family_id, model_id, version, None),))
        with storage.transaction() as tx:
            tx.update("equipment_snapshot", {"snapshot_id": snapshot_id},
                      {"site": "평택", "line": line, "production_area": area, "model_name": model})
    c3_release = release(storage, family=2, raw="v2.1", order=0)
    release(storage, family=2, raw="v2.2", order=2)
    release(storage, family=4, raw="v3.1", order=1)
    release(storage, family=5, raw="v9.1", order=1)
    with storage.transaction() as tx:
        batch_id = all_rows(storage, "import_batch")[0]["batch_id"]
        tx.update("release_version", {"release_version_id": c3_release}, {"version_suffix": "test", "item_mapping_status": "VERIFIED"})
        c3_item = tx.insert("item", {"item_no": 21, "content": "C3 전체 추출 검증", "batch_id": batch_id})
        equipment = tx.insert("equipment", {"equipment_code": "HPA-NO-SW"})
        tx.insert("equipment_snapshot", {"equipment_id": equipment["equipment_id"], "base_date": date(2026, 10, 1),
            "site": "평택", "line": "V2", "model_name": "HPA", "group_id": 4,
            "production_area": "FOUNDRY", "batch_id": batch_id})
        tx.insert("equipment", {"equipment_code": "DB-ONLY-EQ"})
    ReleaseService(storage).save_item_links(c3_release, (ItemLinkInput(item_id=c3_item["item_id"],
        item_state=ItemState.UNKNOWN, target_scope=TargetScope.LISTED, target_group_ids=(3,)),))
    return storage, specs


def records(sheet):
    rows = list(sheet.iter_rows(values_only=True))
    return [dict(zip(rows[0], row)) for row in rows[1:]]


def test_all_export_preserves_lines_classifications_dfb_hpa_and_empty_versions(source, tmp_path):
    storage, specs = source
    path = tmp_path / "정규_관리자료.xlsx"
    result = save_management_summary(storage, path)
    assert result.saved
    with closing(load_workbook(path)) as book:
        assert book.sheetnames == ["설비현황표", "릴리즈노트"]
        for sheet in book:
            assert not {"SW 계열", "버전 계열"}.intersection(cell.value for cell in sheet[1])
        rows = {row["설비 호기"]: row for row in records(book["설비현황표"])}
        for line, area, _, _, model, version in specs:
            row = rows["TEST-" + line]
            assert (row["라인"], row["생산 영역"], row["모델명(원문)"], row["SW 버전(원문)"]) == (line, {None: None, "MEMORY": "Memory", "FOUNDRY": "Foundry"}[area], model, version)
        assert rows["TEST-P4H"]["설비군"] == "DFB"
        assert rows["TEST-P4D"]["설비군"] == "C4"
        assert rows["HPA-NO-SW"]["설비군"] == "HPA" and rows["HPA-NO-SW"]["SW 버전(원문)"] is None
        assert "DB-ONLY-EQ" not in rows  # 관측 없는 식별자는 삭제 후 남는 호기와 구분할 수 없어 현황표에 넣지 않는다.
        assert len(rows) == len(all_rows(storage, "equipment_snapshot"))
        assert {row["버전(원문)"] for row in records(book["릴리즈노트"])} == {row["version_raw"] for row in all_rows(storage, "release_version")}


@pytest.mark.parametrize("scope", ["MEMORY", "FOUNDRY"])
def test_selected_scope_does_not_mix_other_equipment_or_relabel_linked_items_as_orphans(source, scope):
    storage, _ = source
    book = build_management_workbook(storage, production_area=scope)
    expected = {row["equipment_id"] for row in all_rows(storage, "equipment_snapshot")
                if row["production_area"] == scope}
    codes = {row["equipment_code"] for row in all_rows(storage, "equipment") if row["equipment_id"] in expected}
    actual = records(book["설비현황표"])
    assert {row["설비 호기"] for row in actual} == codes
    assert all(row["생산 영역"] == {"MEMORY": "Memory", "FOUNDRY": "Foundry"}[scope] for row in actual)
    if scope == "FOUNDRY":
        assert all(row["생산 영역"] == "Foundry" for row in records(book["릴리즈노트"]))
        assert not any(row["내용"] == "원본 안전 개선" for row in records(book["릴리즈노트"]))


def test_exported_values_reenter_the_real_rule_and_provenance_pipeline(source):
    storage, specs = source
    book = build_management_workbook(storage)
    for sheet, kind in ((book["설비현황표"], ExtractKind.EQUIPMENT_STATUS), (book["릴리즈노트"], ExtractKind.RELEASE_NOTE)):
        data = SheetData("관리자료.xlsx", sheet.title, 1, 1, [list(row) for row in sheet.iter_rows(values_only=True)])
        result = rule_extract(kind, data)
        assert result is not None and result.ok
        reviewed = review_extraction_result(kind, data, result)
        assert reviewed.ok
        if kind is ExtractKind.EQUIPMENT_STATUS:
            values = {row.values.get("equipment_code"): row.values for row in reviewed.data.rows}
            for line, area, _, _, model, version in specs:
                row = values["TEST-" + line]
                assert (row["line"], row["production_area"], row["model_name"], row["version_raw"]) == (line, area, model, version)
            assert values["TEST-P4H"]["family_name"] == "C4"
            assert values["TEST-P1D"]["ui_type"] == "LEGACY"
            assert all(not issue.reason.startswith("추출값을 원본") for issue in reviewed.data.issues)
        else:
            assert Counter(row.values.get("version_raw") for row in reviewed.data.rows) == Counter(row["버전(원문)"] for row in records(sheet))
            foundry = next(row.values for row in reviewed.data.rows if row.values.get("version_raw") == "v3.1")
            assert foundry["production_area"] == "FOUNDRY"


def test_unknown_scope_is_rejected_without_widening_to_all(source):
    with pytest.raises(ValueError):
        build_management_workbook(source[0], production_area="unknown")


def test_reimport_valid_observations_into_a_separate_database_keeps_key_values(source, tmp_path):
    original, specs = source
    path = tmp_path / "equipment-roundtrip.xlsx"
    assert save_management_summary(original, path).saved
    book = load_workbook(path)
    values = list(book["설비현황표"].iter_rows(values_only=True))
    book.close()
    # 원본에서 필수값 자체가 없는 행은 그대로 출력되며, 입력 검토에서도 보완 대상으로 남긴다.
    values = [values[0], *(row for row in values[1:] if row[1] and row[13])]
    sheet = SheetData("관리자료.xlsx", "설비현황표", 1, 1, [list(row) for row in values])
    response = rule_extract(ExtractKind.EQUIPMENT_STATUS, sheet)
    reviewed = review_extraction_result(ExtractKind.EQUIPMENT_STATUS, sheet, response)
    with contextmanager(revision_storage.__wrapped__)() as target:
        configure_catalog(target)
        saver = DatabaseImportSaver(target)
        draft = draft_from_review(reviewed.data, source=response.source)
        reference = saver.load_reference((draft,))
        errors = [(issue.row_id, issue.field, issue.message) for issue in validate(draft, reference)
                  if issue.level.value == "ERROR"]
        assert not errors
        request = build_save_request(draft, "roundtrip-observations", reference)
        saver.save(request)
        equipment = {row["equipment_id"]: row["equipment_code"] for row in all_rows(target, "equipment")}
        snapshots = {equipment[row["equipment_id"]]: row for row in all_rows(target, "equipment_snapshot")}
        software = {row["snapshot_id"]: row for row in all_rows(target, "equipment_version")}
        for line, area, _, _, model, raw_version in specs:
            snapshot = snapshots["TEST-" + line]
            assert (snapshot["line"], snapshot["production_area"], snapshot["model_name"]) == (line, area, model)
            assert software[snapshot["snapshot_id"]]["version_raw"] == raw_version


def test_release_notes_reimport_preserves_all_versions_items_states_and_order(source, tmp_path):
    original, _ = source
    path = tmp_path / "release-roundtrip.xlsx"
    assert save_management_summary(original, path).saved
    book = load_workbook(path)
    rows = [list(row) for row in book["릴리즈노트"].iter_rows(values_only=True)]
    book.close()
    sheet = SheetData("관리자료.xlsx", "릴리즈노트", 1, 1, rows)
    response = rule_extract(ExtractKind.RELEASE_NOTE, sheet)
    reviewed = review_extraction_result(ExtractKind.RELEASE_NOTE, sheet, response)
    with contextmanager(revision_storage.__wrapped__)() as target:
        configure_catalog(target)
        saver = DatabaseImportSaver(target)
        draft = draft_from_review(reviewed.data, source=response.source)
        reference = saver.load_reference((draft,))
        errors = [(issue.row_id, issue.field, issue.message) for issue in validate(draft, reference) if issue.blocks_save]
        assert not errors
        saver.save(build_save_request(draft, "roundtrip-releases", reference))
        key = lambda row: (row["family_id"], row["version_raw"], row["version_suffix"], row["version_order"], row["item_mapping_status"])
        assert Counter(map(key, all_rows(target, "release_version"))) == Counter(map(key, all_rows(original, "release_version")))
        assert Counter((row["item_no"], row["content"]) for row in all_rows(target, "item")) == Counter(
            (row["item_no"], row["content"]) for row in all_rows(original, "item"))
        assert [(row["item_state"], row["target_scope"], row["target_group_ids"]) for row in all_rows(target, "release_version_item")] == [
            (row["item_state"], row["target_scope"], row["target_group_ids"]) for row in all_rows(original, "release_version_item")]


def test_native_format_still_blocks_missing_hpa_rows(source):
    book = build_management_workbook(source[0])
    sheet = SheetData("관리자료.xlsx", "설비현황표", 1, 1,
                     [list(row) for row in book["설비현황표"].iter_rows(values_only=True)])
    response = rule_extract(ExtractKind.EQUIPMENT_STATUS, sheet)
    omitted = replace(response, data=replace(response.data, rows=tuple(
        row for row in response.data.rows if row.values.get("model_name") != "HPA")))
    reviewed = review_extraction_result(ExtractKind.EQUIPMENT_STATUS, sheet, omitted)
    assert any(issue.code == "HPA_COVERAGE_MISSING" and issue.blocks_save for issue in reviewed.data.issues)


def test_native_format_still_blocks_conflicting_sites_for_one_equipment(source):
    book = build_management_workbook(source[0])
    values = [list(row) for row in book["설비현황표"].iter_rows(values_only=True)]
    native = next(row for row in values[1:] if row[0] == "TEST-P1F")
    values.append([native[0], native[1], "다른 사업장", *native[3:]])
    sheet = SheetData("관리자료.xlsx", "설비현황표", 1, 1, values)
    response = rule_extract(ExtractKind.EQUIPMENT_STATUS, sheet)
    reviewed = review_extraction_result(ExtractKind.EQUIPMENT_STATUS, sheet, response)
    assert any(issue.code == "SITE_CONFLICT" for issue in validate(draft_from_review(reviewed.data, source=response.source)))
