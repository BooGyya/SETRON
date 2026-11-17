"""QA Q-01: 기준일 조회는 설비마다 기준일 이전 가장 최근 관측을 쓴다.

현황표는 사업장·시트 단위로 따로 들어오므로, 가장 최근 날짜 하나만 고르면
그날 넣지 않은 설비가 화면에서 사라진다.
"""

from dataclasses import replace
from datetime import date

import pytest

from setron.ui.dashboard_data import NoObservationBefore
from setron.ui.equipment_data import equipment_data
from setron.ui.query_state import QueryState

from test_hpa_analysis_dates import FIRST, LAST, record, source


def snapshot_of(data, as_of=None):
    state = QueryState(data.catalog)
    if as_of is not None:
        state.set_as_of(as_of)
    return data.snapshot(state.apply())


def test_equipment_missing_on_latest_date_keeps_its_last_observation():
    """1번은 두 날짜 모두, 2번은 첫 날짜에만 관측 → 최신 조회에 두 대 모두 나온다."""
    data = source(record(1, FIRST), record(1, LAST), record(2, FIRST))

    snapshot = snapshot_of(data)

    assert snapshot.base_date == LAST                       # 화면 기준일은 가장 최근 일반 관측일
    assert snapshot.summary.equipment_count == 2            # 예전에는 1대
    assert {r.equipment_id: r.base_date for r in snapshot.selected_records} == {1: LAST, 2: FIRST}


def test_as_of_date_uses_observations_until_that_day_only():
    data = source(record(1, FIRST), record(1, LAST), record(2, LAST))

    earlier = snapshot_of(data, FIRST)

    assert earlier.base_date == FIRST
    assert [r.equipment_id for r in earlier.selected_records] == [1]   # 2번은 LAST에 처음 관측
    with pytest.raises(NoObservationBefore):
        snapshot_of(data, date(2026, 1, 1))


def test_current_records_picks_latest_per_equipment_including_hpa():
    data = source(record(1, FIRST), record(1, LAST), record(2, FIRST), record(9, FIRST, hpa=True))

    latest = {r.equipment_id: r.base_date for r in data.current_records()}

    assert latest == {1: LAST, 2: FIRST, 9: FIRST}
    assert {r.equipment_id for r in data.current_records(FIRST)} == {1, 2, 9}


def test_equipment_list_uses_same_carried_observations():
    """설비 목록도 대시보드와 같은 설비별 최신 관측으로 만든다 (예전에는 '같은 기준일의 전체 SW 관측이 없습니다' 오류)."""
    data = source(record(1, FIRST), record(1, LAST), record(2, FIRST))

    listing = equipment_data(snapshot_of(data), data)

    assert listing.equipment_ids == (1, 2)
