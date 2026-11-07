"""VER5 Replay 화면과 연결 팝업의 프론트 검증. 합성 자료만 조회한다."""

from dataclasses import replace
from datetime import date
from threading import Event

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.ui.app import create_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.heatmap_data import GoalState
from setron.ui.query_state import QuerySelection, VersionFilter
from setron.ui.replay_data import load_replay
from setron.ui.replay_widgets import ReplayChart
from setron.ui.replay_page import ReplayPage
from setron.ui.theme import ASSETS, svg_size
from test_ui_import_ver5 import own, until  # noqa: F401


def replay_page(own, **kwargs):
    page = own(ReplayPage(demo_dashboard_source(), **kwargs))
    page.resize(1320, 1000)
    assert page.apply_query()
    until(lambda: not page.busy)
    assert page.data is not None
    return page


def test_screen_has_all_replay_controls_and_defaults_to_latest_observation(own):
    page = replay_page(own)
    assert len(page.data.frames) == len(page.date_buttons) == 6
    assert page.index == 5 and page.snapshot_title.text().startswith("2026-10-21")
    assert page.play_button.isEnabled() and not page.pause_button.isEnabled()
    assert len(page.legend_buttons) == 9 and len(page.chart.values) == 9
    assert "2026-05-21" in page.context.text() and "목표 N" in page.context.text()
    assert "기준일 최신" not in page.context.text()
    assert page.heatmap.visible_ids and len(page.heatmap.group_cards) == 9
    assert all(t.data.observation_date == page.frame.day for t in page.heatmap.tile_occurrences)
    assert page.heatmap_title.text() == "선택 시점(2026-10-21)의 설비 Heatmap"


def test_date_click_and_keyboard_update_same_screen_not_common_conditions(own):
    window = own(create_window())
    window.resize(1600, 960)
    before = window.query_state.draft, window.query_state.applied, window.query_state.target_draft
    assert window.nav["replay"].isEnabled() and window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    assert page.data is not None and not window.query_panel.isVisibleTo(window)
    assert not window.header.toolbar.isVisibleTo(window)
    target = page.date_buttons[0]
    target.setFocus()
    QTest.keyClick(target, Qt.Key.Key_Space)
    assert page.index == 0 and page.frame.day == date(2026, 5, 21)
    assert window.route == "replay" and not page.timer.isActive()
    assert (window.query_state.draft, window.query_state.applied, window.query_state.target_draft) == before
    window.navigate("dashboard")
    assert window.header.toolbar.isVisibleTo(window)


def test_play_pause_restart_and_end_use_cached_frames_without_more_reads(own):
    calls = []
    source = demo_dashboard_source()
    def load(request):
        calls.append(request)
        return load_replay(source, request)
    page = replay_page(own, loader=load)
    data = page.data
    assert page.play() and page.index == 0 and page.timer.isActive()
    page.advance()
    assert page.index == 1 and "재생 중" in page.playback_status.text()
    page.pause_button.click()
    assert not page.timer.isActive() and page.play_button.isEnabled()
    page.restart_button.click()
    assert page.index == 0 and not page.timer.isActive()
    assert page.play()
    while page.timer.isActive():
        page.advance()
    assert page.index == 5 and "일시정지" in page.playback_status.text()
    assert page.data is data and len(calls) == 1
    assert 0 <= page.progress.value() <= 1000


def test_real_timer_advances_and_hiding_page_stops_it(own):
    page = replay_page(own)
    page.timer.setInterval(50)
    assert page.play()
    QTest.qWait(80)
    assert page.index > 0
    page.hide()
    index = page.index
    assert not page.timer.isActive()
    QTest.qWait(100)
    assert page.index == index


def test_legend_only_changes_lines_not_scope_totals_or_heatmap(own):
    page = replay_page(own)
    original = page.frame, page.achievement.text(), page.heatmap.visible_ids
    page.legend_buttons["c3"].click()
    assert "c3" not in page.chart.values
    assert (page.frame, page.achievement.text(), page.heatmap.visible_ids) == original
    page.legend_buttons["c3"].click()
    assert "c3" in page.chart.values


def test_version_popup_all_clear_cancel_confirm_and_reapply_without_changing_n(own):
    page = replay_page(own)
    n = dict(page.source.targets)
    page.open_versions()
    dialog = page.version_dialog
    dialog.clear_button.click()
    assert not dialog.confirm_button.isEnabled() and "하나 이상" in dialog.error.text()
    dialog.choices["v1.0.1"].click()
    assert dialog.confirm_button.isEnabled()
    dialog.cancel_button.click()
    assert page.version_dialog is None and page.state.draft.versions is None and not page.dirty
    page.open_versions()
    dialog = page.version_dialog
    dialog.clear_button.click()
    dialog.choices["v1.0.1"].click()
    dialog.confirm_button.click()
    assert page.state.draft.versions == VersionFilter(("v1.0.1",))
    assert page.dirty and page.badge.text() == "재적용 필요" and not page.play_button.isEnabled()
    assert page.apply_query()
    until(lambda: not page.busy)
    assert page.source.targets == n and page.data.request.query.selection.versions == VersionFilter(("v1.0.1",))


def test_changed_conditions_stop_play_and_reset_does_not_apply_or_erase_targets(own):
    page = replay_page(own)
    data, n = page.data, dict(page.source.targets)
    page.play()
    page.combos["site_key"].setCurrentIndex(page.combos["site_key"].findData("hwaseong"))
    assert not page.timer.isActive() and page.dirty and not page.play_button.isEnabled()
    assert page.data is data
    page.reset_button.click()
    assert page.state.draft == QuerySelection() and page.source.targets == n and page.data is data
    assert page.start.text() == "2026-05-21" and page.end.text() == "2026-10-21"


@pytest.mark.parametrize("text", ["20261301", "20260230", "202611", "00000101"])
def test_invalid_date_never_starts_external_loader(own, text):
    calls = []
    page = own(ReplayPage(demo_dashboard_source(), loader=lambda request: calls.append(request)))
    page.start.setText(text)
    assert not page.apply_query() and calls == []
    assert "YYYY-MM-DD" in page.message.text()


def test_empty_single_date_and_zero_match_are_not_playable_or_fake_zero_percent(own):
    page = replay_page(own)
    page.start.setText("2025-01-01")
    page.end.setText("2025-12-31")
    page.apply_query()
    until(lambda: not page.busy)
    assert page.data.frames == () and not page.play_button.isEnabled() and not page.date_buttons
    assert not page.heatmap.visible_ids and "저장된 관측일이 없습니다" in page.message.text()
    page.start.setText("2026-05-21")
    page.end.setText("2026-05-21")
    page.apply_query()
    until(lambda: not page.busy)
    assert len(page.date_buttons) == 1 and not page.play_button.isEnabled()
    page.state.set_draft(replace(page.state.draft, versions=VersionFilter(("not-stored",))))
    page.apply_query()
    until(lambda: not page.busy)
    assert "계산 불가" in page.achievement.text() and page.frame.snapshot.summary.rate is None
    assert page.heatmap.visible_ids == () and not page.play()


def test_slow_request_is_responsive_blocks_duplicate_and_ignores_late_result_after_exit(own):
    gate, ticks = Event(), []
    source = demo_dashboard_source()
    def load(request):
        gate.wait(4)
        return load_replay(source, request)
    page = own(ReplayPage(source, loader=load))
    try:
        assert page.apply_query() and page.busy and not page.apply_button.isEnabled()
        assert not page.apply_query()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: ticks.append(True))
        QTest.qWait(20)
        assert ticks
        page.hide()
        assert not page.busy and page.pending_request is None
        gate.set()
        QTest.qWait(150)
        assert page.data is None
    finally:
        gate.set()


def test_loader_failure_is_sanitized_and_can_retry(own):
    page = own(ReplayPage(demo_dashboard_source(), loader=lambda _: (_ for _ in ()).throw(RuntimeError("secret-token"))))
    assert page.apply_query()
    until(lambda: not page.busy)
    assert page.data is None and "다시 조회" in page.message.text() and "secret" not in page.message.text()
    assert page.apply_button.isEnabled() and not page.play_button.isEnabled()
    page.loader = None
    assert page.apply_query()
    until(lambda: not page.busy)
    assert page.data is not None


def test_tile_opens_same_observation_readonly_drawer_without_navigation_or_writes(own):
    window = own(create_window())
    window.resize(1600, 960)
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    page.select_date(0)
    before = page.data, page.index, page.state.draft
    assert page.open_equipment(1)
    assert page.drawer.isVisibleTo(window) and window.route == "replay"
    assert page.drawer.detail.record.base_date == page.frame.day
    assert len(page.drawer.software_cards) == 3 and not page.drawer.edit_available
    page.drawer.close_panel()
    assert (page.data, page.index, page.state.draft) == before
    assert not page.timer.isActive()


def test_multisw_group_duplicates_do_not_inflate_unique_physical_count(own):
    page = replay_page(own)
    occurrences = [control for control in page.heatmap.tile_occurrences if control.data.equipment_id == 1]
    assert len(occurrences) == 2
    assert page.heatmap.visible_ids.count(1) == 1
    assert len(page.heatmap.visible_ids) == page.frame.snapshot.summary.equipment_count
    assert all(t.data.goal is not GoalState.EXCLUDED for t in page.heatmap.tile_occurrences)


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
def test_replay_has_no_main_horizontal_overflow_and_header_layout_recovers(own, size):
    window = own(create_window())
    window.resize(*size)
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    QTest.qWait(50)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert page.width() <= window.scroll.viewport().width()
    assert page.reset_button.iconSize() == svg_size("replay-ver5/65ae3.svg")
    assert svg_size("replay-ver5/65ae3.svg").width() == 18
    assert (ASSETS / "replay-ver5/65ae3.svg").stat().st_size > 0
    window.navigate("import")
    assert window.header.toolbar.isVisibleTo(window)


def test_invalidate_stops_timer_closes_old_detail_and_rejects_stale_popup(own):
    page = replay_page(own)
    page.open_versions()
    dialog = page.version_dialog
    page.invalidate()
    dialog.accept()
    assert "바뀌었습니다" in dialog.error.text()
    dialog.reject()
    assert page.data is None and not page.timer.isActive() and not page.play_button.isEnabled()


def test_initial_layout_has_no_expanded_chart_blank_and_buttons_keep_figma_heights(own):
    window = own(create_window())
    window.resize(1600, 960)
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    QTest.qWait(100)
    assert page.chart_card.height() < 600
    assert page.chart.y() < 250
    assert page.chart.mapTo(window, QPoint()).y() < 750
    assert all(b.height() == 40 for b in page.date_buttons)
    assert all(b.height() == 36 for b in page.legend_buttons.values())
    page.date_buttons[0].setFocus()
    page.select_date(0)
    page.legend_buttons["c3"].click()
    QTest.qWait(20)
    assert page.date_buttons[0].height() == 40
    assert page.legend_buttons["c3"].height() == 36


def test_replay_ticks_do_not_jump_scroll_or_leave_a_detail_from_another_date(own):
    window = own(create_window())
    window.resize(1600, 960)
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    QTest.qWait(100)
    bar = window.scroll.verticalScrollBar()
    bar.setValue(450)
    assert page.play()
    QTest.qWait(100)
    assert bar.value() == 450
    page.advance()
    QTest.qWait(100)
    assert bar.value() == 450
    page.pause()
    assert page.open_equipment(1)
    page.restart()
    assert not page.drawer.isVisibleTo(window)


def test_chart_breaks_lines_at_unknown_dates_and_clear_removes_previous_values(own):
    page = replay_page(own)
    data = page.data
    gap = replace(data.frames[2], snapshot=replace(data.frames[2].snapshot,
        groups=tuple(g for g in data.frames[2].snapshot.groups if g.group_keys != ("c3",))))
    data = replace(data, frames=data.frames[:2] + (gap,) + data.frames[3:])
    chart = own(ReplayChart())
    chart.show_data(data, {"c3"})
    assert chart.values["c3"][2] is None
    assert len(chart.chart().series()) == 2
    assert [[p.x() for p in series.points()] for series in chart.chart().series()] == [[0, 1], [3, 4, 5]]
    chart.clear()
    assert chart.values == {} and chart.accessibleDescription() == ""


def test_management_save_blocks_replay_and_completion_requires_new_read(own):
    window = own(create_window())
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    page.play()
    management = window.pages["management"]
    management.save_started.emit()
    assert not page.isEnabled() and not page.timer.isActive() and page.data is None
    window.after_management_save(None)
    assert page.isEnabled() and page.data is None and page.badge.text() == "재조회 필요"


def test_app_entry_without_any_configured_n_shows_unknown_instead_of_read_failure(own):
    source = demo_dashboard_source()
    source.targets.clear()
    window = own(create_window(source))
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    assert page.data is not None and page.frame.tiles
    assert "계산 불가" in page.achievement.text() and page.badge.text() == "적용됨"
