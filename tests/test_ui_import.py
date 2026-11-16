"""C-1~C-3 Qt: 사용자 요청 시에만 Excel 연결, 원본 미리보기, 검토·수정, 확인 후 저장과 실패·중복 방지."""

import os
import warnings
from types import SimpleNamespace
from unittest.mock import create_autospec

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.ai.examples import example_client
from setron.features.ai.schemas import AITask, ExtractKind
from setron.features.ai.service import extract_sheet
from setron.features.excel_import.service import MemoryImportSaver, draft_from_review
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseSaveError
from setron.infrastructure import excel_reader
from setron.infrastructure.excel_reader import ExcelNotRunningError, NOT_RUNNING_MESSAGE, SheetData, WorkbookInfo
from setron.ui.app import create_window
from setron.ui.import_demo import EQUIPMENT_BOOK, RELEASE_BOOK, DemoExcel
from setron.ui.theme import install_theme
from test_import_validation import extraction_review


class CountingExcel(DemoExcel):
    def __init__(self):
        self.calls = []

    def list_open_workbooks(self):
        self.calls.append("list")
        return super().list_open_workbooks()

    def read_sheet(self, workbook_name, sheet_name, *, app_pid, full_name):
        """실제 읽기 API의 필수 선택 정보를 유지하며 호출을 기록한다.

        param
        - workbook_name: 선택한 합성 파일명.
        - sheet_name: 선택한 합성 시트명.
        - app_pid: 선택 목록에서 받은 프로세스 번호.
        - full_name: 선택 목록에서 받은 전체 파일명.

        return
        - 합성 원본 셀과 위치를 담은 SheetData.
        """
        self.calls.append(("read", workbook_name, sheet_name, app_pid, full_name))
        return super().read_sheet(workbook_name, sheet_name, app_pid=app_pid, full_name=full_name)


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
    wait_for_extraction(page)
    page.workbook_combo.setCurrentIndex(page.workbook_combo.findText(book))
    page.sheet_combo.setCurrentIndex(page.sheet_combo.findData(sheet))
    assert page.read_selected()
    wait_for_extraction(page)


def extracted(page, **kwargs):
    page.load_workbooks()
    read(page, **kwargs)
    assert page.extract()
    wait_for_extraction(page)
    assert page.draft is not None
    return page.draft


def wait_for_extraction(page, *, timeout=5):
    """추출 워커 완료를 기다리며 화면 이벤트를 처리한다.

    param
    - page: 추출 결과를 기다리는 가져오기 화면.
    - timeout: 이벤트를 처리하며 기다릴 최대 초. 실제 SDK 최초 초기화는 별도로 여유를 둔다.
    """
    # 실제 앱과 같은 이벤트 루프로 대기한다. 반복 qWait는 일부 PySide 버전에서
    # Python 작업자에 GIL 실행 시간을 주지 않아 SDK 초기화가 지연될 수 있다.
    loop, poll, deadline = QEventLoop(), QTimer(), QTimer()
    deadline.setSingleShot(True)
    poll.timeout.connect(lambda: loop.quit() if not page.busy else None)
    deadline.timeout.connect(loop.quit)
    poll.start(10)
    deadline.start(round(timeout * 1000))
    if page.busy:
        loop.exec()
    poll.stop()
    deadline.stop()
    assert not page.busy


def test_menu_enabled_but_excel_is_connected_only_on_request(window, excel, page):
    assert window.nav["import"].isEnabled() and window.route == "import"
    assert not window.query_panel.isVisible()
    assert excel.calls == []
    assert not page.read_button.isEnabled() and not page.extract_button.isEnabled()
    page.list_button.click()
    wait_for_extraction(page)
    assert excel.calls == ["list"] and page.workbook_combo.count() == 2


def identity_reader(books):
    """실제 BE-006 호출 형식을 강제하며 선택한 창의 식별 정보를 셀로 반환한다.

    param
    - books: 목록 갱신·순서 변경을 재현할 통합문서 선택 정보 목록.

    return
    - 실제 read_sheet 시그니처를 검사하는 호출 기록 가능 가짜 Excel 객체.
    """
    def read_selected(workbook_name, sheet_name, *, app_pid, full_name):
        """선택 식별 정보를 가짜 원본 값으로 반환한다.

        param
        - workbook_name: 선택한 파일명.
        - sheet_name: 선택한 시트명.
        - app_pid: 선택한 Excel 프로세스 번호.
        - full_name: 선택 목록에서 받은 전체 파일명.

        return
        - 읽은 프로세스와 경로를 확인할 수 있는 합성 SheetData.
        """
        return SheetData(workbook_name, sheet_name, 1, 1, [[app_pid, full_name]], app_pid=app_pid)

    return SimpleNamespace(
        list_open_workbooks=lambda: books,
        read_sheet=create_autospec(excel_reader.read_sheet, side_effect=read_selected),
    )


def test_real_reader_signature_receives_selected_pid_and_full_name(page):
    """실제 읽기 API가 요구하는 프로세스 번호와 전체 이름을 UI가 전달하는지 확인한다.

    param
    - page: 실제 Qt 가져오기 화면 fixture.
    """
    selected = WorkbookInfo("원본.xlsx", "C:/source/원본.xlsx", ["Memory"], 7001)
    reader = identity_reader([selected])
    page.excel = reader
    page.load_workbooks()
    wait_for_extraction(page)
    assert page.read_selected()
    wait_for_extraction(page)
    assert page.snapshot is not None
    reader.read_sheet.assert_called_once_with(
        selected.name, "Memory", app_pid=selected.app_pid, full_name=selected.full_name,
    )
    assert page.snapshot.cells["B1"] == selected.full_name


@pytest.mark.parametrize("same_path", [False, True], ids=["다른 경로", "같은 경로의 다른 창"])
def test_duplicate_workbook_names_keep_selected_process_and_sheet(page, same_path):
    """동명 파일도 선택한 창의 시트 목록과 읽기 대상으로 구분하는지 확인한다.

    param
    - page: 실제 Qt 가져오기 화면 fixture.
    - same_path: 같은 전체 경로가 서로 다른 Excel 프로세스에 열린 경우 여부.
    """
    first = WorkbookInfo("동일.xlsx", "C:/first/동일.xlsx", ["첫 시트"], 7001)
    selected = WorkbookInfo("동일.xlsx", first.full_name if same_path else "C:/second/동일.xlsx", ["선택 시트"], 7002)
    reader = identity_reader([first, selected])
    page.excel = reader
    page.load_workbooks()
    wait_for_extraction(page)
    page.workbook_combo.setCurrentIndex(1)
    assert page.workbook_combo.itemText(0) != page.workbook_combo.itemText(1)
    assert page.sheet_combo.currentData() == "선택 시트"
    assert page.read_selected()
    wait_for_extraction(page)
    assert page.snapshot is not None and page.snapshot.cells["A1"] == 7002
    reader.read_sheet.assert_called_once_with(
        selected.name, "선택 시트", app_pid=7002, full_name=selected.full_name,
    )


def test_workbook_refresh_preserves_identity_when_list_order_changes(page):
    """목록 앞에 동명 파일이 추가돼도 이전에 선택한 창을 유지하는지 확인한다.

    param
    - page: 실제 Qt 가져오기 화면 fixture.
    """
    first = WorkbookInfo("동일.xlsx", "C:/first/동일.xlsx", ["첫 시트"], 7001)
    selected = WorkbookInfo("동일.xlsx", "C:/second/동일.xlsx", ["선택 시트"], 7002)
    books = [first, selected]
    reader = identity_reader(books)
    page.excel = reader
    page.load_workbooks()
    wait_for_extraction(page)
    page.workbook_combo.setCurrentIndex(1)
    books.insert(0, WorkbookInfo("동일.xlsx", "C:/third/동일.xlsx", ["새 시트"], 7003))
    page.load_workbooks()
    wait_for_extraction(page)
    assert page.workbook_combo.currentIndex() == 2
    assert page.sheet_combo.currentData() == "선택 시트"
    assert page.read_selected()
    wait_for_extraction(page)
    reader.read_sheet.assert_called_once_with(selected.name, "선택 시트", app_pid=7002, full_name=selected.full_name)


def test_excel_not_running_is_reported_without_preview(window, page):
    def not_running():
        raise ExcelNotRunningError(NOT_RUNNING_MESSAGE)
    page.excel.list_open_workbooks = not_running
    assert page.load_workbooks()  # 요청 수락과 비동기 조회 결과는 구분한다.
    wait_for_extraction(page)
    assert page.status.tone == "danger" and page.notice.text() == NOT_RUNNING_MESSAGE
    assert not page.read_button.isEnabled() and page.snapshot is None


def test_partial_discovery_warning_is_visible_with_usable_books(page):
    """일부 창 조회에 실패해도 정상 파일을 읽고 화면에서 경고를 확인한다."""
    selected = WorkbookInfo("정상.xlsx", "C:/data/정상.xlsx", ["P1"], 7001)
    reader = identity_reader([selected])

    def discover():
        warnings.warn("일부 Excel 파일을 목록에서 확인하지 못했습니다. 제한된 보기 창을 확인하세요.",
                      excel_reader.ExcelDiscoveryWarning)
        return [selected]

    reader.list_open_workbooks = discover
    page.excel = reader
    assert page.load_workbooks()
    wait_for_extraction(page)
    assert page.workbook_combo.count() == 1 and page.read_button.isEnabled()
    assert page.status.tone == "warning" and "제한된 보기" in page.notice.text()
    assert page.read_selected()
    wait_for_extraction(page)
    assert page.snapshot is not None
    reader.read_sheet.assert_called_once_with(selected.name, "P1", app_pid=7001, full_name=selected.full_name)

    reader.list_open_workbooks = lambda: [selected]
    assert page.load_workbooks()
    wait_for_extraction(page)
    assert page.status.tone == "info" and "제한된 보기" not in page.notice.text()


def test_changed_workbook_selection_clears_preview_and_reports_error(page):
    """선택한 경로가 바뀌면 이전 미리보기를 지우고 재선택을 안내한다."""
    selected = WorkbookInfo("원본.xlsx", "C:/data/원본.xlsx", ["P1"], 7001)
    reader = identity_reader([selected])
    page.excel = reader
    page.load_workbooks()
    wait_for_extraction(page)
    assert page.read_selected()
    wait_for_extraction(page)
    assert page.snapshot is not None
    reader.read_sheet.side_effect = excel_reader.WorkbookSelectionChangedError("파일 경로가 바뀌었습니다. 다시 선택하세요.")
    assert page.read_selected()
    wait_for_extraction(page)
    assert page.snapshot is None and page.status.tone == "danger"
    assert "다시 선택" in page.notice.text() and not page.extract_button.isEnabled()


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
    assert page.source_badge.text() == "추출 완료" and page.source_badge.tone == "warning"
    assert "EXAMPLE" in page.source_badge.toolTip() and "실제 AI 결과 아님" in page.source_badge.toolTip()
    assert not page.save_button.isEnabled() and any(issue.field == "base_date" and issue.blocks_save for issue in page.issues)
    assert not page.save()
    assert page.edit_cell("base_date", "2026-10-21")
    assert page.draft.row(1).original.get("base_date") is None  # AI 원본은 바뀌지 않는다
    assert page.draft.row(1).edited_fields == ("base_date",)
    assert "수정됨" in page.row_model.rows[0][1]
    edited = page.row_model.index(0, 2 + page.draft.fields.index("base_date"))
    assert "원래 값 빈 값 → 새 값 2026-10-21" in page.row_model.data(edited, Qt.ItemDataRole.ToolTipRole)
    assert page.save_button.isEnabled() and draft.row(1).edits == {}
    assert saver.saved == []


def test_unrelated_edit_does_not_confirm_automatic_blank_but_explicit_clear_does(page):
    """다른 칸 수정은 오류를 유지하고 자동 공란을 직접 편집한 경우에만 해당 오류를 해제한다.

    param
    - page: 원문과 정규값을 분리한 검토 초안을 표시하는 가져오기 화면.
    """
    review = extraction_review(production_area="잘못된 생산 영역")
    source = draft_from_review(review)
    page.draft = source
    page._changed(initial=True)
    page.select_row(1)
    assert page.draft.row(1).value("production_area") in (None, "")
    assert page.edit_cell("base_date", "2026-11-03")
    assert page.draft.row(1).edited_fields == ("base_date",)
    assert any(i.field == "production_area" and i.blocks_save for i in page.issues)
    assert not page.save_button.isEnabled()

    # 빈 칸을 표에서 직접 열어 빈 채로 확정하면 자동 공란 오류가 풀린다.
    cell = page.row_model.index(0, 2 + page.draft.fields.index("production_area"))
    assert page.row_model.setData(cell, "")
    QTest.qWait(10)
    assert page.draft.row(1).edits["production_area"] is None
    assert not any(i.blocks_save for i in page.issues)
    assert page.save_button.isEnabled()
    assert page.draft.row(1).original["production_area"] == "잘못된 생산 영역"

    assert page.revert_selected()
    assert page.draft == source
    assert any(i.field == "production_area" and i.blocks_save for i in page.issues)
    assert not page.save_button.isEnabled()


def test_cancelled_confirmation_saves_nothing(page, saver):
    extracted(page)
    page.edit_cell("base_date", "2026-10-21")
    page.confirm = lambda text: False
    assert not page.save()
    QTest.qWait(10)
    assert saver.saved == [] and page.state == "idle" and page.save_button.isEnabled()


def test_confirmed_save_shows_toast_and_returns_to_first_step(window, page, saver):
    """저장을 마치면 아래 결과 문구 없이 토스트를 띄우고 1단계로 돌아간다 (11-10 2차 1-1·1-2)."""
    extracted(page)
    page.edit_cell("base_date", "2026-10-21")
    query, targets = window.query_state.applied, window.query_state.target_draft
    workbook = page.workbook_combo.currentData()
    assert page.save()
    assert EQUIPMENT_BOOK in page.confirms[0] and "Memory" in page.confirms[0] and "테스트 메모리 저장소" in page.confirms[0]
    assert page.state == "saving" and page.status.text() == "저장 중"
    assert not page.save_button.isEnabled() and not page.read_button.isEnabled() and not page.save()
    wait_for_extraction(page)
    assert len(saver.saved) == 1 and saver.saved[0].rows[0].edited_fields == ("base_date",)
    assert EQUIPMENT_BOOK not in str(saver.saved[0].raw_data)
    assert page.toast is not None and not page.toast.isHidden() and "저장했습니다" in page.toast.text.text()
    assert page.toast.tone == "success"
    # 처음으로: 통합문서 선택은 남고, 시트·미리보기·검토 내용·결과 문구는 비운다.
    assert page.state == "idle" and page.draft is None and page.snapshot is None and page.receipt is None
    assert page.workbook_combo.currentData() == workbook and page.sheet_combo.currentIndex() == -1
    assert page.result_label.text() == "" and page.progress_steps.stage == 0
    assert not page.save() and len(saver.saved) == 1
    # 저장으로 조회 결과·목표 N이 바뀌지 않는다.
    assert window.query_state.applied is query and window.query_state.target_draft == targets


def test_failed_save_keeps_draft_and_retry_uses_same_request(page, saver):
    saver.failures.append(DatabaseSaveError("데이터 저장에 실패해 이번 저장 묶음을 취소했습니다."))
    extracted(page)
    page.edit_cell("base_date", "2026-10-21")
    request_id = page.request_id
    page.save()
    wait_for_extraction(page)
    assert page.state == "failed" and page.status.tone == "danger" and "기존 자료는 그대로" in page.result_label.text()
    assert not page.toast.isHidden() and page.toast.tone == "danger" and "기존 자료는 그대로" in page.toast.text.text()
    assert page.save_button.isEnabled() and page.draft.row(1).value("base_date") == "2026-10-21"
    page.save()
    wait_for_extraction(page)
    assert page.draft is None and [r.request_id for r in saver.saved] == [request_id]


def test_unknown_commit_result_locks_retry(page, saver):
    saver.failures.append(DatabaseCommitUnknownError("최종 저장 중 DB 연결이 끊겨 저장 결과를 확인할 수 없습니다."))
    extracted(page)
    page.edit_cell("base_date", "2026-10-21")
    page.save()
    wait_for_extraction(page)
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
    assert page.selected_row_id == 1
    # 검토 항목을 누르면 추출 표의 해당 칸으로 간다 (값 수정 칸은 없앴다).
    current = page.row_table.currentIndex()
    assert current.row() == 0 and current.column() == 2 + page.draft.fields.index("base_date")
    page.kind_combo.setCurrentIndex(page.kind_combo.findData(ExtractKind.RELEASE_NOTE.value))
    extracted(page, book=RELEASE_BOOK, sheet="Release")
    page.open_issue(next(i for i, issue in enumerate(page.issues) if issue.address == "E3"))
    assert page.grid_model.highlight == "E3" and "원본값 C4, C4 Plus" in page.cell_label.text()
    assert not page.highlight_cell("Z999") and page.grid_model.highlight == "E3"


def test_release_note_flow_saves_items_without_equipment_fields(page, saver):
    """릴리즈 전용 행을 저장 요청으로 만들고 공통 저장 계약의 셀 목록을 보존한다.

    param
    - page: 검토·저장할 가져오기 화면.
    - saver: 저장 요청을 기록하는 메모리 저장소.
    """
    page.kind_combo.setCurrentIndex(page.kind_combo.findData(ExtractKind.RELEASE_NOTE.value))
    extracted(page, book=RELEASE_BOOK, sheet="Release")
    assert page.row_model.headings[2:] == (
        "SW 버전 원문", "출시일", "Item 번호", "변경 내용", "Jira 코드", "대상 모델",
        "설비군", "모델명", "SW 계열", "UI 구분", "Inline/Cluster", "개조 정보", "개조 적용 표식",
        "Memory/Foundry", "확인된 순서", "접미사", "Item 매핑",
    )
    assert "설비 호기" not in page.row_model.headings and "기준일" not in page.row_model.headings
    physical_fields = {"base_date", "site", "line", "equipment_code", "pm_name", "process_name",
                       "engine_version", "epd_version"}
    assert physical_fields.isdisjoint(page.draft.fields)
    assert page.save_button.isEnabled()
    page.save()
    wait_for_extraction(page)
    request = saver.saved[0]
    assert request.kind is ExtractKind.RELEASE_NOTE
    assert [r.values["item_no"] for r in request.rows] == [1, 2]
    assert {(r.values["version_raw"], r.values["model_name"], r.values["family_name"]) for r in request.rows} == {
        ("v1.0.3", "C4", "C4"),
    }
    for row in request.rows:
        assert physical_fields.isdisjoint(row.values)
        for field in ("production_area", "version_order", "version_suffix", "item_mapping_status"):
            assert row.values[field] is None and field not in row.source_reference
    assert request.rows[0].source_reference["content"] == {"sheet": "Release", "cells": ["D2"]}


def test_kind_change_discards_draft_and_inputs_lock_while_extracting(page):
    extracted(page)
    page.kind_combo.setCurrentIndex(page.kind_combo.findData(ExtractKind.RELEASE_NOTE.value))
    assert page.draft is None and page.status.tone == "warning"
    page.kind_combo.setCurrentIndex(page.kind_combo.findData(ExtractKind.EQUIPMENT_STATUS.value))
    assert page.extract()
    # 결과가 오기 전에는 다른 시트를 읽거나 종류를 바꿔 결과가 섞이지 않게 한다.
    assert not page.read_selected() and not page.extract() and not page.kind_combo.isEnabled()
    wait_for_extraction(page)
    assert page.draft is not None and page.kind_combo.isEnabled()
    read(page, RELEASE_BOOK, "Release")
    assert page.draft is None and page.snapshot.sheet_name == "Release"


def test_ai_failure_shows_message_and_no_draft(page):
    page.extractor = lambda kind, name, cells: extract_sheet(
        example_client(AITask.EXCEL_EXTRACT, "rate_limit"), kind, name, cells)
    page.load_workbooks()
    read(page)
    page.extract()
    wait_for_extraction(page)
    assert page.draft is None and page.status.tone == "danger" and "호출 제한" in page.notice.text()
    assert page.extract_button.isEnabled()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
def test_responsive_without_main_horizontal_scroll(window, page, size):
    extracted(page)
    window.resize(*size)
    QTest.qWait(60)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert page.row_table.width() <= page.width() and page.grid.width() <= page.width()


def test_model_candidates_are_applied_only_after_confirmation(page):
    """데이터 파트 번역표 후보는 확인창 승인 후에만 적용되고 사용자 수정으로 기록된다."""
    from test_excel_import_service import draft_for, eq_row, sheet

    data = sheet(["2026-10-21", "화성", "E1", "PM1", "M-C3 (6각) (통합UI)", None, "v3", None])
    page.draft = draft_for(data, [eq_row(2, equipment_code="E1", model_name="M-C3 (6각) (통합UI)",
                                         family_name=None, version_raw="v3")])
    page._changed(initial=True)
    assert page.candidate_button.isEnabled() and "(1행)" in page.candidate_button.text()

    page.confirm = lambda text: False
    assert not page.apply_model_candidates()
    assert page.draft.row(1).value("model_name") == "M-C3 (6각) (통합UI)"

    page.confirm = lambda text: page.confirms.append(text) or True
    assert page.apply_model_candidates()
    assert "번역표" in page.confirms[-1]
    row = page.draft.row(1)
    assert (row.value("model_name"), row.value("family_name"), row.value("ui_type")) == ("M-C3", "C3", "통합UI")
    assert "model_name" in row.edits and row.original["model_name"] == "M-C3 (6각) (통합UI)"
    assert not page.candidate_button.isEnabled()


def test_extraction_keeps_running_while_away_from_the_page(window, page):
    """추출 중 다른 메뉴로 이동해도 취소하지 않고, 돌아오면 결과가 보인다 (원본 사본으로 추출하므로 안전)."""
    import threading

    gate = threading.Event()
    original = page.extractor

    def slow(*args, **kwargs):
        gate.wait(5)
        return original(*args, **kwargs)

    page.extractor = slow
    page.load_workbooks()
    read(page)
    assert page.extract()
    window.navigate("dashboard")
    QTest.qWait(30)
    assert not page.isVisible() and page.state == "extracting"
    gate.set()
    wait_for_extraction(page)
    window.navigate("import")
    QTest.qWait(10)
    assert page.draft is not None and page.state == "idle"


def test_stop_reason_is_shown_inside_the_review_area(window, page):
    """추출이 멈추면 그 이유를 빈 검토 칸에 바로 보여 준다 (위쪽 안내는 3번 카드까지 내리면 안 보임, 11/09)."""
    from setron.features.ai.schemas import AIResult, AIStatus, AITask
    from setron.infrastructure.ai_client import ResponseSource

    reason = "설비 현황표 양식이지만 설비호기·버전 칸이 비어 있어 읽을 설비가 없습니다."
    page.rule_extractor = lambda kind, sheet: AIResult(AITask.EXCEL_EXTRACT, AIStatus.FAILED, reason, None, ResponseSource.RULE)
    page.load_workbooks()
    read(page)
    assert page.extract()
    wait_for_extraction(page)
    assert page.draft is None and page.row_empty.isVisibleTo(page) and page.row_empty.text() == reason
    assert page.source_badge.text() == "추출할 내용 없음"

    page.rule_extractor = None                                                 # AI 실패도 같은 자리에 이유
    page.extractor = lambda *args, **kwargs: AIResult(AITask.EXCEL_EXTRACT, AIStatus.FAILED, "AI 응답 시간 초과", None,
                                                      ResponseSource.REAL)
    assert page.extract()
    assert page.row_empty.text() != reason                                     # 새 추출을 시작하면 이전 이유는 지운다
    wait_for_extraction(page)
    assert "AI 응답 시간 초과" in page.row_empty.text() and page.source_badge.text() == "AI 추출 실패"
