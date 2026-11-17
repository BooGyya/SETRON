"""자동 병합이 실제 저장값·원본·묶음 구성과 사용자 재선택을 보존한다."""

from copy import deepcopy
import json

import pytest

from setron.features.peer_sync import read_state
from setron.features.peer_sync import Baseline
from setron.features.db_import.automatic import ReviewAutomation
from test_peer_exchange import PEERS, choose_all, delete_snapshot, exchange_file, peers, stamp  # noqa: F401
from test_peer_auto_exchange import edit, shared, snapshot
from test_sql_import_service import bundle, current
from test_db_import_group_settings import _rows as group_rows


def child_version(peer, value):
    with peer[0].begin() as connection:
        connection.execute(peer[1]["equipment_version"].update().values(version_raw=value))


def test_user_can_restore_automatic_decision_after_overriding_it(peers, tmp_path):
    local, remote = shared(peers, tmp_path)
    edit(remote, line="다른 PC 변경")
    stamp(remote, PEERS[1])
    service = local[2]
    plan = service.preview(exchange_file(remote, tmp_path / "remote.sql"), "review")
    item, = [item for item in plan.differences if item.field == "line"]
    plan = service.resolve(plan, {item.id: "CURRENT"})
    assert not plan.differences[0].automatic and plan.choices[item.id] == "CURRENT"
    plan = service.resolve(plan, {item.id: "AUTO"})
    chosen, = [value for value in plan.differences if value.id == item.id]
    assert chosen.automatic and chosen.choice == "INCOMING" and item.id not in plan.choices
    service.apply(plan)
    assert snapshot(local)["line"] == "다른 PC 변경"


def test_auto_reset_rejects_unknown_review_item(peers, tmp_path):
    local, remote = shared(peers, tmp_path)
    edit(remote, line="다른 PC 변경")
    stamp(remote, PEERS[1])
    plan = local[2].preview(exchange_file(remote, tmp_path / "remote.sql"), "review")
    with pytest.raises(ValueError, match="검토 목록"):
        local[2].resolve(plan, {"unknown-review-item": "AUTO"})


@pytest.mark.parametrize("side", ["local", "remote"])
def test_pm_software_only_one_side_changed_selects_the_whole_group(peers, tmp_path, side):
    local, remote = shared(peers, tmp_path)
    child_version(local if side == "local" else remote, b"v2")
    stamp(remote, PEERS[1])
    plan = local[2].preview(exchange_file(remote, tmp_path / "pm.sql"), "review")
    group, = [value for value in plan.differences if value.field == "PM·SW 구성"]
    assert group.has_baseline and group.automatic
    assert group.choice == ("CURRENT" if side == "local" else "INCOMING")
    local[2].apply(plan)
    version, = current(local)["equipment_version"]
    pm, = current(local)["equipment_pm"]
    assert version["version_raw"] == b"v2" and pm["equipment_version_id"] == version["equipment_version_id"]
    decisions = [receipt.get("decisions", {}).get("PM·SW 구성")
                 for event in read_state(current(local)).events.values()
                 for receipt in event.get("selection", {}).get("entities", {}).values()]
    assert any(item and item["automatic"] and item["baseline_revisions"] for item in decisions)


def test_divergent_pm_software_changes_are_an_atomic_manual_conflict(peers, tmp_path):
    local, remote = shared(peers, tmp_path)
    child_version(local, b"local-v2")
    child_version(remote, b"remote-v2")
    stamp(remote, PEERS[1])
    service = local[2]
    plan = service.preview(exchange_file(remote, tmp_path / "pm-conflict.sql"), "review")
    group, = [value for value in plan.differences if value.field == "PM·SW 구성"]
    assert group.has_baseline and not group.automatic and group.choice is None
    before = deepcopy(current(local))
    with pytest.raises(ValueError, match="선택"):
        service.apply(plan)
    assert current(local) == before
    service.apply(service.resolve(plan, {group.id: "INCOMING"}))
    assert current(local)["equipment_version"][0]["version_raw"] == b"remote-v2"


def test_unexported_local_added_pm_is_never_omitted_from_group_decision(peers, tmp_path):
    """UUID를 아직 배정받지 않은 로컬 PM을 상대의 자동 묶음 교체로 삭제하지 않는다."""
    local, remote = shared(peers, tmp_path)
    with local[0].begin() as connection:
        connection.execute(local[1]["equipment_version"].insert().values(
            equipment_version_id=2, snapshot_id=1, family_id=1, version_raw=b"local-added",
            release_version_id=None, batch_id=1, source_reference=None))
        connection.execute(local[1]["equipment_pm"].insert().values(
            pm_id=2, snapshot_id=1, equipment_version_id=2, pm_name=b"PM2", group_id=1,
            model_name_raw="O2", batch_id=1, source_reference=None))
    child_version(remote, b"remote-new")
    stamp(remote, PEERS[1])
    service = local[2]
    plan = service.preview(exchange_file(remote, tmp_path / "new-pm.sql"), "review")
    group, = [value for value in plan.differences if value.field == "PM·SW 구성"]
    assert not group.automatic and group.choice is None
    assert {row["pm_name"] for row in current(local)["equipment_pm"]} == {b"PM1", b"PM2"}


def test_local_deleted_snapshot_conflicts_with_a_new_remote_child_revision(peers, tmp_path):
    """현황 공통값이 같아도 새 SW 변경과 로컬 삭제가 겹치면 복원을 묻는다."""
    local, remote = shared(peers, tmp_path)
    delete_snapshot(local)
    child_version(remote, b"new-remote-version")
    stamp(remote, PEERS[1])
    plan = local[2].preview(exchange_file(remote, tmp_path / "deleted-vs-child.sql"), "review")
    assert plan.unresolved > 0
    assert any(item.field == "삭제된 항목 복원" and item.choice is None for item in plan.differences)
    assert not current(local)["equipment_snapshot"]


@pytest.mark.parametrize("choice", ["CURRENT", "INCOMING"])
def test_incoming_pm_deletion_needs_confirmation_even_with_a_proven_group_baseline(peers, tmp_path, choice):
    local, remote = shared(peers, tmp_path)
    with remote[0].begin() as connection:
        connection.execute(remote[1]["equipment_pm"].delete())
    stamp(remote, PEERS[1])
    service = local[2]
    path = exchange_file(remote, tmp_path / "removed-pm.sql")
    plan = service.preview(path, "review")
    group, = [item for item in plan.differences if item.field == "PM·SW 구성"]
    assert group.has_baseline and group.choice is None and not group.automatic
    assert group.requires_confirmation and "개별 확인" in group.reason
    service.apply(service.resolve(plan, {group.id: choice}))
    assert len(current(local)["equipment_pm"]) == (choice == "CURRENT")
    assert service.preview(path, "review").unresolved == 0


def test_equal_count_pm_replacement_is_also_an_incoming_deletion(peers, tmp_path):
    local, remote = shared(peers, tmp_path)
    with remote[0].begin() as connection:
        table = remote[1]["equipment_pm"]
        previous = dict(connection.execute(table.select()).mappings().one())
        connection.execute(table.delete())
        connection.execute(table.insert().values(**{**previous, "pm_id": 2, "pm_name": b"PM2"}))
    stamp(remote, PEERS[1])
    plan = local[2].preview(exchange_file(remote, tmp_path / "replaced-pm.sql"), "review")
    group, = [item for item in plan.differences if item.field == "PM·SW 구성"]
    assert group.requires_confirmation and group.choice is None and not group.automatic


def test_auto_merge_keeps_portable_fk_comparison_when_local_ids_differ(peers, tmp_path):
    source = peers("A", initial=bundle(line="공통"))
    destination = peers("B", initial=bundle(code=b"OTHER", group="O3"))
    stamp(source, PEERS[0])
    first = exchange_file(source, tmp_path / "first.sql")
    destination[2].apply(choose_all(destination[2], destination[2].preview(first, "review")))
    with source[0].begin() as connection:
        connection.execute(source[1]["equipment_snapshot"].update().values(line="파일 새 라인"))
    stamp(source, PEERS[0])
    plan = destination[2].preview(exchange_file(source, tmp_path / "new.sql"), "review")
    line, = [value for value in plan.differences if value.field == "line"]
    assert line.automatic and line.has_baseline and line.choice == "INCOMING" and line.baseline == "공통"
    destination[2].apply(plan)
    rows = current(destination)
    code = next(row for row in rows["equipment"] if row["equipment_code"] == b"EQ-1")
    assert code["equipment_id"] != 1
    saved = next(row for row in rows["equipment_snapshot"] if row["equipment_id"] == code["equipment_id"])
    assert saved["line"] == "파일 새 라인"


def test_automatic_choices_are_audited_with_baseline_and_classification(peers, tmp_path):
    local, remote = shared(peers, tmp_path)
    edit(remote, line="자동 변경")
    stamp(remote, PEERS[1])
    plan = local[2].preview(exchange_file(remote, tmp_path / "audit.sql"), "review")
    local[2].apply(plan)
    receipts = [receipt for event in read_state(current(local)).events.values()
                for receipt in event.get("selection", {}).get("entities", {}).values()]
    decisions = [receipt.get("decisions", {}).get("line") for receipt in receipts]
    assert any(item and item["automatic"] and item["choice"] == "INCOMING" and item["baseline_revisions"]
               for item in decisions)


def group_bundle(count=1):
    rows = bundle()
    rows["version_family"] = [(2, 1, "O2", None)]
    rows["equipment_version"] = [(1, 1, 2, b"v1", None, 1, None)]
    rows["release_version"] = [(i, None, 2, f"v{i}".encode(), None, None, i, "UNKNOWN", 1, None, "[]")
                               for i in range(1, 4)]
    rows["app_setting"] = [(row["setting_key"], row["setting_value"]) for row in group_rows(count, model=1)]
    return rows


def set_groups(peer, count, memo=""):
    rows = group_rows(count, model=1)
    for row in rows:
        value = json.loads(row["setting_value"])
        if "current" in value:
            value["current"]["memo"] = memo
        else:
            value["memo"] = memo if row["setting_key"].endswith(f"{count:020d}".encode()) else value["memo"]
        row["setting_value"] = json.dumps(value)
    with peer[0].begin() as connection:
        table = peer[1]["app_setting"]
        for row in rows:
            exists = connection.execute(table.select().where(table.c.setting_key == row["setting_key"])).first()
            if exists:
                connection.execute(table.update().where(table.c.setting_key == row["setting_key"]).values(setting_value=row["setting_value"]))
            else:
                connection.execute(table.insert().values(**row))


@pytest.mark.parametrize("divergent", [False, True])
def test_n_group_and_new_history_use_common_version_as_atomic_baseline(peers, tmp_path, divergent):
    local, remote = peers("A", initial=group_bundle()), peers("B")
    stamp(local, PEERS[0])
    path = exchange_file(local, tmp_path / "initial.sql")
    remote[2].apply(choose_all(remote[2], remote[2].preview(path, "review")))
    set_groups(remote, 2, "상대의 기준")
    if divergent:
        set_groups(local, 2, "로컬 기준")
    stamp(remote, PEERS[1])
    service = local[2]
    plan = service.preview(exchange_file(remote, tmp_path / "groups.sql"), "review")
    group, = [value for value in plan.differences if value.field == "N 그룹·저장 이력"]
    assert group.has_baseline and group.grouped
    if divergent:
        assert group.choice is None and not group.automatic
        plan = service.resolve(plan, {group.id: "INCOMING"})
    else:
        assert group.automatic and group.choice == "INCOMING" and plan.unresolved == 0
    service.apply(plan)
    settings = [row for row in current(local)["app_setting"] if row["setting_key"].startswith(b"version_groups.")]
    current_setting = json.loads(next(row["setting_value"] for row in settings if row["setting_key"].startswith(b"version_groups.v1:")))
    assert current_setting["sequence"] == 2 and current_setting["current"]["memo"] == "상대의 기준"
    assert len(settings) == 3


@pytest.mark.parametrize("choice", ["CURRENT", "INCOMING"])
def test_incoming_n_history_removal_requires_confirmation(peers, tmp_path, choice):
    local, remote = peers("A", initial=group_bundle(2)), peers("B")
    stamp(local, PEERS[0])
    initial = exchange_file(local, tmp_path / "initial.sql")
    remote[2].apply(choose_all(remote[2], remote[2].preview(initial, "review")))
    with remote[0].begin() as connection:
        table = remote[1]["app_setting"]
        keys = [row["setting_key"] for row in current(remote)["app_setting"]
                if row["setting_key"].startswith(b"version_groups.")]
        connection.execute(table.delete().where(table.c.setting_key.in_(keys)))
    set_groups(remote, 1)
    stamp(remote, PEERS[1])
    service = local[2]
    path = exchange_file(remote, tmp_path / "removed-history.sql")
    plan = service.preview(path, "review")
    group, = [item for item in plan.differences if item.field == "N 그룹·저장 이력"]
    assert group.has_baseline and group.requires_confirmation and group.choice is None and not group.automatic
    assert "개별 확인" in group.reason
    service.apply(service.resolve(plan, {group.id: choice}))
    settings = [row for row in current(local)["app_setting"] if row["setting_key"].startswith(b"version_groups.")]
    assert len(settings) == (3 if choice == "CURRENT" else 2)
    before = deepcopy(current(local))
    repeated = service.preview(path, "review")
    assert repeated.unresolved == 0
    service.apply(repeated)
    assert current(local) == before


def test_safe_local_only_n_history_addition_still_automatically_keeps_current(peers, tmp_path):
    local, remote = peers("A", initial=group_bundle()), peers("B")
    stamp(local, PEERS[0])
    initial = exchange_file(local, tmp_path / "initial.sql")
    remote[2].apply(choose_all(remote[2], remote[2].preview(initial, "review")))
    set_groups(local, 2, "로컬 새 이력")
    plan = local[2].preview(exchange_file(remote, tmp_path / "older.sql"), "review")
    group, = [item for item in plan.differences if item.field == "N 그룹·저장 이력"]
    assert group.requires_confirmation and group.has_baseline and group.choice == "CURRENT" and group.automatic
    local[2].apply(plan)
    assert len([row for row in current(local)["app_setting"] if row["setting_key"].startswith(b"version_groups.")]) == 3


@pytest.mark.parametrize("historical_sequence", ["not-an-integer", 10 ** 18])
def test_unusable_historical_n_group_sequence_falls_back_to_manual_without_unbounded_work(peers, tmp_path, monkeypatch, historical_sequence):
    """현재 파일이 정상이어도 불완전한 과거 N 기준은 자동 병합의 근거로 쓰지 않는다."""
    local, remote = peers("A", initial=group_bundle()), peers("B")
    stamp(local, PEERS[0])
    initial = exchange_file(local, tmp_path / "initial.sql")
    remote[2].apply(choose_all(remote[2], remote[2].preview(initial, "review")))
    set_groups(remote, 2, "새 기준")
    stamp(remote, PEERS[1])
    original = ReviewAutomation.baseline

    def incomplete(self, *args, **kwargs):
        baseline = original(self, *args, **kwargs)
        if baseline and baseline.found and isinstance(baseline.payload, dict):
            payload = deepcopy(baseline.payload)
            setting = payload.get("setting_value")
            if isinstance(setting, dict) and setting.get("schema") == 3:
                setting["sequence"] = historical_sequence
                return Baseline(True, payload, baseline.revisions, baseline.reason)
        return baseline

    monkeypatch.setattr(ReviewAutomation, "baseline", incomplete)
    plan = local[2].preview(exchange_file(remote, tmp_path / "new.sql"), "review")
    group, = [item for item in plan.differences if item.field == "N 그룹·저장 이력"]
    assert group.choice is None and not group.automatic and not group.has_baseline
