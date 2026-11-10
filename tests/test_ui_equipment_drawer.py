"""설비 정보 패널의 라우트 유지, 실제 판정·관측 버전 표시, 수정 경계 회귀 검사."""

from dataclasses import replace
from datetime import timedelta
from threading import Event, get_ident

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from setron.features.aggregation.schemas import PmVersion
from setron.ui.app import create_window
from setron.ui.equipment_drawer import EquipmentDrawer, observed_software
from setron.ui.heatmap_data import EquipmentSelection, GoalState
from setron.ui.query_state import QuerySelection
from setron.ui.theme import svg_size

from test_ui_heatmap import DAY, app, source
from test_ui_management import FakeGateway, record, wait_for


@pytest.fixture
def window(app, source):
    result = create_window(source)
    result.resize(1440, 960)
    result.show()
    result.navigate("heatmap")
    result.pages["heatmap"].hpa_button.setChecked(True)
    result.query_panel.apply_draft()
    QTest.qWait(10)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def open_tile(window, eid=1):
    window.pages["heatmap"].tile_buttons[eid].click()
    QApplication.processEvents()
    assert window.equipment_drawer.isVisible()
    return window.equipment_drawer


def test_open_close_preserves_applied_query_scroll_view_search_selection(window, app):
    page, dashboard = window.pages["heatmap"], window.pages["dashboard"]
    page.view_toggle.setChecked(True)
    before = page.snapshot
    metrics = tuple(c.value.text() for c in dashboard.metrics.values())
    bar = window.scroll.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    position = bar.value()
    drawer = open_tile(window)
    assert window.route == "heatmap" and not window.query_panel.isHidden()
    assert page.snapshot is before and bar.value() == position
    assert tuple(c.value.text() for c in dashboard.metrics.values()) == metrics
    assert drawer.panel.width() == 560 and drawer.panel.height() == window.centralWidget().height()
    drawer.close_button.click()
    wait_for(app, drawer.isHidden)
    assert drawer.isHidden() and bar.value() == position and page.snapshot is before
    assert page.view_toggle.isChecked() and page.selected_id == 1
    assert page.tile_buttons[1].isChecked() and app.focusWidget() is page.tile_buttons[1]


def test_escape_backdrop_and_tab_keep_keyboard_in_panel(window, app):
    drawer = open_tile(window)
    assert app.focusWidget() is drawer.panel
    QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
    assert app.focusWidget() is drawer.close_button
    for _ in range(18):
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
        assert drawer.isAncestorOf(app.focusWidget())
    QTest.keyClick(app.focusWidget(), Qt.Key.Key_Escape)
    assert not drawer.is_open and app.focusWidget() is window.pages["heatmap"].tile_buttons[1]
    wait_for(app, drawer.isHidden)
    open_tile(window)
    drawer.backdrop.click()
    wait_for(app, drawer.isHidden)


def test_drawer_slides_each_time_and_can_reopen_during_close(window, app):
    drawer = open_tile(window)
    assert drawer.panel.geometry().right() > drawer.rect().right()
    wait_for(app, lambda: drawer.panel.geometry().right() == drawer.rect().right())
    drawer.close_button.click()
    assert not drawer.is_open and drawer.isVisible()
    wait_for(app, lambda: drawer.panel.geometry().right() > drawer.rect().right())
    open_tile(window, 2)
    assert drawer.is_open and drawer.request.equipment_id == 2
    wait_for(app, lambda: drawer.panel.geometry().right() == drawer.rect().right())
    drawer.close_button.click()
    wait_for(app, drawer.isHidden)
    open_tile(window)
    assert drawer.panel.geometry().right() > drawer.rect().right()
    wait_for(app, lambda: drawer.panel.geometry().right() == drawer.rect().right())


def test_back_action_closes_drawer_without_leaving_heatmap(window, app):
    heatmap = window.pages["heatmap"]
    heatmap.view_toggle.setChecked(True)
    snapshot = heatmap.snapshot
    drawer = open_tile(window, 1)
    controls = [drawer.header.itemAt(index).widget() for index in range(drawer.header.count())]
    assert [control for control in controls if isinstance(control, QPushButton)] == [drawer.close_button]
    assert window.back_shortcut.isEnabled()
    window.back_shortcut.trigger()
    wait_for(app, drawer.isHidden)
    assert window.route == "heatmap"
    assert heatmap.snapshot is snapshot and heatmap.view_toggle.isChecked()
    assert heatmap.selected_id == 1 and heatmap.tile_buttons[1].isChecked()
    assert app.focusWidget() is heatmap.tile_buttons[1]


def test_new_selection_reuses_one_panel_and_resets_scroll(window):
    drawer = open_tile(window)
    drawer.scroll.verticalScrollBar().setValue(50)
    assert open_tile(window, 2) is drawer
    assert window.centralWidget().findChildren(EquipmentDrawer, "equipmentDrawerOverlay") == [drawer]
    assert drawer.request.equipment_id == 2 and drawer.info_values["설비명"].text() == "TEST-2"
    assert len(drawer.software_cards) == 1
    assert drawer.scroll.verticalScrollBar().value() == 0


def test_software_shows_current_and_target_on_separate_rows_without_verdict_or_count(window):
    drawer = open_tile(window)
    card = next(card for card in drawer.software_cards if card.observation.current == "v99")
    labels = {label.text(): label for label in card.findChildren(QLabel) if label.isVisibleTo(card)}
    current, target = labels["현재 버전"], labels["목표 버전"]
    assert current.mapTo(card, QPoint()).y() < target.mapTo(card, QPoint()).y()
    assert labels["v99"].mapTo(card, QPoint()).y() < labels["v2"].mapTo(card, QPoint()).y()
    assert current.font().pixelSize() == target.font().pixelSize() == 14
    assert labels["v99"].font().pixelSize() == labels["v2"].font().pixelSize() == 16
    assert "목표 기준" not in labels
    assert not any(text.startswith(("N-1", "N-2")) or "근거" in text for text in labels)
    captions = [label.text() for label in drawer.body_widget.findChildren(QLabel)
                if label.isVisibleTo(drawer.body_widget)]
    assert not any(text.startswith(("판정 요약", "관측된 SW (", "관측 SW ")) or "원본 근거" in text for text in captions)


@pytest.mark.parametrize("eid,goal,cards", [(1, GoalState.BELOW, 3), (2, GoalState.MET, 1),
                                         (3, GoalState.UNKNOWN, 1), (4, GoalState.EXCLUDED, 0),
                                         (5, GoalState.UNKNOWN, 0)])
def test_representative_states_are_real_data_not_figma_demo(window, eid, goal, cards):
    drawer = open_tile(window, eid)
    assert drawer.goal_badge.text() == goal.value
    assert len(drawer.software_cards) == cards
    if eid == 2:
        assert "위험 후보" in drawer.risk_badge.text()  # 목표 달성 ≠ 위험 해제
        observation = drawer.software_cards[0].observation
        assert observation.current == observation.target == "v2"
    if eid == 3:
        assert drawer.software_cards[0].observation.current == "미입력"
    if eid == 4:
        assert "위험 분석 제외" in drawer.risk_badge.text()
    assert drawer.info_values["설비명"].text() == f"TEST-{eid}"
    assert not drawer.edit_button.isEnabled()  # 합성 공급자는 실제 DB 행을 편집할 수 없다.


def test_panel_shows_actual_equipment_information_in_one_card(window):
    drawer = open_tile(window)
    assert drawer.title.text() == "설비 정보"
    assert drawer.goal_badge.parentWidget() is drawer.risk_badge.parentWidget()
    assert all(drawer.info_card.isAncestorOf(value) for value in drawer.info_values.values())
    expected = {
        "Site": "테스트 A", "Line": "Line A", "모델": "TEST", "설비명": "TEST-1", "기준일": str(DAY),
    }
    assert {name: value.text() for name, value in drawer.info_values.items()} == expected
    labels = {label.text(): label for label in drawer.info_card.findChildren(QLabel)}
    positions = []
    for name in expected:
        caption, value = labels[name], drawer.info_values[name]
        caption_pos = caption.mapTo(drawer.info_card, QPoint())
        value_pos = value.mapTo(drawer.info_card, QPoint())
        assert caption_pos.x() < value_pos.x() and caption_pos.y() == value_pos.y()
        assert caption.font().pixelSize() == 14 and value.font().pixelSize() == 15
        positions.append(caption_pos.y())
    assert positions == sorted(set(positions))


def test_selected_group_keeps_all_sw_in_detail_without_widening_scope(window):
    window.query_panel.set_draft(QuerySelection(group_key="o3"))
    window.query_panel.apply_draft()
    drawer = open_tile(window)
    assert window.pages["heatmap"].snapshot.query.selection.group_key == "o3"
    assert drawer.detail.selected.goal is GoalState.MET and drawer.detail.whole.goal is GoalState.BELOW
    assert drawer.goal_badge.text() == GoalState.BELOW.value
    assert len(drawer.software_cards) == 3
    assert {c.observation.model.split(" · ")[0] for c in drawer.software_cards} == {"O2", "O3"}


def test_same_sw_grouping_keeps_every_pm_and_separates_different_versions(window):
    drawer = open_tile(window)
    detail = drawer.detail
    pms = (PmVersion("PM1", 101, 1001, "v2"), PmVersion("PM2", 101, 1001, "v2"))
    # 원래 비교 결과 두 PM을 같은 모델/계열·동일 판정으로 복제하여 표시 그룹만 검사한다.
    family = detail.status.families[0]
    item = family.pms[1]
    same = replace(detail, record=replace(detail.record, pms=pms), status=replace(
        detail.status, families=(replace(family, pms=tuple(replace(item, pm=p) for p in pms)),)))
    rows = [(p.pm_name, "O2 · 계열 O2", "v2", "v2", "N 이상", "확인된 릴리즈 순서로 비교") for p in pms]
    grouped = observed_software(same, rows)
    assert len(grouped) == 1 and grouped[0].pms == ("PM1", "PM2")
    assert grouped[0].current == grouped[0].target == "v2"
    distinct_rows = (rows[0], ("PM2", "O2 · 계열 O2", "v99", "v2", "N-1", "확인된 릴리즈 순서로 비교"))
    assert [(row.current, row.pms) for row in observed_software(same, distinct_rows)] == [("v2", ("PM1",)), ("v99", ("PM2",))]


def test_draft_keeps_applied_detail_but_apply_navigation_and_catalog_close_it(window):
    drawer = open_tile(window)
    request = drawer.request
    window.query_panel.select("site_key", "s2")
    assert drawer.isVisible() and drawer.request is request
    window.query_panel.apply_draft()
    assert drawer.isHidden() and drawer.request is None
    open_tile(window, 3)
    window.navigate("dashboard")
    assert drawer.isHidden()
    window.navigate("heatmap")
    open_tile(window, 3)
    window.query_panel.catalog_changed.emit(window.query_state.catalog)
    assert drawer.isHidden()


def test_stale_query_or_wrong_observation_does_not_open_panel(window):
    page = window.pages["heatmap"]
    query = page.snapshot.query
    page.equipment_selected.emit(EquipmentSelection(query, DAY, 1, DAY - timedelta(days=1)))
    assert window.equipment_drawer.isHidden()
    window.query_panel.apply_draft()
    page.equipment_selected.emit(EquipmentSelection(query, DAY, 1, DAY))
    assert window.equipment_drawer.isHidden()


def test_resize_and_long_text_stay_inside_panel_without_horizontal_scroll(window, app):
    drawer = open_tile(window)
    wait_for(app, lambda: drawer.panel.geometry().right() == drawer.rect().right())
    for width, height in ((960, 600), (1920, 1080), (1440, 960)):
        window.resize(width, height)
        app.processEvents()
        assert drawer.geometry() == window.centralWidget().rect()
        assert drawer.panel.width() == 560 and drawer.panel.geometry().right() == drawer.rect().right()
        assert drawer.scroll.horizontalScrollBar().maximum() == 0
        assert drawer.body_widget.width() == drawer.scroll.viewport().width()
    assert drawer.close_button.iconSize() == svg_size("529be.svg")
    assert not drawer.close_button.text()


def test_long_raw_version_is_plain_text_and_remains_inside_drawer(window, source, monkeypatch, app):
    raw = "v90.1.0.41(H) + Hotfix <검토 중> " * 8
    monkeypatch.setattr(source, "records", tuple(replace(r, pms=tuple(
        replace(p, version_raw=raw) if p.pm_name == "PM1" else p for p in r.pms)) for r in source.records))
    window.query_panel.apply_draft()
    drawer = open_tile(window)
    assert drawer.software_cards[0].observation.current == raw
    rendered = next(label for label in drawer.software_cards[0].findChildren(QLabel) if label.text() == raw)
    assert rendered.textFormat() == Qt.TextFormat.PlainText
    window.resize(960, 600)
    app.processEvents()
    assert drawer.scroll.horizontalScrollBar().maximum() == 0
    assert drawer.body_widget.width() == drawer.scroll.viewport().width()
    assert drawer.software_cards[0].width() <= drawer.body_widget.width()


def test_hpa_uses_its_actual_observation_date_and_no_comparison(window, source, monkeypatch):
    observed = DAY - timedelta(days=2)
    monkeypatch.setattr(source, "records", tuple(replace(r, base_date=observed,
        pms=(PmVersion(None, 101, 1001, "HPA 원문"),)) if r.is_hpa else r for r in source.records))
    window.query_panel.apply_draft()
    drawer = open_tile(window, 4)
    assert drawer.request.base_date == DAY and drawer.request.observation_date == observed
    observation = drawer.software_cards[0].observation
    assert drawer.goal_badge.text() == GoalState.EXCLUDED.value and observation.current == "HPA 원문"
    assert observation.target == "해당 없음"
    assert not observation.pms
    assert drawer.info_values["기준일"].text() == str(observed)
    assert "해당 없음" in {label.text() for label in drawer.software_cards[0].findChildren(QLabel)}


def test_query_failure_removes_old_panel_and_edit_context(window, source, monkeypatch):
    drawer = open_tile(window)
    def fail(_):
        raise RuntimeError("읽기 실패")
    monkeypatch.setattr(source, "snapshot", fail)
    window.query_panel.apply_draft()
    assert drawer.isHidden() and drawer.request is None
    assert window.pages["equipment"].data is None


class DrawerGateway(FakeGateway):
    def __init__(self, choices=(), error=None):
        super().__init__()
        self.choices, self.lookup_error, self.lookups = choices, error, []
        self.lookup_gate = Event()
        self.lookup_gate.set()

    def equipment_versions(self, eid, day):
        self.lookups.append((eid, day, get_ident()))
        assert self.lookup_gate.wait(3)
        if self.lookup_error:
            raise self.lookup_error
        return self.choices


@pytest.fixture
def connected_window(app, source):
    gateway = DrawerGateway((("equipment_version", {"equipment_version_id": 432}, "공통 SW · v2"),))
    gateway.rows["equipment_version"] = (record("equipment_version", {"equipment_version_id": 432},
                                               {"family_id": 1001, "version_raw": "v2", "release_version_id": None},
                                               ("version_raw",)),)
    result = create_window(source, management_gateway=gateway)
    result.show()
    result.navigate("heatmap")
    result.query_panel.apply_draft()
    yield result, gateway
    gateway.lookup_gate.set()
    wait_for(app, lambda: not result.equipment_edit_request.busy)
    result.close()
    result.deleteLater()
    app.processEvents()


def test_edit_resolves_actual_db_key_off_thread_then_existing_review_editor(connected_window, app):
    window, gateway = connected_window
    drawer = open_tile(window, 2)
    drawer.edit_button.click()
    wait_for(app, lambda: window.route == "management" and not window.pages["management"].busy)
    assert gateway.lookups[0][:2] == (2, DAY) and gateway.lookups[0][2] != get_ident()
    assert window.pages["management"].record.key == {"equipment_version_id": 432}
    assert drawer.isHidden() and gateway.saves == []


def test_optional_request_date_uses_validated_record_date_for_editor(connected_window, app):
    window, gateway = connected_window
    heatmap = window.pages["heatmap"]
    heatmap.equipment_selected.emit(EquipmentSelection(heatmap.snapshot.query, DAY, 2))
    window.equipment_drawer.edit_button.click()
    wait_for(app, lambda: window.route == "management" and not window.pages["management"].busy)
    assert gateway.lookups[0][:2] == (2, DAY)


def test_edit_lookup_failure_and_empty_records_stay_in_heatmap(connected_window, app):
    window, gateway = connected_window
    drawer = open_tile(window)
    gateway.lookup_error = RuntimeError("sensitive DB connection string")
    drawer.edit_button.click()
    wait_for(app, lambda: not window.equipment_edit_request.busy)
    assert window.route == "heatmap" and drawer.isVisible() and drawer.edit_button.isEnabled()
    assert "읽지 못했습니다" in drawer.edit_notice.text() and "sensitive" not in drawer.edit_notice.text()
    gateway.lookup_error, gateway.choices = None, ()
    drawer.edit_button.click()
    wait_for(app, lambda: not window.equipment_edit_request.busy)
    assert "SW 기록이 없습니다" in drawer.edit_notice.text() and gateway.saves == []


def test_edit_multiple_pms_require_explicit_choice_and_cancelled_read_is_ignored(connected_window, app):
    window, gateway = connected_window
    gateway.choices = (("equipment_pm", {"pm_id": 98}, "PM1 · v2"), ("equipment_pm", {"pm_id": 99}, "PM2 · v2"))
    drawer = open_tile(window)
    drawer.edit_button.click()
    wait_for(app, lambda: drawer.edit_menu is not None)
    assert [a.text() for a in drawer.edit_menu.actions()] == ["PM1 · v2", "PM2 · v2"]
    assert window.route == "heatmap" and gateway.saves == []
    drawer.edit_menu.close()
    gateway.lookup_gate.clear()
    drawer.edit_button.click()
    drawer.close_button.click()
    gateway.lookup_gate.set()
    wait_for(app, drawer.isHidden)
    assert drawer.isHidden() and window.route == "heatmap" and gateway.saves == []


def test_management_lock_blocks_edit_without_leaving_heatmap(connected_window):
    window, gateway = connected_window
    drawer = open_tile(window)
    window.pages["management"].locked = True
    drawer.edit_button.click()
    assert not gateway.lookups and window.route == "heatmap"
    assert "마친 뒤" in drawer.edit_notice.text()
