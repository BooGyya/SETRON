"""실제 MySQL 교체 뒤 저장 전 관리 재조회의 성공·실패 응답을 폐기한다."""

import os
import pytest

import test_ui_release_management_refresh as cases
from test_release_replacement_mysql import revision_mysql_storage
from test_schema_mysql import schema_database
from test_ui_release_refresh import app

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"),
                               reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


@pytest.mark.parametrize("late_error", (False, True))
def test_mysql_import_discards_pending_management_refresh(app, revision_mysql_storage, late_error):
    cases.test_import_save_discards_previous_management_refresh(app, revision_mysql_storage, late_error)
