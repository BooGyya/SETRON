"""Exercise the SQL import reader with SETRON's actual export value encoding."""

import re
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from setron.features.db_export.service import _header, _write_rows
from setron.features.db_import.dump import (
    LEGACY_SNAPSHOT_COLUMNS,
    DUMP_COLUMNS, DUAL_ITEM_COLUMNS, LEGACY_ITEM_COLUMNS, LEGACY_KEY_ITEM_COLUMNS,
    DumpFormatError, allowed_columns, project_row, read_dump,
)
from setron.features.storage.validation import TABLE_NAMES


def _export(rows, *, legacy_item=False, item_columns=None):
    """Use the export writer with known MySQL result rows and SHOW CREATE framing."""
    file = StringIO()
    file.write(_header(datetime(2026, 11, 11), "5.6.19", "test", len(TABLE_NAMES), sum(map(len, rows.values()))))
    for table in TABLE_NAMES:
        table_rows = rows.get(table, ())
        columns = DUMP_COLUMNS[table]
        if table == "item":
            columns = item_columns or (LEGACY_ITEM_COLUMNS if legacy_item else DUMP_COLUMNS[table])
            if not legacy_item and item_columns is None:
                table_rows = [(*row, None) if len(row) == len(LEGACY_ITEM_COLUMNS) else row for row in table_rows]
        elif table == "equipment_snapshot":                   # 공정 칸(11/13) 전 13열 행은 공정 없이 새 구조로 쓴다
            table_rows = [(*row, None) if len(row) == len(LEGACY_SNAPSHOT_COLUMNS) else row for row in table_rows]
        fields = ",\n".join(f"  `{column}` text COMMENT '원문; 보존'" for column in columns)
        file.write(f"\n-- 테이블 {table} · {len(table_rows)}행\nCREATE TABLE `{table}` (\n{fields}\n"
                   ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
        result = MagicMock()
        result.keys.return_value = columns
        result.fetchmany.side_effect = [table_rows[index:index + 200] for index in range(0, len(table_rows), 200)] + [[]]
        connection = MagicMock()
        connection.exec_driver_sql.return_value = result
        _write_rows(connection, file, table)
    file.write("\nSET FOREIGN_KEY_CHECKS = @OLD_FOREIGN_KEY_CHECKS;\n"
               "SET SESSION SQL_MODE = @OLD_SQL_MODE;\n"
               "SET character_set_client = @OLD_CHARACTER_SET_CLIENT;\n"
               "SET character_set_results = @OLD_CHARACTER_SET_RESULTS;\n"
               "SET collation_connection = @OLD_COLLATION_CONNECTION;\n")
    return file.getvalue()


@pytest.mark.parametrize("legacy_item", [False, True])
def test_item_dump_accepts_new_jira_and_only_the_known_old_layout(tmp_path, legacy_item):
    row = (1, None, 7, "본문", 1, None, "[]")
    values = row if legacy_item else (*row, "etoj-3257")
    path = tmp_path / "item.sql"
    path.write_text(_export({"item": [values]}, legacy_item=legacy_item), encoding="utf-8")
    restored = read_dump(path)["item"][0]
    assert restored["content"] == "본문" and restored["item_no"] == 7
    assert restored.get("jira_code") == (None if legacy_item else "etoj-3257")


@pytest.mark.parametrize("alter", [
    lambda sql: sql.replace("`item_no`, `content`", "`jira_key`, `content`"),
    lambda sql: sql.replace("`item_no` text", "`unknown` text"),
    lambda sql: sql.replace("`item_no`, `content`", "`item_no`, `jira_key`, `content`"),
])
def test_legacy_item_compatibility_does_not_relax_other_columns_or_insert_headers(tmp_path, alter):
    path = tmp_path / "wrong-item.sql"
    path.write_text(alter(_export({"item": [(1, None, 1, "본문", 1, None, "[]")]}, legacy_item=True)), encoding="utf-8")
    with pytest.raises(DumpFormatError):
        read_dump(path)


def test_dump_columns_follow_the_packaged_mysql_schema():
    schema = Path(__file__).resolve().parents[1] / "database" / "schema_mysql5619.sql"
    table_parts = re.findall(
        r"^CREATE TABLE ([a-z_]+) \(\n(.*?)^\) ENGINE=", schema.read_text(encoding="utf-8"), re.M | re.S,
    )
    assert DUMP_COLUMNS == {
        table: tuple(re.findall(r"^    ([a-z_]+) [A-Z]+", sql, re.M)) for table, sql in table_parts
    }


def test_round_trip_preserves_values_and_multiple_insert_batches(tmp_path):
    path = tmp_path / "백업.SQL"
    original = "한글😀'\"\\\t\n\r\0\x1a ;, ) --\nSET FOREIGN_KEY_CHECKS = 0;"
    binary = b"\x00\xff ' \x1a "
    settings = [(f"설정{index}".encode(), original) for index in range(201)]
    path.write_text(_export({
        "import_batch": [(1, "MANUAL", datetime(2026, 11, 11, 12, 34, 56), None)],
        "equipment": [(1, binary), (2, b"")],
        "release_note": [(1, 1, date(2026, 11, 10), 1)],
        "item": [(1, None, -2, "", 1, None, "[]")],
        "app_setting": settings,
    }), encoding="utf-8-sig")

    tables = read_dump(path)

    assert tuple(tables) == TABLE_NAMES
    assert not tables["risk_rule"]
    assert tables["import_batch"] == ({
        "batch_id": 1, "source_type": "MANUAL", "imported_at": "2026-11-11 12:34:56", "raw_data": None,
    },)
    assert tables["equipment"] == (
        {"equipment_id": 1, "equipment_code": binary}, {"equipment_id": 2, "equipment_code": b""},
    )
    assert tables["release_note"][0]["release_date"] == "2026-11-10"
    assert tables["item"][0]["item_no"] == -2
    assert tables["item"][0]["content"] == ""
    assert [(row["setting_key"], row["setting_value"]) for row in tables["app_setting"]] == settings


@pytest.mark.parametrize("alter", [
    lambda sql: sql + "DROP TABLE `equipment`;\n",
    lambda sql: sql.replace("(1, _binary X'4142');", "(1, _binary X'4142'); DROP TABLE `equipment`;"),
    lambda sql: sql.replace("_binary X'4142'", "CONCAT('A', 'B')"),
    lambda sql: sql.replace("_binary X'4142'", "_binary X'414'"),
    lambda sql: sql.replace("`equipment_code`) VALUES", "`unexpected`) VALUES"),
    lambda sql: sql.replace("equipment · 1행", "equipment · 2행"),
    lambda sql: sql.replace("테이블 13개 · 1행", "테이블 13개 · 2행"),
    lambda sql: sql[:sql.index("SET SESSION SQL_MODE = @OLD_SQL_MODE;")],
])
def test_invalid_or_incomplete_dump_is_rejected(tmp_path, alter):
    path = tmp_path / "wrong.sql"
    path.write_text(alter(_export({"equipment": [(1, b"AB")]})), encoding="utf-8")
    with pytest.raises(DumpFormatError, match="파일"):
        read_dump(path)


def test_legacy_dump_preserves_unambiguous_text_and_binary(tmp_path):
    path = tmp_path / "legacy.sql"
    sql = _export({"app_setting": [(b"key", "'value'")]})
    old_start = sql.index("SET @OLD_CHARACTER_SET_CLIENT")
    first_foreign_key = sql.index("SET @OLD_FOREIGN_KEY_CHECKS")
    old_end = sql.index("SET SESSION SQL_MODE = @OLD_SQL_MODE;")
    sql = sql[:old_start] + "SET NAMES utf8mb4;\n" + sql[first_foreign_key:old_end]
    sql = sql.replace("_binary X'", "X'").replace("\\'", "''")
    path.write_text(sql, encoding="utf-8")
    assert read_dump(path)["app_setting"] == ({"setting_key": b"key", "setting_value": "'value'"},)

    # The historical format did not record how backslashes were interpreted by its source server.
    path.write_text(sql.replace("''value''", r"\n"), encoding="utf-8")
    with pytest.raises(DumpFormatError, match="다시 내보내"):
        read_dump(path)


def test_item_jira_code_round_trips_and_older_exports_without_it_still_read(tmp_path):
    """11/12 item.jira_code: 칸이 있는 새 내보내기는 값을 지키고, 칸이 없던 예전 내보내기도 그대로 읽는다."""
    new, old = tmp_path / "new.sql", tmp_path / "old.sql"
    new.write_text(_export({"import_batch": [(1, "EXCEL", datetime(2026, 11, 12), "{}")],
                            "item": [(1, None, 3, "Recipe Edit 경고", 1, None, "[]", "ETOJ-1793")]}), encoding="utf-8")
    old.write_text(_export({"import_batch": [(1, "EXCEL", datetime(2026, 11, 12), "{}")],
                            "item": [(1, None, 3, "Recipe Edit 경고", 1, None, "[]")]}, legacy_item=True), encoding="utf-8")
    assert read_dump(new)["item"][0]["jira_code"] == "ETOJ-1793"
    assert "jira_code" not in read_dump(old)["item"][0]


@pytest.mark.parametrize("columns", allowed_columns("item"))
def test_five_exact_item_layouts_preserve_raw_columns_and_project_without_mutation(tmp_path, columns):
    from copy import deepcopy
    original = {"item_id": 1, "release_note_id": None, "item_no": 1, "content": "원문", "batch_id": 1,
                "source_reference": None, "edited_fields": "[]"}
    if "jira_key" in columns:
        original["jira_key"] = "ETOJ-1, ETOJ-2"
    if "jira_code" in columns:
        original["jira_code"] = "ETOJ-1, ETOJ-2"
    path = tmp_path / "layout.sql"
    path.write_text(_export({"item": [tuple(original[column] for column in columns)]}, item_columns=columns), encoding="utf-8")
    row, = read_dump(path)["item"]
    assert tuple(row) == columns and row == original
    before = deepcopy(row)
    projected = project_row("item", row, DUMP_COLUMNS["item"])
    assert projected["jira_code"] == original.get("jira_code", original.get("jira_key"))
    assert row == before


@pytest.mark.parametrize("code,key", [("ETOJ-1", "ETOJ-2"), (None, "ETOJ-1")])
def test_conflicting_dual_jira_values_are_rejected_without_selecting_or_losing_one(tmp_path, code, key):
    row = (1, None, 1, key, "원문", 1, None, "[]", code)
    path = tmp_path / "conflict.sql"
    path.write_text(_export({"item": [row]}, item_columns=DUAL_ITEM_COLUMNS), encoding="utf-8")
    with pytest.raises(DumpFormatError, match="충돌"):
        read_dump(path)


def test_nonnull_jira_cannot_be_projected_into_a_table_without_it():
    row = dict(zip(DUMP_COLUMNS["item"], (1, None, 1, "원문", 1, None, "[]", "ETOJ-1"), strict=True))
    with pytest.raises(DumpFormatError, match="손실 없이"):
        project_row("item", row, LEGACY_ITEM_COLUMNS)
    row["jira_code"] = None
    assert "jira_code" not in project_row("item", row, LEGACY_ITEM_COLUMNS)


@pytest.mark.parametrize("columns", [
    (*LEGACY_ITEM_COLUMNS, "jira_key"),
    ("jira_code", *LEGACY_ITEM_COLUMNS),
    (*DUMP_COLUMNS["item"][:-2], "jira_code", "edited_fields"),
])
def test_jira_alias_support_does_not_accept_arbitrary_column_orders(tmp_path, columns):
    path = tmp_path / "order.sql"
    path.write_text(_export({}, item_columns=columns), encoding="utf-8")
    with pytest.raises(DumpFormatError):
        read_dump(path)


def test_columnless_jira_derivation_preserves_known_null_edits_and_original_dataset():
    from copy import deepcopy
    import json
    from setron.features.db_import.dump import with_legacy_jira

    raw = {"노트": {"A1": "Update내용", "B1": "JIRA", "A2": "원문", "B2": "ETOJ-987"}}
    base = {"release_note_id": None, "item_no": 1, "content": "원문", "batch_id": 1,
            "source_reference": json.dumps({"content": {"sheet": "노트", "cells": ["A2"]}}), "edited_fields": "[]"}
    items = ({**base, "item_id": 1}, {**base, "item_id": 2, "jira_code": None},
             {**base, "item_id": 3, "jira_key": None},
             {**base, "item_id": 4, "edited_fields": '["jira_code"]'},
             {**base, "item_id": 5, "edited_fields": '["jira_key"]'})
    rows = {table: () for table in DUMP_COLUMNS}
    rows.update(item=items, import_batch=({"batch_id": 1, "raw_data": json.dumps(raw)},))
    before = deepcopy(rows)
    result = with_legacy_jira(rows, DUMP_COLUMNS["item"])
    assert result["item"][0]["jira_code"] == "ETOJ-987"
    assert result["item"][1:] == items[1:]
    assert rows == before and result["import_batch"] == before["import_batch"]
    assert with_legacy_jira(rows, LEGACY_ITEM_COLUMNS) is rows
