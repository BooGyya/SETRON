"""AI 분석 Word 보고서의 입력 형식 (명세 BE-022, FUN-043).

입력은 이미 받은 AI 설명 결과(:class:`AnalysisReport`)다. 보고서의 숫자는 AI에 보낸 자료를 그대로
옮기므로, 같은 조건으로 조회한 화면 숫자와 같다. 보고서를 만들면서 AI를 다시 호출하지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from setron.features.analysis.schemas import AnalysisReport
from setron.features.priority.schemas import PriorityList


class ReportSection(str, Enum):
    """보고서로 저장 창(화면 10-2)의 "포함 항목". 값은 화면에 보이는 이름이다."""

    SUMMARY = "요약 · 요청 인식 조건"
    LOCATION_RATES = "원본 구역별 목표 N 달성률"
    TREND_FORECAST = "달성률 추이 및 통일 예측"
    CAUSE = "원인 분석"
    PRIORITY = "업그레이드 우선순위"
    PREVIOUS = "이전 질문"


# 화면 기본값: 이전 질문만 빼고 모두 포함한다.
DEFAULT_SECTIONS = tuple(s for s in ReportSection if s is not ReportSection.PREVIOUS)


@dataclass(frozen=True)
class PreviousAnswer:
    """같은 대화에서 앞서 받은 질문과 답변. "이전 질문 포함"을 고르면 보고서 끝에 붙인다."""

    question: str
    analysis: AnalysisReport


@dataclass(frozen=True, kw_only=True)
class WordReport:
    """Word 보고서에 넣을 내용.

    - ``analysis``: 원인·현황 설명 결과 (BE-019 ``explain``).
    - ``forecast``: 참고 예측 설명 결과 (BE-020 ``explain_forecast``). 없으면 예측 구역을 넣지 않는다.
    - ``sections``: 포함할 항목. 자료가 없는 항목(예: 우선순위를 계산하지 않음)은 골라도 넣지 않는다.
    - ``priority``: 업그레이드 우선순위 (BE-017). ``family_names``는 그 표의 계열 이름이다.
    - ``charts``: 화면에서 그린 그래프 그림(PNG 바이트). 항목별로 표 아래에 넣는다. 없으면 표만 넣는다.
    """

    question: str
    analysis: AnalysisReport
    forecast: AnalysisReport | None = None
    title: str = "SETRON AI 분석 보고서"
    created_at: datetime | None = None
    sections: tuple[ReportSection, ...] = DEFAULT_SECTIONS
    priority: PriorityList | None = None
    family_names: Mapping[int, str] = field(default_factory=dict)
    previous: tuple[PreviousAnswer, ...] = ()
    charts: Mapping[ReportSection, bytes] = field(default_factory=dict)
