"""진행률은 GUI 스레드에서 표시하고 취소한 추출의 늦은 응답은 반영하지 않는다."""

from threading import Event, get_ident
from time import monotonic

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest

from setron.features.ai.schemas import AIResult, AIStatus, AITask
from setron.features.extraction.schemas import ExtractionProgress
from setron.features.ai.service import extract_sheet
from setron.infrastructure.ai_client import ExampleChatClient
from setron.ui.import_demo import DemoExcel
from setron.ui.import_page import ImportPage
from test_ui_import import app  # noqa: F401


def wait_until(predicate):
    end = monotonic() + 5
    while not predicate() and monotonic() < end:
        QTest.qWait(10)
    assert predicate()


def test_progress_and_explicit_cancel_do_not_accept_late_updates(app):
    started, released, cancelled_seen = Event(), Event(), Event()

    def extract(kind, sheet, *, progress, cancelled):
        progress(ExtractionProgress(1, 27))
        started.set()
        while not released.wait(0.01) and not cancelled():
            pass
        if cancelled():
            cancelled_seen.set()
        progress(ExtractionProgress(2, 27))
        return AIResult(AITask.EXCEL_EXTRACT, AIStatus.FAILED, "늦은 시험 응답")

    page = ImportPage(excel=DemoExcel(), sheet_extractor=extract)
    page.show()
    threads = []
    page.extraction_request.progressed.connect(lambda _: threads.append(get_ident()))
    try:
        assert page.load_workbooks() and page.read_selected()
        wait_until(lambda: not page.busy)
        assert page.extract()
        wait_until(lambda: started.is_set() and "1/27" in page.status.text())
        assert page.cancel_extract_button.isVisible()
        assert "1/27" in page.extraction_feedback.text()
        assert page.source_badge.text() == "AI 추출 중"
        assert "기다리고" in page.row_empty.text()
        assert threads == [get_ident()]
        page.cancel_extract_button.click()
        wait_until(cancelled_seen.is_set)
        assert page.state == "idle" and page.draft is None
        assert "취소" in page.status.text()
        assert "취소" in page.source_badge.text() and "취소" in page.extraction_feedback.text()
        QTest.qWait(30)
        assert "취소" in page.status.text() and "늦은" not in page.notice.text()
        assert not page.cancel_extract_button.isVisible()
    finally:
        released.set()
        QThreadPool.globalInstance().waitForDone(3000)
        page.close()
        page.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("failure", ["invalid_json", "exception"])
def test_failed_extraction_explains_empty_results_in_review_area(app, failure):
    def extract(kind, sheet, **options):
        if failure == "exception":
            raise RuntimeError("사용자에게 노출하지 않을 내부 오류")
        return extract_sheet(ExampleChatClient(['{"rows": [{"values": {}, "sources": {"version_raw": null}}]}']),
                             kind, sheet.sheet_name, [])

    page = ImportPage(excel=DemoExcel(), sheet_extractor=extract)
    page.show()
    try:
        assert page.load_workbooks() and page.read_selected()
        wait_until(lambda: not page.busy)
        assert page.extract()
        wait_until(lambda: not page.busy)
        assert page.draft is None and page.row_model.rowCount() == 0
        assert page.source_badge.text() == "AI 추출 실패"
        assert "AI 추출 실패" in page.extraction_feedback.text()
        assert "위 안내" in page.row_empty.text()
        assert not page.save_button.isEnabled()
        assert page.extract_button.isEnabled()
        if failure == "invalid_json":
            assert "출처는 셀 주소 문자열" in page.extraction_feedback.text()
        else:
            assert "내부 오류" not in page.extraction_feedback.text()
    finally:
        QThreadPool.globalInstance().waitForDone(3000)
        page.close()
        page.deleteLater()
        app.processEvents()
