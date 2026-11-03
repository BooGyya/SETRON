"""실제 MySQL에서도 공백이 다른 릴리즈 원문을 가져올 수 있다."""

import os
import pytest

import test_release_exact_import as cases
from test_release_replacement_mysql import revision_mysql_storage
from test_schema_mysql import schema_database

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"),
                               reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


def test_mysql_actual_import_preserves_distinct_raw_versions(revision_mysql_storage):
    """원문을 보존하는 검토·교체를 실제 DB 저장까지 검증한다."""
    cases.test_actual_import_preserves_distinct_raw_versions(revision_mysql_storage)
