"""부분 재가져오기에서 공통정보의 명시값과 원본 입력 작업을 함께 보존한다."""

import pytest

from setron.features.excel_import.service import ImportValidationService
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.validation import StorageValidationError

from test_import_validation import draft, row
from test_snapshot_reimport import count, original_value, save, snapshot, storage  # noqa: F401


@pytest.mark.parametrize("field,value", [
    ("line", "14L"), ("production_area", "MEMORY"), ("structure_type", "INLINE"),
    ("geometry_type", "5각"), ("modification_type", "CIP"),
])
@pytest.mark.parametrize("known_first", [False, True])
def test_one_sheet_keeps_the_only_known_common_value_and_its_original(storage, field, value, known_first):
    """첫 행의 공란 때문에 같은 시트 다른 PM 행의 공통정보가 사라지지 않는다."""
    values = (value, None) if known_first else (None, value)
    receipt = save(storage, row(**{field: values[0]}), row(2, pm_name="PM2", **{field: values[1]}),
                   request_id=f"one-sheet-{field}-{known_first}")
    stored = snapshot(storage)
    assert getattr(stored, field) == value and stored.batch_id == receipt.batch_id
    source = stored.source_reference[field]
    with storage.transaction() as tx:
        raw = tx.get("import_batch", {"batch_id": stored.batch_id})["raw_data"]
    originals = [raw[source["sheet"]][cell] for cell in source["cells"]]
    assert value in originals


@pytest.mark.parametrize("known_first", [False, True])
@pytest.mark.parametrize("already_saved", [False, True])
def test_multiple_sheets_keep_known_metadata_regardless_of_order(storage, known_first, already_saved):
    """값이 있는 시트를 공통정보 출처로 쓰고 이후 공란 시트가 이를 덮어쓰지 않는다."""
    if already_saved:
        save(storage, row(line="12L"), row(2, pm_name="PM2", line="12L"), request_id="initial")
    known = draft(row(pm_name="PM1", line="14L", geometry_type="5각"), sheet="공통정보 있음")
    blank = draft(row(pm_name="PM2", line=None, geometry_type=None), sheet="공통정보 공란")
    sources = (known, blank) if known_first else (blank, known)
    requests = ImportValidationService(storage).prepare(sources, "metadata-bundle")
    receipts = DatabaseImportSaver(storage).save_many(requests)
    stored = snapshot(storage)
    owner = 0 if known_first else 1
    assert stored.line == "14L" and stored.geometry_type == "5각"
    assert stored.batch_id == receipts[owner].batch_id
    assert stored.source_reference["line"]["sheet"] == "공통정보 있음"
    assert original_value(storage, stored, "line") == "14L"
    assert original_value(storage, stored, "geometry_type") == "5각"
    assert {pm.pm_name for sw in stored.software for pm in sw.pms} == {"PM1", "PM2"}


def test_last_complete_metadata_sheet_owns_the_snapshot_source(storage):
    """모든 공통정보가 같은 시트가 여러 개면 마지막 시트의 입력 작업을 유지한다."""
    sources = (draft(row(line="14L"), sheet="먼저 확인"),
               draft(row(pm_name="PM2", line="14L"), sheet="나중 확인"))
    requests = ImportValidationService(storage).prepare(sources, "complete-twice")
    receipts = DatabaseImportSaver(storage).save_many(requests)
    stored = snapshot(storage)
    assert stored.batch_id == receipts[-1].batch_id
    assert stored.source_reference["line"]["sheet"] == "나중 확인"
    assert original_value(storage, stored, "line") == "14L"


def test_metadata_split_across_sheets_without_complete_source_holds_the_whole_batch(storage):
    """라인과 형상이 서로 다른 원본 작업에만 있으면 출처를 합성하지 않고 전체를 보류한다."""
    save(storage, row(line="12L", geometry_type="6각"), row(2, pm_name="PM2", line="12L", geometry_type="6각"))
    before, before_batches = snapshot(storage), count(storage, "import_batch")
    sources = (draft(row(line="14L", geometry_type=None), sheet="라인만 있음"),
               draft(row(pm_name="PM2", line=None, geometry_type="5각"), sheet="형상만 있음"))
    requests = ImportValidationService(storage).prepare(sources, "no-complete-source")
    with pytest.raises(StorageValidationError, match="한 시트에서 함께 확인"):
        DatabaseImportSaver(storage).save_many(requests)
    assert snapshot(storage) == before and count(storage, "import_batch") == before_batches


def test_complete_metadata_sheet_can_combine_with_partial_sheets(storage):
    """전체 공통정보를 가진 시트가 있으면 부분 시트들의 PM도 함께 저장한다."""
    sources = (draft(row(line="14L"), sheet="라인 일부"),
               draft(row(pm_name="PM2", geometry_type="5각"), sheet="형상 일부"),
               draft(row(pm_name="PM3", line="14L", geometry_type="5각"), sheet="전체 확인"))
    requests = ImportValidationService(storage).prepare(sources, "complete-source")
    receipts = DatabaseImportSaver(storage).save_many(requests)
    stored = snapshot(storage)
    assert stored.line == "14L" and stored.geometry_type == "5각"
    assert stored.batch_id == receipts[-1].batch_id
    assert original_value(storage, stored, "line") == "14L"
    assert original_value(storage, stored, "geometry_type") == "5각"


def test_all_blank_common_metadata_remains_an_explicit_clear(storage):
    """모든 시트에서 비운 값은 이전 값을 임의로 보충하지 않고 마지막 원본으로 갱신한다."""
    save(storage, row(line="12L"), row(2, pm_name="PM2", line="12L"))
    sources = (draft(row(line=None), sheet="첫 공란"),
               draft(row(pm_name="PM2", line=None), sheet="마지막 공란"))
    requests = ImportValidationService(storage).prepare(sources, "clear-line")
    receipts = DatabaseImportSaver(storage).save_many(requests)
    stored = snapshot(storage)
    assert stored.line is None and stored.batch_id == receipts[-1].batch_id
