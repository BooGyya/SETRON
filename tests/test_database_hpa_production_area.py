"""HPA 비적용과 일반 설비의 저장 영역·기존 라인 기본값을 실제 읽기 공급자로 구분한다."""

from datetime import date

import pytest

from setron.ui.database_source import load_dashboard_source
from setron.ui.query_state import UNASSIGNED, AppliedQuery, QuerySelection
from setron.ui.replay_data import ReplayRequest, load_replay

from test_release_revision import observation, revision_storage  # noqa: F401


DAY = date(2026, 10, 21)


def query(area=None):
    return AppliedQuery(QuerySelection(fab_key=area), 1, 0, "저장 영역 검증")


def codes(snapshot):
    return {record.equipment_code for record in snapshot.selected_records}


@pytest.mark.parametrize("old_hpa_area", [None, "FOUNDRY"])
def test_hpa_stays_unassigned_while_normal_equipment_keeps_stored_area_or_legacy_fallback(revision_storage, old_hpa_area):
    storage = revision_storage
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 1}, {"group_name": "M-O2"})
        tx.insert("version_family", {"family_id": 3, "group_id": 4, "family_name": "HPA", "ui_type": None})
        tx.insert("app_setting", {"setting_key": "registered_lines.v1", "setting_value": {
            "lines": [{"site": "평택", "line": "S5", "fab": "FOUNDRY"}]}})
    for code, area, versions, model in (
            ("ESHC13", old_hpa_area, ((3, 4, "v1.0.0", None),), "HPA"),
            ("ESOS21", "MEMORY", ((1, 1, "v3.53_2", None),), "M-O2"),
            ("ESOH91", None, ((1, 1, "v3.53_F2", None),), "M-O2")):
        snapshot_id, _ = observation(storage, code=code, day=DAY, versions=versions)
        with storage.transaction() as tx:
            tx.update("equipment_snapshot", {"snapshot_id": snapshot_id}, {
                "site": "평택", "line": "S5", "production_area": area, "model_name": model})
    source = load_dashboard_source(storage, publish_lines=False)
    records = {record.equipment_code: record for record in source.records}
    assert source.location_for(records["ESHC13"]).fab_key is None
    assert source.location_for(records["ESOS21"]).fab_key == "MEMORY"
    assert source.location_for(records["ESOH91"]).fab_key == "FOUNDRY"
    assert codes(source.snapshot(query())) == {"ESHC13", "ESOS21", "ESOH91"}
    assert codes(source.snapshot(query("MEMORY"))) == {"ESOS21"}
    assert codes(source.snapshot(query("FOUNDRY"))) == {"ESOH91"}
    unassigned = source.snapshot(query(UNASSIGNED))
    assert codes(unassigned) == {"ESHC13"}
    assert {record.equipment_code for record in unassigned.hpa_lookup_records} == {"ESHC13"}
    assert not source.snapshot(query("FOUNDRY")).hpa_lookup_records
    # Replay가 공급자를 고정한 뒤에도 같은 비적용 위치를 사용한다.
    replay = load_replay(source, ReplayRequest(DAY, DAY, query(UNASSIGNED)))
    assert len(replay.frames) == 1 and codes(replay.frames[0].snapshot) == {"ESHC13"}
    assert replay.source.location_for(records["ESHC13"]).fab_key is None
