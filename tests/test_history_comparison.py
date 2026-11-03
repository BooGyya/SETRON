"""BE-013 두 날짜의 실제 버전 변경을 고정 PM 위치와 확인된 릴리즈로 검증한다."""

from datetime import date, datetime

import pytest

from setron.features.history import (
    ChangeReason, ChangeStatus, HistoryFrame, HistoryService, VersionDirection, compare_frames,
)
from setron.features.query_options import QueryScope
from setron.features.storage.validation import StorageValidationError
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

from test_release_revision import all_rows, observation, release, revision_storage


EARLY, GAP, LATE = date(2026, 10, 1), date(2026, 10, 2), date(2026, 11, 1)


def test_unregistered_different_raw_is_changed_with_unknown_direction(revision_storage):
    storage = revision_storage
    observation(storage, day=EARLY, versions=((1, 1, "v1", None),))
    observation(storage, day=LATE, versions=((1, 1, "v2", None),))

    result = HistoryService(storage).compare(EARLY, LATE)

    change, = result.changed
    assert change.status is ChangeStatus.CHANGED
    assert change.direction is VersionDirection.UNKNOWN
    assert change.reason is ChangeReason.RELEASE_UNCONFIRMED
    assert (change.before.software.version_raw, change.after.software.version_raw) == ("v1", "v2")
    assert (change.before_date, change.after_date) == (EARLY, LATE)
    assert change.pm_name is None
    assert result.changed_equipment_ids == (change.equipment_id,)


def test_exact_same_unregistered_raw_is_unchanged(revision_storage):
    storage = revision_storage
    for day in (EARLY, LATE):
        observation(storage, day=day, versions=((1, 1, "v1_n4", None),))

    result = HistoryService(storage).compare(EARLY, LATE)

    change, = result.changes
    assert change.status is ChangeStatus.UNCHANGED
    assert change.direction is VersionDirection.UNCHANGED
    assert result.changed == result.changed_equipment_ids == ()


def test_verified_same_release_with_different_raw_is_unchanged(revision_storage):
    storage = revision_storage
    confirmed = release(storage, raw="v999.3.7")
    observation(storage, day=EARLY, versions=((1, 1, "v999.3.7", confirmed),))
    observation(storage, day=LATE, versions=((1, 1, "v999.3_7", confirmed),))

    result = HistoryService(storage).compare(EARLY, LATE)

    change, = result.changes
    assert change.status is ChangeStatus.UNCHANGED
    assert change.before.software.version_raw == "v999.3.7"
    assert change.after.software.version_raw == "v999.3_7"
    assert result.changed_equipment_ids == ()


def test_pure_comparison_preserves_verified_same_release_without_catalog(revision_storage):
    storage = revision_storage
    confirmed = release(storage, raw="v999.3.7")
    observation(storage, day=EARLY, versions=((1, 1, "v999.3.7", confirmed),))
    observation(storage, day=LATE, versions=((1, 1, "v999.3_7", confirmed),))
    service = HistoryService(storage)

    result = compare_frames(service.read(QueryScope(EARLY)), service.read(QueryScope(LATE)))

    change, = result.changes
    assert change.status is ChangeStatus.UNCHANGED
    assert change.raw_changed is True
    assert result.changed == ()


def test_release_order_controls_direction_instead_of_version_text(revision_storage):
    storage = revision_storage
    oldest = release(storage, raw="v9", order=1)
    newest = release(storage, raw="v1_n4", order=5)
    observation(storage, day=EARLY, code="UP", versions=((1, 1, "v9", oldest),))
    observation(storage, day=LATE, code="UP", versions=((1, 1, "v1_n4", newest),))
    observation(storage, day=EARLY, code="DOWN", versions=((1, 1, "v1_n4", newest),))
    observation(storage, day=LATE, code="DOWN", versions=((1, 1, "v9", oldest),))

    result = HistoryService(storage).compare(EARLY, LATE)

    changes = {change.equipment_code: change for change in result.changed}
    assert changes["UP"].direction is VersionDirection.UPGRADE
    assert changes["DOWN"].direction is VersionDirection.DOWNGRADE
    assert changes["UP"].reason is changes["DOWN"].reason is None


@pytest.mark.parametrize("orders,reason", [
    ((None, 5), ChangeReason.ORDER_UNKNOWN),
    ((1, 1), ChangeReason.ORDER_CONFLICT),
])
def test_unconfirmed_or_conflicting_order_does_not_hide_observed_change(revision_storage, orders, reason):
    storage = revision_storage
    before_id = release(storage, raw="v1", order=orders[0])
    after_id = release(storage, raw="v2", order=orders[1])
    observation(storage, day=EARLY, versions=((1, 1, "v1", before_id),))
    observation(storage, day=LATE, versions=((1, 1, "v2", after_id),))

    change, = HistoryService(storage).compare(EARLY, LATE).changed

    assert change.direction is VersionDirection.UNKNOWN
    assert change.reason is reason


def test_swapped_versions_compare_each_fixed_pm_even_when_version_set_is_same(revision_storage):
    storage = revision_storage
    observation(storage, day=EARLY, versions=((1, 1, "v1", None), (1, 1, "v2", None)), pms=True)
    observation(storage, day=LATE, versions=((1, 1, "v2", None), (1, 1, "v1", None)), pms=True)

    result = HistoryService(storage).compare(EARLY, LATE)

    by_pm = {change.pm_name: change for change in result.changed}
    assert set(by_pm) == {"PM1", "PM2"}
    assert [(by_pm[name].before.software.version_raw, by_pm[name].after.software.version_raw)
            for name in ("PM1", "PM2")] == [("v1", "v2"), ("v2", "v1")]
    assert all(change.before.pm.pm_id != change.after.pm.pm_id for change in by_pm.values())
    assert len(result.changed_equipment_ids) == 1


def test_shared_software_keeps_each_pm_position_in_comparison(revision_storage):
    storage = revision_storage
    for day, raw in ((EARLY, "v1"), (LATE, "v2")):
        snapshot_id, versions = observation(storage, day=day, versions=((1, 1, raw, None),), pms=True)
        with storage.transaction() as tx:
            tx.insert("equipment_pm", {"snapshot_id": snapshot_id,
                "equipment_version_id": versions[0]["equipment_version_id"], "pm_name": "PM3",
                "group_id": 1, "batch_id": versions[0]["batch_id"]})

    result = HistoryService(storage).compare(EARLY, LATE)

    assert {change.pm_name for change in result.changed} == {"PM1", "PM3"}
    assert len(result.changed_equipment_ids) == 1
    assert len({change.before.software.equipment_version_id for change in result.changed}) == 1
    assert len({change.after.software.equipment_version_id for change in result.changed}) == 1


def test_ordinary_software_is_matched_by_family_not_row_order(revision_storage):
    storage = revision_storage
    with storage.transaction() as tx:
        tx.insert("version_family", {"family_id": 3, "group_id": 1, "family_name": "별도 SW"})
    observation(storage, day=EARLY, versions=((1, 1, "v1", None), (3, 1, "engine1", None)))
    observation(storage, day=LATE, versions=((3, 1, "engine1", None), (1, 1, "v2", None)))

    result = HistoryService(storage).compare(EARLY, LATE)

    change, = result.changed
    assert change.pm_name is None
    assert change.before.software.family_id == change.after.software.family_id == 1
    stable, = [entry for entry in result.changes if entry.status is ChangeStatus.UNCHANGED]
    assert stable.before.software.family_id == stable.after.software.family_id == 3


def test_absent_date_never_uses_latest_or_previous_observation(revision_storage):
    storage = revision_storage
    observation(storage, day=EARLY, versions=((1, 1, "v1", None),), pms=True)
    observation(storage, day=LATE, versions=((1, 1, "v2", None),), pms=True)

    missing_after = HistoryService(storage).compare(EARLY, GAP)
    missing_before = HistoryService(storage).compare(GAP, LATE)

    assert missing_after.after == HistoryFrame(GAP)
    assert missing_before.before == HistoryFrame(GAP)
    assert missing_after.changed == missing_before.changed == ()
    assert all(entry.status is ChangeStatus.UNDETERMINED for entry in missing_after.changes + missing_before.changes)
    assert {entry.reason for entry in missing_after.changes} == {ChangeReason.MISSING_AFTER_SNAPSHOT}
    assert {entry.reason for entry in missing_before.changes} == {ChangeReason.MISSING_BEFORE_SNAPSHOT}


def test_missing_pm_position_is_not_a_version_change(revision_storage):
    storage = revision_storage
    observation(storage, day=EARLY, versions=((1, 1, "v1", None), (1, 1, "v2", None)), pms=True)
    observation(storage, day=LATE, versions=((1, 1, "v1", None),), pms=True)

    result = HistoryService(storage).compare(EARLY, LATE)

    missing, = [entry for entry in result.changes if entry.pm_name == "PM2"]
    assert missing.status is ChangeStatus.UNDETERMINED
    assert missing.reason is ChangeReason.MISSING_AFTER_POSITION
    assert missing.after is None
    assert result.changed == ()


def test_family_change_at_same_pm_is_not_ordered_across_families(revision_storage):
    storage = revision_storage
    observation(storage, day=EARLY, versions=((1, 1, "v1", None),), pms=True)
    observation(storage, day=LATE, versions=((2, 3, "c2", None),), pms=True)

    result = HistoryService(storage).compare(EARLY, LATE)

    change, = result.changes
    assert change.pm_name == "PM1"
    assert change.status is ChangeStatus.UNDETERMINED
    assert change.reason is ChangeReason.FAMILY_CHANGED
    assert change.direction is VersionDirection.UNKNOWN
    assert result.changed == ()


def test_pm_tracking_change_is_not_joined_to_ordinary_software(revision_storage):
    storage = revision_storage
    observation(storage, day=EARLY, versions=((1, 1, "v1", None),))
    observation(storage, day=LATE, versions=((1, 1, "v2", None),), pms=True)

    result = HistoryService(storage).compare(EARLY, LATE)

    assert result.changed == ()
    assert result.changes
    assert all(entry.status is ChangeStatus.UNDETERMINED for entry in result.changes)
    assert all(entry.reason is ChangeReason.TRACKING_CHANGED for entry in result.changes)


def test_scope_filters_actual_pm_pair_and_retains_historical_metadata(revision_storage):
    storage = revision_storage
    early_id, _ = observation(storage, day=EARLY,
        versions=((1, 1, "v1", None), (2, 3, "c1", None)), pms=True)
    late_id, _ = observation(storage, day=LATE,
        versions=((1, 1, "v2", None), (2, 3, "c2", None)), pms=True)
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": early_id}, {"line": "과거 라인", "geometry_type": "5각"})
        tx.update("equipment_snapshot", {"snapshot_id": late_id}, {"line": "새 라인", "geometry_type": "6각"})

    result = HistoryService(storage).compare(EARLY, LATE, QueryScope(model_ids=(1,), family_ids=(1,)))

    change, = result.changed
    assert change.pm_name == "PM1"
    assert (change.before.snapshot.line, change.after.snapshot.line) == ("과거 라인", "새 라인")
    assert (change.before.snapshot.geometry_type, change.after.snapshot.geometry_type) == ("5각", "6각")
    assert change.before.software.source_reference == {"version_raw": {"sheet": "현황", "cells": ["B1"]}}
    assert len(result.before.snapshots[0].software) == 2
    assert len(result.after.snapshots[0].software) == 2
    cross_pair = HistoryService(storage).compare(EARLY, LATE, QueryScope(model_ids=(1,), family_ids=(2,)))
    assert cross_pair.changes == ()


def test_scope_entry_is_not_counted_as_existing_equipment_version_change(revision_storage):
    storage = revision_storage
    observation(storage, day=EARLY, versions=((1, 1, "v1", None),))
    late, _ = observation(storage, day=LATE, versions=((1, 1, "v2", None),))
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": late}, {"line": "새 라인"})

    result = HistoryService(storage).compare(EARLY, LATE, QueryScope(lines=("새 라인",)))

    assert not result.before.has_data and result.after.has_data
    assert result.changed == result.changed_equipment_ids == ()


def test_pm_model_leaving_scope_is_not_counted_as_a_patch(revision_storage):
    storage = revision_storage
    observation(storage, day=EARLY, versions=((1, 1, "v1", None), (1, 2, "v2", None)), pms=True)
    observation(storage, day=LATE, versions=((1, 2, "v3", None), (1, 1, "v4", None)), pms=True)

    result = HistoryService(storage).compare(EARLY, LATE, QueryScope(model_ids=(1,)))

    assert result.before.has_data and result.after.has_data
    assert result.changed == ()
    assert {change.reason for change in result.changes} == {
        ChangeReason.OUTSIDE_BEFORE_SCOPE, ChangeReason.OUTSIDE_AFTER_SCOPE,
    }
    assert all(change.status is ChangeStatus.UNDETERMINED for change in result.changes)


def test_comparison_preserves_all_saved_observations_and_sources(revision_storage):
    storage = revision_storage
    observation(storage, day=EARLY, versions=((1, 1, "v1", None),), pms=True)
    observation(storage, day=LATE, versions=((1, 1, "v2", None),), pms=True)
    tables = ("equipment", "equipment_snapshot", "equipment_version", "equipment_pm", "import_batch", "release_version", "app_setting")
    before = {table: all_rows(storage, table) for table in tables}

    HistoryService(storage).compare(EARLY, LATE)

    assert {table: all_rows(storage, table) for table in tables} == before


def test_pure_comparison_uses_only_confirmed_aliases(revision_storage):
    storage = revision_storage
    observation(storage, day=EARLY, versions=((1, 1, "v999.3.7", None),))
    observation(storage, day=LATE, versions=((1, 1, "v999.3_7", None),))
    service = HistoryService(storage)
    before, after = service.read(QueryScope(EARLY)), service.read(QueryScope(LATE))
    family = FamilyReleases(1, (ReleaseVersion(1, 1, "v999.3.7", 1),), {"v999.3_7": "v999.3.7"})

    raw_result = compare_frames(before, after)
    confirmed = compare_frames(before, after, {1: family})

    assert len(raw_result.changed) == 1
    assert confirmed.changed == ()
    assert confirmed.changes[0].status is ChangeStatus.UNCHANGED


@pytest.mark.parametrize("before,after", [
    (EARLY, EARLY), (LATE, EARLY), (None, LATE), (EARLY, "2026-11-01"),
    (datetime(2026, 10, 1), LATE),
])
def test_comparison_requires_two_explicit_chronological_dates(revision_storage, before, after):
    with pytest.raises(StorageValidationError):
        HistoryService(revision_storage).compare(before, after)


@pytest.mark.parametrize("scope", [
    QueryScope(family_ids=(1,)), QueryScope(model_ids=(1,)), QueryScope(categories=("OXIDE",)),
])
def test_unrelated_whitespace_colliding_releases_do_not_block_comparison(revision_storage, scope):
    storage = revision_storage
    observation(storage, day=EARLY, versions=((1, 1, "ordinary-v1", None),))
    observation(storage, day=LATE, versions=((1, 1, "ordinary-v2", None),))
    # 원문 보존 정책상 등록 서비스는 공백까지 다른 두 릴리즈를 저장할 수 있다.
    release(storage, family=2, raw="v1", order=1)
    release(storage, family=2, raw=" v1", order=2)

    result = HistoryService(storage).compare(EARLY, LATE, scope)

    change, = result.changed
    assert change.direction is VersionDirection.UNKNOWN
    assert (change.before.software.version_raw, change.after.software.version_raw) == (
        "ordinary-v1", "ordinary-v2",
    )
    assert change.before.software.family_id == change.after.software.family_id == 1


def test_ambiguous_release_notation_keeps_raw_change_with_unknown_direction(revision_storage):
    storage = revision_storage
    release(storage, raw="v1", order=1)
    release(storage, raw=" v1", order=2)
    release(storage, raw="v2", order=3)
    # 'v1 '은 두 릴리즈 중 어느 것인지 확인되지 않았으며 저장 원문은 그대로 남긴다.
    observation(storage, day=EARLY, versions=((1, 1, "v1 ", None),))
    observation(storage, day=LATE, versions=((1, 1, "v2", None),))

    change, = HistoryService(storage).compare(EARLY, LATE).changed

    assert change.direction is VersionDirection.UNKNOWN
    assert change.raw_changed is True
    assert change.before.software.version_raw == "v1 "
    assert change.after.software.version_raw == "v2"


def test_identical_raw_stays_unchanged_despite_ambiguous_release_notation(revision_storage):
    storage = revision_storage
    release(storage, raw="v1", order=1)
    release(storage, raw=" v1", order=2)
    for day in (EARLY, LATE):
        observation(storage, day=day, versions=((1, 1, "v1 ", None),))

    result = HistoryService(storage).compare(EARLY, LATE)

    change, = result.changes
    assert change.status is ChangeStatus.UNCHANGED
    assert change.direction is VersionDirection.UNCHANGED
    assert result.changed == ()


def test_confirmed_same_link_survives_whitespace_colliding_release_catalog(revision_storage):
    storage = revision_storage
    confirmed = release(storage, raw="v1", order=1)
    release(storage, raw=" v1", order=2)
    observation(storage, day=EARLY, versions=((1, 1, "v1", confirmed),))
    observation(storage, day=LATE, versions=((1, 1, "확인한 별도 표기", confirmed),))

    result = HistoryService(storage).compare(EARLY, LATE)

    change, = result.changes
    assert change.status is ChangeStatus.UNCHANGED
    assert change.raw_changed is True
    assert result.changed == ()


def test_unique_release_identity_survives_another_notations_collision(revision_storage):
    storage = revision_storage
    release(storage, raw="v1", order=1)
    release(storage, raw=" v1", order=2)
    confirmed = release(storage, raw="v2", order=3)
    observation(storage, day=EARLY, versions=((1, 1, "확인한 별도 표기", confirmed),))
    observation(storage, day=LATE, versions=((1, 1, "v2", None),))

    result = HistoryService(storage).compare(EARLY, LATE)

    change, = result.changes
    assert change.status is ChangeStatus.UNCHANGED
    assert change.direction is VersionDirection.UNCHANGED
    assert change.raw_changed is True
    assert change.before.software.release_version_id == confirmed
    assert change.after.software.release_version_id is None
    assert result.changed == ()


@pytest.mark.parametrize("before,after,reason", [
    (EARLY, GAP, ChangeReason.MISSING_AFTER_SNAPSHOT),
    (GAP, LATE, ChangeReason.MISSING_BEFORE_SNAPSHOT),
])
def test_missing_date_remains_undetermined_with_ambiguous_release_catalog(revision_storage, before, after, reason):
    storage = revision_storage
    release(storage, raw="v1", order=1)
    release(storage, raw=" v1", order=2)
    observation(storage, day=EARLY, versions=((1, 1, "v1", None),))
    observation(storage, day=LATE, versions=((1, 1, "v2", None),))

    result = HistoryService(storage).compare(before, after)

    change, = result.changes
    assert change.status is ChangeStatus.UNDETERMINED
    assert change.reason is reason
    assert (change.before_date, change.after_date) == (before, after)
    assert result.changed == ()


def test_empty_dates_return_empty_comparison_despite_ambiguous_release_catalog(revision_storage):
    storage = revision_storage
    release(storage, raw="v1", order=1)
    release(storage, raw=" v1", order=2)

    result = HistoryService(storage).compare(EARLY, LATE)

    assert not result.before.has_data and not result.after.has_data
    assert result.changes == result.changed == result.changed_equipment_ids == ()
