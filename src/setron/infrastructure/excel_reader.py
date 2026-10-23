"""사용자가 열어 둔 Excel 창에서 통합문서와 시트 데이터를 읽는다.

보안 정책상 Excel 파일을 직접 열 수 없는 환경을 고려해, 파일 경로가 아니라
실행 중인 Excel 프로세스(xlwings)에 연결해서 값을 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import xlwings as xw


class ExcelReadError(Exception):
    """열린 Excel에서 데이터를 읽지 못했을 때 발생한다."""


class ExcelNotRunningError(ExcelReadError):
    """Excel이 실행 중이 아니거나 열린 통합문서가 하나도 없다."""


class ExcelConnectionError(ExcelReadError):
    """실행 중인 Excel에 연결하지 못했다 (Excel 미설치, 응답 없음 등)."""


class WorkbookNotFoundError(ExcelReadError):
    """지정한 이름의 통합문서가 열려 있지 않다."""


class SheetNotFoundError(ExcelReadError):
    """통합문서에 지정한 이름의 시트가 없다."""


@dataclass(frozen=True)
class WorkbookInfo:
    """열려 있는 통합문서 하나의 정보."""

    name: str
    full_name: str
    sheet_names: list[str]


@dataclass(frozen=True)
class MergedRange:
    """병합된 셀 범위. 행·열 번호는 Excel과 같은 1부터 시작하는 번호다."""

    top: int
    left: int
    bottom: int
    right: int

    @property
    def address(self) -> str:
        return f"{cell_address(self.top, self.left)}:{cell_address(self.bottom, self.right)}"

    def contains(self, row: int, col: int) -> bool:
        return self.top <= row <= self.bottom and self.left <= col <= self.right


@dataclass
class SheetData:
    """시트의 사용 영역에서 읽은 값.

    ``values[i][j]``는 Excel의 ``(first_row + i, first_col + j)`` 셀 값이다.
    병합 셀은 Excel과 같이 왼쪽 위 칸에만 값이 있고 나머지는 None이다.
    병합 범위까지 값을 채운 표가 필요하면 :meth:`filled_values`를 쓴다.
    """

    workbook_name: str
    sheet_name: str
    first_row: int
    first_col: int
    values: list[list[Any]]
    merged_ranges: list[MergedRange] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.values)

    @property
    def col_count(self) -> int:
        return len(self.values[0]) if self.values else 0

    def address_of(self, i: int, j: int) -> str:
        """``values[i][j]``의 원본 셀 주소(예: ``C5``)를 돌려준다."""
        return cell_address(self.first_row + i, self.first_col + j)

    def filled_values(self) -> list[list[Any]]:
        """병합 범위 안의 빈 칸을 왼쪽 위 칸의 값으로 채운 복사본을 돌려준다."""
        return fill_merged(self.values, self.merged_ranges, self.first_row, self.first_col)


def cell_address(row: int, col: int) -> str:
    """1부터 시작하는 행·열 번호를 ``A1`` 형식 주소로 바꾼다."""
    if row < 1 or col < 1:
        raise ValueError(f"행과 열은 1 이상이어야 합니다: row={row}, col={col}")
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return f"{letters}{row}"


def fill_merged(
    values: list[list[Any]],
    merged_ranges: list[MergedRange],
    first_row: int = 1,
    first_col: int = 1,
) -> list[list[Any]]:
    """병합 범위의 왼쪽 위 값을 범위 안의 모든 칸에 채운 새 표를 돌려준다.

    원본 ``values``는 바꾸지 않는다. 사용 영역 밖에 걸친 병합 범위는 영역 안쪽만 채운다.
    """
    filled = [list(row) for row in values]
    rows = len(filled)
    cols = len(filled[0]) if filled else 0
    for rng in merged_ranges:
        top_i, left_j = rng.top - first_row, rng.left - first_col
        if not (0 <= top_i < rows and 0 <= left_j < cols):
            continue
        source = filled[top_i][left_j]
        for i in range(max(top_i, 0), min(rng.bottom - first_row, rows - 1) + 1):
            for j in range(max(left_j, 0), min(rng.right - first_col, cols - 1) + 1):
                filled[i][j] = source
    return filled


NOT_RUNNING_MESSAGE = "열려 있는 Excel 파일이 없습니다. 가져올 파일을 Excel로 연 뒤 다시 시도하세요."


def list_open_workbooks() -> list[WorkbookInfo]:
    """실행 중인 모든 Excel 창의 열린 통합문서 목록을 돌려준다.

    Excel이 실행 중이 아니거나 열린 통합문서가 없으면 :class:`ExcelNotRunningError`를,
    Excel에 연결하지 못하면 :class:`ExcelConnectionError`를 낸다.
    """
    try:
        return [
            WorkbookInfo(
                name=book.name,
                full_name=book.fullname,
                sheet_names=[sheet.name for sheet in book.sheets],
            )
            for book in _open_books()
        ]
    except ExcelReadError:
        raise
    except Exception as exc:  # COM 호출 중 Excel이 닫히는 경우 등
        raise ExcelConnectionError(f"Excel에서 통합문서 정보를 읽지 못했습니다: {exc}") from exc


def read_sheet(workbook_name: str, sheet_name: str) -> SheetData:
    """열려 있는 통합문서의 시트에서 사용 영역의 값과 병합 범위를 읽는다."""
    book = _find_open_book(workbook_name)
    try:
        sheet = book.sheets[sheet_name]
    except Exception as exc:  # xlwings는 COM 오류를 그대로 올리므로 넓게 받는다.
        raise SheetNotFoundError(f"'{workbook_name}'에 '{sheet_name}' 시트가 없습니다.") from exc

    used = sheet.used_range
    raw = used.options(ndim=2).value
    values = [list(row) for row in raw] if raw else []
    first_row, first_col = used.row, used.column

    merged = _find_merged_ranges(used.api) if values else []
    return SheetData(
        workbook_name=book.name,
        sheet_name=sheet.name,
        first_row=first_row,
        first_col=first_col,
        values=values,
        merged_ranges=merged,
    )


def _open_books() -> list[xw.Book]:
    """실행 중인 모든 Excel 창의 열린 통합문서. 하나도 없으면 오류를 낸다."""
    try:
        books = [book for app in xw.apps for book in app.books]
    except Exception as exc:  # pywin32·COM 오류는 종류가 다양해서 넓게 받는다.
        raise ExcelConnectionError(
            "Excel에 연결하지 못했습니다. Excel이 설치되어 있고 응답하는지 확인하세요."
        ) from exc
    if not books:
        raise ExcelNotRunningError(NOT_RUNNING_MESSAGE)
    return books


def _find_open_book(workbook_name: str) -> xw.Book:
    for book in _open_books():
        if book.name == workbook_name:
            return book
    raise WorkbookNotFoundError(f"열려 있는 통합문서 중 '{workbook_name}'이(가) 없습니다.")


def _find_merged_ranges(com_range: Any) -> list[MergedRange]:
    """COM Range에서 병합 범위를 찾는다.

    셀을 하나씩 확인하면 COM 호출이 셀 수만큼 생겨 느리다. 그래서 열마다
    ``MergeCells``로 병합이 있는지 먼저 묻고, 병합이 있는 열만 위아래로 반씩
    나눠 가며 병합 범위를 찾는다. 찾은 범위의 아래쪽은 건너뛴다.
    """
    sheet = com_range.Worksheet
    first_row, first_col = com_range.Row, com_range.Column
    last_row = first_row + com_range.Rows.Count - 1
    last_col = first_col + com_range.Columns.Count - 1
    found: dict[tuple[int, int, int, int], MergedRange] = {}

    def merge_state(col: int, top: int, bottom: int) -> bool | None:
        # True: 모두 병합, False: 병합 없음, None: 섞임
        return sheet.Range(sheet.Cells(top, col), sheet.Cells(bottom, col)).MergeCells

    def scan_column(col: int, top: int, bottom: int) -> None:
        while top <= bottom:
            state = merge_state(col, top, bottom)
            if state is False:
                return
            if top == bottom or state is True:
                area = sheet.Cells(top, col).MergeArea
                a_top, a_left = area.Row, area.Column
                a_bottom = a_top + area.Rows.Count - 1
                a_right = a_left + area.Columns.Count - 1
                if area.MergeCells:
                    found[(a_top, a_left, a_bottom, a_right)] = MergedRange(a_top, a_left, a_bottom, a_right)
                top = a_bottom + 1
                continue
            mid = (top + bottom) // 2
            scan_column(col, top, mid)
            top = mid + 1

    for col in range(first_col, last_col + 1):
        scan_column(col, first_row, last_row)
    return sorted(found.values(), key=lambda m: (m.top, m.left))
