"""날짜가 다른 같은 호기의 HPA·일반 구분 충돌을 저장 경계에서 보류한다."""

from dataclasses import replace
from datetime import date

import pytest

from setron.features.equipment.read import read_equipment_record
from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.service import ImportValidationService
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.validation import StorageValidationError

from test_hpa_merge_safety import HPA, _counts, hpa_storage  # noqa: F401
from test_import_validation import draft, row
from test_snapshot_reimport import storage  # noqa: F401


OLD_DAY = date(2026, 11, 2)
NEW_DAY = date(2026, 11, 3)


def _input(storage, code, day, hpa, *, pms=()):
    """저장 서비스 자체의 검증을 확인할 명시적 수동 입력을 만든다."""
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL"})
    group_id, family_id, model = (9, 9, "HPA") if hpa else (1, 1, "M-O2")
    return SnapshotInput(equipment_code=code, base_date=day, site="화성", model_name=model,
        batch_id=batch["batch_id"], group_id=group_id,
        software=(SoftwareInput(family_id=family_id, version_raw="vHPA" if hpa else "v1",
            pms=tuple(PmInput(pm_name=name, group_id=group_id, model_name_raw=model) for name in pms)),))


@pytest.mark.parametrize("old_hpa", [True, False], ids=["HPA에서일반", "일반에서HPA"])
@pytest.mark.parametrize("pms", [(), ("PM1", "PM2")], ids=["PM없음", "PM있음"])
def test_new_date_with_opposite_classification_rolls_back_the_entire_batch(hpa_storage, old_hpa, pms):
    """다른 날의 분류 충돌은 호기·양쪽 날짜·종류와 원본 확인 사유를 남기고 전부 취소한다."""
    storage = hpa_storage
    equipment = EquipmentService(storage)
    old, = equipment.save_snapshots((_input(storage, "HISTORY-1", OLD_DAY, old_hpa, pms=pms),))
    good = _input(storage, "NORMAL-BEFORE-HISTORY", NEW_DAY, False)
    conflicting = _input(storage, "HISTORY-1", NEW_DAY, not old_hpa, pms=pms)
    before_counts = _counts(storage)

    with pytest.raises(StorageValidationError) as error:
        equipment.save_snapshots((good, conflicting))

    assert all(text in str(error.value) for text in
               ("HISTORY-1", OLD_DAY.isoformat(), NEW_DAY.isoformat(), "HPA", "일반 설비", "원본", "전체"))
    assert _counts(storage) == before_counts
    assert equipment.get_snapshot("HISTORY-1", OLD_DAY) == old
    assert equipment.get_snapshot("HISTORY-1", NEW_DAY) is None
    assert equipment.get_snapshot("NORMAL-BEFORE-HISTORY", NEW_DAY) is None


@pytest.mark.parametrize("first_hpa", [True, False], ids=["HPA먼저", "일반먼저"])
def test_new_equipment_cannot_have_opposite_dates_in_one_save_batch(hpa_storage, first_hpa):
    """DB에 없던 호기도 같은 묶음의 앞선 날짜와 분류가 다르면 두 날짜 모두 취소한다."""
    storage = hpa_storage
    first = _input(storage, "NEW-HISTORY", OLD_DAY, first_hpa)
    second = _input(storage, "NEW-HISTORY", NEW_DAY, not first_hpa)
    before_counts = _counts(storage)

    with pytest.raises(StorageValidationError, match="날짜별 분류가 다릅니다"):
        EquipmentService(storage).save_snapshots((first, second))

    assert _counts(storage) == before_counts
    assert EquipmentService(storage).get_snapshot("NEW-HISTORY", OLD_DAY) is None
    assert EquipmentService(storage).get_snapshot("NEW-HISTORY", NEW_DAY) is None


@pytest.mark.parametrize("old_hpa", [True, False], ids=["HPA에서일반", "일반에서HPA"])
def test_same_day_full_pm_replacement_still_checks_other_dates(hpa_storage, old_hpa):
    """PM 전체를 같은 날 바꿔 혼합이 없어도 다른 날의 분류와 충돌하면 원상 유지한다."""
    storage = hpa_storage
    equipment = EquipmentService(storage)
    older, old = equipment.save_snapshots((
        _input(storage, "FULL-PM-HISTORY", OLD_DAY, old_hpa, pms=("PM1", "PM2")),
        _input(storage, "FULL-PM-HISTORY", NEW_DAY, old_hpa, pms=("PM1", "PM2")),
    ))
    changed = _input(storage, "FULL-PM-HISTORY", NEW_DAY, not old_hpa, pms=("PM1", "PM2"))
    before_counts = _counts(storage)

    with pytest.raises(StorageValidationError, match="날짜별 분류가 다릅니다"):
        with storage.transaction() as tx:
            equipment.import_snapshots_in(tx, (changed,))

    assert _counts(storage) == before_counts
    assert equipment.get_snapshot("FULL-PM-HISTORY", OLD_DAY) == older
    assert equipment.get_snapshot("FULL-PM-HISTORY", NEW_DAY) == old


@pytest.mark.parametrize("old_hpa", [True, False], ids=["HPA에서일반", "일반에서HPA"])
def test_same_day_full_pm_correction_is_allowed_without_other_date_conflict(hpa_storage, old_hpa):
    """다른 날짜가 없으면 검토한 같은 날짜의 전체 PM 분류 정정은 허용한다."""
    storage = hpa_storage
    equipment = EquipmentService(storage)
    old, = equipment.save_snapshots((_input(storage, "FULL-PM-CORRECTION", OLD_DAY, old_hpa, pms=("PM1", "PM2")),))
    changed = _input(storage, "FULL-PM-CORRECTION", OLD_DAY, not old_hpa, pms=("PM1", "PM2"))

    with storage.transaction() as tx:
        updated, = equipment.import_snapshots_in(tx, (changed,))

    assert updated.snapshot_id == old.snapshot_id
    assert read_equipment_record(updated, {9}).is_hpa is not old_hpa


@pytest.mark.parametrize("hpa", [True, False], ids=["HPA", "일반"])
def test_same_classification_on_different_dates_preserves_versions_and_optional_blanks(hpa_storage, hpa):
    """HPA·일반 구분이 같으면 날짜별 버전 차이와 선택 공란을 그대로 저장한다."""
    storage = hpa_storage
    first = _input(storage, "SAME-KIND", OLD_DAY, hpa, pms=("PM1",))
    second = _input(storage, "SAME-KIND", NEW_DAY, hpa, pms=("PM1",))
    second = replace(second, software=(replace(second.software[0], version_raw="vNEW "),))
    old, new = EquipmentService(storage).save_snapshots((first, second))

    assert old.software[0].version_raw != new.software[0].version_raw == "vNEW "
    assert old.production_area is None and new.production_area is None
    assert read_equipment_record(old, {9}).is_hpa is hpa
    assert read_equipment_record(new, {9}).is_hpa is hpa


def test_opposite_history_added_after_review_is_rejected_before_excel_save(hpa_storage):
    """검토 뒤 추가된 다른 날짜도 저장 시 재검사하여 준비된 HPA 요청을 취소한다."""
    storage = hpa_storage
    incoming = draft(row(equipment_code="LATE-HISTORY", base_date=NEW_DAY.isoformat(), **HPA))
    requests = ImportValidationService(storage).prepare((incoming,), "prepared-before-history")
    equipment = EquipmentService(storage)
    old, = equipment.save_snapshots((_input(storage, "LATE-HISTORY", OLD_DAY, False),))
    before_counts = _counts(storage)

    with pytest.raises(StorageValidationError, match="HPA"):
        DatabaseImportSaver(storage).save_many(requests)

    assert _counts(storage) == before_counts
    assert equipment.get_snapshot("LATE-HISTORY", OLD_DAY) == old
    assert equipment.get_snapshot("LATE-HISTORY", NEW_DAY) is None
