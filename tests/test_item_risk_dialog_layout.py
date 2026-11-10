"""위험 Item 입력은 스크롤하고, 동작 버튼은 창 안에 고정한다."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest

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
@pytest.mark.parametrize("criterion", ["SAFETY_REQUIRED", "DIRECT_VERSION", "DEFECT_ITEM"])
def test_scroll_form_keeps_footer_visible_for_all_criteria(dialog, size, criterion):
    dialog.criterion.setCurrentIndex(dialog.criterion.findData(criterion))
    before = dialog.values()
    dialog.resize(*size)
    dialog.show()
    QTest.qWait(40)
    assert dialog.width() == size[0] and dialog.height() == size[1]
    assert dialog.scroll.widget().width() <= dialog.scroll.viewport().width()
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
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
    verified = dialog.verified.rect().translated(dialog.verified.mapTo(dialog, QPoint()))
    assert dialog.rect().contains(verified) and dialog.footer.isAncestorOf(dialog.verified)


def test_tab_focus_reaches_fixed_confirmation_and_cancel_stays_accessible(dialog):
    dialog.resize(460, 500)
    dialog.show()
    dialog.name.setFocus()
    QTest.qWait(30)
    dialog.scroll.verticalScrollBar().setValue(0)
    for _ in range(40):
        if dialog.verified.hasFocus():
            break
        QTest.keyClick(dialog.focusWidget(), Qt.Key.Key_Tab)
    assert dialog.verified.hasFocus()
    assert dialog.verified.isVisible()
    assert not dialog.save_button.isEnabled()
    dialog.cancel_button.click()
    assert not dialog.isVisible()


def test_long_preview_and_criterion_change_only_expand_scroll_content(dialog):
    dialog.resize(460, 500)
    dialog.show()
    QTest.qWait(30)
    footer_position = dialog.footer.pos()
    initial_range = dialog.scroll.verticalScrollBar().maximum()
    dialog.criterion.setCurrentIndex(dialog.criterion.findData("DEFECT_ITEM"))
    dialog.preview.setText("대상 범위를 확인해 주세요. " * 80)
    QTest.qWait(40)
    assert dialog.height() == 500 and dialog.footer.pos() == footer_position
    assert dialog.scroll.verticalScrollBar().maximum() > initial_range
    assert not dialog.save_button.isEnabled()
