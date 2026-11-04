"""가져오기 DB 저장 중에도 GUI가 응답하고 저장 결과에 맞게 재시도를 제어한다."""

from threading import Event
from time import monotonic

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest

from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseSaveError
from setron.features.ai.schemas import ExtractKind
from setron.ui.import_page import ImportPage
from setron.ui.app import create_window
from test_import_validation import draft, row, family, reference
from test_ui_import import app  # noqa: F401
from test_ui_import_db_review import ReviewingDatabaseSaver, existing_reference


def wait_until(predicate):
    end = monotonic() + 5
    while not predicate() and monotonic() < end:
        QTest.qWait(10)
    assert predicate()


class SlowSaver(ReviewingDatabaseSaver):
    def __init__(self, error=None):
        super().__init__(existing_reference())
        self.background_save = True
        self.started, self.release = Event(), Event()
        self.error = error

    def save(self, request):
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError("검증용 저장 대기 시간 초과")
        if self.error is not None:
            raise self.error
        return super().save(request)


@pytest.mark.parametrize("error, expected", [
    (None, "saved"),
    (DatabaseSaveError("이번 저장 묶음 전체 취소"), "failed"),
    (DatabaseCommitUnknownError("최종 저장 결과 미확인"), "unknown"),
])
def test_background_save_keeps_gui_responsive_and_blocks_close(app, error, expected):
    saver = SlowSaver(error)
    window = create_window(import_saver=saver)
    window.show()
    window.navigate("import")
    page = window.pages["import"]
    page.confirm = lambda text: True
    saved, ticks = [], []
    page.saved.connect(saved.append)
    try:
        page.draft = draft(row())
        page._changed(initial=True)
        wait_until(lambda: not page.busy)
        assert page.save_button.isEnabled()
        assert page.save()
        wait_until(saver.started.is_set)
        QTimer.singleShot(0, lambda: ticks.append(True))
        QTest.qWait(20)
        assert ticks and page.state == "saving"
        assert not page.save() and not page.read_button.isEnabled()
        assert not window.close() and window.isVisible()
        assert not page.close() and page.isVisible()
        saver.release.set()
        wait_until(lambda: page.state != "saving")
        assert page.state == expected
        assert len(saved) == (1 if error is None else 0)
        assert page.save_button.isEnabled() == (expected == "failed")
        if expected == "saved":
            assert page.requery_button.text() == "저장 자료 조회"
            assert "저장 자료 조회" in page.result_label.text()
        elif expected == "unknown":
            assert not page.save()
    finally:
        saver.release.set()
        wait_until(lambda: not page.save_request.busy)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_release_common_selection_is_explicit_and_preserves_originals(app):
    saver = ReviewingDatabaseSaver(reference(family()))
    page = ImportPage(saver=saver, confirm=lambda text: True)
    page.kind_combo.setCurrentIndex(page.kind_combo.findData(ExtractKind.RELEASE_NOTE.value))
    page.draft = draft(row(1, model_name=None, family_name=None, group_name="CTC", content="변경 1", item_no="1"),
                       row(2, model_name=None, family_name=None, group_name=None, content="변경 2", item_no="2"),
                       kind=ExtractKind.RELEASE_NOTE)
    original = page.draft
    try:
        page._changed(initial=True)
        wait_until(lambda: not page.busy)
        assert page.release_family_combo.currentData() is None
        assert not page.apply_release_common()
        page.release_family_combo.setCurrentIndex(1)
        page.confirm = lambda text: False
        assert not page.apply_release_common() and page.draft == original
        page.confirm = lambda text: True
        assert page.apply_release_common()
        wait_until(lambda: not page.busy)
        for row_value, before in zip(page.draft.rows, original.rows):
            assert row_value.value("model_name") == "M-O2"
            assert row_value.value("family_name") == "O2"
            assert row_value.value("group_name") == "OXIDE"
            assert row_value.original == before.original
            assert row_value.sources == before.sources
            assert "model_name" in row_value.edited_fields
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()
