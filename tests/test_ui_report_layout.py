"""보고서 유형 확정과 긴 Item·서로 다른 전용 입력란의 안정된 카드 배치."""

from dataclasses import replace
from datetime import datetime

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from setron.ui import report_widgets
from setron.ui.report_data import REPORT_TYPES
from test_ui_import_ver5 import own, until  # noqa: F401
from test_ui_reports_ver5 import page


def settle():
    QApplication.processEvents()
    QTest.qWait(30)


def geometry(p):
    panels = p.config_panels
    return (panels.left.geometry(), panels.right.geometry(),
            p.filename.mapTo(p.config, QPoint()), p.generate_button.mapTo(p.config, QPoint()))


@pytest.mark.parametrize("kind,caption", [(r[0], r[1]) for r in REPORT_TYPES])
def test_only_landing_selected_type_is_shown_and_not_clickable(own, tmp_path, kind, caption):
    p = page(own, tmp_path)
    p.create_buttons[kind].click()
    settle()
    assert isinstance(p.kind, report_widgets.SelectedReportType)
    assert p.kind.text() == caption and p.kind.value() == kind
    assert p.kind.caption.width() >= p.kind.caption.sizeHint().width()
    assert p.kind.caption.height() >= p.kind.caption.sizeHint().height()
    assert p.kind.width() <= p.kind.caption.sizeHint().width() + 36
    assert p.kind.width() < p.config_panels.left.width() / 2
    other_types = {r[1] for r in REPORT_TYPES}
    assert not any(b.text() in other_types for b in p.config_panels.left.findChildren(QPushButton))
    assert p.item_field.isVisibleTo(p) == (kind == "item")
    assert p.ai_fields.isVisibleTo(p) == (kind == "ai")
    p.tabs.setCurrentIndex(1)
    p.tabs.setCurrentIndex(0)
    assert p.kind.value() == kind
    until(lambda: not p.item_request.busy)


@pytest.mark.parametrize("width", [700, 1000, 1350])
def test_card_widths_stay_fixed_and_unused_sections_collapse(own, tmp_path, width):
    p = page(own, tmp_path)
    p.resize(width, 1100)
    p.start_flow("status")
    until(lambda: not p.item_request.busy)
    settle()
    heights = {}
    output_y = {}
    for kind, *_ in REPORT_TYPES:
        p.start_flow(kind)
        settle()
        heights[kind] = p.config_panels.height()
        output_y[kind] = p.filename_field.y()
        assert p.item_slot.isVisibleTo(p) == (kind == "item")
        assert p.ai_slot.isVisibleTo(p) == (kind == "ai")
        if kind != "ai":
            last_option = p.options["evidence"]
            gap = p.filename_field.y() - last_option.geometry().bottom() - 1
            assert gap == p.output_body.spacing()
        left, right = p.config_panels.left, p.config_panels.right
        assert left.x() >= 0 and right.geometry().right() < p.config_panels.width()
        if p.config_panels.wide:
            assert left.y() == right.y() and left.height() == right.height()
            assert abs(left.width() / right.width() - 8 / 5) < 0.03
        else:
            assert right.y() > left.geometry().bottom()
        # 대형 빈 예약 영역 대신 보이는 내용과 기본 카드 여백만 남긴다.
        natural = []
        for panel in (left, right):
            body = panel.body
            natural.append(body.totalHeightForWidth(panel.width()) if body.hasHeightForWidth()
                           else body.totalSizeHint().height())
        if p.config_panels.wide:
            assert left.height() <= max(natural) + 2
        else:
            assert left.height() <= natural[0] + 2
            assert right.height() <= natural[1] + 2
        assert p.minimumSizeHint().width() <= width
        if kind == "item":
            assert p.item_note.mapTo(p.item_slot, QPoint()).y() + p.item_note.height() <= p.item_slot.height()
        if kind == "ai":
            last_note = p.ai_fields.layout().itemAt(p.ai_fields.layout().count() - 1).widget()
            assert last_note.mapTo(p.ai_slot, QPoint()).y() + last_note.height() <= p.ai_slot.height()
    assert all(heights[kind] < heights["ai"] for kind in ("status", "upgrade", "item", "change"))
    assert len({output_y[kind] for kind in ("status", "upgrade", "item", "change")}) == 1
    assert output_y["ai"] > output_y["status"]


def test_long_item_original_and_tooltip_preserved_without_expanding_cards(own, tmp_path):
    p = page(own, tmp_path)
    p.start_flow("item")
    until(lambda: not p.item_request.busy)
    settle()
    baseline = geometry(p)
    original = p.catalog
    long_content = "릴리즈 노트의 매우 긴 Item 변경 내용 · " * 100
    first = original.items[0]
    updated = replace(first, item=replace(first.item, content=long_content))
    p._items_loaded(replace(original, items=(updated, *original.items[1:])), None)
    p.item.setCurrentIndex(1)
    settle()
    assert geometry(p) == baseline
    assert long_content in p.item.currentText() and p.item.toolTip() == p.item.currentText()
    assert p.item.minimumSizeHint().width() < 300
    assert p.item.currentData() == first.item.item_id
    p.item.showPopup()
    settle()
    assert geometry(p) == baseline
    p.item.hidePopup()
    p.start_flow("upgrade")
    settle()
    assert p.config_panels.left.width() == baseline[0].width()
    assert p.config_panels.right.width() == baseline[1].width()
    assert not p.item_slot.isVisibleTo(p)
    assert p.config_panels.left.height() <= baseline[0].height()


def test_long_ai_question_does_not_move_output_settings_or_generate_button(own, tmp_path):
    p = page(own, tmp_path)
    p.start_flow("ai")
    until(lambda: not p.item_request.busy)
    settle()
    baseline = geometry(p)
    p.ai_status.setText("완료한 AI 응답 불러옴 · " + "아주 긴 질문 " * 100)
    settle()
    assert geometry(p) == baseline


def test_switching_from_ai_restores_compact_file_controls(own, tmp_path):
    p = page(own, tmp_path)
    p.start_flow("status")
    until(lambda: not p.item_request.busy)
    expected = {}
    for kind, *_ in REPORT_TYPES:
        p.start_flow(kind)
        settle()
        expected[kind] = geometry(p)
    for _ in range(2):
        p.start_flow("ai")
        settle()
        assert p.ai_slot.isVisibleTo(p) and geometry(p) == expected["ai"]
        for kind in ("status", "upgrade", "item", "change"):
            p.start_flow(kind)
            settle()
            assert geometry(p) == expected[kind]
            assert not p.ai_slot.isVisibleTo(p)


def test_landing_current_time_refreshes_and_pauses_when_hidden(own, tmp_path, monkeypatch):
    class Clock:
        value = datetime(2026, 11, 8, 13, 53)

        @classmethod
        def now(cls):
            return cls.value

    monkeypatch.setattr(report_widgets, "datetime", Clock)
    p = page(own, tmp_path)
    clock = p.clock
    clock.timer.setInterval(10)
    assert clock.time_label.text() == "현재 시각 2026-11-08 13:53"
    assert clock.timer.isActive()
    Clock.value = datetime(2026, 11, 8, 13, 54)
    until(lambda: clock.time_label.text() == "현재 시각 2026-11-08 13:54")
    p.start_flow("status")
    assert not clock.timer.isActive()
    Clock.value = datetime(2026, 11, 9, 0, 1)
    p.enter()
    assert clock.time_label.text() == "현재 시각 2026-11-09 00:01"
    assert clock.timer.isActive()
    p.hide()
    assert not clock.timer.isActive()
    Clock.value = datetime(2026, 11, 9, 0, 2)
    p.show()
    assert clock.time_label.text() == "현재 시각 2026-11-09 00:02"
    assert clock.timer.isActive()
    until(lambda: not p.item_request.busy)
