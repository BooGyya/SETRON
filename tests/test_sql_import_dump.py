"""Exercise the SQL import reader with SETRON's actual export value encoding."""

import re
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from setron.features.db_export.service import _header, _write_rows
from setron.features.db_import.dump import DUMP_COLUMNS, DumpFormatError, allowed_columns, read_dump
from setron.features.storage.validation import TABLE_NAMES


def _export(rows):
    """Use the export writer with known MySQL result rows and SHOW CREATE framing."""
    file = StringIO()
    file.write(_header(datetime(2026, 11, 11), "5.6.19", "test", len(TABLE_NAMES), sum(map(len, rows.values()))))
    for table in TABLE_NAMES:
        table_rows = rows.get(table, ())
        # 행 길이로 형식을 고른다: 지금 형식(item.jira_code 포함) 또는 그 칸이 없던 예전 형식(11/12)
        columns = next((c for c in allowed_columns(table) if table_rows and len(table_rows[0]) == len(c)),
                       DUMP_COLUMNS[table])
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
                            "item": [(1, None, 3, "Recipe Edit 경고", 1, None, "[]")]}), encoding="utf-8")
    assert read_dump(new)["item"][0]["jira_code"] == "ETOJ-1793"
    assert "jira_code" not in read_dump(old)["item"][0]
