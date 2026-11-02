"""AI 분석 Word 보고서의 입력 형식 (명세 BE-022, FUN-043).

입력은 이미 받은 AI 설명 결과(:class:`AnalysisReport`)다. 보고서의 숫자는 AI에 보낸 자료를 그대로
옮기므로, 같은 조건으로 조회한 화면 숫자와 같다. 보고서를 만들면서 AI를 다시 호출하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from setron.features.analysis.schemas import AnalysisReport


@dataclass(frozen=True, kw_only=True)
class WordReport:
    """Word 보고서에 넣을 내용.

    - ``analysis``: 원인·현황 설명 결과 (BE-019 ``explain``).
    - ``forecast``: 참고 예측 설명 결과 (BE-020 ``explain_forecast``). 없으면 예측 구역을 넣지 않는다.
    """

    question: str
    analysis: AnalysisReport
    forecast: AnalysisReport | None = None
    title: str = "SETRON AI 분석 보고서"
    created_at: datetime | None = None
