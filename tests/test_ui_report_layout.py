"""보고서 유형과 Replay 조회 바의 화면 배치."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QStyleOptionViewItem

from setron.ui.report_data import REPORT_TYPES
from setron.ui.version_demo import demo_version_catalog
from test_ui_import_ver5 import own, until  # noqa: F401
from test_ui_reports_ver5 import page


@pytest.mark.parametrize("width", [700, 1000, 1350])
def test_report_controls_fit_and_no_current_time_is_displayed(own, tmp_path, width):
    p = page(own, tmp_path)
    p.resize(width, 1000)
    assert list(p.content_checks) == ["summary", "trend", "distribution", "risk", "evidence"]
    assert not hasattr(p, "clear_all_button")
    assert not any("복수 선택" in text.text() or "히트맵 보기" in text.text()
                   for text in p.config_card.findChildren(QLabel))
    assert p.heatmap_options.parentWidget().isAncestorOf(p.content_checks["summary"])
    assert p.heatmap_title.font().bold()
    common = [control.isChecked() for control in p.content_checks.values()]
    assert not any("도움말" in b.text() for b in p.findChildren(QPushButton))
    descriptions = {
        "status": "선택 범위의 표준화 및 적용 현황",
        "upgrade": "업그레이드 필요 설비, 우선순위, 부족 단계, 판정 근거",
        "item": "선택 Item의 설비별 적용 결과, 판정 근거, 원본 문서 위치",
        "change": "관측일 사이 설비와 PM별 이전 버전 및 다음 버전",
        "ai": "최근 완료된 AI 답변의 요약, 원인 분석, 표, 주의사항",
    }
    for kind, *_ in REPORT_TYPES:
        for control in p.type_buttons.values():
            control.setChecked(False)
        choice = p.type_buttons[kind]
        choice.click()
        QTest.qWait(20)
        title_center = p.contents_title.mapTo(p, p.contents_title.rect().center()).y()
        button_center = p.select_all_button.mapTo(p, p.select_all_button.rect().center()).y()
        assert abs(title_center - button_center) <= 1
        assert isinstance(choice, QCheckBox) and p.selected_types == (kind,)
        assert p.item.isVisibleTo(p) and p.item.isEnabled() == (kind == "item")
        assert p.item_label.isVisibleTo(p) and p.item_label.isEnabled() == (kind == "item")
        assert p.item.parentWidget().isAncestorOf(p.type_buttons["item"])
        assert (p.type_buttons["item"].mapTo(p, QPoint()).y()
                < p.item_label.mapTo(p, QPoint()).y()
                < p.item.mapTo(p, QPoint()).y()
                < p.type_buttons["change"].mapTo(p, QPoint()).y())
        assert not choice.toolTip()
        assert descriptions[kind] in [text.text() for text in p.config_card.findChildren(QLabel)]
        row = choice.parentWidget()
        assert choice.font().bold()
        assert {text.text() for text in row.findChildren(QLabel)} == {"|", descriptions[kind]}
        description = next(text for text in row.findChildren(QLabel) if text.text() != "|")
        assert description.mapTo(p, QPoint()).x() > choice.mapTo(p, QPoint(choice.width(), 0)).x()
        assert [control.isChecked() for control in p.content_checks.values()] == common
        divider_x = p.contents_divider.mapTo(p, QPoint()).x()
        assert p.content_checks["summary"].mapTo(p, QPoint()).x() < divider_x
        assert choice.mapTo(p, QPoint()).x() > divider_x
        title_left = p.heatmap_title.mapTo(p, QPoint()).x()
        title_bottom = p.heatmap_title.mapTo(p, QPoint(0, p.heatmap_title.height())).y()
        assert all(control.mapTo(p, QPoint()).x() >= title_left + 24 for control in p.heatmap_checks.values())
        assert all(control.mapTo(p, QPoint()).y() > title_bottom for control in p.heatmap_checks.values())
        assert (p.content_checks["trend"].mapTo(p, QPoint()).y()
                < p.heatmap_options.mapTo(p, QPoint()).y()
                < p.content_checks["distribution"].mapTo(p, QPoint()).y())
        for text in p.config_card.findChildren(QLabel):
            if text.wordWrap():
                assert text.height() >= text.heightForWidth(text.width())
        for control in (p.query_panel.reset_button, p.query_panel.search_button, choice, p.generate_button):
            assert control.mapTo(p, QPoint(control.width(), 0)).x() <= p.width()
        reset, search = p.query_panel.reset_button, p.query_panel.search_button
        assert reset.size() == search.size()
        assert search.mapTo(p, QPoint(0, 0)).y() == reset.mapTo(p, QPoint(0, 0)).y()
        assert search.mapTo(p, QPoint(0, 0)).x() > reset.mapTo(p, QPoint(reset.width(), 0)).x()
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
