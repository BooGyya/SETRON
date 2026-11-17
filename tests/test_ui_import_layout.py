"""가져오기 화면 배치: 불러오기 후 화면 유지, 기준 날짜·Site와 번역표 후보 적용은 3단계 검토 항목 위, 합성 예시 안내 숨김."""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from setron.ui.app import startup_warning
from test_ui_import import app, excel, page, saver, wait_for_extraction, window  # noqa: F401  (fixtures)


def test_list_button_keeps_focus_and_scroll_on_first_step(window, page):
    page.list_button.setFocus(Qt.FocusReason.MouseFocusReason)
    assert window.focusWidget() is page.list_button
    bar = page.import_scroll.verticalScrollBar()
    bar.setValue(0)
    assert page.load_workbooks()
    assert window.focusWidget() is not page.grid and bar.value() == 0
    wait_for_extraction(page)
    QTest.qWait(20)
    assert page.workbook_combo.count() and window.focusWidget() is page.workbook_combo
    assert bar.value() == 0


def test_common_box_and_candidate_button_sit_above_review_issues(page):
    review, save = page.step_cards[2], page.step_cards[3]
    select = page.step_cards[0]
    assert review.isAncestorOf(page.common_box) and review.isAncestorOf(page.candidate_button)
    assert not select.isAncestorOf(page.common_box) and not save.isAncestorOf(page.candidate_button)
    top = lambda widget: widget.mapTo(review, widget.rect().topLeft()).y()  # noqa: E731
    assert top(page.common_box) < top(page.candidate_button) < top(page.issue_table)


def test_demo_warning_hidden_only_on_import(window, app):
    window.set_data_warning(startup_warning(None, None, demo_without_db=True))
    window.navigate("dashboard")
    app.processEvents()
    assert window.data_warning.isVisible()
    window.navigate("import")
    app.processEvents()
    assert not window.data_warning.isVisible()
    window.set_data_warning(startup_warning("DB 연결 실패", None))
    window.navigate("dashboard")
    window.navigate("import")
    app.processEvents()
    assert window.data_warning.isVisible()   # DB 접속 실패 경고는 가져오기에서도 보인다
