"""N 그룹 저장·이력과 기존 형식 이관을 실제 SQLite 트랜잭션으로 검증한다."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from setron.features.aggregation.service import summarize
from setron.features.storage.validation import StorageValidationError
from setron.features.targets.groups import (
    GroupSaveBlocked,
    GroupStore,
    HISTORY_KEY_PREFIX,
    setting_key,
    with_saved_groups,
)
from setron.features.version.groups import GroupIssueCode, Slot, VersionGroups
from setron.features.version.schemas import CompareStatus
from test_analysis_service import F_O2, F_O3, FAMILIES, M_O2, M_O3, RECORDS, V21, V22, V23, V31
from test_release_revision import revision_storage  # noqa: F401 - pytest fixture

PAIR = (M_O2, F_O2)
O2 = FAMILIES[F_O2]


@pytest.fixture
def storage(revision_storage):
    with revision_storage.transaction() as tx:
        for family_id, model_id, name in ((F_O2, M_O2, "O2 이력"), (F_O3, M_O3, "O3 이력")):
            tx.insert("version_family", {"family_id": family_id, "group_id": model_id, "family_name": name})
    return revision_storage


def stored_value(storage, pair=PAIR):
    with storage.transaction() as tx:
        return tx.get("app_setting", {"setting_key": setting_key(pair)})["setting_value"]


def put_raw(storage, value, pair=PAIR):
    with storage.transaction() as tx:
        tx.insert("app_setting", {"setting_key": setting_key(pair), "setting_value": value})


def clock(start=datetime(2026, 11, 6, 15, 30, 0, 123456)):
    times = iter(start + timedelta(minutes=i) for i in range(100))
    return lambda: next(times)


GROUPS = VersionGroups({Slot.N: frozenset({V23}), Slot.N_MINUS_1: frozenset({V22})})


def test_save_then_load_returns_groups_and_first_history_entry(storage):
    store = GroupStore(storage, clock=clock())

    result = store.save(PAIR, GROUPS, O2, memo="  핫픽스 묶음  ")
    loaded = store.load(PAIR)

    assert result.warnings == ()
    assert loaded.groups == GROUPS
    assert [(e.saved_at, e.memo) for e in loaded.history] == [(datetime(2026, 11, 6, 15, 30), "핫픽스 묶음")]
    stored = stored_value(storage)
    assert stored["schema"] == 3 and stored["sequence"] == stored["active_sequence"] == 1
    assert stored["current"]["groups"] == {"N-1": [V22], "N": [V23]}
    assert store.load_current(PAIR) == result.saved == loaded.history[0]


def test_each_save_appends_history_and_latest_is_current(storage):
    store = GroupStore(storage, clock=clock())
    second = VersionGroups({Slot.N: frozenset({V22, V23})})

    store.save(PAIR, GROUPS, O2, memo="처음")
    store.save(PAIR, second, O2, memo="v2.2도 N으로")
    loaded = store.load(PAIR)

    assert [e.memo for e in loaded.history] == ["처음", "v2.2도 N으로"]
    assert [e.groups for e in loaded.history] == [GROUPS, second]
    assert loaded.groups == second and loaded.saved_at == datetime(2026, 11, 6, 15, 31)


def test_empty_n_slot_blocks_save_and_keeps_previous(storage):
    store = GroupStore(storage, clock=clock())
    store.save(PAIR, GROUPS, O2)

    with pytest.raises(GroupSaveBlocked) as blocked:
        store.save(PAIR, VersionGroups({Slot.N_MINUS_1: frozenset({V22})}), O2)

    assert [i.code for i in blocked.value.issues if i.blocks] == [GroupIssueCode.N_EMPTY]
    assert len(store.load(PAIR).history) == 1


def test_warnings_do_not_block_save(storage):
    store = GroupStore(storage, clock=clock())
    crossed = VersionGroups({Slot.N: frozenset({V21}), Slot.N_MINUS_1: frozenset({V23}), Slot.N_MINUS_2: frozenset({999})})

    result = store.save(PAIR, crossed, O2)

    assert {w.code for w in result.warnings} == {GroupIssueCode.MISSING_RELEASE, GroupIssueCode.ORDER_CROSSED}
    assert store.load(PAIR).groups == crossed


def test_save_rejects_other_family_releases_and_long_memo(storage):
    store = GroupStore(storage, clock=clock())

    with pytest.raises(StorageValidationError):
        store.save(PAIR, GROUPS, FAMILIES[F_O3])
    with pytest.raises(StorageValidationError):
        store.save(PAIR, GROUPS, O2, memo="가" * 1001)
    with pytest.raises(StorageValidationError):
        store.save((0, F_O2), GROUPS, O2)


def test_migrate_single_moves_existing_target_once(storage):
    store = GroupStore(storage, clock=clock())

    first = store.migrate_single(PAIR, V22, O2)
    again = store.migrate_single(PAIR, V23, O2)

    assert first.saved.groups == VersionGroups.single(V22)
    assert again is None  # 이미 저장된 그룹은 덮어쓰지 않는다
    assert store.load(PAIR).history[0].memo == "기존 목표 N(릴리즈 하나)에서 옮김"


def test_load_many_skips_pairs_without_groups(storage):
    store = GroupStore(storage, clock=clock())
    store.save(PAIR, GROUPS, O2)

    assert store.load_many([PAIR, (M_O3, F_O3), PAIR]) == {PAIR: GROUPS}
    assert store.load((M_O3, F_O3)) is None


@pytest.mark.parametrize("value", [
    {"schema": 2, "model_id": M_O2, "family_id": F_O2, "history": []},
    {"schema": 1, "model_id": M_O3, "family_id": F_O2, "history": [{"saved_at": "2026-11-06T00:00:00", "memo": "", "groups": {"N": [V23]}}]},
    {"schema": 1, "model_id": M_O2, "family_id": F_O2, "history": []},
    {"schema": 1, "model_id": M_O2, "family_id": F_O2, "history": [{"saved_at": "어제", "memo": "", "groups": {"N": [V23]}}]},
    {"schema": 1, "model_id": M_O2, "family_id": F_O2, "history": [{"saved_at": "2026-11-06T00:00:00", "memo": "", "groups": {"N+3": [V23]}}]},
    {"schema": 1, "model_id": M_O2, "family_id": F_O2, "history": [{"saved_at": "2026-11-06T00:00:00", "memo": "", "groups": {"N": ["103"]}}]},
    {"schema": 1, "model_id": M_O2, "family_id": F_O2, "history": [{"saved_at": "2026-11-06T00:00:00", "memo": "",
                                                                      "groups": {"N": [V23], "N-1": [V23]}}]},
])
def test_broken_saved_value_is_reported_and_not_overwritten(storage, value):
    put_raw(storage, value)
    store = GroupStore(storage, clock=clock())

    with pytest.raises(StorageValidationError):
        store.load(PAIR)
    with pytest.raises(StorageValidationError):
        store.save(PAIR, GROUPS, O2)
    assert stored_value(storage) == value


def legacy_history(count):
    when = datetime(2026, 11, 6, 15, 30)
    return {"schema": 1, "model_id": M_O2, "family_id": F_O2, "history": [
        {"saved_at": when.isoformat(), "memo": str(index), "groups": {"N": [V22, V23]}}
        for index in range(1, count + 1)
    ]}


def test_legacy_history_is_migrated_and_fetched_in_bounded_key_ranges(storage):
    put_raw(storage, legacy_history(25))
    store = GroupStore(storage, clock=clock())
    assert store.load_current(PAIR).memo == "25"
    assert stored_value(storage)["schema"] == 1  # 현재만 읽는 조회는 구 형식을 쓰지 않는다.
    selects = []

    @event.listens_for(storage.database.engine, "before_cursor_execute")
    def capture(_connection, _cursor, statement, parameters, _context, _executemany):
        if statement.startswith("SELECT") and "FROM app_setting" in statement:
            selects.append((statement, parameters))

    first = store.history_page(PAIR)
    assert stored_value(storage)["schema"] == 3
    assert first.current.memo == "25"
    assert [entry.memo for entry in first.entries] == [str(i) for i in range(25, 15, -1)]
    assert first.next_cursor == 16

    store.save(PAIR, GROUPS, O2, memo="새 기준")
    middle = store.history_page(PAIR, cursor=first.next_cursor)
    last = store.history_page(PAIR, cursor=middle.next_cursor)
    assert middle.current.memo == "새 기준"
    assert [entry.memo for entry in middle.entries + last.entries] == [str(i) for i in range(15, 0, -1)]
    assert last.next_cursor is None
    ranges = [(sql, params) for sql, params in selects if " BETWEEN " in sql]
    assert all("ORDER BY app_setting.setting_key DESC" in sql for sql, _ in ranges)
    assert [params[-2] for _, params in ranges] == [10, 10, 5]  # 세 페이지에만 LIMIT 사용

    selects.clear()
    assert store.load_many([PAIR, (M_O3, F_O3)]) == {PAIR: GROUPS}
    assert len(selects) == 1 and " BETWEEN " not in selects[0][0]


def test_changed_save_migrates_all_legacy_entries_without_overwriting_them(storage):
    put_raw(storage, legacy_history(2))
    store = GroupStore(storage, clock=clock())
    result = store.save(PAIR, GROUPS, O2, memo="새 기준")

    assert result.saved.memo == "새 기준"
    assert [entry.memo for entry in store.load(PAIR).history] == ["1", "2", "새 기준"]
    assert stored_value(storage)["sequence"] == 3


def test_restore_migrates_v2_without_losing_previous_current(storage):
    legacy = legacy_history(2)["history"]
    legacy[0]["groups"] = {"N": [V22]}
    pair_prefix = f"{HISTORY_KEY_PREFIX}{PAIR[0]}:{PAIR[1]}:"
    with storage.transaction() as tx:
        tx.insert("app_setting", {"setting_key": pair_prefix + "00000000000000000001", "setting_value": legacy[0]})
    put_raw(storage, {"schema": 2, "model_id": M_O2, "family_id": F_O2, "sequence": 2, "current": legacy[1]})
    store = GroupStore(storage, clock=clock())
    original = store.load(PAIR)
    assert original.current.sequence == 2

    restored = store.restore(PAIR, 1, family=O2)
    page = store.history_page(PAIR)
    assert restored.changed and restored.saved == original.history[0] == page.current
    assert page.entries == tuple(reversed(original.history))
    assert stored_value(storage)["schema"] == 3
    assert stored_value(storage)["sequence"] == 2
    assert stored_value(storage)["active_sequence"] == 1


def test_restore_keeps_history_pages_and_later_save_appends_once(storage):
    store = GroupStore(storage, clock=clock())
    for sequence in range(1, 14):
        groups = VersionGroups.single(V22 if sequence % 2 else V23)
        store.save(PAIR, groups, O2, memo=str(sequence))
    first = store.history_page(PAIR)
    assert [entry.sequence for entry in first.entries] == list(range(13, 3, -1))

    restored = store.restore(PAIR, 2, family=O2)
    loaded = store.load(PAIR)
    assert restored.saved == loaded.history[1] == loaded.current
    assert loaded.groups == VersionGroups.single(V23) and loaded.saved_at == restored.saved.saved_at
    assert store.load_current(PAIR) == restored.saved
    assert store.load_many([PAIR]) == {PAIR: restored.saved.groups}
    after = store.history_page(PAIR)
    assert after.current.sequence == 2 and after.entries == first.entries and after.next_cursor == first.next_cursor
    assert [entry.sequence for entry in store.history_page(PAIR, cursor=first.next_cursor).entries] == [3, 2, 1]
    assert not store.restore(PAIR, 2, family=O2).changed
    assert store.restore(PAIR, 13, family=O2).saved == first.current
    store.restore(PAIR, 2, family=O2)

    assert not store.save(PAIR, restored.saved.groups, O2).changed
    saved = store.save(PAIR, first.current.groups, O2, memo="명시적으로 새 저장")
    assert saved.changed and saved.saved.sequence == 14
    assert store.load(PAIR).history == (*loaded.history, saved.saved)
    assert stored_value(storage)["active_sequence"] == 14


def test_corrupt_legacy_history_blocks_migration_without_overwriting(storage):
    legacy = legacy_history(12)
    legacy["history"][0]["saved_at"] = "어제"
    put_raw(storage, legacy)
    store = GroupStore(storage, clock=clock())
    assert store.load_current(PAIR).memo == "12"

    with pytest.raises(StorageValidationError):
        store.history_page(PAIR)
    with pytest.raises(StorageValidationError):
        store.save(PAIR, GROUPS, O2)
    assert stored_value(storage) == legacy


def test_with_saved_groups_overrides_only_saved_pairs():
    targets = {PAIR: V21, (M_O3, F_O3): V31, (9, 99): None}

    merged = with_saved_groups(targets, {PAIR: GROUPS})

    assert merged == {PAIR: GROUPS, (M_O3, F_O3): V31}
    assert targets[PAIR] == V21  # 입력은 그대로


def test_saved_groups_change_aggregation_result():
    """저장 그룹을 넣으면 목표 하나(v2.3)일 때 N 미만이던 v2.2가 그룹의 N 칸이 되어 N 이상으로 바뀐다."""
    single = {PAIR: V23, (M_O3, F_O3): V31}
    grouped = with_saved_groups(single, {PAIR: VersionGroups({Slot.N: frozenset({V22, V23})})})

    def status(targets, version):
        summary = summarize(RECORDS, FAMILIES, targets)
        return next(f.status for e in summary.equipment for f in e.families
                    if f.family_id == F_O2 and f.pms[0].pm.version_raw == version)

    assert status(single, "v2.2") is CompareStatus.BELOW
    assert status(grouped, "v2.2") is CompareStatus.AT_OR_ABOVE


def test_saving_the_same_groups_again_does_not_add_history(storage):
    """같은 내용을 다시 저장하면 이력을 늘리지 않는다."""
    store = GroupStore(storage, clock=clock())
    first = store.save(PAIR, GROUPS, O2, memo="처음")
    again = store.save(PAIR, VersionGroups(dict(GROUPS.slots)), O2, memo="같은 내용")
    assert first.changed and not again.changed
    assert len(store.load(PAIR).history) == 1 and again.saved.groups == GROUPS
    second = VersionGroups({Slot.N: frozenset({V22, V23})})
    assert store.save(PAIR, second, O2).changed and len(store.load(PAIR).history) == 2
    assert store.save(PAIR, GROUPS, O2, memo="과거 내용과 같은 새 저장").changed
    assert not store.save(PAIR, GROUPS, O2).changed and len(store.load(PAIR).history) == 3
