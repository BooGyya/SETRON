"""데이터를 넣거나 지운 뒤 [조회]를 다시 누르지 않아도 대시보드 · 히트맵이 바뀐 현황을 보인다 (11-17 요청).

가져오기 저장 · 저장 자료 삭제 · SQL 저장이 확정되면 선택지를 다시 읽고, 그 전에 적용돼 있던 같은 조회 조건이
새 선택지에서도 유효하면 바로 다시 조회한다. 사용자가 바꿔 두고 아직 적용하지 않은 선택은 건드리지 않는다.
"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest

from setron.ui.query_state import QuerySelection

from test_management_app_integration import app, file_storage, running_window, wait_for  # noqa: F401
from test_release_revision import revision_storage  # noqa: F401
from test_management_service import observe


def emit_sql_saved(window, app):
    window.navigate("import")
    page = window.pages["import"]
    wait_for(app, lambda: page is not None)
    page.sql_page.saved.emit(object())


def test_sql_save_requeries_the_same_query_without_pressing_apply(app, file_storage):
    with running_window(app, file_storage) as (window, source, calls, release_id, item_id, snapshot):
        dashboard = window.pages["dashboard"]
        before = window.query_state.applied
        assert dashboard.snapshot.summary.equipment_count == 1
        observe(file_storage, release_id, code="EQ-2")             # 다른 경로(SQL 넣기)로 들어온 설비
        emit_sql_saved(window, app)
        wait_for(app, lambda: window.query_state.applied is not before and dashboard.snapshot is not None, timeout=10)
        applied = window.query_state.applied
        assert applied.selection == before.selection and applied.targets == before.targets
        assert window.query_state.applied_is_current and not window.query_state.has_pending_changes
        assert dashboard.snapshot.summary.equipment_count == 2
        assert "같은 조회 조건으로 다시 조회했습니다" in window.notice.text()


def test_unapplied_draft_is_left_for_the_user(app, file_storage):
    with running_window(app, file_storage) as (window, source, calls, release_id, item_id, snapshot):
        dashboard = window.pages["dashboard"]
        before = window.query_state.applied
        window.query_panel.set_draft(QuerySelection(site_key="hwaseong"))  # 바꿔 두고 아직 [조회]를 누르지 않음
        observe(file_storage, release_id, code="EQ-2")
        emit_sql_saved(window, app)
        wait_for(app, lambda: not window.management_progress.isVisible(), timeout=10)   # 선택지 다시 읽기가 끝남
        QTest.qWait(300)
        assert window.query_state.applied is before and dashboard.snapshot is None
        assert window.query_state.draft == QuerySelection(site_key="hwaseong")
        assert "조회 조건을 다시 선택해 주세요" in window.notice.text()
