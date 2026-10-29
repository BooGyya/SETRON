"""모든 화면이 함께 쓰는 공통 집계 (명세 BE-012, FUN-050).

계산 순서는 명세를 따른다.
1. 각 PM의 버전을 그 PM의 모델·계열에 지정한 N과 비교한다 (BE-003).
2. 설비·계열마다 PM 결과를 모아 부합·미달·판단 불가를 정한다.
3. 실제 호기 수, 모델별 대수, 업그레이드 필요 수, 설비·계열당 1건의 적용률을 센다.

HPA는 모든 숫자에서 빼고 뺀 대수만 따로 알린다. 조회 범위(사업장·라인·모델) 필터는 호출하는
쪽에서 적용한 뒤 넘긴다. 날짜별 추이는 날짜마다 그날의 기록만으로 계산하고 빈 날짜를 채우지 않는다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date

from setron.features.aggregation.schemas import (
    EquipmentRecord,
    EquipmentStatus,
    FamilyCount,
    FamilyStatus,
    ModelCount,
    PmComparison,
    Summary,
)
from setron.features.version.schemas import CompareReason, CompareStatus, VersionComparison
from setron.features.version.service import FamilyReleases, compare_version

# (모델 ID, 계열 ID) → 목표 N의 릴리즈 ID. 모델·계열마다 따로 지정한다 (REQ-051).
Targets = Mapping[tuple[int, int], int]


def family_status(equipment_id: int, family_id: int, pms: Iterable[PmComparison]) -> FamilyStatus:
    """같은 설비·계열의 PM 비교 결과를 모아 계열 상태를 정한다 (FUN-003)."""
    pms = tuple(pms)
    statuses = [p.comparison.status for p in pms]
    if CompareStatus.BELOW in statuses:
        status = CompareStatus.BELOW
    elif statuses and all(s is CompareStatus.AT_OR_ABOVE for s in statuses):
        status = CompareStatus.AT_OR_ABOVE
    else:
        status = CompareStatus.UNDETERMINED
    raws = {(p.pm.version_raw or "").strip() for p in pms} - {""}
    return FamilyStatus(equipment_id, family_id, status, pms, pm_mismatch=len(raws) > 1)


def equipment_status(
    record: EquipmentRecord,
    families: Mapping[int, FamilyReleases],
    targets: Targets,
) -> EquipmentStatus:
    """설비 한 대의 PM을 각자의 모델·계열 N과 비교하고 계열별로 묶는다."""
    by_family: dict[int, list[PmComparison]] = defaultdict(list)
    for pm in record.pms:
        by_family[pm.family_id].append(PmComparison(pm, _compare_pm(pm, families, targets)))
    return EquipmentStatus(
        record,
        tuple(family_status(record.equipment_id, fid, pms) for fid, pms in sorted(by_family.items())),
    )


def summarize(
    records: Iterable[EquipmentRecord],
    families: Mapping[int, FamilyReleases],
    targets: Targets,
) -> Summary:
    """한 기준 날짜의 설비 현황으로 공통 집계를 만든다. 날짜가 섞이면 ValueError를 낸다."""
    records = tuple(records)
    dates = {r.base_date for r in records}
    if len(dates) > 1:
        raise ValueError(f"한 번에 한 기준 날짜만 집계합니다: {sorted(dates)}")
    ids = [r.equipment_id for r in records]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    if duplicated:
        raise ValueError(f"같은 날짜에 같은 설비가 두 번 있습니다: {duplicated}")

    hpa = [r for r in records if r.is_hpa]
    statuses = tuple(equipment_status(r, families, targets) for r in records if not r.is_hpa)

    units = [f for e in statuses for f in e.families]
    family_units: dict[int, list[FamilyStatus]] = defaultdict(list)
    for unit in units:
        family_units[unit.family_id].append(unit)

    return Summary(
        **_count(units),
        base_date=dates.pop() if dates else None,
        equipment_count=len(statuses),
        upgrade_needed=sum(e.needs_upgrade for e in statuses),
        hpa_excluded=len(hpa),
        by_model=_count_models(statuses),
        by_family=tuple(
            FamilyCount(**_count(items), family_id=fid) for fid, items in sorted(family_units.items())
        ),
        equipment=statuses,
    )


def summarize_by_date(
    records: Iterable[EquipmentRecord],
    families: Mapping[int, FamilyReleases],
    targets: Targets,
) -> tuple[Summary, ...]:
    """기준 날짜별 집계를 날짜순으로 돌려준다. 기록이 있는 날짜만 나오며 빈 날짜를 채우지 않는다."""
    by_date: dict[date, list[EquipmentRecord]] = defaultdict(list)
    for record in records:
        by_date[record.base_date].append(record)
    return tuple(summarize(by_date[d], families, targets) for d in sorted(by_date))


def _compare_pm(pm, families: Mapping[int, FamilyReleases], targets: Targets) -> VersionComparison:
    target_id = targets.get((pm.group_id, pm.family_id))
    family = families.get(pm.family_id)
    if target_id is None or family is None:
        return VersionComparison(
            pm.version_raw, None, None, CompareStatus.UNDETERMINED, reason=CompareReason.TARGET_INVALID
        )
    return compare_version(family, pm.version_raw, target_id, release_version_id=pm.release_version_id)


def _count(units: Iterable[FamilyStatus]) -> dict[str, int]:
    statuses = [u.status for u in units]
    return {
        "compliant": statuses.count(CompareStatus.AT_OR_ABOVE),
        "below": statuses.count(CompareStatus.BELOW),
        "undetermined": statuses.count(CompareStatus.UNDETERMINED),
    }


def _count_models(statuses: Iterable[EquipmentStatus]) -> tuple[ModelCount, ...]:
    """모델별로 그 모델의 PM이 있는 호기 수와, 그 모델 PM 중 N 미만이 있는 호기 수를 센다."""
    equipment: dict[int, set[int]] = defaultdict(set)
    needed: dict[int, set[int]] = defaultdict(set)
    for status in statuses:
        for unit in status.families:
            for pm in unit.pms:
                equipment[pm.pm.group_id].add(status.record.equipment_id)
                if pm.comparison.status is CompareStatus.BELOW:
                    needed[pm.pm.group_id].add(status.record.equipment_id)
    return tuple(
        ModelCount(gid, len(equipment[gid]), len(needed[gid])) for gid in sorted(equipment)
    )
