"""SQL 출력 작업의 UI 응답성·실패 복구와 결과 미확인 시 캐시 폐기를 검증한다."""

from pathlib import Path
from threading import Event, get_ident

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

from setron.features.export.schemas import ExportResult, ExportStatus
from setron.ui.app import create_window
from test_ui_import_ver5 import own, until  # noqa: F401


def test_interactive_sql_export_keeps_ui_responsive_and_blocks_duplicates_and_close(own, tmp_path, monkeypatch):
    started, release = Event(), Event()
    ui_thread = get_ident()
    calls, messages, ticks = [], [], []
    path = tmp_path / "backup.sql"

    def export(destination):
        calls.append((destination, get_ident()))
        started.set()
        assert release.wait(4)
        destination.write_text("complete", encoding="utf-8")
        return ExportResult(ExportStatus.SAVED, destination, "13개 테이블")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_, **__: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[-1]))
    window = own(create_window(db_exporter=export))
    window.navigate("export")
    page = window.pages["export"]
    page.buttons["sql"].click()
    try:
        until(started.is_set)
        QTimer.singleShot(0, lambda: ticks.append(True))
        until(lambda: bool(ticks))
        assert calls == [(path, calls[0][1])] and calls[0][1] != ui_thread
        assert window.sql_export_request.busy and not messages
        assert not page.buttons["sql"].isEnabled()
        assert not window.header.actions["export"].isEnabled()
        assert not window.close_guard()
        duplicate = window.export_sql(tmp_path / "duplicate.sql")
        assert not duplicate.saved and len(calls) == 1
        assert not (tmp_path / "duplicate.sql").exists()
    finally:
        release.set()
        until(lambda: not window.sql_export_request.busy)
    assert path.read_text(encoding="utf-8") == "complete" and len(messages) == 1
    assert window.close_guard() and page.buttons["sql"].isEnabled()
    assert window.header.actions["export"].isEnabled()


@pytest.mark.parametrize("raise_error", [False, True])
def test_async_sql_failure_removes_reserved_copy_and_allows_retry(own, tmp_path, monkeypatch, raise_error):
    path = tmp_path / "backup.sql"
    path.write_text("original", encoding="utf-8")
    warnings, confirmations, threads = [], [], []
    ui_thread = get_ident()

    def confirm(_, requested):
        confirmations.append((requested, get_ident()))
        return True

    def export(destination):
        threads.append(get_ident())
        destination.write_text("partial", encoding="utf-8")
        if raise_error:
            raise RuntimeError("private connection details")
        return ExportResult(ExportStatus.FAILED, destination, "연결 오류")

    monkeypatch.setattr("setron.ui.app.confirm_numbered_save", confirm)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_, **__: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    window = own(create_window(db_exporter=export))
    for attempt in range(2):
        window.export_sql()
        until(lambda: not window.sql_export_request.busy)
        assert len(warnings) == attempt + 1
        assert window.close_guard() and window.header.actions["export"].isEnabled()
        assert path.read_text(encoding="utf-8") == "original"
        assert not path.with_name("backup (1).sql").exists()
    assert confirmations == [(path, ui_thread)] * 2
    assert all(thread != ui_thread for thread in threads)
    assert all("private connection details" not in warning for warning in warnings)


def test_uncertain_sql_import_discards_applied_query_and_cached_results(own, monkeypatch):
    window = own(create_window(sql_importer=object()))
    window.query_panel.apply_draft()
    dashboard = window.pages["dashboard"]
    until(lambda: dashboard.snapshot is not None)
    old_generation = window.pages["ai"].generation
    data = window.pages["data"]
    invalidated = []
    monkeypatch.setattr(data, "invalidate", lambda: invalidated.append(True))
    assert window.query_state.applied_is_current
    window.pages["import"].sql_page.result_uncertain.emit()
    assert dashboard.snapshot is None
    assert not window.query_state.applied_is_current
    assert window.pages["ai"].generation > old_generation
    assert invalidated == [True]
    assert "SQL 저장 결과 확인 필요" in window.notice.text()


def test_deletion_waits_for_backup_before_opening_or_applying_confirmation(own, tmp_path, monkeypatch):
    from test_ui_import_ver5 import Gateway, approve, select_period

    started, release = Event(), Event()
    gateway = Gateway()
    path = tmp_path / "backup.sql"

    def export(destination):
        started.set()
        assert release.wait(4)
        destination.write_text("backup before delete", encoding="utf-8")
        return ExportResult(ExportStatus.SAVED, destination, "저장 완료")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_, **__: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    window = own(create_window(db_exporter=export, import_delete_gateway=gateway))
    window.navigate("import")
    deletion = window.pages["import"].deletion
    select_period(deletion)
    deletion.open_dialog = approve
    assert deletion.delete_button.isEnabled()
    deletion.backup_button.click()
    try:
        until(started.is_set)
        assert deletion.backup_busy and not deletion.delete_button.isEnabled()
        assert not deletion.confirm_delete() and not gateway.calls
    finally:
        release.set()
        until(lambda: not window.sql_export_request.busy)
    assert not deletion.backup_busy and deletion.delete_button.isEnabled()
    assert path.read_text(encoding="utf-8") == "backup before delete"
    assert deletion.confirm_delete()
    until(lambda: not deletion.busy)
    assert len(gateway.calls) == 1


def test_backup_started_while_delete_dialog_is_open_blocks_confirmed_delete(own):
    from setron.ui.import_delete import ImportDeletePage
    from test_ui_import_ver5 import Gateway, select_period

    gateway = Gateway()
    deletion = own(ImportDeletePage(gateway))
    select_period(deletion)

    def confirm(dialog):
        dialog.phrase.setText("삭제")
        deletion.set_backup_busy(True)
        return True

    deletion.open_dialog = confirm
    assert not deletion.confirm_delete() and not gateway.calls
    assert "DB 백업이 끝난 뒤" in deletion.notice.text()


def test_rejected_worker_start_releases_reserved_output(own, tmp_path, monkeypatch):
    path = tmp_path / "backup.sql"
    warnings = []
    window = own(create_window(db_exporter=lambda _: pytest.fail("중복 요청은 실행하지 않음")))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_, **__: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(window.sql_export_request, "start", lambda _: False)
    result = window.export_sql()
    assert not result.saved and not path.exists()
    assert warnings == ["다른 내보내기가 진행 중입니다."]
