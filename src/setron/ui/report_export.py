"""고정된 보고서를 Word·PDF로 저장한다. 두 형식은 같은 그래프·히트맵 PNG를 사용한다."""

import os
from html import escape
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor

from setron.features.export.docx_fonts import use_korean_font
from setron.features.export.docx_pagination import add_page_numbers
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.ui.report_pagination import configure_report_device, configure_report_document, print_numbered_document


def display_value(value):
    return "계산 불가" if value is None else f"{value:.1%}" if type(value) is float else str(value)


def _show_table(section):
    return section.table is not None and section.chart not in {"line_trend", "heatmap"}


def report_html(report, tab=None):
    """미리보기·PDF의 텍스트와 수치표. 시각화는 pdf_document에서 같은 위치에 넣는다."""
    esc = lambda x: escape(str(x)).replace("\n", "<br>")
    parts = ["<style>body{font-family:'IBM Plex Sans KR';font-size:10pt;color:#142033;}"
             "th{background:#eef1f5;}td,th{padding:6px;}h1{font-size:20pt;color:#000;}"
             "h2{font-size:13pt;color:#000;}</style>",
             f"<h1>{esc(report.title)}</h1><p>생성 {report.created:%Y-%m-%d %H:%M} · {esc(report.scope)}</p>"]
    for section in report.sections:
        if tab is not None and section.tab != tab:
            continue
        parts.extend((f"<h2>{esc(section.title)}</h2>", f"<p>{esc(section.note)}</p>"))
        if _show_table(section):
            table = section.table
            parts.append("<table border='1' cellspacing='0' width='100%'><thead><tr>" +
                         "".join(f"<th>{esc(c)}</th>" for c in table.columns) + "</tr></thead>")
            parts.extend("<tr>" + "".join(f"<td>{esc(display_value(c))}</td>" for c in row) + "</tr>"
                         for row in table.rows)
            parts.append("</table>" if table.rows else "</table><p>조건에 맞는 자료가 없습니다.</p>")
    return "".join(parts)


def _pictures(report, pictures):
    if pictures is None:
        from setron.ui.report_widgets import report_pictures
        return report_pictures(report)
    return pictures


def save_generated_report(report, path, pictures=None):
    """완성 후 원자적으로 교체한다. 실패하면 기존 파일은 보존한다."""
    path, temporary = Path(path), None
    if report.request.format not in {"docx", "pdf"}:
        return ExportResult(ExportStatus.FAILED, path, "보고서는 Word·PDF 형식으로 저장해 주세요.")
    if path.suffix.lower() != "." + report.request.format:
        return ExportResult(ExportStatus.FAILED, path, "생성한 보고서와 저장 파일의 형식이 다릅니다.")
    try:
        if report.request.demo == "save_failure":
            raise OSError("UI 시연 · 저장 실패")
        pictures = _pictures(report, pictures)
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, suffix=path.suffix, delete=False) as handle:
            temporary = Path(handle.name)
        if report.request.format == "docx":
            _word(report, temporary, pictures)
        else:
            _pdf(report, temporary, pictures)
        os.replace(temporary, path)
        return ExportResult(ExportStatus.SAVED, path, "생성 당시 조건·내용으로 저장했습니다.")
    except Exception as error:
        return ExportResult(ExportStatus.FAILED, path, f"저장하지 못했습니다. 기존 파일은 유지했습니다. {error}")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _word(report, path, pictures):
    document = Document()
    use_korean_font(document)
    page = document.sections[0]
    page.page_width, page.page_height = Mm(210), Mm(297)
    page.top_margin = page.bottom_margin = page.left_margin = page.right_margin = Mm(16)
    add_page_numbers(document)
    normal = document.styles["Normal"]
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.12
    for name, size in (("Title", 22), ("Heading 1", 13)):
        document.styles[name].font.color.rgb = RGBColor(0, 0, 0)
        document.styles[name].font.size = Pt(size)
        properties = document.styles[name].element.get_or_add_pPr()
        for border in properties.findall(qn("w:pBdr")):
            properties.remove(border)
    document.add_heading(report.title, 0)
    document.add_paragraph(f"생성 {report.created:%Y-%m-%d %H:%M} · {report.scope}")
    for index, section in enumerate(report.sections):
        document.add_heading(section.title, 1)
        note = document.add_paragraph(section.note)
        images = [png for section_index, png in pictures if section_index == index]
        note.paragraph_format.keep_with_next = bool(images)
        for png in images:
            picture = document.add_picture(BytesIO(png), width=Inches(6.7))
            picture._inline.docPr.set("descr", section.title)
        if _show_table(section):
            table = document.add_table(rows=1, cols=len(section.table.columns))
            table.style = "Table Grid"
            repeat = OxmlElement("w:tblHeader")
            table.rows[0]._tr.get_or_add_trPr().append(repeat)
            for cell, value in zip(table.rows[0].cells, section.table.columns):
                cell.text = value
                shade = OxmlElement("w:shd")
                shade.set(qn("w:fill"), "EEF1F5")
                cell._tc.get_or_add_tcPr().append(shade)
            for row in section.table.rows:
                for cell, value in zip(table.add_row().cells, row):
                    cell.text = display_value(value)
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        paragraph.paragraph_format.space_after = Pt(3)
                        for run in paragraph.runs:
                            run.font.size = Pt(9)
    document.save(path)


def pdf_document(report, pictures=None):
    """미리보기·인쇄·다운로드가 같은 이미지와 숫자를 사용한다."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QImage, QTextDocument
    document = QTextDocument()
    configure_report_document(document)
    html = report_html(report)
    figures = {}
    for number, (index, png) in enumerate(_pictures(report, pictures)):
        image = QImage.fromData(png)
        if image.isNull():
            raise ValueError("미리보기 그래프를 읽지 못했습니다. 다시 생성해 주세요.")
        url = QUrl(f"report-chart:{index}/{number}")
        document.addResource(QTextDocument.ResourceType.ImageResource, url, image)
        figures.setdefault(index, []).append(
            f"<p><img src='{url.toString()}' width='640' height='{int(image.height() * 640 / image.width())}'></p>")
    for index, images in figures.items():
        section = report.sections[index]
        note = escape(section.note).replace("\n", "<br>")
        anchor = f"<h2>{escape(section.title)}</h2><p>{note}</p>"
        # 큰 그림의 제목·설명이 앞 페이지에 남지 않도록 시각화 절은 새 페이지에서 시작한다.
        heading = anchor.replace("<h2>", "<h2 style='page-break-before:always'>", 1)
        html = html.replace(anchor, heading + "".join(images), 1)
    document.setHtml(html)
    return document


def _pdf(report, path, pictures):
    from PySide6.QtGui import QPdfWriter
    document = pdf_document(report, pictures)
    writer = QPdfWriter(str(path))
    writer.setTitle(report.title)
    writer.setCreator("SETRON")
    writer.setResolution(96)
    configure_report_device(writer)
    print_numbered_document(document, writer)
    del writer
    if not path.read_bytes().startswith(b"%PDF-"):
        raise OSError("PDF 생성 결과가 올바르지 않습니다.")
