"""BE-024의 실제 화면 추출·원본 검증·보완·저장 경로를 검증한다. 외부 API는 호출하지 않는다."""

from copy import deepcopy
import json
from threading import Event, get_ident
from time import monotonic

import httpx2
import pytest
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtTest import QTest

from setron.features.ai.schemas import ExtractKind
from setron.features.ai.service import extract_sheet
from setron.features.excel_import.service import MemoryImportSaver
from setron.infrastructure.ai_client import ExampleChatClient, OpenAICompatibleChatClient
from setron.infrastructure.excel_reader import SheetData, WorkbookInfo
from setron.ui.app import create_window
from setron.ui.import_extraction import LiveSheetExtractor
from setron.ui.import_page import ImportPage

from test_openai_client import completion, settings
from test_ui_import import app, wait_for_extraction  # noqa: F401


FIELDS = ("base_date", "site", "group_name", "model_name", "equipment_code", "family_name", "version_raw", "production_area")


class HpaExcel:
    """실제 읽기 계약을 따르는 시험용 HPA 증강 시트 공급자."""

    def __init__(self, version="vHPA.2_d5 "):
        """필수값 공란과 정상 버전을 구분할 원본을 만든다.

        param
        - version: 시험 SW 원문. None은 실제 빈 셀.
        """
        self.sheet = SheetData("HPA_화면검증.xlsx", "현황", 5, 2,
            [["기준일", "사업장", "설비군", "모델", "호기", "SW 계열", "SW 버전", "Memory/Foundry"],
             ["2026-11-03", "화성", "Oxide", "M-O2", "TEST-O2-UI", "O2", "v2", None],
             ["2026-11-03", "화성", "HPA", "HPA", "TEST-HPA-UI", "HPA", version, None]])

    def list_open_workbooks(self):
        """시험용 파일·프로세스 선택 정보를 반환한다.

        return
        - 경로·프로세스가 고정된 통합문서 하나.
        """
        return [WorkbookInfo(self.sheet.workbook_name, "C:/test/HPA_화면검증.xlsx", [self.sheet.sheet_name], 123)]

    def read_sheet(self, workbook_name, sheet_name, *, app_pid, full_name):
        """선택 식별자를 확인하고 원본 복사본을 반환한다.

        param
        - workbook_name, sheet_name, app_pid, full_name: 사용자가 고정한 선택 정보.

        return
        - 호출 이후 원본 변경과 독립적인 SheetData.
        """
        assert (workbook_name, sheet_name, app_pid, full_name) == (
            self.sheet.workbook_name, self.sheet.sheet_name, 123, "C:/test/HPA_화면검증.xlsx")
        return deepcopy(self.sheet)


def payload(excel, *, omit_hpa=False, forged=False):
    """원본 좌표를 가진 응답을 만들고 오류 조건만 명시적으로 바꾼다.

    param
    - excel: 시험 원본 공급자.
    - omit_hpa, forged: HPA 응답 누락 또는 공란 SW에 허위값 응답을 재현.

    return
    - AI 응답 형식의 JSON 문자열.
    """
    rows = []
    for index in (1,) if omit_hpa else (1, 2):
        values = dict(zip(FIELDS, excel.sheet.values[index]))
        sources = {field: excel.sheet.address_of(index, column) for column, field in enumerate(FIELDS)}
        if index == 2 and forged:
            values["version_raw"] = "vFAKE"
            sources["version_raw"] = "E7"  # 실제 값은 모델명 HPA이며 SW가 아니다.
        elif index == 2 and values["version_raw"] is None:
            sources.pop("version_raw")  # 공란 위치는 확정된 머리글·행 교차점에서만 복원.
        rows.append({"values": values, "sources": sources})
    return json.dumps({"rows": rows}, ensure_ascii=False)


def prepare(page):
    """실제 화면의 파일 선택·읽기·추출 요청을 실행한다.

    param
    - page: 검증할 가져오기 화면.
    """
    assert page.load_workbooks()
    wait_for_extraction(page)
    assert page.read_selected()
    wait_for_extraction(page)
    assert page.extract()
    wait_for_extraction(page, timeout=15)


@pytest.fixture
def pages(app):
    """위젯과 워커를 정리할 시험 화면 목록을 제공한다.

    param
    - app: Qt 이벤트를 처리할 QApplication.

    return
    - 생성한 화면을 등록할 목록.
    """
    created = []
    yield created
    assert QThreadPool.globalInstance().waitForDone(6000)
    for page in created:
        page.close()
        page.deleteLater()
    app.processEvents()


def test_native_sdk_pipeline_saves_hpa_from_selected_original(app, pages):
    """SDK의 모의 HTTP 응답을 화면에서 검토·저장하고 실제 호출 경로를 확인한다.

    param
    - app, pages: Qt 앱과 정리할 시험 화면 목록.
    """
    excel, requests, saver = HpaExcel(), [], MemoryImportSaver()
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=completion(payload(excel)))
    native = LiveSheetExtractor(lambda: OpenAICompatibleChatClient.from_env(
        settings(), http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    window = create_window(excel=excel, sheet_extractor=native, import_saver=saver)
    page = window.pages["import"]
    page.confirm = lambda _: True
    try:
        prepare(page)
        assert "REAL" in page.source_badge.toolTip() and len(requests) == 1
        assert "읽은 범위: B5:I7" in requests[0]["messages"][1]["content"]
        assert page.draft.row(2).value("equipment_code") == "TEST-HPA-UI"
        assert page.draft.row(2).value("version_raw") == "vHPA.2_d5 "
        assert page.save_button.isEnabled() and page.save()
        wait_for_extraction(page)
        assert saver.saved[0].rows[1].values["version_raw"] == "vHPA.2_d5 "
        assert saver.saved[0].raw_data["cells"]["I7"] is None
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_blank_hpa_sw_shows_actual_position_and_requires_explicit_completion(pages):
    """공란 오류의 파일·시트·행·열을 표시하고 명시적 보완 후에만 저장한다.

    param
    - pages: 생성한 시험 화면의 정리 목록.
    """
    excel, saver = HpaExcel(None), MemoryImportSaver()
    page = ImportPage(excel=excel, saver=saver, confirm=lambda _: True,
        extractor=lambda kind, sheet, cells: extract_sheet(ExampleChatClient([payload(excel)]), kind, sheet, cells))
    pages.append(page)
    prepare(page)
    missing = next(issue for issue in page.issues if issue.row_id == 2 and issue.field == "version_raw" and issue.blocks_save)
    assert (missing.workbook_name, missing.sheet_name, missing.excel_position) == ("HPA_화면검증.xlsx", "현황", (7, 8))
    assert any(row[5:] == ("HPA_화면검증.xlsx", "현황", "7", "8") for row in page.issue_model.rows)
    assert not page.save() and not saver.saved
    page.select_row(2)
    assert page.edit_cell("version_raw", "vHPA.USER") and page.save_button.isEnabled() and page.save()
    wait_for_extraction(page)
    assert len(saver.saved[0].rows) == 2
    assert saver.saved[0].raw_data["cells"]["H7"] is None
    assert saver.saved[0].rows[1].values["version_raw"] == "vHPA.USER"


@pytest.mark.parametrize("classifiers", ["family", "none"])
@pytest.mark.parametrize("missing", ["equipment_code", "version_raw"])
def test_native_ui_hpa_exclusion_cannot_hide_required_errors_with_missing_classifiers(pages, classifiers, missing):
    """실제 SDK 응답에서 분류가 누락되어도 행 제외로 우회하지 못하고 명시적 보완 후 함께 저장한다."""
    excel, saver = HpaExcel(), MemoryImportSaver()
    excel.sheet.values[2][FIELDS.index(missing)] = None
    response = json.loads(payload(excel))
    omitted = ("group_name", "model_name") if classifiers == "family" else ("group_name", "model_name", "family_name")
    for name in omitted:
        response["rows"][1]["values"][name] = None
        response["rows"][1]["sources"].pop(name)
    native = LiveSheetExtractor(lambda: OpenAICompatibleChatClient.from_env(settings(),
        http_client=httpx2.Client(transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, json=completion(json.dumps(response, ensure_ascii=False)))))))
    page = ImportPage(excel=excel, saver=saver, confirm=lambda _: True, sheet_extractor=native)
    pages.append(page)
    prepare(page)
    assert "REAL" in page.source_badge.toolTip()
    assert page.select_row(2) and page.toggle_excluded()

    problems = [issue for issue in page.issues if issue.row_id == 2 and issue.field == missing and issue.blocks_save]
    assert problems and all(issue.excel_position == (7, 6 if missing == "equipment_code" else 8) for issue in problems)
    assert not page.save_button.isEnabled() and not page.save() and not saver.saved

    assert page.toggle_excluded()
    for name in ("model_name", "group_name", "family_name"):
        if page.draft.row(2).value(name) is None:
            page.edit_cell(name, "HPA")
    assert page.edit_cell(missing, "TEST-HPA-UI" if missing == "equipment_code" else "vHPA.USER")
    assert page.save_button.isEnabled() and page.save()
    wait_for_extraction(page)
    assert len(saver.saved[0].rows) == 2
    assert saver.saved[0].raw_data["cells"]["F7" if missing == "equipment_code" else "H7"] is None
    assert all(saver.saved[0].raw_data["cells"][address] == "HPA" for address in ("D7", "E7", "G7"))


@pytest.mark.parametrize("failure", ["omitted", "forged"])
def test_legacy_extractor_cannot_bypass_hpa_coverage_or_original_validation(pages, failure):
    """기존 추출기 주입도 HPA 누락·허위 SW가 있으면 정상 행까지 저장하지 않는다.

    param
    - pages: 생성한 시험 화면의 정리 목록.
    - failure: HPA 행 누락 또는 원본에 없는 SW 응답.
    """
    excel, saver = HpaExcel(None), MemoryImportSaver()
    page = ImportPage(excel=excel, saver=saver,
        extractor=lambda kind, sheet, cells: extract_sheet(ExampleChatClient([
            payload(excel, omit_hpa=failure == "omitted", forged=failure == "forged")]), kind, sheet, cells))
    pages.append(page)
    prepare(page)
    assert any(issue.blocks_save for issue in page.issues)
    assert not page.save_button.isEnabled() and not page.save() and not saver.saved
    if failure == "forged":
        assert page.draft.row(2).value("version_raw") is None


def test_native_import_holds_normal_rows_when_sparse_hpa_after_blank_row_is_omitted(pages):
    """실제 SDK 추출 경로에서 구분행 뒤 필수 공란 HPA가 빠지면 전체 저장을 막는다."""
    excel, saver = HpaExcel(None), MemoryImportSaver()
    excel.sheet.values[2][0:2] = [None, None]
    excel.sheet.values.insert(2, [None] * len(FIELDS))
    native = LiveSheetExtractor(lambda: OpenAICompatibleChatClient.from_env(
        settings(), http_client=httpx2.Client(transport=httpx2.MockTransport(
            lambda _: httpx2.Response(200, json=completion(payload(excel, omit_hpa=True)))))))
    page = ImportPage(excel=excel, saver=saver, sheet_extractor=native)
    pages.append(page)

    prepare(page)

    missing = next(issue for issue in page.issues if issue.code == "HPA_COVERAGE_MISSING")
    assert missing.blocks_save and missing.workbook_name == "HPA_화면검증.xlsx" and missing.sheet_name == "현황"
    assert missing.excel_position[0] == 8
    assert "F8" in missing.message and "H8" in missing.message
    assert not page.save_button.isEnabled() and not page.save() and not saver.saved


def test_default_importer_reports_missing_ai_settings_without_example_fallback(monkeypatch, pages):
    """실제 공급자 설정이 없으면 예제 행 대신 명확한 연결 설정 오류를 표시한다.

    param
    - monkeypatch, pages: 환경변수 격리와 화면 정리 목록.
    """
    for name in ("SETRON_AI_BASE_URL", "SETRON_AI_API_KEY", "SETRON_AI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    page = ImportPage(excel=HpaExcel())
    pages.append(page)
    prepare(page)
    assert page.draft is None and page.status.tone == "danger"
    assert "SETRON_AI_" in page.notice.text() and page.extract_button.isEnabled()


def test_user_confirms_ambiguous_blank_source_and_invalid_address_keeps_original(pages):
    """중복 SW 머리글은 주소를 강제 추정하지 않고 사용자가 확인한 범위 안 위치만 받는다.

    param
    - pages: 생성한 시험 화면의 정리 목록.
    """
    excel, saver = HpaExcel(None), MemoryImportSaver()
    excel.sheet.values[0].append("Version")
    for row in excel.sheet.values[1:]:
        row.append(None)
    page = ImportPage(excel=excel, saver=saver, confirm=lambda _: True,
        extractor=lambda kind, sheet, cells: extract_sheet(ExampleChatClient([payload(excel)]), kind, sheet, cells))
    pages.append(page)
    prepare(page)
    assert any(issue.code == "REQUIRED_SOURCE_UNRESOLVED" for issue in page.issues)
    page.select_row(2)
    assert page.edit_cell("version_raw", "vHPA.USER") and not page.save_button.isEnabled()
    before = page.draft
    assert not page.edit_source("ZZ9999") and page.draft is before
    assert page.edit_source("J7") and page.save_button.isEnabled() and page.save()
    wait_for_extraction(page)
    assert saver.saved[0].rows[1].source_reference["version_raw"] == {"sheet": "현황", "cells": ["J7"]}
    assert saver.saved[0].raw_data["cells"]["J7"] is None and saver.saved[0].raw_data["cells"]["H7"] is None


def test_closing_before_scheduled_extraction_does_not_start_the_provider(pages):
    """추출 예약 직후 화면을 닫으면 공급자와 API 호출을 시작하지 않는다.

    param
    - pages: 생성한 시험 화면의 정리 목록.
    """
    calls = []
    page = ImportPage(excel=HpaExcel(), extractor=lambda *args: calls.append(args))
    pages.append(page)
    page.show()
    page.load_workbooks()
    wait_for_extraction(page)
    page.read_selected()
    wait_for_extraction(page)
    assert page.extract()
    page.close()
    QTest.qWait(30)
    assert calls == [] and not page.extraction_request.busy


def test_slow_extraction_keeps_ui_responsive_and_discards_closed_page_result(pages):
    """추출 중 UI 이벤트·중복 차단과 화면을 닫은 뒤 지연 응답 폐기를 확인한다.

    화면을 숨기기만 하면(다른 메뉴 이동·최소화) 추출은 계속된다 — test_ui_import 참고 (2026-11-07).

    param
    - pages: 생성한 시험 화면의 정리 목록.
    """
    excel, started, release = HpaExcel(), Event(), Event()
    threads, ticks = [], []
    def slow(kind, sheet, cells):
        threads.append(get_ident())
        started.set()
        assert release.wait(5)
        return extract_sheet(ExampleChatClient([payload(excel)]), kind, sheet, cells)
    page = ImportPage(excel=excel, extractor=slow)
    pages.append(page)
    page.show()
    try:
        page.load_workbooks()
        wait_for_extraction(page)
        page.read_selected()
        wait_for_extraction(page)
        page.extract()
        end = monotonic() + 3
        while not started.is_set() and monotonic() < end:
            QTest.qWait(10)
        assert started.is_set() and threads == [threads[0]] and threads[0] != get_ident()
        assert page.busy and not page.extract() and not page.save()
        QTimer.singleShot(0, lambda: ticks.append(True))
        QTest.qWait(20)
        assert ticks == [True]
        page.close()
        release.set()
        assert QThreadPool.globalInstance().waitForDone(3000)
        QTest.qWait(20)
        assert page.draft is None and not page.busy and not page.extraction_request.busy
    finally:
        release.set()
