"""실제 MySQL에서 수동 Item 포함 상태·모델 범위 충돌과 취소를 검증한다."""

import os

import pytest

from setron.features.risk.schemas import RiskStatus

import test_release_link_conflicts as cases
from test_release_replacement_mysql import revision_mysql_storage
from test_schema_mysql import schema_database

pytestmark = pytest.mark.skipif(
    not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


def test_mysql_missing_link_choice_blocks_whole_replacement(revision_mysql_storage):
    """실제 MySQL에서도 명시 선택 없는 연결 충돌이 전체 교체를 보류하는지 확인한다.

    param
    - revision_mysql_storage: 전용 MySQL 검증 저장소.
    """
    cases.test_missing_link_choice_blocks_entire_replacement(revision_mysql_storage)


@pytest.mark.parametrize("choice,state,scope,groups,status", [
    ("CURRENT", "NOT_APPLIED", "LISTED", [1], RiskStatus.RISK),
    ("INCOMING", "APPLIED", "ALL", None, RiskStatus.NO_RISK),
])
def test_mysql_explicit_link_choice_controls_safety_result(revision_mysql_storage, choice, state, scope, groups, status):
    """실제 MySQL의 연결 상태·범위와 위험 판정이 선택한 값으로 함께 저장되는지 확인한다.

    param
    - revision_mysql_storage: 전용 MySQL 검증 저장소.
    - choice, state, scope, groups, status: 명시 선택과 저장·판정 기대값.
    """
    cases.test_explicit_link_choice_preserves_or_replaces_safety_state(
        revision_mysql_storage, choice, state, scope, groups, status)


def test_mysql_shared_item_has_release_specific_link_choices(revision_mysql_storage):
    """같은 Item을 여러 릴리즈가 공유해도 선택이 버전별로 분리되는지 확인한다.

    param
    - revision_mysql_storage: 전용 MySQL 검증 저장소.
    """
    cases.test_same_item_in_two_releases_has_independent_collision_free_choices(revision_mysql_storage)


def test_mysql_outer_transaction_rolls_back_link_choice(revision_mysql_storage):
    """후속 시트 실패 시 선택·교체된 연결을 포함한 전체 변경이 취소되는지 확인한다.

    param
    - revision_mysql_storage: 전용 MySQL 검증 저장소.
    """
    cases.test_transaction_hook_does_not_commit_partial_replacement(revision_mysql_storage)
