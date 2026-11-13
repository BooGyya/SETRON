"""아이템 매니저: 릴리즈노트 원본 Jira 코드 연결과 위험 기준 등록 결과의 대시보드 위험 후보 연결."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.schemas import ItemInput, ItemLinkInput
from setron.features.release.service import ReleaseService
from setron.features.risk.schemas import MappingStatus, RiskRule, RiskStatus, RuleType
from setron.ui.app import create_window
from setron.ui.item_original import (
    JIRA_EMPTY, JIRA_FOUND, JIRA_NO_COLUMN, JIRA_NO_ORIGINAL, OriginalItemRow, item_jira,
)
from setron.ui.item_risk_list import ItemRuleListDialog
from setron.ui.item_risk_verdicts import ItemRuleVerdictDialog, rule_verdicts
from setron.ui.read_worker import BackgroundRead

from test_ui_items import app, data  # noqa: F401
from test_release_revision import revision_storage  # noqa: F401
from test_management_service import service_for


def wait_until(predicate):
    for _ in range(300):
        if predicate():
            return
        QTest.qWait(10)
    assert predicate()


def _row(item_id, headers, values, sheet="O5", row=2):
    return OriginalItemRow(item_id, 8, sheet, row, headers, values)


def test_jira_uses_only_original_cells_and_separates_missing_states():
    rows = (
        _row(1, ("No", "Update내용", "JIRA"), ("1", "내용", "ETOJ-1, ETOJ-2\nETOJ-1")),
        _row(2, ("No", "Update내용", "Jira Key"), ("2", "내용", " - ")),
        _row(3, ("No", "항목", "REMARK"), ("3", "내용", "고객 요청 · 참고 ABC-77 반영")),
        _row(4, ("No", "항목", "REMARK"), ("4", "내용", "자유 비고")),
        _row(5, ("No", "Update내용"), ("5", "내용")),
    )
    first = item_jira(rows, 1)
    assert first.status == JIRA_FOUND and first.codes == ("ETOJ-1", "ETOJ-2")
    assert first.row_text == "JIRA ETOJ-1, ETOJ-2" and "입력 작업 8 · O5 · 2행" in first.tooltip
    assert item_jira(rows, 2).status == JIRA_EMPTY and item_jira(rows, 2).label == "Jira 없음"
    # 비고(REMARK) 열은 실제 적힌 Jira 키 모양만 쓰고 나머지 문장은 코드로 만들지 않는다.
    assert item_jira(rows, 3).codes == ("ABC-77",)
    assert item_jira(rows, 4).status == JIRA_NO_COLUMN and item_jira(rows, 4).label == "Jira 미확인"
    assert item_jira(rows, 5).status == JIRA_NO_COLUMN
    missing = item_jira(rows, 6)
    assert missing.status == JIRA_NO_ORIGINAL and missing.codes == () and "원본 행이 없어" in missing.tooltip


def test_imported_release_note_jira_links_to_item_and_risk_matches_dashboard(revision_storage):
    """가져오기 원본(import_batch.raw_data)의 JIRA 열이 Item 출처로 연결되고, 등록한 위험 기준의 후보가 대시보드 판정과 같다."""
    storage = revision_storage
    service, _, first, second = service_for(storage)
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": {"O5": {
            "A1": "No", "B1": "Update내용", "C1": "JIRA",
            "A2": 1, "B2": "필수 안전 기능", "C2": "ETOJ-101",
            "A3": 2, "B3": "표시 개선", "C3": "-",
        }}})["batch_id"]
    releases = ReleaseService(storage)
    ids = [releases.register_item(second, ItemInput(content=content, batch_id=batch, item_no=no, source_reference={
        "content": {"sheet": "O5", "cells": [cell]}})).current["item_id"]
        for no, content, cell in ((1, "필수 안전 기능", "B2"), (2, "표시 개선", "B3"))]
    releases.save_item_links(second, tuple(ItemLinkInput(item_id=i, item_state=ItemState.APPLIED, target_scope=TargetScope.ALL)
                                           for i in ids))
    service.create_risk_rule(dict(rule_type="SAFETY_REQUIRED", family_id=1, item_id=ids[0], risk_reason="안전 기능",
                                  mapping_status="VERIFIED"))
    from setron.ui.database_source import load_dashboard_source
    from setron.ui.item_data import item_data_from_source
    source = load_dashboard_source(storage)
    data = item_data_from_source(source.version_catalog, source)
    jira = data.jira(ids)
    assert jira[ids[0]].codes == ("ETOJ-101",) and jira[ids[1]].status == JIRA_EMPTY
    assert [e.item.item_id for e in data.search("etoj-101")] == [ids[0]]
    candidates = [record.equipment_id for rule in data.risk_rules
                  for record, result, _ in rule_verdicts(data, rule) if result.status is RiskStatus.RISK]
    assert candidates and all(source.risks[i] is RiskStatus.RISK for i in candidates)


def _jira_data(data):
    rows = (_row(1, ("No", "Update내용", "JIRA"), ("7", "Alarm", "ETOJ-9")),
            _row(2, ("No", "Update내용", "JIRA"), ("7", "다른", "")))
    rule = RiskRule(9, RuleType.SAFETY_REQUIRED, 10, "필수 기능", item_id=1, mapping_status=MappingStatus.VERIFIED)
    return replace(data, versions=replace(data.versions, original_rows=rows), risk_rules=(rule,))


def test_rows_show_version_item_and_jira_together(app, data):
    window = create_window(item_loader=lambda: _jira_data(data))
    window.resize(1600, 960)
    window.show()
    window.navigate("items")
    QTest.qWait(30)
    page = window.pages["items"]
    first, second = page.result_buttons[1], page.result_buttons[2]
    assert first.version == "v99 외 1" and first.jira_label.text() == "ETOJ-9" and first.jira_label.isVisible()
    assert second.jira_label.text() == "-" and second.jira_slot_width == first.jira_slot_width
    assert "릴리즈노트 원본 Jira: ETOJ-9" in first.toolTip() and "Jira ETOJ-9" in first.accessibleDescription()
    assert first.caption.geometry().right() < first.jira_label.geometry().left()
    page.search.setText("ETOJ-9")
    assert tuple(page.result_buttons) == (1,)
    page.search.clear()
    page.result_buttons[1].click()
    assert page.item_facts.isHidden() and page.item_facts.text() == ""
    # 펼친 Item에는 위험 기준 노란 칸(위험 후보 대수·대시보드 이동 버튼)을 두지 않는다.
    assert not hasattr(page, "rules_box") and not hasattr(page, "rules_dashboard")
    assert not any(b.text() == "대시보드 위험 후보 보기" for b in page.findChildren(QPushButton) if b.isVisibleTo(page))
    assert not hasattr(window, "show_item_risk_candidates") and window.route == "items"
    window.close()


def test_rows_without_original_show_dash_and_preserve_unknown_jira_metadata(app, data):
    window = create_window(item_loader=lambda: data)
    window.show()
    window.navigate("items")
    QTest.qWait(30)
    page = window.pages["items"]
    assert {b.jira_label.text() for b in page.result_buttons.values()} == {"-"}
    assert all(b.jira.status == JIRA_NO_ORIGINAL for b in page.result_buttons.values())
    window.close()


def test_registered_rules_do_not_expose_removed_dashboard_navigation(app):
    from setron.ui.dashboard_demo import demo_dashboard_source
    from setron.ui.item_data import item_data_from_source
    from setron.ui.version_demo import demo_version_catalog
    source = demo_dashboard_source()
    window = create_window(source, item_loader=lambda: item_data_from_source(demo_version_catalog(source), source))
    window.resize(1500, 960)
    window.show()
    window.query_panel.apply_draft()
    dashboard = window.pages["dashboard"]
    wait_until(lambda: dashboard.snapshot is not None)
    risky = dashboard.snapshot.risky_ids
    assert risky
    page = window.pages["items"]
    window.navigate("items")
    wait_until(lambda: page.data is not None)
    # 범위 밖 설비만이면 이동하지 않는다(0대로 확정하지 않고 이유 안내).
    assert not hasattr(window, "show_item_risk_candidates")
    assert window.route == "items" and not window.equipment_list_drawer.is_open
    window.close()


def test_verdict_dialog_close_stays_on_item_without_removed_actions(app, data):
    window = create_window(item_loader=lambda: _jira_data(data))
    window.show()
    window.navigate("items")
    QTest.qWait(30)
    page = window.pages["items"]
    with patch.object(ItemRuleVerdictDialog, "exec", lambda self: self.close_button.click()):
        assert page.open_registered_rule(9)
    assert not hasattr(page.verdict_dialog, "dashboard_button") and not hasattr(page.verdict_dialog, "source_button")
    assert not hasattr(page, "risk_candidates_requested") and window.route == "items"
    window.close()


@pytest.mark.parametrize("several", [False, True])
def test_expanded_item_button_opens_list_before_exact_selected_rule_detail(app, data, several):
    value = _jira_data(data)
    selected = 9
    if several:
        extra = RiskRule(10, RuleType.DIRECT_VERSION, 10, "두 번째 기준", item_id=1,
                         release_version_id=2, mapping_status=MappingStatus.VERIFIED)
        foreign = replace(extra, risk_rule_id=77, family_id=20)
        value = replace(value, risk_rules=(*value.risk_rules, extra, foreign))
        selected = 10
    window = create_window(item_loader=lambda: value)
    window.resize(1500, 960)
    window.show()
    window.navigate("items")
    QTest.qWait(20)
    page = window.pages["items"]
    page.result_buttons[1].click()
    assert page.view_rules.isVisible() and page.view_rules.isEnabled()
    assert page.view_rules.text() == "위험 기준 보기"
    view_position = page.view_rules.mapToGlobal(QPoint())
    register_position = page.register_rule.mapToGlobal(QPoint())
    assert view_position.x() + page.view_rules.width() < register_position.x()
    assert view_position.y() == register_position.y()
    original_query = window.query_state.applied
    entered, errors = [], []
    list_exec, detail_exec = ItemRuleListDialog.exec, ItemRuleVerdictDialog.exec
    watchdog = QTimer(window)
    watchdog.setSingleShot(True)

    def timeout():
        errors.append("위험 기준 선택 또는 상세 닫기가 완료되지 않았습니다.")
        active = QApplication.activeModalWidget()
        if active is not None:
            active.reject()

    watchdog.timeout.connect(timeout)

    def choose(dialog):
        try:
            assert dialog.isVisible() and dialog.selected_rule_id is None
            assert entered == [("list", 1, 10, (9, 10) if several else (9,))]
            index = dialog.model.index(dialog.model.ids.index(selected), 0)
            QTest.mouseClick(dialog.table.viewport(), Qt.MouseButton.LeftButton,
                             pos=dialog.table.visualRect(index).center())
        except Exception as error:
            errors.append(repr(error))
            dialog.reject()

    def show_list(dialog):
        entered.append(("list", dialog.item_id, dialog.family_id, dialog.model.ids))
        QTimer.singleShot(0, lambda: choose(dialog))
        return list_exec(dialog)

    def show_detail(dialog):
        entered.append(("detail", dialog.item_id, dialog.rule.risk_rule_id))
        QTimer.singleShot(0, dialog.close_button.click)
        return detail_exec(dialog)

    try:
        watchdog.start(4000)
        with patch.object(ItemRuleListDialog, "exec", show_list), patch.object(ItemRuleVerdictDialog, "exec", show_detail):
            page.view_rules.click()
        assert not errors
        assert entered == [("list", 1, 10, (9, 10) if several else (9,)), ("detail", 1, selected)]
        assert page.rule_list_dialog.selected_rule_id == selected
        assert page.verdict_dialog.rule.risk_rule_id == selected
        assert page.mode == "detail" and page.selected_id == 1 and window.route == "items"
        assert window.query_state.applied is original_query
    finally:
        watchdog.stop()
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_registered_risk_save_refreshes_items_and_dashboard(app, data):
    """위험 기준 저장 뒤 아이템 목록(배지·필터)과 대시보드 위험 판정을 같은 조회 조건으로 다시 읽는다."""
    from test_ui_management import FakeGateway
    from setron.ui.dashboard_demo import demo_dashboard_source
    rule = RiskRule(9, RuleType.SAFETY_REQUIRED, 10, "필수 기능", item_id=1, mapping_status=MappingStatus.VERIFIED)
    saved = {"done": False}
    gateway = FakeGateway()

    def create(values, context):
        from setron.features.management import ManagementResult
        saved["done"] = True
        return ManagementResult((SimpleNamespace(table="risk_rule", key={"risk_rule_id": 9}),), None, (), None, 1)

    gateway.create_risk_rule = create
    loader = BackgroundRead(lambda: replace(data, risk_rules=(rule,) if saved["done"] else ()))
    window = create_window(demo_dashboard_source(), item_loader=loader, management_gateway=gateway)
    window.show()
    window.query_panel.apply_draft()
    dashboard = window.pages["dashboard"]
    wait_until(lambda: dashboard.snapshot is not None)
    page = window.pages["items"]
    window.navigate("items")
    wait_until(lambda: page.data is not None and not page.read_request.busy)
    assert page.result_buttons[1].rule_badge is None
    page.registered_only.setChecked(True)
    assert not page.result_buttons
    page.registered_only.setChecked(False)
    before = dashboard.snapshot
    assert page.manager.submit({"risk_reason": "필수 기능"})
    wait_until(lambda: saved["done"] and not window.management_saving and not window.management_refresh.busy
               and not page.read_request.busy and page.data is not None and page.data.risk_rules)
    assert page.result_buttons[1].rule_badge.text() == "위험 기준 1건"
    page.registered_only.setChecked(True)
    assert tuple(page.result_buttons) == (1,)
    # 저장 시작 시 대시보드 판정을 무효화하고, 같은 조회 조건으로 새 위험 판정 결과를 다시 받는다.
    assert dashboard.snapshot is not None and dashboard.snapshot is not before
    assert window.query_state.is_current(dashboard.snapshot.query) and dashboard.snapshot.query == before.query
    window.close()


def test_original_rows_are_found_in_both_import_and_demo_seed_raw_data():
    """가져오기 형식(맨 위 키가 시트)과 시연 seed 형식(raw_data["sheets"] 안) 모두에서 원본 행 · Jira를 읽는다 (11-12)."""
    from setron.ui.item_original import jira_by_item, original_item_rows

    cells = {"D2": "JIRA", "E2": "Update내용", "D3": "ETOJ-4120", "E3": "보호동작 수정"}
    seed = {"augmentation": "합성", "seed": "demo_seed.v2", "sheets": {"Release": cells},
            "source_workbook": "P_PJT_M-O5_SW_정기_Release_List_교육용.xlsx"}
    item = {"item_id": 1, "batch_id": 9, "source_reference": {"content": [{"sheet": "Release", "cells": ["E3"]}]}}
    for raw in ({"Release": cells}, seed, __import__("json").dumps(seed)):
        tx = SimpleNamespace(get=lambda table, key, raw=raw: {"raw_data": raw})
        rows = original_item_rows(tx, [item])
        assert [(r.sheet, r.row) for r in rows] == [("Release", 3)]
        assert jira_by_item(rows, [1])[1].codes == ("ETOJ-4120",)

