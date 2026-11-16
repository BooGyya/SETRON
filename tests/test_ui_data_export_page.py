"""형식 전환과 카드 안의 내보내기 동작이 선택·사용 가능 상태를 보존하는지 검증한다."""

from datetime import datetime

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.export.management_files import ImportedFile
from setron.ui.data_export_page import DataExportPage
from setron.ui.import_widgets import ImportTabs


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(qapp):
    value = DataExportPage()
    value.set_availability({"sql": "", "summary": ""})
    value.set_file_support(True)
    value.set_files((
        ImportedFile(11, "현황.xlsx", "설비현황표", datetime(2026, 11, 16, 9)),
        ImportedFile(22, "릴리즈.xlsx", "릴리즈노트", datetime(2026, 11, 15, 9)),
    ))
    value.show()
    qapp.processEvents()
    yield value
    value.close()


def test_format_switch_preserves_excel_scope_and_keeps_actions_inside_active_card(page):
    assert page.active_format == "summary"
    assert isinstance(page.tabs, ImportTabs)
    assert page.tabs.currentIndex() == 0
    assert [page.tabs.tabText(index) for index in range(page.tabs.count())] == ["Excel", "SQL"]
    assert page.panels["summary"].isVisible()
    assert not page.panels["sql"].isVisible()
    assert page.selected_batch_ids() is None
    assert not any(check.isChecked() for check in page.file_checks)
    assert page.production_area.currentData() is None
    assert page.panels["summary"].isAncestorOf(page.action_bar)
    assert page.panels["summary"].isAncestorOf(page.buttons["summary"])
    assert page.action_hint.text() == ""
    assert not page.action_hint.isVisible()

    page.selected_files_button.click()
    page.file_checks[0].setChecked(True)
    page.production_area.setCurrentIndex(page.production_area.findData("MEMORY"))
    assert page.selected_batch_ids() == (11,)
    assert page.production_area.currentData() == "MEMORY"

    page.tabs.setCurrentIndex(1)
    assert page.active_format == "sql"
    assert page.tabs.currentIndex() == 1
    assert page.panels["sql"].isVisible()
    assert not page.panels["summary"].isVisible()
    assert page.buttons["sql"].isVisible() and not page.buttons["summary"].isVisible()
    assert page.panels["sql"].isAncestorOf(page.action_bar)
    assert page.panels["sql"].isAncestorOf(page.buttons["sql"])
    assert page.panels["sql"].isAncestorOf(page.action_hint)

    page.tabs.setCurrentIndex(0)
    assert page.selected_batch_ids() == (11,)
    assert page.production_area.currentData() == "MEMORY"
    assert page.panels["summary"].isAncestorOf(page.action_bar)
    assert page.buttons["summary"].isVisible() and not page.buttons["sql"].isVisible()


def test_file_loading_and_failed_refresh_never_export_stale_or_all_data(page):
    page.selected_files_button.click()
    assert page.selected_batch_ids() == ()
    assert not page.buttons["summary"].isEnabled()

    page.file_checks[0].setChecked(True)
    assert page.buttons["summary"].isEnabled()
    page.set_files_loading()
    assert not page.buttons["summary"].isEnabled()
    assert not page.file_table.isEnabled()

    page.tabs.setCurrentIndex(1)
    assert page.buttons["sql"].isEnabled()
    page.set_files((), error=True)
    page.tabs.setCurrentIndex(0)
    assert page.selected_files_button.isChecked()
    assert page.selected_batch_ids() == ()
    assert not page.buttons["summary"].isEnabled()

    page.all_files_button.click()
    assert page.selected_batch_ids() is None
    assert page.buttons["summary"].isEnabled()


def test_card_actions_keep_their_provider_and_respect_busy_and_availability(page):
    calls = []
    page.buttons["summary"].clicked.connect(lambda: calls.append("summary"))
    page.buttons["sql"].clicked.connect(lambda: calls.append("sql"))
    page.buttons["summary"].click()
    page.tabs.setCurrentIndex(1)
    page.buttons["sql"].click()
    assert calls == ["summary", "sql"]

    page.set_availability({"sql": "", "summary": ""}, busy="자료를 저장하는 중입니다.")
    assert not any(action.isEnabled() for action in page.buttons.values())
    assert not page.tabs.isEnabled()
    QTest.mouseClick(page.tabs, Qt.MouseButton.LeftButton, pos=page.tabs.tabRect(0).center())
    assert page.active_format == "sql"
    page.buttons["sql"].click()
    assert calls == ["summary", "sql"]

    page.set_availability({"sql": "DB 연결이 필요합니다.", "summary": ""})
    assert not page.buttons["sql"].isEnabled()
    page.tabs.setCurrentIndex(0)
    assert page.buttons["summary"].isEnabled()
    page.buttons["summary"].click()
    assert calls == ["summary", "sql", "summary"]


def test_production_area_selection_is_preserved_across_sql(page):
    assert page.production_area.currentData() is None
    page.production_area.setCurrentIndex(page.production_area.findData("FOUNDRY"))
    assert page.production_area.currentData() == "FOUNDRY"
    page.tabs.setCurrentIndex(1)
    assert page.active_format == "sql" and page.buttons["sql"].isEnabled()
    page.tabs.setCurrentIndex(0)
    assert page.production_area.currentData() == "FOUNDRY"
    page.production_area.setCurrentIndex(0)
    assert page.production_area.currentData() is None


def test_excel_uses_one_default_workbook_without_upload_or_mapping_actions(page):
    from PySide6.QtWidgets import QAbstractButton

    assert "설비현황표" in page.details["summary"].text()
    assert "릴리즈노트" in page.details["summary"].text()
    assert "기본 양식" in page.details["summary"].text()
    assert "파일 하나" in page.details["summary"].text()
    assert page.buttons["summary"].isEnabled()
    assert all(not any(word in action.text() for word in ("양식", "업로드", "매핑"))
               for action in page.findChildren(QAbstractButton))
    page.production_area.setCurrentIndex(page.production_area.findData("MEMORY"))
    page.selected_files_button.click()
    page.file_checks[0].setChecked(True)
    assert page.selected_batch_ids() == (11,) and page.production_area.currentData() == "MEMORY"
    page.tabs.setCurrentIndex(1)
    page.tabs.setCurrentIndex(0)
    assert page.selected_batch_ids() == (11,) and page.production_area.currentData() == "MEMORY"
    assert page.buttons["summary"].isEnabled()


def test_sql_save_is_independent_of_empty_excel_file_selection(page):
    page.selected_files_button.click()
    assert page.selected_batch_ids() == ()
    assert not page.buttons["summary"].isEnabled()

    page.tabs.setCurrentIndex(1)
    assert page.buttons["sql"].isEnabled()
    assert page.action_hint.text() == ""
    assert not page.action_hint.isVisible()
    page.tabs.setCurrentIndex(0)
    assert page.selected_files_button.isChecked()
    assert not page.buttons["summary"].isEnabled()


def test_narrow_page_fits_shared_tabs_and_allows_keyboard_selection(page, qapp):
    page.resize(540, 900)
    qapp.processEvents()
    assert page.width() < 580
    assert page.rect().contains(page.tab_strip.geometry())
    assert page.tab_strip.rect().contains(page.tabs.geometry())
    page.tabs.setFocus()
    QTest.keyClick(page.tabs, Qt.Key.Key_Right)
    assert page.active_format == "sql" and page.panels["sql"].isVisible()
    QTest.keyClick(page.tabs, Qt.Key.Key_Left)
    assert page.active_format == "summary" and page.panels["summary"].isVisible()


@pytest.mark.parametrize("mode,font_size,width", [
    ("light", "normal", 1365),
    ("light", "large", 960),
    ("dark", "large", 1365),
])
def test_shared_tabs_and_export_controls_fit_after_scope_and_format_changes(qapp, mode, font_size, width):
    from setron.ui.app import create_window
    from setron.ui.appearance import manager
    from setron.ui.appearance_data import Appearance
    from setron.ui.theme import install_theme

    install_theme(qapp)
    controller = manager()
    previous = controller.appearance
    window = create_window(db_exporter=lambda path: None, data_exporter=lambda path: None,
                           export_file_loader=lambda: (ImportedFile(11, "현황.xlsx", "설비현황표", None),))
    try:
        controller.apply(Appearance(mode=mode, font_size=font_size))
        window.resize(width, 937)
        window.navigate("export")
        window.show()
        value = window.pages["export"]
        assert isinstance(value.tabs, ImportTabs)
        assert [value.tabs.tabText(index) for index in range(value.tabs.count())] == ["Excel", "SQL"]

        def check_bounds():
            QTest.qWait(30)
            kind = value.active_format
            card = value.panels[kind]
            controls = [value.tabs, value.action_bar, value.buttons[kind]]
            if kind == "summary":
                controls.extend((value.all_files_button, value.selected_files_button, value.production_area))
            if value.action_hint.isVisible():
                controls.append(value.action_hint)
            for control in controls:
                child = control
                while child is not value:
                    parent = child.parentWidget()
                    assert parent.rect().contains(child.geometry()), (
                        child.metaObject().className(), child.geometry(), parent.rect())
                    child = parent
            assert card.isAncestorOf(value.action_bar)
            assert card.isAncestorOf(value.buttons[kind])
            assert card.rect().contains(value.action_bar.geometry())

        check_bounds()
        value.tabs.setCurrentIndex(1)
        check_bounds()
        value.tabs.setCurrentIndex(0)
        check_bounds()
        value.selected_files_button.click()
        check_bounds()
        value.all_files_button.click()
        check_bounds()
        window.resize(960, 600)
        QTest.qWait(30)
        check_bounds()
        value.selected_files_button.click()
        assert value.action_hint.isVisible()
        check_bounds()
        value.tabs.setCurrentIndex(1)
        value.set_availability({"sql": "", "summary": ""}, busy="자료를 저장하는 중입니다.")
        assert value.action_hint.isVisible() and not value.buttons["sql"].isEnabled()
        check_bounds()
        value.set_availability({"sql": "", "summary": ""})
        value.tabs.setCurrentIndex(0)
        window.resize(width, 937)
        check_bounds()
    finally:
        window.close()
        controller.apply(previous)
