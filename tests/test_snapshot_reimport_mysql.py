"""BE-009 부분 재가져오기·원본 출처·원자성을 실제 MySQL 5.6.19에서 검증한다."""

from dataclasses import replace
from datetime import date
import os

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.schemas import ImportBlockedError
from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.service import ImportValidationService, edit_value
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.database import DatabaseSaveError
from setron.features.data_admin import WORKBOOK_KEY  # 11-10: 가져오기 원본에 파일 이름도 보관

from test_import_save_mysql import count, storage  # noqa: F401  실제 스키마·시연 기준 자료 fixture
from test_import_validation import draft, row
from test_schema_mysql import schema_database  # noqa: F401  storage의 전용 DB fixture


pytestmark = pytest.mark.skipif(
    not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.",
)


def save(storage, *rows, token="save", sheet="현황"):
    """실제 DB 기존값 검토 후 원본 시트와 함께 한 묶음을 저장한다."""
    source = draft(*rows, sheet=sheet)
    request, = ImportValidationService(storage).prepare((source,), token)
    return DatabaseImportSaver(storage).save(request)


def snapshot(storage, code, base="2026-11-02"):
    return EquipmentService(storage).get_snapshot(code, date.fromisoformat(base))


def positions(value):
    """고정 PM 위치를 실제 SW 행과 함께 조회한다."""
    return {pm.pm_name: (software, pm) for software in value.software for pm in software.pms}


def raw_batch(storage, batch_id):
    with storage.transaction() as tx:
        return tx.get("import_batch", {"batch_id": batch_id})["raw_data"]


def test_same_values_keep_ids_and_link_the_latest_original_cells(storage):
    code = "MYSQL-REIMPORT-SAME"
    first = save(storage, row(equipment_code=code), token="mysql-same-1", sheet="처음 원본")
    before = snapshot(storage, code)
    original = raw_batch(storage, first.batch_id)
    second = save(storage, row(equipment_code=code), token="mysql-same-2", sheet="최신 원본")
    after = snapshot(storage, code)

    assert (after.equipment_id, after.snapshot_id) == (before.equipment_id, before.snapshot_id)
    assert after.software[0].equipment_version_id == before.software[0].equipment_version_id
    assert positions(after)["PM1"][1].pm_id == positions(before)["PM1"][1].pm_id
    assert after.batch_id == after.software[0].batch_id == positions(after)["PM1"][1].batch_id == second.batch_id
    assert after.source_reference["equipment_code"]["sheet"] == "최신 원본"
    assert positions(after)["PM1"][1].source_reference["version_raw"]["sheet"] == "최신 원본"
    assert raw_batch(storage, first.batch_id) == original
    assert set(raw_batch(storage, second.batch_id)) == {"최신 원본", WORKBOOK_KEY}
    assert count(storage, "equipment_snapshot", equipment_id=after.equipment_id) == 1
    assert count(storage, "equipment_version", snapshot_id=after.snapshot_id) == 1


def test_one_shared_pm_changes_while_unselected_pm_keeps_version_and_original(storage):
    code = "MYSQL-REIMPORT-SHARED"
    save(storage, row(equipment_code=code), row(2, equipment_code=code, pm_name="PM2"),
         token="mysql-shared-1", sheet="공유 버전 원본")
    before = snapshot(storage, code)
    old_software, old_pm2 = positions(before)["PM2"]
    latest = save(storage, row(equipment_code=code, version_raw="v2"),
                  token="mysql-shared-2", sheet="PM1 변경 원본")
    after = snapshot(storage, code)
    sw1, pm1 = positions(after)["PM1"]
    sw2, pm2 = positions(after)["PM2"]

    assert sw1.version_raw == "v2" and sw2.version_raw == "v1"
    assert sw1.equipment_version_id != sw2.equipment_version_id
    assert pm1.pm_id == positions(before)["PM1"][1].pm_id and pm1.batch_id == latest.batch_id
    assert pm2 == old_pm2 and sw2 == replace(old_software, pms=(old_pm2,))
    assert pm1.source_reference["version_raw"]["sheet"] == "PM1 변경 원본"
    assert pm2.source_reference["version_raw"]["sheet"] == "공유 버전 원본"


def test_partial_reimport_preserves_other_family_date_and_equipment(storage):
    code, other_code = "MYSQL-REIMPORT-SCOPE", "MYSQL-REIMPORT-OTHER"
    save(storage, row(equipment_code=code),
         row(2, equipment_code=code, pm_name="PM3", model_name="M-O3", family_name="O3", version_raw="o3-v1"),
         token="mysql-scope-1")
    save(storage, row(equipment_code=code, base_date="2026-11-01", version_raw="old-v1", line="이전 라인"),
         token="mysql-scope-day")
    save(storage, row(equipment_code=other_code, version_raw="other-v1"), token="mysql-scope-other")
    before, old_day, other = snapshot(storage, code), snapshot(storage, code, "2026-11-01"), snapshot(storage, other_code)

    save(storage, row(equipment_code=code, version_raw="v2"), token="mysql-scope-update")

    assert positions(snapshot(storage, code))["PM3"] == positions(before)["PM3"]
    assert snapshot(storage, code, "2026-11-01") == old_day
    assert snapshot(storage, other_code) == other
    assert snapshot(storage, code, "2026-10-31") is None


def test_line_can_change_without_changing_another_dates_line(storage):
    code = "MYSQL-REIMPORT-LINE"
    save(storage, row(equipment_code=code, line="13L"), token="mysql-line-1")
    save(storage, row(equipment_code=code, base_date="2026-11-01", line="12L"), token="mysql-line-old")
    old_day = snapshot(storage, code, "2026-11-01")

    save(storage, row(equipment_code=code, line="14L"), token="mysql-line-2")

    assert snapshot(storage, code).line == "14L"
    assert snapshot(storage, code).site == "화성"
    assert snapshot(storage, code, "2026-11-01") == old_day


def test_site_change_is_blocked_for_the_same_date_and_for_a_new_date(storage):
    code = "MYSQL-REIMPORT-SITE"
    save(storage, row(equipment_code=code), token="mysql-site-1")
    before = snapshot(storage, code)
    batch_count = count(storage, "import_batch")

    for base in ("2026-11-02", "2026-11-03"):
        with pytest.raises((ImportBlockedError, StorageValidationError)):
            save(storage, row(equipment_code=code, base_date=base, site="평택"), token=f"mysql-site-{base}")
        assert snapshot(storage, code) == before
        assert count(storage, "import_batch") == batch_count
    assert snapshot(storage, code, "2026-11-03") is None


def test_later_sheet_sql_failure_rolls_back_prior_update_and_new_equipment(storage):
    code, new_code = "MYSQL-REIMPORT-ROLLBACK", "MYSQL-REIMPORT-CANCELLED"
    save(storage, row(equipment_code=code), token="mysql-rollback-1")
    before = snapshot(storage, code)
    batch_count = count(storage, "import_batch")
    requests = ImportValidationService(storage).prepare((
        draft(row(equipment_code=code, version_raw="v2", line="변경 라인"), sheet="먼저 처리"),
        draft(row(equipment_code=new_code, version_raw="v-SQL-FAIL"), sheet="후속 실패"),
    ), "mysql-rollback-many")
    injected = []

    def fail_later_version(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO EQUIPMENT_VERSION"):
            if isinstance(parameters, dict) and parameters.get("version_raw") == b"v-SQL-FAIL":
                injected.append(statement)
                raise OperationalError(statement, parameters, RuntimeError("BE-009 후속 SQL 실패 재현"))

    event.listen(storage.database.engine, "before_cursor_execute", fail_later_version)
    try:
        with pytest.raises(DatabaseSaveError):
            DatabaseImportSaver(storage).save_many(requests)
    finally:
        event.remove(storage.database.engine, "before_cursor_execute", fail_later_version)

    assert injected
    assert snapshot(storage, code) == before
    assert snapshot(storage, new_code) is None
    assert count(storage, "import_batch") == batch_count


def test_versions_split_and_merge_without_phantom_rows_or_changed_fixed_pm_ids(storage):
    code = "MYSQL-REIMPORT-MERGE"
    save(storage, row(equipment_code=code), row(2, equipment_code=code, pm_name="PM2"), token="mysql-merge-1")
    before = snapshot(storage, code)
    first_version_id = before.software[0].equipment_version_id
    first_pm_ids = {name: pm.pm_id for name, (_, pm) in positions(before).items()}

    save(storage, row(equipment_code=code, version_raw="v2"), token="mysql-merge-split")
    split = snapshot(storage, code)
    assert count(storage, "equipment_version", snapshot_id=split.snapshot_id) == 2
    assert {sw.version_raw for sw in split.software} == {"v1", "v2"}

    save(storage, row(equipment_code=code, version_raw="v1"), token="mysql-merge-back")
    after = snapshot(storage, code)
    assert len(after.software) == count(storage, "equipment_version", snapshot_id=after.snapshot_id) == 1
    assert after.software[0].equipment_version_id == first_version_id
    assert {name: pm.pm_id for name, (_, pm) in positions(after).items()} == first_pm_ids
    assert all(software.version_raw == "v1" for software, _ in positions(after).values())


def test_new_equipment_keeps_unregistered_edited_version_and_raw_cell_separately(storage):
    code = "MYSQL-REIMPORT-NEW"
    source = draft(row(equipment_code=code, version_raw="v1_원본(H)", production_area=None), sheet="원문 보관")
    edited = edit_value(source, 1, "version_raw", "v2_검토값(H)")
    request, = ImportValidationService(storage).prepare((edited,), "mysql-new-edited")
    receipt = DatabaseImportSaver(storage).save(request)
    saved = snapshot(storage, code)

    assert saved is not None and saved.production_area is None
    assert saved.software[0].version_raw == "v2_검토값(H)" and saved.software[0].release_version_id is None
    raw = raw_batch(storage, receipt.batch_id)
    assert raw["원문 보관"][source.row(1).sources["version_raw"]] == "v1_원본(H)"
    # 11-10 변경: 파일 이름은 시트 셀과 섞지 않고 WORKBOOK_KEY에만 보관한다 (데이터 관리 변경 이력용).
    assert raw[WORKBOOK_KEY] == source.workbook_name and source.workbook_name not in str(raw["원문 보관"])


def test_release_registered_in_the_same_batch_links_to_the_following_status(storage):
    version, code = "v-mysql-same-batch", "MYSQL-REIMPORT-SAME-BATCH"
    release = draft(row(version_raw=version), sheet="O2 릴리즈", kind=ExtractKind.RELEASE_NOTE)
    status = draft(row(equipment_code=code, version_raw=version), sheet="화성 현황")
    requests = ImportValidationService(storage).prepare((release, status), "mixed-kind-bundle")
    DatabaseImportSaver(storage).save_many(requests)
    saved = snapshot(storage, code)
    assert saved.software[0].version_raw == version
    assert saved.software[0].release_version_id is not None
    with storage.transaction() as tx:
        linked = tx.get("release_version", {"release_version_id": saved.software[0].release_version_id})
    assert linked["version_raw"] == version


def test_partial_mixed_pm_preserves_model_original_and_exposes_each_actual_source_batch(storage):
    code = "MYSQL-REIMPORT-MODEL-ORIGINAL"
    first = save(storage, row(equipment_code=code, line="12L"),
        row(2, equipment_code=code, line="12L", pm_name="PM2", model_name="M-O3", family_name="O3", version_raw="o3-v1"),
        token="model-original-first", sheet="설비 원본")
    before = snapshot(storage, code)
    latest = save(storage, row(equipment_code=code, line="14L", pm_name="PM2", model_name="M-O3", family_name="O3", version_raw="o3-v2"),
        token="model-original-later", sheet="PM2 갱신")
    after = snapshot(storage, code)
    assert after.model_name == before.model_name and after.line == "14L"
    source = EquipmentService(storage).get_snapshot_sources(code, after.base_date)
    assert {cell.batch_id for cell in source if cell.field == "model_name"} == {first.batch_id}
    assert {cell.batch_id for cell in source if cell.field == "line"} == {latest.batch_id}
    assert all(cell.available for cell in source)
    pm_source = EquipmentService(storage).get_snapshot_sources(code, after.base_date, pm_name="PM2")
    assert next(cell.value for cell in pm_source if cell.field == "version_raw") == "o3-v2"
