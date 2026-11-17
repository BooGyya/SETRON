"""검색해도 선택을 유지하는 공통 조회 체크리스트의 Qt 동작."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from setron.ui.query_filter_popup import QueryFilterPopup


OPTIONS = (("a", "P1", "화성"), ("b", "P2", "화성"), ("c", "<b>P3</b>", "평택"))


@pytest.fixture
def anchor():
    app = QApplication.instance() or QApplication([])
    control = QPushButton("Line")
    control.resize(140, 36)
    control.show()
    yield control
    control.close()
    control.deleteLater()
    app.processEvents()


def test_search_matches_group_and_keeps_hidden_selection(anchor):
    popup = QueryFilterPopup("Line", OPTIONS, ("a", "c"), parent=anchor)
    accepted = QSignalSpy(popup.accepted)
    popup.open()
    QApplication.processEvents()
    assert popup.search.hasFocus()
    title = next(caption for caption in popup.findChildren(QLabel) if caption.text() == "Line 선택")
    title_rect, count_rect, search_rect, all_rect, confirm_rect = (
        QRect(control.mapTo(popup, QPoint()), control.size())
        for control in (title, popup.count, popup.search, popup.all_button, popup.confirm_button)
    )
    assert title_rect.left() == search_rect.left() == all_rect.left()
    assert count_rect.right() == search_rect.right() == confirm_rect.right()
    assert title_rect.right() < count_rect.left()
    assert abs(title_rect.center().y() - count_rect.center().y()) <= 1
    assert title_rect.bottom() < search_rect.top() < all_rect.top()
    assert (all_rect.top(), all_rect.height()) == (confirm_rect.top(), confirm_rect.height())
    assert all_rect.right() < confirm_rect.left()
    popup.search.setText("평택 <B>")
    assert popup.checks["c"].isVisible() and not popup.checks["a"].isVisible()
    assert popup.checks["c"].caption.text() == "<b>P3</b>"
    assert popup.checks["a"].isChecked() and popup.count.text() == "2개 선택 중"
    popup.confirm_button.click()
    assert accepted.count() == 1 and popup.selected_keys == ("a", "c")


def test_all_toggle_and_dismissal_do_not_confirm_empty_selection(anchor):
    popup = QueryFilterPopup("Line", OPTIONS, (), parent=anchor)
    accepted, rejected = QSignalSpy(popup.accepted), QSignalSpy(popup.rejected)
    popup.open()
    assert popup.all_button.text() == "전체 해제"
    popup.all_button.click()
    assert popup.all_button.text() == "전체 선택" and not popup.confirm_button.isEnabled()
    popup.accept()
    assert accepted.count() == 0 and popup.isVisible()
    popup.all_button.click()
    popup.confirm_button.click()
    assert accepted.count() == 1 and popup.selected_keys == ()

    popup.open()
    QTest.keyClick(popup.search, Qt.Key.Key_Escape)
    assert rejected.count() == 1 and not popup.isVisible()
    popup.open()
    QTest.mouseClick(popup, Qt.MouseButton.LeftButton, pos=QPoint(-4, -4))
    assert rejected.count() == 2 and accepted.count() == 1 and not popup.isVisible()

    empty = QueryFilterPopup("Line", (), (), parent=anchor)
    assert not empty.all_button.isEnabled() and not empty.confirm_button.isEnabled()


def test_popup_anchors_below_and_stays_on_screen_near_an_edge(anchor):
    bounds = anchor.screen().availableGeometry()
    anchor.move(bounds.left() + 40, bounds.top() + 40)
    popup = QueryFilterPopup("Line", OPTIONS, (), parent=anchor)
    popup.open()
    assert popup.y() == anchor.mapToGlobal(QPoint(0, anchor.height() + 4)).y()
    assert bounds.contains(popup.geometry())
    popup.reject()

    anchor.move(bounds.right() - anchor.width(), bounds.bottom() - anchor.height() - 20)
    popup.open()
    assert bounds.contains(popup.geometry())
    assert popup.geometry().bottom() < anchor.mapToGlobal(QPoint()).y()
    popup.reject()
