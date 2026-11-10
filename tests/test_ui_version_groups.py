"""기준 버전 설정의 N 선택, 수동 설정, 막힘·경고, 저장 이력 및 조회 적용 검증."""

import os
from dataclasses import replace
from datetime import date, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from setron.features.version.service import FamilyReleases
from setron.ui.app import create_window
from setron.ui.appearance_data import PersonalSettings
from setron.ui.components import DisclosureArrow
from setron.ui.group_data import SLOT_LABELS, TIER_LABELS, Slot, Tier, VersionGroups, group_store_for
from setron.ui.group_dialog import BLOCK_INK, SLOT_CHOICES, VersionGroupDialog
from setron.ui.query_state import QuerySelection
from setron.ui.theme import install_theme
from test_ui_import_ver5 import until

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
    dialog.pair_combo.setCurrentIndex(dialog.pair_combo.findData(key[0]))
    dialog.family_combo.setCurrentIndex(dialog.family_combo.findData(key[1]))


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
    assert dialog.rows.itemAtPosition(0, 2).widget().text() == "기준"
    # 그룹 저장 전(목표 1개)에도 tier_of 단계로 색을 낸다.
    assert tiers(dialog) == {10013: TIER_LABELS[Tier.ABOVE], 10012: TIER_LABELS[Tier.AT], 10011: TIER_LABELS[Tier.ONE_BELOW]}
    assert "저장 이력이 없습니다" in dialog.history.itemAt(0).widget().text()
    assert not any(widget.text().startswith(("버전을 클릭하면", "기준 N 1개 선택")) for widget in dialog.findChildren(QLabel))


def test_model_dropdown_groups_pairs_and_shows_family_only_when_needed(window, monkeypatch):
    source = window.pages["dashboard"].source
    extra_pair = (101, 1002)
    monkeypatch.setattr(source, "targets", {**source.targets, extra_pair: 10022})
    dialog = open_editor(window)
    assert dialog.pair_combo.currentText() == "C3" and dialog.family_combo.isVisibleTo(dialog)
    family, model, manual = dialog.family_combo, dialog.pair_combo, dialog.details_button
    assert family.geometry().right() < model.x() and model.geometry().right() < manual.x()
    assert family.y() == model.y() == manual.y() and family.height() == model.height() == manual.height() == 38
    assert manual.width() >= manual.sizeHint().width()
    assert [dialog.pair_combo.itemData(i) for i in range(dialog.pair_combo.count())].count(PAIR[0]) == 1
    choose_pair(dialog, extra_pair)
    assert dialog.pair.key == extra_pair and set(dialog.version_checks) == {10021, 10022, 10023}
    choose_pair(dialog, (102, 1002))
    assert dialog.pair_combo.currentText() == "C4" and not dialog.family_combo.isVisibleTo(dialog)


def test_version_rows_hide_only_model_evidence_reason_from_display_and_accessibility(window, monkeypatch):
    source = window.pages["dashboard"].source
    family = source.families[PAIR[1]]
    releases = tuple(replace(release, version_order=None) if release.release_version_id == 10013 else release
                     for release in family.releases)
    monkeypatch.setattr(source, "families", {**source.families, PAIR[1]: FamilyReleases(PAIR[1], releases)})
    monkeypatch.setattr(source, "applicability", {PAIR: {10012}}, raising=False)
    dialog = open_editor(window)

    hidden_reason = "모델 적용 근거 미확인 · 재확인 필요"
    options = {option.release.release_version_id: option for option in dialog.pair.options}
    assert options[10011].unavailable_reason == hidden_reason
    assert dialog.version_checks[10011].caption.text() == "v1.0.1"
    assert dialog.version_checks[10011].accessibleName() == "v1.0.1 · 기준 N"
    assert "순서 미확인" in dialog.version_checks[10013].caption.text()
    assert "순서 미확인" in dialog.version_checks[10013].accessibleName()


def test_reopen_prefers_last_selected_pair_and_only_saved_n(window, monkeypatch):
    preferences = window.pages["settings"].store
    source = window.pages["dashboard"].source
    group_store_for(source).save((102, 1002), VersionGroups.single(10021), source.families[1002])
    initial = open_editor(window, pair_key=None)
    assert initial.pair.key == (102, 1002) and initial.version_checks[10021].isChecked()
    initial.cancel_button.click()

    preferences.remember_version_group_pair((999, 999))
    window.query_panel.set_draft(QuerySelection(group_key="c4"))
    dialog = open_editor(window, pair_key=None)
    assert dialog.pair.key == (102, 1002)  # unavailable preference falls back to the query group

    choose_pair(dialog, PAIR)
    dialog.version_checks[10011].click()
    assert dialog.save()
    dialog = open_editor(window, pair_key=None)
    dialog.version_checks[10013].click()
    dialog.cancel_button.click()

    reopened = open_editor(window, pair_key=None)
    assert reopened.pair.key == PAIR
    assert {rid for rid, check in reopened.version_checks.items() if check.isChecked()} == {10011, 10012}
    reopened.cancel_button.click()

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
        dialog.cancel_button.click()
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
    store = dialog.store
    assert store.load(PAIR) is None
    dialog.save_button.click()
    assert window.group_dialog is None and not dialog.isVisible()
    assert store.load(PAIR).groups.members(Slot.N) == {10011, 10012}
    assert dashboard.snapshot is heatmap.snapshot and dashboard.snapshot is not before
    assert dashboard.snapshot.query.selection == before.query.selection
    assert dashboard.snapshot.summary.compliant > before.summary.compliant
    assert next(tile for tile in heatmap.tiles if tile.equipment_id == below.equipment_id).tier is Tier.AT
    detail = next(item for item in window.pages["equipment"].data.details if item.record.equipment_id == below.equipment_id)
    assert detail.whole.tier is Tier.AT

    reopened = open_editor(window)
    assert {rid for rid, button in reopened.version_checks.items() if button.isChecked()} == {10011, 10012}
    frame, caption, restore = reopened.history_rows[store.load_current(PAIR).sequence]
    assert frame.property("activeHistory") and caption.text() == "v1.0.2, v1.0.1"
    assert restore is None
    reopened.cancel_button.click()


@pytest.mark.parametrize("first,last", [(10011, 10013), (10013, 10011)])
def test_two_n_checkboxes_also_select_versions_between_them(window, first, last):
    dialog = open_editor(window)
    dialog.version_checks[10012].click()
    dialog.version_checks[first].click()
    assert dialog.groups().members(Slot.N) == {first}

    dialog.version_checks[last].click()
    assert dialog.version_checks[10012].isChecked()
    assert dialog.slot_combos[10012].currentData() is Slot.N
    assert dialog.groups().members(Slot.N) == {first, 10012, last}

    dialog.version_checks[10012].click()
    assert dialog.groups().members(Slot.N) == {first, last}


def test_empty_n_blocks_save_with_manual_settings_off(window):
    dialog = open_editor(window)
    assert not dialog.details_button.isChecked()
    dialog.version_checks[10012].click()
    assert not dialog.groups().has_target and not dialog.slot_combos[10012].isVisibleTo(dialog)
    notice = dialog.issues.itemAt(0).widget()
    assert notice.body.itemAt(0).widget().text() == "저장 불가"
    assert notice.body.itemAt(1).widget().text() == "기준 N에 넣을 버전을 하나 이상 선택해 주세요."
    assert not dialog.save_button.isEnabled()


def test_crossed_order_warns_in_red_and_keeps_save_available(window, monkeypatch):
    source = window.pages["dashboard"].source
    family = source.families[PAIR[1]]
    releases = tuple(replace(release, version_order=None) if release.release_version_id == 10013 else release
                     for release in family.releases)
    monkeypatch.setattr(source, "families", {**source.families, PAIR[1]: FamilyReleases(PAIR[1], releases)})
    dialog = open_editor(window)
    choose(dialog, 10013, Slot.N_MINUS_1)  # 순서 미확인 경고도 함께 표시한다.
    choose(dialog, 10011, Slot.N_PLUS_1)  # 더 오래된 버전을 더 높은 칸에
    QApplication.processEvents()
    assert dialog.issues.count() == 2
    unknown, crossed = (dialog.issues.itemAt(i).widget() for i in range(2))
    assert unknown.geometry().bottom() < crossed.geometry().top()
    assert unknown.body.itemAt(1).widget().text().endswith("버전 관리에서 순서를 확인해 주세요.")
    title, message = (crossed.body.itemAt(i).widget() for i in range(2))
    assert title.text() == "경고 · 저장 가능" and dialog.save_button.isEnabled()
    assert "v1.0.1(N+1)" in message.text() and "v1.0.2(N)" in message.text()
    assert message.text().endswith("수동 설정에서 버전별 단계를 확인해 주세요.")
    assert message.wordWrap() and message.font().pixelSize() == 13
    assert message.styleSheet() == f"color: {BLOCK_INK};"


def test_manual_settings_keep_steps_when_hidden_and_reopened(window):
    dialog = open_editor(window)
    manual = dialog.details_button
    assert manual.text() == "수동 설정" and manual.isCheckable() and not manual.isChecked()
    assert not any(button.text().strip() == "기본 후보" for button in dialog.findChildren(QPushButton))
    assert manual.icon().isNull() and manual.findChild(DisclosureArrow) is None
    assert dialog.slot_heading.text() == "수동 설정" and not dialog.slot_heading.isVisibleTo(dialog)
    assert all(not combo.isVisibleTo(dialog) for combo in dialog.slot_combos.values())

    position = manual.mapTo(dialog, manual.rect().center())
    QTest.mouseClick(dialog.windowHandle(), Qt.MouseButton.LeftButton, pos=position)
    assert manual.isChecked() and dialog.slot_heading.isVisibleTo(dialog)
    choose(dialog, 10011, Slot.N_MINUS_1)
    choose(dialog, 10013, Slot.N_PLUS_1)
    assert all(combo.isVisibleTo(dialog) for combo in dialog.slot_combos.values())
    manual.click()
    assert not manual.isChecked() and not dialog.slot_heading.isVisibleTo(dialog)
    manual.click()
    assert dialog.slot_combos[10011].currentText() == SLOT_LABELS[Slot.N_MINUS_1]
    assert dialog.slot_combos[10013].currentText() == SLOT_LABELS[Slot.N_PLUS_1]
    manual.click()
    assert dialog.save()

    dialog = open_editor(window)
    assert not dialog.details_button.isChecked() and not dialog.slot_heading.isVisibleTo(dialog)
    assert dialog.version_checks[10012].isChecked()
    dialog.details_button.click()
    assert dialog.slot_combos[10011].currentText() == SLOT_LABELS[Slot.N_MINUS_1]
    assert dialog.slot_combos[10013].currentText() == SLOT_LABELS[Slot.N_PLUS_1]


def test_save_applies_after_close_and_cancel_discards_restored_draft(window):
    window.query_panel.apply_draft()
    dashboard, heatmap = window.pages["dashboard"], window.pages["heatmap"]
    before = dashboard.snapshot
    dialog = open_editor(window)
    store = dialog.store
    choose(dialog, 10012, Slot.N_MINUS_1)
    choose(dialog, 10013, Slot.N)  # 목표를 한 단계 올린다
    assert tiers(dialog)[10012] == TIER_LABELS[Tier.ONE_BELOW]
    assert dashboard.snapshot is heatmap.snapshot is before
    visible_at_apply = []

    def record_visibility(_):
        visible_at_apply.append(dialog.isVisible())

    window.query_applied.connect(record_visibility)
    assert dialog.save()
    window.query_applied.disconnect(record_visibility)
    assert window.group_dialog is None and visible_at_apply == [False]
    first_snapshot = dashboard.snapshot
    assert first_snapshot is heatmap.snapshot and first_snapshot.summary.compliant < before.summary.compliant
    window.navigate("heatmap")
    heatmap.below_filter.click()
    assert window.route == "heatmap" and len(heatmap.visible_ids) == dashboard.snapshot.summary.upgrade_needed
    tile_id = heatmap.visible_ids[0]
    heatmap.tile_buttons[tile_id].click()
    drawer = window.equipment_drawer
    assert drawer.isVisible() and drawer.goal_badge.text() == "목표 미달"
    assert not any("미달 판정 근거" in widget.text() for widget in drawer.findChildren(QLabel))
    drawer.close_panel()
    heatmap.below_filter.click()
    assert len(heatmap.visible_ids) >= dashboard.snapshot.summary.upgrade_needed
    first = store.load_current(PAIR)
    dialog = open_editor(window)
    choose(dialog, 10012, Slot.N)
    assert dialog.save()
    history = store.load(PAIR).history
    assert len(history) == 2
    latest = dashboard.snapshot
    assert latest.summary.compliant > first_snapshot.summary.compliant
    dialog = open_editor(window)
    assert dialog.revert(first)
    assert store.load_current(PAIR) == history[-1]
    assert dialog.cancel_button.text() == "취소"
    dialog.cancel_button.click()
    assert dashboard.snapshot is heatmap.snapshot is latest
    assert store.load_current(PAIR) == history[-1]

    dialog = open_editor(window)
    assert dialog.revert(first) and dialog.save()
    assert store.load_current(PAIR) == first
    assert store.load(PAIR).history == history
    restored = dashboard.snapshot
    assert restored is heatmap.snapshot and restored.query.revision == latest.query.revision + 1
    assert restored.summary.compliant == first_snapshot.summary.compliant


def test_replay_baseline_uses_saved_groups_and_discards_cancelled_changes(window):
    replay = window.pages["replay"]
    window.navigate("replay")
    until(lambda: not replay.busy)
    before = replay.data
    replay.query_panel.goal_button.click()
    dialog = window.group_dialog
    choose_pair(dialog, PAIR)
    choose(dialog, 10012, Slot.N_MINUS_1)
    choose(dialog, 10013, Slot.N)
    dialog.cancel_button.click()
    assert replay.data is before and not replay.busy

    replay.query_panel.goal_button.click()
    dialog = window.group_dialog
    choose_pair(dialog, PAIR)
    choose(dialog, 10012, Slot.N_MINUS_1)
    choose(dialog, 10013, Slot.N)
    assert replay.data is before
    dialog.save_button.click()
    assert window.group_dialog is None and replay.busy
    until(lambda: not replay.busy)
    assert replay.data.source.saved_groups[PAIR].members(Slot.N) == {10013}

    window.navigate("dashboard")
    dialog = open_editor(window)
    choose(dialog, 10012, Slot.N)
    dialog.save_button.click()
    window.navigate("replay")
    until(lambda: not replay.busy)
    assert replay.data.source.saved_groups[PAIR].members(Slot.N) == {10012, 10013}


def test_repeated_save_and_revert_to_current_do_not_pile_up(window):
    """같은 기준을 다시 저장하거나 현재 적용 중인 기록을 선택해도 이력이 늘지 않는다."""
    dialog = open_editor(window)
    choose(dialog, 10013, Slot.N)
    store = dialog.store
    assert dialog.save()
    count = len(store.load(PAIR).history)
    dialog = open_editor(window)
    assert dialog.save() and len(store.load(PAIR).history) == count
    current = store.load_current(PAIR)
    dialog = open_editor(window)
    assert dialog.revert(current) and dialog.save() and len(store.load(PAIR).history) == count


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

    def date_label(row):
        return row.findChild(QLabel, "groupHistoryDate")

    def version_label(row):
        return row.layout().itemAt(1).widget()

    def captions():
        return [(date_label(row).text(), version_label(row).text()) for row in rows()]

    def active_rows():
        return [row for row in rows() if row.property("activeHistory")]

    def restore_button(row):
        item = row.layout().itemAt(2)
        return item.widget() if item is not None else None

    initial = captions()
    assert len(initial) == 10 and initial[0] == ("2026-11-09 16:24", "v1.0.1")
    assert initial[-1] == ("2026-11-09 16:15", "v1.0.1") and active_rows() == [rows()[0]]
    accessible = version_label(rows()[0]).accessibleName()
    assert accessible.startswith("현재 기준 · ") and all(text in accessible for text in initial[0])
    positions = {(date_label(row).x(), date_label(row).width(), version_label(row).x()) for row in rows()}
    assert len(positions) == 1
    date_x, date_width, version_x = positions.pop()
    assert date_x + date_width < version_x
    assert requests == [(PAIR, None, 10)]
    assert restore_button(rows()[0]) is None and restore_button(rows()[3]).isEnabled()
    assert dialog.history_more.isVisible()
    dialog.history_more.click()
    expanded = captions()
    assert expanded[:10] == initial and expanded[-1] == ("2026-11-09 16:05", "v1.0.3")
    assert requests[-1] == (PAIR, 15, 10)

    restored_row = rows()[-1]
    restore_button(restored_row).click()
    assert dialog.groups() == history[4].groups
    assert store.load_current(PAIR) == history[-1]
    assert captions() == expanded and requests == [(PAIR, None, 10), (PAIR, 15, 10)]
    assert active_rows() == [rows()[0]] and restore_button(restored_row).isEnabled()

    dialog.history_more.click()
    assert len(captions()) == 24 and captions()[:20] == expanded
    assert captions()[-1] == ("2026-11-09 16:01", "v1.0.2")
    assert requests[-1] == (PAIR, 5, 10) and not dialog.history_more.isVisible()
    assert store.load(PAIR).history == history

    assert dialog.save()
    assert store.load_current(PAIR) == history[4]
    dialog = open_editor(window)
    assert captions() == [expanded[-1], *initial] and active_rows() == [rows()[0]]
    assert len(dialog.history_pages[PAIR].entries) == 10 and requests[-1] == (PAIR, None, 10)
    dialog.history_more.click()
    assert captions() == expanded and active_rows() == [rows()[-1]]
    assert requests[-1] == (PAIR, 15, 10)
