"""버전(N) 기준 입력: 줄마다 N 칸 선택, 기본 후보, 막힘/경고, 저장 이력·되돌리기, 저장 그룹으로 계산."""

import os
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from setron.ui.app import create_window
from setron.ui.group_data import SLOT_LABELS, TIER_LABELS, Slot, Tier
from setron.ui.group_dialog import SLOT_CHOICES, VersionGroupDialog
from setron.ui.query_state import QuerySelection
from setron.ui.theme import install_theme

PAIR = (101, 1001)  # 예시 자료 C3 · 계열 1001: v1.0.1(10011) · v1.0.2(10012, 기본 목표) · v1.0.3(10013)


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    install_theme(app)
    result = create_window()
    result.resize(1400, 900)
    result.show()
    QTest.qWait(20)
    yield result
    if result.group_dialog is not None:
        result.group_dialog.reject()
    result.close()
    result.deleteLater()
    app.processEvents()


def open_editor(window) -> VersionGroupDialog:
    window.query_panel.goal_button.click()
    QTest.qWait(20)
    dialog = window.group_dialog
    assert isinstance(dialog, VersionGroupDialog)
    dialog.pair_buttons[PAIR].click()
    return dialog


def tiers(dialog):
    return {rid: chip.text() for rid, chip in dialog.tier_labels.items()}


def choose(dialog, release_id, slot):
    if not dialog.details_button.isChecked():
        dialog.details_button.click()
    dialog.slot_combos[release_id].setCurrentIndex(SLOT_CHOICES.index(slot))


def test_rows_follow_version_order_and_single_target_uses_same_tiers(window):
    dialog = open_editor(window)
    assert list(dialog.slot_combos) == [10013, 10012, 10011]  # 최신 → 오래된 순
    assert dialog.slot_combos[10012].currentText() == "N" and dialog.slot_combos[10011].currentText() == "—"
    # 그룹 저장 전(목표 1개)에도 tier_of 단계로 색을 낸다.
    assert tiers(dialog) == {10013: TIER_LABELS[Tier.ABOVE], 10012: TIER_LABELS[Tier.AT], 10011: TIER_LABELS[Tier.ONE_BELOW]}
    assert "저장 이력이 없습니다" in dialog.history.itemAt(0).widget().text()


def test_click_multiple_n_versions_and_keep_each_pair_draft_until_save(window):
    window.query_panel.set_draft(QuerySelection(group_key="c3", as_of=date(2026, 5, 21)))
    window.query_panel.apply_draft()
    dashboard, heatmap = window.pages["dashboard"], window.pages["heatmap"]
    before = dashboard.snapshot
    below = next(tile for tile in heatmap.tiles if tile.tier is Tier.ONE_BELOW)

    dialog = open_editor(window)
    other_pair = (102, 1002)
    assert dialog.pair_buttons[PAIR].isChecked() and not dialog.pair_buttons[other_pair].isChecked()
    assert not dialog.slot_combos[10012].isVisibleTo(dialog)

    for release_id in (10011, 10013):
        check = dialog.version_checks[release_id]
        text_position = check.caption.mapTo(dialog, QPoint(4, check.caption.height() // 2))
        QTest.mouseClick(dialog.windowHandle(), Qt.MouseButton.LeftButton, pos=text_position)
        assert check.isChecked() and dialog.slot_combos[release_id].currentText() == "N"
    assert dialog.groups().members(Slot.N) == {10011, 10012, 10013}

    check = dialog.version_checks[10013]
    check.setFocus()
    QTest.keyClick(check, Qt.Key.Key_Space)
    assert not check.isChecked() and dialog.slot_combos[10013].currentText() == "—"

    dialog.pair_buttons[other_pair].click()
    assert dialog.pair_buttons[other_pair].isChecked() and not dialog.pair_buttons[PAIR].isChecked()
    assert dialog.groups().members(Slot.N) == {10022}
    dialog.pair_buttons[PAIR].click()
    assert {rid for rid, button in dialog.version_checks.items() if button.isChecked()} == {10011, 10012}
    assert dialog.store.load(PAIR) is None
    dialog.save_button.click()
    assert dialog.store.load(PAIR).groups.members(Slot.N) == {10011, 10012}
    assert dashboard.snapshot is heatmap.snapshot is before

    dialog.close_button.click()
    reopened = open_editor(window)
    assert {rid for rid, button in reopened.version_checks.items() if button.isChecked()} == {10011, 10012}
    reopened.close_button.click()
    window.query_panel.apply_draft()
    assert dashboard.snapshot is heatmap.snapshot and dashboard.snapshot.summary.compliant > before.summary.compliant
    assert next(tile for tile in heatmap.tiles if tile.equipment_id == below.equipment_id).tier is Tier.AT
    detail = next(item for item in window.pages["equipment"].data.details if item.record.equipment_id == below.equipment_id)
    assert detail.whole.tier is Tier.AT


def test_suggest_fills_neighbours_and_empty_n_blocks_save(window):
    dialog = open_editor(window)
    assert dialog.suggest()
    assert not dialog.status.text()
    assert dialog.slot_combos[10011].currentText() == SLOT_LABELS[Slot.N_MINUS_1]
    assert dialog.slot_combos[10013].currentText() == SLOT_LABELS[Slot.N_PLUS_1]
    choose(dialog, 10012, None)
    texts = [dialog.issues.itemAt(i).widget().text() for i in range(dialog.issues.count())]
    assert any(text.startswith("막힘") for text in texts)
    assert not dialog.save_button.isEnabled() and not dialog.suggest_button.isEnabled()


def test_crossed_order_is_warning_not_block(window):
    dialog = open_editor(window)
    choose(dialog, 10011, Slot.N_PLUS_1)  # 더 오래된 버전을 더 높은 칸에
    texts = [dialog.issues.itemAt(i).widget().text() for i in range(dialog.issues.count())]
    assert any(text.startswith("경고") for text in texts) and dialog.save_button.isEnabled()


def test_save_then_apply_uses_saved_groups_and_history_can_revert(window):
    window.query_panel.apply_draft()
    before = window.pages["dashboard"].snapshot.summary.compliant
    dialog = open_editor(window)
    choose(dialog, 10012, Slot.N_MINUS_1)
    choose(dialog, 10013, Slot.N)  # 목표를 한 단계 올린다
    assert tiers(dialog)[10012] == TIER_LABELS[Tier.ONE_BELOW]
    assert dialog.save() and "조회 적용" in dialog.status.text()
    assert window.pages["dashboard"].snapshot.summary.compliant == before  # 저장만으로 화면 결과는 바뀌지 않는다
    window.query_panel.apply_draft()
    dashboard = window.pages["dashboard"]
    assert dashboard.snapshot.summary.compliant < before
    dashboard.metrics["upgrade"].action.click()
    heatmap = window.pages["heatmap"]
    assert window.route == "heatmap" and len(heatmap.visible_ids) == dashboard.snapshot.summary.upgrade_needed
    tile_id = heatmap.visible_ids[0]
    heatmap.tile_buttons[tile_id].click()
    drawer = window.equipment_drawer
    assert drawer.isVisible() and drawer.summary_title.text() == "목표 미달"
    assert any("미달 판정 근거" in widget.text() for widget in drawer.findChildren(QLabel))
    drawer.close_panel()
    heatmap.below_filter.click()
    assert len(heatmap.visible_ids) >= dashboard.snapshot.summary.upgrade_needed
    first = dialog.store.load(PAIR).history[0]
    choose(dialog, 10011, Slot.N_MINUS_2)
    assert dialog.save()
    assert len(dialog.store.load(PAIR).history) == 2
    assert dialog.revert(first)
    saved = dialog.store.load(PAIR)
    assert len(saved.history) == 3 and saved.groups == first.groups and "되돌림" in saved.history[-1].memo


def test_repeated_save_and_revert_to_current_do_not_pile_up(window):
    """같은 기준을 다시 저장하거나 지금 기준과 같은 이력으로 되돌리면 이력이 늘지 않고, 그런 이력의 되돌리기는 꺼진다."""
    dialog = open_editor(window)
    choose(dialog, 10013, Slot.N)
    assert dialog.save()
    count = len(dialog.store.load(PAIR).history)
    assert dialog.save() and "같아 저장하지 않았습니다" in dialog.status.text()
    assert len(dialog.store.load(PAIR).history) == count
    current = dialog.store.load(PAIR).history[-1]
    assert dialog.revert(current) and len(dialog.store.load(PAIR).history) == count
