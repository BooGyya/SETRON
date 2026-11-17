"""DB 일괄 읽기와 워커 조회의 최신성·원문·채택 경계를 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from copy import copy
from dataclasses import replace
from datetime import date
from threading import Event, Lock

import pytest
from sqlalchemy import event

from setron.features import reference_data
from setron.features.equipment.repository import EquipmentRepository
from setron.features.risk.schemas import RiskStatus
from setron.features.risk.service import RiskAssessor
from setron.features.storage.validation import StorageValidationError
from setron.ui.database_source import DatabaseDashboardSource, load_dashboard_source
from setron.ui.query_state import AppliedQuery, QuerySelection, TargetN

from test_management_db_source import ALL, connected
from test_management_service import observe
from test_release_revision import observation, revision_storage  # noqa: F401


def test_bulk_snapshot_read_matches_single_reads_with_three_selects(revision_storage):
    storage = revision_storage
    release_id, _, _, _ = connected(storage)
    observe(storage, release_id, code="SHARED", shared=True)
    observation(storage, code="MIXED", day=date(2026, 9, 30), pms=True,
                versions=((1, 1, "v1 원문 😀 ", None), (2, 3, "c3_(H) ", None)))
    with storage.transaction() as tx:
        tx.insert("version_family", {"family_id": 3, "group_id": 4, "family_name": "HPA", "ui_type": None})
    observation(storage, code="HPA", day=date(2026, 10, 5), versions=((3, 4, "hpa-원문 ", None),))
    repository = EquipmentRepository()
    with storage.transaction() as tx:
        equipment = {row["equipment_id"]: row for row in repository.rows(tx, "equipment")}
        snapshots = repository.rows(tx, "equipment_snapshot")
        expected = tuple(repository.load(tx, equipment[row["equipment_id"]], row) for row in snapshots)
        statements = []

        def count(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        connection = tx.session.connection()
        event.listen(connection, "before_cursor_execute", count)
        try:
            actual = repository.load_all(tx, equipment)
        finally:
            event.remove(connection, "before_cursor_execute", count)
    assert actual == expected and len(statements) == 3
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert any(sw.version_raw.endswith(" ") for snapshot in actual for sw in snapshot.software)
    assert any(len(sw.pms) == 2 for snapshot in actual for sw in snapshot.software)


def test_bulk_read_does_not_hide_pm_without_software_link(revision_storage):
    storage = revision_storage
    release_id, _, _, _ = connected(storage)
    saved = observe(storage, release_id, code="BROKEN", shared=True)
    repository = EquipmentRepository()
    with storage.transaction() as tx:
        pm = repository.rows(tx, "equipment_pm", snapshot_id=saved.snapshot_id)[0]
        tx.update("equipment_pm", {"pm_id": pm["pm_id"]}, {"equipment_version_id": None})
    with storage.transaction() as tx:
        equipment = {row["equipment_id"]: row for row in repository.rows(tx, "equipment")}
        with pytest.raises(StorageValidationError, match="PM의 SW 연결"):
            repository.load_all(tx, equipment)


def test_async_reads_publish_only_accepted_source_and_do_not_block_a_new_query(revision_storage, monkeypatch):
    connected(revision_storage)
    base = load_dashboard_source(revision_storage)
    old_started, release_old = Event(), Event()
    calls, call_lock = [], Lock()
    monkeypatch.setattr(reference_data, "REGISTERED_AREAS", {})

    def loader(catalog=None, *, publish_lines=True):
        with call_lock:
            number = len(calls) + 1
            calls.append((number, publish_lines))
        fresh = copy(base)
        fresh.source_label = str(number)
        fresh.registered_line_entries = ({"site": "화성", "line": str(number), "fab": "MEMORY"},)
        if publish_lines:
            reference_data.use_registered_lines(fresh.registered_line_entries)
        if number == 2:
            old_started.set()
            assert release_old.wait(3)
        return fresh

    source = DatabaseDashboardSource(loader)
    with ThreadPoolExecutor(max_workers=2) as executor:
        old = executor.submit(source.read_query, ALL)
        try:
            assert old_started.wait(3)
            fresh, result = executor.submit(source.read_query, ALL).result(timeout=3)
            assert source.source_label == "1" and reference_data.default_area("화성", "1") == "MEMORY"
            assert reference_data.default_area("화성", "3") is None
            source.accept_query(fresh)
            assert result.query is ALL and source.source_label == "3"
        finally:
            release_old.set()
        ignored, _ = old.result(timeout=3)
    assert ignored.source_label == "2" and source.source_label == "3"
    assert reference_data.default_area("화성", "3") == "MEMORY"
    assert reference_data.default_area("화성", "2") is None
    assert calls == [(1, True), (2, False), (3, False)]


def test_worker_query_and_sync_query_keep_invalid_target_notice_and_explicit_refresh(revision_storage):
    release_id, _, _, _ = connected(revision_storage)
    calls = []

    def loader(catalog=None, *, publish_lines=True):
        calls.append(publish_lines)
        return load_dashboard_source(revision_storage, catalog, publish_lines=publish_lines)

    source = DatabaseDashboardSource(loader)
    query = AppliedQuery(QuerySelection(), 1, 0, "전체", (TargetN(1, 1, 999999),))
    expected = source.snapshot(query)
    fresh, result = source.read_query(query)
    assert result == expected and result.query is query
    assert "목표 N 재선택" in result.source_label
    source.accept_query(fresh)
    observe(revision_storage, release_id, code="NEW-AFTER-READ")
    assert source.snapshot(ALL).summary.equipment_count == 2
    assert calls == [True, False, False, False]


def test_accept_query_rechecks_mapping_before_replacing_current_source(revision_storage):
    connected(revision_storage)
    base = load_dashboard_source(revision_storage)
    source = DatabaseDashboardSource(lambda: copy(base))
    fresh, _ = source.read_query(ALL)
    fresh.source_label = "채택되면 안 되는 결과"
    source.bindings = {**source.bindings, "new-model": (999,)}
    with pytest.raises(RuntimeError, match="설비군 매핑"):
        source.accept_query(fresh)
    assert source.source_label == base.source_label and "new-model" in source.bindings


def test_deferred_line_publication_still_uses_this_reads_registered_fab(revision_storage, monkeypatch):
    connected(revision_storage)
    with revision_storage.transaction() as tx:
        tx.insert("app_setting", {"setting_key": "registered_lines.v1", "setting_value": {"lines": [
            {"site": "화성", "line": "1라인", "fab": "FOUNDRY"},
        ]}})
    monkeypatch.setattr(reference_data, "REGISTERED_AREAS", {("화성", "1라인"): "MEMORY"})
    source = load_dashboard_source(revision_storage, publish_lines=False)
    assert reference_data.default_area("화성", "1라인") == "MEMORY"
    assert source.records and source.locations[source.records[0].equipment_id].fab_key == "FOUNDRY"
    assert any(option.fab_key == "FOUNDRY" for option in source.catalog.lines)


def test_loaded_risk_cache_keeps_pm_scope_and_rule_refresh_separate(revision_storage, monkeypatch):
    release_id, _, _, _ = connected(revision_storage)
    observe(revision_storage, release_id, code="SHARED", shared=True)
    calls, original = [], RiskAssessor.equipment_risk

    def counted(assessor, record):
        calls.append(record)
        return original(assessor, record)

    monkeypatch.setattr(RiskAssessor, "equipment_risk", counted)
    source = load_dashboard_source(revision_storage)
    whole = next(record for record in source.records if record.equipment_code == "SHARED")
    before = len(calls)
    assert source.risk_status(whole) is RiskStatus.RISK
    assert source.assessor.equipment_risk(whole).status is RiskStatus.RISK
    assert len(calls) == before
    selected = replace(whole, pms=whole.pms[:1])
    assert source.assessor.equipment_risk(selected).record.pms == selected.pms
    assert len(calls) == before + 1
    source.assessor.rules = ()
    assert source.assessor.equipment_risk(whole).status is RiskStatus.NO_RISK
    assert len(calls) == before + 2


@pytest.mark.parametrize("method", ["refresh", "load_version_catalog", "load_item_data", "snapshot", "reload_catalog"])
def test_legacy_read_does_not_block_new_query_or_overwrite_it_when_finishing_late(revision_storage, monkeypatch, method):
    connected(revision_storage)
    base = load_dashboard_source(revision_storage)
    blocked, release = Event(), Event()
    calls, call_lock = [], Lock()
    monkeypatch.setattr(reference_data, "REGISTERED_AREAS", {})

    def loader(catalog=None, *, publish_lines=True):
        with call_lock:
            number = len(calls) + 1
            calls.append((number, publish_lines))
        fresh = copy(base)
        fresh.source_label = str(number)
        fresh.registered_line_entries = ({"site": "화성", "line": str(number), "fab": "MEMORY"},)
        if publish_lines:
            reference_data.use_registered_lines(fresh.registered_line_entries)
        if number == 2:
            blocked.set()
            assert release.wait(3)
        return fresh

    source = DatabaseDashboardSource(loader)
    operation = (lambda: source.snapshot(ALL)) if method == "snapshot" else getattr(source, method)
    with ThreadPoolExecutor(max_workers=2) as executor:
        old = executor.submit(operation)
        try:
            assert blocked.wait(2)
            fresh, result = executor.submit(source.read_query, ALL).result(timeout=2)
            assert source.accept_query(fresh) is True
            assert result.query is ALL and source.source_label == "3" and not old.done()
        finally:
            release.set()
        old.result(timeout=2)
    assert source.source_label == "3"
    assert reference_data.default_area("화성", "3") == "MEMORY"
    assert reference_data.default_area("화성", "2") is None
    assert calls == [(1, True), (2, False), (3, False)]


def test_query_acceptance_rejects_result_older_than_a_completed_legacy_read(revision_storage):
    connected(revision_storage)
    base = load_dashboard_source(revision_storage)
    calls = []

    def loader():
        calls.append(True)
        fresh = copy(base)
        fresh.source_label = str(len(calls))
        return fresh

    source = DatabaseDashboardSource(loader)
    prepared, _ = source.read_query(ALL)
    source.refresh()
    assert source.source_label == "3"
    assert source.accept_query(prepared) is False
    assert source.source_label == "3"


def test_fixed_loader_reusing_one_object_keeps_request_results_and_generations_separate(revision_storage):
    connected(revision_storage)
    base = load_dashboard_source(revision_storage)
    source = DatabaseDashboardSource(lambda: base)
    base.source_label = "first"
    first, _ = source.read_query(ALL)
    base.source_label = "second"
    second, _ = source.read_query(ALL)
    assert first.source_label == "first" and second.source_label == "second"
    assert source.accept_query(second) is True
    assert source.accept_query(first) is False
    assert source.source_label == "second"
