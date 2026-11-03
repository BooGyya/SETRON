"""실제 MySQL 교체 후 화면 갱신·확인 안내와 조회 실패 복구를 검증한다."""

import os
import pytest

import test_ui_release_saved_pages as cases
import test_ui_release_risk_notice as risk_cases
from test_release_replacement_mysql import revision_mysql_storage
from test_schema_mysql import schema_database
from test_ui_release_refresh import app

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"),
                               reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


def test_mysql_save_refreshes_both_pages_and_reports_unknown_risk(app, revision_mysql_storage):
    cases.test_actual_save_refreshes_both_pages_and_reports_unresolved_risk(app, revision_mysql_storage)


def test_mysql_page_read_failure_can_retry_after_committed_save(app, revision_mysql_storage):
    cases.test_failed_page_refresh_clears_old_data_and_retry_reads_current_db(app, revision_mysql_storage)


def test_mysql_missing_risk_is_acknowledged_and_preserved(app, revision_mysql_storage, monkeypatch):
    missing = risk_cases.missing_risk.__wrapped__(revision_mysql_storage)
    risk_cases.test_missing_risk_target_is_explained_and_preserved_unresolved(app, missing, monkeypatch)


def test_mysql_missing_risk_notice_can_cancel(app, revision_mysql_storage, monkeypatch):
    missing = risk_cases.missing_risk.__wrapped__(revision_mysql_storage)
    risk_cases.test_missing_risk_warning_can_cancel_without_approving_or_writing(app, missing, monkeypatch)
