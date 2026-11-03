"""관리 저장 뒤 최신 AI 자료 조회와 비동기 요청 무효화를 검증한다. DB·실제 AI는 호출하지 않는다."""

import os
from threading import Event
from time import monotonic
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.ai.schemas import AIResult, AIStatus, AITask, AnalysisAnswer
from setron.infrastructure.ai_client import ResponseSource
from setron.ui.ai_data import FreshAnalysisData
from setron.ui.ai_page import AIPage
from setron.ui.query_state import QueryState, preview_catalog
from setron.ui.read_worker import BackgroundRead


def answer():
    """검증용 분석 응답. 회사 서버나 예제 클라이언트를 호출하지 않는다."""
    return AIResult(AITask.ANALYSIS, AIStatus.OK, "ok",
                    AnalysisAnswer("최신 수치 분석", ("검증용 근거",), (), ()), ResponseSource.REAL)


def facts(query, count):
    """관측 공급자가 넘기는 최소 분석 자료를 구성한다."""
    return {"조회 조건": query.description, "적용 번호": query.revision, "기준일": "2026-11-03", "설비 수": count}


def wait_for(app, condition, *, timeout=2):
    """짧은 이벤트 처리로 해당 테스트 요청만 완료될 때까지 기다린다."""
    deadline = monotonic() + timeout
    while not condition() and monotonic() < deadline:
        app.processEvents()
        QTest.qWait(1)
    assert condition(), "검증용 비동기 조회가 제한 시간 내 완료되지 않았습니다."


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_page(app):
    """실제 조회 상태·Qt 위젯과 독립된 모의 공급자를 연결한다."""
    pages = []

    def create(*, fresh_factory=None, analyzer=None):
        state = QueryState(preview_catalog())
        query = state.apply()
        calls, order = [], []

        def analyze(question, supplied):
            calls.append(supplied)
            order.append("analysis")
            return analyzer(state, question, supplied) if analyzer is not None else answer()

        page = AIPage(state, None, analyze, lambda selection: state.apply(), lambda: facts(query, 0),
                      fresh_loader=(lambda: fresh_factory(state)) if fresh_factory is not None else None)
        page.fresh_data_ready.connect(lambda data: order.append("fresh"))
        pages.append(page)
        return page, state, query, calls, order

    yield create
    for page in pages:
        page.invalidate_data()
        page.close()
        page.deleteLater()
    app.processEvents()


def test_fresh_loader_uses_latest_facts_and_updates_snapshot_before_analyzer(app, make_page):
    """캐시의 0대 대신 요청 시 읽은 최신 수치와 스냅샷을 먼저 전달한다."""
    latest = {"count": 17}
    snapshots = []

    def factory(state):
        query = state.applied
        return lambda: FreshAnalysisData(facts(query, latest["count"]), SimpleNamespace(query=query))

    page, state, query, calls, order = make_page(fresh_factory=factory)
    page.fresh_data_ready.connect(lambda data: snapshots.append(data.snapshot))
    assert page.analyze()
    assert order == ["fresh"]
    assert calls == []
    wait_for(app, lambda: page.busy is None)
    assert calls[0]["설비 수"] == 17
    assert snapshots[0].query is query is state.applied
    assert order == ["fresh", "analysis"]
    assert page.answer is not None


def test_query_changed_while_background_facts_loading_does_not_call_analyzer(app, make_page):
    """자료를 읽는 동안 적용 조건이 바뀌면 이전 요청의 응답을 버린다."""
    entered, release = Event(), Event()

    def factory(state):
        query = state.applied
        def load():
            entered.set()
            assert release.wait(2), "검증용 읽기 해제 대기 초과"
            return FreshAnalysisData(facts(query, 17), SimpleNamespace(query=query))
        return BackgroundRead(load)

    page, state, _, calls, order = make_page(fresh_factory=factory)
    try:
        assert page.analyze()
        wait_for(app, entered.is_set)
        state.apply()
        release.set()
        wait_for(app, lambda: not page.fresh_request.busy)
        assert calls == [] and order == []
        assert page.answer is None
        assert "조회 조건 변경" in page.status.text()
    finally:
        release.set()


def test_query_changed_after_fresh_signal_prevents_queued_analysis(app, make_page):
    """자료 전달 신호의 연결 함수가 조회를 바꾸면 분석 타이머를 시작하지 않는다."""
    page, state, _, calls, _ = make_page(fresh_factory=lambda state: lambda: FreshAnalysisData(
        facts(state.applied, 17), SimpleNamespace(query=state.applied)))
    page.fresh_data_ready.connect(lambda data: state.apply())
    assert page.analyze()
    app.processEvents()
    assert calls == []
    assert page.answer is None and page.busy is None
    assert "조회 조건 변경" in page.status.text()


def test_query_changed_before_zero_timer_prevents_old_facts_call(app, make_page):
    """최신 자료 검사를 마쳤어도 분석 타이머 실행 전에 조건이 바뀌면 호출하지 않는다."""
    page, state, _, calls, order = make_page(fresh_factory=lambda state: lambda: FreshAnalysisData(
        facts(state.applied, 17), SimpleNamespace(query=state.applied)))
    assert page.analyze()
    assert page.busy == "analysis" and order == ["fresh"]
    state.apply()
    wait_for(app, lambda: page.busy is None)
    assert calls == [] and page.answer is None
    assert "조회 조건 변경" in page.status.text()


def test_invalidation_during_pending_facts_discards_response_and_never_runs_ai(app, make_page):
    """저장 시작으로 무효화한 읽기가 뒤늦게 끝나도 AI를 자동 실행하지 않는다."""
    entered, release, finished = Event(), Event(), Event()

    def factory(state):
        query = state.applied
        def load():
            try:
                entered.set()
                assert release.wait(2), "검증용 읽기 해제 대기 초과"
                return FreshAnalysisData(facts(query, 17), SimpleNamespace(query=query))
            finally:
                finished.set()
        return BackgroundRead(load)

    page, _, _, calls, order = make_page(fresh_factory=factory)
    try:
        assert page.analyze()
        wait_for(app, entered.is_set)
        page.invalidate_data()
        release.set()
        wait_for(app, finished.is_set)
        app.processEvents()
        QTest.qWait(2)
        app.processEvents()
        assert calls == [] and order == []
        assert page.answer is None and page.analysis_ticket is None
        assert page.busy is None
        assert "자료 변경" in page.analysis_badge.text()
    finally:
        release.set()


def test_fresh_read_error_never_falls_back_to_cached_facts(app, make_page):
    """이전 캐시가 있어도 DB 최신 조회가 실패하면 분석을 진행하지 않는다."""
    def factory(state):
        def load():
            raise RuntimeError("검증용 조회 실패")
        return load
    page, _, _, calls, order = make_page(fresh_factory=factory)
    assert page.facts_provider()["설비 수"] == 0
    assert page.analyze()
    app.processEvents()
    assert calls == [] and order == []
    assert page.answer is None and page.busy is None
    assert "최신 자료 조회 실패" in page.status.text()


def test_query_changed_inside_analyzer_does_not_display_obsolete_answer(app, make_page):
    """응답 처리 전에 적용 조건이 달라진 경우 이미 호출한 응답도 화면에 채택하지 않는다."""
    def analyzer(state, question, supplied):
        state.apply()
        return answer()

    page, _, _, calls, _ = make_page(fresh_factory=lambda state: lambda: FreshAnalysisData(
        facts(state.applied, 17), SimpleNamespace(query=state.applied)), analyzer=analyzer)
    assert page.analyze()
    wait_for(app, lambda: page.busy is None)
    assert len(calls) == 1
    assert page.answer is None and page.answer_label.text() == ""
    assert "자료 변경" in page.status.text()
