"""헤더의 '데이터 내보내기'로 DB 전체 SQL 내보내기를 실행하는 연결을 검증한다 (Jira S15P31B106-76)."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from setron.features.export.schemas import ExportResult, ExportStatus
from setron.ui.app import create_window
from setron.ui.database_source import sql_exporter_from_env


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_export_action_is_disabled_without_db(qapp):
    window = create_window()

    assert not window.header.actions["export"].isEnabled()
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
