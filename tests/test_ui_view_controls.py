"""보기 전환과 간격 선택의 공통 조작을 확인한다."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from setron.ui.interval_selector import IntervalSelector
from setron.ui.heatmap_grouping import GROUPING_MODES
from setron.ui.replay_widgets import ReplayViewControls
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
    interval = IntervalSelector(compact=True, custom_days=True, action="재생")
    interval.set_saved_days()
    changed = QSignalSpy(interval.step_changed)
    first = interval.buttons[None]
    assert interval._current_step is None and first.text() == "지정 일수"
    assert first.toolTip() == "실제 저장된 날짜마다 재생"
    first.click()
    interval.day_dialog.setIntValue(3)
    interval.day_dialog.reject()
    assert changed.count() == 0 and interval._current_step is None
    assert first.text() == "지정 일수" and first.toolTip() == "실제 저장된 날짜마다 재생"

    first.click()
    interval.day_dialog.setIntValue(3)
    interval.day_dialog.accept()
    assert changed.count() == 1 and changed.at(0) == [3]
    assert interval._current_step == 3 and first.text() == "지정 일수"
    assert "3일 간격" in first.toolTip()
    interval.set_saved_days()
    assert interval._current_step is None and first.text() == "지정 일수" and first.isChecked()
    interval.close()


def test_bulk_selection_button_preserves_explicit_contrast(app):
    control = bulk_selection_button("전체 선택")
    assert control.text() == "전체 선택"
    assert "background:#000000;color:#ffffff" in control.styleSheet()
    assert "background:#000000;color:#ffffff" in control._setron_color_style({})


@pytest.mark.parametrize("width", [1400, 480])
def test_replay_controls_keep_one_row_and_scroll_only_inside_the_toolbar(app, width):
    views, grouping = SegmentedControl(), SegmentedControl()
    for text in ("달성률 추이", "히트맵"):
        views.addTab(text)
    for _, text in GROUPING_MODES:
        grouping.addTab(text)
    interval = IntervalSelector(compact=True, custom_days=True, action="재생", show_caption=False)
    interval.set_saved_days()
    controls = ReplayViewControls(views, grouping, interval)
    controls.resize(width, controls.height())
    controls.show()
    QTest.qWait(30)
    try:
        assert [controls.row_body.itemAt(i).widget() for i in range(3)] == [views, grouping, interval]
        assert views.y() == grouping.y() == interval.y()
        assert views.geometry().right() < grouping.x() and grouping.geometry().right() < interval.x()
        assert interval.caption_label.isHidden() and interval.width() == interval.unwrapped_width()
        assert interval.segment.parentWidget() is interval and interval.parentWidget() is controls.row
        assert interval.segment.grid.rowCount() == 1
        assert controls.width() == width and controls.minimumSizeHint().width() == 0
        assert controls.verticalScrollBar().maximum() == 0
        assert (controls.horizontalScrollBar().maximum() > 0) == (width == 480)
        assert controls.scroll_left.isVisible() == controls.scroll_right.isVisible() == (width == 480)
        if width == 480:
            scrollbar = controls.horizontalScrollBar()
            assert not controls.scroll_left.isEnabled() and controls.scroll_right.isEnabled()
            controls.scroll_right.click()
            assert scrollbar.value() > 0 and controls.scroll_left.isEnabled()
            while controls.scroll_right.isEnabled():
                controls.scroll_right.click()
            assert scrollbar.value() == scrollbar.maximum()
            controls.scroll_left.click()
            assert scrollbar.value() < scrollbar.maximum()
        changed = QSignalSpy(interval.step_changed)
        interval.buttons["week"].click()
        assert changed.count() == 1 and changed.at(0) == ["week"]
        interval.buttons[None].click()
        assert interval.day_dialog.parentWidget() is controls.window()
        interval.day_dialog.reject()
        assert interval.buttons["week"].isChecked() and changed.count() == 1
    finally:
        controls.close()


def test_non_custom_interval_keeps_saved_date_label_and_semantics(app):
    interval = IntervalSelector(custom_days=False)
    first = interval.buttons[None]
    changed = QSignalSpy(interval.step_changed)
    assert first.text() == "저장일마다" and interval._current_step is None
    interval.set_step("week")
    first.click()
    assert first.text() == "저장일마다" and interval._current_step is None
    assert changed.count() == 1 and changed.at(0) == [None] and interval.day_dialog is None
    interval.close()
