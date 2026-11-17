"""AI 답변과 생성된 보고서의 로컬 미리보기·인쇄."""
from __future__ import annotations

import os
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt, QUrl
from PySide6.QtGui import QImage, QPdfWriter, QTextDocument
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout

from setron.features.chat.export import conditions_of
from setron.features.chat.schemas import INTENT_LABELS
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.ui.chat_components import asset_label
from setron.ui.components import button, label
from setron.ui.import_dialogs import confirm_numbered_save
from setron.ui.management_integration import save_numbered_export
from setron.ui.report_pagination import configure_report_device, configure_report_document, print_numbered_document


def report_document(report) -> QTextDocument:
    """미리보기·PDF·인쇄가 같은 선택 항목과 같은 문서를 사용한다."""
    doc = QTextDocument()
    configure_report_document(doc)
    html = ["<style>body {font-family: 'IBM Plex Sans KR'; font-size: 10pt; color:#142033;}"
            "h1{font-size:21pt;}h2{font-size:15pt;}h3{font-size:12pt;}"
            "th{background:#eef0f3;}td,th{padding:6px;}table{border-collapse:collapse;}</style>"]
    esc = lambda value: escape(str(value)).replace("\n", "<br>")
    paragraph = lambda value: f"<p>{esc(value)}</p>"

    def table(columns, rows):
        return ("<table border='1' width='100%' cellspacing='0'><thead><tr>" +
                "".join(f"<th>{esc(c)}</th>" for c in columns) + "</tr></thead>" +
                "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows) + "</table>")

    html += [f"<h1>{esc(report.title)}</h1>", paragraph(f"생성 {report.created_at:%Y-%m-%d %H:%M} · SETRON AI 분석")]
    options = report.options
    for i, part in enumerate(report.parts):
        answer = part.answer
        html.append(f"<h2>{esc(answer.title or answer.request.question)}</h2>")
        html.append(paragraph(f"질문: {answer.request.question}"))
        if options.summary:
            html.append(paragraph("요청 인식: " + " · ".join(INTENT_LABELS[t] for t in answer.request.intents)))
            html.append(paragraph(answer.summary))
        if options.conditions:
            html.append(paragraph(answer.understood))
            if pairs := conditions_of(answer):
                html.append(table(("조건", "값"), pairs))
        if options.charts:
            for j, png in enumerate(part.charts):
                image = QImage.fromData(png)
                if image.isNull():
                    continue
                name = QUrl(f"chart:{i}/{j}")
                doc.addResource(QTextDocument.ResourceType.ImageResource, name, image)
                width = min(640, image.width())
                height = round(image.height() * width / image.width())
                html.append(f"<p><img src='{name.toString()}' width='{width}' height='{height}'></p>")
            for chart in answer.charts:
                note = getattr(chart, "note", "")
                if note:
                    html.append(paragraph(note))
        if options.cause and answer.cause:
            html += ["<h3>원인 분석 · " + ("AI 설명" if answer.cause_is_ai else "자료 근거") + "</h3>", paragraph(answer.cause)]
        if options.tables:
            for value in answer.tables:
                if answer.cause and value.title.startswith("원인 근거"):
                    continue
                html.append(f"<h3>{esc(value.title)}</h3>")
                html.append(table(value.columns, value.rows) if value.rows else paragraph("조건에 맞는 설비가 없습니다."))
        if options.notices:
            for notice in (*answer.issues, *answer.notices):
                html.append(paragraph(f"주의 · {notice}"))
    doc.setHtml("".join(html))
    return doc


def save_report_pdf(report, path) -> ExportResult:
    path = Path(path)
    temporary = None
    if path.suffix.lower() != ".pdf":
        return ExportResult(ExportStatus.FAILED, path, "파일 이름은 .pdf로 끝나야 합니다.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, suffix=".pdf", delete=False) as handle:
            temporary = Path(handle.name)
        writer = QPdfWriter(str(temporary))
        writer.setResolution(96)
        configure_report_device(writer)
        writer.setTitle(report.title)
        writer.setCreator("SETRON")
        print_numbered_document(report_document(report), writer)
        del writer
        if temporary.stat().st_size < 10 or not temporary.read_bytes().startswith(b"%PDF-"):
            raise OSError("PDF 생성 결과가 비어 있습니다.")
        os.replace(temporary, path)
        return ExportResult(ExportStatus.SAVED, path)
    except (OSError, RuntimeError):
        return ExportResult(ExportStatus.FAILED, path, "PDF를 저장하지 못했습니다. 파일 사용 여부와 저장 권한을 확인해 주세요.")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class PdfPreviewDialog(QDialog):
    def __init__(self, report, *, filename="SETRON_보고서.pdf", parent=None,
                 preview_writer=None, document_factory=report_document, download_writer=None):
        super().__init__(parent)
        self.report = report
        self.filename = filename
        self.document_factory = document_factory
        self.download_writer = download_writer
        self.suffix = Path(filename).suffix.lower()
        self.format_name = "Word" if self.suffix == ".docx" else "PDF"
        self.result = None
        title = "보고서 미리보기" if download_writer else "PDF 미리보기"
        self.setWindowTitle(f"SETRON · {title}")
        self.resize(880, 825)
        self.setMinimumSize(560, 420)
        self.setStyleSheet("QDialog { background: white; }")
        self._temporary = TemporaryDirectory(prefix="setron-preview-")
        self._path = Path(self._temporary.name) / "preview.pdf"
        body = QVBoxLayout(self)
        body.setContentsMargins(22, 18, 22, 18)
        header = QHBoxLayout()
        header.addWidget(asset_label("87080.svg", slot=40))
        header.addWidget(label(title, 18, medium=True), 1)
        body.addLayout(header)
        self.metadata = label(filename, 12, wrap=True)
        body.addWidget(self.metadata)
        self.pdf = QPdfDocument(self)
        self.pages = QListWidget()
        self.pages.setFixedWidth(126)
        self.pages.setIconSize(QSize(94, 132))
        self.pages.setStyleSheet("QListWidget {background:#f4f5f8; border:0;} QListWidget::item {padding:8px 4px;} QListWidget::item:selected {background:#e8ebfc; color:#3c5ce9;}")
        self.pages.setViewMode(QListWidget.ViewMode.IconMode)
        self.pages.setMovement(QListWidget.Movement.Static)
        self.pages.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.pages.setWordWrap(True)
        self.view = QPdfView()
        self.view.setDocument(self.pdf)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        row = QHBoxLayout()
        row.addWidget(self.pages)
        row.addWidget(self.view, 1)
        body.addLayout(row, 1)
        self.position = label("", 12)
        body.addWidget(self.position)
        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel, self.print_button = button("닫기"), button("인쇄")
        self.download_button = button(f"{self.format_name} 다운로드", primary=True)
        cancel.clicked.connect(self.reject)
        self.print_button.clicked.connect(self.print_report)
        self.download_button.clicked.connect(self.download)
        for value in (cancel, self.print_button, self.download_button):
            footer.addWidget(value)
        body.addLayout(footer)
        self.pages.currentRowChanged.connect(self._jump)
        self.view.pageNavigator().currentPageChanged.connect(self._current)
        generated = (preview_writer or save_report_pdf)(report, self._path)
        # PDF 뷰의 비동기 렌더러가 Windows 임시 파일을 잡아 두지 않도록 메모리에서 읽는다.
        self._buffer = QBuffer(self)
        if generated.saved:
            self._buffer.setData(QByteArray(self._path.read_bytes()))
            self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            self.pdf.load(self._buffer)
        self.available = generated.saved and self.pdf.error() == QPdfDocument.Error.None_ and self.pdf.pageCount() > 0
        self.print_button.setEnabled(self.available)
        self.download_button.setEnabled(self.available)
        if self.available:
            from PySide6.QtGui import QIcon, QPixmap
            count = self.pdf.pageCount()
            self.metadata.setText(f"{filename} · {count}페이지")
            for index in range(count):
                image = self.pdf.render(index, QSize(94, 132))
                self.pages.addItem(QListWidgetItem(QIcon(QPixmap.fromImage(image)), f"{index + 1}페이지"))
            self.pages.setCurrentRow(0)
        else:
            self.metadata.setText(generated.message or "PDF 미리보기를 열지 못했습니다.")
        self.finished.connect(self._cleanup)

    def _jump(self, index):
        if index >= 0 and index != self.view.pageNavigator().currentPage():
            self.view.pageNavigator().jump(index, self.view.pageNavigator().currentLocation())
        self._current(index)

    def _current(self, index):
        self.pages.blockSignals(True)
        self.pages.setCurrentRow(index)
        self.pages.blockSignals(False)
        self.position.setText(f"{index + 1} / {self.pdf.pageCount()}페이지")

    def download(self):
        if self.download_writer:
            path, _ = QFileDialog.getSaveFileName(self, f"{self.format_name} 저장", self.filename,
                f"{self.format_name} (*{self.suffix})", options=QFileDialog.Option.DontConfirmOverwrite)
        else:
            path, _ = QFileDialog.getSaveFileName(self, "PDF 저장", self.filename, "PDF (*.pdf)")
        if not path:
            return
        if self.download_writer:
            def write(destination):
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    return self.download_writer(destination)
                finally:
                    QApplication.restoreOverrideCursor()
            self.result = save_numbered_export(write, path, self.suffix,
                                               confirm_duplicate=lambda requested: confirm_numbered_save(self, requested))
        else:
            self.result = save_report_pdf(self.report, path)
        if self.result is None:
            return
        if self.result.saved:
            self.accept()
        else:
            self.metadata.setText(self.result.message)

    def print_report(self):
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        configure_report_device(printer)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            print_numbered_document(self.document_factory(self.report), printer)

    def _cleanup(self, *_):
        # close()가 뷰에 빈 상태를 알린다. setDocument(None)은 Qt 링크 모델에서 경고를 낸다.
        self.pdf.close()
        self._buffer.close()
        self._temporary.cleanup()
