"""명시적인 삭제·복원은 새 로컬 PK에도 원래 교환 UUID와 계보를 보존한다."""

from copy import deepcopy
from datetime import date
from uuid import UUID

import pytest

from setron.features.data_admin.service import DataAdminService
from setron.features.peer_sync import identity_index, key_token, read_state
from setron.features.peer_sync import service as sync
from setron.features.storage.service import StorageService
from setron.infrastructure.database import Database

from test_peer_exchange import PEERS, choose_all, exchange_file, peers, stamp  # noqa: F401
from test_sql_import_service import bundle, current


@pytest.fixture(autouse=True)
def isolated_peer_id(monkeypatch):
    monkeypatch.setenv("SETRON_PEER_ID", PEERS[0])


def admin(peer):
    return DataAdminService(StorageService(Database(peer[0])))


def two_snapshots():
    rows = bundle()
    rows["equipment"].append((2, b"EQ-2"))
    rows["equipment_snapshot"].append(
        (2, 2, date(2026, 11, 1), "화성", "다른 라인", "O2", 1, None, None, None, None, 1, None))
    rows["equipment_version"].append((2, 2, 1, b"v1", None, 1, None))
    rows["equipment_pm"].append((2, 2, 2, b"PM1", 1, "O2", 1, None))
    return rows


@pytest.mark.parametrize("new_ids", [False, True])
@pytest.mark.parametrize("export_deleted", [False, True])
def test_rows_restore_original_uuid_and_deleted_ancestry(peers, tmp_path, new_ids, export_deleted):
    source = peers("source", initial=two_snapshots() if new_ids else bundle())
    destination = peers("destination")
    original = stamp(source, PEERS[0])
    initial_path = exchange_file(source, tmp_path / "initial.sql")
    destination[2].apply(choose_all(destination[2], destination[2].preview(initial_path, "review")))
    identifiers = {name: identity_index(current(source))[name, key_token({key: 1})]
                   for name, key in (("equipment_snapshot", "snapshot_id"),
                                     ("equipment_version", "equipment_version_id"), ("equipment_pm", "pm_id"))}
    management = admin(source)
    journal = management.delete_rows(1, [1])
    deleted = stamp(source, PEERS[0]) if export_deleted else original
    assert management.restore_rows(journal) == 1
    restored = stamp(source, PEERS[0])
    records = current(source)
    snapshot = next(row for row in records["equipment_snapshot"] if row["equipment_id"] == 1)
    assert (snapshot["snapshot_id"] > 1) == new_ids
    for name, field in (("equipment_snapshot", "snapshot_id"),
                        ("equipment_version", "equipment_version_id"), ("equipment_pm", "pm_id")):
        row = next(row for row in records[name] if row["snapshot_id"] == snapshot["snapshot_id"])
        identifier = identifiers[name]
        assert identity_index(records)[name, key_token({field: row[field]})] == identifier
        if export_deleted:
            deleted_head = deleted.entities[identifier]["head"]
            assert deleted.events[deleted_head]["after"] is None
            assert deleted_head in restored.events[restored.entities[identifier]["head"]]["parents"]
    path = exchange_file(source, tmp_path / "restored.sql")
    plan = destination[2].preview(path, "review")
    destination[2].apply(choose_all(destination[2], plan) if plan.unresolved else plan)
    assert len(current(destination)["equipment_snapshot"]) == (2 if new_ids else 1)
    assert len(current(destination)["equipment_pm"]) == (2 if new_ids else 1)


def test_failed_restore_rolls_back_rows_journal_and_uuid_binding(peers, monkeypatch):
    source = peers("source", initial=bundle())
    stamp(source, PEERS[0])
    management = admin(source)
    journal = management.delete_rows(1, [1])
    stamp(source, PEERS[0])
    before = deepcopy(current(source))

    def fail(*args, **kwargs):
        raise RuntimeError("UUID 기록 저장 실패")

    monkeypatch.setattr(sync, "_persist", fail)
    with pytest.raises(RuntimeError, match="UUID 기록 저장 실패"):
        management.restore_rows(journal)
    assert current(source) == before


def test_legacy_unstamped_restore_does_not_add_peer_metadata(peers):
    source = peers("source", initial=bundle())
    management = admin(source)
    journal = management.delete_rows(1, [1])
    archived = next(row for row in current(source)["import_batch"] if row["batch_id"] == journal)
    assert "_peer_uuid" not in archived["raw_data"]
    assert management.restore_rows(journal) == 1
    assert not read_state(current(source)).entities
    assert not current(source)["app_setting"]


def test_release_item_and_link_restore_original_uuid(peers):
    rows = bundle()
    rows["release_version"] = [(1, None, 1, b"v1", None, None, 1, "VERIFIED", 1, None, "[]")]
    rows["item"] = [(1, None, 1, "복원할 Item", 1, None, "[]", None)]
    rows["release_version_item"] = [(1, 1, "APPLIED", "ALL", None)]
    source = peers("source", initial=rows)
    original = stamp(source, PEERS[0])
    tracked = {identifier for identifier, entity in original.entities.items()
               if entity["table"] in {"release_version", "item", "release_version_item"}}
    management = admin(source)
    with management.storage.transaction() as tx:
        journal = management._delete_release_in(tx, 1)
    deleted = stamp(source, PEERS[0])
    with management.storage.transaction() as tx:
        management._restore_release_in(tx, journal)
    restored = stamp(source, PEERS[0])
    for identifier in tracked:
        assert not restored.entities[identifier]["deleted"]
        assert deleted.entities[identifier]["head"] in restored.events[restored.entities[identifier]["head"]]["parents"]


@pytest.mark.parametrize("hint_first", [True, False])
def test_restore_hint_never_takes_another_live_alias_binding(peers, hint_first):
    source = peers("source", initial=two_snapshots())
    repository = admin(source).storage.repository
    stale_id, live_id = (str(UUID(int=value)) for value in (501, 502))
    stale = {"uuid": stale_id, "table": "equipment_snapshot", "key": {"snapshot_id": 9},
             "aliases": [live_id], "deleted": True}
    live = {"uuid": live_id, "table": "equipment_snapshot", "key": {"snapshot_id": 2},
            "aliases": [stale_id], "deleted": False}
    ordered = (stale, live) if hint_first else (live, stale)
    state = sync.SyncState(entities={entity["uuid"]: deepcopy(entity) for entity in ordered})
    current_rows = {("equipment_snapshot", key_token({"snapshot_id": number})): {} for number in (1, 2)}
    index = {(entity["table"], key_token(entity["key"])): entity for entity in state.entities.values()}
    result = sync._restore_bindings(state, current_rows, index, [
        {"table": "equipment_snapshot", "key": {"snapshot_id": 1}, "uuid": stale_id}], repository)
    assert not result
    assert state.entities[live_id]["key"] == {"snapshot_id": 2}
    assert state.entities[stale_id]["key"] == {"snapshot_id": 9}


def test_repeated_reconcile_preserves_live_identity_when_legacy_tombstone_reuses_pk(peers, monkeypatch):
    """옛 파일의 tombstone과 현재 행이 같은 숫자 PK여도 정렬 순서로 새 UUID를 만들지 않는다."""
    source = peers("source", initial=bundle())
    stamp(source, PEERS[0])
    records = current(source)
    with source[0].begin() as connection:
        for name in ("equipment_pm", "equipment_version", "equipment_snapshot"):
            connection.execute(source[1][name].delete())
    stamp(source, PEERS[0])
    with source[0].begin() as connection:
        for name in ("equipment_snapshot", "equipment_version", "equipment_pm"):
            connection.execute(source[1][name].insert(), records[name])
    # 기존 무작위 UUID보다 앞에 정렬되는 새 UUID로 실패 조건을 고정한다.
    identifiers = iter(UUID(int=value) for value in range(1000, 1100))
    monkeypatch.setattr(sync, "uuid4", lambda: next(identifiers))
    stamp(source, PEERS[0])
    before = deepcopy(current(source))
    stamp(source, PEERS[0])
    assert current(source) == before


def test_reconcile_rejects_multiple_live_uuids_at_one_local_address(peers):
    source = peers("source", initial=bundle())
    state = stamp(source, PEERS[0])
    original = next(entity for entity in state.entities.values() if entity["table"] == "equipment_snapshot")
    duplicate = deepcopy(original)
    duplicate["uuid"], duplicate["head"] = str(UUID(int=6001)), str(UUID(int=6002))
    event = deepcopy(state.events[original["head"]])
    event["uuid"], event["entity"] = duplicate["head"], duplicate["uuid"]
    with source[0].begin() as connection:
        connection.execute(source[1]["app_setting"].insert(), [
            {"setting_key": sync.PREFIX + kind.encode() + b":" + value["uuid"].encode(),
             "setting_value": sync._json(value)} for kind, value in (("entity", duplicate), ("event", event))])
    before = deepcopy(current(source))
    with pytest.raises(sync.PeerSyncError, match="중복 참조"):
        stamp(source, PEERS[0])
    assert current(source) == before
