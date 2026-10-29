"""버전 출시 후 경과 기간과 노후도 상태를 계산한다 (명세 BE-016, FUN-072·073).

경계는 달력 기준이다. 기본값(1년·2년)이면 2025-10-29 출시 버전은 2026-10-29부터 주의,
2027-10-29부터 위험이다. 2월 29일 출시 버전은 평년에는 2월 28일을 1년이 되는 날로 본다.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from datetime import date, timedelta

from setron.features.aging.schemas import (
    AgeReason,
    AgeStatus,
    AgeThresholds,
    AgeUnit,
    EquipmentAge,
    PmAge,
    ThresholdError,
    VersionAge,
)
from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

_CALCULATED = (AgeStatus.SAFE, AgeStatus.WARNING, AgeStatus.DANGER)


def threshold_problems(value: object) -> tuple[str, ...]:
    """저장하거나 읽은 기간 기준 값을 검사한다 (FUN-073). 문제가 없으면 빈 tuple이다."""
    if not isinstance(value, Mapping):
        return ("기간 기준 형식이 올바르지 않습니다.",)
    problems = []
    numbers = {}
    for field, name in (("warning", "주의"), ("danger", "위험")):
        number = value.get(field)
        if not isinstance(number, int) or isinstance(number, bool):
            problems.append(f"{name} 기간은 정수로 입력해 주세요.")
        elif number <= 0:
            problems.append(f"{name} 기간은 1 이상이어야 합니다.")
        else:
            numbers[field] = number
    if len(numbers) == 2 and numbers["danger"] <= numbers["warning"]:
        problems.append("위험 기간은 주의 기간보다 길어야 합니다.")
    if value.get("unit") not in {u.value for u in AgeUnit}:
        problems.append("기간 단위는 YEAR, MONTH, DAY 중 하나여야 합니다.")
    return tuple(problems)


def parse_thresholds(value: object) -> AgeThresholds:
    """검사를 통과한 값만 기간 기준으로 바꾼다. 문제가 있으면 :class:`ThresholdError`를 낸다."""
    problems = threshold_problems(value)
    if problems:
        raise ThresholdError(problems)
    return AgeThresholds(value["warning"], value["danger"], AgeUnit(value["unit"]))


def assess_age(released_at: date | None, base_date: date, thresholds: AgeThresholds | None) -> VersionAge:
    """출시일과 조회 날짜로 노후도를 정한다. 기간 기준이 None이면 저장된 기준이 잘못된 것이다."""
    if released_at is None:
        return VersionAge(AgeStatus.UNKNOWN, None, base_date, reason=AgeReason.RELEASE_DATE_MISSING)
    if released_at > base_date:
        return VersionAge(AgeStatus.RELEASED_AFTER_BASE_DATE, released_at, base_date)
    elapsed_days = (base_date - released_at).days
    parts = _elapsed_parts(released_at, base_date)
    if thresholds is None:
        return VersionAge(AgeStatus.SETTING_INVALID, released_at, base_date, elapsed_days, parts)
    if _add(released_at, thresholds.danger, thresholds.unit) <= base_date:
        status = AgeStatus.DANGER
    elif _add(released_at, thresholds.warning, thresholds.unit) <= base_date:
        status = AgeStatus.WARNING
    else:
        status = AgeStatus.SAFE
    return VersionAge(status, released_at, base_date, elapsed_days, parts)


def pm_age(
    pm: PmVersion,
    families: Mapping[int, FamilyReleases],
    thresholds: AgeThresholds | None,
    base_date: date,
) -> PmAge:
    """PM 하나의 버전 노후도. 버전이 없거나 릴리즈에 없으면 출시일을 알 수 없으므로 미확인이다."""
    family = families.get(pm.family_id)
    release = None
    if family is not None:
        if pm.release_version_id is not None:
            release = family.get(pm.release_version_id)
        else:
            release = family.match(pm.version_raw)
    if release is None:
        reason = AgeReason.MISSING_VERSION if not (pm.version_raw or "").strip() else AgeReason.NOT_REGISTERED
        return PmAge(pm, None, VersionAge(AgeStatus.UNKNOWN, None, base_date, reason=reason))
    return PmAge(pm, release, assess_age(release.released_at, base_date, thresholds))


def equipment_age(
    record: EquipmentRecord,
    families: Mapping[int, FamilyReleases],
    thresholds: AgeThresholds | None,
    base_date: date | None = None,
) -> EquipmentAge:
    """설비 한 대의 노후도. 조회 날짜를 주지 않으면 그 현황의 기준 날짜를 쓴다."""
    base_date = base_date or record.base_date
    pms = tuple(pm_age(pm, families, thresholds, base_date) for pm in record.pms)
    calculated = [p for p in pms if p.age.elapsed_days is not None and p.age.status in _CALCULATED]
    oldest = max(calculated, key=lambda p: p.age.elapsed_days) if calculated else None
    return EquipmentAge(record, pms, oldest)


def _add(start: date, amount: int, unit: AgeUnit) -> date:
    if unit is AgeUnit.DAY:
        return start + timedelta(days=amount)
    months = amount * 12 if unit is AgeUnit.YEAR else amount
    return _add_months(start, months)


def _add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def _elapsed_parts(start: date, end: date) -> tuple[int, int, int]:
    months = (end.year - start.year) * 12 + end.month - start.month
    if _add_months(start, months) > end:
        months -= 1
    days = (end - _add_months(start, months)).days
    return months // 12, months % 12, days
