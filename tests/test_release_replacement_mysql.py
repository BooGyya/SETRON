"""실제 MySQL 5.6.19에서 BE-010 교체·전체 취소·위험 재연결을 검증한다."""

import os

import pytest

from setron.features.storage.service import StorageService
from setron.features.storage.validation import TABLE_NAMES

import test_release_replacement as cases
from test_release_revision import all_rows_in
from test_schema_mysql import schema_database

pytestmark = pytest.mark.skipif(
    not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.",
)


@pytest.fixture
def revision_mysql_storage(schema_database):
    """전용 빈 업무 DB에 검증 모델을 준비하고 이번 테스트의 자료만 정리한다.

    param
    - schema_database: 기존 테이블이 없는지 검사하고 실제 스키마를 만든 전용 테스트 DB.

    return
    - SQLite 검증과 같은 확인용 모델·계열을 가진 MySQL StorageService.
    """
    storage = StorageService(schema_database)
    with storage.transaction() as tx:
        for name in TABLE_NAMES:
            if name != "app_setting":
                assert not all_rows_in(tx, name), "기존 업무 자료가 있으면 검증 자료를 추가하지 않습니다."
        for identifier, name, category, parent, enabled in (
            (1, "O2", "OXIDE", None, True), (2, "O2 Plus", "OXIDE", 1, True),
            (3, "C3", "POLY", None, True), (4, "HPA", "HPA", None, False),
        ):
            tx.insert("equipment_group", {"group_id": identifier, "group_name": name, "category": category,
                "parent_group_id": parent, "analysis_enabled": enabled})
        tx.insert("version_family", {"family_id": 1, "group_id": 1, "family_name": "O2", "ui_type": None})
        tx.insert("version_family", {"family_id": 2, "group_id": 3, "family_name": "C3", "ui_type": "LEGACY"})
    try:
        yield storage
    finally:
        # 시작할 때 비어 있음을 확인한 테이블만 FK 역순으로 정리한다. 기본 설정은 유지한다.
        with storage.transaction() as tx:
            for table in reversed(storage.repository.metadata.sorted_tables):
                if table.name == "equipment_group":
                    for row in reversed(all_rows_in(tx, "equipment_group")):
                        tx.session.execute(table.delete().where(table.c.group_id == row["group_id"]))
                elif table.name != "app_setting":
                    tx.session.execute(table.delete())


def test_mysql_late_replacement_failure_rolls_back_originals_risks_and_links(revision_mysql_storage):
    """실제 MySQL에서 연결 삭제·원본 교체 뒤 실패해도 이번 묶음 전체가 취소되는지 검증한다.

    param
    - revision_mysql_storage: 이번 검증 전용 MySQL 업무 저장소.
    """
    cases.test_late_model_failure_rolls_back_entire_family_replacement(revision_mysql_storage)


def test_mysql_shared_item_preserves_other_family_and_reconnects_selected_risk(revision_mysql_storage):
    """실제 MySQL의 FK·원본 저장 형식에서도 공유 Item과 수동 위험의 분리가 유지되는지 검증한다.

    param
    - revision_mysql_storage: 이번 검증 전용 MySQL 업무 저장소.
    """
    cases.test_shared_item_clones_only_replaced_family_and_reconnects_its_manual_risk(revision_mysql_storage)


def test_mysql_removed_defect_end_stays_unknown_until_confirmed_recovery(revision_mysql_storage):
    """실제 MySQL에서 사라진 위험 해제 경계의 보존과 명시 확인 후 재연결을 검증한다.

    param
    - revision_mysql_storage: 이번 검증 전용 MySQL 업무 저장소.
    """
    cases.test_removed_defect_end_remains_unknown_until_exact_boundary_restored_and_confirmed(revision_mysql_storage)


@pytest.mark.parametrize("choice, expected_number, expected_content", [
    ("CURRENT", 70, "앱 수정 내용"), ("INCOMING", 7, "새 원본 내용"),
])
def test_mysql_original_number_conflict_choices_keep_new_source_separate(revision_mysql_storage, choice, expected_number, expected_content):
    """실제 MySQL에서 원본 번호 매칭·사용자 선택·새 출처와 기존 원본 보존을 검증한다.

    param
    - revision_mysql_storage: 이번 검증 전용 MySQL 업무 저장소.
    - choice, expected_number, expected_content: 사용자 선택과 저장할 기대 관리값.
    """
    cases.test_original_item_number_matches_after_edit_and_choices_use_new_source(
        revision_mysql_storage, choice, expected_number, expected_content)
