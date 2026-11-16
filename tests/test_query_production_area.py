"""공통 Fab 조회는 저장된 생산 영역과 관측일을 사용한다."""

from datetime import date

from setron.features.data_admin.service import SITES_KEY
from setron.ui.database_source import load_dashboard_source
from setron.ui.heatmap_data import equipment_tiles
from setron.ui.query_panel import QueryPanel
from setron.ui.query_state import UNASSIGNED, AppliedQuery, QuerySelection, QueryState
from setron.ui.replay_data import ReplayRequest, load_replay
from setron.ui.target_data import initial_targets
from test_release_revision import observation, revision_storage  # noqa: F401
from test_ui_query import app  # noqa: F401


EARLY, LATE = date(2026, 10, 1), date(2026, 10, 15)


def source_with_saved_areas(storage):
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 1}, {"group_name": "M-O2"})
        tx.insert("app_setting", {"setting_key": SITES_KEY, "setting_value": {"sites": ["평택"]}})
    for code, day, area in (("MOVING", EARLY, "MEMORY"), ("MOVING", LATE, "FOUNDRY"),
                             ("MEMORY", LATE, "MEMORY"), ("UNKNOWN", LATE, None)):
        snapshot_id, _ = observation(storage, code=code, day=day)
        with storage.transaction() as tx:
            tx.update("equipment_snapshot", {"snapshot_id": snapshot_id}, {"production_area": area})
    return load_dashboard_source(storage)


def query(fab=None, day=None, *, targets=None):
    return AppliedQuery(QuerySelection(fab_key=fab, as_of=day), 1, 0, "합성 자료 조회", targets)


def codes(snapshot):
    return {record.equipment_code for record in snapshot.selected_records}


def test_database_fab_choices_are_shared_across_sites_and_filter_observation_date(revision_storage):
    source = source_with_saved_areas(revision_storage)
    state = QueryState(source.catalog)
    assert [(option.key, option.label) for option in state.available_fabs()] == [
        ("MEMORY", "Memory"), ("FOUNDRY", "Foundry")]
    state.select("fab_key", "MEMORY")
    state.select("site_key", "pyeongtaek")
    assert state.draft.fab_key == "MEMORY"
    state.select("site_key", "hwaseong")
    assert {option.fab_key for option in state.available_lines()} == {"MEMORY"}
    assert codes(source.snapshot(query("MEMORY", EARLY))) == {"MOVING"}
    assert codes(source.snapshot(query("MEMORY", LATE))) == {"MEMORY"}
    assert codes(source.snapshot(query("FOUNDRY", LATE))) == {"MOVING"}
    assert codes(source.snapshot(query(UNASSIGNED, LATE))) == {"UNKNOWN"}
    # 목표를 전달해 생성하는 내부 공급자도 관측일별 생산 영역을 그대로 공유한다.
    assert codes(source.snapshot(query("MEMORY", EARLY, targets=initial_targets(source)))) == {"MOVING"}


def test_heatmap_and_replay_reuse_saved_fab_location(revision_storage):
    source = source_with_saved_areas(revision_storage)
    early = source.snapshot(query("MEMORY", EARLY))
    tiles = equipment_tiles(early, source)
    assert len(tiles) == 1 and "Fab Memory" in tiles[0].location
    data = load_replay(source, ReplayRequest(EARLY, LATE, query("MEMORY")))
    assert [codes(frame.snapshot) for frame in data.frames] == [{"MOVING"}, {"MEMORY"}]


def test_common_query_popup_offers_memory_and_foundry_with_black_bulk_button(app, revision_storage):
    source = source_with_saved_areas(revision_storage)
    panel = QueryPanel(QueryState(source.catalog))
    panel.show()
    try:
        panel.combos["fab_key"].click()
        popup = panel.filter_popup
        assert set(popup.checks) == {UNASSIGNED, "MEMORY", "FOUNDRY"}
        assert popup.checks["MEMORY"].accessibleName() == "Memory"
        assert popup.checks["FOUNDRY"].accessibleName() == "Foundry"
        assert "#000000" in popup.all_button.styleSheet() and "#ffffff" in popup.all_button.styleSheet()
    finally:
        if panel.filter_popup:
            panel.filter_popup.reject()
        panel.close()
        panel.deleteLater()
        app.processEvents()
