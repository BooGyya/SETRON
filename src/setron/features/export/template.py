"""확인한 Excel 양식의 지정 영역만 현재 관리값으로 채운다.

양식은 읽기 전용이며, OOXML 패키지의 대상 셀만 바꿔 별도 파일로 저장한다.
그림·관계·서식과 매핑하지 않은 셀·수식은 패키지 원문으로 유지한다.
"""

from __future__ import annotations

from collections import OrderedDict
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from io import BytesIO
import math
from functools import cached_property
import os
from pathlib import Path
import posixpath
import re
from tempfile import NamedTemporaryFile
from zipfile import BadZipFile, ZipFile

from lxml import etree
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles.numbers import BUILTIN_FORMATS, is_date_format
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900, to_excel

from setron.features.export.management import HEADERS, read_management_records
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.features.export.xlsx_cells import MAX_CELL_CHARACTERS, MAX_ROWS
from setron.infrastructure.database import DatabaseError

MAX_COLUMNS = 16_384
MAX_TEMPLATE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_XML_BYTES = 50 * 1024 * 1024
MAX_TEMPLATE_CELLS = 250_000
MAX_OUTPUT_CELLS = 500_000
MAX_INSPECT_COLUMNS = 256
MAX_INSPECT_ROWS = 1_000
KINDS = {"equipment": "설비현황표", "release": "릴리즈노트"}
_CELL = re.compile(r"^([A-Z]{1,3})([1-9][0-9]*)$")


@dataclass(frozen=True)
class TemplateColumn:
    column: int
    field: str
    qualifier: str = ""


@dataclass(frozen=True)
class TemplateRow:
    row: int
    field: str
    qualifier: str = ""


@dataclass(frozen=True)
class TemplateConfig:
    path: Path
    sheet_name: str
    kind: str
    header_row: int
    start_row: int
    end_row: int
    mode: str
    columns: tuple[TemplateColumn, ...]
    fingerprint: str
    site: str | None = None
    orientation: str = "rows"
    header_column: int = 1
    start_column: int = 2
    end_column: int = 2
    rows: tuple[TemplateRow, ...] = ()


def template_mappings(config):
    """양식에서 항목명이 가로면 열 연결, 세로면 행 연결을 반환한다."""
    return config.columns if config.orientation == "rows" else config.rows


def _field_position(mapping, orientation):
    return mapping.column if orientation == "rows" else mapping.row


def _record_bounds(config):
    return ((config.start_row, config.end_row) if config.orientation == "rows"
            else (config.start_column, config.end_column))


def _target_coordinate(config, record_position, mapping):
    return ((record_position, mapping.column) if config.orientation == "rows"
            else (mapping.row, record_position))


def _xml(raw):
    if len(raw) > MAX_XML_BYTES or b"<!DOCTYPE" in raw.upper():
        raise ValueError("양식 XML의 크기 또는 구조를 지원하지 않습니다.")
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        return etree.parse(BytesIO(raw), parser)
    except etree.XMLSyntaxError:
        raise ValueError("양식의 Excel XML 구조가 올바르지 않습니다.") from None


def _tag(root, name):
    namespace = etree.QName(root).namespace
    return f"{{{namespace}}}{name}" if namespace else name


def _part_path(base, target):
    path = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                              else posixpath.join(posixpath.dirname(base), target))
    if path.startswith("../") or path == "..":
        raise ValueError("양식의 내부 파일 경로가 올바르지 않습니다.")
    return path


@dataclass
class _Template:
    raw: bytes
    fingerprint: str
    workbook: object
    sheets: dict[str, str]
    strings: tuple[str, ...]
    date_styles: frozenset[int]
    epoch: datetime

    def part(self, path):
        with ZipFile(BytesIO(self.raw)) as archive:
            return archive.read(path)


def _load(path, fingerprint=None):
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise ValueError("맞춤 양식은 .xlsx 파일을 선택해 주세요.")
    if path.stat().st_size > MAX_TEMPLATE_BYTES:
        raise ValueError("양식 파일이 너무 큽니다. 50MB 이하의 .xlsx를 선택해 주세요.")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if fingerprint is not None and digest != fingerprint:
        raise ValueError("검토 이후 양식 파일이 바뀌었습니다. 양식을 다시 불러와 매핑과 미리보기를 확인해 주세요.")
    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if (len(entries) > 10_000 or len(names) != len(entries)
                    or sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED_BYTES
                    or any(entry.flag_bits & 1 for entry in entries)):
                raise ValueError("양식의 압축 크기·파일 수 또는 암호화 구조를 지원하지 않습니다.")
            workbook = _xml(archive.read("xl/workbook.xml"))
            relationships = _xml(archive.read("xl/_rels/workbook.xml.rels"))
            parts, sheets = {}, {}
            for relation in relationships.getroot():
                if relation.get("TargetMode") != "External":
                    parts[relation.get("Id")] = (_part_path("xl/workbook.xml", relation.get("Target", "")),
                                                 relation.get("Type", ""))
            root = workbook.getroot()
            for sheet in root.findall(f"{_tag(root, 'sheets')}/{_tag(root, 'sheet')}"):
                relationship = next((value for name, value in sheet.attrib.items()
                                     if etree.QName(name).localname == "id"), None)
                part, kind = parts.get(relationship, (None, ""))
                if part is not None and kind.endswith("/worksheet"):
                    sheets[sheet.get("name")] = part
            if not sheets:
                raise ValueError("양식에 데이터를 채울 워크시트가 없습니다.")
            shared = next((part for part, kind in parts.values() if kind.endswith("/sharedStrings")), None)
            strings = ()
            if shared:
                shared_root = _xml(archive.read(shared)).getroot()
                strings = tuple(_text_runs(item) for item in shared_root)
            styles = next((part for part, kind in parts.values() if kind.endswith("/styles")), None)
            date_styles = set()
            if styles:
                style_root = _xml(archive.read(styles)).getroot()
                formats = dict(BUILTIN_FORMATS)
                custom = style_root.find(_tag(style_root, "numFmts"))
                if custom is not None:
                    formats.update({int(item.get("numFmtId")): item.get("formatCode", "") for item in custom})
                cell_formats = style_root.find(_tag(style_root, "cellXfs"))
                if cell_formats is not None:
                    date_styles = {index for index, item in enumerate(cell_formats)
                                   if is_date_format(formats.get(int(item.get("numFmtId", "0")), ""))}
            properties = root.find(_tag(root, "workbookPr"))
            epoch = (CALENDAR_MAC_1904 if properties is not None
                     and properties.get("date1904") in {"1", "true"} else CALENDAR_WINDOWS_1900)
            return _Template(raw, digest, workbook, sheets, strings, frozenset(date_styles), epoch)
    except (BadZipFile, KeyError, RuntimeError, TypeError, OverflowError):
        raise ValueError("지원하는 Excel 양식을 읽을 수 없습니다. 정상적인 .xlsx 파일인지 확인해 주세요.") from None


def _text_runs(element):
    return "".join(node.text or "" for node in element.iter()
                   if isinstance(node.tag, str) and etree.QName(node).localname == "t"
                   and not any(etree.QName(parent).localname == "rPh" for parent in node.iterancestors()))


def _coordinates(reference):
    match = _CELL.fullmatch(reference or "")
    if match is None:
        raise ValueError("양식의 셀 주소가 올바르지 않습니다.")
    column = 0
    for letter in match[1]:
        column = column * 26 + ord(letter) - ord("A") + 1
    row = int(match[2])
    if column > MAX_COLUMNS or row > MAX_ROWS:
        raise ValueError("양식의 셀 주소가 Excel 범위를 벗어났습니다.")
    return row, column


@dataclass
class _Sheet:
    tree: object
    cells: dict[tuple[int, int], object]
    merges: tuple[tuple[int, int, int, int], ...]
    max_row: int
    max_column: int

    @cached_property
    def merge_index(self):
        ranges = sorted(self.merges, key=lambda value: value[1])
        tops, bottoms, maximum = [], [], 0
        for _, top, _, bottom in ranges:
            tops.append(top)
            maximum = max(maximum, bottom)
            bottoms.append(maximum)
        return ranges, tops, bottoms

    def anchor(self, row, column):
        ranges, tops, bottoms = self.merge_index
        index = bisect_right(tops, row) - 1
        while index >= 0 and bottoms[index] >= row:
            left, top, right, bottom = ranges[index]
            if row <= bottom and left <= column <= right:
                return top, left
            index -= 1
        return row, column


def _bounds(reference):
    try:
        bounds = range_boundaries(reference)
    except (TypeError, ValueError):
        raise ValueError("양식의 셀 범위가 올바르지 않습니다.") from None
    if (any(type(value) is not int for value in bounds) or
            not 1 <= bounds[0] <= bounds[2] <= MAX_COLUMNS or
            not 1 <= bounds[1] <= bounds[3] <= MAX_ROWS):
        raise ValueError("양식의 셀 범위가 Excel 한도를 벗어났습니다.")
    return bounds


def _sheet(template, name):
    if name not in template.sheets:
        raise ValueError("선택한 시트를 양식에서 찾을 수 없습니다. 양식을 다시 확인해 주세요.")
    tree = _xml(template.part(template.sheets[name]))
    root = tree.getroot()
    data = root.find(_tag(root, "sheetData"))
    cells = {}
    if data is not None:
        for row in data:
            if not isinstance(row.tag, str) or etree.QName(row).localname != "row":
                continue
            try:
                row_number = int(row.get("r"))
            except (TypeError, ValueError):
                raise ValueError("양식의 행 주소를 확인할 수 없습니다.") from None
            for cell in row:
                if isinstance(cell.tag, str) and etree.QName(cell).localname == "c":
                    coordinate = _coordinates(cell.get("r"))
                    if coordinate[0] != row_number:
                        raise ValueError("양식의 행과 셀 주소가 일치하지 않습니다.")
                    if coordinate in cells:
                        raise ValueError("양식에 같은 셀 주소가 중복됩니다.")
                    cells[coordinate] = cell
                    if len(cells) > MAX_TEMPLATE_CELLS:
                        raise ValueError("양식에 저장된 셀이 너무 많습니다. 250,000셀 이하의 양식을 사용해 주세요.")
    merges = root.find(_tag(root, "mergeCells"))
    ranges = tuple(_bounds(item.get("ref")) for item in merges) if merges is not None else ()
    if len(ranges) > 10_000:
        raise ValueError("양식의 병합 영역이 너무 많습니다. 사용할 시트의 병합 영역을 줄여 주세요.")
    maximum_row = max((row for row, _ in cells), default=1)
    maximum_column = max((column for _, column in cells), default=1)
    dimension = root.find(_tag(root, "dimension"))
    if dimension is not None:
        _, _, right, bottom = _bounds(dimension.get("ref", "A1"))
        maximum_row, maximum_column = max(maximum_row, bottom), max(maximum_column, right)
    if maximum_row > MAX_ROWS or maximum_column > MAX_COLUMNS:
        raise ValueError("양식의 데이터 범위가 Excel 한도를 넘었습니다.")
    return _Sheet(tree, cells, ranges, maximum_row, maximum_column)


def _cell_text(template, cell):
    if cell is None:
        return ""
    value = cell.find(_tag(cell, "v"))
    if cell.get("t") == "inlineStr":
        return _text_runs(cell)
    if value is None or value.text is None:
        return ""
    if cell.get("t") == "s":
        try:
            index = int(value.text)
            if index < 0:
                raise ValueError
            return template.strings[index]
        except (ValueError, IndexError):
            raise ValueError("양식의 공유 문자열 참조가 올바르지 않습니다.") from None
    return value.text


def _normal(value):
    return re.sub(r"[^0-9a-z가-힣]", "", value.casefold())


_ALIASES = {
    "설비 호기": ("equipmentcode", "equipmentid", "eqpid", "eqpno", "설비번호", "호기", "설비id"),
    "기준일": ("basedate", "기준날짜", "측정일", "date"),
    "사업장": ("site",), "라인": ("line",),
    "모델명(원문)": ("model", "modelname", "모델", "모델명", "장비모델"),
    "PM": ("pmname", "pm명"),
    "SW 버전(원문)": ("softwareversion", "swversion", "sw버전", "소프트웨어버전"),
    "공정": ("process", "processname", "공정명"),
    "생산 영역": ("area", "productionarea", "memoryfoundry", "생산분류"),
    "설비군": ("equipmentgroup", "group"),
    "Item 번호": ("itemno", "no", "번호", "항목번호"),
    "내용": ("content", "update내용", "변경내용", "설명", "description"),
    "Jira 코드": ("jira", "jiracode", "이슈코드"),
    "버전(원문)": ("releaseversion", "릴리즈버전"),
    "출시일": ("releasedate", "릴리즈일", "배포일"),
    "적용 상태": ("itemstate", "적용여부", "적용상태"),
}
_FIELDS = {_normal(header): header for headers in HEADERS.values() for header in headers}
_FIELDS.update({_normal(alias): field for field, aliases in _ALIASES.items() for alias in aliases})


def _suggest(title, sheet_name):
    normalized = _normal(title)
    if normalized in {"버전", "version"}:
        return ("버전(원문)" if any(word in sheet_name.casefold() for word in ("release", "릴리즈"))
                else "SW 버전(원문)"), ""
    field = _FIELDS.get(normalized)
    if field:
        return field, ""
    pm = re.search(r"\bPM\s*[-_ ]?([0-9]+)\b", title, re.I)
    if pm and ("버전" in title or re.search(r"version|\bsw\b", title, re.I) or normalized == _normal(pm[0])):
        return "SW 버전(원문)", "PM" + pm[1]
    if re.fullmatch(r"[vV][0-9][0-9A-Za-z._ -]*", title.strip()):
        return "적용 상태", title.strip()
    if " / " in title:
        return _suggest(title.rsplit(" / ", 1)[-1], sheet_name)
    return None, ""


def _header_title(template, sheet, row, column):
    anchor = sheet.anchor(row, column)
    own = _cell_text(template, sheet.cells.get(anchor)).strip()
    parents = []
    for left, top, right, bottom in sheet.merges:
        if left <= column <= right and bottom == row - 1:
            value = _cell_text(template, sheet.cells.get((top, left))).strip()
            if value and value != own:
                parents.append(value)
    return " / ".join((*parents, own)) if parents else own


def _row_title(template, sheet, row, column):
    own = _cell_text(template, sheet.cells.get(sheet.anchor(row, column))).strip()
    parents = []
    for left, top, right, bottom in sheet.merges:
        if top <= row <= bottom and right == column - 1:
            value = _cell_text(template, sheet.cells.get((top, left))).strip()
            if value and value != own:
                parents.append(value)
    return " / ".join((*parents, own)) if parents else own


def _header_row(template, sheet):
    scores = []
    for row in sorted({row for row, _ in sheet.cells if row <= 100}):
        values = [_cell_text(template, cell).strip() for (number, _), cell in sheet.cells.items() if number == row]
        nonempty = [value for value in values if value]
        score = sum(5 for value in nonempty if _suggest(value, "")[0] is not None) + len(nonempty)
        scores.append((score, -row, row))
    return max(scores)[2] if scores else 1


def _header_column(template, sheet):
    scores = []
    for column in sorted({column for _, column in sheet.cells if column <= 100}):
        values = [_cell_text(template, cell).strip() for (_, number), cell in sheet.cells.items() if number == column]
        nonempty = [value for value in values if value]
        score = sum(5 for value in nonempty if _suggest(value, "")[0] is not None) + len(nonempty)
        scores.append((score, -column, column))
    return max(scores)[2] if scores else 1


def inspect_template(path):
    template = _load(path)
    sheets = []
    for name in template.sheets:
        sheet = _sheet(template, name)
        sheets.append({"name": name, "max_row": sheet.max_row, "max_column": sheet.max_column,
                       "header_row": _header_row(template, sheet),
                       "header_column": _header_column(template, sheet)})
    return {"path": str(Path(path)), "fingerprint": template.fingerprint, "sheets": sheets}


def inspect_template_sheet(path, sheet_name, header_row=1, *, orientation="rows", header_column=1):
    if orientation not in {"rows", "columns"}:
        raise ValueError("항목명의 가로·세로 방향을 선택해 주세요.")
    if orientation == "rows" and (type(header_row) is not int or not 1 <= header_row <= MAX_ROWS):
        raise ValueError("열 이름이 있는 행을 1부터 Excel 행 한도 안에서 선택해 주세요.")
    if orientation == "columns" and (type(header_column) is not int or not 1 <= header_column <= MAX_COLUMNS):
        raise ValueError("항목명이 있는 열을 Excel 열 한도 안에서 선택해 주세요.")
    template, columns = _load(path), []
    sheet = _sheet(template, sheet_name)
    rows = []
    if orientation == "rows" and any(row == header_row and column > MAX_INSPECT_COLUMNS and _cell_text(template, cell).strip()
                                     for (row, column), cell in sheet.cells.items()):
        raise ValueError("열 매핑은 앞쪽 256열까지 지원합니다. 사용할 영역을 줄인 양식을 선택해 주세요.")
    if orientation == "columns":
        if any(column == header_column and row > MAX_INSPECT_ROWS and _cell_text(template, cell).strip()
               for (row, column), cell in sheet.cells.items()):
            raise ValueError(f"행 매핑은 앞쪽 {MAX_INSPECT_ROWS:,}행까지 지원합니다. 사용할 영역을 줄인 양식을 선택해 주세요.")
        for row in range(1, min(sheet.max_row, MAX_INSPECT_ROWS) + 1):
            title = _row_title(template, sheet, row, header_column)
            field, qualifier = _suggest(title, sheet_name)
            rows.append({"row": row, "label": str(row), "title": title,
                         "suggested_field": field, "qualifier": qualifier})
    else:
        for column in range(1, min(sheet.max_column, MAX_INSPECT_COLUMNS) + 1):
            title = _header_title(template, sheet, header_row, column)
            field, qualifier = _suggest(title, sheet_name)
            columns.append({"column": column, "letter": get_column_letter(column), "title": title,
                            "suggested_field": field, "qualifier": qualifier})
    return {"path": str(Path(path)), "fingerprint": template.fingerprint, "sheet_name": sheet_name,
            "orientation": orientation, "header_column": header_column,
            "columns": columns, "rows": rows, "max_row": sheet.max_row, "max_column": sheet.max_column,
            "formula_cells": tuple(cell.get("r") for cell in sheet.cells.values()
                                   if cell.find(_tag(cell, "f")) is not None)}


def _validate_config(config):
    if (not isinstance(config.kind, str) or config.kind not in KINDS
            or not isinstance(config.mode, str) or config.mode not in {"rows", "columns", "lists"}):
        raise ValueError("자료 종류와 행·열 배치 방식을 선택해 주세요.")
    if config.mode == "lists" and config.kind != "release":
        raise ValueError("버전별 독립 목록은 릴리즈노트에서만 사용할 수 있습니다.")
    if not isinstance(config.orientation, str) or config.orientation not in {"rows", "columns"}:
        raise ValueError("항목명의 가로·세로 방향을 선택해 주세요.")
    if config.orientation == "rows":
        if (any(type(value) is not int for value in (config.header_row, config.start_row, config.end_row))
                or not 1 <= config.header_row < config.start_row <= config.end_row <= MAX_ROWS):
            raise ValueError("열 이름 행 다음부터 시작하는 데이터 영역의 시작·마지막 행을 확인해 주세요.")
    elif (any(type(value) is not int for value in (config.header_column, config.start_column, config.end_column))
            or not 1 <= config.header_column < config.start_column <= config.end_column <= MAX_COLUMNS):
        raise ValueError("항목명 열 다음부터 시작하는 데이터 영역의 시작·마지막 열을 확인해 주세요.")
    mappings = template_mappings(config)
    axis = "열" if config.orientation == "rows" else "행"
    maximum = MAX_COLUMNS if config.orientation == "rows" else MAX_ROWS
    mapping_type = TemplateColumn if config.orientation == "rows" else TemplateRow
    if not mappings:
        raise ValueError(f"내보낼 항목을 한 {axis} 이상 연결해 주세요.")
    positions = []
    for mapping in mappings:
        if not isinstance(mapping, mapping_type):
            raise ValueError("양식 방향에 맞는 행·열 연결을 확인해 주세요.")
        position = _field_position(mapping, config.orientation)
        if (type(position) is not int or not 1 <= position <= maximum
                or mapping.field not in HEADERS[KINDS[config.kind]] or not isinstance(mapping.qualifier, str)):
            raise ValueError("양식의 열·표준 항목·PM 또는 버전 연결을 확인해 주세요.")
        positions.append(position)
    if len(set(positions)) != len(positions):
        raise ValueError(f"같은 양식 {axis}에는 하나의 항목만 연결할 수 있습니다.")
    if config.site is not None and (config.kind != "equipment" or not isinstance(config.site, str) or not config.site):
        raise ValueError("사업장 선택은 설비현황표에만 사용할 수 있습니다.")
    if not isinstance(config.fingerprint, str) or not config.fingerprint:
        raise ValueError("양식을 먼저 불러와 매핑과 미리보기를 확인해 주세요.")


def _mapped_rows(records, config, *, allow_empty=False):
    _validate_config(config)
    source = records[KINDS[config.kind]] if isinstance(records, dict) else records
    if config.site is not None:
        source = [row for row in source if row.get("사업장") == config.site]
    if config.mode == "lists":
        result = _list_rows(tuple(source), config, allow_empty=allow_empty)
        _check_output_size(len(result), config, allow_empty=allow_empty)
        return result
    groups = []
    if config.mode == "rows":
        groups = [(row,) for row in source]
    else:
        grouped = OrderedDict()
        fields = (("설비 호기", "기준일") if config.kind == "equipment" else
                  ("관리 모델", "UI", "생산 영역", "Item 번호", "내용", "Jira 코드"))
        for row in source:
            identity = tuple(row.get(field) for field in fields)
            if config.kind == "release" and "_item_id" in row:
                identity += (("item", row["_item_id"], row.get("_family_id")),)
            grouped.setdefault(identity, []).append(row)
        groups = list(grouped.values())
        if config.kind == "release":
            for group in groups:
                versions = [row.get("버전(원문)") for row in group]
                if len(set(versions)) != len(versions):
                    raise ValueError("같은 버전에 번호·내용·Jira가 같은 Item이 여러 개 있어 열별 배치 대상을 구분할 수 없습니다. 행별 배치를 사용해 주세요.")
    _check_output_size(len(groups), config, allow_empty=allow_empty)
    selector = "PM" if config.kind == "equipment" else "버전(원문)"
    result = []
    for group in groups:
        values = []
        for column in template_mappings(config):
            selected = [row for row in group if not column.qualifier or row.get(selector) == column.qualifier]
            unique = []
            for row in selected:
                value = row.get(column.field)
                value = "" if value is None else value
                if not any(value == previous for previous in unique):
                    unique.append(value)
            if len(unique) > 1:
                qualifier = f" ({column.qualifier})" if column.qualifier else ""
                raise ValueError(f"{column.field}{qualifier}: 같은 출력 행에 서로 다른 값이 있습니다. "
                                 "PM·버전을 지정하거나 행별 배치로 변경해 주세요.")
            values.append(unique[0] if unique else "")
        result.append(tuple(values))
    return tuple(result)


def _check_output_size(row_count, config, *, allow_empty=False):
    if not row_count and not allow_empty:
        raise ValueError("선택한 자료 범위에 내보낼 현재 저장 자료가 없습니다.")
    start, end = _record_bounds(config)
    axis = "행" if config.orientation == "rows" else "열"
    if row_count > end - start + 1:
        raise ValueError(f"내보낼 자료는 {row_count:,}{axis}이지만 지정한 양식 영역은 "
                         f"{end - start + 1:,}{axis}입니다. 마지막 {axis}을 늘려 주세요.")
    if row_count * len(template_mappings(config)) > MAX_OUTPUT_CELLS:
        raise ValueError("한 번에 채울 셀이 너무 많습니다. 자료 범위를 줄여 주세요.")


def _list_rows(source, config, *, allow_empty=False):
    """각 버전의 Item 목록을 독립적으로 위에서부터 채운다. 항목을 합치지 않는다."""
    packed, constants = {}, {}
    for index, column in enumerate(template_mappings(config)):
        if column.qualifier:
            selected = [row for row in source if row.get("버전(원문)") == column.qualifier]
            families = {row["_family_id"] for row in selected if row.get("_family_id") is not None}
            if len(families) > 1:
                raise ValueError(f"{column.qualifier}: 같은 버전 원문이 여러 계열에 있습니다. "
                                 "내보낼 파일·생산 영역을 좁혀 한 계열만 선택해 주세요.")
            packed[index] = tuple("" if row.get(column.field) is None else row[column.field] for row in selected)
        else:
            values = []
            for row in source:
                value = "" if row.get(column.field) is None else row[column.field]
                if not any(value == previous for previous in values):
                    values.append(value)
            if len(values) > 1:
                raise ValueError(f"{column.field}: 버전을 지정하지 않은 열에 서로 다른 값이 있습니다. "
                                 "이 열의 버전을 지정해 주세요.")
            constants[index] = values[0] if values else ""
    count = max((len(values) for values in packed.values()), default=1 if source else 0)
    _check_output_size(count, config, allow_empty=allow_empty)
    return tuple(tuple(constants[index] if index in constants else
                       packed[index][row] if row < len(packed[index]) else ""
                       for index in range(len(template_mappings(config)))) for row in range(count))


def _validate_value(value, label):
    if isinstance(value, str):
        if len(value) > MAX_CELL_CHARACTERS:
            raise ValueError(f"{label}: Excel 한 셀의 32,767자 한도를 넘었습니다.")
        if ILLEGAL_CHARACTERS_RE.search(value):
            raise ValueError(f"{label}: Excel이 보존할 수 없는 제어 문자가 있습니다.")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError(f"{label}: UTF-8로 보존할 수 없는 문자가 있습니다.") from None
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label}: Excel에 기록할 수 없는 숫자입니다.")
    elif value is not None and not isinstance(value, (bool, int, float, date, datetime)):
        raise ValueError(f"{label}: Excel에 기록할 수 없는 값 형식입니다.")


def _prepare(records, config, *, allow_empty=False, template=None):
    rows = _mapped_rows(records, config, allow_empty=allow_empty)
    template = _load(config.path, config.fingerprint) if template is None else template
    if template.fingerprint != config.fingerprint:
        raise ValueError("검토한 양식과 입력 파일이 다릅니다. 양식을 다시 불러와 확인해 주세요.")
    sheet = _sheet(template, config.sheet_name)
    mappings = template_mappings(config)
    positions = {_field_position(mapping, config.orientation) for mapping in mappings}
    start, end = _record_bounds(config)

    def in_region(row, column):
        return (start <= row <= end and column in positions if config.orientation == "rows"
                else start <= column <= end and row in positions)

    def intersects(bounds):
        left, top, right, bottom = bounds
        return (top <= end and bottom >= start and any(left <= position <= right for position in positions)
                if config.orientation == "rows" else
                left <= end and right >= start and any(top <= position <= bottom for position in positions))

    # 배열·공유 수식은 시작 셀이 영역 밖이어도 결과 범위 전체를 소유한다.
    for cell in sheet.cells.values():
        formula = cell.find(_tag(cell, "f"))
        if formula is not None and formula.get("ref"):
            left, top, right, bottom = _bounds(formula.get("ref"))
            if intersects((left, top, right, bottom)):
                raise ValueError(f"{config.sheet_name}!{formula.get('ref')}: 배열·공유 수식의 결과 범위에는 "
                                 "데이터를 채울 수 없습니다. 해당 열의 매핑이나 데이터 영역을 변경해 주세요.")

    def anchor(row, column):
        top, left = sheet.anchor(row, column)
        if not in_region(top, left):
            raise ValueError("매핑한 병합 셀의 시작 위치가 지정한 데이터 영역 밖에 있습니다. 병합 시작 열과 행을 연결해 주세요.")
        return top, left

    for left, top, right, bottom in sheet.merges:
        if intersects((left, top, right, bottom)):
            if config.orientation == "rows":
                if top < start or bottom > end:
                    raise ValueError("병합 셀이 데이터 영역의 시작·마지막 행을 벗어납니다. 병합 범위 전체를 포함하거나 다른 영역을 선택해 주세요.")
                anchor(max(top, start), next(position for position in positions if left <= position <= right))
            else:
                if left < start or right > end:
                    raise ValueError("병합 셀이 데이터 영역의 시작·마지막 열을 벗어납니다. 병합 범위 전체를 포함하거나 다른 영역을 선택해 주세요.")
                anchor(next(position for position in positions if top <= position <= bottom), max(left, start))
    clear = set()
    for (row, column), cell in sheet.cells.items():
        if in_region(row, column):
            target = anchor(row, column)
            original = sheet.cells.get(target)
            if original is not None and original.find(_tag(original, "f")) is not None:
                raise ValueError(f"{config.sheet_name}!{get_column_letter(target[1])}{target[0]}: "
                                 "수식이 있는 셀은 덮어쓸 수 없습니다. 해당 열의 매핑을 해제해 주세요.")
            clear.add(target)
    assignments = {}
    for record_position, values in enumerate(rows, start):
        for mapping, value in zip(mappings, values, strict=True):
            row_number, column_number = _target_coordinate(config, record_position, mapping)
            target = anchor(row_number, column_number)
            label = f"{config.sheet_name}!{get_column_letter(column_number)}{row_number} ({mapping.field})"
            _validate_value(value, label)
            if target in assignments and assignments[target] != value:
                raise ValueError(f"{label}: 같은 병합 셀에 서로 다른 값을 채울 수 없습니다. 데이터 영역이나 매핑을 확인해 주세요.")
            assignments[target] = value
    return template, sheet, rows, clear, assignments


def preview_template(records, config, *, allow_empty=False, template=None):
    _, _, rows, _, _ = _prepare(records, config, allow_empty=allow_empty, template=template)
    return describe_template_preview(records, config, rows)


def describe_template_preview(records, config, rows):
    """이미 검증한 논리 레코드를 실제 양식 방향으로 미리 보여 준다."""
    mappings = template_mappings(config)
    first = min(_field_position(mapping, config.orientation) for mapping in mappings)
    last = max(_field_position(mapping, config.orientation) for mapping in mappings)
    row_labels = ()
    if config.orientation == "rows":
        columns = tuple(f"{get_column_letter(column.column)} · {column.field}"
                        + (f" [{column.qualifier}]" if column.qualifier else "") for column in mappings)
        preview_rows = rows[:5]
        end = config.start_row + len(rows) - 1 if rows else config.end_row
        target_range = f"{get_column_letter(first)}{config.start_row}:{get_column_letter(last)}{end}"
    else:
        columns = tuple(get_column_letter(config.start_column + index) for index in range(min(5, len(rows))))
        row_labels = tuple(f"{mapping.row} · {mapping.field}"
                           + (f" [{mapping.qualifier}]" if mapping.qualifier else "") for mapping in mappings)
        preview_rows = tuple(tuple(record[index] for record in rows[:5]) for index in range(len(mappings)))
        end = config.start_column + len(rows) - 1 if rows else config.end_column
        target_range = f"{get_column_letter(config.start_column)}{first}:{get_column_letter(end)}{last}"
    sites = {}
    if config.kind == "equipment":
        source = records[KINDS[config.kind]] if isinstance(records, dict) else records
        for record in source:
            site = record.get("사업장") or "(미지정)"
            sites[site] = sites.get(site, 0) + 1
    return {"row_count": len(rows), "record_count": len(rows), "orientation": config.orientation,
            "columns": columns, "rows": preview_rows, "row_labels": row_labels, "range": target_range,
            "site_counts": sites, "selected_site": config.site}


def _clear_cell(cell):
    for child in tuple(cell):
        if etree.QName(child).localname in {"v", "is"}:
            cell.remove(child)
    cell.attrib.pop("t", None)


def _fill_cell(template, cell, value):
    _clear_cell(cell)
    if value is None or value == "":
        return
    if isinstance(value, (date, datetime)):
        if int(cell.get("s", "0")) in template.date_styles:
            value = to_excel(value, template.epoch)
        else:
            value = value.isoformat()
    if isinstance(value, str):
        cell.set("t", "inlineStr")
        inline = etree.Element(_tag(cell, "is"))
        text = etree.SubElement(inline, _tag(cell, "t"))
        text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text.text = value
        cell.insert(0, inline)
    else:
        cell.set("t", "b" if isinstance(value, bool) else "n")
        number = etree.Element(_tag(cell, "v"))
        number.text = ("1" if value else "0") if isinstance(value, bool) else str(value)
        cell.insert(0, number)


def _patched_sheet(template, sheet, config, clear, assignments):
    root = sheet.tree.getroot()
    data = root.find(_tag(root, "sheetData"))
    if data is None:
        raise ValueError("양식의 데이터 영역을 확인할 수 없습니다.")
    row_nodes = {int(row.get("r")): row for row in data}
    prototype_row = (config.start_row if config.orientation == "rows"
                     else min(mapping.row for mapping in config.rows))
    prototype = row_nodes.get(prototype_row)
    for position in sorted(clear | assignments.keys()):
        row_number, column_number = position
        cell = sheet.cells.get(position)
        if cell is None:
            if position not in assignments or assignments[position] in (None, ""):
                continue
            row = row_nodes.get(row_number)
            if row is None:
                row = etree.Element(_tag(root, "row"), r=str(row_number))
                if prototype is not None:
                    for attribute in ("ht", "customHeight"):
                        if attribute in prototype.attrib:
                            row.set(attribute, prototype.get(attribute))
                following = next((index for index, old in enumerate(data) if int(old.get("r")) > row_number), len(data))
                data.insert(following, row)
                row_nodes[row_number] = row
            cell = etree.Element(_tag(root, "c"), r=f"{get_column_letter(column_number)}{row_number}")
            original = sheet.cells.get((config.start_row, column_number) if config.orientation == "rows"
                                       else (row_number, config.start_column))
            if original is not None and "s" in original.attrib:
                cell.set("s", original.get("s"))
            following = next((index for index, old in enumerate(row)
                              if _coordinates(old.get("r"))[1] > column_number), len(row))
            row.insert(following, cell)
            sheet.cells[position] = cell
        _fill_cell(template, cell, assignments.get(position, ""))
    if config.orientation == "columns" and assignments:
        column_styles = root.find(_tag(root, "cols"))
        if column_styles is not None:
            prototype_column = next((column for column in column_styles
                                     if int(column.get("min")) <= config.start_column <= int(column.get("max"))), None)
            if prototype_column is not None:
                for number in sorted({column for _, column in assignments}):
                    if any(int(column.get("min")) <= number <= int(column.get("max")) for column in column_styles):
                        continue
                    copied = etree.Element(_tag(root, "col"), **dict(prototype_column.attrib))
                    copied.set("min", str(number))
                    copied.set("max", str(number))
                    index = next((index for index, column in enumerate(column_styles)
                                  if int(column.get("min")) > number), len(column_styles))
                    column_styles.insert(index, copied)
    if assignments:
        dimension = root.find(_tag(root, "dimension"))
        if dimension is not None:
            left, top, right, bottom = range_boundaries(dimension.get("ref", "A1"))
            positions = tuple(assignments)
            left, top = min(left, min(column for _, column in positions)), min(top, min(row for row, _ in positions))
            right, bottom = max(right, max(column for _, column in positions)), max(bottom, max(row for row, _ in positions))
            dimension.set("ref", f"{get_column_letter(left)}{top}:{get_column_letter(right)}{bottom}")
    return etree.tostring(sheet.tree, encoding="utf-8", xml_declaration=True)


def load_template(path, fingerprint=None):
    """여러 시트 처리에서도 같은 원본 패키지와 검토 지문을 사용한다."""
    return _load(path, fingerprint)


def prepare_template_records(records, config, *, allow_empty=False, template=None):
    """최종 셀 변경을 메모리에서 준비한다. 빈 자료도 수식·병합 검사를 생략하지 않는다."""
    return _prepare(records, config, allow_empty=allow_empty, template=template)


def render_template_sheet(template, sheet, config, clear, assignments):
    return _patched_sheet(template, sheet, config, clear, assignments)


def write_template_package(template, path, replacements):
    """미리 검증한 시트 변경을 한 파일에 원자적으로 반영한다."""
    path, temporary = Path(path), None
    replacements = dict(replacements)
    workbook = template.workbook.getroot()
    calculation = workbook.find(_tag(workbook, "calcPr"))
    if calculation is None:
        calculation = etree.Element(_tag(workbook, "calcPr"))
        later = {"oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes",
                 "webPublishing", "fileRecoveryPr", "webPublishObjects", "extLst"}
        index = next((index for index, child in enumerate(workbook)
                      if isinstance(child.tag, str) and etree.QName(child).localname in later), len(workbook))
        workbook.insert(index, calculation)
    calculation.set("calcMode", "auto")
    calculation.set("fullCalcOnLoad", "1")
    calculation.set("forceFullCalc", "1")
    replacements["xl/workbook.xml"] = etree.tostring(template.workbook, encoding="utf-8", xml_declaration=True)
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".xlsx", delete=False) as handle:
            temporary = Path(handle.name)
        with ZipFile(BytesIO(template.raw)) as original, ZipFile(temporary, "w") as output:
            output.comment = original.comment
            for entry in original.infolist():
                output.writestr(entry, replacements.get(entry.filename, original.read(entry.filename)))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def save_template_export(storage, path, config, *, production_area=None, batch_ids=None):
    path = Path(path)
    try:
        if path.suffix.lower() != ".xlsx":
            raise ValueError("맞춤 출력 파일 이름은 .xlsx로 끝나야 합니다.")
        source = Path(config.path)
        if source.resolve() == path.resolve() or path.exists() and os.path.samefile(source, path):
            raise ValueError("원본 양식에는 저장할 수 없습니다. 다른 출력 파일 이름을 선택해 주세요.")
        records = read_management_records(storage, production_area=production_area, batch_ids=batch_ids)
        template, sheet, rows, clear, assignments = _prepare(records, config)
        if not rows:
            raise ValueError("선택한 자료 범위에 내보낼 현재 저장 자료가 없습니다.")
        replacements = {template.sheets[config.sheet_name]: _patched_sheet(template, sheet, config, clear, assignments)}
        write_template_package(template, path, replacements)
        axis = "행" if config.orientation == "rows" else "열"
        return ExportResult(ExportStatus.SAVED, path, f"{config.sheet_name} · {len(rows):,}{axis} · 지정 양식으로 저장했습니다.")
    except DatabaseError as error:
        return ExportResult(ExportStatus.FAILED, path, f"DB 자료를 읽지 못했습니다: {error}")
    except PermissionError:
        return ExportResult(ExportStatus.FAILED, path, "파일 저장 권한과 Excel에서 출력 파일이 열려 있는지 확인해 주세요.")
    except (OSError, ValueError, BadZipFile, etree.LxmlError) as error:
        return ExportResult(ExportStatus.FAILED, path, f"맞춤 양식으로 저장하지 못했습니다: {error}")
