"""화면 저장의 원자성·최종 결과 미확인 처리를 전용 MySQL에서도 검증한다."""

import os

import pytest

from setron.features import production_area, reference_data
from test_schema_mysql import schema_database, storage  # noqa: F401
from test_atomic_management_save import (  # noqa: F401
    test_commit_unknown_is_not_retried_or_published,
    test_later_invalid_row_cancels_earlier_new_row_and_cell_edit,
    test_registration_and_audits_roll_back_on_stale_cell_revision,
    test_release_save_failure_cancels_cell_edit_new_version_item_and_audits,
    test_release_undo_failure_cancels_structure_and_cell_restore,
    test_row_failure_rolls_back_registrations_edits_rows_and_audits_then_retries,
    test_sheet_undo_structure_and_cells_roll_back_together_and_rebase_owned_revisions,
)

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="전용 MySQL 테스트 DB 필요")


@pytest.fixture
def atomic_storage(storage, monkeypatch):
    monkeypatch.setattr(reference_data, "REGISTERED_AREAS", dict(reference_data.REGISTERED_AREAS))
    monkeypatch.setattr(production_area, "_managed_labels", dict(production_area._managed_labels))
    return storage
