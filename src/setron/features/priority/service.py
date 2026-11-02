"""업그레이드할 설비의 우선순위를 정한다 (명세 BE-017).

정렬 기준 (멘토링 3차, FUN-037)
1. 위험 설비(BE-015)를 먼저 둔다.
2. 같은 위험 수준 안에서는 버전 노후도(BE-016)가 긴 설비를 먼저 둔다. 혼합 설비는 계열 중
   가장 오래된 버전의 경과 기간을 대표값으로 쓴다.

명세에 없어 다음과 같이 정했다.
- 기간 기준 설정이 잘못돼도 경과 기간은 계산되므로 정렬에는 경과 기간만 쓴다.
- 일부 PM만 기간을 알 수 없는 혼합 설비는 알려진 기간으로 순위를 매기고 ``partial_age``로 알린다.
- 경과 기간까지 같으면 호기 코드 순서로 둔다. 화면 순서를 고정하려는 것이지 우선순위 근거는 아니다.
- 목표 N 달성 여부는 순위에 쓰지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord
from setron.features.aging.schemas import AgeThresholds
from setron.features.aging.service import equipment_age
from setron.features.priority.schemas import PriorityEntry, PriorityGap, PriorityList
from setron.features.risk.schemas import RiskStatus
from setron.features.risk.service import RiskAssessor
from setron.features.version.service import FamilyReleases


def prioritize(
    records: Iterable[EquipmentRecord],
    assessor: RiskAssessor,
    families: Mapping[int, FamilyReleases],
    thresholds: AgeThresholds | None,
    base_date: date | None = None,
) -> PriorityList:
    """설비를 우선순위 목록과 확인 필요 목록으로 나눈다. HPA는 빼고 뺀 대수만 알린다.

    조회 날짜를 주지 않으면 설비마다 그 현황의 기준 날짜로 경과 기간을 계산한다.
    """
    records = tuple(records)
    entries = [_entry(r, assessor, families, thresholds, base_date) for r in records if not r.is_hpa]

    ranked = sorted(
        (e for e in entries if not e.gaps),
        key=lambda e: (not e.is_risk, -e.elapsed_days, e.record.equipment_code),
    )
    needs_check = sorted(
        (e for e in entries if e.gaps),
        key=lambda e: (not e.is_risk, e.record.equipment_code),
    )
    return PriorityList(
        tuple(_with_rank(e, i) for i, e in enumerate(ranked, start=1)),
        tuple(needs_check),
        hpa_excluded=sum(r.is_hpa for r in records),
        has_rules=bool(assessor.rules),
    )


def _entry(
    record: EquipmentRecord,
    assessor: RiskAssessor,
    families: Mapping[int, FamilyReleases],
    thresholds: AgeThresholds | None,
    base_date: date | None,
) -> PriorityEntry:
    risk = assessor.equipment_risk(record)
    age = equipment_age(record, families, thresholds, base_date)
    calculated = [p for p in age.pms if p.age.elapsed_days is not None]
    representative = max(calculated, key=lambda p: p.age.elapsed_days) if calculated else None

    gaps = []
    if risk.status is RiskStatus.UNKNOWN:
        gaps.append(PriorityGap.RISK_UNKNOWN)
    if representative is None:
        gaps.append(PriorityGap.AGE_UNKNOWN)
    return PriorityEntry(record, risk, age, representative, gaps=tuple(gaps))


def _with_rank(entry: PriorityEntry, rank: int) -> PriorityEntry:
    return PriorityEntry(entry.record, entry.risk, entry.age, entry.representative, rank, entry.gaps)
