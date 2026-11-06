"""Figma 2-1: 라우트 유지·단일 패널·실제 판정·근거·수정 경계 회귀 검사."""

from dataclasses import replace
from datetime import timedelta
from threading import Event, get_ident

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

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
    page.view_buttons.button(1).click()
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
    app.processEvents()
    assert drawer.isHidden() and bar.value() == position and page.snapshot is before
    assert page.view_buttons.checkedId() == 1 and page.selected_id == 1
    assert page.tile_buttons[1].isChecked() and app.focusWidget() is page.tile_buttons[1]


def test_escape_backdrop_and_tab_keep_keyboard_in_panel(window, app):
    drawer = open_tile(window)
    for _ in range(18):
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
        assert drawer.isAncestorOf(app.focusWidget())
    QTest.keyClick(app.focusWidget(), Qt.Key.Key_Escape)
    assert drawer.isHidden() and app.focusWidget() is window.pages["heatmap"].tile_buttons[1]
    open_tile(window)
    drawer.backdrop.click()
    assert drawer.isHidden()


def test_new_selection_reuses_one_panel_and_resets_accordions_scroll(window):
    drawer = open_tile(window)
    drawer.evidence_toggle.click()
    assert drawer.evidence_content.isVisible()
    drawer.software_cards[0].toggle.click()
    drawer.scroll.verticalScrollBar().setValue(50)
    open_tile(window, 2)
    assert len(window.centralWidget().findChildren(EquipmentDrawer)) == 1
    assert drawer.request.equipment_id == 2 and "TEST-2" in drawer.title.text()
    assert not drawer.evidence_toggle.isChecked() and not drawer.evidence_content.isVisible()
    assert all(c.toggle.isChecked() for c in drawer.software_cards)
    assert all(c.chevron.expanded and c.chevron.isVisible() for c in drawer.software_cards)
    assert not drawer.evidence_chevron.expanded and drawer.evidence_chevron.isVisible()
    assert drawer.scroll.verticalScrollBar().value() == 0


def test_sw_disclosure_arrow_stays_visible_and_changes_direction_for_mouse_and_keyboard(window, app):
    drawer = open_tile(window)
    for card in drawer.software_cards:
        arrow = card.chevron
        up = arrow.pixmap().toImage()
        assert arrow.expanded and arrow.isVisible() and arrow.size() == svg_size("c9be0.svg")
        for _ in range(2):
            # 화살표 슬롯을 클릭해도 전체 헤더와 동일하게 토글한다.
            QTest.mouseClick(card.toggle, Qt.MouseButton.LeftButton,
                             pos=arrow.mapTo(card.toggle, QPoint(8, 8)))
            app.processEvents()
            assert not card.toggle.isChecked() and not card.contents.isVisible()
            assert arrow.isVisible() and not arrow.expanded
            assert arrow.pixmap().toImage() != up and arrow.pixmap().size() == svg_size("c9be0.svg")
            assert card.toggle.accessibleName().endswith("펼치기")
            card.toggle.setFocus()
            QTest.keyClick(card.toggle, Qt.Key.Key_Space)
            app.processEvents()
            assert card.toggle.isChecked() and card.contents.isVisible()
            assert arrow.isVisible() and arrow.expanded and arrow.pixmap().toImage() == up
            assert card.toggle.accessibleName().endswith("접기")


def test_evidence_uses_arrow_instead_of_visible_expand_collapse_text(window, app):
    drawer = open_tile(window)
    toggle, arrow = drawer.evidence_toggle, drawer.evidence_chevron
    down = arrow.pixmap().toImage()
    assert arrow.isVisible() and not arrow.expanded and not drawer.evidence_content.isVisible()
    for expected in (True, False, True, False):
        toggle.click()
        app.processEvents()
        assert arrow.isVisible() and arrow.expanded is expected
        assert drawer.evidence_content.isVisible() is expected
        assert (arrow.pixmap().toImage() != down) is expected
        assert arrow.size() == svg_size("c9be0.svg")
        assert all(child.text() not in {"펼치기", "접기"} for child in toggle.findChildren(QLabel))
        assert toggle.accessibleName().endswith("접기" if expected else "펼치기")


@pytest.mark.parametrize("eid,goal,cards", [(1, GoalState.BELOW, 3), (2, GoalState.MET, 1),
                                         (3, GoalState.UNKNOWN, 1), (4, GoalState.EXCLUDED, 0),
                                         (5, GoalState.UNKNOWN, 0)])
def test_representative_states_are_real_data_not_figma_demo(window, eid, goal, cards):
    drawer = open_tile(window, eid)
    assert drawer.summary_title.text() == goal.value
    assert len(drawer.software_cards) == cards
    if eid == 2:
        assert "위험 후보" in drawer.risk_badge.text()  # 목표 달성 ≠ 위험 해제
        assert drawer.software_cards[0].observation.state == "met"
    if eid == 3:
        observation = drawer.software_cards[0].observation
        assert observation.state == "unknown" and observation.current == "미입력"
        assert "버전 값이 비어" in observation.reason
    if eid == 4:
        assert "위험 분석 제외" in drawer.risk_badge.text()
    assert "DM-067" not in drawer.title.text()
    assert not drawer.edit_button.isEnabled()  # 합성 공급자는 실제 DB 행을 편집할 수 없다.


def test_evidence_uses_actual_observation_and_does_not_invent_source_links(window):
    drawer = open_tile(window)
    values = {title: value for _, title, value in drawer.evidence_values}
    assert str(DAY) in values["실제 관측일"] and "일반 분석 기준일" in values["실제 관측일"]
    assert values["관측 출처"] == "합성 예시 · 테스트 전용"
    assert values["판정 규칙"] == values["필수 아이템명"] == values["릴리즈 문서 · 아이템 위치"] == "미연결"
    assert values["비교 목표 N"] == "v2"


def test_selected_group_keeps_all_sw_in_detail_without_widening_scope(window):
    window.query_panel.set_draft(QuerySelection(group_key="o3"))
    window.query_panel.apply_draft()
    drawer = open_tile(window)
    assert window.pages["heatmap"].snapshot.query.selection.group_key == "o3"
    assert drawer.detail.selected.goal is GoalState.MET and drawer.detail.whole.goal is GoalState.BELOW
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
    assert grouped[0].state == "met" and grouped[0].current == "v2"


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
    drawer.evidence_toggle.click()
    for width, height in ((960, 600), (1920, 1080), (1440, 960)):
        window.resize(width, height)
        app.processEvents()
        assert drawer.geometry() == window.centralWidget().rect()
        assert drawer.panel.width() == 560 and drawer.panel.geometry().right() == drawer.rect().right()
        assert drawer.scroll.horizontalScrollBar().maximum() == 0
        assert drawer.body_widget.width() == drawer.scroll.viewport().width()
    assert drawer.close_button.iconSize() == svg_size("529be.svg")
    for card in drawer.software_cards:
        assert card.toggle.height() >= 54  # 전역 QPushButton min-height가 카드 제목/상태를 가리지 않는다.
        assert all(w.height() > 0 for w in card.toggle.findChildren(QWidget))
    for asset in ("529be.svg", "96d37.svg", "1512d.svg", "c9be0.svg", "b5c69.svg", "08019.svg", "ed395.svg",
                  "98d75.svg", "02a44.svg", "6110f.svg"):
        assert svg_size(asset).width() > 0


def test_long_raw_version_is_plain_text_and_remains_inside_drawer(window, source, monkeypatch, app):
    raw = "v90.1.0.41(H) + Hotfix <검토 중> " * 8
    monkeypatch.setattr(source, "records", tuple(replace(r, pms=tuple(
        replace(p, version_raw=raw) if p.pm_name == "PM1" else p for p in r.pms)) for r in source.records))
    window.query_panel.apply_draft()
    drawer = open_tile(window)
    assert drawer.software_cards[0].observation.current == raw
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
    assert observation.state == "excluded" and observation.current == "HPA 원문"
    assert observation.target == "—"
    values = {title: value for _, title, value in drawer.evidence_values}
    assert str(observed) in values["실제 관측일"] and str(DAY) in values["실제 관측일"]
    assert values["비교 목표 N"] == "HPA 비교 제외"


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
    QTest.qWait(30)
    assert drawer.isHidden() and window.route == "heatmap" and gateway.saves == []


def test_management_lock_blocks_edit_without_leaving_heatmap(connected_window):
    window, gateway = connected_window
    drawer = open_tile(window)
    window.pages["management"].locked = True
    drawer.edit_button.click()
    assert not gateway.lookups and window.route == "heatmap"
    assert "마친 뒤" in drawer.edit_notice.text()
