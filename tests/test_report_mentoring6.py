"""6차 보고서: 원문 라인·분류·DFB·위험·수정값과 Word/PDF의 동일한 그림을 검증한다."""

from dataclasses import replace
from datetime import date
from hashlib import sha256
from zipfile import ZipFile

import pytest
from docx import Document
from PySide6.QtGui import QImage
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QApplication

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.risk.schemas import RiskStatus
from setron.features.version.groups import Tier
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding
from setron.ui.query_state import AppliedQuery, FabOption, GroupOption, LineOption, QueryCatalog, QuerySelection, SiteOption
from setron.ui.report_data import ReportRequest, generate_report
from setron.ui.report_export import pdf_document, save_generated_report
from setron.ui.report_figures import heatmap_images, line_trend_chart
from setron.ui.report_visual_data import ReportHeatmap, ReportHeatmapCell, ReportLineTrend
from setron.ui.report_widgets import report_pictures


DAYS = (date(2026, 10, 1), date(2026, 11, 1), date(2026, 11, 10))


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    from setron.ui.theme import install_theme
    install_theme(app)
    return app


def report_source():
    specs = ((1, 1, 10, "M-C4", "P1F", "MEMORY", ("v1", "v2", "v2")),
             (2, 1, 10, "M-C4", "P1D", "MEMORY", ("v2", "v1", None)),
             (3, 2, 10, "M-C4 DFB", "P4H", "MEMORY", ("v1", "v1", "v2")),
             (4, 3, 20, "M-O2", "P3-S5", "FOUNDRY", ("v2", "v2", "v2")),
             (5, 4, 30, "M-C3", "P3-S5", "FOUNDRY", ("v1", "v2", "v2")),
             (6, 5, 40, "HPA", "V2", "FOUNDRY", ("v1", "v2", "v2")),
             (7, 3, 20, "M-O2", "V2", "MEMORY", ("v1", "v2", "v2")))
    families = {fid: FamilyReleases(fid, [ReleaseVersion(fid + 1, fid, "v1", 1),
                                         ReleaseVersion(fid + 2, fid, "v2", 2)]) for fid in (10, 20, 30, 40)}
    lines = {(area, line) for _, _, _, _, line, area, _ in specs}
    catalog = QueryCatalog((SiteOption("pt", "평택"),),
        (FabOption("MEMORY", "Memory", None), FabOption("FOUNDRY", "Foundry", None)),
        tuple(LineOption(area + ":" + line, line, "pt", area) for area, line in sorted(lines)),
        tuple(GroupOption(str(gid), model) for gid, model in ((1, "M-C4"), (2, "M-C4 DFB"),
                                                           (3, "M-O2"), (4, "M-C3"), (5, "HPA"))))
    records = tuple(EquipmentRecord(eid, f"EQ-{eid}", day, "평택", line, model,
        (PmVersion(None, gid, fid, versions[i]),), model == "HPA")
        for eid, gid, fid, model, line, area, versions in specs for i, day in enumerate(DAYS))
    locations = {eid: EquipmentLocation("pt", line, area, area + ":" + line)
                 for eid, _, _, _, line, area, _ in specs}
    return DashboardSource(catalog=catalog, bindings=tuple(GroupBinding(str(i), (i,)) for i in range(1, 6)),
        records=records, locations=locations, families=families,
        targets={(gid, fid): fid + 2 for _, gid, fid, _, _, _, _ in specs},
        risks={1: RiskStatus.RISK}, source_label="검증용 관측")


def request(source, **kwargs):
    selection = kwargs.pop("selection", QuerySelection())
    query = AppliedQuery(selection, 1, 0, "보고서 조건", None)
    return ReportRequest("status", query, DAYS[-1], DAYS[0], DAYS[-1], **kwargs)


def test_default_graphs_keep_exact_lines_separate_dfb_and_independent_risk():
    source = report_source()
    result = generate_report(source, request(source))
    trends = [s.visual for s in result.sections if s.chart == "line_trend"]
    c4 = next(t for t in trends if t.group == "C4")
    assert dict(zip(c4.lines, c4.rates)) == {"P1D": (1.0, 0.0, None), "P1F": (0.0, 1.0, 1.0)}
    assert next(t for t in trends if t.group == "DFB").lines == ("P4H",)
    assert any("P3-S5" in t.lines and t.area == "Foundry" for t in trends)
    maps = next(s.visual for s in result.sections if s.chart == "heatmap")
    cell = next(c for panel in maps for line, cells in panel.lines for c in cells if c.code == "EQ-1")
    assert cell.tier is Tier.AT and cell.risk is RiskStatus.RISK
    excluded = next(s.table for s in result.sections if s.title.startswith("HPA 관측"))
    assert excluded.rows[0][2:4] == ("V2", "EQ-6")
    assert all(c.code != "EQ-6" for panel in maps for _, cells in panel.lines for c in cells)
    assert all("SW 계열" not in s.table.columns for s in result.sections if s.table)


@pytest.mark.parametrize("area", ["MEMORY", "FOUNDRY"])
def test_customer_filter_never_includes_the_other_area(area):
    source = report_source()
    result = generate_report(source, request(source, selection=QuerySelection(fab_key=area)))
    assert all(s.visual.area.upper() == area for s in result.sections if s.chart == "line_trend")
    assert all(p.area.upper() == area for s in result.sections if s.chart == "heatmap" for p in s.visual)


def test_corrected_v2_o2_c3_values_are_used_only_by_the_next_generated_report():
    source = report_source()
    first = generate_report(source, request(source))
    source.records = tuple(replace(r, pms=tuple(replace(pm, version_raw="v1") for pm in r.pms))
                           if r.equipment_id in {4, 5, 7} and r.base_date == DAYS[-1] else r for r in source.records)
    second = generate_report(source, request(source))
    for group in ("O2", "C3"):
        before = [s.visual.rates for s in first.sections if s.chart == "line_trend" and s.visual.group == group]
        after = [s.visual.rates for s in second.sections if s.chart == "line_trend" and s.visual.group == group]
        assert before and after and before != after
        assert all(rates[-1] == 0.0 for s in second.sections if s.chart == "line_trend" and s.visual.group == group
                   for rates in s.visual.rates)


def test_unknown_rate_breaks_the_line_instead_of_drawing_zero(qapp):
    trend = ReportLineTrend("Memory", "평택", "C4", DAYS, ("P4D",), ((0.5, None, 0.25),))
    view = line_trend_chart(trend)
    try:
        assert [tuple(p.y() for p in s.points()) for s in view.chart().series()] == [(50.0,), (25.0,)]
    finally:
        view.deleteLater()


def test_heatmap_splits_large_lines_and_keeps_risk_separate_from_version_color(qapp):
    cells = tuple(ReportHeatmapCell(f"TEST-{i}", Tier.AT, RiskStatus.RISK if i == 0 else RiskStatus.UNKNOWN)
                  for i in range(85))
    images = heatmap_images(ReportHeatmap("Memory", "평택", "DFB", (("P4D", cells),)))
    assert len(images) == 2  # 85개를 12행씩 8열로 나누고 여섯 열마다 새 그림
    image = QImage.fromData(images[0])
    assert image.width() == 1200 and image.height() < 1000
    assert image.pixelColor(30, 205).name().upper() == "#DDF4E5"
    assert image.pixelColor(22, 202).red() > image.pixelColor(22, 202).green()


@pytest.mark.parametrize("fmt", ["docx", "pdf"])
def test_word_pdf_include_all_generated_images_and_current_line_values(qapp, tmp_path, fmt):
    source = report_source()
    report = generate_report(source, request(source, format=fmt))
    pictures = report_pictures(report)
    path = tmp_path / report.filename
    assert save_generated_report(report, path, pictures).saved
    if fmt == "docx":
        with ZipFile(path) as archive:
            embedded = {sha256(archive.read(name)).digest() for name in archive.namelist() if name.startswith("word/media/")}
        assert {sha256(png).digest() for _, png in pictures} == embedded
        document = Document(path)
        text = " ".join(c.text for t in document.tables for row in t.rows for c in row.cells)
        assert "P3-S5" in text and "P4H" in text and "EQ-6" in text and "SW 계열" not in text
    else:
        preview = pdf_document(report, pictures)
        assert preview.toHtml().count("report-chart:") == len(pictures)
        assert path.read_bytes().count(b"/Subtype /Image") == len(pictures)
        document = QPdfDocument()
        assert document.load(str(path)) == QPdfDocument.Error.None_
        text = " ".join(document.getAllText(i).text() for i in range(document.pageCount()))
        assert "P3-S5" in text and "위험" in text
        document.close()


def test_report_rejects_excel_format():
    source = report_source()
    with pytest.raises(ValueError):
        request(source, format="xlsx")


def test_database_c4_binding_can_include_dfb_without_combining_report_judgments():
    source = report_source()
    source.catalog = replace(source.catalog, groups=tuple(g for g in source.catalog.groups if g.key != "2"),
        lines=(*source.catalog.lines, LineOption("MEMORY:P4D", "P4D", "pt", "MEMORY")))
    source.bindings["1"] = (1, 2)
    source.bindings.pop("2")
    source.model_names = {1: "M-C4", 2: "M-C4 DFB", 3: "M-O2", 4: "M-C3", 5: "HPA"}
    source.records += tuple(EquipmentRecord(8, "MIXED-PM", day, "평택", "P4D", "혼합",
        (PmVersion("PM1", 1, 10, "v2"), PmVersion("PM2", 2, 10, "v1"))) for day in DAYS)
    source.locations[8] = EquipmentLocation("pt", "P4D", "MEMORY", "MEMORY:P4D")
    report = generate_report(source, request(source, selection=QuerySelection(line_key="MEMORY:P4D")))
    trends = {s.visual.group: s.visual for s in report.sections if s.chart == "line_trend"}
    assert trends["C4"].rates == ((1.0, 1.0, 1.0),)
    assert trends["DFB"].rates == ((0.0, 0.0, 0.0),)
    panels = next(s.visual for s in report.sections if s.chart == "heatmap")
    assert {p.group: p.lines[0][1][0].tier for p in panels} == {"C4": Tier.AT, "DFB": Tier.ONE_BELOW}
    history = generate_report(source, replace(request(source), kind="change"))
    changes = next(s.table for s in history.sections if s.title == "설비별 변경 이력")
    assert any(row[2] == "EQ-3" and row[3] == "일반 SW · DFB" for row in changes.rows)


def test_stored_risk_reason_is_frozen_with_the_report():
    from setron.features.item.service import ItemCatalog
    from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
    from setron.features.risk.service import RiskAssessor
    source = report_source()
    rule = RiskRule(1, RuleType.DIRECT_VERSION, 10, "검증용 결함 버전", group_id=1,
                    release_version_id=12, mapping_status=MappingStatus.VERIFIED)
    source.assessor = RiskAssessor(source.families, ItemCatalog(source.families, ()), (rule,))
    source.risk_evaluator = lambda record: source.assessor.equipment_risk(record).status
    report = generate_report(source, request(source))
    risk = next(s.table for s in report.sections if s.title == "위험 후보 설비와 근거")
    assert any("검증용 결함 버전" in row[-1] for row in risk.rows)
    source.assessor.rules = ()
    assert any("검증용 결함 버전" in row[-1] for row in risk.rows)
