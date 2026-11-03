"""실제 MySQL 자료와 조회 화면의 N·위험 재판정 연결을 확인한다."""

import os
import pytest

import test_ui_release_refresh as cases
from setron.features.risk.schemas import RiskStatus
from test_release_replacement_mysql import revision_mysql_storage
from test_schema_mysql import schema_database
from test_ui_release_refresh import app

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"),
                               reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


def test_mysql_current_model_evidence_drives_n_display(revision_mysql_storage):
    """변경 후 N 적용 근거가 무효화되면 실제 DB 조회도 재선택을 안내하는지 확인한다.

    param
    - revision_mysql_storage: 전용 MySQL 업무 저장소.
    """
    cases.test_live_database_query_revalidates_n_proof_without_changing_choice(revision_mysql_storage)


def test_mysql_saved_item_links_drive_current_risk_display(revision_mysql_storage):
    """실제 DB의 Item 상태 변경이 다음 조회의 위험 대수에 반영되는지 확인한다.

    param
    - revision_mysql_storage: 전용 MySQL 업무 저장소.
    """
    cases.test_live_database_query_uses_saved_item_risk_judgment(revision_mysql_storage)


@pytest.mark.parametrize("mapping_status,expected", [("VERIFIED", RiskStatus.RISK), ("UNRESOLVED", RiskStatus.UNKNOWN)])
def test_mysql_risk_display_uses_selected_model(revision_mysql_storage, mapping_status, expected):
    """한 설비의 다른 모델 PM에 지정한 위험이 현재 모델 조회에 섞이지 않는다."""
    cases.test_live_database_risk_uses_displayed_model_scope(revision_mysql_storage, mapping_status, expected)


def test_mysql_successful_save_invalidates_cached_results(app, revision_mysql_storage):
    """저장 뒤 적용 N을 유지하며 실제 조회 화면의 이전 결과만 지우는지 확인한다.

    param
    - app: Qt 이벤트 환경.
    - revision_mysql_storage: 전용 MySQL 업무 저장소.
    """
    cases.test_app_captures_applied_targets_and_invalidates_results_after_save(app, revision_mysql_storage)
