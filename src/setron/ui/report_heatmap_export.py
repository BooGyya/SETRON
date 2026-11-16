"""생성 당시 보고서 히트맵을 항목별 Excel 시트의 편집 가능한 셀로 저장한다."""

from collections import OrderedDict
from itertools import groupby
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from setron.features.export.schemas import ExportResult, ExportStatus
from setron.features.export.xlsx_cells import MAX_CELL_CHARACTERS, MAX_ROWS, write_literal_cell
from setron.features.risk.schemas import RiskStatus
from setron.features.version.groups import TIER_LABELS
from setron.ui.heatmap_grouping import GROUPING_MODES
from setron.ui.report_visual_data import HEATMAP_TIER_COLORS


MAX_COLUMNS = 16_384
_MODES = dict(GROUPING_MODES)
_INK = "142033"
_RISK = "C53B53"
_GRID = Side(style="thin", color="7C8795")
_RISK_EDGE = Side(style="medium", color=_RISK)
_RISK_LABELS = {RiskStatus.RISK: "위험 후보", RiskStatus.NO_RISK: "등록 규칙상 후보 아님",
                RiskStatus.UNKNOWN: "위험 미확인"}


def has_report_heatmaps(report):
    """실제 설비 셀이 있는 히트맵을 생성한 보고서인지 확인한다."""
    return report is not None and any(
        section.chart == "heatmap" and any(cells for panel in section.visual or () for _, cells in panel.lines)
        for section in report.sections)


def _groups(report):
    sections = [section for section in report.sections if section.chart == "heatmap"]
    groups = OrderedDict()
    for index, section in enumerate(sections):
        mode = section.heatmap_mode
        # 모드 필드가 없던 읽기 모델로 구성한 보고서도 요청의 고정 순서로 읽는다.
        if not mode and len(sections) == len(report.request.heatmap_modes):
            mode = report.request.heatmap_modes[index]
        if mode not in _MODES:
            raise ValueError("히트맵 묶음 기준을 확인할 수 없습니다. 보고서를 다시 생성해 주세요.")
        for panel in section.visual or ():
            if any(cells for _, cells in panel.lines):
                groups.setdefault((mode, panel.group), []).append(panel)
    if not groups:
        raise ValueError("Excel로 내보낼 히트맵 자료가 없습니다. 히트맵을 포함해 보고서를 생성해 주세요.")
    return groups


def _sheet_name(name, used):
    cleaned = re.sub(r"[\x00-\x1f\x7f\[\]:*?/\\]", "_", name).strip().strip("'").strip()
    base = cleaned[:31].rstrip("'") or "히트맵"
    if base.casefold() == "history":
        base = "_" + base
    candidate, number = base, 2
    while candidate.casefold() in used:
        suffix = f" ({number})"
        candidate = base[:31 - len(suffix)].rstrip("'") + suffix
        number += 1
    used.add(candidate.casefold())
    return candidate


def _write(sheet, row, column, value, *, bold=False, fill=None, border=None, size=11):
    cell = sheet.cell(row, column)
    write_literal_cell(cell, value)
    cell.font = Font(name="맑은 고딕", size=size, bold=bold, color=_INK)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    if fill:
        cell.fill = PatternFill("solid", fgColor=fill.lstrip("#"))
    if border:
        cell.border = border
    return cell


def _heading(sheet, row, columns, value, *, size=11, fill=None):
    _write(sheet, row, 1, value, bold=True, size=size, fill=fill)
    if columns > 1:
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=columns)
    sheet.row_dimensions[row].height = 28 if size > 11 else 24


def _sheet(sheet, report, mode, item, panels):
    width = max(6, max(len(panel.lines) for panel in panels))
    if width > MAX_COLUMNS:
        raise ValueError(f"{item}: Excel의 열 한도를 초과합니다.")
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
    for column in range(1, width + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 24
    _heading(sheet, 1, width, f"관측 기준일: {report.request.base_date} · 기간: {report.request.start} ~ {report.request.end}")
    _heading(sheet, 2, width, f"조회 조건: {report.scope}")
    tier_ranges = {}
    instructions = "범례 셀 선택 → 홈 > 조건부서식 > 규칙 관리 > 규칙 편집 > 서식에서 채우기 색을 변경하세요."
    for column, (tier, color) in enumerate(HEATMAP_TIER_COLORS.items(), 1):
        legend = _write(sheet, 3, column, TIER_LABELS[tier], fill=color)
        legend.comment = Comment(f"{TIER_LABELS[tier]}\n{instructions}\n이 시트의 같은 단계 설비 셀에도 함께 적용됩니다.", "SETRON")
        tier_ranges[tier] = [legend.coordinate]
    _write(sheet, 3, 6, "위험 후보", border=Border(left=_RISK_EDGE, right=_RISK_EDGE,
                                              top=_RISK_EDGE, bottom=_RISK_EDGE))
    _heading(sheet, 4, width, instructions)
    row = 5
    tiers = tuple(HEATMAP_TIER_COLORS)
    normal_border = Border(left=_GRID, right=_GRID, top=_GRID, bottom=_GRID)
    risk_border = Border(left=_RISK_EDGE, right=_RISK_EDGE, top=_RISK_EDGE, bottom=_RISK_EDGE)
    for panel in panels:
        height = max((len(cells) for _, cells in panel.lines), default=0)
        if row + height + 1 > MAX_ROWS:
            raise ValueError(f"{item}: Excel의 행 한도를 초과합니다.")
        heading = panel.heading or " · ".join(value for value in (panel.area, panel.site, panel.group) if value)
        _heading(sheet, row, width, heading, fill="EEF1F5")
        for column, (caption, cells) in enumerate(panel.lines, 1):
            _write(sheet, row + 1, column, caption, bold=True, fill="EEF1F5", border=normal_border)
            ordered = sorted(cells, key=lambda cell: (tiers.index(cell.tier), cell.code))
            target_row = row + 2
            for tier, entries in groupby(ordered, key=lambda cell: cell.tier):
                first_row = target_row
                for tile in entries:
                    cell = _write(sheet, target_row, column, tile.code, bold=True,
                                  fill=HEATMAP_TIER_COLORS[tier],
                                  border=risk_border if tile.risk is RiskStatus.RISK else normal_border)
                    note = f"버전 단계: {TIER_LABELS[tier]}\n위험: {_RISK_LABELS[tile.risk]}"
                    if tile.risk_detail:
                        note += "\n근거: " + tile.risk_detail
                    if len(note) > MAX_CELL_CHARACTERS:
                        raise ValueError(f"{sheet.title}!{cell.coordinate}: 위험 근거가 Excel 메모의 길이 한도를 초과합니다.")
                    cell.comment = Comment(note, "SETRON")
                    target_row += 1
                letter = get_column_letter(column)
                # 한 단계의 연속 세로 구간을 합쳐 큰 히트맵도 규칙 수를 늘리지 않는다.
                tier_ranges[tier].append(f"{letter}{first_row}:{letter}{target_row - 1}")
        header_lines = max((len(caption.splitlines()) for caption, _ in panel.lines), default=1)
        sheet.row_dimensions[row + 1].height = max(28, header_lines * 17)
        for offset in range(height):
            sheet.row_dimensions[row + 2 + offset].height = 34
        row += height + 4
    for tier, ranges in tier_ranges.items():
        rule = FormulaRule(formula=["TRUE"], fill=PatternFill("solid", fgColor=HEATMAP_TIER_COLORS[tier].lstrip("#")),
                           stopIfTrue=True)
        sheet.conditional_formatting.add(" ".join(ranges), rule)
    sheet.print_area = f"A1:{get_column_letter(width)}{row - 3}"


def build_heatmap_workbook(report):
    """동일 항목의 사업장·분류 패널을 한 시트에 모으며 판정을 다시 계산하지 않는다."""
    groups = _groups(report)
    multiple_modes = len({mode for mode, _ in groups}) > 1
    book = Workbook()
    book.remove(book.active)
    used = set()
    try:
        for (mode, item), panels in groups.items():
            name = f"{_MODES[mode]}_{item}" if multiple_modes else item
            sheet = book.create_sheet(_sheet_name(name, used))
            _sheet(sheet, report, mode, item, panels)
    except Exception:
        book.close()
        raise
    return book


def save_report_heatmaps(report, path):
    """완성한 Excel만 교체해 실패한 저장이 기존 파일을 손상시키지 않게 한다."""
    path, temporary, book = Path(path), None, None
    if path.suffix.lower() != ".xlsx":
        return ExportResult(ExportStatus.FAILED, path, "히트맵 Excel 파일 이름은 .xlsx로 끝나야 합니다.")
    try:
        if report.request.demo == "save_failure":
            raise OSError("UI 시연 · 저장 실패")
        book = build_heatmap_workbook(report)
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".xlsx", delete=False) as handle:
            temporary = Path(handle.name)
        book.save(temporary)
        os.replace(temporary, path)
        return ExportResult(ExportStatus.SAVED, path, f"히트맵을 항목별 {len(book.worksheets)}개 시트로 저장했습니다.")
    except Exception as error:
        return ExportResult(ExportStatus.FAILED, path, f"히트맵 Excel을 저장하지 못했습니다. 기존 파일은 유지했습니다. {error}")
    finally:
        if book is not None:
            book.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
