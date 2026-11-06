"""편집 연결은 실제 기준일의 DB 키를 쓰고, 어떤 자료도 저장하지 않는다."""

from copy import deepcopy
from datetime import date

from setron.features.storage.validation import TABLE_NAMES
from setron.ui.management_data import ManagementGateway

from test_management_service import service_for
from test_release_revision import all_rows, revision_storage


def test_general_sw_exact_date_missing_date_and_no_writes(revision_storage):
    storage = revision_storage
    _, snapshot, _, _ = service_for(storage, second_day=True)
    before = {table: deepcopy(all_rows(storage, table)) for table in TABLE_NAMES}
    gateway = ManagementGateway(storage)
    choices = gateway.equipment_versions(snapshot.equipment_id, date(2026, 10, 1))
    assert len(choices) == 1 and choices[0][0] == "equipment_version"
    assert choices[0][1] == {"equipment_version_id": snapshot.software[0].equipment_version_id}
    assert gateway.equipment_versions(snapshot.equipment_id, date(2026, 10, 3)) == ()
    assert gateway.equipment_versions(99999, date(2026, 10, 1)) == ()
    second = gateway.equipment_versions(snapshot.equipment_id, date(2026, 10, 2))
    assert second[0][1] != choices[0][1]
    assert before == {table: all_rows(storage, table) for table in TABLE_NAMES}


def test_shared_sw_edits_pm_keys_not_shared_parent_or_other_day(revision_storage):
    storage = revision_storage
    _, snapshot, _, _ = service_for(storage, shared=True, second_day=True)
    choices = ManagementGateway(storage).equipment_versions(snapshot.equipment_id, date(2026, 10, 1))
    assert len(choices) == 2 and all(c[0] == "equipment_pm" for c in choices)
    assert [c[1] for c in choices] == [{"pm_id": p.pm_id} for p in snapshot.software[0].pms]
    assert "PM1" in choices[0][2] and "PM2" in choices[1][2]
