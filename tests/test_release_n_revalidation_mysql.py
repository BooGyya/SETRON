"""실제 MySQL에서 BE-010 N 적용 근거 재검증을 확인한다."""

import os

import pytest

import test_release_n_revalidation as cases
from test_release_replacement_mysql import revision_mysql_storage
from test_schema_mysql import schema_database

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


def test_mysql_changed_release_preserves_independent_model_n(revision_mysql_storage):
    """실제 MySQL에서 다른 모델의 변경 없는 목표와 확인 근거를 보존하는지 확인한다.

    param
    - revision_mysql_storage: 빈 DB 확인을 거친 전용 테스트 저장소.
    """
    cases.test_editing_one_target_preserves_other_model_target_and_evidence(revision_mysql_storage)


def test_mysql_saved_evidence_is_read_after_mutation(revision_mysql_storage):
    """실제 MySQL에서도 수정 전의 모델 적용 근거가 재사용되지 않는지 확인한다.

    param
    - revision_mysql_storage: 전용 테스트 저장소.
    """
    cases.test_saved_evidence_is_rechecked_after_mutation_for_every_selected_model(revision_mysql_storage)


def test_mysql_reimport_source_change_requires_reconfirmation(revision_mysql_storage):
    """원본 교체 후 같은 릴리즈 ID를 가리키는 N도 새 근거가 필요한지 확인한다.

    param
    - revision_mysql_storage: 전용 테스트 저장소.
    """
    cases.test_reimport_source_change_invalidates_explicit_context_with_same_release_id(revision_mysql_storage)


def test_mysql_management_edit_filters_old_explicit_applicability(revision_mysql_storage):
    """관리 수정도 변경 전 적용 목록을 버리고 다른 모델의 목표는 보존하는지 확인한다.

    param
    - revision_mysql_storage: 전용 테스트 저장소.
    """
    cases.test_management_edit_filters_stale_explicit_targets_but_keeps_other_model(revision_mysql_storage)


def test_mysql_management_new_proof_restores_only_confirmed_model(revision_mysql_storage):
    """같은 저장 묶음의 새 적용 확인이 명시적으로 확인한 모델에만 반영되는지 검증한다.

    param
    - revision_mysql_storage: 전용 테스트 저장소.
    """
    cases.test_management_same_batch_fresh_proof_restores_only_confirmed_model(revision_mysql_storage)
