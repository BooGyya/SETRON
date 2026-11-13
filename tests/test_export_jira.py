"""저장 Jira를 우선 출력하며 이전 DB의 원본 Jira와 Excel 재가져오기도 보존한다."""

from contextlib import closing, contextmanager

import pytest
from openpyxl import load_workbook
from sqlalchemy import Table

from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.service import build_save_request, draft_from_review, validate
from setron.features.export.management import build_management_workbook, save_management_summary
from setron.features.extraction.service import review_extraction_result
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.release.service import ReleaseService
from setron.infrastructure.excel_reader import SheetData
from test_export_mentoring6 import configure_catalog, records
from test_management_db_source import connected
from test_release_revision import all_rows, release, revision_storage  # noqa: F401


@pytest.fixture
def legacy_storage(revision_storage):
    """이 테스트의 메모리 SQLite만 Jira 저장 열 도입 전 실제 열 구성으로 바꾼다."""
    storage = revision_storage
    assert storage.database.engine.dialect.name == "sqlite"
    with storage.database.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE item DROP COLUMN jira_code")
        metadata = storage.repository.metadata
        metadata.remove(storage.repository.table("item"))
        Table("item", metadata, autoload_with=connection)
    return storage


def jira_source(storage, *, seed=False, value="ETOJ-101, ETOJ-102\nETOJ-101"):
    rid, item_id, _, _ = connected(storage)
    item = next(row for row in all_rows(storage, "item") if row["item_id"] == item_id)
    cells = {"A1": "버전명", "B1": "Update내용", "C1": "JIRA", "D1": "비고",
             "A2": "v1", "B2": item["content"], "C2": value, "D2": "참고 ABC-77 반영"}
    raw = {"노트": cells}
    with storage.transaction() as tx:
        tx.update("import_batch", {"batch_id": item["batch_id"]},
                  {"raw_data": {"seed": "demo_seed.v2", "sheets": raw} if seed else raw})
    return rid, item_id, item["batch_id"]


@pytest.mark.parametrize("seed", [False, True])
def test_export_keeps_legacy_item_jira_from_import_and_seed_with_existing_filters(legacy_storage, tmp_path, seed):
    storage = legacy_storage
    _, item_id, batch_id = jira_source(storage, seed=seed)
    # 직접 수정한 현재 내용과, 현재 Item의 출처에 있는 Jira를 함께 출력한다.
    with storage.transaction() as tx:
        tx.update("item", {"item_id": item_id}, {"content": "현재 저장된 개선 내용"})
    before = {table: all_rows(storage, table) for table in ("item", "import_batch", "release_version_item")}
    for selected in (None, (batch_id,)):
        for area in (None, "MEMORY"):
            path = tmp_path / f"jira-{selected}-{area}.xlsx"
            assert save_management_summary(storage, path, batch_ids=selected, production_area=area).saved
            with closing(load_workbook(path)) as book:
                assert book.sheetnames == ["설비현황표", "릴리즈노트"]
                row = records(book["릴리즈노트"])[0]
                assert row["내용"] == "현재 저장된 개선 내용"
                # JIRA 열이 있으면 비고 열의 코드는 합치지 않는다 (11-12 멘토 답변 · 가져오기 규칙과 같음, 11-13)
                assert row["Jira 코드"] == "ETOJ-101, ETOJ-102"
    assert records(build_management_workbook(storage, batch_ids=(batch_id,), production_area="FOUNDRY")["릴리즈노트"]) == []
    assert {table: all_rows(storage, table) for table in before} == before


def test_export_does_not_invent_jira_for_empty_missing_or_deleted_items(revision_storage):
    storage = revision_storage
    _, item_id, batch_id = jira_source(storage)
    with storage.transaction() as tx:
        raw = tx.get("import_batch", {"batch_id": batch_id})["raw_data"]
        raw["노트"]["C2"], raw["노트"]["D2"] = "-", "자유 비고"
        tx.update("import_batch", {"batch_id": batch_id}, {"raw_data": raw})
        unlinked = tx.insert("item", {"item_no": 8, "content": "연결 전 Item", "batch_id": batch_id,
                                      "edited_fields": []})
    release(storage, raw="v2", order=2)  # Item이 없는 릴리즈도 행을 유지한다.
    rows = records(build_management_workbook(storage)["릴리즈노트"])
    assert len(rows) == 3 and all(not row["Jira 코드"] for row in rows)
    with storage.transaction() as tx:
        tx.delete_release_item(item_id)
        tx.delete_release_item(unlinked["item_id"])
    rows = records(build_management_workbook(storage)["릴리즈노트"])
    assert len(rows) == 2 and all(not row["Jira 코드"] and not row["내용"] for row in rows)


def test_unlinked_legacy_item_can_export_jira_from_merged_source_batches(legacy_storage):
    storage = legacy_storage
    _, _, first_batch = jira_source(storage)
    with storage.transaction() as tx:
        other = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": {"추가": {
            "B1": "내용", "C1": "Jira Key", "B2": "추가 출처", "C2": "ETOJ-102; ETOJ-103"}}})
        tx.insert("item", {"item_no": 8, "content": "합친 Item", "batch_id": first_batch,
                           "edited_fields": ["content"], "source_reference": {"content": [
            {"batch_id": first_batch, "sheet": "노트", "cells": ["B2"]},
            {"batch_id": other["batch_id"], "sheet": "추가", "cells": ["B2"]}]}})
    rows = records(build_management_workbook(storage, batch_ids=(other["batch_id"],))["릴리즈노트"])
    assert len(rows) == 1 and not rows[0]["버전(원문)"]
    assert rows[0]["Jira 코드"] == "ETOJ-101, ETOJ-102, ETOJ-103"


def test_legacy_jira_cell_is_literal_text_even_when_it_starts_with_formula_prefix(legacy_storage, tmp_path):
    storage = legacy_storage
    jira_source(storage, value="=1+1")
    path = tmp_path / "literal-jira.xlsx"
    assert save_management_summary(storage, path).saved
    with closing(load_workbook(path)) as book:
        sheet = book["릴리즈노트"]
        column = [cell.value for cell in sheet[1]].index("Jira 코드") + 1
        assert sheet.cell(2, column).value == "=1+1"
        assert sheet.cell(2, column).data_type == "s"


@pytest.mark.parametrize("jira_code", ["ETOJ-9073", None])
def test_stored_jira_edit_or_explicit_clear_overrides_original_for_linked_and_unlinked_items(revision_storage, jira_code):
    storage = revision_storage
    _, item_id, batch_id = jira_source(storage)
    service = ReleaseService(storage)
    service.update_item(item_id, {"jira_code": "ETOJ-9073"})
    service.update_item(item_id, {"jira_code": jira_code})
    with storage.transaction() as tx:
        tx.insert("item", {"item_no": 8, "content": "연결 전 저장 Item", "jira_code": jira_code,
                           "batch_id": batch_id, "source_reference": {"content": {"sheet": "노트", "cells": ["B2"]}},
                           "edited_fields": []})
    before = {table: all_rows(storage, table) for table in ("item", "import_batch", "release_version_item")}
    for selected in (None, (batch_id,)):
        with closing(build_management_workbook(storage, batch_ids=selected)) as book:
            rows = records(book["릴리즈노트"])
            assert len(rows) == 2 and all(row["Jira 코드"] == (jira_code or "") for row in rows)
    assert {table: all_rows(storage, table) for table in before} == before


def test_stored_jira_survives_export_and_real_reimport_to_another_database(revision_storage):
    storage = revision_storage
    _, item_id, _ = jira_source(storage)
    ReleaseService(storage).update_item(item_id, {"jira_code": "ETOJ-9073"})
    with closing(build_management_workbook(storage)) as book:
        values = [list(row) for row in book["릴리즈노트"].iter_rows(values_only=True)]
    sheet = SheetData("jira.xlsx", "릴리즈노트", 1, 1, values)
    from setron.features.rule_extraction.service import rule_extract
    from test_release_revision import revision_storage as storage_fixture
    response = rule_extract(ExtractKind.RELEASE_NOTE, sheet)
    reviewed = review_extraction_result(ExtractKind.RELEASE_NOTE, sheet, response)
    assert response.ok and reviewed.ok
    with contextmanager(storage_fixture.__wrapped__)() as target:
        configure_catalog(target)
        saver = DatabaseImportSaver(target)
        draft = draft_from_review(reviewed.data, source=response.source)
        reference = saver.load_reference((draft,))
        assert not [issue for issue in validate(draft, reference) if issue.blocks_save]
        saver.save(build_save_request(draft, "jira-roundtrip", reference))
        rows = records(build_management_workbook(target)["릴리즈노트"])
        assert len(rows) == 1 and rows[0]["Jira 코드"] == "ETOJ-9073"
        assert all_rows(target, "item")[0]["jira_code"] == "ETOJ-9073"
