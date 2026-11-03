"""화면과 검증 서비스가 동일한 읽기 원본을 추출 입력으로 전달한다."""

from setron.infrastructure.excel_reader import MergedRange, SheetData, cell_address


def sheet_from_snapshot(workbook_name, snapshot):
    """읽은 범위와 병합 위치를 빈칸 값까지 보존한 추출 입력으로 복원한다.

    param
    - workbook_name, snapshot: 읽은 파일명과 불변 시트 스냅샷.

    return
    - 값을 추정하거나 빈칸을 다른 값으로 채우지 않은 SheetData.
    """
    values = [[snapshot.cells.get(cell_address(snapshot.first_row + i, snapshot.first_col + j))
               for j in range(snapshot.col_count)] for i in range(snapshot.row_count)]
    merged = []
    for area in snapshot.merged:
        from .service import parse_address
        (top, left), (bottom, right) = (parse_address(part) for part in area.split(":"))
        merged.append(MergedRange(top, left, bottom, right))
    return SheetData(workbook_name, snapshot.sheet_name, snapshot.first_row, snapshot.first_col, values, merged)
