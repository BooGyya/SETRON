"""BE-024 재검토에서 발견한 누락·부분 갱신을 실제 MySQL에서 검증한다."""

from datetime import date
import os

import pytest

from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.schemas import ImportBlockedError
from setron.features.excel_import.service import ImportValidationService, draft_from_review, edit_value
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.validation import StorageValidationError

from test_hpa_extraction_coverage import FIELDS, record, review, row_at, table
from test_import_save_mysql import count, storage  # noqa: F401
from test_schema_mysql import schema_database  # noqa: F401


pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")
DAY = date(2026, 11, 3)


def status_draft(code, *, hpa, pms=(None,), version="vTEST.1", base_date=DAY):
    """확인 가능한 원본 좌표를 가진 화성 현황 검토 자료를 만든다."""
    records = []
    for pm in pms:
        values = list(record(code, version, hpa=hpa, pm=pm))
        values[0] = base_date.isoformat()
        values[1] = "화성"
        records.append(values)
    sheet = table(*records)
    return draft_from_review(review(sheet, *(row_at(sheet, index) for index in range(1, len(records) + 1))))


@pytest.mark.parametrize("missing", ["equipment_code", "version_raw"])
def test_blank_separator_holds_all_rows_until_missing_hpa_is_completed(storage, missing):  # noqa: F811
    """구분행 뒤 필수 공란 HPA의 누락을 막고 원본 보존·보완·재저장을 확인한다."""
    suffix = "CODE" if missing == "equipment_code" else "SW"
    normal_code, hpa_code = f"GAP-GOOD-{suffix}-024", f"GAP-HPA-{suffix}-024"
    normal = list(record(normal_code, "v2", hpa=False))
    normal[1] = "화성"
    hpa = [None, None, "HPA", "HPA", "HPA", hpa_code, "vHPA.1", None]
    hpa[FIELDS.index(missing)] = None
    sheet = table(normal, [None] * len(FIELDS), hpa)
    draft = draft_from_review(review(sheet, row_at(sheet, 1)))
    validator = ImportValidationService(storage)
    before = count(storage, "import_batch")

    with pytest.raises(ImportBlockedError):
        DatabaseImportSaver(storage).save_many(validator.prepare((draft,), "gap-blocked"))

    assert count(storage, "import_batch") == before
    service = EquipmentService(storage)
    assert service.get_snapshot(normal_code, DAY) is None
    assert service.get_snapshot(hpa_code, DAY) is None

    completed = draft_from_review(review(sheet, row_at(sheet, 1), row_at(sheet, 3)))
    for field, value in (("base_date", DAY.isoformat()), ("site", "화성"),
                         (missing, hpa_code if missing == "equipment_code" else "vHPA.1")):
        completed = edit_value(completed, 2, field, value)
    receipt, = DatabaseImportSaver(storage).save_many(validator.prepare((completed,), "gap-completed"))
    saved = service.get_snapshot(hpa_code, DAY)
    assert saved.software[0].version_raw == "vHPA.1" and saved.production_area is None
    assert service.get_snapshot(normal_code, DAY) is not None
    with storage.transaction() as tx:
        original = tx.get("import_batch", {"batch_id": receipt.batch_id})["raw_data"][sheet.sheet_name]
    assert original[sheet.address_of(3, FIELDS.index(missing))] is None


@pytest.mark.parametrize("initial_hpa", [False, True], ids=["normal-to-hpa", "hpa-to-normal"])
def test_partial_pm_classification_change_rolls_back_the_entire_batch(storage, initial_hpa):  # noqa: F811
    """남겨진 PM과 새 PM의 분류가 충돌하면 앞선 신규 설비까지 저장하지 않는다."""
    code = f"MERGE-{'HPA' if initial_hpa else 'NORMAL'}-024"
    validator, saver = ImportValidationService(storage), DatabaseImportSaver(storage)
    original = status_draft(code, hpa=initial_hpa, pms=("PM1", "PM2"))
    saver.save_many(validator.prepare((original,), "merge-original"))
    service = EquipmentService(storage)
    before = service.get_snapshot(code, DAY)
    before_batches = count(storage, "import_batch")
    good = status_draft("MERGE-GOOD-024", hpa=False)
    changed = status_draft(code, hpa=not initial_hpa, pms=("PM1",), version="vNEW.2")

    with pytest.raises((ImportBlockedError, StorageValidationError), match="HPA|분석 제외"):
        saver.save_many(validator.prepare((good, changed), "merge-blocked"))

    assert count(storage, "import_batch") == before_batches
    assert service.get_snapshot(code, DAY) == before
    assert service.get_snapshot("MERGE-GOOD-024", DAY) is None


def test_ordinary_partial_pm_update_keeps_untouched_pm(storage):  # noqa: F811
    """일반 설비의 정상 부분 갱신과 미입력 PM 보존은 유지한다."""
    code = "MERGE-ORDINARY-024"
    validator, saver = ImportValidationService(storage), DatabaseImportSaver(storage)
    saver.save_many(validator.prepare((status_draft(code, hpa=False, pms=("PM1", "PM2")),), "ordinary-first"))
    saver.save_many(validator.prepare((status_draft(code, hpa=False, pms=("PM1",), version="vNEW.2"),), "ordinary-update"))
    saved = EquipmentService(storage).get_snapshot(code, DAY)
    assert {pm.pm_name: sw.version_raw for sw in saved.software for pm in sw.pms} == {"PM1": "vNEW.2", "PM2": "vTEST.1"}
    assert saved.production_area is None


@pytest.mark.parametrize("initial_hpa", [False, True], ids=["normal-to-hpa", "hpa-to-normal"])
def test_different_date_classification_conflict_requires_original_review(storage, initial_hpa):  # noqa: F811
    """타 날짜와 HPA 구분이 다르면 위치를 안내하고 정상 자료까지 저장하지 않는다."""
    code = f"HISTORY-{'HPA' if initial_hpa else 'NORMAL'}-024"
    validator, saver = ImportValidationService(storage), DatabaseImportSaver(storage)
    saver.save_many(validator.prepare((status_draft(code, hpa=initial_hpa),), "history-original"))
    service = EquipmentService(storage)
    before, before_batches = service.get_snapshot(code, DAY), count(storage, "import_batch")
    later = date(2026, 11, 4)
    good = status_draft(f"HISTORY-GOOD-{initial_hpa}-024", hpa=False, base_date=later)
    changed = status_draft(code, hpa=not initial_hpa, base_date=later)
    issues = validator.validate((good, changed))
    conflict = next(issue for issue in issues if issue.code == "HPA_HISTORY_CONFLICT")
    assert conflict.blocks_save and conflict.excel_position == (6, 6)
    assert conflict.workbook_name == changed.workbook_name and conflict.sheet_name == changed.sheet.sheet_name
    assert code in conflict.message and DAY.isoformat() in conflict.message and later.isoformat() in conflict.message

    with pytest.raises(ImportBlockedError, match="원본"):
        saver.save_many(validator.prepare((good, changed), "history-blocked"))

    assert count(storage, "import_batch") == before_batches
    assert service.get_snapshot(code, DAY) == before
    assert service.get_snapshot(code, later) is None
    assert service.get_snapshot(f"HISTORY-GOOD-{initial_hpa}-024", later) is None

    # 원본 호기의 오기를 사용자가 확인해 수정하면 별도 설비로 저장할 수 있다.
    corrected_code = f"HISTORY-CORRECTED-{initial_hpa}-024"
    corrected = edit_value(changed, 1, "equipment_code", corrected_code)
    saver.save_many(validator.prepare((good, corrected), "history-corrected"))
    assert service.get_snapshot(corrected_code, later) is not None
    assert service.get_snapshot(code, DAY) == before and service.get_snapshot(code, later) is None


def test_history_changed_after_review_is_rechecked_before_writing_any_request(storage):  # noqa: F811
    """검토 뒤 반대 분류의 과거 관측이 들어와도 저장 경계가 전체 묶음을 다시 차단한다."""
    code, later = "HISTORY-STALE-024", date(2026, 11, 4)
    validator, saver = ImportValidationService(storage), DatabaseImportSaver(storage)
    good = status_draft("HISTORY-STALE-GOOD-024", hpa=False, base_date=later)
    changed = status_draft(code, hpa=False, base_date=later)
    requests = validator.prepare((good, changed), "history-reviewed")
    saver.save_many(validator.prepare((status_draft(code, hpa=True),), "history-arrived-later"))
    service = EquipmentService(storage)
    before, before_batches = service.get_snapshot(code, DAY), count(storage, "import_batch")

    with pytest.raises(StorageValidationError, match="HPA|분류"):
        saver.save_many(requests)

    assert count(storage, "import_batch") == before_batches
    assert service.get_snapshot(code, DAY) == before
    assert service.get_snapshot(code, later) is None
    assert service.get_snapshot("HISTORY-STALE-GOOD-024", later) is None
