"""위험 버전의 세 비교 조건과 기존 Item 사용·미사용 기준 저장 호환성."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.schemas import ItemInput, ItemLinkInput
from setron.features.release.service import ReleaseService
from setron.features.risk.schemas import MappingStatus, RiskRule, RiskStatus, RuleType, criterion_label
from setron.ui.item_risk_dialog import ItemRiskDialog, applied_rule
from setron.ui.item_risk_verdicts import ItemRuleVerdictDialog, category, rule_verdicts

from test_ui_items import app, data
from test_release_revision import revision_storage, all_rows
from test_management_service import service_for
from test_item_manager_ver5 import wait_until

ORDERS = {1: 1, 2: 2, 3: None, 4: 3}   # test_ui_items 계열 10: v99, v2, v1000(순서 미확인), v3


def _close(dialog):
    dialog.reject() if isinstance(dialog, ItemRiskDialog) else dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _dialog(data, calls):
    impact = SimpleNamespace(problems=(), base_date=data.base_date, newly_risky=1, hpa_excluded=0, saveable=True,
                             count=lambda _: 1)
    submitted = []
    gateway = SimpleNamespace(preview_risk_rule=lambda values, context: calls.append(values) or impact)
    dialog = ItemRiskDialog(None, data, gateway, lambda: None, lambda values: submitted.append(values) or True,
                            family_id=10, item_id=1)
    dialog.submitted_values = submitted
    return dialog


def test_criterion_dropdown_has_three_strict_version_relations(app, data):
    dialog = _dialog(data, [])
    try:
        assert [dialog.criterion.itemText(i) for i in range(dialog.criterion.count())] == [
            "v99 미만 버전", "v99 동일 버전", "v99 초과 버전"]
        assert not dialog.version_fields.isHidden()
        assert dialog.values()["rule_type"] == "VERSION_BELOW" and dialog.values()["item_id"] == 1
        assert not hasattr(dialog, "version") and not hasattr(dialog, "end")
        assert dialog.start.currentData() == 1
    finally:
        _close(dialog)


@pytest.mark.parametrize("inputs, rule_type, fields", [
    ((2, None, None), "DIRECT_VERSION", {"release_version_id": 2}),
    ((None, 1, None), "DEFECT_ITEM", {"start_release_version_id": 1, "end_release_version_id": None}),
    ((None, 1, 4), "DEFECT_ITEM", {"start_release_version_id": 1, "end_release_version_id": 4}),
])
def test_applied_inputs_map_to_existing_rule_types(inputs, rule_type, fields):
    assert applied_rule(*inputs, ORDERS.get) == (rule_type, fields, "")


@pytest.mark.parametrize("inputs, reason", [
    ((None, None, None), "위험 버전 또는 시작 버전을 선택하세요."),
    ((None, None, 4), "해제 버전은 시작 버전과 함께 지정하세요."),
    ((2, 1, None), "함께 지정할 수 없습니다"),
    ((2, None, 4), "함께 지정할 수 없습니다"),
    ((None, 4, 1), "해제 버전은 시작 버전보다 뒤의 버전이어야 합니다."),
    ((None, 2, 2), "해제 버전은 시작 버전보다 뒤의 버전이어야 합니다."),
])
def test_ambiguous_or_reversed_inputs_are_refused_with_reason(inputs, reason):
    rule_type, fields, problem = applied_rule(*inputs, ORDERS.get)
    assert rule_type is None and fields == {} and reason in problem


@pytest.mark.parametrize("rule_type, reference", [
    ("VERSION_BELOW", 2), ("DIRECT_VERSION", 1), ("VERSION_ABOVE", 4),
])
def test_dialog_version_relations_preview_and_save_current_item(app, data, rule_type, reference):
    calls = []
    dialog = _dialog(data, calls)
    dialog.show()
    try:
        dialog.criterion.setCurrentIndex(dialog.criterion.findData(rule_type))
        dialog.start.setCurrentIndex(dialog.start.findData(reference))
        wait_until(lambda: dialog.preview_valid)
        assert calls[-1]["rule_type"] == rule_type and dialog.save_button.isEnabled()
        assert dialog.save()
        saved = dialog.submitted_values[0]
        assert saved["rule_type"] == rule_type and saved["release_version_id"] == reference
        assert saved["item_id"] == 1 and saved["family_id"] == 10
        assert saved["item_manager"]["category"] == "결함 버전"
        assert "start_release_version_id" not in saved and "end_release_version_id" not in saved
    finally:
        _close(dialog)


def test_dialog_requires_reference_version_before_preview_or_save(app, data):
    calls = []
    dialog = _dialog(data, calls)
    dialog.show()
    try:
        dialog.start.setCurrentIndex(-1)
        wait_until(lambda: dialog.next_step.text() == "기준 버전을 선택하세요.")
        assert not calls and not dialog.save_button.isEnabled() and not dialog.save()
        assert dialog.values()["release_version_id"] is None and not dialog.submitted_values
    finally:
        _close(dialog)


@pytest.mark.parametrize("pick, rule_type", [("direct", "DIRECT_VERSION"), ("range", "DEFECT_ITEM")])
def test_mapped_values_save_through_management_service(revision_storage, pick, rule_type):
    service, _, first, second = service_for(revision_storage)
    releases = ReleaseService(revision_storage)
    item_id = releases.register_item(first, ItemInput(content="결함 기능")).current["item_id"]
    releases.save_item_links(first, (ItemLinkInput(item_id=item_id, item_state=ItemState.APPLIED, target_scope=TargetScope.ALL),))
    mapped_type, fields, problem = applied_rule(second, None, None) if pick == "direct" else applied_rule(None, first, second)
    assert not problem and mapped_type == rule_type
    values = dict(rule_type=mapped_type, family_id=1, risk_reason="사용 위험", mapping_status="VERIFIED", **fields,
                  item_manager=dict(schema=1, name="사용 위험", category="결함 버전", memo="", model_ids=[1], site=None, line=None))
    if mapped_type == "DEFECT_ITEM":
        values["item_id"] = item_id
    assert service.preview_risk_rule(values).saveable
    service.create_risk_rule(values)
    row = all_rows(revision_storage, "risk_rule")[0]
    assert row["rule_type"] == rule_type
    assert all(row[key] == value for key, value in fields.items())


def test_existing_rules_show_two_names_and_keep_verdicts(app, data):
    """이미 저장된 DIRECT_VERSION·DEFECT_ITEM은 'Item 사용 시 위험', SAFETY_REQUIRED는 '미적용 시 위험'으로 보이고 판정은 그대로다."""
    direct = RiskRule(1, RuleType.DIRECT_VERSION, 10, "v2 사용 위험", item_id=1, release_version_id=2,
                      mapping_status=MappingStatus.VERIFIED, category="결함 버전")
    defect = RiskRule(2, RuleType.DEFECT_ITEM, 10, "결함 범위", item_id=1, start_release_version_id=1,
                      end_release_version_id=4, mapping_status=MappingStatus.VERIFIED, category="공정 위험")
    safety = RiskRule(3, RuleType.SAFETY_REQUIRED, 10, "필수", item_id=1, mapping_status=MappingStatus.VERIFIED)
    assert [criterion_label(r.rule_type) for r in (direct, defect, safety)] == [
        "Item 사용 시 위험", "Item 사용 시 위험", "필수 Item 미적용 시 위험"]
    risky = {rule.risk_rule_id: sorted(r.equipment_code for r, result, _ in rule_verdicts(data, rule)
                                       if result.status is RiskStatus.RISK) for rule in (direct, defect)}
    # v2를 쓰는 EQ-2 / 결함 Item이 모델 A에 포함된 v99를 쓰는 EQ-1 (EQ-4는 모델 B라 대상 밖)
    assert risky == {1: ["EQ-2"], 2: ["EQ-1"]}
    for rule in (direct, defect, safety):
        dialog = ItemRuleVerdictDialog(None, replace(data, risk_rules=(rule,)), 1, rule.risk_rule_id)
        try:
            assert dialog.kind_chip.text() == category(rule)
        finally:
            _close(dialog)
    # 구형 호출의 범위 대응도 유지하며 새 등록 화면에서 구형 조건을 생성하지 않는다.
    kind, fields, problem = applied_rule(None, 1, 4, ORDERS.get)
    assert kind == "DEFECT_ITEM" and not problem
    assert replace(defect, **fields) == defect
