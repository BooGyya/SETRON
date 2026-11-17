"""BE-011 후보 근거·독립 목표·변경 후 재검증을 실제 SQLite 트랜잭션으로 확인한다."""

from copy import deepcopy
from datetime import date

import pytest

from setron.features.storage.validation import StorageValidationError
from setron.features.targets.schemas import CandidateConfirmation
from setron.features.targets.service import TargetService

from test_release_revision import all_rows, observation, release, revision_storage


def evidence(release_id, *, label="합성 검증 자료의 모델 적용 표식"):
    return CandidateConfirmation(release_id, label, "합성 자료에서 확인한 별도 릴리즈 순서",
                                 source_text="합성 검증 자료. 실제 업무 적용 근거가 아님.",
                                 source_reference={"applicability": {"sheet": "합성릴리즈", "cells": ["C3"]}})


def test_no_implicit_model_applicability_or_stored_target(revision_storage):
    storage = revision_storage
    rid = release(storage)
    observation(storage, versions=((1, 1, "v1", rid),))
    result = TargetService(storage).read(((1, 1), (2, 1)), {(1, 1): rid})
    assert all(not pair.candidates for pair in result.pairs)
    assert result.pairs[0].selected_release_id == rid
    assert not result.pairs[0].selection_valid and "근거" in result.pairs[0].reason
    assert result.applicability == {} and result.effective_targets == {}
    assert all_rows(storage, "app_setting") == []


def test_models_choose_independent_latest_confirmed_order_and_hotfix(revision_storage):
    storage = revision_storage
    old = release(storage, raw="v99", order=2)
    hotfix = release(storage, raw="v1 + Hotfix", order=5)
    unconfirmed = release(storage, raw="v999", order=20)
    service = TargetService(storage)
    service.confirm(1, 1, (evidence(old), evidence(hotfix)))
    service.confirm(2, 1, (evidence(old),))
    initial = service.read(((1, 1), (2, 1)))
    assert initial.targets == {(1, 1): hotfix, (2, 1): old}
    assert unconfirmed not in initial.applicability[(1, 1)]
    selected = {(1, 1): old, (2, 1): None}
    before = deepcopy(selected)
    changed = service.read(((1, 1), (2, 1)), selected)
    assert changed.targets == before == selected
    assert changed.pairs[0].selection_valid and not changed.pairs[1].selection_valid
    narrowed = service.read(((1, 1),), selected)
    assert narrowed.targets == {(1, 1): old} and selected == before
    assert len(all_rows(storage, "app_setting")) == 2
    assert all("selected_release_id" not in row["setting_value"] for row in all_rows(storage, "app_setting"))


def test_omitted_mapping_pair_uses_recommendation_but_explicit_none_is_preserved(revision_storage):
    rid = release(revision_storage)
    service = TargetService(revision_storage)
    service.confirm(1, 1, (evidence(rid),))
    service.confirm(2, 1, (evidence(rid),))
    result = service.read(((1, 1), (2, 1)), {(1, 1): None})
    assert result.targets == {(1, 1): None, (2, 1): rid}
    assert result.effective_targets == {(2, 1): rid}


def test_c3_ui_families_do_not_cross_and_shapes_do_not_create_families(revision_storage):
    storage = revision_storage
    with storage.transaction() as tx:
        tx.insert("version_family", {"family_id": 3, "group_id": 3, "family_name": "C3 통합UI", "ui_type": "INTEGRATED"})
        tx.insert("equipment_group", {"group_id": 5, "group_name": "C3 6각", "category": "POLY", "parent_group_id": 3})
    legacy = release(storage, family=2)
    integrated = release(storage, family=3)
    service = TargetService(storage)
    service.confirm(3, 2, (evidence(legacy),))
    service.confirm(3, 3, (evidence(integrated),))
    service.confirm(5, 2, (evidence(legacy),))
    result = service.read(((3, 2), (3, 3), (5, 2)))
    assert result.targets == {(3, 2): legacy, (3, 3): integrated, (5, 2): legacy}
    with pytest.raises(StorageValidationError, match="같은 계열"):
        service.confirm(3, 2, (evidence(integrated),))


@pytest.mark.parametrize("field,value", [
    ("version_raw", "v1 edited"), ("version_order", 8), ("version_suffix", "DEX"),
    ("source_reference", {"version_raw": {"sheet": "다른원본", "cells": ["A9"]}}),
    ("edited_fields", ["version_raw"]), ("released_at", date(2026, 11, 1)),
])
def test_release_fingerprint_change_preserves_invalid_selected_id(revision_storage, field, value):
    storage = revision_storage
    rid = release(storage)
    service = TargetService(storage)
    service.confirm(1, 1, (evidence(rid),))
    with storage.transaction() as tx:
        tx.update("release_version", {"release_version_id": rid}, {field: value})
    result = service.read(((1, 1),), {(1, 1): rid})
    pair = result.pairs[0]
    assert pair.selected_release_id == rid and not pair.selection_valid
    assert pair.candidates == () and "변경" in pair.reason
    assert result.effective_targets == {}


def test_release_batch_replacement_invalidates_even_same_raw_and_order(revision_storage):
    storage = revision_storage
    rid = release(storage)
    service = TargetService(storage)
    service.confirm(1, 1, (evidence(rid),))
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL"})
        tx.update("release_version", {"release_version_id": rid}, {"batch_id": batch["batch_id"]})
    assert not service.read(((1, 1),), {(1, 1): rid}).pairs[0].selection_valid
    service.confirm(1, 1, (evidence(rid),))
    assert service.read(((1, 1),), {(1, 1): rid}).pairs[0].selection_valid


def test_deleted_release_never_replaced_by_new_same_text(revision_storage):
    storage = revision_storage
    rid = release(storage)
    service = TargetService(storage)
    service.confirm(1, 1, (evidence(rid),))
    with storage.transaction() as tx:
        tx.delete_release_version(rid)
    release(storage)
    result = service.read(((1, 1),), {(1, 1): rid})
    assert not result.pairs[0].selection_valid and result.pairs[0].selected_release_id == rid
    assert not result.pairs[0].candidates
    assert "삭제" in result.pairs[0].reason or "변경" in result.pairs[0].reason


def test_unknown_and_duplicate_family_orders_are_not_n_candidates(revision_storage):
    storage = revision_storage
    known = release(storage, raw="known", order=3)
    unknown = release(storage, raw="unknown", order=None)
    duplicate = release(storage, raw="duplicate", order=3)
    service = TargetService(storage)
    result = service.confirm(1, 1, (evidence(known), evidence(unknown)))
    assert result.candidates == () and result.recommended_release_id is None
    assert {r.release_id for r in result.excluded} == {known, unknown}
    assert any("중복" in r.reason for r in result.excluded)
    assert any("미확인" in r.reason for r in result.excluded)
    assert duplicate not in {r.release_id for r in result.excluded}


@pytest.mark.parametrize("model,family", [(3, 1), (4, 1), (999, 1), (1, 999)])
def test_model_category_analysis_and_registered_pair_checked(revision_storage, model, family):
    service = TargetService(revision_storage)
    with pytest.raises(StorageValidationError):
        service.confirm(model, family, ())
    pair = service.read(((model, family),)).pairs[0]
    assert pair.reason and not pair.candidates and not pair.selection_valid


def test_disabled_matching_category_model_rejected(revision_storage):
    with revision_storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 2}, {"analysis_enabled": False})
    with pytest.raises(StorageValidationError, match="분석 제외"):
        TargetService(revision_storage).confirm(2, 1, ())


def test_pair_definition_change_and_parent_cycle_invalidates(revision_storage):
    storage = revision_storage
    rid = release(storage)
    service = TargetService(storage)
    service.confirm(2, 1, (evidence(rid),))
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 2}, {"group_name": "O2 Plus 새이름"})
    pair = service.read(((2, 1),), {(2, 1): rid}).pairs[0]
    assert "변경" in pair.reason and not pair.selection_valid
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 1}, {"parent_group_id": 2})
    with pytest.raises(StorageValidationError, match="순환"):
        service.confirm(2, 1, (evidence(rid),))


@pytest.mark.parametrize("candidate", [
    CandidateConfirmation(1, "", "순서", source_text="원본"),
    CandidateConfirmation(1, "적용", "", source_text="원본"),
    CandidateConfirmation(1, "적용", "순서"),
    CandidateConfirmation(True, "적용", "순서", source_text="원본"),
    CandidateConfirmation(1, "적용", "순서", source_reference={"applicability": {"sheet": "원본", "cells": []}}),
])
def test_confirmation_requires_explicit_applicability_order_and_source(revision_storage, candidate):
    release(revision_storage)
    with pytest.raises(StorageValidationError):
        TargetService(revision_storage).confirm(1, 1, (candidate,))
    assert all_rows(revision_storage, "app_setting") == []


def test_atomic_replace_and_outer_rollback(revision_storage):
    storage = revision_storage
    old = release(storage, raw="v1", order=1)
    new = release(storage, raw="v2", order=2)
    service = TargetService(storage)
    service.confirm(1, 1, (evidence(old),))
    with pytest.raises(RuntimeError, match="취소"):
        with storage.transaction() as tx:
            service.confirm_in(tx, 1, 1, (evidence(new),))
            assert service.read_in(tx, ((1, 1),)).targets == {(1, 1): new}
            raise RuntimeError("업무 전체 취소")
    assert service.read(((1, 1),)).targets == {(1, 1): old}
    with pytest.raises(StorageValidationError, match="중복"):
        service.confirm(1, 1, (evidence(new), evidence(new)))
    assert service.read(((1, 1),)).targets == {(1, 1): old}


def test_saved_evidence_preserved_and_corruption_fails_closed(revision_storage):
    storage = revision_storage
    rid = release(storage)
    service = TargetService(storage)
    confirmation = evidence(rid)
    service.confirm(1, 1, (confirmation,))
    assert service.read(((1, 1),)).pairs[0].candidates[0].confirmation == confirmation
    setting = all_rows(storage, "app_setting")[0]
    payload = deepcopy(setting["setting_value"])
    payload["candidates"].append(payload["candidates"][0])
    with storage.transaction() as tx:
        tx.update("app_setting", {"setting_key": setting["setting_key"]}, {"setting_value": payload})
    result = service.read(((1, 1),), {(1, 1): rid})
    assert not result.pairs[0].selection_valid and not result.applicability


def test_empty_scope_and_invalid_selection_input(revision_storage):
    service = TargetService(revision_storage)
    assert service.read((), {(1, 1): None}).pairs == ()
    with pytest.raises(StorageValidationError):
        service.read((), {(1, 1): True})
    with pytest.raises(StorageValidationError, match="중복"):
        service.read(((1, 1), (1, 1)))
