"""공정의 복합 값과 입력 작업별 원본 위치가 Excel 저장에서 사라지지 않는다."""

import os
from dataclasses import replace

import pytest
from sqlalchemy import Column, String

from setron.features.excel_import.schemas import DuplicateSaveError, IssueLevel
from setron.features.excel_import.service import ImportValidationService
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.validation import StorageValidationError

from test_import_validation import draft, row
from test_import_save_mysql import storage as mysql_storage
from test_schema_mysql import schema_database  # noqa: F401
from test_snapshot_reimport import count, snapshot, storage as base_storage


@pytest.fixture
def storage():
    setup = base_storage.__wrapped__()
    value = next(setup)
    try:
        table = value.repository.table("equipment_snapshot")
        if "process_name" not in table.c:
            with value.database.engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE equipment_snapshot ADD COLUMN process_name VARCHAR(100)")
            table.append_column(Column("process_name", String(100)))
        yield value
    finally:
        setup.close()


def save_sources(storage, sources, request_id):
    requests = ImportValidationService(storage).prepare(sources, request_id)
    return DatabaseImportSaver(storage).save_many(requests)


def process_originals(storage, stored):
    reference = (stored.source_reference or {}).get("process_name")
    locations = reference if isinstance(reference, list) else [reference] if reference else []
    result = []
    with storage.transaction() as tx:
        for location in locations:
            batch_id = location.get("batch_id", stored.batch_id)
            raw = tx.get("import_batch", {"batch_id": batch_id})["raw_data"]
            result.extend((location["sheet"], batch_id, raw[location["sheet"]][cell])
                          for cell in location["cells"])
    return result


@pytest.mark.parametrize("previous,incoming,expected", [
    (None, "NEW", "NEW"),
    ("OLD", "NEW", "NEW"),
    ("OLD", "  NEW  ", "NEW"),
    ("OLD", None, "OLD"),
    ("OLD", "  ", "OLD"),
    ("OLD", "OLD", "OLD"),
    ("AAAA/BBBB", "AAAA/BBBB", "AAAA/BBBB"),
    ("AAAA & BBBB", "AAAA & BBBB", "AAAA & BBBB"),
])
def test_process_review_compares_the_value_that_reimport_will_save(storage, previous, incoming, expected):
    """공정만 바뀌어도 기존값·새 값을 표시하고, 공란 보존을 변경으로 안내하지 않는다."""
    save_sources(storage, (draft(row(process_name=previous)),), "initial-process")
    before = snapshot(storage)
    original_sources = process_originals(storage, before)
    sources = (draft(row(process_name=incoming)),)
    validator = ImportValidationService(storage)

    issues = validator.validate(sources)
    changes = [issue for issue in issues if issue.code == "STORED_VALUE_CHANGED"
               and issue.field == "process_name"]
    if previous != expected:
        assert len(changes) == 1
        assert (changes[0].previous_value, changes[0].current_value) == (previous, expected)
        assert changes[0].level is IssueLevel.REVIEW
        assert not any(issue.code == "STORED_VALUE_IDENTICAL" for issue in issues)
    else:
        assert not changes
        assert any(issue.code == "STORED_VALUE_IDENTICAL" for issue in issues)
    assert not any(issue.blocks_save for issue in issues)

    requests = validator.prepare(sources, "reviewed-process")
    assert snapshot(storage) == before
    DatabaseImportSaver(storage).save_many(requests)
    after = snapshot(storage)
    assert after.process_name == expected and after.snapshot_id == before.snapshot_id
    assert process_originals(storage, before) == original_sources


@pytest.mark.parametrize("separate_sheets", [False, True])
@pytest.mark.parametrize("previous", ["OLD", "A / B"])
def test_process_review_uses_combined_pm_and_sheet_values(storage, separate_sheets, previous):
    """여러 PM·시트의 최종 복합공정을 비교하며 중복·공란·제외한 행은 합산하지 않는다."""
    initial = tuple(row(i, pm_name=f"PM{i}", process_name=previous) for i in range(1, 5))
    save_sources(storage, (draft(*initial),), "initial-processes")
    members = tuple(row(i, pm_name=f"PM{i}", process_name=value)
                    for i, value in enumerate(("A", "B", " A ", None), 1))
    excluded = replace(row(5, pm_name="PM5", process_name="IGNORED"), excluded=True)
    sources = ((draft(members[0], members[2], excluded, sheet="first"),
                draft(members[1], members[3], sheet="second")) if separate_sheets
               else (draft(*members, excluded),))
    validator = ImportValidationService(storage)

    issues = validator.validate(sources)
    changes = [issue for issue in issues if issue.code == "STORED_VALUE_CHANGED"
               and issue.field == "process_name"]
    if previous == "OLD":
        assert len(changes) == len(members)
        assert {(issue.previous_value, issue.current_value) for issue in changes} == {("OLD", "A / B")}
        assert not any(issue.code == "STORED_VALUE_IDENTICAL" for issue in issues)
    else:
        assert not changes
        assert sum(issue.code == "STORED_VALUE_IDENTICAL" for issue in issues) == len(members)
    assert not any(issue.blocks_save for issue in issues)

    requests = validator.prepare(sources, "combined-processes")
    DatabaseImportSaver(storage).save_many(requests)
    assert snapshot(storage).process_name == "A / B"


@pytest.mark.parametrize("multiple_sheets", [False, True])
def test_combined_process_overflow_rejects_the_whole_save_without_truncation(storage, multiple_sheets):
    first, second = row(process_name="A" * 60), row(2, pm_name="PM2", process_name="B" * 60)
    sources = ((draft(first, sheet="first"), draft(second, sheet="second")) if multiple_sheets
               else (draft(first, second),))
    requests = ImportValidationService(storage).prepare(sources, "process-too-long")
    tables = ("import_batch", "equipment", "equipment_snapshot", "equipment_version", "equipment_pm")
    before = {name: count(storage, name) for name in tables}

    with pytest.raises(StorageValidationError, match="합친 공정이 100자를 넘습니다"):
        DatabaseImportSaver(storage).save_many(requests)

    assert {name: count(storage, name) for name in tables} == before
    assert snapshot(storage) is None


@pytest.mark.parametrize("already_saved", [False, True])
def test_processes_from_multiple_sheets_keep_every_source_and_replace_old_value(storage, already_saved):
    if already_saved:
        save_sources(storage, (draft(row(process_name="OLD")),), "initial")
    receipts = save_sources(storage, (
        draft(row(process_name="ETCH-A"), sheet="sheetA"),
        draft(row(pm_name="PM2", process_name="ETCH-B"), sheet="sheetB"),
    ), "two-processes")

    stored = snapshot(storage)
    assert stored.process_name == "ETCH-A / ETCH-B"
    assert stored.batch_id == receipts[1].batch_id
    assert process_originals(storage, stored) == [
        ("sheetA", receipts[0].batch_id, "ETCH-A"),
        ("sheetB", receipts[1].batch_id, "ETCH-B"),
    ]
    assert {pm.pm_name for software in stored.software for pm in software.pms} == {"PM1", "PM2"}


@pytest.mark.parametrize("known_first", [False, True])
def test_blank_sheet_keeps_the_known_process_and_its_actual_batch(storage, known_first):
    known = draft(row(process_name="ETCH-A"), sheet="known")
    blank = draft(row(pm_name="PM2"), sheet="blank")
    receipts = save_sources(storage, (known, blank) if known_first else (blank, known), "known-and-blank")
    owner = 0 if known_first else 1

    stored = snapshot(storage)
    assert stored.process_name == "ETCH-A"
    assert process_originals(storage, stored) == [("known", receipts[owner].batch_id, "ETCH-A")]


def test_blank_reimport_keeps_all_previous_process_sources_with_original_batch_ids(storage):
    receipts = save_sources(storage, (
        draft(row(process_name="ETCH-A"), sheet="sheetA"),
        draft(row(pm_name="PM2", process_name="ETCH-B"), sheet="sheetB"),
    ), "original-processes")
    before = snapshot(storage)
    expected = process_originals(storage, before)
    later, = save_sources(storage, (draft(row(), sheet="blank-reimport"),), "blank-reimport")

    stored = snapshot(storage)
    assert stored.process_name == "ETCH-A / ETCH-B" and stored.batch_id == later.batch_id
    assert process_originals(storage, stored) == expected
    assert {batch_id for _, batch_id, _ in expected} == {receipt.batch_id for receipt in receipts}


@pytest.mark.parametrize("process", ["AAAA/BBBB", "12.9/14.0", "AAAA & BBBB"])
def test_one_compound_process_is_not_split_or_reformatted(storage, process):
    receipt, = save_sources(storage, (draft(row(process_name=process)),), "compound-process")
    stored = snapshot(storage)
    assert stored.process_name == process
    assert process_originals(storage, stored) == [("현황", receipt.batch_id, process)]


def test_repeated_processes_across_sheets_are_not_duplicated_and_request_cannot_save_twice(storage):
    sources = (draft(row(process_name="A"), row(2, pm_name="PM2", process_name="B"), sheet="first"),
               draft(row(pm_name="PM3", process_name="B"), sheet="second"))
    requests = ImportValidationService(storage).prepare(sources, "repeated-process")
    saver = DatabaseImportSaver(storage)
    saver.save_many(requests)
    stored, batches = snapshot(storage), count(storage, "import_batch")
    assert stored.process_name == "A / B"
    assert len(process_originals(storage, stored)) == 3

    with pytest.raises(DuplicateSaveError):
        saver.save_many(requests)

    assert snapshot(storage) == stored and count(storage, "import_batch") == batches


@pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")
def test_mysql_process_sources_survive_blank_reimport_and_overflow_rolls_back(schema_database):
    value = mysql_storage.__wrapped__(schema_database)
    save_sources(value, (draft(row(process_name="ETCH-A"), sheet="first"),
                         draft(row(pm_name="PM2", process_name="ETCH-B"), sheet="second")), "mysql-processes")
    originals = process_originals(value, snapshot(value))
    save_sources(value, (draft(row(), sheet="blank"),), "mysql-blank")
    assert snapshot(value).process_name == "ETCH-A / ETCH-B"
    assert process_originals(value, snapshot(value)) == originals
    tables = ("import_batch", "equipment", "equipment_snapshot", "equipment_version", "equipment_pm")
    before = {name: count(value, name) for name in tables}
    requests = ImportValidationService(value).prepare((draft(
        row(equipment_code="MYSQL-OVERFLOW", process_name="A" * 60),
        row(2, equipment_code="MYSQL-OVERFLOW", pm_name="PM2", process_name="B" * 60)),), "mysql-overflow")

    with pytest.raises(StorageValidationError, match="합친 공정이 100자를 넘습니다"):
        DatabaseImportSaver(value).save_many(requests)

    assert {name: count(value, name) for name in tables} == before
