"""BE-009: DB 기존값을 검토 화면에서 확인한 뒤 파일명 확인과 함께 저장한다."""

from datetime import date, datetime
import os
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest

from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.excel_import.service import MemoryImportSaver, edit_value
from setron.features.excel_import.schemas import SaveReceipt
from setron.features.import_save.service import DatabaseImportSaver
from setron.ui.import_page import ImportPage

from test_import_validation import draft, family, reference, row
from test_ui_import import app  # noqa: F401  실제 Qt 앱 fixture 재사용


def existing_reference(*, version="v2", site="화성", line="기존 라인"):
    """서버 접속 없이 같은 날짜·PM의 기존 관리값을 제공한다."""
    saved = SnapshotInput(
        equipment_code="E-1", base_date=date(2026, 11, 2), site=site, line=line,
        model_name="M-O2", batch_id=1,
        software=(SoftwareInput(family_id=1, version_raw=version,
            pms=(PmInput(pm_name="PM1", group_id=1, model_name_raw="M-O2"),)),),
    )
    return reference(family(), snapshots=(saved,))


class ReviewingDatabaseSaver(DatabaseImportSaver):
    """DB 검토 계약과 저장 요청을 관찰하는 가짜 공급자. 실제 DB는 사용하지 않는다."""

    def __init__(self, reference_data):
        self.reference_data = reference_data
        self.load_calls, self.saved = [], []
        self.failure = None

    def load_reference(self, drafts):
        self.load_calls.append(drafts)
        if self.failure is not None:
            raise self.failure
        return self.reference_data

    def save(self, request):
        self.saved.append(request)
        return SaveReceipt(1, len(request.rows), datetime.now(), self.target_label)


@pytest.fixture
def page(app):
    saver = ReviewingDatabaseSaver(existing_reference())
    result = ImportPage(saver=saver)
    result.confirms = []
    result.confirm = lambda text: result.confirms.append(text) or True
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def review(page):
    page.draft = draft(row(line="새 라인"))
    page._changed(initial=True)
    page.select_row(1)
    wait_review(page)


def wait_review(page):
    end = monotonic() + 5
    while page.busy and monotonic() < end:
        QTest.qWait(10)
    assert not page.busy


def test_db_is_not_read_when_import_page_is_created(page):
    assert page.saver.load_calls == []
    assert page.reference is None


def test_review_reads_database_and_displays_previous_and_new_values(page):
    review(page)
    assert page.saver.load_calls == [(page.draft,)]
    changes = [issue for issue in page.issues if issue.code == "STORED_VALUE_CHANGED"]
    assert any(issue.field == "line" and issue.previous_value == "기존 라인" and issue.current_value == "새 라인"
               for issue in changes)
    assert any(issue.field == "version_raw" and issue.previous_value == "v2" and issue.current_value == "v1"
               for issue in changes)
    assert page.save_button.isEnabled()


def test_edited_values_refresh_reference_and_revalidate_the_whole_draft(page):
    review(page)
    page.saver.reference_data = existing_reference(version="v1", line="새 라인")
    assert page.edit_cell("version_raw", "v2")
    wait_review(page)
    assert len(page.saver.load_calls) == 2
    assert page.saver.load_calls[-1] == (page.draft,)
    assert any(issue.field == "version_raw" and issue.previous_value == "v1" and issue.current_value == "v2"
               for issue in page.issues)
    assert not any(issue.code == "STORED_VALUE_CHANGED" and issue.field == "line" for issue in page.issues)


def test_reference_load_failure_discards_stale_reference_and_blocks_save(page):
    review(page)
    page.saver.failure = RuntimeError("비밀 접속 정보가 포함될 수 있는 드라이버 오류")
    page.draft = edit_value(page.draft, 1, "version_raw", "v3")
    page._changed()
    wait_review(page)
    assert page.reference is None
    issue = next(issue for issue in page.issues if issue.code == "REFERENCE_LOAD_FAILED")
    assert issue.blocks_save
    assert issue.workbook_name == page.draft.workbook_name and issue.sheet_name == page.draft.sheet.sheet_name
    assert "비밀" not in issue.message
    assert not page.save_button.isEnabled()
    assert not page.save()
    assert page.confirms == [] and page.saver.saved == []

    page.saver.failure = None
    page.revert_selected()
    wait_review(page)
    assert page.reference is page.saver.reference_data
    assert not any(issue.code == "REFERENCE_LOAD_FAILED" for issue in page.issues)
    assert page.save_button.isEnabled()


def test_confirmation_uses_reviewed_reference_and_file_name_without_unseen_refresh(page):
    review(page)
    reviewed = page.reference
    page.saver.reference_data = existing_reference(version="v3", line="변경된 라인")
    assert page.save()
    wait_review(page)
    assert page.draft is None and len(page.saver.saved) == 1   # 저장 뒤 1단계로 돌아간다
    assert page.reference is reviewed and len(page.saver.load_calls) == 1
    request, = page.saver.saved
    assert request.rows[0].family_id == 1
    assert request.expected_snapshots == reviewed.snapshots
    assert request.expected_snapshots != page.saver.reference_data.snapshots
    assert request.workbook_name in page.confirms[0]
    assert "확인 필요 항목" in page.confirms[0]


def test_memory_saver_keeps_injected_reference_without_database_lookup(app):
    supplied = existing_reference()
    page = ImportPage(saver=MemoryImportSaver(), reference=supplied)
    try:
        review(page)
        assert page.reference is supplied
        assert any(issue.code == "STORED_VALUE_CHANGED" for issue in page.issues)
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()


def test_revalidate_keeps_review_edits_and_refreshes_database_after_reconnect(page):
    review(page)
    page.saver.failure = RuntimeError("연결 중단")
    assert page.edit_cell("version_raw", "v4")
    wait_review(page)
    edited = page.draft
    request_id = page.request_id
    assert not page.save_button.isEnabled() and page.revalidate_button.isEnabled()
    assert any("다시 검사" in issue.message for issue in page.issues if issue.code == "REFERENCE_LOAD_FAILED")

    page.saver.failure = None
    page.saver.reference_data = existing_reference(version="v3", site="화성")
    page.revalidate_button.click()
    wait_review(page)

    assert page.draft == edited and page.draft.row(1).edits["version_raw"] == "v4"
    assert page.draft.row(1).original["version_raw"] == "v1"
    assert page.request_id != request_id
    assert page.reference is page.saver.reference_data
    assert any(issue.field == "version_raw" and issue.previous_value == "v3" and issue.current_value == "v4"
               for issue in page.issues)
    assert not any(issue.code == "REFERENCE_LOAD_FAILED" for issue in page.issues)
    assert page.save_button.isEnabled()


@pytest.mark.parametrize("state", ["reading", "extracting", "validating", "saving", "saved", "unknown"])
def test_revalidate_is_disabled_while_busy_or_locked(page, state):
    review(page)
    page.state = state
    page._render()
    calls, request_id, original = len(page.saver.load_calls), page.request_id, page.draft

    assert not page.revalidate_button.isEnabled()
    assert not page.revalidate()
    assert len(page.saver.load_calls) == calls and page.request_id == request_id and page.draft == original


def test_revalidate_without_a_draft_does_not_connect_to_database(page):
    assert not page.revalidate_button.isEnabled()
    assert not page.revalidate()
    assert page.saver.load_calls == []
