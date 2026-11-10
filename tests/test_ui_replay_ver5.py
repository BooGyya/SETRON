"""VER5 Replay 화면과 연결 팝업의 프론트 검증. 합성 자료만 조회한다."""

from dataclasses import replace
from datetime import date
from threading import Event

import pytest
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtGui import QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolTip

from setron.features.version.groups import Slot, VersionGroups
from setron.ui.app import create_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.features.risk.schemas import RiskStatus
from setron.ui.heatmap import tile_style
from setron.ui.heatmap_data import GoalState, Tier
from setron.ui.query_state import QuerySelection, VersionFilter
from setron.ui.replay_data import load_replay
from setron.ui.replay_widgets import CURRENT_MARKER, CURRENT_SUFFIX, FUTURE_SUFFIX, POINT_SUFFIX, ReplayChart
from setron.ui.replay_page import ReplayPage
from setron.ui.theme import ASSETS, svg_size
from setron.ui.version_filter_dialog import choice_caption
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
    assert len(page.data.frames) == len(page.frames) == 6 and page.step is None
    assert page.index == 5 and page.snapshot_title.text().startswith("2026-10-21")
    assert page.play_button.isEnabled() and not page.pause_button.isEnabled()
    assert len(page.legend_buttons) == 9 and len(page.chart.values) == 9
    # 요청 1-1: 설비군은 조회 조건 드롭다운이 아니라 그래프 위 설비군 버튼으로 고른다.
    assert set(page.combos) == {"site_key", "fab_key", "line_key"}
    assert [b.text() for b in page.legend_tools] == ["전체 선택", "전체 해제"]
    # 요청 2-1: 날짜 칩 대신 재생 간격
    assert [b.text() for b in page.interval_buttons.values()] == ["저장일마다", "1주", "1개월", "3개월"]
    assert page.interval_buttons[None].isChecked()
    # 요청 3-3: 재생 버튼은 기호, 이름은 그대로
    assert [(b.text(), b.accessibleName()) for b in (page.play_button, page.pause_button, page.restart_button)] == [
        ("▶", "재생"), ("‖", "일시정지"), ("↺", "처음부터")]
    # 기호가 앱 글꼴에 있어야 네모로 깨지지 않는다.
    metrics = QFontMetrics(page.play_button.font())
    assert all(metrics.inFontUcs4(ord(b.text())) for b in (page.play_button, page.pause_button, page.restart_button))
    assert "2026-05-21" in page.context.text() and "목표 N" in page.context.text()
    assert "기준일 최신" not in page.context.text()
    assert page.heatmap.visible_ids and len(page.heatmap.group_cards) == 9
    assert all(t.data.observation_date <= page.frame.day for t in page.heatmap.tile_occurrences)
    # R-02: 범례는 타일과 같은 5단계 색 + 위험 후보 테두리
    legend = [item.accessibleName() for item in page.legend_items]
    assert legend == ["범례 N+1 이상", "범례 N 달성", "범례 N-1", "범례 N-2 이하", "범례 비교 불가 · HPA",
                      "범례 위험 후보 (빨간 테두리)"]
    assert page.observation_note.text().startswith("이날 저장 ")
    assert page.heatmap_title.text() == "선택 시점(2026-10-21)의 설비 Heatmap"


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


def test_group_buttons_filter_lines_achievement_and_heatmap_with_select_all_and_clear(own):
    """요청 2-2: 설비군 버튼에서 고른 설비군으로 그래프 선·달성률·히트맵을 함께 좁힌다."""
    page = replay_page(own)
    full = page.achievement.text(), page.heatmap.visible_ids
    page.legend_tools[1].click()  # 전체 해제
    assert page.chart.values == {} and page.heatmap.group_cards == [] and page.heatmap.visible_ids == ()
    assert "하나 이상" in page.achievement.text() and page.progress.value() == 0
    page.legend_buttons["c3"].click()
    cell = next(g for g in page.frame.snapshot.groups if g.group_keys == ("c3",))
    assert set(page.chart.values) == {"c3"} and len(page.heatmap.group_cards) == 1
    assert set(page.heatmap.visible_ids) == set(cell.equipment_ids)
    assert page.achievement.text().startswith(f"등록 SW 목표 달성 {cell.summary.compliant} / "
                                              f"{cell.summary.compliant + cell.summary.below}건")
    assert "1 / 9개 선택" in page.chart_context.text() and "HPA" not in page.achievement.toolTip()
    page.legend_tools[0].click()  # 전체 선택
    assert (page.achievement.text(), page.heatmap.visible_ids) == full and len(page.chart.values) == 9
    assert "설비군 전체" in page.chart_context.text()


def test_group_buttons_show_only_groups_present_in_selected_site(own):
    """요청 2-2: Site를 고르고 조회하면 그 Site에 설비가 있는 설비군 버튼만 나온다."""
    page = replay_page(own)
    page.combos["site_key"].setCurrentIndex(page.combos["site_key"].findData("hwaseong"))
    assert page.apply_query()
    until(lambda: not page.busy)
    present = {cell.group_keys[0] for frame in page.data.frames for cell in frame.snapshot.groups
               if len(cell.group_keys) == 1 and cell.equipment_ids}
    assert set(page.legend_buttons) == present and present
    assert all(t.site_key == "hwaseong" for f in page.data.frames for t in f.tiles)


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
    assert page.index == 1 and page.snapshot_title.text().startswith("2026-05-28")
    assert "마지막 저장일 2026-05-21 기준" in page.observation_note.text()
    page.interval_buttons["quarter"].click()
    assert [f.day for f in page.frames] == [date(2026, 5, 21), date(2026, 8, 21), date(2026, 10, 21)]
    assert not page.timer.isActive()
    page.interval_buttons[None].click()
    assert page.frames == page.data.frames and len(calls) == 1


def test_interval_too_long_for_period_is_disabled(own):
    page = replay_page(own)
    page.start.setText("2026-05-21")
    page.end.setText("2026-06-21")
    assert page.apply_query()
    until(lambda: not page.busy)
    assert page.interval_buttons["month"].isEnabled() and page.interval_buttons["week"].isEnabled()
    page.end.setText("2026-05-21")
    assert page.apply_query()
    until(lambda: not page.busy)
    assert not any(page.interval_buttons[s].isEnabled() for s in ("week", "month", "quarter"))
    assert not page.set_interval("week") and page.step is None


def test_chart_is_step_shaped_and_hover_shows_completed_equipment(own):
    """요청 3-1·3-2: 계단형 선, 점에 마우스를 올리면 'n / m대 완료'."""
    page = replay_page(own)
    line = next(s for s in page.chart.chart().series() if s.name() == "C3")
    points = [(p.x(), p.y()) for p in line.points()]
    # 날짜마다 (앞 값 유지 → 그 날짜에서 수직 변화): x가 같은 점이 연달아 나온다.
    assert points[0][0] == 0 and points[1][0] == 1 and points[1][1] == points[0][1] and points[2][0] == 1
    done, judged = page.chart.completion["c3"][2]
    cell = next(g for g in page.data.frames[2].snapshot.groups if g.group_keys == ("c3",))
    assert judged <= len(cell.equipment_ids) and 0 <= done <= judged
    page.chart._hover("c3", QPointF(2, 50), True)
    assert QToolTip.text() == f"C3 · 07-21 · {done} / {judged}대 완료"
    page.chart._hover("c3", QPointF(2, 50), False)


def test_playback_buttons_sit_between_chart_and_playback_status(own):
    """요청 2-3·3-3: 재생 버튼은 그래프 아래, '일시정지 · n / n' 글자 위."""
    page = replay_page(own)
    page.show()
    QTest.qWait(50)
    y = lambda w: w.mapTo(page, QPoint()).y()  # noqa: E731
    assert y(page.chart_card) + page.chart_card.height() <= y(page.play_button) < y(page.playback_status)
    assert y(page.play_button) == y(page.pause_button) == y(page.restart_button)


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
    assert page.data.frames == () and not page.play_button.isEnabled() and page.frames == ()
    assert not page.heatmap.visible_ids and "저장된 관측일이 없습니다" in page.message.text()
    page.start.setText("2026-05-21")
    page.end.setText("2026-05-21")
    page.apply_query()
    until(lambda: not page.busy)
    assert len(page.frames) == 1 and not page.play_button.isEnabled()
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
    assert all(b.height() == 34 for b in page.interval_buttons.values())
    assert all(b.height() == 36 for b in (*page.legend_buttons.values(), *page.legend_tools))
    assert all(b.height() == 40 for b in (page.play_button, page.pause_button, page.restart_button))
    page.select_date(0)
    page.legend_buttons["c3"].click()
    QTest.qWait(20)
    assert page.interval_buttons[None].height() == 34
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
    dots = [s for s in chart.chart().series() if s.name() == "C3" + POINT_SUFFIX]
    assert [[p.x() for p in series.points()] for series in dots] == [[0, 1], [3, 4, 5]]
    assert chart.completion["c3"][2] is None  # 판단 불가 날짜는 완료 대수도 없다
    assert not any(s.name().endswith(FUTURE_SUFFIX) for s in chart.chart().series())  # 기본은 마지막 날짜가 현재
    # 판단 불가인 날짜가 현재면 현재 점을 0%로 찍지 않는다.
    chart.show_data(data, {"c3"}, current=2)
    assert not any(s.name().endswith(CURRENT_SUFFIX) for s in chart.chart().series())
    chart.clear()
    assert chart.values == {} and chart.accessibleDescription() == "" and chart.current is None


def chart_parts(page, label):
    series = page.chart.chart().series()
    marker = next(s for s in series if s.name() == CURRENT_MARKER)
    past = [p.x() for s in series if s.name() == label + POINT_SUFFIX for p in s.points()]
    future = [p.x() for s in series if s.name() == label + FUTURE_SUFFIX + POINT_SUFFIX for p in s.points()]
    dot = [p.x() for s in series if s.name() == label + CURRENT_SUFFIX for p in s.points()]
    return {p.x() for p in marker.points()}, past, future, dot


def test_chart_moves_with_playback_solid_until_current_and_faded_after(own):
    page = replay_page(own)
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


def test_selected_date_and_group_buttons_keep_chart_at_selected_date(own):
    page = replay_page(own)
    page.select_date(1)
    assert chart_parts(page, "C3")[0] == {1}
    page.legend_buttons["c4"].click()  # 범례를 바꿔도 현재 날짜 위치는 그대로
    assert page.chart.current == 1 and chart_parts(page, "C3")[0] == {1}
    assert not any(s.name().startswith("C4") for s in page.chart.chart().series())


def test_target_count_includes_saved_n_groups(own):
    """R-03: 개별 목표 N이 없고 저장 N 그룹만 있어도 실제 판정 기준 수를 보인다."""
    source = demo_dashboard_source()
    key, release = next(iter(sorted(source.targets.items())))
    source.targets.clear()
    source.saved_groups = {key: VersionGroups({Slot.N: frozenset({release})})}
    page = own(ReplayPage(source))
    assert page.apply_query()
    until(lambda: not page.busy)
    assert "목표 N 1개" in page.context.text()


def test_empty_period_clears_previous_achievement_tooltip(own):
    """R-06: 빈 기간으로 다시 조회하면 이전 결과의 대수 설명이 남지 않는다."""
    page = replay_page(own)
    assert "분석" in page.achievement.toolTip()
    page.start.setText("2025-01-01")
    page.end.setText("2025-01-31")
    assert page.apply_query()
    until(lambda: not page.busy)
    assert page.data.frames == () and page.achievement.toolTip() == "" and page.observation_note.text() == ""


def test_heatmap_reuses_tiles_when_layout_is_same_and_rebuilds_only_changed_tile(own):
    """R-04: 같은 배치면 타일 위젯을 다시 만들지 않고, 보이는 내용이 바뀐 타일만 교체한다."""
    page = replay_page(own)
    heatmap, data, frame = page.heatmap, page.data, page.frame
    before = list(heatmap.tile_occurrences)
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
    assert all(c.data.tier is changed_tier and c.style_text == tile_style(c.data) for c in restyled)
    # '위험' 표시처럼 타일 모양이 바뀌면 그 타일만 새로 만든다.
    flipped = RiskStatus.NO_RISK if target.risk is RiskStatus.RISK else RiskStatus.RISK
    risky = replace(frame, tiles=(replace(target, risk=flipped), *frame.tiles[1:]))
    heatmap.show_frame(data, risky)
    if target.goal is not GoalState.EXCLUDED:
        assert heatmap.rebuilt_tiles == occurrences
        assert sum(a is b for a, b in zip(before, heatmap.tile_occurrences)) == len(before) - occurrences
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
