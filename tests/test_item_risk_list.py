"""위험 기준 목록 선택·상태·계열 범위와 목록에서 상세로 이어지는 UI 계약."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtTest import QTest

from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
from setron.features.version.schemas import ReleaseVersion
from setron.ui.app import create_window
from setron.ui.item_risk_list import ItemRuleListDialog
from setron.ui.item_risk_verdicts import ItemRuleVerdictDialog
from setron.ui.version_data import ModelFamily, ReleaseEntry

from test_ui_items import app, data  # noqa: F401


@pytest.fixture
def own(app):
    widgets = []

    def show(widget):
        widgets.append(widget)
        widget.show()
        QTest.qWait(15)
        return widget

    yield show
    for widget in reversed(widgets):
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture
def rules_data(data):
    versions = replace(data.versions,
        pairs=(*data.versions.pairs, ModelFamily(1, "모델 A", 20, "SW B")),
        releases=(*data.versions.releases, ReleaseEntry(ReleaseVersion(20, 20, "b1", 1), (1,))))
    below = RiskRule(21, RuleType.VERSION_BELOW, 10, "미만 위험", item_id=1,
                     release_version_id=4, mapping_status=MappingStatus.VERIFIED, display_name="낮은 버전 기준")
    equal = replace(below, risk_rule_id=22, rule_type=RuleType.DIRECT_VERSION,
                    release_version_id=2, display_name="같은 버전 기준")
    above = replace(below, risk_rule_id=23, rule_type=RuleType.VERSION_ABOVE,
                    family_id=20, release_version_id=20, display_name="다른 계열 기준")
    disabled = replace(equal, risk_rule_id=20, is_enabled=False, release_version_id=1, display_name="해제 기준")
    other_item = replace(equal, risk_rule_id=99, item_id=2, display_name="다른 Item 기준")
    return replace(data, versions=versions, risk_rules=(disabled, other_item, above, equal, below))


@pytest.mark.parametrize("family,ids", [(None, (21, 22, 23, 20)), (10, (21, 22, 20)), (20, (23,))])
def test_list_keeps_disabled_rules_and_filters_only_selected_item_and_family(own, rules_data, family, ids):
    dialog = own(ItemRuleListDialog(None, rules_data, 1, family_id=family))
    assert tuple(rule.risk_rule_id for rule in dialog.rules) == dialog.model.ids == ids
    expected = {
        21: ("낮은 버전 기준", "v3", "버전 미만", "적용됨"),
        22: ("같은 버전 기준", "v2", "버전 일치", "적용됨"),
        23: ("다른 계열 기준", "b1", "버전 초과", "적용됨"),
        20: ("해제 기준", "v99", "버전 일치", "해제됨"),
    }
    assert dialog.model.headings == ("위험 기준명", "기준 버전", "판정 기준", "상태")
    assert dialog.model.rows == tuple(expected[identifier] for identifier in ids)
    assert dialog.count.text() == f"등록된 위험 기준 {len(ids)}건 · 적용됨 {sum(i != 20 for i in ids)}건"
    assert dialog.close_x is None and dialog.selected_rule_id is None
    assert dialog.table.isVisible() and dialog.notice.isHidden()
    assert dialog.table.horizontalHeader().defaultAlignment() == Qt.AlignmentFlag.AlignCenter
    assert all(dialog.model.index(row, column).data(Qt.ItemDataRole.TextAlignmentRole) == Qt.AlignmentFlag.AlignCenter
               for row in range(len(ids)) for column in range(4))


@pytest.mark.parametrize("selected", [21, 20])
def test_single_row_click_accepts_exact_active_or_disabled_rule(own, rules_data, selected):
    dialog = own(ItemRuleListDialog(None, rules_data, 1))
    index = dialog.model.index(dialog.model.ids.index(selected), 1)
    dialog.table.scrollTo(index)
    QTest.mouseClick(dialog.table.viewport(), Qt.MouseButton.LeftButton, pos=dialog.table.visualRect(index).center())
    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.selected_rule_id == selected


def test_enter_chooses_keyboard_selected_row_instead_of_first_rule(own, rules_data):
    dialog = own(ItemRuleListDialog(None, rules_data, 1))
    dialog.table.setFocus()
    QTest.keyClick(dialog.table, Qt.Key.Key_Down)
    assert dialog.table.currentIndex().row() == 1
    QTest.keyClick(dialog.table, Qt.Key.Key_Return)
    assert dialog.result() == dialog.DialogCode.Accepted and dialog.selected_rule_id == 22


@pytest.mark.parametrize("item,family", [(3, None), (1, 999)])
def test_empty_list_has_visible_notice_and_close_without_selection(own, rules_data, item, family):
    dialog = own(ItemRuleListDialog(None, rules_data, item, family_id=family))
    assert not dialog.rules and not dialog.model.rows and dialog.table.isHidden()
    assert dialog.notice.isVisible() and "등록된 위험 기준이 없습니다" in dialog.notice.text()
    assert "0건" in dialog.count.text() and dialog.selected_rule_id is None
    assert dialog.rect().contains(QRect(dialog.close_button.mapTo(dialog, QPoint()), dialog.close_button.size()))
    QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
    assert dialog.result() == dialog.DialogCode.Rejected and dialog.selected_rule_id is None


@pytest.mark.parametrize("state", ["active", "disabled", "unconnected"])
def test_list_and_detail_share_size_even_without_judged_equipment(own, rules_data, state):
    rule = next(rule for rule in rules_data.risk_rules if rule.risk_rule_id == 21)
    if state == "disabled":
        rule = replace(rule, is_enabled=False)
    value = replace(rules_data, risk_rules=(rule,))
    if state == "unconnected":
        value = replace(value, records=(), base_date=None, observations_connected=False)
    listing = own(ItemRuleListDialog(None, value, 1, family_id=10))
    detail = own(ItemRuleVerdictDialog(None, value, 1, rule.risk_rule_id))
    assert not hasattr(detail, "selector") and detail.rule.risk_rule_id == rule.risk_rule_id
    assert listing.size() == detail.size()
    available = listing.screen().availableGeometry()
    assert listing.width() == min(780, available.width() - 32)
    assert listing.height() == min(700, available.height() - 60)
    assert listing.close_x is None and detail.close_x is None
    for dialog in (listing, detail):
        assert dialog.rect().contains(QRect(dialog.close_button.mapTo(dialog, QPoint()), dialog.close_button.size()))
    if state != "active":
        assert detail.table.isHidden() and detail.notice.isVisible()


@pytest.mark.parametrize("kind,caption", [(RuleType.VERSION_BELOW, "버전 미만"),
                                         (RuleType.DIRECT_VERSION, "버전 일치"),
                                         (RuleType.VERSION_ABOVE, "버전 초과")])
def test_detail_uses_same_short_comparison_caption_as_list(own, rules_data, kind, caption):
    rule = replace(rules_data.risk_rules[-1], rule_type=kind)
    value = replace(rules_data, risk_rules=(rule,))
    listing = own(ItemRuleListDialog(None, value, 1))
    detail = own(ItemRuleVerdictDialog(None, value, 1, rule.risk_rule_id))
    assert listing.model.rows[0][2] == detail.condition_text.text() == caption
    assert detail.reference_text.text() == "v3"


@pytest.mark.parametrize("expanded", [False, True])
def test_risk_count_badge_does_not_toggle_row_or_receive_tab_focus(own, rules_data, expanded):
    window = own(create_window(item_loader=lambda: rules_data))
    window.navigate("items")
    QTest.qWait(20)
    page = window.pages["items"]
    row = page.result_buttons[1]
    if expanded:
        row.click()
    badge = row.rule_badge
    assert badge.focusPolicy() == Qt.FocusPolicy.NoFocus
    before = page.selected_id, page.mode, row.isChecked(), page.detail_view.isVisible()
    QTest.mouseClick(badge, Qt.MouseButton.LeftButton)
    QTest.mouseDClick(badge, Qt.MouseButton.LeftButton)
    assert (page.selected_id, page.mode, row.isChecked(), page.detail_view.isVisible()) == before
    assert page.rule_list_dialog is None and page.verdict_dialog is None
    row.setFocus()
    QTest.keyClick(row, Qt.Key.Key_Tab)
    assert not badge.hasFocus()
