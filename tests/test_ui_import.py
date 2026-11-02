"""C-1~C-3 Qt: 사용자 요청 시에만 Excel 연결, 원본 미리보기, 검토·수정, 확인 후 저장과 실패·중복 방지."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.ai.examples import example_client
from setron.features.ai.schemas import AITask, ExtractKind
from setron.features.ai.service import extract_sheet
from setron.features.excel_import.service import MemoryImportSaver
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseSaveError
from setron.infrastructure.excel_reader import ExcelNotRunningError, NOT_RUNNING_MESSAGE
from setron.ui.app import create_window
from setron.ui.import_demo import EQUIPMENT_BOOK, RELEASE_BOOK, DemoExcel
from setron.ui.theme import install_theme


class CountingExcel(DemoExcel):
    def __init__(self):
        self.calls = []

    def list_open_workbooks(self):
        self.calls.append("list")
        return super().list_open_workbooks()

    def read_sheet(self, workbook_name, sheet_name):
        self.calls.append(("read", workbook_name, sheet_name))
        return super().read_sheet(workbook_name, sheet_name)


@pytest.fixture
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


@pytest.fixture
def saver():
    return MemoryImportSaver("테스트 메모리 저장소")


@pytest.fixture
def excel():
    return CountingExcel()


@pytest.fixture
def window(app, excel, saver):
    result = create_window(excel=excel, import_saver=saver)
    result.resize(1600, 960)
    result.show()
    result.navigate("import")
    QTest.qWait(20)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


@pytest.fixture
def page(window):
    result = window.pages["import"]
    result.confirms = []
    result.confirm = lambda text: result.confirms.append(text) or True
    return result


def read(page, book=EQUIPMENT_BOOK, sheet="Memory"):
    page.workbook_combo.setCurrentIndex(page.workbook_combo.findData(book))
    page.sheet_combo.setCurrentIndex(page.sheet_combo.findData(sheet))
    assert page.read_selected()
    QTest.qWait(10)


def extracted(page, **kwargs):
    page.load_workbooks()
    read(page, **kwargs)
    assert page.extract()
    QTest.qWait(10)
    assert page.draft is not None
    return page.draft


def test_menu_enabled_but_excel_is_connected_only_on_request(window, excel, page):
    assert window.nav["import"].isEnabled() and window.route == "import"
    assert not window.query_panel.isVisible()
    assert excel.calls == []
    assert not page.read_button.isEnabled() and not page.extract_button.isEnabled()
    page.list_button.click()
    assert excel.calls == ["list"] and page.workbook_combo.count() == 2


def test_excel_not_running_is_reported_without_preview(window, page):
    def not_running():
        raise ExcelNotRunningError(NOT_RUNNING_MESSAGE)
    page.excel.list_open_workbooks = not_running
    assert not page.load_workbooks()
    assert page.status.tone == "danger" and page.notice.text() == NOT_RUNNING_MESSAGE
    assert not page.read_button.isEnabled() and page.snapshot is None


def test_preview_shows_raw_cells_addresses_and_merged_ranges(page):
    page.load_workbooks()
    read(page)
    model = page.grid_model
    assert model.headerData(5, Qt.Orientation.Horizontal) == "F" and model.headerData(4, Qt.Orientation.Vertical) == "5"
    assert model.index(4, 5).data() == "v4.56.6.43" and model.index(4, 0).data() == "1"
    assert page.grid.columnSpan(1, 0) == 6 and page.grid.columnSpan(0, 0) == 2
    page._show_cell(model.index(4, 2))
    assert page.cell_label.text() == "선택 셀 C5 · 원본값 ETCH-001"
    assert "A1:F5" in page.preview_info.text() and "병합 2개" in page.preview_info.text()


def test_example_extract_is_labelled_and_blocked_until_reviewed(page, saver):
    draft = extracted(page)
    assert "EXAMPLE" in page.source_badge.text() and "실제 AI 결과 아님" in page.source_badge.text()
    assert not page.save_button.isEnabled() and "오류 1건" in page.save_summary.text()
    assert any("원본 셀 위치가 없습니다" in row[4] for row in page.issue_model.rows)
    assert not page.save()
    page.inputs["base_date"].setText("2026-10-21")
    assert page.apply_edits()
    assert page.draft.row(1).original.get("base_date") is None  # AI 원본은 바뀌지 않는다
    assert page.draft.row(1).edited_fields == ("base_date",)
    assert "수정됨" in page.row_model.rows[0][1] and "AI: 빈 값" in page.row_model.rows[0][2]
    assert page.save_button.isEnabled() and draft.row(1).edits == {}
    assert saver.saved == []


def test_cancelled_confirmation_saves_nothing(page, saver):
    extracted(page)
    page.inputs["base_date"].setText("2026-10-21")
    page.apply_edits()
    page.confirm = lambda text: False
    assert not page.save()
    QTest.qWait(10)
    assert saver.saved == [] and page.state == "idle" and page.save_button.isEnabled()


def test_confirmed_save_shows_saving_then_success_and_prevents_duplicates(window, page, saver):
    extracted(page)
    page.inputs["base_date"].setText("2026-10-21")
    page.apply_edits()
    query, targets = window.query_state.applied, window.query_state.target_draft
    assert page.save()
    assert EQUIPMENT_BOOK in page.confirms[0] and "Memory" in page.confirms[0] and "테스트 메모리 저장소" in page.confirms[0]
    assert page.state == "saving" and page.status.text() == "저장 중"
    assert not page.save_button.isEnabled() and not page.read_button.isEnabled() and not page.save()
    QTest.qWait(10)
    assert page.state == "saved" and page.status.tone == "success"
    assert len(saver.saved) == 1 and saver.saved[0].rows[0].edited_fields == ("base_date",)
    assert EQUIPMENT_BOOK not in str(saver.saved[0].raw_data)
    assert "조회 적용" in page.result_label.text()
    assert not page.save_button.isEnabled() and not page.save() and not page.apply_edit_button.isEnabled()
    assert all(field.isReadOnly() for field in page.inputs.values())
    assert len(saver.saved) == 1
    # 저장으로 조회 결과·목표 N이 바뀌지 않고, 이동 버튼은 이동만 한다.
    assert page.requery_button.isVisible()
    page.requery_button.click()
    assert window.route == "dashboard" and window.query_state.applied is query
    assert window.query_state.target_draft == targets


def test_failed_save_keeps_draft_and_retry_uses_same_request(page, saver):
    saver.failures.append(DatabaseSaveError("데이터 저장에 실패해 이번 저장 묶음을 취소했습니다."))
    extracted(page)
    page.inputs["base_date"].setText("2026-10-21")
    page.apply_edits()
    request_id = page.request_id
    page.save()
    QTest.qWait(10)
    assert page.state == "failed" and page.status.tone == "danger" and "기존 자료는 그대로" in page.result_label.text()
    assert page.save_button.isEnabled() and page.draft.row(1).value("base_date") == "2026-10-21"
    page.save()
    QTest.qWait(10)
    assert page.state == "saved" and [r.request_id for r in saver.saved] == [request_id]


def test_unknown_commit_result_locks_retry(page, saver):
    saver.failures.append(DatabaseCommitUnknownError("최종 저장 중 DB 연결이 끊겨 저장 결과를 확인할 수 없습니다."))
    extracted(page)
    page.inputs["base_date"].setText("2026-10-21")
    page.apply_edits()
    page.save()
    QTest.qWait(10)
    assert page.state == "unknown" and page.status.tone == "warning"
    assert not page.save_button.isEnabled() and not page.save() and not page.extract_button.isEnabled()
    assert saver.saved == []


def test_excluding_all_rows_blocks_save(page):
    extracted(page)
    page.toggle_excluded()
    assert page.draft.row(1).excluded and page.row_model.rows[0][1] == "제외"
    assert "저장할 행이 없습니다" in " ".join(row[4] for row in page.issue_model.rows)
    assert not page.save_button.isEnabled()
    page.toggle_excluded()
    assert not page.draft.row(1).excluded


def test_issue_selects_row_and_highlights_source_cell(page):
    extracted(page)
    page.selected_row_id = None
    page.open_issue(next(i for i, issue in enumerate(page.issues) if issue.row_id == 1 and issue.field == "base_date"))
    assert page.selected_row_id == 1 and not page.editor.isHidden()
    page.kind_combo.setCurrentIndex(page.kind_combo.findData(ExtractKind.RELEASE_NOTE.value))
    extracted(page, book=RELEASE_BOOK, sheet="Release")
    page.open_issue(next(i for i, issue in enumerate(page.issues) if issue.address == "E3"))
    assert page.grid_model.highlight == "E3" and "원본값 C4, C4 Plus" in page.cell_label.text()
    assert not page.highlight_cell("Z999") and page.grid_model.highlight == "E3"


def test_release_note_flow_saves_items_without_equipment_fields(page, saver):
    page.kind_combo.setCurrentIndex(page.kind_combo.findData(ExtractKind.RELEASE_NOTE.value))
    extracted(page, book=RELEASE_BOOK, sheet="Release")
    assert page.row_model.headings[2:] == (
        "SW 버전 원문", "출시일", "Item 번호", "변경 내용", "대상 모델",
        "설비군", "모델명", "SW 계열", "UI 구분", "구조 구분", "개조 정보", "개조 적용 표식",
    )
    assert "설비 호기" not in page.row_model.headings and "기준일" not in page.row_model.headings
    assert page.save_button.isEnabled()
    page.save()
    QTest.qWait(10)
    assert [r.values["item_no"] for r in saver.saved[0].rows] == [1, 2]
    assert saver.saved[0].rows[0].source_reference["content"] == {"sheet": "Release", "cells": "D2"}


def test_kind_change_discards_draft_and_inputs_lock_while_extracting(page):
    extracted(page)
    page.kind_combo.setCurrentIndex(page.kind_combo.findData(ExtractKind.RELEASE_NOTE.value))
    assert page.draft is None and page.status.tone == "warning"
    page.kind_combo.setCurrentIndex(page.kind_combo.findData(ExtractKind.EQUIPMENT_STATUS.value))
    assert page.extract()
    # 결과가 오기 전에는 다른 시트를 읽거나 종류를 바꿔 결과가 섞이지 않게 한다.
    assert not page.read_selected() and not page.extract() and not page.kind_combo.isEnabled()
    QTest.qWait(10)
    assert page.draft is not None and page.kind_combo.isEnabled()
    read(page, RELEASE_BOOK, "Release")
    assert page.draft is None and page.snapshot.sheet_name == "Release"


def test_ai_failure_shows_message_and_no_draft(page):
    page.extractor = lambda kind, name, cells: extract_sheet(
        example_client(AITask.EXCEL_EXTRACT, "rate_limit"), kind, name, cells)
    page.load_workbooks()
    read(page)
    page.extract()
    QTest.qWait(10)
    assert page.draft is None and page.status.tone == "danger" and "호출 제한" in page.notice.text()
    assert page.extract_button.isEnabled()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
def test_responsive_without_main_horizontal_scroll(window, page, size):
    extracted(page)
    window.resize(*size)
    QTest.qWait(60)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert page.row_table.width() <= page.width() and page.grid.width() <= page.width()
