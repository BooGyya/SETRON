"""실제 SQL 교환에서 공통 이력을 기준으로 자동 병합하고 충돌만 선택한다."""

from copy import deepcopy

import pytest

from setron.features.data_admin.service import DataAdminService
from setron.features.peer_sync import read_state
from setron.features.storage.service import StorageService
from setron.infrastructure.database import Database
from test_peer_exchange import PEERS, choose_all, delete_snapshot, exchange_file, peers, stamp  # noqa: F401
from test_sql_import_service import bundle, current


def shared(peers, tmp_path, *, three=False):
    source = peers("A", initial=bundle(line="공통 라인"))
    destination = peers("B")
    stamp(source, PEERS[0])
    path = exchange_file(source, tmp_path / "common.sql")
    destination[2].apply(choose_all(destination[2], destination[2].preview(path, "review")))
    if not three:
        return source, destination
    third = peers("C")
    third[2].apply(choose_all(third[2], third[2].preview(path, "review")))
    return source, destination, third


def edit(peer, **changes):
    with peer[0].begin() as connection:
        connection.execute(peer[1]["equipment_snapshot"].update().values(**changes))


def snapshot(peer):
    row, = current(peer)["equipment_snapshot"]
    return row


def test_auto_merge_uses_live_local_values_and_combines_disjoint_edits(peers, tmp_path):
    """로컬 이력이 아직 갱신되지 않았어도 양쪽의 서로 다른 필드 수정을 자동으로 합친다."""
    local, remote = shared(peers, tmp_path)
    edit(local, line="A의 라인")
    edit(remote, process_name="B의 공정")
    stamp(remote, PEERS[1])
    path = exchange_file(remote, tmp_path / "remote.sql")
    service = local[2]
    plan = service.preview(path, "review")
    differences = {item.field: item for item in plan.differences}
    assert plan.unresolved == 0
    assert differences["line"].choice == "CURRENT" and differences["line"].automatic
    assert differences["process_name"].choice == "INCOMING" and differences["process_name"].automatic
    assert differences["line"].has_baseline and differences["line"].baseline == "공통 라인"
    assert differences["process_name"].has_baseline and differences["process_name"].baseline is None
    service.apply(plan)
    assert (snapshot(local)["line"], snapshot(local)["process_name"]) == ("A의 라인", "B의 공정")
    before = deepcopy(current(local))
    repeated = service.preview(path, "review")
    assert repeated.unresolved == 0
    service.apply(repeated)
    assert current(local) == before


def test_auto_merge_applies_a_remote_only_edit_without_manual_resolve(peers, tmp_path):
    """기준값과 같은 로컬 값에는 상대 PC만 수정한 값을 자동 선택한다."""
    local, remote = shared(peers, tmp_path)
    edit(remote, line="상대 PC 변경")
    stamp(remote, PEERS[1])
    path = exchange_file(remote, tmp_path / "remote-only.sql")
    plan = local[2].preview(path, "review")
    changed, = [item for item in plan.differences if item.field == "line"]
    assert changed.has_baseline and changed.automatic and changed.choice == "INCOMING"
    assert plan.unresolved == 0
    local[2].apply(plan)
    assert snapshot(local)["line"] == "상대 PC 변경"


@pytest.mark.parametrize("choice", ["CURRENT", "INCOMING"])
def test_auto_merge_keeps_divergent_edits_unresolved_and_audits_resolution(peers, tmp_path, choice):
    """같은 필드가 서로 달라지면 기준값을 보여주고 사용자가 고를 때까지 저장하지 않는다."""
    local, remote = shared(peers, tmp_path)
    edit(local, line="A 변경")
    edit(remote, line="B 변경")
    stamp(remote, PEERS[1])
    path = exchange_file(remote, tmp_path / "diverged.sql")
    service = local[2]
    plan = service.preview(path, "review")
    conflict, = [item for item in plan.differences if item.field == "line"]
    assert conflict.has_baseline and conflict.baseline == "공통 라인"
    assert conflict.choice is None and not conflict.automatic
    before = deepcopy(current(local))
    with pytest.raises(ValueError, match="선택"):
        service.apply(plan)
    assert current(local) == before
    selected = service.resolve(plan, {conflict.id: choice})
    assert selected.unresolved == 0
    service.apply(selected)
    assert snapshot(local)["line"] == ("A 변경" if choice == "CURRENT" else "B 변경")
    assert any(event["operation"] == "resolve" for event in read_state(current(local)).events.values())


def test_identical_parallel_edits_merge_histories_without_overwriting_the_value(peers, tmp_path):
    """같은 값으로 각각 수정한 경우 충돌을 만들지 않고 양쪽의 변경 이력을 보존한다."""
    local, remote = shared(peers, tmp_path)
    edit(local, line="같은 변경")
    edit(remote, line="같은 변경")
    stamp(local, PEERS[0])
    stamp(remote, PEERS[1])
    local_events = set(read_state(current(local)).events)
    remote_events = set(read_state(current(remote)).events)
    path = exchange_file(remote, tmp_path / "same.sql")
    plan = local[2].preview(path, "review")
    assert plan.unresolved == 0
    local[2].apply(plan)
    assert snapshot(local)["line"] == "같은 변경"
    assert local_events | remote_events <= set(read_state(current(local)).events)


def test_independent_roots_never_invent_a_common_baseline(peers, tmp_path):
    """업무 키가 같아도 독립 초기화한 이력에는 자동 병합 기준을 추정하지 않는다."""
    local = peers("A", initial=bundle(line="A 현재"))
    remote = peers("B", initial=bundle(line="B 현재"))
    stamp(local, PEERS[0])
    stamp(remote, PEERS[1])
    plan = local[2].preview(exchange_file(remote, tmp_path / "independent.sql"), "review")
    conflict, = [item for item in plan.differences if item.field == "line"]
    assert conflict.choice is None and not conflict.automatic and not conflict.has_baseline


def test_user_can_override_an_automatic_choice_and_keep_the_decision_on_reimport(peers, tmp_path):
    """자동 선택도 사용자가 현재값 유지로 바꿀 수 있고 같은 파일을 다시 적용하지 않는다."""
    local, remote = shared(peers, tmp_path)
    edit(remote, line="자동 선택 대상")
    stamp(remote, PEERS[1])
    path = exchange_file(remote, tmp_path / "override.sql")
    service = local[2]
    plan = service.preview(path, "review")
    automatic, = [item for item in plan.differences if item.field == "line"]
    assert automatic.automatic and automatic.choice == "INCOMING"
    plan = service.resolve(plan, {automatic.id: "CURRENT"})
    overridden, = [item for item in plan.differences if item.field == "line"]
    assert overridden.choice == "CURRENT" and not overridden.automatic
    service.apply(plan)
    assert snapshot(local)["line"] == "공통 라인"
    before = deepcopy(current(local))
    repeated = service.preview(path, "review")
    assert repeated.unresolved == 0
    service.apply(repeated)
    assert current(local) == before


def test_resolved_branch_converges_across_three_equal_peers(peers, tmp_path):
    """A·B 충돌 해결을 C와 다시 B에 전달해도 이미 해결한 분기가 다시 충돌하지 않는다."""
    first, second, third = shared(peers, tmp_path, three=True)
    edit(first, line="A의 확정 값")
    edit(second, line="B의 분기 값")
    stamp(first, PEERS[0])
    stamp(second, PEERS[1])
    old_file = exchange_file(second, tmp_path / "old-branch.sql")
    plan = first[2].preview(old_file, "review")
    assert plan.unresolved > 0
    first[2].apply(choose_all(first[2], plan, "CURRENT"))
    for source, destination, name in ((first, third, "AC"), (third, second, "CB"), (second, first, "BA")):
        path = exchange_file(source, tmp_path / f"{name}.sql")
        plan = destination[2].preview(path, "review")
        assert plan.unresolved == 0
        destination[2].apply(plan)
        assert snapshot(destination)["line"] == "A의 확정 값"
    before = deepcopy(current(first))
    plan = first[2].preview(old_file, "review")
    assert plan.unresolved == 0
    first[2].apply(plan)
    assert current(first) == before


def test_one_sided_delete_requires_individual_confirmation_even_with_a_baseline(peers, tmp_path):
    """사용자 정책에 따라 상대만 삭제했어도 실제 삭제는 개별 확인 대상으로 남긴다."""
    local, remote = shared(peers, tmp_path)
    delete_snapshot(remote)
    stamp(remote, PEERS[1])
    plan = local[2].preview(exchange_file(remote, tmp_path / "deleted.sql"), "review")
    deletions = [item for item in plan.differences if item.field == "삭제"]
    assert deletions and all(item.choice is None and not item.automatic for item in deletions)
    assert plan.unresolved > 0
    before = deepcopy(current(local))
    with pytest.raises(ValueError, match="선택"):
        local[2].apply(plan)
    assert current(local) == before


def test_new_remote_edit_conflicts_with_a_local_unexported_delete(peers, tmp_path):
    """아직 이력에 반영되지 않은 로컬 삭제와 다른 PC의 수정도 자동 복원하지 않는다."""
    local, remote = shared(peers, tmp_path)
    delete_snapshot(local)
    edit(remote, line="상대의 새 수정")
    stamp(remote, PEERS[1])
    plan = local[2].preview(exchange_file(remote, tmp_path / "delete-edit.sql"), "review")
    assert plan.unresolved > 0
    assert any(item.field == "삭제된 항목 복원" and item.choice is None for item in plan.differences)
    assert not current(local)["equipment_snapshot"]


@pytest.mark.parametrize("choice", ["CURRENT", "INCOMING"])
def test_unexported_new_pm_cannot_be_pruned_by_an_automatic_remote_group_change(peers, tmp_path, choice):
    """UUID가 아직 없는 로컬 새 PM을 구성 비교에서 빼고 자동 삭제하지 않는다."""
    local, remote = shared(peers, tmp_path)
    with local[0].begin() as connection:
        connection.execute(local[1]["equipment_version"].insert().values(
            equipment_version_id=2, snapshot_id=1, family_id=1, version_raw=b"local-v2",
            release_version_id=None, batch_id=1, source_reference=None))
        connection.execute(local[1]["equipment_pm"].insert().values(
            pm_id=2, snapshot_id=1, equipment_version_id=2, pm_name=b"PM2",
            group_id=1, model_name_raw="O2", batch_id=1, source_reference=None))
    with remote[0].begin() as connection:
        connection.execute(remote[1]["equipment_version"].update().values(version_raw=b"remote-v2"))
    stamp(remote, PEERS[1])
    service = local[2]
    plan = service.preview(exchange_file(remote, tmp_path / "new-local-pm.sql"), "review")
    group, = [item for item in plan.differences if item.field == "PM·SW 구성"]
    assert group.choice is None and not group.automatic and plan.unresolved > 0
    assert "PM2" in str(group.current)
    before = deepcopy(current(local))
    with pytest.raises(ValueError, match="선택"):
        service.apply(plan)
    assert current(local) == before
    service.apply(choose_all(service, plan, choice))
    stored = current(local)
    assert {row["pm_name"] for row in stored["equipment_pm"]} == (
        {b"PM1", b"PM2"} if choice == "CURRENT" else {b"PM1"})
    assert {row["version_raw"] for row in stored["equipment_version"]} == (
        {b"v1", b"local-v2"} if choice == "CURRENT" else {b"remote-v2"})


def test_known_pm_software_group_automatically_applies_a_remote_only_version_change(peers, tmp_path):
    """구성원 전체의 공통 이력이 있으면 상대만 바꾼 PM·SW 구성을 묶어서 자동 적용한다."""
    local, remote = shared(peers, tmp_path)
    with remote[0].begin() as connection:
        connection.execute(remote[1]["equipment_version"].update().values(version_raw=b"remote-v2"))
    stamp(remote, PEERS[1])
    service = local[2]
    path = exchange_file(remote, tmp_path / "remote-pm.sql")
    plan = service.preview(path, "review")
    group, = [item for item in plan.differences if item.field == "PM·SW 구성"]
    assert group.has_baseline and group.automatic and group.choice == "INCOMING"
    assert plan.unresolved == 0
    service.apply(plan)
    assert [row["version_raw"] for row in current(local)["equipment_version"]] == [b"remote-v2"]
    before = deepcopy(current(local))
    service.apply(service.preview(path, "review"))
    assert current(local) == before


@pytest.mark.parametrize("legacy_restore", [False, True])
def test_remote_delete_then_restore_does_not_delete_the_restored_row_on_exchange(peers, tmp_path, legacy_restore):
    """같은 PC에서 삭제 후 복원한 최신 자료를 과거 삭제 기록 때문에 다시 지우지 않는다."""
    local, remote = shared(peers, tmp_path)
    original = deepcopy(current(remote))
    admin = DataAdminService(StorageService(Database(remote[0])))
    journal = admin.delete_rows(1, [1])
    stamp(remote, PEERS[1])
    if legacy_restore:
        # 이전 버전처럼 복원한 행에 새 UUID가 생긴 정상 교환 파일도 계속 지원한다.
        with remote[0].begin() as connection:
            for table, primary in (("equipment_snapshot", "snapshot_id"),
                                   ("equipment_version", "equipment_version_id"), ("equipment_pm", "pm_id")):
                values = dict(original[table][0])
                values[primary] = 2
                values["snapshot_id"] = 2
                if table == "equipment_pm":
                    values["equipment_version_id"] = 2
                connection.execute(remote[1][table].insert().values(**values))
    else:
        assert admin.restore_rows(journal) == 1
    stamp(remote, PEERS[1])
    assert len(current(remote)["equipment_snapshot"]) == 1
    path = exchange_file(remote, tmp_path / "restored.sql")
    service = local[2]
    plan = service.preview(path, "review")
    if plan.unresolved:
        plan = choose_all(service, plan, "INCOMING")
    service.apply(plan)
    restored = current(local)
    assert len(restored["equipment_snapshot"]) == 1
    assert len(restored["equipment_pm"]) == 1
    assert len(restored["equipment_version"]) == 1
