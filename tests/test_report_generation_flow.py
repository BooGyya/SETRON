"""생성 시점 검증 팝업 → 형식 선택 → 미리보기 흐름. 운영 DB나 Office UI는 사용하지 않는다."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QDate
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from setron.ui.report_data import generate_report, unavailable_report_contents
from test_report_mentoring6 import report_source, request
from test_ui_import_ver5 import own, until  # noqa: F401
from test_ui_reports_ver5 import page


def test_search_applies_only_filters_and_checks_common_without_report_validation(own, tmp_path, monkeypatch):
    ai_calls = []
    p = page(own, tmp_path, ai_provider=lambda: ai_calls.append(True))
    p.select_contents(False)
    p.type_buttons["item"].click()
    p.type_buttons["ai"].click()
    until(lambda: not p.item_request.busy)
    assert p.item.currentData() is None
    site, group = p.source.catalog.sites[0].key, p.source.catalog.groups[0].key
    p.query_panel.select("site_key", site)
    p.query_panel.select("group_key", group)
    p.start.setDate(QDate(2026, 10, 1))
    p.end.setDate(QDate(2026, 10, 22))
    calls = []
    monkeypatch.setattr("setron.ui.reports_page.generate_report", lambda *args: calls.append(args))
    p.query_panel.search_button.click()
    assert p.state.applied.selection.site_key == site and p.state.applied.selection.group_key == group
    assert p.query_panel._applied_range == (p.start.date().toPython(), p.end.date().toPython())
    assert all(c.isChecked() for c in (*p.content_checks.values(), *p.heatmap_checks.values()))
    assert p.selected_types == () and p.item.currentData() is None and not p.item.isEnabled()
    assert p.select_all_button.text() == "전체 선택"
    assert calls == [] and ai_calls == [] and not p.request.busy
    assert p.result is None and p.alert_dialog is None and p.format_dialog is None and p.generation_dialog is None
    assert not p.saved_entries


def test_report_generation_remains_separate_after_search(own, tmp_path):
    from test_ui_reports_ver5 import generated
    p = page(own, tmp_path)
    p.select_contents(False)
    assert p.search()
    query = p.state.applied
    assert p.result is None and p.format_dialog is None
    for key, control in p.content_checks.items():
        control.setChecked(key == "summary")
    for control in p.heatmap_checks.values():
        control.setChecked(False)
    result = generated(p)
    assert result.request.query.selection == query.selection
    assert {s.content_key for s in result.sections} == {"summary"}


def test_invalid_search_still_selects_only_common_and_does_not_generate(own, tmp_path):
    p = page(own, tmp_path)
    p.select_contents(False)
    p.type_buttons["ai"].click()
    p.start.setDate(QDate(2026, 11, 10))
    p.end.setDate(QDate(2026, 11, 9))
    assert not p.search()
    assert "시작 날짜" in p.alert_dialog.message.text()
    assert all(c.isChecked() for c in (*p.content_checks.values(), *p.heatmap_checks.values()))
    assert p.selected_types == () and p.format_dialog is None and p.result is None


def test_empty_selection_alerts_on_generate_only_and_cancel_format_does_not_open_preview(own, tmp_path):
    p = page(own, tmp_path)
    p.select_contents(False)
    assert p.alert_dialog is None and p.generation_status.isHidden()
    assert not p.generate()
    assert "하나 이상" in p.alert_dialog.message.text()
    assert p.format_dialog is None and p.preview_dialog is None
    p.alert_dialog.confirm_button.click()
    assert p.alert_dialog is None
    p.content_checks["summary"].click()
    assert p.generate()
    until(lambda: p.format_dialog is not None)
    assert p.saving and p.preview_dialog is None
    assert not p.generate()  # 선택창이 열린 동안 중복 생성 금지
    assert not any(text.text() == "생성된 보고서" for text in p.findChildren(QLabel))
    assert not hasattr(p, "download_buttons")
    p.format_dialog.cancel_button.click()
    QTest.qWait(30)
    assert not p.saving and p.preview_dialog is None and not p.saved_entries


def test_ai_selection_defers_validation_and_provider_until_generate(own, tmp_path):
    calls = []
    p = page(own, tmp_path, ai_provider=lambda: calls.append(True))
    p.select_contents(False)
    p.type_buttons["ai"].click()
    assert calls == [] and p.alert_dialog is None
    assert "완료된 AI 답변 없음" not in p.ai_note.text()
    assert not p.generate()
    assert calls == [True] and "완료된 AI 답변이 없습니다" in p.alert_dialog.message.text()
    assert p.generation_status.isHidden() and not p.request.busy
    assert p.format_dialog is None and p.result is None


@pytest.mark.parametrize("choice, expected", [
    ("risk", "위험 판정 근거"), ("change", "변경 이력"), ("upgrade", "업그레이드"),
    ("evidence", "관측 버전과 원문 근거"), ("subline", "세부라인별 히트맵"),
    ("summary", "요약"), ("trend", "적용률 추이"), ("distribution", "구성비와 분포"), ("status", "현황·표준화"),
])
def test_selected_contents_with_no_output_alert_before_format_selection(own, tmp_path, choice, expected):
    source = report_source()
    source.risks.clear()
    if choice == "upgrade":
        from setron.features.risk.schemas import RiskStatus
        source.risks.update({r.equipment_id: RiskStatus.NO_RISK for r in source.records})
        source.records = tuple(replace(r, pms=tuple(replace(pm, version_raw="v2") for pm in r.pms)) for r in source.records)
    elif choice == "evidence":
        source.records = tuple(replace(r, pms=()) for r in source.records if not r.is_hpa)
    p = page(own, tmp_path, source=source)
    p.select_contents(False)
    if choice in p.content_checks:
        p.content_checks[choice].click()
    elif choice in p.type_buttons:
        p.type_buttons[choice].click()
    else:
        p.heatmap_checks[choice].click()
        p.query_panel.select("fab_key", "FOUNDRY")  # 실제 F/D 세부 구분 없는 P3-S5
    if choice == "change":
        p.start.setDate(p.end.date())
    elif choice in {"summary", "trend", "distribution", "status"}:
        p.start.setDate(QDate(2030, 1, 1))
        p.end.setDate(QDate(2030, 1, 2))
    assert p.alert_dialog is None
    assert p.generate()
    until(lambda: not p.request.busy)
    assert p.alert_dialog is not None and expected in p.alert_dialog.message.text()
    assert p.generation_status.isHidden() and p.format_dialog is None and p.preview_dialog is None
    assert not p.saved_entries


def test_missing_one_selected_content_does_not_silently_generate_other_contents(own, tmp_path):
    source = report_source()
    source.risks.clear()
    p = page(own, tmp_path, source=source)
    p.select_contents(False)
    p.content_checks["summary"].click()
    p.content_checks["risk"].click()
    assert p.generate()
    until(lambda: not p.request.busy)
    assert "위험 판정 근거" in p.alert_dialog.message.text() and p.result is None
    p.alert_dialog.accept()
    p.content_checks["risk"].click()
    assert p.generate()
    until(lambda: p.format_dialog is not None)
    assert {s.content_key for s in p.result.sections} == {"summary"}
    p.format_dialog.reject()


def test_zero_percent_and_unknown_values_are_not_treated_as_missing():
    source = report_source()
    source.records = tuple(replace(r, pms=tuple(replace(pm, version_raw="v1") for pm in r.pms)) for r in source.records)
    report = generate_report(source, request(source, contents=("summary", "trend"), types=()))
    assert unavailable_report_contents(report) == ()
    assert "0.0%" in next(s.note for s in report.sections if s.title == "목표 N 달성률")
    source.records = tuple(replace(r, pms=tuple(replace(pm, version_raw=None) for pm in r.pms)) for r in source.records)
    report = generate_report(source, request(source, contents=("summary", "trend"), types=()))
    assert unavailable_report_contents(report) == ()
    assert "계산 불가" in next(s.note for s in report.sections if s.title == "목표 N 달성률")


def test_item_list_failure_is_shown_on_generate_instead_of_selection(own, tmp_path):
    p = page(own, tmp_path)
    def fail():
        raise RuntimeError("검증용 목록 오류")
    p.version_loader = fail
    p.select_contents(False)
    p.type_buttons["item"].click()
    until(lambda: not p.item_request.busy)
    assert p.alert_dialog is None and p.generation_status.isHidden()
    assert not p.generate()
    assert "Item 목록을 읽지 못했습니다" in p.alert_dialog.message.text()
