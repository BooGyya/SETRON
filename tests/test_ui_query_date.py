"""기준일 선택: 조회 적용 전에는 바뀌지 않고, 적용하면 그날 이전 최신 관측으로 계산한다."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date, timedelta

import pytest
from PySide6.QtCore import QDate
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.ui.app import create_window
from setron.ui.dashboard_data import NoObservationBefore
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.query_state import AppliedQuery, QuerySelection, QueryState
from setron.ui.theme import install_theme


def applied(selection):
    return AppliedQuery(selection, 1, 0, "")


def test_snapshot_uses_latest_observation_on_or_before_as_of():
    source = demo_dashboard_source()
    assert source.snapshot(applied(QuerySelection())).base_date == date(2026, 10, 21)
    past = source.snapshot(applied(QuerySelection(as_of=date(2026, 8, 30))))
    assert past.base_date == date(2026, 8, 21)
    assert all(point.day <= date(2026, 8, 21) for point in past.observations)
    assert source.snapshot(applied(QuerySelection(as_of=date(2026, 7, 21)))).base_date == date(2026, 7, 21)
    with pytest.raises(NoObservationBefore, match="2026-01-01 이전"):
        source.snapshot(applied(QuerySelection(as_of=date(2026, 1, 1))))


def test_state_treats_today_or_future_as_latest_and_reset_clears_date():
    state = QueryState(demo_dashboard_source().catalog)
    state.set_as_of(date.today() + timedelta(days=3))
    assert state.draft.as_of is None
    state.set_as_of(date(2026, 8, 1))
    assert state.draft.as_of == date(2026, 8, 1) and "기준일 2026-08-01" in state.describe(state.draft)
    state.reset_draft()
    assert state.draft == QuerySelection()
    with pytest.raises(ValueError):
        state.set_draft(QuerySelection(as_of="2026-08-01"))


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    install_theme(app)
    result = create_window()
    result.resize(1600, 900)
    result.show()
    QTest.qWait(20)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def test_date_field_is_on_first_row_and_today_button_resets(window):
    panel = window.query_panel
    today = date.today()
    assert panel.date_edit.date().toPython() == today
    assert panel.date_edit.mapTo(panel.card, panel.date_edit.rect().topLeft()).y() == \
        panel.combos["site_key"].mapTo(panel.card, panel.combos["site_key"].rect().topLeft()).y()
    panel.date_edit.setDate(QDate(2026, 8, 30))
    assert window.query_state.draft.as_of == date(2026, 8, 30)
    assert window.query_state.applied is None  # 적용 전에는 결과가 바뀌지 않는다
    panel.today_button.click()
    assert window.query_state.draft.as_of is None and panel.date_edit.date().toPython() == today


def test_applied_date_drives_dashboard_and_heatmap(window):
    panel = window.query_panel
    panel.date_edit.setDate(QDate(2026, 8, 30))
    panel.apply_draft()
    QTest.qWait(30)
    assert window.pages["dashboard"].snapshot.base_date == date(2026, 8, 21)
    assert window.pages["heatmap"].snapshot.base_date == date(2026, 8, 21)
    panel.date_edit.setDate(QDate(2026, 1, 1))
    panel.apply_draft()
    QTest.qWait(30)
    dashboard = window.pages["dashboard"]
    assert dashboard.snapshot is None and "2026-01-01 이전" in dashboard.message.text()
    assert "2026-01-01 이전" in window.pages["heatmap"].message.text()
