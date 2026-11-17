"""설비 사업장은 날짜별 현황을 다시 입력해도 바뀌지 않는다."""

from dataclasses import replace
from datetime import date

import pytest

from setron.features.equipment.service import EquipmentService
from setron.features.storage.validation import StorageValidationError
from test_import_validation import row
from test_snapshot_reimport import storage, save, snapshot, count  # noqa: F401


def test_new_date_cannot_move_an_existing_equipment_to_another_site(storage):
    save(storage, row())
    original = snapshot(storage)
    batches = count(storage, "import_batch")
    with pytest.raises((StorageValidationError, ValueError), match="사업장"):
        save(storage, row(base_date="2026-11-03", site="평택"), request_id="different-site")
    assert snapshot(storage) == original
    assert snapshot(storage, base="2026-11-03") is None
    assert count(storage, "import_batch") == batches


def test_direct_equipment_service_also_rejects_site_changes_and_rolls_back_batch(storage):
    save(storage, row())
    original = snapshot(storage)
    valid = replace(original, equipment_code="NEW", base_date=date(2026, 11, 3))
    invalid = replace(original, site="평택", base_date=date(2026, 11, 3))
    with pytest.raises(StorageValidationError, match="사업장은 변경할 수 없습니다"):
        EquipmentService(storage).save_snapshots((valid, invalid))
    assert snapshot(storage, "NEW", "2026-11-03") is None
    assert snapshot(storage) == original


def test_same_site_at_another_date_and_a_new_equipment_in_another_site_are_allowed(storage):
    save(storage, row())
    save(storage, row(base_date="2026-11-03"), request_id="next-day")
    save(storage, row(equipment_code="NEW", site="평택"), request_id="new-site-equipment")
    assert snapshot(storage, base="2026-11-03").site == "화성"
    assert snapshot(storage, "NEW").site == "평택"


def test_existing_multi_site_corruption_cannot_be_silently_chosen(storage):
    save(storage, row())
    save(storage, row(base_date="2026-11-03"), request_id="next-day")
    changed = snapshot(storage, base="2026-11-03")
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": changed.snapshot_id}, {"site": "평택"})
    original = snapshot(storage)
    with pytest.raises(StorageValidationError, match="사업장은 변경할 수 없습니다"):
        EquipmentService(storage).save_snapshots((replace(original, base_date=date(2026, 11, 4)),))
    assert snapshot(storage, base="2026-11-04") is None
