"""관리 화면의 실제 Qt 입력·검토·워커 저장·오류 보존을 검증한다."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from dataclasses import replace
from datetime import date
from threading import Event, get_ident
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.equipment.schemas import EquipmentSourceCell
from setron.features.management import ManagedRecord, ManagementResult
from setron.features.release.refresh_schemas import RefreshContext, ReleaseRefresh
from setron.features.storage.validation import StorageValidationError
from setron.features.targets.schemas import TargetPair
from setron.features.management.schemas import ApplicabilityView
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseSaveError
from setron.ui.management_data import ManagementRows, gateway_from_env, reference_options
import setron.ui.management_data as gateway_module
from setron.ui.management_page import ManagementPage
from setron.ui.theme import install_theme


def record(table, key, current, fields, *, cells=()):
    return ManagedRecord(table, key, {**key, **current}, tuple(fields), cells, "a" * 64)


class FakeGateway:
    """화면 경계만 가짜로 바꾸며 각 DB 호출이 워커에서 이루어지는지 기록한다."""

    def __init__(self):
        self.threads, self.saves, self.error = [], [], None
        self.gate = Event()
        self.gate.set()
        self.references = {
            "equipment": ({"equipment_id": 1, "equipment_code": "EQ-1"},),
            "equipment_snapshot": ({"snapshot_id": 1, "equipment_id": 1, "base_date": date(2026, 10, 1)},),
            "equipment_group": ({"group_id": 1, "group_name": "M-O2", "category": "OXIDE"},
                                {"group_id": 2, "group_name": "M-O3", "category": "OXIDE"}),
            "version_family": ({"family_id": 1, "family_name": "O2", "ui_type": "INTEGRATED"},),
            "release_version": ({"release_version_id": 1, "family_id": 1, "version_raw": "v1"},
                                {"release_version_id": 2, "family_id": 1, "version_raw": "v2"}),
            "item": ({"item_id": 1, "item_no": 7, "content": "안전 개선"},),
        }
        self.rows = {
            "equipment": (record("equipment", {"equipment_id": 1}, {"equipment_code": "EQ-1"}, ("equipment_code",)),),
            "equipment_snapshot": (record("equipment_snapshot", {"snapshot_id": 1}, {
                "equipment_id": 1, "base_date": date(2026, 10, 1), "site": "화성", "line": "1라인",
                "model_name": "M-O2", "group_id": 1, "production_area": None,
            }, ("line", "model_name", "group_id", "production_area"), cells=(EquipmentSourceCell(
                field="line", batch_id=1, sheet="현황", address="A1", value="원본 1라인", available=True),)),),
            "equipment_pm": (record("equipment_pm", {"pm_id": 1}, {"snapshot_id": 1, "pm_name": "PM1",
                "group_id": 1, "model_name_raw": "M-O2", "family_id": 1, "version_raw": "v1", "release_version_id": 1,
            }, ("group_id", "model_name_raw", "family_id", "version_raw", "release_version_id")),),
            "equipment_version": (record("equipment_version", {"equipment_version_id": 1}, {
                "snapshot_id": 1, "family_id": 1, "version_raw": "v1", "release_version_id": 1,
            }, ("family_id", "version_raw", "release_version_id")),),
            "release_version": (record("release_version", {"release_version_id": 1}, {
                "family_id": 1, "version_raw": "v1", "version_suffix": None, "released_at": None,
                "version_order": 1, "item_mapping_status": "UNKNOWN",
            }, ("version_raw", "version_suffix", "released_at", "version_order", "item_mapping_status")),),
            "item": (record("item", {"item_id": 1}, {"item_no": 7, "content": "안전 개선"}, ("item_no", "content")),),
            "release_version_item": (record("release_version_item", {"release_version_id": 1, "item_id": 1}, {
                "item_state": "APPLIED", "target_scope": "ALL", "target_group_ids": None,
            }, ("item_state", "target_scope", "target_group_ids")),),
            "risk_rule": (record("risk_rule", {"risk_rule_id": 1}, {"rule_type": "DIRECT_VERSION", "family_id": 1,
                "group_id": None, "item_id": None, "release_version_id": 1, "start_release_version_id": None,
                "end_release_version_id": None, "risk_reason": "확인한 결함", "is_enabled": True, "mapping_status": "VERIFIED",
            }, ("rule_type", "family_id", "group_id", "item_id", "release_version_id", "start_release_version_id",
                "end_release_version_id", "risk_reason", "is_enabled", "mapping_status")),),
            "app_setting": (record("app_setting", {"setting_key": "version_age_thresholds"}, {
                "setting_value": {"warning": 1, "danger": 2, "unit": "YEAR"},
            }, ("setting_value",)),),
        }

    def load(self, table, offset=0, limit=100):
        self.threads.append(get_ident())
        return ManagementRows(deepcopy(self.rows.get(table, ())), deepcopy(self.references), offset, False)

    def get(self, table, key):
        self.threads.append(get_ident())
        return deepcopy(next(row for row in self.rows[table] if row.key == key))

    def save(self, edits, context, applicability=()):
        self.threads.append(get_ident())
        self.saves.append((edits, context, applicability))
        assert self.gate.wait(3), "테스트 저장 완료 신호가 필요합니다."
        if self.error is not None:
            raise self.error
        for edit in edits:
            self.rows[edit.table] = tuple(record(row.table, row.key, {**row.current, **edit.changes}, row.editable_fields,
                                                cells=row.original_cells) if row.key == edit.key else row
                                          for row in self.rows[edit.table])
        return ManagementResult((), ReleaseRefresh({}, (), (), ()), (), None, 1)

    def get_applicability(self, model_id, family_id):
        self.threads.append(get_ident())
        return ApplicabilityView(TargetPair(model_id, family_id, (), None, None, False, "미지정"), "b" * 64)


def wait_for(app, condition, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not condition():
        app.processEvents()
        QTest.qWait(5)
    assert condition(), "비동기 화면 작업이 완료되지 않았습니다."


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    install_theme(application)
    return application


@pytest.fixture
def page(app):
    gateway = FakeGateway()
    widget = ManagementPage(gateway)
    widget.resize(1000, 900)
    widget.show()
    wait_for(app, lambda: not widget.busy and widget.record is not None)
    yield widget
    gateway.gate.set()
    wait_for(app, lambda: not widget.busy)
    widget.close()
    widget.deleteLater()
    app.processEvents()


def choose(page, app, table):
    page.kind.setCurrentIndex(page.kind.findData(table))
    wait_for(app, lambda: not page.busy and page.record is not None and page.record.table == table)


def prepare(page):
    page.inputs["equipment_code"].editor.setText("EQ-수정")
    assert page.add_current()
    assert page.review()


def test_unconfigured_page_disables_real_edits(app):
    widget = ManagementPage()
    assert not widget.kind.isEnabled()
    assert not widget.save_button.isEnabled()
    assert "DB 연결 설정" in widget.notice.text()
    assert gateway_from_env({}) is None
    widget.deleteLater()


def test_db_reads_and_saves_run_off_gui_and_context_is_captured_on_gui(page, app):
    main = get_ident()
    contexts = []
    page.context_provider = lambda: (contexts.append(get_ident()) or RefreshContext(targets={(1, 1): 2}))
    received = []
    page.saved.connect(received.append)
    prepare(page)
    assert page.save()
    wait_for(app, lambda: received and not page.busy)
    assert contexts == [main]
    assert page.gateway.threads and all(thread != main for thread in page.gateway.threads)
    assert page.gateway.saves[0][0][0].changes == {"equipment_code": "EQ-수정"}
    assert page.gateway.saves[0][1].targets == {(1, 1): 2}
    assert not page.pending
    assert page.record.current["equipment_code"] == "EQ-수정"


def test_originals_and_fixed_identifiers_are_read_only(page, app):
    choose(page, app, "equipment_snapshot")
    assert set(page.inputs) == {"line", "model_name", "group_id", "production_area"}
    assert all(field not in page.inputs for field in ("site", "base_date", "source_reference", "batch_id"))
    assert page.original_model.rows == (("라인", "현황!A1", "원본 1라인"),)
    page.inputs["line"].editor.setText("2라인")
    assert page.add_current()
    assert page.review()
    assert page.review_model.rows[0][1:] == ("라인", "1라인", "2라인")
    assert page.original_model.rows[0][2] == "원본 1라인"


def test_switching_record_kind_keeps_mixed_batch_and_requires_review(page, app):
    page.inputs["equipment_code"].editor.setText("EQ-수정")
    choose(page, app, "item")
    page.inputs["content"].editor.setPlainText("확인한 새 Item 내용")
    assert page.add_current()
    assert len(page.pending) == 2
    assert not page.save()
    assert page.review()
    assert not page.kind.isEnabled()
    assert page.save()
    wait_for(app, lambda: not page.busy)
    assert [request.table for request in page.gateway.saves[0][0]] == ["equipment", "item"]


@pytest.mark.parametrize("table", ["equipment_pm", "equipment_version", "release_version", "release_version_item", "risk_rule", "app_setting"])
def test_typed_controls_cover_all_management_kinds(page, app, table):
    choose(page, app, table)
    assert page.inputs
    if table == "equipment_pm":
        assert "pm_name" not in page.inputs
        assert page.inputs["family_id"].editor.itemText(0).startswith("O2")
        assert "v1" in page.inputs["release_version_id"].editor.currentText()
    elif table == "release_version":
        editor = page.inputs["released_at"]
        assert editor.empty.isChecked() and editor.value() is None
        editor.empty.setChecked(False)
        assert isinstance(editor.value(), date)
        page.inputs["version_order"].editor.setValue(5)
        assert page.inputs["version_order"].value() == 5
    elif table == "release_version_item":
        scope = page.inputs["target_scope"].editor
        assert not page.inputs["target_group_ids"].isEnabled()
        scope.setCurrentIndex(scope.findData("LISTED"))
        assert page.inputs["target_group_ids"].isEnabled()
        page.inputs["target_group_ids"].editor.item(1).setCheckState(Qt.CheckState.Checked)
        assert page._changes()["target_group_ids"] == [2]
    elif table == "risk_rule":
        page.inputs["is_enabled"].editor.setChecked(False)
        assert page._changes()["is_enabled"] is False
    elif table == "app_setting":
        page.inputs["setting_value"].warning.setValue(3)
        page.inputs["setting_value"].danger.setValue(4)
        assert page._changes()["setting_value"] == {"warning": 3, "danger": 4, "unit": "YEAR"}


def test_required_blank_prevents_queue_or_save(page):
    page.inputs["equipment_code"].editor.setText("  ")
    assert not page.add_current()
    assert not page.pending
    assert not page.review()
    assert not page.save()
    assert "필수값" in page.notice.text()


@pytest.mark.parametrize("error", [StorageValidationError("equipment_code: 중복된 호기입니다."), DatabaseSaveError("테스트 DB 오류")])
def test_definite_save_failure_preserves_draft_and_allows_review_after_fix(page, app, error):
    page.gateway.error = error
    failures = []
    page.save_failed.connect(lambda: failures.append(True))
    prepare(page)
    assert page.save()
    wait_for(app, lambda: not page.busy)
    assert page.state == "failed" and not page.locked
    assert failures == [True]
    assert page.pending and page.inputs["equipment_code"].value() == "EQ-수정"
    assert not page.save_button.isEnabled()
    page.gateway.error = None
    assert page.review()
    assert page.save()
    wait_for(app, lambda: not page.busy)
    assert not page.pending


def test_commit_unknown_locks_resave_and_emits_invalidation(page, app):
    page.gateway.error = DatabaseCommitUnknownError("테스트 commit 결과 미확인")
    invalidated = []
    page.save_uncertain.connect(lambda: invalidated.append(True))
    prepare(page)
    page.save()
    wait_for(app, lambda: not page.busy)
    assert page.locked and page.state == "unknown" and invalidated == [True]
    assert page.pending
    assert not page.save() and not page.refresh()
    assert not page.save_button.isEnabled() and not page.clear_button.isEnabled()


def test_unexpected_save_error_is_not_treated_as_confirmed_rollback(page, app):
    page.gateway.error = RuntimeError("전달 중 오류")
    prepare(page)
    page.save()
    wait_for(app, lambda: not page.busy)
    assert page.locked and page.state == "unknown"
    assert page.pending and not page.save()


def test_busy_save_rejects_duplicate_and_keeps_input_locked(page, app):
    page.gateway.gate.clear()
    prepare(page)
    assert page.save()
    wait_for(app, lambda: len(page.gateway.saves) == 1)
    assert not page.save()
    assert not page.kind.isEnabled() and not page.form.isEnabled()
    page.gateway.gate.set()
    wait_for(app, lambda: not page.busy)
    assert len(page.gateway.saves) == 1


def test_model_applicability_requires_three_confirmed_evidence_fields(page, app):
    page._load_applicability()
    wait_for(app, lambda: not page.busy and page.applicability_view is not None)
    page.candidate_list.item(1).setCheckState(Qt.CheckState.Checked)
    assert not page._stage_applicability()
    page.applicability_basis.setText("원형 모델 적용 표식 확인")
    page.order_basis.setText("v2 분기와 순서를 확인")
    page.source_text.setText("확인한 릴리즈 노트")
    assert page._stage_applicability()
    assert page.review()
    assert page.save()
    wait_for(app, lambda: not page.busy)
    change = page.gateway.saves[0][2][0]
    assert change.candidates[0].release_id == 2
    assert change.candidates[0].source_text == "확인한 릴리즈 노트"


def test_open_specific_record_loads_references_and_record(page, app):
    assert page.open_record("release_version", {"release_version_id": 1})
    wait_for(app, lambda: not page.busy and page.record.table == "release_version")
    assert page.record.key == {"release_version_id": 1}
    assert page.title.text().endswith("v1")


def test_family_change_only_offers_releases_in_selected_family(page, app):
    page.gateway.references["version_family"] += ({"family_id": 2, "family_name": "O3", "ui_type": None},)
    page.gateway.references["release_version"] += ({"release_version_id": 3, "family_id": 2, "version_raw": "O3-v1"},)
    choose(page, app, "equipment_pm")
    family = page.inputs["family_id"].editor
    family.setCurrentIndex(family.findData(2))
    release = page.inputs["release_version_id"].editor
    assert release.findData(1) == -1
    assert release.findData(3) >= 0
    assert release.currentData() is None


def test_repeated_item_numbers_use_release_and_source_to_distinguish_choices():
    references = FakeGateway().references
    references["item"] = (
        {"item_id": 1, "item_no": 7, "content": "같은 내용", "source_reference": {"content": {"sheet": "원본A", "cells": ["B7"]}}},
        {"item_id": 2, "item_no": 7, "content": "같은 내용", "source_reference": {"content": {"sheet": "원본B", "cells": ["C8"]}}},
    )
    references["release_version_item"] = ({"release_version_id": 1, "item_id": 1}, {"release_version_id": 2, "item_id": 2})
    options = dict(reference_options("item_id", references))
    assert options[1] != options[2]
    assert "O2 v1" in options[1] and "원본A!B7" in options[1]
    assert "O2 v2" in options[2] and "원본B!C8" in options[2]


def test_invalid_current_form_keeps_kind_selection_and_draft(page, app):
    page.inputs["equipment_code"].editor.setText(" ")
    page.kind.setCurrentIndex(page.kind.findData("item"))
    assert page.kind.currentData() == "equipment"
    assert page.record.table == "equipment"
    assert page.inputs["equipment_code"].value() == " "
    assert not page.busy


def test_refresh_preserves_desired_changes_and_requires_review_against_latest_values(page, app):
    page.inputs["equipment_code"].editor.setText("EQ-원하는값")
    assert page.add_current() and page.review()
    page.gateway.error = StorageValidationError("검토 후 자료가 바뀌었습니다.")
    page.save()
    wait_for(app, lambda: not page.busy)
    original = page.gateway.rows["equipment"][0]
    page.gateway.rows["equipment"] = (replace(original, current={**original.current, "equipment_code": "EQ-최근값"}, revision="c" * 64),)
    assert page.refresh()
    wait_for(app, lambda: not page.busy)
    assert page.record.current["equipment_code"] == "EQ-최근값"
    assert page.inputs["equipment_code"].value() == "EQ-원하는값"
    assert not page.save()
    assert page.review()
    assert page.review_model.rows[0][2:] == ("EQ-최근값", "EQ-원하는값")
    assert page._reviewed[0][0].revision == "c" * 64


def test_environment_gateway_is_lazy_and_disposes_each_operation(monkeypatch):
    calls = []
    class FakeDatabase:
        def dispose(self):
            calls.append("dispose")
    class Service:
        def __init__(self, storage):
            assert storage == "storage"
        def get_record(self, table, key):
            calls.append((table, key))
            return "record"
    monkeypatch.setattr(gateway_module.Database, "from_env", lambda values: (calls.append("connect") or FakeDatabase()))
    monkeypatch.setattr(gateway_module, "StorageService", lambda database: "storage")
    monkeypatch.setattr(gateway_module, "ManagementService", Service)
    gateway = gateway_from_env({"SETRON_DB_HOST": "localhost", "SETRON_DB_PORT": "3306", "SETRON_DB_NAME": "synthetic",
                                "SETRON_DB_USER": "test", "SETRON_DB_PASSWORD": "test-only"})
    assert calls == []
    assert gateway.get("equipment", {"equipment_id": 1}) == "record"
    assert calls == ["connect", ("equipment", {"equipment_id": 1}), "dispose"]


def test_open_record_ignores_old_read_response_but_never_cancels_save_or_review(page, app):
    gate = Event()
    old_finished = Event()
    def old_load():
        assert gate.wait(3)
        old_finished.set()
        return page.gateway.load("equipment")
    page._start("load", old_load)
    assert page.busy
    assert page.open_record("release_version", {"release_version_id": 1})
    wait_for(app, lambda: not page.busy and page.record.table == "release_version")
    gate.set()
    wait_for(app, old_finished.is_set)
    app.processEvents()
    assert page.record.table == "release_version"
    page.inputs["version_raw"].editor.setText("v1_n4")
    assert page.add_current() and page.review()
    assert not page.open_record("equipment", {"equipment_id": 1})
    page.gateway.gate.clear()
    assert page.save()
    wait_for(app, lambda: len(page.gateway.saves) == 1)
    assert not page.open_record("equipment", {"equipment_id": 1})
    page.gateway.gate.set()
    wait_for(app, lambda: not page.busy)


@pytest.mark.parametrize("width,height", [(1600, 960), (1100, 800), (760, 600)])
def test_page_minimum_width_does_not_force_main_horizontal_overflow(page, app, width, height):
    page.resize(width, height)
    app.processEvents()
    assert page.minimumSizeHint().width() <= width
    assert page.kind.width() > 0


def test_small_tables_are_compact_and_empty_originals_and_review_are_hidden(page, app):
    assert page.record_model.rowCount() == 1
    assert page.record_table.height() <= 160
    assert page.original_table.isHidden()
    assert page.review_table.isHidden()
    choose(page, app, "equipment_snapshot")
    assert page.original_model.rowCount() == 1
    assert not page.original_table.isHidden()
    assert page.original_table.height() <= 160
    page.inputs["line"].editor.setText("수정 라인")
    assert page.add_current() and page.review()
    assert not page.review_table.isHidden()
    assert page.review_table.height() <= 160


def test_large_record_table_is_bounded_and_status_column_stays_in_view(page, app):
    page.record_model.set_rows(tuple((f"EQ-{number:03}", "편집 가능") for number in range(100)))
    page.resize(680, 900)
    page._fit_tables()
    app.processEvents()
    assert page.record_table.height() <= 340
    assert page.record_table.verticalScrollBar().maximum() > 0
    assert page.record_table.columnViewportPosition(1) + page.record_table.columnWidth(1) <= page.record_table.viewport().width() + 1


def test_version_order_candidates_are_staged_for_review(page, app):
    """버전 순서 후보는 수정 목록에만 들어가고, 묶음 검토·저장을 거쳐야 반영된다."""
    from setron.features.release.order_candidates import suggest_version_orders

    rows = (record("release_version", {"release_version_id": 2}, {
        "family_id": 1, "version_raw": "v2", "version_suffix": None, "released_at": None, "version_order": None,
        "item_mapping_status": "UNKNOWN", "batch_id": 5,
        "source_reference": {"version_raw": {"sheet": "Release", "cells": ["A9"]}},
    }, ("version_order",)), record("release_version", {"release_version_id": 3}, {
        "family_id": 1, "version_raw": "v1", "version_suffix": None, "released_at": None, "version_order": None,
        "item_mapping_status": "UNKNOWN", "batch_id": 5,
        "source_reference": {"version_raw": {"sheet": "Release", "cells": ["A2"]}},
    }, ("version_order",)))
    page.gateway.version_order_candidates = lambda family: (rows, suggest_version_orders(
        family, [r.current for r in rows]))
    page.family_choice.setCurrentIndex(page.family_choice.findData(1))

    assert page._load_order_candidates()
    wait_for(app, lambda: not page.busy)

    staged = {record.key["release_version_id"]: changes for record, changes in page.pending.values()}
    assert staged == {3: {"version_order": 10}, 2: {"version_order": 20}}
    assert "2건" in page.applicability_notice.text()
    assert not page.gateway.saves
