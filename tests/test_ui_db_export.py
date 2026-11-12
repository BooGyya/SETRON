"""데이터 내보내기 화면과 기존 내보내기 공급자 연결을 검증한다."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from setron.features.export.schemas import ExportResult, ExportStatus
from setron.infrastructure.database import Database, DatabaseConnectionError
from setron.ui.app import create_window
from setron.ui.database_source import data_exporter_from_env, sql_exporter_from_env
from setron.ui.import_dialogs import ImportConfirmDialog
from test_release_revision import revision_storage  # noqa: F401


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_export_action_is_disabled_without_db(qapp):
    window = create_window()

    assert not window.header.actions["export"].isEnabled()
    window.nav["export"].click()
    assert window.route == "export"
    assert not any(action.isEnabled() for action in window.pages["export"].buttons.values())
    window.close()


def test_sidebar_export_confirms_and_numbers_duplicate_names(qapp, tmp_path, monkeypatch):
    from test_ui_management import FakeGateway

    calls, messages, prompts = [], [], []
    confirmed = False

    def exporter(key):
        def save(path):
            calls.append((key, Path(path)))
            Path(path).write_text(key, encoding="utf-8")
            return ExportResult(ExportStatus.SAVED, Path(path), "저장 완료")
        return save

    window = create_window(db_exporter=exporter("sql"), data_exporter=exporter("summary"),
                           management_gateway=FakeGateway())
    page = window.pages["export"]
    original_exec = ImportConfirmDialog.exec

    def confirm(dialog):
        prompts.append(dialog.message.text())
        assert dialog.save_button.text() == "확인"
        assert dialog.cancel_button.text() == "취소"
        action = dialog.save_button if confirmed else dialog.cancel_button
        QTimer.singleShot(0, action.click)
        return original_exec(dialog)

    monkeypatch.setattr(ImportConfirmDialog, "exec", confirm)
    monkeypatch.setattr(QMessageBox, "information", lambda _, title, text: messages.append(text))
    monkeypatch.setattr(QMessageBox, "warning", lambda _, title, text: pytest.fail(f"{title}: {text}"))
    try:
        window.nav["export"].click()
        assert window.route == "export" and list(page.buttons) == ["sql", "summary"]
        assert all(action.isEnabled() for action in page.buttons.values())
        for key, suffix in (("sql", ".sql"), ("summary", ".xlsx")):
            path = tmp_path / f"setron_db_20261110_2158{suffix}"

            def select(*_, options):
                assert options & QFileDialog.Option.DontConfirmOverwrite
                return str(path), ""

            monkeypatch.setattr(QFileDialog, "getSaveFileName", select)
            prompt_count = len(prompts)
            page.buttons[key].click()
            assert calls[-1] == (key, path) and len(prompts) == prompt_count
            path.write_text("기존 파일", encoding="utf-8")
            call_count = len(calls)
            confirmed = False
            page.buttons[key].click()
            assert len(prompts) == prompt_count + 1 and path.name in prompts[-1]
            assert len(calls) == call_count
            assert not path.with_name(f"{path.stem} (1){suffix}").exists()

            confirmed = True
            original = path
            for number in (1, 2, 3):
                page.buttons[key].click()
                saved = original.with_name(f"{original.stem} ({number}){suffix}")
                assert calls[-1] == (key, saved)
                assert saved.read_text(encoding="utf-8") == key
                assert path.name in prompts[-1]
                assert str(saved) in messages[-1]
                path = saved

            path = original.with_name(f"{original.stem} (2) (1){suffix}")
            path.write_text("이전 사본", encoding="utf-8")
            page.buttons[key].click()
            assert calls[-1] == (key, original.with_name(f"{original.stem} (4){suffix}"))
            assert path.read_text(encoding="utf-8") == "이전 사본"
            assert len(prompts) == prompt_count + 5
            assert original.read_text(encoding="utf-8") == "기존 파일"

        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_, **__: ("", ""))
        page.buttons["sql"].click()
        assert len(calls) == 10 and len(prompts) == 10
    finally:
        window.close()


def test_numbered_export_failure_removes_only_new_partial_file(qapp, tmp_path):
    requested = tmp_path / "요약.xlsx"
    requested.write_bytes(b"original")

    def save(path):
        path.write_bytes(b"partial")
        return ExportResult(ExportStatus.FAILED, path, "저장 실패")

    window = create_window(data_exporter=save)
    try:
        result = window.export_data(requested)
        assert not result.saved and result.path == tmp_path / "요약 (1).xlsx"
        assert requested.read_bytes() == b"original"
        assert not result.path.exists()
    finally:
        window.close()


def test_export_action_runs_given_exporter(qapp, tmp_path):
    calls = []

    def exporter(path):
        calls.append(path)
        return ExportResult(ExportStatus.SAVED, Path(path), "테이블 13개 · 0행")

    window = create_window(db_exporter=exporter)
    result = window.export_sql(tmp_path / "백업.sql")

    assert window.header.actions["export"].isEnabled()
    assert calls == [tmp_path / "백업.sql"] and result.saved
    window.close()


def test_exporter_exists_only_with_db_settings():
    assert sql_exporter_from_env({}) is None
    assert callable(sql_exporter_from_env({"SETRON_DB_NAME": "setron"}))


@pytest.mark.parametrize("settings", [
    {"SETRON_DB_NAME": "setron"},
    {"SETRON_DB_NAME": "setron", "SETRON_DB_HOST": "127.0.0.1", "SETRON_DB_PORT": "잘못된 포트",
     "SETRON_DB_USER": "user", "SETRON_DB_PASSWORD": "password"},
])
def test_exporter_reports_invalid_db_settings(settings, tmp_path):
    path = tmp_path / "백업.sql"

    result = sql_exporter_from_env(settings)(path)

    assert result.status is ExportStatus.FAILED
    assert result.path == path
    assert result.message.startswith("DB 자료를 읽지 못했습니다:")
    assert not path.exists()


@pytest.mark.parametrize("status", [ExportStatus.SAVED, ExportStatus.FAILED])
def test_exporter_disposes_database_after_result(monkeypatch, tmp_path, status):
    calls = []
    database = SimpleNamespace(dispose=lambda: calls.append("disposed"))
    path = tmp_path / "백업.sql"
    expected = ExportResult(status, path, "내보내기 결과")

    def export(db, destination):
        assert db is database and destination == path
        calls.append("exported")
        return expected

    monkeypatch.setattr(Database, "from_env", lambda environ: database)
    monkeypatch.setattr("setron.features.db_export.service.export_database", export)

    result = sql_exporter_from_env({"SETRON_DB_NAME": "setron"})(path)

    assert result is expected
    assert calls == ["exported", "disposed"]


def test_exporter_reports_db_error_and_disposes_database(monkeypatch, tmp_path):
    disposed = []
    database = SimpleNamespace(dispose=lambda: disposed.append(True))

    def export(db, path):
        raise DatabaseConnectionError("MySQL 연결을 확인하세요.")

    monkeypatch.setattr(Database, "from_env", lambda environ: database)
    monkeypatch.setattr("setron.features.db_export.service.export_database", export)

    result = sql_exporter_from_env({"SETRON_DB_NAME": "setron"})(tmp_path / "백업.sql")

    assert result.status is ExportStatus.FAILED
    assert result.message == "DB 자료를 읽지 못했습니다: MySQL 연결을 확인하세요."
    assert disposed == [True]


def test_export_action_runs_data_exporter(qapp, tmp_path):
    """관리 데이터 Excel 내보내기도 같은 버튼으로 실행한다 (BE-021)."""
    calls = []

    def exporter(path):
        calls.append(path)
        return ExportResult(ExportStatus.SAVED, Path(path), "설비 현황 0행")

    window = create_window(data_exporter=exporter)
    result = window.export_data(tmp_path / "관리데이터.xlsx")

    assert window.header.actions["export"].isEnabled()
    assert calls == [tmp_path / "관리데이터.xlsx"] and result.saved
    window.close()


def test_sidebar_excel_export_saves_only_equipment_and_release_notes(qapp, revision_storage, tmp_path, monkeypatch):
    from openpyxl import load_workbook

    from setron.features.export.management import save_management_summary
    from test_management_db_source import connected

    connected(revision_storage)
    path = tmp_path / "두시트.xlsx"
    messages = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_, **__: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[-1]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: pytest.fail(args[-1]))
    window = create_window(data_exporter=lambda destination: save_management_summary(revision_storage, destination))
    try:
        window.nav["export"].click()
        page = window.pages["export"]
        assert page.details["summary"].text() == "설비현황표와 릴리즈노트 두 시트를 Excel로 저장합니다."
        page.buttons["summary"].click()
        book = load_workbook(path)
        try:
            assert book.sheetnames == ["설비현황표", "릴리즈노트"]
            assert book["설비현황표"].max_row == 2
            assert book["릴리즈노트"].cell(2, 10).value == "원본 안전 개선"
        finally:
            book.close()
        assert len(messages) == 1 and "설비현황표 1행 · 릴리즈노트 1행" in messages[0]
    finally:
        window.close()


def test_data_exporter_exists_only_with_db_settings(tmp_path):
    assert data_exporter_from_env({}) is None
    result = data_exporter_from_env({"SETRON_DB_NAME": "setron"})(tmp_path / "관리데이터.xlsx")
    assert not result.saved and result.message.startswith("DB 자료를 읽지 못했습니다")


def test_summary_and_full_management_exports_coexist(qapp, tmp_path):
    """원격 요약 Excel과 로컬 전체 관리 내보내기가 같은 창에서 각각 유지된다."""
    from test_ui_management import FakeGateway
    calls = []
    def summary(path):
        calls.append(path)
        return ExportResult(ExportStatus.SAVED, Path(path), "설비 현황 0행")
    window = create_window(management_gateway=FakeGateway(), data_exporter=summary)
    try:
        full = []
        window.management_gateway.export_management = lambda path: full.append(path) or ExportResult(
            ExportStatus.SAVED, Path(path), "관리 테이블")
        assert window.export_data(tmp_path / "요약.xlsx").saved
        assert window.export_management_xlsx(tmp_path / "전체.xlsx").saved
        assert calls == [tmp_path / "요약.xlsx"] and full == [tmp_path / "전체.xlsx"]
    finally:
        window.close()
