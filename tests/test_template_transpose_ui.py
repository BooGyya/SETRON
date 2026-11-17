"""반전 양식과 시트별 출력 조건을 실제 Qt 흐름으로 검증한다."""

from contextlib import closing
from datetime import date

from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill
from PySide6.QtCore import QEventLoop, QSettings, QTimer
from PySide6.QtWidgets import QDialog

from setron.features.export import template
from setron.features.export.template_workbook import SheetExportConfig, WorkbookTemplateConfig
from setron.features.management_format import EQUIPMENT_HEADERS, RELEASE_HEADERS
from setron.ui.template_export_dialog import TemplateExportDialog
from test_ui_import_ver5 import own  # noqa: F401


def wait(predicate, timeout=8):
    if predicate():
        return
    loop, poll, deadline = QEventLoop(), QTimer(), QTimer()
    deadline.setSingleShot(True)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.timeout.connect(loop.quit)
    poll.start(10)
    deadline.start(timeout * 1000)
    loop.exec()
    poll.stop()
    deadline.stop()
    assert predicate()


def records():
    equipment = tuple(
        {**dict.fromkeys(EQUIPMENT_HEADERS), "설비 호기": code, "사업장": site, "라인": line,
         "기준일": date(2026, 11, 16), "PM": "PM1", "SW 버전(원문)": version,
         "공정": process, "생산 영역": "메모리 맞춤", "_area_code": "MEMORY"}
        for code, site, line, version, process in (
            ("EQ-A", "화성", "13L", "v1", "ETCH-A"),
            ("EQ-B", "화성", "14L", "v2", "ETCH-B"),
            ("EQ-C", "평택", "13L", "v3", "ETCH-C"),
        ))
    release = ({**dict.fromkeys(RELEASE_HEADERS), "관리 모델": "M-O2", "버전(원문)": "v1",
                "Item 번호": "7", "내용": "=현재 원문", "적용 상태": "적용"},)
    return {"설비현황표": equipment, "릴리즈노트": release,
            "_scope_choices": {"sites": ("평택", "화성"), "lines": ("13L", "14L"),
                               "lines_by_site": {"화성": ("13L", "14L"), "평택": ("13L",)},
                               "production_areas": {"MEMORY": "메모리 맞춤", "FOUNDRY": "파운드리 맞춤"}}}


def scoped_records(config):
    source = records()
    result = {}
    for plan in config.sheets:
        site = getattr(plan, "site", None) or plan.template.site
        selected = tuple(row for row in source["설비현황표"]
                         if (site is None or row["사업장"] == site)
                         and (plan.line is None or row["라인"] == plan.line)
                         and (plan.production_area is None or row["_area_code"] == plan.production_area))
        result[plan.template.sheet_name] = {**source, "설비현황표": selected}
    return result


def workbook(path):
    book = Workbook()
    normal = book.active
    normal.title = "라인 양식"
    normal.append(["설비번호", "SW 버전", "공정", "사용자 메모", "계산"])
    for row in range(2, 6):
        for column in range(1, 4):
            normal.cell(row, column, "이전 값")
    normal["D2"] = "유지할 메모"
    normal["E2"] = "=LEN(A2)"
    normal["A7"] = "영역 밖 설명"

    transposed = book.create_sheet("세로 항목")
    for row, title in ((3, "호기"), (5, "공정"), (7, "SW 버전")):
        transposed.cell(row, 2, title)
        for column in range(4, 8):
            transposed.cell(row, column, "이전 값")
    transposed["D5"].fill = PatternFill("solid", fgColor="11AA88")
    transposed["D6"] = "=LEN(D3)"
    transposed["H3"] = "오른쪽 설명"

    empty = book.create_sheet("자료 없는 시트")
    empty.append(["호기", "SW 버전", "메모", "계산"])
    for row in range(2, 5):
        empty.cell(row, 1, "이전 설비")
        empty.cell(row, 2, "이전 버전")
    empty["C2"] = "유지할 내용"
    empty["D2"] = "=1+1"
    book.create_sheet("설명")["A1"] = "전체 시트 보존"
    book.save(path)
    book.close()
    return path


def dialog(own, tmp_path, *, initial=None, settings=None):
    return own(TemplateExportDialog(
        records_loader=records, workbook_records_loader=scoped_records,
        initial=initial, settings=settings or QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)))


def idle(value):
    return not value.busy and not value.header_timer.isActive() and value.sheet_metadata is not None


def load(value, path):
    wait(lambda: value.records is not None and not value.busy)
    value._load_file(path)
    wait(lambda: idle(value))


def select_sheet(value, name):
    index = value.sheet_combo.findText(name)
    assert index >= 0
    value.sheet_combo.setCurrentIndex(index)
    wait(lambda: idle(value))


def map_fields(value, fields):
    for _, combo, qualifier in value.mapping_controls:
        combo.setCurrentIndex(0)
        qualifier.setCurrentIndex(0)
    for position, field in fields.items():
        control = next((control for control in value.mapping_controls if control[0] == position), None)
        assert control is not None, (position, [row[0] for row in value.mapping_controls])
        control[1].setCurrentIndex(control[1].findData(field))


def preview(value):
    value._preview()
    wait(lambda: not value.busy)
    assert value.reviewed_config is not None, value.status.text()
    value.region_confirm.setChecked(True)


def queue_current(value):
    preview(value)
    assert value.add_sheet_button.isEnabled()
    value.add_sheet_button.click()


def transposed_editor(value, name="세로 항목", end_column=6):
    select_sheet(value, name)
    value.orientation_combo.setCurrentIndex(value.orientation_combo.findData("columns"))
    wait(lambda: idle(value))
    value.header_column.setValue(2)
    wait(lambda: idle(value))
    value.start_column.setValue(4)
    value.end_column.setValue(end_column)
    map_fields(value, {3: "설비 호기", 5: "공정", 7: "SW 버전(원문)"})


def save(monkeypatch, config, output):
    from setron.features.export import template_workbook

    if isinstance(config, template.TemplateConfig):
        monkeypatch.setattr(template, "read_management_records", lambda *_, **__: records())
        result = template.save_template_export(object(), output, config)
    else:
        monkeypatch.setattr(template_workbook, "read_workbook_records", lambda *_, **__: scoped_records(config))
        result = template_workbook.save_workbook_template_export(object(), output, config)
    assert result.saved, result.message


def mixed_config(path):
    fingerprint = template.inspect_template(path)["fingerprint"]
    normal = template.TemplateConfig(
        path, "라인 양식", "equipment", 1, 2, 5, "rows",
        (template.TemplateColumn(1, "설비 호기"), template.TemplateColumn(2, "SW 버전(원문)")),
        fingerprint, "화성")
    across = template.TemplateConfig(
        path, "세로 항목", "equipment", 1, 2, 7, "rows", (), fingerprint,
        orientation="columns", header_column=2, start_column=4, end_column=7,
        rows=(template.TemplateRow(3, "설비 호기"), template.TemplateRow(5, "공정"),
              template.TemplateRow(7, "SW 버전(원문)")))
    return WorkbookTemplateConfig(path, (
        SheetExportConfig(normal, line="13L", site="화성"),
        SheetExportConfig(across, production_area="MEMORY"),
    ))


def test_transposed_editor_maps_rows_and_requires_new_preview_after_axis_change(own, tmp_path, monkeypatch):
    source = workbook(tmp_path / "uploaded.xlsx")
    original = source.read_bytes()
    value = dialog(own, tmp_path)
    load(value, source)
    transposed_editor(value)
    value.site_combo.setCurrentIndex(value.site_combo.findData("화성"))
    preview(value)
    config = value._config()
    assert config.orientation == "columns"
    assert [(mapping.row, mapping.field) for mapping in config.rows] == [
        (3, "설비 호기"), (5, "공정"), (7, "SW 버전(원문)"),
    ]
    value.end_column.setValue(7)
    assert value.reviewed_config is None and not value.region_confirm.isChecked()
    value.accept()
    assert value.accepted_config is None
    preview(value)
    value.use_button.click()
    assert value.result() == QDialog.DialogCode.Accepted
    output = tmp_path / "transposed-filled.xlsx"
    save(monkeypatch, value.accepted_config, output)
    assert source.read_bytes() == original
    with closing(load_workbook(output)) as checked:
        sheet = checked["세로 항목"]
        assert [sheet.cell(3, column).value for column in range(4, 8)] == ["EQ-A", "EQ-B", None, None]
        assert [sheet.cell(5, column).value for column in range(4, 6)] == ["ETCH-A", "ETCH-B"]
        assert sheet["D6"].value == "=LEN(D3)" and sheet["D5"].fill.fgColor.rgb == "0011AA88"
        assert sheet["H3"].value == "오른쪽 설명" and checked["설명"]["A1"].value == "전체 시트 보존"


def test_transposed_release_preview_and_export_preserve_literal_item_content(own, tmp_path, monkeypatch):
    source = workbook(tmp_path / "release-across.xlsx")
    value = dialog(own, tmp_path)
    load(value, source)
    transposed_editor(value)
    value.kind_combo.setCurrentIndex(value.kind_combo.findData("release"))
    map_fields(value, {3: "Item 번호", 5: "내용", 7: "적용 상태"})
    preview(value)
    assert (value.preview_table.rowCount(), value.preview_table.columnCount()) == (3, 1)
    assert value.preview_table.item(1, 0).text() == "=현재 원문"
    assert "내용" in value.preview_table.verticalHeaderItem(1).text()
    value.use_button.click()
    assert value.accepted_config is not None
    output = tmp_path / "release-filled.xlsx"
    save(monkeypatch, value.accepted_config, output)
    with closing(load_workbook(output)) as checked:
        sheet = checked["세로 항목"]
        assert [sheet.cell(row, 4).value for row in (3, 5, 7)] == ["7", "=현재 원문", "적용"]
        assert sheet["D5"].data_type == "s"
        assert sheet["E3"].value is None and sheet["F7"].value is None
        assert sheet["D6"].value == "=LEN(D3)"


def test_mixed_sheet_conditions_fill_once_and_clear_only_empty_mapped_region(own, tmp_path, monkeypatch):
    source = workbook(tmp_path / "multi.xlsx")
    original = source.read_bytes()
    value = dialog(own, tmp_path)
    load(value, source)
    value.header_row.setValue(1)
    wait(lambda: idle(value))
    value.start_row.setValue(2)
    value.end_row.setValue(5)
    map_fields(value, {1: "설비 호기", 2: "SW 버전(원문)", 3: "공정"})
    value.site_combo.setCurrentIndex(value.site_combo.findData("화성"))
    value.line_combo.setCurrentIndex(value.line_combo.findData("13L"))
    queue_current(value)

    transposed_editor(value, end_column=7)
    value.site_combo.setCurrentIndex(value.site_combo.findData(None))
    value.line_combo.setCurrentIndex(value.line_combo.findData(None))
    assert value.area_combo.findData("MEMORY") >= 0
    value.area_combo.setCurrentIndex(value.area_combo.findData("MEMORY"))
    queue_current(value)

    select_sheet(value, "자료 없는 시트")
    value.orientation_combo.setCurrentIndex(value.orientation_combo.findData("rows"))
    wait(lambda: idle(value))
    value.header_row.setValue(1)
    wait(lambda: idle(value))
    value.start_row.setValue(2)
    value.end_row.setValue(4)
    map_fields(value, {1: "설비 호기", 2: "SW 버전(원문)"})
    value.area_combo.setCurrentIndex(value.area_combo.findData("FOUNDRY"))
    queue_current(value)
    assert set(value.sheet_plans) == {"라인 양식", "세로 항목", "자료 없는 시트"}

    value.preview_all_button.click()
    wait(lambda: not value.busy)
    assert value.reviewed_workbook is not None, value.status.text()
    assert "자료 없는 시트" in value.status.text()
    value.region_confirm.setChecked(True)
    value.use_button.click()
    config = value.accepted_config
    assert config is not None and len(config.sheets) == 3 and config.empty_policy == "clear"
    output = tmp_path / "one-output.xlsx"
    save(monkeypatch, config, output)
    assert source.read_bytes() == original
    with closing(load_workbook(output)) as checked:
        assert checked["라인 양식"]["A2"].value == "EQ-A" and checked["라인 양식"]["A3"].value is None
        assert [checked["세로 항목"].cell(3, column).value for column in range(4, 8)] == ["EQ-A", "EQ-B", "EQ-C", None]
        empty = checked["자료 없는 시트"]
        assert empty["A2"].value is None and empty["B4"].value is None
        assert empty["C2"].value == "유지할 내용" and empty["D2"].value == "=1+1"
        assert checked["라인 양식"]["E2"].value == "=LEN(A2)" and checked["라인 양식"]["A7"].value == "영역 밖 설명"


def test_queued_filter_change_cannot_accept_a_stale_workbook_preview(own, tmp_path):
    source = workbook(tmp_path / "stale.xlsx")
    value = dialog(own, tmp_path)
    load(value, source)
    value.header_row.setValue(1)
    wait(lambda: idle(value))
    value.start_row.setValue(2)
    value.end_row.setValue(5)
    map_fields(value, {1: "설비 호기", 2: "SW 버전(원문)"})
    value.line_combo.setCurrentIndex(value.line_combo.findData("13L"))
    queue_current(value)
    value._preview_all()
    wait(lambda: not value.busy)
    assert value.reviewed_workbook is not None
    value.region_confirm.setChecked(True)
    value.line_combo.setCurrentIndex(value.line_combo.findData("14L"))
    assert value.reviewed_workbook is None and not value.use_button.isEnabled()
    value.accept()
    assert value.accepted_config is None
    assert "라인 양식" in value.dirty_sheets


def test_remembered_transposed_sheet_retains_missing_exact_line_and_requires_review(own, tmp_path):
    source = workbook(tmp_path / "remembered.xlsx")
    settings = QSettings(str(tmp_path / "remembered.ini"), QSettings.Format.IniFormat)
    value = dialog(own, tmp_path, settings=settings)
    load(value, source)
    transposed_editor(value, end_column=7)
    value.line_combo.addItem("폐쇄된 라인", "CLOSED-LINE")
    value.line_combo.setCurrentIndex(value.line_combo.findData("CLOSED-LINE"))
    queue_current(value)
    value._preview_all()
    wait(lambda: not value.busy)
    assert value.reviewed_workbook is not None, value.status.text()
    value.region_confirm.setChecked(True)
    value.use_button.click()
    assert value.accepted_config is not None and value.result() == QDialog.DialogCode.Accepted

    restored = dialog(own, tmp_path, settings=settings)
    wait(lambda: restored.records is not None and idle(restored))
    assert restored.sheet_combo.currentText() == "세로 항목"
    assert restored.orientation_combo.currentData() == "columns"
    assert (restored.header_column.value(), restored.start_column.value(), restored.end_column.value()) == (2, 4, 7)
    assert restored.line_combo.currentData() == "CLOSED-LINE"
    assert restored.line_combo.currentData() is not None
    assert [(row, field.currentData()) for row, field, _ in restored.mapping_controls if field.currentData()] == [
        (3, "설비 호기"), (5, "공정"), (7, "SW 버전(원문)"),
    ]
    assert restored.reviewed_workbook is None and restored.reviewed_config is None
    assert not restored.use_button.isEnabled()
    restored._preview_all()
    wait(lambda: not restored.busy)
    assert restored.reviewed_workbook is not None
    assert "총 0건" in restored.status.text() and "입력 영역 비움" in restored.status.text()


def test_provider_writes_only_standard_equipment_and_release_sheets(monkeypatch, tmp_path):
    from setron.features.export import management
    from setron.infrastructure.database import Database
    from setron.ui.database_source import data_exporter_from_env

    pools, calls = [], []
    values = {"SETRON_DB_NAME": "synthetic-default-export-review"}

    class DatabasePool:
        disposed = 0

        def dispose(self):
            self.disposed += 1

    def connect(environ):
        assert environ is values
        pool = DatabasePool()
        pools.append(pool)
        return pool

    def read_records(storage, **options):
        calls.append((storage, options))
        return records()

    monkeypatch.setattr(Database, "from_env", connect)
    monkeypatch.setattr("setron.features.storage.service.StorageService", lambda database: database)
    monkeypatch.setattr(management, "read_management_records", read_records)
    exporter = data_exporter_from_env(values)
    output = tmp_path / "provider-output.xlsx"
    result = exporter(output, production_area="MEMORY", batch_ids=(7, 8))
    assert result.saved, result.message
    assert len(pools) == 1 and pools[0].disposed == 1
    assert calls == [(pools[0], {"production_area": "MEMORY", "batch_ids": (7, 8)})]
    with closing(load_workbook(output)) as checked:
        assert checked.sheetnames == ["설비현황표", "릴리즈노트"]
        assert tuple(cell.value for cell in checked["설비현황표"][1]) == EQUIPMENT_HEADERS
        assert tuple(cell.value for cell in checked["릴리즈노트"][1]) == RELEASE_HEADERS
        assert [checked["설비현황표"].cell(row, 1).value for row in range(2, 5)] == ["EQ-A", "EQ-B", "EQ-C"]
        content = checked["릴리즈노트"].cell(2, RELEASE_HEADERS.index("내용") + 1)
        assert content.value == "=현재 원문" and content.data_type == "s"


def test_default_workbook_page_preserves_area_and_busy_state_across_format_switch(own):
    from setron.ui.data_export_page import DataExportPage

    page = own(DataExportPage())
    page.set_availability({"sql": "", "summary": ""})
    page.production_area.setCurrentIndex(page.production_area.findData("MEMORY"))
    assert not hasattr(page, "set_template_config") and not hasattr(page, "custom_template_button")
    assert page.buttons["summary"].isEnabled()
    assert page.production_area.currentData() == "MEMORY"
    assert page.panels["summary"].isAncestorOf(page.buttons["summary"])
    page.tabs.setCurrentIndex(1)
    assert page.panels["sql"].isAncestorOf(page.buttons["sql"])
    page.tabs.setCurrentIndex(0)
    assert page.production_area.currentData() == "MEMORY"
    assert page.panels["summary"].isAncestorOf(page.buttons["summary"])
    page.set_availability({"sql": "", "summary": ""}, busy="자료를 저장하는 중입니다.")
    assert not page.buttons["summary"].isEnabled()
    page.set_availability({"sql": "", "summary": ""})
    assert page.buttons["summary"].isEnabled() and page.production_area.currentData() == "MEMORY"
