"""업그레이드할 설비의 우선순위를 정한다 (명세 BE-017).

정렬 기준 (멘토링 3차 기본 우선순위, 데이터 파트 `ai/svhm/core/risk.py`의 `priority_list`와 같은 규칙)
1. 위험 설비(BE-015)를 먼저 둔다.
2. 기준 N에서 가장 많이 뒤처진 설비를 먼저 둔다. 혼합 설비는 PM 중 가장 많이 뒤처진 칸 수를 쓴다.
   N이 지정되지 않은 설비는 0칸으로 본다.
3. 칸 수가 같으면 계열 안 버전 위치가 낮은 순, 그다음 그 버전의 출시일이 오래된 순으로 둔다.

명세에 없어 다음과 같이 정했다.
- 칸 수는 계열의 확인된 순서(version_order)로 센다. 순서를 모르거나 겹치는 릴리즈는 위치를 정하지 않는다.
- 어느 PM도 버전 위치를 정할 수 없으면 순위를 매기지 않고 '확인 필요'에 둔다. 출시일이 없으면 마지막에 둔다.
- 모든 기준이 같으면 호기 코드 순서로 둔다. 화면 순서를 고정하려는 것이지 우선순위 근거는 아니다.
- 노후도(BE-016)는 근거로 함께 보여 주지만 순위에는 쓰지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aggregation.service import Targets
from setron.features.aging.schemas import AgeThresholds
from setron.features.aging.service import equipment_age
from setron.features.priority.schemas import PmPosition, PriorityEntry, PriorityGap, PriorityList
from setron.features.risk.schemas import EquipmentRisk, RiskStatus, RiskSummary
from setron.features.risk.service import RiskAssessor
from setron.features.version.service import FamilyReleases


def prioritize(
    records: Iterable[EquipmentRecord],
    assessor: RiskAssessor,
    families: Mapping[int, FamilyReleases],
    thresholds: AgeThresholds | None,
    base_date: date | None = None,
    *,
    targets: Targets | None = None,
) -> PriorityList:
    """설비를 우선순위 목록과 확인 필요 목록으로 나눈다. HPA는 빼고 뺀 대수만 알린다.

    ``targets``는 (모델 ID, 계열 ID) → 목표 N 릴리즈 ID다. 없으면 모든 설비를 0칸으로 보고 버전 위치로 정렬한다.
    조회 날짜를 주지 않으면 설비마다 그 현황의 기준 날짜로 노후도를 계산한다.
    """
    return _prioritize(records, assessor.equipment_risk, bool(assessor.rules), families, thresholds, base_date,
                       targets or {})


def prioritize_from_risks(
    records: Iterable[EquipmentRecord],
    risks: RiskSummary,
    families: Mapping[int, FamilyReleases],
    thresholds: AgeThresholds | None,
    base_date: date | None = None,
    *,
    targets: Targets | None = None,
) -> PriorityList:
    """이미 판정한 위험 결과(:class:`RiskSummary`)로 우선순위를 정한다. 판정 결과가 없는 설비는 위험 미확인이다.

    현황 보고서처럼 위험을 먼저 계산해 둔 곳에서 같은 판정을 다시 하지 않으려고 쓴다.
    """
    by_id = {risk.record.equipment_id: risk for risk in risks.equipment}

    def risk_of(record: EquipmentRecord) -> EquipmentRisk:
        return by_id.get(record.equipment_id) or EquipmentRisk(record, RiskStatus.UNKNOWN, ())

    return _prioritize(records, risk_of, risks.has_rules, families, thresholds, base_date, targets or {})


def _prioritize(records, risk_of, has_rules, families, thresholds, base_date, targets) -> PriorityList:
    records = tuple(records)
    entries = [_entry(r, risk_of(r), families, thresholds, base_date, targets) for r in records if not r.is_hpa]

    ranked = sorted((e for e in entries if not e.gaps), key=_order)
    needs_check = sorted(
        (e for e in entries if e.gaps),
        key=lambda e: (not e.is_risk, e.record.equipment_code),
    )
    return PriorityList(
        tuple(_with_rank(e, i) for i, e in enumerate(ranked, start=1)),
        tuple(needs_check),
        hpa_excluded=sum(r.is_hpa for r in records),
        has_rules=has_rules,
    )


def _order(entry: PriorityEntry):
    lowest = entry.lowest
    return (not entry.is_risk, -(entry.steps_behind or 0), lowest.position,
            lowest.release.released_at or date.max, entry.record.equipment_code)


def _entry(
    record: EquipmentRecord,
    risk: EquipmentRisk,
    families: Mapping[int, FamilyReleases],
    thresholds: AgeThresholds | None,
    base_date: date | None,
    targets: Targets,
) -> PriorityEntry:
    age = equipment_age(record, families, thresholds, base_date)
    calculated = [p for p in age.pms if p.age.elapsed_days is not None]
    representative = max(calculated, key=lambda p: p.age.elapsed_days) if calculated else None
    positions = tuple(p for p in (_position(pm, families, targets) for pm in record.pms) if p is not None)

    gaps = []
    if risk.status is RiskStatus.UNKNOWN:
        gaps.append(PriorityGap.RISK_UNKNOWN)
    if not positions:
        gaps.append(PriorityGap.VERSION_UNKNOWN)
    return PriorityEntry(record, risk, age, representative, positions, gaps=tuple(gaps))


def _position(pm: PmVersion, families: Mapping[int, FamilyReleases], targets: Targets) -> PmPosition | None:
    """PM 버전의 계열 안 위치와 기준 N까지의 칸 수. 위치를 정할 수 없으면 None."""
    family = families.get(pm.family_id)
    if family is None:
        return None
    release = family.get(pm.release_version_id) if pm.release_version_id is not None else family.match(pm.version_raw)
    index = _index(family)
    if release is None or release.release_version_id not in index:
        return None
    target_id = targets.get((pm.group_id, pm.family_id))
    steps = index[target_id] - index[release.release_version_id] if target_id in index else None
    return PmPosition(pm, release, index[release.release_version_id], steps)


def _index(family: FamilyReleases) -> dict[int, int]:
    """확인된 순서로 센 릴리즈 위치. 순서가 겹치는 릴리즈는 위치를 정하지 않는다."""
    duplicated = set(family.sorted.duplicated_orders)
    ordered = [r for r in family.sorted.ordered if r.version_order not in duplicated]
    return {r.release_version_id: i for i, r in enumerate(ordered)}


def _with_rank(entry: PriorityEntry, rank: int) -> PriorityEntry:
    return PriorityEntry(entry.record, entry.risk, entry.age, entry.representative, entry.positions, rank, entry.gaps)
