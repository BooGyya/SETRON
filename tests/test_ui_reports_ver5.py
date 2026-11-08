"""VER5 보고서 프론트·로컬 출력 검증. 회사 DB·AI·Excel에 연결하지 않는다."""

from dataclasses import replace
from datetime import date
from threading import Event
from time import monotonic
from zipfile import ZipFile

import pytest
from docx import Document
from openpyxl import load_workbook
from PySide6.QtCore import QSettings, Qt
from PySide6.QtPdf import QPdfDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from setron.features.chat.schemas import ChatAnswer, ChatRequest, Intent, Table
from setron.ui.app import create_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.query_state import AppliedQuery, QuerySelection
from setron.ui.report_data import GeneratedReport, ReportRequest, ReportSection, generate_report
from setron.ui.report_export import pdf_document, report_html, save_generated_report
from setron.ui.reports_page import ReportsPage
from setron.ui.report_widgets import ReportSaveDialog
from setron.ui.target_data import initial_targets
from setron.ui.theme import ASSETS, svg_size
from setron.ui.version_demo import demo_version_catalog
from test_ui_import_ver5 import own, until  # noqa: F401


def request(source, **kwargs):
    query = AppliedQuery(QuerySelection(), 1, 0, "전체", initial_targets(source))
    return replace(ReportRequest("status", query, max(source.analysis_dates), min(source.analysis_dates),
                                 max(source.analysis_dates)), **kwargs)


def page(own, tmp_path, **kwargs):
    source = demo_dashboard_source()
    result = own(ReportsPage(source, targets_provider=lambda: initial_targets(source),
        version_loader=lambda: demo_version_catalog(source),
        settings=QSettings(str(tmp_path / "reports.ini"), QSettings.Format.IniFormat),
        ai_settings=QSettings(str(tmp_path / "ai.ini"), QSettings.Format.IniFormat), **kwargs))
    result.resize(1350, 1000)
    QApplication.processEvents()
    return result


def generated(page):
    page.generate()
    until(lambda: not page.request.busy)
    assert page.result is not None, page.result_status.text()
    return page.result


def test_landing_all_five_cards_and_two_fixed_steps_without_back(own, tmp_path):
    p = page(own, tmp_path)
    assert p.stack.currentIndex() == 0 and len(p.create_buttons) == 5
    assert p.library_model.rowCount() == 0
    for kind, b in p.create_buttons.items():
        b.click()
        assert p.kind.value() == kind and p.stack.currentIndex() == 1
        assert p.tabs.count() == 2 and not p.tabs.usesScrollButtons()
        p.enter()
        assert p.stack.currentIndex() == 0
    assert not any("뒤로" in b.text() for b in p.findChildren(QPushButton))
    until(lambda: not p.item_request.busy)


def test_landing_transfers_scope_period_format_and_date_calendar(own, tmp_path):
    p = page(own, tmp_path)
    p.landing_site.setCurrentIndex(1)
    p.landing_group.setCurrentIndex(1)
    p.landing_format.setCurrentIndex(1)
    p.start_flow("upgrade")
    assert p.site.currentData() == p.landing_site.currentData()
    assert p.group.currentData() == p.landing_group.currentData()
    assert p.format.value() == "docx"
    assert p._applied[1] == date(2026, 9, 21)
    p.base_date.open_calendar()
    assert p.base_date.calendar_menu.actions()[0].defaultWidget().year_combo is not None
    p.base_date.calendar_menu.close()
    until(lambda: not p.item_request.busy)


def test_generate_has_four_document_tabs_and_options_really_exclude_sections(own, tmp_path):
    p = page(own, tmp_path)
    p.start_flow("status")
    p.options["heatmap"].setChecked(False)
    p.options["distribution"].setChecked(False)
    r = generated(p)
    assert p.tabs.currentIndex() == 1 and p.save_button.isEnabled()
    assert p.document.stack.count() == 4
    assert not any(s.chart == "distribution" or s.title == "버전 히트맵 발췌" for s in r.sections)
    for index in range(4):
        p.document.tabs.buttons[str(index)].click()
        assert p.document.stack.currentIndex() == index
    assert p.result_filename.text().endswith(".xlsx")


def test_conditions_require_apply_and_invalid_dates_do_not_generate(own, tmp_path):
    p = page(own, tmp_path)
    p.start_flow("status")
    p.base_date.setText("2026-22-40")
    assert not p._apply_conditions()
    p.generate()
    assert not p.request.busy and p.result is None
    p.reset_conditions()
    p.site.setCurrentIndex(1)
    p.generate()
    assert "먼저 적용" in p.generation_status.text() and p.result is None
    until(lambda: not p.item_request.busy)


def test_report_counts_exact_denominator_dates_and_no_fake_lines():
    source = demo_dashboard_source()
    r = generate_report(source, request(source))
    summary = next(s.table for s in r.sections if s.title == "조회 조건과 집계 기준")
    snapshot = source.snapshot(request(source).query)
    assert summary.rows[0] == (snapshot.summary.equipment_count,
        snapshot.summary.compliant + snapshot.summary.below + snapshot.summary.undetermined,
        snapshot.summary.compliant, snapshot.summary.below, snapshot.summary.undetermined)
    trends = next(s.table for s in r.sections if s.title == "날짜별 적용률 추이")
    assert tuple(row[0] for row in trends.rows) == tuple(map(str, source.analysis_dates))
    heatmap = next(s.table for s in r.sections if s.title == "버전 히트맵 발췌")
    assert all(row[1] == "Line 미지정" for row in heatmap.rows)
    distribution = next(s.table for s in r.sections if s.title == "구성비·막대·트리맵")
    assert tuple(sum(row[i] for row in distribution.rows) for i in (1, 2, 3)) == summary.rows[0][2:]


def test_unknown_date_does_not_substitute_previous_snapshot_and_empty_single_states():
    source = demo_dashboard_source()
    r = generate_report(source, request(source, base_date=date(2026, 10, 22)))
    assert next(s.table for s in r.sections if s.title == "조회 조건과 집계 기준").rows[0] == (0, 0, 0, 0, 0)
    assert "保存" not in report_html(r)
    for mode in ("empty", "single"):
        r = generate_report(source, request(source, demo=mode))
        trend = next(s for s in r.sections if s.title == "날짜별 적용률 추이")
        assert not trend.chart and len(trend.table.rows) <= 1
        assert "UI 시연" in r.source


def test_hpa_only_source_has_no_analysis_but_preserves_excluded_count(own, tmp_path):
    from setron.ui.dashboard_data import DashboardSource, GroupBinding
    source = demo_dashboard_source()
    hpa = tuple(r for r in source.records if r.is_hpa)
    source = DashboardSource(catalog=source.catalog, records=hpa, locations=source.locations,
        bindings=tuple(GroupBinding(k, v) for k, v in source.bindings.items()), families=source.families,
        targets=source.targets, risks=source.risks, source_label=source.source_label)
    query = AppliedQuery(QuerySelection(), 1, 0, "전체", initial_targets(source))
    r = generate_report(source, ReportRequest("status", query, max(source.dates), min(source.dates), max(source.dates)))
    text = report_html(r)
    assert "HPA 제외 3대" in text and "계산 불가" in text
    assert next(s.table.rows[0] for s in r.sections if s.title == "조회 조건과 집계 기준") == (0, 0, 0, 0, 0)


def test_frozen_result_unchanged_when_source_or_ui_changes_and_recent_two(own, tmp_path):
    p = page(own, tmp_path)
    p.start_flow("status")
    first = generated(p)
    html = report_html(first)
    p.source.targets.clear()
    p.filename.setText("다른 파일")
    p.options["heatmap"].setChecked(False)
    assert report_html(first) == html and first.filename != "다른 파일.xlsx"
    p.start_flow("upgrade")
    second = generated(p)
    p.start_flow("change")
    third = generated(p)
    assert p.recent == [third, second]
    p.open_recent(1)
    assert p.result is second and p.result_filename.text() == second.filename


def test_item_selection_and_existing_cumulative_service_used():
    source = demo_dashboard_source()
    catalog = demo_version_catalog(source)
    item_id = catalog.items[0].item.item_id
    r = generate_report(source, request(source, kind="item", item_id=item_id), catalog)
    rows = next(s.table.rows for s in r.sections if s.title.startswith("Item 배포"))
    from setron.features.item.service import ItemCatalog
    from setron.features.item.schemas import APPLICATION_LABELS
    checker = ItemCatalog(catalog.families(), catalog.version_items)
    assert tuple(row[1] for row in rows) == tuple(APPLICATION_LABELS[checker.equipment_application(item_id, record).application]
        for record in source.records if record.base_date == max(source.analysis_dates) and not record.is_hpa)
    with pytest.raises(ValueError, match="Item"):
        generate_report(source, request(source, kind="item", item_id=999999), catalog)


def test_change_and_upgrade_use_real_observations_and_missing_risk_stays_unknown():
    source = demo_dashboard_source()
    r = generate_report(source, request(source, kind="change"))
    changes = next(s.table for s in r.sections if s.title == "설비별 변경 이력")
    assert changes.rows and all(date.fromisoformat(row[0]) in source.analysis_dates for row in changes.rows)
    r = generate_report(source, request(source, kind="upgrade"))
    upgrades = next(s.table for s in r.sections if s.title == "업그레이드 필요 설비")
    assert any(row[0] == "확인 필요" for row in upgrades.rows)


def test_ai_only_uses_explicitly_loaded_answer_or_numeric_mode(own, tmp_path):
    calls = []
    answer = ChatAnswer(ChatRequest("질문 <원문>", (Intent.SEARCH,)), "Site = 화성", "요약", date(2026, 10, 21))
    p = page(own, tmp_path, ai_provider=lambda: calls.append(1) or answer)
    p.start_flow("ai")
    p.generate()
    assert p.result is None and calls == []
    p.load_ai()
    assert len(calls) == 1
    r = generated(p)
    assert "질문 &lt;원문&gt;" in report_html(r)
    assert len(calls) == 1 and "일치하는지 확인 필요" in report_html(r)
    p.example_ai()
    r = generated(p)
    assert "회사 AI 결과가 아닙니다" in report_html(r)
    p.numeric_only.setChecked(True)
    r = generated(p)
    assert not any(s.title.startswith("AI 분석 ·") for s in r.sections)


def test_cancel_discards_late_worker_result_and_failure_retry(own, tmp_path, monkeypatch):
    started, release, finished = Event(), Event(), Event()
    real = generate_report
    def delayed(*args):
        started.set()
        release.wait(5)
        result = real(*args)
        finished.set()
        return result
    monkeypatch.setattr("setron.ui.reports_page.generate_report", delayed)
    p = page(own, tmp_path)
    p.start_flow("status")
    p.generate()
    until(started.is_set)
    assert p.generation_dialog.isVisible()
    p.generation_dialog.reject()
    release.set()
    until(finished.is_set)
    QTest.qWait(30)
    assert p.result is None and not p.request.busy
    monkeypatch.setattr("setron.ui.reports_page.generate_report", real)
    p.demo.setCurrentIndex(p.demo.findData("generation_failure"))
    p.generate()
    until(lambda: not p.request.busy)
    assert p.retry_button.isVisibleTo(p) and not p.save_button.isEnabled()
    p.retry_button.click()
    until(lambda: not p.request.busy)
    assert p.result is not None and p.save_button.isEnabled()


@pytest.mark.parametrize("fmt", ["xlsx", "docx", "pdf"])
def test_local_files_contain_same_frozen_conditions_counts_and_valid_format(own, tmp_path, fmt):
    source = demo_dashboard_source()
    r = generate_report(source, request(source, format=fmt, filename="보고서_합성검증"))
    path = tmp_path / r.filename
    result = save_generated_report(r, path)
    assert result.saved, result.message
    if fmt == "xlsx":
        with ZipFile(path) as zipped:
            assert any("chartEx" in name for name in zipped.namelist())
            assert any("charts/chart" in name for name in zipped.namelist())
        book = load_workbook(path)
        assert book.worksheets[0].cell(5, 1).value == source.snapshot(r.request.query).summary.equipment_count
        assert any("추이" in sheet.title and sheet._charts for sheet in book)
        book.close()
    elif fmt == "docx":
        doc = Document(path)
        text = " ".join(p.text for p in doc.paragraphs)
        assert "합성 예시" in " ".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
        assert "목표 N 달성률" in text
    else:
        pdf = QPdfDocument()
        assert pdf.load(str(path)) == QPdfDocument.Error.None_
        assert pdf.pageCount() > 0 and "리포트" in pdf.getAllText(0).text()
        pdf.close()


@pytest.mark.parametrize("fmt", ["docx", "pdf"])
def test_word_and_pdf_save_in_worker_with_preview_chart_images(own, tmp_path, fmt):
    p = page(own, tmp_path)
    p.start_flow("status")
    p.format.set_value(fmt)
    generated(p)
    p.open_save()
    dialog = p.save_dialog
    assert dialog.pictures
    dialog.folder.setText(str(tmp_path))
    dialog.save()
    deadline = monotonic() + 20  # Word의 긴 표·그림 직렬화는 기존 5초 UI 조회 제한과 구분한다.
    while dialog.busy and monotonic() < deadline:
        QTest.qWait(20)
    assert not dialog.busy
    path = tmp_path / p.result.filename
    assert path.is_file(), dialog.status.text()
    if fmt == "docx":
        with ZipFile(path) as zipped:
            assert any(name.startswith("word/media/") for name in zipped.namelist())
    else:
        assert path.read_bytes().startswith(b"%PDF-")


def test_unsaved_recent_report_is_available_from_landing(own, tmp_path):
    p = page(own, tmp_path)
    p.start_flow("status")
    first = generated(p)
    p.enter()
    assert p.library_model.rowCount() == 1
    assert p.library_model.rows[0][6] == "미리보기·저장"
    assert p.open_saved(0) and p.result is first
    assert p.tabs.currentIndex() == 1


def test_pdf_retains_every_distinct_chart_in_same_section(own, tmp_path):
    p = page(own, tmp_path)
    p.start_flow("status")
    r = generated(p)
    pictures = p.document.pictures()
    assert len(pictures) == 4  # 추이 + 구성비·막대·트리맵
    document = pdf_document(r, pictures)
    html = document.toHtml()
    for number, (index, _) in enumerate(pictures):
        assert f"report-chart:{index}/{number}" in html


def test_excel_literal_formula_and_long_text_are_preserved(own, tmp_path):
    source = demo_dashboard_source()
    r = generate_report(source, request(source))
    extra = ReportSection(2, "원문", "", Table("원문", ("내용",), (("=HYPERLINK(\"x\")",), ("긴" * 40000,))))
    r = replace(r, sections=(*r.sections, extra))
    result = save_generated_report(r, tmp_path / r.filename)
    assert result.saved, result.message
    book = load_workbook(result.path)
    sheet = next(s for s in book if s.title.endswith(" 원문"))
    assert sheet["A5"].data_type == "s" and sheet["A5"].value.startswith("=HYPERLINK")
    assert "긴값_분할" in book.sheetnames
    book.close()


def test_save_failure_preserves_old_file_and_dialog_preview_can_retry(own, tmp_path):
    source = demo_dashboard_source()
    r = generate_report(source, request(source, demo="save_failure"))
    path = tmp_path / r.filename
    path.write_bytes(b"previous")
    result = save_generated_report(r, path)
    assert not result.saved and path.read_bytes() == b"previous"
    path.unlink()
    dialog = own(ReportSaveDialog(r))
    dialog.folder.setText(str(tmp_path))
    dialog.save()
    until(lambda: not dialog.busy)
    assert dialog.retry_button.isVisibleTo(dialog) and not path.exists()
    dialog.report = replace(r, request=replace(r.request, demo=""))
    dialog.save()
    until(lambda: not dialog.busy)
    assert path.is_file()


def test_library_persists_actual_files_keeps_ai_entries_and_restores(own, tmp_path):
    p = page(own, tmp_path)
    fake_ai_path = tmp_path / "기존AI.docx"
    fake_ai_path.write_bytes(b"test")
    p.add((fake_ai_path,))
    assert p.ai_library.entries and p.library_model.rowCount() == 1
    r = generate_report(p.source, request(p.source))
    result = save_generated_report(r, tmp_path / r.filename)
    p._saved(result.path, r)
    assert p.library_model.rowCount() == 2
    another = page(own, tmp_path)
    assert another.library_model.rowCount() == 2
    result.path.unlink()
    another._render_library()
    assert any("파일 없음" in row for row in another.library_model.rows)


def test_app_reports_enabled_landing_on_menu_and_no_common_query_or_toolbar(own):
    window = own(create_window(chat_enabled=True))
    window.resize(1600, 1000)
    before = window.query_state.applied
    window.navigate("reports")
    p = window.pages["reports"]
    assert p.stack.currentIndex() == 0 and not window.query_panel.isVisibleTo(window)
    p.start_flow("status")
    assert window.header.title.text() == "보고서"
    p.tabs.setCurrentIndex(1)
    assert window.header.title.text() == "보고서 미리보기"
    window.navigate("dashboard")
    window.navigate("reports")
    assert p.stack.currentIndex() == 0 and window.query_state.applied == before
    assert not window.header.toolbar.isVisibleTo(window)
    until(lambda: not p.item_request.busy)


@pytest.mark.parametrize("value", ["../이름", "CON", "nul", "a:b", "a\\b", "", "a."])
def test_unsafe_filenames_rejected(value):
    with pytest.raises(ValueError):
        request(demo_dashboard_source(), filename=value)


def test_assets_have_original_size_and_ui_has_no_horizontal_overflow(own, tmp_path):
    p = page(own, tmp_path)
    for path in (ASSETS / "reports-ver5").glob("*.svg"):
        assert svg_size(f"reports-ver5/{path.name}").isValid()
    p.resize(1000, 1000)
    p.start_flow("status")
    QApplication.processEvents()
    assert p.tabs.minimumSizeHint().width() < 1000
    assert p.minimumSizeHint().width() <= 1000
    until(lambda: not p.item_request.busy)
