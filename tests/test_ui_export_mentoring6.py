from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from setron.features.export.schemas import ExportResult, ExportStatus
from setron.features.export.management_files import ImportedFile
from setron.ui.app import create_window
from test_ui_db_export import qapp  # noqa: F401
from test_release_revision import revision_storage  # noqa: F401
from test_management_app_integration import file_storage  # noqa: F401
from test_ui_import_ver5 import until


def _source_with_areas():
    """출력 공급자가 지원하는 등록 분류를 합성 조회 선택지에도 명시한다."""
    from setron.ui.dashboard_demo import demo_dashboard_source
    from setron.ui.query_state import FabOption

    source = demo_dashboard_source()
    registered = {option.key for option in source.catalog.fabs}
    missing = tuple(FabOption(code, text, None) for code, text in (("MEMORY", "Memory"), ("FOUNDRY", "Foundry"))
                    if code not in registered)
    source.catalog = replace(source.catalog, fabs=(*source.catalog.fabs, *missing))
    return source


def test_sidebar_classification_is_passed_to_excel_only(qapp, tmp_path):
    calls = []

    def export(path, *, production_area=None):
        calls.append(production_area)
        Path(path).write_bytes(b"test export")
        return ExportResult(ExportStatus.SAVED, Path(path))

    window = create_window(source=_source_with_areas(), data_exporter=export)
    try:
        window.navigate("export")
        scope = window.pages["export"].production_area
        assert [scope.itemText(i) for i in range(scope.count())] == ["전체", "Memory", "Foundry"]
        for index, expected in enumerate((None, "MEMORY", "FOUNDRY")):
            scope.setCurrentIndex(index)
            assert window.export_data(tmp_path / f"scope-{index}.xlsx").saved
            assert calls[-1] == expected
        window.navigate("dashboard")
        assert window.export_data(tmp_path / "all-from-header.xlsx").saved and calls[-1] is None
    finally:
        window.close()


def test_file_checkboxes_single_multiple_scope_and_unselected_guard(qapp, tmp_path):
    files = (ImportedFile(11, "동일파일.xlsx", "설비현황표", datetime(2026, 11, 10, 9, 0)),
             ImportedFile(22, "동일파일.xlsx", "릴리즈노트", datetime(2026, 11, 11, 9, 0)))
    calls = []

    def export(path, **options):
        calls.append(options)
        Path(path).write_bytes(b"selected")
        return ExportResult(ExportStatus.SAVED, Path(path))

    window = create_window(source=_source_with_areas(), data_exporter=export, export_file_loader=lambda: files)
    try:
        window.navigate("export")
        page = window.pages["export"]
        page.selected_files_button.click()
        assert page.file_table.rowCount() == 2
        assert page.file_table.item(0, 1).text() == page.file_table.item(1, 1).text() == "동일파일.xlsx"
        assert page.file_table.item(0, 3).text() == "2026-11-10 09:00:00"
        assert page.file_table.item(1, 3).text() == "2026-11-11 09:00:00"
        assert "#" not in page.file_table.item(0, 3).toolTip()
        assert page.file_table.item(1, 2).text() == "릴리즈노트"
        assert not page.buttons["summary"].isEnabled()
        assert not window.export_data(tmp_path / "empty.xlsx").saved and not calls
        window.show()
        QTest.qWait(30)
        cell = page.file_table.visualRect(page.file_table.model().index(0, 0))
        center = page.file_checks[0].mapTo(page.file_table.viewport(), page.file_checks[0].rect().center())
        assert abs(center.x() - cell.center().x()) <= 2
        assert abs(center.y() - cell.center().y()) <= 2
        assert page.file_table.item(0, 0).data(Qt.ItemDataRole.CheckStateRole) is None
        assert page.file_table.focusPolicy() == Qt.FocusPolicy.NoFocus
        QTest.mouseClick(page.file_checks[0], Qt.MouseButton.LeftButton)
        page.production_area.setCurrentIndex(2)
        assert window.export_data(tmp_path / "one.xlsx").saved
        assert calls[-1] == {"production_area": "FOUNDRY", "batch_ids": (11,)}
        page.select_all_files.click()
        assert window.export_data(tmp_path / "two.xlsx").saved
        assert calls[-1]["batch_ids"] == (11, 22)
        assert page.select_all_files.text() == "전체 선택 해제"
        page.file_checks[1].setChecked(False)
        assert page.select_all_files.text() == "전체 선택"
        page.select_all_files.click()
        assert page.selected_batch_ids() == (11, 22)
        page.select_all_files.click()
        assert page.select_all_files.text() == "전체 선택"
        assert not page.buttons["summary"].isEnabled()
        page.all_files_button.click()
        assert not page.file_panel.isVisible()
        assert window.export_data(tmp_path / "all.xlsx").saved and calls[-1] == {"production_area": "FOUNDRY"}
        window.navigate("dashboard")
        assert window.export_data(tmp_path / "header.xlsx").saved and calls[-1] == {}
    finally:
        window.close()


def test_file_reload_preserves_only_existing_selections_and_list_scrolls(qapp):
    files = [ImportedFile(i + 1, f"현황-{i}.xlsx", "설비현황표", datetime(2026, 11, 10) + timedelta(minutes=i))
             for i in range(80)]
    window = create_window(data_exporter=lambda path: None, export_file_loader=lambda: tuple(files))
    try:
        window.resize(1500, 900)
        window.navigate("export")
        page = window.pages["export"]
        page.selected_files_button.click()
        window.show()
        qapp.processEvents()
        # 파일명은 남는 폭을 사용하고 자료 종류·날짜는 읽을 수 있는 폭을 확보한다.
        assert page.file_table.columnWidth(1) > page.file_table.columnWidth(3)
        assert sum(page.file_table.columnWidth(column) for column in range(4)) == page.file_table.viewport().width()
        assert page.file_table.horizontalScrollBar().maximum() == 0
        assert page.file_table.columnWidth(2) > page.file_table.columnWidth(0)
        assert page.file_table.columnWidth(3) > page.file_table.columnWidth(2)
        assert 3 <= page.file_table.viewport().height() // page.file_table.rowHeight(0) <= 8
        assert page.file_table.verticalScrollBar().maximum() > 0
        window.resize(1500, 620)
        QTest.qWait(30)
        short_height = page.file_table.height()
        window.resize(1500, 1200)
        until(lambda: page.file_table.height() > short_height)
        tall_height = page.file_table.height()
        assert tall_height > short_height
        files.extend(ImportedFile(i + 1, f"현황-{i}.xlsx", "설비현황표", None) for i in range(80, 160))
        page.reload_files.click()
        QTest.qWait(30)
        assert page.file_table.height() == tall_height
        page.file_table.scrollToBottom()
        assert page.file_table.verticalScrollBar().value() == page.file_table.verticalScrollBar().maximum()
        page.file_checks[0].setChecked(True)
        page.reload_files.click()
        assert page.selected_batch_ids() == (1,)
        files.pop(0)
        page.reload_files.click()
        assert page.selected_batch_ids() == () and not page.buttons["summary"].isEnabled()
    finally:
        window.close()


def test_failed_file_reload_clears_stale_selection(qapp):
    failed = False

    def load():
        if failed:
            raise RuntimeError("test connection failure")
        return (ImportedFile(1, "파일.xlsx", "설비현황표", None),)

    window = create_window(data_exporter=lambda path: None, export_file_loader=load)
    try:
        window.navigate("export")
        page = window.pages["export"]
        page.selected_files_button.click()
        page.select_all_files.click()
        assert page.selected_batch_ids() == (1,)
        failed = True
        page.reload_files.click()
        assert page.selected_batch_ids() == ()
        assert "불러오지 못했습니다" in page.file_status.text()
        assert not page.buttons["summary"].isEnabled()
        page.all_files_button.click()
        assert page.buttons["summary"].isEnabled()
    finally:
        window.close()


def test_zero_matching_scope_warns_without_leaving_a_blank_file(qapp, tmp_path, monkeypatch, file_storage):
    from setron.features.export.management import save_management_summary
    from test_management_db_source import connected
    from test_release_revision import all_rows

    connected(file_storage)
    snapshot = all_rows(file_storage, "equipment_snapshot")[0]
    files = (ImportedFile(snapshot["batch_id"], "평택설비현황표.xlsx", "설비현황표", None),)
    path = tmp_path / "empty-foundry.xlsx"
    warnings, successes = [], []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(path), "Excel"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: successes.append(args[-1]))
    window = create_window(source=_source_with_areas(), data_exporter=lambda destination, **options: save_management_summary(
        file_storage, destination, **options), export_file_loader=lambda: files)
    try:
        window.navigate("export")
        page = window.pages["export"]
        page.selected_files_button.click()
        page.select_all_files.click()
        page.production_area.setCurrentIndex(2)
        page.buttons["summary"].click()
        until(lambda: not window.data_export_request.busy)
        assert warnings and "Foundry 조건" in warnings[-1]
        assert not successes and not path.exists()  # 중복 방지로 확보한 파일도 실패 시 정리한다.
        page.production_area.setCurrentIndex(0)
        page.buttons["summary"].click()
        until(lambda: not window.data_export_request.busy)
        assert path.exists() and successes and "설비현황표 1행" in successes[-1]
    finally:
        window.close()
