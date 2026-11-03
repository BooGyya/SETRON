"""실제 MySQL에서 HPA 검토·저장 경계의 전체 취소를 검증한다 (BE-024)."""

from dataclasses import replace
from datetime import date
import os

import pytest

from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.schemas import ImportBlockedError
from setron.features.excel_import.service import ImportValidationService, edit_value, set_excluded
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.validation import StorageValidationError

from test_hpa_import import hpa_draft
from test_hpa_extraction_coverage import record, row_at, table
from test_hpa_source_classification import request_for
from test_import_save_mysql import _family_id, _group_id, count, storage  # noqa: F401
from test_schema_mysql import schema_database  # noqa: F401


pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")
DAY = date(2026, 11, 3)


def test_hpa_identifier_with_normal_classification_holds_the_entire_save_batch(storage):  # noqa: F811
    """필드별 원문이 맞아도 HPA 식별값과 일반 분류를 합친 요청은 정상 시트까지 보류한다."""
    normal = hpa_draft(code="SAFE-CLASS-GOOD-024", version="v2", model="M-O2", group="Oxide", family_name="O2")
    good, = ImportValidationService(storage).prepare((normal,), "classification-good")
    sheet = table(record("SAFE-CLASS-NORMAL-024", "vO2", hpa=False), record("SAFE-CLASS-HPA-024", "vHPA"))
    mixed, hpa = row_at(sheet, 1), row_at(sheet, 2)
    for field in ("equipment_code", "version_raw", "pm_name"):
        mixed["values"][field] = hpa["values"][field]
        mixed["sources"][field] = hpa["sources"][field]
    forged = request_for(sheet, mixed)
    before = count(storage, "import_batch")

    with pytest.raises(StorageValidationError, match="HPA"):
        DatabaseImportSaver(storage).save_many((good, forged))

    assert count(storage, "import_batch") == before
    assert EquipmentService(storage).get_snapshot("SAFE-CLASS-GOOD-024", DAY) is None
    assert EquipmentService(storage).get_snapshot("SAFE-CLASS-HPA-024", DAY) is None


def test_unreviewed_source_mismatch_holds_all_requests_before_any_db_write(storage):  # noqa: F811
    """정상 시트와 함께 보낸 미보완 가짜 SW는 입력 작업·설비 어느 것도 남기지 않는다."""
    normal = hpa_draft(code="SAFE-GOOD-024", version="v2", model="M-O2", group="Oxide", family_name="O2")
    hpa = hpa_draft(code="SAFE-HPA-024")
    requests = ImportValidationService(storage).prepare((normal, hpa), "source-mismatch")
    last = requests[1]
    forged = replace(last, rows=(replace(last.rows[0], values={**last.rows[0].values, "version_raw": "vFAKE"}),))
    before = count(storage, "import_batch")

    with pytest.raises(StorageValidationError, match="SW 버전 원문"):
        DatabaseImportSaver(storage).save_many((requests[0], forged))

    assert count(storage, "import_batch") == before
    assert EquipmentService(storage).get_snapshot("SAFE-GOOD-024", DAY) is None
    assert EquipmentService(storage).get_snapshot("SAFE-HPA-024", DAY) is None


def test_excluded_hpa_error_holds_normal_sheet_until_reincluded_and_completed(storage):  # noqa: F811
    """필수 오류 HPA를 제외해도 정상 시트만 저장하지 않으며 보완 후 함께 저장한다."""
    normal = hpa_draft(code="SAFE-EXCLUDE-GOOD-024", version="v2", model="M-O2", group="Oxide", family_name="O2")
    broken = set_excluded(hpa_draft(code="SAFE-EXCLUDE-HPA-024", version=None), 1, True)
    validator = ImportValidationService(storage)
    before = count(storage, "import_batch")

    with pytest.raises(ImportBlockedError):
        validator.prepare((normal, broken), "exclude-invalid")

    assert count(storage, "import_batch") == before
    completed = edit_value(set_excluded(broken, 1, False), 1, "version_raw", "vHPA.USER")
    DatabaseImportSaver(storage).save_many(validator.prepare((normal, completed), "correct-excluded"))
    saved = EquipmentService(storage).get_snapshot("SAFE-EXCLUDE-HPA-024", DAY)
    assert saved.software[0].version_raw == "vHPA.USER" and saved.production_area is None
    assert EquipmentService(storage).get_snapshot("SAFE-EXCLUDE-GOOD-024", DAY) is not None


def test_direct_mixed_hpa_snapshot_rolls_back_preceding_normal_snapshot(storage):  # noqa: F811
    """공통 저장 API로 들어온 혼합 HPA 오류도 같은 묶음의 앞선 정상 저장을 취소한다."""
    normal_group, hpa_group = _group_id(storage, "M-O2"), _group_id(storage, "HPA")
    normal_family, hpa_family = _family_id(storage, "O2"), _family_id(storage, "HPA")
    with storage.transaction() as tx:
        batch_id = tx.insert("import_batch", {"source_type": "MANUAL"})["batch_id"]
    normal = SnapshotInput(equipment_code="SAFE-DIRECT-GOOD-024", base_date=DAY, site="화성", model_name="M-O2",
        batch_id=batch_id, group_id=normal_group, software=(SoftwareInput(family_id=normal_family, version_raw="v1"),))
    mixed = SnapshotInput(equipment_code="SAFE-DIRECT-MIX-024", base_date=DAY, site="화성", model_name="혼합",
        batch_id=batch_id, software=(
            SoftwareInput(family_id=normal_family, version_raw="v1", pms=(PmInput(pm_name="PM1", group_id=normal_group),)),
            SoftwareInput(family_id=hpa_family, version_raw="vHPA", pms=(PmInput(pm_name="PM2", group_id=hpa_group),))))
    before = count(storage, "equipment_snapshot")

    with pytest.raises(StorageValidationError, match="HPA"):
        EquipmentService(storage).save_snapshots((normal, mixed))

    assert count(storage, "equipment_snapshot") == before
    assert EquipmentService(storage).get_snapshot(normal.equipment_code, DAY) is None
    assert EquipmentService(storage).get_snapshot(mixed.equipment_code, DAY) is None
