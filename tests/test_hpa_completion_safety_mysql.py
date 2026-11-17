"""BE-024 추가 재검토의 제외·관리 수정·여러 시트 보완을 실제 MySQL에서 검증한다."""

from datetime import date
import os

import pytest

from setron.features.equipment.read import read_equipment_record
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.schemas import ImportBlockedError
from setron.features.excel_import.service import ImportValidationService, draft_from_review, edit_value, set_excluded
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.management.schemas import RecordEdit
from setron.features.management.service import ManagementService
from setron.features.storage.validation import StorageValidationError
from setron.ui.database_source import load_dashboard_source
from setron.ui.query_state import QueryState

from test_hpa_extraction_coverage import record, review, row_at, table
from test_hpa_reimport_safety_mysql import status_draft
from test_import_save_mysql import _family_id, _group_id, count, storage  # noqa: F401
from test_schema_mysql import schema_database  # noqa: F401


pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")
DAY, NEXT_DAY = date(2026, 11, 3), date(2026, 11, 4)


def managed_edit(service, table_name, key, changes):
    """실제 관리 화면과 같이 현재 지문을 읽고 수정 요청을 만든다."""
    current = service.get_record(table_name, key)
    return RecordEdit(table=table_name, key=key, revision=current.revision, changes=changes)


@pytest.mark.parametrize("classifiers", ["family", "none"])
@pytest.mark.parametrize("missing", ["equipment_code", "version_raw"])
def test_excluded_hpa_with_missing_classifiers_still_holds_all_rows(storage, classifiers, missing):  # noqa: F811
    """AI 분류 누락과 행 제외가 원본 HPA 필수 오류를 숨기지 못하며 보완 후에만 저장한다."""
    suffix = f"{classifiers}-{missing}"
    normal_code, hpa_code = f"EXCLUDE-GOOD-{suffix}", f"EXCLUDE-HPA-{suffix}"
    rows = [list(record(normal_code, "v1", hpa=False)), list(record(hpa_code, "vHPA"))]
    for values in rows:
        values[1] = "화성"
    rows[1][5 if missing == "equipment_code" else 6] = None
    sheet = table(*rows)
    normal, hpa = row_at(sheet, 1), row_at(sheet, 2)
    for field in (("group_name", "model_name") if classifiers == "family" else ("group_name", "model_name", "family_name")):
        hpa["values"][field] = None
        hpa["sources"].pop(field)
    source = draft_from_review(review(sheet, normal, hpa))
    excluded = set_excluded(source, 2, True)
    validator, saver = ImportValidationService(storage), DatabaseImportSaver(storage)
    before = count(storage, "import_batch")

    with pytest.raises(ImportBlockedError):
        saver.save_many(validator.prepare((excluded,), f"excluded-{suffix}"))

    equipment = EquipmentService(storage)
    assert count(storage, "import_batch") == before
    assert equipment.get_snapshot(normal_code, DAY) is None and equipment.get_snapshot(hpa_code, DAY) is None

    completed = set_excluded(excluded, 2, False)
    for field in ("model_name", "group_name", "family_name"):
        if completed.row(2).value(field) is None:
            completed = edit_value(completed, 2, field, "HPA")
    completed = edit_value(completed, 2, missing, hpa_code if missing == "equipment_code" else "vHPA")
    receipt, = saver.save_many(validator.prepare((completed,), f"completed-{suffix}"))
    assert equipment.get_snapshot(normal_code, DAY) is not None
    saved = equipment.get_snapshot(hpa_code, DAY)
    assert saved.software[0].version_raw == "vHPA" and saved.production_area is None
    with storage.transaction() as tx:
        original = tx.get("import_batch", {"batch_id": receipt.batch_id})["raw_data"][sheet.sheet_name]
    assert original[sheet.address_of(2, 5 if missing == "equipment_code" else 6)] is None


@pytest.mark.parametrize("initial_hpa", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_split_full_pm_correction_is_order_independent_and_preserves_each_source(storage, initial_hpa, reverse):  # noqa: F811
    """최종 PM 전체가 일관되면 시트 순서와 무관하게 저장하고 각 원본 입력 작업을 보존한다."""
    code = f"SPLIT-FULL-{initial_hpa}-{reverse}"
    validator, saver, equipment = ImportValidationService(storage), DatabaseImportSaver(storage), EquipmentService(storage)
    saver.save_many(validator.prepare((status_draft(code, hpa=initial_hpa, pms=("PM1", "PM2")),), f"old-{code}"))
    before = equipment.get_snapshot(code, DAY)
    requests = []
    names = ("PM2", "PM1") if reverse else ("PM1", "PM2")
    for pm_name in names:
        item = status_draft(code, hpa=not initial_hpa, pms=(pm_name,), version=f"vFINAL.{pm_name} ")
        requests.append(item)
    receipts = saver.save_many(validator.prepare(tuple(requests), f"corrected-{code}"))
    saved = equipment.get_snapshot(code, DAY)
    hpa_group = _group_id(storage, "HPA")
    assert saved.snapshot_id == before.snapshot_id
    assert read_equipment_record(saved, {hpa_group}).is_hpa is not initial_hpa
    assert saved.production_area is None
    for pm_name, receipt in zip(names, receipts):
        sources = equipment.get_snapshot_sources(code, DAY, pm_name=pm_name)
        assert any(cell.field == "pm_name" and cell.value == pm_name and cell.batch_id == receipt.batch_id for cell in sources)
        assert any(cell.field == "version_raw" and cell.value == f"vFINAL.{pm_name} "
                   and cell.batch_id == receipt.batch_id for cell in sources)


@pytest.mark.parametrize("initial_hpa", [False, True])
def test_management_classification_conflict_rolls_back_other_edits_and_audit(storage, initial_hpa):  # noqa: F811
    """관리 수정도 HPA 이력 충돌이면 앞선 정상 수정·감사 입력 작업까지 전체 취소한다."""
    code, other_code = f"MANAGED-HISTORY-{initial_hpa}", f"MANAGED-CONTROL-{initial_hpa}"
    validator, saver, equipment = ImportValidationService(storage), DatabaseImportSaver(storage), EquipmentService(storage)
    saver.save_many(validator.prepare((status_draft(code, hpa=initial_hpa, pms=("PM1",)),
                                      status_draft(code, hpa=initial_hpa, pms=("PM1",), base_date=NEXT_DAY),
                                      status_draft(other_code, hpa=False)), f"managed-initial-{initial_hpa}"))
    original, latest = equipment.get_snapshot(code, DAY), equipment.get_snapshot(code, NEXT_DAY)
    other = equipment.get_snapshot(other_code, DAY)
    management = ManagementService(storage)
    model, family = ("M-O2", "O2") if initial_hpa else ("HPA", "HPA")
    edits = (
        managed_edit(management, "equipment_snapshot", {"snapshot_id": other.snapshot_id}, {"line": "취소되어야 할 수정"}),
        managed_edit(management, "equipment_pm", {"pm_id": latest.software[0].pms[0].pm_id},
                     {"group_id": _group_id(storage, model), "model_name_raw": model,
                      "family_id": _family_id(storage, family), "version_raw": "vCHANGED", "release_version_id": None}),
    )
    before_batches = count(storage, "import_batch")

    with pytest.raises(StorageValidationError, match="HPA|분류"):
        management.save(edits)

    assert count(storage, "import_batch") == before_batches
    assert equipment.get_snapshot(code, DAY) == original and equipment.get_snapshot(code, NEXT_DAY) == latest
    assert equipment.get_snapshot(other_code, DAY) == other
    source = load_dashboard_source(storage)
    assert source.snapshot(QueryState(source.catalog).apply()) is not None

    # 같은 HPA·일반 구분을 유지한 SW 보완은 정상 저장·재조회할 수 있다.
    management.save((managed_edit(management, "equipment_pm", {"pm_id": latest.software[0].pms[0].pm_id},
                                 {"version_raw": "vCORRECTED "}),))
    assert equipment.get_snapshot(code, NEXT_DAY).software[0].version_raw == "vCORRECTED "
    assert equipment.get_snapshot(code, DAY) == original
