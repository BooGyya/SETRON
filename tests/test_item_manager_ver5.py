"""VER5 Item 검색·원본·범위 저장의 실제 경계 검증. 회사 AI/운영 DB에는 접근하지 않는다."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

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
from setron.ui.item_risk_verdicts import ItemRuleVerdictDialog

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
    assert not hasattr(page, "tabs") and page.manager.isHidden()  # 위험 기준 관리 탭은 없고 필터로 대체됐다.
    original_query = window.query_state.applied
    page.search.setText("다른")
    page.ai_search.trigger()
    assert page.answer_request.busy
    wait_until(lambda: not page.answer_request.busy)
    assert page.answer.item_ids == (2,)
    assert window.query_state.applied == original_query  # 검색만으로 전역 조건은 바뀌지 않는다.
    assert window.navigate_item_answer("ai", page.answer)
    assert window.route == "ai" and "관련 Item: 2" in window.pages["ai"].input.text()
    assert not window.pages["ai"].busy  # 초안만 전달, 자동 전송 금지
    window.navigate("items")
    assert page.manager.isHidden() and not page.list_view.isHidden()
    page.search.setText("없는 질문")
    assert page.answer is None and page.answer_card.isHidden()
    window.close()


def test_query_candidates_and_rule_drilldown(app, data):
    from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
    rule = RiskRule(9, RuleType.SAFETY_REQUIRED, 10, "필수 기능", item_id=1,
                    mapping_status=MappingStatus.VERIFIED)
    window = create_window(item_loader=lambda: replace(data, risk_rules=(rule,)))
    window.show()
    window.navigate("items")
    page = window.pages["items"]
    page.search.setText("7")
    assert page.search_answer()
    wait_until(lambda: not page.answer_request.busy)
    assert tuple(page.result_buttons) == (1, 2) and page.selected_id is None
    assert page.open_row(0)
    assert page.list_view.isVisible() and page.detail_view.isVisible()
    page.result_buttons[2].click()
    assert page.selected_id == 2 and page.search.text() == "7"
    assert page.result_rows.indexOf(page.detail_view) == page.result_rows.indexOf(page.result_buttons[2]) + 1
    page.result_buttons[2].click()
    assert page.detail_view.isHidden()
    page.result_buttons[1].click()
    assert page.result_rows.indexOf(page.detail_view) + 1 == page.result_rows.indexOf(page.result_buttons[2])
    assert page.title.text() == data.versions.items[0].item.content
    assert page.equipment_model.ids == (2,) and "미적용 (저장된 버전별 Item 상태)" in page.equipment_model.rows[0][6]
    page.site_filter.setCurrentIndex(1)
    page.refresh_after_save(preserve_detail=True)
    assert page.selected_id == 1 and page.site_filter.currentData() == "Site A"
    assert page.answer.item_ids == (1, 2) and page.equipment_model.ids == (2,)
    assert page.result_buttons[1].rule_badge.text() == "위험 기준 1건 ›"
    assert page.result_buttons[2].rule_badge is None
    # 배지 툴팁은 기준명 하나만, 부모 행 툴팁에는 기준명을 겹쳐 넣지 않는다(툴팁 중복 방지).
    # 행 툴팁은 전체 제목 + 릴리즈 정보 하나(기준명은 배지 툴팁에만 둔다).
    assert page.result_buttons[1].toolTip().startswith("Alarm <원문> & 보존\n") and "v99" in page.result_buttons[1].toolTip()
    assert page.result_buttons[1].rule_badge.toolTip() == "필수 기능"
    # 배지는 상세를 열지 않고 그 Item의 위험 기준 판정 팝업을 연다.
    page.back_to_list()
    with patch.object(ItemRuleVerdictDialog, "exec", lambda self: 0):
        page.result_buttons[1].rule_badge.click()
    assert page.mode == "list" and page.verdict_dialog.item_id == 1 and page.verdict_dialog.rule.risk_rule_id == 9
    page.verdict_dialog.deleteLater()
    page.result_buttons[1].click()
    assert page.selected_id == 1 and page.application_details.isVisible()
    assert "필수 기능" in page.content_rules.text() and "위험 규칙" not in page.title.text()
    assert "필수 기능" in page.inline_rules.text() and not page.inline_rules.isHidden()
    # '위험 등록된 Item' 필터
    page.registered_only.setChecked(True)
    assert tuple(page.result_buttons) == (1,) and page.count.text() == "검색 결과 1건"
    page.registered_only.setChecked(False)
    assert tuple(page.result_buttons) == (1, 2)
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
    dialog.preview_impact()
    wait_until(lambda: not dialog.request.busy)
    assert dialog.save_button.isEnabled() and dialog.stat_values["new"].text() == "2" and not dialog.stats.isHidden()
    # 메모는 영향 계산과 무관하므로 입력해도 등록 버튼이 꺼지지 않고, 메모 입력 뒤 등록 한 번으로 저장된다.
    dialog.memo.setText("변경")
    assert dialog.save_button.isEnabled()
    # 영향에 쓰이는 값(기준명 등)을 바꾸면 미리보기를 다시 계산하기 전까지 등록할 수 없다.
    dialog.name.setText("안전 필수 변경")
    assert not dialog.save_button.isEnabled() and not dialog.save()
    dialog.preview_impact()
    wait_until(lambda: not dialog.request.busy)
    assert dialog.save_button.isEnabled()
    assert dialog.save() and len(submitted) == 1
    assert not dialog.save() and len(submitted) == 1  # 같은 창의 중복 저장 금지
    assert submitted[0]["item_manager"]["memo"] == "변경"
    cancel = ItemRiskDialog(None, data, gateway, lambda: None, lambda v: submitted.append(v))
    cancel.reject()
    assert len(submitted) == 1


def test_selected_item_and_preview_appear_without_search_or_preview_click(app, data):
    calls = []
    impact = SimpleNamespace(problems=(), base_date=data.base_date, newly_risky=1, hpa_excluded=0,
                             saveable=True, count=lambda _: 1)
    def preview(values, context):
        calls.append(values)
        return impact
    dialog = ItemRiskDialog(None, data, SimpleNamespace(preview_risk_rule=preview), lambda: None,
                            lambda _: False, family_id=10, item_id=2)
    dialog.show()
    wait_until(lambda: dialog.preview_valid)
    assert dialog.item_editor.isHidden() and not dialog.item_search.isVisible()
    assert data.versions.items[1].item.content in dialog.item_note.toolTip()
    assert len(calls) == 1 and calls[0]["item_id"] == 2
    assert "등록하면" in dialog.save_button.toolTip() and dialog.advanced.isHidden()
    dialog.change_item.click()
    assert dialog.item_search.isVisible()
    dialog.item.setCurrentIndex(dialog.item.findData(1))
    wait_until(lambda: dialog.preview_valid)
    assert calls[-1]["item_id"] == 1 and dialog.save_button.isEnabled()  # 확인란 없이 계산 완료 즉시 등록 가능
    dialog.name.clear()
    wait_until(lambda: dialog.next_step.text() == "위험 기준명을 입력하세요.")
    assert not dialog.save_button.isEnabled()
    dialog.reject()


def test_changing_criterion_keeps_name_and_category_follows_criterion(app, data):
    """판정 기준을 바꿔도 기준명이 비지 않고, 구분은 판정 기준에서 정한다 (11-11 점검)."""
    calls = []
    impact = SimpleNamespace(problems=(), base_date=data.base_date, newly_risky=0, hpa_excluded=0,
                             saveable=True, count=lambda _: 0)
    def preview(values, context):
        calls.append(values)
        return impact
    dialog = ItemRiskDialog(None, data, SimpleNamespace(preview_risk_rule=preview), lambda: None,
                            lambda _: False, family_id=10, item_id=2)
    dialog.show()
    assert not hasattr(dialog, "categories")   # 판정 기준과 겹치던 구분 칩은 없다
    item_name = dialog.name.text()
    assert item_name and dialog.values()["item_manager"]["category"] == "환경안전 필수"
    dialog.criterion.setCurrentIndex(dialog.criterion.findData("DIRECT_VERSION"))
    version = dialog.start.currentText().split(" · ")[0]
    assert "순서" not in dialog.start.currentText()   # 순서가 확인된 버전은 이름만 보인다 (11-11)
    assert dialog.name.text() == f"{dialog.family.currentText()} {version} 사용 위험"
    assert dialog.values()["item_manager"]["category"] == "결함 버전"
    wait_until(lambda: dialog.preview_valid)
    assert dialog.save_button.isEnabled() and calls[-1]["rule_type"] == "DIRECT_VERSION"
    # 대상 버전을 바꾸면 제안한 이름도 따라 바뀐다.
    dialog.start.setCurrentIndex(dialog.start.count() - 1)
    assert dialog.start.currentText().split(" · ")[0] in dialog.name.text()
    dialog.criterion.setCurrentIndex(dialog.criterion.findData("DEFECT_ITEM"))
    assert dialog.name.text() == item_name and dialog.values()["item_manager"]["category"] == "결함 버전"
    wait_until(lambda: dialog.preview_valid)
    assert dialog.save_button.isEnabled() and calls[-1]["rule_type"] == "DEFECT_ITEM"
    # 직접 고친 이름은 판정 기준을 바꿔도 그대로 둔다.
    dialog.name.setText("직접 정한 이름")
    dialog.criterion.setCurrentIndex(dialog.criterion.findData("DIRECT_VERSION"))
    assert dialog.name.text() == "직접 정한 이름"
    dialog.reject()


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


def test_dialog_without_observations_shows_single_clear_empty_state(app, data):
    impact = SimpleNamespace(problems=(), base_date=None, newly_risky=0, hpa_excluded=0, saveable=True, count=lambda _: 0)
    dialog = ItemRiskDialog(None, data, SimpleNamespace(preview_risk_rule=lambda values, context: impact), lambda: None,
                            lambda _: True, family_id=10, item_id=2)
    dialog.show()
    wait_until(lambda: dialog.preview_valid)
    assert dialog.stats.isHidden() and dialog.preview.text() == "설비 관측 자료가 없어 영향 설비를 계산할 수 없습니다."
    assert dialog.save_button.isEnabled()
    assert [c.text() for c in dialog.scope_chips][1:] == ["전체 Site", "전체 Line"]
    assert dialog.item_first_chip.text().startswith("최초 포함 버전") and "미확인인" not in dialog.item_first_chip.text() and dialog.item_first_chip.toolTip() == ""  # 최초 포함 버전을 알면 설명 툴팁이 없다
    dialog.close()


def test_full_detail_without_observations_shows_one_notice_and_no_filters(app, data):
    empty = replace(data, records=(), base_date=None, observations_connected=False)
    window = create_window(item_loader=lambda: empty, chat_enabled=True)
    window.resize(1500, 960)
    window.show()
    window.navigate("items")
    page = window.pages["items"]
    page.open_row(0)
    assert not page.no_data_notice.isHidden() and "설비 관측 자료가 없어" in page.no_data_reason.text()
    assert page.summary.isHidden() and page.filter_row.isHidden() and page.equipment_empty.isHidden()
    assert not hasattr(page, "observation_note") and page.application_details.isVisible()
    window.close()


def test_family_without_releases_shows_notice_and_disables_search(app, data):
    no_release = replace(data, versions=replace(data.versions, releases=(), version_items=()))
    window = create_window(item_loader=lambda: no_release, chat_enabled=True)
    window.resize(1500, 960)
    window.show()
    window.navigate("items")
    page = window.pages["items"]
    assert page.search.placeholderText() == page.SEARCH_PLACEHOLDER
    assert not page.family_notice.isHidden() and "연결된 릴리즈노트 아이템이 없습니다" in page.family_notice_title.text()
    assert not page.search.isEnabled() and not page.registered_only.isEnabled() and page.empty.isHidden()
    assert not hasattr(page, "import_button")
    window.close()


def test_popups_are_frameless_modal_without_duplicate_texts(app, data):
    impact = SimpleNamespace(problems=(), base_date=None, newly_risky=0, hpa_excluded=0, saveable=True, count=lambda _: 0)
    dialog = ItemRiskDialog(None, data, SimpleNamespace(preview_risk_rule=lambda values, context: impact), lambda: None,
                            lambda _: True, family_id=10, item_id=2)
    assert dialog.isModal() and dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    texts = [w.text() for w in dialog.findChildren(QLabel)]
    assert "저장 후 설비별 판정과 대시보드·히트맵 위험 집계에 반영됩니다." not in texts and "관련 릴리즈노트 아이템" not in texts
    assert dialog.title.full == "위험 기준 등록" and dialog.close_x.accessibleName() == "닫기"
    assert dialog.change_item.isVisibleTo(dialog)  # 정해진 Item이 있으면 변경 링크가 보인다
    dialog.close()
