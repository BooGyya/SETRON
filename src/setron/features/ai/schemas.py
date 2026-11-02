"""AI에 보낼 작업과 AI에게 받을 결과의 형식 (명세 BE-005).

세 작업의 결과 형식을 정한다.
- Excel 추출 (BE-007): :class:`ExtractResult`
- 질문 해석 (BE-018): :class:`QueryConditions`
- 분석 (BE-019, BE-020): :class:`AnalysisAnswer`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar

from setron.infrastructure.ai_client import ResponseSource


class AITask(str, Enum):
    EXCEL_EXTRACT = "EXCEL_EXTRACT"
    QUERY_PARSE = "QUERY_PARSE"
    ANALYSIS = "ANALYSIS"


class AIStatus(str, Enum):
    OK = "OK"
    FAILED = "FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    INVALID_RESPONSE = "INVALID_RESPONSE"


T = TypeVar("T")


@dataclass(frozen=True)
class AIResult(Generic[T]):
    """AI 작업 한 번의 결과.

    ``status``가 OK일 때만 ``data``가 있다. 실패해도 예외를 내지 않고 ``message``에
    화면에 보여줄 안내 문구를 담는다. ``source``로 예제 응답과 실제 API 결과를 구분한다.
    """

    task: AITask
    status: AIStatus
    message: str
    data: T | None = None
    source: ResponseSource | None = None
    raw_content: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is AIStatus.OK


# --- Excel 추출 (BE-007) ---------------------------------------------------

class ExtractKind(str, Enum):
    EQUIPMENT_STATUS = "EQUIPMENT_STATUS"
    RELEASE_NOTE = "RELEASE_NOTE"


# 추출 결과에는 원본 표기를 담는다. features/extraction에서 출처를 대조하고 정리한다.
EQUIPMENT_STATUS_FIELDS = (
    "base_date", "site", "line", "group_name", "model_name", "production_area",
    "equipment_code", "pm_name", "family_name", "version_raw",
    "ui_type", "structure_type", "geometry_type", "modification_type",
    "engine_version", "epd_version",
)
RELEASE_NOTE_FIELDS = (
    "version_raw", "release_date", "item_no", "content", "target_models",
    "group_name", "model_name", "family_name", "ui_type", "structure_type",
    "modification_type", "modification_mark",
)


@dataclass(frozen=True)
class ExtractedRow:
    """추출한 행 하나. ``values``는 항목 이름별 원본 표기, ``sources``는 항목 이름별 셀 주소다."""

    values: dict[str, str | None]
    sources: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewNote:
    """사용자 확인이 필요한 항목. ``address``는 관련 셀 주소이며 모르면 None이다."""

    reason: str
    field: str | None = None
    address: str | None = None


@dataclass(frozen=True)
class ExtractResult:
    kind: ExtractKind
    sheet_name: str
    rows: tuple[ExtractedRow, ...]
    review_notes: tuple[ReviewNote, ...] = ()


# --- 질문 해석 (BE-018) ----------------------------------------------------

class TargetView(str, Enum):
    """답변 뒤에 이동할 화면 (FUN-062)."""

    DASHBOARD = "DASHBOARD"
    HEATMAP = "HEATMAP"
    ITEM_MANAGER = "ITEM_MANAGER"
    ANALYSIS = "ANALYSIS"


@dataclass(frozen=True)
class QueryConditions:
    """질문에서 찾은 조회 조건. 값은 질문에 적힌 표기 그대로이며, DB 값과의 대조는 조회 단계에서 한다.

    ``unresolved``에는 해석하지 못한 조건을 담아 사용자에게 알린다 (FUN-041).
    """

    sites: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    families: tuple[str, ...] = ()
    versions: tuple[str, ...] = ()
    items: tuple[str, ...] = ()
    base_date: str | None = None
    target_view: TargetView | None = None
    unresolved: tuple[str, ...] = ()


# --- 분석 (BE-019, BE-020) -------------------------------------------------

@dataclass(frozen=True)
class AnalysisAnswer:
    """분석 답변. 자료로 확인한 근거와 추정·미확인 내용을 나눠 담는다 (FUN-036, FUN-040)."""

    answer: str
    evidence: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    unconfirmed: tuple[str, ...] = ()
