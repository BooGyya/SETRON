"""현황 보고서 Excel 출력의 입력과 결과 형식 (명세 BE-021).

입력은 화면에서 이미 계산한 결과다. 집계(BE-012)·위험(BE-015)·노후도(BE-016)·우선순위(BE-017)를
다시 계산하지 않고 그대로 표로 옮기므로, 같은 조건이면 화면 숫자와 파일 숫자가 같다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from setron.features.aggregation.schemas import Summary
from setron.features.aging.schemas import AgeThresholds, EquipmentAge
from setron.features.priority.schemas import PriorityList
from setron.features.risk.schemas import RiskSummary


@dataclass(frozen=True)
class TargetRow:
    """모델·계열별 목표 N 한 줄. ``version_raw``가 None이면 목표 미지정이다."""

    model_name: str
    family_name: str
    version_raw: str | None


@dataclass(frozen=True, kw_only=True)
class StatusReport:
    """현황 보고서에 넣을 조회 조건과 계산 결과.

    - ``scope``: 화면에 적용된 조회 범위. (조건 이름, 값) 목록이며 예: ("Site", "평택").
    - ``family_names``: 계열 ID → 화면 이름. 없으면 '계열 {ID}'로 적는다.
    - ``risks``·``ages``·``priority``: 없으면 해당 열이나 시트를 '미계산'으로 둔다.
    - ``trend``: 날짜별 집계(``summarize_by_date``). 두 날짜 이상이면 추이 시트와 꺾은선 차트를 만든다.
    - ``thresholds``: 노후도 기간 기준. None이면 저장된 기준이 잘못된 것이다.
    """

    summary: Summary
    scope: tuple[tuple[str, str], ...] = ()
    targets: tuple[TargetRow, ...] = ()
    family_names: Mapping[int, str] = field(default_factory=dict)
    risks: RiskSummary | None = None
    ages: tuple[EquipmentAge, ...] | None = None
    thresholds: AgeThresholds | None = None
    priority: PriorityList | None = None
    trend: tuple[Summary, ...] = ()
    created_at: datetime | None = None


class ExportStatus(str, Enum):
    SAVED = "SAVED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ExportResult:
    """파일 저장 결과. 실패하면 ``message``에 원인과 해결 방법을 담는다."""

    status: ExportStatus
    path: Path
    message: str | None = None

    @property
    def saved(self) -> bool:
        return self.status is ExportStatus.SAVED
