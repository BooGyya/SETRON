"""조회 조건의 초안/적용 분리와 실제 Qt 조작. 아래 Fab/Line은 테스트 전용 값이다."""

from dataclasses import FrozenInstanceError, replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget

from setron.ui.main_window import MainWindow
from setron.ui.query_state import (
    UNASSIGNED, FabOption, GroupOption, LineOption, QueryCatalog, QuerySelection,
    QueryState, SiteOption, preview_catalog,
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
        groups=(GroupOption("g1", "O2", "OXIDE"), GroupOption("g2", "Inline O2", "KCTC")),
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
    assert state.draft == QuerySelection("s2", None, None, "g2")
    assert [o.key for o in state.available_fabs()] == ["f2"]
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


def choose(panel, field, key):
    combo = panel.combos[field]
    combo.setCurrentIndex(next(i for i in range(combo.count()) if combo.itemData(i) == key))


def test_combo_chips_and_applied_signal_agree(window):
    panel = window.query_panel
    spy = QSignalSpy(window.query_applied)
    draft_spy = QSignalSpy(panel.draft_changed)
    assert panel.status.text() == "미적용"
    QTest.mouseClick(panel.quick_buttons["site_key", "s1"], Qt.MouseButton.LeftButton)
    choose(panel, "fab_key", "f1")
    choose(panel, "line_key", "l1")
    choose(panel, "group_key", "g1")
    assert draft_spy.count() == 4
    assert spy.count() == 0
    assert panel.quick_buttons["group_key", "g1"].isChecked()
    assert not panel.quick_buttons["group_key", None].isChecked()
    QTest.mouseClick(panel.apply_button, Qt.MouseButton.LeftButton)
    applied = window.query_state.applied
    assert spy.count() == 1 and spy.at(0)[0] is applied
    assert panel.status.text() == "조건 적용됨"
    assert "테스트 Line A" in panel.applied_summary.text()
    QTest.mouseClick(panel.reset_button, Qt.MouseButton.LeftButton)
    assert panel.state.draft == QuerySelection()
    assert panel.state.applied is applied
    assert spy.count() == 1
    assert "재적용 필요" in panel.status.text()
    assert "테스트 Line A" in panel.applied_summary.text()


def test_cascading_controls_and_unassigned_line(window):
    panel = window.query_panel
    panel.set_draft(QuerySelection("s1", "f1", "l1", "g1"))
    choose(panel, "site_key", "s2")
    assert panel.state.draft == QuerySelection("s2", None, None, "g1")
    assert [panel.combos["line_key"].itemData(i) for i in range(panel.combos["line_key"].count())] == [None, UNASSIGNED, "l2"]
    panel.select("site_key", "s1")
    QTest.mouseClick(panel.quick_buttons["fab_key", UNASSIGNED], Qt.MouseButton.LeftButton)
    choose(panel, "line_key", "l3")
    assert panel.combos["fab_key"].currentData() is UNASSIGNED
    assert panel.state.draft.line_key == "l3"


def test_catalog_replacement_rebuilds_controls_but_keeps_applied_summary(window):
    panel = window.query_panel
    panel.set_draft(QuerySelection("s1", "f1", "l1", "g1"))
    old = panel.apply_draft()
    spy = QSignalSpy(panel.applied)
    window.set_query_catalog(QueryCatalog())
    assert panel.state.applied is old
    assert panel.status.text() == "선택지 변경 · 재적용 필요"
    assert "테스트 Line A" in panel.applied_summary.text()
    assert panel.combos["site_key"].count() == 1
    assert ("site_key", "s1") not in panel.quick_buttons
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


def test_keyboard_apply_labels_and_unavailable_features(window):
    panel = window.query_panel
    for field, caption in (("site_key", "Site"), ("fab_key", "Fab"), ("line_key", "Line"), ("group_key", "설비군")):
        assert panel.combos[field].accessibleName() == caption
    assert not panel.goal_button.isEnabled()
    assert not panel.preferences_button.isEnabled()
    site = panel.combos["site_key"]
    site.setFocus()
    QTest.keyClick(site, Qt.Key.Key_Home)
    QTest.keyClick(site, Qt.Key.Key_Down)
    assert panel.state.draft.site_key == "s1"
    assert panel.state.applied is None
    panel.apply_button.setFocus()
    QTest.keyClick(panel.apply_button, Qt.Key.Key_Space)
    assert panel.state.applied.revision == 1
    assert "DB" not in panel.status.text()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
@pytest.mark.parametrize("preview", [False, True])
def test_responsive_query_controls_stay_in_card(window, size, preview):
    if preview:
        window.set_query_catalog(preview_catalog())
    window.resize(*size)
    panel = window.query_panel
    # VER5: 빠른 선택 줄 없이 콤보와 버튼만 보인다. 조회 카드 안쪽 여백은 좌우 20px·상하 16px이다.
    assert not any(row.isVisible() for row in panel.quick_rows)
    assert not hasattr(panel, "choice_button")
    assert [w.text().strip() for w in panel.actions_row.widgets] == ["조건 초기화", "버전(N) 기준 입력", "조회 적용"]
    QTest.qWait(60)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert window.scroll.widget().width() == window.scroll.viewport().width()
    for widget in [*panel.combos.values(), *panel.actions_row.widgets]:
        assert widget.isVisible()
        point = widget.mapTo(panel.card, QPoint(0, 0))
        assert point.x() >= 20
        assert point.x() + widget.width() <= panel.card.width() - 20
        assert point.y() >= 16
        assert point.y() + widget.height() <= panel.card.height() - 16
    for item in panel.quick_buttons.values():
        assert item.height() == (36 if item.property("compactChoice") else 40)
    assert panel.fields_row.columns == (5 if panel.fields_row.width() >= 1048 else 2)
