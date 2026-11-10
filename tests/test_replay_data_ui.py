"""Replay 프론트 읽기 모델은 합성 관측만 사용한다. 운영 DB·AI 호출이나 저장 없음."""

from dataclasses import replace
from datetime import date

import pytest

from setron.features.risk.schemas import RiskStatus
from setron.ui.dashboard_data import DashboardSource, GroupBinding
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.query_state import QuerySelection, QueryState, TargetN, VersionFilter
from setron.ui.replay_data import (
    ReplayRequest, group_completion, group_view, interval_days, load_replay, replay_detail,
)
from setron.ui.target_data import initial_targets


def request(source, *, start=None, end=None, selection=None, targets=None):
    state = QueryState(source.catalog)
    state.set_draft(selection or QuerySelection())
    state.set_target_draft(targets)
    return ReplayRequest(start or source.dates[0], end or source.dates[-1], state.apply())


def with_records(source, records):
    result = DashboardSource(catalog=source.catalog, bindings=tuple(GroupBinding(k, v) for k, v in source.bindings.items()),
                             records=records, locations=source.locations, families=source.families,
                             targets=source.targets, risks=source.risks, source_label=source.source_label)
    return result


def test_frames_are_actual_stored_dates_and_carry_latest_observation_like_dashboard():
    """시점은 실제 저장 날짜만. 그날 없는 설비는 이전 마지막 관측(실제 관측일 유지)으로 센다 (QA R-01)."""
    source = demo_dashboard_source()
    value = load_replay(source, request(source, start=date(2026, 6, 1), end=date(2026, 8, 31)))
    assert [f.day for f in value.frames] == [date(2026, m, 21) for m in (6, 7, 8)]
    assert value.can_replay
    for frame in value.frames:
        assert all(t.observation_date <= frame.day for t in frame.tiles)
        assert all(r.base_date <= frame.day for r in frame.snapshot.selected_records)
        assert frame.observed == sum(r.base_date == frame.day for r in frame.snapshot.selected_records if not r.is_hpa)
    july = value.frames[1]
    # 7월에 저장되지 않은 설비가 사라지지 않고 6월 관측으로 이어진다.
    assert july.carried > 0 and len(july.tiles) >= len(value.frames[0].tiles)
    assert any(t.observation_date == date(2026, 6, 21) for t in july.tiles)
    empty = load_replay(source, request(source, start=date(2025, 1, 1), end=date(2025, 12, 31)))
    assert empty.frames == () and not empty.can_replay


def test_partially_saved_last_date_matches_dashboard_instead_of_dropping_to_one_equipment():
    """한 설비만 저장한 날짜도 대시보드와 같은 달성률·대수가 나온다 (QA R-01 재현: 11-01에 1대만 저장)."""
    source = demo_dashboard_source()
    last = next(r for r in source.records if r.base_date == source.dates[-1] and not r.is_hpa and r.pms)
    partial_day = date(2026, 11, 1)
    source = with_records(source, (*source.records, replace(last, base_date=partial_day)))
    value = load_replay(source, request(source))
    frame = value.frames[-1]
    assert frame.day == partial_day and frame.observed == 1 and frame.carried > 0
    query = request(source).query
    dashboard = source.snapshot(replace(query, selection=replace(query.selection, as_of=partial_day)))
    assert frame.snapshot.summary.equipment_count == dashboard.summary.equipment_count
    assert frame.snapshot.summary.rate == dashboard.summary.rate is not None
    assert (frame.snapshot.summary.compliant, frame.snapshot.summary.below) == (dashboard.summary.compliant,
                                                                               dashboard.summary.below)


def test_version_options_sort_ignoring_case_but_keep_raw_spaces_separate():
    source = demo_dashboard_source()
    day = source.dates[-1]
    targets = [r for r in source.records if r.base_date == day and not r.is_hpa and r.pms][:2]
    renamed = {targets[0].equipment_id: "V1.0.1", targets[1].equipment_id: "v1.0.1 "}  # 대문자 · 끝 공백 원문
    records = tuple(replace(r, pms=(replace(r.pms[0], version_raw=renamed[r.equipment_id]), *r.pms[1:]))
                    if r.base_date == day and r.equipment_id in renamed else r for r in source.records)
    value = load_replay(with_records(source, records), request(source))
    plain = [v for v in value.options if v is not None]
    assert plain == sorted(plain, key=lambda v: (v.strip().casefold(), v))  # 대문자가 맨 위로 몰리지 않는다
    assert {"V1.0.1", "v1.0.1", "v1.0.1 "} <= set(plain)  # 원문은 합치지 않는다
    assert plain.index("v1.0.1") < plain.index("v1.0.1 ") and abs(plain.index("V1.0.1") - plain.index("v1.0.1")) <= 2


def test_interval_days_keep_month_end_and_always_end_on_period_end():
    assert interval_days(date(2026, 1, 31), date(2026, 4, 30), "month") == (
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30))
    assert interval_days(date(2026, 11, 30), date(2027, 3, 1), "quarter") == (
        date(2026, 11, 30), date(2027, 2, 28), date(2027, 3, 1))
    assert interval_days(date(2026, 5, 1), date(2026, 5, 20), "week")[-1] == date(2026, 5, 20)
    assert interval_days(date(2026, 5, 1), date(2026, 5, 1), "week") == (date(2026, 5, 1),)


def test_interval_frames_reuse_last_saved_state_and_seed_covers_start_between_saved_dates():
    """요청 2-1: 간격 시점은 그 날짜 전 마지막 저장일의 상태. 시작 날짜가 저장일 사이면 그 전 저장일 상태로 시작한다."""
    source = demo_dashboard_source()
    value = load_replay(source, request(source, start=date(2026, 6, 1), end=date(2026, 8, 31)))
    assert value.seed is not None and value.seed.snapshot.base_date == date(2026, 5, 21)
    weekly = value.frames_for("week")
    assert weekly[0].day == date(2026, 6, 1) and weekly[0].snapshot is value.seed.snapshot
    assert weekly[-1].day == date(2026, 8, 31) and weekly[-1].snapshot is value.frames[-1].snapshot
    assert value.frames_for(None) == value.frames
    # 첫 저장일 전 날짜는 보여 줄 상태가 없으므로 뺀다.
    early = load_replay(source, request(source, start=date(2026, 5, 1), end=date(2026, 6, 30)))
    assert early.seed is None and early.frames_for("week")[0].day == date(2026, 5, 22)


def test_group_view_and_completion_count_selected_groups_only():
    """요청 2-2·3-2: 고른 설비군만의 집계와 설비군별 완료 설비 수."""
    source = demo_dashboard_source()
    value = load_replay(source, request(source))
    frame = value.frames[-1]
    whole, observed, carried = group_view(value, frame, None)
    assert whole is frame.snapshot.summary and (observed, carried) == (frame.observed, frame.carried)
    assert group_view(value, frame, set()) == (None, 0, 0)
    c3 = next(g for g in frame.snapshot.groups if g.group_keys == ("c3",))
    only, *_ = group_view(value, frame, {"c3"})
    assert (only.compliant, only.below, only.undetermined) == (c3.summary.compliant, c3.summary.below, c3.summary.undetermined)
    both, *_ = group_view(value, frame, {"c3", "c4"})
    assert both.compliant >= only.compliant
    done, judged = group_completion(c3)
    assert 0 <= done <= judged <= len(c3.equipment_ids)


def test_single_actual_date_can_be_viewed_but_cannot_play():
    source = demo_dashboard_source()
    value = load_replay(source, request(source, start=source.dates[0], end=source.dates[0]))
    assert len(value.frames) == 1 and value.frames[0].tiles and not value.can_replay


def test_unset_targets_still_allow_reading_unknown_frames():
    source = demo_dashboard_source()
    source.targets.clear()
    value = load_replay(source, request(source, targets=initial_targets(source)))
    assert len(value.frames) == 6 and value.frames[0].tiles
    assert all(frame.snapshot.summary.rate is None for frame in value.frames)
    assert value.source.targets == {} and source.targets == {}


def test_model_applicability_invalidates_requested_n_without_editing_shared_selection():
    source = demo_dashboard_source()
    selected = initial_targets(source)
    before = dict(source.targets)
    source.applicability = {}  # 합성 읽기 공급자가 모델 적용 근거 없음으로 전달.
    value = load_replay(source, request(source, targets=selected))
    assert value.request.query.targets == selected and source.targets == before
    assert value.source.targets == {}
    assert all(frame.snapshot.summary.rate is None for frame in value.frames)
    assert "적용 근거 재확인" in value.frames[0].snapshot.source_label


def test_same_scope_and_explicit_target_ids_are_used_at_every_date():
    source = demo_dashboard_source()
    selection = QuerySelection(site_key="hwaseong", group_key="c3")
    targets = tuple(TargetN(mid, fid, rid) for (mid, fid), rid in sorted(source.targets.items()))
    value = load_replay(source, request(source, selection=selection, targets=targets))
    assert all(f.snapshot.query.targets == targets for f in value.frames)
    assert all(f.snapshot.query.selection.site_key == "hwaseong" for f in value.frames)
    assert all(f.snapshot.query.selection.group_key == "c3" for f in value.frames)
    assert all(t.site_key == "hwaseong" for f in value.frames for t in f.tiles)
    assert all(f.snapshot.target_values == targets for f in value.frames)


def test_multiversion_filter_is_or_in_each_frame_and_does_not_edit_n():
    source = demo_dashboard_source()
    original = dict(source.targets)
    selected = VersionFilter(("v1.0.1",), True)
    value = load_replay(source, request(source, selection=QuerySelection(versions=selected)))
    assert source.targets == original and value.source.targets == original
    assert "v1.0.1" in value.options and "v1.0.2" in value.options and None in value.options
    for frame in value.frames:
        for record in frame.snapshot.selected_records:
            assert record.is_hpa or not record.pms or any(p.version_raw in (None, "v1.0.1") for p in record.pms)


def test_zero_matching_records_keep_real_dates_without_inventing_rate_or_equipment():
    source = demo_dashboard_source()
    value = load_replay(source, request(source, selection=QuerySelection(versions=VersionFilter(("not-stored",)))))
    assert len(value.frames) == 6 and not value.can_replay
    assert all(f.snapshot.summary.rate is None for f in value.frames)
    assert all(not any(not r.is_hpa for r in f.snapshot.selected_records) for f in value.frames)


def test_later_hpa_never_appears_in_earlier_frame_and_earlier_hpa_carries_like_dashboard():
    source = demo_dashboard_source()
    early = with_records(source, tuple(r for r in source.records if not r.is_hpa or r.base_date == source.dates[0]))
    value = load_replay(early, request(early))
    assert value.frames[0].snapshot.summary.hpa_excluded == 3
    assert value.frames[-1].snapshot.summary.hpa_excluded == 3  # 대시보드처럼 마지막 관측을 이어받아 제외 대수로 센다
    assert not any(t.goal.value.startswith("HPA") for f in value.frames for t in f.tiles)
    assert not value.frames[-1].snapshot.hpa_lookup_records
    late = with_records(source, tuple(r for r in source.records if not r.is_hpa or r.base_date == source.dates[-1]))
    value = load_replay(late, request(late))
    assert value.frames[0].snapshot.summary.hpa_excluded == 0  # 나중에 저장된 HPA를 과거 시점에 넣지 않는다
    assert not value.frames[0].snapshot.hpa_lookup_records


def test_loaded_frames_and_detail_are_frozen_against_later_target_or_policy_changes():
    source = demo_dashboard_source()
    calls, policy = [], {"risk": RiskStatus.RISK}
    source.risk_evaluator = lambda record: calls.append(record.equipment_id) or policy["risk"]
    original_records, original_targets = source.records, dict(source.targets)
    value = load_replay(source, request(source))
    baseline = tuple((f.snapshot.summary, f.tiles) for f in value.frames)
    count = len(calls)
    source.targets.clear()
    source.risks.clear()
    policy["risk"] = RiskStatus.NO_RISK
    frame = value.frames[0]
    tile = next(t for t in frame.tiles if not t.goal.value.startswith("HPA"))
    detail, rows, evidence = replay_detail(value, frame, tile.equipment_id)
    assert detail.whole.risk is RiskStatus.RISK and len(calls) == count
    assert value.source.targets == original_targets and source.records is original_records
    assert tuple((f.snapshot.summary, f.tiles) for f in value.frames) == baseline
    assert rows and str(frame.day) in str(evidence)


def test_multisw_equipment_keeps_one_physical_id_and_full_original_in_detail():
    source = demo_dashboard_source()
    value = load_replay(source, request(source, selection=QuerySelection(group_key="c3")))
    frame = value.frames[0]
    assert len({t.equipment_id for t in frame.tiles}) == len(frame.tiles)
    detail, rows, _ = replay_detail(value, frame, 1)
    assert len(detail.record.pms) == 3 and len(rows) == 3
    assert len(detail.selected.families) < len(detail.whole.families)
    original = next(r for r in source.records if r.equipment_id == 1 and r.base_date == frame.day)
    assert detail.record is original


@pytest.mark.parametrize("start,end", [(date(2026, 11, 1), date(2026, 1, 1)), ("2026-01-01", date(2026, 11, 1))])
def test_invalid_date_request_is_rejected(start, end):
    source = demo_dashboard_source()
    with pytest.raises(ValueError):
        ReplayRequest(start, end, QueryState(source.catalog).apply())


def test_unknown_scope_is_rejected_without_modifying_source():
    source = demo_dashboard_source()
    query = QueryState(source.catalog).apply()
    invalid = replace(query, selection=replace(query.selection, site_key="unregistered"))
    original = source.records, dict(source.targets), dict(source.locations)
    with pytest.raises(ValueError):
        load_replay(source, ReplayRequest(source.dates[0], source.dates[-1], invalid))
    assert (source.records, source.targets, source.locations) == original
