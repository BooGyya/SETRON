"""삭제 화면 전용 고정 단계 탭. 이동은 외부 조회·삭제나 완료 처리를 하지 않는다."""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.service import MemoryImportSaver
from setron.ui.app import create_window
from setron.ui.import_delete import ImportDeletePage
from setron.ui.import_demo import DemoExcel, example_extractor
from setron.ui.import_page import ImportPage
from setron.ui.import_widgets import ImportSteps
from test_ui_import import CountingExcel, extracted
from test_ui_import_ver5 import Gateway, own, select_period  # noqa: F401


TITLES = ("삭제 범위 선택", "삭제 대상 확인", "삭제 영향 확인")


def test_each_workflow_has_its_own_step_bar_without_losing_import_draft(own):
    page = own(ImportPage(excel=DemoExcel(), extractor=example_extractor))
    extracted(page)
    page.edit_cell("version_raw", "수정 중인 버전")
    draft, snapshot = page.draft, page.snapshot
    import_stage = page.progress_steps.stage
    assert page.steps.isVisibleTo(page) and not page.delete_steps.isVisibleTo(page)
    page.tabs.setCurrentIndex(1)
    assert page.delete_steps.isVisibleTo(page) and not page.steps.isVisibleTo(page)
    assert tuple(c.text() for c in page.delete_progress_steps.captions) == TITLES
    assert [m.text() for m in page.delete_progress_steps.markers] == ["1", "2", "3"]
    page.tabs.setCurrentIndex(0)
    assert page.steps.isVisibleTo(page) and not page.delete_steps.isVisibleTo(page)
    assert page.draft is draft and page.snapshot is snapshot
    assert page.draft.row(page.selected_row_id).value("version_raw") == "수정 중인 버전"
    assert page.progress_steps.stage == import_stage


@pytest.mark.parametrize("kind", list(ExtractKind))
def test_unavailable_notice_is_removed_without_faking_empty_database_or_enabling_delete(own, kind):
    page = own(ImportDeletePage())
    page.kind_combo.setCurrentIndex(page.kind_combo.findData(kind.value))
    assert not page.reload()
    assert not page.notice.text() and not page.notice.isVisibleTo(page)
    assert page.empty.text() == "저장된 자료를 조회해 주세요."
    assert page.catalog is None and page.table.rowCount() == 0
    assert not page.selected and "—" in page.counts.text()
    assert not page.delete_button.isEnabled() and not page.backup_button.isEnabled()


def test_step_click_and_keyboard_only_navigate_without_io_or_completion(own):
    excel, saver, gateway = CountingExcel(), MemoryImportSaver(), Gateway()
    page = own(ImportPage(excel=excel, saver=saver, delete_gateway=gateway))
    select_period(page.deletion)
    catalog, selected = page.deletion.catalog, set(page.deletion.selected)
    page.resize(1100, 620)
    page.tabs.setCurrentIndex(1)
    QTest.qWait(30)
    before = tuple(excel.calls), tuple(saver.saved)
    for index in (2, 1, 0):
        item = page.delete_progress_steps.buttons[index]
        item.setFocus()
        QTest.keyClick(item, Qt.Key.Key_Space)
        assert page.delete_progress_steps.stage == index
        assert [b.isChecked() for b in page.delete_progress_steps.buttons] == [i == index for i in range(3)]
        assert "선택됨" in item.accessibleName()
        assert all(not c.isVisibleTo(page) for c in page.delete_progress_steps.completions)
    assert page.delete_scroll.verticalScrollBar().value() == 0
    assert page.deletion.catalog is catalog and page.deletion.selected == selected
    assert page.deletion.phase == "idle" and gateway.calls == []
    assert (tuple(excel.calls), tuple(saver.saved)) == before


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
def test_delete_step_bar_stays_fixed_and_visible_when_content_scrolls(own, size):
    window = own(create_window(excel=DemoExcel()))
    window.resize(*size)
    window.navigate("import")
    page = window.pages["import"]
    page.tabs.setCurrentIndex(1)
    QTest.qWait(60)
    before = page.delete_steps.mapTo(window, QPoint()).y()
    assert page.delete_steps.isVisibleTo(window)
    for index in (2, 1, 0):
        page.delete_progress_steps.buttons[index].click()
        QTest.qWait(10)
        assert page.delete_progress_steps.stage == index
        assert page.delete_steps.mapTo(window, QPoint()).y() == before
        target = page.deletion.step_cards[index]
        top = target.mapTo(page.delete_scroll.viewport(), QPoint()).y()
        assert top < page.delete_scroll.viewport().height() and top + target.height() > 0
    assert page.tab_strip.isVisibleTo(window)
    assert page.mapTo(window, QPoint(0, page.height())).y() <= window.height()


def test_manual_scroll_updates_highlight_without_completing_steps(own):
    page = own(ImportPage(excel=DemoExcel()))
    page.resize(1000, 620)
    page.tabs.setCurrentIndex(1)
    QTest.qWait(30)
    bar = page.delete_scroll.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())
    assert page.delete_progress_steps.stage == 2
    bar.setValue(0)
    assert page.delete_progress_steps.stage == 0
    assert all(not c.isVisibleTo(page) for c in page.delete_progress_steps.completions)


@pytest.mark.parametrize("width", [340, 700, 1000])
def test_delete_step_numbers_and_captions_are_not_clipped(own, width):
    steps = own(ImportSteps(titles=TITLES, show_completion=False))
    steps.resize(width, 160)
    for stage in range(3):
        steps.set_stage(stage)
        QTest.qWait(10)
        for marker, caption, complete in zip(steps.markers, steps.captions, steps.completions):
            assert marker.text() in ("1", "2", "3")
            assert 0 <= caption.x() - marker.geometry().right() - 1 <= 6
            assert caption.width() >= caption.sizeHint().width()
            assert not complete.isVisibleTo(steps)
