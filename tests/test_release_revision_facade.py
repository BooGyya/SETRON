"""BE-010 관리값 변경과 재판정이 한 저장 묶음인지 검증한다."""

from datetime import date

import pytest

from setron.features.item.schemas import Application, ItemState, TargetScope
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.revision import ReleaseRevisionService
from setron.features.release.schemas import ItemInput, ItemLinkInput, ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.risk.schemas import RiskStatus
from setron.features.storage.validation import StorageValidationError

from test_release_revision import all_rows, observation, release, revision_storage


def test_register_release_updates_existing_unregistered_history(revision_storage):
    """등록 즉시 과거 미등록 원문을 연결하고 원본·다른 날짜를 그대로 유지한다.

    param
    - revision_storage: 독립적인 SQLite 저장소.
    """
    storage = revision_storage
    observation(storage, versions=((1, 1, "v1_n4", None),))
    observation(storage, day=date(2026, 10, 2), versions=((1, 1, "v1_n4", None),))
    before = all_rows(storage, "equipment_version")
    result = ReleaseRevisionService(storage).register_release(
        ReleaseInput(family_id=1, version_raw="v1_n4", version_order=1),
        (ItemInput(content="확인한 변경", item_no=7),),
    )
    registered = result.value.release.current["release_version_id"]
    after = all_rows(storage, "equipment_version")
    assert [row["release_version_id"] for row in after] == [registered, registered]
    assert [{**row, "release_version_id": None} for row in after] == before
    assert len(result.refresh.dates) == 2
    assert all(day.summary.equipment_count == 1 for day in result.refresh.dates)
    assert result.value.items[0].item.current["content"] == "확인한 변경"


def test_release_rename_rechecks_old_correspondence_without_changing_original(revision_storage):
    """대응 버전명을 수정하면 이전 원문을 새 버전으로 단정하지 않는다.

    param
    - revision_storage: 독립적인 SQLite 저장소.
    """
    storage = revision_storage
    identifier = release(storage)
    observation(storage, versions=((1, 1, "v1", identifier),))
    before = all_rows(storage, "equipment_version")[0]
    result = ReleaseRevisionService(storage).update_release(identifier, {"version_raw": "v2"})
    assert result.value.release.current["version_raw"] == "v2"
    after = all_rows(storage, "equipment_version")[0]
    assert after["release_version_id"] is None
    assert {**after, "release_version_id": identifier} == before


def test_confirmed_alias_can_preserve_correspondence_after_rename(revision_storage):
    """이전 표기의 확정 대응을 명시한 경우에만 이름 수정 후 연결을 유지한다.

    param
    - revision_storage: 독립적인 SQLite 저장소.
    """
    storage = revision_storage
    identifier = release(storage)
    observation(storage, versions=((1, 1, "v1", identifier),))
    service = ReleaseRevisionService(storage, RefreshContext(aliases={1: {"v1": "v2"}}))
    service.update_release(identifier, {"version_raw": "v2"})
    assert all_rows(storage, "equipment_version")[0]["release_version_id"] == identifier


def test_saved_item_scope_and_state_recalculate_item_and_risk_together(revision_storage):
    """범위·상태 저장 결과를 Item 적용과 환경·안전 위험에 함께 반영한다.

    param
    - revision_storage: 독립적인 SQLite 저장소.
    """
    storage = revision_storage
    identifier = release(storage)
    observation(storage, versions=((1, 1, "v1", identifier),))
    service = ReleaseRevisionService(storage)
    registered = service.register_item(identifier, ItemInput(content="안전 개선", item_no=7))
    item_id = registered.value.current["item_id"]
    assert registered.refresh.dates[0].item_summaries[0].equipment[0].application is Application.UNKNOWN
    with storage.transaction() as tx:
        tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 1, "item_id": item_id,
            "risk_reason": "필수 안전 개선", "target_reference": {"manual": "보존"}, "mapping_status": "VERIFIED"})
    safe = service.save_item_links(identifier, (
        ItemLinkInput(item_id=item_id, item_state=ItemState.APPLIED, target_scope=TargetScope.ALL),
    ))
    assert safe.refresh.dates[0].item_summaries[0].equipment[0].application is Application.APPLIED
    assert safe.refresh.dates[0].risks.equipment[0].status is RiskStatus.NO_RISK
    unsafe = service.save_item_links(identifier, (
        ItemLinkInput(item_id=item_id, item_state=ItemState.NOT_APPLIED, target_scope=TargetScope.ALL),
    ))
    assert unsafe.refresh.dates[0].item_summaries[0].equipment[0].application is Application.NOT_APPLIED
    assert unsafe.refresh.dates[0].risks.equipment[0].status is RiskStatus.RISK
    assert all_rows(storage, "risk_rule")[0]["target_reference"] == {"manual": "보존"}


def test_shared_item_edit_updates_all_linked_families(revision_storage):
    """공유 Item 수정은 다른 계열의 명시적 연결과 원본을 보존하며 새 내용으로 판정한다.

    param
    - revision_storage: 독립적인 SQLite 저장소.
    """
    storage = revision_storage
    first = release(storage)
    other = release(storage, family=2, raw="C3-v1")
    low = ReleaseService(storage)
    item_id = low.register_item(first, ItemInput(content="이전 내용", item_no=7)).current["item_id"]
    low.save_item_links(first, (ItemLinkInput(item_id=item_id, item_state=ItemState.APPLIED,
                                            target_scope=TargetScope.ALL),))
    low.save_item_links(other, (ItemLinkInput(item_id=item_id, item_state=ItemState.APPLIED,
                                            target_scope=TargetScope.ALL),))
    observation(storage, versions=((1, 1, "v1", first), (2, 3, "C3-v1", other)), pms=True)
    links = all_rows(storage, "release_version_item")
    result = ReleaseRevisionService(storage).update_item(item_id, {"content": "공유 수정 내용"})
    assert result.value.current["content"] == "공유 수정 내용"
    assert all_rows(storage, "release_version_item") == links
    summary = result.refresh.dates[0].item_summaries[0]
    assert summary.item.content == "공유 수정 내용"
    assert len(summary.equipment[0].pms) == 2
    assert summary.equipment[0].application is Application.APPLIED


def test_recalculation_failure_rolls_back_release_and_new_items(revision_storage, monkeypatch):
    """재판정에 실패하면 앞서 만든 입력 작업·릴리즈·Item을 모두 취소한다.

    param
    - revision_storage: 독립적인 SQLite 저장소.
    - monkeypatch: 최종 재판정 실패를 주입하는 pytest 도구.
    """
    storage = revision_storage
    service = ReleaseRevisionService(storage)

    def fail_refresh(tx, **kwargs):
        """관리값 저장 이후의 판정 오류를 재현한다.

        param
        - tx, kwargs: 공통 저장 트랜잭션과 재판정 조건.

        raises
        - StorageValidationError: 테스트에서 주입한 재판정 실패.
        """
        raise StorageValidationError("판정 자료 확인 필요")

    monkeypatch.setattr(service.refresher, "refresh_in", fail_refresh)
    with pytest.raises(StorageValidationError, match="판정 자료"):
        service.register_release(ReleaseInput(family_id=1, version_raw="v1"), (ItemInput(content="보류"),))
    for table in ("import_batch", "release_version", "item", "release_version_item"):
        assert all_rows(storage, table) == []


def test_edit_recalculation_failure_restores_previous_management_values(revision_storage, monkeypatch):
    """수정 후 재판정 실패도 기존 관리값과 수정 이력을 복구한다.

    param
    - revision_storage: 독립적인 SQLite 저장소.
    - monkeypatch: 재판정 실패를 주입하는 pytest 도구.
    """
    storage = revision_storage
    identifier = release(storage)
    before = all_rows(storage, "release_version")
    service = ReleaseRevisionService(storage)

    def fail_refresh(tx, **kwargs):
        """수정 이후의 재판정 실패를 재현한다.

        param
        - tx, kwargs: 공통 저장 트랜잭션과 재판정 조건.

        raises
        - StorageValidationError: 테스트에서 주입한 재판정 실패.
        """
        raise StorageValidationError("재판정 실패")

    monkeypatch.setattr(service.refresher, "refresh_in", fail_refresh)
    with pytest.raises(StorageValidationError, match="재판정 실패"):
        service.update_release(identifier, {"version_order": 9})
    assert all_rows(storage, "release_version") == before


def test_operation_uses_captured_target_context_when_caller_changes_draft(revision_storage, monkeypatch):
    """저장 시작 후 호출자의 목표 초안이 바뀌어도 시작 시 조건으로만 재판정한다.

    param
    - revision_storage: 독립적인 SQLite 저장소.
    - monkeypatch: 사전 확인 도중 호출자 초안 변경을 재현하는 pytest 도구.
    """
    storage = revision_storage
    first = release(storage)
    second = release(storage, raw="v2", order=2)
    observation(storage, versions=((1, 1, "v1", first),))
    targets = {(1, 1): first}
    applicability = {(1, 1): (first, second)}
    service = ReleaseRevisionService(storage, RefreshContext(targets=targets, applicability=applicability))
    discover = service._discover_release_family

    def change_caller_draft(identifier):
        """읽기 업무 이후 호출자가 관리하는 별도 초안을 바꾼다.

        param
        - identifier: 수정할 릴리즈 ID.

        return
        - 실제 저장된 계열 ID.
        """
        family_id = discover(identifier)
        targets[(1, 1)] = second
        applicability[(1, 1)] = ()
        return family_id

    monkeypatch.setattr(service, "_discover_release_family", change_caller_draft)
    result = service.update_release(first, {"released_at": date(2026, 10, 3)})
    assert result.refresh.targets[0].selected_release_id == first
    assert result.refresh.targets[0].valid
    assert targets[(1, 1)] == second and applicability[(1, 1)] == ()
