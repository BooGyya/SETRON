"""검색은 숨은 화면을 만들지 않으며 비동기 응답은 현재 조회에만 반영한다."""

import os
from dataclasses import dataclass, field
from threading import Event, Lock, get_ident
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import setron.ui.equipment as equipment_module
import setron.ui.heatmap as heatmap_module
from setron.ui.app import create_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.query_state import QuerySelection
from setron.ui.theme import install_theme


def until(app, predicate, timeout=4):
    deadline = monotonic() + timeout
    while not predicate() and monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert predicate()


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    return result


@dataclass
class QueryGate:
    entered: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)
    returned: Event = field(default_factory=Event)
    query: object = None
    thread: int | None = None
    error: Exception | None = None


class GatedSource:
    """실제 DB 없이 워커 읽기와 GUI에서의 응답 채택을 분리한다."""

    def __init__(self):
        self.base = demo_dashboard_source()
        self.gates = [QueryGate() for _ in range(3)]
        self._next_gate, self._lock = 0, Lock()
        self.accepted = []

    def __getattr__(self, name):
        return getattr(self.base, name)

    def read_query(self, query):
        with self._lock:
            gate = self.gates[self._next_gate]
            self._next_gate += 1
        gate.query, gate.thread = query, get_ident()
        gate.entered.set()
        try:
            assert gate.release.wait(8), "검증용 조회 해제 대기 초과"
            if gate.error is not None:
                raise gate.error
            fresh = demo_dashboard_source()
            fresh.source_label = f"검증용 비동기 응답 {query.revision}"
            fresh.request_query = query
            return fresh, fresh.snapshot(query)
        finally:
            gate.returned.set()

    def accept_query(self, fresh):
        self.accepted.append((fresh.request_query, get_ident()))
        self.base = fresh


@pytest.fixture
def delayed_window(app):
    source = GatedSource()
    window = create_window(source, chat_enabled=True)
    window.show()
    app.processEvents()
    yield window, source
    for gate in source.gates:
        gate.release.set()
    window.close()
    assert QThreadPool.globalInstance().waitForDone(4000)
    app.processEvents()
    window.deleteLater()
    app.processEvents()


def loaded_keys(window):
    return {key for key, _ in window.pages.loaded_items()}


def test_start_search_and_close_guard_leave_unvisited_pages_uncreated(app):
    source = demo_dashboard_source()
    window = create_window(source, chat_enabled=True)
    try:
        expected = {"dashboard", "ai"}
        assert loaded_keys(window) == expected
        assert window.close_guard() and loaded_keys(window) == expected
        window.query_panel.set_draft(QuerySelection(site_key="hwaseong"))
        query = window.query_panel.apply_draft()
        assert window.pages["dashboard"].snapshot.query is query
        assert loaded_keys(window) == expected
        assert window.header.actions["equipment_list"].isEnabled()
        assert window.close_guard() and loaded_keys(window) == expected
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_direct_hidden_page_access_uses_latest_query_snapshot(app):
    window = create_window(demo_dashboard_source(), chat_enabled=True)
    try:
        first = window.query_panel.apply_draft()
        window.query_panel.set_draft(QuerySelection(site_key="hwaseong"))
        latest = window.query_panel.apply_draft()
        snapshot = window.pages["dashboard"].snapshot
        assert snapshot.query is latest and latest is not first
        assert loaded_keys(window) == {"dashboard", "ai"}
        heatmap = window.pages.get("heatmap")
        assert heatmap.snapshot is snapshot and window.route == "dashboard"
        equipment = window.pages.get("equipment")
        assert equipment.snapshot is snapshot and equipment.data.snapshot is snapshot
        assert set(equipment.scope_ids) == set(equipment.data.equipment_ids)
        assert loaded_keys(window) == {"dashboard", "ai", "heatmap", "equipment"}
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_previously_loaded_hidden_pages_only_compute_latest_result_when_accessed(app, monkeypatch):
    window = create_window(demo_dashboard_source(), chat_enabled=True)
    try:
        window.query_panel.apply_draft()
        heatmap, equipment = window.pages["heatmap"], window.pages["equipment"]
        assert window.route == "dashboard" and heatmap.isHidden() and equipment.isHidden()
        calls = []
        make_tiles, make_data = heatmap_module.equipment_tiles, equipment_module.equipment_data

        def tiles(snapshot, source):
            calls.append(("heatmap", snapshot))
            return make_tiles(snapshot, source)

        def data(snapshot, source):
            calls.append(("equipment", snapshot))
            return make_data(snapshot, source)

        monkeypatch.setattr(heatmap_module, "equipment_tiles", tiles)
        monkeypatch.setattr(equipment_module, "equipment_data", data)
        for site in ("hwaseong", "giheung"):
            window.query_panel.set_draft(QuerySelection(site_key=site))
            window.query_panel.apply_draft()
        latest = window.pages["dashboard"].snapshot
        assert heatmap.snapshot is equipment.snapshot is latest and calls == []
        assert heatmap.tile_buttons
        assert calls == [("heatmap", latest)]
        assert equipment.data.snapshot is latest
        assert calls == [("heatmap", latest), ("equipment", latest)]
        assert heatmap.list_model is not None and equipment.model.ids
        assert len(calls) == 2
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_pending_query_keeps_gui_timers_running_and_initializes_newly_opened_page(delayed_window, app):
    window, source = delayed_window
    ticks = []
    timer = QTimer(window)
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(None))
    timer.start()
    query = window.query_panel.apply_draft()
    gate = source.gates[0]
    until(app, lambda: gate.entered.is_set() and bool(ticks))
    assert gate.thread != get_ident() and not gate.release.is_set()
    assert window.query_request.busy and not source.accepted
    assert loaded_keys(window) == {"dashboard", "ai"}
    heatmap = window.pages["heatmap"]
    assert heatmap.snapshot is None
    gate.release.set()
    until(app, lambda: not window.query_request.busy)
    timer.stop()
    assert source.accepted == [(query, get_ident())]
    assert heatmap.snapshot is window.pages["dashboard"].snapshot
    assert heatmap.snapshot.query is query
    assert loaded_keys(window) == {"dashboard", "ai", "heatmap"}


@pytest.mark.parametrize("old_fails", [False, True])
def test_late_response_cannot_replace_newer_query_or_source(delayed_window, app, old_fails):
    window, source = delayed_window
    first = window.query_panel.apply_draft()
    older, newer = source.gates[:2]
    until(app, older.entered.is_set)
    if old_fails:
        older.error = RuntimeError("이전 요청만 실패")
    window.query_panel.set_draft(QuerySelection(site_key="hwaseong"))
    latest = window.query_panel.apply_draft()
    until(app, newer.entered.is_set)
    assert first is not latest and newer.query is latest
    newer.release.set()
    until(app, lambda: not window.query_request.busy)
    snapshot = window.pages["dashboard"].snapshot
    assert snapshot.query is latest and source.accepted == [(latest, get_ident())]
    older.release.set()
    until(app, older.returned.is_set)
    assert QThreadPool.globalInstance().waitForDone(4000)
    app.processEvents()
    assert window.pages["dashboard"].snapshot is snapshot
    assert source.accepted == [(latest, get_ident())]
    assert source.source_label == snapshot.source_label
    assert loaded_keys(window) == {"dashboard", "ai"}


@pytest.mark.parametrize("accepted_value", [True, None])
def test_rejected_source_copy_reloads_same_current_query_before_display(delayed_window, app, monkeypatch, accepted_value):
    window, source = delayed_window
    attempts, original, initial_source = [], source.accept_query, source.base

    def accept(fresh):
        attempts.append(fresh)
        if len(attempts) == 1:
            return False
        original(fresh)
        return accepted_value

    monkeypatch.setattr(source, "accept_query", accept)
    query = window.query_panel.apply_draft()
    first, retry = source.gates[:2]
    until(app, first.entered.is_set)
    first.release.set()
    until(app, retry.entered.is_set)
    assert window.query_state.applied is query and retry.query is first.query is query
    assert len(attempts) == 1 and source.accepted == [] and source.base is initial_source
    assert window.pages["dashboard"].snapshot is None and window.query_request.busy
    assert not window.header.actions["equipment_list"].isEnabled()
    assert loaded_keys(window) == {"dashboard", "ai"}
    retry.release.set()
    until(app, lambda: not window.query_request.busy)
    assert len(attempts) == 2 and source.base is attempts[1]
    assert source.accepted == [(query, get_ident())]
    assert window.pages["dashboard"].snapshot.query is query
    assert window.header.actions["equipment_list"].isEnabled()


def test_management_save_blocks_starting_new_query(delayed_window, app, monkeypatch):
    window, source = delayed_window
    starts, original = [], window.query_request.start

    def start(loader):
        starts.append(loader)
        return original(loader)

    monkeypatch.setattr(window.query_request, "start", start)
    window.management_saving = True
    try:
        query = window.query_panel.apply_draft()
        app.processEvents()
        assert window.query_state.is_current(query) and not window.query_request.busy
        assert starts == [] and source.accepted == []
        assert window.pages["dashboard"].snapshot is None
        assert loaded_keys(window) == {"dashboard", "ai"}
    finally:
        window.management_saving = False


def test_pending_query_response_is_not_adopted_during_management_save(delayed_window, app):
    window, source = delayed_window
    query = window.query_panel.apply_draft()
    gate = source.gates[0]
    until(app, gate.entered.is_set)
    window.management_saving = True
    try:
        gate.release.set()
        until(app, gate.returned.is_set)
        assert QThreadPool.globalInstance().waitForDone(4000)
        app.processEvents()
        assert window.query_state.is_current(query) and not window.query_request.busy
        assert source.accepted == [] and window.pages["dashboard"].snapshot is None
        assert not window.header.actions["equipment_list"].isEnabled()
        assert loaded_keys(window) == {"dashboard", "ai"}
    finally:
        window.management_saving = False
    app.processEvents()
    assert source.accepted == [] and window.pages["dashboard"].snapshot is None


@pytest.mark.parametrize("boundary", ["catalog", "close"])
def test_invalidated_or_closed_window_discards_pending_source_response(delayed_window, app, boundary):
    window, source = delayed_window
    query = window.query_panel.apply_draft()
    gate = source.gates[0]
    until(app, gate.entered.is_set)
    if boundary == "catalog":
        window.query_panel.set_catalog(source.catalog)
        assert not window.query_state.is_current(query)
    else:
        assert window.close()
        assert not window.isVisible()
    assert not window.query_request.busy
    gate.release.set()
    until(app, gate.returned.is_set)
    assert QThreadPool.globalInstance().waitForDone(4000)
    app.processEvents()
    assert not source.accepted and window.pages["dashboard"].snapshot is None
    assert loaded_keys(window) == {"dashboard", "ai"}
