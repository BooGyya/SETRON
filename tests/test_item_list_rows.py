"""아이템 목록: 표시 이름 통일, 자료 없는 계열 표시, 행의 릴리즈 버전, 긴 제목, 페이지 이동, 행 어포던스."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QBrush
from PySide6.QtTest import QTest

from setron.features.item.schemas import Item, ItemState, TargetScope, VersionItem
from setron.ui.app import create_window
from setron.ui.version_data import ItemEntry, ModelFamily, family_display_name

from test_ui_items import app, data


def _open(app, value, width=1600):
    window = create_window(item_loader=lambda: value)
    window.resize(width, 960)
    window.show()
    window.navigate("items")
    QTest.qWait(30)
    return window


def _close(window):
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _with_family(data, name="C4 (파운드리)"):
    pairs = (*data.versions.pairs, ModelFamily(1, "모델 A", 99, name))
    return replace(data, versions=replace(data.versions, pairs=pairs))


@pytest.fixture
def window(app, data):
    result = _open(app, _with_family(data))
    yield result
    _close(result)


def test_display_name_changes_only_the_label():
    assert family_display_name("C4 (파운드리)") == "C4 (Foundry)" and family_display_name("C4") == "C4"


def test_empty_family_is_dimmed_selectable_and_titled_without_count(window):
    page = window.pages["items"]
    combo = page.family_combo
    empty, full = combo.findData(99), combo.findData(10)
    assert page.family_item_counts == {10: 2, 99: 0}  # 릴리즈에 연결된 Item만 센다(연결 없는 Item 3 제외)
    assert combo.itemData(empty, Qt.ItemDataRole.ForegroundRole) is not None
    assert combo.itemData(full, Qt.ItemDataRole.ForegroundRole) is None
    assert combo.view().sizeHintForRow(empty) == combo.view().sizeHintForRow(full)
    assert "건" not in combo.itemText(empty) and combo.itemText(empty) == "C4 (Foundry)"
    assert page.count.text() == "SW A 아이템 전체 2건"
    combo.setCurrentIndex(empty)
    assert page.family_id == 99 and page.count.text() == "C4 (Foundry)"
    assert not page.family_notice.isHidden()
    assert page.family_notice_title.text() == "C4 (Foundry)에 연결된 릴리즈노트 아이템이 없습니다."
    assert "파운드리" not in page.count.text() + page.family_notice_title.text()


def test_row_shows_representative_release_and_tooltip(window):
    page = window.pages["items"]
    first = page.result_buttons[1]
    assert first.version == "v99 외 1" and first.version_label.isVisible()
    assert "v99" in first.toolTip() and "v3" in first.toolTip() and "출시일" in first.toolTip()
    assert first.toolTip().startswith("Alarm <원문> & 보존")
    assert "\n포함 릴리즈: v99 외 1\n" in first.toolTip()  # 행의 버전은 '최초 포함'이 아니라 포함 릴리즈
    assert first.accessibleDescription().startswith("포함 릴리즈 v99")
    assert page.result_buttons[2].toolTip().split("\n")[1].startswith("포함 릴리즈: v99")
    assert page.result_buttons[2].version == "v99"
    assert page.result_buttons[2].version_label.text() == "v99"
    assert page.result_buttons[2].cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_summary_explains_unknown_first_version_without_contradicting_row(app, data):
    unordered = tuple(replace(e, release=replace(e.release, version_order=None)) for e in data.versions.releases)
    window = _open(app, replace(data, versions=replace(data.versions, releases=unordered)))
    try:
        page = window.pages["items"]
        row = page.result_buttons[1]
        assert row.version.startswith("v")  # 행에는 포함 릴리즈가 보인다
        row.click()
        assert page.first_value.text() == "미확인"
        note = page.first_note.text()
        assert "버전 순서 미확정" in note and "포함 릴리즈 " + row.version in note
    finally:
        _close(window)


def test_summary_shows_the_observation_date_below_both_metrics(window):
    page = window.pages["items"]
    page.result_buttons[1].click()
    assert page.first_value.text() != "미확인" and page.first_note.isHidden()
    assert "판정 불가" not in page.inline_equipment.text()
    assert page.inline_date.text() == f"{page.data.base_date} 기준"
    assert page.inline_date.parentWidget() is page.inline_box


def test_long_title_is_elided_in_one_line_and_keeps_badge_and_version(app, data):
    title = "Application Startup 초기화 절차 검증 기능 개선 — 필수 Module 초기화 누락 시 Startup 중단 원인 표시 " * 3
    value = replace(data, versions=replace(data.versions, items=(ItemEntry(Item(1, 1, title)),) + data.versions.items[1:]))
    window = _open(app, value, width=1000)
    try:
        page = window.pages["items"]
        row = page.result_buttons[1]
        assert len(title) > 100 and row.toolTip().startswith(title)
        assert row.height() == page.result_buttons[2].height() and window.scroll.horizontalScrollBar().maximum() == 0
        assert row.caption.text().endswith("…") and row.caption.text() != title
        assert row.version_label.isVisible()
        assert row.caption.geometry().right() <= row.version_label.geometry().left()
    finally:
        _close(window)


def test_row_hover_checked_arrow_and_toggle(window):
    page = window.pages["items"]
    row = page.result_buttons[1]
    assert ":hover" in row.styleSheet() and "#f5f6f8" in row.styleSheet()
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(40, 20))  # 행 어디를 눌러도 펼쳐진다.
    assert row.isChecked() and page.mode == "detail"
    assert row.arrow.expanded
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(40, 20))  # 다시 누르면 접힌다.
    assert not row.isChecked() and not row.arrow.expanded and page.mode == "list"
    assert not row.keyboard_focus  # 마우스로 펼치고 접어도 포커스 링 잔상이 남지 않는다.


def test_detail_controls_are_consistent_and_not_blank(window):
    page = window.pages["items"]
    page.result_buttons[1].click()
    page.more_button.setChecked(True)
    QTest.qWait(50)
    # 같은 동작의 두 버튼은 같은 문구이고, 보기 전환 콤보는 글자가 보일 만큼의 폭을 가진다.
    assert page.inline_register.text() == page.register_rule.text()
    assert page.distribution_view.width() >= 160 and page.distribution_view.currentText() == "설비 목록"
    page.back_to_list()
    page.result_buttons[2].click()
    assert page.inline_register.text() == page.register_rule.text() == "위험 기준 등록"


def test_keyboard_focus_ring_is_solid_and_only_for_keyboard(window):
    page = window.pages["items"]
    row = page.result_buttons[2]
    assert "dashed" not in row.styleSheet()
    row.setFocus(Qt.FocusReason.MouseFocusReason)
    assert not row.keyboard_focus
    row.setFocus(Qt.FocusReason.TabFocusReason)
    row.clearFocus()
    row.setFocus(Qt.FocusReason.TabFocusReason)
    assert row.keyboard_focus
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(40, 20))
    assert not row.keyboard_focus
    row.setFocus(Qt.FocusReason.TabFocusReason)
    row.clearFocus()
    assert not row.keyboard_focus


def test_rows_are_reused_and_no_toplevel_window_flashes(many):
    page = many.pages["items"]
    pool = list(page.row_pool)
    shown = []

    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication, QWidget

    class Spy(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
                shown.append(type(obj).__name__)
            return False

    spy = Spy()
    QApplication.instance().installEventFilter(spy)
    try:
        page.change_page(1)
        page.change_page(1)
        page.go_to_page(0)
        page.search.setText("항목 1")
    finally:
        QApplication.instance().removeEventFilter(spy)
    assert page.row_pool[:len(pool)] == pool  # 같은 위젯을 재사용
    assert shown == []  # 행 위젯이 부모 없이 show 되어 빈 창이 뜨지 않는다


@pytest.fixture
def many(app, data):
    value = replace(data, versions=replace(data.versions,
        items=tuple(ItemEntry(Item(i, i, f"항목 {i}")) for i in range(1, 29)),
        version_items=tuple(VersionItem(1, i, ItemState.APPLIED, TargetScope.ALL) for i in range(1, 29))))
    window = _open(app, value)
    yield window
    _close(window)


def _tokens(page):
    return [b.text() for b in page.page_bar.numbers if not b.isHidden()]


def test_number_pagination_window_current_highlight_and_names(many):
    page = many.pages["items"]
    bar = page.page_bar
    assert page.page_total == 3 and _tokens(page) == ["1", "2", "3"]
    assert not page.first_page.isEnabled() and not page.previous.isEnabled() and page.next.isEnabled()
    current = [b for b in bar.numbers if b.property("current")]
    assert [b.text() for b in current] == ["1"] and current[0].accessibleDescription() == "현재 페이지"
    assert bar.numbers[1].accessibleName() == "2페이지로 이동"
    bar.numbers[2].click()
    assert page.page_index == 2 and not page.last_page.isEnabled() and not page.next.isEnabled()
    page.first_page.click()
    assert page.page_index == 0
    page.next.click()
    assert page.page_index == 1
    page.last_page.click()
    assert page.page_index == 2 and [b.text() for b in bar.numbers if b.property("current")] == ["3"]
    page.search.setText("항목 1")  # 검색하면 1페이지로 돌아간다.
    assert page.page_index == 0


def test_many_pages_use_ellipsis_window_and_ten_page_jump(app, data):
    value = replace(data, versions=replace(data.versions,
        items=tuple(ItemEntry(Item(i, i, f"항목 {i}")) for i in range(1, 649)),
        version_items=tuple(VersionItem(1, i, ItemState.APPLIED, TargetScope.ALL) for i in range(1, 649))))
    window = _open(app, value, width=1000)
    try:
        page = window.pages["items"]
        bar = page.page_bar
        assert page.page_total == 54 and _tokens(page) == ["1", "2", "3", "4", "5", "…", "54"]
        assert window.scroll.horizontalScrollBar().maximum() == 0 and bar.geometry().right() <= page.results_card.width()
        next(b for b in bar.numbers if b.text() == "…").click()  # 10페이지 뒤로
        assert page.page_index == 10 and _tokens(page) == ["1", "…", "10", "11", "12", "…", "54"]
        next(b for b in bar.numbers if b.text() == "…" and "앞" in b.accessibleName()).click()
        assert page.page_index == 1
        page.go_to_page(53)
        assert _tokens(page) == ["1", "…", "50", "51", "52", "53", "54"] and page.page_index == 53
        page.go_to_page(999)  # 범위 밖은 보정
        assert page.page_index == 53
        page.go_to_page(-5)
        assert page.page_index == 0
    finally:
        _close(window)


def test_paging_controls_hidden_for_single_page(window):
    page = window.pages["items"]
    assert page.page_bar.isHidden()
