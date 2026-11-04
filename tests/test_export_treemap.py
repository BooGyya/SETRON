"""현황 보고서 xlsx의 트리맵 차트(chartEx) 부품을 검증한다 (BE-021).

Excel로 실제로 여는 확인은 Excel이 있는 PC에서 따로 했다(11-04, Excel 16.0: 종류 117=트리맵, 상태 색 확인).
여기서는 Excel 없이 파일 구조만 본다.
"""

import re
import zipfile

from openpyxl import load_workbook

from setron.features.export.service import TREEMAP_COLORS, TREEMAP_SHEET, save_status_report, treemap_rows
from setron.features.export.treemap import PARENT_COLOR, _point_colors
from test_export_service import report


def test_report_has_treemap_sheet_table_and_chart_parts(tmp_path):
    path = tmp_path / "보고서.xlsx"
    assert save_status_report(report(), path).saved

    book = load_workbook(path)
    rows = [tuple(r) for r in book[TREEMAP_SHEET].iter_rows(min_row=2, values_only=True)]
    assert rows == treemap_rows(report()) and all(value > 0 for *_, value in rows)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        chart = next(n for n in names if re.fullmatch(r"xl/charts/chartEx\d+\.xml", n))
        xml = z.read(chart).decode()
        workbook = z.read("xl/workbook.xml").decode()
        types = z.read("[Content_Types].xml").decode()
        rels = z.read(chart.replace("charts/", "charts/_rels/") + ".rels").decode()
    assert 'layoutId="treemap"' in xml
    # 차트는 숨은 이름으로 같은 시트의 표 셀을 참조한다 (셀을 고치면 차트가 바뀜)
    assert f"'{TREEMAP_SHEET}'!$A$2:$B${len(rows) + 1}" in workbook and 'name="_xlchart.v1.0" hidden="1"' in workbook
    assert "application/vnd.ms-office.chartex+xml" in types and "chartstyle+xml" in types and "chartcolorstyle+xml" in types
    assert "chartStyle" in rels and "chartColorStyle" in rels  # Excel은 둘 다 있어야 연다
    assert "<cx:dataPt" in xml


def test_point_colors_follow_excel_tree_order():
    """점 번호: 상위 범주 칸, 이어서 그 하위 칸들 (표 순서)."""
    rows = [("O2", "N 이상", 1), ("O2", "N 미만", 2), ("O3", "N 이상", 1)]
    assert _point_colors(rows, TREEMAP_COLORS) == [
        (0, PARENT_COLOR), (1, TREEMAP_COLORS["N 이상"]), (2, TREEMAP_COLORS["N 미만"]),
        (3, PARENT_COLOR), (4, TREEMAP_COLORS["N 이상"]),
    ]


def test_report_without_counts_has_no_treemap(tmp_path):
    from dataclasses import replace

    empty = report()
    empty = replace(empty, summary=replace(empty.summary, by_family=()))
    path = tmp_path / "빈.xlsx"
    assert save_status_report(empty, path).saved
    assert TREEMAP_SHEET not in load_workbook(path).sheetnames
