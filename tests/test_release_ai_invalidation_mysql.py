"""실제 MySQL 교체 뒤 저장 전 AI 작업 폐기와 최신 자료 재요청을 확인한다."""

import os
import pytest

import test_ui_release_ai_invalidation as cases
from test_release_replacement_mysql import revision_mysql_storage
from test_schema_mysql import schema_database
from test_ui_release_refresh import app

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"),
                               reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


def test_mysql_import_save_discards_old_ai_facts(app, revision_mysql_storage):
    cases.test_import_save_discards_late_ai_facts_without_restoring_old_snapshot(app, revision_mysql_storage, False)


def test_mysql_import_save_cancels_queued_analysis(app, revision_mysql_storage):
    cases.test_import_save_cancels_queued_ai_analysis_after_facts_were_accepted(app, revision_mysql_storage)


def test_mysql_save_during_fresh_delivery_cancels_old_analysis(app, revision_mysql_storage):
    cases.test_save_inside_fresh_data_signal_does_not_reschedule_previous_analysis(app, revision_mysql_storage)


def test_mysql_explicit_ai_request_reads_current_judgments_after_save(app, revision_mysql_storage):
    cases.test_import_save_clears_answer_and_explicit_new_request_uses_current_judgments(app, revision_mysql_storage)
