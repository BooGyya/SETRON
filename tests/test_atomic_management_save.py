"""한 번의 화면 저장·되돌리기에서 일부 변경만 남지 않는지 실제 트랜잭션으로 확인한다."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from setron.features import production_area, reference_data
from setron.features.data_admin import DataAdminService
from setron.features.equipment.repository import EquipmentRepository
from setron.features.management import ManagementService, RecordEdit
from setron.features.storage.validation import StorageValidationError, TABLE_NAMES
from setron.infrastructure.database import DatabaseCommitUnknownError
from test_release_revision import revision_storage  # noqa: F401


@pytest.fixture
def atomic_storage(revision_storage, monkeypatch):
    monkeypatch.setattr(reference_data, "REGISTERED_AREAS", dict(reference_data.REGISTERED_AREAS))
    monkeypatch.setattr(production_area, "_managed_labels", dict(production_area._managed_labels))
    return revision_storage


def all_data(storage):
    with storage.transaction() as tx:
        return {name: EquipmentRepository().rows(tx, name) for name in TABLE_NAMES}


def edit(storage, table, key, changes):
    view = ManagementService(storage).get_record(table, key)
    return RecordEdit(table, view.key, view.revision, changes)


def sheet(storage):
    token = uuid4().hex[:9]
    admin = DataAdminService(storage)
    group = admin.create_group("원자 기본 " + token, "OXIDE")
    site = "원자 Site " + token
    admin.add_site(site)
    admin.add_line(site, "L0", "MEMORY")
    batch = admin.create_sheet(site, date(2026, 11, 16), "MEMORY")
    sid = admin.add_row(batch, {"equipment_code": "EXISTING-" + token, "group_name": group.name,
                               "line": "L0", "version_raw": "v1"}, None)
    row = next(row for row in admin.sheet_rows(batch).rows if row.snapshot_id == sid)
    request = edit(storage, "equipment_version", {"equipment_version_id": row.version_id}, {"version_raw": "v2"})
    creates = (("원자 새 모델 " + token, "POLY"),)
    adds = (({"equipment_code": "NEW-" + token, "group_name": creates[0][0],
              "line": "LNEW", "version_raw": "vnew"}, None),)
    return admin, batch, sid, request, creates, adds, site


def test_registration_and_audits_roll_back_on_stale_cell_revision(atomic_storage):
    admin, batch, _, request, creates, adds, _ = sheet(atomic_storage)
    before = all_data(atomic_storage)
    areas = dict(reference_data.REGISTERED_AREAS)
    with pytest.raises(StorageValidationError, match="검토 후"):
        admin.save_sheet(batch, registers=(("LNEW", "MEMORY"),), creates=creates,
                         edits=(replace(request, revision="0" * 64),), adds=adds)
    assert all_data(atomic_storage) == before
    assert reference_data.REGISTERED_AREAS == areas
    result = admin.save_sheet(batch, registers=(("LNEW", "MEMORY"),), creates=creates, edits=(request,), adds=adds)
    assert len(result.records) == 1 and len(result.rows["added"]) == 1


def test_existing_cells_can_be_saved_when_legacy_batch_has_no_original_sheet(atomic_storage):
    admin, batch, _, request, _, _, _ = sheet(atomic_storage)
    with atomic_storage.transaction() as tx:
        tx.update("import_batch", {"batch_id": batch}, {"raw_data": None})
    result = admin.save_sheet(batch, edits=(request,))
    assert result.records[0].current["version_raw"] == "v2"


def test_row_failure_rolls_back_registrations_edits_rows_and_audits_then_retries(atomic_storage, monkeypatch):
    admin, batch, _, request, creates, adds, site = sheet(atomic_storage)
    before = all_data(atomic_storage)
    areas = dict(reference_data.REGISTERED_AREAS)
    original = admin._apply_rows_in

    def fail_after_rows(tx, *args):
        assert tx.get("equipment_version", request.key)["version_raw"] == "v2"
        assert (site, "LNEW") not in reference_data.REGISTERED_AREAS
        original(tx, *args)
        raise RuntimeError("failure after row writes")

    monkeypatch.setattr(admin, "_apply_rows_in", fail_after_rows)
    with pytest.raises(RuntimeError, match="after row"):
        admin.save_sheet(batch, registers=(("LNEW", "MEMORY"),), creates=creates, edits=(request,), adds=adds)
    assert all_data(atomic_storage) == before
    assert reference_data.REGISTERED_AREAS == areas
    monkeypatch.setattr(admin, "_apply_rows_in", original)
    result = admin.save_sheet(batch, registers=(("LNEW", "MEMORY"),), creates=creates, edits=(request,), adds=adds)
    assert reference_data.REGISTERED_AREAS[site, "LNEW"] == "MEMORY"
    assert any(record.equipment_code == adds[0][0]["equipment_code"]
               for judgment in result.refresh.dates for record in judgment.records)


def test_later_invalid_row_cancels_earlier_new_row_and_cell_edit(atomic_storage):
    admin, batch, _, request, creates, adds, _ = sheet(atomic_storage)
    before = all_data(atomic_storage)
    invalid = ({**adds[0][0], "equipment_code": "INVALID-" + uuid4().hex[:9], "group_name": "없는 모델"}, None)
    with pytest.raises(StorageValidationError, match="먼저 등록"):
        admin.save_sheet(batch, registers=(("LNEW", "MEMORY"),), creates=creates,
                         edits=(request,), adds=(*adds, invalid))
    assert all_data(atomic_storage) == before


def test_atomic_sheet_save_and_undo_keep_new_pm_rows_on_one_snapshot(atomic_storage):
    admin, batch, _, _, creates, adds, _ = sheet(atomic_storage)
    values = {**adds[0][0], "geometry_type": "5각", "modification_type": "CIP"}
    pm_adds = tuple(({**values, "pm_name": name, "version_raw": version}, None)
                    for name, version in (("PM1", "v1"), ("PM2", "v2")))
    saved = admin.save_sheet(batch, registers=(("LNEW", "MEMORY"),), creates=creates, adds=pm_adds)
    assert len(saved.rows["added"]) == 1
    rows = [row for row in admin.sheet_rows(batch).rows if row.equipment_code == values["equipment_code"]]
    assert sorted((row.pm_names, row.version_raw) for row in rows) == [("PM1", "v1"), ("PM2", "v2")]
    assert {row.snapshot_id for row in rows} == set(saved.rows["added"])
    assert all(row.geometry_type == "5각" and row.modification_type == "CIP" for row in rows)
    admin.undo_sheet(batch, added=saved.rows["added"])
    with atomic_storage.transaction() as tx:
        assert not EquipmentRepository().rows(tx, "equipment_snapshot", snapshot_id=saved.rows["added"][0])
        assert not EquipmentRepository().rows(tx, "equipment_pm", snapshot_id=saved.rows["added"][0])
    assert not any(row.equipment_code == values["equipment_code"] for row in admin.sheet_rows(batch).rows)


def test_atomic_sheet_save_rolls_back_duplicate_pm_and_new_registrations(atomic_storage):
    admin, batch, _, request, creates, adds, _ = sheet(atomic_storage)
    before = all_data(atomic_storage)
    values = {**adds[0][0], "pm_name": "PM1"}
    with pytest.raises(StorageValidationError, match="두 번"):
        admin.save_sheet(batch, registers=(("LNEW", "MEMORY"),), creates=creates, edits=(request,),
                         adds=((values, None), ({**values, "pm_name": "pm1"}, None)))
    assert all_data(atomic_storage) == before


def test_sheet_undo_structure_and_cells_roll_back_together_and_rebase_owned_revisions(atomic_storage, monkeypatch):
    admin, batch, sid, request, creates, adds, _ = sheet(atomic_storage)
    other = admin.create_group("되돌릴 소속 " + uuid4().hex[:9], "OXIDE")
    saved = admin.save_sheet(batch, registers=(("LNEW", "MEMORY"),), creates=creates,
                             edits=(request,), adds=adds, regroup=((sid, other.group_id),))
    restore = edit(atomic_storage, "equipment_version", request.key, {"version_raw": "v1"})
    before_undo = all_data(atomic_storage)
    original = admin._restore_edits_in

    def fail_after_edits(tx, manager, edits):
        original(tx, manager, edits)
        raise RuntimeError("failure after undo edits")

    monkeypatch.setattr(admin, "_restore_edits_in", fail_after_edits)
    arguments = {"added": saved.rows["added"], "journal": saved.rows["journal"],
                 "regroup": saved.rows["regroup"], "edits": (restore,)}
    with pytest.raises(RuntimeError, match="undo edits"):
        admin.undo_sheet(batch, **arguments)
    assert all_data(atomic_storage) == before_undo
    monkeypatch.setattr(admin, "_restore_edits_in", original)
    result = admin.undo_sheet(batch, **arguments)
    assert result.records[0].current["version_raw"] == "v1"
    with atomic_storage.transaction() as tx:
        assert tx.get("equipment_snapshot", {"snapshot_id": sid})["group_id"] == saved.rows["regroup"][0][1]
        assert not EquipmentRepository().rows(tx, "equipment_snapshot", snapshot_id=saved.rows["added"][0])


def releases(storage):
    admin = DataAdminService(storage)
    group = admin.create_group("릴리즈 원자 " + uuid4().hex[:9], "POLY")
    family_id = next(f.family_id for f in admin.release_families() if f.group_id == group.group_id)
    release_id = admin.add_release_version(family_id, "v1")
    item_id = admin.add_release_item(release_id, "before")
    request = edit(storage, "item", {"item_id": item_id}, {"content": "after"})
    plan = {"add_versions": [{"temp": -1, "version_raw": "vnew"}],
            "add_items": [{"temp": -2, "release": -1, "content": "new item"}]}
    return admin, family_id, request, plan


def test_release_save_failure_cancels_cell_edit_new_version_item_and_audits(atomic_storage, monkeypatch):
    admin, family_id, request, plan = releases(atomic_storage)
    before = all_data(atomic_storage)
    original = admin._add_item_in

    def fail_after_item(tx, *args):
        assert tx.get("item", request.key)["content"] == "after"
        original(tx, *args)
        raise RuntimeError("failure after item")

    monkeypatch.setattr(admin, "_add_item_in", fail_after_item)
    with pytest.raises(RuntimeError, match="after item"):
        admin.save_release(family_id, plan, edits=(request,))
    assert all_data(atomic_storage) == before
    monkeypatch.setattr(admin, "_add_item_in", original)
    saved = admin.save_release(family_id, plan, edits=(request,))
    assert saved.records[0].current["content"] == "after"
    assert len(saved.undo["versions"]) == 1


def test_release_undo_failure_cancels_structure_and_cell_restore(atomic_storage, monkeypatch):
    admin, family_id, request, plan = releases(atomic_storage)
    saved = admin.save_release(family_id, plan, edits=(request,))
    restore = edit(atomic_storage, "item", request.key, {"content": "before"})
    before = all_data(atomic_storage)
    original = admin._restore_edits_in

    def fail_after_restore(tx, manager, edits):
        original(tx, manager, edits)
        raise RuntimeError("failure after release undo")

    monkeypatch.setattr(admin, "_restore_edits_in", fail_after_restore)
    with pytest.raises(RuntimeError, match="release undo"):
        admin.undo_release_changes(saved.undo, edits=(restore,))
    assert all_data(atomic_storage) == before
    monkeypatch.setattr(admin, "_restore_edits_in", original)
    restored = admin.undo_release_changes(saved.undo, edits=(restore,))
    assert restored.records[0].current["content"] == "before"
    with atomic_storage.transaction() as tx:
        assert not EquipmentRepository().rows(tx, "release_version", release_version_id=saved.undo["versions"][0])


def test_commit_unknown_is_not_retried_or_published(atomic_storage):
    _, batch, _, request, creates, adds, _ = sheet(atomic_storage)
    before_areas = dict(reference_data.REGISTERED_AREAS)

    class UncertainStorage:
        calls = 0

        @contextmanager
        def transaction(self):
            self.calls += 1
            with atomic_storage.transaction() as tx:
                yield tx
            raise DatabaseCommitUnknownError("commit outcome unknown")

    wrapped = UncertainStorage()
    with pytest.raises(DatabaseCommitUnknownError):
        DataAdminService(wrapped).save_sheet(batch, registers=(("LNEW", "MEMORY"),), creates=creates,
                                            edits=(request,), adds=adds)
    assert wrapped.calls == 1
    assert reference_data.REGISTERED_AREAS == before_areas
    assert sum(row["equipment_code"] == adds[0][0]["equipment_code"]
               for row in all_data(atomic_storage)["equipment"]) == 1
