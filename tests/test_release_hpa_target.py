"""릴리즈 변경 후 분석 제외 모델의 목표 N이 유효하게 반환되지 않는다."""

import pytest

from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.revision import ReleaseRevisionService
from setron.features.release.schemas import ReleaseInput
from setron.features.targets.service import TargetService
from test_release_n_revalidation import confirmation
from test_release_revision import release, revision_storage


@pytest.mark.parametrize("use_saved", [False, True], ids=["explicit-evidence", "saved-evidence"])
def test_hpa_target_is_excluded_after_registration_and_keeps_other_model_n(revision_storage, use_saved):
    """실제 HPA 계열을 등록해도 전달한 N을 보존하며 분석 제외 이유를 반환한다."""
    storage = revision_storage
    normal = release(storage)
    with storage.transaction() as tx:
        tx.insert("version_family", {"family_id": 3, "group_id": 4, "family_name": "HPA", "ui_type": None})
    hpa = release(storage, family=3, raw="hpa-v1")
    TargetService(storage).confirm(1, 1, (confirmation(normal),))
    context = RefreshContext(targets={(1, 1): normal, (4, 3): hpa},
        applicability={(1, 1): (normal,), (4, 3): (hpa,)}, use_saved_applicability=use_saved)
    result = ReleaseRevisionService(storage, context).register_release(
        ReleaseInput(family_id=3, version_raw="hpa-v2", version_order=2))
    targets = {(t.model_id, t.family_id): t for t in result.refresh.targets}
    assert targets[1, 1].valid and targets[1, 1].selected_release_id == normal
    assert not targets[4, 3].valid and targets[4, 3].selected_release_id == hpa
    assert "분석 제외" in targets[4, 3].reason and "재선택" not in targets[4, 3].reason
    assert context.targets == {(1, 1): normal, (4, 3): hpa}


def test_disabled_model_cannot_reuse_explicit_n_evidence(revision_storage):
    """분석 대상에서 제외된 일반 모델도 명시적 적용 목록으로 검사를 우회하지 않는다."""
    storage = revision_storage
    selected = release(storage)
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 2}, {"analysis_enabled": False})
    context = RefreshContext(targets={(1, 1): selected, (2, 1): selected},
        applicability={(1, 1): (selected,), (2, 1): (selected,)})
    result = ReleaseRevisionService(storage, context).register_release(
        ReleaseInput(family_id=1, version_raw="v2", version_order=2))
    targets = {(t.model_id, t.family_id): t for t in result.refresh.targets}
    assert targets[1, 1].valid
    assert not targets[2, 1].valid and targets[2, 1].selected_release_id == selected
    assert "분석 제외" in targets[2, 1].reason


def test_unselected_hpa_target_does_not_request_n_selection(revision_storage):
    """HPA에 N이 없을 때도 목표 선택을 요구하지 않고 분석 제외를 설명한다."""
    storage = revision_storage
    with storage.transaction() as tx:
        tx.insert("version_family", {"family_id": 3, "group_id": 4, "family_name": "HPA", "ui_type": None})
    context = RefreshContext(targets={(4, 3): None})
    result = ReleaseRevisionService(storage, context).register_release(
        ReleaseInput(family_id=3, version_raw="hpa-v1", version_order=1))
    target, = result.refresh.targets
    assert not target.valid and target.selected_release_id is None
    assert "분석 제외" in target.reason and "선택해" not in target.reason
