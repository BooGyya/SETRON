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
from setron.features.version.schemas import CompareStatus, ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.app import create_window
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding
from setron.ui.heatmap import RISK_FILL, RISK_LINE, TIER_FILL, TileButton
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
    # 범위 검증은 HPA까지 모두 보이는 상태에서 한다. 기본값(HPA 숨김)은 별도 테스트로 확인한다.
    result.pages["heatmap"].hpa_button.setChecked(True)
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
    assert window.pages.keys() == {"dashboard", "heatmap", "equipment", "versions", "items", "import", "ai", "management", "replay"}
    assert window.route == "heatmap" and window.pages["equipment"].mode == "detail"
    assert window.equipment_drawer.isVisible()
    page.view_buttons.button(1).click()
    assert page.tile_buttons[1].isChecked()
    page.tile_buttons[2].click()
    assert page.selected_id == 2 and not page.tile_buttons[1].isChecked()
    window.query_panel.apply_draft()
    assert page.selected_id is None and not any(t.isChecked() for t in page.tile_buttons.values())


def test_figma_tiles_show_risk_first_and_markers_and_original_icon_size(window):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    # 위험 후보는 빨강(N-2 이하)과 헷갈리지 않게 채움색 대신 굵은 빨간 테두리와 '위험' 표시로 보인다 (11/07, 5단계 색).
    style = page.tile_buttons[2].styleSheet()
    assert f"2px solid {RISK_LINE}" in style and RISK_FILL not in style
    assert TIER_FILL[page.tile_buttons[2].data.tier] in style
    assert page.tile_buttons[2].marker == "위험" and page.tile_buttons[3].marker == ""
    assert page.tile_buttons[2].data.goal is GoalState.MET
    assert page.risk_button.iconSize() == svg_size("7880d.svg")


@pytest.mark.parametrize("view", [0, 1])
@pytest.mark.parametrize("layout", [0, 1])
def test_no_question_mark_decoration_in_any_tile_view(window, view, layout):
    window.query_panel.apply_draft()
    page = window.pages["heatmap"]
    page.view_buttons.button(view).click()
    page.layout_buttons.button(layout).click()
    tiles = page.findChildren(TileButton)
    assert tiles and all(tile.marker != "?" for tile in tiles)
    assert all(child.text() != "?" for tile in tiles for child in tile.findChildren(QLabel))
    assert not any(child.text().startswith("? ") for child in page.findChildren(QLabel))
    unknown = next(tile for tile in tiles if tile.data.equipment_id == 3)
    assert "위험 미확인" in unknown.toolTip() and "정보 부족" in unknown.toolTip()


def test_hpa_is_hidden_until_special_equipment_toggle(window):
    page = window.pages["heatmap"]
    page.hpa_button.setChecked(False)
    window.query_panel.apply_draft()
    hpa = {t.equipment_id for t in page.tiles if t.goal is GoalState.EXCLUDED}
    assert hpa and not hpa & set(page.tile_buttons)
    page.hpa_button.setChecked(True)
    assert hpa <= set(page.tile_buttons)


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
    window.pages["heatmap"].hpa_button.setChecked(True)
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


@pytest.mark.parametrize("status,steps,tone", [
    (CompareStatus.AT_OR_ABOVE, None, "success"),
    (CompareStatus.BELOW, 1, "warning"),
    (CompareStatus.BELOW, None, "warning"),
    (CompareStatus.BELOW, 2, "danger"),
    (CompareStatus.BELOW, 5, "danger"),
    (CompareStatus.UNDETERMINED, None, "neutral"),
], ids=["N 이상 초록", "N-1 노랑", "단계 모름은 많이 낮음으로 단정하지 않음", "N-2 빨강", "N-5 빨강", "판단 불가 회색"])
def test_version_gap_colors_follow_requirement(status, steps, tone):
    """요구사항 3.2: 기준 버전 적용은 초록, 많이 낮으면 빨강. 임계값은 확정 전 임시 기준(N-2 이하)."""
    from setron.ui.heatmap_data import FAR_BELOW_STEPS, compare_tone

    assert FAR_BELOW_STEPS == 2
    assert compare_tone(status, steps) == tone



def test_tiles_use_five_tiers_from_shared_tier_of():
    """11/05 팀 결정 5단계: 진초록 N+1 이상 · 연초록 N · 노랑 N-1 · 빨강 N-2 이하 · 회색 비교 불가 (tier_of와 같은 기준)."""
    from setron.features.version.groups import Tier
    from setron.ui.heatmap_data import EquipmentTile, GoalState as Goal, TIER_TONES, worst_tier
    from setron.features.risk.schemas import RiskStatus

    assert worst_tier([Tier.ABOVE, Tier.ONE_BELOW, Tier.UNKNOWN]) is Tier.ONE_BELOW
    assert worst_tier([Tier.UNKNOWN]) is Tier.UNKNOWN and worst_tier([]) is Tier.UNKNOWN
    assert TIER_TONES == {Tier.ABOVE: "strong", Tier.AT: "success", Tier.ONE_BELOW: "warning", Tier.TWO_BELOW: "danger",
                          Tier.UNKNOWN: "neutral"}
    tile = lambda goal, tier: EquipmentTile(1, "E", "s", "화성", "loc", goal, RiskStatus.NO_RISK, (), False, False,  # noqa: E731
                                            tier=tier)
    assert tile(Goal.MET, Tier.ABOVE).version_tone == "strong" and tile(Goal.MET, Tier.AT).version_tone == "success"
    assert tile(Goal.BELOW, Tier.TWO_BELOW).version_tone == "danger" and tile(Goal.UNKNOWN, Tier.UNKNOWN).version_tone == "neutral"
    assert tile(Goal.EXCLUDED, Tier.ABOVE).version_tone == "neutral"
    assert len(set(TIER_FILL.values())) == 5
