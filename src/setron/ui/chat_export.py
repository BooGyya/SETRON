"""AI 분석 대화의 Excel 내보내기 창(10-1)과 보고서로 저장 창(10-2)."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QStandardPaths, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QRadioButton,
    QVBoxLayout, QScrollArea, QWidget, QMessageBox, QApplication,
)

from setron.features.chat.report import ChatReport, ReportOptions, ReportPart, default_report_title, save_chat_report
from setron.features.export.query_result import (
    ALL_COLUMNS, ExportRange, QueryColumn, default_filename, range_counts, save_query_result,
)
from setron.features.export.schemas import ExportStatus
from setron.ui.components import button, label
from setron.ui.theme import PRIMARY, SECONDARY
from setron.ui.chat_components import asset_label, close_button, selection_style, install_selection_style
from setron.ui.chat_report_preview import PdfPreviewDialog, save_report_pdf

_STYLE = f"""
QDialog {{ background: white; }}
QWidget#chatExportContent {{ background: white; }}
QFrame#exportChoice {{ background: white; border: 1px solid #dee0e5; border-radius: 10px; }}
QFrame#exportChoice[selected="true"] {{ border: 2px solid {PRIMARY}; background: #f3f5ff; }}
"""


def export_folder() -> Path:
    """기본 저장 위치: 문서\\SETRON\\Export"""
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
    return Path(documents) / "SETRON" / "Export"


def chart_png(view, size=None) -> bytes:
    """차트 뷰를 PNG로 (보고서용). 화면에 없는 뷰는 크기를 정해 그리고, 화면의 뷰는 지금 크기 그대로 찍는다."""
    if size is not None:
        view.resize(*size)
    pixmap = view.grab()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(data.data())


class _FileRow(QGridLayout):
    """파일 이름 · 저장 위치 · 폴더 선택."""

    def __init__(self, dialog: QDialog, filename: str):
        super().__init__()
        self.dialog = dialog
        self.setHorizontalSpacing(8)
        self.setVerticalSpacing(6)
        self.name = QLineEdit(filename)
        self.folder = QLineEdit(str(export_folder()))
        choose = button("폴더 선택")
        choose.clicked.connect(self._choose)
        self.addWidget(label("파일명", 12, color=SECONDARY), 0, 0, 1, 2)
        self.addWidget(self.name, 1, 0, 1, 2)
        self.addWidget(label("저장 위치", 12, color=SECONDARY), 2, 0, 1, 2)
        self.addWidget(self.folder, 3, 0)
        self.addWidget(choose, 3, 1)

    def _choose(self):
        chosen = QFileDialog.getExistingDirectory(self.dialog, "저장 위치", self.folder.text())
        if chosen:
            self.folder.setText(chosen)

    def path(self, suffix: str) -> Path:
        name = self.name.text().strip() or f"SETRON{suffix}"
        name = Path(name).name
        if Path(name).suffix.lower() in (".docx", ".pdf", ".xlsx"):
            name = str(Path(name).with_suffix(""))
        if not name.lower().endswith(suffix):
            name += suffix
        return Path(self.folder.text().strip() or export_folder()) / name


class _Base(QDialog):
    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"SETRON · {title}")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(480, 400)
        self.resize(600, 636)
        self.setStyleSheet(_STYLE + selection_style())
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(24, 22, 24, 20)
        header = QHBoxLayout()
        header.addWidget(asset_label("297a6.svg" if title.startswith("Excel") else "c36ff.svg", slot=40))
        header.addWidget(label(title, 20, medium=True), 1)
        header.addWidget(close_button(self.reject))
        self.outer.addLayout(header)
        self.outer.addWidget(label(subtitle, 12, color=SECONDARY, wrap=True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("chatExportContent")
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(0, 8, 4, 8)
        self.body.setSpacing(10)
        scroll.setWidget(content)
        self.outer.addWidget(scroll, 1)
        self.result = None
        self.saved_paths = ()
        self.confirm_overwrite = lambda paths: QMessageBox.question(
            self, "파일 덮어쓰기", "이미 있는 파일을 바꿀까요?\n" + "\n".join(p.name for p in paths)
        ) == QMessageBox.StandardButton.Yes
        self.message = label("", 12, color="#c62828", wrap=True)
        self.message.hide()

    def _buttons(self, note: str, save_text: str = "저장"):
        install_selection_style(self)
        self.outer.addWidget(self.message)
        self.outer.addWidget(label(note, 11, color=SECONDARY, wrap=True))
        row = QHBoxLayout()
        row.addStretch(1)
        if hasattr(self, "preview"):
            self.preview_button = button("미리보기")
            self.preview_button.clicked.connect(self.preview)
            row.addWidget(self.preview_button)
        cancel = button("취소")
        cancel.clicked.connect(self.reject)
        self.save_button = button(save_text, primary=True)
        self.save_button.clicked.connect(self.save)
        row.addWidget(cancel)
        row.addWidget(self.save_button)
        self.outer.addLayout(row)

    def _prepare(self, paths):
        existing = [p for p in paths if p.exists()]
        if existing and not self.confirm_overwrite(existing):
            return False
        try:
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.message.setText("저장 폴더를 만들 수 없습니다. 저장 위치와 권한을 확인해 주세요.")
            self.message.show()
            return False
        return True

    def _finish(self, result):
        self.result = result
        if result.status is ExportStatus.SAVED:
            if not self.saved_paths:
                self.saved_paths = (result.path,)
            self.accept()
            return True
        self.message.setText(result.message or "저장하지 못했습니다.")
        self.message.show()
        return False


class ExcelExportDialog(_Base):
    """Excel 내보내기 (화면 10-1): 범위 · 포함할 열 · 요청 인식 조건 · 파일명 · 저장 위치."""

    def __init__(self, export, parent=None):
        super().__init__("Excel 내보내기", "AI 조회 결과를 Excel 파일로 저장합니다", parent)
        self.export = export
        self.resize(560, 616)
        counts = range_counts(export)
        self.body.addWidget(label("내보낼 범위", 12, medium=True))
        self.ranges = QButtonGroup(self)
        self.range_buttons = {}
        for scope, title, caption in ((ExportRange.SHOWN, f"조회 결과 {counts[ExportRange.SHOWN]}대", "화면에 보이는 표"),
                                      (ExportRange.ALL, f"같은 조건의 전체 설비 {counts[ExportRange.ALL]}대",
                                       "목표 달성 설비까지 포함")):
            radio = QRadioButton(f"{title}  ·  {caption}")
            self.ranges.addButton(radio)
            self.range_buttons[scope] = radio
            self.body.addWidget(radio)
        self.range_buttons[ExportRange.SHOWN].setChecked(True)
        self.body.addWidget(label("포함할 열", 12, medium=True))
        grid = QGridLayout()
        self.columns = {}
        for index, column in enumerate(ALL_COLUMNS):
            box = QCheckBox(column.value)
            box.setChecked(True)
            if column is QueryColumn.SOURCE and not export.sources:
                box.setChecked(False)
                box.setEnabled(False)
                box.setToolTip("저장된 셀 위치를 이 화면에서 불러오지 않아 비워 둡니다.")
            self.columns[column] = box
            grid.addWidget(box, index // 3, index % 3)
        self.conditions = QCheckBox("요청 인식 조건 (질문과 인식한 조건 줄)")
        self.conditions.setChecked(True)
        grid.addWidget(self.conditions, 2, 0, 1, 3)
        self.body.addLayout(grid)
        self.files = _FileRow(self, default_filename(export))
        self.body.addLayout(self.files)
        self._buttons("시트: 조회 결과 · 조회 조건 (기준일·범위·모델·계열별 목표 N은 항상 넣어요)")

    def save(self) -> bool:
        columns = [c for c, box in self.columns.items() if box.isChecked()]
        if not columns:
            self.message.setText("포함할 열을 하나 이상 골라 주세요.")
            self.message.show()
            return False
        scope = ExportRange.ALL if self.range_buttons[ExportRange.ALL].isChecked() else ExportRange.SHOWN
        path = self.files.path(".xlsx")
        if not self._prepare((path,)):
            return False
        return self._finish(save_query_result(self.export, path, scope=scope, columns=columns,
                                              include_conditions=self.conditions.isChecked()))


class ReportDialog(_Base):
    """보고서로 저장 (화면 10-2): 항목 선택·이전 질문·Word/PDF·미리보기."""

    def __init__(self, parts, *, previous=(), title: str | None = None, parent=None, format="word"):
        super().__init__("보고서로 저장", "별도 양식 없이 답변의 요약 · 그래프 · 표로 보고서를 만듭니다", parent)
        self.parts, self.previous = tuple(parts), tuple(previous)
        first = self.parts[0].answer
        self.body.addWidget(label("보고서 제목", 12, medium=True))
        self.title = QLineEdit(title or default_report_title(first))
        self.body.addWidget(self.title)
        self.body.addWidget(label("포함 항목", 12, medium=True))
        self.options = {}
        has = lambda attr: any(getattr(p.answer, attr) for p in self.parts)  # noqa: E731
        for key, text, enabled in (("summary", "요약 · 요청 인식", True), ("conditions", "요청 인식 조건 표", True),
                                   ("charts", "그래프", any(p.charts for p in self.parts)),
                                   ("cause", "원인 분석", has("cause")), ("tables", "표 (조회 결과·달성률·우선순위)", has("tables")),
                                   ("notices", "주의 사항", has("notices") or has("issues"))):
            box = QCheckBox(text)
            box.setChecked(enabled)
            box.setEnabled(enabled)
            self.options[key] = box
            self.body.addWidget(box)
        self.chart_options, self.table_options = {}, {}
        for pi, part in enumerate(self.parts):
            for ci, chart in enumerate(part.answer.charts[:len(part.charts)]):
                box = QCheckBox(f"    {chart.title}")
                box.setChecked(True)
                self.chart_options[pi, ci] = box
                self.body.addWidget(box)
                self.options["charts"].toggled.connect(box.setEnabled)
            for ti, table in enumerate(part.answer.tables):
                if part.answer.cause and table.title.startswith("원인 근거"):
                    continue
                box = QCheckBox(f"    {table.title}")
                box.setChecked(True)
                self.table_options[pi, ti] = box
                self.body.addWidget(box)
                self.options["tables"].toggled.connect(box.setEnabled)
        self.include_previous = QCheckBox(f"이전 질문 {len(self.previous)}개 포함")
        self.include_previous.setVisible(bool(self.previous))
        self.include_previous.toggled.connect(self._previous_changed)
        self.body.addWidget(self.include_previous)
        self.outer.addWidget(label("파일 형식", 12, medium=True))
        self.formats = QButtonGroup(self)
        self.format_buttons = {}
        row = QHBoxLayout()
        for key, text in (("word", "Word (.docx)"), ("pdf", "PDF (.pdf)"), ("both", "둘 다")):
            radio = QRadioButton(text)
            self.formats.addButton(radio)
            self.format_buttons[key] = radio
            row.addWidget(radio)
        self.format_buttons[format].setChecked(True)
        self.outer.addLayout(row)
        safe = "".join(ch for ch in self.title.text() if ch not in '\\/:*?"<>|').strip()[:60] or "SETRON_보고서"
        self.files = _FileRow(self, f"{safe}.docx")
        self.body.addLayout(self.files)
        self._buttons("숫자는 조회 자료 기준입니다. AI 원인 설명과 예측은 참고용입니다.")

    def _previous_changed(self, checked):
        parts = (*self.parts, *(self.previous if checked else ()))
        available = {"charts": any(p.charts for p in parts), "cause": any(p.answer.cause for p in parts),
                     "tables": any(p.answer.tables for p in parts),
                     "notices": any(p.answer.notices or p.answer.issues for p in parts)}
        for key, enabled in available.items():
            box = self.options[key]
            was_enabled = box.isEnabled()
            box.setEnabled(enabled)
            if enabled and not was_enabled:
                box.setChecked(True)
            elif not enabled:
                box.setChecked(False)

    def report(self) -> ChatReport:
        options = ReportOptions(**{key: box.isChecked() for key, box in self.options.items()})
        selected = []
        for pi, part in enumerate(self.parts):
            charts = tuple(png for ci, png in enumerate(part.charts) if self.chart_options[pi, ci].isChecked())
            schema_charts = tuple(c for ci, c in enumerate(part.answer.charts)
                                  if (pi, ci) in self.chart_options and self.chart_options[pi, ci].isChecked())
            tables = tuple(t for ti, t in enumerate(part.answer.tables)
                           if (pi, ti) in self.table_options and self.table_options[pi, ti].isChecked())
            selected.append(ReportPart(replace(part.answer, charts=schema_charts, tables=tables), charts))
        parts = (*(self.previous if self.include_previous.isChecked() else ()), *selected)
        return ChatReport(self.title.text().strip() or "SETRON AI 분석 보고서", tuple(parts), options)

    def preview(self):
        self.preview_dialog = PdfPreviewDialog(self.report(), filename=self.files.path(".pdf").name, parent=self)
        # 미리보기의 다운로드가 실제 저장된 경우에만 상위 화면에도 성공을 전달한다.
        if self.preview_dialog.exec() == QDialog.DialogCode.Accepted and self.preview_dialog.result is not None:
            self.saved_paths = (self.preview_dialog.result.path,)
            self._finish(self.preview_dialog.result)

    def save(self) -> bool:
        if not any(box.isChecked() and box.isEnabled() for box in self.options.values()):
            self.message.setText("포함 항목을 하나 이상 선택해 주세요.")
            self.message.show()
            return False
        format = next(k for k, radio in self.format_buttons.items() if radio.isChecked())
        paths = tuple(self.files.path(s) for s in ((".docx", ".pdf") if format == "both" else
                                                  (".pdf",) if format == "pdf" else (".docx",)))
        if not self._prepare(paths):
            return False
        report = self.report()
        saved = []
        self.save_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for path in paths:
                result = save_report_pdf(report, path) if path.suffix == ".pdf" else save_chat_report(report, path)
                if not result.saved:
                    self.saved_paths = tuple(saved)
                    self._finish(result)
                    if saved:
                        self.message.setText(f"일부 파일만 저장됨: {', '.join(p.name for p in saved)}. " + self.message.text())
                    return False
                saved.append(path)
            self.saved_paths = tuple(saved)
            return self._finish(result)
        finally:
            QApplication.restoreOverrideCursor()
            self.save_button.setEnabled(True)
