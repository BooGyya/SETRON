"""BE-011의 날짜별 조회 목록과 모델별 N 후보 통합 계약."""

from datetime import date

import pytest

from setron.features.query_options.catalog import QueryCatalogService
from setron.features.query_options.schemas import QueryScope
from setron.features.release.service import ReleaseService
from setron.features.release.revision import ReleaseRevisionService
from setron.features.targets.schemas import CandidateConfirmation
from setron.features.targets.service import TargetService

from test_release_revision import all_rows, observation, release, revision_storage


DAY = date(2026, 10, 1)


def confirmation(release_id):
    """실제 업무 후보와 구분되는, 명시적으로 검토한 합성 시험 근거."""
    return CandidateConfirmation(release_id, "합성 시험의 모델 적용 표식 확인",
                                 "합성 시험의 같은 분기 순서 확인", source_text="합성 예시")


def test_one_read_returns_exact_scope_and_independent_initial_targets(revision_storage):
    storage = revision_storage
    older = release(storage, raw="v99", order=1)
    latest = release(storage, raw="v2_n4", order=2)
    observation(storage, code="BASE", versions=((1, 1, "v99", older),))
    observation(storage, code="MODIFIED", versions=((1, 2, "v99", older),))
    targets = TargetService(storage)
    targets.confirm(1, 1, (confirmation(older), confirmation(latest)))
    targets.confirm(2, 1, (confirmation(older),))
    service = QueryCatalogService(storage)
    initial = service.read(QueryScope(DAY))
    assert initial.options.model_family_pairs == ((1, 1), (2, 1))
    assert initial.target_catalog.targets == {(1, 1): latest, (2, 1): older}
    assert initial.refresh_context.applicability == {(1, 1): (older, latest), (2, 1): (older,)}
    selections = {(1, 1): older, (2, 1): older}
    before = all_rows(storage, "app_setting")
    narrowed = service.read(QueryScope(DAY, model_ids=(1,)), selections)
    assert narrowed.target_catalog.targets == {(1, 1): older}
    assert selections == {(1, 1): older, (2, 1): older}
    assert all_rows(storage, "app_setting") == before  # 선택 N은 저장하지 않음
    assert service.read().options.dates == (DAY,)
    assert service.read().target_catalog.pairs == ()
    assert service.read(QueryScope(date(2026, 10, 2))).target_catalog.pairs == ()


def test_changed_release_requires_reselection_without_replacing_the_selected_id(revision_storage):
    storage = revision_storage
    selected = release(storage)
    observation(storage, versions=((1, 1, "v1", selected),))
    TargetService(storage).confirm(1, 1, (confirmation(selected),))
    service = QueryCatalogService(storage)
    assert service.read(QueryScope(DAY)).target_catalog.pairs[0].selection_valid
    ReleaseService(storage).update_release(selected, {"version_suffix": "_DEX"})
    after = service.read(QueryScope(DAY), {(1, 1): selected})
    pair = after.target_catalog.pairs[0]
    assert pair.selected_release_id == selected
    assert not pair.selection_valid and pair.reason
    assert after.refresh_context.targets == {(1, 1): selected}
    assert after.refresh_context.applicability[(1, 1)] == ()


def test_same_transaction_can_read_new_candidate_confirmation(revision_storage):
    storage = revision_storage
    selected = release(storage)
    observation(storage, versions=((1, 1, "v1", selected),))
    service = QueryCatalogService(storage)
    with storage.transaction() as tx:
        service.targets.confirm_in(tx, 1, 1, (confirmation(selected),))
        result = service.read_in(tx, QueryScope(DAY))
        assert result.target_catalog.targets == {(1, 1): selected}
        assert result.target_catalog.pairs[0].selection_valid


@pytest.mark.parametrize("changes", [{"version_raw": "renamed"}, {"version_order": 7}, {"version_suffix": "_DEX"}])
def test_release_revision_revalidates_saved_evidence_before_returning_judgments(revision_storage, changes):
    storage = revision_storage
    selected = release(storage)
    observation(storage, versions=((1, 1, "v1", selected),))
    TargetService(storage).confirm(1, 1, (confirmation(selected),))
    catalog = QueryCatalogService(storage).read(QueryScope(DAY))
    assert catalog.refresh_context.use_saved_applicability
    result = ReleaseRevisionService(storage, catalog.refresh_context).update_release(selected, changes)
    validity = result.refresh.targets[0]
    assert validity.selected_release_id == selected
    assert not validity.valid and validity.reason
    # 저장 직후 반환하는 집계에도 더는 유효하지 않은 목표를 사용하지 않는다.
    assert result.refresh.dates[0].summary.equipment[0].families[0].status.value == "UNDETERMINED"


def test_narrow_scope_preserves_other_models_n_for_whole_database_rejudgment(revision_storage):
    storage = revision_storage
    low = release(storage, raw="v1", order=1)
    high = release(storage, raw="v2", order=2)
    observation(storage, code="BASE", versions=((1, 1, "v1", low),))
    observation(storage, code="MODIFIED", versions=((1, 2, "v1", low),))
    target_service = TargetService(storage)
    target_service.confirm(1, 1, (confirmation(low), confirmation(high)))
    target_service.confirm(2, 1, (confirmation(low),))
    chosen = {(1, 1): high, (2, 1): low}
    catalog = QueryCatalogService(storage).read(QueryScope(DAY, model_ids=(1,)), chosen)

    assert catalog.target_catalog.targets == {(1, 1): high}  # 표시 목록은 선택 범위만
    assert catalog.refresh_context.targets == chosen
    assert catalog.refresh_context.applicability == {(1, 1): (low, high), (2, 1): (low,)}
    result = ReleaseRevisionService(storage, catalog.refresh_context).update_release(high, {"version_suffix": "_DEX"})
    checked = {(value.model_id, value.family_id): value for value in result.refresh.targets}
    assert not checked[1, 1].valid and checked[2, 1].valid
    by_code = {equipment.record.equipment_code: equipment for equipment in result.refresh.dates[0].summary.equipment}
    assert by_code["BASE"].families[0].status.value == "UNDETERMINED"
    assert by_code["MODIFIED"].families[0].status.value == "AT_OR_ABOVE"


@pytest.mark.parametrize("scope", [QueryScope(), QueryScope(date(2026, 10, 2)), QueryScope(DAY, model_ids=(3,))])
def test_empty_display_scope_preserves_request_selection_snapshot_for_refresh(revision_storage, scope):
    selected = release(revision_storage)
    TargetService(revision_storage).confirm(1, 1, (confirmation(selected),))
    chosen = {(1, 1): selected, (2, 1): None, (999, 1): 999}
    catalog = QueryCatalogService(revision_storage).read(scope, chosen)
    original = dict(chosen)
    chosen[(1, 1)] = None
    chosen.pop((2, 1))

    assert catalog.target_catalog.pairs == ()
    assert catalog.refresh_context.targets == original
    assert catalog.refresh_context.applicability == {(1, 1): (selected,)}
    # 속성으로 반환한 변경 가능한 매핑도 보관한 결과를 오염시키지 않는다.
    external_context = catalog.refresh_context
    external_context.targets.clear()
    assert catalog.refresh_context.targets == original
