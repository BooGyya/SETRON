"""관리 화면의 직접 수정도 날짜별 HPA 분류 충돌 시 전체 묶음을 취소한다."""

from dataclasses import replace
from datetime import date

import pytest

from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.management import ManagementService, RecordEdit
from setron.features.storage.validation import TABLE_NAMES, StorageValidationError
from setron.ui.database_source import load_dashboard_source
from setron.ui.query_state import QueryState

from test_release_revision import all_rows, revision_storage  # noqa: F401


FIRST, LAST = date(2026, 11, 1), date(2026, 11, 3)
CODE = "TEST-HPA-MANAGEMENT"


def source(field, address):
    return {field: {"sheet": "현황", "cells": [address]}}


def seed_history(storage, *, hpa=True, pms=True, shared=False):
    """실제 저장 서비스로 원본·선택 공란을 보존한 두 날짜의 동일 분류를 준비한다."""
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 1}, {"group_name": "M-O2"})
        tx.insert("version_family", {"family_id": 3, "group_id": 4, "family_name": "HPA", "ui_type": None})
    model_id, family_id, model, raw = (4, 3, "HPA", "vHPA.2_d5 ") if hpa else (1, 1, "M-O2", "v1 ")
    snapshots = []
    for day in (FIRST, LAST):
        with storage.transaction() as tx:
            batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": {"현황": {
                "A1": CODE, "B1": model, "C1": day.isoformat(), "D1": "화성", "E1": raw,
                "F1": "PM1", "F2": "PM2", "G1": None, "H1": "원본 라인",
            }}})
        positions = tuple(PmInput(pm_name=f"PM{number}", group_id=model_id, model_name_raw=model,
                                   source_reference=source("pm_name", f"F{number}"))
                          for number in ((1, 2) if shared else (1,))) if pms else ()
        snapshots.append(SnapshotInput(equipment_code=CODE, base_date=day, site="화성", line="원본 라인",
            model_name=model, group_id=model_id, production_area=None, batch_id=batch["batch_id"],
            source_reference={**source("equipment_code", "A1"), **source("model_name", "B1"),
                              **source("base_date", "C1"), **source("site", "D1"),
                              **source("production_area", "G1"), **source("line", "H1")},
            software=(SoftwareInput(family_id=family_id, version_raw=raw,
                                    source_reference=source("version_raw", "E1"), pms=positions),)))
    return EquipmentService(storage).save_snapshots(tuple(snapshots))


def edit(service, table, key, changes):
    record = service.get_record(table, key)
    return RecordEdit(table=table, key=key, revision=record.revision, changes=changes)


def classification_edit(service, snapshot, *, hpa):
    pm = snapshot.software[0].pms[0]
    return edit(service, "equipment_pm", {"pm_id": pm.pm_id}, {
        "group_id": 4 if hpa else 1, "model_name_raw": "HPA" if hpa else "M-O2",
        "family_id": 3 if hpa else 1, "release_version_id": None,
    })


def stored_rows(storage):
    return {table: all_rows(storage, table) for table in TABLE_NAMES}


@pytest.mark.parametrize("hpa", [True, False], ids=["HPA에서 일반", "일반에서 HPA"])
def test_pm_classification_conflict_rolls_back_other_edits_and_audit(revision_storage, hpa):
    storage = revision_storage
    first, last = seed_history(storage, hpa=hpa)
    other, = EquipmentService(storage).save_snapshots((replace(first, equipment_code="TEST-UNRELATED"),))
    service = ManagementService(storage)
    before = stored_rows(storage)
    edits = (edit(service, "equipment_snapshot", {"snapshot_id": other.snapshot_id}, {"line": "검토한 새 라인"}),
             classification_edit(service, last, hpa=not hpa))

    with pytest.raises(StorageValidationError) as failure:
        service.save(edits)

    assert CODE in str(failure.value) and FIRST.isoformat() in str(failure.value)
    assert LAST.isoformat() in str(failure.value)
    assert "HPA" in str(failure.value) and "일반 설비" in str(failure.value)
    assert "전체" in str(failure.value)
    assert stored_rows(storage) == before


def test_non_pm_classification_change_is_also_checked_after_all_edits(revision_storage):
    storage = revision_storage
    _, last = seed_history(storage, pms=False)
    service = ManagementService(storage)
    before = stored_rows(storage)
    edits = (edit(service, "equipment_snapshot", {"snapshot_id": last.snapshot_id},
                  {"group_id": 1, "model_name": "M-O2"}),
             edit(service, "equipment_version", {"equipment_version_id": last.software[0].equipment_version_id},
                  {"family_id": 1}))
    with pytest.raises(StorageValidationError, match="날짜별 분류"):
        service.save(edits)
    assert stored_rows(storage) == before


@pytest.mark.parametrize("reverse", [False, True], ids=["과거부터 수정", "최신부터 수정"])
def test_explicit_corrections_to_all_dates_use_final_consistent_state(revision_storage, reverse):
    storage = revision_storage
    snapshots = seed_history(storage)
    service = ManagementService(storage)
    originals = all_rows(storage, "import_batch")
    edits = tuple(value for snapshot in (reversed(snapshots) if reverse else snapshots) for value in (
        classification_edit(service, snapshot, hpa=False),
        edit(service, "equipment_snapshot", {"snapshot_id": snapshot.snapshot_id}, {"model_name": "M-O2"})))

    result = service.save(edits)

    assert result.audit_batch_id is not None
    assert all_rows(storage, "import_batch")[:len(originals)] == originals
    for day in (FIRST, LAST):
        loaded = EquipmentService(storage).get_snapshot(CODE, day)
        assert loaded.group_id == 1 and loaded.model_name == "M-O2"
        assert loaded.software[0].family_id == 1 and loaded.software[0].version_raw == "vHPA.2_d5 "
        assert loaded.production_area is None
    dashboard = load_dashboard_source(storage)
    assert dashboard.snapshot(QueryState(dashboard.catalog).apply()).summary.equipment_count == 1


def test_shared_hpa_sw_edit_preserves_other_pm_raw_sources_and_optional_blank(revision_storage):
    storage = revision_storage
    _, last = seed_history(storage, shared=True)
    service = ManagementService(storage)
    originals = all_rows(storage, "import_batch")
    pm_id = last.software[0].pms[0].pm_id
    service.save((edit(service, "equipment_pm", {"pm_id": pm_id}, {"version_raw": "vHPA.NEW_d9 "}),))

    loaded = EquipmentService(storage).get_snapshot(CODE, LAST)
    versions = {pm.pm_name: sw.version_raw for sw in loaded.software for pm in sw.pms}
    assert versions == {"PM1": "vHPA.NEW_d9 ", "PM2": "vHPA.2_d5 "}
    assert loaded.production_area is None
    assert all_rows(storage, "import_batch")[:len(originals)] == originals
    assert EquipmentService(storage).get_snapshot(CODE, FIRST).software[0].version_raw == "vHPA.2_d5 "


def test_same_day_hpa_and_general_pm_mix_still_rolls_back(revision_storage):
    storage = revision_storage
    _, last = seed_history(storage, shared=True)
    before = stored_rows(storage)
    service = ManagementService(storage)
    with pytest.raises(StorageValidationError, match="대표 모델과 PM별 모델|한 설비"):
        service.save((classification_edit(service, last, hpa=False),))
    assert stored_rows(storage) == before


def test_existing_mixed_history_can_be_explicitly_corrected_without_changing_other_dates(revision_storage):
    storage = revision_storage
    first, last = seed_history(storage)
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": last.snapshot_id}, {"group_id": 1, "model_name": "M-O2"})
        tx.update("equipment_pm", {"pm_id": last.software[0].pms[0].pm_id}, {"group_id": 1, "model_name_raw": "M-O2"})
        tx.update("equipment_version", {"equipment_version_id": last.software[0].equipment_version_id}, {"family_id": 1})
    last_before = EquipmentService(storage).get_snapshot(CODE, LAST)
    originals = all_rows(storage, "import_batch")
    service = ManagementService(storage)
    service.save((classification_edit(service, first, hpa=False),
                  edit(service, "equipment_snapshot", {"snapshot_id": first.snapshot_id}, {"model_name": "M-O2"})))

    assert EquipmentService(storage).get_snapshot(CODE, LAST) == last_before
    assert all_rows(storage, "import_batch")[:len(originals)] == originals
    dashboard = load_dashboard_source(storage)
    assert dashboard.snapshot(QueryState(dashboard.catalog).apply()).summary.equipment_count == 1
