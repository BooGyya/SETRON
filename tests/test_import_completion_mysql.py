"""실제 MySQL에서 기준정보 준비와 Excel 읽기·검토·저장·재조회 전체 경로를 검증한다."""

import importlib.util
import os
import re
from datetime import date
from pathlib import Path
from time import monotonic

import pymysql
import pytest
from PySide6.QtTest import QTest

from setron.features.ai.schemas import AIResult, AIStatus, AITask, ExtractKind, ExtractResult, ExtractedRow
from setron.features.equipment.service import EquipmentService
from setron.features.storage.service import StorageService
from setron.infrastructure.ai_client import ChatReply, ResponseSource
from setron.infrastructure.database import DatabaseConfig
from setron.infrastructure.excel_reader import SheetData, WorkbookInfo
from setron.ui.app import create_window
from setron.ui.database_source import DatabaseDashboardSource, load_dashboard_source
from setron.ui.ai_connection import ai_providers_from_env
from setron.features.import_save.service import DatabaseImportSaver
from test_schema_mysql import schema_database  # noqa: F401
from test_ui_import import app  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 설정 필요")

spec = importlib.util.spec_from_file_location(
    "seed_reference_data", Path(__file__).resolve().parents[1] / "database" / "seed_reference_data.py")
seed_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed_module)


@pytest.fixture(scope="module")
def storage(schema_database):
    config = DatabaseConfig.from_env(prefix="SETRON_TEST_DB_")
    assert seed_module.seed_reference_data(config) == {"equipment_group": 14, "version_family": 15}
    assert seed_module.seed_reference_data(config) == {"equipment_group": 0, "version_family": 0}
    return StorageService(schema_database)


def wait_until(predicate, timeout=10):
    end = monotonic() + timeout
    while not predicate() and monotonic() < end:
        QTest.qWait(10)
    assert predicate()


def test_seed_failure_rolls_back_all_new_rows(storage, tmp_path, monkeypatch):
    invalid = tmp_path / "invalid.sql"
    invalid.write_text(
        "INSERT INTO equipment_group (group_name, category) VALUES ('SEED-ROLLBACK', 'OXIDE');"
        "INSERT INTO no_such_seed_table VALUES (1);", encoding="utf-8")
    monkeypatch.setattr(seed_module, "SEED", invalid)
    with pytest.raises(pymysql.MySQLError):
        seed_module.seed_reference_data(DatabaseConfig.from_env(prefix="SETRON_TEST_DB_"))
    with storage.transaction() as tx:
        rows = tx.repository.table("equipment_group")
        from sqlalchemy import select
        assert tx.session.execute(select(rows).where(rows.c.group_name == "SEED-ROLLBACK")).first() is None


class WorkbookExcel:
    """실제 XLSX를 읽는 시험 공급자. 운영 앱의 Excel COM 선택 계약을 그대로 따른다."""

    def __init__(self, path):
        self.path = path

    def list_open_workbooks(self):
        return [WorkbookInfo(self.path.name, str(self.path), ["현황"], app_pid=100)]

    def read_sheet(self, name, sheet_name, *, app_pid, full_name):
        from openpyxl import load_workbook
        assert (name, app_pid, full_name) == (self.path.name, 100, str(self.path))
        book = load_workbook(self.path, data_only=True)
        try:
            sheet = book[sheet_name]
            return SheetData(name, sheet_name, 1, 1, [list(row) for row in sheet.values])
        finally:
            book.close()


def test_excel_review_commit_and_requery_show_saved_values(app, storage, tmp_path):
    from openpyxl import Workbook
    path = tmp_path / "가져오기_전체흐름.xlsx"
    values = dict(base_date="2026-11-04", site="화성", line="새 검증 라인", group_name="OXIDE",
                  model_name="M-O2", equipment_code="IMPORT-E2E", pm_name="PM1", family_name="O2",
                  version_raw="v3.53_1")
    book = Workbook()
    sheet = book.active
    sheet.title = "현황"
    sheet.append(list(values))
    sheet.append(list(values.values()))
    book.save(path)
    book.close()
    sources = {name: f"{chr(65 + i)}2" for i, name in enumerate(values)}

    def extract(kind, sheet_name, cells):
        # AI의 비결정성은 이 통합 검증에서 제외한다. 원본 대조·검토·저장은 실제 코드다.
        return AIResult(AITask.EXCEL_EXTRACT, AIStatus.OK, "시험 추출",
                        ExtractResult(kind, sheet_name, (ExtractedRow(values, sources),)), ResponseSource.EXAMPLE)

    source = DatabaseDashboardSource(lambda catalog=None: load_dashboard_source(storage, catalog))
    saver = DatabaseImportSaver(storage, background_save=True)
    window = create_window(source, excel=WorkbookExcel(path), extractor=extract, import_saver=saver)
    window.show()
    window.navigate("import")
    page = window.pages["import"]
    page.confirm = lambda text: True
    try:
        assert page.load_workbooks() and page.read_selected()
        wait_until(lambda: not page.busy)
        assert page.snapshot.cells["I2"] == "v3.53_1"
        assert page.extract()
        wait_until(lambda: not page.busy)
        assert not [issue for issue in page.issues if issue.blocks_save]
        page.select_row(1)
        page.inputs["version_raw"].setText("v3.53_2")
        assert page.apply_edits()
        wait_until(lambda: not page.busy)
        assert page.save_button.isEnabled()
        assert page.save()
        wait_until(lambda: page.state != "saving")
        assert page.state == "saved", page.result_label.text()
        saved = EquipmentService(storage).get_snapshot("IMPORT-E2E", date(2026, 11, 4))
        assert saved.software[0].version_raw == "v3.53_2"
        with storage.transaction() as tx:
            batch = tx.get("import_batch", {"batch_id": page.receipt.batch_id})
            assert batch["raw_data"]["현황"]["I2"] == "v3.53_1"
        page.requery_button.click()
        assert window.route == "dashboard"
        snapshot = window.pages["dashboard"].snapshot
        assert snapshot is not None and snapshot.summary.equipment_count == 1
        assert snapshot.selected_records[0].pms[0].version_raw == "v3.53_2"
        assert window.pages["heatmap"].snapshot is snapshot
        config = DatabaseConfig.from_env(prefix="SETRON_TEST_DB_")
        assert seed_module.seed_reference_data(config) == {"equipment_group": 0, "version_family": 0}
        assert EquipmentService(storage).get_snapshot("IMPORT-E2E", date(2026, 11, 4)) == saved
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


@pytest.mark.skipif(not os.environ.get("SETRON_LIVE_EXCEL_WORKBOOK"), reason="열린 Excel·실제 AI 검증은 명시적으로 실행")
def test_live_excel_ai_review_save_and_reload(app, storage, tmp_path):
    """지정한 열린 교육용 파일만 읽고 실제 AI 응답을 전용 테스트 DB에 저장한다."""
    expected_name = os.environ["SETRON_LIVE_EXCEL_WORKBOOK"]
    sheet_name = os.environ.get("SETRON_LIVE_EXCEL_SHEET", "구UI")
    ai = ai_providers_from_env()
    assert ai.connected
    complete = ai.client.complete
    calls = []

    def observe(messages):
        recorded = os.environ.get("SETRON_LIVE_AI_RESPONSES")
        if recorded:
            content = (Path(recorded) / f"ai_response_{len(calls) + 1}.json").read_text(encoding="utf-8")
            response = ChatReply(content, ResponseSource.REAL)
        else:
            response = complete(messages)
        calls.append(response)
        output = tmp_path / f"ai_response_{len(calls)}.json"
        output.write_text(response.content, encoding="utf-8")
        interval = re.search(r"이번 요청의 추출 대상 행: ([0-9]+~[0-9]+행)", messages[-1].content)
        print(f"{'기록된 GMS 응답 재생' if recorded else 'AI 응답 완료'}: "
              f"{interval[1] if interval else '전체'} / 응답 {len(response.content)}자", flush=True)
        return response

    ai.client.complete = observe
    source = DatabaseDashboardSource(lambda catalog=None: load_dashboard_source(storage, catalog))
    window = create_window(source, sheet_extractor=ai.sheet_extractor,
                           import_saver=DatabaseImportSaver(storage, background_save=True))
    window.show()
    window.navigate("import")
    page = window.pages["import"]
    page.confirm = lambda text: True
    preview = page.saver.preview_release_import

    def observe_preview(request, reviews=()):
        try:
            return preview(request, reviews)
        except Exception as error:
            print(f"릴리즈 교체 검토 오류: {type(error).__name__}: {error}", flush=True)
            raise

    page.saver.preview_release_import = observe_preview
    try:
        assert page.load_workbooks(), page.notice.text()
        selected = next(i for i, book in enumerate(page.workbooks) if book.name == expected_name)
        page.workbook_combo.setCurrentIndex(selected)
        page.sheet_combo.setCurrentIndex(page.sheet_combo.findData(sheet_name))
        page.kind_combo.setCurrentIndex(page.kind_combo.findData(ExtractKind.RELEASE_NOTE.value))
        assert page.read_selected()
        wait_until(lambda: not page.busy)
        assert page.snapshot is not None, page.notice.text()
        print(f"\n실제 Excel 읽기: {expected_name} / {sheet_name} / {len(page.snapshot.cells)}셀", flush=True)
        print(f"실제 AI 검증 임시 응답: {tmp_path}", flush=True)
        assert page.extract()
        end = monotonic() + 600
        while page.busy and monotonic() < end:
            QTest.qWait(50)
        assert not page.busy, "실제 AI 추출·DB 검토 대기 시간 초과"
        assert page.draft is not None, page.notice.text()
        assert page.draft.ai_source is ResponseSource.REAL
        # 원본 제목의 M-C3·구UI와 등록 기준정보를 검토해 공통 관리 계열을 명시적으로 선택한다.
        selected = next(index for index in range(page.release_family_combo.count())
                        if (value := page.release_family_values.get(page.release_family_combo.itemData(index)))
                        and value["model_name"] == "M-C3" and value["ui_type"] == "LEGACY")
        page.release_family_combo.setCurrentIndex(selected)
        assert page.apply_release_common()
        wait_until(lambda: not page.busy)
        blocking = [(issue.row_id, issue.field, issue.message) for issue in page.issues if issue.blocks_save]
        print(f"실제 AI 추출: {len(page.draft.rows)}행 / 저장 차단 {len(blocking)}건", flush=True)
        assert not blocking, blocking[:15]
        content_cells = {address for address, value in page.snapshot.cells.items()
                         if address.startswith("D") and address[1:].isdigit() and int(address[1:]) > 2
                         and value not in (None, "")}
        extracted_cells = {row.sources.get("content") for row in page.draft.active_rows}
        assert extracted_cells == content_cells, "교육용 C3 시트의 변경 내용이 누락되거나 중복 매핑되었습니다."
        assert page.save(), page.notice.text()
        wait_until(lambda: not page.busy, timeout=120)
        assert page.state == "saved", page.result_label.text() + page.notice.text()
        page.requery_button.click()
        assert window.route == "versions"
        versions = window.pages["versions"]
        wait_until(lambda: not versions.read_request.busy, timeout=30)
        assert versions.catalog is not None and versions.catalog.releases
        assert versions.catalog.items
        print(f"실제 MySQL 저장·재조회: 릴리즈 {len(versions.catalog.releases)}개 / Item {len(versions.catalog.items)}개", flush=True)
    finally:
        # AI 요청이나 DB 저장 중이면 결과를 받은 뒤 화면과 클라이언트를 정리한다.
        from PySide6.QtCore import QThreadPool
        QThreadPool.globalInstance().waitForDone(240000)
        app.processEvents()
        window.close()
        window.deleteLater()
        app.processEvents()
        ai.close()
