"""N 그룹 저장·이력(``features/targets/groups.py``)을 검증한다.

DB 대신 공통 저장 서비스와 같은 변환(``encode_record``·``decode_record``)을 쓰는 가짜 트랜잭션을 쓴다.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

from setron.features.aggregation.service import summarize
from setron.features.storage.validation import StorageValidationError, decode_record, encode_record
from setron.features.targets.groups import (
    GroupSaveBlocked,
    GroupStore,
    setting_key,
    with_saved_groups,
)
from setron.features.version.groups import GroupIssueCode, Slot, VersionGroups
from setron.features.version.schemas import CompareStatus
from test_analysis_service import F_O2, F_O3, FAMILIES, M_O2, M_O3, RECORDS, V21, V22, V23, V31

PAIR = (M_O2, F_O2)
O2 = FAMILIES[F_O2]


class FakeRepository:
    def __init__(self):
        self.rows = {}
        self.locked = []

    def get(self, session, table, key, *, for_update=False):
        if for_update:
            self.locked.append(key["setting_key"])
        if key["setting_key"] not in self.rows:
            raise LookupError("해당 기록이 없습니다.")
        return dict(self.rows[key["setting_key"]])


class FakeStorage:
    """공통 저장 서비스처럼 JSON을 문자열로 바꿔 보관한다."""

    def __init__(self):
        self.repository = FakeRepository()
        self.session = None

    @contextmanager
    def transaction(self):
        yield self

    def get(self, table, key):
        return decode_record(table, self.repository.get(None, table, key))

    def insert(self, table, values):
        encoded = encode_record(table, values, inserting=True)
        self.repository.rows[values["setting_key"]] = {**encoded, "setting_key": values["setting_key"]}

    def update(self, table, key, changes):
        current = decode_record(table, self.repository.get(None, table, key, for_update=True))
        encoded = encode_record(table, {**current, **changes})
        self.repository.rows[key["setting_key"]] = {**encoded, "setting_key": key["setting_key"]}

    def put_raw(self, pair, value):
        self.insert("app_setting", {"setting_key": setting_key(pair), "setting_value": value})


def clock(start=datetime(2026, 11, 6, 15, 30, 0, 123456)):
    times = iter(start + timedelta(minutes=i) for i in range(100))
    return lambda: next(times)


GROUPS = VersionGroups({Slot.N: frozenset({V23}), Slot.N_MINUS_1: frozenset({V22})})


def test_save_then_load_returns_groups_and_first_history_entry():
    storage = FakeStorage()
    store = GroupStore(storage, clock=clock())

    result = store.save(PAIR, GROUPS, O2, memo="  핫픽스 묶음  ")
    loaded = store.load(PAIR)

    assert result.warnings == ()
    assert loaded.groups == GROUPS
    assert [(e.saved_at, e.memo) for e in loaded.history] == [(datetime(2026, 11, 6, 15, 30), "핫픽스 묶음")]
    stored = storage.get("app_setting", {"setting_key": "version_groups.v1:1:10"})["setting_value"]
    assert stored["history"][0]["groups"] == {"N-1": [V22], "N": [V23]}


def test_each_save_appends_history_and_latest_is_current():
    storage = FakeStorage()
    store = GroupStore(storage, clock=clock())
    second = VersionGroups({Slot.N: frozenset({V22, V23})})

    store.save(PAIR, GROUPS, O2, memo="처음")
    store.save(PAIR, second, O2, memo="v2.2도 N으로")
    loaded = store.load(PAIR)

    assert [e.memo for e in loaded.history] == ["처음", "v2.2도 N으로"]
    assert [e.groups for e in loaded.history] == [GROUPS, second]
    assert loaded.groups == second and loaded.saved_at == datetime(2026, 11, 6, 15, 31)
    assert storage.repository.locked.count(setting_key(PAIR)) >= 2  # 덧붙이기 전에 잠그고 읽는다


def test_empty_n_slot_blocks_save_and_keeps_previous():
    storage = FakeStorage()
    store = GroupStore(storage, clock=clock())
    store.save(PAIR, GROUPS, O2)

    with pytest.raises(GroupSaveBlocked) as blocked:
        store.save(PAIR, VersionGroups({Slot.N_MINUS_1: frozenset({V22})}), O2)

    assert [i.code for i in blocked.value.issues if i.blocks] == [GroupIssueCode.N_EMPTY]
    assert len(store.load(PAIR).history) == 1


def test_warnings_do_not_block_save():
    store = GroupStore(FakeStorage(), clock=clock())
    crossed = VersionGroups({Slot.N: frozenset({V21}), Slot.N_MINUS_1: frozenset({V23}), Slot.N_MINUS_2: frozenset({999})})

    result = store.save(PAIR, crossed, O2)

    assert {w.code for w in result.warnings} == {GroupIssueCode.MISSING_RELEASE, GroupIssueCode.ORDER_CROSSED}
    assert store.load(PAIR).groups == crossed


def test_save_rejects_other_family_releases_and_long_memo():
    store = GroupStore(FakeStorage(), clock=clock())

    with pytest.raises(StorageValidationError):
        store.save(PAIR, GROUPS, FAMILIES[F_O3])
    with pytest.raises(StorageValidationError):
        store.save(PAIR, GROUPS, O2, memo="가" * 1001)
    with pytest.raises(StorageValidationError):
        store.save((0, F_O2), GROUPS, O2)


def test_migrate_single_moves_existing_target_once():
    store = GroupStore(FakeStorage(), clock=clock())

    first = store.migrate_single(PAIR, V22, O2)
    again = store.migrate_single(PAIR, V23, O2)

    assert first.saved.groups == VersionGroups.single(V22)
    assert again is None  # 이미 저장된 그룹은 덮어쓰지 않는다
    assert store.load(PAIR).history[0].memo == "기존 목표 N(릴리즈 하나)에서 옮김"


def test_load_many_skips_pairs_without_groups():
    store = GroupStore(FakeStorage(), clock=clock())
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
def test_broken_saved_value_is_reported_and_not_overwritten(value):
    storage = FakeStorage()
    storage.put_raw(PAIR, value)
    store = GroupStore(storage, clock=clock())

    with pytest.raises(StorageValidationError):
        store.load(PAIR)
    with pytest.raises(StorageValidationError):
        store.save(PAIR, GROUPS, O2)
    assert storage.get("app_setting", {"setting_key": setting_key(PAIR)})["setting_value"] == value


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


def test_saving_the_same_groups_again_does_not_add_history():
    """같은 내용을 다시 저장하거나 지금 기준으로 되돌리면 이력을 늘리지 않는다 (11/08: 같은 줄이 계속 쌓이던 문제)."""
    storage = FakeStorage()
    store = GroupStore(storage, clock=clock())
    first = store.save(PAIR, GROUPS, O2, memo="처음")
    again = store.save(PAIR, VersionGroups(dict(GROUPS.slots)), O2, memo="같은 내용")
    assert first.changed and not again.changed
    assert len(store.load(PAIR).history) == 1 and again.saved.groups == GROUPS
    second = VersionGroups({Slot.N: frozenset({V22, V23})})
    assert store.save(PAIR, second, O2).changed and len(store.load(PAIR).history) == 2
    assert store.save(PAIR, GROUPS, O2, memo="되돌림").changed                 # 다른 내용으로 되돌리기는 남긴다
    assert not store.save(PAIR, GROUPS, O2, memo="되돌림").changed and len(store.load(PAIR).history) == 3
