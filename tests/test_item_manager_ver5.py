"""VER5 Item 검색·원본·범위 저장의 실제 경계 검증. 회사 AI/운영 DB에는 접근하지 않는다."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from setron.features.item.schemas import ItemState, TargetScope
from setron.features.management.service import ManagementService
from setron.features.release.schemas import ItemInput, ItemLinkInput
from setron.features.release.service import ReleaseService
from setron.features.risk.schemas import RiskStatus
from setron.infrastructure.ai_client import ChatReply, ResponseSource
from setron.ui.app import create_window
from setron.ui.item_analysis import answer_items
from setron.ui.item_original import OriginalItemRow, original_item_rows
from setron.ui.item_risk_dialog import ItemRiskDialog

from test_ui_items import app, data
from test_release_revision import revision_storage, all_rows
from test_management_service import service_for, observe


def wait_until(predicate):
    for _ in range(200):
        if predicate():
            return
        QTest.qWait(10)
    assert predicate()


def test_ai_answer_uses_actual_ids_and_cumulative_application(data):
    client = SimpleNamespace(complete=lambda _: ChatReply('{"item_ids":[2],"explanation":"관련 Item"}', ResponseSource.EXAMPLE))
    answer = answer_items(data, 10, "기능 알려줘", client)
    assert answer.source is ResponseSource.EXAMPLE
    # Item 2는 최초 릴리즈에만 저장돼도 순서가 확인된 후속 버전까지 누적된다.
    assert [r.version_raw for r in answer.versions] == ["v99", "v2", "v3"]
    assert answer.counts().applied == 4 and answer.counts().unknown == 1
    assert answer_items(data, 10, "다른").item_ids == (2,)


@pytest.mark.parametrize("reply", ['[]', '{}', '{"item_ids":[999]}', '{"item_ids":[true]}', '{"item_ids":[1,1]}', 'not json'])
def test_ai_cannot_invent_ids_or_execute_content(data, reply):
    client = SimpleNamespace(complete=lambda _: ChatReply(reply, ResponseSource.EXAMPLE))
    with pytest.raises(ValueError, match="검증"):
        answer_items(data, 10, "질문", client)


def test_async_search_tab_original_and_stale_answer(app, data):
    source_rows = (OriginalItemRow(2, 8, "C4", 2, ("No", "항목", "REMARK"), ("7", "다른 Item", "원본 비고")),)
    data = replace(data, versions=replace(data.versions, original_rows=source_rows))
    window = create_window(item_loader=lambda: data, chat_enabled=True)
    window.resize(1500, 960)
    window.show()
    window.navigate("items")
    page = window.pages["items"]
    assert page.tabs.tabText(0) == "Item 분석" and page.tabs.tabText(1) == "Item 관리"
    assert page.original.model.headings == ("No", "항목", "REMARK")
    assert page.original.model.rows == (("7", "다른 Item", "원본 비고"),)
    original_query = window.query_state.applied
    page.search.setText("다른")
    assert page.search_answer()
    wait_until(lambda: not page.answer_request.busy)
    assert page.answer.item_ids == (2,)
    assert window.query_state.applied == original_query  # 검색만으로 전역 조건은 바뀌지 않는다.
    assert page.original.model.highlighted == {2}
    assert window.navigate_item_answer("ai", page.answer)
    assert window.route == "ai" and "관련 Item: 2" in window.pages["ai"].input.text()
    assert not window.pages["ai"].busy  # 초안만 전달, 자동 전송 금지
    window.navigate("items")
    page.tabs.setCurrentIndex(1)
    assert not page.manager.isHidden() and page.list_view.isHidden()
    page.tabs.setCurrentIndex(0)
    assert page.manager.isHidden() and not page.list_view.isHidden()
    page.search.setText("없는 질문")
    assert page.answer is None and page.answer_card.isHidden()
    window.close()


def test_raw_table_reader_preserves_original_columns_and_values():
    batch = {"raw_data": {"O5": {"A1": "No", "B1": "Update내용", "C1": "JIRA", "D1": "비고",
                                "A2": 7, "B2": "실제 내용", "C2": "ETOJ-1", "D2": "원문 & <값>"}}}
    tx = SimpleNamespace(get=lambda *args: deepcopy(batch))
    rows = original_item_rows(tx, [{"item_id": 3, "batch_id": 1, "source_reference": {
        "content": {"sheet": "O5", "cells": ["B2"]}}}])
    assert rows[0].headers == ("No", "Update내용", "JIRA", "비고")
    assert rows[0].values == ("7", "실제 내용", "ETOJ-1", "원문 & <값>")


def test_scoped_risk_preview_save_and_reload_keep_same_scope(revision_storage):
    storage = revision_storage
    service, snapshot, first, second = service_for(storage)
    observe(storage, first, code="EQ-2")
    with storage.transaction() as tx:
        rows = all_rows(storage, "equipment_snapshot")
        tx.update("equipment_snapshot", {"snapshot_id": rows[1]["snapshot_id"]}, {"line": "2라인"})
    releases = ReleaseService(storage)
    item_id = releases.register_item(second, ItemInput(content="필수 안전 기능")).current["item_id"]
    releases.save_item_links(second, (ItemLinkInput(item_id=item_id, item_state=ItemState.APPLIED, target_scope=TargetScope.ALL),))
    values = dict(rule_type="SAFETY_REQUIRED", family_id=1, item_id=item_id, risk_reason="안전 기능",
                  mapping_status="VERIFIED", item_manager=dict(schema=1, name="안전 기능", category="환경안전 필수",
                      memo="검증용", model_ids=[1], site="화성", line="1라인"))
    before = all_rows(storage, "risk_rule"), all_rows(storage, "import_batch")
    preview = service.preview_risk_rule(values)
    assert preview.saveable and preview.newly_risky == 1
    assert (all_rows(storage, "risk_rule"), all_rows(storage, "import_batch")) == before
    saved = service.create_risk_rule(values)
    assert sum(e.status is RiskStatus.RISK for e in saved.refresh.dates[0].risks.equipment) == 1
    assert all_rows(storage, "risk_rule")[0]["target_reference"]["item_manager"] == values["item_manager"]
    from setron.ui.database_source import load_dashboard_source
    from setron.ui.item_data import item_data_from_source
    source = load_dashboard_source(storage)
    data = item_data_from_source(source.version_catalog, source)
    assert data.risk_rules[0].target_line == "1라인" and data.risk_rules[0].memo == "검증용"


@pytest.mark.parametrize("changes", [dict(model_ids=[999]), dict(line="없는 라인"), dict(category="없는 구분"), dict(model_ids=[3])])
def test_invalid_risk_scope_never_writes(revision_storage, changes):
    service, _, first, _ = service_for(revision_storage)
    values = dict(rule_type="DIRECT_VERSION", family_id=1, release_version_id=first, risk_reason="직접 지정", mapping_status="VERIFIED",
                  item_manager=dict(schema=1, name="위험 버전", category="결함 버전", memo="", model_ids=[1], site="화성", line="1라인"))
    values["item_manager"].update(changes)
    before = all_rows(revision_storage, "risk_rule"), all_rows(revision_storage, "import_batch")
    assert not service.preview_risk_rule(values).saveable
    with pytest.raises((ValueError, LookupError)):
        service.create_risk_rule(values)
    assert (all_rows(revision_storage, "risk_rule"), all_rows(revision_storage, "import_batch")) == before


def test_risk_dialog_preview_invalidated_cancel_and_explicit_submit(app, data):
    submitted = []
    impact = SimpleNamespace(problems=(), base_date=data.base_date, newly_risky=2, hpa_excluded=0, saveable=True,
                             count=lambda state: 2 if state is RiskStatus.RISK else 0)
    gateway = SimpleNamespace(preview_risk_rule=lambda values, context: impact)
    dialog = ItemRiskDialog(None, data, gateway, lambda: None, lambda values: submitted.append(values) or True)
    dialog.name.setText("안전 필수")
    dialog.verified.setChecked(True)
    dialog.preview_impact()
    wait_until(lambda: not dialog.request.busy)
    assert dialog.save_button.isEnabled() and "새 위험 후보 2대" in dialog.preview.text()
    dialog.memo.setText("변경")
    assert not dialog.save_button.isEnabled() and not dialog.save()
    dialog.preview_impact()
    wait_until(lambda: not dialog.request.busy)
    assert dialog.save() and len(submitted) == 1
    assert not dialog.save() and len(submitted) == 1  # 같은 창의 중복 저장 금지
    assert submitted[0]["item_manager"]["memo"] == "변경"
    cancel = ItemRiskDialog(None, data, gateway, lambda: None, lambda v: submitted.append(v))
    cancel.reject()
    assert len(submitted) == 1


def test_answer_navigation_applies_only_selected_family_and_leaves_global_sources_unchanged(app):
    from setron.ui.dashboard_demo import demo_dashboard_source
    from setron.ui.version_demo import demo_version_catalog
    from setron.ui.item_data import item_data_from_source
    source = demo_dashboard_source()
    data = item_data_from_source(demo_version_catalog(source), source)
    window = create_window(source, item_loader=lambda: data)
    window.show()
    window.navigate("items")
    page = window.pages["items"]
    page.search.setText("Recipe")
    page.search_answer()
    wait_until(lambda: not page.answer_request.busy)
    answer = page.answer
    original_targets = dict(source.targets)
    assert window.navigate_item_answer("heatmap", answer)
    query = window.query_state.applied
    assert query.selection.item_family_id == answer.family_id
    snapshot = window.pages["dashboard"].snapshot
    assert snapshot.selected_records and all(pm.family_id == answer.family_id for r in snapshot.selected_records for pm in r.pms)
    assert source.targets == original_targets
    from setron.ui.management_integration import report_filter
    assert report_filter(snapshot, source).family_ids == (answer.family_id,)
    window.query_panel.reset_draft()
    assert window.query_state.draft.item_family_id is None
    window.close()


@pytest.mark.parametrize("uncertain", [False, True])
def test_item_save_failure_notice_survives_followup_reload(app, data, uncertain):
    from test_ui_management import FakeGateway
    from setron.infrastructure.database import DatabaseSaveError, DatabaseCommitUnknownError
    from setron.ui.read_worker import BackgroundRead
    gateway = FakeGateway()
    def fail(*args):
        raise DatabaseCommitUnknownError("테스트 미확인") if uncertain else DatabaseSaveError("테스트 실패")
    gateway.create_risk_rule = fail
    window = create_window(item_loader=BackgroundRead(lambda: data), management_gateway=gateway)
    window.show()
    window.navigate("items")
    page = window.pages["items"]
    wait_until(lambda: page.data is not None)
    assert page.manager.submit({"risk_reason": "검증"})
    wait_until(lambda: not window.pages["management"].busy and not page.read_request.busy)
    assert "저장 결과 미확인" in page.notice.text() if uncertain else "저장 실패" in page.notice.text()
    assert page.status_host.isVisible()
    assert window.pages["management"].locked is uncertain
    window.close()
