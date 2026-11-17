"""실제 양식 파일의 매핑·미리보기·확인과 비동기 수명 경계를 검증한다."""

from contextlib import closing
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from PySide6.QtCore import QSettings, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

from setron.features.export import template
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.features.export.template import TemplateColumn, TemplateConfig, inspect_template_sheet
from setron.features.management_format import EQUIPMENT_HEADERS, RELEASE_HEADERS
from setron.ui.template_export_dialog import TemplateExportDialog
from test_ui_import_ver5 import own, until  # noqa: F401


def _records():
    def equipment(code, site, pm, version, line):
        return {**dict.fromkeys(EQUIPMENT_HEADERS), "설비 호기": code, "기준일": date(2026, 11, 2),
                "사업장": site, "PM": pm, "SW 버전(원문)": version, "라인": line, "공정": "ETCH"}

    def release(version, state):
        return {**dict.fromkeys(RELEASE_HEADERS), "관리 모델": "M-O2", "버전(원문)": version,
                "Item 번호": 7, "내용": "=원문 내용을 보존", "적용 상태": state, "Jira 코드": "ETOJ-7"}

    return {
        "설비현황표": (
            equipment("EQ-A", "화성", "PM1", "v1", "15L"),
            equipment("EQ-A", "화성", "PM2", "v2", "15L"),
            equipment("EQ-B", "평택", "PM1", "v9", "P1"),
        ),
        "릴리즈노트": (release("v1", "적용"), release("v2", "미적용")),
    }


def _workbook(path, headers, *, sheet_name="사업장 양식"):
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_name
    sheet.append(headers)
    sheet["A2"].font = Font(bold=True, color="113355")
    sheet["A8"].fill = PatternFill("solid", fgColor="EEF2F6")
    sheet.cell(2, len(headers) + 1, "=1+1")
    book.create_sheet("보존 시트")["A1"] = "이 시트는 유지"
    book.save(path)
    book.close()
    return path


def _settings(path):
    return QSettings(str(path), QSettings.Format.IniFormat)


def _dialog(own, tmp_path, records=None, *, initial=None, settings=None, loader=None):
    return own(TemplateExportDialog(
        records_loader=loader or (lambda: records or _records()), initial=initial,
        settings=settings or _settings(tmp_path / "settings.ini")))


def _load(dialog, path, monkeypatch):
    until(lambda: not dialog.records_request.busy and dialog.records is not None)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_, **__: (str(path), ""))
    dialog.file_button.click()
    until(lambda: dialog.sheet_metadata is not None and not dialog.busy)


def _map(dialog, column, field, qualifier=""):
    _, fields, qualifiers = next(controls for controls in dialog.mapping_controls if controls[0] == column)
    fields.setCurrentIndex(fields.findData(field))
    if qualifier:
        found = qualifiers.findData(qualifier)
        qualifiers.setCurrentIndex(found) if found >= 0 else qualifiers.setEditText(qualifier)


def _preview_and_accept(dialog):
    dialog.preview_button.click()
    until(lambda: not dialog.busy)
    assert dialog.reviewed_config is not None, dialog.status.text()
    assert not dialog.use_button.isEnabled()
    dialog.region_confirm.setChecked(True)
    assert dialog.use_button.isEnabled()
    dialog.use_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    return dialog.accepted_config


def _save_records(monkeypatch, records, config, output):
    monkeypatch.setattr(template, "read_management_records", lambda _, **options: records)
    result = template.save_template_export(object(), output, config)
    assert result.saved, result.message


def test_row_template_manual_mapping_and_fresh_preview_preserve_original(own, tmp_path, monkeypatch):
    path = _workbook(tmp_path / "rows.xlsx", ["설비번호", "사업장", "PM", "SW 버전", "담당 칸"])
    original = path.read_bytes()
    records = _records()
    dialog = _dialog(own, tmp_path, records)
    _load(dialog, path, monkeypatch)
    unknown = dialog.mapping_controls[4][1]
    assert unknown.currentData() is None
    _map(dialog, 5, "라인")
    dialog.site_combo.setCurrentIndex(dialog.site_combo.findData("화성"))
    dialog.preview_button.click()
    until(lambda: not dialog.busy)
    assert dialog.preview_table.rowCount() == 2
    dialog.region_confirm.setChecked(True)
    assert dialog.use_button.isEnabled()
    dialog.end_row.setValue(dialog.end_row.value() + 1)
    assert dialog.reviewed_config is None and not dialog.region_confirm.isChecked()
    dialog.accept()
    assert dialog.accepted_config is None
    config = _preview_and_accept(dialog)
    output = tmp_path / "filled.xlsx"
    _save_records(monkeypatch, records, config, output)
    assert path.read_bytes() == original
    with closing(load_workbook(output)) as book:
        sheet = book["사업장 양식"]
        assert [sheet.cell(2, column).value for column in range(1, 6)] == ["EQ-A", "화성", "PM1", "v1", "15L"]
        assert sheet["C3"].value == "PM2" and sheet["D3"].value == "v2"
        assert sheet["A4"].value is None
        assert sheet["F2"].value == "=1+1" and sheet["A2"].font.bold
        assert book["보존 시트"]["A1"].value == "이 시트는 유지"


def test_pm_columns_template_filters_site_and_places_versions_in_one_row(own, tmp_path, monkeypatch):
    path = _workbook(tmp_path / "pm-columns.xlsx", ["설비 호기", "사업장", "PM1 버전", "PM2 버전"])
    dialog = _dialog(own, tmp_path)
    _load(dialog, path, monkeypatch)
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("columns"))
    dialog.site_combo.setCurrentIndex(dialog.site_combo.findData("화성"))
    _map(dialog, 3, "SW 버전(원문)", "PM1")
    _map(dialog, 4, "SW 버전(원문)", "PM2")
    config = _preview_and_accept(dialog)
    assert dialog.preview_table.rowCount() == 1 and config.site == "화성"
    output = tmp_path / "pm-filled.xlsx"
    _save_records(monkeypatch, _records(), config, output)
    with closing(load_workbook(output)) as book:
        assert [book.active.cell(2, column).value for column in range(1, 5)] == ["EQ-A", "화성", "v1", "v2"]
        assert book.active["A3"].value is None


def test_release_columns_template_keeps_item_content_literal(own, tmp_path, monkeypatch):
    path = _workbook(tmp_path / "release.xlsx", ["Item 번호", "내용", "v1", "v2"], sheet_name="릴리즈노트")
    dialog = _dialog(own, tmp_path)
    _load(dialog, path, monkeypatch)
    dialog.kind_combo.setCurrentIndex(dialog.kind_combo.findData("release"))
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("columns"))
    _map(dialog, 1, "Item 번호")
    _map(dialog, 2, "내용")
    _map(dialog, 3, "적용 상태", "v1")
    _map(dialog, 4, "적용 상태", "v2")
    config = _preview_and_accept(dialog)
    assert config.kind == "release" and config.site is None
    output = tmp_path / "release-filled.xlsx"
    _save_records(monkeypatch, _records(), config, output)
    with closing(load_workbook(output)) as book:
        assert [book.active.cell(2, column).value for column in range(1, 5)] == [7, "=원문 내용을 보존", "적용", "미적용"]
        assert book.active["B2"].data_type == "s"


def test_release_independent_lists_pack_uneven_versions_and_remember_mode(own, tmp_path, monkeypatch):
    path = _workbook(tmp_path / "version-lists.xlsx", ["v1", "v2"], sheet_name="릴리즈노트")
    original = path.read_bytes()
    records = {"설비현황표": (), "릴리즈노트": tuple(
        {**dict.fromkeys(RELEASE_HEADERS), "관리 모델": "M-O2", "버전(원문)": version,
         "Item 번호": number, "내용": content}
        for version, number, content in (("v1", 1, "v1 첫 항목"),
                                          ("v2", 2, "v2 첫 항목"), ("v2", 3, "v2 둘째 항목")))}
    settings = _settings(tmp_path / "version-lists.ini")
    dialog = _dialog(own, tmp_path, records, settings=settings)
    _load(dialog, path, monkeypatch)
    dialog.kind_combo.setCurrentIndex(dialog.kind_combo.findData("release"))
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("lists"))
    assert [control[1].currentData() for control in dialog.mapping_controls[:2]] == ["내용", "내용"]
    assert all(control[2].isEnabled() for control in dialog.mapping_controls[:2])
    config = _preview_and_accept(dialog)
    assert config.mode == "lists" and dialog.preview_table.rowCount() == 2
    assert [dialog.preview_table.item(0, column).text() for column in range(2)] == ["v1 첫 항목", "v2 첫 항목"]
    assert [dialog.preview_table.item(1, column).text() for column in range(2)] == ["", "v2 둘째 항목"]
    assert json.loads(settings.value("last"))["mode"] == "lists"
    output = tmp_path / "version-lists-filled.xlsx"
    _save_records(monkeypatch, records, config, output)
    assert path.read_bytes() == original
    with closing(load_workbook(output)) as book:
        assert (book.active["A2"].value, book.active["B2"].value) == ("v1 첫 항목", "v2 첫 항목")
        assert book.active["A3"].value is None and book.active["B3"].value == "v2 둘째 항목"
        assert book.active["A4"].value is None and book.active["B4"].value is None
    restored = _dialog(own, tmp_path, records, settings=settings)
    until(lambda: restored.sheet_metadata is not None and not restored.busy)
    assert restored.kind_combo.currentData() == "release" and restored.mode_combo.currentData() == "lists"
    assert [control[1].currentData() for control in restored.mapping_controls[:2]] == ["내용", "내용"]
    assert not restored.use_button.isEnabled()


@pytest.mark.parametrize("slow_records", [False, True])
def test_saved_mapping_and_site_survive_load_completion_order(own, tmp_path, slow_records):
    path = _workbook(tmp_path / "remembered.xlsx", ["설비 호기", "알 수 없는 열"])
    metadata = inspect_template_sheet(path, "사업장 양식", 1)
    initial = TemplateConfig(path, "사업장 양식", "equipment", 1, 2, 8, "rows",
                             (TemplateColumn(1, "설비 호기"), TemplateColumn(2, "라인")), metadata["fingerprint"], "화성")
    settings = _settings(tmp_path / "remembered.ini")
    payload = asdict(initial)
    payload["path"] = str(path)
    settings.setValue("last", json.dumps(payload, ensure_ascii=False))
    settings.sync()
    started, release = Event(), Event()

    def records():
        started.set()
        if slow_records:
            assert release.wait(5)
        return _records()

    dialog = _dialog(own, tmp_path, settings=settings, loader=records)
    try:
        until(started.is_set)
        until(lambda: dialog.sheet_metadata is not None)
        if slow_records:
            assert dialog.records is None
    finally:
        release.set()
        until(lambda: not dialog.busy)
    assert dialog.mapping_controls[1][1].currentData() == "라인"
    assert dialog.site_combo.currentData() == "화성"
    assert not dialog.use_button.isEnabled()
    _preview_and_accept(dialog)
    remembered = json.loads(settings.value("last"))
    assert remembered["site"] == "화성" and remembered["columns"][1]["field"] == "라인"


@pytest.mark.parametrize("saved_site", [None, "평택"])
def test_file_picker_restores_profile_site_after_records_already_loaded(own, tmp_path, monkeypatch, saved_site):
    path = _workbook(tmp_path / "site-profile.xlsx", ["설비 호기", "알 수 없는 열"])
    metadata = inspect_template_sheet(path, "사업장 양식", 1)
    config = TemplateConfig(path, "사업장 양식", "equipment", 1, 2, 8, "rows",
                            (TemplateColumn(1, "설비 호기"), TemplateColumn(2, "라인")), metadata["fingerprint"], saved_site)
    settings = _settings(tmp_path / "site-profile.ini")
    payload = asdict(config)
    payload["path"] = str(path)
    settings.setValue("profiles", json.dumps({str(path.resolve()): payload}, ensure_ascii=False))
    settings.sync()
    dialog = _dialog(own, tmp_path, settings=settings)
    until(lambda: dialog.records is not None and not dialog.busy)
    dialog.site_combo.setCurrentIndex(dialog.site_combo.findData("화성"))
    _load(dialog, path, monkeypatch)
    assert dialog.site_combo.currentData() == saved_site
    assert dialog.mapping_controls[1][1].currentData() == "라인"
    accepted = _preview_and_accept(dialog)
    assert accepted.site == saved_site and dialog.preview_table.rowCount() == (1 if saved_site else 3)


@pytest.mark.parametrize("profile_key", ["last", "profiles"])
def test_missing_remembered_site_never_widens_to_other_sites(own, tmp_path, monkeypatch, profile_key):
    path = _workbook(tmp_path / "missing-site.xlsx", ["설비 호기", "사업장"])
    metadata = inspect_template_sheet(path, "사업장 양식", 1)
    config = TemplateConfig(path, "사업장 양식", "equipment", 1, 2, 8, "rows",
                            (TemplateColumn(1, "설비 호기"), TemplateColumn(2, "사업장")), metadata["fingerprint"], "화성")
    payload = {**asdict(config), "path": str(path)}
    settings = _settings(tmp_path / "missing-site.ini")
    stored = payload if profile_key == "last" else {str(path.resolve()): payload}
    settings.setValue(profile_key, json.dumps(stored, ensure_ascii=False))
    settings.sync()
    records = _records()
    records["설비현황표"] = tuple(row for row in records["설비현황표"] if row["사업장"] == "평택")
    dialog = _dialog(own, tmp_path, records, settings=settings)
    if profile_key == "profiles":
        _load(dialog, path, monkeypatch)
    else:
        until(lambda: dialog.sheet_metadata is not None and not dialog.busy)
    assert dialog.site_combo.currentData() == "화성" and "자료 없음" in dialog.site_combo.currentText()
    assert dialog._config().site == "화성"
    dialog.preview_button.click()
    until(lambda: not dialog.busy)
    assert dialog.reviewed_config is None and not dialog.use_button.isEnabled()
    assert "없" in dialog.status.text() and dialog.accepted_config is None
    dialog.site_combo.setCurrentIndex(dialog.site_combo.findData("평택"))
    accepted = _preview_and_accept(dialog)
    assert accepted.site == "평택" and dialog.preview_table.rowCount() == 1


@pytest.mark.parametrize("corruption", [
    {"kind": "removed-legacy-kind"},
    {"header_row": True},
    {"columns": [{"column": 1, "field": "설비 호기", "qualifier": None}]},
])
def test_corrupted_last_profile_is_ignored_without_starting_file_load(own, tmp_path, corruption):
    path = _workbook(tmp_path / "invalid-profile.xlsx", ["설비 호기", "알 수 없는 열"])
    metadata = inspect_template_sheet(path, "사업장 양식", 1)
    config = TemplateConfig(path, "사업장 양식", "equipment", 1, 2, 8, "rows",
                            (TemplateColumn(1, "설비 호기"),), metadata["fingerprint"])
    payload = {**asdict(config), "path": str(path), **corruption}
    settings = _settings(tmp_path / "invalid-profile.ini")
    settings.setValue("last", json.dumps(payload, ensure_ascii=False))
    settings.sync()
    dialog = _dialog(own, tmp_path, settings=settings)
    until(lambda: dialog.records is not None and not dialog.busy)
    assert dialog.initial is None and dialog.path is None and dialog.metadata is None
    assert dialog.kind_combo.currentData() == "equipment"
    assert not dialog.use_button.isEnabled() and dialog.accepted_config is None


def test_corrupted_file_profile_uses_fresh_mapping_instead(own, tmp_path, monkeypatch):
    path = _workbook(tmp_path / "invalid-file-profile.xlsx", ["설비번호", "알 수 없는 열"])
    metadata = inspect_template_sheet(path, "사업장 양식", 1)
    config = TemplateConfig(path, "사업장 양식", "equipment", 1, 2, 8, "rows",
                            (TemplateColumn(1, "설비 호기"), TemplateColumn(2, "라인")), metadata["fingerprint"], "평택")
    payload = {**asdict(config), "path": str(path), "mode": "removed-layout"}
    settings = _settings(tmp_path / "invalid-file-profile.ini")
    settings.setValue("profiles", json.dumps({str(path.resolve()): payload}, ensure_ascii=False))
    settings.sync()
    dialog = _dialog(own, tmp_path, settings=settings)
    _load(dialog, path, monkeypatch)
    assert dialog.mode_combo.currentData() == "rows" and dialog.site_combo.currentData() is None
    assert dialog.mapping_controls[0][1].currentData() == "설비 호기"
    assert dialog.mapping_controls[1][1].currentData() is None
    assert dialog.reviewed_config is None and not dialog.use_button.isEnabled()


def test_changed_template_is_rejected_until_reloaded(own, tmp_path, monkeypatch):
    path = _workbook(tmp_path / "changed.xlsx", ["설비 호기", "PM", "SW 버전"])
    dialog = _dialog(own, tmp_path)
    _load(dialog, path, monkeypatch)
    with closing(load_workbook(path)) as book:
        book.active["A10"] = "양식 변경"
        book.save(path)
    dialog.preview_button.click()
    until(lambda: not dialog.busy)
    assert dialog.reviewed_config is None and not dialog.use_button.isEnabled()
    assert "바뀌" in dialog.status.text() and dialog.accepted_config is None
    dialog.reject()
    assert not dialog.settings.contains("last")


def test_changed_after_confirmation_cannot_replace_existing_output(own, tmp_path, monkeypatch):
    path = _workbook(tmp_path / "confirmed.xlsx", ["설비 호기", "PM", "SW 버전"])
    dialog = _dialog(own, tmp_path)
    _load(dialog, path, monkeypatch)
    config = _preview_and_accept(dialog)
    with closing(load_workbook(path)) as book:
        book.active["A10"] = "확인 후 변경"
        book.save(path)
    changed_source = path.read_bytes()
    output = tmp_path / "existing.xlsx"
    output.write_bytes(b"previous output")
    monkeypatch.setattr(template, "read_management_records", lambda _, **options: _records())
    result = template.save_template_export(object(), output, config)
    assert not result.saved and "바뀌" in result.message
    assert output.read_bytes() == b"previous output" and path.read_bytes() == changed_source


def test_replacing_template_rebuilds_controls_without_deleted_widgets(own, tmp_path, monkeypatch):
    first = _workbook(tmp_path / "first.xlsx", ["설비 호기", "PM", "SW 버전"])
    second = _workbook(tmp_path / "second.xlsx", ["설비번호", "사업장"])
    dialog = _dialog(own, tmp_path)
    _load(dialog, first, monkeypatch)
    first_controls = tuple(dialog.mapping_controls)
    _load(dialog, second, monkeypatch)
    assert dialog.path == second and len(dialog.mapping_controls) == 3
    assert dialog.mapping_controls[0][1] is not first_controls[0][1]
    dialog.preview_button.click()
    until(lambda: not dialog.busy)
    assert dialog.reviewed_config is not None


def test_cancel_discards_late_records_and_does_not_save_profile(own, tmp_path):
    started, release = Event(), Event()

    def load():
        started.set()
        assert release.wait(5)
        return _records()

    dialog = _dialog(own, tmp_path, loader=load)
    try:
        until(started.is_set)
        dialog.reject()
    finally:
        release.set()
        assert QThreadPool.globalInstance().waitForDone(2000)
        QTest.qWait(30)
    assert dialog.records is None and dialog.accepted_config is None
    assert not dialog.settings.contains("last")


def test_cancel_discards_late_file_metadata_without_chaining_sheet_read(own, tmp_path, monkeypatch):
    import setron.ui.template_export_dialog as dialog_module

    path = _workbook(tmp_path / "late.xlsx", ["설비 호기", "PM", "SW 버전"])
    started, release, calls = Event(), Event(), []
    original = dialog_module.inspect_template

    def inspect(filename):
        started.set()
        assert release.wait(5)
        return original(filename)

    monkeypatch.setattr(dialog_module, "inspect_template", inspect)
    monkeypatch.setattr(dialog_module, "inspect_template_sheet", lambda *args: calls.append(args))
    dialog = _dialog(own, tmp_path)
    until(lambda: dialog.records is not None and not dialog.busy)
    try:
        dialog._load_file(path)
        until(started.is_set)
        dialog.reject()
    finally:
        release.set()
        assert QThreadPool.globalInstance().waitForDone(2000)
        QTest.qWait(30)
    assert dialog.metadata is None and dialog.sheet_metadata is None
    assert not calls and dialog.accepted_config is None


def test_reject_before_initial_event_never_starts_loading(tmp_path):
    app = QApplication.instance() or QApplication([])
    calls = []
    dialog = TemplateExportDialog(records_loader=lambda: calls.append(True), settings=_settings(tmp_path / "closed.ini"))
    dialog.reject()
    QTest.qWait(40)
    assert not calls and not dialog.busy
    dialog.deleteLater()
    app.processEvents()


def test_environment_exporter_always_uses_default_format_and_disposes_database(monkeypatch, tmp_path):
    from setron.infrastructure.database import Database
    from setron.ui.database_source import data_exporter_from_env

    calls, disposed = [], []
    marker = object()
    monkeypatch.setattr(Database, "from_env", lambda _: SimpleNamespace(dispose=lambda: disposed.append(True)))
    monkeypatch.setattr("setron.features.storage.service.StorageService", lambda _: marker)
    monkeypatch.setattr("setron.features.export.management.save_management_summary",
                        lambda storage, path, **options: calls.append((storage, options))
                        or ExportResult(ExportStatus.SAVED, Path(path)))
    exporter = data_exporter_from_env({"SETRON_DB_NAME": "review"})
    result = exporter(tmp_path / "output.xlsx", production_area="FOUNDRY", batch_ids=(8,))
    assert result.saved and calls == [(marker, {"production_area": "FOUNDRY", "batch_ids": (8,)})]
    assert disposed == [True]
    assert not hasattr(exporter, "read_records") and not hasattr(exporter, "read_workbook_records")
