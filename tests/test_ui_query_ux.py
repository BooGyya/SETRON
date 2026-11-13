"""조회 초안 안내와 좁은 화면에서도 펼쳐진 공통 필터를 검증한다."""

from datetime import date, timedelta

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from setron.ui.app import create_window
from setron.ui.query_panel import QueryPanel
from setron.ui.query_state import QuerySelection, QueryState, VersionFilter, preview_catalog
from test_ui_import_ver5 import own  # noqa: F401


def test_changed_site_is_visible_as_pending_without_replacing_displayed_data(own):
    window = own(create_window())
    window.resize(1600, 960)
    panel = window.query_panel
    panel.apply_draft()
    before = window.pages["dashboard"].snapshot
    panel.select("site_key", "giheung")
    assert panel.status.isVisibleTo(window) and "미적용" in panel.status.text()
    assert "조회 버튼" in panel.status.toolTip()
    assert window.pages["dashboard"].snapshot is before
    assert before.query.selection.site_key is None
    panel.search_button.click()
    after = window.pages["dashboard"].snapshot
    assert after is not before and after.query.selection.site_key == "giheung"
    assert not panel.status.isVisibleTo(window)


def test_filter_stays_expanded_and_keeps_applied_scope_until_search_on_small_window(own):
    window = own(create_window())
    window.resize(960, 600)
    panel = window.query_panel
    QTest.qWait(40)
    search, reset = panel.search_button, panel.reset_button
    panel.apply_draft()
    QTest.qWait(30)
    assert panel.fields_row.isVisibleTo(window) and panel.actions_row.isVisibleTo(window)
    assert not any(control.text() in ("필터 접기", "필터 펼치기") for control in panel.findChildren(QPushButton))
    assert search is panel.search_button and reset is panel.reset_button
    assert search.isVisibleTo(window) and reset.isVisibleTo(window)
    panel.select("site_key", "giheung")
    assert panel.status.isVisibleTo(window) and window.query_state.applied.selection.site_key is None
    search.click()
    assert window.query_state.applied.selection.site_key == "giheung"
    assert panel.fields_row.isVisibleTo(window) and panel.actions_row.isVisibleTo(window)
    assert panel.query_tools.parentWidget() is panel.fields_row
    assert window.scroll.horizontalScrollBar().maximum() == 0
    reset.click()
    assert window.query_state.draft.site_key is None
    assert window.query_state.applied.selection.site_key == "giheung"
    assert panel.status.isVisibleTo(window)


def test_period_start_only_change_is_pending_until_search(own):
    state = QueryState(preview_catalog())
    panel = own(QueryPanel(state, period=True))
    start, end = date(2026, 5, 21), date(2026, 10, 21)
    panel.enable_date_range(start, end)
    previous = panel.apply_draft()
    changed = start + timedelta(days=10)
    panel.range_button.set_range(changed, end)
    panel.range_button.range_changed.emit(changed, end)
    assert state.applied is previous and not state.has_pending_changes
    assert panel.status.isVisible() and "미적용" in panel.status.text()
    assert start.isoformat() in panel.status.toolTip()
    assert changed.isoformat() in panel.status.toolTip()
    panel.apply_draft()
    assert not panel.status.isVisible()
    assert panel._applied_range == (changed, end)


def test_new_trend_view_does_not_claim_pending_until_user_changes_range(own):
    start, end = date(2026, 5, 21), date(2026, 10, 21)
    state = QueryState(preview_catalog())
    state.set_draft(QuerySelection(as_of=end))
    panel = own(QueryPanel(state))
    panel.apply_draft()
    panel.enable_date_range(start, end)
    assert not panel.status.isVisible()
    panel.range_button.set_range(start + timedelta(days=1), end)
    panel.range_button.range_changed.emit(start + timedelta(days=1), end)
    assert not state.has_pending_changes and panel.status.isVisible()
    panel.apply_draft()
    panel.disable_date_range()
    assert panel._range_signature() is None
    assert panel.date_edit.isVisible() and panel.date_edit.date().toPython() == end
    panel.set_version_filter(VersionFilter(("v1",)))
    assert panel.version_pending is panel.status and panel.status.isVisible()


def test_resizing_width_or_height_never_hides_common_filter_fields(own):
    window = own(create_window())
    window.resize(1600, 960)
    panel = window.query_panel
    panel.apply_draft()
    for size in ((1600, 600), (1600, 960), (960, 600), (1600, 580)):
        window.resize(*size)
        QTest.qWait(30)
        assert panel.fields_row.isVisibleTo(window) and panel.actions_row.isVisibleTo(window)
        assert all(control.isVisibleTo(window) for control in panel.combos.values())
        assert panel.query_tools.parentWidget() is panel.fields_row
        assert panel.search_button.isVisibleTo(window) and panel.reset_button.isVisibleTo(window)
        assert window.scroll.horizontalScrollBar().maximum() == 0
