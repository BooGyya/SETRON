"""VER5 가져오기 화면·기간별 삭제의 프론트 계약. 운영 DB·Excel·회사 AI는 사용하지 않는다."""

from dataclasses import replace
from datetime import date
from threading import Event
from time import monotonic

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.service import MemoryImportSaver
from setron.features.excel_import.schemas import Issue, IssueLevel
from setron.ui.app import create_window
from setron.ui.import_delete import (
    DeleteConfirmDialog, DeletionBatch, DeletionCatalog, DeletionPlan, DeletionResult, ImportDeletePage,
)
from setron.ui.import_demo import DemoExcel, example_extractor
from setron.ui.import_page import ImportPage
from setron.ui.import_widgets import ComparisonCard, DateField, ImportComparison, ImportDataStateDialog
from setron.ui.theme import ASSETS, install_theme, svg_size
from test_import_validation import draft, row
from test_ui_import import extracted, wait_for_extraction


@pytest.fixture
def own():
    app = QApplication.instance() or QApplication([])
    install_theme(app)
    widgets = []
    def add(widget):
        widgets.append(widget)
        widget.show()
        QTest.qWait(10)
        return widget
    yield add
    for widget in reversed(widgets):
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def import_page(own, **kwargs):
    return own(ImportPage(excel=DemoExcel(), extractor=example_extractor, confirm=lambda _: True, **kwargs))


def until(predicate):
    end = monotonic() + 5
    while not predicate() and monotonic() < end:
        QTest.qWait(10)
    assert predicate()


def catalog():
    eq = ExtractKind.EQUIPMENT_STATUS
    return DeletionCatalog("rev-1", (
        DeletionBatch("july", eq, date(2026, 7, 21), "화성", None, "Foundry", 12, 16, "2026-07-30", "Excel 가져오기"),
        DeletionBatch("aug", eq, date(2026, 8, 21), "화성", None, "Memory", 84, 100, "2026-08-21", "Excel 가져오기"),
        DeletionBatch("aug-other", eq, date(2026, 8, 21), "평택", None, "Memory", 20, 25, "2026-08-21", "Excel 가져오기"),
        DeletionBatch("oct", eq, date(2026, 10, 21), "화성", None, "Memory", 85, 110, "2026-10-21", "DB 가져오기"),
        DeletionBatch("release", ExtractKind.RELEASE_NOTE, None, None, "C4", "Release List", 0, 900, "2026-11-07", "Excel 가져오기"),
    ), "테스트용 합성 저장 목록")


class Gateway:
    def __init__(self, outcome="success", gate=None):
        self.calls = []
        self.outcome, self.gate = outcome, gate
    def list_batches(self):
        return catalog()
    def delete(self, plan):
        self.calls.append(plan)
        if self.gate:
            self.gate.wait(4)
        if self.outcome == "exception":
            raise RuntimeError("private-token-not-for-ui")
        return DeletionResult(self.outcome, plan.keys if self.outcome == "success" else (),
                              "합성 결과", "rev-2" if self.outcome == "success" else "")


def select_period(page):
    page.set_catalog(catalog())
    page.site_combo.setCurrentIndex(page.site_combo.findData("화성"))
    page.start.setText("2026-07-01")
    page.end.setText("2026-08-31")


def approve(dialog):
    dialog.phrase.setText("삭제")
    return True


def test_tabs_preserve_import_draft_and_editor(own):
    page = import_page(own)
    extracted(page)
    original = page.draft
    page.inputs["version_raw"].setText("unsubmitted edit")
    page.tabs.setCurrentIndex(1)
    assert page.workspace.currentIndex() == 1 and not page.steps.isVisible()
    page.tabs.setCurrentIndex(0)
    assert page.draft is original and page.inputs["version_raw"].text() == "unsubmitted edit"


def test_first_step_context_applies_only_to_blank_fields(own):
    page = import_page(own)
    page.common_date.setText("2026-10-05")
    page.common_site.setText("평택")
    extracted(page)
    assert all(r.value("base_date") == "2026-10-05" for r in page.draft.rows)
    assert all(r.value("site") == "화성" for r in page.draft.rows)  # 추출된 Site를 자동 덮어쓰지 않는다.
    assert all(r.original.get("base_date") is None for r in page.draft.rows)


def test_kind_changes_context_without_inventing_model_mapping(own):
    page = import_page(own)
    page.configure_choices(sites=("화성", "미국"), dates=(date(2026, 10, 21),), families=("C4", "O6"))
    page.kind_combo.setCurrentIndex(page.kind_combo.findData(ExtractKind.RELEASE_NOTE.value))
    # 릴리즈 노트도 ‘설비군 / SW 계열’ 입력 칸은 화면에 두지 않는다. 기준 날짜·Site 칸도 숨는다.
    assert not page.release_context.isVisibleTo(page) and not page.common_box.isVisibleTo(page)
    d = draft(row(family_name=None), kind=ExtractKind.RELEASE_NOTE)
    page.draft = d
    page.release_family.setCurrentText("C4")
    assert page.apply_release_family()
    assert page.draft.row(1).value("family_name") == "C4"
    assert page.draft.row(1).value("model_name") == d.row(1).value("model_name")
    assert page.draft.row(1).original == d.row(1).original


def test_suggestion_is_explicit_and_preserves_source(own):
    page = import_page(own)
    page.draft = draft(row(version_raw="v90.1.0,43"))
    page.select_row(1)
    original = page.draft.row(1)
    assert page.suggestion_button.isVisibleTo(page)
    assert original.value("version_raw") == "v90.1.0,43"
    page.suggestion_button.click()
    assert page.draft.row(1).value("version_raw") == "v90.1.0.43"
    assert page.draft.row(1).original == original.original
    assert page.draft.row(1).sources == original.sources
    page.revert_selected()
    assert page.draft.row(1).value("version_raw") == "v90.1.0,43"


def test_unapplied_editor_values_cannot_be_silently_saved(own):
    page = import_page(own)
    page.draft = draft(row())
    page.select_row(1)
    page._changed()
    assert page.save_button.isEnabled()
    page.inputs["version_raw"].setText("v2")
    assert not page.save_button.isEnabled()
    assert not page.save() and "수정 반영" in page.notice.text()
    assert page.apply_edits() and page.save_button.isEnabled()


def test_origin_rows_are_distinct_from_extraction_ids_and_split_rows_share_cell(own):
    page = import_page(own)
    first = row(version_raw="v1")
    first = replace(first, sources={**first.sources, "equipment_code": "C12", "version_raw": "F12"})
    second = replace(first, row_id=2, original={**first.original, "family_name": "O3"})
    page.draft = draft(first, second)
    page.select_row(1)
    assert [r[0] for r in page.row_model.rows] == ["12-a", "12-b"]
    assert [r[-1] for r in page.row_model.rows] == ["F12", "F12"]
    assert page.row_model.ids == (1, 2)


def test_review_action_marks_only_frontend_acknowledgement_and_edits_reset_it(own):
    page = import_page(own)
    page.draft = draft(row())
    original = page.draft
    page.select_row(1)
    issue = Issue(IssueLevel.REVIEW, "원본 대조 필요", 1, "version_raw", "I1", code="REVIEW_ONLY")
    page.issues = (issue,)
    page._render()
    page._activate_issue(page.issue_model.index(0, page.issue_model.action_column))
    assert page.draft is original and page.issues[0] is issue and issue.level is IssueLevel.REVIEW
    assert page.issue_model.data(page.issue_model.index(0, page.issue_model.action_column)) == "확인 완료"
    assert page.review_ack_button.isEnabled() and page.review_ack_button.text() == "확인 취소"
    assert "검토 확인 1건" in page.save_summary.text()
    page.inputs["version_raw"].setText("v2")
    page.apply_edits()
    assert not page.reviewed_issues


def test_error_cannot_be_acknowledged_instead_of_corrected(own):
    page = import_page(own)
    page.draft = draft(row())
    page.select_row(1)
    page.issues = (Issue(IssueLevel.ERROR, "필수값 수정 필요", 1, "version_raw", "I1"),)
    page._render()
    assert not page.toggle_issue_reviewed(0)
    assert not page.save_button.isEnabled()


def test_review_acknowledgement_does_not_discard_unapplied_editor_text(own):
    page = import_page(own)
    page.draft = draft(row())
    page.select_row(1)
    page.issues = (Issue(IssueLevel.REVIEW, "원본 대조 필요", 1, "version_raw", "I1"),)
    page._render()
    page.inputs["version_raw"].setText("입력 중인 값")
    page._activate_issue(page.issue_model.index(0, page.issue_model.action_column))
    assert page.inputs["version_raw"].text() == "입력 중인 값"
    assert not page.save_button.isEnabled()


def test_comparison_does_not_fake_zero_and_rejects_invalid_counts(own):
    card = own(ComparisonCard())
    assert card.comparison is None and not card.counts.isVisible()
    with pytest.raises(ValueError):
        ImportComparison(date(2026, 9, 21), "화성 Memory", -1, 0, 5, 1)
    result = ImportComparison(date(2026, 9, 21), "화성 Memory", 2, 0, 5, 1, ("DM-044 v1 → v2",))
    card.show_comparison(result)
    assert card.comparison is result and "과거 기록을 자동 삭제하지 않습니다" in card.details.text()
    assert [v.text() for v in card.counts.findChildren(QLabel) if v.text().endswith("대")] == ["2대", "0대", "5대", "1대"]


def test_comparison_is_async_and_ignores_cancelled_results(own):
    gate = Event()
    result = ImportComparison(date(2026, 9, 21), "화성 Memory", 2, 0, 5, 1)
    def load(value):
        gate.wait(4)
        return result
    page = import_page(own, comparison_loader=load)
    page.draft = draft(row())
    page._changed()
    assert page.comparison_request.busy and not page.save_button.isEnabled()
    page._reset_draft()
    gate.set()
    QTest.qWait(100)
    assert page.comparison_card.comparison is None
    page.draft = draft(row())
    page._changed()
    until(lambda: not page.comparison_request.busy)
    assert page.comparison_card.comparison == result


def test_progress_and_tabs_stay_fixed_when_import_content_scrolls(own):
    window = own(create_window(excel=DemoExcel()))
    window.resize(1000, 740)
    window.navigate("import")
    page = window.pages["import"]
    extracted(page)
    QTest.qWait(50)
    before = page.steps.mapTo(window, QPoint()).y()
    page.jump_to_step(3)
    QTest.qWait(50)
    assert page.import_scroll.verticalScrollBar().value() > 0
    assert page.steps.mapTo(window, QPoint()).y() == before
    assert page.tabs.mapTo(window, QPoint()).y() < before
    assert page.mapTo(window, QPoint(0, page.height())).y() <= window.height()


def test_leaving_pending_comparison_restores_controls_without_accepting_late_result(own):
    gate = Event()
    result = ImportComparison(date(2026, 9, 21), "화성 Memory", 2, 0, 5, 1)
    def load(value):
        gate.wait(4)
        return result
    page = import_page(own, comparison_loader=load)
    page.draft = draft(row())
    page.select_row(1)
    page._changed()
    assert page.comparison_request.busy and not page.tabs.isEnabled()
    page.hide()
    page.show()
    assert not page.busy and page.tabs.isEnabled() and page.list_button.isEnabled()
    assert "취소" in page.comparison_card.details.text()
    gate.set()
    QTest.qWait(100)
    assert page.comparison_card.comparison is None


def test_saved_banner_routes_only_after_success_and_locks_second_save(own):
    saver = MemoryImportSaver("합성 저장소")
    page = import_page(own, saver=saver)
    page.draft = draft(row())
    page.workbook_name = page.draft.workbook_name
    page.select_row(1)
    page._changed()
    assert not page.saved_banner.isVisibleTo(page)
    assert page.save()
    wait_for_extraction(page)
    assert page.receipt is not None and page.saved_banner.isVisibleTo(page)
    assert page.progress_steps.stage == 4 and not page.save_button.isEnabled()
    assert not page.save()
    routes = []
    page.navigate_requested.connect(routes.append)
    next(b for b in page.saved_banner.findChildren(QPushButton) if b.text() == "히트맵에서 보기").click()
    assert routes == ["heatmap"] and len(saver.saved) == 1
    page.kind_combo.setCurrentIndex(1)
    assert page.draft is None and page.receipt is None and not page.saved_banner.isVisibleTo(page)


def test_data_status_distinguishes_four_states_and_date_selection(own):
    page = own(ImportDataStateDialog((date(2026, 10, 21),)))
    seen = []
    page.date_selected.connect(seen.append)
    page._choose()
    assert seen == [date(2026, 10, 21)]
    assert page.dates.count() == 1


def test_default_delete_never_calls_or_invents_database(own):
    page = own(ImportDeletePage())
    assert not page.reload() and page.catalog is None
    assert not page.notice.text() and not page.notice.isVisibleTo(page)
    assert "—" in page.counts.text()
    assert not page.delete_button.isEnabled() and not page.backup_button.isEnabled()


def test_period_is_inclusive_scoped_and_distinguishes_physical_and_sw_counts(own):
    page = own(ImportDeletePage())
    select_period(page)
    assert page.selected == {"july", "aug"}
    assert "116행" in page.counts.text() and "남는 기준일 2개" in page.counts.text()
    assert not page.checks["aug-other"].isEnabled()
    assert not page.checks["oct"].isEnabled()
    page.start.setText("2026-09-01")
    assert not page.selected and not page.delete_button.isEnabled()
    page.start.setText("invalid")
    assert page.plan() is None and not page.delete_button.isEnabled()
    assert page.range_message.isVisibleTo(page)


def test_release_mode_uses_family_not_hidden_dates_or_site(own):
    page = own(ImportDeletePage())
    select_period(page)
    page.kind_combo.setCurrentIndex(1)
    assert not page.start_box.isVisibleTo(page) and page.family_box.isVisibleTo(page)
    assert not page.selected
    page.family_combo.setCurrentIndex(page.family_combo.findData("C4"))
    assert page.selected == {"release"}
    assert page.plan().start is None and page.plan().site is None


def test_read_only_release_catalog_refreshes_after_a_successful_import(own):
    saver = MemoryImportSaver()

    class ReadOnlyReleaseGateway(Gateway):
        release_deletion_supported = False

        def list_batches(self):
            stored = replace(catalog().batches[-1], deletable=False)
            new = replace(stored, key="new-release", family="O2", sheet="신규 릴리즈", row_count=3)
            return DeletionCatalog("rev-1", (stored, new) if saver.saved else (stored,), "")

    gateway = ReadOnlyReleaseGateway()
    page = import_page(own, saver=saver, delete_gateway=gateway)
    deletion = page.deletion
    page.tabs.setCurrentIndex(1)
    until(lambda: not deletion.busy)
    deletion.kind_combo.setCurrentIndex(1)
    assert deletion.table.rowCount() == 1
    assert not page.delete_steps.isVisibleTo(page)
    assert deletion.table.isColumnHidden(0) and not deletion.impact_card.isVisibleTo(page)
    assert "아이템 매니저" in deletion.kind_notice.text()

    page.tabs.setCurrentIndex(0)
    page.kind_combo.setCurrentIndex(1)
    page.draft = draft(row(content="수정 내용", item_no="7"), kind=ExtractKind.RELEASE_NOTE)
    page.workbook_name = page.draft.workbook_name
    page._changed()
    assert page.save()
    wait_for_extraction(page)
    assert saver.saved

    page.tabs.setCurrentIndex(1)
    until(lambda: not deletion.busy)
    assert deletion.table.rowCount() == 2 and deletion.family_combo.currentText() == "전체"
    deletion.family_combo.setCurrentIndex(deletion.family_combo.findData("O2"))
    assert deletion.table.rowCount() == 1
    assert deletion.table.item(0, 2).text() == "신규 릴리즈" and deletion.table.item(0, 3).text() == "3개"
    assert not deletion.confirm_delete() and not gateway.calls


def test_quick_ranges_use_latest_saved_date_and_keyboard_toggle_works(own):
    page = own(ImportDeletePage())
    select_period(page)
    page.quick_range("latest")
    assert page.start.text() == page.end.text() == "2026-10-21"
    assert page.selected == {"oct"}
    assert page.quick_buttons[0].isChecked()
    page.checks["oct"].setFocus()
    QTest.keyClick(page.checks["oct"], Qt.Key.Key_Space)
    assert not page.selected
    page.quick_range("quarter")
    assert page.start.text() == "2026-07-21"


def test_header_select_all_never_selects_ineligible_site_or_date(own):
    page = own(ImportDeletePage())
    select_period(page)
    assert page.select_all.checkState() == Qt.CheckState.Checked
    page.select_all.click()
    assert not page.selected
    page.checks["july"].click()
    assert page.select_all.checkState() == Qt.CheckState.PartiallyChecked
    page.select_all.click()
    assert page.selected == {"july", "aug"} and not page.checks["aug-other"].isChecked()


@pytest.mark.parametrize("connected", [False, True])
def test_confirmation_requires_exact_phrase_and_connected_service(own, connected):
    data = catalog()
    plan = DeletionPlan(data.revision, ("july",), ExtractKind.EQUIPMENT_STATUS, date(2026, 7, 1), date(2026, 7, 31), "화성", None)
    dialog = own(DeleteConfirmDialog(plan, data.batches[:1], connected=connected))
    dialog.phrase.setText("삭제 ")
    assert not dialog.delete_button.isEnabled()
    dialog.phrase.setText("삭제")
    assert dialog.delete_button.isEnabled() == connected


def test_programmatic_accept_without_phrase_and_changed_selection_cannot_delete(own):
    gateway = Gateway()
    page = own(ImportDeletePage(gateway))
    select_period(page)
    page.open_dialog = lambda dialog: True
    assert not page.confirm_delete() and not gateway.calls
    def stale(dialog):
        dialog.phrase.setText("삭제")
        page.clear_selection()
        return True
    page.open_dialog = stale
    assert not page.confirm_delete() and not gateway.calls


@pytest.mark.parametrize("status", ["success", "failed", "unknown", "exception"])
def test_delete_service_outcomes_do_not_fake_success_or_repeat_uncertain_write(own, status):
    gate = Event()
    gateway = Gateway(status, gate)
    page = own(ImportDeletePage(gateway))
    select_period(page)
    results = []
    page.deleted.connect(results.append)
    page.open_dialog = approve
    assert page.confirm_delete()
    assert page.busy and not page.delete_button.isEnabled()
    assert not page.confirm_delete()
    gate.set()
    until(lambda: not page.busy)
    assert len(gateway.calls) == 1 and gateway.calls[0].keys == ("july", "aug")
    assert "private-token" not in page.notice.text()
    if status == "success":
        assert len(results) == 1 and {b.key for b in page.catalog.batches} == {"aug-other", "oct", "release"}
    elif status == "failed":
        assert not results and page.selected == {"july", "aug"} and page.phase == "idle"
    else:
        assert not results and page.phase == "unknown"
        assert not page.confirm_delete() and not page.reload()
        assert not page.set_catalog(catalog())


def test_catalog_empty_failure_and_duplicate_ids_are_distinct(own):
    page = own(ImportDeletePage(Gateway()))
    assert page.reload()
    until(lambda: not page.busy)
    assert page.catalog == catalog()
    page.set_catalog(DeletionCatalog("empty", (), "빈 합성 목록"))
    assert "없습니다" in page.empty.text() and "0행" in page.counts.text()
    with pytest.raises(ValueError):
        DeletionCatalog("bad", (catalog().batches[0], catalog().batches[0]), "중복")


def test_failed_catalog_read_is_not_a_successful_empty_list(own):
    class Failed(Gateway):
        def list_batches(self):
            raise RuntimeError("private-connection-secret")
    page = own(ImportDeletePage(Failed()))
    assert page.reload()
    until(lambda: not page.busy)
    assert page.catalog is None and "읽지 못했습니다" in page.notice.text()
    assert "private" not in page.notice.text() and "—" in page.counts.text()


@pytest.mark.parametrize("reply", [
    DeletionResult("success", ("july",), "일부만 응답", "rev-2"),
    DeletionResult("success", ("july", "aug", "other"), "다른 대상 포함", "rev-2"),
    DeletionResult("success", ("july", "aug"), "오래된 응답", "rev-1"),
])
def test_incomplete_or_stale_delete_acknowledgement_is_unknown(own, reply):
    class Partial(Gateway):
        def delete(self, plan):
            return reply
    page = own(ImportDeletePage(Partial()))
    select_period(page)
    results = []
    page.deleted.connect(results.append)
    page.open_dialog = approve
    assert page.confirm_delete()
    until(lambda: not page.busy)
    assert page.phase == "unknown" and not results
    assert page.catalog == catalog()


def test_app_guards_exit_and_ai_while_explicit_delete_service_is_working(own):
    gate = Event()
    window = own(create_window(excel=DemoExcel(), chat_enabled=True, import_delete_gateway=Gateway(gate=gate)))
    window.navigate("import")
    page = window.pages["import"].deletion
    select_period(page)
    page.open_dialog = approve
    assert page.confirm_delete()
    try:
        assert not window.close_guard()
        assert not window.pages["ai"].analysis_allowed()
        assert not window.close()
        assert "저장·삭제" in window.notice.text()
    finally:
        gate.set()
        until(lambda: not page.busy)
    assert window.close_guard()


def test_figma_assets_keep_root_dimensions_and_are_packaged(own):
    for name, size in (("ba8e3.svg", 16), ("fdb91.svg", 16), ("3ad3a.svg", 18), ("116c6.svg", 20)):
        assert (ASSETS / "import-ver5" / name).stat().st_size > 0
        assert svg_size(f"import-ver5/{name}").width() == size
    field = own(DateField())
    field.open_calendar()
    assert field.calendar_menu.isVisible()
    field.calendar_menu.close()
