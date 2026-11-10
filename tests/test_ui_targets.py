"""7번: 모델/계열 목표 N의 임시 초안·적용·취소·선택 가능 조건·동일 결과 계약."""

from dataclasses import FrozenInstanceError, replace
from datetime import date
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.app import create_window
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding
from setron.ui.heatmap_data import EquipmentSelection, GoalState
from setron.ui.query_state import GroupOption, QueryCatalog, QuerySelection, QueryState, SiteOption, TargetN
from setron.ui.target_data import describe_targets, initial_targets, target_map, target_pairs, validate_targets
from setron.ui.target_dialog import TargetDialog
from setron.ui.theme import install_theme


DAY = date(2026, 10, 21)


@pytest.fixture
def source():
    catalog = QueryCatalog(sites=(SiteOption("s", "테스트 Site"),),
                           groups=(GroupOption("o2", "O2", "OXIDE"), GroupOption("o3", "O3", "OXIDE")))
    families = {fid: FamilyReleases(fid, (ReleaseVersion(fid * 10 + 1, fid, "v99", 1),
                                        ReleaseVersion(fid * 10 + 2, fid, "v2", 2),
                                        ReleaseVersion(fid * 10 + 3, fid, "v2_HF", 3),
                                        ReleaseVersion(fid * 10 + 4, fid, "순서_미등록", None))) for fid in (1001, 1002)}
    observations = {1: (PmVersion("PM1", 101, 1001, "v99"), PmVersion("PM2", 101, 1001, "v2"),
                        PmVersion("PM3", 103, 1002, "v2")),
                    2: (PmVersion(None, 102, 1001, "v2"),), 3: (PmVersion(None, 101, 1001, None),),
                    4: (), 5: (PmVersion(None, 103, 1002, "v99"),)}
    records = tuple(EquipmentRecord(eid, f"TEST-{eid}", day, "테스트 Site", None, "TEST", pms, is_hpa=eid == 4)
                    for day in (date(2026, 9, 21), DAY) for eid, pms in observations.items())
    return DashboardSource(catalog=catalog, bindings=(GroupBinding("o2", (101, 102)), GroupBinding("o3", (103,))),
                           records=records, locations={eid: EquipmentLocation("s", "P1") for eid in observations}, families=families,
                           targets={(101, 1001): 10012, (102, 1001): 10011, (103, 1002): 10022},
                           risks={1: RiskStatus.RISK, 2: RiskStatus.NO_RISK}, source_label="합성 예시 · 테스트 전용")


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


@pytest.fixture
def window(app, source):
    result = create_window(source)
    result.show()
    QTest.qWait(20)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def changed(values, key=(101, 1001), release_id=10011):
    return tuple(replace(t, release_id=release_id) if (t.model_id, t.family_id) == key else t for t in values)


def open_dialog(window):
    window.open_single_targets()
    QTest.qWait(20)
    assert window.target_dialog is not None
    return window.target_dialog


@pytest.mark.parametrize("args", [(True, 1001, 1), (101, 0, 1), (101, 1001, False), (101, 1001, -1), ("o2", 1001, 1)])
def test_target_contract_rejects_invalid_ids(args):
    with pytest.raises(ValueError):
        TargetN(*args)


def test_goal_draft_is_canonical_immutable_and_separate_from_applied(source):
    state = QueryState(source.catalog)
    original = initial_targets(source)
    state.set_target_draft(tuple(reversed(original)))
    assert state.target_draft == original and state.applied is None
    query = state.apply()
    state.set_target_draft(changed(original))
    assert state.has_pending_changes and state.is_current(query)
    assert query.targets == original and query.targets != state.target_draft
    with pytest.raises(FrozenInstanceError):
        query.targets[0].release_id = 10013
    with pytest.raises(ValueError):
        state.set_target_draft((original[0], original[0]))
    assert state.target_draft == changed(original)
    state.reset_draft()
    assert state.target_draft == changed(original)  # 위치 '전체 조건으로'는 N을 초기화하지 않음
    next_query = state.apply()
    assert next_query.targets == changed(original) and not state.is_current(query)


def test_pair_mapping_is_model_and_family_not_ui_group_or_cross_product(source):
    pairs = target_pairs(source)
    assert [p.key for p in pairs] == [(101, 1001), (102, 1001), (103, 1002)]
    assert pairs[0].group_key == pairs[1].group_key == "o2"
    assert [p.current_id for p in pairs[:2]] == [10012, 10011]
    assert [o.release.version_raw for o in pairs[0].options] == ["v99", "v2", "v2_HF", "순서_미등록"]
    assert pairs[0].options[2].unavailable_reason is None  # 순서가 확인된 HF 허용
    assert pairs[0].options[3].unavailable_reason == "순서 미확인"
    assert "모델 101" in describe_targets(source, initial_targets(source))


@pytest.mark.parametrize("key,rid", [((101, 1001), 10014), ((101, 1001), 10022), ((101, 1001), 99999), ((101, 1002), 10022)])
def test_new_target_rejects_unordered_foreign_unknown_or_wrong_pair(source, key, rid):
    values = initial_targets(source)
    if key == (101, 1002):
        values = (TargetN(*key, rid), *values[1:])
    else:
        values = changed(values, key, rid)
    with pytest.raises(ValueError):
        validate_targets(source, values)


def test_duplicate_order_or_release_id_is_not_a_new_candidate(source):
    releases = source.families[1001].releases
    source.families[1001] = FamilyReleases(1001, (*releases, ReleaseVersion(10015, 1001, "순서충돌", 3),
                                                ReleaseVersion(10016, 1001, "중복ID-A", 5), ReleaseVersion(10016, 1001, "중복ID-B", 6)))
    options = target_pairs(source)[0].options
    assert next(o for o in options if o.release.release_version_id == 10013).unavailable_reason == "순서 중복"
    assert all(o.unavailable_reason == "릴리즈 ID 중복" for o in options if o.release.release_version_id == 10016)
    for rid in (10013, 10016):
        with pytest.raises(ValueError):
            validate_targets(source, changed(initial_targets(source), release_id=rid))


def test_bad_existing_target_is_preserved_not_guessed_and_remains_unknown(source):
    source.targets[(101, 1001)] = 99999
    values = initial_targets(source)
    assert values[0].release_id == 99999 and validate_targets(source, values) == values
    state = QueryState(source.catalog)
    state.set_target_draft(values)
    data = source.snapshot(state.apply())
    assert data.summary.equipment[0].families[0].status.value == "UNDETERMINED"
    assert "릴리즈 미확인" in describe_targets(source, values)


def test_empty_and_missing_family_do_not_create_a_release(source, app):
    source.families.pop(1001)
    dialog = TargetDialog(source)
    assert target_pairs(source)[0].options == ()
    assert dialog.option_buttons.keys() == {None}
    assert not dialog.choose((101, 1001), 10011)
    assert dialog.choose((101, 1001), None)
    dialog.accept()
    assert dialog.result_targets[0].release_id is None
    dialog.deleteLater()


def test_snapshot_uses_request_n_without_mutating_provider_or_risk(source):
    original = source.targets.copy()
    state = QueryState(source.catalog)
    state.set_target_draft(initial_targets(source))
    old = source.snapshot(state.apply())
    state.set_target_draft(changed(state.target_draft))
    new = source.snapshot(state.apply())
    assert source.targets == original
    assert old.equipment_ids == new.equipment_ids and old.risky_ids == new.risky_ids
    assert old.summary.rate == 0.5 and new.summary.rate == 0.75
    assert old.target_values != new.target_values and target_map(new.target_values)[101, 1001] == 10011
    assert all(point.summary.rate == new.summary.rate for point in new.observations)


def test_initial_app_allows_goal_edit_without_auto_apply(window):
    assert window.query_panel.goal_button.isEnabled()
    assert window.query_state.applied is None
    assert window.pages["dashboard"].snapshot is None
    assert "선택 중 목표 N" in window.query_panel.draft_summary.text()
    dialog = open_dialog(window)
    assert not dialog.confirm_button.isEnabled() and dialog.isModal()
    assert "DB 저장 없음" in dialog.notice.text()
    dialog.reject()


@pytest.mark.parametrize("cancel", ["button", "escape", "close"])
def test_cancel_escape_close_leave_draft_applied_and_results_unchanged(window, cancel):
    query = window.query_panel.apply_draft()
    snapshot = window.pages["dashboard"].snapshot
    before = window.query_state.target_draft
    dialog = open_dialog(window)
    assert dialog.choose((101, 1001), 10011)
    if cancel == "button":
        dialog.cancel_button.click()
    elif cancel == "escape":
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    else:
        dialog.close()
    assert window.target_dialog is None
    assert window.query_state.target_draft == before and window.query_state.applied is query
    assert window.pages["dashboard"].snapshot is snapshot


def test_done_changes_only_goal_draft_and_apply_refreshes_all_views(window):
    old_query = window.query_panel.apply_draft()
    old = window.pages["dashboard"].snapshot
    applied_spy = QSignalSpy(window.query_applied)
    draft_spy = QSignalSpy(window.query_panel.targets_changed)
    dialog = open_dialog(window)
    QTest.mouseClick(dialog.option_buttons[10011], Qt.MouseButton.LeftButton)
    assert dialog.confirm_button.isEnabled()
    dialog.confirm_button.click()
    assert applied_spy.count() == 0 and draft_spy.count() == 1
    assert window.pages["dashboard"].snapshot is old and window.query_state.applied is old_query
    assert "목표 변경" in window.query_panel.status.text()
    assert "v99" in window.query_panel.draft_summary.text() and "v2" in window.query_panel.applied_summary.text()
    new_query = window.query_panel.apply_draft()
    dashboard, heatmap, equipment = (window.pages[k] for k in ("dashboard", "heatmap", "equipment"))
    assert dashboard.snapshot is heatmap.snapshot is equipment.snapshot
    assert dashboard.metrics["rate"].value.text() == "75.0%"
    assert new_query.targets != old_query.targets and not window.query_state.is_current(old_query)
    assert heatmap.tiles[0].goal is GoalState.MET
    equipment.open_detail(EquipmentSelection(new_query, DAY, 1))
    assert equipment.detail_model.rows[0][3] == "v99"  # 전체 상세도 새 N 사용
    assert not equipment.accept_result(old_query, old)


def test_pending_goal_does_not_contaminate_old_detail_or_scope_summary(window):
    query = window.query_panel.apply_draft()
    window.header.actions["equipment_list"].click()
    page = window.pages["equipment"]
    old = page.snapshot
    window.query_panel.set_target_draft(changed(window.query_state.target_draft))
    page.open_all()
    assert page.metrics["rate"].value.text() == "50.0%"
    page.open_detail(EquipmentSelection(query, DAY, 1))
    assert page.detail_model.rows[0][3] == "v2"
    assert page.snapshot is old


def test_multi_pair_edit_is_atomic_and_reset_uses_opening_draft_not_defaults(window):
    window.query_panel.set_target_draft(changed(window.query_state.target_draft))
    before = window.query_state.target_draft
    dialog = open_dialog(window)
    dialog.choose((101, 1001), 10013)
    dialog.pair_combo.setCurrentIndex(2)
    dialog.choose((103, 1002), 10023)
    assert "변경 2개" in dialog.review.text()
    dialog.reset_button.click()
    assert dialog.values() == before and not dialog.confirm_button.isEnabled()
    dialog.choose((102, 1001), 10012)
    dialog.accept()
    assert target_map(window.query_state.target_draft)[101, 1001] == 10011
    assert target_map(window.query_state.target_draft)[102, 1001] == 10012
    assert target_map(window.query_state.target_draft)[103, 1002] == 10022


def test_invalid_option_cannot_be_chosen_and_dialog_noop_does_not_emit_change(window):
    spy = QSignalSpy(window.query_panel.targets_changed)
    dialog = open_dialog(window)
    assert not dialog.option_buttons[10014].isEnabled()
    before = dialog.values()
    assert not dialog.choose((101, 1001), 10022)
    assert dialog.values() == before
    dialog.accept()
    assert spy.count() == 0 and window.query_state.applied is None


def test_explicit_unassigned_goal_produces_unknown_not_zero_or_success(window):
    dialog = open_dialog(window)
    dialog.option_buttons[None].click()
    dialog.confirm_button.click()
    query = window.query_panel.apply_draft()
    page = window.pages["equipment"]
    page.open_detail(EquipmentSelection(query, DAY, 1))
    assert page.detail_model.rows[0][3:5] == ("목표 미확인", "판단 불가")
    assert "목표 N" in page.detail_model.rows[0][5]


@pytest.mark.parametrize("same_catalog", [True, False])
def test_open_dialog_rejects_catalog_replacement_even_when_values_equal(window, same_catalog):
    before = window.query_state.target_draft
    dialog = open_dialog(window)
    dialog.choose((101, 1001), 10011)
    catalog = window.query_state.catalog
    window.set_query_catalog(catalog if same_catalog else replace(catalog, source_label="새 자료"))
    dialog.confirm_button.click()
    assert dialog.isVisible() and "변경되었습니다" in dialog.error.text()
    assert window.query_state.target_draft == before
    assert window.query_panel.goal_button.isEnabled() is same_catalog
    dialog.reject()


def test_open_dialog_rejects_changed_release_choices(window, source):
    before = window.query_state.target_draft
    dialog = open_dialog(window)
    dialog.choose((101, 1001), 10013)
    family = source.families[1001]
    source.families[1001] = FamilyReleases(1001, (*family.releases, ReleaseVersion(10015, 1001, "충돌", 3)))
    dialog.accept()
    assert dialog.isVisible() and "변경되었습니다" in dialog.error.text()
    assert window.query_state.target_draft == before
    dialog.reject()


def test_open_twice_reuses_one_dialog_and_prefers_selected_group(window):
    window.query_panel.select("group_key", "o3")
    first = open_dialog(window)
    window.open_single_targets()
    assert window.target_dialog is first and first.pairs[first.pair_combo.currentIndex()].group_key == "o3"
    first.reject()


def test_fresh_window_does_not_persist_another_windows_target_choice(window, source, app):
    dialog = open_dialog(window)
    dialog.choose((101, 1001), 10011)
    dialog.accept()
    other = create_window(source)
    assert target_map(other.query_state.target_draft)[101, 1001] == 10012
    assert source.targets[101, 1001] == 10012
    other.close()
    other.deleteLater()


def test_reverting_goal_draft_to_applied_clears_pending_without_requery(window):
    query = window.query_panel.apply_draft()
    snapshot = window.pages["dashboard"].snapshot
    window.query_panel.set_target_draft(changed(window.query_state.target_draft))
    assert window.query_state.has_pending_changes
    window.query_panel.set_target_draft(query.targets)
    assert not window.query_state.has_pending_changes
    assert window.query_panel.status.text() == "조건 적용됨"
    assert window.pages["dashboard"].snapshot is snapshot


def test_new_goal_apply_failure_clears_results_and_retry_uses_same_n(window, source, monkeypatch):
    window.query_panel.apply_draft()
    window.query_panel.set_target_draft(changed(window.query_state.target_draft))
    original = source.snapshot
    release_catalog = window.pages["versions"].catalog
    def fail(query):
        raise RuntimeError("계산 실패 예시")
    monkeypatch.setattr(source, "snapshot", fail)
    failed = window.query_panel.apply_draft()
    assert all(window.pages[key].snapshot is None for key in ("dashboard", "heatmap", "equipment"))
    assert window.pages["versions"].catalog is release_catalog
    assert target_map(failed.targets)[101, 1001] == 10011
    monkeypatch.setattr(source, "snapshot", original)
    retry = window.query_panel.apply_draft()
    assert retry.targets == failed.targets
    assert window.pages["dashboard"].metrics["rate"].value.text() == "75.0%"


def test_empty_target_catalog_can_be_reviewed_without_fake_choice(source, app):
    source.records, source.targets = (), {}
    dialog = TargetDialog(source)
    dialog.show()
    QTest.qWait(20)
    assert not dialog.pair_combo.isEnabled() and not dialog.confirm_button.isEnabled()
    assert "설정할 모델·계열이 없습니다" in dialog.choice_heading.text()
    dialog.reject()
    dialog.deleteLater()


def test_long_release_raw_is_plain_preserved_and_wraps_inside_choice(source, app):
    raw = "v999." + "X" * 100 + " + Hotfix <원문> " * 8
    family = source.families[1001]
    source.families[1001] = FamilyReleases(1001, tuple(replace(r, version_raw=raw) if r.release_version_id == 10013 else r
                                                     for r in family.releases))
    dialog = TargetDialog(source)
    dialog.resize(460, 450)
    dialog.show()
    QTest.qWait(80)
    control = dialog.option_buttons[10013]
    assert control.caption.text() == control.toolTip() == raw
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    assert control.caption.textFormat() is Qt.TextFormat.PlainText
    assert control.caption.width() <= control.width()
    assert dialog.choose((101, 1001), 10013)
    dialog.accept()
    assert target_map(dialog.result_targets)[101, 1001] == 10013
    dialog.deleteLater()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
def test_dialog_is_responsive_and_keyboard_choice_is_usable(window, size):
    window.resize(*size)
    dialog = open_dialog(window)
    QTest.qWait(70)
    assert dialog.width() <= window.width() and dialog.height() <= window.height()
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    for child in dialog.findChildren(QLabel):
        assert child.textFormat() is Qt.TextFormat.PlainText
    option = dialog.option_buttons[10011]
    option.setFocus()
    QTest.keyClick(option, Qt.Key.Key_Space)
    assert dialog.draft[101, 1001] == 10011
    dialog.confirm_button.click()
    QTest.qWait(50)
    assert window.scroll.horizontalScrollBar().maximum() == 0
