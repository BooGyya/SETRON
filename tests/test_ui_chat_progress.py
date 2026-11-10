"""AI 분석 대기 카드: 실제로 끝난 단계만 ✓ (11/11). 진행을 알려 주지 않는 서비스는 지금처럼 그대로."""

import threading

from PySide6.QtTest import QTest

from setron.features.chat import answer, recognize
from setron.ui.chat_page import ChatPage
from test_chat_phrasing import Service
from test_chat_questions import CATALOG, DATA
from test_ui_chat_ver5 import ui  # noqa: F401  (fixture)


class SteppedService:
    """단계마다 멈춰 서서 화면이 실제 알림대로 움직이는지 본다."""

    reports_progress = True

    def __init__(self):
        self.gates = [threading.Event(), threading.Event()]

    def ask(self, question, previous=None, progress=None):
        progress(2)
        self.gates[0].wait(5)
        progress(4)
        self.gates[1].wait(5)
        return answer(recognize(question, CATALOG, previous=previous), DATA)


def _wait(predicate, ms=2000):
    for _ in range(ms // 20):
        if predicate():
            return True
        QTest.qWait(20)
    return predicate()


def test_pending_card_follows_real_steps(ui):
    service = SteppedService()
    page = ui(ChatPage(service))
    page.send("평택 O2 현황 보여줘")
    steps = page.pending.steps
    assert _wait(lambda: steps[1].text().startswith("✓"))
    assert [s.text()[0] for s in steps] == ["✓", "✓", "◐", "○", "○"] and page.pending.note.text() == "2/5단계 완료"
    service.gates[0].set()
    assert _wait(lambda: steps[3].text().startswith("✓"))
    assert steps[4].text() == "◐ 답변 작성 중"
    service.gates[1].set()
    assert _wait(lambda: page.pending is None and len(page.cards) == 1)


def test_stopped_request_ignores_late_progress(ui):
    service = SteppedService()
    page = ui(ChatPage(service))
    page.send("평택 O2 현황 보여줘")
    card = page.pending
    assert _wait(lambda: card.steps[1].text().startswith("✓"))
    page.stop()
    service.gates[0].set()
    service.gates[1].set()
    QTest.qWait(200)
    assert not card.steps[3].text().startswith("✓") and not page.cards        # 중지 뒤 알림·답은 버림


def test_chat_service_reports_two_then_four():
    seen = []
    result = Service(None).ask("뭐부터 손봐야 돼?", progress=seen.append)
    assert seen == [2, 4] and "업그레이드가 가장 급한 설비는" in result.summary
