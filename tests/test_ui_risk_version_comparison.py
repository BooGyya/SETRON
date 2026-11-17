"""버전 비교 위험 기준의 관리 입력·저장 후 판정 표시를 검증한다."""

from dataclasses import replace

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox

from setron.features.risk.schemas import FindingBasis, MappingStatus, RiskRule, RiskStatus, RuleType
from setron.ui.item_risk_verdicts import ItemRuleVerdictDialog
from setron.ui.management_page import ValueEditor
from setron.ui.risk_rule_dialog import RiskRuleDialog
from test_ui_items import app, data  # noqa: F401


@pytest.mark.parametrize("rule_type", [RuleType.VERSION_BELOW, RuleType.DIRECT_VERSION, RuleType.VERSION_ABOVE])
def test_management_editor_and_registration_preserve_version_comparison(app, data, rule_type):
    references = {
        "version_family": ({"family_id": 10, "family_name": "SW A"},),
        "release_version": tuple({"release_version_id": entry.release.release_version_id,
                                  "family_id": 10, "version_raw": entry.release.version_raw,
                                  "version_order": entry.release.version_order} for entry in data.versions.releases),
        "release_version_item": (),
        "item": (),
    }
    editor = ValueEditor("risk_rule", "rule_type", rule_type.value, references)
    dialog = RiskRuleDialog(None, references, family_id=10)
    try:
        assert editor.value() == rule_type.value and editor.editor.currentIndex() >= 0
        dialog.rule_type.setCurrentIndex(dialog.rule_type.findData(rule_type.value))
        dialog.combos["release_version_id"].setCurrentIndex(dialog.combos["release_version_id"].findData(2))
        dialog.reason.setPlainText("버전 비교 위험")
        dialog.verified.setChecked(True)
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        assert not dialog.combos["release_version_id"].isHidden()
        assert dialog.combos["item_id"].isHidden() and dialog.combos["start_release_version_id"].isHidden()
        assert dialog.values() == {"rule_type": rule_type.value, "family_id": 10,
                                   "release_version_id": 2, "risk_reason": "버전 비교 위험", "mapping_status": "VERIFIED"}
        if rule_type is not RuleType.DIRECT_VERSION:
            assert dialog.rows["release_version_id"][0].text() == "기준 버전"
    finally:
        editor.deleteLater()
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("rule_type, phrase, risky_ids, unknown_ids", [
    (RuleType.VERSION_BELOW, "미만", {1, 4}, {3}),
    (RuleType.DIRECT_VERSION, "일치", {2}, set()),
    (RuleType.VERSION_ABOVE, "초과", {5}, {3}),
])
def test_saved_item_comparison_displays_reference_and_real_equipment_verdicts(app, data, rule_type, phrase, risky_ids, unknown_ids):
    rule = RiskRule(9, rule_type, 10, "버전 비교 위험", item_id=1, release_version_id=2,
                    mapping_status=MappingStatus.VERIFIED)
    dialog = ItemRuleVerdictDialog(None, replace(data, risk_rules=(rule,)), 1, rule_id=9)
    try:
        dialog.show()
        QTest.qWait(10)
        assert dialog.rule is rule and dialog.rule_name.text() == "버전 비교 위험" and not dialog.rule_name.isHidden()
        assert dialog.reference_text.text() == "v2" and dialog.condition_text.text() == f"버전 {phrase}"
        assert dialog.target_count.text() == f"위험 적용 대상 {len(risky_ids)}건"
        actual = dict(zip(dialog.model.ids, dialog.model.statuses))
        assert {eid for eid, status in actual.items() if status is RiskStatus.RISK} == risky_ids
        assert {eid for eid, status in actual.items() if status is RiskStatus.UNKNOWN} == unknown_ids
        assert {record.equipment_id: result.status for record, result, _ in dialog.verdicts} == actual
        basis = {RuleType.VERSION_BELOW: FindingBasis.VERSION_BELOW,
                 RuleType.DIRECT_VERSION: FindingBasis.DIRECT_VERSION,
                 RuleType.VERSION_ABOVE: FindingBasis.VERSION_ABOVE}[rule_type]
        assert all(finding.basis is basis and finding.rule.release_version_id == 2
                   for _, result, _ in dialog.verdicts for finding in result.findings if finding.status is RiskStatus.RISK)
        assert {eid: row[3] for eid, row in zip(dialog.model.ids, dialog.model.rows)} == {
            1: "v99", 2: "v2", 3: "v1000", 4: "v99", 5: "v3"}
        assert 6 not in dialog.model.ids  # HPA는 버전 비교의 위험 설비 수에 포함하지 않는다.
    finally:
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()
