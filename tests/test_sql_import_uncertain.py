"""SQL 저장 결과 미확인과 확정 실패를 구분하고 검토한 계획의 재사용을 막는다."""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog

from setron.features.db_import.service import SqlImportError
from setron.infrastructure.database import (
    DatabaseCommitUnknownError, DatabaseConnectionError, DatabaseSaveError,
)
from setron.ui.import_dialogs import ImportConfirmDialog
from setron.ui.sql_import_page import SqlImportPage
from test_ui_import_ver5 import own, until  # noqa: F401


@pytest.mark.parametrize("outcome", [
    DatabaseCommitUnknownError("저장 결과 미확인 · DB 기록을 확인해 주세요."),
    RuntimeError("private-connection-details"),
    None,
    SimpleNamespace(),
])
def test_uncertain_sql_result_invalidates_without_reporting_success(own, tmp_path, monkeypatch, outcome):
    calls, saved, uncertain = [], [], []

    class Gateway:
        def preview(self, *_):
            return SimpleNamespace(changes=1, target_rows=0, summary="추가 1행")

        def apply(self, plan):
            calls.append(plan)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    page = own(SqlImportPage(Gateway()))
    page.saved.connect(saved.append)
    page.result_uncertain.connect(lambda: uncertain.append(True))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(tmp_path / "backup.sql"), ""))
    monkeypatch.setattr(ImportConfirmDialog, "exec", lambda _: QDialog.DialogCode.Accepted)
    page.select_button.click()
    page.preview_button.click()
    until(lambda: not page.busy)
    page.apply_button.click()
    until(lambda: not page.busy)

    assert len(calls) == 1 and uncertain == [True] and saved == []
    assert page.status.text() == "가져오기 결과 확인 필요"
    assert "private-connection-details" not in page.details.text()
    assert page.plan is None and not page.apply_button.isEnabled()
    page._confirm_apply()
    assert len(calls) == 1
    page.preview_button.click()
    until(lambda: not page.busy)
    assert page.apply_button.isEnabled()


@pytest.mark.parametrize("error", [
    SqlImportError("병합할 연결이 올바르지 않습니다."),
    DatabaseConnectionError("DB 연결 실패"),
    DatabaseSaveError("저장 전체 취소"),
])
def test_confirmed_sql_failure_does_not_report_uncertain_or_saved(own, error):
    page = own(SqlImportPage(object()))
    saved, uncertain = [], []
    page.saved.connect(saved.append)
    page.result_uncertain.connect(lambda: uncertain.append(True))
    page.plan = SimpleNamespace(changes=1)

    page._apply_completed(None, error)

    assert page.status.text() == "가져오기 실패" and page.details.text() == str(error)
    assert not saved and not uncertain
    assert page.plan is None and not page.apply_button.isEnabled()
