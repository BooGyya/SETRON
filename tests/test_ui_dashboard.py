"""4번 대시보드의 집계 재사용·범위 전달·표/그래프 일치. 모든 자료는 테스트 전용이다."""

from dataclasses import replace
from datetime import date
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCharts import QBarSeries, QLineSeries, QPieSeries
from PySide6.QtCore import QPoint, Qt
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.app import create_window
from setron.ui.dashboard import DashboardPage
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding, missing_sw_count
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.main_window import MainWindow
from setron.ui.query_state import UNASSIGNED, FabOption, GroupOption, LineOption, QueryCatalog, QuerySelection, QueryState, SiteOption
from setron.ui.theme import ASSETS, install_theme, svg_size


DAY1, DAY2 = date(2026, 9, 21), date(2026, 10, 21)


def test_source(**overrides):
    catalog = QueryCatalog(
        sites=(SiteOption("s1", "테스트 A"), SiteOption("s2", "테스트 B")),
        fabs=(FabOption("f1", "테스트 Fab A", "s1"), FabOption("f2", "테스트 Fab B", "s2")),
        lines=(LineOption("l1", "테스트 Line A", "s1", "f1"), LineOption("l2", "Fab 미등록 Line", "s1", None)),
        groups=(GroupOption("o2", "O2", "OXIDE"), GroupOption("o3", "O3", "OXIDE"), GroupOption("inline-o2", "Inline O2", "KCTC")),
    )
    families = {fid: FamilyReleases(fid, (ReleaseVersion(fid * 10 + 1, fid, "v1", 1),
                                        ReleaseVersion(fid * 10 + 2, fid, "v2", 2))) for fid in (1001, 1002, 1003)}
    pms = {1: (PmVersion("PM1", 101, 1001, "v1"), PmVersion("PM2", 101, 1001, "v2"), PmVersion("PM3", 102, 1002, "v2")),
           2: (PmVersion(None, 101, 1001, "v2"),), 3: (PmVersion(None, 101, 1001, None),),
           4: (), 5: (PmVersion(None, 103, 1003, "v2"),)}
    records = tuple(EquipmentRecord(eid, f"TEST-{eid}", day, "테스트 A" if eid < 5 else "테스트 B", None,
                                    "TEST", values, is_hpa=eid == 4)
                    for day in (DAY1, DAY2) for eid, values in pms.items() if not (day == DAY1 and eid == 2))
    args = dict(catalog=catalog, bindings=(GroupBinding("o2", (101,)), GroupBinding("o3", (102,)), GroupBinding("inline-o2", (103,))),
                records=records, locations={1: EquipmentLocation("s1", "P1", "f1", "l1"),
                                           2: EquipmentLocation("s1", "P1", None, "l2"),
                                           3: EquipmentLocation("s1"), 4: EquipmentLocation("s1", "P1"),
                                           5: EquipmentLocation("s2", "P1")},
                families=families, targets={(101, 1001): 10012, (102, 1002): 10022, (103, 1003): 10032},
                risks={1: RiskStatus.RISK, 2: RiskStatus.NO_RISK, 5: RiskStatus.RISK}, source_label="테스트 전용")
    args.update(overrides)
    return DashboardSource(**args)


test_source.__test__ = False


@pytest.fixture
def source():
    return test_source()


def applied(source, selection=QuerySelection()):
    state = QueryState(source.catalog)
    state.set_draft(selection)
    return state.apply()


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


@pytest.fixture
def window(app, source):
    window = MainWindow(query_catalog=source.catalog)
    page = DashboardPage(window.query_state, source)
    window.register_page("dashboard", page)
    window.query_applied.connect(page.apply_query)
    window.query_panel.catalog_changed.connect(page.invalidate)
    window.show()
    QTest.qWait(40)
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()


def test_source_reuses_backend_mixed_equipment_hpa_and_unknown_rules(source):
    data = source.snapshot(applied(source))
    assert len(data.equipment_ids) == 5  # PM이나 SW 개수가 아닌 물리 대수
    assert (data.summary.equipment_count, data.summary.hpa_excluded, data.summary.upgrade_needed) == (4, 1, 1)
    assert (data.summary.compliant, data.summary.below, data.summary.undetermined) == (3, 1, 1)
    assert data.summary.rate == 0.75
    assert data.risky_ids == (1, 5)
    assert data.risk_unknown == 1  # 누락된 위험 판정을 안전으로 추정하지 않음
    groups = {g.label: len(g.equipment_ids) for g in data.groups}
    assert groups == {"O2": 3, "O3": 1, "Inline O2": 1}
    assert sum(groups.values()) != data.summary.equipment_count


@pytest.mark.parametrize("selection, ids", [
    (QuerySelection(site_key="s2"), (5,)), (QuerySelection(fab_key="f1"), (1,)),
    (QuerySelection(line_key="l2"), (2,)), (QuerySelection(fab_key=UNASSIGNED, line_key="l2"), (2,)),
    (QuerySelection(line_key=UNASSIGNED), (3, 4, 5)),
    (QuerySelection(group_key="o3"), (1,)), (QuerySelection(group_key="inline-o2"), (5,)),
    (QuerySelection(fab_key="f2"), ()),
])
def test_filters_use_explicit_mapping_and_distinct_group_keys(source, selection, ids):
    data = source.snapshot(applied(source, selection))
    assert data.equipment_ids == ids
    if selection.group_key == "o3":
        assert data.summary.rate == 1 and data.summary.upgrade_needed == 0
        assert data.risky_ids == (1,)  # 목표 달성과 위험은 독립적


def test_matrix_groups_by_site_and_raw_area_not_guessed_fab_or_line(source):
    data = source.snapshot(applied(source))
    positions = [row.position for row in data.rows]
    assert positions == [("s1", "P1"), ("s1", None), ("s2", "P1"), None]
    assert data.rows[-1].cells[-1].equipment_ids == data.equipment_ids
    assert data.rows[-1].cells[-1].risky_ids == data.risky_ids
    assert all(cell.equipment_ids == () for row in source.snapshot(applied(source, QuerySelection(group_key="o3"))).rows
               for cell in row.cells if cell.label in ("O2", "KCTC"))


def test_fixed_cohort_history_keeps_gaps_and_does_not_copy_old_values(source):
    data = source.snapshot(applied(source))
    earlier, current = data.observations
    assert earlier.day == DAY1 and earlier.missing_equipment == 1
    assert earlier.summary.equipment_count == 3
    assert current.day == DAY2 and current.missing_equipment == 0
    assert (current.summary.equipment_count, current.summary.compliant, current.summary.below, current.summary.undetermined) == (
        data.summary.equipment_count, data.summary.compliant, data.summary.below, data.summary.undetermined)
    assert [point.day for point in data.observations] == [DAY1, DAY2]
    o3 = source.snapshot(applied(source, QuerySelection(group_key="o3")))
    assert all(p.summary.equipment_count == 1 for p in o3.observations)


@pytest.mark.parametrize("change", ["duplicate_record", "missing_location", "model_collision", "unknown_model", "wrong_parent"])
def test_source_rejects_ambiguous_input(source, change):
    kwargs = {}
    if change == "duplicate_record":
        kwargs["records"] = source.records + (source.records[0],)
    elif change == "missing_location":
        kwargs["locations"] = {}
    elif change == "model_collision":
        kwargs["bindings"] = (GroupBinding("o2", (101,)), GroupBinding("o3", (101,)), GroupBinding("inline-o2", (103,)))
    elif change == "unknown_model":
        kwargs["records"] = (replace(source.records[0], pms=(PmVersion(None, 999, 1001, "v1"),)),)
    else:
        kwargs["locations"] = {**source.locations, 1: EquipmentLocation("s2", "P1", "f1", "l1")}
    with pytest.raises(ValueError):
        test_source(**kwargs)


def test_demo_is_labeled_and_has_no_inferred_fab_line():
    source = demo_dashboard_source()
    assert "합성 예시" in source.source_label and "위험" in source.source_label
    assert all(loc.fab_key is None and loc.line_key is None for loc in source.locations.values())
    assert {group.label for group in source.catalog.groups if group.category == "KCTC"} == {
        "Inline Ox", "Inline O2", "Cluster Ox", "Cluster O2"}
    assert len(source.dates) == 6


def test_dashboard_waits_for_apply_and_draft_changes_do_not_refresh(window):
    page = window.pages["dashboard"]
    assert page.snapshot is None
    assert all(metric.value.text() == "—" for metric in page.metrics.values())
    window.query_panel.select("site_key", "s1")
    assert page.snapshot is None
    window.query_panel.apply_draft()
    before = page.snapshot
    assert page.metrics["total"].value.text() == "4대"
    window.query_panel.reset_draft()
    assert page.snapshot is before
    window.query_panel.apply_draft()
    assert page.snapshot is not before
    assert page.metrics["total"].value.text() == "5대"


def test_kpi_and_matrix_use_the_same_snapshot_and_do_not_treat_risk_as_below(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    assert page.metrics["rate"].value.text() == "75.0%"
    assert "3/4건" in page.metrics["rate"].caption.text()
    assert "판단 불가 1건" in page.metrics["rate"].caption.text()
    assert page.metrics["risk"].value.text() == "2대"
    assert page.metrics["upgrade"].value.text() == "1대"
    assert "3/4건" in page.matrix_buttons[len(page.snapshot.rows), 4].text()


def test_general_cell_and_risk_badge_emit_distinct_exact_cohorts(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    spy = QSignalSpy(page.drilldown_requested)
    row = len(page.snapshot.rows)
    QTest.mouseClick(page.matrix_buttons[row, 4], Qt.MouseButton.LeftButton)
    normal = spy.at(0)[0]
    QTest.mouseClick(page.risk_buttons[row, 4], Qt.MouseButton.LeftButton)
    risk = spy.at(1)[0]
    assert normal.equipment_ids == (1, 2, 3, 4, 5) and not normal.risk_only
    assert risk.equipment_ids == (1, 5) and risk.risk_only
    assert risk.query is normal.query is page.snapshot.query
    assert "6번 작업" in page.selection_notice.text()
    assert window.pages.keys() == {"dashboard"}


def test_all_four_views_and_numeric_table_are_driven_by_same_result(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    before = page.snapshot
    page.tabs.setCurrentIndex(1)
    assert isinstance(page.chart.chart().series()[0], QPieSeries)
    assert page.chart.chart().series()[0].holeSize() < page.chart.chart().series()[0].pieSize()
    assert [part.value() for part in page.chart.chart().series()[0].slices()] == [3, 1, 1]
    page.data_toggle.setChecked(True)
    assert page.data_table.item(0, 2).text() == "60.0%"  # 구성비는 판단 불가 포함 5건이 분모
    page.tabs.setCurrentIndex(2)
    assert isinstance(page.chart.chart().series()[0], QBarSeries)
    assert page.data_table.columnCount() == 3
    page.tabs.setCurrentIndex(3)
    assert page.data_table.columnCount() == 6
    assert page.snapshot is before


def test_chart_legend_only_changes_visibility_not_applied_scope_or_kpis(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    before = page.snapshot
    page.tabs.setCurrentIndex(2)
    page.series_widgets[0].setChecked(False)
    assert len(page.chart.chart().series()[0].barSets()) == 2
    page.tabs.setCurrentIndex(3)
    for check in page.series_widgets:
        check.setChecked(False)
    assert page.chart.chart().series() == []
    assert page.snapshot is before
    assert page.metrics["total"].value.text() == "5대"


def test_bar_click_does_not_inherit_global_or_risk_only_scope(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    page.tabs.setCurrentIndex(2)
    spy = QSignalSpy(page.drilldown_requested)
    series = page.chart.chart().series()[0]
    rows = [row for row in page.snapshot.rows if row.position is not None and row.cells[-1].summary.equipment_count]
    index = next(i for i, row in enumerate(rows) if row.position == ("s1", "P1"))
    series.clicked.emit(index, series.barSets()[0])
    request = spy.at(0)[0]
    assert request.equipment_ids == (1, 2) and not request.risk_only


def test_missing_observation_breaks_trend_line_instead_of_zero_or_forward_fill(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    page.tabs.setCurrentIndex(3)
    assert not any(isinstance(series, QLineSeries) and series.name() == "O2"
                   for series in page.chart.chart().series())
    assert "미관측 1대" in page.observations.text()


def test_empty_scope_is_not_an_error_or_fake_success(window):
    page = window.pages["dashboard"]
    window.query_panel.select("fab_key", "f2")
    window.query_panel.apply_draft()
    assert page.snapshot.equipment_ids == ()
    assert page.metrics["total"].value.text() == "0대"
    assert page.metrics["rate"].value.text() == "계산 불가"
    assert not page.matrix_scroll.isVisible()
    assert not page.chart.isVisible()
    assert "조건에 맞는 설비 없음" in page.result_status.text()


def test_old_results_and_catalog_changes_cannot_overwrite_current_data(window, source):
    page = window.pages["dashboard"]
    old = window.query_panel.apply_draft()
    old_result = page.snapshot
    window.query_panel.select("site_key", "s2")
    new = window.query_panel.apply_draft()
    assert not page.accept_result(old, old_result)
    assert page.snapshot.query is new
    window.set_query_catalog(source.catalog)
    assert page.snapshot is None and page.metrics["total"].value.text() == "—"
    assert not page.accept_result(new, source.snapshot(new))


def test_provider_failure_shows_error_and_retry_does_not_keep_old_result(window, monkeypatch):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    original = page.source.snapshot
    def fail(query):
        raise RuntimeError("테스트 실패")
    monkeypatch.setattr(page.source, "snapshot", fail)
    window.query_panel.apply_draft()
    assert page.snapshot is None
    assert "실패" in page.result_status.text()
    assert page.metrics["total"].value.text() == "—"
    monkeypatch.setattr(page.source, "snapshot", original)
    window.query_panel.apply_draft()
    assert page.metrics["total"].value.text() == "5대"


def test_unregistered_sw_is_separate_and_prevents_complete_claim(app, source):
    records = tuple(replace(record, pms=()) if record.equipment_id == 1 else record
                    for record in source.records if record.equipment_id in (1, 2))
    source = test_source(records=records)
    data = source.snapshot(applied(source))
    assert missing_sw_count(data.summary) == 1
    state = QueryState(source.catalog)
    page = DashboardPage(state, source)
    query = state.apply()
    page.apply_query(query)
    assert "SW 미등록 1대" in page.metrics["rate"].caption.text()
    assert "확인 필요" in page.metrics["rate"].caption.text()
    page.deleteLater()


def test_hpa_only_scope_keeps_physical_count_but_has_no_analysis_chart(app, source):
    source = test_source(records=tuple(r for r in source.records if r.is_hpa))
    state = QueryState(source.catalog)
    page = DashboardPage(state, source)
    page.apply_query(state.apply())
    assert page.metrics["total"].value.text() == "1대"
    assert page.metrics["rate"].value.text() == "계산 불가"
    page.tabs.setCurrentIndex(2)
    assert page.chart.isHidden() and "분석 대상 설비가 없습니다" in page.message.text()
    page.deleteLater()


def test_replaced_catalog_requires_matching_source_mapping(window, source):
    window.set_query_catalog(replace(source.catalog, sites=(SiteOption("s1", "변경된 이름"), source.catalog.sites[1])))
    window.query_panel.apply_draft()
    page = window.pages["dashboard"]
    assert page.snapshot is None and "실패" in page.result_status.text()


def test_window_navigation_keeps_view_and_query_result(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    page.tabs.setCurrentIndex(1)
    before = page.snapshot
    window.register_page("import", QWidget())
    window.navigate("import")
    window.navigate("dashboard")
    assert page.tabs.currentIndex() == 1 and page.snapshot is before


def test_dashboard_static_icons_preserve_original_geometry(window):
    for card in window.pages["dashboard"].metrics.values():
        asset = card.picture.property("assetFile")
        assert (ASSETS / asset).stat().st_size > 0
        assert QSvgRenderer(str(ASSETS / asset)).isValid()
        assert card.picture.pixmap().deviceIndependentSize().toSize() == svg_size(asset)
        assert card.picture.width() in (24, 28)
    for name in ("7880d.svg", "5ced8.svg"):
        assert QSvgRenderer(str(ASSETS / name)).isValid()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_responsive_dashboard_has_only_internal_table_scroll(window, size, mode):
    window.query_panel.apply_draft()
    page = window.pages["dashboard"]
    page.tabs.setCurrentIndex(mode)
    window.resize(*size)
    QTest.qWait(70)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert window.scroll.widget().width() == window.scroll.viewport().width()
    for metric in page.metrics.values():
        point = metric.mapTo(page, QPoint(0, 0))
        assert point.x() >= 0 and point.x() + metric.width() <= page.width()
    assert page.kpi_grid.columns == (4 if page.width() >= 1088 else 2)


def test_real_entrypoint_registers_dashboard_and_heatmap_and_does_not_auto_apply(app):
    window = create_window()
    assert window.pages.keys() == {"dashboard", "heatmap", "equipment", "versions"}
    assert window.query_state.applied is None
    assert "합성 예시" in window.notice.text()
    assert window.nav["heatmap"].isEnabled()
    assert window.query_panel.goal_button.isEnabled()
    window.close()
    window.deleteLater()
