"""SQL 가져오기 탭에서 파일·방식 검토와 병합 확인을 검증한다."""

from threading import Event, get_ident
from types import SimpleNamespace

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog, QFileDialog

from setron.features.db_import.dump import DumpFormatError
from setron.ui.app import create_window
from setron.ui.import_demo import DemoExcel
from setron.ui.import_dialogs import ImportConfirmDialog
from setron.ui.import_page import ImportPage
from test_ui_import import extracted
from test_ui_import_ver5 import own, until  # noqa: F401


def test_excel_and_sql_tabs_are_above_steps_and_preserve_excel_draft(own):
    window = own(create_window(excel=DemoExcel()))
    window.navigate("import")
    page = window.pages["import"]
    assert [page.tabs.tabText(i) for i in range(page.tabs.count()) if page.tabs.isTabVisible(i)] == ["Excel", "SQL"]
    assert page.tab_strip.isVisibleTo(window)
    assert (window.header.title.mapTo(window, QPoint()).y()
            < page.tab_strip.mapTo(window, QPoint()).y()
            < page.steps.mapTo(window, QPoint()).y())
    extracted(page)
    draft = page.draft
    page.tabs.setCurrentIndex(2)
    assert not page.steps.isVisibleTo(window) and page.sql_page.isVisibleTo(window)
    assert not page.sql_page.select_button.isEnabled()
    assert page.sql_page.status.text() == "DB 연결 필요"
    page.tabs.setCurrentIndex(0)
    assert page.steps.isVisibleTo(window) and page.draft is draft


def test_sql_requires_fresh_review_for_mode_and_confirmation_before_apply(own, tmp_path, monkeypatch):
    path = tmp_path / "백업.sql"
    path.touch()
    preview_gate, apply_gate = Event(), Event()
    ui_thread = get_ident()
    calls = []

    class Gateway:
        def preview(self, filename, mode):
            calls.append((filename, mode, get_ident()))
            assert preview_gate.wait(4)
            return SimpleNamespace(changes=2, target_rows=3, summary="대상 3행 · 변경 2행", mode=mode)

        def apply(self, plan):
            calls.append(("apply", plan.mode, get_ident()))
            assert apply_gate.wait(4)
            return SimpleNamespace(summary="병합 2행")

    page = own(ImportPage(sql_importer=Gateway()))
    page.tabs.setCurrentIndex(2)
    sql = page.sql_page
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), "SQL 파일 (*.sql)"))
    sql.select_button.click()
    assert path.name in sql.file_label.text() and not sql.apply_button.isEnabled()
    assert sql.mode_combo.currentData() == "review" and not calls
    sql.preview_button.click()
    assert sql.busy and not page.tabs.isEnabled() and not sql.mode_combo.isEnabled()
    preview_gate.set()
    until(lambda: not sql.busy)
    assert [call[:2] for call in calls] == [(path, "review")]
    assert calls[0][2] != ui_thread
    assert sql.apply_button.isEnabled()

    sql.mode_combo.setCurrentIndex(sql.mode_combo.findData("overwrite"))
    assert not sql.apply_button.isEnabled() and not sql.details.text()
    sql.preview_button.click()
    until(lambda: not sql.busy)
    assert [call[:2] for call in calls] == [(path, "review"), (path, "overwrite")]
    assert sql.apply_button.isEnabled()
    confirmed = False

    def confirm(dialog):
        assert dialog.windowTitle() == "SQL 데이터 병합 확인"
        assert dialog.cancel_button.isDefault()
        assert "같은 항목 덮어쓰기" in dialog.message.text()
        assert "대상 3행 · 변경 2행" in dialog.message.text()
        return QDialog.DialogCode.Accepted if confirmed else QDialog.DialogCode.Rejected

    saved = []
    sql.saved.connect(saved.append)
    monkeypatch.setattr(ImportConfirmDialog, "exec", confirm)
    sql.apply_button.click()
    assert len(calls) == 2 and sql.apply_button.isEnabled()
    confirmed = True
    sql.apply_button.click()
    assert sql.busy and not sql.select_button.isEnabled() and not sql.apply_button.isEnabled()
    apply_gate.set()
    until(lambda: not sql.busy)
    assert calls[-1][:2] == ("apply", "overwrite") and calls[-1][2] != ui_thread
    assert len(saved) == 1 and saved[0].summary == "병합 2행"
    assert sql.status.text() == "가져오기 완료" and not sql.apply_button.isEnabled()
    assert page.request_kind("release") and page.tabs.currentIndex() == 0


def test_sql_invalid_file_and_no_changes_cannot_apply(own, tmp_path, monkeypatch):
    path = tmp_path / "backup.sql"
    problem = DumpFormatError("알 수 없는 <값>")

    class Gateway:
        def preview(self, *_):
            if problem is not None:
                raise problem
            return SimpleNamespace(changes=0, target_rows=5, summary="동일 5행")

    sql = own(ImportPage(sql_importer=Gateway())).sql_page
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    sql.select_button.click()
    sql.preview_button.click()
    until(lambda: not sql.busy)
    assert sql.details.textFormat() == Qt.TextFormat.PlainText and sql.details.text() == "알 수 없는 <값>"
    assert sql.status.text() == "검토 실패" and not sql.apply_button.isEnabled()

    problem = None
    sql.preview_button.click()
    until(lambda: not sql.busy)
    assert sql.status.text() == "가져올 변경분 없음" and not sql.apply_button.isEnabled()
