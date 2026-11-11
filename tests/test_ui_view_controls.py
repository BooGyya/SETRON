"""보기 전환과 간격 선택의 공통 조작을 확인한다."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from setron.ui.interval_selector import IntervalSelector
from setron.ui.view_controls import SegmentedControl, bulk_selection_button


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_segmented_control_has_one_selection_and_keyboard_navigation(app):
    control = SegmentedControl()
    for text in ("히트맵", "원형 그래프", "막대 그래프", "꺾은선 그래프"):
        control.addTab(text)
    control.show()
    QTest.qWait(10)
    changed = QSignalSpy(control.currentChanged)

    control.buttons[2].click()
    assert control.currentIndex() == 2 and changed.count() == 1
    assert sum(button.isChecked() for button in control.buttons) == 1
    QTest.keyClick(control.buttons[2], Qt.Key.Key_Right)
    assert control.currentIndex() == 3 and changed.count() == 2
    QTest.keyClick(control, Qt.Key.Key_Home)
    assert control.currentIndex() == 0 and changed.count() == 3
    assert all(control.rect().contains(control.tabRect(index)) for index in range(control.count()))
    control.close()


def test_segment_wheel_scrolls_ancestor_without_changing_selection(app):
    scroll = QScrollArea()
    scroll.resize(400, 200)
    page = QWidget()
    body = QVBoxLayout(page)
    control = SegmentedControl()
    control.addTab("보기 1")
    control.addTab("보기 2")
    body.addWidget(control)
    body.addSpacing(800)
    scroll.setWidget(page)
    scroll.show()
    QTest.qWait(10)
    point = QPointF(control.rect().center())
    event = QWheelEvent(point, point, QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(control, event)
    assert control.currentIndex() == 0 and scroll.verticalScrollBar().value() > 0
    scroll.close()


def test_interval_reuses_segment_and_cancel_keeps_previous_selection(app):
    interval = IntervalSelector("데이터 간격", compact=True, custom_days=True)
    assert isinstance(interval.segment, SegmentedControl)
    interval.set_step("week")
    changed = QSignalSpy(interval.step_changed)
    interval.buttons[None].click()
    dialog = interval.day_dialog
    assert dialog.isVisible() and interval.buttons["week"].isChecked()
    dialog.setIntValue(3)
    dialog.reject()
    assert changed.count() == 0 and interval.buttons["week"].isChecked()

    interval.buttons[None].click()
    interval.day_dialog.setIntValue(3)
    interval.day_dialog.accept()
    assert changed.count() == 1 and changed.at(0) == [3]
    assert interval.buttons[None].isChecked() and interval.day_value == 3
    interval.close()


def test_interval_keyboard_applies_step_and_hiding_cancels_dialog(app):
    interval = IntervalSelector("데이터 간격", compact=True, custom_days=True)
    interval.show()
    interval.set_step("week")
    changed = QSignalSpy(interval.step_changed)
    QTest.keyClick(interval.buttons["week"], Qt.Key.Key_Right)
    assert changed.count() == 1 and changed.at(0) == ["month"]
    interval.buttons[None].click()
    dialog = interval.day_dialog
    assert dialog.isVisible()
    interval.hide()
    assert not dialog.isVisible() and interval.buttons["month"].isChecked()


def test_saved_days_mode_survives_cancel_and_changes_to_custom_days_on_apply(app):
    interval = IntervalSelector("재생 간격", compact=True, custom_days=True)
    interval.set_saved_days()
    changed = QSignalSpy(interval.step_changed)
    first = interval.buttons[None]
    assert interval._current_step is None and first.text() == "저장일마다"
    assert first.toolTip() == "실제 저장된 날짜마다 재생"
    first.click()
    interval.day_dialog.setIntValue(3)
    interval.day_dialog.reject()
    assert changed.count() == 0 and interval._current_step is None
    assert first.text() == "저장일마다" and first.toolTip() == "실제 저장된 날짜마다 재생"

    first.click()
    interval.day_dialog.setIntValue(3)
    interval.day_dialog.accept()
    assert changed.count() == 1 and changed.at(0) == [3]
    assert interval._current_step == 3 and first.text() == "지정일마다"
    assert "3일 간격" in first.toolTip()
    interval.set_saved_days()
    assert interval._current_step is None and first.text() == "저장일마다" and first.isChecked()
    interval.close()


def test_bulk_selection_button_preserves_explicit_contrast(app):
    control = bulk_selection_button("전체 선택")
    assert control.text() == "전체 선택"
    assert "background:#000000;color:#ffffff" in control.styleSheet()
    assert "background:#000000;color:#ffffff" in control._setron_color_style({})
