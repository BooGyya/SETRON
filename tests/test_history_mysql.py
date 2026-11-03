"""BE-013 정확한 날짜·고정 PM별 변경 판정을 실제 MySQL 5.6.19에서 검증한다."""

import os
from datetime import date
from uuid import uuid4

import pytest

from setron.features.equipment.repository import EquipmentRepository
from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.history import HistoryService
from setron.features.query_options import QueryScope
from setron.features.release.schemas import ReleaseInput
from setron.features.release.service import ReleaseService

from test_import_save_mysql import storage  # noqa: F401  시연 기준 자료를 적용하는 전용 DB fixture
from test_schema_mysql import schema_database  # noqa: F401  실제 스키마 생성·이번 테이블만 정리


pytestmark = pytest.mark.skipif(
    not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.",
)

EARLY, GAP, LATE = date(2026, 10, 1), date(2026, 10, 2), date(2026, 11, 1)


@pytest.fixture
def history_catalog(storage):
    """테스트마다 별도 모델·계열·확인된 릴리즈를 실제 스키마에 등록한다."""
    token = uuid4().hex[:12]
    groups, families, releases = {}, {}, {}
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL"})
        for name in ("O2", "O3"):
            groups[name] = tx.insert("equipment_group", {
                "group_name": f"BE013-{name}-{token}", "category": "OXIDE",
            })["group_id"]
            families[name] = tx.insert("version_family", {
                "group_id": groups[name], "family_name": f"BE013-{name}-{token}",
            })["family_id"]
            for order in (1, 2):
                releases[name, f"v{order}"] = tx.insert("release_version", {
                    "family_id": families[name], "version_raw": f"v{order}", "version_order": order,
                    "batch_id": batch["batch_id"],
                })["release_version_id"]
    return storage, f"BE013-{token}", groups, families, releases


def save_observation(catalog, day, values, *, line="과거 Line", model="당시 혼합 모델", geometry="5각", modification=None):
    """고정 PM·원본 셀을 가진 한 날짜의 현황을 실제 설비 저장 서비스로 등록한다."""
    storage, code, groups, families, releases = catalog
    sheet = day.isoformat()
    cells = {"A1": code, "B1": line, "C1": model, "D1": geometry}
    for number, (pm_name, _, raw, _) in enumerate(values, 1):
        cells[f"H{number}"] = pm_name
        cells[f"I{number}"] = raw
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": {sheet: cells}})

    def source(field, cell):
        return {field: {"sheet": sheet, "cells": [cell]}}

    snapshot = SnapshotInput(
        equipment_code=code, base_date=day, site="화성", line=line, model_name=model,
        geometry_type=geometry, modification_type=modification, batch_id=batch["batch_id"],
        source_reference={**source("equipment_code", "A1"), **source("line", "B1"), **source("model_name", "C1")},
        software=tuple(SoftwareInput(
            family_id=families[family], version_raw=raw,
            release_version_id=releases[family, release_raw] if release_raw else None,
            source_reference=source("version_raw", f"I{number}"),
            pms=(PmInput(pm_name=pm_name, group_id=groups[family], model_name_raw=family,
                         source_reference=source("pm_name", f"H{number}")),),
        ) for number, (pm_name, family, raw, release_raw) in enumerate(values, 1)),
    )
    saved, = EquipmentService(storage).save_snapshots((snapshot,))
    return saved


def scope(catalog, day=None, **filters):
    """같은 전용 DB의 다른 테스트 현황이 섞이지 않도록 이번 계열로 한정한다."""
    return QueryScope(day, family_ids=tuple(catalog[3].values()), **filters)


def managed_rows(storage):
    """이력 조회 전후의 원본·PM·설정 보존을 실제 저장 행으로 비교한다."""
    with storage.transaction() as tx:
        repository = EquipmentRepository()
        return {name: repository.rows(tx, name) for name in (
            "equipment", "equipment_snapshot", "equipment_version", "equipment_pm", "import_batch", "app_setting",
            "release_version",
        )}


def test_mysql_history_preserves_exact_dates_historical_metadata_and_sources(history_catalog):
    storage, *_ = history_catalog
    first = save_observation(history_catalog, EARLY, (("PM1", "O2", " v1 원문 ", None),))
    later = save_observation(history_catalog, LATE, (("PM1", "O2", "v2", "v2"),),
                             line="변경 Line", model="개조 후 모델", geometry="6각", modification="CIP")
    service = HistoryService(storage)
    original = service.read(scope(history_catalog, EARLY)).snapshots
    assert original == (first,)
    assert original[0].line == "과거 Line" and original[0].geometry_type == "5각"
    assert original[0].modification_type is None
    assert original[0].software[0].version_raw == " v1 원문 "
    assert original[0].software[0].source_reference == {
        "version_raw": {"sheet": EARLY.isoformat(), "cells": ["I1"]},
    }
    assert service.read(scope(history_catalog, LATE)).snapshots == (later,)
    replay = service.replay((LATE, GAP, EARLY, LATE), scope(history_catalog))
    assert [frame.base_date for frame in replay.frames] == [EARLY, GAP, LATE]
    assert [frame.snapshots for frame in replay.frames] == [(first,), (), (later,)]
    assert replay.can_replay and replay.message is None


def test_mysql_compare_connects_fixed_pm_names_when_version_sets_are_swapped(history_catalog):
    storage, *_ = history_catalog
    before = save_observation(history_catalog, EARLY, (("PM1", "O2", "v1", "v1"), ("PM2", "O2", "v2", "v2")))
    after = save_observation(history_catalog, LATE, (("PM1", "O2", "v2", "v2"), ("PM2", "O2", "v1", "v1")))
    old_ids = {pm.pm_id for sw in before.software for pm in sw.pms}
    new_ids = {pm.pm_id for sw in after.software for pm in sw.pms}
    assert old_ids.isdisjoint(new_ids)
    result = HistoryService(storage).compare(EARLY, LATE, scope(history_catalog))
    changes = {change.pm_name: change for change in result.changes}
    assert set(changes) == {"PM1", "PM2"}
    assert len(result.changed) == 2 and result.changed_equipment_ids == (before.equipment_id,)
    assert changes["PM1"].direction.value == "UPGRADE"
    assert changes["PM2"].direction.value == "DOWNGRADE"
    for name, change in changes.items():
        assert change.status.value == "CHANGED"
        assert change.before.pm.pm_name == change.after.pm.pm_name == name
        assert change.before.pm.pm_id != change.after.pm.pm_id
        assert (change.before_date, change.after_date) == (EARLY, LATE)
        assert change.before.snapshot.snapshot_id == before.snapshot_id
        assert change.after.snapshot.snapshot_id == after.snapshot_id


def test_mysql_compare_unregistered_changes_and_confirmed_same_release_are_distinct(history_catalog):
    storage, *_ = history_catalog
    save_observation(history_catalog, EARLY, (("PM1", "O2", "미등록 원문 A", None),
                                              ("PM2", "O3", "확인한 표기 A", "v1")))
    save_observation(history_catalog, LATE, (("PM1", "O2", "미등록 원문 B", None),
                                             ("PM2", "O3", "확인한 표기 B", "v1")))
    changes = {change.pm_name: change for change in HistoryService(storage).compare(EARLY, LATE, scope(history_catalog)).changes}
    assert changes["PM1"].status.value == "CHANGED" and changes["PM1"].direction.value == "UNKNOWN"
    assert changes["PM1"].before.software.release_version_id is None
    assert changes["PM2"].status.value == "UNCHANGED" and changes["PM2"].direction.value == "UNCHANGED"
    assert changes["PM2"].before.software.version_raw == "확인한 표기 A"
    assert changes["PM2"].after.software.version_raw == "확인한 표기 B"


def test_mysql_missing_snapshot_cannot_become_a_version_change(history_catalog):
    storage, *_ = history_catalog
    save_observation(history_catalog, EARLY, (("PM1", "O2", "v1", "v1"),))
    service = HistoryService(storage)
    result = service.compare(EARLY, GAP, scope(history_catalog))
    assert result.before.has_data and not result.after.has_data
    assert result.after.snapshots == () and result.changed == result.changed_equipment_ids == ()
    assert result.changes and all(change.status.value == "UNDETERMINED" for change in result.changes)
    assert all(change.direction.value == "UNKNOWN" for change in result.changes)
    replay = service.replay((EARLY, GAP, LATE), scope(history_catalog))
    assert not replay.can_replay and replay.message


def test_mysql_filters_use_each_dates_actual_line_without_modifying_saved_data(history_catalog):
    storage, *_ = history_catalog
    save_observation(history_catalog, EARLY, (("PM1", "O2", "v1", "v1"),))
    save_observation(history_catalog, LATE, (("PM1", "O2", "v2", "v2"),), line="새 Line")
    before = managed_rows(storage)
    service = HistoryService(storage)
    historical = scope(history_catalog, lines=("과거 Line",))
    result = service.compare(EARLY, LATE, historical)
    assert result.before.has_data and not result.after.has_data
    assert not result.changed
    replay = service.replay((EARLY, GAP, LATE), historical)
    assert [frame.has_data for frame in replay.frames] == [True, False, False]
    service.read(scope(history_catalog, LATE))
    assert managed_rows(storage) == before


def test_mysql_unrelated_whitespace_collision_does_not_block_scoped_or_missing_date_comparison(history_catalog):
    """다른 계열에 저장 가능한 표기 충돌이 있어도 선택 계열의 변경·미관측을 반환한다."""
    storage, _, _, families, _ = history_catalog
    ReleaseService(storage).register_release(ReleaseInput(
        family_id=families["O3"], version_raw=" v1", version_order=3,
    ))
    first = save_observation(history_catalog, EARLY, (("PM1", "O2", "unregistered-a", None),))
    last = save_observation(history_catalog, LATE, (("PM1", "O2", "unregistered-b", None),))
    selected = QueryScope(family_ids=(families["O2"],))
    service = HistoryService(storage)
    original = managed_rows(storage)

    change, = service.compare(EARLY, LATE, selected).changed

    assert change.status.value == "CHANGED" and change.direction.value == "UNKNOWN"
    assert change.before.snapshot == first and change.after.snapshot == last
    assert (change.before.software.version_raw, change.after.software.version_raw) == ("unregistered-a", "unregistered-b")
    missing = service.compare(EARLY, GAP, selected)
    assert not missing.after.has_data and missing.changed == ()
    assert all(entry.status.value == "UNDETERMINED" for entry in missing.changes)
    assert managed_rows(storage) == original


def test_mysql_compared_whitespace_collision_preserves_changes_same_links_and_raws(history_catalog):
    """대응을 정할 수 없는 표기는 변경 방향만 보류하고, 동일 원문·확정 연결은 유지한다."""
    storage, _, _, families, _ = history_catalog
    ReleaseService(storage).register_release(ReleaseInput(
        family_id=families["O2"], version_raw=" v1", version_order=3,
    ))
    save_observation(history_catalog, EARLY, (
        ("PM1", "O2", "v1 ", None),
        ("PM2", "O2", "확인한 이전 표기", "v1"),
        ("PM3", "O2", "동일 미등록 원문", None),
        ("PM4", "O2", "verified alias v2", "v2"),
    ))
    save_observation(history_catalog, LATE, (
        ("PM1", "O2", "v2", None),
        ("PM2", "O2", "확인한 이후 표기", "v1"),
        ("PM3", "O2", "동일 미등록 원문", None),
        ("PM4", "O2", "v2", None),
    ))
    selected = QueryScope(family_ids=(families["O2"],))
    original = managed_rows(storage)

    result = HistoryService(storage).compare(EARLY, LATE, selected)

    by_pm = {change.pm_name: change for change in result.changes}
    assert len(result.changed) == 1 and result.changed[0].pm_name == "PM1"
    assert by_pm["PM1"].direction.value == "UNKNOWN"
    assert by_pm["PM1"].before.software.version_raw == "v1 "
    assert by_pm["PM1"].after.software.version_raw == "v2"
    assert by_pm["PM2"].status.value == by_pm["PM3"].status.value == by_pm["PM4"].status.value == "UNCHANGED"
    assert by_pm["PM2"].before.software.release_version_id == by_pm["PM2"].after.software.release_version_id
    assert by_pm["PM2"].before.software.version_raw == "확인한 이전 표기"
    assert by_pm["PM2"].after.software.version_raw == "확인한 이후 표기"
    assert by_pm["PM4"].before.software.version_raw == "verified alias v2"
    assert by_pm["PM4"].before.software.release_version_id is not None
    assert by_pm["PM4"].after.software.version_raw == "v2"
    assert by_pm["PM4"].after.software.release_version_id is None
    assert managed_rows(storage) == original


@pytest.mark.parametrize("confirmed_raw,alias_first", [
    ("v1", True), ("v1", False), (" v1", True), (" v1", False),
])
def test_mysql_exact_release_identity_survives_normalized_collision(history_catalog, confirmed_raw, alias_first):
    """공백까지 정확히 일치하는 원문은 확인된 ID와 같은 버전으로 유지한다."""
    storage, code, groups, families, releases = history_catalog
    registered = ReleaseService(storage).register_release(ReleaseInput(
        family_id=families["O2"], version_raw=" v1", version_order=3,
    ))
    release_ids = {**releases, ("O2", " v1"): registered.release.current["release_version_id"]}
    catalog = storage, code, groups, families, release_ids
    alias, canonical = ("PM1", "O2", "확인된 별도 표기", confirmed_raw), ("PM1", "O2", confirmed_raw, None)
    first = save_observation(catalog, EARLY, (alias if alias_first else canonical,))
    last = save_observation(catalog, LATE, (canonical if alias_first else alias,))
    original = managed_rows(storage)

    result = HistoryService(storage).compare(EARLY, LATE, QueryScope(family_ids=(families["O2"],)))

    change, = result.changes
    assert change.status.value == change.direction.value == "UNCHANGED"
    assert change.raw_changed is True
    assert result.changed == result.changed_equipment_ids == ()
    assert change.before.snapshot == first and change.after.snapshot == last
    assert change.before.software.release_version_id == (release_ids["O2", confirmed_raw] if alias_first else None)
    assert change.after.software.release_version_id == (None if alias_first else release_ids["O2", confirmed_raw])
    assert managed_rows(storage) == original


def test_mysql_exact_whitespace_distinct_releases_remain_distinct(history_catalog):
    """정확한 두 원문을 공백 제거로 같은 버전으로 합치지 않고, 미확인 표기는 보류한다."""
    storage, _, _, families, _ = history_catalog
    ReleaseService(storage).register_release(ReleaseInput(
        family_id=families["O2"], version_raw=" v1", version_order=3,
    ))
    first = save_observation(history_catalog, EARLY, (
        ("PM1", "O2", "v1", None), ("PM2", "O2", "v1 ", None),
    ))
    last = save_observation(history_catalog, LATE, (
        ("PM1", "O2", " v1", None), ("PM2", "O2", "v2", None),
    ))
    original = managed_rows(storage)

    result = HistoryService(storage).compare(EARLY, LATE, QueryScope(family_ids=(families["O2"],)))

    by_pm = {change.pm_name: change for change in result.changes}
    assert set(by_pm) == {"PM1", "PM2"}
    assert all(change.status.value == "CHANGED" for change in result.changes)
    assert by_pm["PM1"].before.software.version_raw == "v1"
    assert by_pm["PM1"].after.software.version_raw == " v1"
    assert by_pm["PM2"].direction.value == "UNKNOWN"
    assert result.changed_equipment_ids == (first.equipment_id,)
    assert all(change.before.snapshot == first and change.after.snapshot == last for change in result.changes)
    assert managed_rows(storage) == original
