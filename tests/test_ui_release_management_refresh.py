"""릴리즈 교체 후 저장 전 관리 재조회가 현재 판정을 덮어쓰지 않는다."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from threading import Event

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest

from setron.features.import_save.service import DatabaseImportSaver
from setron.features.management import ManagementService
from setron.ui.app import create_window
from setron.ui.management_data import ManagementGateway
from setron.ui.query_state import TargetN

from test_management_service import edit
from test_release_import_integration import import_request, reviewed_request
from test_release_revision import all_rows
from test_ui_async_read import wait_until
from test_ui_release_saved_pages import app, connected, ready_pages, revision_storage


def registered(snapshot):
    """조회 결과에서 같은 설비의 설치 버전 등록 여부를 읽는다."""
    return snapshot.summary.equipment[0].families[0].pms[0].comparison.registered


def management_edit(window, storage, release_id, day):
    """검토한 출시일을 실제 저장하고 앱의 관리 저장 완료 신호를 보낸다."""
    service = ManagementService(storage, window.release_refresh_context())
    result = service.save((edit(service, "release_version", {"release_version_id": release_id},
                                {"released_at": day}),))
    window.pages["management"].saved.emit(result)
    return result


@contextmanager
def pending_management_refresh(app, storage, *, late_error=False, import_after_capture=True):
    """실제 관리 저장 뒤 시작한 예전 스냅샷의 전달 시점만 제어한다."""
    release_id, source = connected(storage)
    saver = DatabaseImportSaver(storage)
    window = create_window(source, import_saver=saver, management_gateway=ManagementGateway(storage))
    original_snapshot = source.snapshot
    entered, release, finished = Event(), Event(), Event()
    captured = []
    try:
        ready_pages(window)
        window.query_panel.set_target_draft((TargetN(1, 1, release_id),))
        window.query_panel.apply_draft()
        query = window.query_state.applied
        window.query_state.set_target_draft((TargetN(1, 1, None),))

        def delayed_snapshot(requested):
            try:
                value = original_snapshot(requested)
                captured.append(value)
                entered.set()
                assert release.wait(10), "검증용 이전 관리 조회 해제 대기 초과"
                if late_error:
                    raise RuntimeError("저장 전 관리 조회 실패")
                return value
            finally:
                finished.set()

        source.snapshot = delayed_snapshot
        management_edit(window, storage, release_id, date(2026, 10, 2))
        wait_until(entered.is_set)
        assert window.management_refresh.busy and registered(captured[0])
        # 진행 중 작업은 이미 지연 함수 안에 있다. 다음 명시 조회와 관리 조회는 실제 최신 DB를 읽는다.
        source.snapshot = original_snapshot
        if import_after_capture:
            request = replace(import_request(storage, raw="v2"), refresh_context=window.release_refresh_context())
            receipt = saver.save(reviewed_request(saver, request))
            window.pages["import"].saved.emit(receipt)
        yield window, source, query, captured[0], release, finished
    finally:
        source.snapshot = original_snapshot
        release.set()
        window.management_refresh.cancel()
        window.pages["ai"].invalidate_data()
        window.close()
        assert QThreadPool.globalInstance().waitForDone(3000)
        window.deleteLater()
        app.processEvents()


def finish_old_response(app, window, release, finished):
    """새 페이지 조회와 예전 관리 응답을 끝내고 대기 중 완료 신호까지 처리한다."""
    wait_until(lambda: all(not window.pages[key].read_request.busy for key in ("versions", "items")))
    notice = window.notice.text()
    release.set()
    wait_until(finished.is_set)
    assert QThreadPool.globalInstance().waitForDone(2000)
    app.processEvents()
    QTest.qWait(20)
    return notice


@pytest.mark.parametrize("late_error", (False, True))
def test_import_save_discards_previous_management_refresh(app, revision_storage, late_error):
    """늦은 성공·실패가 화면이나 저장 안내를 바꾸지 않으며 재조회는 교체 후 등록 상태를 사용한다."""
    with pending_management_refresh(app, revision_storage, late_error=late_error) as context:
        window, source, query, old, release, finished = context
        assert window.pages["dashboard"].snapshot is None and window.pages["heatmap"].snapshot is None
        assert not window.management_refresh.busy and window.management_refresh_query is None
        notice = finish_old_response(app, window, release, finished)
        assert window.pages["dashboard"].snapshot is None and window.pages["heatmap"].snapshot is None
        assert window.notice.text() == notice
        assert window.query_state.applied is query
        assert window.query_state.applied.targets == query.targets
        assert window.query_state.target_draft == (TargetN(1, 1, None),)
        assert {row["version_raw"] for row in all_rows(revision_storage, "release_version")} == {"v2"}
        current = source.snapshot(query)
        assert current.query is query and registered(old) and not registered(current)


def test_next_management_save_refreshes_current_data_and_rejects_older_completion(app, revision_storage):
    """교체에서 취소한 관리 조회가 다음 정상 관리 재조회의 최신 결과까지 덮지 못한다."""
    with pending_management_refresh(app, revision_storage) as context:
        window, source, query, old, release, finished = context
        current_release = all_rows(revision_storage, "release_version")[0]["release_version_id"]
        management_edit(window, revision_storage, current_release, date(2026, 10, 3))
        wait_until(lambda: not window.management_refresh.busy and window.pages["dashboard"].snapshot is not None)
        current = window.pages["dashboard"].snapshot
        assert current.query is query and not registered(current)
        assert window.pages["heatmap"].snapshot is current
        notice = finish_old_response(app, window, release, finished)
        assert window.pages["dashboard"].snapshot is current and current is not old
        assert window.pages["heatmap"].snapshot is current and window.notice.text() == notice
        assert window.query_state.applied is query
        assert window.query_state.target_draft == (TargetN(1, 1, None),)


@pytest.mark.parametrize("late_error", (False, True))
def test_changed_query_discards_old_management_result_and_restores_ai_button(app, revision_storage, late_error):
    """새 조회를 적용한 뒤 옛 관리 응답은 버리고 현재 자료의 명시적 AI 요청은 다시 허용한다."""
    with pending_management_refresh(app, revision_storage, late_error=late_error,
                                    import_after_capture=False) as context:
        window, source, old_query, old, release, finished = context
        ai = window.pages["ai"]
        window.navigate("ai")
        assert window.management_refresh.busy and not ai.analyze_button.isEnabled()
        new_query = window.query_panel.apply_draft()
        current = window.pages["dashboard"].snapshot
        assert new_query is window.query_state.applied and new_query is not old_query
        assert current.query is new_query and current is not old
        assert not ai.analyze_button.isEnabled()
        notice = finish_old_response(app, window, release, finished)
        assert not window.management_refresh.busy and ai.analyze_button.isEnabled()
        assert window.pages["dashboard"].snapshot is current and window.pages["heatmap"].snapshot is current
        assert window.query_state.applied is new_query and window.notice.text() == notice
        assert ai.answer is None and ai.busy is None
