"""시연 덤프 보완의 재현성·원본 보존·필수 SW와 출처 연결을 검사한다."""

from collections import Counter
from copy import deepcopy
import importlib
from pathlib import Path
import re
import sqlite3
import sys

import pytest

from setron.features.data_admin.service import sheet_names
from setron.features.storage.validation import encode_record
from setron.features.targets.groups import _current

DATABASE = Path(__file__).resolve().parents[1] / "database"


@pytest.fixture(scope="module")
def builder():
    sys.path.insert(0, str(DATABASE))
    try:
        return importlib.import_module("build_demo_seed")
    finally:
        sys.path.remove(str(DATABASE))


@pytest.fixture(scope="module")
def baseline(builder):
    dump = builder.read_sql((DATABASE / "SETRON_DB.sql").read_text(encoding="utf-8"))
    rows = deepcopy(dump.rows)
    builder._remove_generated(rows)
    models = {r["group_id"]: r["group_name"] for r in rows["equipment_group"]}
    for row in rows["equipment_snapshot"]:
        row["production_area"] = None
        row["model_name"] = models[row["group_id"]]
    dump.rows = rows
    return dump


def test_area_and_hpa_cover_both_production_areas(builder, baseline):
    rows = builder.augment(baseline.rows)
    assert len(rows["equipment"]) == 53
    assert len(rows["equipment_snapshot"]) == len(rows["equipment_version"]) == 159
    originals = rows["equipment_snapshot"][:150]
    assert Counter(r["production_area"] for r in originals) == {"MEMORY": 114, "FOUNDRY": 36}
    hpa = next(r for r in rows["equipment_group"] if r["group_name"] == "HPA")
    assert hpa["analysis_enabled"] == 0
    snapshots = [r for r in rows["equipment_snapshot"] if r["group_id"] == hpa["group_id"]]
    assert len({r["equipment_id"] for r in snapshots}) == 3
    assert Counter(r["production_area"] for r in snapshots) == {"MEMORY": 6, "FOUNDRY": 3}
    assert all(r["version_raw"] and r["release_version_id"] for r in rows["equipment_version"])
    assert {r["family_id"] for r in rows["release_version"]} == {f["family_id"] for f in rows["version_family"]}


def test_original_records_risk_rules_and_saved_targets_are_preserved(builder, baseline):
    rows = builder.augment(baseline.rows)
    for table, old in baseline.rows.items():
        if table != "equipment_snapshot":
            assert all(row in rows[table] for row in old), table
    for old, new in zip(baseline.rows["equipment_snapshot"], rows["equipment_snapshot"], strict=False):
        assert {k: v for k, v in old.items() if k not in {"production_area", "model_name"}} == {
            k: v for k, v in new.items() if k not in {"production_area", "model_name"}}
    restored = [r for r in rows["equipment_snapshot"] if r["model_name"] == "M-C3 (통합UI)"]
    assert len(restored) == 30
    releases = {r["release_version_id"]: r for r in rows["release_version"]}
    for setting in rows["app_setting"]:
        if setting["setting_key"].startswith("version_groups.v1:"):
            pair = tuple(map(int, setting["setting_key"].split(":")[-2:]))
            current = _current(setting, pair)
            for release_id in current.entry.groups.to_dict()["N"]:
                assert releases[release_id]["family_id"] == pair[1]


def test_synthetic_sources_use_real_cells_and_hidden_metadata(builder, baseline):
    rows = builder.augment(baseline.rows)
    batches = {r["batch_id"]: r for r in rows["import_batch"][-2:]}
    assert {sheet_names(b["raw_data"]) for b in batches.values()} == {(builder.RELEASE_SHEET,), (builder.STATUS_SHEET,)}
    assert all(b["source_type"] == "MANUAL" and b["raw_data"][builder.META_KEY]["synthetic"] is True for b in batches.values())
    equipment = {r["equipment_id"]: r["equipment_code"] for r in rows["equipment"]}
    for table in ("release_version", "item", "equipment_snapshot", "equipment_version"):
        for row in rows[table]:
            if row["batch_id"] not in batches:
                continue
            raw = batches[row["batch_id"]]["raw_data"]
            for field, source in row["source_reference"].items():
                actual = equipment[row["equipment_id"]] if field == "equipment_code" else row[field]
                assert [raw[source["sheet"]][cell] for cell in source["cells"]] == [actual]


def test_full_dump_satisfies_primary_unique_and_foreign_keys(builder, baseline):
    rows = builder.augment(baseline.rows)
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        for table, definition in baseline.definitions.items():
            constraints = []
            for line in definition.splitlines()[1:]:
                column = re.match(r"  `(\w+)` (.*)", line)
                if column:
                    constraints.append(f'"{column[1]}"' + (" NOT NULL" if "NOT NULL" in column[2] else ""))
                elif line.startswith(("  PRIMARY KEY", "  CONSTRAINT")):
                    constraints.append(line.strip().rstrip(","))
                elif line.startswith("  UNIQUE KEY"):
                    constraints.append("UNIQUE " + line[line.index("("):].rstrip(","))
            connection.execute(f"CREATE TABLE {table} (" + ", ".join(constraints) + ")")
            columns = builder.columns_from_definition(table, definition)
            placeholders = ", ".join("?" for _ in columns)
            for row in rows[table]:
                encoded = encode_record(table, row)
                connection.execute(f"INSERT INTO {table} VALUES ({placeholders})", builder.values_for_columns(table, encoded, columns))
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_generation_is_byte_reproducible_and_retains_initializer_wrapper(builder, baseline):
    seed = builder.render(baseline, baseline.rows)
    first = builder.build(seed)
    assert builder.build(first) == first
    prefix = "-- 시연 초기화\nCREATE DATABASE IF NOT EXISTS setron;\nUSE setron;\n\n"
    assert builder.build(prefix + seed) == prefix + first


def test_main_preserves_source_and_the_two_variants(builder, baseline, tmp_path, monkeypatch):
    seed = builder.render(baseline, baseline.rows)
    source = tmp_path / "original.sql"
    source.write_text(seed, encoding="utf-8")
    source_bytes = source.read_bytes()
    setup_rows = deepcopy(baseline.rows)
    setup_rows["risk_rule"] = []
    setup = tmp_path / "initializer.sql"
    setup.write_text(builder.render(baseline, setup_rows), encoding="utf-8")
    working = tmp_path / "working.sql"
    monkeypatch.setattr(builder, "ORIGINAL", source)
    monkeypatch.setattr(builder, "WORKING", working)
    monkeypatch.setattr(builder, "INITIALIZER", setup)
    builder.main()
    assert source.read_bytes() == source_bytes
    first = working.read_bytes(), setup.read_bytes()
    assert len(builder.read_sql(working.read_text(encoding="utf-8")).rows["risk_rule"]) == 2
    assert not builder.read_sql(setup.read_text(encoding="utf-8")).rows["risk_rule"]
    builder.main()
    assert (working.read_bytes(), setup.read_bytes()) == first
