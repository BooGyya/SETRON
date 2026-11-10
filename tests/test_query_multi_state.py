"""다중 조회 조건의 정규화, 상위 선택 범위와 적용 스냅샷 검증."""

from dataclasses import replace

import pytest

from setron.ui.query_state import (
    UNASSIGNED, FabOption, GroupOption, LineOption, QueryCatalog, QuerySelection, QueryState,
    SiteOption, matches_selection, selection_keys, single_selection,
)


@pytest.fixture
def state():
    return QueryState(QueryCatalog(
        sites=(SiteOption("s1", "사업장 A"), SiteOption("s2", "사업장 B"), SiteOption("s3", "사업장 C")),
        fabs=(FabOption("f1", "Fab A", "s1"), FabOption("f2", "Fab B", "s2"), FabOption("f3", "Fab C", "s3")),
        lines=(LineOption("l1", "Line A", "s1", "f1"), LineOption("l1u", "Line A 미지정 Fab", "s1", None),
               LineOption("l2", "Line B", "s2", "f2"), LineOption("l2u", "Line B 미지정 Fab", "s2", None),
               LineOption("l3", "Line C", "s3", "f3")),
        groups=(GroupOption("g1", "설비 A"), GroupOption("g2", "설비 B"), GroupOption("g3", "설비 C")),
    ))


def test_canonical_selections_preserve_existing_scalar_and_missing_semantics(state):
    selection = QuerySelection(("s2", "s1", "s2"), (UNASSIGNED, "f1", UNASSIGNED), ("l1",), ("g2", "g1"))
    assert selection == QuerySelection(("s1", "s2"), ("f1", UNASSIGNED), "l1", ("g1", "g2"))
    assert selection_keys(None) == () and selection_keys(selection.line_key) == ("l1",)
    assert selection_keys(selection.fab_key) == ("f1", UNASSIGNED)
    assert single_selection(selection.line_key) == "l1" and single_selection(UNASSIGNED) is UNASSIGNED
    assert single_selection(selection.site_key) is None and single_selection(None) is None
    assert matches_selection(None, None) and matches_selection(None, "f3")
    assert matches_selection(selection.fab_key, "f1") and matches_selection(selection.fab_key, None)
    assert not matches_selection(selection.fab_key, "f2") and not matches_selection("f1", None)

    state.set_draft(selection)
    applied = state.apply()
    state.select("site_key", ("s2", "s1"))
    assert not state.has_pending_changes and state.applied is applied
    assert applied.description == "Site 사업장 A, 사업장 B · Fab Fab A, 미지정 · Line Line A · 설비군 설비 A, 설비 B · 기준일 최신"


def test_parent_changes_retain_only_in_scope_children_without_applying(state):
    state.set_draft(QuerySelection(("s1", "s2"), ("f1", "f2", UNASSIGNED),
                                  ("l1", "l1u", "l2", "l2u", UNASSIGNED), ("g1", "g2"), item_family_id=41))
    assert [option.key for option in state.available_fabs()] == ["f1", "f2"]
    assert [option.key for option in state.available_lines()] == ["l1", "l1u", "l2", "l2u"]
    applied = state.apply()

    state.select("site_key", "s2")
    assert state.draft.fab_key == ("f2", UNASSIGNED)
    assert state.draft.line_key == ("l2", "l2u", UNASSIGNED)
    state.select("fab_key", "f2")
    assert state.draft.line_key == ("l2", UNASSIGNED)
    state.select("line_key", "l2")
    state.select("site_key", "s3")
    assert state.draft.fab_key is None and state.draft.line_key is None

    state.select("group_key", ("g2", "g1"))
    assert state.draft.group_key == ("g1", "g2") and state.draft.item_family_id is None
    assert state.applied is applied and applied.selection.item_family_id == 41 and state.has_pending_changes


def test_catalog_replacement_retains_partial_scope_without_applying(state):
    state.set_draft(QuerySelection(("s1", "s2"), ("f1", "f2", UNASSIGNED),
                                  ("l1", "l1u", "l2", UNASSIGNED), ("g1", "g2"), item_family_id=41))
    applied = state.apply()
    catalog = state.catalog
    state.replace_catalog(replace(catalog,
                                  sites=tuple(option for option in catalog.sites if option.key != "s2"),
                                  fabs=tuple(option for option in catalog.fabs if option.site_key != "s2"),
                                  lines=tuple(option for option in catalog.lines if option.site_key != "s2"),
                                  groups=tuple(option for option in catalog.groups if option.key != "g2")))
    assert state.draft == QuerySelection("s1", ("f1", UNASSIGNED), ("l1", "l1u", UNASSIGNED), "g1")
    assert state.applied is applied and not state.is_current(applied) and state.has_pending_changes


@pytest.mark.parametrize("selection", [
    QuerySelection(site_key=()),
    QuerySelection(site_key=("s1", UNASSIGNED)),
    QuerySelection(site_key=("s1", None)),
    QuerySelection(site_key=["s1", "s2"]),
    QuerySelection(site_key=("s1", "unknown")),
    QuerySelection(group_key=("g1", UNASSIGNED)),
    QuerySelection(group_key=("g1", "unknown")),
    QuerySelection(site_key=("s1", "s2"), fab_key=("f1", "f3")),
    QuerySelection(fab_key=("f1", "f2"), line_key=("l1", "l1u")),
])
def test_invalid_multi_selection_is_rejected_without_mutation(state, selection):
    applied = state.apply()
    with pytest.raises(ValueError):
        state.set_draft(selection)
    assert state.draft == QuerySelection() and state.applied is applied
