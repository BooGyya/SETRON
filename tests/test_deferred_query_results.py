"""이미 방문한 숨은 화면도 최신 결과 사용 전까지 계산과 렌더링을 미룬다."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

import setron.ui.equipment as equipment_module
import setron.ui.heatmap as heatmap_module
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.equipment import EquipmentPage
from setron.ui.heatmap import HeatmapPage
from setron.ui.query_state import QuerySelection, QueryState
from setron.ui.theme import install_theme


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    return result


@pytest.fixture(params=["heatmap", "equipment"])
def view(request, app, monkeypatch):
    source = demo_dashboard_source()
    state = QueryState(source.catalog)
    page = HeatmapPage(state, source) if request.param == "heatmap" else EquipmentPage(state, source)
    first = state.apply()
    page.accept_result(first, source.snapshot(first))
    module, name = ((heatmap_module, "equipment_tiles") if request.param == "heatmap"
                    else (equipment_module, "equipment_data"))
    calls, original = [], getattr(module, name)

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, counted)
    yield page, state, source, calls
    page.close()
    page.deleteLater()
    app.processEvents()


def queue_for(page, state, source, site):
    state.set_draft(QuerySelection(site_key=site))
    query = state.apply()
    snapshot = source.snapshot(query)
    assert page.queue_result(query, snapshot)
    return query, snapshot


def result_of(page):
    return page.tiles if isinstance(page, HeatmapPage) else page.data


def test_queue_replaces_pending_reference_without_computing_and_public_access_uses_latest(view):
    page, state, source, calls = view
    older, old_snapshot = queue_for(page, state, source, "hwaseong")
    assert page.snapshot is old_snapshot and calls == []
    latest, snapshot = queue_for(page, state, source, "giheung")
    assert page.snapshot is snapshot and calls == []
    assert not page.queue_result(older, old_snapshot)
    assert page.snapshot is snapshot and calls == []
    result = result_of(page)
    assert calls == [snapshot] and page.__dict__.get("_pending_result") is None
    assert result_of(page) is result and calls == [snapshot]
    if isinstance(page, HeatmapPage):
        assert {tile.site_key for tile in result if tile.goal.value != "HPA · 분석 제외"} == {"giheung"}
        assert set(page.tile_buttons) <= set(page.visible_ids)
    else:
        assert result.snapshot is snapshot and page.model.ids
        assert page.table.model() is page.model
    assert page.snapshot.query is latest


def test_showing_page_applies_pending_result_once(view, app):
    page, state, source, calls = view
    _, snapshot = queue_for(page, state, source, "hwaseong")
    assert not calls
    page.show()
    app.processEvents()
    assert calls == [snapshot] and page.snapshot is snapshot
    page.ensure_result()
    assert calls == [snapshot]


def test_clear_or_invalidate_discards_pending_without_calculating_it(view):
    page, state, source, calls = view
    queue_for(page, state, source, "hwaseong")
    if isinstance(page, HeatmapPage):
        page.invalidate()
        assert page.tiles == () and page.tile_buttons == {}
    else:
        page.clear()
        assert page.data is None and page.model.rowCount() == 0
    assert page.snapshot is None and not page.ensure_result() and calls == []


def test_equipment_filter_change_after_queue_is_not_lost_during_initialization(app):
    source = demo_dashboard_source()
    state = QueryState(source.catalog)
    page = EquipmentPage(state, source)
    try:
        query = state.apply()
        snapshot = source.snapshot(query)
        assert page.queue_result(query, snapshot)
        code = snapshot.selected_records[0].equipment_code
        page.search.setText(code)
        assert page.search.text() == code and page.visible_tiles
        assert all(tile.code == code for tile in page.visible_tiles)
        assert page.data.snapshot is snapshot
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()


def test_selection_materializes_pending_result_before_emitting_request(view):
    page, state, source, calls = view
    query, snapshot = queue_for(page, state, source, None)
    if isinstance(page, HeatmapPage):
        spy = QSignalSpy(page.equipment_selected)
        eid = snapshot.equipment_ids[0]
        page._select(eid)
        assert spy.count() == 1 and spy.at(0)[0].query is query
    else:
        assert page.open_row(0)
        assert page.selected_id in snapshot.equipment_ids and page.mode == "detail"
    assert calls == [snapshot]


def test_heatmap_begin_query_retains_cards_until_latest_queued_projection_is_used(app):
    source = demo_dashboard_source()
    state = QueryState(source.catalog)
    page = HeatmapPage(state, source)
    try:
        first = state.apply()
        page.accept_result(first, source.snapshot(first))
        original = {card.property("groupKey"): card for card in page.group_cards}
        state.set_draft(QuerySelection(site_key="hwaseong"))
        query = state.apply()
        assert page.begin_query(query)
        assert page._card_cache and page.snapshot is None
        snapshot = source.snapshot(query)
        assert page.queue_result(query, snapshot)
        assert page.snapshot is snapshot and page._pending_result is not None
        assert page.tile_buttons and page.rebuilt_tiles == 0
        assert all(card is original[card.property("groupKey")] for card in page.group_cards)
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()
