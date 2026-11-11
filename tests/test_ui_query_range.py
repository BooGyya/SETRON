"""꺾은선 그래프의 기간 입력은 공통 기준 날짜 필드 하나를 사용한다."""

from datetime import date

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QSignalSpy, QTest

from setron.ui.query_panel import QueryPanel
from setron.ui.query_state import QueryState
from test_ui_query import app, catalog  # noqa: F401


START, END = date(2026, 9, 21), date(2026, 10, 21)


@pytest.fixture
def panel(app, catalog):
    control = QueryPanel(QueryState(catalog))
    control.resize(850, 260)
    control.show()
    QTest.qWait(20)
    yield control
    control.close()
    control.deleteLater()
    app.processEvents()


def test_range_mode_reuses_date_field_and_reflows_then_restores_normal_mode(panel):
    normal = panel.date_edit
    field = panel.original_date_field
    applied = QSignalSpy(panel.applied)
    selected = QSignalSpy(panel.date_range_selected)
    assert panel.range_button is None and normal.isVisible()
    assert panel.fields_row.columns == 6

    panel.enable_date_range(START, END)
    QTest.qWait(20)

    assert panel.date_edit is normal and not normal.isVisible()
    assert panel.range_button.isVisible()
    assert panel.range_button.parentWidget() is field
    assert field.minimumWidth() == 240 and panel.fields_row.columns == 3
    assert (panel.range_button.start, panel.range_button.end) == (START, END)
    assert selected.count() == applied.count() == 0

    panel.disable_date_range()
    QTest.qWait(20)

    assert normal.isVisible() and not panel.range_button.isVisible()
    assert field.minimumWidth() == 128 and panel.fields_row.columns == 6
    assert selected.count() == applied.count() == 0


def test_accepting_range_stages_metadata_until_search_and_keeps_end_date(panel):
    panel.set_as_of(END)
    previous = panel.apply_draft()
    panel.enable_date_range(START, END)
    events = []
    panel.date_range_selected.connect(lambda start, end: events.append(("range", start, end, panel.state.draft.as_of)))
    panel.applied.connect(lambda query: events.append(("query", query.selection.as_of)))
    applied, confirmed = QSignalSpy(panel.applied), QSignalSpy(panel.confirmed)
    selected = QSignalSpy(panel.date_range_selected)

    QTest.mouseClick(panel.range_button, Qt.MouseButton.LeftButton)
    dialog = panel.range_button.dialog
    dialog.calendar.clicked.emit(QDate(2026, 10, 1))
    dialog.calendar.clicked.emit(QDate(2026, 10, 15))
    QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)

    assert selected.count() == confirmed.count() == 1 and applied.count() == 0
    assert events == [("range", date(2026, 10, 1), date(2026, 10, 15), END)]
    assert panel.state.applied is previous and panel.state.draft.as_of == date(2026, 10, 15)
    panel.search_button.click()
    assert applied.count() == 1
    assert events == [("range", date(2026, 10, 1), date(2026, 10, 15), END),
                      ("query", date(2026, 10, 15))]
    assert panel.state.applied.selection.as_of == date(2026, 10, 15)
    assert panel.date_edit.date().toPython() == date(2026, 10, 15)
    panel.disable_date_range()
    assert panel.date_edit.isVisible() and applied.count() == 1


def test_canceling_pending_range_or_leaving_mode_does_not_apply(panel):
    panel.set_as_of(END)
    previous = panel.apply_draft()
    panel.enable_date_range(START, END)
    applied, selected = QSignalSpy(panel.applied), QSignalSpy(panel.date_range_selected)
    QTest.mouseClick(panel.range_button, Qt.MouseButton.LeftButton)
    dialog = panel.range_button.dialog
    dialog.calendar.clicked.emit(QDate(2026, 10, 1))
    dialog.calendar.clicked.emit(QDate(2026, 10, 15))
    QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)

    assert panel.state.applied is previous
    assert (panel.range_button.start, panel.range_button.end) == (START, END)
    assert applied.count() == selected.count() == 0
    QTest.mouseClick(panel.range_button, Qt.MouseButton.LeftButton)
    reopened = panel.range_button.dialog
    assert (reopened.start, reopened.end) == (START, END)
    panel.disable_date_range()
    assert not reopened.isVisible() and panel.date_edit.isVisible()
    assert applied.count() == selected.count() == 0


def test_regular_date_confirmation_and_replay_period_signals_stay_independent(panel, app, catalog):
    regular = QSignalSpy(panel.applied)
    panel.date_edit.setDate(QDate(2026, 10, 15))
    assert regular.count() == 0 and panel.state.applied is None
    panel.search_button.click()
    assert regular.count() == 1 and panel.state.applied.selection.as_of == date(2026, 10, 15)

    replay = QueryPanel(QueryState(catalog), period=True)
    replay.show()
    try:
        confirmed, applied = QSignalSpy(replay.confirmed), QSignalSpy(replay.applied)
        start, end = replay.start_date, replay.end_date
        replay.enable_date_range(START, END)
        replay.disable_date_range()
        assert replay.range_button.isVisible() and replay.date_edit is None
        assert replay.start_date is start and replay.end_date is end
        assert not start.isVisible() and not end.isVisible()
        start.setDate(QDate(2026, 10, 1))
        assert confirmed.count() == 1 and applied.count() == 0
        assert replay.range_button.start == date(2026, 10, 1)
        replay.range_button.click()
        dialog = replay.range_button.dialog
        dialog.calendar.clicked.emit(QDate(2026, 10, 3))
        dialog.calendar.clicked.emit(QDate(2026, 10, 17))
        assert confirmed.count() == 1
        dialog.apply_button.click()
        assert confirmed.count() == 2 and applied.count() == 0
        assert (start.date().toPython(), end.date().toPython()) == (date(2026, 10, 3), date(2026, 10, 17))
        # 기존 기간 입력이 허용하던 저장 관측일 범위를 새 표시에서도 유지한다.
        replay.enable_date_range(date(2099, 1, 1), date(2099, 1, 31))
        assert replay.range_button.end == end.date().toPython() == date(2099, 1, 31)
        end.setDate(QDate(2099, 2, 1))
        assert replay.range_button.end == date(2099, 2, 1)
    finally:
        replay.close()
        replay.deleteLater()
        app.processEvents()
