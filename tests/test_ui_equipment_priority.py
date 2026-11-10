"""설비 목록의 업그레이드 우선순위 정렬(BE-017)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.ui.app import create_window
from setron.ui.query_state import QuerySelection
from setron.ui.theme import install_theme


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


@pytest.fixture
def page(app):
    window = create_window()
    window.show()
    window.query_panel.set_draft(QuerySelection())
    window.query_panel.apply_draft()
    window.header.actions["equipment_list"].click()
    QTest.qWait(50)
    yield window.pages["equipment"]
    window.close()


def test_priority_sort_lists_ranked_first_then_needs_check(page):
    page.sort.setCurrentIndex(page.sort.findData("priority"))
    ranks = [page.data.priority.get(t.equipment_id) for t in page.visible_tiles]
    ranked = [entry.rank for entry in ranks if entry is not None and entry.rank]
    assert ranked == sorted(ranked) and ranked[0] == 1
    first_unranked = next((i for i, e in enumerate(ranks) if e is None or not e.rank), len(ranks))
    assert all(e is None or not e.rank for e in ranks[first_unranked:])  # 확인 필요·HPA는 뒤
    assert page.model.rows[0][0].startswith("1위 · ")


def test_other_sorts_do_not_show_rank(page):
    page.sort.setCurrentIndex(page.sort.findData("code"))
    assert not any(row[0].endswith("위") or "위 · " in row[0] for row in page.model.rows)
