"""실제 MySQL에서 분석 제외 모델의 목표 N 검증 경계를 확인한다."""

import os

import pytest

import test_release_hpa_target as cases
from test_release_replacement_mysql import revision_mysql_storage
from test_schema_mysql import schema_database

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"),
                               reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


@pytest.mark.parametrize("use_saved", [False, True])
def test_mysql_hpa_target_preserves_independent_model_n(revision_mysql_storage, use_saved):
    """명시 목록·저장 근거 양쪽에서 HPA를 제외하고 다른 모델의 N은 유지한다."""
    cases.test_hpa_target_is_excluded_after_registration_and_keeps_other_model_n(revision_mysql_storage, use_saved)


def test_mysql_disabled_model_cannot_reuse_explicit_n_evidence(revision_mysql_storage):
    """같은 분류의 일반 모델도 분석 제외 상태이면 명시 근거로 N을 허용하지 않는다."""
    cases.test_disabled_model_cannot_reuse_explicit_n_evidence(revision_mysql_storage)


def test_mysql_unselected_hpa_target_does_not_request_selection(revision_mysql_storage):
    """HPA에 목표를 새로 고르도록 잘못 안내하지 않는다."""
    cases.test_unselected_hpa_target_does_not_request_n_selection(revision_mysql_storage)
