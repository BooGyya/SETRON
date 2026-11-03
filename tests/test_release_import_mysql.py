"""실제 MySQL에서 Excel 전체 노트 교체·출처 보존·판정 갱신의 원자성을 검증한다."""

import os

import pytest

import test_release_import_integration as cases
from test_release_replacement_mysql import revision_mysql_storage
from test_schema_mysql import schema_database

pytestmark = pytest.mark.skipif(
    not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.",
)


def test_mysql_actual_import_preserves_selected_links_and_refreshes_history(revision_mysql_storage):
    """실제 저장 경로에서 수동 적용 선택과 판정 갱신을 함께 검증한다.

    param
    - revision_mysql_storage: 검증 전용 MySQL 저장소.
    """
    cases.test_real_import_replacement_preserves_selected_manual_link_and_refreshes_history(revision_mysql_storage)


def test_mysql_actual_import_rejects_stale_review_without_new_raw_data(revision_mysql_storage):
    """선택 이후 바뀐 관리값이 있으면 원본 추가 전 교체 전체가 중단되는지 검증한다.

    param
    - revision_mysql_storage: 검증 전용 MySQL 저장소.
    """
    cases.test_real_import_stale_preview_aborts_before_any_source_write(revision_mysql_storage)


def test_mysql_actual_import_merges_cumulative_sheets_and_source_locations(revision_mysql_storage):
    """같은 계열의 여러 실제 시트가 한 누적 교체와 한 원본 묶음으로 저장되는지 검증한다.

    param
    - revision_mysql_storage: 검증 전용 MySQL 저장소.
    """
    cases.test_same_family_multi_sheet_import_is_one_reviewed_cumulative_note(revision_mysql_storage)


def test_mysql_actual_import_preserves_confirmed_shared_item_identity(revision_mysql_storage):
    """원본 근거가 같은 기존 공유 Item을 중복 생성하지 않는지 검증한다.

    param
    - revision_mysql_storage: 검증 전용 MySQL 저장소.
    """
    cases.test_shared_item_identity_is_preserved_across_reviewed_cumulative_versions(revision_mysql_storage)


def test_mysql_actual_import_refresh_failure_rolls_back_and_allows_retry(revision_mysql_storage, monkeypatch):
    """마지막 판정 갱신 실패 시 실제 원본·릴리즈·Item 변경이 모두 취소되는지 검증한다.

    param
    - revision_mysql_storage: 검증 전용 MySQL 저장소.
    - monkeypatch: 마지막 갱신 단계에 검증 실패를 주입하는 도구.
    """
    cases.test_final_recalculation_failure_rolls_back_actual_import_and_allows_retry(
        revision_mysql_storage, monkeypatch)


@pytest.mark.parametrize("fail_second", (False, True))
def test_mysql_actual_import_shared_original_families_are_atomic(revision_mysql_storage, monkeypatch, fail_second):
    """공유 원본·Item의 두 계열 교체가 후속 실패까지 한 번에 확정되거나 취소되는지 검증한다.

    param
    - revision_mysql_storage: 검증 전용 MySQL 저장소.
    - monkeypatch: 두 번째 계열 저장 후 오류 주입 도구.
    - fail_second: True이면 두 번째 계열까지 변경한 뒤 취소를 검증.
    """
    cases.test_two_family_notes_sharing_original_batch_and_item_are_atomic(
        revision_mysql_storage, monkeypatch, fail_second)
