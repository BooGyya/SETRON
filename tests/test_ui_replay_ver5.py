"""VER5 Replay 화면과 연결 팝업의 프론트 검증. 합성 자료만 조회한다."""

from dataclasses import replace
from datetime import date
from threading import Event

import pytest
from PySide6.QtCore import QDate, QPoint, QPointF, QSignalBlocker, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolTip

from setron.features.version.groups import Slot, VersionGroups
from setron.ui.app import create_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import CompareStatus
from setron.ui.heatmap import HeatmapPage
from setron.ui.heatmap_data import GoalState, Tier
from setron.ui.query_state import QuerySelection, QueryState, VersionFilter
from setron.ui.replay_data import load_replay
from setron.ui.replay_widgets import CURRENT_MARKER, CURRENT_SUFFIX, FUTURE_SUFFIX, POINT_SUFFIX, ReplayChart
from setron.ui.replay_page import ReplayPage
from setron.ui.version_filter_dialog import choice_caption
from test_ui_import_ver5 import own, until  # noqa: F401


def replay_page(own, **kwargs):
    page = own(ReplayPage(demo_dashboard_source(), **kwargs))
    page.resize(1320, 1000)
    assert page.apply_query()
    until(lambda: not page.busy)
    assert page.data is not None
    return page


def set_period(page, start, end):
    with QSignalBlocker(page.start), QSignalBlocker(page.end):
        page.start.setDate(QDate.fromString(start, "yyyy-MM-dd"))
        page.end.setDate(QDate.fromString(end, "yyyy-MM-dd"))


def test_screen_has_all_replay_controls_and_defaults_to_latest_observation(own):
    page = replay_page(own)
    assert len(page.data.frames) == len(page.frames) == 6 and page.step is None
    assert page.index == 5 and page.frame.day == date(2026, 10, 21)
    assert page.play_button.isEnabled() and not page.pause_button.isEnabled()
    assert len(page.legend_buttons) == 9 and len(page.chart.values) == 9
    assert set(page.query_panel.combos) == {"site_key", "fab_key", "line_key", "group_key"}
    assert [b.text() for b in page.legend_tools] == ["전체 해제"]  # 버튼 하나로 선택·해제 전환
    # 요청 2-1: 날짜 칩 대신 재생 간격
    assert [b.text() for b in page.interval_buttons.values()] == ["저장일마다", "1주", "1개월", "3개월"]
    assert page.interval_buttons[None].isChecked()
    # 요청 3-3: 재생 버튼은 기호, 이름은 그대로
    assert [(b.text(), b.accessibleName()) for b in (page.play_button, page.pause_button, page.restart_button)] == [
        ("▶", "재생"), ("‖", "일시정지"), ("↺", "처음부터")]
    # 기호가 앱 글꼴에 있어야 네모로 깨지지 않는다.
    metrics = QFontMetrics(page.play_button.font())
    assert all(metrics.inFontUcs4(ord(b.text())) for b in (page.play_button, page.pause_button, page.restart_button))
    assert page.start.text() == "2026-05-21" and page.end.text() == "2026-10-21"
    assert page.query_panel.applied_summary.isHidden()
    assert page.heatmap.visible_ids and len(page.heatmap.group_cards) == 9
    assert page.version_button.text() == "조회 버전 설정"
    card = page.heatmap.group_cards[0].layout()
    for index, size in ((0, 18), (1, 14)):
        heading = card.itemAt(index).widget()
        assert " / " in heading.text() and heading.text().endswith("대") and heading.font().pixelSize() == size
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
    page.advance()
    assert page.index == 1 and page.pause_button.isEnabled()
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


def test_group_buttons_filter_lines_achievement_and_heatmap_with_one_toggle_button(own):
    """요청 2-2: 설비군 버튼에서 고른 설비군으로 그래프 선·달성률·히트맵을 함께 좁힌다.

    전체 선택·해제는 버튼 하나: 모두 골라져 있으면 누를 때 전체 해제, 아니면 전체 선택 (11-10 요청).
    """
    page = replay_page(own)
    full = page.achievement.text(), page.heatmap.visible_ids
    toggle = page.toggle_all_button
    group = page.legend_buttons["c3"]
    assert toggle.text() == "전체 해제" and toggle.isChecked()
    filled = group.grab().toImage().pixelColor(8, group.height() // 2)
    toggle.click()
    assert toggle.text() == "전체 선택" and not toggle.isChecked()
    assert not any(b.isChecked() for b in page.legend_buttons.values())
    assert group.grab().toImage().pixelColor(8, group.height() // 2) != filled
    assert page.chart.values == {} and page.heatmap.group_cards == [] and page.heatmap.visible_ids == ()
    assert "하나 이상" in page.achievement.text() and page.progress.value() == 0
    page.legend_buttons["c3"].click()
    cell = next(g for g in page.frame.snapshot.groups if g.group_keys == ("c3",))
    assert set(page.chart.values) == {"c3"} and len(page.heatmap.group_cards) == 1
    assert set(page.heatmap.visible_ids) == set(cell.equipment_ids)
    assert page.achievement.text().startswith(f"등록 SW 목표 달성 {cell.summary.compliant} / "
                                              f"{cell.summary.compliant + cell.summary.below}건")
    assert "HPA" not in page.achievement.toolTip()
    assert toggle.text() == "전체 선택"  # 일부만 골라져 있으면 누를 때 전체 선택
    toggle.click()
    assert toggle.text() == "전체 해제" and toggle.isChecked()
    assert all(b.isChecked() for b in page.legend_buttons.values())
    assert (page.achievement.text(), page.heatmap.visible_ids) == full and len(page.chart.values) == 9


def test_group_buttons_hide_absent_groups_and_bulk_only_changes_available_groups(own):
    source = demo_dashboard_source()
    source.records = tuple(record for record in source.records
                           if source.locations[record.equipment_id].site_key != "hwaseong"
                           or not any(pm.group_id in source.bindings["c4"] for pm in record.pms))
    page = own(ReplayPage(source))
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
    assert page.heatmap.visible_ids == next(
        cell.equipment_ids for cell in page.frame.snapshot.groups if cell.group_keys == ("o2",)
    )


def test_group_buttons_match_dashboard_style_and_keep_their_height(own):
    window = own(create_window())
    window.resize(1600, 960)
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
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
    # 저장일 자료를 이어 쓰는 시점은 달성률 줄에서도 바로 보인다 (11-11 점검).
    assert page.achievement.text().endswith(" · 05-21 저장 기준")
    # 재생 중 간격을 바꾸면 멈추지 않고, 보던 날짜(05-28) 전 가장 가까운 시점부터 이어서 재생한다.
    page.interval_buttons["quarter"].click()
    assert [f.day for f in page.frames] == [date(2026, 5, 21), date(2026, 8, 21), date(2026, 10, 21)]
    assert page.timer.isActive() and page.index == 0 and page.frame.day == date(2026, 5, 21)
    assert "저장 기준" not in page.achievement.text()   # 저장일과 같은 시점은 붙이지 않는다
    page.pause()
    page.interval_buttons[None].click()
    assert page.frames == page.data.frames and len(calls) == 1


def test_interval_too_long_for_period_is_disabled(own):
    page = replay_page(own)
    set_period(page, "2026-05-21", "2026-06-21")
    assert page.apply_query()
    until(lambda: not page.busy)
    assert page.interval_buttons["month"].isEnabled() and page.interval_buttons["week"].isEnabled()
    page.end.setDate(QDate(2026, 5, 21))
    until(lambda: not page.busy)
    assert not any(page.interval_buttons[s].isEnabled() for s in ("week", "month", "quarter"))
    assert not page.set_interval("week") and page.step is None


def test_chart_line_and_points_show_dashboard_trend_hover(own):
    page = replay_page(own)
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


def test_playback_buttons_are_centered_above_the_view_tabs(own):
    page = replay_page(own)
    page.show()
    QTest.qWait(50)
    y = lambda w: w.mapTo(page, QPoint()).y()  # noqa: E731
    assert y(page.play_button) + page.play_button.height() <= y(page.view_tabs)
    assert y(page.play_button) == y(page.pause_button) == y(page.restart_button)
    row = page.play_button.parentWidget()
    row_center = row.mapTo(page, row.rect().center()).x()
    buttons_center = page.pause_button.mapTo(page, page.pause_button.rect().center()).x()
    assert abs(row_center - buttons_center) <= 1


def test_view_tabs_show_one_view_and_share_the_selected_frame(own):
    page = replay_page(own)
    assert [page.view_tabs.tabText(i) for i in range(page.view_tabs.count())] == ["달성률 추이", "히트맵"]
    assert page.chart.isVisibleTo(page) and not page.heatmap.isVisibleTo(page)
    page.select_date(2)
    frame, data = page.frame, page.data
    page.view_tabs.setCurrentIndex(1)
    assert not page.chart.isVisibleTo(page) and page.heatmap.isVisibleTo(page)
    assert page.frame is frame and page.data is data and page.play_button.isEnabled()
    page.view_tabs.setCurrentIndex(0)
    assert page.chart.isVisibleTo(page) and not page.heatmap.isVisibleTo(page) and page.chart.current == 2


def test_version_popup_all_clear_cancel_confirm_and_reapply_without_changing_n(own):
    page = replay_page(own)
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
    assert page.busy and not page.play_button.isEnabled()
    until(lambda: not page.busy)
    assert page.source.targets == n and page.data.request.query.selection.versions == VersionFilter(("v1.0.1",))


def test_site_choices_apply_while_popup_stays_open_and_reset_preserves_targets(own):
    page = replay_page(own)
    data, n = page.data, dict(page.source.targets)
    page.play()
    panel = page.query_panel
    panel.combos["site_key"].click()
    popup = panel.filter_popup
    popup.all_button.click()
    assert page.data is data and page.timer.isActive()
    popup.checks["hwaseong"].click()
    assert not page.timer.isActive() and page.busy
    assert popup.isVisible() and popup.checks["pyeongtaek"].isEnabled()
    popup.checks["pyeongtaek"].click()
    until(lambda: not page.busy)
    assert page.data.request.query.selection.site_key == ("hwaseong", "pyeongtaek")
    assert popup.isVisible()
    popup.confirm_button.click()
    page.reset_button.click()
    until(lambda: not page.busy)
    assert page.state.draft == page.data.request.query.selection == QuerySelection() and page.source.targets == n
    assert page.start.text() == "2026-05-21" and page.end.text() == "2026-10-21"


def test_inverted_period_never_starts_external_loader(own):
    calls = []
    page = own(ReplayPage(demo_dashboard_source(), loader=lambda request: calls.append(request)))
    page.start.setDate(QDate(2026, 11, 1))
    assert not page.busy and calls == []
    assert "시작 날짜는 종료 날짜와 같거나 앞서야" in page.message.text()


def test_replay_calendar_month_navigation_does_not_apply_until_day_is_confirmed(own):
    calls = []
    source = demo_dashboard_source()
    page = replay_page(own, loader=lambda request: calls.append(request) or load_replay(source, request))
    QTest.mouseClick(page.end.lineEdit(), Qt.MouseButton.LeftButton)
    calendar = page.end.calendarWidget()
    assert calendar.isVisible()
    calendar.previous_button.click()
    chosen = QDate(2026, 9, 21)
    calendar.setSelectedDate(chosen)
    assert len(calls) == 1 and not page.busy
    calendar.activated.emit(chosen)
    until(lambda: not page.busy)
    assert len(calls) == 2 and page.data.request.end == chosen.toPython()


def test_empty_single_date_and_zero_match_are_not_playable_or_fake_zero_percent(own):
    page = replay_page(own)
    set_period(page, "2025-01-01", "2025-12-31")
    page.apply_query()
    until(lambda: not page.busy)
    assert page.data.frames == () and not page.play_button.isEnabled() and page.frames == ()
    assert not page.heatmap.visible_ids and "저장된 관측일이 없습니다" in page.message.text()
    set_period(page, "2026-05-21", "2026-05-21")
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
        assert page.apply_query() and page.busy and not page.reset_button.isEnabled()
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
    assert page.reset_button.isEnabled() and not page.play_button.isEnabled()
    page.loader = None
    page.reset_button.click()
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
    tile = page.heatmap.tile_buttons[1]
    window.scroll.ensureWidgetVisible(tile)
    QTest.qWait(30)
    before = page.data, page.index, page.state.draft
    QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton, pos=tile.mapTo(window, tile.rect().center()))
    assert page.drawer.isVisibleTo(window) and window.route == "replay"
    assert page.drawer.parentWidget() is window.centralWidget()
    assert page.drawer.request.equipment_id == 1
    assert page.drawer.detail.record.base_date == page.frame.day
    assert len(page.drawer.software_cards) == 3 and not page.drawer.edit_available
    page.drawer.close_panel()
    assert (page.data, page.index, page.state.draft) == before
    assert not page.timer.isActive()

    assert page.set_interval("week") and page.select_date(1)
    assert page.frame.day == date(2026, 5, 28) and page.open_equipment(1)
    observation = date(2026, 5, 21)
    assert page.drawer.request.query is page.frame.snapshot.query
    assert page.drawer.request.base_date == page.frame.snapshot.base_date == observation
    assert page.drawer.request.observation_date == page.drawer.detail.record.base_date == observation
    assert page.drawer.info_values["기준일"].text() == str(observation)


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
    assert page.reset_button.iconSize() == window.query_panel.reset_button.iconSize()
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


def test_initial_layout_has_no_expanded_chart_blank_and_buttons_keep_figma_heights(own):
    window = own(create_window())
    window.resize(1600, 960)
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    QTest.qWait(100)
    assert page.chart.height() == 300
    assert page.chart.mapTo(window, QPoint()).y() < 750
    assert all(b.height() == 34 for b in page.interval_buttons.values())
    assert all(b.height() == 40 for b in (page.play_button, page.pause_button, page.restart_button))
    page.select_date(0)
    page.legend_buttons["c3"].click()
    QTest.qWait(20)
    assert page.interval_buttons[None].height() == 34


def test_replay_ticks_do_not_jump_scroll_or_leave_a_detail_from_another_date(own):
    window = own(create_window())
    window.resize(1600, 960)
    window.navigate("replay")
    page = window.pages["replay"]
    until(lambda: not page.busy)
    page.view_tabs.setCurrentIndex(1)
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


def test_saved_n_groups_remain_effective_without_query_summary(own):
    source = demo_dashboard_source()
    key, release = next(iter(sorted(source.targets.items())))
    source.targets.clear()
    source.saved_groups = {key: VersionGroups({Slot.N: frozenset({release})})}
    page = own(ReplayPage(source))
    assert page.apply_query()
    until(lambda: not page.busy)
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


def test_heatmap_reuses_tiles_and_matches_version_heatmap_presentation(own):
    page = replay_page(own)
    heatmap, data, frame = page.heatmap, page.data, page.frame
    before = list(heatmap.tile_occurrences)
    group, areas = heatmap._plan(data, frame)[0]
    reference = own(HeatmapPage(QueryState(data.source.catalog), data.source))
    card = reference._group_card(group, [tile for _, items in areas for tile, _ in items], True, group)
    control = before[0]
    reference_tile = reference.tile_buttons[control.data.equipment_id]
    assert control.code_label.alignment() == reference_tile.code_label.alignment() == Qt.AlignmentFlag.AlignCenter
    assert control.version_label.text() == reference_tile.version_label.text()
    assert control.styleSheet() == reference_tile.styleSheet() and ":hover" in control.styleSheet()
    assert control.toolTip() == reference_tile.toolTip() == control.data.description
    assert heatmap.group_cards[0].styleSheet() == card.styleSheet()
    assert heatmap.group_cards[0].layout().spacing() == card.layout().spacing()
    card.deleteLater()
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
