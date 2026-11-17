"""진행 표시·둥근 확인창·워커 읽기/저장. 실제 Excel·회사 AI·DB는 호출하지 않는다."""

from datetime import datetime
from threading import Event, get_ident
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QPushButton

from setron.features.excel_import.schemas import SaveReceipt
from setron.features.excel_import.service import MemoryImportSaver
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseSaveError
from setron.ui.import_demo import DemoExcel, example_extractor
from setron.ui.import_dialogs import ImportChoiceDialog, ImportConfirmDialog, ImportProgressDialog
from setron.ui.import_page import ImportPage
from setron.ui.import_widgets import ImportSteps
from setron.ui import import_tasks
from test_import_validation import draft, row
from test_ui_import import wait_for_extraction
from test_ui_import_ver5 import own, until  # noqa: F401


def page_with_draft(own, saver=None):
    page = own(ImportPage(excel=DemoExcel(), saver=saver, confirm=lambda _: True))
    page.draft = draft(row())
    page.workbook_name = page.draft.workbook_name
    page.select_row(1)
    page._changed()
    return page


def test_step_numbers_and_korean_captions_remain_after_completion(own):
    steps = own(ImportSteps())
    steps.resize(1000, 44)
    for stage in range(5):
        steps.set_stage(stage)
        assert [v.text() for v in steps.markers] == ["1", "2", "3", "4"]
        assert [v.text() for v in steps.captions] == list(steps.TITLES)
        for index, caption in enumerate(steps.captions):
            assert all(QFontMetrics(caption.font()).inFontUcs4(ord(c)) for c in caption.text())
            assert not steps.completions[index].isVisibleTo(steps)   # '완료' 글자 없이 색으로만 구분한다
            assert str(index + 1) in steps.buttons[index].accessibleName()


@pytest.mark.parametrize("operation", ["list", "sheet"])
def test_slow_excel_task_displays_progress_and_keeps_ui_responsive(own, operation):
    started, gate, threads, ticks = Event(), Event(), [], []
    excel = DemoExcel()
    page = own(ImportPage(excel=excel))
    if operation == "sheet":
        assert page.load_workbooks()
        wait_for_extraction(page)
    method = excel.list_open_workbooks if operation == "list" else excel.read_sheet
    def slow(*args, **kwargs):
        threads.append(get_ident())
        started.set()
        assert gate.wait(5)
        return method(*args, **kwargs)
    setattr(excel, "list_open_workbooks" if operation == "list" else "read_sheet", slow)
    try:
        assert page.load_workbooks() if operation == "list" else page.read_selected()
        until(started.is_set)
        assert page.progress_dialog.isVisible() and page.busy
        assert ("통합문서" if operation == "list" else "시트 읽는 중") in page.progress_dialog.heading.text()
        assert threads[0] != get_ident()
        assert not page.load_workbooks() and not page.read_selected()
        QTimer.singleShot(0, lambda: ticks.append(True))
        QTest.qWait(20)
        assert ticks == [True]
        page.progress_dialog.reject()
        assert page.progress_dialog.isVisible()  # 읽기 중 ESC로 처리가 취소된 것처럼 보이지 않는다.
    finally:
        gate.set()
        wait_for_extraction(page)
    assert not page.progress_dialog.isVisible()
    assert page.workbook_combo.count() == 2
    if operation == "sheet":
        assert page.snapshot is not None


def test_ai_badge_and_button_free_popup_follow_actual_extraction_until_completion(own):
    gate = Event()
    def slow(*args):
        assert gate.wait(5)
        return example_extractor(*args)
    page = own(ImportPage(excel=DemoExcel(), extractor=slow))
    page.load_workbooks()
    wait_for_extraction(page)
    page.read_selected()
    wait_for_extraction(page)
    assert page.source_badge.text() == "AI 추출 전"
    try:
        assert page.extract()
        assert page.source_badge.text() == "AI 추출 중"
        assert "추출 중" in page.row_empty.text()
        assert page.progress_dialog.isVisible()
        assert not page.progress_dialog.findChildren(QPushButton)
        assert "다른 화면" not in page.progress_dialog.hint.text()
        page.progress_dialog.reject()
        assert page.progress_dialog.isVisible() and page.state == "extracting"
        page._render()
        assert page.progress_dialog.isVisible()
    finally:
        gate.set()
        wait_for_extraction(page)
    assert page.source_badge.text() == "추출 완료" and page.source_badge.tone == "warning"
    assert "EXAMPLE" in page.source_badge.toolTip()  # 예제를 실제 AI로 표시하지 않는다.
    assert not page.progress_dialog.isVisible()


@pytest.mark.parametrize("failure", [None, DatabaseSaveError("합성 저장 실패"),
                                     DatabaseCommitUnknownError("합성 결과 미확인")])
def test_slow_save_progress_closes_for_all_outcomes_and_never_duplicates(own, failure):
    gate, started, ticks = Event(), Event(), []
    class Saver(MemoryImportSaver):
        def save(self, request):
            started.set()
            assert gate.wait(5)
            return super().save(request)
    saver = Saver(failures=(failure,) if failure else ())
    page = page_with_draft(own, saver)
    original = page.draft
    try:
        assert page.save()
        until(started.is_set)
        assert page.progress_dialog.isVisible() and "저장 중" in page.progress_dialog.heading.text()
        assert not page.progress_dialog.findChildren(QPushButton)
        assert not page.save() and not page.read_selected()
        assert page._reset_draft() is False and page.draft is original
        assert not page.close()
        QTest.keyClick(page.progress_dialog, Qt.Key.Key_Escape)
        assert page.progress_dialog.isVisible()
        QTimer.singleShot(0, lambda: ticks.append(True))
        QTest.qWait(20)
        assert ticks == [True]
    finally:
        gate.set()
        wait_for_extraction(page)
    assert not page.progress_dialog.isVisible()
    # 저장에 성공하면 1단계로 돌아간다 (11-10 2차 1-2).
    assert page.state == ("idle" if failure is None else "unknown" if isinstance(failure, DatabaseCommitUnknownError) else "failed")
    assert len(saver.saved) == (1 if failure is None else 0)
    assert page.save_button.isEnabled() == (isinstance(failure, DatabaseSaveError)
                                            and not isinstance(failure, DatabaseCommitUnknownError))


@pytest.mark.parametrize("state", ["saved", "unknown"])
def test_refreshing_workbook_list_does_not_unlock_saved_or_uncertain_draft(own, state):
    page = page_with_draft(own)
    page.state = state
    if state == "saved":
        page.receipt = SaveReceipt(1, 1, datetime.now(), "합성 저장소")
    page._render()
    assert page.load_workbooks()
    wait_for_extraction(page)
    assert page.state == state and page.locked and not page.save_button.isEnabled()
    assert not page.save()


def test_unknown_receipt_does_not_show_success_or_allow_retry(own):
    page = page_with_draft(own, SimpleNamespace(target_label="합성", save=lambda request: {}))
    assert page.save()
    wait_for_extraction(page)
    assert page.state == "unknown" and page.receipt is None and not page.save_button.isEnabled()
    assert not page.progress_dialog.isVisible()


def test_closing_before_scheduled_sheet_read_prevents_provider_call(own):
    excel = DemoExcel()
    page = own(ImportPage(excel=excel))
    page.load_workbooks()
    wait_for_extraction(page)
    calls = []
    excel.read_sheet = lambda *args, **kwargs: calls.append(args)
    assert page.read_selected()
    page.close()
    QTest.qWait(30)
    assert calls == [] and not page.busy and not page.progress_dialog.isVisible()


def test_closed_page_ignores_late_excel_read_result(own):
    gate, started = Event(), Event()
    excel = DemoExcel()
    original = excel.read_sheet
    def slow(*args, **kwargs):
        started.set()
        assert gate.wait(5)
        return original(*args, **kwargs)
    page = own(ImportPage(excel=excel))
    page.load_workbooks()
    wait_for_extraction(page)
    excel.read_sheet = slow
    try:
        page.read_selected()
        until(started.is_set)
        page.close()
    finally:
        gate.set()
    QTest.qWait(80)
    assert page.snapshot is None and not page.busy


def test_confirmation_and_choice_dialogs_are_korean_scrollable_and_cancel_by_default(own):
    dialog = own(ImportConfirmDialog("원본 파일 확인\n" + "전체 누적 노트인지 확인하세요.\n" * 100))
    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert dialog.cancel_button.text() == "취소" and dialog.cancel_button.isDefault()
    assert dialog.save_button.text() == "저장" and not dialog.save_button.autoDefault()
    assert dialog.height() < dialog.screen().availableGeometry().height()
    assert dialog.message.scroll_area.verticalScrollBar().maximum() > 0
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.result() == QDialog.DialogCode.Rejected
    choice = own(ImportChoiceDialog("같은 항목인지 확인", "계열 C4 · 새 항목 v4.56.6.41_e",
                                   ["기존 항목과 다른 새 항목", "v4.56.6.41_e (ID 2)"]))
    assert choice.choices.currentIndex() == 0 and choice.cancel_button.isDefault()
    choice.choices.setCurrentIndex(1)
    choice.confirm_button.click()
    assert choice.result() == QDialog.DialogCode.Accepted
    assert choice.choices.currentText() == "v4.56.6.41_e (ID 2)"


def test_excel_worker_initializes_and_releases_com_even_on_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(import_tasks, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(__import__("sys").modules, "pythoncom", SimpleNamespace(
        CoInitialize=lambda: calls.append("init"), CoUninitialize=lambda: calls.append("uninit")))
    monkeypatch.setattr(import_tasks, "_com_backend", lambda: SimpleNamespace(N_COM_ATTEMPTS=0))
    def fail():
        calls.append("read")
        raise ValueError("합성 읽기 실패")
    with pytest.raises(ValueError):
        import_tasks.excel_task(fail, com=True)
    assert calls == ["init", "read", "uninit"]
