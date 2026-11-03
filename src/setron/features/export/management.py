"""저장된 현재 관리값과 보관 원본을 13개 관리 테이블의 XLSX 표로 내보낸다.

현황 보고서의 집계·차트 출력과 별개로, 같은 DB 트랜잭션에서 읽은 관리값을 사용한다.
JSON은 정렬한 키를 가진 JSON 문자열로, 일반 문자열은 수식이 아닌 문자열 셀로 기록한다.
셀 한도보다 긴 값은 별도 시트의 번호 붙은 조각으로 모두 보존한다.
Excel이 보존할 수 없는 값 형식과 제어 문자는 오류로 안내한다.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from itertools import chain
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from sqlalchemy import select

from setron.features.export.schemas import ExportResult, ExportStatus
from setron.features.export.xlsx_cells import (
    MAX_CELL_CHARACTERS, MAX_ROWS, OVERFLOW_MARKER, OVERFLOW_SHEET, text_parts, write_literal_cell,
)
from setron.features.storage.validation import (
    JSON_COLUMNS,
    TABLE_NAMES,
    StorageValidationError,
    decode_record,
    json_text,
)
from setron.infrastructure.database import DatabaseError

MAX_COLUMNS = 16_384
OVERFLOW_COLUMNS = (
    "reference_id", "table_name", "row_number", "column_name", "part_index", "part_count", "text",
)


@dataclass(frozen=True)
class ManagementTable:
    """DB 컬럼 순서와 기본키 순서를 보존한 관리 테이블 읽기 결과.

    ``rows``의 값은 문자열·날짜·정수·JSON 객체 등 앱 형식이며 원본 DB 행과 분리된다.
    """

    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


def read_management_data(storage) -> tuple[ManagementTable, ...]:
    """하나의 읽기 트랜잭션에서 모든 관리 테이블의 최신 저장값을 조회한다.

    빈 테이블도 컬럼과 함께 반환한다. 원본과 수정값을 합치거나 DB를 변경하지 않는다.
    접속·조회 오류와 저장된 JSON·문자열 복원 오류는 호출자에게 전달한다.
    """
    result = []
    with storage.transaction() as tx:
        for name in TABLE_NAMES:
            table = tx.repository.table(name)
            columns = tuple(table.c.keys())
            values = tx.session.execute(select(table).order_by(*table.primary_key.columns)).mappings()
            decoded = (decode_record(name, dict(row)) for row in values)
            result.append(ManagementTable(name, columns, tuple(
                tuple(row[column] for column in columns) for row in decoded
            )))
    return tuple(result)


def build_management_workbook(tables: tuple[ManagementTable, ...]) -> Workbook:
    """관리 테이블을 각각 헤더·필터가 있는 시트로 만든다.

    32,767자를 넘는 문자열·JSON은 기본 셀에 ``긴 값 참조: table!A2``로 표시하고
    마지막 ``긴값_분할`` 시트에 같은 reference_id·테이블·행·컬럼과 분할 번호를 기록한다.
    part_index 순서로 text를 이어 붙이면 전체 값을 복원한다. 실제 원문이 참조 문구와 같아도
    분할 시트에 해당 좌표의 참조가 없으면 일반 문자열이다. 모든 시트·분할 값은 표시한다.
    """
    prepared = _prepare_tables(tables)
    book = Workbook()
    book.remove(book.active)
    # 날짜를 문자열로 바꾸거나 Excel 일련번호로 해석하지 않고 ISO 날짜 셀로 저장한다.
    book.iso_dates = True
    for name, columns, rows in prepared:
        sheet = book.create_sheet(name)
        sheet.freeze_panes = "A2"
        sheet.sheet_view.showGridLines = False
        for row_number, row in enumerate(chain((columns,), rows), 1):
            for column_number, value in enumerate(row, 1):
                cell = sheet.cell(row_number, column_number)
                write_literal_cell(cell, value)
                if row_number == 1:
                    cell.font = Font(bold=True)
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(rows) + 1}"
        for column_number in range(1, len(columns) + 1):
            sheet.column_dimensions[get_column_letter(column_number)].width = 24
    return book


def save_management_data(storage, path: str | Path) -> ExportResult:
    """DB의 현재 관리 자료를 XLSX로 저장한다. 실패하면 기존 목적 파일을 유지한다.

    같은 폴더의 고유 임시파일에 기록을 마친 뒤 교체하며 이번 호출의 임시파일만 정리한다.
    """
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        return ExportResult(ExportStatus.FAILED, path, "파일 이름은 .xlsx로 끝나야 합니다.")
    temporary = None
    book = None
    try:
        book = build_management_workbook(read_management_data(storage))
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False,
        ) as file:
            temporary = Path(file.name)
        book.save(temporary)
        os.replace(temporary, path)
    except PermissionError:
        return ExportResult(ExportStatus.FAILED, path,
                            "파일을 저장할 수 없습니다. 파일이 열려 있는지, 폴더 쓰기 권한이 있는지 확인해 주세요.")
    except DatabaseError as error:
        return ExportResult(ExportStatus.FAILED, path, f"DB 자료를 읽지 못했습니다: {error}")
    except (ValueError, TypeError) as error:
        return ExportResult(ExportStatus.FAILED, path, f"관리 자료를 XLSX로 보존할 수 없습니다: {error}")
    except OSError as error:
        return ExportResult(ExportStatus.FAILED, path, f"파일을 저장하지 못했습니다: {error.strerror or error}")
    finally:
        if book is not None:
            book.close()
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return ExportResult(ExportStatus.SAVED, path, f"관리 테이블 {len(TABLE_NAMES)}개")


def _prepare_tables(tables):
    """표 구조·값을 검사하고 JSON 정규화와 긴 값의 명시적 분할 참조를 준비한다."""
    if not isinstance(tables, tuple) or not tables:
        raise StorageValidationError("내보낼 관리 테이블 튜플이 필요합니다.")
    names, result, overflow = set(), [], []
    for table in tables:
        if not isinstance(table, ManagementTable) or table.name not in TABLE_NAMES:
            raise StorageValidationError("지원하지 않는 관리 테이블입니다.")
        if table.name in names:
            raise StorageValidationError(f"{table.name}: 관리 테이블이 중복됐습니다.")
        names.add(table.name)
        if (not isinstance(table.columns, tuple) or not table.columns
                or any(not isinstance(name, str) or not name for name in table.columns)
                or len(set(table.columns)) != len(table.columns)):
            raise StorageValidationError(f"{table.name}: 중복 없는 컬럼명 튜플이 필요합니다.")
        if not isinstance(table.rows, tuple):
            raise StorageValidationError(f"{table.name}: 행 튜플이 필요합니다.")
        if len(table.columns) > MAX_COLUMNS or len(table.rows) + 1 > MAX_ROWS:
            raise StorageValidationError(f"{table.name}: Excel의 행·열 한도를 초과합니다.")
        columns = tuple(_cell_value(name, f"{table.name} 헤더") for name in table.columns)
        rows = []
        for number, row in enumerate(table.rows, 2):
            if not isinstance(row, tuple) or len(row) != len(table.columns):
                raise StorageValidationError(f"{table.name} {number}행: 컬럼 수와 행의 값 개수가 다릅니다.")
            values = []
            for column_number, (column, value) in enumerate(zip(table.columns, row), 1):
                rule = JSON_COLUMNS.get(table.name, {}).get(column)
                if rule is not None:
                    # 기존 저장 규칙으로 JSON 키·구조·NaN 등을 확인하고 키 순서만 정규화한다.
                    encoded = json_text(table.name, column, value, rule)
                    value = None if encoded is None else json.dumps(
                        value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"),
                    )
                value = _cell_value(value, f"{table.name}.{column} {number}행", allow_long=True)
                if isinstance(value, str) and len(value) > MAX_CELL_CHARACTERS:
                    reference_id = f"{table.name}!{get_column_letter(column_number)}{number}"
                    chunks = text_parts(value)
                    if len(overflow) + len(chunks) + 1 > MAX_ROWS:
                        raise StorageValidationError(f"{OVERFLOW_SHEET}: Excel의 행 한도를 초과합니다.")
                    overflow.extend((reference_id, table.name, number, column, part, len(chunks), text)
                                    for part, text in enumerate(chunks, 1))
                    value = OVERFLOW_MARKER + reference_id
                values.append(value)
            rows.append(tuple(values))
        result.append((table.name, columns, tuple(rows)))
    if overflow:
        result.append((OVERFLOW_SHEET, OVERFLOW_COLUMNS, tuple(overflow)))
    return tuple(result)


def _cell_value(value, location, *, allow_long=False):
    """Excel의 자동 수식 처리·문자열 잘림·숫자 정밀도 손실을 허용하지 않는다."""
    if isinstance(value, str):
        if not allow_long and len(value) > MAX_CELL_CHARACTERS:
            raise StorageValidationError(f"{location}: 셀의 {MAX_CELL_CHARACTERS:,}자 한도를 초과합니다.")
        if ILLEGAL_CHARACTERS_RE.search(value):
            raise StorageValidationError(f"{location}: Excel이 보존할 수 없는 제어 문자가 있습니다.")
        value.encode("utf-8")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.microsecond % 1000:
            raise StorageValidationError(f"{location}: Excel이 보존할 수 없는 시간대 또는 시간 정밀도입니다.")
        return value
    if value is None or isinstance(value, (date, bool)):
        return value
    if isinstance(value, int):
        if abs(value) > 999_999_999_999_999:
            raise StorageValidationError(f"{location}: Excel의 정수 정밀도 한도를 초과합니다.")
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise StorageValidationError(f"{location}: XLSX로 보존할 수 없는 값 형식입니다.")
