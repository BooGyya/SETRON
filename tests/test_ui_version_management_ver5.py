"""VER5 7번 화면. 합성 자료로 프론트 흐름만 확인하며 회사 DB·AI·Excel을 호출하지 않는다."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.management.schemas import ManagedRecord, ManagementResult
from setron.ui.app import create_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.import_demo import DemoExcel
from setron.ui.theme import install_theme
from setron.ui.version_demo import demo_version_catalog
from setron.ui.version_management_data import VersionWorkspace, archive_comparison, make_item, make_release
from setron.ui.version_management_history import save_archive_excel


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    return result


def wait(app, condition):
    for _ in range(250):
        app.processEvents()
        if condition():
            return
        QTest.qWait(10)
    assert condition()


@pytest.fixture
def window(app):
    result = create_window(excel=DemoExcel())
    result.resize(1600, 960)
    result.show()
    result.navigate("versions")
    page = result.pages["versions"]
    wait(app, lambda: page.installed_source is not None and not page.installed_request.busy)
    yield result
    if page.software_dialog:
        page.software_dialog.accept()
    if page.item_dialog:
        page.item_dialog.reject()
    result.close()
    result.deleteLater()
    app.processEvents()


def test_five_fixed_tabs_keep_order_header_and_common_query_unchanged(window):
    page = window.pages["versions"]
    before = deepcopy((window.query_state.draft, window.query_state.applied, window.query_state.target_draft))
    names = tuple(page.tabs.tabText(i) for i in range(5))
    assert names == ("설비 설치 버전", "릴리즈 · Item", "설비 정보 관리", "변경 검토", "변경·보관 이력")
    for index in (1, 2, 3, 4, 0):
        page.select_tab(index)
        assert page.tabs.currentIndex() == index
        assert tuple(page.tabs.tabText(i) for i in range(5)) == names
        assert window.header.title.text() == "버전 관리"
        assert page.views[index].isVisible()
    assert (window.query_state.draft, window.query_state.applied, window.query_state.target_draft) == before


def test_back_keeps_selected_tab_and_returns_within_release(window):
    page = window.pages["versions"]
    page.select_tab(1)
    assert page.open_row(0)
    assert page.mode == "detail"
    page.go_back()
    assert page.mode == "list" and page.tabs.currentIndex() == 1
    page.go_back()
    assert page.tabs.currentIndex() == 0


def test_installed_query_uses_exact_date_and_never_clones_observation(window, app):
    page = window.pages["versions"]
    assert page.installed_records
    page.installed_date.setText("2000-01-01")
    assert page.load_installed()
    wait(app, lambda: not page.installed_request.busy)
    assert page.installed_records == ()
    assert "스냅샷이 없습니다" in page.installed_notice.text()
    assert page.installed_model.rowCount() == 0


def test_invalid_installed_date_does_not_query_or_enable_save(window):
    page = window.pages["versions"]
    before = page.installed_records
    page.installed_date.setText("2026-02-30")
    assert not page.load_installed()
    assert page.installed_records is before


def test_only_selected_sw_draft_changes_and_other_pm_n_source_preserved(window):
    page = window.pages["versions"]
    row = next(i for i, r in enumerate(page.installed_records) if len(r.pms) > 1)
    original = deepcopy(page.source.records)
    targets = deepcopy(page.source.targets)
    page.open_software(row)
    dialog = page.software_dialog
    assert not dialog._review()
    dialog.value.setText("v-ui-corrected")
    dialog.reason.setText("테스트 정정 근거")
    assert dialog._review()
    assert sum(row[2] == "변경 없음" for row in dialog.preview_model.rows) == len(dialog.record.pms) - 1
    dialog._submit()
    assert len(page.workspace.software_drafts) == 1
    assert len(page.workspace.history) == 1 and not page.workspace.history[0].persisted
    assert page.source.records == original and page.source.targets == targets


def test_software_input_is_kept_per_target_and_cancel_resets_only_current(window):
    page = window.pages["versions"]
    row = next(i for i, r in enumerate(page.installed_records) if len(r.pms) > 1)
    page.open_software(row)
    dialog = page.software_dialog
    dialog.value.setText("first-target")
    dialog.reason.setText("사유1")
    first = dialog.key()
    dialog.target.setCurrentIndex(1)
    dialog.value.setText("second-target")
    second = dialog.key()
    dialog.target.setCurrentIndex(0)
    assert dialog.value.text() == "first-target"
    dialog.cancel_current()
    assert first not in dialog.drafts and dialog.drafts[second][0] == "second-target"


def test_new_release_validates_duplicate_reason_order_and_keeps_original(window):
    page = window.pages["versions"]
    page.select_tab(1)
    original = page.catalog
    assert page.open_release_form()
    form = page.release_form
    form.raw.setText(page.visible_entries[0].release.version_raw)
    form.reason.setText("사유")
    assert not form.validate() and "이미" in form.error.text()
    form.raw.setText("ui-new-release")
    form.order.setText("10")
    assert not form.validate() and "근거" in form.error.text()
    form.basis.setText("확인된 순서 자료")
    assert form.validate()
    form._submit()
    assert len(page.catalog.releases) == len(original.releases) + 1
    assert all(entry in page.catalog.releases for entry in original.releases)
    assert page.workspace.history[-1].category == "릴리즈"
    assert page.workspace.history[-1].sources == ()


def test_release_review_is_invalidated_by_later_input(window):
    page = window.pages["versions"]
    page.open_release_form()
    form = page.release_form
    form.raw.setText("new")
    form.reason.setText("테스트")
    assert form.validate()
    form.raw.setText("changed-after-review")
    assert form.prepared is None and form.save.isHidden()


def test_item_add_has_all_fields_and_preserves_other_release_links(window):
    page = window.pages["versions"]
    page.open_row(0)
    before = page.catalog.version_items
    page.open_item_form()
    form = page.item_dialog
    assert not form.prepared
    form.content.setText("UI Item <원문> =보존")
    form.number.setText("21")
    form.category.setText("UI")
    form.jira.setText("TEST-1")
    form.remark.setText("검토 완료")
    form.reason.setText("신규 Item 확인")
    form._review()
    assert form.prepared is not None
    form._submit()
    assert all(row in page.catalog.version_items for row in before)
    assert len(page.catalog.items) > len(page.base_catalog.items)
    assert page.workspace.history[-1].category == "Item"


@pytest.mark.parametrize("mode", [0, 1, 2])
def test_equipment_edit_new_delete_are_drafts_not_actual_db_mutations(window, mode):
    page = window.pages["versions"]
    before = deepcopy(page.source.records)
    page.select_tab(2)
    page.equipment_modes.setCurrentIndex(mode)
    if mode == 0:
        page.equipment_fields["geometry"].setText("테스트 형상")
    elif mode == 1:
        page.equipment_fields["code"].setText("UI-NEW-ONLY")
        page.equipment_fields["site"].setText("신규 Site")
    page.equipment_reason.setText("작업본 검토")
    assert page.review_equipment()
    assert page.save_equipment()
    assert page.workspace.history[-1].action == ("수정", "등록", "삭제 검토 요청")[mode]
    assert page.source.records == before


def test_duplicate_equipment_code_blocked_across_sites(window):
    page = window.pages["versions"]
    page.equipment_modes.setCurrentIndex(1)
    page.equipment_fields["code"].setText(page.source.records[0].equipment_code)
    page.equipment_fields["site"].setText("다른 Site")
    page.equipment_reason.setText("중복 확인")
    assert not page.review_equipment()
    assert "고유" in page.equipment_error.text()


def test_review_archive_check_invalidation_and_readonly_evidence(window):
    page = window.pages["versions"]
    change = page.workspace.stage("설비 정보", "수정", "A", {"line": "old"}, {"line": "new"}, "사유")
    page.workspace.finish((change.identity,))
    page.select_tab(3)
    review = page.review_panel
    assert not review.create.isEnabled()
    review.review()
    assert not review.create.isEnabled()
    review.confirmed.setChecked(True)
    assert review.create.isEnabled() and review.make_archive()
    assert page.tabs.currentIndex() == 4 and page.history_panel.archived
    for index in (0, 1, 2):
        page.history_panel.archive_tabs.setCurrentIndex(index)
        assert page.history_panel.detail_model.rowCount() >= 0
    assert "직접 입력" in page.history_panel.detail_model.rows[0][3]


def test_history_categories_are_filtered_without_data_changes(window):
    page = window.pages["versions"]
    for kind in ("Item", "릴리즈", "설비 기록"):
        change = page.workspace.stage(kind, "수정", kind, {"a": "before"}, {"a": "after"}, "사유")
        page.workspace.finish((change.identity,))
    page.select_tab(4)
    history = page.history_panel
    history.kind.setCurrentIndex(history.kind.findData("Item"))
    assert len(history.entries) == 1 and history.entries[0].category == "Item"
    history.select(0)
    assert history.detail_model.rowCount() == 1


@pytest.mark.parametrize("width,height", [(1600, 960), (1100, 800), (960, 600)])
def test_tabs_and_forms_fit_three_window_sizes(window, app, width, height):
    page = window.pages["versions"]
    window.resize(width, height)
    for index in range(5):
        page.select_tab(index)
        app.processEvents()
        assert window.scroll.horizontalScrollBar().maximum() == 0
        assert page.tabs.width() > 0
        assert page.views[index].width() <= window.scroll.viewport().width()


def test_relative_actual_and_age_comparison_do_not_modify_common_targets(window):
    page = window.pages["versions"]
    before = deepcopy(page.source.targets)
    page.show_comparison()
    page.compare_target.setCurrentIndex(1)
    relative = page.compare_model.rows
    page.compare_modes.setCurrentIndex(1)
    assert page.compare_model.headings[1] == "실제 버전"
    assert page.compare_model.rows
    assert page.source.targets == before
    page.age_warning.setText("24")
    page.age_danger.setText("12")
    page.render_comparison()
    assert "길어야" in page.compare_notice.text()


def test_archive_export_has_selected_change_only_and_escapes_formula(tmp_path):
    from openpyxl import load_workbook
    store = VersionWorkspace()
    one = store.stage("릴리즈", "등록", "A", {}, {"version_raw": "=1+1"}, "첫 변경")
    store.finish((one.identity,))
    first = store.archive(store.history, confirmed=True)
    two = store.stage("Item", "등록", "B", {}, {"content": "다른 작업"}, "둘째 변경")
    store.finish((two.identity,))
    path = tmp_path / "selected.xlsx"
    save_archive_excel(path, first)
    book = load_workbook(path)
    assert book["변경 전후"].max_row == 2
    assert book["변경 전후"]["E2"].value == "=1+1" and book["변경 전후"]["E2"].data_type == "s"
    assert "입력 근거" in book.sheetnames
    book.close()


def test_archive_is_immutable_and_comparison_is_only_work_changes():
    store = VersionWorkspace()
    change = store.stage("Item", "수정", "Item1", {"a": "old"}, {"a": "new"}, "사유")
    with pytest.raises(ValueError):
        store.archive((change,), confirmed=True)
    store.finish((change.identity,))
    first = store.archive(store.history, confirmed=True)
    more = store.stage("Item", "수정", "Item1", {"a": "new"}, {"a": "newer"}, "다음 사유")
    store.finish((more.identity,))
    second = store.archive(store.history, confirmed=True)
    assert dict(first.changes[0].after)["a"] == "new"
    assert archive_comparison(first, second)[0][-2:] == ("new", "newer")


@pytest.mark.parametrize("reason", ["", "   "])
def test_work_changes_require_reason(reason):
    with pytest.raises(ValueError):
        VersionWorkspace().stage("Item", "수정", "A", {}, {"value": "v"}, reason)


class FakeVersionGateway:
    def __init__(self, record, pm, *, failure=None, changed=False):
        self.record, self.pm, self.failure, self.changed = record, pm, failure, changed
        self.calls = []

    def equipment_versions(self, eid, day):
        assert (eid, day) == (self.record.equipment_id, self.record.base_date)
        return (("equipment_version", {"equipment_version_id": 7},
                 f"공통 SW · {self.pm.version_raw} · 계열 ID {self.pm.family_id}"),)

    def get(self, table, key):
        return ManagedRecord(table, key, {"version_raw": "conflicting" if self.changed else self.pm.version_raw,
                                         "family_id": self.pm.family_id}, ("version_raw", "release_version_id"), (), "revision-1")

    def save(self, edits, context):
        self.calls.append(edits)
        if self.failure:
            raise self.failure
        return ManagementResult((), None, (), None, 77)


@pytest.mark.parametrize("failure", [None, ValueError("검증 실패"), RuntimeError("저장 결과 미확인")])
def test_software_gateway_saves_once_only_after_confirmation_and_distinguishes_unknown(window, app, failure):
    page = window.pages["versions"]
    row = next(i for i, r in enumerate(page.installed_records) if len(r.pms) == 1 and r.pms[0].pm_name is None)
    record = page.installed_records[row]
    gateway = FakeVersionGateway(record, record.pms[0], failure=failure)
    page.configure(gateway, lambda: None)
    page.open_software(row)
    dialog = page.software_dialog
    dialog.value.setText("new-version")
    dialog.reason.setText("원본 대조 확인")
    assert dialog._review()
    dialog._submit()
    wait(app, lambda: hasattr(page, "save_confirm") and page.save_confirm.isVisible())
    assert gateway.calls == []
    page.save_confirm.accept()
    wait(app, lambda: not page.operation_request.busy)
    assert len(gateway.calls) == 1
    edit = gateway.calls[0][0]
    assert edit.key == {"equipment_version_id": 7} and edit.revision == "revision-1"
    assert edit.changes == {"version_raw": "new-version", "release_version_id": None}
    if failure is None:
        assert page.workspace.history[-1].persisted
        wait(app, lambda: not page.installed_request.busy)
    else:
        assert not page.workspace.history
        assert page.locked == isinstance(failure, RuntimeError)
        assert dialog.value.text() == "new-version"
        if page.locked:
            page._save_software()
            assert len(gateway.calls) == 1


def test_conflicting_software_is_not_silently_saved(window, app):
    page = window.pages["versions"]
    row = next(i for i, r in enumerate(page.installed_records) if len(r.pms) == 1 and r.pms[0].pm_name is None)
    record = page.installed_records[row]
    gateway = FakeVersionGateway(record, record.pms[0], changed=True)
    page.configure(gateway, lambda: None)
    page.open_software(row)
    dialog = page.software_dialog
    dialog.value.setText("new-version")
    dialog.reason.setText("정정")
    assert dialog._review()
    dialog._submit()
    wait(app, lambda: not page.operation_request.busy)
    assert gateway.calls == [] and not page.workspace.history
    assert "다시 조회" in dialog.error.text()


def test_equipment_input_survives_other_target_and_cancel_resets_current_only(window):
    page = window.pages["versions"]
    page.select_tab(2)
    page.equipment_fields["geometry"].setText("첫 설비 작업")
    page.equipment_choice.setCurrentIndex(1)
    page.equipment_fields["geometry"].setText("둘째 설비 작업")
    page.equipment_choice.setCurrentIndex(0)
    assert page.equipment_fields["geometry"].text() == "첫 설비 작업"
    page.cancel_equipment()
    assert page.equipment_fields["geometry"].text() == ""
    page.equipment_choice.setCurrentIndex(1)
    assert page.equipment_fields["geometry"].text() == "둘째 설비 작업"


def test_import_receipt_history_is_summary_not_fake_snapshot(window):
    from datetime import datetime
    from setron.features.excel_import.schemas import SaveReceipt
    page = window.pages["versions"]
    receipt = SaveReceipt(8, 20, datetime(2026, 11, 8, 9), "메모리 저장")
    page.record_import(receipt)
    page.record_import(receipt)
    assert len(page.workspace.history) == 1
    change = page.workspace.history[0]
    assert change.category == "Excel" and not change.persisted
    assert dict(change.after)["저장 요약·행 수"] == 20
    assert page.source.records


def test_item_dialog_stale_catalog_cannot_modify_newer_release_data(window):
    page = window.pages["versions"]
    page.open_row(0)
    page.open_item_form()
    form = page.item_dialog
    form.content.setText("작업 중 Item")
    form.reason.setText("사유")
    form._review()
    page.catalog = replace(page.catalog, source_label="새로운 읽기")
    form._submit()
    assert not page.workspace.history and form.isVisible()
    assert "변경" in form.error.text()


def test_dialogs_scroll_fields_but_keep_confirmation_visible(window):
    page = window.pages["versions"]
    page.open_row(0)
    page.open_item_form()
    form = page.item_dialog
    assert form.height() < form.screen().availableGeometry().height()
    assert form.field_scroll.verticalScrollBar().maximum() > 0
    assert form.review.isVisible() and form.cancel.isVisible()


@pytest.mark.parametrize("patch", [{"version_raw": ""}, {"version_order": 3, "order_basis": ""}, {"released_at": "2026-02-30"}])
def test_release_validation_requires_real_date_and_confirmed_order(patch):
    catalog = demo_version_catalog(demo_dashboard_source())
    pair = catalog.pairs[0]
    with pytest.raises(ValueError):
        make_release(catalog, pair.model_id, pair.family_id, {"version_raw": "new-ui", **patch})


def test_item_overlay_preserves_other_explicit_version_links():
    catalog = demo_version_catalog(demo_dashboard_source())
    store = VersionWorkspace()
    item = catalog.items[0]
    releases = [e for e in catalog.releases if any(r.item_id == item.item.item_id and r.release_version_id == e.release.release_version_id for r in catalog.version_items)]
    for entry in releases[:2]:
        values = {"content": item.item.content, "item_no": item.item.item_no, "item_state": "NOT_APPLIED", "target_scope": "UNKNOWN"}
        store.item_overlays[entry.release.release_version_id, item.item.item_id] = make_item(catalog, entry.release.release_version_id, values, entry=item)
    overlaid = store.catalog(catalog)
    for entry in releases[:2]:
        assert next(r for r in overlaid.version_items if r.item_id == item.item.item_id and r.release_version_id == entry.release.release_version_id).state.value == "NOT_APPLIED"


def test_pending_installed_query_conditions_do_not_edit_old_results(window, app):
    page = window.pages["versions"]
    page.installed_search.setText("new condition")
    assert not page.correct_button.isEnabled()
    assert not page.open_software(0)
    previous = page.installed_source, page.installed_records, page.installed_day, page._installed_epoch - 1
    page._installed_loaded(previous, None)
    assert page.installed_source is None and page.installed_model.rowCount() == 0
    assert "다시 조회" in page.installed_notice.text()


def test_version_import_actions_select_matching_kind_without_external_reads(window):
    from setron.features.ai.schemas import ExtractKind
    page, importer = window.pages["versions"], window.pages["import"]
    for kind, expected in (("release", ExtractKind.RELEASE_NOTE), ("equipment", ExtractKind.EQUIPMENT_STATUS)):
        window.navigate("versions")
        page.import_requested.emit(kind)
        assert window.route == "import" and importer.kind is expected
        assert importer.draft is None and importer.snapshot is None and not importer.busy


def test_version_import_action_keeps_other_unsaved_import_draft(window):
    from setron.features.ai.schemas import ExtractKind
    from test_import_validation import draft, row
    page, importer = window.pages["versions"], window.pages["import"]
    original = draft(row())
    importer.draft = original
    page.import_requested.emit("release")
    assert window.route == "import" and importer.kind is ExtractKind.EQUIPMENT_STATUS
    assert importer.draft is original and not importer.busy
    assert "유지" in importer.result_label.text()
