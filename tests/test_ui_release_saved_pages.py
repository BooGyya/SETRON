"""실제 교체 저장 후 릴리즈·Item 화면과 미확인 위험 안내를 갱신한다."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QThreadPool
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from setron.features.import_save.service import DatabaseImportSaver
from setron.features.release.revision import ReleaseRevisionService
from setron.features.release.schemas import ItemInput
from setron.ui.app import create_window
from setron.ui.query_state import TargetN

from test_release_import_integration import import_request, reviewed_request
import test_release_revision as revision_cases
from test_release_revision import all_rows
from test_ui_async_read import wait_until
from test_ui_release_refresh import app, connected


@pytest.fixture
def revision_storage(monkeypatch, tmp_path):
    """실제 DB 로더의 워커도 같은 자료를 읽는 격리한 파일 DB를 준비한다."""
    def file_engine(_):
        return create_engine(URL.create("sqlite", database=str(tmp_path / "saved_pages.sqlite")))

    monkeypatch.setattr(revision_cases, "create_engine", file_engine)
    yield from revision_cases.revision_storage.__wrapped__()


def ready_pages(window):
    """처음 숨김으로 취소된 조회는 다시 시작하고 두 화면의 완료를 기다린다."""
    for page, value in ((window.pages["versions"], "catalog"), (window.pages["items"], "data")):
        if getattr(page, value) is None and not page.read_request.busy:
            page.reload()
    wait_until(lambda: all(not window.pages[key].read_request.busy for key in ("versions", "items")))
    assert QThreadPool.globalInstance().waitForDone(2000)


def shown_versions(window):
    """서로 다른 두 화면이 현재 표시하는 릴리즈 원문을 반환한다."""
    versions, items = window.pages["versions"], window.pages["items"]
    return ({entry.release.version_raw for entry in versions.catalog.releases},
            {entry.release.version_raw for entry in items.data.versions.releases})


def test_actual_save_refreshes_both_pages_and_reports_unresolved_risk(app, revision_storage):
    """실제 교체 뒤 제거된 버전·상세를 비우고 N을 유지하며 수동 위험 확인을 안내한다."""
    storage = revision_storage
    selected, source = connected(storage)
    ReleaseRevisionService(storage).register_item(selected, ItemInput(item_no=7, content="기존 내용"))
    with storage.transaction() as tx:
        tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1,
            "release_version_id": selected, "risk_reason": "기존 버전 위험", "target_reference": {},
            "mapping_status": "VERIFIED"})
    saver = DatabaseImportSaver(storage)
    window = create_window(source, import_saver=saver)
    try:
        ready_pages(window)
        assert shown_versions(window) == ({"v1"}, {"v1"})
        window.pages["versions"].open_row(0)
        window.pages["items"].open_row(0)
        window.query_panel.set_target_draft((TargetN(1, 1, selected),))
        window.query_panel.apply_draft()
        window.query_state.set_target_draft((TargetN(1, 1, None),))
        request = replace(import_request(storage, raw="v2"), refresh_context=window.release_refresh_context())
        receipt = saver.save(reviewed_request(saver, request))
        window.pages["import"].saved.emit(receipt)
        ready_pages(window)
        assert shown_versions(window) == ({"v2"}, {"v2"})
        for page in (window.pages["versions"], window.pages["items"]):
            assert page.mode == "list" and page.selected_id is None
        assert window.query_state.applied.targets == (TargetN(1, 1, selected),)
        assert window.query_state.target_draft == (TargetN(1, 1, None),)
        assert receipt.refresh.unresolved_risk_ids
        message = window.pages["import"].toast.text.text()   # 저장 뒤 확인할 점은 토스트로 알린다
        assert "수동 위험 지정 1건" in message and "미확인" in message and "확인해 주세요" in message
        assert all_rows(storage, "risk_rule")[0]["mapping_status"] == "UNRESOLVED"
    finally:
        window.close()
        assert QThreadPool.globalInstance().waitForDone(2000)
        window.deleteLater()
        app.processEvents()


def test_failed_page_refresh_clears_old_data_and_retry_reads_current_db(app, revision_storage):
    """저장은 성공했어도 후속 조회가 실패하면 과거 목록 대신 실패를 표시하고 재시도한다."""
    storage = revision_storage
    _, source = connected(storage)
    saver = DatabaseImportSaver(storage)
    window = create_window(source, import_saver=saver)
    original_loader = source._loader
    try:
        ready_pages(window)
        assert shown_versions(window) == ({"v1"}, {"v1"})
        receipt = saver.save(reviewed_request(saver, import_request(storage, raw="v2")))

        def unavailable():
            raise RuntimeError("후속 DB 조회 실패")

        source._loader = unavailable
        window.pages["import"].saved.emit(receipt)
        ready_pages(window)
        assert window.pages["versions"].catalog is None and window.pages["items"].data is None
        assert window.pages["versions"].list_model.rowCount() == 0
        assert not window.pages["items"].result_buttons
        for page in (window.pages["versions"], window.pages["items"]):
            assert "실패" in page.status.text()
        assert {row["version_raw"] for row in all_rows(storage, "release_version")} == {"v2"}
        source._loader = original_loader
        window.pages["versions"].reload()
        window.pages["items"].reload()
        ready_pages(window)
        assert shown_versions(window) == ({"v2"}, {"v2"})
    finally:
        source._loader = original_loader
        window.close()
        assert QThreadPool.globalInstance().waitForDone(2000)
        window.deleteLater()
        app.processEvents()
