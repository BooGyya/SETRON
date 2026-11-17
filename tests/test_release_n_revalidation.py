"""BE-010 변경 후 N 적용 근거 재검증과 모델별 선택 보존을 검증한다."""

from datetime import date

import pytest

from setron.features.release.refresh import ReleaseRefreshService
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.revision import ReleaseRevisionService
from setron.features.targets.schemas import CandidateConfirmation
from setron.features.targets.service import TargetService

from test_release_revision import all_rows, observation, refresh, release, revision_storage


@pytest.mark.parametrize("changes", [
    {"version_raw": "new_branch"}, {"version_suffix": "_DEX"},
    {"version_order": 9}, {"released_at": date(2026, 11, 1)},
])
def test_changed_release_does_not_reuse_previous_explicit_applicability(revision_storage, changes):
    """같은 ID라도 관리값이 바뀌면 이전 모델 적용 목록으로 N을 유효하다고 단정하지 않는다.

    param
    - revision_storage: 독립적인 SQLite 업무 DB.
    - changes: 적용 확인 뒤 달라진 릴리즈 관리값.
    """
    storage = revision_storage
    selected = release(storage)
    observation(storage, versions=((1, 1, "v1", selected),))
    original = RefreshContext(targets={(1, 1): selected, (2, 1): selected},
        applicability={(1, 1): (selected,), (2, 1): (selected,)})
    result = ReleaseRevisionService(storage, original).update_release(selected, changes)
    assert [target.selected_release_id for target in result.refresh.targets] == [selected, selected]
    assert all(not target.valid and "재선택" in target.reason for target in result.refresh.targets)
    assert result.refresh.dates[0].summary.undetermined == 1
    assert original.applicability == {(1, 1): (selected,), (2, 1): (selected,)}


def test_editing_one_target_preserves_other_model_target_and_evidence(revision_storage):
    """한 릴리즈의 변경이 다른 모델이 고른 변경 없는 N까지 바꾸지 않는지 확인한다.

    param
    - revision_storage: 독립적인 업무 DB.
    """
    storage = revision_storage
    low, high = release(storage), release(storage, raw="v2", order=2)
    observation(storage, code="BASE", versions=((1, 1, "v1", low),))
    observation(storage, code="PLUS", versions=((1, 2, "v1", low),))
    context = RefreshContext(targets={(1, 1): high, (2, 1): low},
        applicability={(1, 1): (low, high), (2, 1): (low,)})
    result = ReleaseRevisionService(storage, context).update_release(high, {"version_suffix": "_DEX"})
    targets = {(target.model_id, target.family_id): target for target in result.refresh.targets}
    assert targets[1, 1].selected_release_id == high and not targets[1, 1].valid
    assert targets[2, 1].selected_release_id == low and targets[2, 1].valid
    assert context.targets == {(1, 1): high, (2, 1): low}


def test_noop_release_edit_keeps_explicit_current_evidence(revision_storage):
    """실제 값이 달라지지 않은 저장은 현재 확인 근거를 불필요하게 무효화하지 않는다.

    param
    - revision_storage: 독립적인 업무 DB.
    """
    storage = revision_storage
    selected = release(storage)
    context = RefreshContext(targets={(1, 1): selected}, applicability={(1, 1): (selected,)})
    result = ReleaseRevisionService(storage, context).update_release(selected, {"version_raw": "v1"})
    assert result.refresh.targets[0].valid
    assert result.refresh.targets[0].selected_release_id == selected


def test_observed_model_without_request_target_returns_reselection_notice(revision_storage):
    """선택 목록에서 빠진 관측 모델도 임의의 최신 N 대신 미지정 안내를 반환한다.

    param
    - revision_storage: 독립적인 업무 DB.
    """
    storage = revision_storage
    selected = release(storage)
    observation(storage, code="BASE", versions=((1, 1, "v1", selected),))
    observation(storage, code="PLUS", versions=((1, 2, "v1", selected),))
    context = RefreshContext(targets={(1, 1): selected}, applicability={(1, 1): (selected,)})
    result = refresh(storage, context=context)
    targets = {(target.model_id, target.family_id): target for target in result.targets}
    assert targets[1, 1].valid
    assert targets[2, 1].selected_release_id is None and not targets[2, 1].valid
    assert "선택" in targets[2, 1].reason
    assert context.targets == {(1, 1): selected}


def confirmation(release_id):
    """새 자료로 확인한 모델 적용·순서 근거를 만든다.

    param
    - release_id: 실제 확인한 릴리즈 ID.

    return
    - 합성 검증 자료임을 명시한 CandidateConfirmation.
    """
    return CandidateConfirmation(release_id, "검증 모델의 적용 표식", "검증 노트의 순서", source_text="합성 원본 검증")


def test_saved_evidence_is_rechecked_after_mutation_for_every_selected_model(revision_storage):
    """저장 근거를 쓰는 경로도 오래된 화면 목록을 신뢰하지 않고 변경 뒤 근거를 읽는다.

    param
    - revision_storage: 독립적인 업무 DB.
    """
    storage = revision_storage
    selected = release(storage)
    target_service = TargetService(storage)
    for model in (1, 2):
        target_service.confirm(model, 1, (confirmation(selected),))
    context = RefreshContext(targets={(1, 1): selected, (2, 1): selected},
        applicability={(1, 1): (selected,), (2, 1): (selected,)}, use_saved_applicability=True)
    result = ReleaseRevisionService(storage, context).update_release(selected, {"version_suffix": "_DEX"})
    assert len(result.refresh.targets) == 2
    assert all(not target.valid and target.selected_release_id == selected for target in result.refresh.targets)


def test_same_transaction_fresh_confirmation_can_validate_changed_release(revision_storage):
    """변경 뒤 원본으로 새로 확인한 근거만 같은 저장 묶음의 유효한 N에 사용할 수 있다.

    param
    - revision_storage: 독립적인 업무 DB.
    """
    storage = revision_storage
    selected = release(storage)
    context = RefreshContext(targets={(1, 1): selected}, use_saved_applicability=True)
    with storage.transaction() as tx:
        tx.update("release_version", {"release_version_id": selected}, {"version_suffix": "_DEX"})
        TargetService(storage).confirm_in(tx, 1, 1, (confirmation(selected),))
        result = ReleaseRefreshService().refresh_in(tx, context=context,
            invalidated_applicability_release_ids=(selected,))
    assert result.targets[0].valid and result.targets[0].selected_release_id == selected


def test_reimport_source_change_invalidates_explicit_context_with_same_release_id(revision_storage):
    """동일한 버전 원문도 원본을 바꾸면 이전 적용 근거를 재사용하지 않는다.

    param
    - revision_storage: 독립적인 업무 DB.
    """
    from test_release_replacement import original, request, reviewed
    from setron.features.release.replacement import FamilyReplacementService

    storage = revision_storage
    before = original(storage)
    selected = before.release.current["release_version_id"]
    context = RefreshContext(targets={(1, 1): selected}, applicability={(1, 1): (selected,)})
    service = FamilyReplacementService(storage, context)
    value = request()
    details, result = service.replace(value, reviewed(service, value))
    assert details[0].release.current["release_version_id"] == selected
    assert not result.targets[0].valid and "재선택" in result.targets[0].reason
    assert result.targets[0].selected_release_id == selected
    assert all_rows(storage, "release_version")[0]["batch_id"] != before.release.current["batch_id"]


def test_management_edit_filters_stale_explicit_targets_but_keeps_other_model(revision_storage):
    """실제 관리 수정 경로도 변경 전 목록을 재사용하지 않고 다른 모델의 N은 보존한다.

    param
    - revision_storage: 독립적인 업무 DB.
    """
    from setron.features.management import ManagementService, RecordEdit

    storage = revision_storage
    low, high = release(storage), release(storage, raw="v2", order=2)
    context = RefreshContext(targets={(1, 1): high, (2, 1): low},
        applicability={(1, 1): (low, high), (2, 1): (low,)})
    service = ManagementService(storage, context)
    view = service.get_record("release_version", {"release_version_id": high})
    result = service.save((RecordEdit("release_version", view.key, view.revision, {"version_suffix": "_DEX"}),))
    targets = {(target.model_id, target.family_id): target for target in result.refresh.targets}
    assert not targets[1, 1].valid and targets[1, 1].selected_release_id == high
    assert targets[2, 1].valid and targets[2, 1].selected_release_id == low


def test_management_same_batch_fresh_proof_restores_only_confirmed_model(revision_storage):
    """같은 릴리즈를 쓰는 두 모델 중 새 적용 근거를 확인한 모델만 다시 유효하게 한다.

    param
    - revision_storage: 독립적인 업무 DB.
    """
    from setron.features.management import ApplicabilityEdit, ManagementService, RecordEdit

    storage = revision_storage
    selected = release(storage)
    context = RefreshContext(targets={(1, 1): selected, (2, 1): selected},
        applicability={(1, 1): (selected,), (2, 1): (selected,)})
    service = ManagementService(storage, context)
    release_view = service.get_record("release_version", {"release_version_id": selected})
    proof_view = service.get_applicability(1, 1)
    result = service.save((RecordEdit("release_version", release_view.key, release_view.revision,
        {"version_suffix": "_DEX"}),), applicability=(ApplicabilityEdit(1, 1, proof_view.revision,
            (confirmation(selected),)),))
    targets = {(target.model_id, target.family_id): target for target in result.refresh.targets}
    assert targets[1, 1].valid and targets[1, 1].selected_release_id == selected
    assert not targets[2, 1].valid and targets[2, 1].selected_release_id == selected
