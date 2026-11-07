"""C4 원본 표의 표시만 바꾸며, 원문·다른 양식·선택 강조는 유지한다."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from setron.ui.item_original import OriginalItemRow
from setron.ui.item_original_table import ItemOriginalTable
from setron.ui.items import _style_raw_table

from test_ui_items import app


HEADERS = ("No", "항목", "Update내용", "REMARK", "담당", "NormalC4", "DFB", "우선순위",
           "비활성화 가능", "HW 연관", "고객사 연관")
VALUES = ("1", "UI", "원문 X 및 불가를 그대로 보존", "<비고>", "담당자", "O", "X", "1", "불가", "X", "")


@pytest.fixture
def original(app):
    widget = ItemOriginalTable()
    _style_raw_table(widget.table)
    widget.resize(1700, 280)
    widget.set_rows((OriginalItemRow(7, 2, "Release List", 13, HEADERS, VALUES),))
    yield widget
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_c4_marks_and_alignment_do_not_change_original_values(original):
    model = original.model
    assert model.headings == HEADERS and model.rows == (VALUES,)
    for column, value in enumerate(VALUES):
        index = model.index(0, column)
        assert index.data() == index.data(Qt.ItemDataRole.AccessibleTextRole) == value
        expected = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter if column == 2 else Qt.AlignmentFlag.AlignCenter
        assert index.data(Qt.ItemDataRole.TextAlignmentRole) == expected
        if column != 2:
            assert model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole) == expected
        assert index.data(Qt.ItemDataRole.ForegroundRole) == (QColor("#c23345") if column in (6, 8, 9) else None)


@pytest.mark.parametrize("highlighted", [False, True])
@pytest.mark.parametrize("selected", [False, True])
def test_red_mark_survives_evidence_and_selection(original, highlighted, selected):
    original.model.highlighted = {7} if highlighted else set()
    index = original.model.index(0, 6)
    option = QStyleOptionViewItem()
    option.rect.setRect(0, 0, 135, 60)
    if selected:
        option.state |= QStyle.StateFlag.State_Selected
    canvas = QImage(135, 60, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.white)
    painter = QPainter(canvas)
    original.table.itemDelegate().paint(painter, option, index)
    painter.end()
    assert any((color := canvas.pixelColor(x, y)).red() > color.green() * 1.8
               and color.red() > color.blue() * 1.4
               for x in range(30, 105) for y in range(10, 50))
    assert index.data() == "X"


def test_switching_to_other_original_form_does_not_reuse_c4_styles(original):
    row = OriginalItemRow(8, 3, "O5", 5, ("No", "Update내용", "DFB", "비활성화 가능"), ("2", "원문", "X", "불가"))
    original.set_rows((*original.rows, row))
    original.documents.setCurrentIndex(1)
    assert original.model.headings == row.headers and original.model.rows == (row.values,)
    assert original.model.index(0, 2).data(Qt.ItemDataRole.ForegroundRole) is None
    assert original.model.index(0, 3).data(Qt.ItemDataRole.ForegroundRole) is None
    assert original.model.index(0, 0).data(Qt.ItemDataRole.TextAlignmentRole) == (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
