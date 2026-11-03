"""AI 분석 보고서를 Word(docx) 파일로 만든다 (명세 BE-022, FUN-043).

문서 구성
1. 제목, 생성 시각, 자료 출처(예제 응답인지 실제 AI 결과인지)
2. 질문과 조회 조건(기준일, 조회 범위, 계열별 목표 N)
3. AI 답변과 근거·추정·미확인 목록. AI 호출이 실패했으면 실패 안내
4. 사용한 수치: 전체 현황, 사업장별 현황, 위험. AI에 보낸 자료를 그대로 옮긴다
5. 참고 예측(있을 때만): 계열별 표, 계산 방법, 가정, 한계
6. 숫자 확인: 답변에 나왔지만 보낸 자료에 없는 숫자

그래프는 Excel 보고서(BE-021) 범위라 넣지 않는다. PDF는 Word에서 저장해 만들 수 있어 따로 만들지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.document import Document as DocxDocument

from setron.features.analysis.schemas import AnalysisReport
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.features.report.schemas import WordReport
from setron.infrastructure.ai_client import ResponseSource

SOURCE_LABELS = {
    ResponseSource.EXAMPLE: "예제 응답 (실제 AI 결과 아님)",
    ResponseSource.REAL: "회사 AI 응답",
}
REFERENCE_NOTE = "예측은 저장된 현황으로 계산한 참고용 값이며 실제 완료 시점을 보장하지 않습니다."
EMPTY = "-"


def build_word_report(report: WordReport) -> DocxDocument:
    """보고서 내용을 Word 문서로 만든다. 파일로 저장하지는 않는다."""
    document = Document()
    facts = report.analysis.facts
    created = report.created_at or datetime.now()

    document.add_heading(report.title, level=0)
    document.add_paragraph(f"생성 시각: {created.strftime('%Y-%m-%d %H:%M')}")
    document.add_paragraph(f"자료 출처: {_source(report.analysis)}")

    document.add_heading("질문과 조회 조건", level=1)
    document.add_paragraph(f"질문: {report.question}")
    _conditions(document, facts.get("조회 조건", {}))

    document.add_heading("분석 결과", level=1)
    _answer(document, report.analysis)

    document.add_heading("사용한 수치", level=1)
    document.add_paragraph("아래 수치는 AI에 보낸 자료와 같으며, 같은 조건의 화면 집계에서 나온 값입니다.")
    _numbers(document, facts)

    if report.forecast is not None:
        document.add_heading("참고 예측", level=1)
        _forecast(document, report.forecast)

    document.add_heading("숫자 확인", level=1)
    _number_check(document, report)
    return document


def save_word_report(report: WordReport, path: str | Path) -> ExportResult:
    """보고서를 docx 파일로 저장한다. 실패해도 예외 대신 원인을 담은 결과를 돌려준다."""
    path = Path(path)
    if path.suffix.lower() != ".docx":
        return ExportResult(ExportStatus.FAILED, path, "파일 이름은 .docx로 끝나야 합니다.")
    try:
        build_word_report(report).save(str(path))
    except PermissionError:
        message = "파일을 저장할 수 없습니다. 같은 이름의 파일이 Word에서 열려 있는지, 폴더 쓰기 권한이 있는지 확인해 주세요."
        return ExportResult(ExportStatus.FAILED, path, message)
    except OSError as error:
        return ExportResult(ExportStatus.FAILED, path, f"파일을 저장하지 못했습니다: {error.strerror or error}")
    return ExportResult(ExportStatus.SAVED, path)


# --- 구역 -----------------------------------------------------------------------

def _conditions(document: DocxDocument, conditions: Mapping[str, Any]) -> None:
    scope = conditions.get("조회 범위") or {}
    rows = [("기준일", conditions.get("기준일") or "기준일 없음"),
            *((name, value) for name, value in scope.items())]
    if "적용률 계산" in conditions:
        rows.append(("적용률 계산", conditions["적용률 계산"]))
    _table(document, ["항목", "값"], rows)
    targets = conditions.get("계열별 목표 N") or []
    if targets:
        document.add_paragraph("계열별 목표 N")
        _table(document, ["계열", "목표 N"], [(t["계열"], t["목표 N"]) for t in targets])


def _answer(document: DocxDocument, report: AnalysisReport) -> None:
    result = report.result
    if not result.ok:
        document.add_paragraph(f"AI 분석을 받지 못했습니다: {result.message}")
        document.add_paragraph("아래 수치는 조회 결과이며, 원인 설명은 포함되지 않았습니다.")
        return
    answer = result.data
    document.add_paragraph(answer.answer)
    for title, items in (("근거 (자료로 확인한 내용)", answer.evidence),
                         ("추정", answer.assumptions),
                         ("미확인 (자료로 확인할 수 없는 내용)", answer.unconfirmed)):
        document.add_heading(title, level=2)
        if not items:
            document.add_paragraph("없음")
        for item in items:
            document.add_paragraph(item, style="List Bullet")


def _numbers(document: DocxDocument, facts: Mapping[str, Any]) -> None:
    overall = facts.get("전체 현황")
    if overall:
        document.add_heading("전체 현황", level=2)
        _table(document, ["항목", "값"], [(k, _value(v)) for k, v in overall.items()])

    sites = facts.get("사업장별 현황") or []
    if sites:
        document.add_heading("사업장별 현황", level=2)
        columns = ["사업장", "설비 수(HPA 제외)", "N 이상", "N 미만", "판단 불가", "N 이상 적용률(%)"]
        _table(document, columns, [[_value(site.get(c)) for c in columns] for site in sites])

    risks = facts.get("위험")
    if risks:
        document.add_heading("위험", level=2)
        if "상태" in risks:
            document.add_paragraph(f"위험 판정: {risks['상태']}")
            return
        if not risks.get("위험 기준 등록", True):
            document.add_paragraph("등록된 위험 기준이 없어 '위험 없음'은 확인된 결과가 아닙니다.")
        _table(document, ["사업장", "위험 설비", "미확인 설비"],
               [(r["사업장"], r["위험 설비"], r["미확인 설비"]) for r in risks.get("사업장별", [])])
        for reason in risks.get("사유별", []):
            document.add_paragraph(f"{reason['사유']}: {', '.join(reason['설비'])}", style="List Bullet")


def _forecast(document: DocxDocument, report: AnalysisReport) -> None:
    facts = report.facts
    document.add_paragraph(REFERENCE_NOTE)
    period = facts.get("분석 기간", {})
    document.add_paragraph(f"분석 기간: {period.get('시작') or EMPTY} ~ {period.get('끝') or EMPTY}")
    document.add_paragraph(f"계산 방법: {facts.get('계산 방법', EMPTY)} (단위: {facts.get('계산 단위', EMPTY)})")
    columns = ["계열", "상태", "데이터 수(장)", "N 이상 증가", "하루 증가(대/일)", "끝 N 미만(남은 대수)",
               "끝 판단 불가", "예상 잔여 기간(일)", "참고 완료일"]
    families = facts.get("계열별 예측", [])
    _table(document, columns, [[_value(f.get(c), EMPTY) for c in columns] for f in families])
    for family in families:
        for reason in family.get("사유", []):
            document.add_paragraph(f"{family['계열']}: {reason}", style="List Bullet")
    for title in ("가정", "한계"):
        document.add_heading(title, level=2)
        for item in facts.get(title, []):
            document.add_paragraph(item, style="List Bullet")
    if report.result.ok:
        document.add_heading("예측 설명", level=2)
        document.add_paragraph(report.result.data.answer)
    else:
        document.add_paragraph(f"예측 설명을 받지 못했습니다: {report.result.message}")


def _number_check(document: DocxDocument, report: WordReport) -> None:
    unmatched = [*report.analysis.unmatched_numbers, *(report.forecast.unmatched_numbers if report.forecast else ())]
    if unmatched:
        document.add_paragraph(
            "AI 답변에 보낸 자료에 없는 숫자가 있습니다. 해당 내용은 사용한 수치 표와 대조해 확인해 주세요: "
            + ", ".join(dict.fromkeys(unmatched))
        )
    else:
        document.add_paragraph("AI 답변의 숫자가 모두 보낸 자료에 있습니다.")


# --- 보조 -----------------------------------------------------------------------

def _source(report: AnalysisReport) -> str:
    source = report.result.source
    return SOURCE_LABELS.get(source, "확인 불가") if source else "AI 응답 없음"


def _table(document: DocxDocument, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    table = document.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    for cell, title in zip(table.rows[0].cells, header):
        cell.text = str(title)
    for row in rows:
        for cell, value in zip(table.add_row().cells, row):
            cell.text = _value(value)


def _value(value: Any, none: str = "계산 불가") -> str:
    """표에 넣을 글자. 적용률처럼 계산할 수 없는 값(None)은 0이 아니라 ``none``으로 적는다."""
    if value is None:
        return none
    if isinstance(value, bool):
        return "예" if value else "아니오"
    return str(value)
