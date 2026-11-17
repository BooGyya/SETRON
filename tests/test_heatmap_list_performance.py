"""대량 목록의 측정 비용을 제한하면서 원문·선택·펼침 상태를 보존한다."""

from dataclasses import dataclass, replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPersistentModelIndex, QRect, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

import setron.ui.heatmap_list as heatmap_list
from setron.ui.heatmap_list import HeatmapListModel, HeatmapTreeView
from setron.ui.theme import install_theme


@dataclass(frozen=True)
class Row:
    equipment_id: int
    code: str
    version: str = "v2"


def node(rows, key=("site", "A")):
    return SimpleNamespace(key=key, title="사업장 A", group_name=None, children=(), tiles=tuple(rows))


def values(row, _group):
    return (row.code, "A", "M-O2", row.version, "목표 달성", "위험 미확인"), "success", {}


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


@pytest.fixture
def table(app):
    model = HeatmapListModel(("설비명", "위치", "모델", "버전", "목표 달성", "위험 판정"), numeric_columns=(3,))
    view = HeatmapTreeView(model, "대량 목록 검증", (220, 150, 190, 230, 120, 160))
    view.resize(750, 800)
    view.show()
    yield view
    view.close()
    view.deleteLater()
    app.processEvents()


def test_large_list_stops_height_measurement_at_limit_and_keeps_last_row_accessible(table, app, monkeypatch):
    rows = tuple(Row(index, f"TEST-{index:04}") for index in range(2000))
    table.set_groups((node(rows),), values)
    app.processEvents()
    calls, original = [], table._row_height

    def measured(index):
        calls.append(index)
        return original(index)

    monkeypatch.setattr(table, "_row_height", measured)
    table.fit_height()
    assert table.height() == 800 and len(calls) < 30
    assert table.model().ids == tuple(range(2000))
    last = table.model().equipment_index(1999)
    table.select_equipment(1999)
    table.scrollTo(last)
    app.processEvents()
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole) == 1999
    assert table.viewport().rect().intersects(table.visualRect(last))


def test_dense_list_reuses_measured_cell_sizes_after_collapsing_and_expanding(table, app, monkeypatch):
    table.resize(1200, 800)
    rows = tuple(Row(index, f"TEST-{index:04}") for index in range(2000))
    table.set_groups((node(rows),), values)
    QTest.qWait(30)
    measured, original = [], heatmap_list.QStyledItemDelegate.sizeHint

    def size_hint(delegate, option, index):
        measured.append(index)
        return original(delegate, option, index)

    monkeypatch.setattr(heatmap_list.QStyledItemDelegate, "sizeHint", size_hint)
    root = table.model().index(0, 0)
    for _ in range(2):
        table.setExpanded(root, False)
        app.processEvents()
        table.setExpanded(root, True)
        app.processEvents()
    # Qt가 다시 방문한 12,000개 셀의 원문과 줄바꿈을 매번 재측정하지 않는다.
    assert len(measured) < 30
    table.select_equipment(1999)
    table.scrollTo(table.model().equipment_index(1999))
    app.processEvents()
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole) == 1999
    assert table.viewport().rect().intersects(table.visualRect(table.currentIndex()))


def test_cell_size_cache_refreshes_for_text_width_font_style_and_compact_changes(table, monkeypatch):
    table.set_groups((node((Row(0, "TEST-0"),)),), values)
    delegate, index = table.itemDelegate(), table.model().equipment_index(0).siblingAtColumn(3)
    option = QStyleOptionViewItem()
    option.initFrom(table)
    option.widget = table
    option.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
    option.rect = QRect(0, 0, 180, 0)
    measured, original = [], heatmap_list.QStyledItemDelegate.sizeHint

    def size_hint(current, current_option, current_index):
        measured.append(current_index)
        return original(current, current_option, current_index)

    monkeypatch.setattr(heatmap_list.QStyledItemDelegate, "sizeHint", size_hint)
    delegate._clear_sizes()
    plain = delegate.sizeHint(option, index)
    assert delegate.sizeHint(option, index) == plain and len(measured) == 1
    table.model().itemFromIndex(index).setText("version\n" * 8)
    measured.clear()
    wrapped = delegate.sizeHint(option, index)
    assert wrapped.height() > plain.height() and len(measured) == 1
    assert delegate.sizeHint(option, index) == wrapped and len(measured) == 1
    option.rect.setWidth(80)
    measured.clear()
    delegate.sizeHint(option, index)
    assert len(measured) == 1
    larger = QFont(table.font())
    larger.setPixelSize(larger.pixelSize() + 2)
    table.setFont(larger)
    measured.clear()
    delegate.sizeHint(option, index)
    assert len(measured) == 1
    table.setStyleSheet(table.styleSheet() + "QTreeView::item {padding:12px;}")
    measured.clear()
    delegate.sizeHint(option, index)
    assert len(measured) == 1
    table.compact = True
    table.setWordWrap(False)
    measured.clear()
    delegate.sizeHint(option, index)
    assert len(measured) == 1
    previous = QPersistentModelIndex(index)
    table.set_groups((node((Row(1, "REPLACED"),)),), values)
    replacement = table.model().equipment_index(1).siblingAtColumn(3)
    assert replacement.isValid() and not previous.isValid()
    assert delegate.sizeHint(option, replacement).height() < wrapped.height()
    monkeypatch.setattr(delegate, "SIZE_CACHE_LIMIT", 8)
    delegate._clear_sizes()
    for width in range(100, 120):
        option.rect.setWidth(width)
        delegate.sizeHint(option, replacement)
    assert len(delegate._sizes) == 8


def test_width_cache_keeps_longest_last_name_and_remeasures_renames_and_indentation(table, app, monkeypatch):
    longest = "LAST-EQUIPMENT-" * 8
    rows = tuple(Row(index, longest if index == 199 else f"TEST-{index:04}") for index in range(200))
    table.set_groups((node(rows),), values)
    app.processEvents()
    index = table.model().equipment_index(199)
    text_width = QFontMetrics(index.data(Qt.ItemDataRole.FontRole)).horizontalAdvance(longest)
    assert table._name_width >= text_width + 2 * table.indentation() + 20
    assert table.columnWidth(0) <= min(360, round(table.viewport().width() * 0.42))
    assert index.data(Qt.ItemDataRole.ToolTipRole) == longest

    measured, original = [], heatmap_list.QFontMetrics

    def metrics(value):
        measured.append(value)
        return original(value)

    monkeypatch.setattr(heatmap_list, "QFontMetrics", metrics)
    table.resize_columns()
    assert measured == []
    before, measured_before = table.columnWidth(0), table._name_width
    table.setIndentation(table.indentation() + 10)
    table.resize_columns()
    assert table._name_width == measured_before + 20 and table.columnWidth(0) == before
    original_paint_font = heatmap_list.paint_font

    def larger_font(*args, **kwargs):
        result = original_paint_font(*args, **kwargs)
        result.setPixelSize(result.pixelSize() + 4)
        return result

    monkeypatch.setattr(heatmap_list, "paint_font", larger_font)
    table.resize_columns()
    assert table._name_width > measured_before + 20 and table.columnWidth(0) == before
    table.set_groups((node(tuple(replace(row, code=f"SHORT-{row.equipment_id}") for row in rows)),), values)
    assert table.columnWidth(0) < before


def test_changing_text_can_shrink_capped_height_and_rebuilding_preserves_collapsed_selection(table, app):
    rows = tuple(Row(index, f"TEST-{index}", "v2\n" * 50 if index == 0 else "v2") for index in range(4))
    table.set_groups((node(rows),), values)
    app.processEvents()
    assert table.height() == 800
    short = tuple(replace(row, version="v2") for row in rows)
    table.set_groups((node(short),), values)
    app.processEvents()
    assert table.height() < 800
    table.select_equipment(2)
    table.setExpanded(table.model().index(0, 0), False)
    table.set_groups((node((*short, Row(4, "TEST-4"))),), values)
    assert not table.isExpanded(table.model().index(0, 0))
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole) == 2
    table.setExpanded(table.model().index(0, 0), True)
    table.compact = True
    table.setWordWrap(False)
    table.set_groups((node(short),), values)
    app.processEvents()
    compact_height = table.visualRect(table.model().equipment_index(0)).height()
    table.compact = False
    table.setWordWrap(True)
    table.set_groups((node(short),), values)
    app.processEvents()
    assert table.visualRect(table.model().equipment_index(0)).height() > compact_height
