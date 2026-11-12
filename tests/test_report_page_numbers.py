"""Word·PDF·인쇄의 여러 페이지 번호와 본문 보존을 실제 출력 파일로 확인한다."""

from dataclasses import replace
from datetime import date, datetime

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from PySide6.QtGui import QPageRanges
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

from setron.features.chat.report import ChatReport, ReportPart, build_chat_report
from setron.features.chat.schemas import ChatAnswer, ChatRequest, Intent, Table
from setron.ui.chat_report_preview import report_document, save_report_pdf
from setron.ui.query_state import AppliedQuery, QuerySelection
from setron.ui.report_data import GeneratedReport, ReportRequest, ReportSection
from setron.ui.report_export import pdf_document, save_generated_report
from setron.ui.report_pagination import configure_report_device, print_numbered_document


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    from setron.ui.theme import install_theme
    install_theme(app)
    return app


def report_pair():
    table = Table("페이지 검증 표", ("행", "내용"), tuple((f"ROW{i:04}", "원문 내용 보존") for i in range(130)))
    day = date(2026, 11, 12)
    report = GeneratedReport(ReportRequest("status", AppliedQuery(QuerySelection(), 1, 0, "전체"), day, day, day),
                             datetime(2026, 11, 12), "페이지 번호 검증", (ReportSection(0, "검증", table=table),), "전체", "검증 자료")
    answer = ChatAnswer(ChatRequest("페이지 검증", (Intent.SEARCH,)), "전체", "검증 요약", day, tables=(table,))
    return report, ChatReport("AI 페이지 번호 검증", (ReportPart(answer),))


def assert_pdf_pages(path, *, numbers=None):
    pdf = QPdfDocument()
    assert pdf.load(str(path)) == QPdfDocument.Error.None_
    try:
        numbers = numbers or list(range(1, pdf.pageCount() + 1))
        assert pdf.pageCount() == len(numbers)
        texts = []
        for index, page in enumerate(numbers):
            text = pdf.getAllText(index).text()
            assert text.rstrip().endswith(str(page))
            selection = pdf.getSelectionAtIndex(index, text.rfind(str(page)), len(str(page)))
            bounds = selection.boundingRectangle()
            size = pdf.pagePointSize(index)
            assert bounds.left() > size.width() * .85 and bounds.top() > size.height() * .94
            assert bounds.right() < size.width() and bounds.bottom() < size.height()
            texts.append(text)
        return texts
    finally:
        pdf.close()


@pytest.mark.parametrize("ai", [False, True])
def test_pdf_has_bottom_right_number_on_every_page_without_losing_rows(qapp, tmp_path, ai):
    report, chat = report_pair()
    path = tmp_path / "numbered.pdf"
    result = save_report_pdf(chat, path) if ai else save_generated_report(replace(report, request=replace(report.request, format="pdf")), path, ())
    assert result.saved, result.message
    texts = assert_pdf_pages(path)
    assert len(texts) > 1
    content = "".join("".join(text.split()) for text in texts)
    for i in range(130):
        assert content.count(f"ROW{i:04}") == 1


def test_word_uses_dynamic_page_field_in_right_aligned_footer(tmp_path):
    report, chat = report_pair()
    path = tmp_path / "numbered.docx"
    assert save_generated_report(report, path, ()).saved
    for document in (Document(path), build_chat_report(chat)):
        section = document.sections[0]
        paragraph = section.footer.paragraphs[0]
        assert paragraph.alignment == WD_ALIGN_PARAGRAPH.RIGHT
        assert [field.get(qn("w:instr")) for field in paragraph._p.findall(qn("w:fldSimple"))] == ["PAGE"]
        assert document.settings.element.find(qn("w:updateFields")).get(qn("w:val")) == "true"
        assert not section.different_first_page_header_footer


@pytest.mark.parametrize("resolution", [96, 1200])
def test_print_range_preserves_page_number_and_matches_pdf_layout(qapp, tmp_path, resolution):
    report, _ = report_pair()
    path = tmp_path / "printed.pdf"
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(path))
    printer.setResolution(resolution)
    configure_report_device(printer)
    ranges = QPageRanges()
    ranges.addPage(2)
    printer.setPageRanges(ranges)
    document = pdf_document(report, ())
    print_numbered_document(document, printer)
    assert_pdf_pages(path, numbers=[2])
