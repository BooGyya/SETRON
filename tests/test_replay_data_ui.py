"""Replay 프론트 읽기 모델은 합성 관측만 사용한다. 운영 DB·AI 호출이나 저장 없음."""

from dataclasses import replace
from datetime import date

import pytest

from setron.features.risk.schemas import RiskStatus
from setron.ui.dashboard_data import DashboardSource, GroupBinding
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.query_state import QuerySelection, QueryState, TargetN, VersionFilter
from setron.ui.replay_data import ReplayRequest, load_replay, replay_detail
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


def test_only_actual_stored_dates_are_sorted_in_period_without_neighbor_copy():
    source = demo_dashboard_source()
    value = load_replay(source, request(source, start=date(2026, 6, 1), end=date(2026, 8, 31)))
    assert [f.day for f in value.frames] == [date(2026, m, 21) for m in (6, 7, 8)]
    assert value.can_replay
    for frame in value.frames:
        assert all(t.observation_date == frame.day for t in frame.tiles)
        assert all(r.base_date == frame.day for r in frame.snapshot.selected_records)
    assert len(value.frames[1].tiles) < len(value.frames[0].tiles)  # 7월의 관측 공백은 채우지 않는다.
    empty = load_replay(source, request(source, start=date(2025, 1, 1), end=date(2025, 12, 31)))
    assert empty.frames == () and not empty.can_replay


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


def test_hpa_from_another_date_never_appears_in_historical_frame():
    source = demo_dashboard_source()
    source = with_records(source, tuple(r for r in source.records if not r.is_hpa or r.base_date == source.dates[0]))
    value = load_replay(source, request(source))
    assert value.frames[0].snapshot.summary.hpa_excluded == 3
    assert value.frames[-1].snapshot.summary.hpa_excluded == 0
    assert not any(t.goal.value.startswith("HPA") for f in value.frames for t in f.tiles)
    assert not value.frames[-1].snapshot.hpa_lookup_records


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
