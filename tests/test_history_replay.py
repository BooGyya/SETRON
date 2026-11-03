"""BE-013 날짜별 이력과 리플레이를 실제 SQLite 트랜잭션으로 검증한다."""

from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import date, datetime

import pytest

from setron.features.history import HistoryFrame, HistoryService, ReplayResult
from setron.features.query_options import QueryScope
from setron.features.storage.validation import StorageValidationError

from test_release_revision import all_rows, observation, revision_storage


EARLY, GAP, LATE = date(2026, 10, 1), date(2026, 10, 2), date(2026, 11, 1)


@pytest.fixture
def history_storage(revision_storage):
    """같은 설비의 당시 소속·모델과 PM별 원문이 다른 두 관측을 저장한다."""
    storage = revision_storage
    early, _ = observation(storage, day=EARLY, versions=((1, 1, " v1 ", None), (2, 3, "c1", None)), pms=True)
    late, _ = observation(storage, day=LATE, versions=((1, 2, "v2", None),), pms=True)
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": early}, {"line": "과거 Line", "geometry_type": "5각"})
        tx.update("equipment_snapshot", {"snapshot_id": late}, {"site": "평택", "line": "현재 Line", "geometry_type": "6각"})
    return storage, early, late


def test_exact_date_preserves_historical_location_pm_raw_and_sources(history_storage):
    storage, early, late = history_storage
    service = HistoryService(storage)
    frame = service.read(QueryScope(EARLY))
    assert frame.base_date == EARLY and frame.has_data
    snapshot, = frame.snapshots
    assert snapshot.snapshot_id == early
    assert (snapshot.site, snapshot.line, snapshot.geometry_type) == ("화성", "과거 Line", "5각")
    assert snapshot.software[0].version_raw == " v1 "
    assert [(sw.family_id, sw.pms[0].pm_name, sw.pms[0].group_id) for sw in snapshot.software] == [(1, "PM1", 1), (2, "PM2", 3)]
    assert snapshot.source_reference == {"model_name": {"sheet": "현황", "cells": ["B1"]}}
    assert snapshot.software[0].source_reference == {"version_raw": {"sheet": "현황", "cells": ["B1"]}}
    latest, = service.read(QueryScope(LATE)).snapshots
    assert latest.snapshot_id == late
    assert (latest.site, latest.line, latest.geometry_type) == ("평택", "현재 Line", "6각")


def test_missing_date_is_empty_instead_of_copied_from_neighbor(history_storage):
    storage, *_ = history_storage
    missing = HistoryService(storage).read(QueryScope(GAP))
    assert missing == HistoryFrame(GAP)
    assert not missing.has_data and missing.snapshots == ()


def test_mixed_scope_retains_only_actual_matching_pairs_and_full_original(history_storage):
    storage, *_ = history_storage
    service = HistoryService(storage)
    assert not service.read(QueryScope(EARLY, model_ids=(1,), family_ids=(2,))).has_data
    selected = service.read(QueryScope(EARLY, model_ids=(3,), family_ids=(2,)))
    match, = selected.matches
    assert match.model_family_pairs == ((3, 2),)
    assert len(selected.snapshots[0].software) == 2


def test_replay_sorts_deduplicates_and_keeps_requested_empty_frames(history_storage):
    storage, *_ = history_storage
    dates = [LATE, GAP, EARLY, LATE]
    replay = HistoryService(storage).replay(iter(dates))
    assert [frame.base_date for frame in replay.frames] == [EARLY, GAP, LATE]
    assert [frame.has_data for frame in replay.frames] == [True, False, True]
    assert replay.frames[1].snapshots == ()
    assert replay.can_replay and replay.message is None
    assert dates == [LATE, GAP, EARLY, LATE]


def test_replay_applies_each_dates_historical_scope_and_overrides_scope_date(history_storage):
    storage, *_ = history_storage
    scope = QueryScope(date(2000, 1, 1), sites=("화성",), lines=("과거 Line",), model_ids=(1,), family_ids=(1,))
    replay = HistoryService(storage).replay((LATE, EARLY), scope)
    assert [frame.has_data for frame in replay.frames] == [True, False]
    assert replay.frames[0].matches[0].model_family_pairs == ((1, 1),)
    assert not replay.can_replay and replay.message
    assert scope.base_date == date(2000, 1, 1)


@pytest.mark.parametrize("dates", [(EARLY,), (EARLY, EARLY), (EARLY, GAP), (GAP,)])
def test_replay_requires_two_distinct_actual_dates(history_storage, dates):
    storage, *_ = history_storage
    result = HistoryService(storage).replay(dates)
    assert not result.can_replay and "실제 관측" in result.message


def test_empty_database_returns_requested_empty_frames(revision_storage):
    result = HistoryService(revision_storage).replay((EARLY, GAP, LATE))
    assert [frame.base_date for frame in result.frames] == [EARLY, GAP, LATE]
    assert not any(frame.has_data for frame in result.frames)
    assert not result.can_replay and result.message


def test_reads_share_callers_transaction_and_observe_uncommitted_data(history_storage):
    storage, early, _ = history_storage
    service = HistoryService(storage)
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": early}, {"line": "아직 미확정"})
        frame = service.read_in(tx, QueryScope(EARLY, lines=("아직 미확정",)))
        assert frame.snapshots[0].line == "아직 미확정"
        replay = service.replay_in(tx, (EARLY, LATE), QueryScope(lines=("아직 미확정",)))
        assert [frame.has_data for frame in replay.frames] == [True, False]


def test_replay_opens_one_transaction_for_all_frames(history_storage, monkeypatch):
    storage, *_ = history_storage
    original = storage.transaction
    opened = []

    @contextmanager
    def counted_transaction():
        with original() as tx:
            opened.append(tx)
            yield tx

    monkeypatch.setattr(storage, "transaction", counted_transaction)
    assert HistoryService(storage).replay((EARLY, GAP, LATE)).can_replay
    assert len(opened) == 1


def test_history_queries_do_not_write_or_change_original_sources(history_storage):
    storage, *_ = history_storage
    tables = ("equipment", "equipment_snapshot", "equipment_version", "equipment_pm", "import_batch", "app_setting")
    before = {table: all_rows(storage, table) for table in tables}
    service = HistoryService(storage)
    service.read(QueryScope(EARLY))
    replay = service.replay((EARLY, GAP, LATE))
    with pytest.raises(FrozenInstanceError):
        replay.frames = ()
    with pytest.raises(FrozenInstanceError):
        replay.frames[0].base_date = GAP
    assert {table: all_rows(storage, table) for table in tables} == before


@pytest.mark.parametrize("scope", [None, QueryScope(), "2026-10-01", EARLY])
def test_read_rejects_missing_or_invalid_explicit_date(revision_storage, scope):
    with pytest.raises(StorageValidationError):
        HistoryService(revision_storage).read(scope)


@pytest.mark.parametrize("dates", [(), [], None, EARLY, "2026-10-01", ("2026-10-01",), (datetime(2026, 10, 1),), (True,)])
def test_replay_rejects_missing_or_non_date_values(revision_storage, dates):
    with pytest.raises(StorageValidationError):
        HistoryService(revision_storage).replay(dates)


def test_replay_rejects_invalid_scope(revision_storage):
    with pytest.raises(StorageValidationError):
        HistoryService(revision_storage).replay((EARLY,), {"sites": ["화성"]})


def test_empty_result_reports_insufficient_actual_dates():
    result = ReplayResult(())
    assert not result.can_replay and result.message
