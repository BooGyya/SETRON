"""5번 작업: 공통 결과·한 설비 한 타일·독립 판정·표시 전환·후속 선택 계약."""

from dataclasses import replace
from datetime import date
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.app import create_window
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding
from setron.ui.heatmap_data import GoalState, equipment_tiles
from setron.ui.query_state import UNASSIGNED, FabOption, GroupOption, LineOption, QueryCatalog, QuerySelection, QueryState, SiteOption
from setron.ui.theme import TONES, install_theme, svg_size


DAY = date(2026, 10, 21)


@pytest.fixture
def source():
    catalog = QueryCatalog(sites=(SiteOption("s1", "테스트 A"), SiteOption("s2", "테스트 B")),
                           fabs=(FabOption("f1", "Fab A", "s1"), FabOption("f2", "빈 Fab", "s2")),
                           lines=(LineOption("l1", "Line A", "s1", "f1"), LineOption("l2", "Line B", "s1", None)),
                           groups=(GroupOption("o2", "O2", "OXIDE"), GroupOption("o3", "O3", "OXIDE")))
    families = {fid: FamilyReleases(fid, (ReleaseVersion(fid * 10 + 1, fid, "v99", 1),
                                        ReleaseVersion(fid * 10 + 2, fid, "v2", 2))) for fid in (1001, 1002)}
    # 문자열의 대소가 아니라 저장된 릴리즈 순서로 N-1을 계산해야 한다.
    records = tuple(EquipmentRecord(eid, f"TEST-{eid}", DAY, "임의 원본 Site", "원본 Line", "TEST", pms, is_hpa=eid == 4)
                    for eid, pms in (
                        (1, (PmVersion("PM1", 101, 1001, "v99"), PmVersion("PM2", 101, 1001, "v2"),
                             PmVersion("PM3", 102, 1002, "v2"))),
                        (2, (PmVersion(None, 101, 1001, "v2"),)),
                        (3, (PmVersion(None, 101, 1001, None),)), (4, ()), (5, ())))
    return DashboardSource(catalog=catalog, bindings=(GroupBinding("o2", (101,)), GroupBinding("o3", (102,))),
                           records=records, locations={1: EquipmentLocation("s1", "P1", "f1", "l1"),
                                                      2: EquipmentLocation("s1", "P1", None, "l2"),
                                                      3: EquipmentLocation("s2"), 4: EquipmentLocation("s2"),
                                                      5: EquipmentLocation("s1")}, families=families,
                           targets={(101, 1001): 10012, (102, 1002): 10022},
                           risks={1: RiskStatus.RISK, 2: RiskStatus.RISK}, source_label="합성 예시 · 테스트 전용")


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


@pytest.fixture
def window(app, source):
    result = create_window(source)
    result.show()
    result.navigate("heatmap")
    QTest.qWait(30)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def test_one_tile_per_physical_equipment_preserves_all_pm_and_comparison(source):
    state = QueryState(source.catalog)
    result = source.snapshot(state.apply())
    tiles = equipment_tiles(result, source)
    assert [t.equipment_id for t in tiles] == list(result.equipment_ids) == [1, 2, 3, 4, 5]
    mixed = tiles[0]
    assert mixed.multi_sw and mixed.pm_mismatch and len(mixed.families) == 2
    assert mixed.goal is GoalState.BELOW and mixed.risk is RiskStatus.RISK
    assert len(mixed.families[0].versions) == 2
    assert "PM1 · v99 · N-1" in mixed.description
    assert "PM2 · v2 · N 이상" in mixed.description
    assert "PM3 · v2 · N 이상" in mixed.description
    assert "Fab Fab A · Line Line A · 원본 구역 P1" in mixed.location
    assert "목표 N: v2" in mixed.description


def test_goal_risk_unknown_hpa_and_unregistered_are_separate(source):
    state = QueryState(source.catalog)
    tiles = equipment_tiles(source.snapshot(state.apply()), source)
    assert tiles[1].goal is GoalState.MET and tiles[1].risk is RiskStatus.RISK
    assert tiles[2].goal is GoalState.UNKNOWN and tiles[2].risk is RiskStatus.UNKNOWN
    assert "미입력" in tiles[2].description and "버전 값이 비어" in tiles[2].description
    assert tiles[3].goal is GoalState.EXCLUDED and "위험 분석 제외" in tiles[3].description
    assert tiles[4].goal is GoalState.UNKNOWN and "SW 미등록" in tiles[4].description
    assert "위험 미확인" in tiles[4].description and "후보 아님" not in tiles[4].description


@pytest.mark.parametrize("selection,ids", [
    (QuerySelection(site_key="s1"), (1, 2, 5)), (QuerySelection(fab_key="f1"), (1,)),
    (QuerySelection(line_key="l2"), (2,)), (QuerySelection(fab_key=UNASSIGNED, line_key="l2"), (2,)),
    (QuerySelection(line_key=UNASSIGNED), (3, 4, 5)), (QuerySelection(fab_key="f2"), ()),
    (QuerySelection(group_key="o3"), (1,)),
])
def test_tiles_use_exact_shared_canonical_scope_not_raw_location(window, selection, ids):
    window.query_panel.set_draft(selection)
    window.query_panel.apply_draft()
    heatmap, dashboard = window.pages["heatmap"], window.pages["dashboard"]
    assert heatmap.snapshot is dashboard.snapshot
    assert set(heatmap.tile_buttons) == set(ids)
    assert ids == dashboard.snapshot.equipment_ids
    if selection.group_key == "o3":
        tile = heatmap.tiles[0]
        assert tile.goal is GoalState.MET and tile.risk is RiskStatus.RISK
        assert len(tile.families) == 1 and not tile.multi_sw


def test_start_draft_edits_and_apply_use_one_shared_result(window, monkeypatch):
    heatmap, dashboard = window.pages["heatmap"], window.pages["dashboard"]
    assert heatmap.snapshot is None and heatmap.tile_buttons == {}
    assert not heatmap.risk_button.isEnabled()
    original = dashboard.source.snapshot
    calls = []
    def counted(query):
        calls.append(query)
        return original(query)
    monkeypatch.setattr(dashboard.source, "snapshot", counted)
    query = window.query_panel.apply_draft()
    assert calls == [query]
    assert dashboard.snapshot is heatmap.snapshot
    assert heatmap.metrics["total"].value.text() == "5대"
    assert heatmap.metrics["rate"].value.text() == "66.7%"
    assert heatmap.metrics["risk"].value.text() == "2대"
    assert heatmap.metrics["upgrade"].value.text() == "1대"
    previous = heatmap.snapshot
    window.query_panel.select("site_key", "s2")
    assert heatmap.snapshot is previous and heatmap.snapshot.query is query
    window.query_panel.apply_draft()
    assert len(calls) == 2 and heatmap.snapshot is not previous
    assert tuple(heatmap.tile_buttons) == (3, 4)


def test_mode_toggle_and_risk_filter_change_display_only_and_can_return(window):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    before = page.snapshot
    QTest.mouseClick(page.view_buttons.button(1), Qt.MouseButton.LeftButton)
    assert page.view_buttons.checkedId() == 1
    assert "PM1 · v99 · N-1" in page.tile_buttons[1].accessibleName()
    page.risk_button.click()
    assert tuple(page.tile_buttons) == (1, 2)
    assert "표시 2대 / 선택 범위 5대" in page.view_count.text()
    assert page.metrics["total"].value.text() == "5대" and page.snapshot is before
    page.risk_button.click()
    assert set(page.tile_buttons) == {1, 2, 3, 4, 5}
    assert window.query_state.applied is before.query


def test_candidate_filter_empty_message_does_not_hide_unknown_as_safe(window):
    window.query_panel.select("site_key", "s2")
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    assert "위험 미확인 1대" in page.metrics["risk"].caption.text()
    page.risk_button.click()
    assert page.tile_buttons == {} and "위험 후보가 없습니다" in page.message.text()
    page.risk_button.click()
    assert tuple(page.tile_buttons) == (3, 4)


def test_keyboard_selection_emits_identity_and_opens_connected_detail(window):
    query = window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    spy = QSignalSpy(page.equipment_selected)
    tile = page.tile_buttons[1]
    tile.setFocus()
    QTest.keyClick(tile, Qt.Key.Key_Space)
    request = spy.at(0)[0]
    assert request.query is query and request.base_date == DAY and request.equipment_id == 1
    assert tile.isChecked() and "설비 상세" in page.selection_notice.text()
    assert window.pages.keys() == {"dashboard", "heatmap", "equipment", "versions"}
    assert window.route == "equipment" and window.pages["equipment"].mode == "detail"
    page.view_buttons.button(1).click()
    assert page.tile_buttons[1].isChecked()
    page.tile_buttons[2].click()
    assert page.selected_id == 2 and not page.tile_buttons[1].isChecked()
    window.query_panel.apply_draft()
    assert page.selected_id is None and not any(t.isChecked() for t in page.tile_buttons.values())


def test_risk_border_does_not_replace_goal_fill_and_original_icon_size(window):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    assert TONES["success"][0] in page.tile_buttons[2].styleSheet()
    assert "#c9485e" in page.tile_buttons[2].styleSheet()
    assert page.tile_buttons[2].code_label.text().startswith("! ")
    assert page.tile_buttons[3].code_label.text().startswith("? ")
    assert page.risk_button.iconSize() == svg_size("7880d.svg")


def test_empty_scope_has_no_stale_tiles_or_fake_success(window):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    window.query_panel.select("fab_key", "f2")
    window.query_panel.apply_draft()
    assert page.tile_buttons == {} and page.metrics["total"].value.text() == "0대"
    assert page.metrics["rate"].value.text() == "계산 불가"
    assert "조건에 맞는 설비 없음" in page.result_status.text()


def test_failure_and_retry_clear_both_pages(window, monkeypatch):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    source = page.source
    original = source.snapshot
    def fail(query):
        raise RuntimeError("테스트 실패")
    monkeypatch.setattr(source, "snapshot", fail)
    window.query_panel.apply_draft()
    assert page.snapshot is None and window.pages["dashboard"].snapshot is None
    assert page.tile_buttons == {} and page.metrics["total"].value.text() == "—"
    assert "실패" in page.result_status.text()
    monkeypatch.setattr(source, "snapshot", original)
    window.query_panel.apply_draft()
    assert len(page.tile_buttons) == 5


def test_stale_result_and_catalog_generation_rejected(window):
    old = window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    previous = page.snapshot
    window.query_panel.apply_draft()
    current = page.snapshot
    assert not page.accept_result(old, previous) and page.snapshot is current
    window.set_query_catalog(page.source.catalog)
    assert page.snapshot is None and page.tile_buttons == {}
    assert not page.accept_result(current.query, current)
    assert "재조회 필요" in page.result_status.text()


def test_mode_result_and_selection_survive_menu_navigation(window):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    page.view_buttons.button(1).click()
    page.tile_buttons[1].click()
    previous = page.snapshot
    window.navigate("dashboard")
    window.navigate("heatmap")
    assert page.snapshot is previous and page.selected_id == 1
    assert page.view_buttons.checkedId() == 1 and page.tile_buttons[1].isChecked()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
@pytest.mark.parametrize("mode", [0, 1])
def test_responsive_tiles_have_no_main_horizontal_overflow(window, size, mode):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    page.view_buttons.button(mode).click()
    window.resize(*size)
    QTest.qWait(100)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    for tile in page.tile_buttons.values():
        point = tile.mapTo(page, QPoint(0, 0))
        assert point.x() >= 0 and point.x() + tile.width() <= page.width()
        assert tile.code_label.width() >= tile.code_label.sizeHint().width()
    assert all(grid.columns >= 1 for grid in page.tile_grids)


def test_default_demo_two_modes_and_catalog_mismatch(app):
    window = create_window()
    window.show()
    window.navigate("heatmap")
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    assert len(page.tile_buttons) == len(page.snapshot.equipment_ids) == 63
    page.view_buttons.button(1).click()
    QTest.qWait(60)
    assert page.tiles[0].multi_sw
    window.set_query_catalog(replace(page.source.catalog, sites=(*page.source.catalog.sites, SiteOption("extra", "새 Site"))))
    window.query_panel.apply_draft()
    assert page.snapshot is None and "실패" in page.result_status.text()
    window.close()
    window.deleteLater()


def test_long_raw_version_is_plain_unchanged_and_wraps_inside_tile(window, source, monkeypatch):
    raw = "v90.1.0.41(H) + Hotfix_Etch_2026 + Safety Fix <검토 중> " * 4
    records = tuple(replace(record, pms=tuple(replace(pm, version_raw=raw) if pm.pm_name == "PM1" else pm
                                             for pm in record.pms)) for record in source.records)
    monkeypatch.setattr(source, "records", records)
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    page.view_buttons.button(1).click()
    window.resize(960, 600)
    QTest.qWait(100)
    tile = page.tile_buttons[1]
    assert raw in tile.accessibleName() and "<검토 중>" in tile.toolTip()
    assert page.tiles[0].families[0].versions[0] == f"PM1 · {raw} · 판단 불가"
    assert window.scroll.horizontalScrollBar().maximum() == 0
    for child in tile.findChildren(QLabel):
        assert child.textFormat() is Qt.TextFormat.PlainText
        point = child.mapTo(tile, QPoint(0, 0))
        assert point.x() + child.width() <= tile.width()
        assert point.y() + child.height() <= tile.height()
        if child.wordWrap():
            assert child.heightForWidth(child.width()) <= child.height()
