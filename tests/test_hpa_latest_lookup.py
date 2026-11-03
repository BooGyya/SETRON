"""분석 기준일을 바꾸지 않는 호기별 최신 HPA 원본 조회를 검증한다 (BE-024)."""

from datetime import date

import pytest

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding, HpaClassificationConflict
from setron.ui.query_state import GroupOption, QueryCatalog, QueryState, SiteOption


FIRST, MIDDLE, LAST = date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)


def record(equipment_id, day, *, hpa=True, line=None, version="vHPA.1"):
    """실제 업무 자료와 무관한 원본 관측을 만든다.

    param
    - equipment_id, day: 시험 설비 ID와 실제 관측일.
    - hpa, line, version: 분류와 그대로 보존할 원본 구역·SW.

    return
    - 시험용 EquipmentRecord.
    """
    return EquipmentRecord(equipment_id, f"TEST-{equipment_id}", day, "시험 사업장", line,
                           "HPA" if hpa else "M-O2",
                           (PmVersion(None, 9 if hpa else 1, 90 if hpa else 10, version),), is_hpa=hpa)


def source(*records):
    """DB와 Qt 없이 원본 조회를 검사할 공급자를 만든다.

    param
    - records: 저장된 순서를 재현할 시험 관측 목록.

    return
    - 일반 모델 매핑만 가진 DashboardSource.
    """
    catalog = QueryCatalog(sites=(SiteOption("site", "시험 사업장"),),
                           groups=(GroupOption("o2", "O2", "OXIDE"),))
    return DashboardSource(catalog=catalog, bindings=(GroupBinding("o2", (1,)),), records=records,
                           locations={r.equipment_id: EquipmentLocation("site", "별도 위치 매핑") for r in records},
                           families={}, targets={}, risks={}, source_label="시험 자료")


def test_latest_hpa_is_selected_per_equipment_without_requiring_a_common_day():
    """원본 순서·일반 설비 날짜와 무관하게 HPA 호기마다 마지막 실제 관측을 고른다."""
    latest_nine = record(9, LAST, version="vHPA.9-new")
    latest_seven = record(7, MIDDLE, version="vHPA.7-new")
    data = source(latest_nine, record(7, FIRST), record(1, LAST, hpa=False),
                  latest_seven, record(9, FIRST))

    found = data.latest_hpa_records()

    assert tuple(r.equipment_id for r in found) == (7, 9)
    assert found[0] is latest_seven and found[1] is latest_nine


def test_latest_hpa_preserves_actual_date_original_line_and_sw():
    """오래된 위치 매핑이나 다른 날짜의 값으로 원본 관측을 덮어쓰지 않는다."""
    latest = record(9, LAST, line=" 원본 L2 ", version="vHPA.2_d5 ")
    data = source(record(9, FIRST, line="원본 L1", version="vHPA.1"), latest)

    found, = data.latest_hpa_records()

    assert found is latest
    assert found.base_date == LAST and found.line == " 원본 L2 "
    assert found.pms is latest.pms and found.pms[0].version_raw == "vHPA.2_d5 "


@pytest.mark.parametrize("records", [(), (record(1, LAST, hpa=False),)], ids=["빈 자료", "일반 설비만"])
def test_source_without_hpa_returns_an_empty_lookup(records):
    """HPA가 없으면 일반 설비를 HPA로 추정하지 않는다."""
    assert source(*records).latest_hpa_records() == ()


@pytest.mark.parametrize("flags", [(False, True), (True, False)], ids=["일반에서 HPA", "HPA에서 일반"])
def test_mixed_classification_history_requires_original_review(flags):
    """같은 호기의 분류가 섞이면 날짜순으로 임의 해석하지 않고 원문 검토를 요청한다."""
    data = source(record(9, FIRST, hpa=flags[0]), record(9, LAST, hpa=flags[1]))

    with pytest.raises(HpaClassificationConflict, match="원문 분류를 검토") as error:
        data.latest_hpa_records()

    assert "설비 ID" not in str(error.value)
    assert "TEST-9" in str(error.value)
    assert f"{FIRST.isoformat()} · {'HPA' if flags[0] else '일반 설비'}" in str(error.value)
    assert f"{LAST.isoformat()} · {'HPA' if flags[1] else '일반 설비'}" in str(error.value)
    assert error.value.observations == (("TEST-9", FIRST, flags[0]), ("TEST-9", LAST, flags[1]))


def test_latest_hpa_lookup_does_not_change_common_snapshot_or_source_records():
    """별도 조회를 호출해도 현재 화면의 날짜·집계·추이·관측 원본은 그대로다."""
    data = source(record(1, MIDDLE, hpa=False), record(9, MIDDLE), record(9, LAST))
    query = QueryState(data.catalog).apply()
    before, original_records = data.snapshot(query), data.records
    dates, analysis_dates = data.dates, data.analysis_dates

    latest, = data.latest_hpa_records()

    assert latest.base_date == LAST
    assert data.records is original_records
    assert data.dates == dates and data.analysis_dates == analysis_dates
    assert data.snapshot(query) == before
    assert before.base_date == MIDDLE
