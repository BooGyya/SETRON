"""수동 Item 포함 상태·모델 범위의 재가져오기 충돌 선택을 검증한다."""

from dataclasses import replace

import pytest

from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.replacement import FamilyReplacementService
from setron.features.release.replacement_schemas import ReplacementLink, ReplacementRelease, ReplacementRequest, ReplacementReview, ReplacementReviewRequired
from setron.features.release.revision import ReleaseRevisionService
from setron.features.release.schemas import ItemLinkInput, ReleaseInput
from setron.features.risk.schemas import RiskStatus
from setron.features.storage.validation import StorageValidationError

from test_release_replacement import original, request, reviewed, snapshot, source
from test_release_revision import all_rows, observation, revision_storage


def edited_safety_link(storage):
    """명시적인 수동 미적용·모델 범위와 환경안전 위험을 준비한다.

    param
    - storage: 독립적인 업무 테스트 저장소.

    return
    - 저장한 릴리즈·Item ID와 수정 직후의 위험 판정.
    """
    detail = original(storage)
    release_id = detail.release.current["release_version_id"]
    item_id = detail.items[0].item.current["item_id"]
    observation(storage, versions=((1, 1, "v1", release_id),))
    with storage.transaction() as tx:
        tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 1, "item_id": item_id,
            "risk_reason": "사용자가 확인한 환경안전 미적용", "target_reference": {}, "mapping_status": "VERIFIED"})
    changed = ReleaseRevisionService(storage).save_item_links(release_id, (ItemLinkInput(item_id=item_id,
        item_state=ItemState.NOT_APPLIED, target_scope=TargetScope.LISTED, target_group_ids=(1,)),))
    return release_id, item_id, changed.refresh.dates[0].risks.equipment[0].status


def test_missing_link_choice_blocks_entire_replacement(revision_storage):
    """검토·파일명 확인만으로 수동 연결값을 자동 덮어쓰지 않는지 확인한다.

    param
    - revision_storage: 원본·위험·연결의 전체 취소를 검증할 임시 업무 DB.
    """
    storage = revision_storage
    _, _, status = edited_safety_link(storage)
    assert status is RiskStatus.RISK
    service, incoming = FamilyReplacementService(storage), request()
    preview = service.preview(incoming)
    assert not preview.questions and not preview.conflicts
    assert len(preview.link_conflicts) == 1
    conflict = preview.link_conflicts[0]
    assert (conflict.release_key, conflict.item_key) == ("release", "item")
    assert (conflict.current.item_state, conflict.current.target_scope, conflict.current.target_group_ids) == (
        ItemState.NOT_APPLIED, TargetScope.LISTED, (1,))
    assert conflict.incoming == incoming.releases[0].links[0]
    before = snapshot(storage)
    with pytest.raises(ReplacementReviewRequired) as caught:
        service.replace(incoming, ReplacementReview(fingerprint=preview.fingerprint))
    assert caught.value.preview.link_conflicts == preview.link_conflicts
    assert snapshot(storage) == before


@pytest.mark.parametrize("choice,state,scope,groups,status", [
    ("CURRENT", "NOT_APPLIED", "LISTED", [1], RiskStatus.RISK),
    ("INCOMING", "APPLIED", "ALL", None, RiskStatus.NO_RISK),
])
def test_explicit_link_choice_preserves_or_replaces_safety_state(revision_storage, choice, state, scope, groups, status):
    """명시 선택한 연결값으로 Item 적용과 위험을 함께 다시 판정하는지 확인한다.

    param
    - revision_storage: 독립적인 테스트 DB.
    - choice, state, scope, groups, status: 사용자 선택과 저장·판정 기대값.
    """
    storage = revision_storage
    release_id, item_id, _ = edited_safety_link(storage)
    service, incoming = FamilyReplacementService(storage), request()
    review = reviewed(service, incoming, link_choices={("release", "item"): choice})
    assert not service.preview(incoming, review).link_conflicts
    _, result = service.replace(incoming, review)
    assert all_rows(storage, "release_version_item") == [{"release_version_id": release_id, "item_id": item_id,
        "item_state": state, "target_scope": scope, "target_group_ids": groups}]
    assert result.dates[0].risks.equipment[0].status is status


def test_model_scope_and_ids_are_selected_as_one_bundle(revision_storage):
    """LISTED 범위와 모델 ID가 서로 다른 선택으로 섞이지 않는지 검증한다.

    param
    - revision_storage: 모델 1과 2를 구분하는 임시 업무 DB.
    """
    storage = revision_storage
    edited_safety_link(storage)
    incoming = request(target_scope=TargetScope.LISTED, target_group_ids=(2,))
    service = FamilyReplacementService(storage)
    service.replace(incoming, reviewed(service, incoming, link_choices={("release", "item"): "CURRENT"}))
    assert all_rows(storage, "release_version_item")[0]["target_group_ids"] == [1]
    service.replace(incoming, reviewed(service, incoming, link_choices={("release", "item"): "INCOMING"}))
    link = all_rows(storage, "release_version_item")[0]
    assert link["target_scope"] == "LISTED" and link["target_group_ids"] == [2]


def test_same_item_in_two_releases_has_independent_collision_free_choices(revision_storage):
    """한 Item을 공유해도 새 릴리즈 키·Item 키 쌍별로 독립 선택하는지 확인한다.

    param
    - revision_storage: 여러 릴리즈 연결을 저장할 임시 업무 DB.
    """
    storage = revision_storage
    first = original(storage)
    item_id = first.items[0].item.current["item_id"]
    second = ReleaseRevisionService(storage).register_release(ReleaseInput(family_id=1, version_raw="v2", version_order=2)).value
    second_id = second.release.current["release_version_id"]
    service = ReleaseRevisionService(storage)
    service.save_item_links(first.release.current["release_version_id"], (ItemLinkInput(item_id=item_id,
        item_state=ItemState.NOT_APPLIED, target_scope=TargetScope.LISTED, target_group_ids=(1,)),))
    service.save_item_links(second_id, (ItemLinkInput(item_id=item_id, item_state=ItemState.UNKNOWN,
        target_scope=TargetScope.LISTED, target_group_ids=(2,)),))
    incoming = request()
    item = replace(incoming.items[0], key="shared:Item/키")
    first_key, second_key = "release:one/키", "release:two/키"
    link = ReplacementLink(item_key=item.key, target_scope=TargetScope.ALL)
    incoming = replace(incoming, items=(item,), releases=(
        replace(incoming.releases[0], key=first_key, links=(link,)),
        ReplacementRelease(key=second_key, value=ReleaseInput(family_id=1, version_raw="v2", version_order=2,
            source_reference=source("version_raw", "A2")), links=(link,))),
        raw_data={"노트": {**incoming.raw_data["노트"], "A2": "v2"}})
    replacement = FamilyReplacementService(storage)
    preview = replacement.preview(incoming)
    assert {(c.release_key, c.item_key) for c in preview.link_conflicts} == {(first_key, item.key), (second_key, item.key)}
    replacement.replace(incoming, reviewed(replacement, incoming, link_choices={
        (first_key, item.key): "CURRENT", (second_key, item.key): "INCOMING"}))
    states = {r["release_version_id"]: r for r in all_rows(storage, "release_version_item")}
    assert states[first.release.current["release_version_id"]]["item_state"] == "NOT_APPLIED"
    assert states[second_id]["item_state"] == "APPLIED"


@pytest.mark.parametrize("choices", [
    {("release", "item"): []}, {"release:item": "CURRENT"}, {("release", "missing"): "CURRENT"},
])
def test_malformed_or_unmatched_link_choice_is_rejected(revision_storage, choices):
    """잘못된 선택 값·키·기존 연결 없는 대상의 선택을 저장 전에 차단한다.

    param
    - revision_storage: 독립적인 테스트 DB.
    - choices: 형식 또는 대상이 잘못된 명시 선택.
    """
    edited_safety_link(revision_storage)
    service, incoming = FamilyReplacementService(revision_storage), request()
    before = snapshot(revision_storage)
    with pytest.raises(StorageValidationError):
        service.replace(incoming, reviewed(service, incoming, link_choices=choices))
    assert snapshot(revision_storage) == before


def test_stale_link_review_does_not_overwrite_later_manual_edit(revision_storage):
    """사용자가 검토한 뒤 연결 상태가 바뀌면 이전 선택을 적용하지 않는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 DB.
    """
    storage = revision_storage
    release_id, item_id, _ = edited_safety_link(storage)
    service, incoming = FamilyReplacementService(storage), request()
    review = reviewed(service, incoming, link_choices={("release", "item"): "INCOMING"})
    ReleaseRevisionService(storage).save_item_links(release_id, (ItemLinkInput(item_id=item_id,
        item_state=ItemState.UNKNOWN, target_scope=TargetScope.LISTED, target_group_ids=(2,)),))
    before = snapshot(storage)
    with pytest.raises(StorageValidationError, match="검토 후 자료가 바뀌었습니다"):
        service.replace(incoming, review)
    assert snapshot(storage) == before


def test_transaction_hook_does_not_commit_partial_replacement(revision_storage):
    """재판정을 묶음 끝에 수행하는 호출자도 후속 실패 시 전체를 취소할 수 있는지 확인한다.

    param
    - revision_storage: 실제 트랜잭션을 사용하는 임시 업무 DB.
    """
    storage = revision_storage
    edited_safety_link(storage)
    service, incoming = FamilyReplacementService(storage), request()
    review = reviewed(service, incoming, link_choices={("release", "item"): "INCOMING"})
    before = snapshot(storage)
    with pytest.raises(RuntimeError, match="후속 시트 실패"):
        with storage.transaction() as tx:
            result, refreshed = service.replace_in(tx, incoming, review, refresh=False)
            assert result and refreshed is None
            raise RuntimeError("후속 시트 실패")
    assert snapshot(storage) == before
