"""VER5 2-V 조회 버전: 원문 OR, 같은 설비 집합, 초안/적용/취소, N·위험 독립."""

import os
from dataclasses import replace
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.app import create_window
from setron.ui.ai_data import facts_from_snapshot
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding
from setron.ui.management_integration import report_filter
from setron.ui.query_state import (
    GroupOption, QueryCatalog, QuerySelection, QueryState, SiteOption, VersionFilter,
)
from setron.ui.theme import install_theme
from setron.ui.version_filter import version_options
from setron.ui.version_filter_dialog import VersionFilterDialog

DAY = date(2026, 10, 21)
PAST = date(2026, 9, 21)


@pytest.fixture
def source():
    def pm(raw, model=101, family=1):
        return PmVersion(None, model, family, raw)

    def record(eid, pms, *, day=DAY, hpa=False):
        return EquipmentRecord(eid, f"TEST-{eid}", day, "테스트 Site", "원본 구역", "TEST", pms, hpa)

    records = (record(1, (pm("v1"), pm("v2"), pm("v3", 102, 2))),
               record(2, (pm("v2"),)), record(3, (pm(None, 102, 2),)), record(4, ()),
               record(5, (), hpa=True), record(6, (pm("v2_DFB"),)),
               record(7, (pm("V2"),)), record(8, (pm("v2 "),)),
               record(1, (pm("v0"), pm("v3", 102, 2)), day=PAST))
    return DashboardSource(
        catalog=QueryCatalog(sites=(SiteOption("s1", "테스트 Site"),),
                             groups=(GroupOption("o2", "O2"), GroupOption("o3", "O3"))),
        bindings=(GroupBinding("o2", (101,)), GroupBinding("o3", (102,))),
        records=records, locations={eid: EquipmentLocation("s1", "원본 구역") for eid in range(1, 9)},
        families={1: FamilyReleases(1, (ReleaseVersion(11, 1, "v1", 1), ReleaseVersion(12, 1, "v2", 2))),
                  2: FamilyReleases(2, (ReleaseVersion(21, 2, "v3", 1),))},
        targets={(101, 1): 12, (102, 2): 21}, risks={1: RiskStatus.RISK}, source_label="테스트용 관측")


def snapshot(source, versions, **selection):
    state = QueryState(source.catalog)
    state.set_draft(QuerySelection(versions=versions, **selection))
    return source.snapshot(state.apply())


@pytest.mark.parametrize("versions,ids", [
    (VersionFilter(("v1", "v2")), (1, 2)),
    (VersionFilter(("v2_DFB",)), (6,)),
    (VersionFilter(("V2",)), (7,)),
    (VersionFilter(("v2 ",)), (8,)),
    (VersionFilter(include_missing=True), (3, 4, 5)),
    (VersionFilter(("v2",), True), (1, 2, 3, 4, 5)),
    (VersionFilter(("없는 버전",)), ()),
])
def test_raw_or_filter_deduplicates_and_never_normalizes_suffix_case_or_space(source, versions, ids):
    assert snapshot(source, versions).equipment_ids == ids


def test_filter_keeps_all_selected_pm_and_risk_goal_facts(source):
    result = snapshot(source, VersionFilter(("v2",)))
    assert result.summary.equipment_count == 2
    assert len(result.selected_records[0].pms) == 3  # 선택 버전이 아닌 PM도 숨기지 않음
    assert result.summary.upgrade_needed == 1  # v1 PM의 미달을 계속 반영
    assert result.risky_ids == (1,)
    assert result.target_values == source.snapshot(QueryState(source.catalog).apply()).target_values
    assert result.groups[1].equipment_ids == (1,)  # O3 셀도 같은 물리 설비 코호트
    assert result.observations[0].summary.equipment_count == 1  # 과거 v0도 코호트 추이에 유지
    assert result.observations[0].missing_equipment == 1
    assert any(r.base_date == PAST and r.pms[0].version_raw == "v0"
               for r in source.scoped_records(result.query))  # AI 이력·예측도 과거 원문 보존
    exported = report_filter(result, source)
    assert exported.equipment_ids == (1, 2) and exported.model_ids is None
    assert "'v2'" in facts_from_snapshot(result)["조회 조건"]


def test_group_and_date_combine_with_version_and_no_result_is_not_all(source):
    assert snapshot(source, VersionFilter(("v3",)), group_key="o2").equipment_ids == ()
    assert snapshot(source, VersionFilter(("v3",)), group_key="o3").equipment_ids == (1,)
    assert snapshot(source, VersionFilter(("v0",)), as_of=PAST).equipment_ids == (1,)
    assert snapshot(source, VersionFilter(("v2",)), as_of=PAST).equipment_ids == ()
    missing = snapshot(source, VersionFilter(include_missing=True))
    assert missing.summary.equipment_count == 2 and missing.summary.hpa_excluded == 1
    assert tuple(r.equipment_id for r in missing.hpa_lookup_records) == (5,)
    assert snapshot(source, VersionFilter(("v2",))).hpa_lookup_records == ()


@pytest.mark.parametrize("values,missing", [((), False), (("",), False), (("  ",), False), ((1,), False), ("v1", False), (("v1",), 1)])
def test_invalid_or_empty_selection_is_rejected(values, missing):
    with pytest.raises(ValueError):
        VersionFilter(values, missing)


def test_state_reset_is_draft_only_and_version_does_not_change_targets(source):
    state = QueryState(source.catalog)
    state.set_draft(QuerySelection(versions=VersionFilter(("v2", "v2"))))
    applied = state.apply()
    assert applied.selection.versions.versions == ("v2",)
    state.reset_draft()
    assert state.has_pending_changes and state.is_current(applied)
    assert state.applied.selection.versions == VersionFilter(("v2",))
    assert state.apply().selection.versions is None
    with pytest.raises(ValueError):
        state.set_draft(QuerySelection(versions=("v2",)))


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    return result


@pytest.fixture
def window(app, source):
    result = create_window(source)
    result.resize(1440, 960)
    result.show()
    app.processEvents()
    yield result
    if result.version_filter_dialog is not None:
        result.version_filter_dialog.reject()
    result.close()
    result.deleteLater()
    app.processEvents()


def open_filter(window):
    window.query_panel.version_button.click()
    QApplication.processEvents()
    return window.version_filter_dialog


def test_popup_matches_figma_controls_cancels_and_blocks_zero(window):
    dialog = open_filter(window)
    assert isinstance(dialog, VersionFilterDialog)
    assert dialog.columns == 2 and all(c.isChecked() for c in dialog.choices.values())
    dialog.clear_button.click()
    assert not dialog.confirm_button.isEnabled() and "하나 이상" in dialog.error.text()
    dialog.accept()
    assert dialog.isVisible() and window.query_state.draft.versions is None
    dialog.choices["v2"].setFocus()
    QTest.keyClick(dialog.choices["v2"], Qt.Key.Key_Space)
    assert dialog.choices["v2"].isChecked() and "1개" in dialog.count.text()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert window.version_filter_dialog is None and window.query_state.draft.versions is None


def test_confirm_then_apply_updates_same_snapshot_and_list_not_targets(window):
    panel, state = window.query_panel, window.query_state
    panel.apply_draft()
    previous = window.pages["dashboard"].snapshot
    targets = state.target_draft
    dialog = open_filter(window)
    dialog.clear_button.click()
    dialog.choices["v2"].click()
    dialog.confirm_button.click()
    assert state.draft.versions == VersionFilter(("v2",))
    assert state.applied is previous.query and window.pages["heatmap"].snapshot is previous
    assert state.target_draft == targets and "1개 선택" in panel.version_button.text()
    assert panel.version_pending.isVisible()
    panel.apply_draft()
    dashboard, heatmap = window.pages["dashboard"], window.pages["heatmap"]
    assert dashboard.snapshot is heatmap.snapshot and dashboard.snapshot.equipment_ids == (1, 2)
    assert not panel.version_pending.isVisible()
    assert dashboard.snapshot.query.selection.versions == VersionFilter(("v2",))
    assert "1개 선택" in heatmap._applied_caption(heatmap.snapshot)
    window.header.action_requested.emit("equipment_list")
    assert window.route == "equipment"
    assert window.pages["equipment"].snapshot is dashboard.snapshot
    window.navigate("dashboard")
    assert state.draft.versions == VersionFilter(("v2",))
    dialog = open_filter(window)
    dialog.all_button.click()
    dialog.confirm_button.click()
    assert state.draft.versions is None and state.applied.selection.versions == VersionFilter(("v2",))
    panel.apply_draft()
    assert dashboard.snapshot.summary.equipment_count == 7


def test_stale_dialog_does_not_overwrite_changed_draft(window, source):
    dialog = open_filter(window)
    window.query_panel.set_version_filter(VersionFilter(("v1",)))
    dialog.accept()
    assert dialog.isVisible() and "바뀌었습니다" in dialog.error.text()
    assert window.query_state.draft.versions == VersionFilter(("v1",))
    dialog.reject()
    dialog = open_filter(window)
    source.records = source.records[:-1]
    dialog.accept()
    assert dialog.isVisible() and "바뀌었습니다" in dialog.error.text()


def test_empty_and_removed_options_do_not_implicitly_widen_to_all(app):
    empty = VersionFilterDialog(())
    assert not empty.confirm_button.isEnabled()
    empty.accept()
    assert empty.result() != empty.DialogCode.Accepted
    old = VersionFilterDialog(("v2",), VersionFilter(("v1",)))
    assert old.choices["v1"].isChecked() and not old.choices["v2"].isChecked()
    old.accept()
    assert old.result_filter == VersionFilter(("v1",))
    empty.deleteLater()
    old.deleteLater()


def test_popup_scrolls_many_versions_and_wraps_long_raw_without_html(app):
    raw = "<b>" + "v1_DFB_" * 30 + "</b>"
    dialog = VersionFilterDialog((raw, *(f"v{i}" for i in range(80))))
    dialog.resize(440, 400)
    dialog.show()
    app.processEvents()
    assert dialog.columns == 1 and dialog.scroll.verticalScrollBar().maximum() > 0
    assert dialog.choices[raw].accessibleName() == raw
    assert dialog.choices[raw].caption.text() == raw
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    assert dialog.choices[raw].width() <= dialog.scroll.viewport().width()
    dialog.reject()
    dialog.deleteLater()


def test_options_are_observed_not_hardcoded_and_preserve_original_bytes(source):
    assert version_options(source) == ("V2", "v0", "v1", "v2", "v2 ", "v2_DFB", "v3", None)
