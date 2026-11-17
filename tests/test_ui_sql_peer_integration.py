"""SQL 비교 화면에서 실제 SQLite 서비스로 선택·병합·변경 이력 저장을 확인한다."""

from pathlib import Path
from time import monotonic, sleep

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QFileDialog

from setron.features.db_import.service import SqlImportService
from setron.features.peer_sync import read_state
from setron.infrastructure.database import Database
from setron.ui.import_dialogs import ImportConfirmDialog
from setron.ui.sql_import_page import SqlImportPage
from test_sql_import_selection import with_process
from test_sql_import_service import backup, bundle, current, seed, target  # noqa: F401
from test_ui_import_ver5 import own  # noqa: F401


PEER = "dcb913c9-4e12-40dc-953c-128b4cc9351f"


def until(predicate):
    deadline = monotonic() + 30
    while not predicate() and monotonic() < deadline:
        QTest.qWait(5)
        sleep(0.01)  # SQLite 반사 조회를 실행하는 Python 워커에도 실행 시간을 준다.
    assert predicate()


def _page(own, target, tmp_path, monkeypatch):
    seed(target, with_process(bundle(line="현재 PC 라인"), "현재 PC 공정"))
    filename = backup(tmp_path, with_process(bundle(line="다른 PC 라인", day=2), "가져온 공정"))
    service = SqlImportService(lambda: Database(target[0]), peer_id=PEER)
    page = own(SqlImportPage(service))
    page.resize(1100, 850)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(filename), "SQL 파일 (*.sql)"))
    page.select_button.click()
    page.preview_button.click()
    until(lambda: not page.busy)
    assert page.path == Path(filename) and page.plan is not None
    assert {difference.field for difference in page.plan.differences} == {"line", "process_name"}
    assert not page.apply_button.isEnabled()
    return page


def _choose(page, choices):
    for index, difference in enumerate(page.comparison.model.differences):
        assert page.comparison.model.setData(page.comparison.model.index(index, 4), choices[difference.field],
                                            Qt.ItemDataRole.EditRole)
    assert page.comparison.review_button.isEnabled() and not page.apply_button.isEnabled()


def test_sql_page_real_service_merges_selected_fields_and_creates_peer_history(own, target, tmp_path, monkeypatch):
    """현재 라인은 유지하고 가져온 공정만 적용한 결과와 동일 트랜잭션의 교환 이력을 확인한다."""
    page = _page(own, target, tmp_path, monkeypatch)
    _choose(page, {"line": "CURRENT", "process_name": "INCOMING"})
    page.comparison.review_button.click()
    until(lambda: not page.busy)
    assert page.plan.unresolved == 0 and page.apply_button.isEnabled()
    assert not read_state(current(target)).events

    saved = []
    page.saved.connect(saved.append)
    monkeypatch.setattr(ImportConfirmDialog, "exec", lambda _: QDialog.DialogCode.Accepted)
    page.apply_button.click()
    until(lambda: not page.busy)

    assert saved and page.status.text() == "가져오기 완료"
    stored = current(target)
    snapshot, = stored["equipment_snapshot"]
    assert (snapshot["line"], snapshot["process_name"]) == ("현재 PC 라인", "가져온 공정")
    state = read_state(stored)
    assert state.entities and state.events
    assert {event["peer"] for event in state.events.values()} == {PEER}
    assert any(event["operation"] == "resolve" for event in state.events.values())
    assert page.plan is None and not page.apply_button.isEnabled()


def test_sql_page_real_service_rejects_stale_comparison_and_never_applies(own, target, tmp_path, monkeypatch):
    """검토 이후 다른 창에서 변경하면 재검토가 실패하고 기존 자료·이력을 덮어쓰지 않는다."""
    page = _page(own, target, tmp_path, monkeypatch)
    _choose(page, {"line": "INCOMING", "process_name": "INCOMING"})
    with target[0].begin() as connection:
        connection.execute(target[1]["equipment_snapshot"].update().values(line="다른 창에서 변경"))
    before = current(target)
    page.comparison.review_button.click()
    until(lambda: not page.busy)

    assert page.status.text() == "선택 검토 실패"
    assert "미리보기 이후" in page.details.text()
    assert not page.apply_button.isEnabled()
    page._confirm_apply()
    assert current(target) == before
    assert not read_state(current(target)).events
