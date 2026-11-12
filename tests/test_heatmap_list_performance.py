"""대량 목록의 측정 비용을 제한하면서 원문·선택·펼침 상태를 보존한다."""

from dataclasses import dataclass, replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication

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
