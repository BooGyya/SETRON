"""라인별 히트맵 시트: 사이트 → 계열 → 라인 → 호기 순으로 묶어 칸을 판정 색으로 칠한다 (BE-021, 5차 멘토 요청).

멘토가 수동으로 만들던 양식(5차 멘토링 예시)을 따른다.

- 맨 위 머리글은 계열, 그 아래 머리글은 라인이다. 칸에는 설비 호기 코드를 적고 판정 색으로 칠한다.
- 한 라인 안에서 위에서 아래로 채우고, ``ROWS_PER_COLUMN``칸을 넘으면 옆 열로 이어 쓴다.
- 계열 묶음은 왼쪽에서 오른쪽으로 놓고, 사이트가 여러 개면 사이트별로 아래로 쌓는다.

칸 하나는 설비 한 대 × 계열 하나(적용률 계산의 한 건)다. O2·O3 혼합 설비는 두 계열에 각각 나온다.
HPA는 공통 집계와 같이 넣지 않는다. 색은 N 그룹 판정의 색 단계(``version.groups.Tier``, 팀 결정 11-05)를 쓴다:
진초록 N+1 이상 · 연초록 N · 노랑 N-1 · 빨강 N-2 이하 · 회색 비교 불가. 멘토 예시 그림의 네 색과 같은 구분이다.
목표가 릴리즈 하나여도 같은 버전(N)과 더 최신(N+1 이상), 한 칸 아래와 두 칸 이상 아래를 나눠 칠한다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from setron.features.aggregation.schemas import FamilyStatus
from setron.features.export.schemas import StatusReport
from setron.features.export.xlsx_cells import ReportTextWriter
from setron.features.version.groups import TIER_LABELS, Tier, tier_of
from setron.features.version.schemas import CompareStatus

LINE_HEATMAP_SHEET = "라인별 히트맵"
ROWS_PER_COLUMN = 6
NO_SITE, NO_LINE = "Site 미확인", "라인 미확인"
# 앱 상태 색상(트리맵과 같음): N 이상 초록, N 미만 노랑, 판단 불가 회색
HEATMAP_COLORS = {
    Tier.ABOVE: "1E7B45",      # 진초록
    Tier.AT: "9BD4A9",         # 연초록
    Tier.ONE_BELOW: "F2D16B",  # 노랑
    Tier.TWO_BELOW: "E07A7A",  # 빨강
    Tier.UNKNOWN: "B8C0CC",    # 회색
}
LEGEND = tuple((tier, TIER_LABELS[tier]) for tier in (Tier.ABOVE, Tier.AT, Tier.ONE_BELOW, Tier.TWO_BELOW, Tier.UNKNOWN))
_WORST_FIRST = (Tier.TWO_BELOW, Tier.ONE_BELOW, Tier.AT, Tier.ABOVE)
_THIN = Side(style="thin", color="FFFFFF")
_BOX = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HEADER_FILL = PatternFill("solid", fgColor="E5EAF3")


@dataclass(frozen=True)
class HeatmapCell:
    """칸 하나: 호기 코드와 그 설비·계열의 색 단계."""

    equipment_code: str
    tier: Tier


@dataclass(frozen=True)
class LineGroup:
    line: str
    cells: tuple[HeatmapCell, ...]

    @property
    def width(self) -> int:
        """이 라인이 차지하는 열 수. 칸이 없어도 머리글 한 열은 둔다."""
        return max(1, -(-len(self.cells) // ROWS_PER_COLUMN))


@dataclass(frozen=True)
class FamilyGroup:
    family: str
    lines: tuple[LineGroup, ...]

    @property
    def width(self) -> int:
        return sum(line.width for line in self.lines)


@dataclass(frozen=True)
class SiteGroup:
    site: str
    families: tuple[FamilyGroup, ...]


def line_heatmap_groups(report: StatusReport) -> tuple[SiteGroup, ...]:
    """보고서의 설비·계열 판정을 사이트 → 계열 → 라인 → 호기로 묶는다.

    사이트·계열은 이름순, 라인은 숫자를 숫자로 보는 자연 정렬, 호기는 코드순이다.
    HPA는 ``report.summary.equipment``에 없으므로 들어가지 않는다.
    """
    tree: dict[str, dict[str, dict[str, list[HeatmapCell]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for equipment in report.summary.equipment:
        record = equipment.record
        site = _label(record.site, NO_SITE)
        line = _label(record.line, NO_LINE)
        for family in equipment.families:
            name = report.family_names.get(family.family_id, f"계열 {family.family_id}")
            tree[site][name][line].append(HeatmapCell(record.equipment_code, _tier(family)))
    return tuple(
        SiteGroup(site, tuple(
            FamilyGroup(family, tuple(
                LineGroup(line, tuple(sorted(cells, key=lambda c: c.equipment_code)))
                for line, cells in sorted(lines.items(), key=lambda item: _natural(item[0]))))
            for family, lines in sorted(families.items(), key=lambda item: _natural(item[0]))))
        for site, families in sorted(tree.items(), key=lambda item: _natural(item[0])))


def write_line_heatmap(sheet: Worksheet, groups: tuple[SiteGroup, ...], text: ReportTextWriter) -> None:
    """범례와 사이트별 히트맵 블록을 시트에 그린다. 묶음이 없으면 안내 한 줄만 적는다."""
    sheet.title = LINE_HEATMAP_SHEET
    _put(sheet, 1, 1, "범례", text, bold=True)
    for offset, (tier, label) in enumerate(LEGEND):
        _put(sheet, 1, 2 + offset, label, text, fill=HEATMAP_COLORS[tier])
    _put(sheet, 2, 1, "칸 하나 = 설비 1대 × 계열 1개. O2·O3 혼합 설비는 두 계열에 각각 나옵니다. HPA는 제외합니다.", text)
    if not groups:
        _put(sheet, 4, 1, "표시할 설비가 없습니다.", text)
        return
    row = 4
    widest = 1
    for site in groups:
        _put(sheet, row, 1, site.site, text, bold=True)
        column = 1
        for family in site.families:
            _merge(sheet, row + 1, column, family.width, family.family, text)
            line_column = column
            for line in family.lines:
                _merge(sheet, row + 2, line_column, line.width, line.line, text)
                for index, cell in enumerate(line.cells):
                    target = sheet.cell(row + 3 + index % ROWS_PER_COLUMN, line_column + index // ROWS_PER_COLUMN)
                    text.write(target, cell.equipment_code, LINE_HEATMAP_SHEET)
                    target.fill = PatternFill("solid", fgColor=HEATMAP_COLORS[cell.tier])
                    target.alignment = Alignment(horizontal="center", vertical="center")
                    target.border = _BOX
                line_column += line.width
            column += family.width + 1  # 계열 묶음 사이 빈 열
        widest = max(widest, column)
        row += 3 + ROWS_PER_COLUMN + 2  # 사이트 머리글·계열·라인 머리글 + 칸 + 빈 줄 2개
    for index in range(1, widest + 1):
        sheet.column_dimensions[get_column_letter(index)].width = 11


def _tier(family: FamilyStatus) -> Tier:
    """설비·계열 한 건의 색 단계. 미달이면 미달 PM 중 가장 낮은 단계, 달성이면 PM 중 가장 낮은 단계다."""
    if family.status is CompareStatus.UNDETERMINED:
        return Tier.UNKNOWN
    tiers = [tier_of(p.comparison) for p in family.pms if p.comparison.status is family.status]
    return min(tiers, key=_WORST_FIRST.index) if tiers else Tier.UNKNOWN


def _label(value: str | None, missing: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else missing


def _natural(value: str) -> tuple:
    """'5L' < '12L' < 'M1'처럼 숫자 부분을 숫자로 비교한다. 미확인 표시는 맨 뒤로 보낸다."""
    missing = value in (NO_SITE, NO_LINE)
    return (missing, [(0, int(part), "") if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", value) if part])


def _put(sheet: Worksheet, row: int, column: int, value: str, text: ReportTextWriter, *,
         bold: bool = False, fill: str | None = None) -> None:
    cell = sheet.cell(row, column)
    text.write(cell, value, LINE_HEATMAP_SHEET)
    if bold:
        cell.font = Font(bold=True)
    if fill:
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.alignment = Alignment(horizontal="center")


def _merge(sheet: Worksheet, row: int, column: int, width: int, value: str, text: ReportTextWriter) -> None:
    cell = sheet.cell(row, column)
    text.write(cell, value, LINE_HEATMAP_SHEET)
    cell.font = Font(bold=True)
    cell.fill = _HEADER_FILL
    cell.alignment = Alignment(horizontal="center", vertical="center")
    if width > 1:
        sheet.merge_cells(start_row=row, start_column=column, end_row=row, end_column=column + width - 1)
