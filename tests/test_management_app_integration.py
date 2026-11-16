"""실제 관리 저장·앱 재조회·다음 AI 요청·XLSX 출력을 한 경로로 검증한다."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from threading import Event
import time

import pytest
from openpyxl import load_workbook
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from sqlalchemy import LargeBinary, create_engine, event

from setron.features.ai.schemas import AIResult, AIStatus, AITask, AnalysisAnswer
from setron.features.management import ManagementService
from setron.features.storage.service import StorageService
from setron.features.targets.schemas import CandidateConfirmation
from setron.features.targets.service import TargetService
from setron.infrastructure.ai_client import ResponseSource
from setron.infrastructure.database import Database, DatabaseCommitUnknownError, DatabaseSaveError
from setron.ui.app import create_window
from setron.ui.database_source import DatabaseDashboardSource, load_dashboard_source
from setron.ui.management_data import ManagementGateway
from setron.ui.query_state import QuerySelection, TargetN
from setron.ui.risk_rule_save import RiskRuleSaveController
from setron.ui.theme import install_theme

from test_management_db_source import connected
from test_management_service import edit, observe
from test_release_revision import all_rows, revision_storage


def test_header_export_menu_routes_both_xlsx_actions(app, monkeypatch):
    """실제 헤더 버튼이 관리 전체와 현재 조회 출력 함수를 각각 호출한다."""
    from test_ui_management import FakeGateway
    window = create_window(management_gateway=FakeGateway())
    calls = []
    selected = ["관리 자료 XLSX"]
    class Menu:
        def __init__(self, parent):
            self.actions = []
        def addAction(self, text):
            action = SimpleNamespace(text=text, setEnabled=lambda enabled: None)
            self.actions.append(action)
            return action
        def exec(self, position):
            return next(action for action in self.actions if action.text == selected[0])
    try:
        window.export_management_xlsx = lambda: calls.append("management")
        window.export_current_xlsx = lambda: calls.append("status")
        monkeypatch.setitem(create_window.__globals__, "QMenu", Menu)
        window.header.actions["export"].click()
        window.query_panel.apply_draft()
        selected[0] = "현재 조회 현황 XLSX"
        window.header.actions["export"].click()
        assert calls == ["management", "status"]
    finally:
        window.close()


def test_interactive_export_failure_explains_locked_save_state(app, monkeypatch):
    """저장 결과 미확인 상태의 출력 거절을 파일 선택 후에도 사용자에게 표시한다."""
    from test_ui_management import FakeGateway
    window = create_window(management_gateway=FakeGateway())
    messages = []
    try:
        controller = window.findChild(RiskRuleSaveController)
        assert controller is not None
        controller.locked = True
        monkeypatch.setitem(create_window.__globals__, "QFileDialog",
                            SimpleNamespace(getSaveFileName=lambda *args: ("unused.xlsx", "")))
        monkeypatch.setitem(create_window.__globals__, "QMessageBox",
                            SimpleNamespace(warning=lambda *args: messages.append(args[-1])))
        result = window.export_management_xlsx()
        assert not result.saved
        assert len(messages) == 1 and "저장 상태" in messages[0]
    finally:
        window.close()


def wait_for(app, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not condition():
        app.processEvents()
        QTest.qWait(5)
    assert condition(), "앱의 비동기 작업을 기다리는 시간이 초과됐습니다."


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    install_theme(application)
    return application


@pytest.fixture
def file_storage(revision_storage):
    """기존 스키마를 독립 파일 DB로 복사해 실제 워커마다 다른 연결을 사용한다."""
    with TemporaryDirectory(prefix="setron-management-app-") as directory:
        path = Path(directory) / "management.sqlite"
        connection = revision_storage.database.engine.raw_connection()
        destination = sqlite3.connect(path)
        try:
            connection.driver_connection.backup(destination)
        finally:
            destination.close()
            connection.close()
        engine = create_engine(f"sqlite:///{path.as_posix()}")

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        storage = StorageService(Database(engine))
        for name in storage.repository.metadata.tables:
            for column in storage.repository.table(name).c:
                if isinstance(column.type, LargeBinary):
                    column.type.length = revision_storage.repository.table(name).c[column.name].type.length
        try:
            yield storage
        finally:
            engine.dispose()


@contextmanager
def running_window(app, storage, *, gateway=None, extra_site=False):
    release_id, item_id, snapshot, _ = connected(storage)
    TargetService(storage).confirm(1, 1, (CandidateConfirmation(
        release_id, "검증용 모델 적용 표시", "검증용 확인 순서", source_text="검증용 자료"),))
    if extra_site:
        other = observe(storage, release_id, code="EQ-2")
        with storage.transaction() as tx:
            tx.update("equipment_snapshot", {"snapshot_id": other.snapshot_id}, {"site": "평택"})
    calls = []

    def analyzer(question, facts):
        calls.append(facts)
        return AIResult(AITask.ANALYSIS, AIStatus.OK, "확인",
                        AnalysisAnswer("검증용 분석", (), (), ()), ResponseSource.REAL)

    source = DatabaseDashboardSource(lambda: load_dashboard_source(storage))
    window = create_window(source, management_gateway=gateway or ManagementGateway(storage), ai_analyzer=analyzer)
    window.resize(1280, 900)
    window.show()
    window.query_panel.set_target_draft((TargetN(1, 1, release_id),))
    window.query_panel.apply_draft()
    wait_for(app, lambda: window.pages["dashboard"].snapshot is not None)
    try:
        yield window, source, calls, release_id, item_id, snapshot
    finally:
        window.close()
        QThreadPool.globalInstance().waitForDone(5000)
        app.processEvents()
        window.deleteLater()
        app.processEvents()


def wait_for_save(window, app):
    page = window.pages["data"]
    wait_for(app, lambda: not page.save_request.busy and not window.management_refresh.busy
             and not window.management_saving)


def save_data_changes(window, app, table, key, changes, *, wait=True):
    """현행 데이터 관리의 저장 요청과 앱의 저장 후 재조회 연결을 함께 거친다."""
    page = window.pages["data"]
    assert page.save_changes(((table, key, changes),), page.sheet_panel)
    if wait:
        wait_for_save(window, app)
    return page


def test_real_management_save_refreshes_graphs_items_risk_and_next_ai_reads_db(app, file_storage):
    """수정창 저장은 위험·그래프·Item을 갱신하고 AI는 다음 명시 요청에서 최신 DB를 읽는다."""
    with running_window(app, file_storage) as (window, source, calls, release_id, item_id, snapshot):
        dashboard, heatmap, items, ai = (window.pages[key] for key in ("dashboard", "heatmap", "items", "ai"))
        query = window.query_state.applied
        assert dashboard.snapshot.risky_ids
        save_data_changes(window, app, "release_version_item",
                          {"release_version_id": release_id, "item_id": item_id}, {"item_state": "APPLIED"})
        wait_for(app, lambda: dashboard.snapshot is not None and items.data is not None and not items.read_request.busy)
        assert dashboard.snapshot.query is query
        assert dashboard.snapshot.risky_ids == ()
        assert heatmap.snapshot is dashboard.snapshot
        assert items.data.versions.version_items[0].state.value == "APPLIED"
        assert calls == []
        # 앱에 알림이 없는 별도 저장도 다음 AI 요청에서 다시 읽어야 한다.
        service = ManagementService(file_storage)
        service.save((edit(service, "release_version_item", {"release_version_id": release_id, "item_id": item_id},
                           {"item_state": "NOT_APPLIED"}),))
        assert dashboard.snapshot.risky_ids == ()
        window.navigate("ai")
        assert ai.analyze()
        wait_for(app, lambda: calls and ai.busy is None)
        assert calls[-1]["위험 후보 설비"] == 1
        assert calls[-1]["설비 수(HPA 제외)"] == 1
        assert dashboard.snapshot.risky_ids
        assert heatmap.snapshot is dashboard.snapshot


def test_release_edit_button_opens_selected_release_form_on_first_navigation(app, file_storage):
    """첫 진입에 불러온 릴리즈의 수정은 같은 버전 관리 화면에서 대상을 유지한다."""
    with running_window(app, file_storage) as (window, source, calls, release_id, item_id, snapshot):
        versions = window.pages["versions"]
        window.navigate("versions")
        wait_for(app, lambda: versions.catalog is not None and not versions.read_request.busy)
        versions.select_tab(1)
        versions.open_row(0)
        assert versions.selected_id == release_id
        versions.edit_button.click()
        form = versions.release_form
        assert window.route == "versions" and form.isVisible()
        assert form.entry.release.release_version_id == release_id
        assert form.catalog is versions.catalog and form.raw.text() == "v1"
        assert (form.model_id, form.family_id) == (1, 1)
        assert "management" not in window.pages and "management" not in window.nav
        assert all_rows(file_storage, "release_version")[0]["version_raw"] == "v1"
        assert versions.editor is None


def test_management_and_current_scope_xlsx_include_latest_saved_values(app, file_storage):
    """관리 전체 출력과 선택 사업장 현황 출력이 같은 저장값과 서로 다른 범위를 지킨다."""
    with running_window(app, file_storage, extra_site=True) as (window, source, calls, release_id, item_id, snapshot):
        window.query_panel.set_draft(QuerySelection(site_key="hwaseong"))
        window.query_panel.apply_draft()
        wait_for(app, lambda: window.pages["dashboard"].snapshot is not None)
        assert window.pages["dashboard"].snapshot.summary.equipment_count == 1
        save_data_changes(window, app, "equipment_snapshot", {"snapshot_id": snapshot.snapshot_id},
                          {"line": "수정된 화성 라인"})
        wait_for(app, lambda: window.pages["dashboard"].snapshot is not None)
        with TemporaryDirectory(prefix="setron-management-output-") as directory:
            management_path, status_path = Path(directory) / "관리.xlsx", Path(directory) / "조회.xlsx"
            managed, status = window.export_management_xlsx(management_path), window.export_current_xlsx(status_path)
            assert managed.saved, managed.message
            assert status.saved, status.message
            book = load_workbook(management_path)
            try:
                sheet = book["equipment_snapshot"]
                columns = {cell.value: cell.column for cell in sheet[1]}
                assert sheet.max_row == 3
                assert sheet.cell(2, columns["line"]).value == "수정된 화성 라인"
                assert {sheet.cell(row, columns["site"]).value for row in (2, 3)} == {"화성", "평택"}
            finally:
                book.close()
            book = load_workbook(status_path)
            try:
                assert book["요약"]["B2"].value == 1
                assert book["조회 조건"]["A3"].value == "사업장"
                assert book["조회 조건"]["B3"].value == "화성"
                rows = tuple(book["설비별 판정"].iter_rows(min_row=2, values_only=True))
                assert len(rows) == 1
                assert rows[0][0:3] == ("EQ-1", "화성", "수정된 화성 라인")
            finally:
                book.close()


def test_line_rename_updates_query_choices_and_reapplies_same_query(app, file_storage):
    """저장으로 Line 선택지가 바뀌면 선택지를 새로 읽고, 여전히 유효한 같은 조회 조건을 다시 적용한다."""
    with running_window(app, file_storage) as (window, source, calls, release_id, item_id, snapshot):
        window.query_panel.set_draft(QuerySelection(site_key="hwaseong"))
        window.query_panel.apply_draft()
        before = window.query_state.applied
        entered, release = Event(), Event()
        reload_catalog = source.reload_catalog
        def slow_reload():
            entered.set()
            assert release.wait(5)
            return reload_catalog()
        source.reload_catalog = slow_reload
        try:
            save_data_changes(window, app, "equipment_snapshot", {"snapshot_id": snapshot.snapshot_id},
                              {"line": "새 화성 라인"}, wait=False)
            wait_for(app, entered.is_set)
            progress = window.management_progress
            assert progress.isVisible() and "저장 결과 반영 중" == progress.heading.text()
            phase = progress.progress.phase
            QTest.qWait(100)
            assert progress.progress.phase != phase and window.query_state.applied is before
        finally:
            release.set()
        wait_for(app, lambda: window.pages["dashboard"].snapshot is not None)
        assert not window.management_progress.isVisible()

        applied = window.query_state.applied
        assert applied is not before and window.query_state.applied_is_current
        assert applied.selection == QuerySelection(site_key="hwaseong") and applied.targets == before.targets
        assert "hwaseong:새 화성 라인" in {o.key for o in window.query_state.catalog.lines}
        assert window.query_state.catalog == source.catalog
        assert window.pages["dashboard"].snapshot.selected_records[0].line == "새 화성 라인"


def test_refresh_failure_after_save_blocks_cached_ai_and_old_graphs(app, file_storage):
    """저장 성공 뒤 재조회 실패 시 예전 화면이나 분석 숫자를 다시 사용하지 않는다."""
    with running_window(app, file_storage) as (window, source, calls, release_id, item_id, snapshot):
        def failed_loader():
            raise DatabaseSaveError("검증용 재조회 실패")

        source._loader = failed_loader
        save_data_changes(window, app, "equipment_snapshot", {"snapshot_id": snapshot.snapshot_id},
                          {"line": "저장 후 연결 실패"})
        assert all_rows(file_storage, "equipment_snapshot")[0]["line"] == "저장 후 연결 실패"
        assert window.pages["dashboard"].snapshot is None
        ai = window.pages["ai"]
        window.navigate("ai")
        assert ai.analyze()
        wait_for(app, lambda: ai.busy is None)
        assert calls == []
        assert "최신 자료 조회 실패" in ai.status.text()


def test_unknown_commit_result_blocks_ai_and_exports_until_reconciliation(app, file_storage):
    """저장 결과 미확인 상태에서는 오래된 자료 분석·중복 저장·내보내기를 허용하지 않는다."""
    class UnknownGateway(ManagementGateway):
        def __init__(self, storage):
            super().__init__(storage)
            self.attempts = 0

        def create_risk_rule(self, values, context):
            self.attempts += 1
            raise DatabaseCommitUnknownError("검증용 결과 미확인")

    gateway = UnknownGateway(file_storage)
    with running_window(app, file_storage, gateway=gateway) as (window, source, calls, release_id, item_id, snapshot):
        controller = window.findChild(RiskRuleSaveController)
        assert controller is not None
        submit = window.pages["items"].manager.submit
        values = {"rule_type": "SAFETY_REQUIRED", "family_id": 1, "item_id": item_id,
                  "risk_reason": "결과 미확인 초안", "mapping_status": "VERIFIED"}
        assert submit(values)
        wait_for(app, lambda: not controller.busy and controller.locked)
        assert controller.state == "unknown"
        assert window.pages["dashboard"].snapshot is None
        assert not submit(values) and not controller.create_risk_rule(values)
        assert gateway.attempts == 1
        window.navigate("ai")
        assert not window.pages["ai"].analyze()
        assert calls == []
        with TemporaryDirectory(prefix="setron-management-unknown-") as directory:
            result = window.export_management_xlsx(Path(directory) / "보류.xlsx")
            assert not result.saved


def test_confirmed_save_failure_retains_draft_and_reloads_known_old_values(app, file_storage):
    """전체 취소가 확인된 실패는 초안을 보존하고 기존 저장값으로 화면을 다시 읽는다."""
    class FailedGateway(ManagementGateway):
        def save_sheet(self, batch_id, **operations):
            raise DatabaseSaveError("검증용 저장 취소")

    with running_window(app, file_storage, gateway=FailedGateway(file_storage)) as (window, source, calls, release_id, item_id, snapshot):
        page = window.pages["data"]
        sheet = page.sheet_panel
        sheet.open_batch(snapshot.batch_id)
        window.navigate("data")
        wait_for(app, lambda: not sheet.request.busy and sheet.data is not None
                 and sheet.data.batch_id == snapshot.batch_id)
        row = next(r for r, (kind, entry) in enumerate(sheet.view)
                   if kind == "row" and entry.snapshot_id == snapshot.snapshot_id)
        sheet.table.item(row, 1).setText("저장 실패 초안")  # Line 칸 (DB 값 기준 현황표, 11-11 6차)
        # 처음 보는 라인은 그 줄의 메모리/파운드리로 등록한다 (11-12). 모르면 저장을 막으므로 값을 정해 둔다
        sheet.table.item(row, 5).setText("메모리")
        assert sheet.pending and sheet.save()
        wait_for_save(window, app)
        wait_for(app, lambda: window.pages["dashboard"].snapshot is not None)
        assert not page.saving and sheet.save_button.isEnabled()
        assert sheet.pending and sheet.edits[("line", snapshot.snapshot_id)] == "저장 실패 초안"
        assert sheet.table.item(row, 1).text() == "저장 실패 초안" and "저장하지 못했습니다" in sheet.notice.text()
        assert all_rows(file_storage, "equipment_snapshot")[0]["line"] == "1라인"
        assert window.pages["dashboard"].snapshot.selected_records[0].line == "1라인"
        assert calls == []


def test_sheet_unknown_commit_is_reconciled_from_db_without_resubmitting(app, file_storage):
    from setron.ui.read_worker import ReadRequest

    class UnknownSheetGateway(ManagementGateway):
        attempts = 0

        def save_sheet(self, batch_id, **operations):
            self.attempts += 1
            super().save_sheet(batch_id, **operations)
            raise DatabaseCommitUnknownError("확정 응답만 유실된 저장")

    gateway = UnknownSheetGateway(file_storage)
    with running_window(app, file_storage, gateway=gateway) as (window, source, calls, release_id, item_id, snapshot):
        page = window.pages["data"]
        sheet = page.sheet_panel
        sheet.open_batch(snapshot.batch_id)
        window.navigate("data")
        wait_for(app, lambda: not sheet.request.busy and sheet.data is not None)
        row = next(r for r, (kind, entry) in enumerate(sheet.view)
                   if kind == "row" and entry.snapshot_id == snapshot.snapshot_id)
        sheet.table.item(row, 6).setText("v-committed")
        assert sheet.save()
        wait_for(app, lambda: page.uncertain and not page.save_request.busy)
        assert window.management_uncertain and not window.management_saving
        assert not sheet.isEnabled() and window.query_panel.apply_draft() is None
        assert "기존값 유지" not in window.notice.text()
        assert all_rows(file_storage, "equipment_version")[0]["version_raw"] == "v-committed"
        assert page.reconcile_save()
        wait_for(app, lambda: not page.uncertain and not window.management_uncertain)
        wait_for(app, lambda: not any(request.busy for request in window.findChildren(ReadRequest)))
        assert gateway.attempts == 1 and sheet.isEnabled() and not sheet.pending
        assert sheet.table.item(row, 6).text() == "v-committed"
        assert window.query_panel.apply_draft() is not None
