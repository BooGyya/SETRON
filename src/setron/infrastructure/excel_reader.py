"""사용자가 열어 둔 Excel 창에서 통합문서와 시트 데이터를 읽는다.

보안 정책상 Excel 파일을 직접 열 수 없는 환경을 고려해, 파일 경로가 아니라
실행 중인 Excel 프로세스(xlwings)에 연결해서 값을 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from ntpath import normcase, normpath
from typing import Any
import warnings

import xlwings as xw


class ExcelReadError(Exception):
    """열린 Excel에서 데이터를 읽지 못했을 때 발생한다."""


class ExcelNotRunningError(ExcelReadError):
    """Excel이 실행 중이 아니거나 열린 통합문서가 하나도 없다."""


class ExcelConnectionError(ExcelReadError):
    """실행 중인 Excel에 연결하지 못했다 (Excel 미설치, 응답 없음 등)."""


class ExcelProtectedViewError(ExcelConnectionError):
    """제한된 보기 창으로 인한 Excel 연결 실패 오류."""


class ExcelSheetReadError(ExcelReadError):
    """선택한 시트의 값·위치·파일명 읽기 실패 오류."""


class WorkbookNotFoundError(ExcelReadError):
    """지정한 이름의 통합문서가 열려 있지 않다."""


class WorkbookSelectionChangedError(WorkbookNotFoundError):
    """선택 당시의 파일과 현재 열린 파일이 달라진 경우의 재선택 오류."""


class ExcelDiscoveryWarning(UserWarning):
    """일부 Excel 창의 목록 조회에 실패했지만 다른 파일은 선택할 수 있다는 경고."""


class SheetNotFoundError(ExcelReadError):
    """통합문서에 지정한 이름의 시트가 없다."""


class AmbiguousWorkbookError(ExcelReadError):
    """여러 Excel 창에 같은 이름의 통합문서가 열린 경우의 선택 오류."""


@dataclass(frozen=True)
class WorkbookInfo:
    """열린 통합문서 선택 정보.

    ivar
    - name: Excel에서 확인한 파일명. 저장 확인창에 표시할 이름이다.
    - full_name: Excel에서 제공하는 전체 이름. 저장하지 않은 파일은 경로가 없을 수 있다.
    - sheet_names: 선택 가능한 시트 이름 목록.
    - app_pid: 같은 파일명이 여러 창에 있을 때 구분하는 Excel 프로세스 번호.
      직접 생성한 정보의 None은 읽기 호출 전에 실제 목록으로 다시 선택해야 한다.
    """

    name: str
    full_name: str
    sheet_names: list[str]
    app_pid: int | None = None


@dataclass(frozen=True)
class MergedRange:
    """원본 시트의 병합 범위.

    ivar
    - top: 위쪽 행 번호. Excel과 같이 1부터 시작한다.
    - left: 왼쪽 열 번호. Excel과 같이 1부터 시작한다.
    - bottom: 아래쪽 행 번호. 범위에 포함된다.
    - right: 오른쪽 열 번호. 범위에 포함된다.
    """

    top: int
    left: int
    bottom: int
    right: int

    @property
    def address(self) -> str:
        """병합 범위를 A1 표기법으로 반환한다.

        return
        - 시트 내 병합 범위 주소. 예: A2:C3.
        """
        return f"{cell_address(self.top, self.left)}:{cell_address(self.bottom, self.right)}"

    def contains(self, row: int, col: int) -> bool:
        """원본 셀 좌표가 병합 범위에 속하는지 확인한다.

        param
        - row: 1부터 시작하는 원본 행 번호.
        - col: 1부터 시작하는 원본 열 번호.

        return
        - 범위의 경계를 포함해 해당 셀이 속하면 True.
        """
        return self.top <= row <= self.bottom and self.left <= col <= self.right


@dataclass
class SheetData:
    """시트 사용 영역의 값과 원본 위치 데이터.

    ``values[i][j]``는 Excel의 ``(first_row + i, first_col + j)`` 셀 값이다.
    병합 셀은 Excel과 같이 왼쪽 위 칸에만 값이 있고 나머지는 None이다.
    병합 범위까지 값을 채운 표가 필요하면 :meth:`filled_values`를 쓴다.

    ivar
    - workbook_name: 읽은 파일의 실제 이름. 저장 확인창에 표시하며 DB 기록용이 아니다.
    - sheet_name: 읽은 원본 시트명.
    - first_row: 사용 영역의 시작 행 번호. Excel과 같이 1부터 시작한다.
    - first_col: 사용 영역의 시작 열 번호. Excel과 같이 1부터 시작한다.
    - values: 원본 순서의 2차원 셀 값. 빈 값은 None, Excel 오류는 오류 문자열이다.
    - merged_ranges: 원본 좌표로 표현한 병합 범위 목록.
    - app_pid: 읽기에 사용한 Excel 프로세스 번호. 직접 생성한 데이터에서는 None일 수 있다.
    """

    workbook_name: str
    sheet_name: str
    first_row: int
    first_col: int
    values: list[list[Any]]
    merged_ranges: list[MergedRange] = field(default_factory=list)
    app_pid: int | None = None

    @property
    def row_count(self) -> int:
        """읽은 행 수를 반환한다.

        return
        - values의 행 수. 빈 표이면 0.
        """
        return len(self.values)

    @property
    def col_count(self) -> int:
        """읽은 열 수를 반환한다.

        return
        - values의 열 수. 빈 표이면 0.
        """
        return len(self.values[0]) if self.values else 0

    def address_of(self, i: int, j: int) -> str:
        """읽은 표의 인덱스에 대응하는 원본 셀 주소를 반환한다.

        param
        - i: 0부터 시작하는 values의 행 인덱스.
        - j: 0부터 시작하는 values의 열 인덱스.

        return
        - 원본 시트 내 셀 주소. 예: C5.

        raises
        - IndexError: 인덱스가 읽은 표의 범위를 벗어난 경우.
        """
        if not (0 <= i < self.row_count and 0 <= j < len(self.values[i])):
            raise IndexError(f"읽은 셀 범위를 벗어났습니다: i={i}, j={j}")
        return cell_address(self.first_row + i, self.first_col + j)

    def filled_values(self) -> list[list[Any]]:
        """병합 범위 안의 칸을 왼쪽 위 값으로 채운 복사본을 반환한다.

        return
        - 원본 values를 바꾸지 않고 병합 값을 펼친 2차원 표.
        """
        return fill_merged(self.values, self.merged_ranges, self.first_row, self.first_col)


def cell_address(row: int, col: int) -> str:
    """원본 행·열 번호를 A1 형식 주소로 바꾼다.

    param
    - row: 1부터 시작하는 행 번호.
    - col: 1부터 시작하는 열 번호.

    return
    - 원본 시트 내 셀 주소.

    raises
    - ValueError: 행이나 열 번호가 1보다 작은 경우.
    """
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
    기준 셀이 사용 영역 밖이면 값을 추정하지 않고 해당 병합 범위를 건너뛴다.

    param
    - values: 읽은 원본 셀의 2차원 표.
    - merged_ranges: 원본 시트 좌표의 병합 범위 목록.
    - first_row: 표가 시작하는 원본 행 번호. 기본값은 1.
    - first_col: 표가 시작하는 원본 열 번호. 기본값은 1.

    return
    - 병합 값을 펼친 새로운 표. 빈 입력이면 빈 목록.
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

    return
    - 파일명·시트명·프로세스 번호를 포함한 선택 목록. Excel 객체는 반환하지 않는다.
      일부 창이나 파일의 조회에 실패해도 정상적으로 확인한 목록은 반환하며,
      실패 사실은 ExcelDiscoveryWarning으로 전달한다.

    raises
    - ExcelNotRunningError: Excel이 실행 중이 아니거나 열린 통합문서가 없는 경우.
    - ExcelProtectedViewError: 선택 가능한 파일이 없고 제한된 보기 연결 오류가 발생한 경우.
    - ExcelConnectionError: 선택 가능한 파일이 없고 연결·정보 조회 오류가 발생한 경우.
    """
    workbooks: list[WorkbookInfo] = []
    errors: list[ExcelConnectionError] = []
    try:
        for app in xw.apps:
            try:
                books = list(app.books)
            except Exception as exc:
                errors.append(_connection_error(exc))
                continue
            for book in books:
                try:
                    workbooks.append(WorkbookInfo(
                        name=book.name,
                        full_name=book.fullname,
                        sheet_names=[sheet.name for sheet in book.sheets],
                        app_pid=app.pid,
                    ))
                except Exception as exc:
                    errors.append(_connection_error(exc))
    except Exception as exc:  # 창 목록을 읽다가 Excel이 닫힌 경우 등
        errors.append(_connection_error(exc))
    if not workbooks:
        if errors:
            raise errors[0]
        raise ExcelNotRunningError(NOT_RUNNING_MESSAGE)
    for error in errors:
        warnings.warn(f"일부 Excel 파일을 목록에서 확인하지 못했습니다. {error}", ExcelDiscoveryWarning, stacklevel=2)
    return workbooks


def read_sheet(workbook_name: str, sheet_name: str, *, app_pid: int, full_name: str) -> SheetData:
    """선택한 열린 통합문서에서 시트 값·병합 범위·원본 위치를 읽는다.

    파일을 새로 열거나 저장하지 않는다. 파일명은 이번 읽기 결과에만 전달한다.
    UI에서 list_open_workbooks()의 name·app_pid·full_name을 함께 전달한다.
    읽기 전후에 선택 당시의 전체 이름을 확인하며 다른 파일로 바뀌면 결과를 반환하지 않는다.

    param
    - workbook_name: 선택한 통합문서의 정확한 파일명.
    - sheet_name: 선택한 원본 시트명.
    - app_pid: 선택 목록에서 받은 Excel 프로세스 번호. 양의 정수여야 한다.
    - full_name: 선택 목록에서 받은 전체 이름. 저장하지 않은 파일은 Excel이 제공한 이름을 쓴다.

    return
    - 파일명·시트명·셀 값·시작 좌표·병합 범위를 담은 SheetData.

    raises
    - ValueError: 프로세스 번호가 잘못됐거나 전체 이름이 비어 있는 경우.
    - ExcelNotRunningError: 열린 통합문서가 없는 경우.
    - ExcelProtectedViewError: 선택한 프로세스가 제한된 보기 창 때문에 연결에 실패한 경우.
    - ExcelConnectionError: Excel 연결이나 통합문서 검색에 실패한 경우.
    - WorkbookNotFoundError: 선택한 파일이나 프로세스가 더 이상 열려 있지 않은 경우.
    - WorkbookSelectionChangedError: 선택한 파일의 전체 이름이 읽기 전후에 달라진 경우.
    - AmbiguousWorkbookError: 선택한 프로세스에 같은 이름의 후보가 여러 개 있는 경우.
    - SheetNotFoundError: 선택한 파일에 해당 시트가 없는 경우.
    - ExcelSheetReadError: 시트 정보·셀 값·병합 범위·파일명을 읽지 못한 경우.
    """
    if type(app_pid) is not int or app_pid < 1:
        raise ValueError("선택 목록의 Excel 프로세스 번호를 전달해야 합니다.")
    if not isinstance(full_name, str) or not full_name.strip():
        raise ValueError("선택 목록의 전체 파일명을 전달해야 합니다.")
    book = _find_open_book(workbook_name, app_pid, full_name)
    try:
        # 실제 목록에서 시트가 없을 때만 '시트 없음'으로 판단한다. COM 실패와 구분한다.
        sheet = next((sheet for sheet in book.sheets if sheet.name == sheet_name), None)
        if sheet is None:
            raise SheetNotFoundError(f"'{workbook_name}'에 '{sheet_name}' 시트가 없습니다.")
        file_name = book.name
        if not file_name or not file_name.strip():
            raise ExcelSheetReadError("원본 파일명을 확인할 수 없습니다. 파일을 다시 선택하세요.")
        used = sheet.used_range
        # 기본 변환은 Excel 오류도 None으로 만든다. 빈 셀과 오류 셀을 구분해 검토에 전달한다.
        raw = used.options(ndim=2, err_to_str=True).value
        values = [list(row) for row in raw] if raw else []
        first_row, first_col = used.row, used.column
        merged = _find_merged_ranges(used.api) if values else []
        _check_workbook_selection(book, workbook_name, full_name)
        return SheetData(
            workbook_name=file_name,
            sheet_name=sheet.name,
            first_row=first_row,
            first_col=first_col,
            values=values,
            merged_ranges=merged,
            app_pid=book.app.pid,
        )
    except ExcelReadError:
        raise
    except Exception as exc:  # 시트 선택 후 Excel이 닫히거나 COM이 응답하지 않는 경우 등
        raise ExcelSheetReadError(
            f"'{workbook_name}'의 '{sheet_name}' 시트를 읽지 못했습니다. "
            "Excel과 선택한 파일·시트를 확인한 뒤 다시 시도하세요."
        ) from exc


def _is_protected_view_error(exc: BaseException) -> bool:
    """예외 연결에서 제한된 보기 창의 연결 실패 근거를 확인한다.

    xlwings가 ProtectedViewWindow의 Application 속성을 조회할 때 발생한
    AttributeError는 마지막 COM 오류의 context에 남는다. COM 오류 번호만으로는
    다른 속성 조회 실패와 구분할 수 없어 원인 예외의 객체 이름을 확인한다.

    param
    - exc: Excel 연결 중 발생한 예외.

    return
    - 원인 또는 문맥 예외에 ProtectedViewWindow가 명시되어 있으면 True.
    """
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if "ProtectedViewWindow" in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False


def _connection_error(exc: Exception) -> ExcelConnectionError:
    """Excel 연결 오류를 사용자에게 알릴 오류 형식으로 바꾼다.

    원인 예외를 보존해 제한된 보기와 일반 연결 실패를 구분한다.

    param
    - exc: 창·통합문서·메타데이터 조회 중 발생한 원인 예외.

    return
    - 원인 예외가 연결된 ExcelConnectionError 또는 ExcelProtectedViewError.
    """
    if _is_protected_view_error(exc):
        error = ExcelProtectedViewError(
            "제한된 보기(Protected View) 창에 연결하지 못했습니다. "
            "해당 창의 보안 안내를 확인하고, 신뢰할 수 있는 파일인 경우 "
            "조직의 정책에 따라 직접 '편집 사용'을 선택한 뒤 다시 시도하세요."
        )
    else:
        error = ExcelConnectionError(
            "Excel에 연결하지 못했습니다. Excel이 설치되어 있고 응답하는지 확인하세요."
        )
    error.__cause__ = exc
    return error


def _check_workbook_selection(book: xw.Book, workbook_name: str, full_name: str) -> None:
    """현재 파일이 선택 당시의 이름·전체 경로와 일치하는지 확인한다.

    Windows 경로의 대소문자와 구분자 차이는 같은 경로로 처리한다.
    파일 시스템을 열거나 경로를 현재 작업 폴더에 맞춰 추정하지 않는다.

    param
    - book: 선택한 프로세스에서 찾은 열린 통합문서.
    - workbook_name: 선택 당시의 파일명.
    - full_name: 선택 당시 Excel이 제공한 전체 이름.

    raises
    - WorkbookSelectionChangedError: 현재 파일명이나 전체 이름이 선택 당시와 다른 경우.
    """
    if book.name != workbook_name or normcase(normpath(book.fullname)) != normcase(normpath(full_name)):
        raise WorkbookSelectionChangedError("선택한 Excel 파일의 이름이나 경로가 바뀌었습니다. 파일을 다시 선택하세요.")


def _find_open_book(workbook_name: str, app_pid: int, full_name: str) -> xw.Book:
    """선택한 프로세스에서 파일을 찾고 전체 이름을 검증한다.

    param
    - workbook_name: 검색할 정확한 파일명.
    - app_pid: 조회할 Excel 프로세스 번호. 다른 프로세스의 통합문서는 조회하지 않는다.
    - full_name: 선택 당시의 전체 이름.

    return
    - 선택 조건에 정확히 대응하는 열린 통합문서.

    raises
    - ExcelNotRunningError: 실행 중인 Excel이 없는 경우.
    - ExcelProtectedViewError: 선택한 프로세스의 제한된 보기 창 때문에 연결이 실패한 경우.
    - ExcelConnectionError: 목록이나 파일명·프로세스 번호를 읽지 못한 경우.
    - WorkbookNotFoundError: 선택 조건에 맞는 파일이 없는 경우.
    - WorkbookSelectionChangedError: 찾은 파일의 전체 이름이 선택 당시와 다른 경우.
    - AmbiguousWorkbookError: 선택 조건에 맞는 파일이 여러 개 있는 경우.
    """
    try:
        try:
            app = xw.apps[app_pid]
        except KeyError:
            if len(xw.apps) == 0:
                raise ExcelNotRunningError(NOT_RUNNING_MESSAGE) from None
            raise WorkbookNotFoundError("선택한 Excel 창이 닫혔습니다. 파일을 다시 선택하세요.") from None
        matches = [
            book for book in app.books if book.name == workbook_name
        ]
        if not matches:
            raise WorkbookNotFoundError(f"열려 있는 통합문서 중 '{workbook_name}'이(가) 없습니다. 파일을 다시 선택하세요.")
        if len(matches) > 1:
            raise AmbiguousWorkbookError(f"'{workbook_name}'의 선택 대상을 구분할 수 없습니다. 파일을 다시 선택하세요.")
        _check_workbook_selection(matches[0], workbook_name, full_name)
        return matches[0]
    except ExcelReadError:
        raise
    except Exception as exc:
        raise _connection_error(exc)


def _find_merged_ranges(com_range: Any) -> list[MergedRange]:
    """COM Range에서 병합 범위를 찾는다.

    셀을 하나씩 확인하면 COM 호출이 셀 수만큼 생겨 느리다. 그래서 열마다
    ``MergeCells``로 병합이 있는지 먼저 묻고, 병합이 있는 열만 위아래로 반씩
    나눠 가며 병합 범위를 찾는다. 찾은 범위의 아래쪽은 건너뛴다.

    param
    - com_range: 읽은 사용 영역에 대응하는 Windows COM Range.

    return
    - 중복을 제거하고 위쪽 행·왼쪽 열 순서로 정렬한 병합 범위.
    """
    sheet = com_range.Worksheet
    first_row, first_col = com_range.Row, com_range.Column
    last_row = first_row + com_range.Rows.Count - 1
    last_col = first_col + com_range.Columns.Count - 1
    found: dict[tuple[int, int, int, int], MergedRange] = {}

    def merge_state(col: int, top: int, bottom: int) -> bool | None:
        """한 열 구간의 병합 상태를 조회한다.

        param
        - col: 조회할 원본 열 번호.
        - top: 구간의 시작 행 번호.
        - bottom: 구간의 끝 행 번호.

        return
        - 전체 병합이면 True, 병합이 없으면 False, 섞여 있으면 None.
        """
        # True: 모두 병합, False: 병합 없음, None: 섞임
        return sheet.Range(sheet.Cells(top, col), sheet.Cells(bottom, col)).MergeCells

    def scan_column(col: int, top: int, bottom: int) -> None:
        """병합이 있는 열 구간만 나눠 원본 범위를 수집한다.

        param
        - col: 탐색할 원본 열 번호.
        - top: 탐색 구간의 시작 행 번호.
        - bottom: 탐색 구간의 끝 행 번호.
        """
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

def open_source_cell(path: str, sheet: str, cells: str) -> None:
    """사용자가 고른 파일의 기록된 셀을 표시한다. 새 파일은 읽기 전용으로 연다."""
    import re
    from pathlib import Path
    import xlwings as xw

    addresses = [cell.strip().upper() for cell in cells.split(",")]
    if not all(re.fullmatch(r"\$?[A-Z]{1,3}\$?[1-9][0-9]{0,6}", cell) for cell in addresses):
        raise ValueError("원본 셀 주소를 확인하세요.")
    target = Path(path).resolve(strict=True)
    book = next((b for app in xw.apps for b in app.books if Path(b.fullname) == target), None)
    created = None
    try:
        if book is None:
            created = xw.App(visible=False, add_book=False)
            security = created.api.AutomationSecurity
            try:
                created.api.AutomationSecurity = 3
                book = created.books.open(str(target), read_only=True, update_links=False)
            finally:
                created.api.AutomationSecurity = security
        tab = book.sheets[sheet]
        tab.activate()
        book.app.api.Goto(tab.api.Range(",".join(addresses)), True)
        book.app.visible = True
        book.app.activate(steal_focus=True)
    except Exception:
        if created is not None:
            created.quit()
        raise
