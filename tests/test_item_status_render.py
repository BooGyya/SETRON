"""위험 기준 판정 팝업: 판정 표시가 한 번만, 잘리지 않게 나오고 자료가 없으면 안내 한 블록만 보인다."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest

from setron.features.risk.schemas import MappingStatus, RiskRule, RiskStatus, RuleType
from setron.ui.components import StatusBadge
from setron.ui.item_risk_page import ItemRiskPage
from setron.ui.item_risk_verdicts import ItemRuleVerdictDialog, repeats_item, rule_verdicts

from test_ui_items import app, data


def _rule(rule_id=1, item_id=1, name="필수 Item"):
    return RiskRule(rule_id, RuleType.SAFETY_REQUIRED, 10, name, item_id=item_id, display_name=name,
                    mapping_status=MappingStatus.VERIFIED)


def _close(dialog):
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("width", [640, 780, 1100])
def test_verdict_table_columns_and_status_filter(app, data, width):
    dialog = ItemRuleVerdictDialog(None, replace(data, risk_rules=(_rule(),)), 1, 1)
    dialog.resize(width, 700)
    dialog.show()
    QTest.qWait(30)
    try:
        assert dialog.notice.isHidden() and not dialog.table.isHidden()
        assert dialog.model.columnCount() == 4 and dialog.model.ids[0] == 2
        assert dialog.table.horizontalScrollBar().maximum() == 0
        assert dialog.model.rows[0][2] == "위험 후보" and dialog.model.statuses[0] is RiskStatus.RISK
        assert dialog.tile_buttons[RiskStatus.UNKNOWN].toolTip() == "버전·관측 자료가 없어 위험 여부를 판단할 수 없음"
        assert dialog.tile_values[RiskStatus.RISK].text() == "1대"
        # 판정 대수 타일은 그 판정만 거르고, 한 번 더 누르면 전체로 돌아간다.
        total = dialog.model.rowCount()
        dialog.tile_buttons[RiskStatus.RISK].click()
        assert dialog.model.rowCount() == 1 and set(dialog.model.statuses) == {RiskStatus.RISK}
        dialog.tile_buttons[RiskStatus.RISK].click()
        assert dialog.model.rowCount() == total
        # 판정 근거는 표준 문구를 쓰고 한 줄로 줄여 툴팁으로 전체를 보여 준다.
        assert "필수 Item" in dialog.model.rows[0][3] and dialog.model.index(0, 3).data(Qt.ItemDataRole.ToolTipRole)
        assert dialog.selector.isHidden() and not dialog.rule_name.isHidden()
    finally:
        _close(dialog)


def test_verdict_order_filters_and_rule_selector(app, data):
    rule = _rule()
    records = tuple(replace(data.records[0], equipment_id=i + 1, equipment_code=f"EQ-{i + 1}") for i in range(16))
    dialog = ItemRuleVerdictDialog(None, replace(data, records=records, risk_rules=(rule, replace(rule, risk_rule_id=2, display_name="둘째"))), 1, 2)
    dialog.show()
    try:
        assert not dialog.selector.isHidden() and dialog.rule.risk_rule_id == 2
        ranks = [v[1].status for v in rule_verdicts(dialog.data, dialog.rule)]
        assert ranks == sorted(ranks, key=[RiskStatus.RISK, RiskStatus.UNKNOWN, RiskStatus.NO_RISK].index)
        assert dialog.model.rowCount() == 16
        dialog.site.setCurrentIndex(dialog.site.findData(records[0].site))
        assert dialog.model.rowCount() == 16 and dialog.group.count() > 1
        dialog.selector.setCurrentIndex(0)
        assert dialog.rule.risk_rule_id == 1
    finally:
        _close(dialog)


@pytest.mark.parametrize("connected", [False, True])
def test_verdict_empty_states_show_one_notice_without_filters(app, data, connected):
    empty = replace(data, records=(), base_date=None if not connected else data.base_date,
                    observations_connected=connected, risk_rules=(_rule(),))
    dialog = ItemRuleVerdictDialog(None, empty, 1, 1)
    dialog.show()
    try:
        assert not dialog.notice.isHidden() and dialog.table.isHidden() and dialog.filters.isHidden() and dialog.tiles.isHidden()
        assert dialog.notice_text.text() == ("이 위험 기준이 적용되는 설비가 없습니다." if connected
                                             else "설비 관측 자료가 없어 판정할 설비가 없습니다.")
        assert not dialog.condition_box.isHidden() and dialog.close_button.isVisible() and dialog.source_button.isVisible()
    finally:
        _close(dialog)


def test_rule_name_is_hidden_when_it_repeats_item_title_and_title_is_not_duplicated(app, data):
    content = next(e.item.content for e in data.versions.items if e.item.item_id == 1)
    assert repeats_item(content[:250], content) and repeats_item(content[:5] + "…", content) and repeats_item("", content)
    assert not repeats_item("전혀 다른 기준 이름", content)
    same = ItemRuleVerdictDialog(None, replace(data, risk_rules=(_rule(name=content[:250]),)), 1, 1)
    other = ItemRuleVerdictDialog(None, replace(data, risk_rules=(_rule(name="전혀 다른 기준 이름"),)), 1, 1)
    same.show(), other.show()
    try:
        assert same.rule_name.isHidden() and not other.rule_name.isHidden()
        assert same.title.full == content and same.title.lines == 2
        # 빈 상태 박스는 점선 테두리로 조건 박스와 구분한다.
        assert "dashed" in same.notice.styleSheet() and "dashed" not in same.condition_box.styleSheet()
    finally:
        _close(same), _close(other)


def test_risk_page_only_keeps_storage_boundary(app, data):
    page = ItemRiskPage()
    assert not page.open_add()
    page.set_data(data)
    assert not page.open_add() and page.data is data


def test_unknown_first_version_chip_explains_when_it_happens(app, data):
    unlinked = replace(data, versions=replace(data.versions, version_items=()), risk_rules=(_rule(),))
    dialog = ItemRuleVerdictDialog(None, unlinked, 1, 1)
    chip = next(c for c in dialog.findChildren(StatusBadge) if c.text().startswith("최초 포함 버전"))
    assert chip.text() == "최초 포함 버전 미확인" and "확정하지 못했습니다" in chip.toolTip()
    known = ItemRuleVerdictDialog(None, replace(data, risk_rules=(_rule(),)), 1, 1)
    chip = next(c for c in known.findChildren(StatusBadge) if c.text().startswith("최초 포함 버전"))
    assert chip.toolTip() == "" and chip.text() != "최초 포함 버전 미확인"


def test_popup_is_frameless_modal_and_escape_closes(app, data):
    dialog = ItemRuleVerdictDialog(None, replace(data, risk_rules=(_rule(),)), 1, 1)
    assert dialog.isModal() and dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    dialog.show()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()

