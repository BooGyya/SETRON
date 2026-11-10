"""가져오기 초기화·단계 간격·실시간 대기 막대. 합성 자료만 사용한다."""

from datetime import date
from threading import Event

import pytest
from PySide6.QtCore import QAbstractAnimation, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.service import MemoryImportSaver
from setron.ui.import_dialogs import ImportBusyBar, ImportProgressDialog
from setron.ui.import_page import ImportPage
from setron.ui.import_widgets import ImportSteps
from setron.ui.import_demo import example_extractor
from test_ui_import import CountingExcel, extracted, wait_for_extraction
from test_ui_import_progress import page_with_draft
from test_ui_import_ver5 import catalog, own, until  # noqa: F401


@pytest.mark.parametrize("width", [1000, 700, 340])
def test_step_numbers_and_captions_stay_together_at_every_stage(own, width):
    steps = own(ImportSteps())
    steps.resize(width, 200)
    for stage in range(5):
        steps.set_stage(stage)
        QTest.qWait(10)
        for marker, caption, complete in zip(steps.markers, steps.captions, steps.completions):
            assert 0 <= caption.x() - (marker.x() + marker.width()) <= 6
            assert caption.width() >= caption.sizeHint().width()
            if complete.isVisibleTo(steps):
                assert 0 <= complete.x() - (caption.x() + caption.width()) <= 6
                assert complete.width() >= complete.sizeHint().width()


def test_reset_cancel_preserves_loaded_data_and_edits(own):
    page = own(ImportPage(excel=CountingExcel(), extractor=example_extractor))
    extracted(page)
    page.select_row(page.draft.rows[0].row_id)
    page.edit_cell("site", "수정 중인 사업장")
    original = (page.draft, page.snapshot, page.workbook_id, page.generation)
    calls = []
    page.reset_confirm = lambda text: calls.append(text) or False
    assert not page.reset_import()
    assert (page.draft, page.snapshot, page.workbook_id, page.generation) == original
    assert page.draft.rows[0].value("site") == "수정 중인 사업장"
    assert "미저장" in calls[0] and "원본 Excel과 이미 저장된 데이터는 변경하지 않습니다" in calls[0]


@pytest.mark.parametrize("saved", [False, True])
def test_reset_clears_only_import_session_without_io_or_stored_data_changes(own, saved):
    excel, saver = CountingExcel(), MemoryImportSaver()
    page = own(ImportPage(excel=excel, extractor=example_extractor, saver=saver,
                          confirm=lambda _: True, reset_confirm=lambda _: True))
    page.common_date.setText("2026-10-05")
    extracted(page)
    if saved:
        assert page.save()
        wait_for_extraction(page)
        assert page.state == "idle" and page.draft is None and len(saver.saved) == 1   # 저장 뒤 1단계로 돌아간다
        extracted(page)
    page.configure_choices(dates=(date(2026, 10, 5),), families=("C4", "O2"), sites=("화성", "평택"))
    page.common_date.setText("2026-10-05")
    page.common_site.setText("화성")
    page.common_area.setCurrentIndex(1)
    page.common_overwrite.setChecked(True)
    page.release_family.setCurrentText("C4")
    page.deletion.set_catalog(catalog())
    deletion_catalog = page.deletion.catalog
    original, snapshot = page.draft, page.snapshot
    original_rows, original_cells = original.rows, dict(snapshot.cells)
    stored, calls = tuple(saver.saved), tuple(excel.calls)
    assert page.reset_import()
    assert page.state == "idle" and page.kind is ExtractKind.EQUIPMENT_STATUS
    assert page.workbooks == () and page.workbook_name is None and page.workbook_id is None
    assert page.snapshot is None and page.draft is None and page.receipt is None and page.request_id is None
    assert page.selected_row_id is None and page.issues == () and not page.reviewed_issues
    assert page.workbook_combo.count() == page.sheet_combo.count() == 0
    assert page.grid_model.rowCount() == page.row_model.rowCount() == page.issue_model.rowCount() == 0
    assert not page.common_date.text() and not page.common_site.text() and not page.release_family.currentText()
    assert page.common_area.currentIndex() == 0 and not page.common_overwrite.isChecked()
    assert page.common_site_combo.findText("평택") >= 0 and page.release_family.findText("C4") >= 0
    assert page.available_dates == (date(2026, 10, 5),) and page.deletion.catalog is deletion_catalog
    assert page.progress_steps.stage == 0 and page.source_badge.text() == "AI 추출 전"
    assert not page.read_button.isEnabled() and not page.extract_button.isEnabled() and not page.save_button.isEnabled()
    assert not page.saved_banner.isVisibleTo(page) and not page.saved_caption.text()
    assert not page.progress_dialog.isVisible() and page.import_scroll.verticalScrollBar().value() == 0
    assert tuple(excel.calls) == calls and tuple(saver.saved) == stored
    assert original.rows == original_rows and dict(snapshot.cells) == original_cells
    assert page.load_workbooks()  # 초기화 후 새 가져오기를 시작할 수 있다.
    wait_for_extraction(page)
    assert page.workbook_combo.count() == 2


def test_reset_confirmation_uses_korean_actions_and_cancel_default(own):
    page = page_with_draft(own)
    def check_dialog():
        dialog = QApplication.activeModalWidget()
        assert dialog.windowTitle() == "가져오기 초기화"
        assert dialog.cancel_button.isDefault() and dialog.save_button.text() == "확인"
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    QTimer.singleShot(0, check_dialog)
    assert not page.reset_import()
    assert page.draft is not None


@pytest.mark.parametrize("state", ["listing", "reading", "extracting", "validating", "saving", "unknown"])
def test_reset_is_blocked_during_work_and_uncertain_save(own, state):
    page = page_with_draft(own)
    original = page.draft
    page.state = state
    page._render()
    page.reset_confirm = lambda _: pytest.fail("처리 중이거나 미확인 결과의 초기화는 확인창도 열지 않는다.")
    assert not page.reset_button.isEnabled() and not page.reset_import()
    assert page.draft is original
    page.state = "idle"  # 합성 상태를 복구하여 fixture의 정상 종료 허용.
    page._render()


def test_reset_rejects_stale_confirmation_and_is_hidden_on_delete_tab(own):
    page = page_with_draft(own)
    original = page.draft
    def changed(_):
        page.generation += 1
        return True
    page.reset_confirm = changed
    assert not page.reset_import() and page.draft is original
    page.deletion.set_catalog(catalog())
    page.tabs.setCurrentIndex(1)
    assert not page.reset_button.isVisibleTo(page) and not page.reset_import()
    page.tabs.setCurrentIndex(0)
    assert page.reset_button.isVisibleTo(page)
    page.deletion.phase = "deleting"
    page._sync_tabs()
    assert not page.reset_button.isEnabled() and not page.reset_import()
    page.deletion.phase = "idle"


def test_animated_busy_bar_changes_pixels_and_stops_when_hidden_or_finished(own):
    dialog = own(ImportProgressDialog())
    dialog.set_operation("read", "시트 읽는 중", "합성 시트 값 읽기")
    bar = dialog.progress
    assert isinstance(bar, ImportBusyBar) and bar.minimum() == bar.maximum() == 0
    assert bar.animation.state() == QAbstractAnimation.State.Running
    until(lambda: 0.2 < bar.phase < 0.35)
    first = bar.grab().toImage()
    QTest.qWait(130)
    assert first != bar.grab().toImage()  # 타이머 변수뿐 아니라 실제 표시도 움직인다.
    before = bar.animation.currentTime()
    dialog.set_operation("read", "시트 읽는 중", "합성 시트 값 읽기")
    assert bar.animation.currentTime() >= before  # 렌더마다 처음으로 되돌리지 않는다.
    dialog.hide()
    assert bar.animation.state() == QAbstractAnimation.State.Stopped
    dialog.set_operation("ai", "AI 추출 중", "합성 추출")
    assert bar.animation.state() == QAbstractAnimation.State.Running
    dialog.reject()
    assert dialog.isVisible() and bar.animation.state() == QAbstractAnimation.State.Running
    dialog.hide()
    assert bar.animation.state() == QAbstractAnimation.State.Stopped
    dialog.set_operation("ai", visible=False)
    assert not dialog.isVisible() and bar.animation.state() == QAbstractAnimation.State.Stopped
    dialog.set_operation("save", "변경분 저장 중", "합성 저장")
    assert bar.animation.state() == QAbstractAnimation.State.Running
    dialog.set_operation(None)
    assert not dialog.isVisible() and bar.animation.state() == QAbstractAnimation.State.Stopped
    assert bar.maximum() == 1 and not bar.isTextVisible()  # 진행률 %를 만들지 않는다.


def test_busy_bar_respects_application_reduced_motion_setting(own):
    app = QApplication.instance()
    previous = app.property("reducedMotion")
    app.setProperty("reducedMotion", True)
    try:
        bar = own(ImportBusyBar())
        assert bar.animation.state() == QAbstractAnimation.State.Stopped
        assert bar.phase == 0.5 and "진행률 미제공" in bar.accessibleName()
    finally:
        app.setProperty("reducedMotion", previous)


def test_busy_bar_keeps_moving_while_slow_worker_is_running(own):
    gate = Event()
    excel = CountingExcel()
    original = excel.list_open_workbooks
    def slow():
        assert gate.wait(5)
        return original()
    excel.list_open_workbooks = slow
    page = own(ImportPage(excel=excel))
    try:
        assert page.load_workbooks()
        until(lambda: page.progress_dialog.progress.phase > 0.15)
        first = page.progress_dialog.progress.phase
        QTest.qWait(110)
        assert page.progress_dialog.progress.phase != first
        assert not page.reset_import()
    finally:
        gate.set()
        wait_for_extraction(page)
    assert not page.progress_dialog.isVisible()
