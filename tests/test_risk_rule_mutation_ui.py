"""선택한 위험 기준의 수정·삭제, 비동기 수명과 공통 저장 후 갱신을 검증한다."""

from copy import deepcopy
from dataclasses import replace
from threading import Event, get_ident
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QDialog

from setron.features.management import ManagedRecord, ManagementResult
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.risk.item_scope import scope_fields
from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseSaveError
from setron.ui.app import create_window
from setron.ui.import_dialogs import ImportConfirmDialog
from setron.ui.item_risk_dialog import ItemRiskDialog
from setron.ui.item_risk_page import ItemRiskPage
from setron.ui.item_risk_verdicts import ItemRuleVerdictDialog
from setron.ui.read_worker import BackgroundRead
from setron.ui.risk_rule_save import RiskRuleSaveController

from test_ui_items import app, data  # noqa: F401
from test_ui_management import wait_for


class MutationGateway:
    def __init__(self, data):
        self.base = data
        self.gets, self.creates, self.updates, self.deletes, self.previews = [], [], [], [], []
        self.read_gate, self.save_gate, self.read_finished = Event(), Event(), Event()
        self.read_gate.set()
        self.save_gate.set()
        self.read_error = self.save_error = None
        self.rows = {}
        for rule_id, name, kind, release in ((9, "첫 기준", "DIRECT_VERSION", 1),
                                             (10, "둘째 기준", "VERSION_BELOW", 2)):
            current = dict(risk_rule_id=rule_id, rule_type=kind, family_id=10, item_id=1, group_id=None,
                           release_version_id=release, start_release_version_id=None, end_release_version_id=None,
                           risk_reason=name, is_enabled=True, mapping_status="VERIFIED",
                           target_reference={"retained": {"source": "원본 근거"}, "item_manager": {
                               "schema": 1, "name": name, "category": "결함 버전", "memo": "기존 메모",
                               "model_ids": [2], "site": "Site A", "line": None}})
            self.rows[rule_id] = ManagedRecord("risk_rule", {"risk_rule_id": rule_id}, current, (), (), str(rule_id) * 32)

    def get(self, table, key):
        self.gets.append((table, deepcopy(key), get_ident()))
        try:
            assert self.read_gate.wait(3)
            if self.read_error:
                raise self.read_error
            return deepcopy(self.rows[key["risk_rule_id"]])
        finally:
            self.read_finished.set()

    def preview_risk_rule(self, values, context):
        self.previews.append(deepcopy(values))
        return SimpleNamespace(problems=(), base_date=self.base.base_date, saveable=True,
                               equipment=(), count=lambda status: 0)

    def create_risk_rule(self, values, context):
        self.creates.append(deepcopy(values))
        return ManagementResult((), None, (), None, 1)

    def update_risk_rule(self, record, values, context):
        self.updates.append((deepcopy(record), deepcopy(values), deepcopy(context), get_ident()))
        assert self.save_gate.wait(3)
        if self.save_error:
            raise self.save_error
        changes = deepcopy(values)
        reference = deepcopy(record.current["target_reference"])
        if "item_manager" in changes:
            reference["item_manager"] = changes.pop("item_manager")
        updated = replace(record, current={**record.current, **changes, "target_reference": reference})
        self.rows[record.key["risk_rule_id"]] = updated
        return ManagementResult((updated,), None, (), None, 2)

    def delete_risk_rule(self, record, context):
        self.deletes.append((deepcopy(record), deepcopy(context), get_ident()))
        assert self.save_gate.wait(3)
        if self.save_error:
            raise self.save_error
        self.rows.pop(record.key["risk_rule_id"])
        return ManagementResult((), None, (), None, 3)

    def data(self):
        rules = []
        for record in self.rows.values():
            row = record.current
            rules.append(RiskRule(
                row["risk_rule_id"], RuleType(row["rule_type"]), row["family_id"], row["risk_reason"],
                group_id=row["group_id"], item_id=row["item_id"], release_version_id=row["release_version_id"],
                start_release_version_id=row["start_release_version_id"], end_release_version_id=row["end_release_version_id"],
                is_enabled=row["is_enabled"], mapping_status=MappingStatus(row["mapping_status"]),
                **scope_fields(row["target_reference"])))
        return replace(self.base, risk_rules=tuple(rules))


@pytest.fixture
def gateway(app, data):
    value = MutationGateway(data)
    yield value
    value.read_gate.set()
    value.save_gate.set()
    assert QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()


@pytest.fixture
def popup(app, gateway):
    manager = ItemRiskPage()
    controller = RiskRuleSaveController(gateway, parent=manager)
    manager.configure(gateway, RefreshContext, controller.create_risk_rule,
                      submit_edit=controller.update_risk_rule, submit_delete=controller.delete_risk_rule)
    manager.set_data(gateway.data())
    dialog = ItemRuleVerdictDialog(None, manager.data, 1, 9, manager=manager)
    dialog.show()
    app.processEvents()
    yield dialog, controller
    gateway.read_gate.set()
    gateway.save_gate.set()
    dialog.reject()
    for child in dialog.findChildren(QDialog):
        child.reject()
    dialog.deleteLater()
    manager.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_selected_rule_edit_prefills_and_updates_existing_id_without_create(app, popup, gateway, monkeypatch):
    dialog, controller = popup
    dialog.selector.setCurrentIndex(dialog.selector.findData(10))
    other = deepcopy(gateway.rows[9])
    inspected = []

    def edit(editor):
        inspected.append(editor)
        assert editor.windowTitle() == "위험 기준 수정"
        assert editor.edit_record.revision == gateway.rows[10].revision
        assert editor.name.text() == "둘째 기준" and editor.memo.text() == "기존 메모"
        assert editor.criterion.currentData() == "VERSION_BELOW" and editor.start.currentData() == 2
        assert editor.values()["item_id"] == 1 and editor.values()["family_id"] == 10
        assert not editor.family.isEnabled() and editor.item_editor.isHidden()
        assert [a.data() for a in editor.model_actions if a.isChecked()] == [2]
        assert editor.site.currentData() == "Site A" and editor.line.currentData() is None
        editor.name.setText("고친 기준")
        editor.memo.setText("고친 메모")
        editor.criterion.setCurrentIndex(editor.criterion.findData("VERSION_ABOVE"))
        editor.start.setCurrentIndex(editor.start.findData(4))
        editor.show()
        wait_for(app, lambda: editor.save_button.isEnabled())
        QTest.mouseClick(editor.save_button, Qt.MouseButton.LeftButton)
        return editor.result()

    monkeypatch.setattr(ItemRiskDialog, "exec", edit)
    QTest.mouseClick(dialog.edit_button, Qt.MouseButton.LeftButton)
    wait_for(app, lambda: bool(gateway.updates) and not controller.busy)
    assert inspected and not dialog.isVisible() and not gateway.creates and not gateway.deletes
    record, values, _, thread = gateway.updates[0]
    assert record.key == {"risk_rule_id": 10} and record.revision == "10" * 32
    assert values["rule_type"] == "VERSION_ABOVE" and values["release_version_id"] == 4
    assert values["item_manager"]["memo"] == "고친 메모"
    assert thread != get_ident() and gateway.gets[0][2] != get_ident()
    assert gateway.rows[9] == other and gateway.rows[10].current["risk_reason"] == "고친 기준"


@pytest.mark.parametrize("accept", [False, True])
def test_selected_rule_delete_requires_confirmation_and_keeps_other_rules(app, popup, gateway, monkeypatch, accept):
    dialog, controller = popup
    dialog.selector.setCurrentIndex(dialog.selector.findData(10))
    inspected = []

    def confirm(confirmation):
        inspected.append(confirmation.message.text())
        confirmation.show()
        QTest.mouseClick(confirmation.save_button if accept else confirmation.cancel_button, Qt.MouseButton.LeftButton)
        return confirmation.result()

    monkeypatch.setattr(ImportConfirmDialog, "exec", confirm)
    QTest.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)
    wait_for(app, lambda: bool(inspected) and not controller.busy)
    assert "둘째 기준" in inspected[0] and "다른 기준은 유지" in inspected[0]
    assert not gateway.creates and not gateway.updates and 9 in gateway.rows
    if accept:
        assert len(gateway.deletes) == 1 and gateway.deletes[0][0].key == {"risk_rule_id": 10}
        assert gateway.deletes[0][0].revision == "10" * 32 and gateway.deletes[0][2] != get_ident()
        assert 10 not in gateway.rows and not dialog.isVisible()
    else:
        assert not gateway.deletes and 10 in gateway.rows and dialog.isVisible()


def test_busy_record_read_blocks_duplicate_actions_and_close_discards_late_result(app, popup, gateway, monkeypatch):
    dialog, _ = popup
    gateway.read_gate.clear()
    opened = []
    monkeypatch.setattr(dialog.manager, "open_edit", lambda *args: opened.append(args))
    QTest.mouseClick(dialog.edit_button, Qt.MouseButton.LeftButton)
    wait_for(app, lambda: bool(gateway.gets))
    assert dialog.mutation_request.busy
    assert not dialog.edit_button.isEnabled() and not dialog.delete_button.isEnabled() and not dialog.selector.isEnabled()
    dialog._load_rule("delete")
    assert len(gateway.gets) == 1
    dialog.reject()
    gateway.read_gate.set()
    wait_for(app, gateway.read_finished.is_set)
    app.processEvents()
    assert not opened and not gateway.updates and not gateway.deletes and not dialog.mutation_request.busy


@pytest.mark.parametrize("problem", ["read_failure", "changed_item"])
def test_current_record_failure_or_changed_item_cannot_mutate(app, popup, gateway, monkeypatch, problem):
    dialog, _ = popup
    opened = []
    monkeypatch.setattr(dialog.manager, "open_edit", lambda *args: opened.append(args))
    if problem == "read_failure":
        gateway.read_error = ValueError("현재 행 읽기 실패")
    else:
        gateway.rows[9] = replace(gateway.rows[9], current={**gateway.rows[9].current, "item_id": 2})
    QTest.mouseClick(dialog.edit_button, Qt.MouseButton.LeftButton)
    wait_for(app, lambda: bool(gateway.gets) and not dialog.mutation_request.busy)
    assert dialog.isVisible() and dialog.mutation_notice.isVisible()
    assert "못했습니다" in dialog.mutation_notice.text() if problem == "read_failure" else "변경" in dialog.mutation_notice.text()
    assert dialog.edit_button.isEnabled() and not opened and not gateway.updates and not gateway.deletes


@pytest.mark.parametrize("kind", ["VERSION_BELOW", "DIRECT_VERSION", "VERSION_ABOVE"])
def test_edit_prefill_preserves_disabled_flag_item_context_and_existing_metadata(app, data, gateway, kind):
    row = gateway.rows[10]
    row = replace(row, current={**row.current, "rule_type": kind, "is_enabled": False})
    editor = ItemRiskDialog(None, data, gateway, RefreshContext, lambda _: False,
                            family_id=10, item_id=1, edit_record=row)
    try:
        values = editor.values()
        assert values["rule_type"] == kind and values["release_version_id"] == 2
        assert values["is_enabled"] is False and values["mapping_status"] == "VERIFIED"
        assert values["item_id"] == 1 and values["family_id"] == 10
        assert values["item_manager"] == row.current["target_reference"]["item_manager"]
        assert editor.save_button.text() == "수정 저장"
    finally:
        editor.reject()
        editor.deleteLater()


@pytest.mark.parametrize("kind", ["SAFETY_REQUIRED", "DEFECT_ITEM"])
def test_legacy_edit_keeps_original_condition_ids_and_unrestricted_scope(app, data, gateway, kind):
    row = gateway.rows[9]
    row = replace(row, current={**row.current, "rule_type": kind, "release_version_id": None,
                               "start_release_version_id": 1, "end_release_version_id": 4,
                               "target_reference": {"retained": "원본"}})
    editor = ItemRiskDialog(None, data, gateway, RefreshContext, lambda _: False,
                            family_id=10, item_id=1, edit_record=row)
    try:
        editor.name.setText("기존 조건 이름 수정")
        values = editor.values()
        assert values["rule_type"] == kind and values["release_version_id"] is None
        assert values["start_release_version_id"] == 1 and values["end_release_version_id"] == 4
        assert "item_manager" not in values and values["group_id"] is None
        assert editor.version_fields.isHidden() and not editor.criterion.isEnabled()
        assert "기존" in editor.legacy_note.text() and "v99" in editor.legacy_note.text() and "v3" in editor.legacy_note.text()
    finally:
        editor.reject()
        editor.deleteLater()


@pytest.mark.parametrize("operation", ["update", "delete"])
@pytest.mark.parametrize("uncertain", [False, True])
def test_mutation_controller_handles_failure_and_prevents_duplicate_writes(app, gateway, operation, uncertain):
    gateway.save_gate.clear()
    gateway.save_error = DatabaseCommitUnknownError("결과 미확인") if uncertain else DatabaseSaveError("저장 취소")
    controller = RiskRuleSaveController(gateway)
    failed, unknown = QSignalSpy(controller.save_failed), QSignalSpy(controller.save_uncertain)
    record = gateway.rows[9]
    if operation == "update":
        assert controller.update_risk_rule(record, {"risk_reason": "수정"})
    else:
        assert controller.delete_risk_rule(record)
    wait_for(app, lambda: bool(gateway.updates or gateway.deletes))
    assert not controller.create_risk_rule({}) and not controller.delete_risk_rule(record)
    gateway.save_gate.set()
    wait_for(app, lambda: not controller.busy)
    assert controller.state == ("unknown" if uncertain else "failed") and controller.locked is uncertain
    assert unknown.count() == int(uncertain) and failed.count() == int(not uncertain)
    assert len(gateway.updates) + len(gateway.deletes) == 1 and len(gateway.rows) == 2
    controller.deleteLater()


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_mutations_refresh_item_badges_expanded_context_and_current_dashboard(app, gateway, operation):
    window = create_window(item_loader=BackgroundRead(gateway.data), management_gateway=gateway)
    window.resize(1440, 960)
    window.show()
    window.query_panel.apply_draft()
    dashboard = window.pages["dashboard"]
    window.navigate("items")
    page = window.pages["items"]
    try:
        wait_for(app, lambda: page.data is not None and not page.read_request.busy)
        page.result_buttons[1].click()
        before = dashboard.snapshot
        old = gateway.rows[10]
        if operation == "update":
            assert page.manager.submit_edit(old, {"risk_reason": "저장 후 기준명", "item_manager": {
                **old.current["target_reference"]["item_manager"], "name": "저장 후 기준명"}})
        else:
            assert page.manager.submit_delete(old)
        wait_for(app, lambda: not window.management_saving and not window.management_refresh.busy
                 and not page.read_request.busy and page.data is not None)
        assert dashboard.snapshot is not None and dashboard.snapshot is not before
        assert dashboard.snapshot.query is before.query and window.query_state.is_current(before.query)
        assert page.selected_id == 1 and page.mode == "detail"
        ids = {rule.risk_rule_id for rule in page.data.risk_rules}
        assert ids == ({9, 10} if operation == "update" else {9})
        assert page.result_buttons[1].rule_badge.text() == ("위험 기준 2건" if operation == "update" else "위험 기준 1건")
        if operation == "update":
            assert "저장 후 기준명" in page.result_buttons[1].rule_badge.toolTip()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
