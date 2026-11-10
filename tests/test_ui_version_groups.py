"""버전(N) 기준 입력: 줄마다 N 칸 선택, 기본 후보, 막힘/경고, 저장 이력·되돌리기, 저장 그룹으로 계산."""

import os
from datetime import date, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from setron.ui.app import create_window
from setron.ui.appearance_data import PersonalSettings
from setron.ui.group_data import SLOT_LABELS, TIER_LABELS, Slot, Tier, VersionGroups, group_store_for
from setron.ui.group_dialog import BLOCK_INK, SLOT_CHOICES, VersionGroupDialog
from setron.ui.query_state import QuerySelection
from setron.ui.theme import install_theme

PAIR = (101, 1001)  # 예시 자료 C3 · 계열 1001: v1.0.1(10011) · v1.0.2(10012, 기본 목표) · v1.0.3(10013)


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    install_theme(app)
    store = PersonalSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(app.appearance_manager, "store", store)
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


def open_editor(window, *, pair_key=PAIR) -> VersionGroupDialog:
    window.query_panel.goal_button.click()
    QTest.qWait(20)
    dialog = window.group_dialog
    assert isinstance(dialog, VersionGroupDialog)
    if pair_key is not None:
        choose_pair(dialog, pair_key)
    return dialog


def choose_pair(dialog, key):
    dialog.pair_combo.setCurrentIndex(next(i for i, pair in enumerate(dialog.pairs) if pair.key == key))


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
    assert not any(widget.text().startswith(("버전을 클릭하면", "기준 N 1개 선택")) for widget in dialog.findChildren(QLabel))


def test_reopen_prefers_last_selected_pair_and_only_saved_n(window, monkeypatch):
    preferences = window.pages["settings"].store
    source = window.pages["dashboard"].source
    group_store_for(source).save((102, 1002), VersionGroups.single(10021), source.families[1002])
    initial = open_editor(window, pair_key=None)
    assert initial.pair.key == (102, 1002) and initial.version_checks[10021].isChecked()
    initial.close_button.click()

    preferences.remember_version_group_pair((999, 999))
    window.query_panel.set_draft(QuerySelection(group_key="c4"))
    dialog = open_editor(window, pair_key=None)
    assert dialog.pair.key == (102, 1002)  # unavailable preference falls back to the query group

    choose_pair(dialog, PAIR)
    dialog.version_checks[10011].click()
    assert dialog.save()
    dialog.version_checks[10013].click()
    dialog.close_button.click()

    reopened = open_editor(window, pair_key=None)
    assert reopened.pair.key == PAIR
    assert {rid for rid, check in reopened.version_checks.items() if check.isChecked()} == {10011, 10012}
    reopened.close_button.click()

    fresh = PersonalSettings(QSettings(preferences.settings.fileName(), QSettings.Format.IniFormat))
    assert fresh.version_group_pair() == PAIR
    monkeypatch.setattr(QApplication.instance().appearance_manager, "store", fresh)
    other_window = create_window(source)
    other_window.show()
    try:
        other_window.query_panel.set_draft(QuerySelection(group_key="c4"))
        dialog = open_editor(other_window, pair_key=None)
        assert dialog.pair.key == PAIR
        assert {rid for rid, check in dialog.version_checks.items() if check.isChecked()} == {10011, 10012}
        dialog.close_button.click()
    finally:
        other_window.close()
        other_window.deleteLater()


def test_click_multiple_n_versions_and_keep_each_pair_draft_until_save(window):
    window.query_panel.set_draft(QuerySelection(group_key="c3", as_of=date(2026, 5, 21)))
    window.query_panel.apply_draft()
    dashboard, heatmap = window.pages["dashboard"], window.pages["heatmap"]
    before = dashboard.snapshot
    below = next(tile for tile in heatmap.tiles if tile.tier is Tier.ONE_BELOW)

    dialog = open_editor(window)
    other_pair = (102, 1002)
    assert dialog.pair_combo.isVisibleTo(dialog) and dialog.pair.key == PAIR
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

    choose_pair(dialog, other_pair)
    assert dialog.pair.key == other_pair
    assert dialog.groups().members(Slot.N) == {10022}
    choose_pair(dialog, PAIR)
    assert {rid for rid, button in dialog.version_checks.items() if button.isChecked()} == {10011, 10012}
    assert dialog.store.load(PAIR) is None
    dialog.save_button.click()
    assert dialog.store.load(PAIR).groups.members(Slot.N) == {10011, 10012}
    frame, caption, _ = dialog.history_rows[dialog.store.load_current(PAIR).sequence]
    assert frame.property("activeHistory") and caption.text().splitlines()[1] == "v1.0.2, v1.0.1"
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
    warning = dialog.issues.itemAt(0).widget()
    assert warning.text().startswith("경고 · 버전 순서와 그룹 순서가 어긋납니다")
    assert warning.styleSheet() == f"color: {BLOCK_INK};" and dialog.save_button.isEnabled()


def test_default_candidates_recalculate_after_selecting_another_n(window):
    dialog = open_editor(window)
    dialog.version_checks[10012].click()
    dialog.version_checks[10011].click()
    dialog.suggest_button.click()
    assert dialog.groups().members(Slot.N_PLUS_2) == {10013}
    assert dialog.save()
    dialog.close_button.click()

    dialog = open_editor(window)
    dialog.version_checks[10012].click()
    dialog.suggest_button.click()
    assert dialog.groups().members(Slot.N) == {10011, 10012}
    assert dialog.groups().members(Slot.N_PLUS_1) == {10013}
    assert not dialog.groups().members(Slot.N_PLUS_2)


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
    history = dialog.store.load(PAIR).history
    assert len(history) == 2
    assert dialog.revert(first)
    assert dialog.store.load_current(PAIR) == first
    assert dialog.store.load(PAIR).history == history
    assert dialog.save() and "같아 저장하지 않았습니다" in dialog.status.text()
    assert dialog.store.load(PAIR).history == history


def test_repeated_save_and_revert_to_current_do_not_pile_up(window):
    """같은 기준을 다시 저장하거나 현재 적용 중인 기록을 선택해도 이력이 늘지 않는다."""
    dialog = open_editor(window)
    choose(dialog, 10013, Slot.N)
    assert dialog.save()
    count = len(dialog.store.load(PAIR).history)
    assert dialog.save() and "같아 저장하지 않았습니다" in dialog.status.text()
    assert len(dialog.store.load(PAIR).history) == count
    current = dialog.store.load_current(PAIR)
    assert dialog.revert(current) and len(dialog.store.load(PAIR).history) == count


def test_history_highlights_active_and_can_revert_from_an_older_page(window, monkeypatch):
    source = window.pages["dashboard"].source
    store = group_store_for(source)
    start = datetime(2026, 11, 9, 16)
    for number in range(1, 25):
        version = (10011, 10012, 10013)[number % 3]
        monkeypatch.setattr(store, "clock", lambda minute=number: start + timedelta(minutes=minute))
        groups = VersionGroups.single(version)
        if number == 24:
            groups = VersionGroups({Slot.N: {version}, Slot.N_PLUS_1: {10012}})
        store.save(PAIR, groups, source.families[PAIR[1]], memo="2026-11-09 13:44 기준으로 되돌림")
    history = store.load(PAIR).history
    requests = []
    read = store.history_page

    def recorded_read(pair, *, cursor=None, limit=10):
        requests.append((pair, cursor, limit))
        return read(pair, cursor=cursor, limit=limit)

    monkeypatch.setattr(store, "history_page", recorded_read)
    dialog = open_editor(window)

    def rows():
        return [dialog.history.itemAt(index).widget() for index in range(dialog.history.count())]

    def captions():
        return [row.layout().itemAt(0).widget().text() for row in rows()]

    def active_rows():
        return [row for row in rows() if row.property("activeHistory")]

    def restore_button(row):
        return row.layout().itemAt(1).widget()

    initial = captions()
    assert len(initial) == 10 and initial[0] == "2026-11-09 16:24\nv1.0.1"
    assert initial[-1] == "2026-11-09 16:15\nv1.0.1" and active_rows() == [rows()[0]]
    assert rows()[0].layout().itemAt(0).widget().accessibleName().startswith("현재 기준 · ")
    assert requests == [(PAIR, None, 10)]
    assert not restore_button(rows()[0]).isEnabled() and restore_button(rows()[3]).isEnabled()
    assert dialog.history_more.isVisible()
    dialog.history_more.click()
    expanded = captions()
    assert expanded[:10] == initial and expanded[-1] == "2026-11-09 16:05\nv1.0.3"
    assert requests[-1] == (PAIR, 15, 10)

    restore = restore_button(rows()[-1])
    restore.click()
    assert store.load_current(PAIR) == history[4]
    assert captions() == expanded and requests == [(PAIR, None, 10), (PAIR, 15, 10)]
    assert active_rows() == [rows()[-1]] and restore_button(rows()[-1]) is restore and not restore.isEnabled()
    assert restore_button(rows()[0]).isEnabled()

    dialog.history_more.click()
    assert len(captions()) == 24 and captions()[:20] == expanded
    assert captions()[-1] == "2026-11-09 16:01\nv1.0.2"
    assert requests[-1] == (PAIR, 5, 10) and not dialog.history_more.isVisible()
    assert store.load(PAIR).history == history

    dialog.close_button.click()
    dialog = open_editor(window)
    assert captions() == [expanded[-1], *initial] and active_rows() == [rows()[0]]
    assert len(dialog.history_pages[PAIR].entries) == 10 and requests[-1] == (PAIR, None, 10)
    dialog.history_more.click()
    assert captions() == expanded and active_rows() == [rows()[-1]]
    assert requests[-1] == (PAIR, 15, 10)
