"""버전 통일 참고 예측 자료를 만들고 AI 설명을 요청한다 (명세 BE-020, FUN-040).

계산 (멘토링 3차의 단순 비례)
- 계열마다 기간의 처음과 끝 현황을 설비 1대 × 계열 1건 단위로 비교한다.
- 패치 속도 = 기존 설비 패치 건수 ÷ 기간 일수
- 예상 잔여 기간 = 기간 끝의 N 미만 건수 ÷ 패치 속도. 참고 완료일 = 기간 끝 날짜 + 잔여 기간(올림)

정한 규칙
- 목표 N은 호출하는 쪽이 정해 기간 내내 같은 값으로 쓴다. 목표가 없는 계열은 예측하지 않는다.
- 신규·전입(처음에 없던 건)과 이탈(끝에 없는 건)은 패치 실적에서 뺀다.
- 판단 불가가 남으면 N 미만이 없어도 완료로 단정하지 않는다. N 미만이 있으면 예측은 하되 함께 알린다.
- 자료가 바뀌어도 자동으로 다시 계산하지 않는다. 요청할 때 받은 자료로만 계산한다.
- 숫자는 모두 여기서 계산하고, AI에는 이 결과를 설명만 맡긴다.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any

from setron.features.aggregation.schemas import EquipmentRecord
from setron.features.aggregation.service import Targets, equipment_status
from setron.features.ai.service import analyze
from setron.features.analysis.schemas import AnalysisReport
from setron.features.analysis.service import check_numbers
from setron.features.forecast.schemas import (
    ASSUMPTIONS,
    LIMITS,
    FamilyForecast,
    ForecastReason,
    ForecastResult,
    ForecastStatus,
)
from setron.features.version.schemas import CompareStatus
from setron.features.version.service import FamilyReleases
from setron.infrastructure.ai_client import ChatClient

Units = dict[tuple[int, int], CompareStatus]  # (설비 ID, 계열 ID) → 상태


def forecast(
    records: Iterable[EquipmentRecord],
    families: Mapping[int, FamilyReleases],
    targets: Targets,
    *,
    start: date | None = None,
    end: date | None = None,
) -> ForecastResult:
    """HPA를 제외한 기간 안의 현황으로 계열별 참고 예측을 만든다. 기간이 없으면 일반 설비 자료의 첫 날짜~마지막 날짜다."""
    by_date: dict[date, list[EquipmentRecord]] = {}
    for record in records:
        if record.is_hpa:
            continue
        if (start is None or record.base_date >= start) and (end is None or record.base_date <= end):
            by_date.setdefault(record.base_date, []).append(record)
    if not by_date:
        return ForecastResult(None, None, ())

    first, last = min(by_date), max(by_date)
    before, after = _units(by_date[first], families, targets), _units(by_date[last], families, targets)
    target_families = {family_id for _, family_id in targets}
    family_ids = sorted({f for _, f in before} | {f for _, f in after})
    return ForecastResult(first, last, tuple(
        _family(f, first, last, before, after, f in target_families) for f in family_ids
    ))


def forecast_facts(result: ForecastResult, family_names: Mapping[int, str] | None = None) -> dict[str, Any]:
    """AI에 보낼 예측 자료. 계산 방법·가정·한계를 함께 담는다."""
    names = family_names or {}
    return {
        "분석 기간": {"시작": _iso(result.start_date), "끝": _iso(result.end_date)},
        "계산 방법": "패치 속도 = 기존 설비 패치 건수 ÷ 기간 일수, 예상 잔여 기간 = 남은 N 미만 건수 ÷ 패치 속도",
        "계산 단위": "설비 1대 × 계열 1건",
        "계열별 예측": [
            {
                "계열": names.get(f.family_id, f"계열 {f.family_id}"),
                "상태": f.label,
                "기간 일수": f.period_days,
                "시작 N 미만": f.start_below,
                "기존 설비 패치": f.patched,
                "기존 설비 N 미만으로 내려감": f.regressed,
                "신규·전입": f.new_units,
                "신규·전입 중 N 이상": f.new_compliant,
                "이탈": f.removed,
                "끝 N 이상": f.end_compliant,
                "끝 N 미만": f.end_below,
                "끝 판단 불가": f.end_undetermined,
                "예상 잔여 기간(일)": f.remaining_days,
                "참고 완료일": _iso(f.completion_date),
                "사유": list(f.messages),
            }
            for f in result.families
        ],
        "가정": list(ASSUMPTIONS),
        "한계": list(LIMITS),
    }


def explain_forecast(
    client: ChatClient,
    question: str,
    result: ForecastResult,
    family_names: Mapping[int, str] | None = None,
) -> AnalysisReport:
    """예측 자료로 AI 설명을 요청하고, 답변의 숫자를 자료와 대조한다 (BE-019와 같은 검사)."""
    facts = forecast_facts(result, family_names)
    answer = analyze(client, question, facts)
    return AnalysisReport(answer, facts, check_numbers(answer.data, facts) if answer.ok else ())


# --- 내부 ----------------------------------------------------------------------

def _units(records: Iterable[EquipmentRecord], families: Mapping[int, FamilyReleases], targets: Targets) -> Units:
    units: Units = {}
    for record in records:
        if record.is_hpa:
            continue
        for family in equipment_status(record, families, targets).families:
            units[(record.equipment_id, family.family_id)] = family.status
    return units


def _family(family_id: int, first: date, last: date, before: Units, after: Units, has_target: bool) -> FamilyForecast:
    start = {e: s for (e, f), s in before.items() if f == family_id}
    end = {e: s for (e, f), s in after.items() if f == family_id}
    kept = start.keys() & end.keys()
    new = end.keys() - start.keys()
    below, above = CompareStatus.BELOW, CompareStatus.AT_OR_ABOVE
    counts = dict(
        family_id=family_id, start_date=first, end_date=last,
        start_below=sum(s is below for s in start.values()),
        end_compliant=sum(s is above for s in end.values()),
        end_below=sum(s is below for s in end.values()),
        end_undetermined=sum(s is CompareStatus.UNDETERMINED for s in end.values()),
        patched=sum(start[e] is below and end[e] is above for e in kept),
        regressed=sum(start[e] is above and end[e] is below for e in kept),
        new_units=len(new),
        new_compliant=sum(end[e] is above for e in new),
        removed=len(start.keys() - end.keys()),
    )

    def result(status, *reasons, days=None):
        completion = last + timedelta(days=math.ceil(days)) if days is not None else None
        return FamilyForecast(**counts, status=status, remaining_days=days, completion_date=completion,
                              reasons=tuple(reasons))

    undetermined = (ForecastReason.UNDETERMINED_LEFT,) if counts["end_undetermined"] else ()
    if not has_target:
        return result(ForecastStatus.NOT_ESTIMABLE, ForecastReason.TARGET_NOT_SET)
    if counts["end_below"] == 0:
        if undetermined:
            return result(ForecastStatus.NOT_CONFIRMED, *undetermined)
        return result(ForecastStatus.COMPLETE, days=0.0)
    if first == last:
        return result(ForecastStatus.NOT_ESTIMABLE, ForecastReason.SINGLE_DATE, *undetermined)
    if not start:
        return result(ForecastStatus.NOT_ESTIMABLE, ForecastReason.NOT_AT_START, *undetermined)
    if counts["patched"] == 0:
        return result(ForecastStatus.NOT_ESTIMABLE, ForecastReason.NO_PATCH, *undetermined)
    days = round(counts["end_below"] * (last - first).days / counts["patched"], 1)
    return result(ForecastStatus.ESTIMATED, *undetermined, days=days)


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
