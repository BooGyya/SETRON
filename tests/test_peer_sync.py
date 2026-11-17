"""동등 교환 UUID·계보가 재전송, 삭제 및 PC별 숫자 ID 차이에도 유지된다."""

import json
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from setron.features.peer_sync import (
    PeerSyncError, canonical_ids, identity_index, key_token, local_peer_id,
    read_rows, read_state, reconcile, validate_exchange,
)
from setron.features.storage.repository import StorageRepository
from setron.infrastructure.database import Database
from test_sql_import_service import bundle, current, seed, target  # noqa: F401

A, B, C = (f"00000000-0000-0000-0000-{number:012d}" for number in (1, 2, 3))


def recorded(target, peer=A, **kwargs):
    with Database(target[0]).transaction() as session:
        repository = StorageRepository(session.connection())
        return reconcile(session, repository, peer_id=peer, **kwargs)


def test_initial_uuid_and_noop_then_edit_and_delete_keep_immutable_parents(target):
    seed(target, bundle())
    first = recorded(target)
    initial = deepcopy(first.events)
    assert first.entities and first.events
    assert recorded(target).events == initial
    equipment = next(entity for entity in first.entities.values() if entity["table"] == "equipment")
    snapshot = next(entity for entity in first.entities.values() if entity["table"] == "equipment_snapshot")
    with target[0].begin() as connection:
        connection.execute(target[1]["equipment_snapshot"].update().values(line="changed"))
    changed = recorded(target)
    updated = changed.entities[snapshot["uuid"]]
    assert changed.events[updated["head"]]["parents"] == [snapshot["head"]]
    assert changed.events[updated["head"]]["after"]["line"] == "changed"
    assert changed.entities[equipment["uuid"]]["head"] == equipment["head"]
    assert all(changed.events[key] == value for key, value in initial.items())
    with target[0].begin() as connection:
        connection.execute(target[1]["equipment_pm"].delete())
        connection.execute(target[1]["equipment_version"].delete())
        connection.execute(target[1]["equipment_snapshot"].delete())
    deleted = recorded(target)
    tombstone = deleted.entities[snapshot["uuid"]]
    assert tombstone["deleted"] and deleted.events[tombstone["head"]]["after"] is None
    assert deleted.events[tombstone["head"]]["operation"] == "delete"
    assert recorded(target).events == deleted.events


def test_peer_id_is_local_to_its_file_and_does_not_follow_database(tmp_path):
    first, second = tmp_path / "pc-a" / "peer-id", tmp_path / "pc-b" / "peer-id"
    identifier = local_peer_id(first)
    assert local_peer_id(first) == identifier
    assert local_peer_id(second) != identifier
    first.write_text("invalid", encoding="ascii")
    with pytest.raises(PeerSyncError, match="식별자"):
        local_peer_id(first)
    assert first.read_text() == "invalid"


def test_legacy_rows_are_allowed_but_modified_exchange_payload_is_rejected(target):
    seed(target, bundle())
    with Database(target[0]).transaction() as session:
        repository = StorageRepository(session.connection())
        assert not validate_exchange(read_rows(session, repository), repository).events
    recorded(target)
    rows = current(target)
    with Database(target[0]).transaction() as session:
        repository = StorageRepository(session.connection())
        validate_exchange(rows, repository)
        rows["equipment_snapshot"][0]["line"] = "forged"
        with pytest.raises(PeerSyncError, match="실제 값"):
            validate_exchange(rows, repository)


def test_event_payload_head_and_parent_tampering_are_rejected(target):
    seed(target, bundle())
    recorded(target)
    original = current(target)
    rows = deepcopy(original)
    entity_row = next(row for row in rows["app_setting"] if b":entity:" in row["setting_key"])
    entity = json.loads(entity_row["setting_value"])
    other = next(value for value in read_state(rows).events.values() if value["entity"] != entity["uuid"])
    entity["head"] = other["uuid"]
    entity_row["setting_value"] = json.dumps(entity)
    with pytest.raises(PeerSyncError):
        read_state(rows)
    rows = deepcopy(original)
    event_row = next(row for row in rows["app_setting"] if b":event:" in row["setting_key"])
    event = json.loads(event_row["setting_value"])
    event["after"]["forged"] = True
    event_row["setting_value"] = json.dumps(event)
    with pytest.raises(PeerSyncError):
        read_state(rows)
    rows = deepcopy(original)
    event_row = next(row for row in rows["app_setting"] if b":event:" in row["setting_key"])
    event = json.loads(event_row["setting_value"])
    parent = next(value for value in read_state(rows).events.values() if value["entity"] != event["entity"])
    event["parents"] = [parent["uuid"]]
    event_row["setting_value"] = json.dumps(event)
    with pytest.raises(PeerSyncError, match="다른 항목"):
        read_state(rows)


def test_reconcile_rolls_back_metadata_with_its_outer_transaction(target):
    seed(target, bundle())
    before = current(target)
    with pytest.raises(RuntimeError):
        with Database(target[0]).transaction() as session:
            repository = StorageRepository(session.connection())
            reconcile(session, repository, peer_id=A)
            raise RuntimeError("cancel")
    assert current(target) == before


def test_alias_union_is_deterministic_and_incoming_heads_are_deduplicated(target):
    seed(target, bundle())
    original = recorded(target, A)
    source = deepcopy(current(target))
    # 같은 자료를 별도로 초기화한 다른 PC의 UUID와 원본 이벤트를 만든다.
    source_state = read_state(source)
    replacement = {identifier: str(uuid4()) for identifier in source_state.entities}
    def replace_refs(value):
        if isinstance(value, dict):
            return {key: replacement.get(item, item) if key == "$ref" and isinstance(item, str)
                    else replace_refs(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace_refs(item) for item in value]
        return value
    source["app_setting"] = []
    import hashlib
    for entity in source_state.entities.values():
        old = entity["uuid"]
        entity["uuid"] = replacement[old]
        event = source_state.events[entity["head"]]
        event["uuid"] = str(uuid4())
        entity["head"] = event["uuid"]
        event["entity"], event["peer"] = entity["uuid"], B
        event["after"] = replace_refs(event["after"])
        event["digest"] = hashlib.sha256(json.dumps(event["after"], ensure_ascii=False, sort_keys=True,
                                                   separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        for kind, value in (("entity", entity), ("event", event)):
            source["app_setting"].append({"setting_key": f"peer_sync.v1:{kind}:{value['uuid']}".encode(),
                                          "setting_value": json.dumps(value)})
    maps = {name: {1: 1} for name in ("import_batch", "equipment_group", "equipment", "version_family",
                                     "equipment_snapshot", "equipment_version", "equipment_pm")}
    merged = recorded(target, C, incoming_rows=source, id_maps=maps)
    repeated = recorded(target, A, incoming_rows=source, id_maps=maps)
    assert repeated.events == merged.events
    left, right = canonical_ids(original, read_state(source)), canonical_ids(read_state(source), original)
    assert left == right
    for entity in merged.entities.values():
        assert set(entity["aliases"]) == {replacement[entity["uuid"]]}
    assert len(identity_index(current(target))) == len(original.entities)


def test_atomic_group_choice_records_one_receipt_even_when_parent_value_is_unchanged(target):
    seed(target, bundle())
    original = recorded(target)
    entity = next(value for value in original.entities.values() if value["table"] == "equipment_snapshot")
    receipt = {"source_group_digest": "reviewed-child-structure", "choice": "CURRENT"}
    choices = {"values": {"snapshot-group": "CURRENT"}, "entities": {entity["uuid"]: receipt}}
    selected = recorded(target, choices=choices)
    assert len(selected.events) == len(original.events) + 1
    event = selected.events[selected.entities[entity["uuid"]]["head"]]
    assert event["parents"] == [entity["head"]] and event["operation"] == "resolve"
    assert event["selection"]["entities"] == {entity["uuid"]: receipt}
    assert recorded(target, choices=choices).events == selected.events


def test_unbound_incoming_tombstone_is_kept_as_archival_history(target):
    seed(target, bundle())
    original = recorded(target)
    source = deepcopy(current(target))
    state = read_state(source)
    source["app_setting"] = []
    identifier, head = str(uuid4()), str(uuid4())
    import hashlib
    event = {"schema": 1, "uuid": head, "peer": B, "entity": identifier, "table": "equipment",
             "parents": [], "operation": "delete", "after": None,
             "digest": hashlib.sha256(b"null").hexdigest(), "recorded_at": "2026-11-17T00:00:00+00:00"}
    entity = {"schema": 1, "uuid": identifier, "table": "equipment", "key": {"equipment_id": 99},
              "aliases": [], "head": head, "deleted": True}
    for kind, value in (("entity", entity), ("event", event)):
        source["app_setting"].append({"setting_key": f"peer_sync.v1:{kind}:{value['uuid']}".encode(),
                                      "setting_value": json.dumps(value)})
    merged = recorded(target, incoming_rows=source)
    assert merged.entities[identifier]["key"] is None
    assert merged.entities[identifier]["deleted"] and merged.events[head] == event
    assert read_state(current(target)).entities[identifier]["key"] is None
    assert recorded(target, incoming_rows=source).events == merged.events


def test_one_canonical_uuid_cannot_bind_two_active_local_rows(target):
    seed(target, bundle())
    recorded(target)
    rows = current(target)
    state = read_state(rows)
    entity = deepcopy(next(value for value in state.entities.values() if value["table"] == "equipment"))
    original = entity["uuid"]
    entity["uuid"], entity["aliases"], entity["key"] = str(uuid4()), [original], {"equipment_id": 999}
    rows["app_setting"].append({"setting_key": f"peer_sync.v1:entity:{entity['uuid']}".encode(),
                                "setting_value": json.dumps(entity)})
    with pytest.raises(PeerSyncError, match="서로 다른 현재 항목"):
        read_state(rows)


def test_long_revision_history_is_iterative_and_cycles_are_rejected():
    """장기간 정상 계보는 파이썬 재귀 제한에 걸리지 않으며 실제 순환만 거부한다."""
    import hashlib
    identifier = str(uuid4())
    rows = {"app_setting": []}
    parent = None
    first = None
    for _ in range(1500):
        head = str(uuid4())
        event = {"schema": 1, "uuid": head, "entity": identifier, "peer": A, "table": "equipment",
                 "parents": [parent] if parent else [], "operation": "delete", "after": None,
                 "digest": hashlib.sha256(b"null").hexdigest(), "recorded_at": "2026-11-17T00:00:00+00:00"}
        row = {"setting_key": f"peer_sync.v1:event:{head}".encode(), "setting_value": json.dumps(event)}
        rows["app_setting"].append(row)
        first = first or row
        parent = head
    entity = {"schema": 1, "uuid": identifier, "table": "equipment", "key": None,
              "aliases": [], "head": parent, "deleted": True}
    rows["app_setting"].append({"setting_key": f"peer_sync.v1:entity:{identifier}".encode(),
                                "setting_value": json.dumps(entity)})
    assert len(read_state(rows).events) == 1500
    event = json.loads(first["setting_value"])
    event["parents"] = [parent]
    first["setting_value"] = json.dumps(event)
    with pytest.raises(PeerSyncError, match="순환"):
        read_state(rows)


def test_remapped_binary_setting_key_keeps_its_entity_uuid(target):
    """FK ID가 들어간 설정 PK가 바뀌어도 명시적 PK 대응으로 같은 항목을 유지한다."""
    rows = bundle()
    original_key, mapped_key = b"version_groups.v1:1:1", b"version_groups.v1:9:9"
    rows["app_setting"] = [(original_key, '{"model_id":1,"family_id":1,"groups":{"N":[]}}')]
    seed(target, rows)
    original = recorded(target)
    source = current(target)
    identifier = identity_index(source)["app_setting", key_token({"setting_key": original_key})]
    with target[0].begin() as connection:
        connection.execute(target[1]["app_setting"].update().where(
            target[1]["app_setting"].c.setting_key == original_key).values(setting_key=mapped_key))
    maps = {name: {1: 1} for name in ("import_batch", "equipment_group", "equipment", "version_family",
                                     "equipment_snapshot", "equipment_version", "equipment_pm")}
    maps["app_setting"] = {original_key: mapped_key}
    merged = recorded(target, incoming_rows=source, id_maps=maps)
    assert merged.entities[identifier]["key"] == {"setting_key": {"$binary": mapped_key.hex()}}
    assert identity_index(current(target))["app_setting", key_token({"setting_key": mapped_key})] == identifier
    assert not merged.entities[identifier]["deleted"]


def test_resolution_event_keeps_only_its_own_field_choices(target):
    """대량 검토 선택 전체를 각 항목마다 복제하지 않고 해당 항목의 선택만 보존한다."""
    seed(target, bundle())
    original = recorded(target)
    entity = next(value for value in original.entities.values() if value["table"] == "equipment_snapshot")
    receipt = {"source_group_digest": "one-group", "choice": "CURRENT"}
    choices = {"values": {str(number): "CURRENT" for number in range(5000)},
               "fields": {entity["uuid"]: {"line-choice": "CURRENT"}}, "entities": {entity["uuid"]: receipt}}
    state = recorded(target, choices=choices)
    event = state.events[state.entities[entity["uuid"]]["head"]]
    assert event["selection"]["values"] == {"line-choice": "CURRENT"}
    assert event["selection"]["operation_choices_count"] == 5000
    assert len(json.dumps(event)) < 2500
