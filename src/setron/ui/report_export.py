"""생성 완료된 보고서만 로컬 파일로 저장한다. 조회·AI 호출·DB 수정은 하지 않는다."""

import os
from html import escape
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from docx import Document
from docx.shared import Inches
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.chart.series import DataPoint
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from setron.features.export.docx_fonts import use_korean_font
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.features.export.treemap import add_treemap
from setron.features.export.xlsx_cells import ReportTextWriter


def display_value(value):
    return "계산 불가" if value is None else f"{value:.1%}" if type(value) is float else str(value)


def report_html(report, tab=None):
    """미리보기와 PDF가 같은 섹션·수치·주의 문구를 사용한다."""
    esc = lambda x: escape(str(x)).replace("\n", "<br>")
    parts = ["<style>body{font-family:'IBM Plex Sans KR';font-size:10pt;color:#142033;}"
             "th{background:#f1f3f7;}td,th{padding:7px;}h1{font-size:20pt;}h2{font-size:14pt;}</style>",
             f"<h1>{esc(report.title)}</h1><p>생성 {report.created:%Y-%m-%d %H:%M} · {esc(report.scope)}</p>"]
    for section in report.sections:
        if tab is not None and section.tab != tab:
            continue
        parts.extend((f"<h2>{esc(section.title)}</h2>", f"<p>{esc(section.note)}</p>"))
        table = section.table
        if table is not None:
            parts.append("<table border='1' cellspacing='0' width='100%'><tr>" +
                         "".join(f"<th>{esc(c)}</th>" for c in table.columns) + "</tr>")
            parts.extend("<tr>" + "".join(f"<td>{esc(display_value(c))}</td>" for c in row) + "</tr>" for row in table.rows)
            parts.append("</table>" if table.rows else "</table><p>조건에 맞는 자료가 없습니다.</p>")
    return "".join(parts)


def save_generated_report(report, path, pictures=()):
    """완성 후 원자적으로 교체한다. 실패·취소 시 기존 파일과 보고서는 유지한다."""
    path, temporary, book = Path(path), None, None
    if path.suffix.lower() != "." + report.request.format:
        return ExportResult(ExportStatus.FAILED, path, "생성한 보고서와 저장 파일의 형식이 다릅니다.")
    try:
        if report.request.demo == "save_failure":
            raise OSError("UI 시연 · 저장 실패")
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, suffix=path.suffix, delete=False) as handle:
            temporary = Path(handle.name)
        if report.request.format == "xlsx":
            book, tree_rows = _workbook(report)
            book.save(temporary)
            if tree_rows:
                add_treemap(temporary, "트리맵", tree_rows, title="계열·상태별 구성 (설비·계열 건)",
                            colors={"N 이상": "3C5CE9", "N 미만": "293B66", "판단 불가": "9CA3AF"})
        elif report.request.format == "docx":
            doc = Document()
            use_korean_font(doc)
            doc.add_heading(report.title, 0)
            doc.add_paragraph(f"생성 {report.created:%Y-%m-%d %H:%M} · {report.scope}")
            for index, section in enumerate(report.sections):
                doc.add_heading(section.title, 1)
                doc.add_paragraph(section.note)
                for section_index, png in pictures:
                    if section_index == index:
                        doc.add_picture(BytesIO(png), width=Inches(6.0))
                if section.table is not None:
                    table = doc.add_table(rows=1, cols=len(section.table.columns))
                    table.style = "Table Grid"
                    for cell, value in zip(table.rows[0].cells, section.table.columns):
                        cell.text = value
                    for row in section.table.rows:
                        for cell, value in zip(table.add_row().cells, row):
                            cell.text = display_value(value)
            doc.save(temporary)
        else:
            _pdf(report, temporary, pictures)
        os.replace(temporary, path)
        return ExportResult(ExportStatus.SAVED, path, "생성 당시 조건·내용으로 저장했습니다.")
    except Exception as error:
        return ExportResult(ExportStatus.FAILED, path, f"저장하지 못했습니다. 기존 파일은 유지했습니다. {error}")
    finally:
        if book is not None:
            book.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _workbook(report):
    book = Workbook()
    book.remove(book.active)
    text = ReportTextWriter(book)
    tree_rows = ()
    for index, section in enumerate(report.sections, 1):
        title = "".join(c for c in section.title if c not in '[]:*?/\\')[:25]
        sheet = book.create_sheet(f"{index:02d} {title}"[:31])
        text.write(sheet.cell(1, 1), section.title, "제목")
        text.write(sheet.cell(2, 1), section.note, "안내")
        sheet.cell(1, 1).font = Font(name="맑은 고딕", bold=True, size=14)
        sheet.freeze_panes = "A5"
        sheet.sheet_view.showGridLines = False
        table = section.table
        if table is None:
            sheet.column_dimensions["A"].width = 100
            continue
        for r, row in enumerate((table.columns, *table.rows), 4):
            for c, value in enumerate(row, 1):
                text.write(sheet.cell(r, c), value, str(table.columns[c - 1]))
                if r == 4:
                    sheet.cell(r, c).font = Font(name="맑은 고딕", bold=True)
                    sheet.cell(r, c).fill = PatternFill("solid", fgColor="E8EBFC")
                if section.chart == "trend" and c == 5 and r > 4:
                    sheet.cell(r, c).number_format = "0.0%"
                sheet.column_dimensions[get_column_letter(c)].width = 25 if c != len(row) else 45
        if table.rows:
            sheet.auto_filter.ref = f"A4:{get_column_letter(len(table.columns))}{len(table.rows) + 4}"
        if section.chart == "distribution" and table.rows:
            chart = BarChart()
            chart.type, chart.grouping, chart.overlap = "col", "stacked", 100
            chart.title = "계열별 구성 (설비·계열 건)"
            chart.add_data(Reference(sheet, min_col=2, max_col=4, min_row=4, max_row=4 + len(table.rows)), titles_from_data=True)
            chart.set_categories(Reference(sheet, min_col=1, min_row=5, max_row=4 + len(table.rows)))
            for series, color in zip(chart.series, ("3C5CE9", "293B66", "9CA3AF")):
                series.graphicalProperties.solidFill = color
                series.graphicalProperties.line.solidFill = color
            sheet.add_chart(chart, "G4")
            composition = book.create_sheet("상태 구성비")
            composition.append(("판정", "설비·계열 건수"))
            for i, name in enumerate(table.columns[1:], 1):
                composition.append((name, sum(row[i] for row in table.rows)))
            pie = PieChart()
            pie.title = "상태 구성비"
            pie.add_data(Reference(composition, min_col=2, min_row=1, max_row=4), titles_from_data=True)
            pie.set_categories(Reference(composition, min_col=1, min_row=2, max_row=4))
            for i, color in enumerate(("3C5CE9", "293B66", "9CA3AF")):
                point = DataPoint(idx=i)
                point.graphicalProperties.solidFill = color
                pie.series[0].data_points.append(point)
            composition.add_chart(pie, "D2")
            tree_rows = tuple((str(row[0]), name, row[i]) for row in table.rows
                              for i, name in enumerate(table.columns[1:], 1) if row[i])
            if tree_rows:
                tree = book.create_sheet("트리맵")
                for r, row in enumerate((("SW 계열", "판정", "건수"), *tree_rows), 1):
                    for c, value in enumerate(row, 1):
                        text.write(tree.cell(r, c), value, ("SW 계열", "판정", "건수")[c - 1])
        if section.chart == "trend" and table.rows:
            chart = LineChart()
            chart.title, chart.display_blanks = "관측 날짜별 적용률", "gap"
            chart.y_axis.numFmt = "0%"
            chart.y_axis.scaling.min, chart.y_axis.scaling.max = 0, 1
            chart.add_data(Reference(sheet, min_col=5, min_row=4, max_row=4 + len(table.rows)), titles_from_data=True)
            chart.set_categories(Reference(sheet, min_col=1, min_row=5, max_row=4 + len(table.rows)))
            sheet.add_chart(chart, "G4")
    text.finish()
    return book, tree_rows


def pdf_document(report, pictures=()):
    """같은 섹션의 막대·구성비·트리맵도 서로 다른 이미지 자원으로 보존한다."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QImage, QTextDocument
    document = QTextDocument()
    html = report_html(report)
    figures = {}
    for number, (index, png) in enumerate(pictures):
        image = QImage.fromData(png)
        if image.isNull():
            raise ValueError("미리보기 그래프를 읽지 못했습니다. 다시 생성해 주세요.")
        url = QUrl(f"report-chart:{index}/{number}")
        document.addResource(QTextDocument.ResourceType.ImageResource, url, image)
        figures.setdefault(index, []).append(
            f"<p><img src='{url.toString()}' width='600' height='{int(image.height() * 600 / image.width())}'></p>")
    for index, images in figures.items():
        section = report.sections[index]
        heading = f"<h2>{escape(section.title)}</h2>"
        html = html.replace(heading, heading + "".join(images), 1)
    document.setHtml(html)
    return document


def _pdf(report, path, pictures):
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter
    document = pdf_document(report, pictures)
    writer = QPdfWriter(str(path))
    writer.setTitle(report.title)
    writer.setCreator("SETRON")
    writer.setResolution(96)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(16, 16, 16, 16), QPageLayout.Unit.Millimeter)
    document.print_(writer)
    del writer
    if not path.read_bytes().startswith(b"%PDF-"):
        raise OSError("PDF 생성 결과가 올바르지 않습니다.")
