"""위험 Item 입력은 스크롤하고, 동작 버튼은 창 안에 고정한다."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QPalette, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from setron.ui.appearance import manager
from setron.ui.appearance_data import Appearance
from setron.ui.item_risk_dialog import ItemRiskDialog

from test_ui_items import app, data


@pytest.fixture
def dialog(app, data):
    value = ItemRiskDialog(None, data, SimpleNamespace(), lambda: None, lambda _: False)
    yield value
    value.reject()
    value.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("size", [(640, 700), (640, 500), (460, 500)])
@pytest.mark.parametrize("criterion", ["VERSION_BELOW", "DIRECT_VERSION", "VERSION_ABOVE"])
def test_scroll_form_keeps_footer_visible_for_all_criteria(dialog, size, criterion):
    dialog.criterion.setCurrentIndex(dialog.criterion.findData(criterion))
    before = dialog.values()
    dialog.setMaximumHeight(size[1])
    dialog.resize(*size)
    dialog.show()
    QTest.qWait(40)
    assert dialog.width() == size[0] and dialog.minimumHeight() <= dialog.height() <= size[1]
    assert dialog.scroll.widget().width() <= dialog.scroll.viewport().width()
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    assert dialog.scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert not dialog.scroll.verticalScrollBar().isVisible()
    assert not dialog.scroll.isAncestorOf(dialog.footer)
    for control in (dialog.next_step, dialog.cancel_button, dialog.save_button):
        bounds = control.rect().translated(control.mapTo(dialog, QPoint()))
        assert dialog.rect().contains(bounds) and control.isVisible()
    footer_position = dialog.footer.pos()
    dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
    QTest.qWait(20)
    assert dialog.footer.pos() == footer_position
    assert dialog.values() == before and not dialog.save_button.isEnabled()
    if size[1] == 500:
        assert dialog.scroll.verticalScrollBar().maximum() > 0
    assert dialog.footer.isAncestorOf(dialog.save_button) and dialog.save_button.text() == "등록"


def test_all_settings_are_visible_without_removed_context_or_disclosure_controls(dialog):
    dialog.resize(460, 500)
    dialog.show()
    QTest.qWait(30)
    assert dialog.advanced.isVisible() and dialog.memo.isVisible() and dialog.name.isVisible()
    assert dialog.criterion.isVisible() and dialog.start.isVisible() and dialog.models.isVisible()
    assert dialog.item_editor.isVisible()  # 별도 Item 없이 열어도 검색·선택을 바로 사용할 수 있다.
    assert not any(hasattr(dialog, name) for name in ("advanced_toggle", "change_item", "source_button",
                                                     "item_note", "item_first_chip", "item_source_chip", "item_family_chip", "item_meta", "end"))
    removed = {"아이템 변경", "원본 행 보기", "상세 설정 접기", "상세 설정 펼치기"}
    assert not any(control.text() in removed for control in dialog.findChildren(QPushButton))
    assert not dialog.save_button.isEnabled()
    dialog.cancel_button.click()
    assert not dialog.isVisible()


def test_long_preview_respects_height_limit_and_keeps_footer_outside_scroll_content(dialog):
    dialog.setMaximumHeight(500)
    dialog.resize(460, 500)
    dialog.show()
    QTest.qWait(30)
    initial_range = dialog.scroll.verticalScrollBar().maximum()
    dialog.criterion.setCurrentIndex(dialog.criterion.findData("VERSION_ABOVE"))
    dialog.preview.setText("대상 범위를 확인해 주세요. " * 80)
    dialog._queue_fit()
    QTest.qWait(40)
    assert dialog.height() <= 500
    assert dialog.scroll.verticalScrollBar().maximum() > initial_range
    footer_position = dialog.footer.pos()
    dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
    assert dialog.footer.pos() == footer_position
    assert dialog.rect().contains(dialog.footer.geometry())
    assert not dialog.save_button.isEnabled()


@pytest.mark.parametrize("size", [(640, 640), (460, 500)])
@pytest.mark.parametrize("font_size", ["normal", "large"])
def test_site_model_and_line_share_one_row_without_form_overflow(dialog, size, font_size):
    theme, previous = manager(), manager().appearance
    theme.apply(Appearance(font_size=font_size))
    try:
        dialog.resize(*size)
        dialog.show()
        dialog.target_summary.setText("위험 적용 대상 100건")
        dialog.target_summary.show()
        dialog.scroll.ensureWidgetVisible(dialog.scope_fields)
        QTest.qWait(40)
        positions = [control.mapTo(dialog.scroll.widget(), QPoint()) for control in (dialog.site, dialog.line, dialog.models)]
        assert len({position.y() for position in positions}) == 1
        assert positions[0].x() < positions[1].x() < positions[2].x()
        assert dialog.scroll.widget().width() <= dialog.scroll.viewport().width()
        assert dialog.scroll.horizontalScrollBar().maximum() == 0
        assert abs(dialog.criterion.width() - dialog.start.width()) <= 1
        assert dialog.target_summary.parentWidget().rect().contains(dialog.target_summary.geometry())
        assert dialog.models.toolTip() == "모델 A · 모델 B"
        for control in (dialog.models, dialog.site, dialog.line):
            bounds = control.rect().translated(control.mapTo(dialog.scope_fields, QPoint()))
            assert dialog.scope_fields.rect().contains(bounds) and control.width() > 50
    finally:
        theme.apply(previous)


def test_reference_version_updates_all_three_strict_relation_labels_and_keeps_item_context(dialog):
    assert [dialog.criterion.itemData(index) for index in range(dialog.criterion.count())] == [
        "VERSION_BELOW", "DIRECT_VERSION", "VERSION_ABOVE"]
    dialog.start.setCurrentIndex(dialog.start.findData(2))
    assert [dialog.criterion.itemText(index) for index in range(3)] == ["v2 미만 버전", "v2 동일 버전", "v2 초과 버전"]
    assert dialog.memo.placeholderText() == "예) 위험 기준 사유"
    for key in ("VERSION_BELOW", "DIRECT_VERSION", "VERSION_ABOVE"):
        dialog.criterion.setCurrentIndex(dialog.criterion.findData(key))
        values = dialog.values()
        assert values["rule_type"] == key and values["release_version_id"] == 2
        assert values["item_id"] == dialog.item.currentData()
    assert "기존" in dialog.target_summary.toolTip()
    assert not any(hasattr(dialog, name) for name in ("stats", "stat_values", "scope_chips"))


def test_hidden_form_scrollbar_still_allows_wheel_and_keyboard_scrolling(dialog):
    dialog.setMaximumHeight(500)
    dialog.resize(460, 500)
    dialog.preview.setText("판정 자료 확인 필요 " * 100)
    dialog.show()
    QTest.qWait(30)
    area, scrollbar = dialog.scroll, dialog.scroll.verticalScrollBar()
    scrollbar.setValue(0)
    assert scrollbar.maximum() > 0 and not scrollbar.isVisible()
    position = area.viewport().rect().center()
    wheel = QWheelEvent(QPointF(position), QPointF(area.viewport().mapToGlobal(position)), QPoint(), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
    QApplication.sendEvent(area.viewport(), wheel)
    assert scrollbar.value() > 0
    before = scrollbar.value()
    area.setFocus()
    QTest.keyClick(area, Qt.Key.Key_PageDown)
    assert scrollbar.value() > before and not scrollbar.isVisible()


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("font_size", ["normal", "large"])
def test_dropdown_rows_fit_and_keyboard_selection_keeps_relation_values(dialog, mode, font_size):
    theme, previous = manager(), manager().appearance
    theme.apply(Appearance(mode=mode, font_size=font_size))
    try:
        dialog.show()
        dialog.scroll.ensureWidgetVisible(dialog.criterion)
        dialog.criterion.showPopup()
        QTest.qWait(30)
        view = dialog.criterion.view()
        assert view.isVisible() and view.font().pixelSize() == dialog.criterion.font().pixelSize()
        assert view.palette().color(QPalette.ColorRole.Base).name() == theme.tokens["surface"]
        assert view.palette().color(QPalette.ColorRole.Highlight).name() == theme.tokens["selected"]
        for row in range(3):
            bounds = view.visualRect(view.model().index(row, 0))
            assert bounds.height() >= view.fontMetrics().height() + 8
            assert view.viewport().rect().contains(bounds)
        QTest.keyClick(view, Qt.Key.Key_Down)
        QTest.keyClick(view, Qt.Key.Key_Return)
        assert dialog.criterion.currentData() == "DIRECT_VERSION" and not view.isVisible()
    finally:
        dialog.criterion.hidePopup()
        theme.apply(previous)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_model_dropdown_keeps_multiple_selection_and_shared_readable_menu_style(dialog, mode):
    theme, previous = manager(), manager().appearance
    theme.apply(Appearance(mode=mode))
    try:
        dialog.show()
        dialog.scroll.ensureWidgetVisible(dialog.models)
        menu = dialog.model_menu
        menu.popup(dialog.models.mapToGlobal(QPoint(0, dialog.models.height() + 4)))
        QTest.qWait(30)
        first = dialog.model_actions[0]
        assert menu.isVisible() and first.isChecked()
        assert menu.actionGeometry(first).height() >= menu.fontMetrics().height() + 8
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(first).center())
        assert not first.isChecked() and dialog.model_actions[1].isChecked()
        assert dialog.values()["item_manager"]["model_ids"] == [2]
    finally:
        dialog.model_menu.hide()
        theme.apply(previous)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_popup_created_after_theme_selection_uses_same_input_colors(app, data, mode):
    theme, previous = manager(), manager().appearance
    theme.apply(Appearance(mode=mode))
    value = ItemRiskDialog(None, data, SimpleNamespace(), lambda: None, lambda _: False)
    try:
        value.show()
        value.criterion.setFocus()
        QTest.qWait(30)
        for control in (value.name, value.memo, value.item_search):
            assert control.palette().color(QPalette.ColorRole.Base).name() == theme.tokens["soft"]
            assert control.palette().color(QPalette.ColorRole.Text).name() == theme.tokens["ink"]
    finally:
        value.reject()
        value.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        theme.apply(previous)
