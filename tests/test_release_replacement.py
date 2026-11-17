"""BE-010의 계열 전체 교체·검토 선택·위험 보존·원자성을 검증한다."""

from dataclasses import replace

import pytest

from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.replacement import FamilyReplacementService
from setron.features.release.replacement_schemas import (
    ReplacementItem, ReplacementLink, ReplacementRelease, ReplacementRequest,
    ReplacementReview, ReplacementReviewRequired,
)
from setron.features.release.schemas import ItemInput, ItemLinkInput, ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.storage.validation import StorageValidationError

from test_release_revision import all_rows, observation, release, revision_storage


def source(field, cell):
    """테스트 노트의 한 셀 출처를 구성한다.

    param
    - field, cell: 관리 항목과 원본 주소.

    return
    - 원본 위치 객체.
    """
    return {field: {"sheet": "노트", "cells": [cell]}}


def original(storage, *, raw="v1", number=7, content="원본 내용", family=1, order=1):
    """원본 셀과 관리값이 분리된 기존 Excel 릴리즈·Item을 등록한다.

    param
    - storage: 독립적인 테스트 저장소.
    - raw, number, content, family, order: 기존 노트의 버전·번호·내용·계열·확인 순서.

    return
    - 등록한 릴리즈 상세.
    """
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": {
            "노트": {"A1": raw, "B1": order, "C1": number, "D1": content}}})
    return ReleaseService(storage).register_release(
        ReleaseInput(family_id=family, version_raw=raw, version_order=order, batch_id=batch["batch_id"],
                     source_reference={**source("version_raw", "A1"), **source("version_order", "B1")}),
        (ItemInput(item_no=number, content=content, batch_id=batch["batch_id"],
                   source_reference={**source("item_no", "C1"), **source("content", "D1")}),))


def request(*, raw="v1", number=7, content="원본 내용", order=1, family=1, target_scope=TargetScope.ALL, target_group_ids=()):
    """전체 검토와 파일명 확인을 마친 한 계열 요청을 만든다.

    param
    - raw, number, content, order, family: 새 노트의 버전·번호·내용·확인 순서·계열.
    - target_scope, target_group_ids: 검토한 Item 모델 범위.

    return
    - 새 원본과 명시적인 Item 포함 관계를 가진 ReplacementRequest.
    """
    return ReplacementRequest(
        family_id=family, releases=(ReplacementRelease(key="release", value=ReleaseInput(
            family_id=family, version_raw=raw, version_order=order,
            source_reference={**source("version_raw", "A1"), **source("version_order", "B1")}),
            links=(ReplacementLink(item_key="item", item_state=ItemState.APPLIED,
                                   target_scope=target_scope, target_group_ids=target_group_ids),)),),
        items=(ReplacementItem(key="item", value=ItemInput(item_no=number, content=content,
            source_reference={**source("item_no", "C1"), **source("content", "D1")})),),
        raw_data={"노트": {"A1": raw, "B1": order, "C1": number, "D1": content}},
        complete_note_confirmed=True, filename_confirmed=True)


def reviewed(service, value, **kwargs):
    """현재 지문을 읽어 명시적인 검토 선택을 만든다.

    param
    - service, value: 교체 서비스와 새 계열 요청.
    - kwargs: 사용자가 고른 매칭·충돌 선택.

    return
    - 현재 DB·요청에만 유효한 ReplacementReview.
    """
    initial = service.preview(value)
    review = ReplacementReview(fingerprint=initial.fingerprint, **kwargs)
    # 매칭 선택으로 새로 확인된 연결까지 표시한 뒤 명시적으로 새 값을 선택한다.
    if "link_choices" not in kwargs:
        preview = service.preview(value, review)
        review = replace(review, link_choices={(c.release_key, c.item_key): "INCOMING" for c in preview.link_conflicts})
    return review


def snapshot(storage):
    """교체 실패 시 되돌아가야 하는 관리 행을 전부 읽는다.

    param
    - storage: 독립적인 테스트 저장소.

    return
    - 전체 관리 테이블의 저장값.
    """
    from setron.features.storage.validation import TABLE_NAMES
    return {name: all_rows(storage, name) for name in TABLE_NAMES}


def test_whole_family_replacement_removes_absent_items_and_preserves_manual_risks(revision_storage):
    """빠진 버전·Item을 제외하고 수동 위험·다른 계열·관측 원문을 보존하는지 검증한다.

    param
    - revision_storage: 외래키를 적용한 독립적인 테스트 저장소.
    """
    storage = revision_storage
    kept = original(storage)
    removed = original(storage, raw="v2", number=8, content="빠질 내용", order=2)
    kept_id = kept.release.current["release_version_id"]
    removed_id = removed.release.current["release_version_id"]
    removed_item = removed.items[0].item.current["item_id"]
    other = release(storage, family=2, raw="c1")
    observation(storage, versions=((1, 1, "v2", removed_id),))
    preserved_observation = all_rows(storage, "equipment_snapshot")
    preserved_batches = all_rows(storage, "import_batch")
    with storage.transaction() as tx:
        direct = tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1,
            "release_version_id": removed_id, "risk_reason": "수동 버전 위험", "is_enabled": False,
            "target_reference": {"수동": "근거"}, "mapping_status": "VERIFIED"})
        safety = tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 1,
            "item_id": removed_item, "risk_reason": "수동 Item 위험", "target_reference": {}, "mapping_status": "VERIFIED"})
    context = RefreshContext(targets={(1, 1): kept_id, (2, 1): removed_id},
                             applicability={(1, 1): (kept_id,), (2, 1): (removed_id,)})
    service = FamilyReplacementService(storage, context)
    value = request()
    preview = service.preview(value)
    assert not preview.questions and not preview.conflicts
    details, result = service.replace(value, reviewed(service, value))
    assert len(details) == 1 and details[0].release.current["release_version_id"] == kept_id
    assert details[0].items[0].item.current["item_id"] == kept.items[0].item.current["item_id"]
    assert {row["release_version_id"] for row in all_rows(storage, "release_version")} == {kept_id, other}
    assert removed_item not in {row["item_id"] for row in all_rows(storage, "item")}
    rules = {row["risk_rule_id"]: row for row in all_rows(storage, "risk_rule")}
    assert rules[direct["risk_rule_id"]]["risk_reason"] == "수동 버전 위험"
    assert rules[direct["risk_rule_id"]]["target_reference"]["수동"] == "근거"
    assert rules[direct["risk_rule_id"]]["release_version_id"] is None
    assert rules[direct["risk_rule_id"]]["is_enabled"] == 0
    assert rules[safety["risk_rule_id"]]["item_id"] is None
    assert all(row["mapping_status"] == "UNRESOLVED" for row in rules.values())
    assert set(result.unresolved_risk_ids) == set(rules)
    assert [valid.valid for valid in result.targets] == [False, False]
    assert all_rows(storage, "equipment_snapshot") == preserved_observation
    assert all_rows(storage, "equipment_version")[0]["version_raw"] == "v2"
    assert all_rows(storage, "equipment_version")[0]["release_version_id"] is None
    assert all_rows(storage, "import_batch")[:len(preserved_batches)] == preserved_batches


@pytest.mark.parametrize("choice, expected_number, expected_content", [
    ("CURRENT", 70, "앱 수정 내용"), ("INCOMING", 7, "새 원본 내용"),
])
def test_original_item_number_matches_after_edit_and_choices_use_new_source(revision_storage, choice, expected_number, expected_content):
    """수정된 번호 대신 보관 원본으로 같은 Item을 찾아 사용자가 고른 값과 새 출처를 저장하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    - choice, expected_number, expected_content: 사용자 충돌 선택과 기대하는 관리값.
    """
    storage = revision_storage
    existing = original(storage)
    item_id = existing.items[0].item.current["item_id"]
    old_batch = existing.items[0].item.current["batch_id"]
    service = ReleaseService(storage)
    service.update_item(item_id, {"item_no": 70, "content": "앱 수정 내용"})
    replacement = FamilyReplacementService(storage)
    value = request(content="새 원본 내용")
    preview = replacement.preview(value)
    assert preview.item_matches == {"item": item_id} and not preview.questions
    assert {(conflict.kind, conflict.key, conflict.field) for conflict in preview.conflicts} == {
        ("item", "item", "item_no"), ("item", "item", "content")}
    before = snapshot(storage)
    with pytest.raises(ReplacementReviewRequired):
        replacement.replace(value, reviewed(replacement, value))
    assert snapshot(storage) == before
    choices = {("item", "item", field): choice for field in ("item_no", "content")}
    details, _ = replacement.replace(value, reviewed(replacement, value, choices=choices))
    item = details[0].items[0].item
    assert item.current["item_id"] == item_id
    assert item.current["item_no"] == expected_number and item.current["content"] == expected_content
    assert item.current["batch_id"] != old_batch
    assert {cell.field: cell.value for cell in item.original_cells} == {"item_no": 7, "content": "새 원본 내용"}
    assert next(row for row in all_rows(storage, "import_batch") if row["batch_id"] == old_batch) == before["import_batch"][0]


def test_missing_or_rearranged_number_requires_explicit_identity_choice(revision_storage):
    """번호가 바뀐 항목을 자동으로 동일 Item 또는 신규로 확정하지 않는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    existing = original(storage)
    item_id = existing.items[0].item.current["item_id"]
    replacement = FamilyReplacementService(storage)
    value = request(number=77)
    preview = replacement.preview(value)
    assert len(preview.questions) == 1 and preview.questions[0].candidates == (item_id,)
    with pytest.raises(ReplacementReviewRequired):
        replacement.replace(value, reviewed(replacement, value))
    reviewed_match = reviewed(replacement, value, item_matches={"item": item_id})
    after_match = replacement.preview(value, reviewed_match)
    assert not after_match.questions and after_match.conflicts[0].field == "item_no"
    details, _ = replacement.replace(value, replace(reviewed_match, choices={("item", "item", "item_no"): "INCOMING"}))
    assert details[0].items[0].item.current["item_id"] == item_id
    assert details[0].items[0].item.current["item_no"] == 77


@pytest.mark.parametrize("field", ["complete_note_confirmed", "filename_confirmed"])
def test_replacement_requires_whole_note_and_filename_confirmations(revision_storage, field):
    """전체 누적 노트나 파일명 확인이 빠지면 어떤 관리값도 쓰지 않는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    - field: 확인하지 않은 필드 이름.
    """
    storage = revision_storage
    original(storage)
    service = FamilyReplacementService(storage)
    value = replace(request(), **{field: False})
    before = snapshot(storage)
    with pytest.raises(StorageValidationError):
        service.replace(value, reviewed(service, value))
    assert snapshot(storage) == before


def test_stale_review_refuses_changed_database_and_changed_request(revision_storage):
    """검토 후 기존값이나 새 원본이 바뀌면 이전 선택으로 저장하지 않는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    existing = original(storage)
    service = FamilyReplacementService(storage)
    value = request()
    review = reviewed(service, value)
    ReleaseService(storage).update_item(existing.items[0].item.current["item_id"], {"content": "검토 후 수정"})
    before = snapshot(storage)
    with pytest.raises(StorageValidationError, match="바뀌"):
        service.replace(value, review)
    assert snapshot(storage) == before
    fresh = reviewed(service, value)
    altered = request(content="검토 후 새 원본 수정")
    with pytest.raises(StorageValidationError, match="바뀌"):
        service.replace(altered, fresh)
    assert snapshot(storage) == before


def test_late_model_failure_rolls_back_entire_family_replacement(revision_storage):
    """이전 연결 삭제와 Item 갱신 뒤 모델 연결이 실패하면 원본·위험까지 모두 취소되는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    existing = original(storage)
    with storage.transaction() as tx:
        tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1,
            "release_version_id": existing.release.current["release_version_id"], "risk_reason": "수동 근거",
            "target_reference": {}, "mapping_status": "VERIFIED"})
    service = FamilyReplacementService(storage)
    value = request(target_scope=TargetScope.LISTED, target_group_ids=(999,))
    before = snapshot(storage)
    with pytest.raises(LookupError):
        service.replace(value, reviewed(service, value))
    assert snapshot(storage) == before


def test_shared_item_clones_only_replaced_family_and_reconnects_its_manual_risk(revision_storage):
    """다른 계열과 공유한 Item은 유지하고 교체한 계열의 확인된 새 항목에만 위험을 연결하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    existing = original(storage)
    item_id = existing.items[0].item.current["item_id"]
    with storage.transaction() as tx:
        tx.insert("version_family", {"family_id": 3, "group_id": 1, "family_name": "O2 분기"})
    outside_release = release(storage, family=3, raw="branch", order=1)
    ReleaseService(storage).save_item_links(outside_release, (
        ItemLinkInput(item_id=item_id, item_state=ItemState.APPLIED, target_scope=TargetScope.ALL),))
    with storage.transaction() as tx:
        selected_risk = tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 1, "item_id": item_id,
            "risk_reason": "교체 계열 수동 위험", "target_reference": {}, "mapping_status": "VERIFIED"})
        outside_risk = tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 3, "item_id": item_id,
            "risk_reason": "다른 계열 수동 위험", "target_reference": {}, "mapping_status": "VERIFIED"})
    service = FamilyReplacementService(storage)
    value = request(content="새 계열 내용")
    details, _ = service.replace(value, reviewed(service, value,
        choices={("item", "item", "content"): "INCOMING"}))
    replacement_item = details[0].items[0].item.current["item_id"]
    assert replacement_item != item_id
    outside_detail = ReleaseService(storage).get_release(outside_release)
    assert outside_detail.items[0].item.current["content"] == "원본 내용"
    assert outside_detail.items[0].item.current["item_id"] == item_id
    rules = {row["risk_rule_id"]: row for row in all_rows(storage, "risk_rule")}
    assert rules[selected_risk["risk_rule_id"]]["item_id"] == replacement_item
    assert rules[selected_risk["risk_rule_id"]]["mapping_status"] == "VERIFIED"
    assert rules[outside_risk["risk_rule_id"]] == outside_risk


def note(*rows):
    """여러 버전의 원본 셀과 Item 연결을 가진 전체 노트를 만든다.

    param
    - rows: (버전 원문, Item 번호, 내용, 확인 순서) 튜플 목록.

    return
    - 계열 1의 전체 교체 요청.
    """
    releases, items, cells = [], [], {}
    for number, (raw, item_no, content, order) in enumerate(rows, 1):
        release_key, item_key = f"release{number}", f"item{number}"
        cells.update({f"A{number}": raw, f"B{number}": order, f"C{number}": item_no, f"D{number}": content})
        releases.append(ReplacementRelease(key=release_key, value=ReleaseInput(
            family_id=1, version_raw=raw, version_order=order,
            source_reference={**source("version_raw", f"A{number}"), **source("version_order", f"B{number}")}),
            links=(ReplacementLink(item_key=item_key, target_scope=TargetScope.ALL),)))
        items.append(ReplacementItem(key=item_key, value=ItemInput(item_no=item_no, content=content,
            source_reference={**source("item_no", f"C{number}"), **source("content", f"D{number}")})))
    return ReplacementRequest(family_id=1, releases=tuple(releases), items=tuple(items),
        raw_data={"노트": cells}, complete_note_confirmed=True, filename_confirmed=True)


def test_removed_item_restores_risk_only_after_explicit_unique_membership_confirmation(revision_storage):
    """삭제했던 Item이 다시 나타나도 자동 연결하지 않고 원본 버전·번호를 확인한 선택만 복원하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    existing = original(storage)
    old_item = existing.items[0].item.current["item_id"]
    with storage.transaction() as tx:
        risk = tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 1, "item_id": old_item,
            "risk_reason": "직접 확인한 환경안전 위험", "target_reference": {"사용자 메모": "보존"}, "mapping_status": "VERIFIED"})
    service = FamilyReplacementService(storage)
    empty = note()
    empty_risk = service.preview(empty).risk_reconnections[0]
    assert not empty_risk.can_restore and empty_risk.reason
    service.replace(empty, reviewed(service, empty))
    unresolved = all_rows(storage, "risk_rule")[0]
    assert unresolved["item_id"] is None and unresolved["mapping_status"] == "UNRESOLVED"
    assert unresolved["target_reference"]["item"]["memberships"] == [
        {"family_id": 1, "version_raw": "v1", "item_no": 7}]
    restored = request()
    proposal = service.preview(restored).risk_reconnections[0]
    assert proposal.risk_rule_id == risk["risk_rule_id"]
    assert proposal.can_restore and proposal.requires_confirmation
    assert proposal.proposed_targets["item_id"] == "item"
    assert proposal.target_reference["사용자 메모"] == "보존"
    details, result = service.replace(restored, reviewed(service, restored))
    assert all_rows(storage, "risk_rule")[0]["item_id"] is None
    assert risk["risk_rule_id"] in result.unresolved_risk_ids
    details, result = service.replace(restored, reviewed(service, restored, risk_confirmations=(risk["risk_rule_id"],)))
    rule = all_rows(storage, "risk_rule")[0]
    assert rule["item_id"] == details[0].items[0].item.current["item_id"]
    assert rule["mapping_status"] == "VERIFIED" and rule["target_reference"]["사용자 메모"] == "보존"
    assert risk["risk_rule_id"] not in result.unresolved_risk_ids


def test_removed_defect_end_remains_unknown_until_exact_boundary_restored_and_confirmed(revision_storage):
    """사라진 해제 버전을 미해제로 바꾸지 않고 같은 계열의 정확한 경계를 확인한 뒤만 복원하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    first = original(storage)
    second = original(storage, raw="v2", number=8, content="해제 버전 내용", order=2)
    first_id, second_id = first.release.current["release_version_id"], second.release.current["release_version_id"]
    with storage.transaction() as tx:
        risk = tx.insert("risk_rule", {"rule_type": "DEFECT_ITEM", "family_id": 1,
            "item_id": first.items[0].item.current["item_id"], "start_release_version_id": first_id,
            "end_release_version_id": second_id, "risk_reason": "해제 경계가 있는 결함", "target_reference": {},
            "mapping_status": "VERIFIED"})
    service = FamilyReplacementService(storage)
    missing_end = request()
    proposal = service.preview(missing_end).risk_reconnections[0]
    assert not proposal.can_restore and proposal.proposed_targets["end_release_version_id"] is None
    service.replace(missing_end, reviewed(service, missing_end))
    risk_id = risk["risk_rule_id"]
    rule = all_rows(storage, "risk_rule")[0]
    assert rule["mapping_status"] == "UNRESOLVED" and rule["end_release_version_id"] is None
    # 사용자 확인만 있어도 사라진 경계는 복원할 수 없다.
    with pytest.raises(StorageValidationError):
        service.replace(missing_end, reviewed(service, missing_end, risk_confirmations=(risk_id,)))
    assert all_rows(storage, "risk_rule")[0]["mapping_status"] == "UNRESOLVED"
    complete = note(("v1", 7, "원본 내용", 1), ("v2", 8, "해제 버전 내용", 2))
    proposal = service.preview(complete).risk_reconnections[0]
    assert proposal.can_restore and proposal.requires_confirmation
    assert proposal.proposed_targets["start_release_version_id"] == "release1"
    assert proposal.proposed_targets["end_release_version_id"] == "release2"
    assert proposal.proposed_targets["item_id"] == "item1"
    service.replace(complete, reviewed(service, complete))
    assert all_rows(storage, "risk_rule")[0]["end_release_version_id"] is None
    details, _ = service.replace(complete, reviewed(service, complete, risk_confirmations=(risk_id,)))
    rule = all_rows(storage, "risk_rule")[0]
    current_end = next(value.release.current["release_version_id"] for value in details if value.release.current["version_raw"] == "v2")
    assert rule["end_release_version_id"] == current_end and rule["mapping_status"] == "VERIFIED"


def test_risk_recovery_rejects_duplicate_original_item_candidates(revision_storage):
    """같은 원본 버전·번호의 Item이 여러 개면 확인 표시만으로 임의 재연결하지 않는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    existing = original(storage)
    with storage.transaction() as tx:
        risk = tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 1,
            "item_id": existing.items[0].item.current["item_id"], "risk_reason": "동일 번호 복원 검토",
            "target_reference": {}, "mapping_status": "VERIFIED"})
    service = FamilyReplacementService(storage)
    empty = note()
    service.replace(empty, reviewed(service, empty))
    value = request()
    duplicate = ReplacementItem(key="duplicate", value=ItemInput(item_no=7, content="같은 번호의 별개 내용",
        source_reference={**source("item_no", "C2"), **source("content", "D2")}))
    value = replace(value,
        releases=(replace(value.releases[0], links=(*value.releases[0].links,
            ReplacementLink(item_key="duplicate", target_scope=TargetScope.ALL))),),
        items=(*value.items, duplicate), raw_data={"노트": {**value.raw_data["노트"], "C2": 7, "D2": "같은 번호의 별개 내용"}})
    proposal = service.preview(value).risk_reconnections[0]
    assert not proposal.can_restore and proposal.proposed_targets["item_id"] is None
    before = snapshot(storage)
    with pytest.raises(StorageValidationError):
        service.replace(value, reviewed(service, value, risk_confirmations=(risk["risk_rule_id"],)))
    assert snapshot(storage) == before


@pytest.mark.parametrize("start_order, end_order", [(None, 2), (1, None), (1, 1), (2, 1)])
def test_invalid_defect_endpoint_order_is_reviewable_and_persisted_unresolved(revision_storage, start_order, end_order):
    """시작·해제 순서의 누락·중복·역전을 저장 전에 알리고 위험 연결을 미확인으로 보존하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    - start_order, end_order: 새 노트의 시작·해제 버전 순서.
    """
    storage = revision_storage
    first = original(storage)
    second = original(storage, raw="v2", number=8, content="해제 버전 내용", order=2)
    with storage.transaction() as tx:
        risk = tx.insert("risk_rule", {"rule_type": "DEFECT_ITEM", "family_id": 1,
            "item_id": first.items[0].item.current["item_id"],
            "start_release_version_id": first.release.current["release_version_id"],
            "end_release_version_id": second.release.current["release_version_id"],
            "risk_reason": "확인된 결함 범위", "target_reference": {}, "mapping_status": "VERIFIED"})
    service = FamilyReplacementService(storage)
    value = note(("v1", 7, "원본 내용", start_order), ("v2", 8, "해제 버전 내용", end_order))
    proposal = service.preview(value).risk_reconnections[0]
    assert not proposal.can_restore and proposal.reason
    _, result = service.replace(value, reviewed(service, value))
    assert all_rows(storage, "risk_rule")[0]["mapping_status"] == "UNRESOLVED"
    assert risk["risk_rule_id"] in result.unresolved_risk_ids


def test_direct_version_risk_does_not_require_comparison_order(revision_storage):
    """직접 위험 버전은 동일 대상을 확인했으면 N 비교 순서가 없어도 연결을 유지하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    existing = original(storage, order=None)
    with storage.transaction() as tx:
        risk = tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1,
            "release_version_id": existing.release.current["release_version_id"], "risk_reason": "직접 위험 버전",
            "target_reference": {}, "mapping_status": "VERIFIED"})
    service = FamilyReplacementService(storage)
    value = request(order=None)
    proposal = service.preview(value).risk_reconnections[0]
    assert proposal.can_restore and not proposal.requires_confirmation
    _, result = service.replace(value, reviewed(service, value))
    assert all_rows(storage, "risk_rule")[0]["mapping_status"] == "VERIFIED"
    assert risk["risk_rule_id"] not in result.unresolved_risk_ids


@pytest.mark.parametrize("confirmed", [False, True])
def test_manually_edited_release_raw_requires_confirmed_risk_identity_on_incoming_choice(revision_storage, confirmed):
    """수정 버전명을 새 원본으로 되돌릴 때 직접 위험 지정을 묵시적으로 다른 원문에 전파하지 않는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    - confirmed: 위험 지정의 동일 대상 연결을 사용자가 명시 확인했는지 여부.
    """
    storage = revision_storage
    existing = original(storage)
    release_id = existing.release.current["release_version_id"]
    ReleaseService(storage).update_release(release_id, {"version_raw": "v1_직접수정"})
    with storage.transaction() as tx:
        risk = tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1,
            "release_version_id": release_id, "risk_reason": "수정한 버전을 직접 위험 지정", "target_reference": {},
            "mapping_status": "VERIFIED"})
    service = FamilyReplacementService(storage)
    value = request()
    review = reviewed(service, value,
        choices={("release", "release", "version_raw"): "INCOMING"},
        risk_confirmations=(risk["risk_rule_id"],) if confirmed else ())
    proposal = service.preview(value, review).risk_reconnections[0]
    assert proposal.can_restore and proposal.requires_confirmation
    assert proposal.proposed_targets["release_version_id"] == "release"
    details, _ = service.replace(value, review)
    assert details[0].release.current["version_raw"] == "v1"
    rule = all_rows(storage, "risk_rule")[0]
    assert rule["release_version_id"] == (release_id if confirmed else None)
    assert rule["mapping_status"] == ("VERIFIED" if confirmed else "UNRESOLVED")
    assert rule["target_reference"]["direct_version"]["version_raw"] == "v1_직접수정"


@pytest.mark.parametrize("bad", [
    lambda value: replace(value, releases=(object(),)),
    lambda value: replace(value, items=(object(),)),
    lambda value: replace(value, releases=(ReplacementRelease(key="release", value=ItemInput(content="잘못된 종류")),)),
    lambda value: replace(value, items=(ReplacementItem(key="item", value=value.releases[0].value),)),
    lambda value: replace(value, releases=(replace(value.releases[0], links=(object(),)),)),
    lambda value: replace(value, releases=(replace(value.releases[0], links=(ReplacementLink(item_key=[]),)),)),
])
def test_malformed_replacement_entries_raise_validation_error(revision_storage, bad):
    """잘못된 항목 클래스나 연결 키를 처리 가능한 검증 오류로 반환하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    - bad: 잘못된 요청을 만드는 함수.
    """
    storage = revision_storage
    service = FamilyReplacementService(storage)
    before = snapshot(storage)
    with pytest.raises(StorageValidationError):
        service.preview(bad(request()))
    assert snapshot(storage) == before


@pytest.mark.parametrize("choices", [{"wrong": "CURRENT"}, {("item", "item", "content"): []}])
def test_malformed_conflict_choices_raise_validation_error(revision_storage, choices):
    """잘못된 충돌 선택 키·값을 내부 자료형 예외 대신 검증 오류로 반환하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    - choices: 형식이 잘못된 충돌 선택.
    """
    storage = revision_storage
    original(storage)
    service = FamilyReplacementService(storage)
    value = request(content="새 내용")
    before = snapshot(storage)
    with pytest.raises(StorageValidationError):
        service.replace(value, reviewed(service, value, choices=choices))
    assert snapshot(storage) == before
