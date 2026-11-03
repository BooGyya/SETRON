"""HPA 관측일이 일반 설비의 대시보드 기준일·추이를 바꾸지 않는지 검증한다 (BE-024)."""

from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding
from setron.ui.query_state import GroupOption, QueryCatalog, QueryState, SiteOption


FIRST, LAST, HPA_LAST = date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)


def record(equipment_id, day, *, hpa=False):
    """확정 모델과 SW가 있는 합성 일반 설비 또는 HPA 관측을 만든다.

    param
    - equipment_id, day: 합성 설비 ID와 관측일.
    - hpa: True이면 공통 분석 제외 관측을 생성.

    return
    - 시험 전용 EquipmentRecord.
    """
    return EquipmentRecord(equipment_id, f"TEST-{equipment_id}", day, "시험 사업장", None,
                           "HPA" if hpa else "M-O2", (PmVersion(None, 9 if hpa else 1, 10, "v1"),),
                           is_hpa=hpa)


def source(*records):
    """DB 없이 대시보드 날짜 선택을 검사할 최소 공급자를 만든다.

    param
    - records: 일반 설비와 HPA의 합성 관측 목록.

    return
    - 합성 모델·사업장 대응과 고정 목표 N을 가진 DashboardSource.
    """
    catalog = QueryCatalog(sites=(SiteOption("site", "시험 사업장"),),
                           groups=(GroupOption("o2", "O2", "OXIDE"),))
    families = {10: FamilyReleases(10, (ReleaseVersion(101, 10, "v1", 1),))}
    return DashboardSource(catalog=catalog, bindings=(GroupBinding("o2", (1,)),), records=records,
                           locations={r.equipment_id: EquipmentLocation("site") for r in records},
                           families=families, targets={(1, 10): 101}, risks={}, source_label="시험 자료")


def test_hpa_dates_do_not_replace_analysis_date_or_add_trend_points():
    """최신 HPA 관측은 보존하되 일반 설비 기준일·관측 추이에 포함하지 않는다."""
    data = source(record(1, FIRST), record(1, LAST), record(9, date(2026, 8, 1), hpa=True),
                  record(9, date(2026, 9, 15), hpa=True), record(9, LAST, hpa=True),
                  record(9, HPA_LAST, hpa=True))

    snapshot = data.snapshot(QueryState(data.catalog).apply())

    assert snapshot.base_date == LAST
    assert (snapshot.summary.equipment_count, snapshot.summary.hpa_excluded) == (1, 1)
    assert snapshot.summary.rate == 1.0
    assert snapshot.equipment_ids == (1, 9)
    assert tuple(point.day for point in snapshot.observations) == (FIRST, LAST)
    assert all(point.missing_equipment == 0 for point in snapshot.observations)
    assert all(tuple(point.day for point in trend.points) == (FIRST, LAST) for trend in snapshot.trends)
    assert any(r.is_hpa and r.base_date == HPA_LAST for r in data.records)


def test_hpa_only_source_keeps_latest_observation_with_zero_analysis_count():
    """HPA만 있어도 최신 자료 조회는 가능하며 일반 분석 추이는 생성하지 않는다."""
    data = source(record(9, FIRST, hpa=True), record(9, HPA_LAST, hpa=True))

    snapshot = data.snapshot(QueryState(data.catalog).apply())

    assert snapshot.base_date == HPA_LAST
    assert snapshot.equipment_ids == (9,)
    assert (snapshot.summary.equipment_count, snapshot.summary.hpa_excluded) == (0, 1)
    assert snapshot.summary.rate is None
    assert snapshot.observations == ()
    assert all(trend.points == () for trend in snapshot.trends)
