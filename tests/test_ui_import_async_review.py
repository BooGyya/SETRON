"""느린 DB 검토 중 UI 응답, 초안 고정, 취소·종료 후 늦은 결과 격리를 검증한다."""

import os
from threading import Event, get_ident
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool, QTimer
from PySide6.QtTest import QTest
from shiboken6 import isValid

from setron.features.excel_import.service import edit_value
from setron.ui.import_page import ImportPage

from test_import_validation import draft, row
from test_ui_import import app  # noqa: F401  실제 Qt 앱 fixture
from test_ui_import_db_review import ReviewingDatabaseSaver, existing_reference


def wait_until(predicate):
    end = monotonic() + 5
    while not predicate() and monotonic() < end:
        QTest.qWait(10)
    assert predicate()


class SlowReference:
    """테스트가 응답 시점을 결정하는 위젯과 독립적인 DB 읽기 공급자."""

    def __init__(self, value):
        self.value = value
        self.started, self.release = Event(), Event()
        self.calls, self.thread = [], None

    def __call__(self, drafts):
        self.calls.append(drafts)
        self.thread = get_ident()
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError("테스트 DB 읽기 제한 시간")
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


@pytest.fixture
def context(app):
    pages, loaders = [], []

    def create(value=None):
        saver = ReviewingDatabaseSaver(existing_reference())
        loader = SlowReference(existing_reference() if value is None else value)
        saver.load_reference = loader
        page = ImportPage(saver=saver)
        pages.append(page)
        loaders.append(loader)
        page.show()
        page.draft = draft(row())
        page.select_row(1)
        return page, loader

    def replace_loader(page, value):
        loader = SlowReference(value)
        loaders.append(loader)
        page.saver.load_reference = loader
        return loader

    yield create, replace_loader
    for loader in loaders:
        loader.release.set()
    for page in pages:
        if isValid(page):
            page.close()
    assert QThreadPool.globalInstance().waitForDone(6000)
    QTest.qWait(10)
    for page in pages:
        if isValid(page):
            page.deleteLater()
    app.processEvents()


def test_slow_review_keeps_ui_responsive_and_blocks_mutation_and_duplicate_work(context):
    create, _ = context
    page, slow = create()
    current = page.draft
    page._changed(initial=True)
    wait_until(slow.started.is_set)

    assert slow.thread != get_ident() and page.busy and page.review_request.busy
    assert not page.save_button.isEnabled() and not page.revalidate_button.isEnabled()
    assert not page.workbook_combo.isEnabled() and not page.sheet_combo.isEnabled() and not page.kind_combo.isEnabled()
    assert page.inputs["version_raw"].isReadOnly()
    assert not page.apply_edit_button.isEnabled() and not page.exclude_button.isEnabled()
    assert not page.save() and not page.revalidate()
    assert not page.apply_edits() and not page.revert_selected() and not page.toggle_excluded()
    assert not page.load_workbooks() and not page.read_selected() and not page.extract()
    assert len(slow.calls) == 1 and slow.calls[0][0] == current and slow.calls[0][0] is not current

    ticks, completed = [], []
    QTimer.singleShot(0, lambda: ticks.append(get_ident()))
    page.review_request.completed.connect(lambda *_: completed.append(get_ident()))
    wait_until(lambda: bool(ticks))
    assert ticks == [get_ident()] and page.busy

    slow.release.set()
    wait_until(lambda: not page.busy)
    assert completed == [get_ident()]
    assert page.save_button.isEnabled() and not page.inputs["version_raw"].isReadOnly()
    assert page.reference is slow.value and page.draft is current


@pytest.mark.parametrize("failure", [RuntimeError("DB 접속 실패"), object()])
def test_failed_async_review_can_retry_without_losing_review_edits(context, failure):
    create, replace_loader = context
    page, slow = create(failure)
    page.draft = edit_value(page.draft, 1, "version_raw", "v4")
    edited = page.draft
    page._changed()
    wait_until(slow.started.is_set)
    slow.release.set()
    wait_until(lambda: not page.busy)
    assert page.reference is None
    assert any(issue.code == "REFERENCE_LOAD_FAILED" for issue in page.issues)
    assert not page.save_button.isEnabled() and page.revalidate_button.isEnabled()

    retry = replace_loader(page, existing_reference(version="v3"))
    assert page.revalidate()
    wait_until(retry.started.is_set)
    retry.release.set()
    wait_until(lambda: not page.busy)
    assert page.draft is edited and page.draft.row(1).edits["version_raw"] == "v4"
    assert page.reference is retry.value and page.save_button.isEnabled()
    assert any(issue.previous_value == "v3" and issue.current_value == "v4" for issue in page.issues)


@pytest.mark.parametrize("cancel", ["hide", "reset"])
def test_cancelled_old_result_cannot_replace_a_new_review(context, cancel):
    create, replace_loader = context
    page, first = create(existing_reference(version="v2"))
    page._changed()
    wait_until(first.started.is_set)
    if cancel == "hide":
        page.hide()
        assert not page.save_button.isEnabled()
        page.show()
    else:
        page._reset_draft()
        page.draft = draft(row(version_raw="v3"))
        page.select_row(1)
    assert not page.review_request.busy

    second = replace_loader(page, existing_reference(version="v3"))
    assert page.revalidate()
    wait_until(second.started.is_set)
    second.release.set()
    wait_until(lambda: not page.busy)
    latest_issues = page.issues
    first.release.set()
    assert QThreadPool.globalInstance().waitForDone(2000)
    QTest.qWait(20)
    assert page.reference is second.value and page.issues == latest_issues
    assert page.save_button.isEnabled()


def test_closing_page_cancels_result_without_waiting_for_the_db(context):
    create, _ = context
    page, slow = create()
    page._changed()
    wait_until(slow.started.is_set)
    started = monotonic()
    page.close()
    assert monotonic() - started < 0.5
    assert not page.review_request.busy and page.reference is None
    slow.release.set()
    assert QThreadPool.globalInstance().waitForDone(2000)
    QTest.qWait(20)
    assert page.reference is None and not page.save_button.isEnabled()


def test_deleted_page_does_not_receive_worker_completion(context):
    create, _ = context
    page, slow = create()
    completed = []
    page.review_request.completed.connect(lambda *_: completed.append(True))
    page._changed()
    wait_until(slow.started.is_set)
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(page)
    slow.release.set()
    assert QThreadPool.globalInstance().waitForDone(2000)
    QTest.qWait(20)
    assert completed == []
