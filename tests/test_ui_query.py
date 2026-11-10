"""조회 조건의 초안/적용 분리와 실제 Qt 조작. 아래 Fab/Line은 테스트 전용 값이다."""

from dataclasses import FrozenInstanceError, replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from setron.ui.main_window import MainWindow
from setron.ui.query_state import (
    UNASSIGNED, FabOption, GroupOption, LineOption, QueryCatalog, QuerySelection,
    QueryState, SiteOption, VersionFilter, preview_catalog,
)
from setron.ui.theme import install_theme


@pytest.fixture
def catalog():
    return QueryCatalog(
        sites=(SiteOption("s1", "테스트 Site A"), SiteOption("s2", "테스트 Site B")),
        fabs=(FabOption("f1", "테스트 Fab A", "s1"), FabOption("f2", "테스트 Fab B", "s2")),
        lines=(LineOption("l1", "테스트 Line A", "s1", "f1"),
               LineOption("l2", "테스트 Line B", "s2", "f2"),
               LineOption("l3", "Fab 미등록 Line", "s1", None)),
        groups=(GroupOption("g1", "O2", "OXIDE"), GroupOption("g2", "Inline O2", "KCTC"), GroupOption("g3", "C4", "POLY")),
        source_label="테스트 전용 선택지 · 실데이터 아님",
    )


@pytest.fixture
def state(catalog):
    return QueryState(catalog)


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    install_theme(instance)
    yield instance


@pytest.fixture
def window(app, catalog):
    result = MainWindow(query_catalog=catalog)
    result.show()
    QTest.qWait(40)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def test_initial_state_has_no_implicit_applied_query(state):
    assert state.draft == QuerySelection()
    assert state.applied is None
    assert state.has_pending_changes
    assert not state.applied_is_current


def test_draft_apply_reset_are_separate_and_immutable(state):
    state.select("site_key", "s1")
    assert state.applied is None
    applied = state.apply()
    assert not state.has_pending_changes
    state.select("group_key", "g1")
    assert state.applied is applied
    assert state.is_current(applied)  # 초안 변경은 적용 요청을 무효화하지 않음
    assert state.has_pending_changes
    state.reset_draft()
    assert state.applied is applied
    assert state.draft == QuerySelection()
    assert applied.selection.site_key == "s1"
    with pytest.raises(FrozenInstanceError):
        applied.selection.site_key = "s2"
    next_query = state.apply()
    assert next_query.selection == QuerySelection()
    assert next_query.revision == 2
    assert not state.is_current(applied)


def test_reapply_same_selection_is_a_new_refresh_token(state):
    first = state.apply()
    second = state.apply()
    assert first.selection == second.selection
    assert (first.revision, second.revision) == (1, 2)
    assert state.is_current(second)
    assert not state.is_current(first)


def test_parent_changes_clear_dependents_but_not_group(state):
    selection = QuerySelection("s1", "f1", "l1", "g1")
    state.set_draft(selection)
    state.select("site_key", "s1")
    assert state.draft == selection
    state.select("group_key", "g2")
    assert state.draft.line_key == "l1"
    state.select("fab_key", UNASSIGNED)
    assert state.draft == QuerySelection("s1", UNASSIGNED, None, "g2")
    assert [o.key for o in state.available_lines()] == ["l3"]
    state.select("line_key", "l3")
    state.select("site_key", "s2")
    assert state.draft == QuerySelection("s2", UNASSIGNED, None, "g2")
    assert [o.key for o in state.available_fabs()] == ["f2"]
    assert state.available_lines() == ()
    state.select("fab_key", None)
    assert [o.key for o in state.available_lines()] == ["l2"]


def test_all_unassigned_and_known_line_are_distinct(state):
    state.set_draft(QuerySelection("s1", UNASSIGNED, "l3"))
    assert "Fab 미지정" in state.describe(state.draft)
    assert "Line Fab 미등록 Line" in state.describe(state.draft)
    state.select("line_key", UNASSIGNED)
    assert state.draft.line_key is UNASSIGNED
    state.select("line_key", None)
    assert state.draft.line_key is None
    assert "Line 전체" in state.describe(state.draft)


@pytest.mark.parametrize("selection", [
    QuerySelection(site_key="P1"), QuerySelection(site_key=UNASSIGNED),
    QuerySelection(fab_key="unknown"), QuerySelection(line_key="15L"),
    QuerySelection("s1", "f2"), QuerySelection("s2", None, "l1"),
    QuerySelection(None, "f1", "l2"), QuerySelection(None, UNASSIGNED, "l1"),
    QuerySelection(group_key="version_family_id"), QuerySelection(group_key=UNASSIGNED),
])
def test_invalid_draft_is_rejected_atomically(state, selection):
    before = state.apply()
    with pytest.raises(ValueError):
        state.set_draft(selection)
    assert state.draft == QuerySelection()
    assert state.applied is before
    assert state.is_current(before)


def test_unknown_field_and_option_do_not_change_state(state):
    with pytest.raises(ValueError):
        state.select("raw_area", "P1")
    with pytest.raises(ValueError):
        state.select("site_key", "unknown")
    assert state.draft == QuerySelection()


def test_replacing_catalog_normalizes_draft_without_reapplying(state):
    state.set_draft(QuerySelection("s1", "f1", "l1", "g1"))
    applied = state.apply()
    state.replace_catalog(QueryCatalog())
    assert state.draft == QuerySelection()
    assert state.applied is applied
    assert "테스트 Fab A" in applied.description
    assert state.has_pending_changes
    assert not state.applied_is_current
    assert not state.is_current(applied)
    assert state.apply().revision == 2


def test_catalog_parent_change_also_invalidates_old_response(state, catalog):
    state.set_draft(QuerySelection("s1", "f1", "l1", "g1"))
    old = state.apply()
    state.replace_catalog(replace(catalog, fabs=(catalog.fabs[1],), lines=(catalog.lines[1], catalog.lines[2])))
    assert state.draft == QuerySelection("s1", None, None, "g1")
    assert not state.is_current(old)
    current = state.apply()
    state.replace_catalog(state.catalog)
    assert not state.is_current(current)  # 같은 선택지여도 새 데이터 세대임


def test_catalog_tuples_and_real_key_named_unassigned_do_not_collide():
    sites = [SiteOption("UNASSIGNED", "전체")]
    catalog = QueryCatalog(sites=sites, fabs=(FabOption("미지정", "미지정", "UNASSIGNED"),))
    sites.clear()
    state = QueryState(catalog)
    state.set_draft(QuerySelection("UNASSIGNED", "미지정"))
    assert state.draft.fab_key != UNASSIGNED
    assert len(catalog.sites) == 1
    with pytest.raises(FrozenInstanceError):
        catalog.sites = ()


@pytest.mark.parametrize("kwargs", [
    {"sites": (SiteOption("s", "A"), SiteOption("s", "B"))},
    {"sites": (SiteOption("", "A"),)}, {"sites": (SiteOption("s", " "),)},
    {"fabs": (FabOption("f", "Fab", "missing"),)},
    {"sites": (SiteOption("s", "Site"),), "lines": (LineOption("l", "Line", "s", "missing"),)},
    {"sites": (SiteOption("a", "A"), SiteOption("b", "B")),
     "fabs": (FabOption("f", "Fab", "a"),), "lines": (LineOption("l", "Line", "b", "f"),)},
])
def test_catalog_rejects_duplicate_empty_and_wrong_parent_options(kwargs):
    with pytest.raises(ValueError):
        QueryCatalog(**kwargs)


def test_empty_catalog_and_preview_do_not_invent_fab_line_mapping():
    state = QueryState(QueryCatalog())
    state.set_draft(QuerySelection(fab_key=UNASSIGNED, line_key=UNASSIGNED))
    assert state.apply().selection.fab_key is UNASSIGNED
    preview = preview_catalog()
    assert preview.fabs == preview.lines == ()
    assert "DB 미연결" in preview.source_label
    assert len({o.key for o in preview.groups}) == 9
    assert {o.label for o in preview.groups if o.category == "KCTC"} == {
        "Inline Ox", "Inline O2", "Cluster Ox", "Cluster O2"}


def open_filter(panel, field):
    QTest.mouseClick(panel.combos[field], Qt.MouseButton.LeftButton)
    popup = panel.filter_popup
    assert popup is not None and popup.isVisible()
    return popup


def check_only(popup, *keys):
    for key, check in popup.checks.items():
        if check.isChecked() != (key in keys):
            QTest.mouseClick(check, Qt.MouseButton.LeftButton)


def choose(panel, field, *keys):
    popup = open_filter(panel, field)
    check_only(popup, *keys)
    QTest.mouseClick(popup.confirm_button, Qt.MouseButton.LeftButton)
    assert panel.filter_popup is None


def test_filter_confirm_applies_once_escape_cancels_and_reset_applies(window):
    panel = window.query_panel
    spy = QSignalSpy(window.query_applied)
    draft_spy = QSignalSpy(panel.draft_changed)
    assert panel.status.text() == "미적용"
    popup = open_filter(panel, "site_key")
    assert all(check.isChecked() for check in popup.checks.values())
    check_only(popup, "s1")
    assert spy.count() == draft_spy.count() == 0 and panel.state.draft == QuerySelection()
    QTest.keyClick(popup.search, Qt.Key.Key_Escape)
    assert panel.filter_popup is None and spy.count() == draft_spy.count() == 0
    assert panel.state.applied is None and panel.combos["site_key"].caption == "전체"

    choose(panel, "site_key", "s1")
    applied = window.query_state.applied
    assert spy.count() == draft_spy.count() == 1 and spy.at(0)[0] is applied
    assert applied.selection.site_key == "s1"
    assert panel.status.text() == "조건 적용됨"
    assert "테스트 Site A" in panel.applied_summary.text()
    panel.select("group_key", "g1")
    panel.reset_draft()
    assert panel.state.draft == QuerySelection() and panel.state.applied is applied and spy.count() == 1

    QTest.mouseClick(panel.reset_button, Qt.MouseButton.LeftButton)
    assert panel.state.applied.selection == panel.state.draft == QuerySelection()
    assert spy.count() == 2 and spy.at(1)[0] is panel.state.applied
    assert panel.status.text() == "조건 적용됨"


def test_cascading_controls_and_unassigned_line(window):
    panel = window.query_panel
    panel.set_draft(QuerySelection("s1", "f1", "l1", "g1"))
    spy = QSignalSpy(panel.applied)
    choose(panel, "site_key", "s2")
    assert panel.state.applied.selection == QuerySelection("s2", None, None, "g1")
    line_popup = open_filter(panel, "line_key")
    assert tuple(line_popup.checks) == (UNASSIGNED, "l2")
    line_popup.search.setText("테스트 Fab B")
    assert line_popup.checks["l2"].isVisible() and not line_popup.checks[UNASSIGNED].isVisible()
    line_popup.reject()
    assert spy.count() == 1
    choose(panel, "group_key", "g1", "g2")
    assert panel.state.applied.selection.group_key == ("g1", "g2")
    assert panel.combos["group_key"].toolTip() == "O2, Inline O2"
    choose(panel, "site_key", "s1")
    choose(panel, "fab_key", UNASSIGNED)
    choose(panel, "line_key", "l3")
    assert panel.combos["fab_key"].caption == "미지정"
    assert panel.state.applied.selection == QuerySelection("s1", UNASSIGNED, "l3", ("g1", "g2"))
    assert spy.count() == 5


def test_line_popup_has_no_site_headers_and_distinguishes_duplicate_names(window, catalog):
    window.set_query_catalog(replace(catalog, lines=(*catalog.lines,
        LineOption("l4", "테스트 Line A", "s2", "f2"))))
    popup = open_filter(window.query_panel, "line_key")
    headings = {caption.text() for caption in popup.findChildren(QLabel)}
    assert not headings.intersection({"테스트 Site A", "테스트 Site B", "테스트 Site A / 테스트 Fab A",
                                      "테스트 Site B / 테스트 Fab B"})
    assert popup.checks["l1"].caption.text() == "테스트 Line A · 테스트 Site A / 테스트 Fab A"
    assert popup.checks["l4"].caption.text() == "테스트 Line A · 테스트 Site B / 테스트 Fab B"
    assert popup.checks["l2"].caption.text() == "테스트 Line B"
    popup.search.setText("테스트 Site B")
    assert popup.checks["l4"].isVisible() and not popup.checks["l1"].isVisible()
    popup.reject()


def test_catalog_replacement_rebuilds_controls_but_keeps_applied_summary(window):
    panel = window.query_panel
    panel.set_draft(QuerySelection("s1", "f1", "l1", "g1"))
    old = panel.apply_draft()
    spy = QSignalSpy(panel.applied)
    window.set_query_catalog(QueryCatalog())
    assert panel.state.applied is old
    assert panel.status.text() == "선택지 변경 · 재적용 필요"
    assert "테스트 Line A" in panel.applied_summary.text()
    assert not panel.combos["site_key"].isEnabled() and panel.combos["site_key"].caption == "전체"
    assert panel.filter_popup is None
    assert spy.count() == 0


def test_navigation_preserves_draft_and_applied_and_query_is_opt_in(window, app):
    panel = window.query_panel
    panel.select("site_key", "s1")
    old = panel.apply_draft()
    panel.select("group_key", "g1")
    window.register_page("import", QWidget())
    window.register_page("heatmap", QWidget())
    window.register_page("items", QWidget(), uses_query=True)
    window.navigate("import")
    assert panel.isHidden()
    window.navigate("heatmap")
    assert not panel.isHidden()
    assert panel.state.applied is old
    assert panel.state.draft == QuerySelection("s1", None, None, "g1")
    window.navigate("items")
    assert not panel.isHidden()
    window.navigate("dashboard")
    assert "재적용 필요" in panel.status.text()


def test_invalid_registration_flag_has_no_partial_mutation(window):
    with pytest.raises(ValueError):
        window.register_page("import", QWidget(), uses_query="yes")
    assert window.pages == {}
    assert not window.nav["import"].isEnabled()


def test_keyboard_filter_labels_and_unavailable_features(window):
    panel = window.query_panel
    for field, caption in (("site_key", "Site"), ("fab_key", "Fab"), ("line_key", "Line"), ("group_key", "설비군")):
        assert panel.combos[field].accessibleName() == f"{caption} 전체"
    assert not panel.goal_button.isEnabled()
    assert not panel.preferences_button.isEnabled()
    site = panel.combos["site_key"]
    site.setFocus()
    QTest.keyClick(site, Qt.Key.Key_Space)
    popup = panel.filter_popup
    assert popup is not None and popup.search.hasFocus()
    other_site = popup.checks["s2"]
    other_site.setFocus()
    QTest.keyClick(other_site, Qt.Key.Key_Space)
    assert not other_site.isChecked() and panel.state.draft.site_key is None
    assert panel.state.applied is None
    popup.confirm_button.setFocus()
    QTest.keyClick(popup.confirm_button, Qt.Key.Key_Space)
    assert panel.state.applied.revision == 1 and panel.state.applied.selection.site_key == "s1"
    assert "DB" not in panel.status.text()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
@pytest.mark.parametrize("preview", [False, True])
def test_responsive_query_controls_stay_in_card(window, size, preview):
    if preview:
        window.set_query_catalog(preview_catalog())
    window.resize(*size)
    panel = window.query_panel
    # 첫 줄은 위치·날짜, 둘째 줄은 목표 N 입력과 독립적인 버전 다중 조회.
    assert panel.reset_button.accessibleName() == "조건 초기화"
    assert not any(button.text() == "조회 적용" and button.isVisible() for button in panel.findChildren(QPushButton))
    assert [w.text().strip() for w in panel.actions_row.widgets] == ["기준 버전 설정", "조회 버전 설정"]
    QTest.qWait(60)
    assert panel.goal_button.width() < 170 and panel.version_button.width() < 190
    assert all(w.height() == 38 and w.width() >= w.sizeHint().width() for w in panel.actions_row.widgets)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert window.scroll.widget().width() == window.scroll.viewport().width()
    for widget in [panel.date_edit, *panel.combos.values(), panel.reset_button,
                   *panel.actions_row.widgets]:
        assert widget.isVisible()
        point = widget.mapTo(panel.card, QPoint(0, 0))
        assert point.x() >= 20
        assert point.x() + widget.width() <= panel.card.width() - 20
        assert point.y() >= 16
        assert point.y() + widget.height() <= panel.card.height() - 16
    assert panel.fields_row.columns == (3 if size[0] == 960 else 6)
    default_width = panel.version_button.width()
    panel.set_version_filter(VersionFilter(tuple(f"v{i}" for i in range(123))))
    QTest.qWait(20)
    assert panel.version_button.text() == "조회 버전 설정 · 123개 선택"
    assert panel.version_button.width() > default_width
    assert panel.version_button.width() >= panel.version_button.sizeHint().width()
    assert panel.version_button.geometry().right() < panel.actions_row.width()
