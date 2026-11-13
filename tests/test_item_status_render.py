"""위험 기준 판정 팝업: 판정 표시가 한 번만, 잘리지 않게 나오고 자료가 없으면 안내 한 블록만 보인다."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest

from setron.features.risk.schemas import FindingBasis, MappingStatus, RiskRule, RiskStatus, RuleType
from setron.ui.components import StatusBadge
from setron.ui.item_risk_page import ItemRiskPage
from setron.ui.item_risk_verdicts import ItemRuleVerdictDialog, rule_verdicts

from test_ui_items import app, data


def _rule(rule_id=1, item_id=1, name="필수 Item"):
    return RiskRule(rule_id, RuleType.SAFETY_REQUIRED, 10, name, item_id=item_id, display_name=name,
                    mapping_status=MappingStatus.VERIFIED)


def _close(dialog):
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("width", [640, 780, 1100])
def test_verdict_table_preserves_statuses_versions_and_scope_filters(app, data, width):
    dialog = ItemRuleVerdictDialog(None, replace(data, risk_rules=(_rule(),)), 1, 1)
    dialog.resize(width, 700)
    dialog.show()
    QTest.qWait(30)
    try:
        assert dialog.notice.isHidden() and not dialog.table.isHidden()
        assert dialog.model.headings == ("설비", "위치", "판정", "SW 버전") and dialog.model.ids[0] == 2
        assert dialog.table.horizontalScrollBar().maximum() == 0
        assert dialog.model.rows[0][2] == "위험 후보" and dialog.model.statuses[0] is RiskStatus.RISK
        assert dialog.target_count.text() == "위험 적용 대상 1건"
        assert not hasattr(dialog, "tile_buttons") and not hasattr(dialog, "tile_values")
        unknown_row = dialog.model.statuses.index(RiskStatus.UNKNOWN)
        assert dialog.model.index(unknown_row, 2).data(Qt.ItemDataRole.ToolTipRole) == \
            "버전·관측 자료가 없어 위험 여부를 판단할 수 없음"
        assert dialog.model.rows[0][3] == "v2" and dialog.model.index(0, 3).data(Qt.ItemDataRole.ToolTipRole) == "v2"
        finding = dialog.verdicts[0][1].findings[0]
        assert finding.status is RiskStatus.RISK and finding.basis is FindingBasis.SAFETY_NOT_APPLIED
        assert finding.rule.item_id == 1 and "필수 Item" in finding.message
        # 상태 카드는 없지만 설비군 필터는 판정과 적용 대상 수를 함께 좁힌다.
        total = dialog.model.rowCount()
        dialog.group.setCurrentIndex(dialog.group.findData(2))
        assert dialog.model.ids == (4,) and dialog.model.statuses == (RiskStatus.NO_RISK,)
        assert dialog.target_count.text() == "위험 적용 대상 0건"
        dialog.group.setCurrentIndex(0)
        assert dialog.model.rowCount() == total and dialog.target_count.text() == "위험 적용 대상 1건"
        assert dialog.model.statuses == tuple(result.status for _, result, _ in dialog.verdicts)
        assert not hasattr(dialog, "selector") and not dialog.rule_name.isHidden()
    finally:
        _close(dialog)


@pytest.mark.parametrize("selected_rule", [1, 2])
def test_verdict_order_and_scope_filters_preserve_constructor_selected_rule(app, data, selected_rule):
    rule = _rule()
    records = tuple(replace(data.records[0], equipment_id=i + 1, equipment_code=f"EQ-{i + 1}") for i in range(16))
    dialog = ItemRuleVerdictDialog(None, replace(data, records=records, risk_rules=(rule, replace(rule, risk_rule_id=2, display_name="둘째"))), 1, selected_rule)
    dialog.show()
    try:
        assert not hasattr(dialog, "selector") and dialog.rule.risk_rule_id == selected_rule
        assert dialog.rule_name.text() == ("필수 Item" if selected_rule == 1 else "둘째")
        ranks = [v[1].status for v in rule_verdicts(dialog.data, dialog.rule)]
        assert ranks == sorted(ranks, key=[RiskStatus.RISK, RiskStatus.UNKNOWN, RiskStatus.NO_RISK].index)
        assert dialog.model.rowCount() == 16
        dialog.site.setCurrentIndex(dialog.site.findData(records[0].site))
        assert dialog.model.rowCount() == 16 and dialog.group.count() > 1
        dialog.group.setCurrentIndex(dialog.group.findData(1))
        assert dialog.model.rowCount() == 16 and dialog.rule.risk_rule_id == selected_rule
    finally:
        _close(dialog)


@pytest.mark.parametrize("connected", [False, True])
def test_verdict_empty_states_show_one_notice_without_filters(app, data, connected):
    empty = replace(data, records=(), base_date=None if not connected else data.base_date,
                    observations_connected=connected, risk_rules=(_rule(),))
    dialog = ItemRuleVerdictDialog(None, empty, 1, 1)
    dialog.show()
    try:
        assert not dialog.notice.isHidden() and dialog.table.isHidden() and dialog.filters.isHidden()
        assert not dialog.verdicts and dialog.model.rowCount() == 0
        assert dialog.target_count.text() == "위험 적용 대상 0건" and not hasattr(dialog, "tiles")
        assert dialog.notice_text.text() == ("이 위험 기준이 적용되는 설비가 없습니다." if connected
                                             else "설비 관측 자료가 없어 판정할 설비가 없습니다.")
        assert not dialog.condition_box.isHidden() and dialog.close_button.isVisible()
        assert not hasattr(dialog, "source_button") and not hasattr(dialog, "dashboard_button")
    finally:
        _close(dialog)


def test_rule_name_stays_in_explanation_even_when_it_repeats_item_title(app, data):
    content = next(e.item.content for e in data.versions.items if e.item.item_id == 1)
    same = ItemRuleVerdictDialog(None, replace(data, risk_rules=(_rule(name=content[:250]),)), 1, 1)
    other = ItemRuleVerdictDialog(None, replace(data, risk_rules=(_rule(name="전혀 다른 기준 이름"),)), 1, 1)
    same.show(), other.show()
    try:
        assert not same.rule_name.isHidden() and not other.rule_name.isHidden()
        assert same.rule_name.text() == content[:250] and other.rule_name.text() == "전혀 다른 기준 이름"
        assert same.rule_name.textFormat() == Qt.TextFormat.PlainText
        assert same.title.full == content and same.title.lines == 2
        assert same.reference_text.text() == "별도 기준 버전 없음" and "필수 Item" in same.condition_text.text()
        assert "border:0" in same.notice.styleSheet() and "dashed" not in same.notice.styleSheet()
    finally:
        _close(same), _close(other)


def test_risk_page_only_keeps_storage_boundary(app, data):
    page = ItemRiskPage()
    assert not page.open_add()
    page.set_data(data)
    assert not page.open_add() and page.data is data


def test_missing_item_history_remains_unknown_without_reintroducing_context_chips(app, data):
    unlinked = replace(data, versions=replace(data.versions, version_items=()), risk_rules=(_rule(),))
    dialog = ItemRuleVerdictDialog(None, unlinked, 1, 1)
    known = ItemRuleVerdictDialog(None, replace(data, risk_rules=(_rule(),)), 1, 1)
    try:
        assert set(dialog.model.ids) == {1, 2, 3, 4, 5} and set(dialog.model.statuses) == {RiskStatus.UNKNOWN}
        assert dialog.target_count.text() == "위험 적용 대상 0건"
        assert all(result.findings and all(finding.basis is FindingBasis.SAFETY_UNKNOWN for finding in result.findings)
                   for _, result, _ in dialog.verdicts)
        assert known.target_count.text() == "위험 적용 대상 1건"
        assert {record.equipment_id for record, result, _ in known.verdicts if result.status is RiskStatus.RISK} == {2}
        assert not dialog.findChildren(StatusBadge) and not known.findChildren(StatusBadge)
    finally:
        _close(dialog), _close(known)


def test_popup_is_frameless_modal_and_escape_closes(app, data):
    dialog = ItemRuleVerdictDialog(None, replace(data, risk_rules=(_rule(),)), 1, 1)
    assert dialog.isModal() and dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    dialog.show()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()

