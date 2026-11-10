"""B-3: Item 식별·저장 상태·한 관측일·Qt 탐색·A 조회와의 독립성."""

from dataclasses import replace
from datetime import date, timedelta
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.item.schemas import Application, Item, ItemState, TargetScope, VersionItem
from setron.features.version.schemas import ReleaseVersion
from setron.ui.app import create_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.item_data import ItemManagerData, item_data_from_source
from setron.ui.theme import install_theme
from setron.ui.version_data import ItemEntry, ModelFamily, ReleaseEntry, SourceReference, VersionCatalog


@pytest.fixture
def data():
    versions = VersionCatalog(
        (ModelFamily(1, "모델 A", 10, "SW A"), ModelFamily(2, "모델 B", 10, "SW A")),
        tuple(ReleaseEntry(ReleaseVersion(i, 10, raw, order), (1, 2)) for i, raw, order in
              ((1, "v99", 1), (2, "v2", 2), (3, "v1000", None), (4, "v3", 3))),
        (ItemEntry(Item(1, 7, "Alarm <원문> & 보존"), (SourceReference("내용", "원본.xlsx", "R1", "변경", "C9", 1),)),
         ItemEntry(Item(2, 7, "다른 Item")), ItemEntry(Item(3, None, "연결 없음"))),
        (VersionItem(1, 1, ItemState.APPLIED, TargetScope.LISTED, (1,)),
         VersionItem(2, 1, ItemState.NOT_APPLIED, TargetScope.LISTED, (1,)),
         VersionItem(4, 1, ItemState.APPLIED, TargetScope.UNKNOWN),
         VersionItem(1, 2, ItemState.APPLIED, TargetScope.ALL)), "테스트 합성 자료")
    day = date(2026, 1, 2)
    def record(i, release, model=1, hpa=False):
        raw = versions.entry(release).release.version_raw
        return EquipmentRecord(i, f"EQ-{i}", day, "Site A", None, "모델 A",
                               (PmVersion("PM1", model, 10, raw, release),), hpa)
    records = (record(1, 1), record(2, 2), record(3, 3), record(4, 1, 2), record(5, 4), record(6, 1, hpa=True))
    return ItemManagerData(versions, records, day, True, "테스트 관측 자료")


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    install_theme(app)
    return app


@pytest.fixture
def window(app, data):
    window = create_window(item_loader=lambda: data)
    window.resize(1600, 960)
    window.show()
    window.navigate("items")
    QTest.qWait(20)
    yield window
    window.close()


def test_search_duplicates_empty_and_raw(data):
    assert [e.item.item_id for e in data.search(" 7 ")] == [1, 2]
    assert [e.item.item_id for e in data.search("ALARM")] == [1]
    assert data.search("<원문>")[0].item.content == "Alarm <원문> & 보존"
    assert len(data.search()) == 3
    assert data.search("없는 검색") == ()


def test_confirmed_order_missing_row_and_four_states(data):
    catalog = data.catalog()
    states = catalog.item_versions(1)
    assert [r.release.release_version_id for r in states] == [1, 2, 4, 3]
    assert states[-1].state is ItemState.UNKNOWN
    assert states[-1].source is None
    summary = catalog.summarize(data.versions.items[0].item, data.records)
    assert summary.counts() == {Application.APPLIED: 1, Application.NOT_APPLIED: 1,
                                Application.UNKNOWN: 2, Application.NOT_TARGET: 1}
    assert summary.hpa_excluded == 1
    assert data.scope_label(states[0].source) == "모델 A"


@pytest.mark.parametrize("change", ["duplicate", "mixed_date", "disconnected", "missing_date", "bad_catalog"])
def test_invalid_observation_contract(data, change):
    changes = {
        "duplicate": dict(records=(*data.records, data.records[0])),
        "mixed_date": dict(records=(replace(data.records[0], base_date=data.base_date + timedelta(days=1)),)),  # 기준일 이후 관측
        "disconnected": dict(observations_connected=False),
        "missing_date": dict(base_date=None),
        "bad_catalog": dict(versions=None),
    }
    with pytest.raises(ValueError):
        replace(data, **changes[change])


def test_adapter_uses_only_latest_date(data):
    source = demo_dashboard_source()
    result = item_data_from_source(data.versions, source)
    assert result.base_date == max(source.dates)
    assert all(r.base_date <= result.base_date for r in result.records)   # 설비별 최신 관측 (QA Q-01)
    assert len({r.equipment_id for r in result.records}) == len(result.records)


def test_mixed_pm_missing_sw_and_unregistered_remain_explicit(window, data):
    page = window.pages["items"]
    original = data.records[0]
    records = (
        replace(original, pms=(original.pms[0], data.records[1].pms[0])),
        replace(original, equipment_id=20, pms=(PmVersion("PM2", 1, 10, "unregistered"),)),
        replace(original, equipment_id=21, pms=(PmVersion("PM2", 1, 10, None),)),
        replace(original, equipment_id=22, pms=()),
    )
    page.loader = lambda: replace(data, records=records)
    page.reload()
    page.open_row(0)
    rows = page.equipment_model.rows
    assert [r[3] for r in rows] == ["미적용", "미확인", "미확인"]
    assert "v99" in rows[0][4] and "v2" in rows[0][4]
    assert "등록 릴리즈 대응 없음" in rows[1][4]
    assert "관측 SW 버전 없음" in rows[2][4]
    assert 22 not in page.equipment_model.ids  # 선택 계열의 PM이 없는 설비


def test_connected_empty_observations_are_zero_not_disconnected(window, data):
    page = window.pages["items"]
    page.loader = lambda: replace(data, records=())
    page.reload()
    page.open_row(0)
    assert "적용 0대" in page.summary.text()
    assert "자료가 필요" not in page.summary.text()
    assert page.state_filter.isEnabled()
    assert not page.equipment_model.rows


def test_detail_keyboard_sources_four_states_and_return(window):
    page = window.pages["items"]
    page.search.setText("7")
    page.result_buttons[1].setFocus()
    QTest.keyClick(page.result_buttons[1], Qt.Key.Key_Return)
    assert page.mode == "detail" and page.selected_id == 1
    assert page.title.text() == "Alarm <원문> & 보존"
    assert page.title.textFormat() == Qt.TextFormat.PlainText
    assert "C9" in page.source_model.rows[0][2]
    assert "미확인 · 저장 행 없음" in page.release_model.rows[-1]
    assert page.state_filter.currentData() == Application.NOT_APPLIED
    assert {row[3] for row in page.equipment_model.rows} == {"미적용"}
    page.state_filter.setCurrentIndex(0)
    assert {row[3] for row in page.equipment_model.rows} == {"적용", "미적용", "미확인", "대상 외"}
    assert "HPA 제외 1대" in page.summary.text()
    assert "2026-01-02" in page.observation_note.text()
    page.state_filter.setCurrentIndex(page.state_filter.findData(Application.UNKNOWN))
    assert len(page.equipment_model.rows) == 2
    assert all(row[3] == "미확인" for row in page.equipment_model.rows)
    QTest.mouseClick(page.result_buttons[1], Qt.MouseButton.LeftButton)
    assert page.search.text() == "7" and page.mode == "list"
    assert page.isAncestorOf(page.result_buttons[1]) and not page.result_buttons[1].isChecked()
    assert page.open_row(1) and page.selected_id == 2
    assert page.state_filter.currentData() == Application.NOT_APPLIED


def test_expanding_result_keeps_search_and_row_geometry(window):
    page = window.pages["items"]
    first, second = page.result_buttons.values()
    window.resize(1280, 760)
    QTest.qWait(30)
    row_size = first.size()
    search_width = page.search.width()
    for button in (first, second, second):
        QTest.mousePress(button, Qt.MouseButton.LeftButton)
        assert button.size() == row_size
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
        QTest.qWait(30)
        assert first.size() == second.size() == row_size
        assert page.search.width() == search_width
    assert page.detail_view.isHidden()


def test_family_list_filters_in_place_and_restores_on_clear(window, data):
    page = window.pages["items"]
    versions = replace(data.versions,
        pairs=(*data.versions.pairs, ModelFamily(3, "모델 C", 20, "SW B")),
        releases=(*data.versions.releases, ReleaseEntry(ReleaseVersion(5, 20, "v1", 1), (3,))),
        version_items=(*data.versions.version_items, VersionItem(5, 3, ItemState.APPLIED, TargetScope.ALL)))
    page.loader = lambda: replace(data, versions=versions)
    page.reload()
    assert page.results_card.isVisible() and page.count.text() == "SW A 아이템 전체 2건"
    assert tuple(page.result_buttons) == (1, 2)
    page.result_buttons[1].click()
    assert page.result_rows.indexOf(page.detail_view) == page.result_rows.indexOf(page.result_buttons[1]) + 1
    page.search.setText("Alarm")
    assert tuple(page.result_buttons) == (1,) and page.detail_view.isHidden()
    page.search.clear()
    assert tuple(page.result_buttons) == (1, 2)
    page.select_family(20)
    assert page.count.text() == "SW B 아이템 전체 1건" and tuple(page.result_buttons) == (3,)


def test_unlinked_item_does_not_claim_all_equipment_not_target(window):
    page = window.pages["items"]
    assert not page.open_row(2) and 3 not in page.result_buttons
    assert page.open_item_id(3)
    assert not page.equipment_model.rows
    assert "미확인" in page.summary.text()
    assert not page.state_filter.isEnabled()
    assert "연결된 릴리즈가 없습니다" in page.release_note.text()


def test_custom_version_catalog_does_not_use_demo_observations(app, data):
    window = create_window(version_loader=lambda: data.versions)
    page = window.pages["items"]
    page.open_row(0)
    assert not page.data.observations_connected
    assert not page.equipment_model.rows
    assert "미연결" in page.observation_note.text()
    window.close()


def test_failure_retry_and_removed_items_clear_detail(window, data):
    page = window.pages["items"]
    page.open_row(0)
    def fail():
        raise RuntimeError("private connection info")
    page.loader = fail
    page.reload()
    assert page.data is None and page.mode == "list" and page.selected_id is None
    assert not page.release_model.rows and not page.equipment_model.rows
    assert page.content.text() == ""
    assert "private" not in page.notice.text()
    page.loader = lambda: data
    QTest.mouseClick(page.refresh, Qt.MouseButton.LeftButton)
    assert page.data is data and page.search.isEnabled()
    page.loader = lambda: replace(data, versions=replace(data.versions, items=(), version_items=()))
    page.reload()
    assert not page.result_buttons and not page.open_row(0)


def test_pagination_menu_return_and_a_state_unchanged(window, data):
    page = window.pages["items"]
    page.loader = lambda: replace(data, versions=replace(data.versions,
        items=tuple(ItemEntry(Item(i, i, f"항목 {i}")) for i in range(1, 29)),
        version_items=tuple(VersionItem(1, i, ItemState.APPLIED, TargetScope.ALL) for i in range(1, 29))))
    page.reload()
    window.navigate("dashboard")
    window.query_panel.apply_draft()
    applied, targets = window.query_state.applied, window.query_state.target_draft
    window.navigate("items")
    QTest.mouseClick(page.next, Qt.MouseButton.LeftButton)
    assert page.page_index == 1
    page.open_row(3)
    selected = page.selected_id
    window.navigate("versions")
    window.navigate("items")
    assert page.selected_id == selected and page.mode == "detail"
    assert not window.query_panel.isVisible()
    assert not window.header.actions["equipment_list"].isEnabled()
    page.back_to_list()
    assert page.page_index == 1 and page.visible_entries[3].item.item_id == page.selected_id
    assert window.query_state.applied is applied and window.query_state.target_draft == targets
    page.search.setText("없는 검색")
    assert not page.result_buttons and page.page_index == 0
    assert not page.previous.isEnabled() and not page.next.isEnabled()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
def test_narrow_window_and_long_original_content(window, data, size):
    page = window.pages["items"]
    page.loader = lambda: replace(data, versions=replace(data.versions, items=(
        replace(data.versions.items[0], item=Item(1, 7, "<원문>&" * 250)), *data.versions.items[1:])))
    page.reload()
    window.resize(*size)
    QTest.qWait(30)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    page.open_row(0)
    QTest.qWait(30)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert page.title.text() == "<원문>&" * 250


def test_release_note_groups_stored_rows_only_and_opens_detail(window):
    page = window.pages["items"]
    assert page.family_id == 10 and page.family_group.button(10).isChecked()
    rows = page.release_note_model.rows
    assert [r[0] for r in rows] == ["v99", "v99", "v2", "v3", "v1000"]
    assert page.release_note_model.ids == (1, 2, 1, 1, None)
    assert page.release_note_model.group_starts == {0, 2, 3, 4}
    assert [r[3] for r in rows[:4]] == ["포함", "포함", "미포함", "포함"]
    # 순서 미확인 버전은 저장 행이 없으므로 이전 버전 Item을 상속하지 않고 미확인으로 남긴다.
    assert "저장된 Item 행 없음" in rows[4][2] and "순서 미확인" in page.release_note_model.dates[4]
    assert "원본 행 미연결" in page.release_subtitle.text() and "관리값" in page.release_subtitle.text()
    assert page.ai_search.isEnabled()
    assert not page.open_release_row(4)
    assert page.open_release_row(1) and page.mode == "detail" and page.selected_id == 2
    page.back_to_list()
    page.search.setText("다른")
    assert page.release_note_model.ids == (2,)
