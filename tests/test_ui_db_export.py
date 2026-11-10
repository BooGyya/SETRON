"""데이터 내보내기 화면과 기존 내보내기 공급자 연결을 검증한다."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from setron.features.export.schemas import ExportResult, ExportStatus
from setron.infrastructure.database import Database, DatabaseConnectionError
from setron.ui.app import create_window
from setron.ui.database_source import data_exporter_from_env, sql_exporter_from_env


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


def test_sidebar_export_actions_use_existing_save_workflows(qapp, tmp_path, monkeypatch):
    from test_ui_management import FakeGateway, wait_for

    calls = []

    def exporter(key):
        def save(path, *_):
            calls.append((key, Path(path)))
            return ExportResult(ExportStatus.SAVED, Path(path), "저장 완료")
        return save

    gateway = FakeGateway()
    gateway.export_management = exporter("management")
    gateway.export_status = exporter("status")
    window = create_window(db_exporter=exporter("sql"), data_exporter=exporter("summary"),
                           management_gateway=gateway)
    page = window.pages["export"]
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda _, title, text: pytest.fail(f"{title}: {text}"))
    try:
        window.nav["export"].click()
        assert window.route == "export" and not page.buttons["status"].isEnabled()
        assert "조회 조건을 먼저 적용" in page.details["status"].text()
        window.navigate("dashboard")
        window.query_panel.apply_draft()
        window.nav["export"].click()
        assert all(action.isEnabled() for action in page.buttons.values())
        for key in page.buttons:
            path = tmp_path / (key + (".sql" if key == "sql" else ".xlsx"))
            monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(path), ""))
            page.buttons[key].click()
            wait_for(qapp, lambda: not window.export_request.busy)
            assert calls[-1] == (key, path)
            assert page.buttons[key].isEnabled()
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
