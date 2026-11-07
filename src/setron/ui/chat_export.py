"""AI 분석 대화의 Excel 내보내기 창(10-1)과 보고서로 저장 창(10-2)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QStandardPaths, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QRadioButton,
    QVBoxLayout,
)

from setron.features.chat.report import ChatReport, ReportOptions, ReportPart, default_report_title, save_chat_report
from setron.features.export.query_result import (
    ALL_COLUMNS, ExportRange, QueryColumn, default_filename, range_counts, save_query_result,
)
from setron.features.export.schemas import ExportStatus
from setron.ui.components import button, label
from setron.ui.theme import CANVAS, PRIMARY, SECONDARY

_STYLE = f"""
QDialog {{ background: {CANVAS}; }}
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
        if not name.lower().endswith(suffix):
            name += suffix
        return Path(self.folder.text().strip() or export_folder()) / name


class _Base(QDialog):
    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"SETRON · {title}")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(520)
        self.setStyleSheet(_STYLE)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(24, 22, 24, 20)
        self.body.setSpacing(10)
        self.body.addWidget(label(title, 20, medium=True))
        self.body.addWidget(label(subtitle, 12, color=SECONDARY))
        self.result = None
        self.message = label("", 12, color="#c62828", wrap=True)
        self.message.hide()

    def _buttons(self, note: str, save_text: str = "저장"):
        self.body.addWidget(self.message)
        row = QHBoxLayout()
        row.addWidget(label(note, 11, color=SECONDARY, wrap=True), 1)
        cancel = button("취소")
        cancel.clicked.connect(self.reject)
        self.save_button = button(save_text, primary=True)
        self.save_button.clicked.connect(self.save)
        row.addWidget(cancel)
        row.addWidget(self.save_button)
        self.body.addLayout(row)

    def _finish(self, result):
        self.result = result
        if result.status is ExportStatus.SAVED:
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
        return self._finish(save_query_result(self.export, self.files.path(".xlsx"), scope=scope, columns=columns,
                                              include_conditions=self.conditions.isChecked()))


class ReportDialog(_Base):
    """보고서로 저장 (화면 10-2): 제목 · 포함 항목 · 이전 질문 포함 · Word."""

    def __init__(self, parts, *, previous=(), title: str | None = None, parent=None):
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
        self.include_previous = QCheckBox(f"이전 질문 {len(self.previous)}개 포함")
        self.include_previous.setVisible(bool(self.previous))
        self.body.addWidget(self.include_previous)
        self.body.addWidget(label("파일 형식", 12, medium=True))
        word = QRadioButton("Word (.docx) · 편집 가능")
        word.setChecked(True)
        self.body.addWidget(word)
        safe = "".join(ch for ch in self.title.text() if ch not in '\\/:*?"<>|').strip()[:60] or "SETRON_보고서"
        self.files = _FileRow(self, f"{safe}.docx")
        self.body.addLayout(self.files)
        self._buttons("PDF가 필요하면 저장한 Word 파일에서 'PDF로 저장'을 쓰세요 · 원인·예측은 참고로 표시됩니다")

    def report(self) -> ChatReport:
        options = ReportOptions(**{key: box.isChecked() for key, box in self.options.items()})
        parts = (*(self.previous if self.include_previous.isChecked() else ()), *self.parts)
        return ChatReport(self.title.text().strip() or "SETRON AI 분석 보고서", tuple(parts), options)

    def save(self) -> bool:
        return self._finish(save_chat_report(self.report(), self.files.path(".docx")))
