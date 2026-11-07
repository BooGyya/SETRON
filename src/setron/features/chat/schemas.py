"""대화 요청·답 묶음. 화면은 ChatAnswer를 그대로 그린다 (표·그래프·원인·우선순위·보고서 카드)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from setron.features.ai.schemas import QueryConditions


class Intent(str, Enum):
    """요청 종류. 한 질문에 여러 개가 함께 올 수 있다 (예: 원인 + 우선순위)."""

    SEARCH = "SEARCH"              # 목록·대수
    COMPARE = "COMPARE"            # 구역·사업장·모델별 달성률 비교
    CHANGE = "CHANGE"              # 이전 기준일 대비 변화
    CAUSE = "CAUSE"                # 원인 분석 (근거 + 추정 설명)
    PRIORITY = "PRIORITY"          # 업그레이드 우선순위
    FORECAST = "FORECAST"          # 버전 통일 예측 (참고)
    VERSION_INFO = "VERSION_INFO"  # 최신 버전·버전별 설비
    REPORT = "REPORT"              # 보고서 생성


INTENT_LABELS: dict[Intent, str] = {
    Intent.SEARCH: "자연어 검색", Intent.COMPARE: "표준화율 분석", Intent.CHANGE: "기간 변화",
    Intent.CAUSE: "원인 분석", Intent.PRIORITY: "업그레이드 우선순위", Intent.FORECAST: "버전 통일 예측",
    Intent.VERSION_INFO: "버전 정보", Intent.REPORT: "자동 보고서 생성",
}


class Verdict(str, Enum):
    """질문이 고른 판정. 없으면 전체."""

    BELOW = "BELOW"        # 목표 N 미만 (업그레이드 필요)
    ACHIEVED = "ACHIEVED"  # 목표 N 이상
    CHECK = "CHECK"        # 판단 불가 (버전 누락·미등록·목표 미지정)


VERDICT_LABELS = {Verdict.BELOW: "목표 N 미만", Verdict.ACHIEVED: "목표 N 이상", Verdict.CHECK: "확인 필요"}


class GroupBy(str, Enum):
    LINE = "LINE"
    SITE = "SITE"
    MODEL = "MODEL"


GROUP_BY_LABELS = {GroupBy.LINE: "원본 구역", GroupBy.SITE: "사업장", GroupBy.MODEL: "모델"}


@dataclass(frozen=True)
class ChatRequest:
    """질문을 이해한 결과. 화면의 '요청 인식' 줄이 이것을 보여 준다."""

    question: str
    intents: tuple[Intent, ...]
    conditions: QueryConditions = field(default_factory=QueryConditions)
    areas: tuple[str, ...] = ()                 # "MEMORY" / "FOUNDRY"
    verdict: Verdict | None = None
    group_by: GroupBy | None = None
    understood: bool = True                     # False면 분석 질문이 아님 (인사 등)
    follow_up: bool = False                     # 앞 질문의 조건을 이어받음 ("그중에 P3만", "그럼 화성은?")


@dataclass(frozen=True)
class Table:
    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    tones: tuple[str | None, ...] = ()          # 행별 판정 배지 색: "danger"·"warning"·"success"·None


@dataclass(frozen=True)
class BarChart:
    title: str
    labels: tuple[str, ...]
    values: tuple[float | None, ...]            # 달성률(%) — 판단 가능 설비가 없으면 None
    highlight: tuple[int, ...] = ()             # 강조할 막대 위치 (가장 낮은 곳 등)


@dataclass(frozen=True)
class LinePoint:
    day: date
    rate: float | None
    projected: bool = False


@dataclass(frozen=True)
class LineChart:
    title: str
    series: tuple[tuple[str, tuple[LinePoint, ...]], ...]
    note: str = ""


@dataclass(frozen=True)
class ReportSpec:
    title: str
    sections: tuple[str, ...]


@dataclass(frozen=True)
class ChatAnswer:
    """답 묶음 하나. 숫자는 모두 DB 집계 값이다.

    - ``understood``: '요청 인식' 줄의 조건 문장 (예: "Site = 평택 · 설비군 = M-O2 · 기준일 2026-10-21").
    - ``summary``: 맨 위 요약 한두 문장.
    - ``cause``·``cause_is_ai``: 원인 설명. AI 문장이면 숫자 확인을 거친 것이고, 아니면 자료에서 뽑은 근거 문장이다.
    - ``notices``: '판단 기준 확정 전' 같은 주의. ``issues``: 이해했지만 선택지에 없어 뺀 조건.
    - ``equipment_ids``: 조회 결과 설비 (Excel 내보내기 범위용).
    """

    request: ChatRequest
    understood: str
    summary: str
    base_date: date | None = None
    tables: tuple[Table, ...] = ()
    charts: tuple[BarChart | LineChart, ...] = ()
    cause: str | None = None
    cause_is_ai: bool = False
    report: ReportSpec | None = None
    notices: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()
    equipment_ids: tuple[int, ...] = ()
    polished: bool = False                      # 요약 문장을 AI가 다듬었음 (숫자·이름은 원문과 같음을 확인)
