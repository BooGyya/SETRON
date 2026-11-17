"""공통 계보의 최대 조상과 3방향 비교가 시각·로컬 ID·누락 값에 의존하지 않는다."""

from copy import deepcopy
from datetime import date, datetime
from uuid import UUID

import pytest

from setron.features.peer_sync import (
    MISSING, UNAVAILABLE, Baseline, BaselineIndex, PeerSyncError, SyncState,
    baseline_for, canonical_value, decide, equivalent, identity_index, portable_row,
)


ENTITY = str(UUID(int=100))
OTHER_ENTITY = str(UUID(int=101))


def identifier(number):
    return str(UUID(int=number))


def event(number, after, *parents, entity=ENTITY, table="equipment_snapshot", recorded_at="2026-11-17"):
    return {"uuid": identifier(number), "entity": entity, "table": table,
            "parents": [identifier(parent) for parent in parents], "after": deepcopy(after),
            "recorded_at": recorded_at}


def state(head, *events, entity=ENTITY, aliases=(), key=None):
    records = {value["uuid"]: deepcopy(value) for value in events}
    head_id = identifier(head)
    return SyncState(entities={entity: {"uuid": entity, "table": records[head_id]["table"],
                                      "aliases": list(aliases), "head": head_id,
                                      "deleted": records[head_id]["after"] is None,
                                      "key": {"snapshot_id": 1} if key is None else key}}, events=records)


def test_common_baseline_uses_topology_and_combines_independent_field_changes():
    base = event(1, {"line": "원래 라인", "process_name": "원래 공정"}, recorded_at="2999-01-01")
    source = state(2, base, event(2, {"line": "가져온 라인", "process_name": "원래 공정"}, 1,
                                        recorded_at="1900-01-01"))
    target = state(3, base, event(3, {"line": "원래 라인", "process_name": "현재 공정"}, 1))
    before = deepcopy((source, target))
    index = BaselineIndex(source, target)
    baseline = index.baseline_for(ENTITY)

    assert baseline.found and baseline.revisions == (identifier(1),)
    assert baseline.payload == base["after"]
    assert decide(baseline.payload["line"], "원래 라인", "가져온 라인").choice == "INCOMING"
    assert decide(baseline.payload["process_name"], "현재 공정", "원래 공정").choice == "CURRENT"
    assert (source, target) == before


@pytest.mark.parametrize("base,current,incoming,choice,kind", [
    ("A", "A", "A", "CURRENT", "unchanged"),
    ("A", "B", "B", "CURRENT", "same_change"),
    ("A", "A", "B", "INCOMING", "incoming_only"),
    ("A", "B", "A", "CURRENT", "current_only"),
    ("A", "B", "C", None, "conflict"),
    (UNAVAILABLE, "A", "B", None, "no_baseline"),
    (UNAVAILABLE, "A", "A", "CURRENT", "same_change"),
    (MISSING, None, MISSING, "CURRENT", "current_only"),
    (None, None, MISSING, "INCOMING", "incoming_only"),
    ({"v": 1}, None, {"v": 2}, None, "conflict"),
    ({"v": 1}, None, {"v": 1}, "CURRENT", "current_only"),
    ({"v": 1}, {"v": 1}, None, "INCOMING", "incoming_only"),
    ({"v": 1}, None, None, "CURRENT", "same_change"),
    (None, None, {"v": 1}, "INCOMING", "incoming_only"),
    (False, 0, False, "CURRENT", "current_only"),
    (True, 1, 2, None, "conflict"),
])
def test_three_way_decision_distinguishes_missing_null_deletion_and_value_types(base, current, incoming, choice, kind):
    result = decide(base, current, incoming)
    assert (result.choice, result.kind) == (choice, kind)


def test_unrelated_histories_never_invent_a_common_baseline():
    first = state(1, event(1, {"v": "A"}))
    second = state(2, event(2, {"v": "A"}))
    found = baseline_for(first, second, ENTITY)
    assert not found.found and found.payload is UNAVAILABLE and found.reason == "no_common_ancestor"
    assert decide(found, "A", "B").choice is None


def test_missing_identity_and_mismatched_identity_do_not_supply_a_baseline():
    first = state(1, event(1, {"v": 1}))
    second = state(2, event(2, {"v": 2}, entity=OTHER_ENTITY), entity=OTHER_ENTITY)
    assert not baseline_for(first, SyncState(), ENTITY).found
    result = baseline_for(first, second, ENTITY, OTHER_ENTITY)
    assert not result.found and result.reason == "identity_mismatch"


@pytest.mark.parametrize("same", [False, True])
def test_criss_cross_requires_identical_maximal_common_payloads(same):
    root = event(1, {"v": "root"})
    left = event(2, {"v": "left"}, 1)
    right = event(3, {"v": "left" if same else "right"}, 1)
    first = state(4, root, left, right, event(4, {"v": "first resolution"}, 2, 3))
    second = state(5, root, left, right, event(5, {"v": "second resolution"}, 2, 3))
    result = baseline_for(first, second, ENTITY)
    assert result.revisions == (identifier(2), identifier(3))
    if same:
        assert result.found and result.payload == {"v": "left"}
        assert result.reason == "equivalent_common_ancestors"
    else:
        assert not result.found and result.payload is UNAVAILABLE
        assert result.reason == "ambiguous_common_ancestors"


def test_tombstone_can_be_a_valid_common_baseline():
    deleted = event(1, None)
    first = state(2, deleted, event(2, {"v": "복원"}, 1))
    second = state(1, deleted)
    result = baseline_for(first, second, ENTITY)
    assert result.found and result.payload is None
    assert decide(result, None, {"v": "복원"}).choice == "INCOMING"


def test_entity_aliases_and_portable_reference_aliases_are_normalized():
    original = event(1, {"group_id": {"$ref": ENTITY}, "v": 1})
    source = state(2, original, event(2, {"group_id": {"$ref": OTHER_ENTITY}, "v": 2}, 1,
                                                entity=OTHER_ENTITY), entity=OTHER_ENTITY, aliases=(ENTITY,))
    target = state(3, original, event(3, {"group_id": {"$ref": ENTITY}, "v": 3}, 1))
    index = BaselineIndex(source, target)
    result = index.baseline_for(OTHER_ENTITY, ENTITY)
    assert result.found
    assert equivalent({"$ref": ENTITY}, {"$ref": OTHER_ENTITY}, index.aliases)
    assert decide({"$ref": ENTITY}, {"$ref": OTHER_ENTITY}, {"$ref": ENTITY}, aliases=index.aliases).choice == "CURRENT"


def test_binary_and_date_native_values_match_the_portable_event_shape():
    assert equivalent(b"\x00\xff", {"$binary": "00ff"})
    assert equivalent(date(2026, 11, 17), {"$date": "2026-11-17"})
    assert equivalent(datetime(2026, 11, 17, 12, 1), {"$datetime": "2026-11-17T12:01:00"})
    assert canonical_value({"v": [MISSING, None]}) == {"v": [MISSING, None]}
    assert not equivalent({"v": MISSING}, {"v": None})


def test_missing_parent_is_rejected_even_if_another_peer_supplies_it():
    root = event(1, {"v": 1})
    first = state(2, event(2, {"v": 2}, 1))
    second = state(1, root)
    with pytest.raises(PeerSyncError, match="부모 변경 이력이 빠져"):
        baseline_for(first, second, ENTITY)


@pytest.mark.parametrize("other_table", [False, True])
def test_parent_of_another_entity_or_table_is_rejected(other_table):
    root = event(1, {"v": 1}, entity=OTHER_ENTITY if not other_table else ENTITY,
                 table="equipment" if other_table else "equipment_snapshot")
    source = state(2, root, event(2, {"v": 2}, 1))
    with pytest.raises(PeerSyncError, match="다른 항목"):
        BaselineIndex(source, source)


def test_event_content_collision_and_cycles_are_rejected():
    first = state(1, event(1, {"v": 1}))
    altered = state(1, event(1, {"v": 2}))
    with pytest.raises(PeerSyncError, match="같은 변경 UUID"):
        BaselineIndex(first, altered)
    cycle = state(1, event(1, {"v": 1}, 2), event(2, {"v": 2}, 1))
    with pytest.raises(PeerSyncError, match="순환"):
        BaselineIndex(cycle, cycle)


def test_duplicate_event_id_keeps_boolean_and_integer_content_distinct():
    boolean = state(1, event(1, {"v": True}))
    integer = state(1, event(1, {"v": 1}))
    with pytest.raises(PeerSyncError, match="같은 변경 UUID"):
        BaselineIndex(boolean, integer)


def test_long_history_is_iterative_and_baseline_payload_cannot_mutate_cached_events():
    events = [event(1, {"v": 1, "nested": {"safe": True}})]
    events.extend(event(number, {"v": number, "nested": {"safe": True}}, number - 1)
                  for number in range(2, 1602))
    first, second = state(1601, *events), state(1600, *events[:-1])
    index = BaselineIndex(first, second)
    result = index.baseline_for(ENTITY)
    assert result.found and result.revisions == (identifier(1600),)
    result.payload["nested"]["safe"] = False
    assert index.baseline_for(ENTITY).payload["nested"]["safe"] is True
    assert first.events[identifier(1600)]["after"]["nested"]["safe"] is True


def test_portable_row_uses_actual_unexported_local_values(target):
    """현재값은 ledger의 오래된 head가 아니라 실제 DB 행에서 구한다."""
    from setron.features.peer_sync import read_rows, reconcile
    from setron.features.storage.repository import StorageRepository
    from setron.infrastructure.database import Database
    from test_sql_import_service import bundle, current, seed

    seed(target, bundle())
    with Database(target[0]).transaction() as session:
        repository = StorageRepository(session.connection())
        recorded = reconcile(session, repository, peer_id=identifier(99))
    entity = next(value for value in recorded.entities.values() if value["table"] == "equipment_snapshot")
    with target[0].begin() as connection:
        connection.execute(target[1]["equipment_snapshot"].update().values(line="내보내기 전 로컬 수정"))
    with Database(target[0]).transaction() as session:
        repository = StorageRepository(session.connection())
        rows = read_rows(session, repository)
        payload = portable_row("equipment_snapshot", rows["equipment_snapshot"][0], repository, identity_index(rows))
    assert payload["line"] == "내보내기 전 로컬 수정"
    assert recorded.events[entity["head"]]["after"]["line"] != payload["line"]


from test_sql_import_service import target  # noqa: E402,F401
