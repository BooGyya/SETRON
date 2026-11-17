"""Verify SETRON dump review and apply against a real, isolated database."""

import json
import os
import re
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import MetaData, create_engine, event, select
from sqlalchemy.engine import URL

from setron.features.db_export.service import export_database
from setron.features.db_import.dump import DUMP_COLUMNS, LEGACY_KEY_ITEM_COLUMNS, allowed_columns
from setron.features.db_import.merge import SqlImportError
from setron.features.db_import.service import SqlImportService
from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import Database, DatabaseSaveError

from test_schema_mysql import schema_database  # noqa: F401
from test_sql_import_dump import _export


@pytest.fixture
def target(tmp_path, request):
    """Translate MySQL DDL spelling, retaining column types, keys, and constraints."""
    engine = create_engine(URL.create("sqlite", database=str(tmp_path / "destination.sqlite")))

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    schema = Path(__file__).resolve().parents[1] / "database" / "schema_mysql5619.sql"
    text = schema.read_text(encoding="utf-8")
    statements = re.findall(r"^CREATE TABLE [a-z_]+ \(\n.*?^\) ENGINE=.*?;\n", text, re.M | re.S)
    assert len(statements) == len(TABLE_NAMES)
    with engine.begin() as connection:
        for statement in statements:
            if statement.startswith("CREATE TABLE item ("):
                layout = getattr(request, "param", "code")
                if layout in {"old", "key"}:
                    statement = re.sub(r"^    jira_code .*\n", "", statement, flags=re.M)
                if layout in {"key", "dual"}:
                    statement = re.sub(r"(^    item_no .*\n)",
                                       r"\g<1>    jira_key VARCHAR(50) NULL,\n", statement, flags=re.M)
            statement = re.sub(r"^    (?:-- |KEY ).*\n", "", statement, flags=re.M)
            statement = re.sub(r"^\) ENGINE=.*\n", ")", statement, flags=re.M)
            statement = re.sub(r" COMMENT '[^\n]*'(?=,?$)", "", statement, flags=re.M)
            statement = re.sub(r" COLLATE utf8mb4_\w+| UNSIGNED| AUTO_INCREMENT", "", statement)
            statement = re.sub(r"UNIQUE KEY \w+", "UNIQUE", statement)
            statement = statement.replace("VARBINARY", "BLOB").replace("LONGTEXT", "TEXT").replace(" INT ", " INTEGER ")
            connection.exec_driver_sql(statement)

    metadata = MetaData()
    metadata.reflect(engine)
    try:
        yield engine, metadata.tables, SqlImportService(lambda: Database(engine))
    finally:
        engine.dispose()


def bundle(*, code=b"EQ-1", group="O2", line="기존 라인", version=b"v1", pm=b"PM1", day=1):
    return {
        "import_batch": [(1, "MANUAL", datetime(2026, 11, day, 9), None)],
        "equipment_group": [(1, group, "OXIDE", None, 1)],
        "equipment": [(1, code)],
        "version_family": [(1, 1, group, None)],
        "equipment_snapshot": [(1, 1, date(2026, 11, 1), "화성", line, group, 1, None, None, None, None, 1, None)],
        "equipment_version": [(1, 1, 1, version, None, 1, None)],
        "equipment_pm": [(1, 1, 1, pm, 1, group, 1, None)],
    }


def seed(target, rows):
    engine, tables, _ = target
    with engine.begin() as connection:
        for name in TABLE_NAMES:
            if name in rows:
                # 행 길이로 형식을 고른다: 지금 형식(item.jira_code 포함) 또는 그 칸이 없던 예전 형식(11/12)
                values = [dict(zip(next(c for c in allowed_columns(name) if len(c) == len(row)), row, strict=True))
                          for row in rows[name]]
                connection.execute(tables[name].insert(), values)


def current(target):
    engine, tables, _ = target
    with engine.connect() as connection:
        return {name: [dict(row) for row in connection.execute(
            select(table).order_by(*table.primary_key.columns)).mappings()] for name, table in tables.items()}


def backup(tmp_path, rows):
    path = tmp_path / "이관.sql"
    path.write_text(_export(rows), encoding="utf-8")
    return path


def test_legacy_item_dump_is_imported_into_new_schema_with_null_jira(target, tmp_path):
    incoming = bundle()
    incoming["item"] = [(1, None, 7, "기존 원문 (ETOJ-3257)", 1, None, "[]")]
    path = tmp_path / "previous-schema.sql"
    path.write_text(_export(incoming, legacy_item=True), encoding="utf-8")
    service = target[2]
    service.apply(service.preview(path, "add"))
    item, = current(target)["item"]
    assert item["jira_code"] is None and item["content"] == "기존 원문 (ETOJ-3257)"


def test_add_keeps_same_equipment_date_and_its_pm_and_version(target, tmp_path):
    seed(target, bundle())
    path = backup(tmp_path, bundle(line="파일 라인", version=b"v2", pm=b"PM2", day=2))
    service = target[2]
    before = current(target)

    plan = service.preview(path, "add")

    assert current(target) == before
    assert "건너뜀" in plan.summary
    service.apply(plan)
    after = current(target)
    for table in ("equipment_snapshot", "equipment_version", "equipment_pm"):
        assert after[table] == before[table]


def test_overwrite_updates_matching_snapshot_and_replaces_its_children(target, tmp_path):
    existing = bundle()
    existing["equipment_pm"].append((2, 1, 1, b"PM2", 1, "O2", 1, None))
    seed(target, existing)
    path = backup(tmp_path, bundle(line="파일 라인", version=b"v2", day=2))
    service = target[2]

    plan = service.preview(path, "overwrite")

    assert "연결 자료 제거 2행" in plan.summary
    assert [write.table for write in plan.writes if write.action == "delete"] == ["equipment_pm", "equipment_version"]
    service.apply(plan)
    after = current(target)
    snapshot, = after["equipment_snapshot"]
    version, = after["equipment_version"]
    pm, = after["equipment_pm"]
    assert (snapshot["snapshot_id"], snapshot["line"]) == (1, "파일 라인")
    assert version["version_raw"] == b"v2"
    assert (pm["pm_id"], pm["pm_name"], pm["equipment_version_id"]) == (1, b"PM1", version["equipment_version_id"])
    assert pm["snapshot_id"] == version["snapshot_id"] == snapshot["snapshot_id"]
    assert service.preview(path, "overwrite").changes == 0


def test_colliding_ids_are_remapped_in_foreign_keys_and_json(target, tmp_path):
    seed(target, bundle())
    incoming = bundle(code=b"EQ-2", group="O3", line="새 라인", version=b"v2", day=2)
    incoming["release_note"] = [(1, 1, date(2026, 11, 1), 1)]
    incoming["release_version"] = [(1, 1, 1, b"v2", None, date(2026, 11, 1), 1, "VERIFIED", 1, None, "[]")]
    incoming["item"] = [(1, 1, 1, "파일의 Item", 1, None, "[]", None)]
    incoming["release_version_item"] = [(1, 1, "APPLIED", "LISTED", "[1]")]
    incoming["equipment_version"] = [(1, 1, 1, b"v2", 1, 1, '{"version_raw":{"batch_id":1,"cell":"B2"}}')]
    path = backup(tmp_path, incoming)
    service = target[2]
    before = current(target)

    service.apply(service.preview(path, "add"))

    after = current(target)
    equipment = next(row for row in after["equipment"] if row["equipment_code"] == b"EQ-2")
    model = next(row for row in after["equipment_group"] if row["group_name"] == "O3")
    snapshot = next(row for row in after["equipment_snapshot"] if row["equipment_id"] == equipment["equipment_id"])
    version = next(row for row in after["equipment_version"] if row["snapshot_id"] == snapshot["snapshot_id"])
    pm = next(row for row in after["equipment_pm"] if row["snapshot_id"] == snapshot["snapshot_id"])
    link, = after["release_version_item"]
    assert snapshot["snapshot_id"] != 1 and equipment["equipment_id"] != 1 and model["group_id"] != 1
    assert pm["group_id"] == snapshot["group_id"] == model["group_id"]
    assert pm["equipment_version_id"] == version["equipment_version_id"] != 1
    assert version["release_version_id"] == link["release_version_id"]
    assert json.loads(link["target_group_ids"]) == [model["group_id"]]
    assert json.loads(version["source_reference"])["version_raw"]["batch_id"] == version["batch_id"] != 1
    for table in ("equipment_snapshot", "equipment_version", "equipment_pm"):
        assert after[table][0] == before[table][0]
    assert service.preview(path, "add").changes == 0


def test_matching_item_number_and_releases_use_the_selected_merge_mode(target, tmp_path):
    existing = bundle()
    existing["release_note"] = [(1, 1, date(2026, 11, 1), 1)]
    existing["release_version"] = [(1, 1, 1, b"v1", None, date(2026, 11, 1), 1, "VERIFIED", 1, None, "[]")]
    existing["item"] = [(1, 1, 7, "기존 Item", 1, None, "[]", None)]
    existing["release_version_item"] = [(1, 1, "APPLIED", "ALL", None)]
    seed(target, existing)
    incoming = bundle(day=2)
    incoming["release_note"] = [(5, 1, date(2026, 11, 1), 1)]
    incoming["release_version"] = [(9, 5, 1, b"v1", None, date(2026, 11, 1), 1, "VERIFIED", 1, None, "[]")]
    incoming["item"] = [(13, 5, 7, "파일 Item", 1, None, "[]", None)]
    incoming["release_version_item"] = [(9, 13, "APPLIED", "ALL", None)]
    path = backup(tmp_path, incoming)
    service = target[2]

    service.apply(service.preview(path, "add"))
    retained, = current(target)["item"]
    assert (retained["item_id"], retained["content"]) == (1, "기존 Item")

    service.apply(service.preview(path, "overwrite"))
    merged = current(target)
    item, = merged["item"]
    link, = merged["release_version_item"]
    assert (item["item_id"], item["item_no"], item["content"]) == (1, 7, "파일 Item")
    assert (link["release_version_id"], link["item_id"]) == (1, 1)


def test_apply_rejects_preview_after_target_content_changes(target, tmp_path):
    seed(target, bundle())
    path = backup(tmp_path, bundle(line="파일 라인", day=2))
    engine, tables, service = target
    plan = service.preview(path, "overwrite")
    with engine.begin() as connection:
        connection.execute(tables["equipment_snapshot"].update().values(line="별도 변경"))
    changed = current(target)

    with pytest.raises(SqlImportError, match="미리보기 이후"):
        service.apply(plan)

    assert current(target) == changed


def test_apply_rolls_back_entire_merge_when_a_later_write_fails(target, tmp_path):
    existing = bundle()
    existing["equipment_pm"].append((2, 1, 1, b"PM2", 1, "O2", 1, None))
    seed(target, existing)
    path = backup(tmp_path, bundle(line="파일 라인", version=b"v2", day=2))
    engine, _, service = target
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TRIGGER reject_version_delete BEFORE DELETE ON equipment_version "
                                   "BEGIN SELECT RAISE(ABORT, 'test rollback'); END")
    plan = service.preview(path, "overwrite")
    before = current(target)

    with pytest.raises(DatabaseSaveError, match="저장"):
        service.apply(plan)

    assert current(target) == before


@pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")
def test_mysql_export_and_merge_round_trip(schema_database, tmp_path):  # noqa: F811
    from setron.features.peer_sync import is_sync_key, read_state

    def business(rows):
        # 새 교환 이력은 보존하되 기존 add/overwrite의 업무 값 동작을 비교한다.
        return {name: [row for row in records if name != "app_setting" or not is_sync_key(row["setting_key"])]
                for name, records in rows.items()}

    engine = schema_database.engine
    metadata = MetaData()
    metadata.reflect(engine, only=TABLE_NAMES)
    service = SqlImportService(lambda: Database.from_env(prefix="SETRON_TEST_DB_"))
    target = engine, metadata.tables, service
    seed(target, bundle())
    original = current(target)
    path = tmp_path / "실제 MySQL 내보내기.sql"
    assert export_database(schema_database, path).saved
    with engine.begin() as connection:
        connection.execute(metadata.tables["equipment_snapshot"].update().values(line="대상에서 변경"))
    modified = current(target)

    service.apply(service.preview(path, "add"))
    assert business(current(target)) == business(modified)
    assert read_state(current(target)).events

    service.apply(service.preview(path, "overwrite"))
    assert business(current(target)) == business(original)
    assert read_state(current(target)).events


def test_item_jira_code_merges_from_new_exports_and_old_exports_leave_it_empty(target, tmp_path):
    """11/12 item.jira_code: 대상 DB에 칸이 있을 때, 새 내보내기의 Jira는 들어오고 예전 내보내기(칸 없음)도 가져와진다."""
    for name, row, expected in (("new", (13, None, 7, "파일 Item", 1, None, "[]", "ETOJ-1793"), "ETOJ-1793"),
                                ("old", (14, None, 8, "예전 Item", 1, None, "[]"), None)):
        incoming = bundle(day=3 if name == "new" else 4)
        incoming["item"] = [row]
        path = tmp_path / f"{name}.sql"
        path.write_text(_export(incoming), encoding="utf-8")
        service = target[2]
        service.apply(service.preview(path, "add"))
        stored = {item["content"]: item.get("jira_code") for item in current(target)["item"]}
        assert stored[row[3]] == expected


@pytest.mark.parametrize("target", ["code", "key", "dual"], indirect=True)
@pytest.mark.parametrize("source_alias", ["jira_code", "jira_key"])
def test_jira_alias_import_maps_without_losing_any_value(target, tmp_path, source_alias):
    incoming = bundle()
    if source_alias == "jira_key":
        incoming["item"] = [(1, None, 1, "ETOJ-1, ETOJ-2", "원문", 1, None, "[]")]
        sql = _export(incoming, item_columns=LEGACY_KEY_ITEM_COLUMNS)
    else:
        incoming["item"] = [(1, None, 1, "원문", 1, None, "[]", "ETOJ-1, ETOJ-2")]
        sql = _export(incoming)
    path = tmp_path / "alias.sql"
    path.write_text(sql, encoding="utf-8")
    service = target[2]
    service.apply(service.preview(path, "add"))
    item, = current(target)["item"]
    assert item.get("jira_code", item.get("jira_key")) == "ETOJ-1, ETOJ-2"


@pytest.mark.parametrize("target", ["old", "key"], indirect=True)
def test_old_target_cannot_silently_drop_or_truncate_jira(target, tmp_path):
    incoming = bundle()
    incoming["item"] = [(1, None, 1, "원문", 1, None, "[]", "TEAM-" + "1" * 60)]
    path = backup(tmp_path, incoming)
    before = current(target)
    with pytest.raises(SqlImportError):
        target[2].preview(path, "add")
    assert current(target) == before


def test_old_columnless_import_derives_jira_from_unchanged_original_rows(target, tmp_path):
    incoming = bundle()
    raw = {"노트": {"A1": "Update내용", "B1": "JIRA", "A2": "원문", "B2": "ETOJ-987"}}
    incoming["import_batch"] = [(1, "EXCEL", datetime(2026, 11, 12), json.dumps(raw, ensure_ascii=False))]
    references = {"content": {"sheet": "노트", "cells": ["A2"]}}
    incoming["item"] = [(1, None, 1, "원문", 1, json.dumps(references), "[]")]
    path = tmp_path / "old-original.sql"
    path.write_text(_export(incoming, legacy_item=True), encoding="utf-8")
    original_bytes = path.read_bytes()
    service = target[2]
    service.apply(service.preview(path, "add"))
    item, = current(target)["item"]
    assert item["jira_code"] == "ETOJ-987" and item["edited_fields"] == "[]"
    assert json.loads(item["source_reference"]) == references
    assert path.read_bytes() == original_bytes
