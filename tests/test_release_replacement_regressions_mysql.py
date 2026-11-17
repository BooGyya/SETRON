"""BE-010 수동 위험 보존·버전명 교환 회귀를 실제 MySQL에서 검증한다."""

import os

import pytest

import test_release_replacement_regressions as cases
from test_release_replacement_mysql import revision_mysql_storage
from test_schema_mysql import schema_database

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"),
                                reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


def test_mysql_preserves_unlinked_manual_risk_target(revision_mysql_storage):
    """릴리즈에 연결되지 않은 수동 위험의 원래 Item 연결·근거를 보존하는지 검증한다.

    param
    - revision_mysql_storage: 전용 MySQL 업무 저장소.
    """
    cases.test_unlinked_manual_target_keeps_pointer_evidence_and_exclusion_from_replacement(revision_mysql_storage)


def test_mysql_preserves_risk_target_owned_by_other_family(revision_mysql_storage):
    """다른 계열에 연결된 수동 대상과 해당 계열 위험을 변경하지 않는지 검증한다.

    param
    - revision_mysql_storage: 전용 MySQL 업무 저장소.
    """
    cases.test_unlinked_risk_target_in_other_family_is_not_changed(revision_mysql_storage)


def test_mysql_rejects_stale_manual_risk_target_review(revision_mysql_storage):
    """검토 이후 수동 대상이 바뀌면 교체를 보류하는지 실제 DB에서 검증한다.

    param
    - revision_mysql_storage: 전용 MySQL 업무 저장소.
    """
    cases.test_unlinked_manual_target_changes_invalidate_review(revision_mysql_storage)


def test_mysql_keeps_missing_manual_target_unresolved(revision_mysql_storage):
    """대상 미확인 위험을 새로운 노트에 임의 연결하지 않는지 검증한다.

    param
    - revision_mysql_storage: 전용 MySQL 업무 저장소.
    """
    cases.test_missing_manual_target_remains_unresolved(revision_mysql_storage)


@pytest.mark.parametrize("count", [2, 3])
def test_mysql_saves_final_unique_name_cycles(revision_mysql_storage, count):
    """MySQL의 즉시 UNIQUE 검사에서도 두 행·세 행의 버전명 교환을 저장하는지 검증한다.

    param
    - revision_mysql_storage: 전용 MySQL 업무 저장소.
    - count: 순환 교환할 릴리즈 수.
    """
    cases.test_retained_version_name_cycles_preserve_ids_sources_targets_and_risks(revision_mysql_storage, count)


def test_mysql_new_release_can_reuse_freed_name(revision_mysql_storage):
    """기존 행에서 비우는 이름으로 신규 릴리즈를 먼저 저장할 수 있는지 검증한다.

    param
    - revision_mysql_storage: 전용 MySQL 업무 저장소.
    """
    cases.test_new_release_can_take_name_freed_by_retained_release(revision_mysql_storage)


def test_mysql_rolls_back_name_staging(revision_mysql_storage, monkeypatch):
    """임시 버전명과 일부 최종값을 저장한 후 실패하면 전체가 복구되는지 검증한다.

    param
    - revision_mysql_storage: 전용 MySQL 업무 저장소.
    - monkeypatch: 저장 단계 실패를 주입하는 pytest 도구.
    """
    cases.test_failure_after_name_staging_restores_all_original_rows(revision_mysql_storage, monkeypatch)


def test_mysql_rejects_duplicate_final_names(revision_mysql_storage):
    """최종 중복 선택을 보류하고 기존 DB를 그대로 유지하는지 검증한다.

    param
    - revision_mysql_storage: 전용 MySQL 업무 저장소.
    """
    cases.test_duplicate_final_names_are_rejected_without_saving_staged_names(revision_mysql_storage)
