"""B-1: 확인된 릴리즈 순서·저장 Item 근거·출처·Qt 목록/상세·A 상태 분리."""

from dataclasses import replace
from datetime import date
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTableView

from setron.features.item.schemas import Item, ItemState, TargetScope, VersionItem
from setron.features.version.schemas import ReleaseVersion
from setron.ui.app import create_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.theme import ASSETS, install_theme, svg_size
from setron.ui.version_data import (
    ItemEntry, ModelFamily, ReleaseEntry, SourceReference, VersionCatalog, catalog_from_dashboard,
)
from setron.ui.version_demo import demo_version_catalog


@pytest.fixture
def catalog():
    source = (SourceReference("버전 원문", "원본 & <릴리즈>.xlsx", "R3", "릴리즈", "B3:B4", 7),)
    return VersionCatalog(
        (ModelFamily(1, "모델 A", 10, "일반 SW"), ModelFamily(1, "모델 A", 20, "DFB SW"),
         ModelFamily(2, "모델 B", 10, "일반 SW"), ModelFamily(3, "빈 모델", 30, "빈 계열")),
        (ReleaseEntry(ReleaseVersion(101, 10, "v99", 1, date(2026, 1, 2)), (1, 2), source, True),
         ReleaseEntry(ReleaseVersion(102, 10, "v2+Hotfix_DFB", 2), (1, 2)),
         ReleaseEntry(ReleaseVersion(103, 10, "v0-별도흐름", 2), (1, 2)),
         ReleaseEntry(ReleaseVersion(104, 10, "v1000", None), (1, 2)),
         ReleaseEntry(ReleaseVersion(201, 20, "v99_DFB", 1), (1,))),
        (ItemEntry(Item(1, 1, "승인 <확인> & 원문"), (SourceReference("내용", "item.xlsx", None, "항목", "D5"),)),
         ItemEntry(Item(2, 1, "제거된 항목")), ItemEntry(Item(3, None, "다른 모델 전용")),
         ItemEntry(Item(4, 4, "범위 미확인")), ItemEntry(Item(5, 5, "다른 계열"))),
        (VersionItem(101, 1, ItemState.APPLIED, TargetScope.ALL),
         VersionItem(101, 2, ItemState.NOT_APPLIED, TargetScope.ALL),
         VersionItem(101, 3, ItemState.APPLIED, TargetScope.LISTED, (2,)),
         VersionItem(101, 4, ItemState.APPLIED, TargetScope.UNKNOWN),
         VersionItem(201, 5, ItemState.APPLIED, TargetScope.ALL)),
        "테스트 전용 합성 자료 · DB 미연결")


def test_confirmed_order_not_text_or_date_and_unknown_always_last(catalog):
    assert [e.release.release_version_id for e in catalog.list_releases(1, 10)] == [102, 103, 101, 104]
    assert [e.release.release_version_id for e in catalog.list_releases(1, 10, descending=False)] == [101, 102, 103, 104]
    assert "중복" in catalog.order_label(catalog.entry(102).release)
    assert "미확인" in catalog.order_label(catalog.entry(104).release)


def test_scope_and_search_use_ids_and_preserve_raw(catalog):
    assert [e.release.release_version_id for e in catalog.list_releases(1, 20)] == [201]
    assert catalog.list_releases(2, 20) == ()
    assert catalog.list_releases(3, 30) == ()
    assert catalog.list_releases(1, 10, " HOTFIX ")[0].release.version_raw == "v2+Hotfix_DFB"
    assert catalog.list_releases(1, 10, "<릴리즈>")[0].release.release_version_id == 101


def test_item_four_states_and_no_inheritance(catalog):
    rows = {item.item.item_id: (state, scope, application) for item, state, scope, application in catalog.item_rows(101, 1)}
    assert rows[1] == ("포함", "전체 모델", "적용")
    assert rows[2] == ("미포함", "전체 모델", "미적용")
    assert rows[3] == ("포함", "모델 B", "대상 외")
    assert rows[4] == ("포함", "미확인", "미확인")
    assert 5 not in rows  # 다른 계열 Item을 섞지 않는다.
    later = catalog.item_rows(102, 1)
    assert all(state == "미확인 · 저장 행 없음" for _, state, _, _ in later)
    assert catalog.included_count(102) == 0
    assert catalog.item_rows(201, 2) == ()


@pytest.mark.parametrize("change", ["duplicate_release", "duplicate_item", "duplicate_row", "missing_item", "missing_release", "bad_pair", "bad_order", "bad_scope", "wrong_family_scope", "duplicate_scope"])
def test_invalid_contract_is_rejected_instead_of_overwriting(catalog, change):
    changes = {
        "duplicate_release": dict(releases=(*catalog.releases, catalog.releases[0])),
        "duplicate_item": dict(items=(*catalog.items, catalog.items[0])),
        "duplicate_row": dict(version_items=(*catalog.version_items, catalog.version_items[0])),
        "missing_item": dict(version_items=(VersionItem(101, 999, ItemState.APPLIED),)),
        "missing_release": dict(version_items=(VersionItem(999, 1, ItemState.APPLIED),)),
        "bad_pair": dict(releases=(replace(catalog.releases[0], model_ids=(999,)),)),
        "bad_order": dict(releases=(replace(catalog.releases[0], release=ReleaseVersion(101, 10, "v99", True)),)),
        "bad_scope": dict(version_items=(VersionItem(101, 1, ItemState.APPLIED, TargetScope.ALL, (1,)),)),
        "wrong_family_scope": dict(version_items=(VersionItem(101, 1, ItemState.APPLIED, TargetScope.LISTED, (3,)),)),
        "duplicate_scope": dict(version_items=(VersionItem(101, 1, ItemState.APPLIED, TargetScope.LISTED, (1, 1)),)),
    }
    with pytest.raises(ValueError):
        replace(catalog, **changes[change])


def test_injected_a_source_is_not_enriched_with_fake_items_or_dates():
    source = demo_dashboard_source()
    data = catalog_from_dashboard(source)
    assert data.items == data.version_items == ()
    assert all(not e.sources and e.release.released_at is None for e in data.releases)
    assert {e.release.release_version_id for e in data.releases} == {
        r.release_version_id for family in source.families.values() for r in family.releases}
    demo = demo_version_catalog(source)
    assert demo.items and "합성" in demo.source_label
    assert all(e.release is data.entry(e.release.release_version_id).release for e in demo.releases)


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


@pytest.fixture
def window(app, catalog):
    result = create_window(version_loader=lambda: catalog)
    result.resize(1600, 960)
    result.show()
    result.navigate("versions")
    QTest.qWait(30)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def test_menu_route_and_keyboard_detail_preserve_a_state(window, app):
    page = window.pages["versions"]
    assert window.header.title.text() == "릴리즈 버전 목록"
    assert not window.query_panel.isVisible()
    assert not window.header.actions["equipment_list"].isEnabled()
    state = window.query_state
    before = state.draft, state.target_draft, state.applied
    page.table.setCurrentIndex(page.list_model.index(0, 0))
    page.table.setFocus()
    QTest.keyClick(page.table, Qt.Key.Key_Return)
    app.processEvents()
    assert page.mode == "detail" and page.selected_id == 102
    assert window.header.title.text() == "릴리즈 버전 상세"
    assert "v2+Hotfix_DFB" == page.detail_title.text()
    assert (state.draft, state.target_draft, state.applied) == before
    QTest.mouseClick(page.back, Qt.MouseButton.LeftButton)
    assert page.mode == "list" and page.table.currentIndex().row() == 0


def test_detail_displays_dates_unknowns_sources_and_plain_text(window):
    page = window.pages["versions"]
    page.open_row(2)
    assert page.selected_id == 101
    assert ("출시일", "2026-01-02") in page.info_model.rows
    assert "승인 <확인> & 원문" == page.item_model.data(page.item_model.index(0, 1))
    assert any("B3:B4" in str(row) and "R3" in str(row) for row in page.source_model.rows)
    assert any("D5" in str(row) for row in page.source_model.rows)
    assert any("출처 미확인" in str(row) for row in page.source_model.rows)
    assert page.detail_title.textFormat() == Qt.TextFormat.PlainText
    assert page.item_table.editTriggers() == QTableView.EditTrigger.NoEditTriggers
    page.back_to_list()
    page.open_row(0)
    assert ("출시일", "미확인") in page.info_model.rows
    assert all("미확인" in row[2] for row in page.item_model.rows)


def test_model_family_cascade_empty_search_and_navigation_preserve_state(window):
    page = window.pages["versions"]
    page.family_filter.setCurrentIndex(page.family_filter.findData(20))
    assert page.list_model.ids == (201,)
    page.model_filter.setCurrentIndex(page.model_filter.findData(2))
    assert page.family_filter.currentData() == 10
    assert page.family_filter.findData(20) == -1
    page.search.setText("Hotfix")
    page.open_row(0)
    window.navigate("dashboard")
    window.navigate("versions")
    assert page.mode == "detail" and page.selected_id == 102
    assert not window.query_panel.isVisible()
    page.back_to_list()
    assert page.search.text() == "Hotfix" and page.list_model.ids == (102,)
    page.search.setText("찾을 수 없는 버전")
    assert page.empty.isVisible() and not page.table.isVisible()
    assert not page.open_row(0)
    page.search.clear()
    page.model_filter.setCurrentIndex(page.model_filter.findData(3))
    assert "등록된 릴리즈가 없습니다" in page.empty.text()


def test_pagination_retains_identity_and_list_position(window, catalog):
    page = window.pages["versions"]
    extra = tuple(ReleaseEntry(ReleaseVersion(300 + i, 10, f"test-{i}", 10 + i), (1,)) for i in range(20))
    page.loader = lambda: replace(catalog, releases=(*catalog.releases, *extra))
    page.reload()
    page.next.click()
    assert page.page_index == 1
    expected = page.list_model.ids[4]
    page.open_row(4)
    assert page.selected_id == expected
    page.back_to_list()
    assert page.page_index == 1 and page.table.currentIndex().row() == 4
    page.search.setText("v1000")
    assert page.page_index == 0 and not page.next.isEnabled()


def test_reload_failure_clears_old_detail_and_retry_replaces_catalog(window, catalog):
    page = window.pages["versions"]
    page.open_row(0)
    def failed():
        raise RuntimeError("접속 정보 등 내부 오류는 화면에 노출하지 않는다")
    page.loader = failed
    page.reload()
    assert page.catalog is None and page.selected_id is None and page.mode == "list"
    assert page.list_model.rows == page.info_model.rows == page.item_model.rows == page.source_model.rows == ()
    assert page.status.tone == "danger" and not page.open_row(0)
    page.loader = lambda: replace(catalog, pairs=(), releases=(), items=(), version_items=())
    page.refresh.click()
    assert page.catalog is not None and page.status.tone == "info"
    assert not page.model_filter.isEnabled()
    page.loader = lambda: catalog
    page.refresh.click()
    assert page.list_model.ids and page.model_filter.isEnabled()


def test_reload_does_not_keep_removed_model_or_stale_release(window, catalog):
    page = window.pages["versions"]
    page.family_filter.setCurrentIndex(page.family_filter.findData(20))
    page.open_row(0)
    page.loader = lambda: VersionCatalog((ModelFamily(7, "새 모델", 70, "새 계열"),),
                                         (ReleaseEntry(ReleaseVersion(700, 70, "new", 1), (7,)),))
    page.reload()
    assert page.selected_id is None and page.mode == "list"
    assert page.model_filter.currentData() == 7 and page.family_filter.currentData() == 70
    assert page.list_model.ids == (700,)
    assert page.source_model.rows == ()
    page.open_row(0)
    assert page.selected_id == 700 and "자료" not in page.detail_title.text()


def test_zero_included_count_does_not_claim_mapping_complete(window, catalog):
    page = window.pages["versions"]
    assert "매핑 미완료" in page.list_model.rows[0][3]
    assert page.list_model.rows[2][3] == "3건"
    page.loader = lambda: VersionCatalog(catalog.pairs, catalog.releases)
    page.reload()
    page.open_row(0)
    assert page.item_empty.isVisible() and "없다고 확정하지" in page.item_empty.text()
    assert not page.item_table.isVisible()


def test_applied_dashboard_and_target_draft_unchanged_by_versions(window):
    window.navigate("dashboard")
    query = window.query_panel.apply_draft()
    snapshot = window.pages["dashboard"].snapshot
    targets = window.query_state.target_draft
    window.navigate("versions")
    page = window.pages["versions"]
    page.search.setText("v99")
    page.open_row(0)
    page.reload()
    assert window.query_state.applied is query
    assert window.query_state.target_draft == targets
    assert window.pages["dashboard"].snapshot is snapshot
    window.navigate("dashboard")
    assert window.query_panel.isVisible()
    assert window.header.actions["equipment_list"].isEnabled()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
def test_responsive_tables_long_raw_and_original_assets(window, catalog, size):
    page = window.pages["versions"]
    long_raw = "<v90_" + "긴원문" * 120 + ">_DFB+Hotfix"
    first = replace(catalog.releases[0], release=replace(catalog.releases[0].release, version_raw=long_raw))
    page.loader = lambda: replace(catalog, releases=(first, *catalog.releases[1:]))
    page.reload()
    window.resize(*size)
    QTest.qWait(60)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert page.table.width() <= page.list_card.width()
    page.open_row(2)
    QTest.qWait(60)
    assert page.detail_title.text() == long_raw
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert page.back.mapTo(page, QPoint(0, 0)).x() + page.back.width() <= page.width()
    assert page.info_table.width() <= page.overview.width()
    nav = window.nav["versions"]
    asset = nav.picture.property("assetFile")
    assert asset == "77fb3.svg" and (ASSETS / asset).stat().st_size > 0
    assert nav.picture.pixmap().deviceIndependentSize().toSize() == svg_size(asset)
    window.navigate("dashboard")
    QTest.qWait(20)
    assert window.scroll.horizontalScrollBar().maximum() == 0
