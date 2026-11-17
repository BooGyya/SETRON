"""노선도 그래프 자료(날짜별 실제 점·예측 점·전체 선)를 검증한다 (BE-020, 화면 10 "달성률 추이 및 통일 예측")."""

from datetime import date

import pytest

from setron.features.forecast.schemas import ForecastPoint, ForecastStatus
from setron.features.forecast.service import forecast, forecast_line
from test_forecast_service import DAY1, DAY2, F_O2, F_O3, FAMILIES, RECORDS, TARGETS, by_family


def points(line):
    return [(p.day, p.applied, p.below, p.projected) for p in line]


def test_history_keeps_actual_counts_per_observed_date():
    o2 = by_family(forecast(RECORDS, FAMILIES, TARGETS))[F_O2]

    assert points(o2.history) == [(DAY1, 1, 3, False), (DAY2, 2, 3, False)]
    assert o2.history[0].rate == 0.25 and o2.history[1].rate == 0.4


def test_line_continues_monthly_to_completion_date_with_projected_points():
    """O2: 하루 1/30대 증가, 남은 3대 → 90일 뒤 2026-12-30. 30일마다 한 점, 마지막 점은 N 미만 0."""
    o2 = by_family(forecast(RECORDS, FAMILIES, TARGETS))[F_O2]

    line = forecast_line(o2)

    assert o2.status is ForecastStatus.ESTIMATED and o2.completion_date == date(2026, 12, 30)
    assert points(line) == [
        (DAY1, 1, 3, False), (DAY2, 2, 3, False),
        (date(2026, 10, 31), 3, 2, True), (date(2026, 11, 30), 4, 1, True), (date(2026, 12, 30), 5, 0, True),
    ]
    assert line[-1].rate == 1.0


def test_line_step_can_be_changed_and_must_be_positive():
    o2 = by_family(forecast(RECORDS, FAMILIES, TARGETS))[F_O2]

    assert [p.day for p in forecast_line(o2, step_days=45) if p.projected] == [date(2026, 11, 15), date(2026, 12, 30)]
    with pytest.raises(ValueError, match="1일 이상"):
        forecast_line(o2, step_days=0)


def test_family_without_estimate_has_only_actual_points():
    o3 = by_family(forecast(RECORDS, FAMILIES, TARGETS))[F_O3]

    assert o3.status is ForecastStatus.COMPLETE
    assert all(not p.projected for p in forecast_line(o3)) and len(forecast_line(o3)) == 2


def test_overall_line_sums_families_and_excludes_hpa():
    """전체 선: O2 + O3 합계. HPA(9번)는 들어가지 않는다."""
    result = forecast(RECORDS, FAMILIES, TARGETS)

    assert points(result.overall) == [(DAY1, 2, 3, False), (DAY2, 3, 3, False)]


def test_empty_period_has_no_lines():
    result = forecast((), FAMILIES, TARGETS)

    assert result.overall == () and result.families == ()


def test_rate_without_determined_units_is_none():
    assert ForecastPoint(DAY1, 0, 0, 2).rate is None
