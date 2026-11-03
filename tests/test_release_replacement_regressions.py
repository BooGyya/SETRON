"""BE-010 검토에서 확인한 수동 위험 보존·릴리즈 원문 교환 회귀 검증."""

from dataclasses import replace

import pytest

from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.replacement import FamilyReplacementService
from setron.features.release.service import ReleaseService
from setron.features.storage.validation import StorageValidationError

from test_release_replacement import note, original, request, reviewed, snapshot
from test_release_revision import all_rows, observation, revision_storage


def manual_target(storage):
    """릴리즈 포함 관계 없이 직접 등록한 수동 위험 대상과 원본을 만든다.

    param
    - storage: 독립적인 업무 저장소.

    return
    - 생성한 Item 행과 수동 위험 행.
    """
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL"})
        item = tx.insert("item", {"content": "직접 확인한 위험 대상", "item_no": 99,
                                  "batch_id": batch["batch_id"]})
        risk = tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 1,
            "item_id": item["item_id"], "risk_reason": "원본 밖의 수동 지정", "mapping_status": "UNRESOLVED",
            "target_reference": {"사용자 메모": "보존"}})
        return item, risk


def test_unlinked_manual_target_keeps_pointer_evidence_and_exclusion_from_replacement(revision_storage):
    """현재 노트에 없는 수동 대상의 연결·원문을 보존하고 교체 대상으로 취급하지 않는지 검증한다.

    param
    - revision_storage: 독립적인 업무 저장소.
    """
    storage = revision_storage
    initial = original(storage)
    initial_item_id = initial.items[0].item.current["item_id"]
    item, risk = manual_target(storage)
    original_batches = all_rows(storage, "import_batch")
    service, value = FamilyReplacementService(storage), request()
    preview = service.preview(value)
    assert preview.item_matches == {"item": initial_item_id}
    proposal = preview.risk_reconnections[0]
    assert not proposal.can_restore and proposal.proposed_targets["item_id"] is None
    assert proposal.target_reference["item"]["item_id"] == item["item_id"]
    assert proposal.target_reference["item"]["content"] == item["content"]
    assert proposal.target_reference["item"]["memberships"] == []
    details, refreshed = service.replace(value, reviewed(service, value))
    assert [entry.item.current["item_id"] for entry in details[0].items] == [initial_item_id]
    saved = next(row for row in all_rows(storage, "risk_rule") if row["risk_rule_id"] == risk["risk_rule_id"])
    assert saved["item_id"] == item["item_id"] and saved["mapping_status"] == "UNRESOLVED"
    assert saved["risk_reason"] == risk["risk_reason"]
    assert saved["target_reference"]["사용자 메모"] == "보존"
    assert saved["target_reference"]["item"]["batch_id"] == item["batch_id"]
    assert next(row for row in all_rows(storage, "item") if row["item_id"] == item["item_id"]) == item
    assert all(row["item_id"] != item["item_id"] for row in all_rows(storage, "release_version_item"))
    assert all_rows(storage, "import_batch")[:len(original_batches)] == original_batches
    assert risk["risk_rule_id"] in refreshed.unresolved_risk_ids


def test_unlinked_risk_target_in_other_family_is_not_changed(revision_storage):
    """다른 계열에 연결된 수동 위험 대상의 Item·연결·그 계열 위험이 유지되는지 검증한다.

    param
    - revision_storage: 독립적인 업무 저장소.
    """
    storage = revision_storage
    original(storage)
    outside = original(storage, raw="c1", number=99, content="다른 계열의 내용", family=2)
    item = outside.items[0].item.current
    with storage.transaction() as tx:
        target = tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 1,
            "item_id": item["item_id"], "risk_reason": "계열 확인 대기", "mapping_status": "UNRESOLVED",
            "target_reference": {"메모": "수동 대상"}})
        outside_risk = tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 2,
            "item_id": item["item_id"], "risk_reason": "다른 계열 위험", "mapping_status": "VERIFIED",
            "target_reference": {"메모": "다른 계열 근거"}})
    before = ReleaseService(storage).get_release(outside.release.current["release_version_id"])
    service, value = FamilyReplacementService(storage), request()
    service.replace(value, reviewed(service, value))
    assert ReleaseService(storage).get_release(outside.release.current["release_version_id"]) == before
    risks = {row["risk_rule_id"]: row for row in all_rows(storage, "risk_rule")}
    assert risks[outside_risk["risk_rule_id"]] == outside_risk
    assert risks[target["risk_rule_id"]]["item_id"] == item["item_id"]
    assert risks[target["risk_rule_id"]]["mapping_status"] == "UNRESOLVED"


def test_unlinked_manual_target_changes_invalidate_review(revision_storage):
    """별도 수동 대상의 관리값이 바뀌면 오래된 교체 검토로 저장하지 않는지 검증한다.

    param
    - revision_storage: 독립적인 업무 저장소.
    """
    storage = revision_storage
    original(storage)
    item, _ = manual_target(storage)
    service, value = FamilyReplacementService(storage), request()
    review = reviewed(service, value)
    ReleaseService(storage).update_item(item["item_id"], {"content": "검토 이후 바뀐 수동 내용"})
    before = snapshot(storage)
    with pytest.raises(StorageValidationError, match="검토 후 자료가 바뀌었습니다"):
        service.replace(value, review)
    assert snapshot(storage) == before


def test_missing_manual_target_remains_unresolved(revision_storage):
    """대상 연결 자체가 없는 수동 위험을 새 노트 Item에 임의로 연결하지 않는지 검증한다.

    param
    - revision_storage: 독립적인 업무 저장소.
    """
    storage = revision_storage
    original(storage)
    with storage.transaction() as tx:
        risk = tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 1,
            "risk_reason": "대상 미확인", "mapping_status": "UNRESOLVED", "target_reference": {"메모": "보존"}})
    service, value = FamilyReplacementService(storage), request()
    _, refreshed = service.replace(value, reviewed(service, value))
    assert all_rows(storage, "risk_rule")[0] == risk
    assert risk["risk_rule_id"] in refreshed.unresolved_risk_ids


def managed_cycle(storage, count):
    """원본 식별은 유지하고 현재 관리 버전명을 순환 교환한다.

    param
    - storage: 독립적인 업무 저장소.
    - count: 순환에 포함할 릴리즈 수.

    return
    - 원본 버전별 기존 ID와 변경 후 전체 교체 요청.
    """
    rows = [(f"v{index}", index + 6, f"내용 {index}", index) for index in range(1, count + 1)]
    ids = {raw: original(storage, raw=raw, number=number, content=content, order=order)
           .release.current["release_version_id"] for raw, number, content, order in rows}
    manager = ReleaseService(storage)
    manager.update_release(ids["v1"], {"version_raw": "교환 준비"})
    for index in range(count, 1, -1):
        manager.update_release(ids[f"v{index}"], {"version_raw": "v1" if index == count else f"v{index + 1}"})
    manager.update_release(ids["v1"], {"version_raw": "v2"})
    return ids, note(*rows)


@pytest.mark.parametrize("count", [2, 3])
def test_retained_version_name_cycles_preserve_ids_sources_targets_and_risks(revision_storage, count):
    """최종 원문이 중복되지 않는 두 행·세 행의 교환과 선택한 N·위험 근거를 검증한다.

    param
    - revision_storage: 독립적인 업무 저장소.
    - count: 순환 교환하는 릴리즈 수.
    """
    storage = revision_storage
    ids, value = managed_cycle(storage, count)
    batches = all_rows(storage, "import_batch")
    observation(storage, versions=((1, 1, "v1", ids[f"v{count}"]),))
    with storage.transaction() as tx:
        risk = tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1,
            "release_version_id": ids["v1"], "risk_reason": "교환 후 확인할 수동 위험", "mapping_status": "VERIFIED",
            "target_reference": {"사용자 메모": "보존"}})
    context = RefreshContext(targets={(1, 1): ids["v1"], (2, 1): ids["v2"]},
                             applicability={(1, 1): tuple(ids.values()), (2, 1): tuple(ids.values())})
    service = FamilyReplacementService(storage, context)
    choices = {("release", f"release{index}", "version_raw"): "INCOMING" for index in range(1, count + 1)}
    details, refreshed = service.replace(value, reviewed(service, value, choices=choices,
                                                         risk_confirmations=(risk["risk_rule_id"],)))
    assert {entry.release.current["version_raw"]: entry.release.current["release_version_id"] for entry in details} == ids
    for entry in details:
        assert entry.release.current["edited_fields"] == []
        assert next(cell.value for cell in entry.release.original_cells if cell.field == "version_raw") == entry.release.current["version_raw"]
    assert all_rows(storage, "import_batch")[:len(batches)] == batches
    assert all(target.valid for target in refreshed.targets)
    assert [target.selected_release_id for target in refreshed.targets] == [ids["v1"], ids["v2"]]
    saved_risk = all_rows(storage, "risk_rule")[0]
    assert saved_risk["release_version_id"] == ids["v1"] and saved_risk["mapping_status"] == "VERIFIED"
    assert saved_risk["target_reference"]["direct_version"]["version_raw"] == "v2"
    assert saved_risk["target_reference"]["사용자 메모"] == "보존"
    assert all_rows(storage, "equipment_version")[0]["release_version_id"] == ids["v1"]
    assert not any(row["version_raw"].startswith("__setron_replace_") for row in all_rows(storage, "release_version"))


def test_new_release_can_take_name_freed_by_retained_release(revision_storage):
    """새 행이 먼저 저장되어도 이름을 되돌릴 기존 행과의 일시 중복으로 실패하지 않는지 검증한다.

    param
    - revision_storage: 독립적인 업무 저장소.
    """
    storage = revision_storage
    existing = original(storage, content="기존 내용")
    retained_id = existing.release.current["release_version_id"]
    ReleaseService(storage).update_release(retained_id, {"version_raw": "v2"})
    value = note(("v2", 8, "신규 내용", 2), ("v1", 7, "기존 내용", 1))
    service = FamilyReplacementService(storage)
    details, _ = service.replace(value, reviewed(service, value,
        choices={("release", "release2", "version_raw"): "INCOMING"}))
    saved = {entry.release.current["version_raw"]: entry.release.current["release_version_id"] for entry in details}
    assert saved["v1"] == retained_id and saved["v2"] != retained_id


def test_failure_after_name_staging_restores_all_original_rows(revision_storage, monkeypatch):
    """임시 이름을 사용한 뒤 저장에 실패해도 원본·관리값·위험을 모두 되돌리는지 검증한다.

    param
    - revision_storage: 독립적인 업무 저장소.
    - monkeypatch: 이름 교환 뒤의 저장 실패를 주입하는 pytest 도구.
    """
    storage = revision_storage
    _, value = managed_cycle(storage, 3)
    before = snapshot(storage)
    service = FamilyReplacementService(storage)
    observed = []

    def fail_link(tx, release, link):
        """일부 최종 원문을 저장한 뒤 남은 임시 원문을 확인하고 업무를 취소한다.

        param
        - tx, release, link: 현재 트랜잭션·저장한 릴리즈·연결할 Item.

        raises
        - StorageValidationError: 테스트가 주입하는 저장 실패.
        """
        observed.extend(row["version_raw"] for row in service.records.rows(tx, "release_version"))
        raise StorageValidationError("교환 이후 저장 실패")

    monkeypatch.setattr(service.releases, "_link", fail_link)
    choices = {("release", f"release{index}", "version_raw"): "INCOMING" for index in (1, 2, 3)}
    with pytest.raises(StorageValidationError, match="교환 이후 저장 실패"):
        service.replace(value, reviewed(service, value, choices=choices))
    assert any(raw.startswith("__setron_replace_") for raw in observed)
    assert snapshot(storage) == before


def test_duplicate_final_names_are_rejected_without_saving_staged_names(revision_storage):
    """선택한 최종 버전명 자체가 중복이면 원본과 기존 관리값을 유지하는지 검증한다.

    param
    - revision_storage: 독립적인 업무 저장소.
    """
    storage = revision_storage
    _, value = managed_cycle(storage, 2)
    second = replace(value.releases[1], value=replace(value.releases[1].value, version_raw="v1"))
    value = replace(value, releases=(value.releases[0], second))
    service = FamilyReplacementService(storage)
    choices = {("release", f"release{index}", "version_raw"): "INCOMING" for index in (1, 2)}
    before = snapshot(storage)
    with pytest.raises(StorageValidationError, match="최종 릴리즈 버전 원문이 중복"):
        service.replace(value, reviewed(service, value, choices=choices))
    assert snapshot(storage) == before
