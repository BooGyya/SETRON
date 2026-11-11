"""SW 계열 드롭다운과 검색·필터가 한 줄 툴바에 놓이는지, 계열 수·창 폭이 달라도 깨지지 않는지 확인한다."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyleOptionViewItem

from setron.ui.app import create_window
from setron.ui.version_data import ModelFamily

from test_ui_items import app, data

NAMES = ("C3 구UI", "C3 통합UI", "C4", "O2", "O3", "O5", "Inline O2", "Cluster O2", "HPA",
         "C3 구UI (파운드리)", "C3 통합UI (파운드리)", "C4 (파운드리)", "O2 (파운드리)", "Inline Ox", "Cluster Ox")


def _open(app, data, names, width=1600):
    pairs = tuple(ModelFamily(1, "모델 A", 10 + i, name) for i, name in enumerate(names))
    if names:  # 합성 릴리즈가 모델 B·계열 10을 참조한다.
        pairs += (ModelFamily(2, "모델 B", 10, names[0]),)
        versions = replace(data.versions, pairs=pairs)
    else:
        versions = replace(data.versions, pairs=(), releases=(), items=(), version_items=())
    value = replace(data, versions=versions)
    result = create_window(item_loader=lambda: value)
    result.resize(width, 900)
    result.show()
    result.navigate("items")
    QTest.qWait(30)
    return result


def _close(result):
    result.close()
    result.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def window(app, data):
    result = _open(app, data, NAMES)
    yield result
    _close(result)


@pytest.mark.parametrize("width", [960, 1100, 1920])
def test_family_dropdown_replaces_chips_and_selects_family(window, width):
    page = window.pages["items"]
    window.resize(width, 900)
    QTest.qWait(50)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert not hasattr(page, "family_title") and page.family_combo.accessibleName() == "SW 계열"
    assert page.family_combo.count() == 15 and page.family_combo.width() >= 220
    page.family_combo.setCurrentIndex(page.family_combo.findData(22))
    assert page.family_id == 22 and page.family_combo.currentData() == 22
    page.render_families()
    assert page.family_id == 22 and page.family_combo.currentData() == 22
    assert page.count.text() == "검색 결과 0건"


@pytest.mark.parametrize("width", [1280, 1920])
def test_toolbar_is_one_row_with_search_on_the_right(window, width):
    page = window.pages["items"]
    window.resize(width, 900)
    QTest.qWait(50)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    bar = page.search
    assert bar.width() == 280 and bar.isVisible()
    assert page.family_combo.size() == bar.size()
    assert page.ai_search in bar.actions() and not page.ai_search.icon().isNull()
    origin = lambda w: w.mapTo(page.results_card, QPoint())
    mid = lambda w: origin(w).y() + w.height() // 2
    assert abs(mid(page.family_combo) - mid(bar)) <= 4 and abs(mid(page.registered_only) - mid(bar)) <= 4
    assert origin(page.count).x() < origin(page.registered_only).x() < origin(page.family_combo).x() < origin(bar).x()
    assert abs(mid(page.count) - mid(bar)) <= 4
    assert origin(page.count).x() == origin(page.toolbar).x()
    assert origin(bar).x() + bar.width() == origin(page.toolbar).x() + page.toolbar.width()
    assert origin(bar).x() - origin(page.family_combo).x() - page.family_combo.width() == 12
    assert origin(page.family_combo).x() - origin(page.registered_only).x() - page.registered_only.width() == 12
    assert page.registered_only.text() == "위험 등록된 Item"
    assert page.registered_only.styleSheet() == window.pages["heatmap"].below_filter.styleSheet()
    assert page.manager.isHidden()


def test_dropdown_and_popup_typography_matches_search(window):
    page = window.pages["items"]
    expected = page.search.font()
    page.result_buttons[1].click()
    for combo in (page.family_combo, page.site_filter, page.state_filter, page.distribution_view):
        window.scroll.ensureWidgetVisible(combo)
        combo.showPopup()
        QTest.qWait(20)
        assert combo.view().window().isVisible()
        for widget in (combo, combo.view()):
            actual = widget.font()
            assert actual.family() == expected.family() and actual.pixelSize() == expected.pixelSize()
            assert actual.weight() == expected.weight() == QFont.Weight.Normal
            assert widget.property("setronBaseFont").pixelSize() == 14
        for row in range(combo.count()):
            option = QStyleOptionViewItem()
            option.initFrom(combo.view())
            combo.itemDelegate().initStyleOption(option, combo.model().index(row, combo.modelColumn()))
            assert option.font.family() == expected.family() and option.font.pixelSize() == expected.pixelSize()
            assert option.font.weight() == QFont.Weight.Normal
        combo.hidePopup()


def test_toolbar_wraps_search_below_on_narrow_width_without_clipping(app, data):
    window = _open(app, data, NAMES, width=800)
    try:
        page = window.pages["items"]
        window.resize(800, 900)
        QTest.qWait(60)
        assert window.scroll.horizontalScrollBar().maximum() == 0
        bar = page.search
        assert page.family_combo.size() == bar.size()
        assert page.toolbar.stacked
        assert bar.mapTo(page.toolbar, QPoint()).y() >= page.family_combo.mapTo(page.toolbar, QPoint()).y() + page.family_combo.height()
        for widget in (page.family_combo, page.registered_only, bar):
            assert page.toolbar.rect().contains(widget.geometry().translated(widget.parentWidget().mapTo(page.toolbar, QPoint())))
        assert bar.mapTo(page.toolbar, QPoint()).x() + bar.width() == page.toolbar.width()
    finally:
        _close(window)


def test_dropdown_popup_keeps_long_names_and_scrolls_with_many_families(app, data):
    names = tuple(f"C{i} 통합UI (파운드리)" for i in range(30))
    window = _open(app, data, names)
    try:
        page = window.pages["items"]
        combo = page.family_combo
        assert combo.count() == 30 and combo.maxVisibleItems() <= 10
        assert combo.view().minimumWidth() >= combo.fontMetrics().horizontalAdvance("C29 통합UI (Foundry)")
        combo.showPopup()
        QTest.qWait(30)
        assert combo.view().window().isVisible()
        combo.hidePopup()
        combo.setCurrentIndex(29)
        assert page.family_id == combo.currentData() and page.count.text() == "검색 결과 0건"
    finally:
        _close(window)


def test_no_family_and_single_family_toolbars(app, data):
    window = _open(app, data, ())
    try:
        page = window.pages["items"]
        assert page.family_combo.count() == 1 and not page.family_combo.isEnabled()
        assert page.family_combo.currentText() == "전달된 SW 계열 없음" and page.family_id is None
        assert page.family_combo.currentData() is None
        assert page.search_answer() is False
    finally:
        _close(window)
    window = _open(app, data, ("C4",))
    try:
        page = window.pages["items"]
        assert page.family_combo.count() == 1 and page.family_combo.currentText() == "C4"
        assert page.family_id == 10 and page.ai_search.isEnabled()
    finally:
        _close(window)


def test_empty_and_reloaded_family_dropdown_keep_label_and_single_message(window):
    page = window.pages["items"]
    original = page.data
    page.data = None
    page.render_families()
    assert page.family_combo.accessibleName() == "SW 계열"
    assert page.family_combo.currentText() == "조회 결과 미확인" and page.family_combo.count() == 1
    assert not page.family_combo.isEnabled()
    page.data = original
    page.render_families()
    assert page.family_combo.isEnabled() and page.family_combo.count() == 15


def test_foundry_labels_change_display_only_and_keep_family_selection(window):
    page = window.pages["items"]
    original_pairs = page.data.versions.pairs
    names = {pair.family_id: pair.family_name for pair in original_pairs}
    assert sum("파운드리" in name for name in names.values()) == 4
    for family_id, name in names.items():
        assert page.family_combo.itemText(page.family_combo.findData(family_id)) == name.replace("파운드리", "Foundry")
    page.family_combo.setCurrentIndex(page.family_combo.findData(22))
    assert page.family_id == 22 and page.family_combo.currentText() == "O2 (Foundry)"
    page.render_families()
    assert page.family_combo.currentData() == 22 and page.family_combo.currentText() == "O2 (Foundry)"
    assert page.data.versions.pairs == original_pairs
    assert names[22] == "O2 (파운드리)"
