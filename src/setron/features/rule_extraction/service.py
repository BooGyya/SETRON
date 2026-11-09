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
from .status_rules import find_header_rows, parse_status

RULE_MESSAGE = "정해진 양식을 규칙으로 읽었습니다. 원본 셀과 대조한 결과를 검토해 주세요."
SPARSE_LIMIT = 3            # 값이 이 개수 이하인 시트는 읽을 내용이 없다고 본다 (예: 'P1과 유사' 메모만 있는 시트)
EMPTY_FORM_MESSAGE = ("설비 현황표 양식이지만 설비호기·버전 칸이 비어 있어 읽을 설비가 없습니다. "
                      "설비가 채워진 현황표 시트로 다시 읽어 주세요.")


def _stop(message: str) -> AIResult[ExtractResult]:
    """AI로 넘기지 않고 바로 알려 줄 실패 (규칙으로 판단 끝). 화면은 'AI 추출 실패'가 아니라 '추출할 내용 없음'으로 보인다."""
    return AIResult(AITask.EXCEL_EXTRACT, AIStatus.FAILED, message, None, ResponseSource.RULE)


def rule_extract(kind: ExtractKind, sheet) -> AIResult[ExtractResult] | None:
    """정해진 양식이면 규칙으로 추출한 결과를, 아니면 None 을 돌려준다.

    param
    - kind: 설비 현황 또는 릴리즈 노트.
    - sheet: 사용자가 읽은 원본 SheetData (병합 셀은 왼쪽 위 칸에만 값).

    return
    - 원본 셀 위치를 담은 추출 결과(출처 RULE). 아는 양식이 아니거나 읽은 행이 없으면 None.
    """
    rows = [list(row) for row in sheet.values]
    filled = sum(1 for row in rows for value in row if value is not None and str(value).strip())
    if filled <= SPARSE_LIMIT:
        # 빈 시트·메모만 있는 시트를 AI에 보내면 30초~1분 기다린 뒤 실패한다 (11/08 교육용 원본 평택 P2 등).
        return _stop(f"읽을 내용이 거의 없는 시트입니다(값 {filled}개). 설비 현황표나 릴리즈 노트가 들어 있는 시트를 골라 주세요.")
    if kind is ExtractKind.EQUIPMENT_STATUS:
        merged = [(m.top - sheet.first_row, m.left - sheet.first_col, m.bottom - sheet.first_row, m.right - sheet.first_col)
                  for m in sheet.merged_ranges]
        parsed, fields = parse_status(rows, merged), EQUIPMENT_STATUS_FIELDS
    else:
        parsed, fields = parse_release(rows), RELEASE_NOTE_FIELDS
    if not parsed or not any(item.values for item in parsed):
        if kind is ExtractKind.EQUIPMENT_STATUS and find_header_rows(rows):
            # 현황표 머리글은 있는데 설비가 하나도 없는 빈 양식 (교육용 원본 평택 P1·화성 Foundry). AI로 넘기지 않는다.
            return _stop(EMPTY_FORM_MESSAGE)
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
