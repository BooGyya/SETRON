"""4번 대시보드의 집계 재사용·범위 전달·표/그래프 일치. 모든 자료는 테스트 전용이다."""

from dataclasses import replace
from datetime import date
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCharts import QBarSeries, QLineSeries, QPieSeries, QScatterSeries
from PySide6.QtCore import QPoint, Qt
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QGraphicsSimpleTextItem, QLabel, QWidget

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui import dashboard
from setron.ui.app import create_window
from setron.ui.dashboard import DashboardPage
from setron.ui.dashboard_charts import DashboardChart, bar_plot
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding, missing_sw_count
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.main_window import MainWindow
from setron.ui.management_integration import report_filter
from setron.ui.query_state import (
    UNASSIGNED, FabOption, GroupOption, LineOption, QueryCatalog, QuerySelection, QueryState, SiteOption, VersionFilter,
)
from setron.ui.report_data import scope_caption
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


def test_multi_location_filters_union_within_fields_and_intersect_across_them(source):
    selection = QuerySelection(site_key=("s1", "s2"), fab_key=("f1", UNASSIGNED), line_key=("l1", UNASSIGNED))
    query = applied(source, selection)
    data = source.snapshot(query)
    assert data.equipment_ids == (1, 3, 4, 5)  # Line L2는 같은 Site여도 범위 밖이다.
    assert data.summary.equipment_count == 3 and data.summary.hpa_excluded == 1
    assert tuple(record.equipment_id for record in data.hpa_lookup_records) == (4,)
    assert {record.equipment_id for record in source.scoped_records(query)} == {1, 3, 4, 5}
    assert report_filter(data, source).site_names == ("테스트 A", "테스트 B")
    assert scope_caption(source.catalog, query.selection) == (
        "테스트 A, 테스트 B · 테스트 Fab A, 미지정 · 테스트 Line A, 미지정 · 설비군 전체"
    )

    filtered = source.snapshot(applied(source, replace(selection, fab_key=("f1", "f2"))))
    assert filtered.equipment_ids == (1,) and not filtered.hpa_lookup_records


def test_multi_group_filters_union_pm_scope_without_duplicate_equipment(source):
    selection = QuerySelection(group_key=("o2", "o3"))
    data = source.snapshot(applied(source, selection))
    assert data.equipment_ids == (1, 2, 3) and not data.hpa_lookup_records
    assert [pm.group_id for pm in data.selected_records[0].pms] == [101, 101, 102]
    assert (data.summary.equipment_count, data.summary.compliant, data.summary.below, data.summary.undetermined) == (3, 2, 1, 1)
    assert [cell.equipment_ids for cell in data.groups] == [(1, 2, 3), (1,), ()]
    assert [group.group_key for group in data.trends] == ["o2", "o3"]
    assert [group.points[-1].denominator_equipment_ids for group in data.trends] == [(1, 2), (1,)]
    assert data.observations[-1].denominator_equipment_ids == (1, 2)
    assert report_filter(data, source).model_ids == (101, 102)
    assert scope_caption(source.catalog, data.query.selection).endswith("O2, O3")

    query = applied(source, replace(selection, versions=VersionFilter(("v1",))))
    filtered = source.snapshot(query)
    # O2의 v1로 포함된 같은 설비의 O3 SW도 유지한다.
    assert filtered.equipment_ids == (1,)
    assert [cell.equipment_ids for cell in filtered.groups] == [(1,), (1,), ()]
    assert {(record.equipment_id, record.base_date) for record in source.scoped_records(query)} == {(1, DAY1), (1, DAY2)}


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
    assert earlier.denominator_equipment_ids == (1, 5)
    assert current.day == DAY2 and current.missing_equipment == 0
    assert current.denominator_equipment_ids == (1, 2, 5)
    o2 = next(group for group in data.trends if group.group_key == "o2")
    assert o2.points[-1].denominator_equipment_ids == (1, 2)
    assert (o2.points[-1].summary.compliant, o2.points[-1].summary.below) == (1, 1)
    assert (current.summary.equipment_count, current.summary.compliant, current.summary.below, current.summary.undetermined) == (
        data.summary.equipment_count, data.summary.compliant, data.summary.below, data.summary.undetermined)
    assert [point.day for point in data.observations] == [DAY1, DAY2]
    o3 = source.snapshot(applied(source, QuerySelection(group_key="o3")))
    assert all(p.summary.equipment_count == 1 for p in o3.observations)


def test_trend_cohort_uses_point_records_even_when_snapshot_carries_forward(source):
    records = tuple(replace(record, base_date=DAY1) if record.equipment_id == 2 else record
                    for record in source.records)
    source = test_source(records=records)
    data = source.snapshot(applied(source, QuerySelection(as_of=DAY2, group_key="o2")))
    assert {record.equipment_id: record.base_date for record in data.selected_records}[2] == DAY1
    earlier, latest = data.trends[0].points
    assert earlier.denominator_equipment_ids == (1, 2)
    assert latest.denominator_equipment_ids == (1,)
    assert latest.missing_equipment == 1

    earlier_data = source.snapshot(applied(source, QuerySelection(as_of=DAY1, group_key="o2")))
    assert earlier_data.trends[0].points == (earlier,)


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
    assert page.metrics["total"].value.text() == "3대"
    window.query_panel.reset_draft()
    assert page.snapshot is before
    window.query_panel.apply_draft()
    assert page.snapshot is not before
    assert page.metrics["total"].value.text() == "4대"


def test_kpi_and_matrix_use_the_same_snapshot_and_do_not_treat_risk_as_below(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    assert page.metrics["total"].value.text() == "4대"
    assert page.snapshot.summary.hpa_excluded == 1
    assert page.metrics["rate"].value.text() == "75.0%"
    assert all(card.caption is None and card.title.alignment() == Qt.AlignmentFlag.AlignCenter
               and card.value.alignment() == Qt.AlignmentFlag.AlignCenter for card in page.metrics.values())
    assert page.metrics["risk"].value.text() == "2대"
    assert page.metrics["upgrade"].value.text() == "1대"
    assert all("border:none" in cell.styleSheet() for cell in page.matrix_buttons.values())
    total = page.matrix_buttons[len(page.snapshot.rows), 4]
    assert total.layout().itemAt(0).widget().text() == "75%"
    assert total.toolTip() == "<qt>전체 / 전체 / 전체<br>N이상 3건<br>N미만 1건<br>정보 부족 1건</qt>"
    assert total.accessibleName() == "전체 전체 전체 설비 4대"
    assert page.matrix_buttons[1, 1].toolTip() == "<qt>테스트 A / P1 / O2<br>N이상 1건<br>N미만 1건<br>정보 부족 0건</qt>"


def test_matrix_distinguishes_site_from_area_and_divides_only_at_site_changes(window, monkeypatch):
    page = window.pages["dashboard"]
    summarized_ids = []
    summarize = dashboard.summarize

    def track_site_summary(records, *args):
        records = tuple(records)
        summarized_ids.append(tuple(sorted(record.equipment_id for record in records)))
        return summarize(records, *args)

    monkeypatch.setattr(dashboard, "summarize", track_site_summary)
    window.query_panel.apply_draft()
    content = page.matrix_scroll.widget()
    grid = content.layout()
    locations = [location.findChildren(QLabel) for location in content.findChildren(QWidget, "dashboardMatrixLocation")]
    assert [[label.text() for label in location] for location in locations] == [
        ["테스트 A", "P1"], ["테스트 A", "구역 미확인"], ["테스트 B", "P1"], ["전체"],
    ]
    (site, area), (same_site, unknown_area), (other_site, _), (total,) = locations
    assert unknown_area.accessibleName() == "Site 테스트 A · 원본 구역 미확인"
    group = grid.itemAtPosition(0, 1).widget()
    assert summarized_ids == [(1, 2, 3, 4), (5,)]
    assert "테스트 A 전체" in site.toolTip() and "설비 4대" in site.toolTip()
    assert same_site.toolTip() == site.toolTip()
    assert "테스트 A · P1" in area.toolTip() and "설비 3대" in area.toolTip()
    assert "테스트 A · 구역 미확인" in unknown_area.toolTip() and "설비 1대" in unknown_area.toolTip()
    assert "테스트 B 전체" in other_site.toolTip() and "설비 1대" in other_site.toolTip()
    assert "설비 5대" in total.toolTip()
    assert "O2" in group.toolTip() and "설비 3대" in group.toolTip()
    assert "총 적용률" in group.toolTip() and "TEST" not in group.toolTip()
    assert all(widget.alignment() == Qt.AlignmentFlag.AlignCenter for widget in (site, area, group))
    dividers = content.findChildren(QWidget, "dashboardMatrixSiteDivider")
    assert len(dividers) == 1 and grid.getItemPosition(grid.indexOf(dividers[0])) == (3, 0, 1, 5)

    selected = QSignalSpy(page.drilldown_requested)
    page.matrix_buttons[3, 4].click()
    assert selected.count() == 1 and selected.at(0)[0].equipment_ids == (5,)


def test_general_cell_and_risk_badge_emit_distinct_exact_cohorts(window):
    page = window.pages["dashboard"]
    window.resize(1600, 1080)
    window.query_panel.apply_draft()
    QTest.qWait(20)
    spy = QSignalSpy(page.drilldown_requested)
    row = len(page.snapshot.rows)
    main = page.matrix_buttons[row, 4]
    count = next(widget for widget in main.findChildren(QLabel) if widget.text() == "3/4건")
    button = page.risk_buttons[row, 4]
    assert "설비 2대" in button.toolTip() and "TEST-" not in button.toolTip()
    page.matrix_scroll.ensureWidgetVisible(main)
    window.scroll.ensureWidgetVisible(main)
    QTest.qWait(20)
    for widget, point in ((count, count.rect().center()), (main, QPoint(3, main.height() - 3)),
                          (button, button.rect().center())):
        QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton, pos=widget.mapTo(window, point))
    assert spy.count() == 3
    for index in (0, 1):
        normal = spy.at(index)[0]
        assert normal.equipment_ids == (1, 2, 3, 4, 5) and not normal.risk_only
    risk = spy.at(2)[0]
    assert risk.equipment_ids == (1, 5) and risk.risk_only
    assert risk.query is normal.query is page.snapshot.query
    assert window.pages.keys() == {"dashboard"}
    removed = ("선택:", "설비 목록으로 이동합니다.", "기준 · 목표 N", "라인 미지정 · 현재 원본 구역 기준",
               "기본 목표 N", "선택한 목표 N", "저장한 N 그룹 반영")
    for tab in range(page.tabs.count()):
        page.tabs.setCurrentIndex(tab)
        visible_labels = [widget.text() for widget in page.findChildren(QLabel) if widget.isVisibleTo(page)]
        assert not any(fragment in caption for fragment in removed for caption in visible_labels)


def test_all_four_views_and_numeric_table_are_driven_by_same_result(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    before = page.snapshot
    page.tabs.setCurrentIndex(1)
    assert isinstance(page.chart.chart().series()[0], QPieSeries)
    assert page.chart.chart().series()[0].holeSize() < page.chart.chart().series()[0].pieSize()
    assert [part.value() for part in page.chart.chart().series()[0].slices()] == [3, 1, 1]
    chips = {widget.text(): widget for widget in page.composition_legend.findChildren(QLabel)
             if widget.text() in ("달성", "미달", "확인 필요")}
    assert {name: widget.font().pixelSize() for name, widget in chips.items()} == {"달성": 16, "미달": 16, "확인 필요": 16}
    assert "달성 · 3건 (60.0%)<br>설비 3대" in chips["달성"].toolTip()
    assert "미달 · 1건 (20.0%)<br>설비 1대" in chips["미달"].toolTip()
    assert "확인 필요 · 1건 (20.0%)<br>설비 1대" in chips["확인 필요"].toolTip()
    assert all("TEST-" not in widget.toolTip() for widget in chips.values())
    page.data_toggle.setChecked(True)
    assert page.data_table.item(0, 2).text() == "60.0%"  # 구성비는 판단 불가 포함 5건이 분모
    page.tabs.setCurrentIndex(2)
    assert isinstance(page.chart.chart().series()[0], QBarSeries)
    assert page.data_table.columnCount() == 6
    assert not any(widget.text() == "설비 대수 · 복수 SW는 설비군별 집계" and widget.isVisibleTo(page)
                   for widget in page.findChildren(QLabel))
    page.tabs.setCurrentIndex(3)
    assert page.data_table.columnCount() == 7
    assert page.snapshot is before


def test_composition_labels_stay_centered_after_applying_another_site(window):
    page = window.pages["dashboard"]
    page.tabs.setCurrentIndex(1)
    for site, expected in (("s1", ("66.7%", "2 / 3건 목표 달성")),
                           ("s2", ("100.0%", "1 / 1건 목표 달성"))):
        window.query_panel.select("site_key", site)
        window.query_panel.apply_draft()
        QTest.qWait(20)

        chips = [page.composition_rows.itemAt(row).layout().itemAt(0).widget() for row in range(3)]
        assert [chip.text() for chip in chips] == ["달성", "미달", "확인 필요"]
        assert all("border:none" in chip.styleSheet() for chip in chips)
        assert not any("대와 중복" in caption.text() for caption in page.content.findChildren(QLabel))
        chart = page.chart.chart()
        labels = {item.text(): item.mapRectToParent(item.boundingRect()) for item in chart.childItems()
                  if isinstance(item, QGraphicsSimpleTextItem)}
        rate, caption = (labels[text] for text in expected)
        center = chart.plotArea().center()
        assert rate.center().x() == pytest.approx(center.x())
        assert caption.center().x() == pytest.approx(center.x())
        assert (rate.top() + caption.bottom()) / 2 == pytest.approx(center.y())
        assert rate.bottom() < caption.top()


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
    assert page.metrics["total"].value.text() == "4대"


def test_series_legend_hides_missing_groups_without_losing_site_preferences(window):
    page, panel = window.pages["dashboard"], window.query_panel
    o2, o3, inline = page.series_widgets
    page.tabs.setCurrentIndex(3)
    panel.select("site_key", "s1")
    panel.apply_draft()
    assert o2.isEnabled() and o2.isChecked()
    assert o3.isEnabled() and o3.isChecked()
    assert inline.isHidden() and not inline.isEnabled() and not inline.isChecked()
    inline.click()
    assert not inline.isChecked() and "inline-o2" in page.visible_groups
    o3.click()
    assert o3.isEnabled() and not o3.isChecked()

    panel.select("site_key", "s2")
    panel.apply_draft()
    assert o2.isHidden() and not o2.isEnabled() and not o2.isChecked()
    assert o3.isHidden() and not o3.isEnabled() and not o3.isChecked()
    assert inline.isVisible() and inline.isEnabled() and inline.isChecked()
    assert page.series_grid.grid.getItemPosition(page.series_grid.grid.indexOf(inline))[:2] == (0, 0)
    assert {series.name() for series in page.chart.chart().series()} == {"Inline O2"}
    trend_style = inline.styleSheet()
    page.tabs.setCurrentIndex(2)
    assert not o2.isEnabled() and not o2.isChecked()
    assert not o3.isEnabled() and not o3.isChecked()
    assert inline.isEnabled() and inline.isChecked() and inline.styleSheet() == trend_style
    assert [bar.label() for bar in page.chart.chart().series()[0].barSets()] == ["Inline O2"]

    panel.select("site_key", "s1")
    panel.apply_draft()
    assert o2.isEnabled() and o2.isChecked()
    assert o3.isEnabled() and not o3.isChecked()
    assert not inline.isEnabled() and not inline.isChecked()
    assert [bar.label() for bar in page.chart.chart().series()[0].barSets()] == ["O2"]
    page.tabs.setCurrentIndex(3)
    assert o2.isEnabled() and o2.isChecked() and not o3.isChecked()


def test_multi_group_selection_shows_both_series_and_only_their_controls(window):
    page = window.pages["dashboard"]
    window.query_state.set_draft(QuerySelection(group_key=("o2", "o3")))
    assert page.apply_query(window.query_state.apply())
    page.tabs.setCurrentIndex(2)
    assert [control.isVisible() for control in page.series_widgets] == [True, True, False]
    assert {bar.label() for bar in page.chart.chart().series()[0].barSets()} == {"O2", "O3"}
    page.tabs.setCurrentIndex(3)
    assert [control.isVisible() for control in page.series_widgets] == [True, True, False]
    assert {series.name() for series in page.chart.chart().series()} == {"O2", "O3"}


@pytest.mark.parametrize("mode", (2, 3))
def test_series_legend_uses_the_whole_painted_button_as_hit_area(window, mode):
    page, panel = window.pages["dashboard"], window.query_panel
    window.resize(1320, 900)
    panel.select("site_key", "s1")
    panel.apply_draft()
    page.tabs.setCurrentIndex(mode)
    QTest.qWait(30)
    active, _, disabled = page.series_widgets
    assert active.isChecked() and disabled.isHidden()

    edge = QPoint(active.width() - 5, active.height() // 2)
    QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton, pos=active.mapTo(window, edge))
    assert not active.isChecked() and "o2" not in page.visible_groups
    assert not disabled.isChecked() and "inline-o2" in page.visible_groups


def test_trend_legend_requires_a_plottable_point_on_any_observation_day(window, source):
    page = window.pages["dashboard"]
    source.records = tuple(
        replace(record, base_date=DAY1) if record.equipment_id == 2 and record.base_date == DAY2 else
        replace(record, pms=(replace(record.pms[0], version_raw=None if record.base_date == DAY2 else "v1"),))
        if record.equipment_id == 5 else
        record
        for record in source.records if not (record.equipment_id == 1 and record.base_date == DAY1)
    )
    page.tabs.setCurrentIndex(3)
    window.query_panel.apply_draft()

    o2, _, inline = page.series_widgets
    o2_trend, _, inline_trend = page.snapshot.trends
    assert all(point.summary.rate is not None and point.missing_equipment for point in o2_trend.points)
    assert o2.isVisible() and o2.isEnabled() and o2.isChecked()
    o2_series, = [series for series in page.chart.chart().series() if series.name() == "O2"]
    assert isinstance(o2_series, QLineSeries)
    elapsed_days = (DAY2 - DAY1).days
    assert [(point.x(), point.y()) for point in o2_series.points()] == [
        *((offset, 100) for offset in range(elapsed_days)), (elapsed_days, 0),
    ]
    assert inline_trend.points[0].summary.rate == 0
    assert inline_trend.points[-1].summary.rate is None
    assert inline.isEnabled()  # 마지막 날짜의 분모가 없어도 이전의 유효한 점은 표시한다.
    assert {series.name() for series in page.chart.chart().series()} == {"O2", "O3", "Inline O2"}
    page.tabs.setCurrentIndex(2)
    assert o2.isEnabled() and o2.isChecked()


def test_search_applies_all_location_date_and_group_filters_to_every_view(app, source):
    window = create_window(source)
    window.show()
    try:
        page, panel = window.pages["dashboard"], window.query_panel
        panel.set_as_of(DAY1)
        for field, value in (("site_key", "s1"), ("fab_key", "f1"), ("line_key", "l1"), ("group_key", "o3")):
            panel.select(field, value)
        panel.search_button.click()

        snapshot = page.snapshot
        assert snapshot is window.pages["heatmap"].snapshot
        assert snapshot.base_date == DAY1 and snapshot.equipment_ids == (1,)
        assert snapshot.rows[0].cells[-1].equipment_ids == (1,) and page.matrix_scroll.isVisible()
        page.tabs.setCurrentIndex(1)
        assert page.chart.chart().series()[0].sum() == 1
        page.tabs.setCurrentIndex(2)
        bars = page.chart.chart().series()[0].barSets()
        assert [bar.label() for bar in bars] == ["O3"] and bars[0].at(0) == 100
        page.tabs.setCurrentIndex(3)
        assert {series.name() for series in page.chart.chart().series()} == {"O3"}
        assert [point.day for point in snapshot.observations] == [DAY1]
        assert [control.isVisible() for control in page.series_widgets] == [False, True, False]
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_bar_click_does_not_open_equipment(window):
    page = window.pages["dashboard"]
    window.query_panel.apply_draft()
    page.tabs.setCurrentIndex(2)
    spy = QSignalSpy(page.drilldown_requested)
    series = page.chart.chart().series()[0]
    rows = [row for row in page.snapshot.rows if row.position is not None and row.cells[-1].summary.equipment_count]
    index = next(i for i, row in enumerate(rows) if row.position == ("s1", "P1"))
    series.clicked.emit(index, series.barSets()[0])
    assert spy.count() == 0
    _, groups = bar_plot(page.snapshot)
    _, points = groups["o2"]
    empty_index = next(i for i, point in enumerate(points) if not point.equipment_ids)
    series.clicked.emit(empty_index, series.barSets()[0])
    assert spy.count() == 0


@pytest.mark.parametrize("gap", ("no_observation", "undetermined"))
def test_unavailable_rate_breaks_trend_without_zero_or_forward_fill(app, source, gap):
    gap_day, later_day = date(2026, 9, 28), date(2026, 10, 7)
    records = {record.equipment_id: record for record in source.records if record.base_date == DAY1}
    # 다른 설비군의 관측일에도 O2가 없거나 모두 판단 불가면 O2 선을 끊는다.
    extra = (replace(records[5], base_date=gap_day), replace(records[1], base_date=later_day))
    if gap == "undetermined":
        extra += (replace(records[3], base_date=gap_day),)
    source = test_source(records=source.records + extra)
    data = source.snapshot(applied(source))
    o2 = next(group for group in data.trends if group.group_key == "o2")
    assert [point.day for point in o2.points] == [DAY1, gap_day, later_day, DAY2]
    assert [point.summary.rate for point in o2.points] == [0, None, 0, 0.5]
    assert o2.points[1].summary.equipment_count == (1 if gap == "undetermined" else 0)
    assert [point.missing_equipment for point in o2.points] == [1, 2 if gap == "undetermined" else 3, 2, 0]

    chart = DashboardChart()
    try:
        chart.trend(data, {"o2"})
        isolated, connected = chart.chart().series()
        assert isinstance(isolated, QScatterSeries)
        assert [(point.x(), point.y()) for point in isolated.points()] == [(0, 0)]
        assert isinstance(connected, QLineSeries)
        assert [(point.x(), point.y()) for point in connected.points()] == [(2, 0), (3, 50)]
    finally:
        chart.deleteLater()
        app.processEvents()


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
    assert page.metrics["total"].value.text() == "4대"


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
    matrix = page.matrix_scroll.widget().layout()
    assert "SW 미등록 1대" in matrix.itemAtPosition(0, 0).widget().toolTip()
    page.deleteLater()


def test_matrix_explains_insufficient_information_on_hover(app, source):
    records = tuple(replace(record, pms=() if record.equipment_id == 1 else
                            (replace(record.pms[0], version_raw="v9"),))
                    for record in source.records if record.equipment_id in (1, 2, 3))
    source = test_source(records=records)
    state = QueryState(source.catalog)
    page = DashboardPage(state, source)
    page.apply_query(state.apply())
    cell = page.matrix_buttons[len(page.snapshot.rows), 4]
    assert cell.layout().itemAt(0).widget().text() == "정보 부족"
    assert cell.toolTip() == "SW 미등록 1대\n릴리즈 노트에 없는 버전 2개"
    page.deleteLater()


def test_hpa_only_scope_is_excluded_from_counts_but_remains_available_for_lookup(app, source):
    """HPA만 있는 범위의 대수·적용률 표시와 상세 조회 연결을 함께 확인한다.

    param
    - app: 화면 이벤트를 처리할 시험용 QApplication.
    - source: HPA 관측을 포함한 합성 대시보드 공급자.
    """
    source = test_source(records=tuple(r for r in source.records if r.is_hpa))
    state = QueryState(source.catalog)
    page = DashboardPage(state, source)
    page.apply_query(state.apply())
    assert page.metrics["total"].value.text() == "0대"
    assert page.snapshot.summary.hpa_excluded == 1
    assert page.metrics["rate"].value.text() == "계산 불가"
    assert page.snapshot.equipment_ids == (4,)
    cell = page.matrix_buttons[len(page.snapshot.rows), 4]
    assert cell.layout().itemAt(0).widget().text() == "분석 제외"
    assert "N 이상 0건 / N 미만 0건" in cell.toolTip() and "분석 0대 · HPA 제외 1대" in cell.toolTip()
    assert cell.accessibleName() == "전체 전체 전체 설비 0대"
    assert cell.isEnabled()
    spy = QSignalSpy(page.drilldown_requested)
    cell.click()
    assert spy.count() == 1 and spy.at(0)[0].equipment_ids == (4,)
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
    assert [page.tabs.tabText(i) for i in range(page.tabs.count())] == [
        "히트맵", "원형 그래프", "막대 그래프", "꺾은선 그래프",
    ]
    assert page.content.body.itemAt(0).widget() is page.view_controls
    assert page.tabs.parentWidget() is page.view_controls
    removed_titles = {"목표 도달 상태 구성비", "원본 구역 · 설비군별 설비 대수", "설비군별 목표 적용 추이"}
    assert not any(label.text() in removed_titles for label in page.content.findChildren(QLabel)
                   if label.isVisibleTo(page.content))
    assert all(page.tabs.rect().contains(page.tabs.tabRect(i)) for i in range(page.tabs.count()))
    assert page.matrix_legend.isVisible() == (mode == 0)
    if mode == 0:
        tabs = page.tabs.rect().translated(page.tabs.mapTo(page.content, QPoint()))
        legend = page.matrix_legend.rect().translated(page.matrix_legend.mapTo(page.content, QPoint()))
        card_body = page.content.contentsRect().marginsRemoved(page.content.body.contentsMargins())
        assert tabs.left() == legend.left() == card_body.left()
        assert tabs.bottom() < legend.top() and legend.right() <= card_body.right()
    for metric in page.metrics.values():
        point = metric.mapTo(page, QPoint(0, 0))
        assert point.x() >= 0 and point.x() + metric.width() <= page.width()
    # VER4 상단 지표는 5장(달성률·분석 대상·목표 미달·버전 확인 필요·위험 후보), 최소 폭 200·간격 12로 줄바꿈한다.
    assert page.kpi_grid.columns == (5 if page.width() >= 1048 else 4 if page.width() >= 836 else 3)


def test_real_entrypoint_registers_dashboard_and_heatmap_and_does_not_auto_apply(app):
    window = create_window()
    assert window.pages.keys() == {"dashboard", "heatmap", "equipment", "versions", "data", "items", "import", "export", "ai", "replay", "reports", "settings"}
    assert window.query_state.applied is None
    assert "합성 예시" in window.notice.text()
    assert window.nav["heatmap"].isEnabled()
    assert window.query_panel.goal_button.isEnabled()
    window.close()
    window.deleteLater()


def test_real_entrypoint_uses_setron_window_title_and_icon(app):
    window = create_window()
    try:
        assert window.windowTitle() == "SETRON"
        assert not window.windowIcon().isNull()
    finally:
        window.close()
        window.deleteLater()
