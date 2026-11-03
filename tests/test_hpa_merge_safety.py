"""부분 재가져오기에서 기존 PM을 합친 최종 HPA 구성을 검사한다 (BE-024)."""

from datetime import date

import pytest

from setron.features.equipment.read import read_equipment_record
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.schemas import ImportBlockedError
from setron.features.excel_import.service import ImportValidationService
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.validation import StorageValidationError

from test_import_validation import draft, row
from test_snapshot_reimport import count, save, storage  # noqa: F401


DAY = date(2026, 11, 2)
HPA = dict(model_name="HPA", group_name="HPA", family_name="HPA", version_raw="vHPA")
NORMAL = dict(model_name="M-O2", group_name="OXIDE", family_name="O2", version_raw="v1")


@pytest.fixture
def hpa_storage(storage):  # noqa: F811
    """일반 모델이 있는 임시 SQLite DB에 분석 제외 HPA 모델·계열을 추가한다."""
    with storage.transaction() as tx:
        tx.insert("equipment_group", {"group_id": 9, "group_name": "HPA", "category": "HPA", "analysis_enabled": False})
        tx.insert("version_family", {"family_id": 9, "group_id": 9, "family_name": "HPA", "ui_type": None})
    return storage


def _counts(storage):
    return {table: count(storage, table) for table in
            ("import_batch", "equipment", "equipment_snapshot", "equipment_version", "equipment_pm")}


@pytest.mark.parametrize("old_hpa", [True, False], ids=["HPA에서일반", "일반에서HPA"])
@pytest.mark.parametrize("incoming_pm", ["PM1", "PM3"], ids=["기존PM교체", "새PM추가"])
def test_partial_pm_hpa_mix_rolls_back_the_entire_import_bundle(hpa_storage, old_hpa, incoming_pm):
    """남은 기존 PM과 새 PM이 혼합되면 앞선 정상 시트까지 취소하고 원본도 유지한다."""
    storage = hpa_storage
    old, new = (HPA, NORMAL) if old_hpa else (NORMAL, HPA)
    save(storage, row(1, **old), row(2, pm_name="PM2", **old), request_id="initial")
    equipment = EquipmentService(storage)
    before = equipment.get_snapshot("E-1", DAY)
    before_counts = _counts(storage)
    good = draft(row(equipment_code="NORMAL-BEFORE-MIX", **NORMAL), sheet="정상 시트")
    changed = draft(row(pm_name=incoming_pm, **new), sheet="일부 PM 재가져오기")
    requests = ImportValidationService(storage).prepare((good, changed), "partial-mix")

    with pytest.raises(StorageValidationError, match="HPA와 일반 설비"):
        DatabaseImportSaver(storage).save_many(requests)

    assert _counts(storage) == before_counts
    assert equipment.get_snapshot("E-1", DAY) == before
    assert equipment.get_snapshot("NORMAL-BEFORE-MIX", DAY) is None
    assert read_equipment_record(before, {9}).is_hpa is old_hpa


def test_hpa_and_normal_pm_in_separate_new_sheets_cannot_merge_into_one_equipment(hpa_storage):
    """신규 입력 시트 간 설비군 충돌은 기존처럼 요청 생성 전 전체 보류한다."""
    storage = hpa_storage
    hpa = draft(row(pm_name="PM1", **HPA), sheet="HPA 시트")
    normal = draft(row(pm_name="PM2", **NORMAL), sheet="일반 시트")
    before_counts = _counts(storage)

    with pytest.raises(ImportBlockedError, match="설비군"):
        ImportValidationService(storage).prepare((hpa, normal), "new-cross-sheet-mix")

    assert _counts(storage) == before_counts
    assert EquipmentService(storage).get_snapshot("E-1", DAY) is None


def test_partial_updates_keep_ordinary_mixed_models_and_hpa_versions_supported(hpa_storage):
    """일반 모델 간 PM 변경과 HPA 내부 버전 갱신은 기존 미입력 PM을 보존한다."""
    storage = hpa_storage
    save(storage, row(1, **NORMAL), row(2, pm_name="PM2", **NORMAL), request_id="normal-initial")
    save(storage, row(model_name="M-O3", family_name="O3", version_raw="vO3"), request_id="normal-model-update")
    normal = EquipmentService(storage).get_snapshot("E-1", DAY)
    assert {(pm.pm_name, pm.group_id, sw.version_raw) for sw in normal.software for pm in sw.pms} == {
        ("PM1", 2, "vO3"), ("PM2", 1, "v1"),
    }
    assert not read_equipment_record(normal, {9}).is_hpa

    save(storage, row(1, equipment_code="HPA-1", **HPA), row(2, equipment_code="HPA-1", pm_name="PM2", **HPA),
         request_id="hpa-initial")
    save(storage, row(equipment_code="HPA-1", **{**HPA, "version_raw": "vHPA.NEW"}), request_id="hpa-version-update")
    hpa = EquipmentService(storage).get_snapshot("HPA-1", DAY)
    assert {(pm.pm_name, sw.version_raw) for sw in hpa.software for pm in sw.pms} == {
        ("PM1", "vHPA.NEW"), ("PM2", "vHPA"),
    }
    assert read_equipment_record(hpa, {9}).is_hpa
