"""MySQL에 접속하지 않고 Jira 이관의 선행검사·DDL·NULL·최초채움 제어를 검증한다."""

from contextlib import contextmanager
from copy import deepcopy

import pytest

from setron.features.item import jira_upgrade as migration
from setron.features.item.original import OriginalItemRow


class Result:
    def __init__(self, rows=(), scalar=None):
        self.rows, self.value = rows, scalar

    def mappings(self):
        return self.rows

    def scalar(self):
        return self.value


class Engine:
    def __init__(self, columns, items, *, indexed=False, fail=False):
        self.columns = {name: {"name": name, "max_length": length, "charset": "utf8mb4", "collation": "utf8mb4_bin"}
                        for name, length in columns.items()}
        self.items, self.indexed, self.fail = deepcopy(items), indexed, fail
        self.statements = []

    @contextmanager
    def begin(self):
        yield self

    def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if "information_schema.COLUMNS" in sql:
            return Result(list(self.columns.values()))
        if "CAST(jira_code AS BINARY)" in sql:
            return Result(scalar=sum(row.get("jira_code") != row.get("jira_key") for row in self.items))
        if "information_schema.STATISTICS" in sql:
            return Result([{"index_name": "idx_item_jira_key", "column_name": "jira_key", "non_unique": 1,
                            "sequence": 1, "sub_part": None, "index_type": "BTREE"}] if self.indexed else [])
        assert sql.startswith("ALTER TABLE item ")
        if self.fail:
            raise RuntimeError("synthetic DDL rejection")
        if "ADD COLUMN jira_code" in sql:
            self.columns["jira_code"] = {"name": "jira_code", "max_length": 200}
            for row in self.items:
                row["jira_code"] = None
        if "CHANGE COLUMN jira_key jira_code" in sql:
            self.columns["jira_code"] = {**self.columns.pop("jira_key"), "name": "jira_code", "max_length": 200}
            for row in self.items:
                row["jira_code"] = row.pop("jira_key")
        if "MODIFY COLUMN jira_code" in sql:
            self.columns["jira_code"]["max_length"] = 200
        if "DROP COLUMN jira_key" in sql:
            self.columns.pop("jira_key")
            for row in self.items:
                row.pop("jira_key")
        return Result()


def test_key_only_migration_preserves_values_null_and_existing_evidence_with_safe_index():
    evidence = {"content": {"sheet": "노트", "cells": ["D4"]}, "jira_key": {"sheet": "노트", "cells": ["E4"]}}
    engine = Engine({"jira_key": 50}, [{"item_id": 1, "jira_key": "etoj-1", "source_reference": evidence},
                                      {"item_id": 2, "jira_key": None, "edited_fields": ["jira_key"]}], indexed=True)
    assert migration.ensure_jira_column(engine)
    assert set(engine.columns) == {"jira_code"} and engine.columns["jira_code"]["max_length"] == 200
    assert engine.items == [{"item_id": 1, "jira_code": "etoj-1", "source_reference": evidence},
                            {"item_id": 2, "jira_code": None, "edited_fields": ["jira_key"]}]
    ddl, = [sql for sql in engine.statements if sql.startswith("ALTER")]
    assert "CHANGE COLUMN jira_key jira_code VARCHAR(200)" in ddl
    assert "AFTER edited_fields" in ddl
    assert "ADD INDEX `idx_item_jira_key` (`jira_code`(50))" in ddl


@pytest.mark.parametrize("code,key", [(None, "ETOJ-1"), ("ETOJ-1", None), ("ETOJ-1", "etoj-1")])
def test_dual_column_conflict_including_null_fails_before_any_ddl(code, key):
    engine = Engine({"jira_code": 200, "jira_key": 50}, [{"item_id": 1, "jira_code": code, "jira_key": key}])
    before = deepcopy(engine.items)
    with pytest.raises(migration.JiraMigrationError, match="값이 다른"):
        migration.ensure_jira_column(engine)
    assert engine.items == before and not any(sql.startswith("ALTER") for sql in engine.statements)


def test_identical_dual_values_are_deduplicated_without_changing_null():
    engine = Engine({"jira_code": 200, "jira_key": 50}, [
        {"item_id": 1, "jira_code": "ETOJ-1", "jira_key": "ETOJ-1"},
        {"item_id": 2, "jira_code": None, "jira_key": None}], indexed=True)
    assert migration.ensure_jira_column(engine)
    assert set(engine.columns) == {"jira_code"}
    assert engine.items == [{"item_id": 1, "jira_code": "ETOJ-1"}, {"item_id": 2, "jira_code": None}]


def test_existing_code_column_and_null_are_left_unchanged():
    engine = Engine({"jira_code": 200}, [{"item_id": 1, "jira_code": None}])
    assert migration.ensure_jira_column(engine)
    assert engine.items[0]["jira_code"] is None
    assert not any(sql.startswith("ALTER") for sql in engine.statements)


def test_ddl_failure_keeps_previous_values_and_returns_unavailable():
    engine = Engine({"jira_key": 50}, [{"item_id": 1, "jira_key": None}], fail=True)
    assert not migration.ensure_jira_column(engine)
    assert set(engine.columns) == {"jira_key"} and engine.items[0]["jira_key"] is None


def test_only_first_addition_backfills_and_refreshes_storage(monkeypatch):
    from setron.features.equipment.repository import EquipmentRepository
    from setron.features.item import original

    engine = Engine({}, [{"item_id": 1, "content": "내용", "edited_fields": []}])

    class Storage:
        @contextmanager
        def transaction(self):
            yield self

        def get(self, table, key):
            return next(row for row in engine.items if row["item_id"] == key["item_id"])

        def update(self, table, key, values):
            self.get(table, key).update(values)

    storage, refreshed = Storage(), []
    monkeypatch.setattr(EquipmentRepository, "rows", lambda *args: engine.items)
    monkeypatch.setattr(original, "original_item_rows", lambda *args: (
        OriginalItemRow(1, 7, "노트", 4, ("JIRA", "내용"), ("ETOJ-1; ETOJ-2", "내용")),))
    monkeypatch.setattr(migration, "_refresh", lambda current: refreshed.append(current))
    assert migration.upgrade(storage, engine) == 1
    assert engine.items[0]["jira_code"] == "ETOJ-1, ETOJ-2" and refreshed == [storage]
    engine.items[0]["jira_code"] = None
    assert migration.upgrade(storage, engine) == 0
    assert engine.items[0]["jira_code"] is None and refreshed == [storage]
    assert len([sql for sql in engine.statements if sql.startswith("ALTER")]) == 1
