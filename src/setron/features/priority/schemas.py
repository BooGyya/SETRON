"""업그레이드 우선순위의 결과 형식 (명세 BE-017).

위험 설비를 먼저 두고, 같은 위험 수준 안에서는 버전 노후도(출시 후 경과 기간)가 긴 설비를 먼저 둔다
(멘토링 3차, FUN-037). 위험 여부나 경과 기간을 알 수 없는 설비는 순위를 매기지 않고
'확인 필요' 목록에 빠진 근거와 함께 따로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from setron.features.aggregation.schemas import EquipmentRecord
from setron.features.aging.schemas import EquipmentAge, PmAge
from setron.features.risk.schemas import EquipmentRisk, RiskStatus


class PriorityGap(str, Enum):
    """순위를 매기지 못한 이유."""

    RISK_UNKNOWN = "RISK_UNKNOWN"
    AGE_UNKNOWN = "AGE_UNKNOWN"


GAP_MESSAGES = {
    PriorityGap.RISK_UNKNOWN: "위험 여부를 확인할 수 없습니다.",
    PriorityGap.AGE_UNKNOWN: "적용 버전의 출시일을 확인할 수 없어 경과 기간을 계산하지 못했습니다.",
}


@dataclass(frozen=True)
class PriorityEntry:
    """설비 한 대의 우선순위 판단 근거.

    - ``rank``: 우선순위 목록에서의 순위(1부터). 확인 필요 목록이면 None이다.
    - ``representative``: 경과 기간이 가장 긴 PM. 혼합 설비는 이 기간을 대표값으로 정렬한다.
      계열별 기간은 ``age.pms``에 모두 있다.
    - ``partial_age``: 일부 PM의 경과 기간을 계산하지 못했다. 실제 대표 기간이 더 길 수 있다.
    """

    record: EquipmentRecord
    risk: EquipmentRisk
    age: EquipmentAge
    representative: PmAge | None
    rank: int | None = None
    gaps: tuple[PriorityGap, ...] = ()

    @property
    def is_risk(self) -> bool:
        return self.risk.status is RiskStatus.RISK

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

    - ``ranked``: 순위를 매긴 설비. 위험 → 위험 없음, 같은 위험 수준에서는 경과 기간이 긴 순서다.
    - ``needs_check``: 근거가 부족해 순위를 매기지 않은 설비. 위험이 확인된 설비도 출시일이 없으면 여기에 둔다.
    - ``has_rules``: False이면 등록된 위험 기준이 없어, 위험 없음이 확인된 결과가 아니다.
    """

    ranked: tuple[PriorityEntry, ...]
    needs_check: tuple[PriorityEntry, ...]
    hpa_excluded: int = 0
    has_rules: bool = True
