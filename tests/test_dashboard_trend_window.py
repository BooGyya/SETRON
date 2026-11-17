"""추이 기간·간격 선택은 저장된 관측과 현재 기준 설비 집합을 바꾸지 않는다."""

from dataclasses import fields, replace
from datetime import date

import pytest

from setron.features.aggregation.schemas import Summary
from setron.ui.dashboard_data import GroupTrend, TrendPoint, trend_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.observation_interval import interval_days
from setron.ui.query_state import QueryState


@pytest.fixture
def snapshot():
    source = demo_dashboard_source()
    return source.snapshot(QueryState(source.catalog).apply())


def point(day, compliant=1, below=0, *, unknown=0, missing=0, source_day=None):
    return TrendPoint(day, Summary(base_date=day, compliant=compliant, below=below,
                                   undetermined=unknown, equipment_count=compliant + below + unknown),
                      missing, source_day)


def with_points(snapshot, points):
    return replace(snapshot, observations=points, trends=(GroupTrend("o2", "O2", points),))


def test_saved_days_filter_inclusively_and_preserve_snapshot_cohort_and_targets(snapshot):
    original_observations, original_trends = snapshot.observations, snapshot.trends
    start, end = original_observations[1].day, original_observations[3].day
    result = trend_window(snapshot, start, end)

    assert result.observations == original_observations[1:4]
    assert all(selected is original for selected, original in zip(result.observations, original_observations[1:4]))
    assert [group.points for group in result.trends] == [group.points[1:4] for group in original_trends]
    for field in fields(snapshot):
        if field.name not in {"observations", "trends"}:
            assert getattr(result, field.name) is getattr(snapshot, field.name)
    assert snapshot.observations is original_observations and snapshot.trends is original_trends


def test_month_interval_clamps_calendar_day_and_preserves_actual_source_date(snapshot):
    points = (point(date(2026, 1, 31), 0, 1, missing=2),
              point(date(2026, 3, 10), 1, 1), point(date(2026, 4, 5)))
    original = with_points(snapshot, points)
    result = trend_window(original, date(2026, 1, 31), date(2026, 4, 30), "month")

    assert [p.day for p in result.observations] == [date(2026, 1, 31), date(2026, 2, 28),
                                                  date(2026, 3, 31), date(2026, 4, 30)]
    assert [p.source_day for p in result.observations] == [points[i].day for i in (0, 0, 1, 2)]
    assert [p.summary.rate for p in result.observations] == [0, 0, 0.5, 1]
    assert [p.missing_equipment for p in result.observations] == [2, 2, 0, 0]
    assert all(p.summary is points[i].summary for p, i in zip(result.observations, (0, 0, 1, 2)))
    assert result.trends[0].points == result.observations
    assert original.observations == points and all(p.source_day is None for p in points)


def test_interval_reuses_prestart_record_and_always_includes_requested_end(snapshot):
    points = (point(date(2026, 5, 21)), point(date(2026, 7, 21), 0, 1))
    original = with_points(snapshot, points)
    start, end = date(2026, 6, 1), date(2026, 8, 31)
    saved = trend_window(original, start, end)
    sampled = trend_window(original, start, end, "quarter")

    assert saved.observations == points[1:]
    assert [p.day for p in sampled.observations] == [start, end]
    assert [p.source_day for p in sampled.observations] == [p.day for p in points]
    assert [p.summary.rate for p in sampled.observations] == [1, 0]
    assert interval_days(start, end, "quarter") == (start, end)


def test_interval_skips_dates_before_first_record_without_looking_ahead(snapshot):
    points = (point(date(2026, 5, 21), 0, 1), point(date(2026, 5, 29)))
    result = trend_window(with_points(snapshot, points), date(2026, 5, 1), date(2026, 6, 1), "week")

    assert [p.day for p in result.observations] == [date(2026, 5, 22), date(2026, 5, 29), date(2026, 6, 1)]
    assert [p.source_day for p in result.observations] == [points[i].day for i in (0, 1, 1)]
    assert [p.summary.rate for p in result.observations] == [0, 1, 1]


@pytest.mark.parametrize("unknown", (0, 1))
def test_interval_keeps_latest_unavailable_rate_and_each_groups_missing_counts(snapshot, unknown):
    points = (point(date(2026, 5, 1), 1, 1, missing=1),
              point(date(2026, 5, 8), 0, 0, unknown=unknown, missing=3 - unknown),
              point(date(2026, 5, 15)))
    other = (points[0], point(points[1].day), point(points[2].day, 0, 0, unknown=1))
    original = replace(with_points(snapshot, points), trends=(GroupTrend("o2", "O2", points),
                                                            GroupTrend("o3", "O3", other)))
    result = trend_window(original, date(2026, 5, 1), date(2026, 5, 22), "week")

    assert [p.summary.rate for p in result.observations] == [0.5, None, 1, 1]
    assert [p.missing_equipment for p in result.trends[0].points] == [1, 3 - unknown, 0, 0]
    assert [p.summary.rate for p in result.trends[1].points] == [0.5, 1, None, None]
    assert result.trends[1].points[-1].summary is other[-1].summary


@pytest.mark.parametrize("step", (None, "week"))
def test_empty_period_has_no_fabricated_observation(snapshot, step):
    result = trend_window(snapshot, date(2025, 1, 1), date(2025, 2, 1), step)
    assert result.observations == ()
    assert all(group.points == () for group in result.trends)
    assert result.summary is snapshot.summary


@pytest.mark.parametrize("start,end,step", ((date(2026, 6, 1), date(2026, 5, 1), None),
                                             (date(2026, 5, 1), date(2026, 6, 1), "day"),
                                             (date(2026, 5, 1), date(2026, 6, 1), {})))
def test_invalid_period_or_interval_is_rejected(snapshot, start, end, step):
    with pytest.raises(ValueError):
        trend_window(snapshot, start, end, step)
