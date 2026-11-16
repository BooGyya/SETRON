"""보고서 히트맵 Excel 선택·비동기 저장·기존 파일 보존을 실제 통합문서로 확인한다."""

from contextlib import closing
from dataclasses import replace
from pathlib import Path
from threading import Event, get_ident

from openpyxl import load_workbook
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel

from setron.features.export.schemas import ExportResult, ExportStatus
from setron.ui import reports_page as module
from setron.ui.report_data import generate_report
from test_report_mentoring6 import report_source, request
from test_ui_import_ver5 import own, until  # noqa: F401


def page(own, tmp_path, *, heatmaps=True):
    source = report_source()
    result = own(module.ReportsPage(
        source, settings=QSettings(str(tmp_path / "reports.ini"), QSettings.Format.IniFormat),
        ai_settings=QSettings(str(tmp_path / "ai.ini"), QSettings.Format.IniFormat)))
    result.result = generate_report(source, request(
        source, contents=("heatmap",) if heatmaps else ("summary",), types=(), heatmap_modes=("group",)))
    return result


def save_to(monkeypatch, path):
    monkeypatch.setattr(module.QFileDialog, "getSaveFileName", lambda *_, **__: (str(path), "Excel (*.xlsx)"))


@pytest.mark.parametrize("heatmaps", [True, False])
def test_excel_format_is_available_only_for_a_generated_heatmap(own, tmp_path, heatmaps):
    p = page(own, tmp_path, heatmaps=heatmaps)
    p._show_formats()
    assert list(p.format_dialog.format_buttons) == (["docx", "pdf", "xlsx"] if heatmaps else ["docx", "pdf"])
    if heatmaps:
        assert any("선택한 히트맵만" in label.text() for label in p.format_dialog.findChildren(QLabel))
    p.format_dialog.cancel_button.click()
    assert not p.saving


def test_excel_choice_saves_editable_group_sheets_without_pdf_and_reopens_in_library(own, tmp_path, monkeypatch):
    p = page(own, tmp_path)
    output = tmp_path / "히트맵.xlsx"
    save_to(monkeypatch, output)
    monkeypatch.setattr(module, "report_pictures", lambda *_, **__: pytest.fail("Excel은 PNG를 만들지 않습니다"))
    monkeypatch.setattr(module, "PdfPreviewDialog", lambda *_, **__: pytest.fail("Excel은 PDF 미리보기를 열지 않습니다"))
    p._show_formats()
    p.format_dialog.format_buttons["xlsx"].click()
    until(lambda: not p.excel_request.busy and len(p.saved_entries) == 1)
    assert p.preview_dialog is None and p.pictures is None and not p.saving
    assert p.generate_button.isEnabled() and p.saved_entries[0]["path"] == str(output)
    with closing(load_workbook(output)) as book:
        assert {"C4", "C3"} <= set(book.sheetnames)
        assert any(cell.value == "EQ-1" and cell.data_type == "s"
                   for row in book["C4"] for cell in row)
    reopened = page(own, tmp_path)
    assert reopened.library_model.rowCount() == 1
    opened = []
    monkeypatch.setattr(module.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    assert reopened.open_saved(0) and [Path(path) for path in opened] == [output]


def test_cancelled_excel_file_dialog_creates_nothing_and_guards_reentrant_generation(own, tmp_path, monkeypatch):
    p = page(own, tmp_path)

    def cancel(*_, **__):
        assert p.saving and not p.generate_button.isEnabled()
        assert not p.generate() and not p.export_heatmaps()
        return "", ""

    monkeypatch.setattr(module.QFileDialog, "getSaveFileName", cancel)
    monkeypatch.setattr(module, "save_report_heatmaps", lambda *args: pytest.fail("취소 시 저장하면 안 됩니다"))
    assert not p.export_heatmaps()
    assert not p.saving and p.generate_button.isEnabled() and not p.saved_entries
    assert not list(tmp_path.glob("*.xlsx"))


def test_excel_duplicate_decline_and_numbered_save_preserve_existing_file(own, tmp_path, monkeypatch):
    p = page(own, tmp_path)
    output = tmp_path / "히트맵.xlsx"
    output.write_bytes(b"existing")
    save_to(monkeypatch, output)
    accepted, prompts = [False], []
    monkeypatch.setattr(module, "confirm_numbered_save", lambda _, path: prompts.append(path) or accepted[0])
    assert not p.export_heatmaps()
    assert output.read_bytes() == b"existing" and not (tmp_path / "히트맵 (1).xlsx").exists()
    accepted[0] = True
    assert p.export_heatmaps()
    until(lambda: not p.excel_request.busy)
    numbered = tmp_path / "히트맵 (1).xlsx"
    assert output.read_bytes() == b"existing" and numbered.is_file()
    assert prompts == [output, output] and p.saved_entries[0]["path"] == str(numbered)


@pytest.mark.parametrize("raises", [False, True])
def test_excel_failure_removes_only_reserved_file_and_can_retry(own, tmp_path, monkeypatch, raises):
    p = page(own, tmp_path)
    output = tmp_path / "실패.xlsx"
    save_to(monkeypatch, output)
    original = module.save_report_heatmaps

    def fail(report, path):
        if raises:
            raise OSError("검증용 저장 거절")
        return ExportResult(ExportStatus.FAILED, path, "검증용 저장 거절")

    monkeypatch.setattr(module, "save_report_heatmaps", fail)
    assert p.export_heatmaps()
    until(lambda: not p.excel_request.busy)
    assert not output.exists() and not p.saved_entries and not p.saving
    assert p.generate_button.isEnabled() and "저장 거절" in p.generation_status.text()
    monkeypatch.setattr(module, "save_report_heatmaps", original)
    assert p.export_heatmaps()
    until(lambda: not p.excel_request.busy)
    assert output.is_file() and len(p.saved_entries) == 1


def test_busy_excel_keeps_frozen_report_and_close_guard_state_after_catalog_invalidation(own, tmp_path, monkeypatch):
    p = page(own, tmp_path)
    frozen = p.result
    output = tmp_path / "고정.xlsx"
    save_to(monkeypatch, output)
    original = module.save_report_heatmaps
    entered, release = Event(), Event()
    received = []

    def slow(report, path):
        received.append((report, get_ident()))
        entered.set()
        assert release.wait(5), "테스트 저장 작업이 해제되지 않았습니다"
        return original(report, path)

    monkeypatch.setattr(module, "save_report_heatmaps", slow)
    try:
        assert p.export_heatmaps()
        until(entered.is_set)
        assert p.saving and not p.generate_button.isEnabled()
        assert not p.generate() and not p.export_heatmaps()
        p.source.records = tuple(replace(record, equipment_code="CHANGED") for record in p.source.records)
        p.select_contents(False)
        p.invalidate_catalog()
        assert p.result is None and p.excel_request.busy and p.saving
        assert not p.generate_button.isEnabled()
    finally:
        release.set()
        until(lambda: not p.excel_request.busy)
    assert len(received) == 1 and received[0][0] is frozen and received[0][1] != get_ident()
    assert p.saved_entries[0]["path"] == str(output) and p.generate_button.isEnabled()
    with closing(load_workbook(output)) as book:
        values = [cell.value for sheet in book for row in sheet for cell in row]
    assert "EQ-1" in values and "CHANGED" not in values


@pytest.mark.parametrize("during_duplicate", [False, True])
def test_invalidated_report_during_file_selection_does_not_write_or_keep_reservation(
        own, tmp_path, monkeypatch, during_duplicate):
    p = page(own, tmp_path)
    output = tmp_path / "변경.xlsx"
    if during_duplicate:
        output.write_bytes(b"existing")
        save_to(monkeypatch, output)

        def confirm(*_):
            p.invalidate_catalog()
            return True

        monkeypatch.setattr(module, "confirm_numbered_save", confirm)
    else:
        def select(*_, **__):
            p.invalidate_catalog()
            return str(output), ""

        monkeypatch.setattr(module.QFileDialog, "getSaveFileName", select)
    monkeypatch.setattr(module, "save_report_heatmaps", lambda *args: pytest.fail("무효화된 보고서를 저장하면 안 됩니다"))
    assert not p.export_heatmaps()
    assert not p.saving and p.generate_button.isEnabled() and not p.saved_entries
    if during_duplicate:
        assert output.read_bytes() == b"existing" and not (tmp_path / "변경 (1).xlsx").exists()
    else:
        assert not output.exists()


def test_excel_rejects_wrong_suffix_before_creating_file(own, tmp_path, monkeypatch):
    p = page(own, tmp_path)
    output = tmp_path / "히트맵.csv"
    save_to(monkeypatch, output)
    assert not p.export_heatmaps()
    assert not output.exists() and not p.saving and ".xlsx" in p.generation_status.text()
