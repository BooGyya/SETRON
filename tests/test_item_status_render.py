"""릴리즈노트 유무와 무관하게 상태 글자를 한 번만, 잘리지 않게 표시한다."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest

from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
from setron.ui.item_risk_page import ItemRiskPage, ItemStatusDelegate
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
        assert isinstance(page.table.itemDelegateForColumn(5), ItemStatusDelegate)
        assert page.model.rowCount() == 5
        expected = {"위험 후보", "적용 완료", "미확인", "대상 외"} if with_rule else {"위험 기준 없음"}
        assert {row[5] for row in page.model.rows} == expected
        for row in range(page.model.rowCount()):
            index = page.model.index(row, 5)
            # 위젯 배지와 기본 셀 텍스트를 겹쳐 놓지 않는다.
            assert page.table.indexWidget(index) is None
            text = index.data()
            required = page.status_delegate.required_size(text)
            assert page.table.columnWidth(5) >= required.width()
            assert page.table.rowHeight(row) >= required.height()
            assert index.data(Qt.ItemDataRole.AccessibleTextRole) == text
            page.table.selectRow(row)
            page.table.viewport().repaint()
        page.search.setText("EQ-1")
        assert page.model.rowCount() == 1
        page.search.clear()
        page.resize(width - 40, 760)
        QTest.qWait(20)
        for row in range(page.model.rowCount()):
            assert page.table.columnWidth(5) >= page.status_delegate.required_size(page.model.rows[row][5]).width()
            assert page.table.indexWidget(page.model.index(row, 5)) is None
    finally:
        page.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
