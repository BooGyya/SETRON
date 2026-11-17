"""PC별 ID가 겹치는 SQL 병합 뒤 삭제 표시·복원이 원래 업무 대상을 유지한다."""

import json
from copy import deepcopy
from datetime import date, datetime

import pytest

from setron.features.data_admin.service import DataAdminService, _plain
from setron.features.db_import.dump import allowed_columns
from setron.features.storage.service import StorageService
from setron.features.storage.validation import StorageValidationError, decode_record
from setron.infrastructure.database import Database

from test_sql_import_service import backup, bundle, current, seed, target  # noqa: F401


def _record(table, row):
    columns = next(columns for columns in allowed_columns(table) if len(columns) == len(row))
    return _plain(decode_record(table, dict(zip(columns, row, strict=True))))


def _raw(code, workbook):
    return {"S": {"A2": code, "B2": "v1"}, "[파일]": workbook}


def _destination(target, *, extra_batch=False, releases=False):
    rows = bundle(code=b"TARGET-EQ")
    rows["import_batch"] = [(1, "EXCEL", datetime(2026, 11, 1, 9), json.dumps(_raw("TARGET-EQ", "target.xlsx")))]
    if extra_batch:
        rows["import_batch"].append((2, "MANUAL", datetime(2026, 11, 1, 10), None))
    if releases:
        rows["release_version"] = [(1, None, 1, b"target-release", None, None, 1, "UNKNOWN", 1, None, "[]")]
        rows["item"] = [(1, None, 1, "target item", 1, None, "[]")]
    seed(target, rows)
    return DataAdminService(StorageService(Database(target[0])))


def _deleted_equipment(*, restored=False, missing_equipment=False, missing_release=False):
    rows = bundle(code=b"SOURCE-EQ", group="O3", day=2)
    snapshot = _record("equipment_snapshot", rows.pop("equipment_snapshot")[0])
    snapshot["base_date"] = "2026-11-02"
    snapshot["source_reference"] = {"equipment_code": {"sheet": "S", "cells": ["A2"], "batch_id": 1}}
    version = _record("equipment_version", rows.pop("equipment_version")[0])
    version["source_reference"] = {"version_raw": {"sheet": "S", "cells": ["B2"], "batch_id": 1}}
    if missing_release:
        version["release_version_id"] = 1
    pm = _record("equipment_pm", rows.pop("equipment_pm")[0])
    if missing_equipment:
        rows.pop("equipment")
    removed = {"table": "equipment_snapshot", "batch_id": 1, "sheet": "S", "rows": [{
        "snapshot_id": 1, "equipment_code": "SOURCE-EQ", "base_date": "2026-11-02", "site": "화성",
        "line": "원본 라인", "versions": ["v1"], "cells": ["A2", "B2"],
        "data": {"snapshot": snapshot, "versions": [version], "pms": [pm]},
    }]}
    rows["import_batch"] = [
        (1, "EXCEL", datetime(2026, 11, 2, 9), json.dumps(_raw("SOURCE-EQ", "source.xlsx"))),
        (2, "MANUAL", datetime(2026, 11, 2, 10), json.dumps({"schema": 1, "management_delete": removed})),
    ]
    if restored:
        rows["import_batch"].append((3, "MANUAL", datetime(2026, 11, 2, 11), json.dumps({
            "schema": 1, "management_restore": {"batch_id": 2, "source": 1, "sheet": "S", "codes": ["SOURCE-EQ"]},
        })))
    return rows


def _journal(target, name):
    return next((batch, json.loads(batch["raw_data"])[name]) for batch in current(target)["import_batch"]
                if name in json.loads(batch["raw_data"] or "{}"))


@pytest.mark.parametrize("mode", ["add", "overwrite"])
def test_sql_deleted_equipment_restores_into_its_remapped_equipment_and_source(target, tmp_path, mode):
    admin = _destination(target)
    service = target[2]
    path = backup(tmp_path, _deleted_equipment())
    service.apply(service.preview(path, mode))
    assert service.preview(path, mode).changes == 0
    journal, deleted = _journal(target, "management_delete")
    source_batch = deleted["batch_id"]
    assert source_batch != 1
    assert not admin.sheet_grid(1).deleted
    assert admin.sheet_grid(source_batch).deleted == {"A2": "삭제됨 · SOURCE-EQ", "B2": "삭제됨 · SOURCE-EQ"}
    history = next(entry for entry in admin.history() if entry.kind == "삭제")
    assert history.workbook == "source.xlsx" and history.target == "SOURCE-EQ"

    assert admin.restore_rows(journal["batch_id"]) == 1

    after = current(target)
    equipment = next(row for row in after["equipment"] if row["equipment_code"] == b"SOURCE-EQ")
    snapshot = next(row for row in after["equipment_snapshot"] if row["base_date"] == date(2026, 11, 2))
    version = next(row for row in after["equipment_version"] if row["snapshot_id"] == snapshot["snapshot_id"])
    pm = next(row for row in after["equipment_pm"] if row["snapshot_id"] == snapshot["snapshot_id"])
    assert equipment["equipment_id"] != 1 and snapshot["equipment_id"] == equipment["equipment_id"]
    assert snapshot["batch_id"] == version["batch_id"] == pm["batch_id"] == source_batch
    assert version["equipment_version_id"] != 1 and pm["equipment_version_id"] == version["equipment_version_id"]
    assert json.loads(version["source_reference"])["version_raw"]["batch_id"] == source_batch
    assert not admin.sheet_grid(source_batch).deleted
    with pytest.raises(StorageValidationError, match="이미 되살린"):
        admin.restore_rows(journal["batch_id"])
    assert service.preview(path, mode).changes == 0


def test_sql_restore_journal_remaps_parent_and_prevents_a_second_restore(target, tmp_path):
    admin = _destination(target, extra_batch=True)
    service = target[2]
    path = backup(tmp_path, _deleted_equipment(restored=True))
    service.apply(service.preview(path, "add"))
    journal, deleted = _journal(target, "management_delete")
    _, restored = _journal(target, "management_restore")

    assert journal["batch_id"] != 2
    assert restored["batch_id"] == journal["batch_id"] and restored["source"] == deleted["batch_id"]
    assert not admin.sheet_grid(deleted["batch_id"]).deleted
    with pytest.raises(StorageValidationError, match="이미 되살린"):
        admin.restore_rows(journal["batch_id"])
    assert service.preview(path, "add").changes == 0


@pytest.mark.parametrize("missing", ["equipment", "release"])
def test_sql_missing_deleted_equipment_reference_is_archived_and_cannot_restore_over_another_pc(target, tmp_path, missing):
    admin = _destination(target, releases=True)
    service = target[2]
    path = backup(tmp_path, _deleted_equipment(missing_equipment=missing == "equipment", missing_release=missing == "release"))
    service.apply(service.preview(path, "add"))
    journal, deleted = _journal(target, "management_delete")
    data = deleted["rows"][0]["data"]
    assert (data["snapshot"]["equipment_id"] if missing == "equipment"
            else data["versions"][0]["release_version_id"]) == "원본 ID 1"
    before = current(target)

    with pytest.raises(StorageValidationError, match="삭제 당시 연결 자료를 찾을 수 없어 복원"):
        admin.restore_rows(journal["batch_id"])

    assert current(target) == before


def _deleted_release(*, item_only=False, restored=False, missing_kept_item=False, missing_note=False):
    rows = _deleted_equipment()
    rows["import_batch"] = rows["import_batch"][:1]
    live_release = (2, None, 1, b"source-live", None, None, 2, "UNKNOWN", 1, None, "[]")
    deleted_release = (1, None, 1, b"source-deleted", None, None, 1, "UNKNOWN", 1, None, "[]")
    shared = (1, None, 1, "source shared item", 1, None, "[]")
    removed_item = (2, None, 2, "source removed item", 1, None, "[]")
    rows["release_version"] = [live_release]
    if not missing_kept_item:
        rows["item"] = [shared]
        rows["release_version_item"] = [(2, 1, "APPLIED", "ALL", None)]
    release_id = 2 if item_only else 1
    entries = [{"item_id": 1, "kept": True, "item": _record("item", shared),
                "link": {"release_version_id": release_id, "item_id": 1, "item_state": "APPLIED",
                         "target_scope": "LISTED", "target_group_ids": [1]}}]
    if not item_only:
        entries.append({"item_id": 2, "kept": False, "item": _record("item", removed_item),
                        "link": {"release_version_id": 1, "item_id": 2, "item_state": "NOT_APPLIED",
                                 "target_scope": "ALL", "target_group_ids": None}})
    else:
        # Item만 삭제하면 같은 버전에는 이 Item 연결이 없어야 한다.
        rows.pop("release_version_item", None)
    deleted = {"release_version_id": release_id, "family_id": 1,
               "version_raw": "source-live" if item_only else "source-deleted", "item_id": 1 if item_only else None,
               "items": entries, "label": "source deletion",
               "release": None if item_only else _record("release_version", deleted_release)}
    if missing_note:
        deleted["release"]["release_note_id"] = 1
    rows["import_batch"].append((2, "MANUAL", datetime(2026, 11, 2, 10), json.dumps({
        "schema": 1, "management_delete_release": deleted,
    })))
    if restored:
        rows["import_batch"].append((3, "MANUAL", datetime(2026, 11, 2, 11), json.dumps({
            "schema": 1, "management_restore_release": {"batch_id": 2, "release_version_id": 2, "label": "restored"},
        })))
    return rows


@pytest.mark.parametrize("item_only", [False, True])
def test_sql_release_restore_remaps_live_parents_kept_items_and_deleted_children(target, tmp_path, item_only):
    admin = _destination(target, releases=True)
    service = target[2]
    path = backup(tmp_path, _deleted_release(item_only=item_only))
    service.apply(service.preview(path, "add"))
    assert service.preview(path, "add").changes == 0
    journal, deleted = _journal(target, "management_delete_release")
    family = next(row for row in current(target)["version_family"] if row["family_name"] == "O3")
    assert family["family_id"] != 1 and deleted["family_id"] == family["family_id"]
    with admin.storage.transaction() as tx:
        release_id = admin._restore_release_in(tx, journal["batch_id"])

    after = current(target)
    release = next(row for row in after["release_version"] if row["release_version_id"] == release_id)
    assert release["family_id"] == family["family_id"] and release_id != 1
    links = [row for row in after["release_version_item"] if row["release_version_id"] == release_id]
    assert len(links) == (1 if item_only else 2)
    linked = {next(item["content"] for item in after["item"] if item["item_id"] == link["item_id"]): link
              for link in links}
    assert "target item" not in linked
    assert linked["source shared item"]["item_id"] != 1
    assert json.loads(linked["source shared item"]["target_group_ids"]) == [family["group_id"]]
    if not item_only:
        assert linked["source removed item"]["item_state"] == "NOT_APPLIED"
    with pytest.raises(StorageValidationError, match="이미 되살린"):
        with admin.storage.transaction() as tx:
            admin._restore_release_in(tx, journal["batch_id"])


def test_sql_release_restore_marker_remaps_its_deleted_journal(target, tmp_path):
    admin = _destination(target, extra_batch=True, releases=True)
    service = target[2]
    path = backup(tmp_path, _deleted_release(restored=True))
    service.apply(service.preview(path, "add"))
    journal, _ = _journal(target, "management_delete_release")
    _, restored = _journal(target, "management_restore_release")
    assert journal["batch_id"] != 2 and restored["batch_id"] == journal["batch_id"]
    assert restored["release_version_id"] != 1
    with pytest.raises(StorageValidationError, match="이미 되살린"):
        with admin.storage.transaction() as tx:
            admin._restore_release_in(tx, journal["batch_id"])
    assert service.preview(path, "add").changes == 0


@pytest.mark.parametrize("missing", ["item", "note"])
def test_sql_missing_release_reference_is_not_reconnected_to_an_unrelated_record(target, tmp_path, missing):
    admin = _destination(target, releases=True)
    service = target[2]
    service.apply(service.preview(backup(tmp_path, _deleted_release(missing_kept_item=missing == "item", missing_note=missing == "note")), "add"))
    journal, deleted = _journal(target, "management_delete_release")
    assert (deleted["items"][0]["item_id"] if missing == "item"
            else deleted["release"]["release_note_id"]) == "원본 ID 1"
    before = deepcopy(current(target))
    with pytest.raises(StorageValidationError, match="삭제 당시 연결 자료를 찾을 수 없어 복원"):
        with admin.storage.transaction() as tx:
            admin._restore_release_in(tx, journal["batch_id"])
    assert current(target) == before
