"""AI 답변 하단의 조회 동작과 파일 저장 동작을 분리한다. 엔진·운영 자료는 수정하지 않는다."""
from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.chat.schemas import Intent
from setron.ui.chat_page import AnswerCard, _CHAT_STYLE
from setron.ui.theme import install_theme
from test_ui_chat import EngineService


@pytest.fixture
def own():
    app = QApplication.instance() or QApplication([])
    install_theme(app)
    widgets = []

    def add(widget, width=1100):
        widgets.append(widget)
        widget.setStyleSheet(_CHAT_STYLE)
        widget.resize(width, 650)
        widget.show()
        QTest.qWait(50)
        return widget

    yield add
    for widget in widgets:
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def search_answer():
    return EngineService().ask("평택 O2 중 목표 N 미만인 설비만 보여줘")


def pos(widget, footer):
    return widget.mapTo(footer, QPoint())


def test_wide_answer_groups_count_with_view_and_exports_at_right(own):
    card = own(AnswerCard(search_answer(), lambda _: None, on_export=lambda _: None))
    footer = card.footer
    assert not footer.stacked
    assert pos(card.result_count, footer).x() < pos(card.expand_button, footer).x() < pos(card.export_button, footer).x()
    assert abs(pos(card.export_button, footer).x() + card.export_button.width() - footer.width()) <= 1
    assert abs(pos(card.expand_button, footer).y() - pos(card.export_button, footer).y()) <= 1
    assert pos(card.export_button, footer).x() - (pos(card.expand_button, footer).x() + card.expand_button.width()) > 100


def test_narrow_answer_moves_export_to_own_right_aligned_bottom_row(own):
    card = own(AnswerCard(search_answer(), lambda _: None, on_export=lambda _: None), width=390)
    footer = card.footer
    assert footer.stacked
    assert pos(card.export_button, footer).y() >= pos(card.expand_button, footer).y() + card.expand_button.height()
    assert abs(pos(card.export_button, footer).x() + card.export_button.width() - footer.width()) <= 1
    card.resize(1100, 650)
    QTest.qWait(50)
    assert not footer.stacked


def test_only_report_action_is_right_aligned_and_buttons_keep_native_actions(own):
    calls = []
    answer = EngineService().ask("목표 N 달성률이 가장 낮은 원본 구역을 분석해줘")
    card = own(AnswerCard(answer, lambda _: None, on_report=calls.append))
    assert card.expand_button is None and card.report_button is not None
    assert abs(pos(card.report_button, card.footer).x() + card.report_button.width() - card.footer.width()) <= 1
    card.report_button.setFocus()
    QTest.keyClick(card.report_button, Qt.Key.Key_Space)
    assert calls == [card]


def test_mixed_intents_keep_both_file_actions_on_right_without_reordering_result(own):
    answer = search_answer()
    answer = replace(answer, request=replace(answer.request, intents=(Intent.SEARCH, Intent.COMPARE)))
    card = own(AnswerCard(answer, lambda _: None, on_export=lambda _: None, on_report=lambda _: None))
    assert card.footer.right_widgets == (card.export_button, card.report_button)
    assert card.footer.left_widgets == (card.result_count, card.expand_button)
    assert card.answer is answer and card.answer.equipment_ids == answer.equipment_ids
    card.mark_stale()
    assert not card.export_button.isEnabled() and not card.report_button.isEnabled()
