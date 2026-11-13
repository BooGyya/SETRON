"""VER5 Replay 화면과 연결 팝업의 프론트 검증. 합성 자료만 조회한다."""

from dataclasses import replace
from datetime import date
from threading import Event

import pytest
from PySide6.QtCore import QDate, QPoint, QPointF, QSignalBlocker, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QStyle, QStyleOptionSlider, QToolTip

from setron.features.version.groups import Slot, VersionGroups
from setron.ui.app import create_window
from setron.ui.appearance_data import Appearance
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import CompareStatus
from setron.ui.heatmap import HeatmapPage
from setron.ui.heatmap_data import GoalState, Tier
from setron.ui.heatmap_grouping import GROUPING_MODES
from setron.ui.observation_interval import interval_days
from setron.ui.query_state import QuerySelection, VersionFilter
from setron.ui.replay_data import load_replay
from setron.ui.replay_widgets import CURRENT_MARKER, CURRENT_SUFFIX, FUTURE_SUFFIX, POINT_SUFFIX, ReplayChart
from setron.ui.replay_page import ReplayPage
from setron.ui.version_filter_dialog import choice_caption
from test_ui_import_ver5 import own, until  # noqa: F401


def replay_page(own, **kwargs):
    page = own(ReplayPage(demo_dashboard_source(), **kwargs))
    page.resize(1320, 1000)
    until(lambda: page.data is not None and not page.busy)
    assert page.data is not None
    return page


def set_period(page, start, end):
    with QSignalBlocker(page.start), QSignalBlocker(page.end):
        page.start.setDate(QDate.fromString(start, "yyyy-MM-dd"))
        page.end.setDate(QDate.fromString(end, "yyyy-MM-dd"))


def test_first_show_and_enter_start_one_automatic_request(own):
    source, gate, calls = demo_dashboard_source(), Event(), []

    def load(request):
        calls.append(request)
        assert gate.wait(10)
        return load_replay(source, request)

    page = own(ReplayPage(source, loader=load))
    try:
        until(lambda: len(calls) == 1)
        assert page.busy and page.state.applied is calls[0].query
        assert not page.enter() and not page.apply_query()
        QTest.qWait(20)  # showEvent의 지연 진입도 같은 실행 중 요청을 재시작하지 않는다.
        assert len(calls) == 1
        gate.set()
        until(lambda: page.data is not None and not page.busy)
        assert page.data.request is calls[0] and page.frames and not page.dirty
        assert page.playback_controls.date_label.text() == f"재생일 {page.frame.day}"
        assert page.playback_controls.frame_label.text() == "6 / 6"
        assert not page.enter() and len(calls) == 1
    finally:
        gate.set()


def test_navigation_cancel_then_reenter_resumes_applied_request_and_rejects_old_result(own, monkeypatch):
    gates, calls, returned = (Event(), Event()), [], {}
    actual_load = load_replay

    def load(source, request):
        index = len(calls)
        calls.append(request)
        assert gates[index].wait(10)
        result = actual_load(source, request)
        returned[index] = result
        return result

    monkeypatch.setattr("setron.ui.replay_page.load_replay", load)
    window = own(create_window())
    try:
        window.navigate("replay")
        page = window.pages["replay"]
        until(lambda: len(calls) == 1)
        applied = page.state.applied
        window.navigate("dashboard")
        assert not page.busy and page.data is None and page.pending_request is None
        assert page.state.applied is applied
        window.navigate("replay")
        until(lambda: len(calls) == 2)
        assert calls[1] is calls[0] and page.state.applied is applied
        gates[1].set()
        until(lambda: page.data is not None and not page.busy)
        current = page.data
        assert current is returned[1] and page.chart.values and page.frames
        gates[0].set()
        until(lambda: 0 in returned)
        QTest.qWait(30)
        assert page.data is current and page.data is not returned[0]
        assert page.state.applied is applied and window.route == "replay"
    finally:
        for gate in gates:
            gate.set()


def test_reenter_with_cached_data_redraws_current_frame_without_reading_again(own):
    source, calls = demo_dashboard_source(), []
    page = replay_page(own, loader=lambda request: calls.append(request) or load_replay(source, request))
    page.select_date(1)
    data, frame, index = page.data, page.frame, page.index
    page.hide()
    page.chart.clear()
    page.playback_controls.clear_context()
    assert not page.chart.values and page.playback_controls.frame_label.text() == "0 / 0"
    page.show()
    until(lambda: bool(page.chart.values) and page.chart.current == index)
    assert page.data is data and page.frame is frame and page.index == index and len(calls) == 1
    assert page.playback_controls.date_label.text() == f"재생일 {frame.day}"
    assert page.playback_controls.frame_label.text() == f"{index + 1} / {len(page.frames)}"


@pytest.mark.parametrize("availability", ["visible", "hidden", "disabled"])
def test_invalidated_replay_loads_only_when_visible_and_enabled(own, availability):
    source, calls = demo_dashboard_source(), []
    page = replay_page(own, loader=lambda request: calls.append(request) or load_replay(source, request))
    request = page.data.request
    if availability == "hidden":
        page.hide()
    elif availability == "disabled":
        page.setEnabled(False)
    page.invalidate()
    assert page.data is None and not page.frames
    if availability != "visible":
        QTest.qWait(30)
        assert not page.busy and len(calls) == 1
        if availability == "hidden":
            page.show()
        else:
            # 저장 완료 경로도 다시 활성화한 뒤 현재 자료를 무효화해 갱신한다.
            page.setEnabled(True)
            page.invalidate()
    until(lambda: page.data is not None and not page.busy)
    assert len(calls) == 2 and calls[1] is request and page.data.request is request
    assert page.chart.values and page.playback_controls.frame_label.text() == "6 / 6"


@pytest.mark.parametrize("edited", ["draft", "period"])
def test_canceled_entry_does_not_auto_apply_unsubmitted_inputs(own, edited):
    source, gate, calls, old_finished = demo_dashboard_source(), Event(), [], Event()

    def load(request):
        calls.append(request)
        if len(calls) == 1:
            assert gate.wait(10)
        result = load_replay(source, request)
        if len(calls) == 1:
            old_finished.set()
        return result

    page = own(ReplayPage(source, loader=load))
    try:
        until(lambda: len(calls) == 1)
        applied = page.state.applied
        page.hide()
        if edited == "draft":
            page.query_panel.select("site_key", "hwaseong")
        else:
            set_period(page, "2026-06-01", "2026-08-21")
        draft, period = page.state.draft, (page.start.date(), page.end.date())
        page.show()
        QTest.qWait(40)
        assert len(calls) == 1 and not page.busy and page.data is None
        assert page.state.applied is applied and page.state.draft == draft
        assert (page.start.date(), page.end.date()) == period
        gate.set()
        until(old_finished.is_set)
        QTest.qWait(20)
        assert page.data is None
        page.query_panel.search_button.click()
        until(lambda: page.data is not None and not page.busy)
        assert len(calls) == 2 and page.data.request.query.selection == draft
        assert page.data.request.start == period[0].toPython() and page.data.request.end == period[1].toPython()
        assert not page.dirty
    finally:
        gate.set()


@pytest.mark.parametrize("edited", ["draft", "period", "targets"])
def test_inflight_result_retains_new_inputs_and_disables_stale_replay_until_manual_apply(own, edited):
    page = replay_page(own)
    gate, calls = Event(), []

    def load(request):
        calls.append(request)
        assert gate.wait(10)
        return load_replay(page.source, request)

    page.loader = load
    try:
        assert page.apply_query()
        until(lambda: len(calls) == 1)
        submitted = calls[0]
        if edited == "draft":
            page.query_panel.select("site_key", "hwaseong")
        elif edited == "period":
            set_period(page, "2026-06-01", "2026-08-21")
        else:
            page.state.set_target_draft(())
        draft, targets = page.state.draft, page.state.target_draft
        period = page.start.date(), page.end.date()
        gate.set()
        until(lambda: page.data is not None and not page.busy)
        assert page.data.request is submitted and page.dirty
        assert page.state.draft == draft and page.state.target_draft == targets
        assert (page.start.date(), page.end.date()) == period
        assert "데이터 조회" in page.message.text() and not page.play_button.isEnabled()
        assert not page.select_date(0) and not page.enter() and len(calls) == 1
        page.hide()
        page.show()
        QTest.qWait(20)
        assert page.dirty and len(calls) == 1
        page.query_panel.search_button.click()
        until(lambda: not page.busy and not page.dirty)
        assert len(calls) == 2 and page.data.request is calls[1]
    finally:
        gate.set()


@pytest.mark.parametrize("changed_scope", [False, True])
def test_catalog_refresh_rebinds_only_unchanged_submitted_scope(own, changed_scope):
    source, calls = demo_dashboard_source(), []
    page = own(ReplayPage(source, loader=lambda request: calls.append(request) or load_replay(source, request)))
    until(lambda: page.data is not None and not page.busy)
    page.query_panel.select("site_key", "hwaseong")
    assert page.apply_query()
    until(lambda: page.data is not None and not page.busy)
    request = page.data.request
    if changed_scope:
        source.catalog = replace(source.catalog,
                                 sites=tuple(s for s in source.catalog.sites if s.key != "hwaseong"),
                                 fabs=tuple(f for f in source.catalog.fabs if f.site_key != "hwaseong"),
                                 lines=tuple(line for line in source.catalog.lines if line.site_key != "hwaseong"))
    else:
        source.catalog = replace(source.catalog, source_label="현재 자료의 조회 선택지")
    page.invalidate()
    if changed_scope:
        QTest.qWait(40)
        assert page.data is None and not page.busy and len(calls) == 2
        assert page.state.applied is request.query and page.state.draft.site_key is None
    else:
        until(lambda: page.data is not None and not page.busy)
        assert len(calls) == 3 and page.data.request.query is not request.query
        assert page.data.request.query.selection == request.query.selection
        assert page.data.request.start == request.start and page.data.request.end == request.end


def test_screen_has_all_replay_controls_and_defaults_to_latest_observation(own):
    page = replay_page(own)
    assert len(page.data.frames) == len(page.frames) == 6 and page.step is None
    assert page.index == 5 and page.frame.day == date(2026, 10, 21)
    assert page.play_button.isEnabled() and not page.pause_button.isEnabled()
    assert len(page.legend_buttons) == 9 and page.legend_host.isHidden()
    assert page.grouping_mode == page.heatmap.grouping_mode == "site"
    assert [page.grouping_tabs.tabText(i) for i in range(page.grouping_tabs.count())] == [caption for _, caption in GROUPING_MODES]
    assert set(page.chart.values) == {("site", tile.site_key) for frame in page.frames for tile in frame.tiles}
    assert page.heatmap.view is None and page.heatmap.visible_ids == ()  # 숨은 보기는 처음 열 때 만든다.
    assert set(page.query_panel.combos) == {"site_key", "fab_key", "line_key", "group_key"}
    assert [b.text() for b in page.legend_tools] == ["전체 해제"]  # 버튼 하나로 선택·해제 전환
    # 요청 2-1: 날짜 칩 대신 재생 간격
    assert [b.text() for b in page.interval_buttons.values()] == ["지정 일수", "1주", "1개월", "3개월"]
    assert page.interval_buttons[None].isChecked()
    assert [page.view_controls.row_body.itemAt(i).widget() for i in range(3)] == [
        page.view_tabs, page.grouping_tabs, page.timeline]
    assert page.timeline.caption_label.isHidden()
    # 요청 3-3: 재생 버튼은 기호, 이름은 그대로
    assert [(b.text(), b.accessibleName()) for b in (page.play_button, page.pause_button, page.restart_button)] == [
        ("▶", "재생"), ("‖", "일시정지"), ("↺", "처음부터")]
    # 기호가 앱 글꼴에 있어야 네모로 깨지지 않는다.
    metrics = QFontMetrics(page.play_button.font())
    assert all(metrics.inFontUcs4(ord(b.text())) for b in (page.play_button, page.pause_button, page.restart_button))
    assert page.start.text() == "2026-05-21" and page.end.text() == "2026-10-21"
    assert page.query_panel.applied_summary.isHidden()
    assert page.version_button.text() == "조회 버전 설정"
    page.view_tabs.setCurrentIndex(1)
    assert page.heatmap.visible_ids
    assert {tuple(card.property("groupKey")) for card in page.heatmap.group_cards} == {
        ("site", tile.site_key) for tile in page.frame.tiles}
    first_card = page.heatmap.group_cards[0]
    heading = first_card.findChild(QLabel, "heatmapGroupHeading")
    site_key = tuple(first_card.property("groupKey"))[1]
    assert heading.text() == next(tile.site_label for tile in page.frame.tiles if tile.site_key == site_key)
    assert heading.alignment() == Qt.AlignmentFlag.AlignCenter and heading.font().pixelSize() == 18
    assert " / " not in heading.text() and not heading.text().endswith("대")
    assert all(t.data.observation_date <= page.frame.day for t in page.heatmap.tile_occurrences)
    # R-02: 범례는 타일과 같은 5단계 색 + 위험 후보 테두리
    legend = [item.accessibleName() for item in page.heatmap.legend.items]
    assert legend == ["범례 N+1 이상", "범례 N", "범례 N-1", "범례 N-2 이하", "범례 비교 불가 · HPA", "범례 위험 후보"]
    assert "이날 저장 " in page.achievement.toolTip()


def test_chart_point_selects_date_without_touching_common_conditions(own):
    window = own(create_window())
    window.resize(1600, 960)
    before = window.query_state.draft, window.query_state.applied, window.query_state.target_draft
    assert window.nav["replay"].isEnabled() and window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    assert page.data is not None and not window.query_panel.isVisibleTo(window)
    assert not window.header.toolbar.isVisibleTo(window)
    page.chart.date_selected.emit(0)  # 그래프 점 누르기 (날짜 칩은 없앴다, 요청 2-1)
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
    assert page.position_slider.value() == 0
    page.advance()
    assert page.index == 1 and page.pause_button.isEnabled()
    assert page.position_slider.value() == 1
    page.pause_button.click()
    assert not page.timer.isActive() and page.play_button.isEnabled()
    page.restart_button.click()
    assert page.index == 0 and not page.timer.isActive()
    assert page.play()
    while page.timer.isActive():
        page.advance()
    assert page.index == 5 and not page.pause_button.isEnabled()
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


@pytest.mark.parametrize("playing", [False, True])
def test_dragging_playback_position_commits_once_and_keeps_playback_mode(own, monkeypatch, playing):
    page = replay_page(own)
    if playing:
        assert page.play()
    slider, original = page.position_slider, page.index
    option = QStyleOptionSlider()
    slider.initStyleOption(option)
    handle = slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                          QStyle.SubControl.SC_SliderHandle, slider)
    rendered, render = [], page._render_frame

    def count_render():
        rendered.append(page.index)
        render()

    monkeypatch.setattr(page, "_render_frame", count_render)
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=handle.center())
    assert not page.timer.isActive()
    target = QPoint(handle.width() // 2 + (slider.width() - handle.width()) * 3 // 5, handle.center().y())
    QTest.mouseMove(slider, target)
    selected = slider.sliderPosition()
    assert selected != original and 0 < selected < slider.maximum()
    assert page.index == original and rendered == []
    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=target)
    assert page.index == selected and rendered == [selected]
    assert page.chart.current == selected
    assert page.timer.isActive() == playing
    assert slider.value() == selected
    assert page.playback_controls.date_label.text() == f"재생일 {page.frame.day}"


@pytest.mark.parametrize("view", [0, 1])
def test_playback_position_keys_follow_frames_and_do_not_reload(own, view):
    source, calls = demo_dashboard_source(), []
    page = replay_page(own, loader=lambda request: calls.append(request) or load_replay(source, request))
    page.view_tabs.setCurrentIndex(view)
    slider, data = page.position_slider, page.data
    assert (slider.minimum(), slider.maximum(), slider.value()) == (0, len(page.frames) - 1, page.index)
    slider.setFocus()
    QTest.keyClick(slider, Qt.Key.Key_Home)
    assert page.index == 0 and not page.timer.isActive()
    QTest.keyClick(slider, Qt.Key.Key_Right)
    assert page.index == 1
    if view == 0:
        assert page.chart.current == 1
    else:
        assert page.heatmap.view.snapshot is page.frame.snapshot
    assert page.play()
    QTest.keyClick(slider, Qt.Key.Key_Right)
    assert page.index == 2 and page.timer.isActive()
    QTest.keyClick(slider, Qt.Key.Key_End)
    assert page.index == slider.maximum() and not page.timer.isActive()
    assert page.data is data and len(calls) == 1
    page.restart()
    assert slider.value() == 0
    page.advance()
    assert slider.value() == page.index
    page.set_interval("week")
    assert slider.maximum() == len(page.frames) - 1 and slider.value() == page.index
    page.invalidate()
    assert not slider.isEnabled() and (slider.minimum(), slider.maximum(), slider.value()) == (0, 0, 0)


def test_group_buttons_filter_lines_achievement_and_heatmap_with_one_toggle_button(own):
    """요청 2-2: 설비군 버튼에서 고른 설비군으로 그래프 선·달성률·히트맵을 함께 좁힌다.

    전체 선택·해제는 버튼 하나: 모두 골라져 있으면 누를 때 전체 해제, 아니면 전체 선택 (11-10 요청).
    """
    page = replay_page(own)
    page.set_grouping("group")
    page.view_tabs.setCurrentIndex(1)
    full = page.achievement.text(), page.heatmap.visible_ids
    page.view_tabs.setCurrentIndex(0)
    toggle = page.toggle_all_button
    group = page.legend_buttons["c3"]
    assert toggle.text() == "전체 해제" and toggle.isChecked()
    filled = group.grab().toImage().pixelColor(8, group.height() // 2)
    toggle.click()
    assert toggle.text() == "전체 선택" and not toggle.isChecked()
    assert not any(b.isChecked() for b in page.legend_buttons.values())
    assert group.grab().toImage().pixelColor(8, group.height() // 2) != filled
    assert page.chart.values == {} and page.heatmap.visible_ids == full[1]  # 숨은 히트맵은 갱신하지 않는다.
    page.view_tabs.setCurrentIndex(1)
    assert page.heatmap.group_cards == [] and page.heatmap.visible_ids == ()
    page.view_tabs.setCurrentIndex(0)
    assert page.achievement.text() == "전체 적용률 —" and page.progress.value() == 0
    assert "하나 이상" in page.achievement.toolTip()
    page.legend_buttons["c3"].click()
    cell = next(g for g in page.frame.snapshot.groups if g.group_keys == ("c3",))
    assert set(page.chart.values) == {"c3"}
    page.view_tabs.setCurrentIndex(1)
    assert len(page.heatmap.group_cards) == 1
    assert set(page.heatmap.visible_ids) == set(cell.equipment_ids)
    assert page.achievement.text() == f"전체 적용률 {cell.summary.rate:.1%}"
    assert (f"등록 SW 목표 달성 {cell.summary.compliant} / "
            f"{cell.summary.compliant + cell.summary.below}건") in page.achievement.toolTip()
    assert "HPA" not in page.achievement.toolTip()
    assert toggle.text() == "전체 선택"  # 일부만 골라져 있으면 누를 때 전체 선택
    page.view_tabs.setCurrentIndex(0)
    toggle.click()
    assert toggle.text() == "전체 해제" and toggle.isChecked()
    assert all(b.isChecked() for b in page.legend_buttons.values())
    assert len(page.chart.values) == 9
    page.view_tabs.setCurrentIndex(1)
    assert (page.achievement.text(), page.heatmap.visible_ids) == full


def test_group_buttons_hide_absent_groups_and_bulk_only_changes_available_groups(own):
    source = demo_dashboard_source()
    source.records = tuple(record for record in source.records
                           if source.locations[record.equipment_id].site_key != "hwaseong"
                           or not any(pm.group_id in source.bindings["c4"] for pm in record.pms))
    page = own(ReplayPage(source))
    until(lambda: page.data is not None and not page.busy)
    page.set_grouping("group")
    page.query_panel.select("site_key", "hwaseong")
    page.query_panel.search_button.click()
    until(lambda: not page.busy)
    present = {cell.group_keys[0] for frame in page.data.frames for cell in frame.snapshot.groups
               if len(cell.group_keys) == 1 and cell.equipment_ids}
    assert present and set(page.legend_buttons) == present and "c4" not in page.legend_buttons
    assert set(page.chart.values) == present
    page.toggle_all_button.click()
    assert not any(control.isChecked() for control in page.legend_buttons.values())
    page.toggle_all_button.click()
    assert {key for key, control in page.legend_buttons.items() if control.isChecked()} == present
    assert all(t.site_key == "hwaseong" for f in page.data.frames for t in f.tiles)
    page.query_panel.select("group_key", "o2")
    set_period(page, "2026-08-21", "2026-10-21")
    page.query_panel.search_button.click()
    until(lambda: not page.busy)
    assert page.data.request.query.selection.group_key == "o2"
    assert [frame.day for frame in page.frames] == [date(2026, m, 21) for m in (8, 9, 10)]
    assert set(page.legend_buttons) == set(page.chart.values) == {"o2"}
    page.view_tabs.setCurrentIndex(1)
    assert page.heatmap.visible_ids == next(
        cell.equipment_ids for cell in page.frame.snapshot.groups if cell.group_keys == ("o2",)
    )


def test_group_buttons_match_dashboard_style_and_keep_their_height(own):
    window = own(create_window())
    window.resize(1600, 960)
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    page.set_grouping("group")
    QTest.qWait(20)
    dashboard_button = window.pages["dashboard"].series_widgets[0]
    replay_button = page.legend_buttons["c3"]
    assert replay_button.styleSheet() == dashboard_button.styleSheet()
    legend_height = dashboard_button.sizeHint().height()
    assert all(control.height() == legend_height for control in (*page.legend_buttons.values(), *page.legend_tools))
    replay_button.click()
    assert replay_button.height() == legend_height


def test_interval_buttons_rebuild_frames_without_reading_again(own):
    """요청 2-1: 재생 간격을 바꾸면 다시 조회하지 않고 시점만 다시 만든다."""
    calls = []
    source = demo_dashboard_source()
    page = replay_page(own, loader=lambda request: calls.append(request) or load_replay(source, request))
    saved = {f.snapshot.base_date: f for f in page.data.frames}
    page.interval_buttons["week"].click()
    days = [f.day for f in page.frames]
    assert page.step == "week" and days[0] == date(2026, 5, 21) and days[-1] == date(2026, 10, 21)
    assert all((b - a).days == 7 for a, b in zip(days, days[1:-1]))
    # 각 시점은 그 날짜 전 마지막 저장일의 상태(대시보드와 같은 규칙)
    assert all(f.snapshot is saved[max(d for d in saved if d <= f.day)].snapshot for f in page.frames)
    assert page.index == len(page.frames) - 1 and page.chart.days == tuple(days)
    assert page.play() and page.index == 0
    page.advance()
    assert page.index == 1 and page.frame.day == date(2026, 5, 28)
    assert "마지막 저장일 2026-05-21 기준" in page.achievement.toolTip()
    # 플로팅에는 재생일을 표시하고, 이어받은 관측 기준은 기존 적용률 툴팁에서 확인한다.
    assert "05-21 저장 기준" in page.achievement.toolTip()
    assert page.playback_controls.date_label.text() == "재생일 2026-05-28"
    assert not hasattr(page.playback_controls, "observation_label")
    assert page.playback_controls.frame_label.text() == f"2 / {len(page.frames)}"
    # 재생 중 간격을 바꾸면 멈추지 않고, 보던 날짜(05-28) 전 가장 가까운 시점부터 이어서 재생한다.
    page.interval_buttons["quarter"].click()
    assert [f.day for f in page.frames] == [date(2026, 5, 21), date(2026, 8, 21), date(2026, 10, 21)]
    assert page.timer.isActive() and page.index == 0 and page.frame.day == date(2026, 5, 21)
    assert "저장 기준" not in page.achievement.toolTip()  # 저장일과 같은 시점은 붙이지 않는다.
    page.pause()
    page.interval_buttons[None].click()
    dialog = page.timeline.day_dialog
    assert dialog.isVisible()
    dialog.setIntValue(10)
    dialog.accept()
    assert page.step == 10 and len(calls) == 1
    assert [frame.day for frame in page.frames] == list(interval_days(page.data.request.start, page.data.request.end, 10))
    frames = page.frames
    page.interval_buttons[None].click()
    page.timeline.day_dialog.setIntValue(3)
    page.timeline.day_dialog.reject()
    assert page.frames is frames and page.step == 10


def test_interval_too_long_for_period_is_disabled(own):
    page = replay_page(own)
    assert page.set_interval(3)
    set_period(page, "2026-05-21", "2026-06-21")
    assert page.apply_query()
    until(lambda: not page.busy)
    assert page.interval_buttons["month"].isEnabled() and page.interval_buttons["week"].isEnabled()
    previous = page.data
    page.end.setDate(QDate(2026, 5, 21))
    assert page.data is previous and not page.busy and page.interval_buttons["month"].isEnabled()
    page.query_panel.search_button.click()
    until(lambda: not page.busy)
    assert not any(page.interval_buttons[s].isEnabled() for s in ("week", "month", "quarter"))
    assert not page.set_interval("week") and page.step is None
    first = page.interval_buttons[None]
    assert first.text() == "지정 일수" and page.timeline._current_step is None
    assert first.toolTip() == "실제 저장된 날짜마다 재생"
    first.click()
    page.timeline.day_dialog.setIntValue(3)
    page.timeline.day_dialog.accept()
    assert page.step is None and page.timeline._current_step is None
    assert first.text() == "지정 일수" and first.toolTip() == "실제 저장된 날짜마다 재생"


def test_chart_line_and_points_show_dashboard_trend_hover(own):
    page = replay_page(own)
    page.set_grouping("group")
    line = next(s for s in page.chart.chart().series() if s.name() == "C3")
    assert [p.x() for p in line.points()] == [0, 1, 2, 3, 4, 5]  # 시점마다 점 하나, 계단 모서리 없음
    cell = next(g for g in page.data.frames[2].snapshot.groups if g.group_keys == ("c3",))
    summary = cell.summary
    judged = sum(any(family.status in (CompareStatus.AT_OR_ABOVE, CompareStatus.BELOW) for family in item.families)
                 for item in summary.equipment)
    day = page.data.frames[2].day
    expected = (f"<qt>{day:%Y.%m.%d} · C3<br>목표 달성 {summary.compliant} / "
                f"{summary.compliant + summary.below}건 ({summary.rate:.1%})<br>설비 {judged}대</qt>")
    line.hovered.emit(QPointF(2.2, 50), True)
    assert QToolTip.text() == expected
    points = next(s for s in page.chart.chart().series() if s.name() == "C3" + POINT_SUFFIX)
    points.hovered.emit(points.points()[2], True)
    line.hovered.emit(QPointF(2.2, 50), False)
    assert page.chart._active_tooltip == (points, expected)
    points.hovered.emit(points.points()[2], False)
    assert page.chart._active_tooltip is None


def test_playback_buttons_share_design_and_float_at_body_bottom(own):
    page = replay_page(own)
    page.show()
    QTest.qWait(50)
    y = lambda w: w.mapTo(page, QPoint()).y()  # noqa: E731
    assert y(page.play_button) == y(page.pause_button) == y(page.restart_button)
    controls = page.playback_controls
    assert abs(controls.geometry().center().x() - page.rect().center().x()) <= 1
    assert page.height() - controls.geometry().bottom() == 17
    assert page.content.body.indexOf(controls) == -1
    assert controls.isAncestorOf(page.achievement) and controls.isAncestorOf(page.progress)
    assert controls.date_label.text() == "재생일 2026-10-21" and controls.frame_label.text() == "6 / 6"
    assert not hasattr(controls, "observation_label")
    assert controls.scope_label.text() == "" and controls.scope_label.isHidden()
    assert page.content.body.indexOf(page.achievement) == page.content.body.indexOf(page.progress) == -1
    assert page.progress.height() == 4 and y(page.progress) < y(page.play_button)
    assert y(page.position_slider) > y(page.play_button) + page.play_button.height()
    assert page.position_slider.isEnabled() and page.position_slider.value() == page.index
    assert page.play_button.styleSheet() == page.pause_button.styleSheet() == page.restart_button.styleSheet()


def test_playback_speed_changes_active_timer_without_reloading_or_resetting_frame(own):
    page = replay_page(own)
    assert page.playback_speed == 1 and page.timer.interval() == 1000
    page.play()
    page.advance()
    frame, data = page.frame, page.data
    for speed, delay in ((0.5, 2000), (1, 1000), (2, 500), (4, 250)):
        page.speed_buttons[speed].click()
        assert page.playback_speed == speed and page.timer.interval() == delay
        assert page.timer.isActive() and page.frame is frame and page.data is data
        assert page.speed_buttons[speed].isChecked()
    assert not page.set_playback_speed(3)
    page.pause()
    assert page.playback_speed == 4 and page.timer.interval() == 250


def test_floating_rate_scope_explains_legend_display_and_group_filtering(own):
    page = replay_page(own)
    controls, data = page.playback_controls, page.data
    full = page.achievement.text(), page.progress.value()
    assert controls.scope_label.text() == "" and controls.scope_label.isHidden()
    page.set_all_trends(False)
    assert not page.chart.trends
    assert (page.achievement.text(), page.progress.value()) == full
    assert controls.scope_label.text() == "" and "표시만" in controls.scope_label.toolTip()
    page.view_tabs.setCurrentIndex(1)
    assert page.heatmap.visible_ids and controls.scope_label.isHidden()
    page.set_grouping("group")
    page.set_all_groups(False)
    assert controls.scope_label.text() == "선택 설비군"
    assert page.achievement.text() == "전체 적용률 —" and page.heatmap.visible_ids == ()
    assert controls.date_label.text() == f"재생일 {page.frame.day}" and controls.frame_label.text() == "6 / 6"
    page.legend_buttons["c3"].click()
    assert controls.scope_label.text() == "선택 설비군" and page.heatmap.visible_ids
    page.set_all_groups(True)
    assert controls.scope_label.text() == "" and controls.scope_label.isHidden()
    assert (page.achievement.text(), page.progress.value()) == full and page.data is data
    page.invalidate()
    assert controls.date_label.text() == "재생일 —" and controls.frame_label.text() == "0 / 0"
    assert not hasattr(controls, "observation_label") and controls.scope_label.toolTip() == ""


def test_playback_overlay_tracks_viewport_and_hides_on_other_pages_and_drawer(own):
    window = own(create_window())
    window.resize(1100, 800)
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    controls, viewport = page.playback_controls, window.scroll.viewport()
    assert controls.parentWidget() is viewport and controls.isVisible()
    assert abs(controls.geometry().center().x() - viewport.rect().center().x()) <= 1
    page.view_tabs.setCurrentIndex(1)
    QTest.qWait(40)
    # 보기를 바꾸면 문서 높이에 따라 수직 스크롤바가 생겨 viewport 폭이 달라질 수 있다.
    assert abs(controls.geometry().center().x() - viewport.rect().center().x()) <= 1
    initial = controls.geometry()
    initial_viewport = viewport.size()
    window.scroll.verticalScrollBar().setValue(window.scroll.verticalScrollBar().maximum())
    QTest.qWait(20)
    assert controls.size() == initial.size()
    assert abs(controls.geometry().center().x() - viewport.rect().center().x()) <= 1
    assert viewport.height() - controls.geometry().bottom() == 17
    if viewport.size() == initial_viewport:
        assert controls.geometry() == initial
    page.open_equipment(next(iter(page.heatmap.tile_buttons)))
    QTest.qWait(20)
    assert not controls.isVisible()
    page.drawer.close_panel(restore_focus=False)
    until(controls.isVisible)
    window.navigate("dashboard")
    assert not controls.isVisible()
    window.navigate("replay")
    assert controls.isVisible()
    window.resize(960, 600)
    QTest.qWait(40)
    assert abs(controls.geometry().center().x() - viewport.rect().center().x()) <= 1
    assert viewport.height() - controls.geometry().bottom() == 17


def test_view_tabs_show_one_view_and_share_the_selected_frame(own):
    page = replay_page(own)
    assert [page.view_tabs.tabText(i) for i in range(page.view_tabs.count())] == ["달성률 추이", "히트맵"]
    assert page.chart.isVisibleTo(page) and not page.heatmap.isVisibleTo(page)
    assert page.heatmap.view is None
    chart, series = page.chart.chart(), tuple(page.chart.chart().series())
    page.select_date(2)
    assert page.chart.chart() is chart and tuple(chart.series()) == series
    frame, data = page.frame, page.data
    page.view_tabs.setCurrentIndex(1)
    assert not page.chart.isVisibleTo(page) and page.heatmap.isVisibleTo(page)
    assert page.frame is frame and page.data is data and page.play_button.isEnabled()
    assert page.heatmap.view.snapshot is frame.snapshot
    page.select_date(3)
    assert page.chart.current == 2 and page.heatmap.view.snapshot is page.frame.snapshot
    page.view_tabs.setCurrentIndex(0)
    assert page.chart.isVisibleTo(page) and not page.heatmap.isVisibleTo(page) and page.chart.current == 3
    assert page.chart.chart() is chart and tuple(chart.series()) == series


def test_version_popup_all_clear_cancel_confirm_and_reapply_without_changing_n(own):
    page = replay_page(own)
    before = page.data
    n = dict(page.source.targets)
    page.open_versions()
    dialog = page.version_dialog
    assert dialog.all_button.text() == "전체 해제"
    dialog.all_button.click()
    assert not dialog.confirm_button.isEnabled() and "하나 이상" in dialog.error.text()
    dialog.choices["v1.0.1"].click()
    assert dialog.confirm_button.isEnabled()
    dialog.cancel_button.click()
    assert page.version_dialog is None and page.state.draft.versions is None and not page.dirty
    page.open_versions()
    dialog = page.version_dialog
    dialog.all_button.click()
    dialog.choices["v1.0.1"].click()
    dialog.confirm_button.click()
    assert page.state.draft.versions == VersionFilter(("v1.0.1",))
    assert page.data is before and not page.busy and page.play_button.isEnabled()
    page.query_panel.search_button.click()
    assert page.busy and not page.play_button.isEnabled()
    until(lambda: not page.busy)
    assert page.source.targets == n and page.data.request.query.selection.versions == VersionFilter(("v1.0.1",))


def test_site_choices_close_and_reset_wait_for_search_and_preserve_targets(own):
    page = replay_page(own)
    data, n = page.data, dict(page.source.targets)
    page.play()
    panel = page.query_panel
    panel.combos["site_key"].click()
    popup = panel.filter_popup
    popup.all_button.click()
    assert page.data is data and page.timer.isActive()
    popup.checks["hwaseong"].click()
    assert page.data is data and page.timer.isActive() and not page.busy
    assert popup.isVisible() and popup.checks["pyeongtaek"].isEnabled()
    popup.checks["pyeongtaek"].click()
    assert page.state.draft.site_key is None  # 팝업 안의 체크는 선택 완료 전까지 초안을 바꾸지 않는다.
    assert page.data is data and not page.busy
    assert popup.isVisible()
    popup.confirm_button.click()
    assert page.state.draft.site_key == ("hwaseong", "pyeongtaek")
    assert page.data is data and not page.busy
    panel.search_button.click()
    assert not page.timer.isActive() and page.busy
    until(lambda: not page.busy)
    data = page.data
    assert data.request.query.selection.site_key == ("hwaseong", "pyeongtaek")
    page.reset_button.click()
    assert page.data is data and not page.busy and page.state.draft == QuerySelection()
    panel.search_button.click()
    until(lambda: not page.busy)
    assert page.state.draft == page.data.request.query.selection == QuerySelection() and page.source.targets == n
    assert page.start.text() == "2026-05-21" and page.end.text() == "2026-10-21"


def test_inverted_period_never_starts_external_loader(own):
    calls = []
    page = ReplayPage(demo_dashboard_source(), loader=lambda request: calls.append(request))
    page.start.setDate(QDate(2026, 11, 1))
    own(page)
    assert not page.busy and calls == []
    assert "시작 날짜는 종료 날짜와 같거나 앞서야" in page.message.text()
    page.query_panel.search_button.click()
    assert not page.busy and calls == []
    assert "시작 날짜는 종료 날짜와 같거나 앞서야" in page.message.text()


def test_replay_calendar_day_selection_waits_for_search(own):
    calls = []
    source = demo_dashboard_source()
    page = replay_page(own, loader=lambda request: calls.append(request) or load_replay(source, request))
    before = page.data
    page.query_panel.range_button.click()
    dialog = page.query_panel.range_button.dialog
    calendar = dialog.calendar
    assert calendar.isVisible()
    calendar.previous_button.click()
    start = page.data.request.start
    chosen = QDate(2026, 9, 21)
    calendar.setSelectedDate(chosen)
    assert len(calls) == 1 and not page.busy
    calendar.clicked.emit(QDate(start.year, start.month, start.day))
    calendar.clicked.emit(chosen)
    assert len(calls) == 1 and not page.busy
    dialog.apply_button.click()
    assert page.end.date() == chosen and len(calls) == 1 and not page.busy and page.data is before
    page.query_panel.search_button.click()
    until(lambda: not page.busy)
    assert len(calls) == 2 and page.data.request.end == chosen.toPython()


def test_empty_single_date_and_zero_match_are_not_playable_or_fake_zero_percent(own):
    page = replay_page(own)
    set_period(page, "2025-01-01", "2025-12-31")
    page.apply_query()
    until(lambda: not page.busy)
    assert page.data.frames == () and not page.play_button.isEnabled() and page.frames == ()
    assert not page.position_slider.isEnabled() and page.position_slider.maximum() == 0
    assert not page.heatmap.visible_ids and "저장된 관측일이 없습니다" in page.message.text()
    set_period(page, "2026-05-21", "2026-05-21")
    page.apply_query()
    until(lambda: not page.busy)
    assert len(page.frames) == 1 and not page.play_button.isEnabled()
    assert not page.position_slider.isEnabled() and page.position_slider.maximum() == 0
    page.state.set_draft(replace(page.state.draft, versions=VersionFilter(("not-stored",))))
    page.apply_query()
    until(lambda: not page.busy)
    assert "계산 불가" in page.achievement.text() and page.frame.snapshot.summary.rate is None
    assert page.heatmap.visible_ids == () and not page.play()


def test_requery_uses_clicked_conditions_and_does_not_submit_edits_during_load(own):
    page = replay_page(own)
    page.view_tabs.setCurrentIndex(1)
    chart = page.chart.chart()
    tiles, legend = page.heatmap.tile_buttons, page.legend_buttons
    achievement, progress = page.achievement.text(), page.progress.value()
    gates, requests = (Event(), Event()), []

    def load(request):
        gate = gates[len(requests)]
        requests.append(request)
        assert gate.wait(5)
        return load_replay(page.source, request)

    def assert_previous_display():
        assert page.busy and page.data is None
        assert page.chart.chart() is chart and page.heatmap.tile_buttons is tiles
        assert page.heatmap.isVisibleTo(page) and page.legend_buttons is legend
        assert page.achievement.text() == achievement and page.progress.value() == progress
        assert page.message.isHidden()
        assert not page.view_tabs.isEnabled() and not page.play_button.isEnabled()
        assert not page.position_slider.isEnabled()
        assert not page.chart.isEnabled() and not page.heatmap.isEnabled()
        assert all(not control.isEnabled() for control in legend.values())
        assert not page.select_date(0) and not page.open_equipment(next(iter(tiles)))

    page.loader = load
    page.query_panel.select("site_key", "hwaseong")
    try:
        assert page.apply_query()
        until(lambda: len(requests) == 1)
        assert_previous_display()
        set_period(page, "2026-06-01", "2026-08-21")
        gates[0].set()
        until(lambda: not page.busy)
        assert len(requests) == 1 and page.data.request is requests[0]
        assert page.data.request.end == date(2026, 10, 21)
        assert page.end.date().toPython() == date(2026, 8, 21)
        assert page.chart.days[-1] == date(2026, 10, 21)
        chart = page.chart.chart()
        tiles, legend = page.heatmap.tile_buttons, page.legend_buttons
        achievement, progress = page.achievement.text(), page.progress.value()
        page.query_panel.search_button.click()
        until(lambda: len(requests) == 2)
        assert_previous_display()
        gates[1].set()
        until(lambda: not page.busy)
    finally:
        for gate in gates:
            gate.set()

    assert page.data.request is requests[1] and page.chart.chart() is chart  # 숨은 차트는 그대로 둔다.
    assert page.heatmap.tile_buttons is not tiles and page.legend_buttons is not legend
    assert all(tile.data.site_key == "hwaseong" for tile in page.heatmap.tile_buttons.values())
    assert "2026-08-21" in page.achievement.toolTip()
    assert page.view_tabs.isEnabled() and page.heatmap.isEnabled()
    page.view_tabs.setCurrentIndex(0)
    assert page.chart.chart() is not chart and page.chart.days[-1] == date(2026, 8, 21)
    assert set(page.chart.values) == {("site", "hwaseong")}


def test_slow_request_is_responsive_blocks_duplicate_and_ignores_late_result_after_exit(own):
    gate, ticks = Event(), []
    source = demo_dashboard_source()
    def load(request):
        gate.wait(4)
        return load_replay(source, request)
    page = own(ReplayPage(source, loader=load))
    try:
        assert page.busy and not page.reset_button.isEnabled()
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
    page = replay_page(own)
    page.loader = lambda _: (_ for _ in ()).throw(RuntimeError("secret-token"))
    assert page.apply_query()
    until(lambda: not page.busy)
    assert page.data is None and "다시 조회" in page.message.text() and "secret" not in page.message.text()
    assert not page.chart.values and not page.heatmap.visible_ids and not page.legend_buttons
    assert page.reset_button.isEnabled() and not page.play_button.isEnabled()
    page.loader = None
    page.reset_button.click()
    assert page.data is None and not page.busy
    page.query_panel.search_button.click()
    until(lambda: not page.busy)
    assert page.data is not None


def test_tile_opens_same_observation_readonly_drawer_without_navigation_or_writes(own):
    window = own(create_window())
    window.resize(1600, 960)
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    page.select_date(0)
    page.view_tabs.setCurrentIndex(1)
    tile = next(control for control in page.heatmap.tile_occurrences
                if control.data.equipment_id == 1 and control is not page.heatmap.tile_buttons[1])
    QTest.qWait(30)
    next(card for card in page.heatmap.group_cards if card.isAncestorOf(tile)).scroll.ensureWidgetVisible(tile)
    window.scroll.ensureWidgetVisible(tile)
    QTest.qWait(30)
    before = page.data, page.index, page.state.draft
    QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton, pos=tile.mapTo(window, tile.rect().center()))
    assert page.drawer.isVisibleTo(window) and window.route == "replay"
    assert page.drawer.parentWidget() is window.centralWidget()
    assert page.drawer.request.equipment_id == 1
    assert page.drawer.detail.record.base_date == page.frame.day
    assert len(page.drawer.software_cards) == 3 and not hasattr(page.drawer, "edit_button")
    assert {control for control in page.heatmap.tile_occurrences if control.isChecked()} == {
        control for control in page.heatmap.tile_occurrences if control.data.equipment_id == 1}
    assert tile.isChecked()
    window.back_shortcut.trigger()
    until(page.drawer.isHidden)
    assert window.route == "replay" and tile.hasFocus()
    assert (page.data, page.index, page.state.draft) == before
    assert not page.timer.isActive()

    next_tile = next(control for control in page.heatmap.tile_occurrences if control.data.equipment_id != 1)
    QTest.mouseClick(next_tile, Qt.MouseButton.LeftButton)
    assert page.drawer.request.equipment_id == next_tile.data.equipment_id
    assert [control for control in page.heatmap.tile_occurrences if control.isChecked()] == [next_tile]
    page.drawer.close_button.click()
    assert next_tile.hasFocus()

    assert page.set_interval("week") and page.select_date(1)
    assert not any(control.isChecked() for control in page.heatmap.tile_occurrences)
    assert page.frame.day == date(2026, 5, 28) and page.open_equipment(1)
    observation = date(2026, 5, 21)
    assert page.drawer.request.query is page.frame.snapshot.query
    assert page.drawer.request.base_date == page.frame.snapshot.base_date == observation
    assert page.drawer.request.observation_date == page.drawer.detail.record.base_date == observation
    assert page.drawer.info_values["기준일"].text() == str(observation)


def test_multisw_group_duplicates_do_not_inflate_unique_physical_count(own):
    page = replay_page(own)
    page.set_grouping("group")
    page.view_tabs.setCurrentIndex(1)
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
    controls = page.playback_controls
    assert controls.geometry().left() >= 12 and controls.geometry().right() <= window.scroll.viewport().width() - 12
    assert controls.height() <= 140
    for widget in (controls.date_label, controls.frame_label, page.position_slider,
                   page.play_button, page.pause_button, page.restart_button, page.speed_tabs):
        assert controls.rect().contains(widget.mapTo(controls, widget.rect().topLeft()))
        assert controls.rect().contains(widget.mapTo(controls, widget.rect().bottomRight()))
    assert page.width() <= window.scroll.viewport().width()
    assert page.reset_button.iconSize() == window.query_panel.reset_button.iconSize()
    assert all(combo.isVisible() for combo in page.query_panel.combos.values())
    assert not any(control.text() in {"필터 접기", "필터 펼치기"}
                   for control in page.query_panel.findChildren(QPushButton))
    assert page.query_panel.search_button.isVisible() and page.query_panel.search_button.text() == ""
    assert page.query_panel.search_button.x() > page.reset_button.geometry().right()
    assert page.query_panel.card.property("outlined") is True
    window.navigate("dashboard")   # 데이터 가져오기는 머리 줄 툴바를 숨기므로(11-10 2차 3-1) 대시보드로 확인한다
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


def test_replay_chart_is_taller_and_buttons_keep_figma_heights(own):
    window = own(create_window())
    window.resize(1600, 960)
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    QTest.qWait(100)
    assert 480 <= page.chart.height() <= 520
    assert page.chart.mapTo(window, QPoint()).y() < 750
    assert all(b.height() == 30 for b in page.interval_buttons.values())
    assert all(b.height() == 40 for b in (page.play_button, page.pause_button, page.restart_button))
    page.set_grouping("group")
    page.select_date(0)
    page.legend_buttons["c3"].click()
    QTest.qWait(20)
    assert page.interval_buttons[None].height() == 30


def test_dense_replay_scroll_keeps_y_axis_and_frame_interaction_visible(own):
    window = own(create_window())
    window.resize(960, 760)
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    assert page.set_interval(1)
    QTest.qWait(60)
    row, chart = page.chart_row, page.chart.chart()
    scroll, fixed = row.scroll, row.fixed_axis
    assert len(page.frames) > 100 and scroll.horizontalScrollBar().maximum() > 0
    assert 480 <= page.chart.height() <= 520
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert fixed.isVisible() and fixed.axis.titleText() == "목표 N 이상 적용률 (%)"
    assert not fixed.axis.labelsVisible() and not fixed.axis.isTitleVisible()
    origin = fixed.mapToGlobal(QPoint())
    ticks = fixed.tick_positions()
    assert [label for label, _ in ticks] == ["0%", "25%", "50%", "75%", "100%"]
    scroll.horizontalScrollBar().setValue(scroll.horizontalScrollBar().maximum())
    QTest.qWait(20)
    assert fixed.mapToGlobal(QPoint()) == origin and fixed.tick_positions() == ticks
    target = next(series for series in chart.series() if series.name().endswith(POINT_SUFFIX)
                  and len(series.points()) > 1 and series.points()[-1].x() == len(page.frames) - 1)
    point = target.points()[-2]
    local = page.chart.mapFromScene(chart.mapToScene(chart.mapToPosition(point, target)))
    visible = scroll.viewport().mapFromGlobal(page.chart.mapToGlobal(local))
    assert scroll.viewport().rect().contains(visible)
    QTest.mouseClick(page.chart.viewport(), Qt.MouseButton.LeftButton, pos=local)
    assert page.index == round(point.x()) and page.chart.current == page.index
    assert page.position_slider.value() == page.index
    target.hovered.emit(point, True)
    assert page.frames[page.index].day.strftime("%Y.%m.%d") in page.chart._active_tooltip[1]
    assert page.set_interval(None)
    QTest.qWait(30)
    assert scroll.horizontalScrollBar().maximum() == 0 and fixed.isVisible()


def test_replay_ticks_do_not_jump_scroll_or_leave_a_detail_from_another_date(own):
    window = own(create_window())
    window.resize(1600, 960)
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    page.view_tabs.setCurrentIndex(1)
    QTest.qWait(100)
    bar = window.scroll.verticalScrollBar()
    until(lambda: bar.maximum() > 0 and all(
        card.height() >= card.minimumSizeHint().height()
        and card.scroll.viewport().height() >= card.scroll.widget().height()
        for card in page.heatmap.group_cards))
    position = min(450, bar.maximum() // 2)
    assert position > 0
    bar.setValue(position)
    assert bar.value() == position
    assert page.play()
    QTest.qWait(100)
    assert bar.value() == position
    page.advance()
    QTest.qWait(100)
    assert bar.value() == position
    page.pause()
    assert page.open_equipment(1)
    assert page.drawer.request.base_date == page.frame.snapshot.base_date
    page.restart()
    assert not page.drawer.is_open and page.drawer.request is None
    until(lambda: not page.drawer.isVisibleTo(window))


def test_chart_breaks_lines_at_unknown_dates_and_clear_removes_previous_values(own):
    page = replay_page(own)
    data = page.data
    gap = replace(data.frames[2], snapshot=replace(data.frames[2].snapshot,
        groups=tuple(g for g in data.frames[2].snapshot.groups if g.group_keys != ("c3",))))
    data = replace(data, frames=data.frames[:2] + (gap,) + data.frames[3:])
    chart = own(ReplayChart())
    chart.show_data(data, {"c3"})
    chart_object, series = chart.chart(), tuple(chart.chart().series())
    assert chart.values["c3"][2] is None
    dots = [s for s in chart.chart().series() if s.isVisible() and s.name() == "C3" + POINT_SUFFIX]
    assert [[p.x() for p in series.points()] for series in dots] == [[0, 1], [3, 4, 5]]
    assert not any(s.isVisible() and FUTURE_SUFFIX in s.name() for s in chart.chart().series())  # 기본은 마지막 날짜가 현재
    # 판단 불가인 날짜가 현재면 현재 점을 0%로 찍지 않는다.
    chart.show_data(data, {"c3"}, current=2)
    assert chart.chart() is chart_object and tuple(chart_object.series()) == series
    assert not any(s.isVisible() and s.name().endswith(CURRENT_SUFFIX) for s in chart.chart().series())
    chart.clear()
    assert chart.values == {} and chart.accessibleDescription() == "" and chart.current is None


def chart_parts(page, label):
    series = [part for part in page.chart.chart().series() if part.isVisible()]
    marker = next(s for s in series if s.name() == CURRENT_MARKER)
    past = [p.x() for s in series if s.name() == label + POINT_SUFFIX for p in s.points()]
    future = [p.x() for s in series if s.name() == label + FUTURE_SUFFIX + POINT_SUFFIX for p in s.points()]
    dot = [p.x() for s in series if s.name() == label + CURRENT_SUFFIX for p in s.points()]
    return {p.x() for p in marker.points()}, past, future, dot


def test_chart_moves_with_playback_solid_until_current_and_faded_after(own):
    page = replay_page(own)
    page.set_grouping("group")
    chart, series = page.chart.chart(), tuple(page.chart.chart().series())
    label = next(g.label for g in page.data.source.catalog.groups if g.key == "c3")
    marker, past, future, dot = chart_parts(page, label)
    assert marker == {5} and max(past) == 5 and future == [] and dot == [5]
    assert page.play() and page.index == 0
    marker, past, future, dot = chart_parts(page, label)
    assert marker == {0} and past == [0] and future == [0, 1, 2, 3, 4, 5] and dot == [0]
    page.advance()
    page.advance()
    marker, past, future, dot = chart_parts(page, label)
    assert page.index == 2 and marker == {2} and past == [0, 1, 2] and future == [2, 3, 4, 5] and dot == [2]
    assert page.chart.current == 2 and page.chart.accessibleDescription().startswith(f"현재 {page.frame.day}")
    assert page.chart.chart() is chart and tuple(chart.series()) == series
    past_line = next(part for part in chart.series() if part.isVisible() and part.name() == label)
    future_line = next(part for part in chart.series() if part.isVisible() and part.name() == label + FUTURE_SUFFIX)
    assert past_line.pen().style() == Qt.PenStyle.SolidLine and future_line.pen().style() == Qt.PenStyle.DashLine
    assert past_line.pen().color().name() == future_line.pen().color().name()
    assert future_line.pen().color().alpha() < past_line.pen().color().alpha()


def test_selected_date_and_group_buttons_keep_chart_at_selected_date(own):
    page = replay_page(own)
    page.set_grouping("group")
    page.select_date(1)
    assert chart_parts(page, "C3")[0] == {1}
    page.legend_buttons["c4"].click()  # 범례를 바꿔도 현재 날짜 위치는 그대로
    assert page.chart.current == 1 and chart_parts(page, "C3")[0] == {1}
    assert not any(s.name().startswith("C4") for s in page.chart.chart().series())


def test_saved_n_groups_remain_effective_without_query_summary(own):
    source = demo_dashboard_source()
    key, release = next(iter(sorted(source.targets.items())))
    source.targets.clear()
    source.saved_groups = {key: VersionGroups({Slot.N: frozenset({release})})}
    page = own(ReplayPage(source))
    until(lambda: page.data is not None and not page.busy)
    assert len(page.frame.snapshot.effective_targets) == 1
    assert page.query_panel.applied_summary.isHidden()
    page.view_tabs.setCurrentIndex(1)
    equipment_id, tile = next(iter(page.heatmap.tile_buttons.items()))
    QTest.mouseClick(tile, Qt.MouseButton.LeftButton)
    assert page.drawer.is_open and page.drawer.request.equipment_id == equipment_id


def test_empty_period_clears_previous_achievement_tooltip(own):
    """R-06: 빈 기간으로 다시 조회하면 이전 결과의 대수 설명이 남지 않는다."""
    page = replay_page(own)
    assert "분석" in page.achievement.toolTip()
    set_period(page, "2025-01-01", "2025-01-31")
    assert page.apply_query()
    until(lambda: not page.busy)
    assert page.data.frames == () and page.achievement.toolTip() == ""
    assert page.playback_controls.date_label.text() == "재생일 —"
    assert page.playback_controls.frame_label.text() == "0 / 0"
    assert not hasattr(page.playback_controls, "observation_label")


def test_heatmap_reuses_tiles_and_matches_version_heatmap_presentation(own):
    page = replay_page(own)
    page.set_grouping("group")
    page.view_tabs.setCurrentIndex(1)
    heatmap, data, frame = page.heatmap, page.data, page.frame
    before = list(heatmap.tile_occurrences)
    reference = own(HeatmapPage(None, data.source, detail_connected=True, show_summary=False, show_grouping=False))
    reference.set_grouping("group")
    reference.present_snapshot(frame.snapshot, source=data.source, tiles=frame.tiles)
    assert isinstance(heatmap.view, HeatmapPage)
    assert [tuple(card.property("groupKey")) for card in heatmap.group_cards] == [
        tuple(card.property("groupKey")) for card in reference.group_cards]
    card = reference.group_cards[0]
    control = before[0]
    reference_tile = reference.tile_buttons[control.data.equipment_id]
    assert control.code_label.alignment() == reference_tile.code_label.alignment() == Qt.AlignmentFlag.AlignCenter
    assert control.version_label.text() == reference_tile.version_label.text()
    assert control.styleSheet() == reference_tile.styleSheet() and ":hover" in control.styleSheet()
    assert control.toolTip() == reference_tile.toolTip() == control.data.description
    assert heatmap.group_cards[0].styleSheet() == card.styleSheet()
    assert heatmap.group_cards[0].layout().spacing() == card.layout().spacing()
    heatmap.show_frame(data, frame)
    assert heatmap.rebuilt_tiles == 0 and heatmap.tile_occurrences == before
    target = frame.tiles[0]
    changed_tier = next(t for t in Tier if t is not target.tier)
    changed = replace(frame, tiles=(replace(target, tier=changed_tier), *frame.tiles[1:]))
    heatmap.show_frame(data, changed)
    occurrences = sum(control.data.equipment_id == target.equipment_id for control in before)
    # 색만 바뀌면 위젯은 그대로 두고 스타일·글자만 바꾼다.
    assert heatmap.rebuilt_tiles == 0 and heatmap.restyled_tiles == occurrences
    assert heatmap.tile_occurrences == before
    restyled = [c for c in heatmap.tile_occurrences if c.data.equipment_id == target.equipment_id]
    assert all(c.data.tier is changed_tier for c in restyled)
    # 위험 테두리와 툴팁은 기존 타일에서 함께 갱신한다.
    flipped = RiskStatus.NO_RISK if target.risk is RiskStatus.RISK else RiskStatus.RISK
    risky = replace(frame, tiles=(replace(target, risk=flipped), *frame.tiles[1:]))
    heatmap.show_frame(data, risky)
    assert heatmap.rebuilt_tiles == 0 and heatmap.tile_occurrences == before
    assert all(c.toolTip() == c.data.description and c.data.risk is flipped for c in restyled)
    # 설비가 하나 빠지면 그 설비가 있던 설비군 카드만 새로 만들고 나머지 카드는 그대로 쓴다.
    heatmap.show_frame(data, frame)
    cards = list(heatmap.group_cards)
    dropped = next(t for t in frame.tiles if t.goal is not GoalState.EXCLUDED and not t.multi_sw)
    heatmap.show_frame(data, replace(frame, tiles=tuple(t for t in frame.tiles if t is not dropped)))
    kept = sum(a is b for a, b in zip(cards, heatmap.group_cards))
    assert len(heatmap.group_cards) == len(cards) and kept == len(cards) - 1
    assert dropped.equipment_id not in heatmap.visible_ids
    assert 0 < heatmap.rebuilt_tiles < len(before)


def test_version_popup_marks_raw_versions_with_spaces():
    """R-05: 끝 공백 원문은 합치지 않되 같은 글자로 보이지 않게 표시한다."""
    assert choice_caption("v3.53_1") == "v3.53_1"
    assert choice_caption("v3.53_1 ") == "v3.53_1  (끝 공백 포함 원문)"
    assert choice_caption(" v3.53_1 ") == "v3.53_1  (앞·끝 공백 포함 원문)"


def test_data_save_blocks_replay_and_completion_requires_new_read(own):
    window = own(create_window())
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    page.play()
    assert "management" not in window.pages
    window.pages["data"].save_started.emit()
    assert not page.isEnabled() and not page.timer.isActive() and page.data is None
    window.after_management_save(None)
    assert page.isEnabled() and page.data is None and "조회 조건을 다시 선택" in page.message.text()


def test_app_entry_without_any_configured_n_shows_unknown_instead_of_read_failure(own):
    source = demo_dashboard_source()
    source.targets.clear()
    window = own(create_window(source))
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    assert page.data is not None and page.frame.tiles
    assert "계산 불가" in page.achievement.text() and page.data is not None


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_rate_bar_colors_measured_rates_continuously_and_unknown_stays_neutral(own, monkeypatch, mode):
    page = replay_page(own)
    controller = QApplication.instance().appearance_manager
    previous = controller.appearance
    try:
        controller.apply(Appearance(mode=mode))
        base = replace(page.frame.snapshot.summary, equipment=(), undetermined=0)
        current = [base]
        monkeypatch.setattr("setron.ui.replay_page.group_view", lambda *_: (current[0], 0, 0))

        def show_rate(compliant, below):
            current[0] = replace(base, compliant=compliant, below=below)
            page._render_frame()
            image = page.progress.grab().toImage()
            rate = current[0].rate
            x = 1 if rate is None or rate == 0 else round(image.width() * rate / 2)
            color = image.pixelColor(x, image.height() // 2)
            assert page.progress.value() == (round(rate * 1000) if rate is not None else 0)
            expected = f"{rate:.1%}" if rate is not None else "계산 불가"
            assert page.achievement.text() == f"전체 적용률 {expected}"
            return color

        red, orange, amber, green = [show_rate(value, 1000 - value) for value in (0, 283, 500, 1000)]
        assert red.red() > red.green() and red.red() > red.blue()
        assert orange.red() > orange.green() > orange.blue()
        assert amber.red() > amber.blue() and amber.green() > amber.blue()
        assert green.green() > green.red() and green.green() > green.blue()
        assert len({color.name() for color in (red, orange, amber, green)}) == 4
        before, after = show_rate(490, 510), show_rate(510, 490)
        assert before != after
        assert max(abs(a - b) for a, b in zip(before.getRgb()[:3], after.getRgb()[:3])) <= 6
        neutral = show_rate(0, 0)
        assert neutral.name() == controller.tokens["heat_unknown"] and neutral != red
        page.invalidate()
        assert page.playback_controls.application_rate is None
        image = page.progress.grab().toImage()
        assert image.pixelColor(1, image.height() // 2) == neutral
    finally:
        controller.apply(previous)


def test_rate_bar_recolors_on_frames_groups_and_theme_changes(own):
    page = replay_page(own)
    controls = page.playback_controls
    controller = QApplication.instance().appearance_manager
    previous = controller.appearance
    try:
        controller.apply(Appearance(mode="light"))
        page.select_date(0)
        first = controls.application_rate, page.progress.styleSheet()
        page.select_date(len(page.frames) - 1)
        assert controls.application_rate != first[0] and page.progress.styleSheet() != first[1]
        rate, light = controls.application_rate, page.progress.styleSheet()
        controller.apply(Appearance(mode="dark"))
        assert controls.application_rate == rate and page.progress.styleSheet() != light
        page.view_tabs.setCurrentIndex(1)
        page.set_grouping("group")
        page.set_all_groups(False)
        neutral = page.progress.styleSheet()
        assert controls.application_rate is None and page.progress.value() == 0
        page.legend_buttons["c3"].click()
        selected = next(cell for cell in page.frame.snapshot.groups if cell.group_keys == ("c3",))
        assert controls.application_rate == selected.summary.rate and page.progress.styleSheet() != neutral
        page.invalidate()
        assert controls.application_rate is None and page.progress.styleSheet() == neutral
    finally:
        controller.apply(previous)
