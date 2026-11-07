"""답변 카드의 기존 버튼 순서·이동 의미를 유지하며 우측 정렬·줄바꿈한다."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtTest import QSignalSpy, QTest

from setron.ui.item_analysis import answer_items
from setron.ui.item_answer_card import ItemAnswerCard

from test_ui_items import app, data


@pytest.fixture
def card(app, data):
    value = ItemAnswerCard()
    value.show_answer(answer_items(data, 10, "다른"))
    yield value
    value.close()
    value.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_actions_remain_right_aligned_without_clipping_after_repeated_resize(card):
    for width, columns in ((1200, 3), (580, 2), (400, 1), (850, 3), (400, 1), (1200, 3)):
        card.resize(width, 900)
        card.show()
        QTest.qWait(40)
        assert card.width() == width
        assert card.action_row.columns == columns
        bounds = [control.rect().translated(control.mapTo(card.action_row, QPoint()))
                  for control in card.actions.values()]
        for control, rect in zip(card.actions.values(), bounds):
            assert card.action_row.rect().contains(rect)
            assert control.width() >= control.sizeHint().width()
        # 각 줄의 마지막 버튼을 카드 내용 우측에 맞춘다.
        for y in {rect.y() for rect in bounds}:
            rightmost = max(rect.right() for rect in bounds if rect.y() == y)
            assert abs(rightmost - card.action_row.contentsRect().right()) <= 1
        assert all((a.y(), a.x()) < (b.y(), b.x()) for a, b in zip(bounds, bounds[1:]))


def test_button_routes_and_answer_payload_are_unchanged(card):
    signals = QSignalSpy(card.navigate_requested)
    for control in card.actions.values():
        assert control.isEnabled()
        control.click()
    assert [signals.at(i)[0] for i in range(signals.count())] == ["ai", "heatmap", "dashboard"]
    assert all(signals.at(i)[1] is card.answer for i in range(signals.count()))
    assert card.actions["dashboard"].property("primary") is True
    card.show_message("조회 중", loading=True)
    assert all(not control.isEnabled() for control in card.actions.values())
