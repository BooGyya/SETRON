"""대화 답 → AI 조회 결과 Excel 자료 (화면 10-1, 백엔드 export.query_result BE-021).

같은 조건(판정 조건은 빼고)으로 다시 집계한 전체 설비와, 답의 표에 보인 설비를 함께 넘긴다.
요청 인식 조건은 답의 조건 줄을 (이름, 값)으로 나눠 '조회 조건' 시트에 넣는다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from setron.features.aggregation.service import summarize
from setron.features.export.query_result import QueryResultExport

from .answers import ChatData, _scope
from .schemas import ChatAnswer, Intent


def conditions_of(result: ChatAnswer) -> tuple[tuple[str, str], ...]:
    """'Site = 평택 · 설비군 = M-O2 · 기준일 2026-10-21' → (("Site", "평택"), ("설비군", "M-O2"), ("기준일", "2026-10-21"))."""
    pairs = []
    for part in result.understood.split(" · "):
        if " = " in part:
            name, value = part.split(" = ", 1)
            pairs.append((name.strip(), value.strip()))
        elif part.startswith("기준일 "):
            pairs.append(("기준일", part.removeprefix("기준일 ").strip()))
    return tuple(pairs)


def query_export(result: ChatAnswer, data: ChatData, *, sources=None) -> QueryResultExport:
    """답 하나를 Excel로 내보낼 자료로 만든다.

    param
    - result: 대화 답 (검색 답이면 표에 보인 설비가 '조회 결과' 범위가 된다).
    - data: 답을 만든 것과 같은 자료.
    - sources: (설비 ID, 계열 ID) → 원본 출처 문구. 없으면 Excel에 '출처 없음'.

    return
    - ``QueryResultExport`` (같은 조건의 전체 설비 + 화면에 보인 설비).
    """
    whole = replace(result.request, verdict=None)          # '같은 조건의 전체 설비'는 판정 조건을 뺀 범위
    scope = _scope(whole, data)
    records = [r for r in scope.records if not r.is_hpa]
    summary = summarize(records, data.catalog.releases, data.targets)
    shown = result.equipment_ids if Intent.SEARCH in result.request.intents else None
    # 기준일은 백엔드가 파일 이름과 '조회 조건' 시트에 항상 넣으므로 조건 줄에서는 뺀다 (이름에 날짜가 두 번 붙지 않게).
    conditions = tuple(pair for pair in conditions_of(result) if pair[0] != "기준일")
    return QueryResultExport(summary=summary, question=result.request.question, conditions=conditions,
                             shown_ids=shown, family_names=dict(data.family_names), sources=dict(sources or {}),
                             created_at=datetime.now())
