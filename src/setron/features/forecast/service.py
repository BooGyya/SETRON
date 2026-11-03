"""버전 통일 참고 예측 자료를 만들고 AI 설명을 요청한다 (명세 BE-020, FUN-040).

계산 (멘토링 3차의 단순 비례, 데이터 파트 `ai/svhm/core/forecast.py`와 같은 규칙)
- 계열마다 기간 안의 저장 날짜별로 N 이상·N 미만·판단 불가 대수를 센다 (설비 1대 × 계열 1건, HPA 제외).
- 증가 속도 = (끝 날짜 N 이상 대수 − 처음 날짜 N 이상 대수) ÷ 기간 일수. 신규·전입 설비도 포함한다.
- 예상 잔여 기간 = 끝 날짜 N 미만 대수 ÷ 증가 속도 (일 단위 반올림). 참고 완료일 = 끝 날짜 + 잔여 기간

정한 규칙
- 목표 N은 호출하는 쪽이 정해 기간 내내 같은 값으로 쓴다. 목표가 없는 계열은 예측하지 않는다.
- 이 계열의 날짜가 하나뿐이거나 N 이상 대수가 늘지 않았으면 예측하지 않는다.
- 판단 불가가 남으면 N 미만이 없어도 완료로 단정하지 않는다. N 미만이 있으면 예측은 하되 함께 알린다.
- 자료가 바뀌어도 자동으로 다시 계산하지 않는다. 요청할 때 받은 자료로만 계산한다.
- 숫자는 모두 여기서 계산하고, AI에는 이 결과를 설명만 맡긴다.
"""

from __future__ import annotations

from collections import Counter
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


def forecast(
    records: Iterable[EquipmentRecord],
    families: Mapping[int, FamilyReleases],
    targets: Targets,
    *,
    start: date | None = None,
    end: date | None = None,
) -> ForecastResult:
    """기간 안의 현황으로 계열별 참고 예측을 만든다. 기간을 주지 않으면 자료의 첫 날짜~마지막 날짜다."""
    by_date: dict[date, list[EquipmentRecord]] = {}
    for record in records:
        if (start is None or record.base_date >= start) and (end is None or record.base_date <= end):
            by_date.setdefault(record.base_date, []).append(record)
    if not by_date:
        return ForecastResult(None, None, ())

    counts = {day: _counts(day_records, families, targets) for day, day_records in sorted(by_date.items())}
    target_families = {family_id for _, family_id in targets}
    family_ids = sorted({f for day_counts in counts.values() for f in day_counts})
    return ForecastResult(min(by_date), max(by_date), tuple(
        _family(f, {day: c[f] for day, c in counts.items() if f in c}, f in target_families) for f in family_ids
    ))


def forecast_facts(result: ForecastResult, family_names: Mapping[int, str] | None = None) -> dict[str, Any]:
    """AI에 보낼 예측 자료. 계산 방법·가정·한계를 함께 담는다."""
    names = family_names or {}
    return {
        "분석 기간": {"시작": _iso(result.start_date), "끝": _iso(result.end_date)},
        "계산 방법": "증가 속도 = (끝 N 이상 대수 − 처음 N 이상 대수) ÷ 기간 일수, "
                     "예상 잔여 기간 = 끝 N 미만 대수 ÷ 증가 속도 (일 단위 반올림)",
        "계산 단위": "설비 1대 × 계열 1건",
        "계열별 예측": [
            {
                "계열": names.get(f.family_id, f"계열 {f.family_id}"),
                "상태": f.label,
                "표시": f.note,
                "데이터 수(장)": f.points,
                "기간 일수": f.period_days,
                "처음 N 이상": f.start_applied,
                "끝 N 이상": f.end_applied,
                "N 이상 증가": f.gained,
                "하루 증가(대/일)": None if f.rate_per_day is None else round(f.rate_per_day, 2),
                "끝 N 미만(남은 대수)": f.end_below,
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

def _counts(records: Iterable[EquipmentRecord], families: Mapping[int, FamilyReleases],
            targets: Targets) -> dict[int, Counter]:
    """한 날짜의 계열별 상태 대수 (설비 1대 × 계열 1건, HPA 제외)."""
    result: dict[int, Counter] = {}
    for record in records:
        if record.is_hpa:
            continue
        for family in equipment_status(record, families, targets).families:
            result.setdefault(family.family_id, Counter())[family.status] += 1
    return result


def _family(family_id: int, counts: Mapping[date, Counter], has_target: bool) -> FamilyForecast:
    first, last = min(counts), max(counts)
    above, below = CompareStatus.AT_OR_ABOVE, CompareStatus.BELOW
    end = counts[last]
    values = dict(family_id=family_id, start_date=first, end_date=last, points=len(counts),
                  start_applied=counts[first][above], end_applied=end[above], end_below=end[below],
                  end_undetermined=end[CompareStatus.UNDETERMINED])

    def result(status, *reasons, rate=None, days=None):
        completion = last + timedelta(days=days) if days is not None else None
        return FamilyForecast(**values, status=status, rate_per_day=rate, remaining_days=days,
                              completion_date=completion, reasons=tuple(reasons))

    undetermined = (ForecastReason.UNDETERMINED_LEFT,) if values["end_undetermined"] else ()
    if not has_target:
        return result(ForecastStatus.NOT_ESTIMABLE, ForecastReason.TARGET_NOT_SET)
    if values["end_below"] == 0:
        if undetermined:
            return result(ForecastStatus.NOT_CONFIRMED, *undetermined)
        return result(ForecastStatus.COMPLETE, days=0)
    if first == last:
        return result(ForecastStatus.NOT_ESTIMABLE, ForecastReason.SINGLE_DATE, *undetermined)
    gained = values["end_applied"] - values["start_applied"]
    if gained <= 0:
        return result(ForecastStatus.NOT_ESTIMABLE, ForecastReason.NO_PROGRESS, *undetermined)
    rate = gained / (last - first).days
    return result(ForecastStatus.ESTIMATED, *undetermined, rate=rate, days=round(values["end_below"] / rate))


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
