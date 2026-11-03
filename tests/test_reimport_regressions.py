"""BE-009 작업 검토에서 발견한 부분 갱신·묶음 연결 충돌 회귀를 검증한다."""

import pytest
from copy import deepcopy
from dataclasses import replace
from datetime import date

from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.service import ImportValidationService
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.inputs import merge_sources
from setron.features.storage.validation import StorageValidationError
from test_import_validation import draft, row
from test_snapshot_reimport import count, save, snapshot, storage  # noqa: F401


@pytest.mark.parametrize("other_pm", ["PM1", "PM2"])
@pytest.mark.parametrize("existing", [False, True])
def test_conflicting_reviewed_release_ids_across_requests_hold_the_whole_batch(storage, other_pm, existing):
    if existing:
        save(storage, row(version_raw="alias"), row(2, pm_name="PM2", version_raw="alias"))
    before = snapshot(storage)
    batches = count(storage, "import_batch")
    first, = ImportValidationService(storage).prepare((draft(row(version_raw="alias"), sheet="첫 검토"),),
        "review-one", aliases={1: {"alias": "v1"}})
    second, = ImportValidationService(storage).prepare((draft(row(pm_name=other_pm, version_raw="alias"), sheet="다른 검토"),),
        "review-two", aliases={1: {"alias": "v2"}})
    with pytest.raises(StorageValidationError, match="릴리즈 연결.*상충"):
        DatabaseImportSaver(storage).save_many((first, second))
    assert snapshot(storage) == before
    assert count(storage, "import_batch") == batches


def test_matching_confirmed_release_ids_across_requests_can_share_the_same_sw(storage):
    first, = ImportValidationService(storage).prepare((draft(row(version_raw="alias"), sheet="PM1 검토"),),
        "review-one", aliases={1: {"alias": "v1"}})
    second, = ImportValidationService(storage).prepare((draft(row(pm_name="PM2", version_raw="alias"), sheet="PM2 검토"),),
        "review-two", aliases={1: {"alias": "v1"}})
    DatabaseImportSaver(storage).save_many((first, second))
    stored = snapshot(storage)
    assert len(stored.software) == 1
    assert stored.software[0].release_version_id == 1
    assert {pm.pm_name for pm in stored.software[0].pms} == {"PM1", "PM2"}


def original(storage, record, field):
    locations = record.source_reference[field]
    locations = locations if isinstance(locations, list) else [locations]
    values = []
    with storage.transaction() as tx:
        for location in locations:
            batch = tx.get("import_batch", {"batch_id": location.get("batch_id", record.batch_id)})
            values.extend(batch["raw_data"][location["sheet"]][cell] for cell in location["cells"])
    return values


def test_partial_pm_import_preserves_whole_model_and_its_original_while_updating_line(storage):
    save(storage, row(line="12L"), row(2, pm_name="PM2", model_name="M-O3", family_name="O3",
                                      version_raw="o3-v1", line="12L"), sheet="처음 현황")
    before = snapshot(storage)
    receipt = save(storage, row(pm_name="PM2", model_name="M-O3", family_name="O3", version_raw="o3-v2", line="14L"),
                   request_id="partial-pm-model", sheet="나중 현황")
    stored = snapshot(storage)
    assert stored.model_name == before.model_name == "M-O2"
    assert stored.line == "14L" and stored.batch_id == receipt.batch_id
    assert original(storage, stored, "model_name") == original(storage, before, "model_name")
    assert stored.source_reference["model_name"]["batch_id"] == before.batch_id
    assert original(storage, stored, "line") == ["14L"]
    pm2 = next(pm for sw in stored.software for pm in sw.pms if pm.pm_name == "PM2")
    assert pm2.model_name_raw == "M-O3" and pm2.batch_id == receipt.batch_id
    cells = EquipmentService(storage).get_snapshot_sources("E-1", stored.base_date)
    assert {cell.batch_id for cell in cells if cell.field == "model_name"} == {before.batch_id}
    assert {cell.value for cell in cells if cell.field == "line"} == {"14L"}
    assert all(cell.available for cell in cells)
    pm_cells = EquipmentService(storage).get_snapshot_sources("E-1", stored.base_date, pm_name="PM2")
    assert {cell.batch_id for cell in pm_cells} == {receipt.batch_id}
    assert next(cell.value for cell in pm_cells if cell.field == "version_raw") == "o3-v2"


def test_equal_model_name_uses_latest_model_original_instead_of_retaining_an_old_pointer(storage):
    save(storage, row(), sheet="이전 동일 모델")
    before = snapshot(storage)
    receipt = save(storage, row(line="14L"), request_id="same-model-latest-source", sheet="최근 동일 모델")
    stored = snapshot(storage)
    assert stored.model_name == before.model_name == "M-O2"
    assert stored.batch_id == receipt.batch_id
    assert stored.source_reference["model_name"]["sheet"] == "최근 동일 모델"
    assert "batch_id" not in stored.source_reference["model_name"]
    assert original(storage, stored, "model_name") == ["M-O2"]


def test_full_pm_reimport_preserves_a_mixed_whole_model_raw_text_and_repeatable_origin(storage):
    sheet = "설비 전체 모델 원문"
    def source(cell):
        return {"sheet": sheet, "cells": [cell]}
    raw = {"A1": "M-O2/O3 혼합", "B1": "v1", "C1": "PM1", "D1": "o3-v1", "E1": "PM2"}
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": {sheet: raw}})
    initial = SnapshotInput(equipment_code="E-1", base_date=date(2026, 11, 2), site="화성", model_name=raw["A1"],
        batch_id=batch["batch_id"], source_reference={"model_name": source("A1")},
        software=(SoftwareInput(family_id=1, version_raw="v1", source_reference={"version_raw": source("B1")},
            pms=(PmInput(pm_name="PM1", group_id=1, model_name_raw="M-O2", source_reference={"pm_name": source("C1")}),)),
            SoftwareInput(family_id=2, version_raw="o3-v1", source_reference={"version_raw": source("D1")},
            pms=(PmInput(pm_name="PM2", group_id=2, model_name_raw="M-O3", source_reference={"pm_name": source("E1")}),))))
    EquipmentService(storage).save_snapshots((initial,))
    save(storage, row(version_raw="v2", line="14L"),
         row(2, pm_name="PM2", model_name="M-O3", family_name="O3", version_raw="o3-v2", line="14L"),
         request_id="full-pms", sheet="PM 전체 현황")
    save(storage, row(line="15L"), request_id="partial-after-full", sheet="다시 부분 현황")
    stored = snapshot(storage)
    assert stored.model_name == "M-O2/O3 혼합" and stored.line == "15L"
    assert stored.source_reference["model_name"]["batch_id"] == batch["batch_id"]
    assert original(storage, stored, "model_name") == ["M-O2/O3 혼합"]


def test_source_batch_overrides_merge_only_within_the_same_batch_and_are_equipment_only():
    first = {"model_name": {"sheet": "S", "cells": ["A1"], "batch_id": 1}}
    second = {"model_name": {"sheet": "S", "cells": ["B1"], "batch_id": 2}}
    merged = merge_sources(first, second, allow_batch=True)
    assert merged["model_name"] == [first["model_name"], second["model_name"]]
    assert first["model_name"]["cells"] == ["A1"]
    with pytest.raises(StorageValidationError):
        merge_sources(first)


@pytest.mark.parametrize("bad_id", [None, False, 0, -1, "1", 4294967296])
def test_source_batch_overrides_need_a_valid_positive_id(bad_id):
    with pytest.raises(StorageValidationError):
        merge_sources({"model_name": {"sheet": "S", "cells": ["A1"], "batch_id": bad_id}}, allow_batch=True)


@pytest.mark.parametrize("wrong_batch", ["missing", "manual"])
def test_equipment_sources_cannot_point_to_missing_or_non_excel_batches(storage, wrong_batch):
    save(storage, row())
    before = snapshot(storage)
    batch_id = 999999
    if wrong_batch == "manual":
        with storage.transaction() as tx:
            batch_id = tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {}})["batch_id"]
    sources = deepcopy(before.source_reference)
    sources["model_name"]["batch_id"] = batch_id
    invalid = replace(before, base_date=date(2026, 11, 3), source_reference=sources)
    with pytest.raises((LookupError, StorageValidationError)):
        EquipmentService(storage).save_snapshots((invalid,))
    assert snapshot(storage, base="2026-11-03") is None
    assert snapshot(storage) == before


def test_general_equipment_cannot_reassign_an_unimported_family_to_a_new_model(storage):
    save(storage, row(pm_name=None), row(2, pm_name=None, family_name="O2 보조", version_raw="aux1"))
    before = snapshot(storage)
    batches = count(storage, "import_batch")
    with pytest.raises(StorageValidationError, match="기존 모든 계열을 함께 검토"):
        save(storage, row(pm_name=None, model_name="M-O3", family_name="O3", version_raw="o3-v1"),
             request_id="new-general-model")
    assert snapshot(storage) == before
    assert count(storage, "import_batch") == batches


def test_general_equipment_with_the_same_model_can_update_one_family_and_keep_the_other(storage):
    save(storage, row(pm_name=None), row(2, pm_name=None, family_name="O2 보조", version_raw="aux1"))
    before = snapshot(storage)
    untouched = next(sw for sw in before.software if sw.family_id == 3)
    save(storage, row(pm_name=None, version_raw="v2"), request_id="same-general-model")
    after = snapshot(storage)
    assert after.group_id == before.group_id == 1
    assert after.model_name == before.model_name == "M-O2"
    assert next(sw for sw in after.software if sw.family_id == 3) == untouched
