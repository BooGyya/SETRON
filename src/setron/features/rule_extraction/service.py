"""규칙 추출을 AI 추출과 같은 결과 모양(``AIResult[ExtractResult]``)으로 돌려준다.

정해진 양식(설비 현황표·세로형 릴리즈노트)이면 규칙으로 읽고, 아니면 None 을 돌려 AI 추출로 넘긴다.
규칙 결과도 AI 결과와 똑같이 원본 셀 대조·번역표 후보·저장 전 검증·저장 직전 원본 검사를 모두 거친다.
화면 배지만 '규칙 추출'로 다르다.
"""

from __future__ import annotations

from setron.features.ai.schemas import (
    EQUIPMENT_STATUS_FIELDS, RELEASE_NOTE_FIELDS, AIResult, AIStatus, AITask, ExtractedRow, ExtractKind, ExtractResult,
    ReviewNote,
)
from setron.infrastructure.ai_client import ResponseSource

from .release_rules import parse_release
from .status_rules import parse_status

RULE_MESSAGE = "정해진 양식을 규칙으로 읽었습니다. 원본 셀과 대조한 결과를 검토해 주세요."


def rule_extract(kind: ExtractKind, sheet) -> AIResult[ExtractResult] | None:
    """정해진 양식이면 규칙으로 추출한 결과를, 아니면 None 을 돌려준다.

    param
    - kind: 설비 현황 또는 릴리즈 노트.
    - sheet: 사용자가 읽은 원본 SheetData (병합 셀은 왼쪽 위 칸에만 값).

    return
    - 원본 셀 위치를 담은 추출 결과(출처 RULE). 아는 양식이 아니거나 읽은 행이 없으면 None.
    """
    rows = [list(row) for row in sheet.values]
    if kind is ExtractKind.EQUIPMENT_STATUS:
        merged = [(m.top - sheet.first_row, m.left - sheet.first_col, m.bottom - sheet.first_row, m.right - sheet.first_col)
                  for m in sheet.merged_ranges]
        parsed, fields = parse_status(rows, merged), EQUIPMENT_STATUS_FIELDS
    else:
        parsed, fields = parse_release(rows), RELEASE_NOTE_FIELDS
    if not parsed or not any(item.values for item in parsed):
        return None
    extracted, notes = [], []
    for item in parsed:
        notes.extend(ReviewNote(reason, name, sheet.address_of(*pos) if pos else None) for reason, name, pos in item.notes)
        if not item.values:
            continue
        values = {name: item.values.get(name) for name in fields}
        sources = {name: sheet.address_of(*pos) for name, pos in item.positions.items() if name in fields and pos is not None}
        extracted.append(ExtractedRow(values, sources))
    result = ExtractResult(kind, sheet.sheet_name, tuple(extracted), tuple(notes))
    return AIResult(AITask.EXCEL_EXTRACT, AIStatus.OK, RULE_MESSAGE, result, ResponseSource.RULE)
