"""분류 캐시의 수명과 화면 밖 타일의 지연 구성이 판정을 바꾸지 않는지 검증한다."""

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QScrollArea
from shiboken6 import isValid

import setron.ui.heatmap as heatmap
from setron.features.risk.schemas import RiskStatus
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.heatmap import HeatmapPage
from setron.ui.query_state import QueryState
from setron.ui.theme import install_theme


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    return result


@pytest.fixture
def view(app):
    source = demo_dashboard_source()
    state = QueryState(source.catalog)
    snapshot = source.snapshot(state.apply())
    page = HeatmapPage(state, source, detail_connected=True)
    host = QScrollArea()
    host.setWidgetResizable(True)
    host.setWidget(page)
    host.resize(1100, 700)
    host.show()
    page.accept_result(snapshot.query, snapshot)
    app.processEvents()
    yield page, host
    host.close()
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_grouping_round_trip_reuses_cards_and_updates_cached_risk_and_selection(view, app):
    page, host = view
    cards, controls = tuple(page.group_cards), tuple(page.tile_occurrences)
    target = page.tiles[0]
    page.select_equipment(target.equipment_id)
    page.set_grouping("group")
    updated = replace(target, risk=RiskStatus.RISK if target.risk is not RiskStatus.RISK else RiskStatus.NO_RISK)
    page.present_snapshot(page.snapshot, tiles=(updated, *page.tiles[1:]))
    page.select_equipment(target.equipment_id)
    page.set_grouping("site")
    assert tuple(page.group_cards) == cards and tuple(page.tile_occurrences) == controls
    assert page.rebuilt_tiles == 0
    matches = [control for control in controls if control.data.equipment_id == target.equipment_id]
    assert matches and all(control.data == updated and control.isChecked() for control in matches)
    assert all(control.toolTip() == updated.description for control in matches)
    assert page.tile_body.count() == len(cards)
    assert all(not card.isHidden() for card in cards)
    app.processEvents()


def test_cache_is_bounded_and_clear_releases_inactive_cards(view, app, monkeypatch):
    page, host = view
    original = tuple(page.group_cards)
    for mode in ("group", "fab", "line"):
        page.set_grouping(mode)
    assert len(page._card_cache) <= heatmap.CARD_CACHE_LIMIT
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert all(not isValid(card) for card in original)
    monkeypatch.setattr(heatmap, "CARD_CACHE_TILE_LIMIT", len(page.tile_occurrences))
    page.set_grouping("group")
    assert len(page._card_cache) == 1
    retained = [entry[1] for entries in page._card_cache.values() for entry in entries.values()]
    page.clear_snapshot()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert page.tile_occurrences == [] and page.tile_body.count() == 0
    assert not page._card_cache
    assert all(not isValid(card) for card in retained)


def test_offscreen_tile_builds_latest_content_when_scrolled_into_view(view, app, monkeypatch):
    page, host = view
    monkeypatch.setattr(heatmap, "DEFERRED_TILE_THRESHOLD", 0)
    page.clear_snapshot()
    source = page.source
    snapshot = source.snapshot(page.state.applied)
    page.accept_result(snapshot.query, snapshot)
    app.processEvents()
    pending = [control for control in page.tile_occurrences if not control._content_ready]
    assert pending
    control = pending[-1]
    assert control.layout() is None and control.isVisibleTo(page)
    changed = replace(control.data, code="LATEST-OFFSCREEN", risk=RiskStatus.RISK)
    control.set_data(changed)
    control.update_appearance(control._families)
    host.ensureWidgetVisible(control)
    app.processEvents()
    assert control._content_ready and control.code_label is not None
    assert control.toolTip() == control.accessibleName() == changed.description
    assert control.code_label.text().startswith("LATEST")
    assert control.version_label.text()
    assert control.parentWidget().rect().contains(control.geometry())


def test_lazy_tile_keeps_keyboard_activation_and_focus_return(view, app, monkeypatch):
    page, host = view
    monkeypatch.setattr(heatmap, "DEFERRED_TILE_THRESHOLD", 0)
    page.set_grouping("group")
    control = next(iter(page.tile_buttons.values()))
    spy = QSignalSpy(page.equipment_selected)
    host.ensureWidgetVisible(control)
    control.setFocus()
    app.processEvents()
    QTest.keyClick(control, Qt.Key.Key_Space)
    assert spy.count() == 1 and spy.at(0)[0].equipment_id == control.data.equipment_id
    page.set_grouping("site")
    page.set_grouping("group")
    assert page.tile_buttons[control.data.equipment_id] is control
    control.setFocus()
    assert app.focusWidget() is control and control.isChecked()


def test_new_query_hides_old_result_and_reuses_matching_cards_with_latest_risk(view, app):
    page, host = view
    cards, controls = tuple(page.group_cards), tuple(page.tile_occurrences)
    target = page.tiles[0]
    query = page.state.apply()
    spy = QSignalSpy(page.equipment_selected)
    assert page.begin_query(query)
    assert page.snapshot is None and page.visible_ids == () and page.tile_body.count() == 0
    assert all(card.isHidden() for card in cards)
    page._select(target.equipment_id)
    assert spy.count() == 0
    page.source.risks[target.equipment_id] = RiskStatus.RISK
    snapshot = page.source.snapshot(query)
    assert page.accept_result(query, snapshot)
    assert tuple(page.group_cards) == cards and tuple(page.tile_occurrences) == controls
    assert page.rebuilt_tiles == 0
    assert all(control.data.risk is RiskStatus.RISK for control in controls
               if control.data.equipment_id == target.equipment_id)
    page.invalidate()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not page._card_cache and all(not isValid(card) for card in cards)
