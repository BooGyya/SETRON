"""위험 기준 해제·다시 사용: 행을 지우지 않고 is_enabled만 바꾸며, 확인 창·갱신·실패 안내를 검증한다."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from setron.features.management import ManagementService
from setron.features.risk.schemas import MappingStatus, RiskRule, RiskStatus, RuleType
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseSaveError
from setron.ui.app import create_window
from setron.ui.database_source import load_dashboard_source
from setron.ui.item_data import item_data_from_source
from setron.ui.item_risk_verdicts import (ItemRuleVerdictDialog, RuleToggleConfirmDialog, rule_verdicts,
                                         toggle_impact, toggle_message)
from setron.ui.read_worker import BackgroundRead

from test_item_jira_risk_link import _jira_data, wait_until
from test_management_service import observe
from test_release_revision import all_rows, release, revision_storage  # noqa: F401
from test_risk_rule_create import direct_rule
from test_ui_items import app, data  # noqa: F401


def _saved_rule(storage):
    with storage.transaction() as tx:  # 조회 화면 설비군(M-O2)에 연결되도록 등록 모델 이름을 맞춘다
        tx.update("equipment_group", {"group_id": 1}, {"group_name": "M-O2"})
    release_id = release(storage, raw="v1", order=1)
    snapshot = observe(storage, release_id)
    created = ManagementService(storage).create_risk_rule(direct_rule(release_id)).records[0].current
    return created["risk_rule_id"], snapshot.equipment_id


def test_disable_and_restore_keep_row_journal_and_rejudge(revision_storage):  # noqa: F811
    storage = revision_storage
    rule_id, equipment_id = _saved_rule(storage)
    assert load_dashboard_source(storage).risks[equipment_id] is RiskStatus.RISK
    batches = len(all_rows(storage, "import_batch"))

    result = ManagementService(storage).set_risk_rule_enabled(rule_id, False)

    rows = all_rows(storage, "risk_rule")
    assert len(rows) == 1 and rows[0]["risk_rule_id"] == rule_id and not rows[0]["is_enabled"]  # 삭제하지 않는다
    assert rows[0]["target_reference"]["direct_version"]["version_raw"] == "v1"  # 재연결 근거 유지
    assert not result.records[0].current["is_enabled"]
    journal = all_rows(storage, "import_batch")[batches:]
    assert len(journal) == 1 and journal[0]["source_type"] == "MANUAL"
    assert journal[0]["raw_data"]["management_edit"]["changes"][0]["changes"] == {"is_enabled": False}
    source = load_dashboard_source(storage)
    assert source.risks[equipment_id] is not RiskStatus.RISK
    # 판정기는 해제된 기준을 빼지만, 아이템 매니저는 '해제됨'·다시 사용을 위해 전체 기준을 받는다.
    assert source.assessor.rules == ()
    item_rules = item_data_from_source(source.version_catalog, source).risk_rules
    assert [(r.risk_rule_id, r.is_enabled) for r in item_rules] == [(rule_id, False)]

    ManagementService(storage).set_risk_rule_enabled(rule_id, True)
    assert all_rows(storage, "risk_rule")[0]["is_enabled"]
    assert load_dashboard_source(storage).risks[equipment_id] is RiskStatus.RISK


def test_repeated_toggle_is_rejected_without_change(revision_storage):  # noqa: F811
    storage = revision_storage
    rule_id, _ = _saved_rule(storage)
    service = ManagementService(storage)
    service.set_risk_rule_enabled(rule_id, False)
    before = (all_rows(storage, "risk_rule"), len(all_rows(storage, "import_batch")))
    with pytest.raises(ValueError, match="이미 해제된"):
        service.set_risk_rule_enabled(rule_id, False)  # 중복 클릭·다른 화면에서 먼저 해제
    with pytest.raises(ValueError):
        service.set_risk_rule_enabled(rule_id, 1)  # 참/거짓만 받는다
    assert (all_rows(storage, "risk_rule"), len(all_rows(storage, "import_batch"))) == before


def test_failed_rejudge_rolls_back_toggle(revision_storage):  # noqa: F811
    storage = revision_storage
    rule_id, _ = _saved_rule(storage)
    before = (all_rows(storage, "risk_rule"), len(all_rows(storage, "import_batch")))
    with patch("setron.features.management.service.ReleaseRefreshService.refresh_in", side_effect=RuntimeError("재판정 실패")):
        with pytest.raises(RuntimeError):
            ManagementService(storage).set_risk_rule_enabled(rule_id, False)
    assert (all_rows(storage, "risk_rule"), len(all_rows(storage, "import_batch"))) == before


def test_toggle_impact_reuses_preview_and_marks_unknown(data):  # noqa: F811
    risky = _jira_data(data)
    rule = risky.risk_rules[0]
    expected = sum(res.status is RiskStatus.RISK for _, res, _ in rule_verdicts(risky, rule))
    assert expected and toggle_impact(risky, rule) == expected
    # 해제된 기준도 '켰을 때' 기준으로 다시 사용 영향을 센다.
    assert toggle_impact(replace(risky, risk_rules=(replace(rule, is_enabled=False),)), rule) == expected
    # 다른 기준으로도 위험 후보인 설비는 빠지지 않으므로 세지 않는다.
    twin = replace(rule, risk_rule_id=10)
    assert toggle_impact(replace(risky, risk_rules=(rule, twin)), rule) == 0
    # 관측 자료가 없으면 0대로 쓰지 않고 '확인 불가'로 표기한다.
    blind = replace(risky, records=(), observations_connected=False, base_date=None)
    assert toggle_impact(blind, rule) is None
    assert "확인 불가" in toggle_message(False, None) and "0대" not in toggle_message(False, None)
    assert toggle_message(False, 2) == "해제하면 위험 후보 2대가 빠집니다."
    assert toggle_message(True, 2) == "다시 사용하면 위험 후보 2대가 추가됩니다."
    with patch("setron.ui.item_risk_verdicts.preview_rule_impact", side_effect=RuntimeError):
        assert toggle_impact(risky, rule) is None


def test_confirm_cancel_close_and_double_click_do_not_request(app, data):  # noqa: F811
    risky = _jira_data(data)
    dialog = ItemRuleVerdictDialog(None, risky, 1, 9, toggle_allowed=True)
    assert dialog.toggle_button.text() == "기준 해제" and dialog.toggle_button.isEnabled()
    assert dialog.state_chip.isHidden()
    shown = []

    def reject(self):
        shown.append(self.message.text())
        return QDialog.DialogCode.Rejected  # 취소 또는 창 닫기

    with patch.object(RuleToggleConfirmDialog, "exec", reject):
        assert not dialog._toggle_clicked()
    assert dialog.toggle_request is None and shown == ["해제하면 위험 후보 1대가 빠집니다."]
    assert dialog.confirm_dialog.confirm_button.text() == "해제" and dialog.confirm_dialog.cancel_button.text() == "취소"
    with patch.object(RuleToggleConfirmDialog, "exec", lambda self: QDialog.DialogCode.Accepted):
        assert dialog._toggle_clicked()
        assert not dialog._toggle_clicked()  # 중복 클릭은 두 번째 요청을 만들지 않는다
    assert dialog.toggle_request == (9, False)
    # 저장 경로가 없으면(시연 자료) 버튼을 막고 요청을 만들지 않는다.
    readonly = ItemRuleVerdictDialog(None, risky, 1, 9)
    assert not readonly.toggle_button.isEnabled() and not readonly._toggle_clicked()


def test_disabled_rule_keeps_reenable_entry(app, data):  # noqa: F811
    risky = _jira_data(data)
    disabled = replace(risky, risk_rules=(replace(risky.risk_rules[0], is_enabled=False),))
    window = create_window(item_loader=lambda: disabled)
    window.resize(1600, 960)
    window.show()
    window.navigate("items")
    QTest.qWait(30)
    page = window.pages["items"]
    badge = page.result_buttons[1].rule_badge
    assert badge is not None and badge.text() == "해제된 기준 1건" and badge.icon().isNull()
    page.registered_only.setChecked(True)  # 위험 등록 필터는 사용 중 기준만 센다
    assert not page.result_buttons
    page.registered_only.setChecked(False)
    dialog = ItemRuleVerdictDialog(None, disabled, 1, 9, toggle_allowed=True)
    assert not dialog.state_chip.isHidden() and dialog.state_chip.text() == "해제됨"
    assert dialog.toggle_button.text() == "다시 사용" and "다시 사용" in dialog.notice_text.text()
    assert dialog.table.isHidden() and not hasattr(dialog, "dashboard_button")
    with patch.object(RuleToggleConfirmDialog, "exec", lambda self: QDialog.DialogCode.Accepted):
        assert dialog._toggle_clicked()
    assert dialog.toggle_request == (9, True)
    # 사용 중 기준과 해제된 기준이 함께 있으면 선택 목록에 '(해제됨)'으로 남는다.
    both = replace(risky, risk_rules=(risky.risk_rules[0], replace(risky.risk_rules[0], risk_rule_id=11, is_enabled=False,
                                                                   display_name="예전 기준")))
    mixed = ItemRuleVerdictDialog(None, both, 1, 9, toggle_allowed=True)
    assert [mixed.selector.itemText(i) for i in range(mixed.selector.count())] == ["필수 기능", "예전 기준 (해제됨)"]
    window.close()


class _ToggleGateway:
    """해제·다시 사용 저장만 흉내 내는 관리 경계. 실패·결과 미확인도 고를 수 있다."""

    def __init__(self, test_gateway):
        self.base = test_gateway
        self.enabled = True
        self.calls = []
        self.error = None

    def __getattr__(self, name):
        return getattr(self.base, name)

    def set_risk_rule_enabled(self, rule_id, enabled, context):
        from setron.features.management import ManagementResult
        self.calls.append((rule_id, enabled))
        if self.error is not None:
            raise self.error
        self.enabled = enabled
        return ManagementResult((SimpleNamespace(table="risk_rule", key={"risk_rule_id": rule_id}),), None, (), None, 1)


def _toggle_window(data, gateway):
    from setron.ui.dashboard_demo import demo_dashboard_source
    risky = _jira_data(data)
    rule = risky.risk_rules[0]
    loader = BackgroundRead(lambda: replace(risky, risk_rules=(replace(rule, is_enabled=gateway.enabled),)))
    window = create_window(demo_dashboard_source(), item_loader=loader, management_gateway=gateway)
    window.show()
    window.query_panel.apply_draft()
    dashboard = window.pages["dashboard"]
    wait_until(lambda: dashboard.snapshot is not None)
    page = window.pages["items"]
    window.navigate("items")
    wait_until(lambda: page.data is not None and not page.read_request.busy)
    return window, page, dashboard


def _settled(window, page):
    return (not window.management_saving and not window.management_refresh.busy
            and not page.read_request.busy and page.data is not None)


def test_disable_from_popup_refreshes_badge_dashboard_and_toast(app, data):  # noqa: F811
    from test_ui_management import FakeGateway
    gateway = _ToggleGateway(FakeGateway())
    window, page, dashboard = _toggle_window(data, gateway)
    assert page.toast_timer.interval() == 4000
    page.toast_timer.setInterval(600000)  # 느린 환경에서 확인 전에 사라지지 않게 하고, 사라짐은 아래에서 직접 발생시킨다
    assert page.result_buttons[1].rule_badge.text() == "위험 기준 1건"
    before = dashboard.snapshot

    # 취소하면 저장 경로를 부르지 않는다.
    def cancel(self):
        with patch.object(RuleToggleConfirmDialog, "exec", lambda confirm: QDialog.DialogCode.Rejected):
            self.toggle_button.click()
        return QDialog.DialogCode.Rejected

    with patch.object(ItemRuleVerdictDialog, "exec", cancel):
        assert page.open_registered_rule(9)
    assert gateway.calls == [] and page.verdict_dialog.toggle_request is None

    def confirm(self):
        with patch.object(RuleToggleConfirmDialog, "exec", lambda confirm: QDialog.DialogCode.Accepted):
            self.toggle_button.click()
        return QDialog.DialogCode.Accepted

    with patch.object(ItemRuleVerdictDialog, "exec", confirm):
        assert page.open_registered_rule(9)
    wait_until(lambda: gateway.calls and _settled(window, page) and not page.data.risk_rules[0].is_enabled
               and dashboard.snapshot is not before)
    assert gateway.calls == [(9, False)]
    badge = page.result_buttons[1].rule_badge
    assert badge.text() == "해제된 기준 1건"
    page.registered_only.setChecked(True)
    assert not page.result_buttons
    page.registered_only.setChecked(False)
    assert window.query_state.is_current(dashboard.snapshot.query) and dashboard.snapshot.query == before.query
    assert page.notice.text() == "기준을 해제했습니다." and page.status_host.isVisible()
    page.toast_timer.timeout.emit()  # 몇 초 뒤 사라진다
    assert page.save_feedback is None and not page.status_host.isVisible()

    # 해제된 기준은 배지 → 팝업 → '다시 사용'으로 되돌린다.
    with patch.object(ItemRuleVerdictDialog, "exec", confirm):
        assert page.open_registered_rule(9)
    wait_until(lambda: len(gateway.calls) == 2 and _settled(window, page) and page.data.risk_rules[0].is_enabled)
    assert gateway.calls[-1] == (9, True)
    assert page.result_buttons[1].rule_badge.text() == "위험 기준 1건"
    assert page.notice.text() == "기준을 다시 사용합니다."
    window.close()


@pytest.mark.parametrize("error, text", [
    (DatabaseSaveError("저장 실패"), "기준 해제 실패 · 기존 상태 유지"),
    (DatabaseCommitUnknownError("결과 미확인"), "기준 해제 결과 미확인"),
])
def test_failed_or_unknown_toggle_keeps_state_and_notice(app, data, error, text):  # noqa: F811
    from test_ui_management import FakeGateway
    gateway = _ToggleGateway(FakeGateway())
    gateway.error = error
    window, page, _ = _toggle_window(data, gateway)
    assert page.request_rule_toggle(9, False)
    wait_until(lambda: gateway.calls and _settled(window, page) and page.save_feedback is not None)
    assert page.data.risk_rules[0].is_enabled  # 낙관적으로 바꾸지 않는다
    assert page.result_buttons[1].rule_badge.text() == "위험 기준 1건"
    assert text in page.notice.text()
    page.toast_timer.timeout.emit()  # 실패·미확인 안내는 저절로 사라지지 않는다
    assert text in page.notice.text()
    if isinstance(error, DatabaseCommitUnknownError):
        # 결과 미확인이면 다시 저장을 막고 이유를 안내한다.
        assert not page.request_rule_toggle(9, False) and len(gateway.calls) == 1
        assert "바꿀 수 없습니다" in page.notice.text()
    window.close()
