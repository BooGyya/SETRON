"""대화 답 → Word 보고서 (화면 10-2 "보고서로 저장", 10-3 자동 보고서).

별도 양식 없이 답의 요약·요청 인식 조건·그래프·원인·표·주의를 차례로 넣는다. 숫자는 답과 같은 집계값이다.
PDF는 만들지 않는다 — Word 파일에서 'PDF로 저장'으로 만든다 (보고서 백엔드 report.service와 같은 결정).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from docx import Document
from docx.shared import Inches, Pt

from setron.features.export.docx_fonts import use_korean_font
from setron.features.export.docx_pagination import add_page_numbers
from setron.features.export.schemas import ExportResult, ExportStatus

from .export import conditions_of
from .schemas import INTENT_LABELS, ChatAnswer


@dataclass(frozen=True)
class ReportPart:
    """보고서에 넣을 답 하나와 그 답의 그래프 그림(PNG)."""

    answer: ChatAnswer
    charts: tuple[bytes, ...] = ()


@dataclass(frozen=True)
class ReportOptions:
    summary: bool = True          # 요약 · 요청 인식
    conditions: bool = True       # 요청 인식 조건
    charts: bool = True           # 그래프
    cause: bool = True            # 원인 분석
    tables: bool = True           # 표 (조회 결과·달성률·우선순위 등)
    notices: bool = True          # 주의 사항


@dataclass(frozen=True)
class ChatReport:
    title: str
    parts: tuple[ReportPart, ...]
    options: ReportOptions = field(default_factory=ReportOptions)
    created_at: datetime = field(default_factory=datetime.now)


def default_report_title(result: ChatAnswer) -> str:
    """기본 제목: 답의 보고서 제목(예: '목표 N 달성률 가장 낮은 원본 구역 분석'). 기준일은 제목 아래 줄에 적는다."""
    return (result.title or result.request.question).strip()[:80]


def build_chat_report(report: ChatReport):
    """보고서 문서(python-docx Document)를 만든다."""
    document = Document()
    use_korean_font(document)                 # 기본 서식의 한글 글꼴(MS Mincho)로는 한글이 □로 깨진다
    add_page_numbers(document)
    document.styles["Normal"].font.size = Pt(10)
    document.add_heading(report.title, 0)
    bases = sorted({p.answer.base_date for p in report.parts if p.answer.base_date})
    meta = f"생성 {report.created_at:%Y-%m-%d %H:%M}"
    if bases:
        meta += f" · 자료 기준일 {', '.join(d.isoformat() for d in bases)}"
    document.add_paragraph(meta + " · SETRON AI 분석 (숫자는 DB 집계값, 원인·예측은 참고)")
    options = report.options
    for index, part in enumerate(report.parts, 1):
        result = part.answer
        heading = (result.title or result.request.question).strip()
        document.add_heading(f"{index}. {heading}" if len(report.parts) > 1 else heading, 1)
        if heading != result.request.question.strip() and result.request.intents:
            asked = document.add_paragraph(f"질문: {result.request.question}")
            asked.runs[0].italic = True
        if options.summary:
            intents = " · ".join(INTENT_LABELS[i] for i in result.request.intents)
            if intents:
                document.add_paragraph(f"요청 인식: {intents}")
            if result.summary:
                document.add_paragraph(result.summary)
        if options.conditions and (pairs := conditions_of(result)):
            _table(document, ("조건", "값"), pairs)
        if options.charts:
            for image in part.charts:
                document.add_picture(BytesIO(image), width=Inches(6.2))
        if options.cause and result.cause:
            document.add_heading("원인 분석" + (" (AI 설명 · 숫자 확인 통과)" if result.cause_is_ai else " (자료 근거)"), 2)
            document.add_paragraph(result.cause)
        if options.tables:
            for table in result.tables:
                if result.cause and table.title.startswith("원인 근거"):
                    continue
                document.add_heading(table.title, 2)
                if table.rows:
                    _table(document, table.columns, table.rows)
                else:
                    document.add_paragraph("조건에 맞는 설비가 없습니다.")
        if options.notices and (result.notices or result.issues):
            document.add_heading("주의 사항", 2)
            for line in (*result.issues, *result.notices):
                document.add_paragraph(line, style="List Bullet")
    return document


def save_chat_report(report: ChatReport, path: str | Path) -> ExportResult:
    """보고서를 docx로 저장한다. 다 쓴 뒤 이름을 바꿔 반쯤 쓴 파일이 남지 않게 한다. 실패해도 예외 대신 결과를 돌려준다."""
    path = Path(path)
    if path.suffix.lower() != ".docx":
        return ExportResult(ExportStatus.FAILED, path, "파일 이름은 .docx로 끝나야 합니다.")
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        document = build_chat_report(report)
        with NamedTemporaryFile(dir=path.parent, suffix=".docx", delete=False) as handle:
            temporary = Path(handle.name)
        document.save(temporary)
        os.replace(temporary, path)
        return ExportResult(ExportStatus.SAVED, path)
    except PermissionError:
        return ExportResult(ExportStatus.FAILED, path, "파일이 열려 있거나 저장 권한이 없습니다. Word에서 닫은 뒤 다시 저장해 주세요.")
    except OSError as error:
        return ExportResult(ExportStatus.FAILED, path, f"저장하지 못했습니다: {error}")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def _table(document, header, rows) -> None:
    table = document.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, header):
        cell.text = str(text)
    for row in rows:
        cells = table.add_row().cells
        for cell, text in zip(cells, row):
            cell.text = str(text)
