"""위험 지정 수정·삭제의 충돌 검사와 실제 SQLite 저장 경계를 검증한다."""

from copy import deepcopy

import pytest

from setron.features.release.refresh import ReleaseRefreshService
from setron.features.risk.schemas import RiskStatus
from setron.features.storage.validation import StorageValidationError

from test_management_service import service_for
from test_release_revision import all_rows, release, revision_storage  # noqa: F401


def risk_values(reference, **changes):
    return {"rule_type": "VERSION_BELOW", "family_id": 1,
            "release_version_id": reference, "mapping_status": "VERIFIED",
            "risk_reason": "기준 버전 미만 위험", **changes}


def database_state(storage):
    """변경 대상뿐 아니라 재판정과 감사 이력의 부분 저장도 감지한다."""
    return {table: all_rows(storage, table) for table in (
        "risk_rule", "import_batch", "app_setting", "release_version", "item",
        "release_version_item", "equipment_snapshot", "equipment_version", "equipment_pm",
    )}


def audit_for(storage, result):
    return next(row["raw_data"] for row in all_rows(storage, "import_batch")
                if row["batch_id"] == result.audit_batch_id)


@pytest.mark.parametrize("enabled", [True, False])
def test_update_keeps_identity_activation_metadata_and_other_rules(revision_storage, enabled):
    storage = revision_storage
    service, _, first, second = service_for(storage, second_day=True)
    created = service.create_risk_rule(risk_values(second, is_enabled=enabled, item_manager={
        "schema": 1, "name": "원래 지정", "category": "공정 위험", "memo": "원래 메모",
        "model_ids": [1], "site": "화성", "line": "1라인",
    })).records[0]
    counterpart = service.create_risk_rule(risk_values(
        second, rule_type="DIRECT_VERSION", risk_reason="다른 위험 지정")).records[0]
    with storage.transaction() as tx:
        reference = deepcopy(tx.get("risk_rule", created.key)["target_reference"])
        reference["external_evidence"] = {"source": "보존할 기존 확인 근거"}
        tx.update("risk_rule", created.key, {"target_reference": reference})
    record = service.get_record("risk_rule", created.key)
    before = deepcopy(record.current)
    preserved = {table: all_rows(storage, table) for table in (
        "release_version", "item", "release_version_item", "equipment_snapshot", "equipment_pm",
    )}

    result = service.update_risk_rule(record, {
        "rule_type": "VERSION_ABOVE", "release_version_id": first, "risk_reason": "수정한 위험 기준",
        "item_manager": {"name": "수정한 지정", "memo": "수정한 메모"},
    })

    assert len(result.records) == 1
    updated = result.records[0]
    assert updated.table == "risk_rule" and updated.key == created.key
    assert updated.current["risk_rule_id"] == created.current["risk_rule_id"]
    assert updated.revision != record.revision
    assert updated.current["is_enabled"] == enabled
    assert updated.current["rule_type"] == "VERSION_ABOVE"
    assert updated.current["release_version_id"] == first
    assert updated.current["risk_reason"] == "수정한 위험 기준"
    expected_metadata = {**before["target_reference"]["item_manager"],
                         "name": "수정한 지정", "memo": "수정한 메모"}
    assert updated.current["target_reference"]["item_manager"] == expected_metadata
    assert updated.current["target_reference"]["external_evidence"] == reference["external_evidence"]
    assert updated.current["target_reference"]["direct_version"]["version_raw"] == "v1"
    assert service.get_record("risk_rule", counterpart.key).current == counterpart.current
    assert {table: all_rows(storage, table) for table in preserved} == preserved
    assert len(all_rows(storage, "risk_rule")) == 2
    assert len(result.refresh.dates) == 2 and len(result.ages) == 2
    assert all(day.risks.counts()[RiskStatus.RISK] == 0 for day in result.refresh.dates)
    assert audit_for(storage, result)["management_edit"]["before"] == [
        {"table": "risk_rule", "current": before}]


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_stale_mutation_preserves_newer_rule_and_writes_nothing(revision_storage, operation):
    storage = revision_storage
    service, _, _, second = service_for(storage)
    old = service.create_risk_rule(risk_values(second)).records[0]
    latest = service.update_risk_rule(old, {"risk_reason": "다른 창에서 저장한 최신 사유"}).records[0]
    before = database_state(storage)

    with pytest.raises(StorageValidationError, match="바뀌|다시|검토"):
        if operation == "update":
            service.update_risk_rule(old, {"risk_reason": "오래된 창의 사유"})
        else:
            service.delete_risk_rule(old)

    assert database_state(storage) == before
    assert service.get_record("risk_rule", old.key).current == latest.current


@pytest.mark.parametrize("invalid", ["other-family", "unordered", "duplicated", "missing"])
def test_invalid_directional_edit_rejects_without_partial_writes(revision_storage, invalid):
    storage = revision_storage
    service, _, _, second = service_for(storage)
    record = service.create_risk_rule(risk_values(second)).records[0]
    if invalid == "missing":
        reference = None
    else:
        reference = release(storage, family=2 if invalid == "other-family" else 1,
                            raw="invalid-reference", order=None if invalid == "unordered" else 3)
        if invalid == "duplicated":
            release(storage, raw="same-order", order=3)
    record = service.get_record("risk_rule", record.key)
    before = database_state(storage)

    with pytest.raises(StorageValidationError):
        service.update_risk_rule(record, {"release_version_id": reference,
                                          "risk_reason": "저장되면 안 되는 사유"})

    assert database_state(storage) == before


def test_delete_only_selected_rule_rejudges_and_records_complete_before_value(revision_storage):
    storage = revision_storage
    service, _, first, second = service_for(storage, second_day=True)
    selected = service.create_risk_rule(risk_values(
        first, rule_type="DIRECT_VERSION", risk_reason="삭제할 위험 지정")).records[0]
    remaining = service.create_risk_rule(risk_values(
        second, rule_type="DIRECT_VERSION", risk_reason="유지할 위험 지정")).records[0]
    before_batches = all_rows(storage, "import_batch")
    preserved = {table: all_rows(storage, table) for table in (
        "release_version", "item", "release_version_item", "equipment_snapshot", "equipment_version", "equipment_pm",
    )}

    result = service.delete_risk_rule(service.get_record("risk_rule", selected.key))

    assert result.records == ()
    assert all_rows(storage, "risk_rule") == [remaining.current]
    assert {table: all_rows(storage, table) for table in preserved} == preserved
    assert all_rows(storage, "import_batch")[:-1] == before_batches
    assert len(result.refresh.dates) == 2 and len(result.ages) == 2
    assert all(day.risks.has_rules and day.risks.counts()[RiskStatus.RISK] == 0
               for day in result.refresh.dates)
    deletion = audit_for(storage, result)["management_delete_risk"]
    assert deletion["before"] == {"table": "risk_rule", "current": selected.current}
    assert deletion["key"] == selected.key and deletion["revision"] == selected.revision


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_refresh_failure_rolls_back_mutation_and_audit(revision_storage, monkeypatch, operation):
    storage = revision_storage
    service, _, _, second = service_for(storage)
    record = service.create_risk_rule(risk_values(second)).records[0]
    before = database_state(storage)

    def fail_refresh(*args, **kwargs):
        raise RuntimeError("재판정 실패 검증")

    monkeypatch.setattr(ReleaseRefreshService, "refresh_in", fail_refresh)
    with pytest.raises(RuntimeError, match="재판정 실패 검증"):
        if operation == "update":
            service.update_risk_rule(record, {"risk_reason": "취소되어야 할 수정"})
        else:
            service.delete_risk_rule(record)

    assert database_state(storage) == before
