"""마우스 휠로 조회 조건 콤보나 대시보드 보기 탭이 바뀌지 않고, 휠은 화면 스크롤로 넘어간다."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.ui.app import create_window
from setron.ui.theme import install_theme


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    install_theme(app)
    result = create_window()
    result.resize(960, 600)
    result.show()
    QTest.qWait(20)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def wheel(widget, delta=-120):
    center = QPointF(widget.rect().center())
    event = QWheelEvent(center, QPointF(widget.mapToGlobal(QPoint(int(center.x()), int(center.y())))),
                        QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def test_wheel_does_not_change_query_combo(window):
    for combo in window.query_panel.combos.values():
        if combo.count() > 1 and combo.isEnabled():
            before = combo.currentIndex()
            wheel(combo)
            wheel(combo, 120)
            assert combo.currentIndex() == before


def test_wheel_does_not_change_dashboard_tab_and_scrolls_page(window):
    window.navigate("dashboard")
    window.query_panel.apply_draft()
    QTest.qWait(30)
    tabs = window.pages["dashboard"].tabs
    bar = window.scroll.verticalScrollBar()
    assert bar.maximum() > 0
    before = tabs.currentIndex()
    wheel(tabs)
    QTest.qWait(10)
    assert tabs.currentIndex() == before
    assert bar.value() > 0
