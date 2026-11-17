"""Excel 저장 결과 미확인과 비동기 출력의 화면·파일 보호를 검증한다."""

from pathlib import Path
from threading import Event, get_ident

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

from setron.features.excel_import.service import MemoryImportSaver
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseSaveError
from setron.ui.app import create_window
from setron.ui.import_page import ImportPage
from test_import_validation import draft, row
from test_ui_import_ver5 import own, until  # noqa: F401


def test_interactive_excel_export_keeps_ui_responsive_and_blocks_other_exports(own, tmp_path, monkeypatch):
    started, release = Event(), Event()
    path = tmp_path / "summary.xlsx"
    ui_thread, calls, messages, ticks = get_ident(), [], [], []

    def export(destination):
        calls.append((destination, get_ident()))
        started.set()
        assert release.wait(4)
        destination.write_bytes(b"complete")
        return ExportResult(ExportStatus.SAVED, destination, "저장 완료")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_, **__: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[-1]))
    window = own(create_window(data_exporter=export, db_exporter=lambda _: pytest.fail("중복 내보내기")))
    window.navigate("export")
    page = window.pages["export"]
    page.buttons["summary"].click()
    try:
        until(started.is_set)
        QTimer.singleShot(0, lambda: ticks.append(True))
        until(lambda: bool(ticks))
        assert calls[0] == (path, calls[0][1]) and calls[0][1] != ui_thread
        assert window.data_export_request.busy and not messages
        assert not page.buttons["summary"].isEnabled() and not page.buttons["sql"].isEnabled()
        assert not window.header.actions["export"].isEnabled() and not window.close_guard()
        assert not window.export_data(tmp_path / "duplicate.xlsx").saved
        assert not window.export_sql(tmp_path / "backup.sql").saved
        assert not (tmp_path / "duplicate.xlsx").exists() and len(calls) == 1
        assert window.pages["import"].deletion.backup_busy
    finally:
        release.set()
        until(lambda: not window.data_export_request.busy)
    assert path.read_bytes() == b"complete" and len(messages) == 1
    assert window.close_guard() and page.buttons["summary"].isEnabled()
    assert not window.pages["import"].deletion.backup_busy


@pytest.mark.parametrize("outcome", ["failure", "exception"])
def test_excel_export_failure_preserves_existing_file_and_can_retry(own, tmp_path, monkeypatch, outcome):
    path = tmp_path / "summary.xlsx"
    path.write_bytes(b"original")
    warnings, confirmations = [], []
    ui_thread = get_ident()

    def confirm(_, requested):
        confirmations.append((requested, get_ident()))
        return True

    def export(destination):
        destination.write_bytes(b"partial")
        if outcome == "exception":
            raise RuntimeError("private database details")
        return ExportResult(ExportStatus.FAILED, destination, "검증용 저장 실패")

    monkeypatch.setattr("setron.ui.app.confirm_numbered_save", confirm)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_, **__: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    window = own(create_window(data_exporter=export))
    for attempt in range(2):
        window.export_data()
        until(lambda: not window.data_export_request.busy)
        assert path.read_bytes() == b"original" and not path.with_name("summary (1).xlsx").exists()
        assert len(warnings) == attempt + 1 and window.close_guard()
    assert confirmations == [(path, ui_thread)] * 2
    assert all("private database details" not in warning for warning in warnings)


def test_rejected_excel_worker_start_removes_only_reserved_output(own, tmp_path, monkeypatch):
    path = tmp_path / "summary.xlsx"
    window = own(create_window(data_exporter=lambda _: pytest.fail("시작하지 않은 출력")))
    warnings = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_, **__: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(window.data_export_request, "start", lambda _: False)
    result = window.export_data()
    assert not result.saved and not path.exists() and warnings == ["다른 내보내기가 진행 중입니다."]


@pytest.mark.parametrize("receipt,error", [
    (None, DatabaseCommitUnknownError("DB 저장 결과를 확인해 주세요.")),
    (None, RuntimeError("private connection details")),
    ({}, None),
])
def test_uncertain_excel_save_invalidates_cached_results_and_locks_draft(own, monkeypatch, receipt, error):
    window = own(create_window(import_saver=MemoryImportSaver()))
    window.query_panel.apply_draft()
    dashboard = window.pages["dashboard"]
    until(lambda: dashboard.snapshot is not None)
    ai_generation = window.pages["ai"].generation
    data = window.pages["data"]
    invalidated, saved = [], []
    monkeypatch.setattr(data, "invalidate", lambda: invalidated.append(True))
    page = window.pages["import"]
    page.draft = draft(row())
    page.workbook_name = page.draft.workbook_name
    page.saved.connect(saved.append)

    page._save_completed(receipt, error)

    assert page.state == "unknown" and page.locked and not page.save_button.isEnabled()
    assert not page.save() and not saved and invalidated == [True]
    assert dashboard.snapshot is None and not window.query_state.applied_is_current
    assert window.pages["ai"].generation > ai_generation
    assert "Excel 저장 결과 확인 필요" in window.notice.text()
    assert "private connection details" not in page.result_label.text()


def test_confirmed_excel_failure_preserves_draft_without_uncertain_signal(own):
    page = own(ImportPage(saver=MemoryImportSaver()))
    original = page.draft = draft(row())
    uncertain = []
    page.result_uncertain.connect(lambda: uncertain.append(True))
    page._save_completed(None, DatabaseSaveError("저장 전체 취소"))
    assert page.state == "failed" and page.draft is original and not uncertain


def test_default_excel_export_needs_no_template_mapping_and_preserves_selected_scope(own, tmp_path):
    from setron.features.export.management_files import ImportedFile
    from test_ui_export_mentoring6 import _source_with_areas

    calls = []

    def export(path, **options):
        calls.append(options)
        Path(path).write_bytes(b"default workbook")
        return ExportResult(ExportStatus.SAVED, Path(path))

    window = own(create_window(source=_source_with_areas(), data_exporter=export))
    window.navigate("export")
    page = window.pages["export"]
    assert not hasattr(page, "template_requested") and not hasattr(page, "custom_template_button")
    assert page.buttons["summary"].isEnabled()
    assert window.export_data(tmp_path / "all.xlsx").saved
    assert calls == [{}]

    page.set_file_support(True)
    page.set_files((ImportedFile(7, "현황.xlsx", "설비현황표", None),))
    page.selected_files_button.click()
    page.file_checks[0].setChecked(True)
    memory = page.production_area.findData("MEMORY")
    assert memory >= 0
    page.production_area.setCurrentIndex(memory)
    assert window.export_data(tmp_path / "memory.xlsx").saved
    assert calls[-1] == {"production_area": "MEMORY", "batch_ids": (7,)}
    assert (tmp_path / "memory.xlsx").read_bytes() == b"default workbook"
