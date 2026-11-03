"""실제 릴리즈 교체 저장 후 이전 AI 조회·분석은 폐기하고 새 요청만 허용한다."""

from contextlib import contextmanager
from dataclasses import replace
from threading import Event

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest

from setron.features.import_save.service import DatabaseImportSaver
from setron.ui.ai_data import FreshAnalysisData, facts_from_snapshot
from setron.ui.app import create_window
from setron.ui.query_state import TargetN
from setron.ui.read_worker import BackgroundRead

from test_release_import_integration import import_request, reviewed_request
from test_release_revision import all_rows
from test_ui_async_read import wait_until
from test_ui_management_ai import answer
from test_ui_release_saved_pages import app, connected, ready_pages, revision_storage


@contextmanager
def saved_ai_window(app, storage):
    """실제 조회·저장과 호출 기록만 남기는 AI 공급자를 연결한다."""
    selected, source = connected(storage)
    saver, calls = DatabaseImportSaver(storage), []

    def analyze(question, facts):
        calls.append(facts)
        return answer()

    window = create_window(source, import_saver=saver, ai_analyzer=analyze)
    try:
        ready_pages(window)
        window.query_panel.set_target_draft((TargetN(1, 1, selected),))
        window.query_panel.apply_draft()
        assert window.pages["dashboard"].snapshot.summary.compliant == 1
        yield window, saver, calls
    finally:
        window.pages["ai"].invalidate_data()
        window.close()
        assert QThreadPool.globalInstance().waitForDone(3000)
        window.deleteLater()
        app.processEvents()


def replace_note(window, saver, storage):
    """현재 선택 N을 포함한 전체 노트를 저장하고 실제 저장 완료 신호를 전달한다."""
    request = replace(import_request(storage, raw="v2"), refresh_context=window.release_refresh_context())
    receipt = saver.save(reviewed_request(saver, request))
    window.pages["import"].saved.emit(receipt)
    assert {row["version_raw"] for row in all_rows(storage, "release_version")} == {"v2"}
    return receipt


@pytest.mark.parametrize("late_error", (False, True))
def test_import_save_discards_late_ai_facts_without_restoring_old_snapshot(app, revision_storage, late_error):
    """저장 전 읽던 자료의 성공·실패 응답이 옛 화면을 복원하거나 AI를 호출하지 않는다."""
    entered, release, finished = Event(), Event(), Event()
    with saved_ai_window(app, revision_storage) as (window, saver, calls):
        ai = window.pages["ai"]
        old = window.pages["dashboard"].snapshot

        def delayed_facts():
            try:
                entered.set()
                assert release.wait(5), "검증용 이전 조회 해제 대기 초과"
                if late_error:
                    raise RuntimeError("저장 전 자료 조회 실패")
                return FreshAnalysisData(facts_from_snapshot(old), old)
            finally:
                finished.set()

        ai.fresh_loader = lambda: BackgroundRead(delayed_facts)
        try:
            assert ai.analyze()
            wait_until(entered.is_set)
            replace_note(window, saver, revision_storage)
            assert window.pages["dashboard"].snapshot is None
            assert ai.busy is None and ai.analysis_ticket is None
            assert not ai.fresh_request.busy
            release.set()
            wait_until(finished.is_set)
            assert QThreadPool.globalInstance().waitForDone(2000)
            app.processEvents()
            QTest.qWait(20)
            assert calls == [] and ai.answer is None
            assert window.pages["dashboard"].snapshot is None
            assert window.pages["heatmap"].snapshot is None
            assert "자료 변경" in ai.analysis_badge.text()
        finally:
            release.set()


def test_import_save_cancels_queued_ai_analysis_after_facts_were_accepted(app, revision_storage):
    """자료 조회를 마쳤더라도 저장 전에 대기하던 분석 타이머는 실행하지 않는다."""
    with saved_ai_window(app, revision_storage) as (window, saver, calls):
        ai = window.pages["ai"]
        old = window.pages["dashboard"].snapshot
        ai.fresh_loader = lambda: lambda: FreshAnalysisData(facts_from_snapshot(old), old)
        assert ai.analyze() and ai.busy == "analysis" and calls == []
        replace_note(window, saver, revision_storage)
        ready_pages(window)
        app.processEvents()
        assert calls == [] and ai.answer is None and ai.busy is None
        assert window.pages["dashboard"].snapshot is None


def test_save_inside_fresh_data_signal_does_not_reschedule_previous_analysis(app, revision_storage):
    """자료 전달 슬롯에서 저장해 세대가 바뀌면 같은 조회 조건이어도 이전 분석을 재예약하지 않는다."""
    with saved_ai_window(app, revision_storage) as (window, saver, calls):
        ai = window.pages["ai"]
        old = window.pages["dashboard"].snapshot
        query = window.query_state.applied
        ai.fresh_loader = lambda: lambda: FreshAnalysisData(facts_from_snapshot(old), old)
        saved = []

        def save_during_delivery(data):
            saved.append(replace_note(window, saver, revision_storage))

        ai.fresh_data_ready.connect(save_during_delivery)
        assert ai.analyze()
        ready_pages(window)
        app.processEvents()
        assert len(saved) == 1 and window.query_state.applied is query
        assert calls == [] and ai.answer is None and ai.busy is None
        assert window.pages["dashboard"].snapshot is None


def test_import_save_clears_answer_and_explicit_new_request_uses_current_judgments(app, revision_storage):
    """기존 답변은 지우며 사용자가 다시 요청해야 교체 후 현재 판정으로 분석한다."""
    with saved_ai_window(app, revision_storage) as (window, saver, calls):
        ai = window.pages["ai"]
        query = window.query_state.applied
        assert ai.analyze()
        wait_until(lambda: ai.answer is not None)
        assert len(calls) == 1 and calls[0]["N 이상 건수"] == 1
        replace_note(window, saver, revision_storage)
        ready_pages(window)
        assert ai.answer is None and ai.answer_label.text() == ""
        assert len(calls) == 1 and window.pages["dashboard"].snapshot is None
        assert window.query_state.applied is query
        assert ai.analyze()
        wait_until(lambda: ai.answer is not None)
        assert len(calls) == 2
        assert calls[1]["N 이상 건수"] == 0 and calls[1]["판단 불가 건수"] == 1
        current = window.pages["dashboard"].snapshot
        assert current.query is query and current.summary.compliant == 0
