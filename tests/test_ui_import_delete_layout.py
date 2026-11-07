"""삭제 탭의 VER5 배치·날짜 입력 계약. 합성 목록만 사용하며 실제 삭제는 하지 않는다."""

from datetime import date

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLayout

from setron.ui.import_delete import ImportDeletePage
from setron.ui.import_widgets import DateField
from setron.ui.theme import ASSETS, SECONDARY, svg_size
from test_ui_import_ver5 import catalog, own, select_period  # noqa: F401


def test_date_field_accepts_eight_digits_and_limits_extra_characters(own):
    field = own(DateField())
    field.setFocus()
    assert field.text() == ""
    QTest.keyClicks(field, "20261107")
    assert field.text() == "2026-11-07" and field.hasAcceptableInput()
    QTest.keyClicks(field, "1234abc")
    assert field.text() == "2026-11-07"
    field.clear()
    assert field.text() == ""
    QTest.keyClicks(field, "abc")
    assert field.text() == ""
    QTest.keyClicks(field, "2024-02-29")
    assert field.text() == "2024-02-29"
    assert field.calendar_action.icon().actualSize(svg_size("import-ver5/fdb91.svg")) == svg_size("import-ver5/fdb91.svg")
    assert (ASSETS / "import-ver5/fdb91.svg").stat().st_size > 0


def test_date_field_paste_edit_and_calendar_remain_native(own):
    field = own(DateField())
    clipboard = QApplication.clipboard()
    original = clipboard.text()
    try:
        clipboard.setText("20261107")
        field.paste()
        assert field.text() == "2026-11-07"
        field.selectAll()
        QTest.keyClicks(field, "20260102")
        assert field.text() == "2026-01-02"
        field.undo()
        assert field.text() != "2026-01-02"
        field.clear()
        field.open_calendar()
        calendar = field.calendar_menu.actions()[0].defaultWidget()
        from PySide6.QtCore import QDate
        calendar.clicked.emit(QDate(2026, 8, 21))
        assert field.text() == "2026-08-21"
        field.calendar_menu.close()
    finally:
        clipboard.setText(original)


@pytest.mark.parametrize("text", ["20260230", "20261301", "202611", "00000101"])
def test_invalid_or_partial_calendar_dates_cannot_produce_delete_plan(own, text):
    page = own(ImportDeletePage())
    select_period(page)
    page.start.selectAll()
    QTest.keyClicks(page.start, text)
    assert page.plan() is None and not page.delete_button.isEnabled()
    assert page.range_message.isVisibleTo(page)
    assert SECONDARY in page.range_message.styleSheet()
    assert page.range_message.styleSheet() == page.kind_notice.styleSheet()


@pytest.mark.parametrize("width", [620, 1320])
def test_release_fields_are_adjacent_and_mode_notices_do_not_leak(own, width):
    page = own(ImportDeletePage())
    page.resize(width, 1200)
    QTest.qWait(20)
    assert page.fields_row.grid.sizeConstraint() == QLayout.SizeConstraint.SetNoConstraint
    assert page.fields_row.minimumWidth() == 0
    assert page.quick_choices.isVisibleTo(page) and not page.kind_notice.isVisibleTo(page)
    assert not page.family_box.isVisibleTo(page)
    page.kind_combo.setCurrentIndex(1)
    QTest.qWait(20)
    assert not page.quick_choices.isVisibleTo(page) and page.kind_notice.isVisibleTo(page)
    assert not page.start_box.isVisibleTo(page) and not page.site_box.isVisibleTo(page)
    assert page.family_box.isVisibleTo(page)
    assert page.family_box.y() == page.kind_box.y()
    assert 0 <= page.family_box.x() - page.kind_box.geometry().right() <= 17
    assert page.listing_title.text() == "2. 삭제될 릴리즈 묶음 확인"
    page.kind_combo.setCurrentIndex(0)
    QTest.qWait(20)
    assert page.quick_choices.isVisibleTo(page) and not page.kind_notice.isVisibleTo(page)
    assert page.start_box.isVisibleTo(page) and page.end_box.isVisibleTo(page)
    assert not page.family_box.isVisibleTo(page)
    assert page.listing_title.text() == "2. 삭제될 기준일 확인"


@pytest.mark.parametrize("width", [620, 1320])
def test_quick_chips_and_separate_right_aligned_reload_match_figma(own, width):
    page = own(ImportDeletePage())
    page.resize(width, 1200)
    QTest.qWait(20)
    assert page.quick_label.text() == "빠른 선택"
    assert [b.text() for b in page.quick_buttons] == ["가장 최근 기준일", "최근 1개월", "최근 3개월", "직접 선택"]
    assert all(b.height() == 32 and "border-radius:16px" in b.styleSheet() for b in page.quick_buttons)
    assert page.quick_buttons[-1].isChecked()
    reload_top = page.reload_button.mapTo(page.filters_card, QPoint()).y()
    quick_bottom = page.quick_choices.mapTo(page.filters_card, QPoint(0, page.quick_choices.height())).y()
    assert reload_top >= quick_bottom
    assert page.reload_button.geometry().right() == (page.filters_card.contentsRect().right()
                                                     - page.filters_card.body.contentsMargins().right())
    assert not page.reload_button.isCheckable()
    assert not page.reload() and page.catalog is None  # 배치 변경으로 외부 서비스가 연결되지 않는다.


def test_quick_date_buttons_use_catalog_ranges_and_manual_edit_changes_selection(own):
    page = own(ImportDeletePage())
    page.set_catalog(catalog())
    page.site_combo.setCurrentIndex(page.site_combo.findData("화성"))
    page.quick_buttons[0].click()
    assert page.start.text() == page.end.text() == "2026-10-21"
    page.quick_buttons[1].click()
    assert page.start.text() == "2026-09-21" and page.quick_buttons[1].isChecked()
    page.quick_buttons[2].click()
    assert page.start.text() == "2026-07-21" and page.quick_buttons[2].isChecked()
    page.start.selectAll()
    QTest.keyClicks(page.start, "20260821")
    assert page.quick_buttons[-1].isChecked() and page.plan().start == date(2026, 8, 21)
    page.quick_buttons[-1].click()
    assert page.start.hasFocus()


def test_impact_groups_and_backup_tip_match_source_data_without_inventing_counts(own):
    page = own(ImportDeletePage())
    assert page._check_style.parent() is page.table
    assert [v.text() for v in page.stat_values] == ["—", "—", "—"]
    assert page.backup_button.parentWidget() is page.backup_tip
    assert "#f6f0e0" in page.backup_tip.styleSheet()
    select_period(page)
    assert [v.text() for v in page.stat_values] == ["2개", "116행", "2개"]
    assert page.impact_stats.accessibleName() == page.counts.text()
    page.kind_combo.setCurrentIndex(1)
    page.family_combo.setCurrentIndex(page.family_combo.findData("C4"))
    assert [v.text() for v in page.stat_values[:2]] == ["1개", "900행"]
    assert not page.stat_boxes[2].isVisibleTo(page)
    assert page.stat_titles[0].text() == "삭제할 릴리즈 묶음"
