"""실제 가져오기 탭 합성 렌더에서 밑줄 겹침과 빈 안내행의 여백을 검증한다."""

from math import ceil

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.ui.app import create_window
from setron.ui.appearance_data import Appearance
from setron.ui.import_demo import DemoExcel
from test_ui_import_ver5 import own  # noqa: F401


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("font_size", ["small", "normal", "large"])
def test_import_active_underline_covers_baseline_in_composite_render(own, mode, font_size):
    window = own(create_window(excel=DemoExcel()))
    window.navigate("import")
    page = window.pages["import"]
    controller = QApplication.instance().appearance_manager
    previous = controller.appearance
    try:
        controller.apply(Appearance(mode=mode, font_size=font_size))
        for width in (960, 1600):
            window.resize(width, 900)
            QTest.qWait(20)
            for selected, other in ((0, 2), (2, 0)):
                page.tabs.setCurrentIndex(selected)
                page.tabs.setFocus()
                QTest.mouseMove(page.tabs, page.tabs.tabRect(other).center())
                image = page.tab_strip.grab().toImage()
                ratio = image.devicePixelRatio()
                origin = page.tabs.mapTo(page.tab_strip, QPoint())
                rect = page.tabs.tabRect(selected).translated(origin)
                left, right = ceil(rect.left() * ratio) + 1, int((rect.right() + 1) * ratio) - 1
                for offset in (1, 2):
                    y = int((origin.y() + page.tabs.height() - offset + .5) * ratio)
                    assert all(image.pixelColor(x, y).name() == controller.tokens["accent"]
                               for x in range(left, right))
                    other_x = int((origin.x() + page.tabs.tabRect(other).center().x()) * ratio)
                    assert image.pixelColor(other_x, y).name() == controller.tokens["line"]
                assert not page.tabs.drawBase()
                assert page.workspace.currentIndex() == selected
    finally:
        controller.apply(previous)


@pytest.mark.parametrize("width", [960, 1600])
def test_import_first_card_has_normal_gap_when_notice_is_empty(own, width):
    window = own(create_window(excel=DemoExcel()))
    window.resize(width, 900)
    window.navigate("import")
    page = window.pages["import"]
    QTest.qWait(30)

    def gap():
        return (page.step_cards[0].mapTo(window, QPoint()).y()
                - page.steps.mapTo(window, QPoint()).y() - page.steps.height())

    assert not page.notice.text() and page.notice.isHidden()
    assert 12 <= gap() <= 16
    page._set_status("확인 필요", "warning", "통합문서 상태를 확인해 주세요.")
    QTest.qWait(10)
    assert page.notice.isVisibleTo(window) and gap() > 16
    page._set_status("준비", "info", "")
    QTest.qWait(10)
    assert page.notice.isHidden() and 12 <= gap() <= 16
