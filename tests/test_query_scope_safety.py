"""계층 변경으로 사라진 조회 범위가 전체로 확대되지 않는지 검증한다."""

from dataclasses import replace

import pytest
from PySide6.QtTest import QSignalSpy

from setron.ui.query_panel import QueryPanel
from setron.ui.query_state import QueryCatalog, QuerySelection, QueryState, selection_keys

from test_ui_query import app, catalog  # noqa: F401
from test_app_query_performance import delayed_window, until  # noqa: F401


@pytest.mark.parametrize("field", ["site_key", "fab_key", "line_key", "group_key"])
def test_removed_single_scope_keeps_old_name_until_explicit_reset(catalog, field):
    state = QueryState(catalog)
    selection = QuerySelection("s1", "f1", "l1", "g1")
    state.set_draft(selection)
    old = state.apply()
    changed = {"groups": tuple(group for group in catalog.groups if group.key != "g1")} if field == "group_key" else {
        "lines": tuple(line for line in catalog.lines if line.key != "l1")}
    if field in {"site_key", "fab_key"}:
        changed["fabs"] = tuple(fab for fab in catalog.fabs if fab.key != "f1")
    if field == "site_key":
        changed["sites"] = tuple(site for site in catalog.sites if site.key != "s1")
        changed["lines"] = tuple(line for line in catalog.lines if line.site_key != "s1")
    state.replace_catalog(replace(catalog, **changed))
    assert state.draft == selection and field in state.invalid_selections
    assert "선택 다시 확인" in state.describe(state.draft) and "선택 다시 확인" in state.selection_error
    assert not state.is_current(old)
    with pytest.raises(ValueError, match="선택 다시 확인"):
        state.apply()
    state.reset_draft()
    assert not state.invalid_selections and state.apply().selection == QuerySelection()


def test_stable_key_renames_keep_the_selected_identity(catalog):
    state = QueryState(catalog)
    state.set_draft(QuerySelection("s1", "f1", "l1", "g1"))
    old = state.apply()
    renamed = replace(catalog, sites=tuple(replace(site, label="새 사업장") if site.key == "s1" else site for site in catalog.sites),
                      groups=tuple(replace(group, label="새 설비군") if group.key == "g1" else group for group in catalog.groups))
    state.replace_catalog(renamed)
    assert state.draft == old.selection and not state.invalid_selections and not state.is_current(old)
    assert "새 사업장" in state.apply().description


def test_moved_line_with_incompatible_parent_blocks_original_scope(catalog):
    state = QueryState(catalog)
    state.set_draft(QuerySelection("s1", "f1", "l1"))
    state.replace_catalog(replace(catalog, lines=tuple(replace(line, site_key="s2", fab_key="f2")
                                                      if line.key == "l1" else line for line in catalog.lines)))
    assert state.draft.line_key == "l1" and state.invalid_selections == {"line_key": ("l1",)}
    state.set_as_of(None)
    assert state.invalid_selections
    state.select("site_key", "s2")  # 명시적인 상위 변경은 기존 cascading UX를 유지한다.
    assert not state.invalid_selections and state.draft.line_key == "l1"


def test_partial_multiple_selection_requires_explicit_confirmation_and_does_not_become_all(app, catalog):
    state = QueryState(catalog)
    panel = QueryPanel(state)
    panel.set_draft(QuerySelection(group_key=("g1", "g2")))
    old = panel.apply_draft()
    applied = QSignalSpy(panel.applied)
    panel.set_catalog(replace(catalog, groups=(catalog.groups[0],)))
    assert state.draft.group_key == ("g1", "g2")
    assert panel.apply_draft() is None and applied.count() == 0
    assert panel.status.text() == "선택 다시 확인" and not panel.search_button.isEnabled()
    assert "O2" in panel.combos["group_key"].toolTip() and "Inline O2" in panel.combos["group_key"].toolTip()
    panel._open_filter("group_key")
    popup = panel.filter_popup
    assert popup.selected_keys == ("g1",)
    popup.accept()
    assert state.draft.group_key == "g1" and not state.invalid_selections
    current = panel.apply_draft()
    assert current is not old and selection_keys(current.selection.group_key) == ("g1",)
    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_scope_dependent_target_actions_are_blocked_until_scope_is_repaired(app):
    from setron.ui.app import create_window

    window = create_window()
    try:
        panel = window.query_panel
        panel.set_draft(QuerySelection(group_key="c3"))
        old = panel.apply_draft()
        panel.set_catalog(replace(panel.state.catalog, groups=tuple(group for group in panel.state.catalog.groups if group.key != "c3")))
        assert not panel.goal_button.isEnabled() and not panel.version_button.isEnabled()
        assert window.open_single_targets() is None and window.target_dialog is None
        assert not window.export_current_xlsx("unused.xlsx").saved
        assert panel.state.applied is old and panel.state.draft.group_key == "c3"
        panel.select("group_key", "c4")
        assert panel.goal_button.isEnabled() and panel.version_button.isEnabled()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_multiple_invalid_fields_can_be_repaired_without_discarding_other_stale_fields(catalog):
    state = QueryState(catalog)
    state.set_draft(QuerySelection("s1", "f1", "l1", "g1"))
    state.replace_catalog(replace(catalog, groups=(catalog.groups[1],), lines=(catalog.lines[1], catalog.lines[2])))
    assert {"group_key", "line_key"} <= state.invalid_selections.keys()
    state.select("group_key", "g2")
    assert state.draft.line_key == "l1" and state.invalid_selections == {"line_key": ("l1",)}
    with pytest.raises(ValueError):
        state.select("line_key", "l3")  # f1에는 미지정 Fab의 l3를 임의로 넣을 수 없다.
    state.select("fab_key", None)
    state.select("line_key", "l3")
    assert not state.invalid_selections and state.apply().selection.group_key == "g2"


def test_async_old_scope_response_is_discarded_and_no_broader_query_runs(delayed_window, app):
    window, source = delayed_window
    panel = window.query_panel
    panel.set_draft(QuerySelection(group_key="c3"))
    old = panel.apply_draft()
    gate = source.gates[0]
    until(app, gate.entered.is_set)
    panel.set_catalog(replace(source.catalog, groups=tuple(group for group in source.catalog.groups if group.key != "c3")))
    assert panel.state.draft.group_key == "c3" and panel.apply_draft() is None
    gate.release.set()
    until(app, gate.returned.is_set)
    app.processEvents()
    assert not panel.state.is_current(old) and not source.accepted
    assert window.pages["dashboard"].snapshot is None and source._next_gate == 1
    panel.select("group_key", "c4")
    new = panel.apply_draft()
    next_gate = source.gates[1]
    until(app, next_gate.entered.is_set)
    next_gate.release.set()
    until(app, lambda: not window.query_request.busy)
    assert new.selection.group_key == "c4" and window.pages["dashboard"].snapshot.query is new


def test_unknown_management_commit_blocks_query_exports_and_analysis_until_reconciled(app, tmp_path):
    from setron.ui.app import create_window

    calls = []
    window = create_window(db_exporter=lambda path: calls.append(path), chat_enabled=True)
    try:
        old = window.query_panel.apply_draft()
        page = window.pages["data"]
        page.save_started.emit()
        page.save_uncertain.emit()
        assert window.management_uncertain and not window.management_saving
        assert not window.query_state.is_current(old) and window.query_panel.apply_draft() is None
        assert "저장 결과" in window.notice.text() and "기존값" not in window.notice.text()
        assert not window.pages["ai"].analysis_allowed()
        versions = window.pages["versions"]  # 저장 결과 미확인 뒤 처음 생성한 화면도 쓰기를 차단한다.
        assert not versions.edit_allowed()
        result = window.export_sql(tmp_path / "blocked.sql")
        assert not result.saved and "저장 결과" in result.message and calls == []
        page.save_reconciled.emit()
        assert not window.management_uncertain
        assert versions.edit_allowed()
        assert window.query_panel.apply_draft() is not None
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_replay_and_report_shared_query_state_preserves_removed_scope(app, tmp_path):
    from PySide6.QtCore import QSettings
    from setron.ui.dashboard_demo import demo_dashboard_source
    from setron.ui.replay_page import ReplayPage
    from setron.ui.reports_page import ReportsPage

    source = demo_dashboard_source()
    replay = ReplayPage(source)
    reports = ReportsPage(source, settings=QSettings(str(tmp_path / "reports.ini"), QSettings.Format.IniFormat),
                          ai_settings=QSettings(str(tmp_path / "ai.ini"), QSettings.Format.IniFormat))
    try:
        for page in (replay, reports):
            page.query_panel.set_draft(QuerySelection(group_key="c3"))
            page.query_panel.apply_draft()
        source.catalog = replace(source.catalog, groups=tuple(group for group in source.catalog.groups if group.key != "c3"))
        replay.invalidate()
        reports.invalidate_catalog()
        for page in (replay, reports):
            assert page.state.draft.group_key == "c3" and page.state.invalid_selections
            assert page.query_panel.apply_draft() is None and not page.query_panel.search_button.isEnabled()
        assert not replay.apply_query() and not replay.busy
        assert reports._apply_scope() is None and reports.result is None
    finally:
        for page in (replay, reports):
            page.close()
            page.deleteLater()
        app.processEvents()


def test_late_report_generation_cannot_restore_result_from_an_old_catalog_generation(app, tmp_path):
    from PySide6.QtCore import QSettings
    from setron.ui.dashboard_demo import demo_dashboard_source
    from setron.ui.reports_page import ReportsPage

    source = demo_dashboard_source()
    page = ReportsPage(source, settings=QSettings(str(tmp_path / "reports.ini"), QSettings.Format.IniFormat),
                       ai_settings=QSettings(str(tmp_path / "ai.ini"), QSettings.Format.IniFormat))
    try:
        page._generation_query = page.query_panel.apply_draft()
        page.query_panel.set_catalog(source.catalog)
        page._generated(None, RuntimeError("이전 조회의 늦은 오류"))
        assert page.result is None and page.alert_dialog is None
        assert "다시 확인" in page.generation_status.text()
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()
