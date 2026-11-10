"""릴리즈노트 유무와 무관하게 상태 글자를 한 번만, 잘리지 않게 표시한다."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest

from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
from setron.ui.item_risk_page import ItemRiskPage
from setron.ui.items import _style_raw_table

from test_ui_items import app, data


@pytest.mark.parametrize("width", [960, 1100, 1600])
@pytest.mark.parametrize("with_rule", [False, True])
def test_status_cells_have_one_painter_and_enough_space(app, data, width, with_rule):
    rules = (RiskRule(1, RuleType.SAFETY_REQUIRED, 10, "필수 Item", item_id=1,
                      mapping_status=MappingStatus.VERIFIED),) if with_rule else ()
    page = ItemRiskPage()
    _style_raw_table(page.table)
    page.resize(width, 800)
    page.set_data(replace(data, risk_rules=rules))
    page.show()
    QTest.qWait(30)
    try:
        if not with_rule:
            assert page.detail_card.isHidden() and page.no_rules.isVisible()
            return
        page.select_rule(1)
        QTest.qWait(30)
        assert page.model.ids == (2,)
        geometry = page.table.geometry()
        for tab in (1, 2, 0):
            page.status_filter.setCurrentIndex(tab)
            QTest.qWait(30)
            assert page.table.geometry() == geometry
            assert page.table.horizontalScrollBar().maximum() == 0
        assert page.model.columnCount() == 5
        reason = page.model.index(0, 4)
        assert page.table.indexWidget(reason) is None
        assert reason.data(Qt.ItemDataRole.AccessibleTextRole) == reason.data()
        page.search.setText("EQ-2")
        assert page.model.rowCount() == 1
        page.search.clear()
        page.resize(width - 40, 760)
        QTest.qWait(20)
        assert page.table.horizontalScrollBar().maximum() == 0
    finally:
        page.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

def test_rule_drilldown_and_page_controls(app, data):
    rule = RiskRule(1, RuleType.SAFETY_REQUIRED, 10, "첫 기준", item_id=1)
    records = tuple(replace(data.records[0], equipment_id=i+1, equipment_code=f"EQ-{i+1}") for i in range(16))
    page = ItemRiskPage()
    page.set_data(replace(data, records=records, risk_rules=(rule, replace(rule, risk_rule_id=2))))
    page.show()
    try:
        for rid in (1, 2):
            page.rule_buttons[rid].click()
            assert page.rule_rows.indexOf(page.detail_card) == page.rule_rows.indexOf(page.rule_buttons[rid]) + 1
            assert not page.condition.isVisible() and not page.source_button.isVisible()
            assert page.pager.isVisible() and page.page_label.text() == "1 / 2"
            page.next.click()
            assert page.model.rowCount() == 1 and page.page_label.text() == "2 / 2"
            page.info_toggle.click()
            assert page.condition.isVisible() and page.source_button.isVisible()
        page.filter_toggle.click()
        page.search.setText("EQ-16")
        assert page.model.rowCount() == 1 and page.pager.isHidden()
        page.rule_buttons[2].click()
        assert page.detail_card.isHidden()
    finally:
        page.close()
