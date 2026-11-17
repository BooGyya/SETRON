"""BE-025 최종 수정의 날짜별 위치와 XLSX 원문 보존을 실제 MySQL로 검증한다."""

import os

import pytest

import test_management_db_source as source_cases
import test_management_export as export_cases
from test_release_replacement_mysql import revision_mysql_storage
from test_schema_mysql import schema_database


pytestmark = pytest.mark.skipif(
    not os.environ.get("SETRON_TEST_DB_NAME"),
    reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.",
)


def test_mysql_out_of_order_dates_preserve_current_dashboard_and_tile_location(revision_mysql_storage):
    """과거 관측을 나중 저장·수정해도 현재 위치와 다른 날짜의 값은 유지한다."""
    source_cases.test_out_of_order_observations_keep_current_location_after_each_date_edit(
        revision_mysql_storage,
    )


def test_mysql_hpa_latest_location_remains_independent_of_analysis_date(revision_mysql_storage):
    """HPA의 최신 위치를 일반 설비 분석일이나 저장 순서로 덮어쓰지 않는다."""
    source_cases.test_out_of_order_hpa_locations_keep_independent_latest_observation(
        revision_mysql_storage,
    )


def test_mysql_saved_text_and_long_risk_reason_survive_current_xlsx(revision_mysql_storage, tmp_path):
    """실제 TEXT 저장값을 수식 해석·잘림 없이 현재 현황 XLSX로 내보낸다."""
    export_cases.test_saved_edits_survive_current_status_xlsx_roundtrip_without_formula_or_text_loss(
        revision_mysql_storage, tmp_path,
    )
