"""BE-025의 직접 수정·검증·원본 보존·묶음 저장을 실제 SQLite로 검증한다."""

from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy.exc import SQLAlchemyError

from setron.features.aging.schemas import SETTING_KEY
from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.export.service import build_status_report
from setron.features.item.schemas import Application, ItemState, TargetScope
from setron.features.management import ApplicabilityEdit, ManagementService, RecordEdit
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.schemas import ItemInput, ItemLinkInput
from setron.features.release.service import ReleaseService
from setron.features.risk.schemas import RiskStatus
from setron.features.storage.validation import StorageValidationError
from setron.features.targets.schemas import CandidateConfirmation
from setron.infrastructure.database import DatabaseSaveError

from test_release_revision import all_rows, release, revision_storage


def location(field, cell):
    """검증용 현황 시트의 실제 셀 위치를 만든다."""
    return {field: {"sheet": "현황", "cells": [cell]}}


def observe(storage, release_id, *, day=date(2026, 10, 1), code="EQ-1", shared=False):
    """전체 원본·위치를 가진 일반 설비 또는 PM1·2 공유 SW 현황을 저장한다."""
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {
            "source_type": "EXCEL",
            "raw_data": {"현황": {
                "A1": code, "B1": "원본 모델", "C1": "화성", "D1": "1라인",
                "E1": "v1", "F1": "PM1", "G1": "PM2",
            }},
        })
    pms = tuple(PmInput(pm_name=f"PM{number}", group_id=1, model_name_raw="O2",
                        source_reference=location("pm_name", cell))
                for number, cell in ((1, "F1"), (2, "G1"))) if shared else ()
    result = EquipmentService(storage).save_snapshots((SnapshotInput(
        equipment_code=code, base_date=day, site="화성", line="1라인", model_name="원본 모델",
        group_id=1, batch_id=batch["batch_id"], source_reference={
            **location("model_name", "B1"), **location("site", "C1"), **location("line", "D1"),
        },
        software=(SoftwareInput(family_id=1, version_raw="v1", release_version_id=release_id,
                                source_reference=location("version_raw", "E1"), pms=pms),),
    ),))[0]
    return result


def service_for(storage, *, shared=False, second_day=False):
    """정확한 모델·계열별 목표와 두 릴리즈를 가진 관리 서비스를 준비한다."""
    first = release(storage)
    second = release(storage, raw="v2", order=2)
    current = observe(storage, first, shared=shared)
    if second_day:
        observe(storage, first, shared=shared, day=date(2026, 10, 2))
    context = RefreshContext(targets={(1, 1): second}, applicability={(1, 1): (first, second)})
    return ManagementService(storage, context=context), current, first, second


def edit(service, table, key, changes):
    """사용자가 조회한 관리값의 지문을 포함한 수정 요청을 만든다."""
    record = service.get_record(table, key)
    return RecordEdit(table=table, key=key, revision=record.revision, changes=changes)


def test_snapshot_edit_preserves_original_and_other_date(revision_storage):
    """선택한 날짜의 관리값만 바뀌고 원본 셀·입력 배치·다른 날짜는 유지된다."""
    storage = revision_storage
    service, snapshot, _, _ = service_for(storage, second_day=True)
    key = {"snapshot_id": snapshot.snapshot_id}
    before = all_rows(storage, "equipment_snapshot")
    original_batches = deepcopy(all_rows(storage, "import_batch"))
    request = edit(service, "equipment_snapshot", key, {"line": "2라인", "model_name": "확인한 모델"})
    result = service.save((request,))
    after = all_rows(storage, "equipment_snapshot")
    assert after[0]["line"] == "2라인"
    assert after[0]["model_name"] == "확인한 모델"
    assert {**after[0], "line": before[0]["line"], "model_name": before[0]["model_name"]} == before[0]
    assert after[1] == before[1]
    assert all_rows(storage, "import_batch")[:len(original_batches)] == original_batches
    record = service.get_record("equipment_snapshot", key)
    assert record.current["line"] == "2라인"
    assert any(cell.field == "line" and cell.address == "D1" and cell.value == "1라인"
               for cell in record.original_cells)
    assert result.records[0].current["line"] == "2라인"
    assert [value.base_date for value in result.refresh.dates] == [date(2026, 10, 1), date(2026, 10, 2)]
    assert result.refresh.dates[0].summary.equipment[0].record.line == "2라인"


def test_selected_pm_split_and_coalesce_leave_other_pm_and_date_unchanged(revision_storage):
    """공유 SW에서 PM 하나만 바꾸고 나중에 같은 버전으로 합칠 때 위치·출처를 지킨다."""
    storage = revision_storage
    service, snapshot, first, second = service_for(storage, shared=True, second_day=True)
    pms_before = all_rows(storage, "equipment_pm")
    versions_before = all_rows(storage, "equipment_version")
    original_batches = deepcopy(all_rows(storage, "import_batch"))
    first_pm, second_pm = pms_before[:2]
    result = service.save((edit(service, "equipment_pm", {"pm_id": first_pm["pm_id"]},
                                {"version_raw": "v2"}),))
    pms = all_rows(storage, "equipment_pm")
    versions = {row["equipment_version_id"]: row for row in all_rows(storage, "equipment_version")}
    assert versions[pms[0]["equipment_version_id"]]["version_raw"] == "v2"
    assert versions[pms[0]["equipment_version_id"]]["release_version_id"] == second
    assert versions[pms[1]["equipment_version_id"]]["version_raw"] == "v1"
    assert versions[pms[1]["equipment_version_id"]]["release_version_id"] == first
    assert pms[1:] == pms_before[1:]
    assert {**pms[0], "equipment_version_id": first_pm["equipment_version_id"]} == first_pm
    assert result.refresh.dates[0].summary.below == 1
    assert result.refresh.dates[0].summary.equipment[0].families[0].pm_mismatch
    service.save((edit(service, "equipment_pm", {"pm_id": second_pm["pm_id"]},
                       {"version_raw": "v2"}),))
    pms = all_rows(storage, "equipment_pm")
    current_versions = [row for row in all_rows(storage, "equipment_version")
                        if row["snapshot_id"] == snapshot.snapshot_id]
    assert pms[0]["equipment_version_id"] == pms[1]["equipment_version_id"]
    assert len(current_versions) == 1
    assert current_versions[0]["version_raw"] == "v2"
    assert pms[2:] == pms_before[2:]
    other_date = [row for row in all_rows(storage, "equipment_version")
                  if row["snapshot_id"] != snapshot.snapshot_id]
    assert other_date == [row for row in versions_before if row["snapshot_id"] != snapshot.snapshot_id]
    assert all_rows(storage, "import_batch")[:len(original_batches)] == original_batches


def test_non_pm_version_change_rechecks_release_link(revision_storage):
    """일반 설비 SW 수정은 이전 릴리즈 ID를 남기지 않고 새 원문과 재대응한다."""
    storage = revision_storage
    service, snapshot, _, second = service_for(storage, second_day=True)
    before = all_rows(storage, "equipment_version")
    row = before[0]
    result = service.save((edit(service, "equipment_version",
        {"equipment_version_id": row["equipment_version_id"]}, {"version_raw": "v2"}),))
    after = all_rows(storage, "equipment_version")
    assert after[0]["version_raw"] == "v2"
    assert after[0]["release_version_id"] == second
    assert after[0]["source_reference"] == row["source_reference"]
    assert after[0]["batch_id"] == row["batch_id"]
    assert after[1] == before[1]
    assert result.refresh.dates[0].summary.compliant == 1


def test_shared_sw_requires_pm_selection(revision_storage):
    """공유 SW 행의 직접 수정으로 선택하지 않은 다른 PM을 함께 바꾸지 않는다."""
    storage = revision_storage
    service, _, _, _ = service_for(storage, shared=True)
    before = all_rows(storage, "equipment_version")
    request = edit(service, "equipment_version", {"equipment_version_id": before[0]["equipment_version_id"]},
                   {"version_raw": "v2"})
    with pytest.raises(StorageValidationError):
        service.save((request,))
    assert all_rows(storage, "equipment_version") == before


@pytest.mark.parametrize("table,key_name,field,value", [
    ("equipment_snapshot", "snapshot_id", "site", "평택"),
    ("equipment_snapshot", "snapshot_id", "base_date", date(2026, 9, 1)),
    ("equipment_snapshot", "snapshot_id", "source_reference", None),
    ("equipment_snapshot", "snapshot_id", "batch_id", 999),
    ("equipment_pm", "pm_id", "pm_name", "PM9"),
    ("equipment_pm", "pm_id", "snapshot_id", 999),
    ("release_version", "release_version_id", "family_id", 2),
])
def test_identity_site_and_original_fields_are_immutable(revision_storage, table, key_name, field, value):
    """사업장·날짜·고정 PM 위치·계열 식별자·보관 원본을 관리값 수정으로 바꾸지 못한다."""
    storage = revision_storage
    service, _, _, _ = service_for(storage, shared=True)
    before = all_rows(storage, table)
    request = edit(service, table, {key_name: before[0][key_name]}, {field: value})
    with pytest.raises(StorageValidationError):
        service.save((request,))
    assert all_rows(storage, table) == before


def test_invalid_later_edit_rolls_back_entire_mixed_batch(revision_storage):
    """두 번째 변경의 필수값 오류가 첫 번째 Item 변경과 수정 이력까지 취소한다."""
    storage = revision_storage
    service, snapshot, first, _ = service_for(storage)
    item_id = ReleaseService(storage).register_item(first, ItemInput(content="원래 Item")).current["item_id"]
    before_items = all_rows(storage, "item")
    before_snapshots = all_rows(storage, "equipment_snapshot")
    before_batches = all_rows(storage, "import_batch")
    requests = (
        edit(service, "item", {"item_id": item_id}, {"content": "새 Item"}),
        edit(service, "equipment_snapshot", {"snapshot_id": snapshot.snapshot_id}, {"model_name": "  "}),
    )
    with pytest.raises(StorageValidationError):
        service.save(requests)
    assert all_rows(storage, "item") == before_items
    assert all_rows(storage, "equipment_snapshot") == before_snapshots
    assert all_rows(storage, "import_batch") == before_batches


def test_database_failure_rolls_back_changed_rows_and_journal(revision_storage, monkeypatch):
    """실제 트랜잭션 중 SQL 오류가 나면 먼저 바꾼 Item과 이력도 되돌린다."""
    storage = revision_storage
    service, snapshot, first, _ = service_for(storage)
    item_id = ReleaseService(storage).register_item(first, ItemInput(content="원래 Item")).current["item_id"]
    before = {table: all_rows(storage, table) for table in ("item", "equipment_snapshot", "import_batch")}
    update = storage.repository.update

    def fail_snapshot(session, name, key, values):
        if name == "equipment_snapshot":
            raise SQLAlchemyError("테스트 저장 실패")
        return update(session, name, key, values)

    requests = (
        edit(service, "item", {"item_id": item_id}, {"content": "새 Item"}),
        edit(service, "equipment_snapshot", {"snapshot_id": snapshot.snapshot_id}, {"line": "2라인"}),
    )
    monkeypatch.setattr(storage.repository, "update", fail_snapshot)
    with pytest.raises(DatabaseSaveError):
        service.save(requests)
    assert {table: all_rows(storage, table) for table in before} == before


def test_stale_record_requires_review_without_overwriting_newer_value(revision_storage):
    """다른 저장 뒤 오래된 화면의 요청은 새 관리값을 덮어쓰지 못한다."""
    storage = revision_storage
    service, snapshot, _, _ = service_for(storage)
    key = {"snapshot_id": snapshot.snapshot_id}
    old = edit(service, "equipment_snapshot", key, {"line": "오래된 수정"})
    service.save((edit(service, "equipment_snapshot", key, {"line": "최신 수정"}),))
    with pytest.raises(StorageValidationError):
        service.save((old,))
    assert service.get_record("equipment_snapshot", key).current["line"] == "최신 수정"


def test_snapshot_revision_includes_pm_graph(revision_storage):
    """같은 날짜의 PM 구성이 조회 후 바뀌어도 오래된 현황 검토를 거부한다."""
    storage = revision_storage
    service, snapshot, _, _ = service_for(storage, shared=True)
    request = edit(service, "equipment_snapshot", {"snapshot_id": snapshot.snapshot_id}, {"line": "2라인"})
    pm = all_rows(storage, "equipment_pm")[0]
    with storage.transaction() as tx:
        tx.update("equipment_pm", {"pm_id": pm["pm_id"]}, {"model_name_raw": "다른 작업의 관리값"})
    with pytest.raises(StorageValidationError):
        service.save((request,))
    assert all_rows(storage, "equipment_snapshot")[0]["line"] == "1라인"


def test_duplicate_edit_for_same_record_is_rejected(revision_storage):
    """한 묶음의 중복 대상은 순서에 따른 마지막 값으로 조용히 덮지 않는다."""
    service, snapshot, _, _ = service_for(revision_storage)
    request = edit(service, "equipment_snapshot", {"snapshot_id": snapshot.snapshot_id}, {"line": "2라인"})
    with pytest.raises(StorageValidationError):
        service.save((request, request))


def test_model_family_mismatch_rolls_back_pm_change(revision_storage):
    """Oxide 모델을 C3 계열에 연결하는 수정은 묶음 전체를 보류한다."""
    storage = revision_storage
    service, _, _, _ = service_for(storage, shared=True)
    pm = all_rows(storage, "equipment_pm")[0]
    before = all_rows(storage, "equipment_pm"), all_rows(storage, "equipment_version")
    with pytest.raises(StorageValidationError):
        service.save((edit(service, "equipment_pm", {"pm_id": pm["pm_id"]}, {"family_id": 2}),))
    assert (all_rows(storage, "equipment_pm"), all_rows(storage, "equipment_version")) == before


def test_item_link_edit_rejudges_application_and_safety_risk(revision_storage):
    """Item 포함 상태 저장 후 같은 최신 자료로 적용·위험 결과를 함께 갱신한다."""
    storage = revision_storage
    service, _, first, _ = service_for(storage)
    releases = ReleaseService(storage)
    item_id = releases.register_item(first, ItemInput(content="안전 개선")).current["item_id"]
    releases.save_item_links(first, (ItemLinkInput(item_id=item_id, item_state=ItemState.APPLIED,
                                                target_scope=TargetScope.ALL),))
    with storage.transaction() as tx:
        tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 1, "item_id": item_id,
            "risk_reason": "필수 안전 개선", "target_reference": {"manual": "유지"},
            "mapping_status": "VERIFIED"})
    result = service.save((edit(service, "release_version_item",
        {"release_version_id": first, "item_id": item_id}, {"item_state": "NOT_APPLIED"}),))
    assert result.refresh.dates[0].item_summaries[0].equipment[0].application is Application.NOT_APPLIED
    assert result.refresh.dates[0].risks.equipment[0].status is RiskStatus.RISK
    assert all_rows(storage, "risk_rule")[0]["target_reference"] == {"manual": "유지"}


def test_release_rename_invalidates_old_correspondence_without_changing_observation(revision_storage):
    """릴리즈 이름 수정 뒤 과거 SW 원문을 새 이름으로 단정하지 않고 재판정한다."""
    storage = revision_storage
    service, _, first, _ = service_for(storage, second_day=True)
    before = all_rows(storage, "equipment_version")
    result = service.save((edit(service, "release_version", {"release_version_id": first},
                               {"version_raw": "v1-corrected"}),))
    after = all_rows(storage, "equipment_version")
    assert all(row["release_version_id"] is None for row in after)
    assert [{**row, "release_version_id": first} for row in after] == before
    assert result.records[0].current["edited_fields"] == ["version_raw"]


def test_aging_setting_is_validated_with_other_changes(revision_storage):
    """기간 설정의 역전 오류는 묶음을 막고 유효한 설정은 최신 노후도에 반영한다."""
    storage = revision_storage
    service, snapshot, first, _ = service_for(storage)
    with storage.transaction() as tx:
        tx.update("release_version", {"release_version_id": first}, {"released_at": date(2024, 1, 1)})
        tx.insert("app_setting", {"setting_key": SETTING_KEY,
            "setting_value": {"warning": 1, "danger": 2, "unit": "YEAR"}})
    key = {"setting_key": SETTING_KEY}
    request = edit(service, "app_setting", key,
                   {"setting_value": {"warning": 3, "danger": 2, "unit": "YEAR"}})
    with pytest.raises((StorageValidationError, ValueError)):
        service.save((request,))
    assert all_rows(storage, "app_setting")[0]["setting_value"]["warning"] == 1
    result = service.save((edit(service, "app_setting", key,
        {"setting_value": {"warning": 4, "danger": 5, "unit": "YEAR"}}),))
    assert result.thresholds.warning == 4
    assert result.thresholds.danger == 5
    assert result.ages[0][0] == snapshot.base_date
    assert result.ages[0][1][0].pms[0].age.status.value == "SAFE"


def test_unrecognized_setting_cannot_bypass_business_validation(revision_storage):
    """일반 JSON 편집으로 미정의 관리 설정을 저장하지 못한다."""
    storage = revision_storage
    service, _, _, _ = service_for(storage)
    with storage.transaction() as tx:
        tx.insert("app_setting", {"setting_key": "unknown_setting", "setting_value": {"data": 1}})
    with pytest.raises(StorageValidationError):
        service.save((edit(service, "app_setting", {"setting_key": "unknown_setting"},
                           {"setting_value": {"data": 2}}),))
    assert next(row for row in all_rows(storage, "app_setting")
                if row["setting_key"] == "unknown_setting")["setting_value"] == {"data": 1}


def test_applicability_confirmation_uses_existing_contract_in_same_batch(revision_storage):
    """모델 적용 근거 수정은 기존 후보 검증을 거치고 목표 N을 영구 저장하지 않는다."""
    storage = revision_storage
    service, _, first, second = service_for(storage)
    view = service.get_applicability(1, 1)
    candidates = tuple(CandidateConfirmation(release_id=release_id,
        applicability_basis="원형 모델 적용 표시", order_basis="확인한 릴리즈 순서", source_text="검토한 자료")
        for release_id in (first, second))
    result = service.save((), applicability=(ApplicabilityEdit(1, 1, view.revision, candidates),))
    latest = service.get_applicability(1, 1)
    assert [candidate.release_id for candidate in latest.value.candidates] == [first, second]
    assert latest.value.applicability_confirmed
    assert result.refresh.targets[0].selected_release_id == second
    settings = all_rows(storage, "app_setting")
    assert all("selected_release_id" not in str(row["setting_value"]) for row in settings)
    with pytest.raises(StorageValidationError):
        service.save((), applicability=(ApplicabilityEdit(1, 1, view.revision, candidates),))


def test_list_records_has_bounded_pages_and_returns_independent_values(revision_storage):
    """기록 선택은 페이지·조건을 따르고 반환값 수정이 저장 자료를 바꾸지 않는다."""
    storage = revision_storage
    service, snapshot, _, _ = service_for(storage, second_day=True)
    values = service.list_records("equipment_snapshot", limit=1, offset=1)
    assert len(values) == 1
    assert values[0].current["base_date"] == date(2026, 10, 2)
    filtered = service.list_records("equipment_snapshot", filters={"snapshot_id": snapshot.snapshot_id})
    assert len(filtered) == 1
    filtered[0].current["line"] = "메모리 초안"
    assert service.get_record("equipment_snapshot", {"snapshot_id": snapshot.snapshot_id}).current["line"] == "1라인"
    with pytest.raises(StorageValidationError):
        service.list_records("equipment_snapshot", limit=0)


def test_duplicate_integer_and_string_key_cannot_edit_same_record_twice(revision_storage):
    """DB가 같은 키로 해석하는 정수·숫자 문자열을 중복 수정에 사용할 수 없다."""
    storage = revision_storage
    service, snapshot, _, _ = service_for(storage)
    key = {"snapshot_id": snapshot.snapshot_id}
    record = service.get_record("equipment_snapshot", key)
    requests = (
        RecordEdit("equipment_snapshot", key, record.revision, {"line": "2라인"}),
        RecordEdit("equipment_snapshot", {"snapshot_id": str(snapshot.snapshot_id)}, record.revision,
                   {"line": "3라인"}),
    )
    with pytest.raises(StorageValidationError):
        service.save(requests)
    assert service.get_record("equipment_snapshot", key).current["line"] == "1라인"


def test_submitted_cross_family_release_is_not_silently_repaired(revision_storage):
    """다른 계열 릴리즈 ID 입력을 자동 재연결로 숨기지 않고 검증 오류로 돌려준다."""
    storage = revision_storage
    service, _, _, _ = service_for(storage)
    foreign = release(storage, family=2, raw="C3-v1")
    before = all_rows(storage, "equipment_version")
    row = before[0]
    with pytest.raises(StorageValidationError):
        service.save((edit(service, "equipment_version",
            {"equipment_version_id": row["equipment_version_id"]}, {"release_version_id": foreign}),))
    assert all_rows(storage, "equipment_version") == before


def test_risk_disable_updates_results_and_keeps_manual_evidence(revision_storage):
    """위험 지정 사용 여부 수정은 즉시 재판정하며 수동 지정 근거는 보존한다."""
    storage = revision_storage
    service, _, first, _ = service_for(storage)
    with storage.transaction() as tx:
        risk = tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1,
            "release_version_id": first, "risk_reason": "직접 확인한 위험",
            "target_reference": {"manual": "지정 원본"}, "mapping_status": "VERIFIED"})
    result = service.save((edit(service, "risk_rule", {"risk_rule_id": risk["risk_rule_id"]},
                               {"is_enabled": False, "risk_reason": "확인 후 사용 중지"}),))
    assert result.refresh.dates[0].risks.equipment[0].status is RiskStatus.NO_RISK
    current = all_rows(storage, "risk_rule")[0]
    assert current["risk_reason"] == "확인 후 사용 중지"
    assert current["target_reference"]["manual"] == "지정 원본"


def test_invalid_risk_range_rolls_back_other_changes(revision_storage):
    """시작·해제 역전은 위험 정보와 같은 묶음의 현황 수정을 모두 막는다."""
    storage = revision_storage
    service, snapshot, first, second = service_for(storage)
    with storage.transaction() as tx:
        risk = tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1,
            "release_version_id": first, "risk_reason": "직접 확인한 위험",
            "start_release_version_id": first, "end_release_version_id": second,
            "target_reference": {"manual": "유지"}, "mapping_status": "VERIFIED"})
    before = all_rows(storage, "risk_rule")
    with pytest.raises(StorageValidationError):
        service.save((
            edit(service, "equipment_snapshot", {"snapshot_id": snapshot.snapshot_id}, {"line": "2라인"}),
            edit(service, "risk_rule", {"risk_rule_id": risk["risk_rule_id"]},
                 {"start_release_version_id": second, "end_release_version_id": first}),
        ))
    assert all_rows(storage, "risk_rule") == before
    assert all_rows(storage, "equipment_snapshot")[0]["line"] == "1라인"


def test_unresolved_missing_target_can_keep_reason_edit(revision_storage):
    """교체 후 대상이 끊긴 위험 정보도 미확인 상태와 기존 근거를 유지하며 사유를 고친다."""
    storage = revision_storage
    service, _, _, _ = service_for(storage)
    with storage.transaction() as tx:
        risk = tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1,
            "risk_reason": "기존 사유", "target_reference": {"direct_version": {"version_raw": "과거 원문"}},
            "mapping_status": "UNRESOLVED"})
    result = service.save((edit(service, "risk_rule", {"risk_rule_id": risk["risk_rule_id"]},
                               {"risk_reason": "자료 재확인 필요"}),))
    assert result.refresh.dates[0].risks.equipment[0].status is RiskStatus.UNKNOWN
    assert all_rows(storage, "risk_rule")[0]["mapping_status"] == "UNRESOLVED"
    assert all_rows(storage, "risk_rule")[0]["target_reference"]["direct_version"]["version_raw"] == "과거 원문"


def test_latest_save_result_feeds_existing_status_workbook(revision_storage):
    """직접 수정 후 집계·조회 조건·SW 원문이 기존 표와 편집 가능한 차트에 전달된다."""
    storage = revision_storage
    service, snapshot, _, _ = service_for(storage)
    row = all_rows(storage, "equipment_version")[0]
    result = service.save((edit(service, "equipment_version",
        {"equipment_version_id": row["equipment_version_id"]}, {"version_raw": "v2"}),))
    report = service.status_report(result, snapshot.base_date, scope=(("사업장", "화성"),))
    assert report.summary is result.refresh.dates[0].summary
    assert report.risks is result.refresh.dates[0].risks
    book = build_status_report(report)
    assert book["요약"]["B6"].value == 1
    assert book["요약"]["B7"].value == 0
    assert book["설비별 판정"]["F2"].value == "v2"
    assert book["계열별 현황"]._charts
    assert book["상태 구성비"]._charts
    with pytest.raises(StorageValidationError):
        service.status_report(result, date(2026, 9, 30))


def test_coalesced_pm_keeps_original_software_cells_from_different_batch(revision_storage):
    """다른 입력 작업의 기존 SW로 합쳐도 선택 PM의 원래 버전 셀을 다시 조회할 수 있다."""
    storage = revision_storage
    service, snapshot, _, second = service_for(storage)
    original_version = all_rows(storage, "equipment_version")[0]
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "EXCEL",
            "raw_data": {"두번째현황": {"A5": "v2", "B5": "PM2"}}})
        other_version = tx.insert("equipment_version", {"snapshot_id": snapshot.snapshot_id,
            "family_id": 1, "version_raw": "v2", "release_version_id": second,
            "batch_id": batch["batch_id"],
            "source_reference": {"version_raw": {"sheet": "두번째현황", "cells": ["A5"]}}})
        first_pm = tx.insert("equipment_pm", {"snapshot_id": snapshot.snapshot_id,
            "equipment_version_id": original_version["equipment_version_id"], "pm_name": "PM1",
            "group_id": 1, "model_name_raw": "O2", "batch_id": snapshot.batch_id,
            "source_reference": location("pm_name", "F1")})
        second_pm = tx.insert("equipment_pm", {"snapshot_id": snapshot.snapshot_id,
            "equipment_version_id": other_version["equipment_version_id"], "pm_name": "PM2",
            "group_id": 1, "model_name_raw": "O2", "batch_id": batch["batch_id"],
            "source_reference": {"pm_name": {"sheet": "두번째현황", "cells": ["B5"]}}})
    before_batches = deepcopy(all_rows(storage, "import_batch"))
    service.save((edit(service, "equipment_pm", {"pm_id": first_pm["pm_id"]}, {"version_raw": "v2"}),))
    latest = service.get_record("equipment_pm", {"pm_id": first_pm["pm_id"]})
    assert latest.current["equipment_version_id"] == other_version["equipment_version_id"]
    assert any(cell.field == "version_raw" and cell.batch_id == original_version["batch_id"]
               and cell.sheet == "현황" and cell.address == "E1" and cell.value == "v1"
               for cell in latest.original_cells)
    assert any(cell.field == "version_raw" and cell.batch_id == batch["batch_id"]
               and cell.sheet == "두번째현황" and cell.address == "A5" and cell.value == "v2"
               for cell in latest.original_cells)
    assert all_rows(storage, "equipment_pm")[1] == second_pm
    assert len(all_rows(storage, "equipment_version")) == 1
    assert all_rows(storage, "import_batch")[:len(before_batches)] == before_batches


def test_cleared_risk_boundary_does_not_keep_active_reconnection_evidence(revision_storage):
    """사용자가 해제 경계를 지우면 재연결에 쓰는 키는 제거하고 원래 근거는 이력에 보존한다."""
    storage = revision_storage
    service, _, first, second = service_for(storage)
    original_evidence = {"manual": "원본 유지", "end_version": {
        "release_version_id": second, "family_id": 1, "version_raw": "v2",
    }}
    with storage.transaction() as tx:
        risk = tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1,
            "release_version_id": first, "start_release_version_id": first,
            "end_release_version_id": second, "risk_reason": "기간 위험",
            "target_reference": original_evidence, "mapping_status": "VERIFIED"})
    result = service.save((edit(service, "risk_rule", {"risk_rule_id": risk["risk_rule_id"]},
                               {"end_release_version_id": None}),))
    current = all_rows(storage, "risk_rule")[0]
    assert current["end_release_version_id"] is None
    assert "end_version" not in current["target_reference"]
    assert current["target_reference"]["manual"] == "원본 유지"
    journal = next(batch for batch in all_rows(storage, "import_batch")
                   if batch["batch_id"] == result.audit_batch_id)["raw_data"]["management_edit"]
    previous = next(record["current"] for record in journal["before"]
                    if record["table"] == "risk_rule")
    assert previous["target_reference"] == original_evidence


def test_selected_pm_cannot_explicitly_clear_other_pms_known_target_link(revision_storage):
    """기존 SW로 합칠 때 명시한 미연결을 다른 PM의 확정 연결로 조용히 대체하지 않는다."""
    storage = revision_storage
    service, _, _, _ = service_for(storage, shared=True)
    pms = all_rows(storage, "equipment_pm")
    service.save((edit(service, "equipment_pm", {"pm_id": pms[0]["pm_id"]}, {"version_raw": "v2"}),))
    before = all_rows(storage, "equipment_pm"), all_rows(storage, "equipment_version")
    request = edit(service, "equipment_pm", {"pm_id": pms[1]["pm_id"]},
                   {"version_raw": "v2", "release_version_id": None})
    with pytest.raises(StorageValidationError):
        service.save((request,))
    assert (all_rows(storage, "equipment_pm"), all_rows(storage, "equipment_version")) == before


def test_all_shared_pms_can_confirm_same_changed_release_link_together(revision_storage):
    """모든 공유 PM을 함께 검토한 릴리즈 대응 변경은 선택 날짜의 공유 SW에만 반영한다."""
    storage = revision_storage
    service, snapshot, _, second = service_for(storage, shared=True, second_day=True)
    before_pms = all_rows(storage, "equipment_pm")
    before_versions = all_rows(storage, "equipment_version")
    requests = tuple(edit(service, "equipment_pm", {"pm_id": pm["pm_id"]},
                          {"release_version_id": second}) for pm in before_pms[:2])
    result = service.save(requests)
    after_versions = all_rows(storage, "equipment_version")
    assert after_versions[0]["release_version_id"] == second
    assert {**after_versions[0], "release_version_id": before_versions[0]["release_version_id"]} == before_versions[0]
    assert after_versions[1:] == before_versions[1:]
    assert all_rows(storage, "equipment_pm") == before_pms
    assert result.refresh.dates[0].summary.compliant == 1
    assert result.refresh.dates[0].base_date == snapshot.base_date


def test_one_shared_pm_cannot_change_confirmed_release_link_for_other_pm(revision_storage):
    """같은 SW를 공유하는 PM 하나의 대응 변경으로 다른 PM의 확인 결과를 바꾸지 않는다."""
    storage = revision_storage
    service, _, _, second = service_for(storage, shared=True)
    pm = all_rows(storage, "equipment_pm")[0]
    before = all_rows(storage, "equipment_pm"), all_rows(storage, "equipment_version")
    with pytest.raises(StorageValidationError):
        service.save((edit(service, "equipment_pm", {"pm_id": pm["pm_id"]},
                           {"release_version_id": second}),))
    assert (all_rows(storage, "equipment_pm"), all_rows(storage, "equipment_version")) == before


def _add_pm(storage, snapshot, release_id, *, raw="v2", name="PM3"):
    """별도 원본 셀을 가진 비선택 PM을 같은 날짜의 SW에 연결한다."""
    versions = [row for row in all_rows(storage, "equipment_version")
                if row["snapshot_id"] == snapshot.snapshot_id and row["version_raw"] == raw]
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "EXCEL",
            "raw_data": {"추가PM": {"A1": raw, "B1": name}}})
        software = versions[0] if versions else tx.insert("equipment_version", {
            "snapshot_id": snapshot.snapshot_id, "family_id": 1, "version_raw": raw,
            "release_version_id": release_id, "batch_id": batch["batch_id"],
            "source_reference": {"version_raw": {"sheet": "추가PM", "cells": ["A1"]}},
        })
        return tx.insert("equipment_pm", {"snapshot_id": snapshot.snapshot_id,
            "equipment_version_id": software["equipment_version_id"], "pm_name": name,
            "group_id": 1, "model_name_raw": "O2", "batch_id": batch["batch_id"],
            "source_reference": {"pm_name": {"sheet": "추가PM", "cells": ["B1"]}},
        })


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("existing_target", [False, True])
def test_shared_pm_split_and_link_batch_uses_final_configuration(
    revision_storage, reverse, existing_target,
):
    """공유 PM의 분리·대응 수정은 순서와 무관하며 비선택 PM·날짜·원본을 보존한다."""
    storage = revision_storage
    service, snapshot, _, second = service_for(storage, shared=True, second_day=True)
    selected = all_rows(storage, "equipment_pm")[:2]
    unselected = _add_pm(storage, snapshot, second) if existing_target else None
    before_pms = all_rows(storage, "equipment_pm")
    before_versions = all_rows(storage, "equipment_version")
    before_batches = deepcopy(all_rows(storage, "import_batch"))
    requests = (
        edit(service, "equipment_pm", {"pm_id": selected[0]["pm_id"]}, {"release_version_id": second}),
        edit(service, "equipment_pm", {"pm_id": selected[1]["pm_id"]},
             {"version_raw": "v2", "release_version_id": second}),
    )
    result = service.save(requests[::-1] if reverse else requests)
    current = [service.get_record("equipment_pm", {"pm_id": pm["pm_id"]}) for pm in selected]
    assert [(view.current["version_raw"], view.current["release_version_id"]) for view in current] == [
        ("v1", second), ("v2", second),
    ]
    assert all(any(cell.field == "version_raw" and cell.value == "v1" for cell in view.original_cells)
               for view in current)
    assert [row for row in all_rows(storage, "equipment_pm") if row["snapshot_id"] != snapshot.snapshot_id] == [
        row for row in before_pms if row["snapshot_id"] != snapshot.snapshot_id]
    assert [row for row in all_rows(storage, "equipment_version") if row["snapshot_id"] != snapshot.snapshot_id] == [
        row for row in before_versions if row["snapshot_id"] != snapshot.snapshot_id]
    if unselected:
        assert next(row for row in all_rows(storage, "equipment_pm") if row["pm_id"] == unselected["pm_id"]) == unselected
        source = service.get_record("equipment_pm", {"pm_id": unselected["pm_id"]})
        assert any(cell.field == "pm_name" and cell.value == "PM3" for cell in source.original_cells)
    assert all_rows(storage, "import_batch")[:len(before_batches)] == before_batches
    assert result.refresh.dates[0].summary.compliant == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_pm_software_raw_swap_preserves_unselected_pm_and_originals(revision_storage, reverse):
    """서로 다른 SW의 원문 교환도 선택 PM의 최종값과 원본 추적을 보존한다."""
    storage = revision_storage
    service, snapshot, first, second = service_for(storage, shared=True, second_day=True)
    selected = all_rows(storage, "equipment_pm")[:2]
    service.save((edit(service, "equipment_pm", {"pm_id": selected[1]["pm_id"]}, {"version_raw": "v2"}),))
    unselected = _add_pm(storage, snapshot, second)
    before_pms = all_rows(storage, "equipment_pm")
    before_versions = all_rows(storage, "equipment_version")
    before_batches = deepcopy(all_rows(storage, "import_batch"))
    requests = tuple(edit(service, "equipment_pm", {"pm_id": pm["pm_id"]}, {"version_raw": raw})
                     for pm, raw in zip(selected, ("v2", "v1")))
    service.save(requests[::-1] if reverse else requests)
    current = [service.get_record("equipment_pm", {"pm_id": pm["pm_id"]}) for pm in selected]
    assert [(view.current["version_raw"], view.current["release_version_id"]) for view in current] == [
        ("v2", second), ("v1", first),
    ]
    assert all(any(cell.field == "version_raw" and cell.value == "v1" for cell in view.original_cells)
               for view in current)
    assert next(row for row in all_rows(storage, "equipment_pm") if row["pm_id"] == unselected["pm_id"]) == unselected
    assert [row for row in all_rows(storage, "equipment_pm") if row["snapshot_id"] != snapshot.snapshot_id] == [
        row for row in before_pms if row["snapshot_id"] != snapshot.snapshot_id]
    assert [row for row in all_rows(storage, "equipment_version") if row["snapshot_id"] != snapshot.snapshot_id] == [
        row for row in before_versions if row["snapshot_id"] != snapshot.snapshot_id]
    assert all_rows(storage, "import_batch")[:len(before_batches)] == before_batches


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("invalid_link", [None, "foreign"])
def test_final_shared_pm_link_conflict_rolls_back_whole_batch(revision_storage, reverse, invalid_link):
    """새 공유 대상의 확정 연결과 다른 명시값은 처리 순서와 무관하게 전체를 취소한다."""
    storage = revision_storage
    service, snapshot, _, second = service_for(storage, shared=True, second_day=True)
    selected = all_rows(storage, "equipment_pm")[:2]
    _add_pm(storage, snapshot, second)
    link = release(storage, family=2, raw="foreign-v1") if invalid_link == "foreign" else None
    tables = ("equipment_snapshot", "equipment_pm", "equipment_version", "import_batch")
    before = {table: all_rows(storage, table) for table in tables}
    requests = (
        edit(service, "equipment_pm", {"pm_id": selected[0]["pm_id"]}, {"release_version_id": second}),
        edit(service, "equipment_pm", {"pm_id": selected[1]["pm_id"]},
             {"version_raw": "v2", "release_version_id": link}),
    )
    with pytest.raises(StorageValidationError):
        service.save((edit(service, "equipment_snapshot", {"snapshot_id": snapshot.snapshot_id}, {"line": "수정"}),
                      *(requests[::-1] if reverse else requests)))
    assert {table: all_rows(storage, table) for table in tables} == before


def test_pm_move_cannot_overwrite_unselected_non_pm_software_link(revision_storage):
    """기존 혼재 자료를 정리할 때도 선택하지 않은 비-PM SW의 연결을 보호한다."""
    storage = revision_storage
    service, snapshot, _, second = service_for(storage, shared=True)
    selected = all_rows(storage, "equipment_pm")
    with storage.transaction() as tx:
        tx.insert("equipment_version", {"snapshot_id": snapshot.snapshot_id, "family_id": 1,
            "version_raw": "v2", "release_version_id": second, "batch_id": snapshot.batch_id,
            "source_reference": location("version_raw", "E1")})
    before = {table: all_rows(storage, table) for table in ("equipment_pm", "equipment_version", "import_batch")}
    with pytest.raises(StorageValidationError):
        service.save(tuple(edit(service, "equipment_pm", {"pm_id": pm["pm_id"]},
                                {"version_raw": "v2", "release_version_id": None}) for pm in selected))
    assert {table: all_rows(storage, table) for table in before} == before


@pytest.mark.parametrize("table,id_field,field,value", [
    ("equipment_version", "equipment_version_id", "version_raw", "v1\nv2"),
    ("equipment_version", "equipment_version_id", "version_raw", "#N/A"),
    ("release_version", "release_version_id", "version_raw", "v1\nv2"),
    ("release_version", "release_version_id", "version_raw", "#N/A"),
    ("equipment", "equipment_id", "equipment_code", "EQ-1\nEQ-2"),
])
def test_changed_identity_and_version_formats_are_validated_atomically(
    revision_storage, table, id_field, field, value,
):
    """줄바꿈 식별자·Excel 오류값을 새 관리값으로 저장하지 않고 원래 자료를 유지한다."""
    storage = revision_storage
    service, _, _, _ = service_for(storage)
    before = all_rows(storage, table)
    before_batches = all_rows(storage, "import_batch")
    with pytest.raises(StorageValidationError):
        service.save((edit(service, table, {id_field: before[0][id_field]}, {field: value}),))
    assert all_rows(storage, table) == before
    assert all_rows(storage, "import_batch") == before_batches


def test_item_content_checks_mysql_utf8_byte_limit_before_saving(revision_storage):
    """글자 수만 맞아도 MySQL TEXT의 UTF-8 바이트 한도를 넘는 Item은 저장하지 않는다."""
    storage = revision_storage
    service, _, first, _ = service_for(storage)
    item_id = ReleaseService(storage).register_item(first, ItemInput(content="원래 내용")).current["item_id"]
    before = all_rows(storage, "item"), all_rows(storage, "import_batch")
    with pytest.raises(StorageValidationError):
        service.save((edit(service, "item", {"item_id": item_id}, {"content": "한" * 30000}),))
    assert (all_rows(storage, "item"), all_rows(storage, "import_batch")) == before


def test_unregistered_hotfix_keeps_raw_without_requiring_narrow_pattern(revision_storage):
    """정상 핫픽스 원문은 좁은 버전명 정규식이나 릴리즈 미등록을 이유로 막지 않는다."""
    storage = revision_storage
    service, _, _, _ = service_for(storage)
    version = all_rows(storage, "equipment_version")[0]
    raw = "v2_custom.7_hotfix"
    result = service.save((edit(service, "equipment_version",
        {"equipment_version_id": version["equipment_version_id"]}, {"version_raw": raw}),))
    assert result.records[0].current["version_raw"] == raw
    assert result.records[0].current["release_version_id"] is None


def test_two_release_names_can_be_swapped_in_one_valid_batch(revision_storage):
    """최종 버전명이 고유한 두 릴리즈의 맞교환은 중간 고유키 충돌 없이 함께 저장된다."""
    storage = revision_storage
    service, _, first, second = service_for(storage, second_day=True)
    observed_before = all_rows(storage, "equipment_version")
    before_batches = deepcopy(all_rows(storage, "import_batch"))
    result = service.save((
        edit(service, "release_version", {"release_version_id": first}, {"version_raw": "v2"}),
        edit(service, "release_version", {"release_version_id": second}, {"version_raw": "v1"}),
    ))
    releases = {row["release_version_id"]: row for row in all_rows(storage, "release_version")}
    assert releases[first]["version_raw"] == "v2"
    assert releases[second]["version_raw"] == "v1"
    assert [record.current["version_raw"] for record in result.records] == ["v2", "v1"]
    after = all_rows(storage, "equipment_version")
    assert all(row["version_raw"] == "v1" and row["release_version_id"] == second for row in after)
    assert [{**row, "release_version_id": first} for row in after] == observed_before
    assert all_rows(storage, "import_batch")[:len(before_batches)] == before_batches
    journal = next(batch for batch in all_rows(storage, "import_batch")
                   if batch["batch_id"] == result.audit_batch_id)["raw_data"]["management_edit"]
    assert [record["current"]["version_raw"] for record in journal["before"]] == ["v1", "v2"]


def test_duplicate_final_release_name_is_validation_error_and_changes_nothing(revision_storage):
    """수정 묶음의 최종 버전명이 겹치면 DB 오류 전에 보완 요청하고 기존값을 보존한다."""
    storage = revision_storage
    service, _, first, second = service_for(storage)
    before = all_rows(storage, "release_version"), all_rows(storage, "import_batch")
    with pytest.raises(StorageValidationError):
        service.save((
            edit(service, "release_version", {"release_version_id": first}, {"version_raw": "same"}),
            edit(service, "release_version", {"release_version_id": second}, {"version_raw": "same"}),
        ))
    assert (all_rows(storage, "release_version"), all_rows(storage, "import_batch")) == before


def test_duplicate_global_equipment_code_is_validation_error(revision_storage):
    """사업장 전체에서 고유한 호기 코드를 기존 다른 설비와 같게 수정할 수 없다."""
    storage = revision_storage
    service, _, first, _ = service_for(storage)
    observe(storage, first, code="EQ-2")
    equipment = all_rows(storage, "equipment")
    before_batches = all_rows(storage, "import_batch")
    with pytest.raises(StorageValidationError):
        service.save((edit(service, "equipment", {"equipment_id": equipment[0]["equipment_id"]},
                           {"equipment_code": "EQ-2"}),))
    assert all_rows(storage, "equipment") == equipment
    assert all_rows(storage, "import_batch") == before_batches


def test_two_global_equipment_codes_can_be_swapped_without_moving_records(revision_storage):
    """두 설비의 코드 오기를 함께 맞바꿔도 실제 ID와 날짜별 현황 연결은 그대로 유지한다."""
    storage = revision_storage
    service, _, first, _ = service_for(storage)
    observe(storage, first, code="EQ-2")
    equipment = all_rows(storage, "equipment")
    snapshots = all_rows(storage, "equipment_snapshot")
    result = service.save(tuple(edit(service, "equipment", {"equipment_id": row["equipment_id"]},
        {"equipment_code": target}) for row, target in zip(equipment, ("EQ-2", "EQ-1"))))
    after = all_rows(storage, "equipment")
    assert [row["equipment_code"] for row in after] == ["EQ-2", "EQ-1"]
    assert [row["equipment_id"] for row in after] == [row["equipment_id"] for row in equipment]
    assert all_rows(storage, "equipment_snapshot") == snapshots
    assert [record.current["equipment_code"] for record in result.records] == ["EQ-2", "EQ-1"]


def test_changed_applicability_invalidates_removed_target_without_changing_selection(revision_storage):
    """적용 후보에서 현재 목표를 제외하면 이전 요청의 후보를 재사용하지 않고 재선택을 안내한다."""
    storage = revision_storage
    service, _, first, second = service_for(storage)
    view = service.get_applicability(1, 1)
    candidate = CandidateConfirmation(release_id=first, applicability_basis="원형 모델 적용 표시",
        order_basis="확인한 순서", source_text="재검토한 적용 자료")
    result = service.save((), applicability=(ApplicabilityEdit(1, 1, view.revision, (candidate,)),))
    target = result.refresh.targets[0]
    assert target.selected_release_id == second
    assert not target.valid
    assert target.reason
    assert result.refresh.dates[0].summary.undetermined == 1
    assert result.refresh.dates[0].summary.compliant == 0
    assert result.refresh.dates[0].summary.below == 0
    latest = service.get_applicability(1, 1)
    assert [value.release_id for value in latest.value.candidates] == [first]
    snapshot = all_rows(storage, "equipment_snapshot")[0]
    following = service.save((edit(service, "equipment_snapshot", {"snapshot_id": snapshot["snapshot_id"]},
                                    {"line": "후속 수정"}),))
    assert following.refresh.targets[0].selected_release_id == second
    assert not following.refresh.targets[0].valid
    assert following.refresh.dates[0].summary.undetermined == 1
