"""Read SETRON SQL exports as data without sending any file commands to MySQL."""

import re
from pathlib import Path
from typing import TextIO

from setron.features.storage.validation import TABLE_NAMES


class DumpFormatError(ValueError):
    """The selected file is not a complete, supported SETRON export."""


DUMP_COLUMNS: dict[str, tuple[str, ...]] = {
    "import_batch": ("batch_id", "source_type", "imported_at", "raw_data"),
    "equipment_group": ("group_id", "group_name", "category", "parent_group_id", "analysis_enabled"),
    "equipment": ("equipment_id", "equipment_code"),
    "version_family": ("family_id", "group_id", "family_name", "ui_type"),
    "release_note": ("release_note_id", "group_id", "release_date", "batch_id"),
    "release_version": (
        "release_version_id", "release_note_id", "family_id", "version_raw", "version_suffix", "released_at",
        "version_order", "item_mapping_status", "batch_id", "source_reference", "edited_fields",
    ),
    "item": ("item_id", "release_note_id", "item_no", "jira_key", "content", "batch_id", "source_reference", "edited_fields"),
    "equipment_snapshot": (
        "snapshot_id", "equipment_id", "base_date", "site", "line", "model_name", "group_id", "production_area",
        "structure_type", "geometry_type", "modification_type", "batch_id", "source_reference",
    ),
    "equipment_version": (
        "equipment_version_id", "snapshot_id", "family_id", "version_raw", "release_version_id", "batch_id",
        "source_reference",
    ),
    "equipment_pm": (
        "pm_id", "snapshot_id", "equipment_version_id", "pm_name", "group_id", "model_name_raw", "batch_id",
        "source_reference",
    ),
    "release_version_item": ("release_version_id", "item_id", "item_state", "target_scope", "target_group_ids"),
    "risk_rule": (
        "risk_rule_id", "rule_type", "family_id", "group_id", "item_id", "release_version_id",
        "start_release_version_id", "end_release_version_id", "risk_reason", "is_enabled", "target_reference",
        "mapping_status",
    ),
    "app_setting": ("setting_key", "setting_value"),
}
LEGACY_ITEM_COLUMNS = tuple(column for column in DUMP_COLUMNS["item"] if column != "jira_key")


def supported_columns(table: str, columns) -> bool:
    """지원하는 전체 열 또는 jira_key 하나만 없는 이전 item 열 순서만 허용한다."""
    columns = tuple(columns)
    return columns == DUMP_COLUMNS.get(table) or table == "item" and columns == LEGACY_ITEM_COLUMNS


def columns_from_definition(table: str, definition: str) -> tuple[str, ...]:
    """실제 SHOW CREATE 또는 번들 스키마의 허용된 열 순서를 읽는다. SQL을 실행하지 않는다."""
    columns = tuple(re.findall(r"^  `([a-z_]+)` ", definition, re.M))
    if not columns:
        columns = tuple(re.findall(r"^    ([a-z_]+) [A-Z]+", definition, re.M))
    if not supported_columns(table, columns):
        raise DumpFormatError(f"{table}의 구조가 지원하는 SETRON 스키마와 다릅니다.")
    return columns


def values_for_columns(table: str, row: dict, columns) -> tuple:
    """새 선택 필드의 NULL만 이전 구조에서 생략하며 나머지 값 누락·손실은 차단한다."""
    if not supported_columns(table, columns):
        raise DumpFormatError(f"{table}의 열 순서가 올바르지 않습니다.")
    extra = set(row) - set(columns)
    if extra and not (table == "item" and extra == {"jira_key"} and row["jira_key"] is None):
        raise DumpFormatError(f"{table}의 값을 이전 구조에 손실 없이 저장할 수 없습니다.")
    return tuple(row.get(column) if table == "item" and column == "jira_key" else row[column] for column in columns)

_INVALID = "SETRON에서 내보낸 SQL 파일 형식과 일치하지 않거나 파일이 불완전합니다."
_FOREIGN_KEY_START = (
    "SET @OLD_FOREIGN_KEY_CHECKS = @@FOREIGN_KEY_CHECKS;\n",
    "SET FOREIGN_KEY_CHECKS = 0;\n",
)
_FOREIGN_KEY_END = "SET FOREIGN_KEY_CHECKS = @OLD_FOREIGN_KEY_CHECKS;\n"
_START = (
    "SET @OLD_CHARACTER_SET_CLIENT = @@character_set_client;\n",
    "SET @OLD_CHARACTER_SET_RESULTS = @@character_set_results;\n",
    "SET @OLD_COLLATION_CONNECTION = @@collation_connection;\n",
    "SET NAMES utf8mb4;\n",
    "SET @OLD_SQL_MODE = @@SESSION.SQL_MODE;\n",
    "SET SESSION SQL_MODE = 'NO_AUTO_VALUE_ON_ZERO';\n",
    *_FOREIGN_KEY_START,
)
_END = (
    _FOREIGN_KEY_END,
    "SET SESSION SQL_MODE = @OLD_SQL_MODE;\n",
    "SET character_set_client = @OLD_CHARACTER_SET_CLIENT;\n",
    "SET character_set_results = @OLD_CHARACTER_SET_RESULTS;\n",
    "SET collation_connection = @OLD_COLLATION_CONNECTION;\n",
)
_COUNT = re.compile(r"-- 테이블 ([a-z_]+|[0-9]{1,20}개) · ([0-9]{1,20})행\n")
_DDL_COLUMN = re.compile(r"  `([a-z_]+)` ")
_DDL_CONSTRAINTS = ("  PRIMARY KEY (", "  UNIQUE KEY `", "  KEY `", "  CONSTRAINT `")
_ESCAPES = {"0": "\0", "n": "\n", "r": "\r", "Z": "\x1a", "\\": "\\", "'": "'", '"': '"'}
_ESCAPE = re.compile(r"\\(.)")
_LITERAL = re.compile(
    r"NULL|(?P<number>-?[0-9]{1,20})|_binary X'(?P<binary>[0-9a-fA-F]*)'"
    r"|'(?P<string>(?>[^'\\\x00\x1a\r\n]+|\\[0nrZ\\'\"])*)'"
)
_LEGACY_LITERAL = re.compile(
    r"NULL|(?P<number>-?[0-9]{1,20})|X'(?P<binary>[0-9a-fA-F]*)'"
    r"|'(?P<string>(?>[^'\\\x00\x1a\r\n]+|'')*)'"
)


def read_dump(path: str | Path) -> dict[str, tuple[dict[str, object], ...]]:
    """Return the exported rows in schema order, retaining exact binary and text values."""
    path = Path(path)
    if path.suffix.lower() != ".sql":
        raise DumpFormatError("확장자가 .sql인 SETRON 내보내기 파일을 선택해 주세요.")
    try:
        with path.open(encoding="utf-8-sig") as source:
            return _read_file(source)
    except UnicodeError:
        raise DumpFormatError("SQL 파일을 UTF-8로 읽을 수 없습니다.") from None
    except OSError:
        raise DumpFormatError("SQL 파일을 열 수 없습니다. 파일 위치와 읽기 권한을 확인해 주세요.") from None


def _read_file(source: TextIO) -> dict[str, tuple[dict[str, object], ...]]:
    _expect(source, "-- SETRON DB 내보내기\n")
    if not source.readline().startswith("-- 생성 "):
        raise DumpFormatError(_INVALID)
    total = _read_count(source.readline(), f"{len(TABLE_NAMES)}개")
    for line in source:
        if line == "\n":
            break
        if not (line == "--\n" or line.startswith("-- ")):
            raise DumpFormatError(_INVALID)

    first = source.readline()
    # Early exports omitted the SQL mode, so their backslashes cannot always be decoded safely.
    legacy = first == "SET NAMES utf8mb4;\n"
    if not legacy and first != _START[0]:
        raise DumpFormatError(_INVALID)
    _expect(source, *(_FOREIGN_KEY_START if legacy else _START[1:]), "\n")

    tables = {}
    for table in TABLE_NAMES:
        count = _read_count(source.readline(), table)
        columns = _skip_table_definition(source, table, DUMP_COLUMNS[table])
        rows = _read_rows(source, table, columns, count, legacy)
        if table == "item" and columns == LEGACY_ITEM_COLUMNS:
            rows = [{**row, "jira_key": None} for row in rows]
        tables[table] = tuple(rows)
        _expect(source, "\n")

    _expect(source, *((_FOREIGN_KEY_END,) if legacy else _END))
    if sum(map(len, tables.values())) != total or any(line.strip() for line in source):
        raise DumpFormatError(_INVALID)
    return tables


def _expect(source: TextIO, *expected_lines: str) -> None:
    for expected in expected_lines:
        if source.readline() != expected:
            raise DumpFormatError(_INVALID)


def _read_count(line: str, name: str) -> int:
    match = _COUNT.fullmatch(line)
    if not match or match[1] != name:
        raise DumpFormatError(_INVALID)
    return int(match[2])


def _skip_table_definition(source: TextIO, table: str, columns: tuple[str, ...]) -> tuple[str, ...]:
    _expect(source, f"CREATE TABLE `{table}` (\n")
    found = []
    for line in source:
        if column := _DDL_COLUMN.match(line):
            found.append(column[1])
        elif line.startswith(") ENGINE=InnoDB ") and line.endswith(";\n"):
            if tuple(found) == columns or table == "item" and tuple(found) == LEGACY_ITEM_COLUMNS:
                return tuple(found)
            break
        elif not line.startswith(_DDL_CONSTRAINTS):
            break
    raise DumpFormatError(_INVALID)


def _read_rows(source: TextIO, table: str, columns: tuple[str, ...], count: int, legacy: bool) -> list[dict[str, object]]:
    quoted_columns = ", ".join(f"`{column}`" for column in columns)
    header = f"INSERT INTO `{table}` ({quoted_columns}) VALUES\n"
    rows = []
    while len(rows) < count:
        _expect(source, header)
        while True:
            line = source.readline()
            if len(rows) == count or not line.startswith("(") or not line.endswith(("),\n", ");\n")):
                raise DumpFormatError(_INVALID)
            rows.append(_parse_row(line, columns, legacy))
            if line.endswith(";\n"):
                break
    return rows


def _parse_row(line: str, columns: tuple[str, ...], legacy: bool) -> dict[str, object]:
    literal = _LEGACY_LITERAL if legacy else _LITERAL
    row = {}
    position = 1
    for index, column in enumerate(columns, start=1):
        match = literal.match(line, position)
        if not match:
            if legacy:
                raise DumpFormatError("이전 버전의 SQL 원문을 정확히 읽을 수 없습니다. SETRON에서 다시 내보내 주세요.")
            raise DumpFormatError(_INVALID)
        separator = ", " if index < len(columns) else ")"
        if not line.startswith(separator, match.end()):
            raise DumpFormatError(_INVALID)
        position = match.end() + len(separator)
        if match["number"] is not None:
            value = int(match["number"])
        elif match["binary"] is not None:
            if len(match["binary"]) % 2:
                raise DumpFormatError(_INVALID)
            value = bytes.fromhex(match["binary"])
        elif match["string"] is not None:
            original = match["string"]
            value = original.replace("''", "'") if legacy else _ESCAPE.sub(lambda escape: _ESCAPES[escape[1]], original)
        else:
            value = None
        row[column] = value
    if position != len(line) - 2:
        raise DumpFormatError(_INVALID)
    return row
