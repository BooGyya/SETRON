"""보고서 유형과 Replay 조회 바의 화면 배치."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QStyleOptionViewItem

from setron.ui.report_data import REPORT_TYPES
from setron.ui.version_demo import demo_version_catalog
from test_ui_import_ver5 import own, until  # noqa: F401
from test_ui_reports_ver5 import page


@pytest.mark.parametrize("width", [700, 1000, 1350])
def test_report_controls_fit_and_no_current_time_is_displayed(own, tmp_path, width):
    p = page(own, tmp_path)
    p.resize(width, 1000)
    for kind, _, _, description, _ in REPORT_TYPES:
        choice = p.type_buttons[kind]
        choice.click()
        QTest.qWait(20)
        assert p.kind == kind and p.item.isVisibleTo(p) == (kind == "item")
        assert p.type_note.isVisibleTo(p)
        assert not choice.toolTip()
        assert p.type_note.text() == description + (" · 답변이 없으면 수치표만 포함" if kind == "ai" else "")
        for control in (p.query_panel.reset_button, choice, p.generate_button):
            assert control.mapTo(p, QPoint(control.width(), 0)).x() <= p.width()
    until(lambda: not p.item_request.busy)
    assert p.minimumSizeHint().width() <= width
    assert not any("현재 시각" in label.text() for label in p.findChildren(QLabel))


def test_long_item_preserves_tooltip_without_expanding_layout(own, tmp_path):
    p = page(own, tmp_path)
    p.type_buttons["item"].click()
    until(lambda: not p.item_request.busy)
    QTest.qWait(20)
    baseline = p.item.width()
    catalog = demo_version_catalog(p.source)
    first = catalog.items[0]
    long_content = "릴리즈 노트의 매우 긴 Item 변경 내용 · " * 100
    changed = replace(first, item=replace(first.item, content=long_content))
    p._items_loaded(replace(catalog, items=(changed, *catalog.items[1:])), None)
    p.item.setCurrentIndex(1)
    QTest.qWait(20)
    assert p.item.width() == baseline
    assert long_content in p.item.currentText() and p.item.toolTip() == p.item.currentText()


def test_saved_reports_prioritize_filenames_and_scroll_within_table(own, tmp_path):
    p = page(own, tmp_path)
    p.resize(1150, 1000)
    name = "SETRON_현황_표준화_보고서_2026-11-09 (1).docx"
    entries = [{"path": str(tmp_path / (name if i == 0 else f"보고서_{i}.pdf")),
                "created": "2026-11-11 00:04:59", "kind": "버전 표준화 리포트",
                "scope": "평택 · Fab 전체 · Line 전체 · 설비군 전체"} for i in range(40)]
    p.saved_entries = entries[:20]
    p._render_library()
    QTest.qWait(30)
    table, model = p.library_table, p.library_model
    filename = model.index(0, 0)
    font = QFontMetrics(filename.data(Qt.ItemDataRole.FontRole))
    assert table.columnWidth(0) > max(table.columnWidth(i) for i in range(1, 5))
    assert table.columnWidth(0) >= font.horizontalAdvance(name) + 20
    assert filename.data() == filename.data(Qt.ItemDataRole.ToolTipRole) == name
    option = QStyleOptionViewItem()
    table.itemDelegate().initStyleOption(option, filename)
    assert "\u200b" in option.text and option.text.replace("\u200b", "") == name
    table.itemDelegate().initStyleOption(option, model.index(0, 1))
    assert option.displayAlignment == Qt.AlignmentFlag.AlignCenter

    height = table.height()
    assert height <= 320 and table.verticalScrollBar().maximum() > 0
    p.saved_entries = entries
    p._render_library()
    QTest.qWait(30)
    assert model.rowCount() == 40 and table.height() == height
    last = model.index(39, 0)
    table.scrollTo(last)
    assert table.verticalScrollBar().value() > 0
    assert table.viewport().rect().intersects(table.visualRect(last))
