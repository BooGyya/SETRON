"""Excel에 원문을 수식이나 잘린 문자열로 바꾸지 않고 기록한다."""

from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.cell.rich_text import CellRichText
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from itertools import chain

MAX_ROWS = 1_048_576
MAX_CELL_CHARACTERS = 32_767
OVERFLOW_SHEET = "긴값_분할"
OVERFLOW_MARKER = "긴 값 참조: "
REPORT_OVERFLOW_COLUMNS = (
    "reference_id", "sheet_name", "row_number", "column_name", "part_index", "part_count", "text",
)


def write_literal_cell(cell, value):
    """문자열은 빈 값·수식·오류 표기도 그대로 텍스트 셀로 보존한다."""
    if isinstance(value, str) and len(value) > MAX_CELL_CHARACTERS:
        raise ValueError(f"{cell.parent.title}!{cell.coordinate}: 긴 문자열을 먼저 분할해야 합니다.")
    cell.value = CellRichText([""]) if isinstance(value, str) and value == "" else value
    if isinstance(value, str):
        cell.data_type = "s"
        cell.number_format = "@"


def text_parts(value):
    """셀 한도 안의 순서 있는 조각으로 나눈다. 조각 자체는 변경하지 않는다."""
    return tuple(value[start:start + MAX_CELL_CHARACTERS]
                 for start in range(0, len(value), MAX_CELL_CHARACTERS))


class ReportTextWriter:
    """현황 표의 긴 값을 원래 좌표와 순서가 있는 마지막 시트에 보존한다."""

    def __init__(self, book):
        self.book = book
        self.parts = []

    def write(self, cell, value, column_name):
        if isinstance(value, str):
            location = f"{cell.parent.title}!{cell.coordinate}"
            if ILLEGAL_CHARACTERS_RE.search(value):
                raise ValueError(f"{location}: Excel이 보존할 수 없는 제어 문자가 있습니다.")
            value.encode("utf-8")
            if len(value) > MAX_CELL_CHARACTERS:
                chunks = text_parts(value)
                if len(self.parts) + len(chunks) + 1 > MAX_ROWS:
                    raise ValueError(f"{OVERFLOW_SHEET}: Excel의 행 한도를 초과합니다.")
                self.parts.extend((location, cell.parent.title, cell.row, column_name,
                                   number, len(chunks), text)
                                  for number, text in enumerate(chunks, 1))
                value = OVERFLOW_MARKER + location
        write_literal_cell(cell, value)

    def finish(self):
        """기존 표·차트 순서를 유지하고 동명 시트가 있어도 덮어쓰지 않는다."""
        if not self.parts:
            return
        sheet = self.book.create_sheet(OVERFLOW_SHEET)
        sheet.freeze_panes = "A2"
        sheet.sheet_view.showGridLines = False
        for number, row in enumerate(chain((REPORT_OVERFLOW_COLUMNS,), self.parts), 1):
            for column, value in enumerate(row, 1):
                cell = sheet.cell(number, column)
                write_literal_cell(cell, value)
                if number == 1:
                    cell.font = Font(bold=True)
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(REPORT_OVERFLOW_COLUMNS))}{len(self.parts) + 1}"
        for column in range(1, len(REPORT_OVERFLOW_COLUMNS) + 1):
            sheet.column_dimensions[get_column_letter(column)].width = 24
