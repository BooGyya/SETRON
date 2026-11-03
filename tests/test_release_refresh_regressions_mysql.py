"""실제 MySQL에서 빈 선택 Line과 공백까지 다른 버전 원문의 재판정을 검증한다."""

import os

import pytest

import test_release_refresh_regressions as cases
from test_release_replacement_mysql import revision_mysql_storage
from test_schema_mysql import schema_database

pytestmark = pytest.mark.skipif(
    not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.",
)


@pytest.mark.parametrize("line", [None, "", "   "])
def test_mysql_optional_line_does_not_block_unrelated_release(revision_mysql_storage, line):
    """MySQL에서 정상 저장한 빈 Line이 다른 계열의 등록을 차단하지 않는지 확인한다.

    param
    - revision_mysql_storage: 기존 자료가 없는 전용 MySQL 저장소.
    - line: 미지정·빈 문자열·공백 문자열 Line 원문.
    """
    cases.test_saved_optional_line_does_not_block_unrelated_release(revision_mysql_storage, line)


def test_mysql_exact_whitespace_distinct_releases_preserve_other_family(revision_mysql_storage):
    """MySQL VARBINARY가 구분한 원문 때문에 다른 계열의 등록이 취소되지 않는지 확인한다.

    param
    - revision_mysql_storage: 기존 자료가 없는 전용 MySQL 저장소.
    """
    cases.test_exact_whitespace_distinct_releases_do_not_block_unrelated_family(revision_mysql_storage)


def test_mysql_exact_raw_comparison_and_confirmed_aliases(revision_mysql_storage):
    """MySQL에서도 정확한 원문·확정 별칭만 연결하고 N과 비교하는지 확인한다.

    param
    - revision_mysql_storage: 기존 자료가 없는 전용 MySQL 저장소.
    """
    cases.test_same_family_comparison_preserves_exact_raws_and_only_confirmed_aliases(revision_mysql_storage)
