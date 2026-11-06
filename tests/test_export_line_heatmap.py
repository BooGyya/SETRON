"""라인별 히트맵 시트를 검증한다 (BE-021, 5차 멘토 요청: 설비군 → 라인 → 설비 양식)."""

from datetime import date

from openpyxl import Workbook, load_workbook

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aggregation.service import summarize
from setron.features.export.line_heatmap import (HEATMAP_COLORS, LINE_HEATMAP_SHEET, NO_LINE, NO_SITE, ROWS_PER_COLUMN,
                                                 line_heatmap_groups, write_line_heatmap)
from setron.features.export.schemas import StatusReport
from setron.features.export.service import save_status_report
from setron.features.export.xlsx_cells import ReportTextWriter
from setron.features.version.groups import Tier
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

M_O2, M_O3 = 1, 2
F_O2, F_O3 = 10, 20
DAY = date(2026, 10, 21)
FAMILIES = {
    F_O2: FamilyReleases(F_O2, [ReleaseVersion(101, F_O2, "v2.1", 1), ReleaseVersion(102, F_O2, "v2.2", 2)]),
    F_O3: FamilyReleases(F_O3, [ReleaseVersion(201, F_O3, "v3.1", 1)]),
}
TARGETS = {(M_O2, F_O2): 102, (M_O3, F_O3): 201}
NAMES = {F_O2: "O2", F_O3: "O3"}


def o2(version):
    return PmVersion(None, M_O2, F_O2, version)


def o3(version):
    return PmVersion(None, M_O3, F_O3, version)


def record(eid, code, site, line, *pms, hpa=False):
    return EquipmentRecord(eid, code, DAY, site, line, "M-O2", tuple(pms), is_hpa=hpa)


def report(*records):
    return StatusReport(summary=summarize(records, FAMILIES, TARGETS), family_names=NAMES)


def codes(line):
    return [(c.equipment_code, c.tier) for c in line.cells]


def test_groups_follow_site_family_line_order_and_skip_hpa():
    """사이트·계열은 이름순, 라인은 숫자 자연 정렬, 호기는 코드순. HPA는 넣지 않는다."""
    value = report(
        record(1, "ESO-B", "화성", "12L", o2("v2.2")),
        record(2, "ESO-A", "화성", "12L", o2("v2.1")),
        record(3, "ESO-C", "화성", "5L", o2(None)),
        record(4, "ESO-D", "평택", "P1", o2("v2.2")),
        record(5, "ESH-1", "화성", "5L", o2("v2.2"), hpa=True),
    )

    groups = line_heatmap_groups(value)

    assert [g.site for g in groups] == ["평택", "화성"]
    hwaseong = groups[1].families[0]
    assert hwaseong.family == "O2"
    assert [line.line for line in hwaseong.lines] == ["5L", "12L"]
    assert codes(hwaseong.lines[0]) == [("ESO-C", Tier.UNKNOWN)]
    assert codes(hwaseong.lines[1]) == [("ESO-A", Tier.ONE_BELOW), ("ESO-B", Tier.AT)]


def test_mixed_equipment_appears_once_in_each_family():
    """O2·O3 혼합 설비는 설비·계열 건마다 칸이 하나라 두 계열에 각각 나온다."""
    groups = line_heatmap_groups(report(record(1, "ESO-MIX", "화성", "16L", o2("v2.1"), o3("v3.1"))))

    families = {f.family: codes(f.lines[0]) for f in groups[0].families}
    assert families == {"O2": [("ESO-MIX", Tier.ONE_BELOW)], "O3": [("ESO-MIX", Tier.AT)]}


def test_missing_site_and_line_are_labeled_and_sorted_last():
    groups = line_heatmap_groups(report(
        record(1, "ESO-1", None, None, o2("v2.2")),
        record(2, "ESO-2", "화성", "  ", o2("v2.2")),
        record(3, "ESO-3", "화성", "1L", o2("v2.2")),
    ))

    assert [g.site for g in groups] == ["화성", NO_SITE]
    assert [line.line for line in groups[0].families[0].lines] == ["1L", NO_LINE]


def test_sheet_lays_out_headers_wraps_columns_and_colors_cells():
    """계열 머리글은 그 계열의 모든 라인 열에, 라인 머리글은 그 라인 열에 병합한다.
    한 라인이 ROWS_PER_COLUMN칸을 넘으면 옆 열로 이어 쓴다."""
    many = [record(i, f"ESO-{i:02d}", "화성", "16L", o2("v2.2")) for i in range(1, ROWS_PER_COLUMN + 2)]
    value = report(*many, record(50, "ESO-50", "화성", "17L", o2("v2.1")),
                   record(60, "ESO-60", "화성", "17L", o3("v3.1")))
    book = Workbook()
    text = ReportTextWriter(book)

    write_line_heatmap(book.active, line_heatmap_groups(value), text)
    sheet = book.active

    assert sheet.title == LINE_HEATMAP_SHEET
    assert [sheet.cell(1, c).value for c in range(1, 7)] == ["범례", "N+1 이상", "N", "N-1", "N-2 이하", "비교 불가"]
    assert sheet["A4"].value == "화성"
    merged = {str(r) for r in sheet.merged_cells.ranges}
    # O2: 16L 두 열(B..) + 17L 한 열 → A5:C5, 16L 머리글 A6:B6. 빈 열 하나 뒤 O3는 E열.
    assert "A5:C5" in merged and "A6:B6" in merged
    assert (sheet["A5"].value, sheet["A6"].value, sheet["C6"].value) == ("O2", "16L", "17L")
    assert sheet["E5"].value == "O3" and sheet["E6"].value == "17L"
    first, wrapped = sheet.cell(7, 1), sheet.cell(7, 2)
    assert first.value == "ESO-01" and sheet.cell(7 + ROWS_PER_COLUMN - 1, 1).value == f"ESO-{ROWS_PER_COLUMN:02d}"
    assert wrapped.value == f"ESO-{ROWS_PER_COLUMN + 1:02d}"
    assert first.fill.fgColor.rgb.endswith(HEATMAP_COLORS[Tier.AT])
    assert sheet["C7"].value == "ESO-50" and sheet["C7"].fill.fgColor.rgb.endswith(HEATMAP_COLORS[Tier.ONE_BELOW])
    assert sheet["E7"].value == "ESO-60"


def test_sites_are_stacked_below_each_other():
    value = report(record(1, "ESO-1", "화성", "16L", o2("v2.2")), record(2, "ESO-2", "평택", "P1", o2("v2.2")))
    book = Workbook()

    write_line_heatmap(book.active, line_heatmap_groups(value), ReportTextWriter(book))

    next_site = 4 + 3 + ROWS_PER_COLUMN + 2
    assert (book.active["A4"].value, book.active[f"A{next_site}"].value) == ("평택", "화성")


def test_empty_report_keeps_legend_and_notice():
    book = Workbook()

    write_line_heatmap(book.active, line_heatmap_groups(report()), ReportTextWriter(book))

    assert book.active["A1"].value == "범례" and book.active["A4"].value == "표시할 설비가 없습니다."


def test_saved_report_contains_line_heatmap_sheet(tmp_path):
    path = tmp_path / "라인별.xlsx"
    assert save_status_report(report(record(1, "ESO-1", "화성", "16L", o2("v2.2"))), path).saved

    saved = load_workbook(path)
    try:
        assert saved.sheetnames[-1] == LINE_HEATMAP_SHEET
        assert saved[LINE_HEATMAP_SHEET]["A7"].value == "ESO-1"
    finally:
        saved.close()
