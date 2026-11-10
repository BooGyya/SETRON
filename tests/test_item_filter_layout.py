"""설비군 제목과 선택 영역을 구분하고, 관리 필터의 추가 버튼을 입력칸에 맞춘다."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtTest import QTest

from setron.ui.app import create_window
from setron.ui.version_data import ModelFamily

from test_ui_items import app, data


@pytest.fixture
def window(app, data):
    names = ("C3 구UI", "C3 통합UI", "C4", "O2", "O3", "O5", "Inline O2", "Cluster O2", "HPA",
             "C3 구UI (파운드리)", "C3 통합UI (파운드리)", "C4 (파운드리)", "O2 (파운드리)", "Inline Ox", "Cluster Ox")
    pairs = (*tuple(ModelFamily(1, "모델 A", 10 + i, name) for i, name in enumerate(names)),
             ModelFamily(2, "모델 B", 10, names[0]))
    value = replace(data, versions=replace(data.versions, pairs=pairs))
    result = create_window(item_loader=lambda: value)
    result.resize(1600, 960)
    result.show()
    result.navigate("items")
    yield result
    result.close()
    result.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("width", [960, 1100, 1920])
def test_family_title_is_outside_wrapping_choice_group(window, width):
    page = window.pages["items"]
    window.resize(width, 900)
    QTest.qWait(50)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert page.family_title.text() == "SW 계열"
    assert not page.family_host.isAncestorOf(page.family_title)
    title_bottom = page.family_title.mapTo(page.list_view, page.family_title.rect().bottomLeft()).y()
    choices_top = page.family_choices.mapTo(page.list_view, QPoint()).y()
    assert title_bottom < choices_top
    assert len(page.family_group.buttons()) == 15
    for chip in page.family_group.buttons():
        assert page.family_host.rect().contains(chip.geometry())
    if width < 1920:
        assert len({chip.y() for chip in page.family_group.buttons()}) > 1
    selected = page.family_group.button(22)
    selected.click()
    assert page.family_id == 22 and selected.isChecked()
    page.render_families()
    QTest.qWait(30)
    assert page.family_id == 22 and page.family_group.button(22).isChecked()
    assert page.family_flow.count() == 15  # 선택지 이외의 안내·가짜 추가 버튼은 표시하지 않는다.
    assert all(page.family_flow.itemAt(i).widget() in page.family_group.buttons()
               for i in range(page.family_flow.count()))


@pytest.mark.parametrize("width", [960, 1100, 1920])
def test_registration_visible_before_choosing_rule(window, width):
    page = window.pages["items"]
    page.tabs.setCurrentIndex(1)
    manager = page.manager
    window.resize(width, 900)
    QTest.qWait(50)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert manager.filters.isHidden()
    assert manager.detail_card.isHidden()
    assert manager.add_button.isVisible()
    assert manager.add_button.text() == "+ 위험 기준 등록"
    assert manager.rect().contains(manager.add_button.mapTo(manager, QPoint()))


def test_empty_and_reloaded_family_group_keep_heading_and_single_message(window):
    page = window.pages["items"]
    original = page.data
    page.data = None
    page.render_families()
    assert page.family_title.text() == "SW 계열"
    assert page.family_empty.text() == "조회 결과 미확인" and not page.family_empty.isHidden()
    assert page.family_flow.count() == 1
    page.data = original
    page.render_families()
    assert page.family_empty.isHidden() and page.family_flow.count() == 15


def test_foundry_labels_change_display_only_and_keep_family_selection(window):
    page = window.pages["items"]
    original_pairs = page.data.versions.pairs
    names = {pair.family_id: pair.family_name for pair in original_pairs}
    assert sum("파운드리" in name for name in names.values()) == 4
    for family_id, name in names.items():
        assert page.family_group.button(family_id).text() == name.replace("파운드리", "Foundry")
    page.family_group.button(22).click()
    assert page.family_id == 22 and page.family_group.button(22).text() == "O2 (Foundry)"
    page.render_families()
    assert page.family_group.button(22).isChecked()
    assert page.family_group.button(22).text() == "O2 (Foundry)"
    assert page.data.versions.pairs == original_pairs
    assert names[22] == "O2 (파운드리)"
