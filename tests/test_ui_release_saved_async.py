"""저장 후 이전 비동기 응답이 새 릴리즈·Item 목록을 덮어쓰지 않는다."""

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest

from setron.ui.item_data import ItemManagerData
from setron.ui.version_data import VersionCatalog
from test_ui_async_read import SlowRead, app, context, wait_until


@pytest.mark.parametrize("late_error", (False, True))
def test_save_refresh_discards_previous_background_result(context, late_error):
    """진행 중이던 성공·실패 응답은 저장 후 새 조회의 세대를 통과하지 못한다."""
    create, result, old_value, _ = context
    old = SlowRead(RuntimeError("저장 전 조회 실패") if late_error else old_value)
    page = create(old)
    wait_until(old.started.is_set)
    empty = VersionCatalog((), ())
    current = empty if isinstance(old_value, VersionCatalog) else ItemManagerData(empty)
    page.loader = lambda: current
    page.refresh_after_save()
    assert not page.read_request.busy and result(page) is current
    status = page.status.text()
    old.release.set()
    assert QThreadPool.globalInstance().waitForDone(2000)
    QTest.qWait(20)
    assert result(page) is current and page.status.text() == status
