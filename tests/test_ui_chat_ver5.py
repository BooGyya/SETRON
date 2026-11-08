"""VER5 AI 분석 화면 계약. 운영 DB·외부 AI·실제 프린터를 사용하지 않는다."""
from dataclasses import replace
from pathlib import Path
import ast
import hashlib
import threading

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtPdf import QPdfDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLabel, QStyleOptionViewItem

from setron.features.chat.report import ChatReport, ReportPart
from setron.features.chat.schemas import BarChart, Table
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.ui.chat_components import SavedFileToast, VerdictDelegate
from setron.ui.chat_export import ExcelExportDialog, ReportDialog, chart_png
from setron.ui.chat_page import AnswerCard, ChatPage, PendingCard, bar_chart
from setron.ui.chat_report_library import ChatReportLibrary
from setron.ui.chat_report_preview import PdfPreviewDialog, report_document, save_report_pdf
from setron.ui.theme import ASSETS, install_theme, svg_size
from test_ui_chat import EngineService, wait


@pytest.fixture
def ui():
    app = QApplication.instance() or QApplication([])
    install_theme(app)
    widgets = []

    def own(widget):
        widgets.append(widget)
        widget.show()
        QTest.qWait(10)
        return widget
    yield own
    for widget in reversed(widgets):
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def sample():
    return EngineService().ask("목표 N 달성률이 가장 낮은 원본 구역을 분석해줘")


def part(answer=None):
    answer = answer or sample()
    images = []
    for chart in answer.charts:
        view = ChatPage._chart(chart)
        images.append(chart_png(view, (900, 420)))
        view.deleteLater()
    return ReportPart(answer, tuple(images))


def test_initial_screen_classes_are_unchanged():
    """기준 develop 37feecd의 초기 화면 AST를 보호한다. 얕은 클론에서도 실행 가능하다."""
    root = Path(__file__).parents[1]
    after = (root / "src/setron/ui/chat_page.py").read_text(encoding="utf-8")

    def protected(source):
        tree = ast.parse(source)
        values = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in ("StarterCard", "EmptyState")]
        page = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ChatPage")
        values.append(next(node for node in page.body if isinstance(node, ast.FunctionDef) and node.name == "_starters"))
        return {node.name: hashlib.sha256(ast.dump(node).encode()).hexdigest() for node in values}
    assert protected(after) == {
        "StarterCard": "2e10b38a37aca432e7fe3374389e152e629ab2fa1c2903050e1ddbd3e0f7e6ca",
        "EmptyState": "c4dcfb2dc13ecac3b7c950e05edf2215ee2f104f85c36d0f23fa973500aa81c4",
        "_starters": "e97f5f795354c7cc9e6f3dbcfab7274df805a394d5e884003448fe2112a561bb",
    }


def test_answer_exposes_contract_conditions_cause_charts_and_notices(ui):
    answer = replace(sample(), cause_is_ai=True)
    card = ui(AnswerCard(answer, lambda _: None, on_report=lambda _: None))
    # 조건 줄은 답 묶음에 그대로 두고 화면에서는 숨긴다. 원인 분석 출처 꼬리표도 두지 않는다 (11/08 팀 결정).
    assert card.conditions.text() == answer.understood and not card.conditions.isVisibleTo(card)
    assert len(card.charts) == len(answer.charts) == 2
    texts = [w.text() for w in card.findChildren(QLabel)]
    assert "AI 설명" not in texts and "자료 근거" not in texts
    assert all(notice in texts for notice in answer.notices)
    assert all(c.note in texts for c in answer.charts if hasattr(c, "note") and c.note)
    assert all(isinstance(t.itemDelegate(), VerdictDelegate) for t in card.tables)
    for widget, table in zip(card.tables, answer.tables):
        for index, tone in enumerate(table.tones):
            if tone in VerdictDelegate.COLORS:
                assert widget.item(index, widget.columnCount() - 1).data(Qt.ItemDataRole.UserRole) == tone


def test_real_empty_and_unrecognized_are_distinct_and_navigation_is_explicit(ui):
    source = sample()
    empty = replace(source, tables=(Table("조회 결과", ("설비",), ()),), charts=(), equipment_ids=(), cause=None, report=None, issues=())
    routes = []
    card = ui(AnswerCard(empty, lambda _: None, on_navigate=routes.append))
    assert any("자료 없음" in w.text() for w in card.findChildren(QLabel))
    assert not routes
    button = next(w for w in card.findChildren(__import__('PySide6.QtWidgets', fromlist=['QPushButton']).QPushButton) if w.text() == "데이터 가져오기")
    button.click()
    assert routes == ["import"]
    bad = ui(AnswerCard(EngineService().ask("안녕"), lambda _: None))
    assert any("조건 인식 실패" in w.text() for w in bad.findChildren(QLabel))
    assert not any("자료 없음" in w.text() for w in bad.findChildren(QLabel))


def test_pending_does_not_fake_progress_and_keeps_draft(ui):
    gate = threading.Event()
    page = ui(ChatPage(EngineService(gate)))
    page.send("평택 O2 현황 보여줘")
    assert page.input.isEnabled() and not page.send_button.isEnabled()
    page.input.setText("다음 질문 초안")
    QTest.qWait(100)
    assert all("✓" not in step.text() for step in page.pending.steps)
    assert not page.send()
    page.stop()
    gate.set()
    QTest.qWait(100)
    assert page.input.text() == "다음 질문 초안" and not page.cards


def test_changed_data_disables_exports_instead_of_mixing_snapshots(ui):
    page = ui(ChatPage(EngineService()))
    page.send("평택 O2 중 목표 N 미만인 설비만 보여줘")
    wait(page)
    card = page.cards[0]
    page.invalidate_data()
    assert not card.export_button.isEnabled() and card.is_stale
    assert page._export(card) is None


def test_error_is_sanitized_and_retry_remembers_question(ui):
    class Failed(EngineService):
        def ask(self, question, previous=None):
            raise RuntimeError("secret-token-should-not-leak")
    page = ui(ChatPage(Failed()))
    page.send("평택 O2 현황 보여줘")
    wait(page)
    assert "secret-token" not in page.status.text()
    assert page.retry_button.isVisible() and page.last_question == "평택 O2 현황 보여줘"


@pytest.mark.parametrize("format,suffixes", [("word", {".docx"}), ("pdf", {".pdf"}), ("both", {".docx", ".pdf"})])
def test_report_formats_save_real_files_and_selected_sections(ui, tmp_path, format, suffixes):
    dialog = ui(ReportDialog([part()], format=format))
    dialog.files.folder.setText(str(tmp_path))
    dialog.files.name.setText("선택_보고서.docx")
    dialog.chart_options[0, 0].setChecked(False)
    dialog.options["cause"].setChecked(False)
    selected = dialog.report()
    assert len(selected.parts[0].charts) == 1 and not selected.options.cause
    assert dialog.save() and {p.suffix for p in dialog.saved_paths} == suffixes
    for path in dialog.saved_paths:
        assert path.stat().st_size > 100
        assert path.name.count(".") == 1
        if path.suffix == ".pdf":
            pdf = QPdfDocument()
            assert pdf.load(str(path)) == QPdfDocument.Error.None_
            text = " ".join(pdf.getAllText(index).text() for index in range(pdf.pageCount()))
            assert "목표" in text and "원인 분석 · 자료 근거" not in text
            pdf.close()


def test_pdf_preview_has_real_pages_navigation_and_cancelled_download(ui, tmp_path, monkeypatch):
    dialog = ui(PdfPreviewDialog(ChatReport("PDF 검사", (part(),))))
    assert dialog.available and dialog.pages.count() == dialog.pdf.pageCount() >= 2
    dialog.pages.setCurrentRow(1)
    QTest.qWait(20)
    assert dialog.view.pageNavigator().currentPage() == 1
    assert dialog.position.text().startswith("2 /")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: ("", ""))
    dialog.download()
    assert dialog.result is None
    path = tmp_path / "preview.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(path), "PDF (*.pdf)"))
    dialog.download()
    assert dialog.result.saved and path.is_file()
    assert not Path(dialog._temporary.name).exists()      # Windows 잠금 없이 임시 파일 회수


def test_pdf_print_is_opt_in_and_cancel_does_not_print(ui, monkeypatch):
    import setron.ui.chat_report_preview as module
    dialog = ui(PdfPreviewDialog(ChatReport("인쇄 검사", (part(),))))
    monkeypatch.setattr(module.QPrintDialog, "exec", lambda _: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(module, "report_document", lambda _: pytest.fail("취소 시 인쇄하면 안 됩니다"))
    dialog.print_report()


def test_save_failure_never_claims_success_or_overwrites(ui, tmp_path, monkeypatch):
    import setron.ui.chat_export as module
    dialog = ui(ReportDialog([part()], format="pdf"))
    dialog.files.folder.setText(str(tmp_path))
    path = dialog.files.path(".pdf")
    path.write_bytes(b"existing")
    dialog.confirm_overwrite = lambda _: False
    assert not dialog.save() and path.read_bytes() == b"existing"
    dialog.confirm_overwrite = lambda _: True
    monkeypatch.setattr(module, "save_report_pdf", lambda report, path: ExportResult(ExportStatus.FAILED, path, "저장 권한 없음"))
    assert not dialog.save() and dialog.result.status == ExportStatus.FAILED
    assert dialog.saved_paths == () and "저장 권한" in dialog.message.text()


def test_excel_no_columns_does_not_save(ui, tmp_path):
    service = EngineService()
    answer = service.ask("평택 O2 중 목표 N 미만인 설비만 보여줘")
    dialog = ui(ExcelExportDialog(service.export_data(answer)))
    dialog.files.folder.setText(str(tmp_path))
    for box in dialog.columns.values():
        box.setChecked(False)
    assert not dialog.save() and not list(tmp_path.iterdir())


def test_saved_report_library_only_adds_existing_reports_and_persists(ui, tmp_path):
    settings = QSettings(str(tmp_path / "reports.ini"), QSettings.Format.IniFormat)
    page = ui(ChatReportLibrary(settings=settings))
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-qa")
    page.add((pdf, tmp_path / "missing.docx"))
    assert page.table.rowCount() == 1
    again = ui(ChatReportLibrary(settings=settings))
    assert again.table.rowCount() == 1
    pdf.unlink()
    again.refresh()
    assert again.table.item(0, 3).text() == "파일 없음"


def test_report_success_emits_actual_paths_and_shows_toast(ui, tmp_path):
    page = ui(ChatPage(EngineService()))
    saved = []
    page.report_saved.connect(saved.append)
    page.send("목표 N 달성률이 가장 낮은 원본 구역을 분석해줘")
    wait(page)
    def run(dialog):
        dialog.files.folder.setText(str(tmp_path))
        return dialog.save()
    page.open_dialog = run
    page._report(page.cards[0])
    assert len(saved) == 1 and all(p.is_file() for p in saved[0])
    assert isinstance(page.saved_toast, SavedFileToast)
    assert page.saved_toast.isVisible()


def test_narrow_answer_charts_stack_and_native_dialog_footer_stays_visible(ui):
    card = ui(AnswerCard(sample(), lambda _: None))
    card.resize(650, 1500)
    QTest.qWait(100)
    assert card.charts[1].mapTo(card, card.charts[1].rect().topLeft()).y() > card.charts[0].mapTo(card, card.charts[0].rect().topLeft()).y()
    dialog = ui(ReportDialog([part()]))
    dialog.resize(520, 450)
    QTest.qWait(30)
    assert dialog.save_button.isVisible()
    assert dialog.save_button.geometry().bottom() < dialog.height()
    assert dialog.format_buttons["pdf"].isVisible()


def test_original_assets_are_nonempty_and_report_pdf_uses_local_renderer():
    sizes = {"297a6.svg": 20, "c36ff.svg": 20, "87080.svg": 20, "d5f0a.svg": 24,
             "62b26.svg": 22, "e6e51.svg": 18, "b0efa.svg": 20, "330d5.svg": 14,
             "eacd9.svg": 18, "f0598.svg": 18}
    for name, width in sizes.items():
        assert (ASSETS / "ai-ver5" / name).stat().st_size > 0
        assert svg_size(f"ai-ver5/{name}").width() == width


def test_small_app_keeps_input_and_send_button_in_view_and_reports_route(ui, tmp_path, monkeypatch):
    import setron.ui.chat_report_library as library
    from setron.ui.app import create_window
    original = library.ChatReportLibrary
    settings = QSettings(str(tmp_path / "app-reports.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(library, "ChatReportLibrary", lambda: original(settings=settings))
    window = ui(create_window(chat_enabled=True))
    window.resize(1000, 740)
    window.navigate("ai")
    chat = window.pages["ai"]
    chat.send("목표 N 달성률이 가장 낮은 원본 구역을 분석해줘")
    wait(chat, 10)
    QTest.qWait(100)
    for widget in (chat.input, chat.send_button):
        assert widget.mapTo(window, QPoint(0, widget.height())).y() < window.height() - 5
    assert window.navigate("reports") and window.nav["reports"].isEnabled()
    assert not window.notice.isVisible()
    assert window.navigate("ai") and chat.cards
    assert chat.new_conversation(ask=False)
    QTest.qWait(20)
    assert chat.empty.isVisible() and chat.maximumHeight() == 16777215


def test_native_selection_keyboard_and_previous_question_content(ui):
    source = EngineService()
    current = source.ask("평택 O2 중 목표 N 미만인 설비만 보여줘")
    dialog = ui(ReportDialog([ReportPart(current)], previous=[part()]))
    assert not dialog.options["charts"].isEnabled()
    dialog.include_previous.setFocus()
    QTest.keyClick(dialog.include_previous, Qt.Key.Key_Space)
    assert dialog.include_previous.isChecked() and dialog.options["charts"].isEnabled()
    assert len(dialog.report().parts) == 2
    dialog.format_buttons["pdf"].setFocus()
    QTest.keyClick(dialog.format_buttons["pdf"], Qt.Key.Key_Space)
    assert dialog.format_buttons["pdf"].isChecked() and not dialog.format_buttons["word"].isChecked()


def test_report_partial_save_is_not_reported_as_complete(ui, tmp_path, monkeypatch):
    import setron.ui.chat_export as module
    dialog = ui(ReportDialog([part()], format="both"))
    dialog.files.folder.setText(str(tmp_path))
    monkeypatch.setattr(module, "save_report_pdf", lambda report, path: ExportResult(ExportStatus.FAILED, path, "PDF 저장 실패"))
    assert not dialog.save()
    assert dialog.result.status == ExportStatus.FAILED
    assert len(dialog.saved_paths) == 1 and dialog.saved_paths[0].suffix == ".docx"
    assert dialog.saved_paths[0].is_file() and "일부 파일만 저장됨" in dialog.message.text()


def test_chart_highlight_and_projected_line_are_driven_by_contract(ui):
    from datetime import date
    from setron.features.chat.schemas import LineChart, LinePoint
    from setron.ui.chat_page import line_chart
    chart = ui(bar_chart(BarChart("강조", ("A", "B", "C"), (25, 50, None), (0,))))
    normal, highlighted = chart.chart().series()[0].barSets()
    assert highlighted.at(0) == 25 and normal.at(0) == 0 and normal.at(1) == 50
    assert not chart.chart().series()[0].isLabelsVisible()    # None을 0% 숫자로 표시하지 않는다.
    line = ui(line_chart(LineChart("실적·예측", (("노선", (
        LinePoint(date(2026, 9, 1), 30), LinePoint(date(2026, 10, 1), 50),
        LinePoint(date(2026, 11, 1), 80, projected=True),
    )),), "예측은 참고용")))
    actual, projected = line.chart().series()
    assert actual.pen().style() == Qt.PenStyle.SolidLine
    assert projected.pen().style() == Qt.PenStyle.DashLine
    assert projected.count() == 2 and projected.at(1).y() == 80


@pytest.mark.parametrize("cancel", ["stop", "invalidate_data", "new_conversation"])
def test_report_preparation_runs_off_ui_and_discards_cancelled_result(ui, cancel):
    gate, started = threading.Event(), threading.Event()
    threads, opened = [], []
    class SlowReport(EngineService):
        def report_parts(self, result):
            threads.append(threading.get_ident())
            started.set()
            gate.wait(3)
            return super().report_parts(result)
    page = ui(ChatPage(SlowReport()))
    page.send("이번 달 표준화 보고서 만들어줘")
    wait(page)
    page.open_dialog = lambda dialog: opened.append(dialog) or False
    card = page.cards[0]
    card.report_button.click()
    assert page.busy and not card.report_button.isEnabled()
    for _ in range(100):
        if started.is_set():
            break
        QTest.qWait(10)
    assert started.is_set() and threads == [threads[0]] and threads[0] != threading.get_ident()
    page.input.setText("다음 질문 초안")
    assert page.input.isEnabled() and not page.send()
    if cancel == "new_conversation":
        page.new_conversation(ask=False)
    else:
        getattr(page, cancel)()
    gate.set()
    QTest.qWait(150)
    assert not page.busy and not opened
    if cancel != "new_conversation":
        assert page.input.text() == "다음 질문 초안"


def test_prepared_report_is_reused_without_another_ai_call(ui, tmp_path):
    class Counted(EngineService):
        count = 0
        def report_parts(self, result):
            self.count += 1
            return super().report_parts(result)
    service = Counted()
    page = ui(ChatPage(service))
    page.send("이번 달 표준화 보고서 만들어줘")
    wait(page)
    def save(dialog):
        dialog.files.folder.setText(str(tmp_path))
        return dialog.save()
    page.open_dialog = save
    card = page.cards[0]
    card.report_button.click()
    wait(page)
    assert service.count == 1 and card.report_button.text() == "다운로드됨"
    page.open_dialog = lambda dialog: False
    page._report(card)
    assert service.count == 1 and not page.busy
