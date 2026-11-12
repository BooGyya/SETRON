"""선택 파일의 현재 DB 값만 출력하고 수정·삭제·출처 병합을 실제 SQLite 업무로 검증한다."""

from datetime import datetime
from contextlib import closing
from types import SimpleNamespace

import pytest

from setron.features.data_admin import DataAdminService, WORKBOOK_KEY
from setron.features.deletion.service import SnapshotDeletionService
from setron.features.export.management import build_management_workbook, save_management_summary
from setron.features.export.management_files import management_files
from setron.features.item.schemas import ItemState, TargetScope
from setron.features.management.service import ManagementService
from setron.features.release.schemas import ItemLinkInput
from setron.features.release.service import ReleaseService
from test_export_mentoring6 import configure_catalog, records
from test_management_db_source import connected
from test_management_service import edit
from test_release_revision import all_rows, observation, release, revision_storage  # noqa: F401


def name_batch(storage, batch_id, name, when):
    with storage.transaction() as tx:
        raw = tx.get("import_batch", {"batch_id": batch_id})["raw_data"] or {}
        tx.update("import_batch", {"batch_id": batch_id},
                  {"source_type": "EXCEL", "raw_data": {**raw, WORKBOOK_KEY: name}, "imported_at": when})


@pytest.fixture
def files(revision_storage):
    storage = revision_storage
    rid, item_id, first, _ = connected(storage)
    configure_catalog(storage)
    sid, versions = observation(storage, code="EQ-2", versions=((4, 1, "foundry-v1", None),))
    with storage.transaction() as tx:
        second = tx.get("equipment_snapshot", {"snapshot_id": sid})
        rel = tx.get("release_version", {"release_version_id": rid})
        tx.update("equipment_snapshot", {"snapshot_id": first.snapshot_id},
                  {"model_name": "M-O2", "line": "P1F", "production_area": "MEMORY"})
        tx.update("equipment_snapshot", {"snapshot_id": sid},
                  {"model_name": "M-O2", "line": "P3-S5", "production_area": "FOUNDRY"})
    first_id, second_id, note_id = first.batch_id, second["batch_id"], rel["batch_id"]
    name_batch(storage, first_id, "현황.xlsx", datetime(2026, 11, 10, 9, 1, 1))
    name_batch(storage, second_id, "현황.xlsx", datetime(2026, 11, 11, 9, 1, 1))
    name_batch(storage, note_id, "릴리즈.xlsx", datetime(2026, 11, 10, 9, 2, 1))
    return SimpleNamespace(storage=storage, first=first, second=sid, first_id=first_id, second_id=second_id,
                           note_id=note_id, release=rid, item=item_id)


def test_catalog_distinguishes_repeated_filename_and_excludes_manual_audit(files):
    values = management_files(files.storage)
    assert len(values) == 3
    equipment = [value for value in values if value.filename == "현황.xlsx"]
    assert {value.batch_id for value in equipment} == {files.first_id, files.second_id}
    assert equipment[0].imported_at > equipment[1].imported_at
    assert {value.kind for value in equipment} == {"설비현황표"}
    assert next(value for value in values if value.filename == "릴리즈.xlsx").kind == "릴리즈노트"


def test_single_multiple_and_all_files_use_current_records_without_unrelated_releases(files):
    book = build_management_workbook(files.storage, batch_ids=(files.first_id,))
    assert [row["설비 호기"] for row in records(book["설비현황표"])] == ["EQ-1"]
    assert records(book["릴리즈노트"]) == []
    book = build_management_workbook(files.storage, batch_ids=(files.first_id, files.second_id, files.first_id))
    assert {row["설비 호기"] for row in records(book["설비현황표"])} == {"EQ-1", "EQ-2"}
    assert len(records(book["설비현황표"])) == 2
    book = build_management_workbook(files.storage, batch_ids=(files.note_id,))
    assert records(book["설비현황표"]) == []
    assert [row["버전(원문)"] for row in records(book["릴리즈노트"])] == ["v1"]
    book = build_management_workbook(files.storage)
    assert len(records(book["설비현황표"])) == 2 and len(records(book["릴리즈노트"])) == 1


def test_file_and_production_area_filters_are_intersected(files):
    selected = (files.first_id, files.second_id)
    for area, expected in (("MEMORY", "EQ-1"), ("FOUNDRY", "EQ-2")):
        book = build_management_workbook(files.storage, batch_ids=selected, production_area=area)
        assert [row["설비 호기"] for row in records(book["설비현황표"])] == [expected]
    book = build_management_workbook(files.storage, batch_ids=(files.first_id,), production_area="FOUNDRY")
    assert records(book["설비현황표"]) == [] and records(book["릴리즈노트"]) == []


def test_empty_filtered_export_does_not_save_header_only_file_or_replace_existing_file(files, tmp_path):
    path = tmp_path / "Foundry.xlsx"
    result = save_management_summary(files.storage, path, batch_ids=(files.first_id, files.note_id), production_area="FOUNDRY")
    assert not result.saved and not path.exists()
    assert "Foundry 조건" in result.message and "‘전체’" in result.message
    path.write_bytes(b"existing workbook")
    result = save_management_summary(files.storage, path, batch_ids=(files.first_id,), production_area="FOUNDRY")
    assert not result.saved and path.read_bytes() == b"existing workbook"


def test_unclassified_current_equipment_is_exported_by_all_and_not_invented_as_foundry(files, tmp_path):
    with files.storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": files.first.snapshot_id}, {"production_area": None})
    foundry = save_management_summary(files.storage, tmp_path / "Foundry.xlsx", batch_ids=(files.first_id,), production_area="FOUNDRY")
    assert not foundry.saved and "분류가 비어 있는 자료" in foundry.message
    result = save_management_summary(files.storage, tmp_path / "전체.xlsx", batch_ids=(files.first_id, files.note_id))
    assert result.saved
    from openpyxl import load_workbook
    with closing(load_workbook(result.path)) as book:
        assert book["설비현황표"].max_row == 2 and book["릴리즈노트"].max_row == 2
        headers = [cell.value for cell in book["설비현황표"][1]]
        assert book["설비현황표"].cell(2, headers.index("생산 영역") + 1).value is None


def test_management_saved_changes_export_current_values_and_keep_original_file(files):
    management_files(files.storage)  # 목록을 읽은 뒤 저장한 변경도 내보내기에 반영한다.
    service = ManagementService(files.storage)
    version = files.first.software[0]
    service.save((edit(service, "equipment_snapshot", {"snapshot_id": files.first.snapshot_id}, {"line": "P4H"}),
                  edit(service, "equipment_version", {"equipment_version_id": version.equipment_version_id},
                       {"version_raw": "v1-edited", "release_version_id": None}),
                  edit(service, "item", {"item_id": files.item}, {"content": "저장한 최신 내용"})))
    book = build_management_workbook(files.storage, batch_ids=(files.first_id, files.note_id))
    row = records(book["설비현황표"])[0]
    assert (row["라인"], row["SW 버전(원문)"]) == ("P4H", "v1-edited")
    assert records(book["릴리즈노트"])[0]["내용"] == "저장한 최신 내용"
    assert len(management_files(files.storage)) == 3  # MANUAL 저장 이력은 별도 가져온 파일이 아니다.


@pytest.mark.parametrize("deletion", ["data-management", "import-delete"])
def test_deleted_snapshot_never_returns_from_raw_source_or_remaining_equipment_identity(files, deletion):
    if deletion == "data-management":
        DataAdminService(files.storage).delete_rows(files.first_id, (files.first.snapshot_id,))
    else:
        service = SnapshotDeletionService(files.storage)
        revision, batches = service.batches()
        result = service.delete(revision, (next(value.key for value in batches if value.batch_id == files.first_id),))
        assert result.status == "success"
    assert any(row["equipment_code"] == "EQ-1" for row in all_rows(files.storage, "equipment"))
    assert all(row.batch_id != files.first_id for row in management_files(files.storage))
    for selected in (None, (files.first_id,), (files.first_id, files.second_id)):
        book = build_management_workbook(files.storage, batch_ids=selected)
        assert all(row["설비 호기"] != "EQ-1" for row in records(book["설비현황표"]))


def test_current_merged_source_is_selected_once_not_reconstructed_from_old_cells(files):
    version = files.first.software[0]
    with files.storage.transaction() as tx:
        tx.update("equipment_version", {"equipment_version_id": version.equipment_version_id},
                  {"version_raw": "merged-current", "batch_id": files.second_id, "source_reference": {"version_raw": [
                      {"batch_id": files.first_id, "sheet": "현황", "cells": ["E1"]},
                      {"batch_id": files.second_id, "sheet": "현황", "cells": ["B1"]}]}})
    book = build_management_workbook(files.storage, batch_ids=(files.first_id, files.second_id))
    rows = records(book["설비현황표"])
    assert len(rows) == 2
    assert next(row for row in rows if row["설비 호기"] == "EQ-1")["SW 버전(원문)"] == "merged-current"


@pytest.mark.parametrize("removed", ["item", "release"])
def test_deleted_release_or_item_never_reappears_from_the_original_batch(files, removed):
    before = records(build_management_workbook(files.storage, batch_ids=(files.note_id,))["릴리즈노트"])
    with files.storage.transaction() as tx:
        if removed == "item":
            tx.delete_release_item(files.item)
        else:
            tx.delete_release_version(files.release)
    for selected in (None, (files.note_id,)):
        rows = records(build_management_workbook(files.storage, batch_ids=selected)["릴리즈노트"])
        if removed == "item":
            assert all(row["내용"] != before[0]["내용"] for row in rows)
        else:
            assert all(row["버전(원문)"] != "v1" for row in rows)


def test_pm_selection_does_not_include_other_files_sibling_pm(files):
    sid, _ = observation(files.storage, code="SHARED", pms=True,
                         versions=((1, 1, "v1", files.release), (1, 1, "v2", None)))
    with files.storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": sid}, {"batch_id": files.first_id})
        pms = [row for row in all_rows(files.storage, "equipment_pm") if row["snapshot_id"] == sid]
        for row, batch in zip(pms, (files.first_id, files.second_id)):
            tx.update("equipment_pm", {"pm_id": row["pm_id"]}, {"batch_id": batch})
    for batch, pm in ((files.first_id, "PM1"), (files.second_id, "PM2")):
        book = build_management_workbook(files.storage, batch_ids=(batch,))
        assert [row["PM"] for row in records(book["설비현황표"]) if row["설비 호기"] == "SHARED"] == [pm]


def test_shared_item_uses_current_release_context_without_other_files_items(files):
    foreign_release = release(files.storage, raw="foreign-v2", order=2)
    with files.storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": {WORKBOOK_KEY: "외부.xlsx"}})
        item = tx.insert("item", {"item_no": 99, "content": "다른 파일의 항목", "batch_id": batch["batch_id"]})
    ReleaseService(files.storage).save_item_links(foreign_release, (
        ItemLinkInput(item_id=files.item, item_state=ItemState.APPLIED, target_scope=TargetScope.ALL),
        ItemLinkInput(item_id=item["item_id"], item_state=ItemState.APPLIED, target_scope=TargetScope.ALL)))
    rows = records(build_management_workbook(files.storage, batch_ids=(files.note_id,))["릴리즈노트"])
    assert {row["버전(원문)"] for row in rows} == {"v1", "foreign-v2"}
    assert all(row["내용"] != "다른 파일의 항목" for row in rows)


@pytest.mark.parametrize("selection", [(), (999999,), (True,), ("1",)])
def test_missing_or_invalid_selection_does_not_fall_back_to_all(files, selection, tmp_path):
    path = tmp_path / "선택.xlsx"
    result = save_management_summary(files.storage, path, batch_ids=selection)
    assert not result.saved and not path.exists()
