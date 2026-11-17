"""고정된 보고서 히트맵을 항목별 편집 가능한 Excel 시트로 저장한다."""

from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import re

from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter
import pytest

from setron.features.export.schemas import ExportStatus
from setron.features.risk.schemas import RiskStatus
from setron.features.version.groups import TIER_LABELS, Tier
from setron.ui import report_heatmap_export as exporter
from setron.ui.heatmap_grouping import GROUPING_MODES
from setron.ui.query_state import QuerySelection
from setron.ui.report_data import GeneratedReport, ReportSection, generate_report
from setron.ui.report_visual_data import HEATMAP_TIER_COLORS, ReportHeatmap, ReportHeatmapCell
from test_report_mentoring6 import report_source, request


def heatmap_section(mode, *panels):
    return ReportSection(1, f"{dict(GROUPING_MODES)[mode]}별 히트맵", chart="heatmap",
                         visual=tuple(panels), content_key="heatmap", heatmap_mode=mode)


def report_with(*sections, modes=("group",)):
    source = report_source()
    selected = request(source, contents=("heatmap",), types=(), heatmap_modes=modes)
    return GeneratedReport(selected, datetime(2026, 11, 16, 12), "히트맵 검사", tuple(sections),
                           "검증 범위", "고정된 합성 자료")


def panel(group, code, *, site="평택", area="Memory", line="P1", tier=Tier.AT,
          risk=RiskStatus.NO_RISK, risk_detail=""):
    return ReportHeatmap(area, site, group,
                         ((line, (ReportHeatmapCell(code, tier, risk, risk_detail),)),))


def values(sheet):
    return tuple(cell.value for row in sheet.iter_rows() for cell in row if cell.value is not None)


def equipment_codes(book):
    return {value for sheet in book for value in values(sheet)
            if isinstance(value, str) and value.startswith("EQ-")}


def code_cell(book, code):
    found = [cell for sheet in book for row in sheet.iter_rows() for cell in row if cell.value == code]
    assert len(found) == 1
    return found[0]


def tier_rules(sheet):
    found = {}
    for tier, caption in TIER_LABELS.items():
        legend = next(cell for row in sheet.iter_rows() for cell in row if cell.value == caption)
        matching = [(formatting, rule) for formatting in sheet.conditional_formatting
                    if legend.coordinate in formatting.sqref
                    for rule in sheet.conditional_formatting[formatting]]
        assert len(matching) == 1
        found[tier] = matching[0]
    return found


def conditional_coordinates(formatting):
    return {f"{get_column_letter(column)}{row}"
            for area in formatting.sqref.ranges
            for row in range(area.min_row, area.max_row + 1)
            for column in range(area.min_col, area.max_col + 1)}


def all_tiers_report():
    entries = tuple(ReportHeatmapCell(f"EQ-{tier.value}", tier, RiskStatus.NO_RISK) for tier in Tier)
    risk = ReportHeatmapCell("EQ-AT-RISK", Tier.AT, RiskStatus.RISK, "버전 결함 규칙의 근거")
    another = ReportHeatmapCell("EQ-AT-OTHER", Tier.AT, RiskStatus.NO_RISK)
    return report_with(heatmap_section("group",
        ReportHeatmap("Memory", "평택", "C4", (("13L", (*entries, risk)), ("P1F", (another,))))))


@pytest.mark.parametrize("mode,expected", [
    ("group", {"C4", "C3", "DFB", "O2"}),
    ("site", {"평택"}),
    ("fab", {"Memory", "Foundry"}),
    ("line", {"P1", "P4", "P3-S5", "V2"}),
    ("subline", {"P1F", "P1D", "P4H"}),
])
def test_each_selected_heatmap_type_uses_one_sheet_per_item_and_excludes_hpa(mode, expected):
    source = report_source()
    report = generate_report(source, request(source, contents=("heatmap",), types=(), heatmap_modes=(mode,)))
    assert exporter.has_report_heatmaps(report)
    with closing(exporter.build_heatmap_workbook(report)) as book:
        assert set(book.sheetnames) == expected
        expected_codes = {"EQ-1", "EQ-2", "EQ-3"} if mode == "subline" else {
            "EQ-1", "EQ-2", "EQ-3", "EQ-4", "EQ-5", "EQ-7"}
        assert equipment_codes(book) == expected_codes
        assert "EQ-6" not in equipment_codes(book)
        assert all(not sheet.protection.sheet and not sheet._images for sheet in book)


def test_multiple_types_prefix_sheet_names_and_keep_all_selected_items():
    source = report_source()
    report = generate_report(source, request(source, contents=("heatmap",), types=(),
                                             heatmap_modes=("group", "site", "fab")))
    with closing(exporter.build_heatmap_workbook(report)) as book:
        assert set(book.sheetnames) == {"설비군_C4", "설비군_C3", "설비군_DFB", "설비군_O2",
                                       "사업장_평택", "분류_Memory", "분류_Foundry"}
        assert "EQ-4" in values(book["설비군_O2"]) and "EQ-7" in values(book["설비군_O2"])


def test_same_group_in_separate_locations_uses_one_sheet_and_preserves_both_blocks():
    report = report_with(heatmap_section("group",
        panel("C4", "EQ-A", site="화성", area="Memory", line="13L"),
        panel("C4", "EQ-B", site="평택", area="Foundry", line="P3-S5"),
        panel("C3", "EQ-C", line="V2")))
    with closing(exporter.build_heatmap_workbook(report)) as book:
        assert set(book.sheetnames) == {"C4", "C3"}
        content = "\n".join(str(value) for value in values(book["C4"]))
        assert all(value in content for value in ("화성", "평택", "Memory", "Foundry", "13L", "P3-S5", "EQ-A", "EQ-B"))
        assert "EQ-C" not in values(book["C4"]) and "EQ-C" in values(book["C3"])


def test_compact_header_keeps_date_scope_legend_and_data_without_freeze_or_repeated_titles():
    report = report_with(heatmap_section("group", panel("C4", "EQ-1", line="13L")))
    with closing(exporter.build_heatmap_workbook(report)) as book:
        sheet = book["C4"]
        assert sheet.freeze_panes is None and sheet.sheet_view.pane is None
        assert not sheet.print_title_rows
        assert "관측 기준일" in sheet["A1"].value and str(report.request.base_date) in sheet["A1"].value
        assert "기간" in sheet["A1"].value and str(report.request.start) in sheet["A1"].value
        assert "조회 조건" in sheet["A2"].value and report.scope in sheet["A2"].value
        assert [sheet.cell(3, column).value for column in range(1, 6)] == list(TIER_LABELS.values())
        assert sheet["F3"].value == "위험 후보"
        assert "조건부" in sheet["A4"].value and "서식" in sheet["A4"].value
        assert "C4" in sheet["A5"].value and sheet["A6"].value == "13L"
        assert code_cell(book, "EQ-1").coordinate == "A7"
        text = "\n".join(str(value) for value in values(sheet))
        assert report.title not in text and report.source not in text
        assert "히트맵 유형:" not in text and "자료 출처:" not in text and "생성 당시 판정" not in text


def test_each_tier_rule_targets_only_its_legend_and_tiles_and_changes_fill_only():
    with closing(exporter.build_heatmap_workbook(all_tiers_report())) as book:
        sheet = book["C4"]
        rules = tier_rules(sheet)
        assert len(sheet.conditional_formatting) == len(rules) == len(Tier)
        for tier, (formatting, rule) in rules.items():
            legend = next(cell for row in sheet.iter_rows() for cell in row if cell.value == TIER_LABELS[tier])
            codes = {f"EQ-{tier.value}"}
            if tier is Tier.AT:
                codes.update(("EQ-AT-RISK", "EQ-AT-OTHER"))
            expected = {legend.coordinate, *(code_cell(book, code).coordinate for code in codes)}
            assert conditional_coordinates(formatting) == expected
            assert rule.type == "expression" and tuple(rule.formula) in {("TRUE",), ("TRUE()",)}
            assert rule.dxf is not None and rule.dxf.fill is not None
            assert rule.dxf.fill.fgColor.rgb[-6:] == HEATMAP_TIER_COLORS[tier].lstrip("#").upper()
            assert all(getattr(rule.dxf, field) is None
                       for field in ("font", "border", "numFmt", "alignment", "protection"))
        at_formatting, _ = rules[Tier.AT]
        same_column = sorted((code_cell(book, code) for code in ("EQ-AT", "EQ-AT-RISK")), key=lambda cell: cell.row)
        assert same_column[0].column == same_column[1].column
        assert same_column[1].row == same_column[0].row + 1
        assert any(area.min_col == area.max_col == same_column[0].column
                   and area.min_row == same_column[0].row and area.max_row == same_column[1].row
                   for area in at_formatting.sqref.ranges)
        risk = code_cell(book, "EQ-AT-RISK")
        assert risk.border.top.style == "medium" and "버전 결함 규칙의 근거" in risk.comment.text


def test_saved_tier_color_rule_can_be_changed_for_legend_and_all_matching_tiles_together(tmp_path):
    original = tmp_path / "단계별 색상.xlsx"
    changed = tmp_path / "단계별 색상 변경.xlsx"
    assert exporter.save_report_heatmaps(all_tiers_report(), original).saved
    with closing(load_workbook(original)) as book:
        sheet = book["C4"]
        assert sheet.freeze_panes is None and not sheet.print_title_rows
        rules = tier_rules(sheet)
        formatting, rule = rules[Tier.AT]
        shared_coordinates = conditional_coordinates(formatting)
        expected_at = {code_cell(book, code).coordinate for code in ("EQ-AT", "EQ-AT-RISK", "EQ-AT-OTHER")}
        legend = next(cell for row in sheet.iter_rows() for cell in row if cell.value == TIER_LABELS[Tier.AT])
        assert shared_coordinates == expected_at | {legend.coordinate}
        rule.dxf.fill = PatternFill("solid", fgColor="3366CC")
        book.save(changed)
    with closing(load_workbook(changed)) as book:
        rules = tier_rules(book["C4"])
        assert len(rules) == len(Tier)
        assert conditional_coordinates(rules[Tier.AT][0]) == shared_coordinates
        for tier, (_, rule) in rules.items():
            expected = "3366CC" if tier is Tier.AT else HEATMAP_TIER_COLORS[tier].lstrip("#").upper()
            assert rule.dxf.fill.fgColor.rgb[-6:] == expected
        risk = code_cell(book, "EQ-AT-RISK")
        assert risk.border.top.style == "medium" and "버전 결함 규칙의 근거" in risk.comment.text


def test_grouped_scope_and_export_snapshot_do_not_change_with_later_source_edits():
    source = report_source()
    report = generate_report(source, request(source, selection=QuerySelection(fab_key="MEMORY"),
                                             contents=("heatmap",), types=(), heatmap_modes=("group",)))
    expected_tiers = {cell.code: cell.tier for section in report.sections if section.chart == "heatmap"
                      for item in section.visual for _, cells in item.lines for cell in cells}
    source.records = tuple(replace(row, pms=tuple(replace(pm, version_raw="v1") for pm in row.pms))
                           for row in source.records)
    with closing(exporter.build_heatmap_workbook(report)) as book:
        assert equipment_codes(book) == {"EQ-1", "EQ-2", "EQ-3", "EQ-7"}
        for code, tier in expected_tiers.items():
            assert code_cell(book, code).fill.fgColor.rgb[-6:] == HEATMAP_TIER_COLORS[tier].lstrip("#").upper()


@pytest.mark.parametrize("tier", list(Tier))
def test_editable_cells_keep_report_tier_colors_and_separate_risk_border_and_reason(tier):
    report = report_with(heatmap_section("group",
        panel("C4", "EQ-OK", tier=tier),
        panel("C4", "EQ-RISK", tier=tier, line="P2", risk=RiskStatus.RISK, risk_detail="특정 버전의 결함 규칙")))
    with closing(exporter.build_heatmap_workbook(report)) as book:
        ordinary, risk = code_cell(book, "EQ-OK"), code_cell(book, "EQ-RISK")
        expected = HEATMAP_TIER_COLORS[tier].lstrip("#").upper()
        assert ordinary.fill.fgColor.rgb[-6:] == risk.fill.fgColor.rgb[-6:] == expected
        assert risk.border != ordinary.border
        sides = (risk.border.top, risk.border.bottom, risk.border.left, risk.border.right)
        assert any(side.style and side.color and side.color.type == "rgb"
                   and int(side.color.rgb[-6:-4], 16) > int(side.color.rgb[-4:-2], 16) + 30
                   and int(side.color.rgb[-6:-4], 16) > int(side.color.rgb[-2:], 16) + 30 for side in sides)
        assert risk.comment is not None and "특정 버전의 결함 규칙" in risk.comment.text
        assert risk.data_type == ordinary.data_type == "s"
        risk.value = "수정 가능한 호기"
        assert risk.value == "수정 가능한 호기" and not risk.parent.protection.sheet


def test_saved_workbook_preserves_formula_like_equipment_codes_as_literal_text(tmp_path):
    codes = ("=1+1", "+SUM(A1:A2)", "-1", "@SUM(A1:A2)")
    report = report_with(heatmap_section("group", *(panel("C4", code, line=f"L{i}")
                                                    for i, code in enumerate(codes))))
    path = tmp_path / "문자열.xlsx"
    result = exporter.save_report_heatmaps(report, path)
    assert result.status is ExportStatus.SAVED and result.path == path
    with closing(load_workbook(path)) as book:
        assert set(book.sheetnames) == {"C4"}
        for code in codes:
            assert code_cell(book, code).data_type == "s"
        assert not any(cell.data_type == "f" for sheet in book for row in sheet.iter_rows() for cell in row)


def test_sheet_names_are_safe_unique_and_keep_original_item_names_in_cells():
    names = ("C4/[검사]:*?\\", "c4_[검사]:*?\\", "아주 긴 설비군 이름" * 5 + "A",
             "아주 긴 설비군 이름" * 5 + "B", "c4", "C4")
    report = report_with(heatmap_section("group", *(panel(name, f"EQ-{i}") for i, name in enumerate(names))))
    with closing(exporter.build_heatmap_workbook(report)) as book:
        assert len(book.sheetnames) == len(names)
        assert len({name.casefold() for name in book.sheetnames}) == len(names)
        assert all(0 < len(name) <= 31 and not re.search(r"[\\/*?:\[\]]", name) for name in book.sheetnames)
        for i, name in enumerate(names):
            sheet = code_cell(book, f"EQ-{i}").parent
            assert any(name in value for value in values(sheet) if isinstance(value, str))
        assert equipment_codes(book) == {f"EQ-{i}" for i in range(len(names))}


@pytest.mark.parametrize("sections", [(), (ReportSection(0, "요약"),),
    (ReportSection(1, "비어 있는 히트맵", chart="heatmap", visual=(), content_key="heatmap", heatmap_mode="group"),)])
def test_missing_heatmap_is_unavailable_and_does_not_replace_existing_file(tmp_path, sections):
    report = report_with(*sections)
    assert not exporter.has_report_heatmaps(report)
    with pytest.raises(ValueError):
        exporter.build_heatmap_workbook(report)
    path = tmp_path / "기존.xlsx"
    path.write_bytes(b"existing content")
    result = exporter.save_report_heatmaps(report, path)
    assert result.status is ExportStatus.FAILED and result.message
    assert path.read_bytes() == b"existing content"
    assert sorted(item.name for item in tmp_path.iterdir()) == [path.name]


def test_wrong_suffix_fails_without_touching_existing_file(tmp_path):
    report = report_with(heatmap_section("group", panel("C4", "EQ-1")))
    path = tmp_path / "기존.pdf"
    path.write_bytes(b"existing content")
    result = exporter.save_report_heatmaps(report, path)
    assert result.status is ExportStatus.FAILED and result.message
    assert path.read_bytes() == b"existing content"
    assert sorted(item.name for item in tmp_path.iterdir()) == [path.name]


def test_failed_atomic_publish_keeps_existing_workbook_and_removes_temporary_file(tmp_path, monkeypatch):
    report = report_with(heatmap_section("group", panel("C4", "EQ-1")))
    path = tmp_path / "기존.xlsx"
    path.write_bytes(b"existing content")

    def fail(source, destination):
        assert Path(source) != Path(destination) and Path(destination) == path
        raise PermissionError("파일이 다른 프로그램에서 열려 있습니다")

    monkeypatch.setattr(exporter.os, "replace", fail)
    result = exporter.save_report_heatmaps(report, path)
    assert result.status is ExportStatus.FAILED and result.message
    assert path.read_bytes() == b"existing content"
    assert sorted(item.name for item in tmp_path.iterdir()) == [path.name]


def test_last_allowed_excel_row_is_exported_without_an_out_of_range_print_area(tmp_path, monkeypatch):
    monkeypatch.setattr(exporter, "MAX_ROWS", 9)
    cells = tuple(ReportHeatmapCell(f"EQ-{i}", Tier.AT, RiskStatus.NO_RISK) for i in range(3))
    report = report_with(heatmap_section("group", ReportHeatmap("Memory", "평택", "C4", (("P1", cells),))))
    path = tmp_path / "행 한도.xlsx"
    assert exporter.save_report_heatmaps(report, path).saved
    with closing(load_workbook(path)) as book:
        assert book["C4"].max_row == 9
        assert "$F$9" in book["C4"].print_area
        assert equipment_codes(book) == {"EQ-0", "EQ-1", "EQ-2"}


def test_rows_beyond_excel_limit_fail_without_truncating_or_replacing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(exporter, "MAX_ROWS", 9)
    cells = tuple(ReportHeatmapCell(f"EQ-{i}", Tier.AT, RiskStatus.NO_RISK) for i in range(4))
    report = report_with(heatmap_section("group", ReportHeatmap("Memory", "평택", "C4", (("P1", cells),))))
    path = tmp_path / "기존.xlsx"
    path.write_bytes(b"existing content")
    result = exporter.save_report_heatmaps(report, path)
    assert not result.saved and "행 한도" in result.message
    assert path.read_bytes() == b"existing content"
    assert sorted(item.name for item in tmp_path.iterdir()) == [path.name]
