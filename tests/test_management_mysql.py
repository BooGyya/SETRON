"""전용 MySQL 5.6.19에서 BE-025 저장·재판정·SQL 출력의 실제 동작을 검증한다."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from openpyxl import load_workbook

from setron.features.db_export.service import export_database
from setron.features.export.management import save_management_data
from setron.features.management import ReportFilter
from setron.features.storage.validation import TABLE_NAMES

import test_management_service as cases
from test_release_replacement_mysql import revision_mysql_storage
from test_release_revision import all_rows
from test_schema_mysql import schema_database


pytestmark = pytest.mark.skipif(
    not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.",
)


def test_mysql_snapshot_edit_preserves_original_and_other_date(revision_mysql_storage):
    """실제 MySQL의 저장 형식에서도 선택 날짜와 원본 보존을 지킨다."""
    cases.test_snapshot_edit_preserves_original_and_other_date(revision_mysql_storage)


def test_mysql_selected_pm_split_and_coalesce(revision_mysql_storage):
    """실제 복합 외래키·고유키에서 PM별 SW 분리·통합을 검증한다."""
    cases.test_selected_pm_split_and_coalesce_leave_other_pm_and_date_unchanged(revision_mysql_storage)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("existing_target", [False, True])
def test_mysql_shared_pm_final_configuration(revision_mysql_storage, reverse, existing_target):
    """실제 고유키·복합 외래키에서도 최종 PM 묶음을 순서와 무관하게 저장한다."""
    cases.test_shared_pm_split_and_link_batch_uses_final_configuration(
        revision_mysql_storage, reverse, existing_target,
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_mysql_pm_software_raw_swap(revision_mysql_storage, reverse):
    """MySQL에서도 원문 교환이 비선택 PM과 입력 근거를 바꾸지 않는다."""
    cases.test_pm_software_raw_swap_preserves_unselected_pm_and_originals(revision_mysql_storage, reverse)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("invalid_link", [None, "foreign"])
def test_mysql_final_shared_pm_link_conflict(revision_mysql_storage, reverse, invalid_link):
    """명시한 연결의 최종 충돌은 MySQL 변경·수정 이력도 함께 취소한다."""
    cases.test_final_shared_pm_link_conflict_rolls_back_whole_batch(revision_mysql_storage, reverse, invalid_link)


def test_mysql_non_pm_version_change_rechecks_release(revision_mysql_storage):
    """실제 MySQL에서도 SW 수정 후 이전 연결을 새 원문과 재검증한다."""
    cases.test_non_pm_version_change_rechecks_release_link(revision_mysql_storage)


def test_mysql_mixed_batch_validation_rolls_back(revision_mysql_storage):
    """실제 저장 중 뒤쪽 필수값 오류가 먼저 수정한 Item도 되돌린다."""
    cases.test_invalid_later_edit_rolls_back_entire_mixed_batch(revision_mysql_storage)


def test_mysql_database_failure_rolls_back(revision_mysql_storage, monkeypatch):
    """SQL 오류가 발생하면 실제 MySQL 트랜잭션의 관리값·이력이 모두 취소된다."""
    cases.test_database_failure_rolls_back_changed_rows_and_journal(revision_mysql_storage, monkeypatch)


def test_mysql_stale_revision_cannot_overwrite_latest(revision_mysql_storage):
    """실제 MySQL에서도 오래된 검토 지문으로 최신 관리값을 덮지 못한다."""
    cases.test_stale_record_requires_review_without_overwriting_newer_value(revision_mysql_storage)


def test_mysql_item_edit_rejudges_risk(revision_mysql_storage):
    """Item 적용·위험 결과가 같은 MySQL 저장 묶음의 최신값을 사용한다."""
    cases.test_item_link_edit_rejudges_application_and_safety_risk(revision_mysql_storage)


def test_mysql_duplicate_key_formats_rejected(revision_mysql_storage):
    """숫자 문자열로 같은 실제 키의 중복 수정 검사를 우회하지 못한다."""
    cases.test_duplicate_integer_and_string_key_cannot_edit_same_record_twice(revision_mysql_storage)


def test_mysql_wrong_family_link_is_rejected_before_refresh(revision_mysql_storage):
    """잘못된 입력 릴리즈 연결을 재판정이 자동 수정해 검증 오류를 숨기지 않는다."""
    cases.test_submitted_cross_family_release_is_not_silently_repaired(revision_mysql_storage)


def test_mysql_latest_direct_edit_is_in_management_xlsx_and_sql(revision_mysql_storage):
    """저장된 수정값과 보관 원본이 별도 변환 없이 전체 XLSX·SQL 출력에 반영된다."""
    storage = revision_mysql_storage
    service, snapshot, _, _ = cases.service_for(storage)
    sources = all_rows(storage, "import_batch")
    service.save((cases.edit(service, "equipment_snapshot", {"snapshot_id": snapshot.snapshot_id},
                              {"line": "직접수정한라인"}),))
    with TemporaryDirectory(prefix="setron-management-export-") as directory:
        root = Path(directory)
        xlsx_path, sql_path = root / "관리자료.xlsx", root / "전체자료.sql"
        result = save_management_data(storage, xlsx_path)
        assert result.saved, result.message
        with xlsx_path.open("rb") as file:
            book = load_workbook(file)
        assert tuple(book.sheetnames) == TABLE_NAMES
        sheet = book["equipment_snapshot"]
        columns = {cell.value: cell.column for cell in sheet[1]}
        assert sheet.cell(2, columns["line"]).value == "직접수정한라인"
        assert sheet.cell(2, columns["source_reference"]).value
        book.close()
        result = export_database(storage.database, sql_path)
        assert result.saved, result.message
        sql = sql_path.read_text(encoding="utf-8")
        assert "직접수정한라인" in sql
        assert "원본 모델" in sql
        assert "CREATE TABLE `equipment_snapshot`" in sql
        assert "CREATE TABLE `import_batch`" in sql
    assert all_rows(storage, "import_batch")[:len(sources)] == sources


def test_mysql_current_report_applies_exact_saved_site_and_excludes_other_site(revision_mysql_storage):
    """실제 MySQL의 최신 관리값을 범위로 좁힌 뒤 집계·XLSX 입력을 생성한다."""
    storage = revision_mysql_storage
    service, snapshot, first, _ = cases.service_for(storage)
    other = cases.observe(storage, first, code="EQ-2")
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": other.snapshot_id}, {"site": "평택"})
    service.save((cases.edit(service, "equipment_snapshot", {"snapshot_id": snapshot.snapshot_id},
                              {"line": "수정한라인"}),))
    current = service.current_report(snapshot.base_date, filters=ReportFilter(site_names=("화성",)))
    assert current.summary.equipment_count == 1
    assert [value.record.equipment_code for value in current.summary.equipment] == ["EQ-1"]
    assert current.summary.equipment[0].record.line == "수정한라인"
    assert current.scope[0] == ("사업장", "화성")
    assert all(point.equipment_count == 1 for point in current.trend)
