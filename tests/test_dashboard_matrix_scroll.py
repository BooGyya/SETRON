"""집계 히트맵의 내부 스크롤, 고정 머리글/위치 열과 실제 클릭 범위를 검증한다."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QLabel

from setron.ui.appearance_data import Appearance
from setron.ui.dashboard import DashboardPage
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.main_window import MainWindow
from setron.ui.query_state import QuerySelection
from test_ui_dashboard import app, source, window  # noqa: F401


@pytest.fixture
def dense_window(app):
    source = demo_dashboard_source()
    source.locations = {eid: replace(location, raw_area=f"P{eid:02d} · 긴 세부 구역 이름")
                        for eid, location in source.locations.items()}
    source.catalog = replace(source.catalog,
        sites=tuple(replace(site, label=site.label + " · 긴 사업장 이름을 포함한 위치") for site in source.catalog.sites),
        groups=tuple(replace(group, label="M-C4 DFB2 확장 설비군" if group.key == "c4"
                            else group.label + " · 추가 설비군 표시 이름")
                     for group in source.catalog.groups))
    host = MainWindow(query_catalog=source.catalog)
    page = DashboardPage(host.query_state, source)
    host.register_page("dashboard", page)
    host.query_applied.connect(page.apply_query)
    host.resize(1100, 900)
    host.show()
    host.query_panel.apply_draft()
    QTest.qWait(40)
    yield host
    host.close()
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def point(widget, parent):
    return widget.mapTo(parent, QPoint())


def check_alignment(matrix):
    body, header, locations = (area.widget().layout() for area in (matrix.body, matrix.header, matrix.row_header))
    assert matrix.header.viewport().width() == matrix.body.viewport().width()
    assert matrix.row_header.viewport().height() == matrix.body.viewport().height()
    for column in range(header.columnCount()):
        title = header.itemAtPosition(0, column).widget()
        cell = next(body.itemAtPosition(row, column).widget() for row in range(body.rowCount())
                    if body.itemAtPosition(row, column).widget().objectName() != "dashboardMatrixSiteDivider")
        assert point(title, matrix).x() == point(cell, matrix).x()
        assert title.width() == cell.width() >= matrix.MINIMUM_COLUMN_WIDTH
    for row in range(body.rowCount()):
        location, cell = locations.itemAtPosition(row, 0).widget(), body.itemAtPosition(row, 0).widget()
        assert point(location, matrix).y() == point(cell, matrix).y()
        assert location.height() == cell.height()


@pytest.mark.parametrize("width", [960, 1600])
def test_many_rows_and_columns_scroll_inside_matrix_with_frozen_headers(dense_window, width):
    window = dense_window
    window.resize(width, 900)
    QTest.qWait(40)
    matrix = window.pages["dashboard"].matrix_scroll
    assert matrix.height() <= matrix.MAXIMUM_HEIGHT
    assert matrix.horizontalScrollBar().maximum() > 0 and matrix.verticalScrollBar().maximum() > 0
    corner = point(matrix.corner, matrix)
    header_y, location_x = point(matrix.header, matrix).y(), point(matrix.row_header, matrix).x()
    matrix.horizontalScrollBar().setValue(matrix.horizontalScrollBar().maximum())
    matrix.verticalScrollBar().setValue(matrix.verticalScrollBar().maximum())
    QTest.qWait(30)
    assert point(matrix.corner, matrix) == corner
    assert point(matrix.header, matrix).y() == header_y and point(matrix.row_header, matrix).x() == location_x
    check_alignment(matrix)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert window.scroll.widget().width() == window.scroll.viewport().width()
    body = matrix.widget().layout()
    last = body.itemAtPosition(body.rowCount() - 1, body.columnCount() - 1).widget()
    rect = last.rect().translated(point(last, matrix.viewport()))
    assert matrix.viewport().rect().contains(rect)


def wheel(viewport, delta):
    position = viewport.rect().center()
    event = QWheelEvent(QPointF(position), QPointF(viewport.mapToGlobal(position)), QPoint(), delta,
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QCoreApplication.sendEvent(viewport, event)


def test_wheel_over_frozen_panes_scrolls_body_without_desynchronizing(dense_window):
    matrix = dense_window.pages["dashboard"].matrix_scroll
    wheel(matrix.row_header.viewport(), QPoint(0, -120))
    assert matrix.verticalScrollBar().value() > 0
    assert matrix.row_header.verticalScrollBar().value() == matrix.verticalScrollBar().value()
    wheel(matrix.header.viewport(), QPoint(-120, 0))
    assert matrix.horizontalScrollBar().value() > 0
    assert matrix.header.horizontalScrollBar().value() == matrix.horizontalScrollBar().value()
    previous = matrix.verticalScrollBar().value()
    wheel(matrix.corner, QPoint(0, -120))
    assert matrix.verticalScrollBar().value() > previous
    check_alignment(matrix)


def test_large_font_wrapped_locations_and_site_dividers_keep_exact_row_alignment(dense_window, app):
    matrix = dense_window.pages["dashboard"].matrix_scroll
    current, previous = app.appearance_manager, app.appearance_manager.appearance
    first = matrix.row_header.widget().layout().itemAtPosition(0, 0).widget()
    normal_height = first.height()
    try:
        current.apply(Appearance(mode="dark", font_size="large"))
        QTest.qWait(50)
        assert first.height() > normal_height
        matrix.horizontalScrollBar().setValue(matrix.horizontalScrollBar().maximum())
        matrix.verticalScrollBar().setValue(matrix.verticalScrollBar().maximum())
        check_alignment(matrix)
        labels = matrix.header.widget().findChildren(QLabel)
        assert any("M-C4 DFB2" in label.text() for label in labels)
        assert all(label.fontMetrics().horizontalAdvance(label.text()) + 16 <= label.width() for label in labels)
    finally:
        current.apply(previous)
        QTest.qWait(50)
    assert first.height() == normal_height
    check_alignment(matrix)


def test_scrolled_total_cell_and_risk_badge_keep_distinct_drilldown_cohorts(dense_window):
    window, page = dense_window, dense_window.pages["dashboard"]
    window.resize(1100, 700)
    QTest.qWait(40)
    row, column = len(page.snapshot.rows), len(page.snapshot.rows[-1].cells)
    cell, risk = page.matrix_buttons[row, column], page.risk_buttons[row, column]
    page.matrix_scroll.ensureWidgetVisible(risk)
    QTest.qWait(30)
    window.scroll.verticalScrollBar().setValue(window.scroll.verticalScrollBar().maximum())
    QTest.qWait(20)
    assert window.rect().contains(risk.rect().translated(point(risk, window)))
    selected = QSignalSpy(page.drilldown_requested)
    QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton,
                     pos=cell.mapTo(window, QPoint(4, 4)))
    QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton,
                     pos=risk.mapTo(window, risk.rect().center()))
    assert selected.count() == 2
    assert selected.at(0)[0].equipment_ids == page.snapshot.rows[-1].cells[-1].equipment_ids
    assert not selected.at(0)[0].risk_only
    assert selected.at(1)[0].equipment_ids == page.snapshot.rows[-1].cells[-1].risky_ids
    assert selected.at(1)[0].risk_only


def test_short_filtered_matrix_reserves_actual_horizontal_bar_without_vertical_overflow(window):
    window.resize(960, 600)
    window.query_panel.set_draft(QuerySelection(site_key="s2"))
    window.query_panel.apply_draft()
    matrix = window.pages["dashboard"].matrix_scroll
    matrix.horizontalScrollBar().setStyleSheet("QScrollBar:horizontal {height:26px;}")
    QTest.qWait(60)
    assert matrix.horizontalScrollBar().maximum() > 0
    assert matrix.horizontalScrollBar().sizeHint().height() > 12
    assert matrix.verticalScrollBar().maximum() == 0
    check_alignment(matrix)
    for mode in (2, 3, 0):
        window.pages["dashboard"].tabs.setCurrentIndex(mode)
        QTest.qWait(20)
        assert matrix.isVisible() == (mode == 0)
