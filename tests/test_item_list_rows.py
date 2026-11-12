"""아이템 목록: 표시 이름 통일, 자료 없는 계열 표시, 행의 릴리즈 버전, 긴 제목, 페이지 이동, 행 어포던스."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QBrush, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyle, QStyleOptionComboBox

from setron.features.item.schemas import Item, ItemState, TargetScope, VersionItem
from setron.features.version.schemas import ReleaseVersion
from setron.ui.app import create_window
from setron.ui.theme import INK
from setron.ui.version_data import ItemEntry, ModelFamily, ReleaseEntry, family_display_name

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


def test_initial_family_skips_empty_and_keeps_user_selection_on_reload(app, data):
    versions = replace(data.versions,
        pairs=(ModelFamily(1, "모델 A", 1, "빈 계열"), *data.versions.pairs,
               ModelFamily(1, "모델 A", 20, "다른 계열")),
        releases=(*data.versions.releases, ReleaseEntry(ReleaseVersion(20, 20, "v1", 1), (1,))),
        version_items=(*data.versions.version_items, VersionItem(20, 2, ItemState.APPLIED, TargetScope.ALL)))
    window = _open(app, replace(data, versions=versions))
    try:
        page = window.pages["items"]
        assert page.family_combo.itemData(0) == 1 and page.family_item_counts[1] == 0
        assert page.family_id == page.family_combo.currentData() == 10
        assert tuple(page.result_buttons) == (1, 2)

        page.family_combo.setCurrentIndex(page.family_combo.findData(1))
        page.render_families()
        assert page.family_id == page.family_combo.currentData() == 1
        assert not page.result_buttons

        page.family_combo.setCurrentIndex(page.family_combo.findData(20))
        page.reload()
        QTest.qWait(30)
        window.navigate("dashboard")
        window.navigate("items")
        assert page.family_id == page.family_combo.currentData() == 20
        assert tuple(page.result_buttons) == (2,)
    finally:
        _close(window)


def test_empty_family_is_dimmed_selectable_and_titled_without_count(window):
    page = window.pages["items"]
    combo = page.family_combo
    empty, full = combo.findData(99), combo.findData(10)
    assert page.family_item_counts == {10: 2, 99: 0}  # 릴리즈에 연결된 Item만 센다(연결 없는 Item 3 제외)
    assert combo.itemData(empty, Qt.ItemDataRole.ForegroundRole) is not None
    assert combo.itemData(full, Qt.ItemDataRole.ForegroundRole) is None
    assert combo.view().sizeHintForRow(empty) == combo.view().sizeHintForRow(full)
    assert "건" not in combo.itemText(empty) and combo.itemText(empty) == "C4 (Foundry)"
    assert page.count.text() == "검색 결과 2건"
    combo.setCurrentIndex(empty)
    assert page.family_id == 99 and page.count.text() == "검색 결과 0건"
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
    assert page.result_buttons[2].version_label.text() == "" and page.result_buttons[2].version_label.isVisible()
    assert page.result_buttons[2].caption.x() == first.caption.x()
    assert page.result_buttons[2].cursor().shape() == Qt.CursorShape.PointingHandCursor


@pytest.mark.parametrize("width", [1600, 960])
def test_list_header_jira_style_and_expanded_detail_avoid_repeated_summary(app, data, width):
    entries = tuple(replace(entry, item=replace(entry.item, jira_code="ETOJ-9073" if entry.item.item_id == 1 else None),
                            jira_key_known=True) for entry in data.versions.items)
    window = _open(app, replace(data, versions=replace(data.versions, items=entries)), width=width)
    try:
        page = window.pages["items"]
        header = page.result_header
        assert header.isVisible() and page.result_rows.indexOf(header) == 0
        assert [header.version_label.text(), header.caption.text(), header.jira_label.text()] == ["버전", "업데이트 내용", "JIRA"]
        first, second = page.result_buttons[1], page.result_buttons[2]
        assert [first.jira_label.text(), second.jira_label.text()] == ["ETOJ-9073", "-"]
        for row in (first, second):
            assert row.jira_label.isVisible()
            assert row.jira_label.font() == row.version_label.font() == row.caption.font()
            assert row.jira_label.property("setronBaseFont").pixelSize() == 15
            assert row.jira_label.property("setronBaseFont").weight() == QFont.Weight.Medium
            assert INK in row.jira_label.styleSheet()
            for name in ("version_label", "caption", "jira_label"):
                actual = getattr(row, name).mapTo(page.result_host, QPoint()).x()
                expected = getattr(header, name).mapTo(page.result_host, QPoint()).x()
                assert abs(actual - expected) <= 1
        page.search.setText("ETOJ-9073")
        assert tuple(page.result_buttons) == (1,)
        page.result_buttons[1].click()
        QTest.qWait(30)
        assert page.application_details.isVisible()
        assert page.item_facts.isHidden() and page.item_facts.text() == ""
        assert window.scroll.horizontalScrollBar().maximum() == 0
    finally:
        _close(window)


def test_version_groups_keep_title_alignment_across_pages_filters_and_reused_rows(app, data):
    from setron.features.risk.schemas import RiskRule, RuleType

    items = tuple(ItemEntry(Item(i, i, f"항목 {i}")) for i in range(1, 9))
    links = tuple(VersionItem(4 if i <= 3 else 2 if i <= 6 else 1, i, ItemState.APPLIED, TargetScope.ALL)
                  for i in range(1, 9)) + (VersionItem(5, 2, ItemState.APPLIED, TargetScope.ALL),)
    rules = tuple(RiskRule(i, RuleType.SAFETY_REQUIRED, 10, "등록 기준", item_id=i) for i in (2, 3, 5, 6, 8))
    versions = replace(data.versions, items=items, version_items=links,
                       releases=(*data.versions.releases, ReleaseEntry(ReleaseVersion(5, 10, "v4", 4), (1, 2))))
    window = _open(app, replace(data, versions=versions, risk_rules=rules))
    try:
        page = window.pages["items"]
        page.page_size = 4
        page.render_list()
        pool = list(page.row_pool)

        def assert_rows(ids, labels):
            QTest.qWait(20)
            rows = list(page.result_buttons.values())
            assert tuple(page.result_buttons) == ids
            assert [row.version_label.text() for row in rows] == labels
            assert all(row.version_label.isVisible() for row in rows)
            assert len({row.version_label.width() for row in rows}) == 1
            assert len({row.caption.x() for row in rows}) == 1
            assert all(row.caption.x() - row.version_label.x() - row.version_label.width() == 16 for row in rows)
            assert page.row_pool[:len(pool)] == pool

        assert_rows((1, 2, 3, 4), ["v3", "", "", "v2"])
        second = page.result_buttons[2]
        assert second.version == "v3 외 1"  # 포함 목록이 달라도 대표 릴리즈 ID가 같으면 같은 그룹이다.
        assert "포함 릴리즈 v3 외 1" in second.accessibleDescription()
        assert "포함 릴리즈: v3 외 1" in second.toolTip()
        page.next.click()
        assert_rows((5, 6, 7, 8), ["v2", "", "v99", ""])
        page.search.setText("항목 6")
        assert_rows((6,), ["v2"])
        page.search.clear()
        page.registered_only.setChecked(True)
        assert_rows((2, 3, 5, 6), ["v3 외 1", "", "v2", ""])
        page.next.click()
        assert_rows((8,), ["v99"])
        page.registered_only.setChecked(False)
        assert_rows((1, 2, 3, 4), ["v3", "", "", "v2"])
    finally:
        _close(window)


def test_row_strips_only_linked_version_suffix_and_keeps_original_content(window, data):
    page = window.pages["items"]
    for content, displayed in (("동작 개선 (v99)", "동작 개선"),
                               ("동작 개선 (v2)", "동작 개선"),  # 미적용 연결도 알려진 원본 버전이다.
                               ("동작 개선 (참고)", "동작 개선 (참고)"),
                               ("동작 개선 (v123)", "동작 개선 (v123)")):
        entry = replace(data.versions.items[0], item=replace(data.versions.items[0].item, content=content))
        value = replace(data, versions=replace(data.versions, items=(entry, *data.versions.items[1:])))
        page.loader = lambda current=value: current
        page.reload()
        QTest.qWait(20)
        row = page.result_buttons[1]
        assert row.caption.text() == displayed
        assert row.toolTip().startswith(content) and row.accessibleName() == content
        assert page.data.versions.items[0].item.content == content
        assert row.version_label.x() < row.caption.x() < row.arrow.x()
        assert row.caption.x() - row.version_label.x() - row.version_label.width() == 16
        assert row.version_label.font() == row.caption.font()


def test_items_use_latest_displayed_included_version_before_pagination_and_filters(app, data):
    from setron.features.risk.schemas import RiskRule, RuleType
    from setron.infrastructure.ai_client import ResponseSource
    from setron.ui.item_analysis import ItemAnswer

    items = tuple(ItemEntry(Item(i, i, f"{'선택' if i < 5 else '제외'} 항목 {i}")) for i in range(1, 6))
    links = tuple(VersionItem(release, item, ItemState.APPLIED, TargetScope.ALL)
                  for release, item in ((1, 1), (4, 1), (2, 2), (3, 3), (2, 4), (4, 5)))
    rules = tuple(RiskRule(i, RuleType.SAFETY_REQUIRED, 10, "등록 기준", item_id=i) for i in (1, 2, 3, 5))
    value = replace(data, versions=replace(data.versions, items=items, version_items=links), risk_rules=rules)
    window = _open(app, value)
    try:
        page = window.pages["items"]
        page.page_size = 2
        page.render_list()
        assert tuple(page.result_buttons) == (5, 2)  # v3(order 3), v2(order 2): 문자열 크기와 무관하다.
        page.next.click()
        assert tuple(page.result_buttons) == (4, 1)  # 같은 v2는 원래 Item 순서, 대표가 v99인 Item 1은 뒤에 둔다.
        assert page.result_buttons[1].version == "v99 외 1"
        page.next.click()
        assert tuple(page.result_buttons) == (3,)  # v1000은 순서 미확인이라 마지막이다.

        page.search.setText("선택")
        assert tuple(page.result_buttons) == (2, 4)
        page.next.click()
        assert tuple(page.result_buttons) == (1, 3)

        page.search.clear()
        page.registered_only.setChecked(True)
        assert tuple(page.result_buttons) == (5, 2)
        page.next.click()
        assert tuple(page.result_buttons) == (1, 3)

        page.registered_only.setChecked(False)
        page.answer = ItemAnswer(value, 10, "", (1, 3, 4, 5), ResponseSource.RULE)
        page.page_index = 0
        page.render_list()
        assert tuple(page.result_buttons) == (5, 4)
        page.next.click()
        assert tuple(page.result_buttons) == (1, 3)
    finally:
        _close(window)


def test_selected_item_keeps_original_content_when_release_order_is_unknown(app, data):
    unordered = tuple(replace(e, release=replace(e.release, version_order=None)) for e in data.versions.releases)
    window = _open(app, replace(data, versions=replace(data.versions, releases=unordered)))
    try:
        page = window.pages["items"]
        row = page.result_buttons[1]
        assert row.version.startswith("v")  # 행에는 포함 릴리즈가 보인다
        row.click()
        assert page.title.text() == data.versions.items[0].item.content
        assert not hasattr(page, "first_value") and not hasattr(page, "first_note")
    finally:
        _close(window)


@pytest.mark.parametrize("width", [1600, 960])
def test_open_row_shows_application_toolbar_and_preserves_original_multiline_content(app, data, width):
    content = "Alarm <원문> & 보존\n" + "모듈 초기화 누락 시 원인과 복구 절차를 표시합니다. " * 12 + "\n마지막 줄 <br> 그대로"
    entry = replace(data.versions.items[0], item=replace(data.versions.items[0].item, content=content))
    value = replace(data, versions=replace(data.versions, items=(entry, *data.versions.items[1:])))
    window = _open(app, value, width=width)
    try:
        page = window.pages["items"]
        page.result_buttons[1].click()
        QTest.qWait(30)
        assert page.title.text() == content and page.title.wordWrap()
        assert page.title.textFormat() == Qt.TextFormat.PlainText
        assert not page.title.isVisible() and page.result_buttons[1].toolTip().startswith(content)
        assert page.summary.parentWidget() is page.application_toolbar and page.summary.isVisible()
        assert [badge.text() for badge in page.summary.widgets] == ["적용 1대", "미적용 1대", "미확인 2대", "대상 외 1대", "HPA 제외 1대"]
        assert page.application_details.isVisible() and page.equipment_table.isVisible()
        assert not any(hasattr(page, name) for name in ("more_button", "update_card", "first_value", "missing_value", "inline_date", "inline_equipment", "observation_note", "content"))
        assert page.summary.isVisible() and page.application_details.isAncestorOf(page.summary)
        assert all(badge.isVisible() for badge in page.summary.widgets)
        assert page.application_toolbar.stacked == (width == 960)
        origin = lambda widget: widget.mapTo(page.application_toolbar, QPoint())
        badge_right = max(origin(badge).x() + badge.width() for badge in page.summary.widgets)
        assert badge_right == page.application_toolbar.width()
        if page.application_toolbar.stacked:
            assert origin(page.summary).y() >= origin(page.filter_row).y() + page.filter_row.height()
        else:
            assert origin(page.filter_row).x() == 0
            assert origin(page.summary).x() >= origin(page.filter_row).x() + page.filter_row.width()
        assert window.scroll.horizontalScrollBar().maximum() == 0
        # 목록에 보이지 않는 Item을 직접 열 때도 같은 카드와 원문을 사용한다.
        page.search.setText("없는 검색어")
        assert page.open_item_id(1)
        QTest.qWait(30)
        assert page.title.parentWidget() is page.full_detail and page.title.text() == content
        assert page.title.isVisible() and page.summary.isVisible()
        assert page.title.heightForWidth(page.title.width()) <= page.title.height()
    finally:
        _close(window)


def test_application_dropdown_arrows_open_popups_and_apply_selection(window):
    page = window.pages["items"]
    page.result_buttons[1].click()
    assert page.distribution_view.isHidden()
    for combo in (page.site_filter, page.state_filter):
        window.scroll.ensureWidgetVisible(combo)
        QTest.qWait(30)
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        arrow = combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                            QStyle.SubControl.SC_ComboBoxArrow, combo)
        assert arrow.isValid() and combo.rect().contains(arrow)
        QTest.mouseClick(combo, Qt.MouseButton.LeftButton, pos=arrow.center())
        assert combo.view().isVisible()
        QTest.qWait(20)
        QTest.keyClick(combo.view(), Qt.Key.Key_Down)
        QTest.keyClick(combo.view(), Qt.Key.Key_Return)
        QTest.qWait(20)
        assert not combo.view().window().isVisible() and combo.currentIndex() == 1
        combo.setCurrentIndex(0)


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
        assert row.caption.x() - row.version_label.x() - row.version_label.width() == 16
        assert row.version_label.font() == row.caption.font() and row.version_label.styleSheet() == row.caption.styleSheet()
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
    assert page.application_details.isVisible()
    window.resize(960, 760)
    QTest.qWait(50)
    assert page.application_details.parentWidget() is page.inline_box
    assert page.full_detail.isHidden() and page.application_details.isVisible()
    assert page.equipment_table.isVisible() and window.scroll.horizontalScrollBar().maximum() == 0
    assert page.register_rule.isVisible()
    assert page.distribution_view.currentText() == "설비 목록"
    assert all(100 <= combo.width() <= 184 for combo in (page.site_filter, page.state_filter, page.distribution_view))
    assert not hasattr(page, "source_button") and not hasattr(page, "scope_count")
    page.site_filter.setCurrentIndex(1)
    page.result_buttons[2].click()
    assert page.selected_id == 2 and page.application_details.isVisible()
    assert page.site_filter.currentData() is None and page.register_rule.text() == "위험 기준 등록"
    page.result_buttons[2].click()
    assert page.detail_view.isHidden() and not page.equipment_table.isVisible()


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


@pytest.mark.parametrize("width", [1600, 960])
def test_short_last_page_keeps_card_height_and_pagination_position(many, width):
    page = many.pages["items"]
    many.resize(width, 960)
    QTest.qWait(30)
    assert len(page.visible_entries) == page.page_size
    card_height = page.results_card.height()
    pagination_y = page.page_bar.mapTo(page.results_card, QPoint()).y()

    page.last_page.click()
    QTest.qWait(30)

    assert len(page.visible_entries) == 4
    assert page.results_card.height() == card_height
    assert page.page_bar.mapTo(page.results_card, QPoint()).y() == pagination_y
    window_scroll = many.scroll
    assert window_scroll.horizontalScrollBar().maximum() == 0
    next(iter(page.result_buttons.values())).click()
    QTest.qWait(30)
    window_scroll.ensureWidgetVisible(page.equipment_table)
    assert page.application_details.isVisible() and page.inline_box.isAncestorOf(page.equipment_table)
    assert page.result_rows.indexOf(page.detail_view) > 0
    page.first_page.click()
    QTest.qWait(30)
    assert page.detail_view.isHidden() and page.results_card.height() == card_height


def test_many_pages_use_fixed_five_page_blocks_and_keep_first_last_controls(app, data):
    value = replace(data, versions=replace(data.versions,
        items=tuple(ItemEntry(Item(i, i, f"항목 {i}")) for i in range(1, 649)),
        version_items=tuple(VersionItem(1, i, ItemState.APPLIED, TargetScope.ALL) for i in range(1, 649))))
    window = _open(app, value, width=1000)
    try:
        page = window.pages["items"]
        bar = page.page_bar
        assert page.page_total == 54 and _tokens(page) == ["1", "2", "3", "4", "5"]
        assert window.scroll.horizontalScrollBar().maximum() == 0 and bar.geometry().right() <= page.results_card.width()
        page.go_to_page(4)
        assert _tokens(page) == ["1", "2", "3", "4", "5"]
        page.next.click()
        assert page.page_index == 5 and _tokens(page) == ["6", "7", "8", "9", "10"]
        assert [b.text() for b in bar.numbers if b.property("current")] == ["6"]
        assert bar.numbers[0].accessibleName() == "6페이지로 이동"
        page.previous.click()
        assert page.page_index == 4 and _tokens(page) == ["1", "2", "3", "4", "5"]
        page.last_page.click()
        assert _tokens(page) == ["51", "52", "53", "54"] and page.page_index == 53
        assert not page.last_page.isEnabled() and not page.next.isEnabled()
        page.go_to_page(999)  # 범위 밖은 보정
        assert page.page_index == 53
        page.first_page.click()
        assert page.page_index == 0 and _tokens(page) == ["1", "2", "3", "4", "5"]
        page.go_to_page(-5)
        assert page.page_index == 0
    finally:
        _close(window)


def test_paging_controls_hidden_for_single_page(window):
    page = window.pages["items"]
    assert page.page_bar.isHidden()
