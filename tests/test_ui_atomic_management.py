"""한 번의 저장 요청, 실패 초안 유지, 결과 미확인 시 재저장 차단을 검증한다."""

from types import SimpleNamespace

from setron.infrastructure.database import DatabaseCommitUnknownError
from test_ui_data_management import C, FakeGateway, answer, cell, settle, sheet_page
from test_ui_import_ver5 import own, until  # noqa: F401


class AtomicGateway(FakeGateway):
    def __init__(self):
        super().__init__()
        self.requests, self.error, self.read_error = [], None, False

    def save_sheet(self, batch_id, **operations):
        self.requests.append((batch_id, operations))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(records=(), refresh=None, rows={"added": [77], "journal": None, "regroup": []})

    def data_admin(self, method, *args, **kwargs):
        if self.read_error and method == "status_sheets":
            raise RuntimeError("검증용 다시 읽기 실패")
        return super().data_admin(method, *args, **kwargs)


def test_mixed_sheet_save_dispatches_one_atomic_request_and_preserves_failed_draft(own, monkeypatch):
    answer(monkeypatch)
    gateway = AtomicGateway()
    gateway.error = ValueError("검증용 행 저장 거절")
    page, sheet = sheet_page(own, gateway)
    cell(sheet, 0, C["version_raw"]).setText("v2")
    sheet.add_row()
    new = sheet.table.rowCount() - 1
    for column, value in ((C["equipment_code"], "NEW-1"), (C["line"], "신규 Line"), (C["group"], "신규 모델"),
                          (C["category"], "POLY"), (C["production_area"], "Memory"), (C["version_raw"], "v1")):
        cell(sheet, new, column).setText(value)
    assert sheet.save()
    settle(page)
    assert len(gateway.requests) == 1
    batch, request = gateway.requests[0]
    assert batch == 7 and request["registers"] == [("신규 Line", "MEMORY")]
    assert request["creates"] == [("신규 모델", "POLY")]
    assert request["adds"] and request["changes"]
    assert not gateway.saved and not gateway.created
    assert not any(method in gateway.calls for method in ("add_line", "create_group", "apply_rows"))
    assert sheet.pending and sheet.new_rows and "행 저장 거절" in sheet.notice.text()
    assert not page.uncertain and sheet.save_button.isEnabled()
    gateway.error = None
    assert sheet.save()
    settle(page)
    assert len(gateway.requests) == 2 and not sheet.pending


def test_uncertain_commit_blocks_retry_until_successful_explicit_reread(own):
    gateway = AtomicGateway()
    gateway.error = DatabaseCommitUnknownError("검증용 commit 응답 끊김")
    page, sheet = sheet_page(own, gateway)
    cell(sheet, 0, C["version_raw"]).setText("v2")
    uncertain, reconciled, failed = [], [], []
    page.save_uncertain.connect(lambda: uncertain.append(True))
    page.save_reconciled.connect(lambda: reconciled.append(True))
    page.save_failed.connect(lambda: failed.append(True))
    assert sheet.save()
    settle(page)
    assert page.uncertain and uncertain and not failed
    assert page.recovery_banner.isVisible() and not sheet.isEnabled()
    assert not sheet.save() and len(gateway.requests) == 1
    before_reads = list(gateway.calls)
    page.invalidate()
    page.enter()
    assert gateway.calls == before_reads and sheet.pending
    gateway.read_error = True
    assert page.reconcile_save()
    until(lambda: not page._recovering)
    assert page.uncertain and not reconciled and not sheet.isEnabled()
    gateway.read_error = False
    assert page.reconcile_save()
    until(lambda: not page._recovering)
    settle(page)
    assert not page.uncertain and reconciled and sheet.isEnabled()
    assert not sheet.pending and not sheet.steps and not sheet.redo_steps
    assert len(gateway.requests) == 1  # 다시 읽기는 원래 저장을 재시도하지 않는다.


def test_recovery_list_success_cannot_override_later_row_read_failure(own):
    gateway = AtomicGateway()
    page, sheet = sheet_page(own, gateway)
    page.uncertain, page._uncertain_owner, page._recovering = True, sheet, True
    page._sync_saving()
    page._recovery_read_done(sheet, None)
    page._recovery_read_done(sheet, RuntimeError("두 번째 행 읽기 실패"))
    until(lambda: not page._recovering)
    assert page.uncertain and not sheet.isEnabled()
    assert "잠금을 유지" in page.recovery_notice.text()
