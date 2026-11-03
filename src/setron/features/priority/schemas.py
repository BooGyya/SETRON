"""업그레이드 우선순위의 결과 형식 (명세 BE-017).

멘토링 3차의 기본 우선순위를 따른다: 1) 위험 버전이 적용된 설비 2) 버전이 가장 낮은 설비 3) 나머지를 버전이
낮은 순서로. "버전이 낮다"는 기준 N에서 뒤처진 칸 수로 보고, 같으면 계열 안 버전 위치가 낮은 순, 출시일이
오래된 순으로 둔다 (데이터 파트 `ai/svhm/core/risk.py`의 `priority_list`와 같은 규칙). 위험 여부나 버전 위치를
알 수 없는 설비는 순위를 매기지 않고 '확인 필요' 목록에 빠진 근거와 함께 따로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aging.schemas import EquipmentAge, PmAge
from setron.features.risk.schemas import EquipmentRisk, RiskStatus
from setron.features.version.schemas import ReleaseVersion


class PriorityGap(str, Enum):
    """순위를 매기지 못한 이유."""

    RISK_UNKNOWN = "RISK_UNKNOWN"
    VERSION_UNKNOWN = "VERSION_UNKNOWN"


GAP_MESSAGES = {
    PriorityGap.RISK_UNKNOWN: "위험 여부를 확인할 수 없습니다.",
    PriorityGap.VERSION_UNKNOWN: "적용 버전이 등록 릴리즈와 연결되지 않았거나 순서를 몰라 버전 위치를 정할 수 없습니다.",
}


@dataclass(frozen=True)
class PmPosition:
    """PM 하나의 버전 위치.

    - ``position``: 계열 안에서 확인된 순서로 센 위치(0부터, 낮을수록 오래된 버전).
    - ``steps_behind``: 기준 N까지 남은 칸 수. 양수면 N 아래, 0이면 N, 음수면 N 위. N이 없으면 None.
    """

    pm: PmVersion
    release: ReleaseVersion
    position: int
    steps_behind: int | None = None


@dataclass(frozen=True)
class PriorityEntry:
    """설비 한 대의 우선순위 판단 근거.

    - ``rank``: 우선순위 목록에서의 순위(1부터). 확인 필요 목록이면 None이다.
    - ``positions``: 버전 위치를 정한 PM들. 혼합 설비는 가장 많이 뒤처진 칸 수와 가장 낮은 위치를 쓴다.
    - ``age``·``representative``: 화면·보고서에 함께 보여 줄 노후도(BE-016). 순위에는 쓰지 않는다.
    """

    record: EquipmentRecord
    risk: EquipmentRisk
    age: EquipmentAge
    representative: PmAge | None
    positions: tuple[PmPosition, ...] = ()
    rank: int | None = None
    gaps: tuple[PriorityGap, ...] = ()

    @property
    def is_risk(self) -> bool:
        return self.risk.status is RiskStatus.RISK

    @property
    def steps_behind(self) -> int | None:
        """기준 N에서 가장 많이 뒤처진 칸 수. N이 있는 PM이 없으면 None."""
        steps = [p.steps_behind for p in self.positions if p.steps_behind is not None]
        return max(steps) if steps else None

    @property
    def lowest(self) -> PmPosition | None:
        """계열 안 위치가 가장 낮은 PM."""
        return min(self.positions, key=lambda p: p.position) if self.positions else None

    @property
    def elapsed_days(self) -> int | None:
        return self.representative.age.elapsed_days if self.representative else None

    @property
    def partial_age(self) -> bool:
        return self.representative is not None and self.age.has_unchecked

    @property
    def messages(self) -> tuple[str, ...]:
        return tuple(GAP_MESSAGES[g] for g in self.gaps)


@dataclass(frozen=True)
class PriorityList:
    """업그레이드 우선순위 결과.

    - ``ranked``: 순위를 매긴 설비. 위험 → 위험 없음, 같은 위험 수준에서는 버전이 낮은 순서다.
    - ``needs_check``: 근거가 부족해 순위를 매기지 않은 설비. 위험이 확인된 설비도 버전 위치를 모르면 여기에 둔다.
    - ``has_rules``: False이면 등록된 위험 기준이 없어, 위험 없음이 확인된 결과가 아니다.
    """

    ranked: tuple[PriorityEntry, ...]
    needs_check: tuple[PriorityEntry, ...]
    hpa_excluded: int = 0
    has_rules: bool = True
