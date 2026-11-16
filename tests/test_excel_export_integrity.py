"""사용자용 두 시트 출력이 원문과 PM 소속을 보존하고 실패 파일을 공개하지 않는지 검사한다."""

from contextlib import closing
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import Column, String

from setron.features.ai.schemas import ExtractKind
from setron.features.export import management
from setron.features.export.management import (
    build_management_workbook, read_management_records, save_management_summary,
)
from setron.features.management_format import EQUIPMENT_HEADERS
from setron.features.rule_extraction.management_rules import parse_management_sheet
from setron.infrastructure.excel_reader import SheetData
from test_management_db_source import connected
from test_release_revision import revision_storage  # noqa: F401


@pytest.mark.parametrize(("content", "message"), [
    ("a" * 40_000, "32,767자"),
    ("control\0character", "제어 문자"),
], ids=["long-content", "control-character"])
def test_unrepresentable_content_is_rejected_without_replacing_destination(
        revision_storage, tmp_path, content, message):
    storage = revision_storage
    _, item_id, _, _ = connected(storage)
    with storage.transaction() as tx:
        tx.update("item", {"item_id": item_id}, {"content": content})
    path = tmp_path / "preserved.xlsx"
    path.write_bytes(b"original workbook")

    result = save_management_summary(storage, path)

    assert not result.saved and message in result.message
    assert "릴리즈노트!J2" in result.message
    assert path.read_bytes() == b"original workbook"
    assert list(tmp_path.iterdir()) == [path]


def test_excel_cell_limit_is_preserved_exactly_and_formula_like_content_stays_text(revision_storage, tmp_path):
    _, item_id, _, _ = connected(revision_storage)
    text = "=" + "a" * 32_766
    with revision_storage.transaction() as tx:
        tx.update("item", {"item_id": item_id}, {"content": text})
    path = tmp_path / "boundary.xlsx"

    assert save_management_summary(revision_storage, path).saved

    with closing(load_workbook(path)) as book:
        cell = book["릴리즈노트"]["J2"]
        assert cell.value == text and cell.data_type == "s"


@pytest.mark.parametrize("failure", ["save", "replace"])
def test_partial_write_and_replace_failures_preserve_destination_and_clean_own_temp(
        revision_storage, tmp_path, monkeypatch, failure):
    path = tmp_path / "preserved.xlsx"
    path.write_bytes(b"original workbook")

    def fail_save(_book, destination):
        assert Path(destination) != path
        Path(destination).write_bytes(b"partial zip")
        raise OSError("simulated disk failure")

    def fail_replace(source, destination):
        assert Path(source) != path and Path(destination) == path
        raise PermissionError("simulated locked workbook")

    if failure == "save":
        monkeypatch.setattr(Workbook, "save", fail_save)
    else:
        monkeypatch.setattr(management.os, "replace", fail_replace)

    result = save_management_summary(revision_storage, path)

    assert not result.saved and path.read_bytes() == b"original workbook"
    assert list(tmp_path.iterdir()) == [path]


def test_nullable_pm_links_never_cross_equipment_and_are_kept_beside_known_software(revision_storage):
    storage = revision_storage
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL"})["batch_id"]
        for suffix in ("A", "B"):
            equipment = tx.insert("equipment", {"equipment_code": "UNKNOWN-" + suffix})["equipment_id"]
            snapshot = tx.insert("equipment_snapshot", {
                "equipment_id": equipment, "base_date": date(2026, 11, 2), "site": "화성",
                "model_name": "M-O2", "group_id": 1, "batch_id": batch,
            })["snapshot_id"]
            tx.insert("equipment_pm", {"snapshot_id": snapshot, "equipment_version_id": None,
                                       "pm_name": "PM-" + suffix, "group_id": 1, "batch_id": batch})
            if suffix == "A":
                version = tx.insert("equipment_version", {"snapshot_id": snapshot, "family_id": 1,
                    "version_raw": "v1", "batch_id": batch})["equipment_version_id"]
                tx.insert("equipment_pm", {"snapshot_id": snapshot, "equipment_version_id": version,
                    "pm_name": "KNOWN-A", "group_id": 1, "batch_id": batch})

    records = read_management_records(storage)["설비현황표"]

    assert {(row["설비 호기"], row["PM"], row["SW 버전(원문)"]) for row in records} == {
        ("UNKNOWN-A", "KNOWN-A", "v1"), ("UNKNOWN-A", "PM-A", ""), ("UNKNOWN-B", "PM-B", ""),
    }
    with closing(build_management_workbook(storage)) as book:
        assert book["설비현황표"].max_row == 4


def test_process_column_round_trips_and_old_headers_are_still_accepted(revision_storage, tmp_path):
    storage = revision_storage
    with storage.database.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE equipment_snapshot ADD COLUMN process_name VARCHAR(100)")
    storage.repository.table("equipment_snapshot").append_column(Column("process_name", String(100)))
    _, _, snapshot, _ = connected(storage)
    process = "ETCH-A / ETCH-B"
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": snapshot.snapshot_id}, {"process_name": process})
    path = tmp_path / "process.xlsx"

    assert save_management_summary(storage, path).saved

    with closing(load_workbook(path)) as book:
        values = [list(row) for row in book["설비현황표"].values]
    assert tuple(values[0]) == EQUIPMENT_HEADERS and values[0][-1] == "공정"
    assert values[1][-1] == process
    sheet = SheetData(path.name, "설비현황표", 1, 1, values)
    imported = parse_management_sheet(ExtractKind.EQUIPMENT_STATUS, sheet)
    assert imported.rows[0].values["process_name"] == process
    assert imported.rows[0].sources["process_name"] == "S2"

    old_values = [row[:-1] for row in values]
    old = parse_management_sheet(ExtractKind.EQUIPMENT_STATUS,
                                SheetData("old.xlsx", "설비현황표", 1, 1, old_values))
    assert old is not None and len(old.rows) == len(imported.rows)
    assert "process_name" not in old.rows[0].values
