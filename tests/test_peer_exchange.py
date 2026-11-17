"""여러 PC의 실제 SQL 검토·선택·저장이 UUID와 변경 이력을 보존한다."""

from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from setron.features.db_import.service import SqlImportService, _read_target
from setron.features.peer_sync import identity_index, key_token, read_state, reconcile
from setron.infrastructure.database import Database, DatabaseSaveError

from test_sql_import_dump import _export
from test_sql_import_service import bundle, current, seed, target as sql_target
from test_sql_import_history import _deleted_equipment


PEERS = tuple(str(UUID(int=value)) for value in (101, 102, 103))


@pytest.fixture
def peers(tmp_path):
    setups = []

    def create(name, *, initial=None):
        path = tmp_path / name
        path.mkdir()
        setup = sql_target.__wrapped__(path, SimpleNamespace())
        setups.append(setup)
        engine, tables, _ = next(setup)
        value = engine, tables, SqlImportService(lambda: Database(engine), peer_id=PEERS[len(setups) - 1])
        if initial is not None:
            seed(value, initial)
        return value

    yield create
    for setup in reversed(setups):
        setup.close()


def stamp(peer, peer_id):
    with Database(peer[0]).transaction() as session:
        repository, rows, _ = _read_target(session)
        return reconcile(session, repository, rows=rows, peer_id=peer_id)


def exchange_file(peer, path, *, rows=None):
    rows = current(peer) if rows is None else rows
    values = {name: [tuple(row[column] for column in peer[1][name].columns.keys()) for row in records]
              for name, records in rows.items()}
    path.write_text(_export(values), encoding="utf-8")
    return path


def choose_all(service, plan, choice="INCOMING"):
    return service.resolve(plan, {difference.id: choice for difference in plan.differences})


def entity(peer, table, key):
    return identity_index(current(peer))[table, key_token(key)]


def test_peer_exchange_loop_preserves_identity_and_does_not_reapply_known_history(peers, tmp_path):
    """A→B→C→A가 돌아와도 같은 자료와 이벤트를 새 변경으로 쌓지 않는다."""
    first, second, third = peers("A", initial=bundle()), peers("B"), peers("C")
    stamp(first, PEERS[0])
    original_id = entity(first, "equipment", {"equipment_id": 1})
    for source, destination, name in ((first, second, "AB"), (second, third, "BC"), (third, first, "CA")):
        path = exchange_file(source, tmp_path / f"{name}.sql")
        service = destination[2]
        service.apply(choose_all(service, service.preview(path, "review")))
        stamp(destination, PEERS[(first, second, third).index(destination)])
    assert entity(second, "equipment", {"equipment_id": 1}) == original_id
    assert entity(third, "equipment", {"equipment_id": 1}) == original_id
    before = deepcopy(current(first))
    path = exchange_file(third, tmp_path / "again.sql")
    plan = first[2].preview(path, "review")
    assert plan.unresolved == 0
    first[2].apply(plan)
    assert current(first) == before


def test_peer_review_combines_independent_fields_and_retains_decision_on_old_file(peers, tmp_path):
    """각 PC의 라인·공정 수정을 필드별로 선택하고 오래된 파일을 중복 적용하지 않는다."""
    local, remote = peers("A", initial=bundle()), peers("B")
    stamp(local, PEERS[0])
    original = exchange_file(local, tmp_path / "initial.sql")
    remote[2].apply(choose_all(remote[2], remote[2].preview(original, "review")))
    with local[0].begin() as connection:
        connection.execute(local[1]["equipment_snapshot"].update().values(line="A의 라인"))
    with remote[0].begin() as connection:
        connection.execute(remote[1]["equipment_snapshot"].update().values(process_name="B의 공정"))
    stamp(local, PEERS[0])
    stamp(remote, PEERS[1])
    path = exchange_file(remote, tmp_path / "remote.sql")
    service = local[2]
    plan = service.preview(path, "review")
    choices = {difference.id: "CURRENT" if difference.field == "line" else "INCOMING"
               for difference in plan.differences}
    reviewed = service.resolve(plan, choices)
    assert reviewed.unresolved == 0
    service.apply(reviewed)
    stored, = current(local)["equipment_snapshot"]
    assert (stored["line"], stored["process_name"]) == ("A의 라인", "B의 공정")
    events = read_state(current(local)).events
    assert events
    before = deepcopy(current(local))
    repeated = service.preview(path, "review")
    assert repeated.unresolved == 0
    service.apply(repeated)
    assert current(local) == before


def test_peer_uuid_matches_renamed_equipment_instead_of_creating_a_duplicate(peers, tmp_path):
    """이미 교환한 동일 UUID는 호기 코드가 바뀌어도 기존 로컬 행으로 매칭한다."""
    source, destination = peers("A", initial=bundle()), peers("B")
    stamp(source, PEERS[0])
    path = exchange_file(source, tmp_path / "initial.sql")
    destination[2].apply(choose_all(destination[2], destination[2].preview(path, "review")))
    identifier = entity(destination, "equipment", {"equipment_id": 1})
    with source[0].begin() as connection:
        connection.execute(source[1]["equipment"].update().values(equipment_code=b"RENAMED"))
    stamp(source, PEERS[0])
    path = exchange_file(source, tmp_path / "renamed.sql")
    service = destination[2]
    service.apply(choose_all(service, service.preview(path, "review")))
    equipment, = current(destination)["equipment"]
    assert equipment["equipment_id"] == 1 and equipment["equipment_code"] == b"RENAMED"
    assert entity(destination, "equipment", {"equipment_id": 1}) == identifier


def test_failed_peer_merge_rolls_back_business_values_and_sync_history(peers, tmp_path):
    """선택한 자료 저장이 실패하면 UUID·변경 이벤트도 함께 취소된다."""
    local, remote = peers("A", initial=bundle()), peers("B")
    stamp(local, PEERS[0])
    path = exchange_file(local, tmp_path / "initial.sql")
    remote[2].apply(choose_all(remote[2], remote[2].preview(path, "review")))
    with remote[0].begin() as connection:
        connection.execute(remote[1]["equipment_snapshot"].update().values(line="실패할 변경"))
    stamp(remote, PEERS[1])
    path = exchange_file(remote, tmp_path / "changed.sql")
    service = local[2]
    plan = choose_all(service, service.preview(path, "review"))
    with local[0].begin() as connection:
        connection.exec_driver_sql("CREATE TRIGGER reject_peer_change BEFORE UPDATE ON equipment_snapshot "
                                   "BEGIN SELECT RAISE(ABORT, 'peer rollback'); END")
    before = deepcopy(current(local))
    with pytest.raises(DatabaseSaveError):
        service.apply(plan)
    assert current(local) == before


def test_peer_exchange_preserves_archived_delete_history_without_false_revisions(peers, tmp_path):
    """삭제된 ID의 보존 표기가 바뀌어도 동일 원본 이력을 새 수정으로 오인하지 않는다."""
    first, second, third = peers("A", initial=_deleted_equipment()), peers("B"), peers("C")
    stamp(first, PEERS[0])
    initial_events = set(read_state(current(first)).events)
    for source, destination, name in ((first, second, "history-AB"),
                                       (second, third, "history-BC"),
                                       (third, first, "history-CA")):
        path = exchange_file(source, tmp_path / f"{name}.sql")
        service = destination[2]
        service.apply(choose_all(service, service.preview(path, "review")))
    assert set(read_state(current(first)).events) == initial_events
    before = deepcopy(current(first))
    path = exchange_file(third, tmp_path / "history-again.sql")
    plan = first[2].preview(path, "review")
    assert plan.unresolved == 0
    first[2].apply(plan)
    assert current(first) == before


@pytest.mark.parametrize("choice", ["CURRENT", "INCOMING"])
def test_peer_snapshot_tombstone_needs_confirmation_and_is_not_reapplied(peers, tmp_path, choice):
    """파일에 없는 행과 명시적인 삭제 이력을 구분하고 삭제 선택도 다음 교환에 보존한다."""
    source, destination = peers("A", initial=bundle()), peers("B")
    stamp(source, PEERS[0])
    path = exchange_file(source, tmp_path / "initial.sql")
    destination[2].apply(choose_all(destination[2], destination[2].preview(path, "review")))
    with source[0].begin() as connection:
        for table in ("equipment_pm", "equipment_version", "equipment_snapshot"):
            connection.execute(source[1][table].delete())
    stamp(source, PEERS[0])
    path = exchange_file(source, tmp_path / "deleted.sql")
    service = destination[2]
    plan = service.preview(path, "review")
    assert plan.unresolved > 0
    assert any(difference.field == "삭제" for difference in plan.differences)
    before = deepcopy(current(destination))
    with pytest.raises(ValueError, match="선택"):
        service.apply(plan)
    assert current(destination) == before
    service.apply(choose_all(service, plan, choice))
    rows = current(destination)
    for table in ("equipment_pm", "equipment_version", "equipment_snapshot"):
        assert len(rows[table]) == (1 if choice == "CURRENT" else 0)
    before = deepcopy(rows)
    repeated = service.preview(path, "review")
    assert repeated.unresolved == 0
    service.apply(repeated)
    assert current(destination) == before


@pytest.mark.parametrize("choice", ["CURRENT", "INCOMING"])
def test_peer_children_change_is_reviewed_even_when_snapshot_head_is_unchanged(peers, tmp_path, choice):
    """현황 공통값이 같아도 SW 버전의 새 변경을 검토하며 해결된 구성은 다시 묻지 않는다."""
    source, destination = peers("A", initial=bundle()), peers("B")
    stamp(source, PEERS[0])
    path = exchange_file(source, tmp_path / "initial.sql")
    destination[2].apply(choose_all(destination[2], destination[2].preview(path, "review")))
    with source[0].begin() as connection:
        connection.execute(source[1]["equipment_version"].update().values(version_raw=b"v2"))
    stamp(source, PEERS[0])
    path = exchange_file(source, tmp_path / "changed-child.sql")
    service = destination[2]
    plan = service.preview(path, "review")
    assert any(difference.field == "PM·SW 구성" for difference in plan.differences)
    service.apply(choose_all(service, plan, choice))
    version, = current(destination)["equipment_version"]
    assert version["version_raw"] == (b"v1" if choice == "CURRENT" else b"v2")
    before = deepcopy(current(destination))
    repeated = service.preview(path, "review")
    assert repeated.unresolved == 0
    service.apply(repeated)
    assert current(destination) == before


def test_peer_independent_initialization_unifies_aliases_without_rewriting_old_events(peers, tmp_path):
    """같은 업무 자료를 따로 초기화한 PC도 별칭으로 연결하고 불변 이벤트를 유지한다."""
    first, second = peers("A", initial=bundle()), peers("B", initial=bundle())
    stamp(first, PEERS[0])
    stamp(second, PEERS[1])
    first_events = deepcopy(read_state(current(first)).events)
    second_events = deepcopy(read_state(current(second)).events)
    assert set(first_events).isdisjoint(second_events)
    for source, destination, name in ((first, second, "independent-AB"), (second, first, "independent-BA")):
        path = exchange_file(source, tmp_path / f"{name}.sql")
        service = destination[2]
        service.apply(choose_all(service, service.preview(path, "review")))
    combined = read_state(current(first)).events
    assert all(combined[identifier] == event for identifier, event in {**first_events, **second_events}.items())
    before = deepcopy(current(first))
    path = exchange_file(second, tmp_path / "independent-again.sql")
    plan = first[2].preview(path, "review")
    assert plan.unresolved == 0
    first[2].apply(plan)
    assert current(first) == before


@pytest.mark.parametrize("corruption", ["payload", "event"])
def test_peer_exchange_rejects_forged_payload_or_reused_event_before_selection(peers, tmp_path, corruption):
    """동일 UUID에 다른 내용을 붙이거나 과거 head로 파일값을 바꾼 SQL은 검토 단계에서 거부한다."""
    source, destination = peers("A", initial=bundle()), peers("B")
    stamp(source, PEERS[0])
    path = exchange_file(source, tmp_path / "initial.sql")
    destination[2].apply(choose_all(destination[2], destination[2].preview(path, "review")))
    rows = deepcopy(current(source))
    if corruption == "payload":
        rows["equipment_snapshot"][0]["line"] = "이력과 맞지 않는 조작 값"
    else:
        row = next(row for row in rows["app_setting"]
                   if row["setting_key"].startswith(b"peer_sync.v1:event:"))
        event = json.loads(row["setting_value"])
        event["peer"] = PEERS[2]
        row["setting_value"] = json.dumps(event)
    path = exchange_file(source, tmp_path / "invalid.sql", rows=rows)
    before = deepcopy(current(destination))
    with pytest.raises(ValueError):
        destination[2].preview(path, "review")
    assert current(destination) == before


def delete_snapshot(peer):
    with peer[0].begin() as connection:
        for table in ("equipment_pm", "equipment_version", "equipment_snapshot"):
            connection.execute(peer[1][table].delete())


def test_old_exchange_does_not_resurrect_a_local_delete_not_yet_exported(peers, tmp_path):
    """아직 다시 내보내지 않은 로컬 삭제도 이미 알고 있는 과거 파일로 복원하지 않는다."""
    source, destination = peers("A", initial=bundle()), peers("B")
    stamp(source, PEERS[0])
    path = exchange_file(source, tmp_path / "initial.sql")
    destination[2].apply(choose_all(destination[2], destination[2].preview(path, "review")))
    delete_snapshot(destination)
    service = destination[2]
    plan = service.preview(path, "review")
    service.apply(choose_all(service, plan, "CURRENT"))
    assert not current(destination)["equipment_snapshot"]
    assert not current(destination)["equipment_version"]
    assert not current(destination)["equipment_pm"]


@pytest.mark.parametrize("exported_delete", [False, True])
@pytest.mark.parametrize("choice", ["CURRENT", "INCOMING"])
def test_local_delete_and_remote_edit_require_explicit_choice(peers, tmp_path, exported_delete, choice):
    """로컬 삭제와 다른 PC의 새 수정이 겹치면 확인 전 복원하지 않는다."""
    source, destination = peers("A", initial=bundle()), peers("B")
    stamp(source, PEERS[0])
    path = exchange_file(source, tmp_path / "initial.sql")
    destination[2].apply(choose_all(destination[2], destination[2].preview(path, "review")))
    delete_snapshot(destination)
    if exported_delete:
        stamp(destination, PEERS[1])
    with source[0].begin() as connection:
        connection.execute(source[1]["equipment_snapshot"].update().values(line="다른 PC의 새 수정"))
    stamp(source, PEERS[0])
    path = exchange_file(source, tmp_path / "edited.sql")
    service = destination[2]
    plan = service.preview(path, "review")
    assert plan.unresolved > 0
    before = deepcopy(current(destination))
    with pytest.raises(ValueError, match="선택"):
        service.apply(plan)
    assert current(destination) == before
    service.apply(choose_all(service, plan, choice))
    records = current(destination)["equipment_snapshot"]
    assert len(records) == (0 if choice == "CURRENT" else 1)
    if records:
        assert records[0]["line"] == "다른 PC의 새 수정"
        assert len(current(destination)["equipment_pm"]) == 1
