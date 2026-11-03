"""버전 통일 참고 예측을 검증한다 (BE-020 완료 조건)."""

import json
from datetime import date

import pytest

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.ai.examples import example_client
from setron.features.ai.schemas import AIStatus, AITask
from setron.features.forecast.schemas import ForecastReason, ForecastStatus
from setron.features.forecast.service import explain_forecast, forecast, forecast_facts
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.infrastructure.ai_client import ExampleChatClient

M_O2, M_O3, M_C4 = 1, 2, 3
F_O2, F_O3, F_C4 = 10, 20, 30
DAY1, DAY2, DAY3 = date(2026, 9, 1), date(2026, 10, 1), date(2026, 10, 21)

FAMILIES = {
    F_O2: FamilyReleases(F_O2, [ReleaseVersion(101, F_O2, "v1", 1), ReleaseVersion(102, F_O2, "v2", 2),
                                ReleaseVersion(103, F_O2, "v3", 3)]),
    F_O3: FamilyReleases(F_O3, [ReleaseVersion(201, F_O3, "v3.1", 1)]),
    F_C4: FamilyReleases(F_C4, [ReleaseVersion(301, F_C4, "v4", 1)]),
}
TARGETS = {(M_O2, F_O2): 103, (M_O3, F_O3): 201}  # C4는 목표 미지정


def equipment(eid, day, *pms, hpa=False):
    return EquipmentRecord(eid, f"DM-{eid:03d}", day, "화성", "16L", "model", tuple(pms), is_hpa=hpa)


def o2(version):
    return PmVersion(None, M_O2, F_O2, version)


def o3(version):
    return PmVersion(None, M_O3, F_O3, version)


RECORDS = (
    equipment(1, DAY1, o2("v1")), equipment(2, DAY1, o2("v1")), equipment(3, DAY1, o2("v3")),
    equipment(4, DAY1, o2("v1")), equipment(5, DAY1, o3("v3.1")),
    equipment(1, DAY2, o2("v3")),   # 기존 설비 패치
    equipment(2, DAY2, o2("v1")),   # 그대로 N 미만
    equipment(3, DAY2, o2("v1")),   # N 이상 → N 미만
    equipment(6, DAY2, o2("v3")),   # 신규·전입, N 이상
    equipment(7, DAY2, o2("v2")),   # 신규·전입, N 미만
    equipment(5, DAY2, o3("v3.1")),
    equipment(9, DAY2, o2("v1"), hpa=True),
)


def by_family(result):
    return {f.family_id: f for f in result.families}


def test_counts_applied_units_at_first_and_last_date_including_new_equipment():
    """O2: 처음 N 이상 1대(3번) → 끝 N 이상 2대(1번 패치 + 6번 신규). 3번이 내려간 것도 그대로 반영된다."""
    o2_result = by_family(forecast(RECORDS, FAMILIES, TARGETS))[F_O2]

    assert (o2_result.start_applied, o2_result.end_applied, o2_result.gained) == (1, 2, 1)
    assert (o2_result.end_below, o2_result.points, o2_result.period_days) == (3, 2, 30)
    assert o2_result.note == "참고용 · 데이터 2장"


def test_remaining_period_is_proportional_to_applied_increase():
    """30일 동안 N 이상 1대 증가, 남은 N 미만 3대 → 90일."""
    o2_result = by_family(forecast(RECORDS, FAMILIES, TARGETS))[F_O2]

    assert o2_result.status is ForecastStatus.ESTIMATED
    assert o2_result.rate_per_day == 1 / 30
    assert (o2_result.remaining_days, o2_result.completion_date) == (90, date(2026, 12, 30))


def test_new_equipment_at_or_above_counts_as_progress():
    """기존 설비가 그대로여도 N 이상 신규 설비가 들어오면 증가로 센다 (멘토링 3차 단순 비례)."""
    records = (equipment(1, DAY1, o2("v1")), equipment(1, DAY2, o2("v1")), equipment(2, DAY2, o2("v3")))

    o2_result = by_family(forecast(records, FAMILIES, TARGETS))[F_O2]

    assert (o2_result.gained, o2_result.end_below) == (1, 1)
    assert (o2_result.status, o2_result.remaining_days) == (ForecastStatus.ESTIMATED, 30)


def test_remaining_days_are_rounded_to_whole_days():
    """20일 동안 3대 증가, 남은 2대 → 2 ÷ 0.15 = 13.3 → 13일."""
    records = tuple(equipment(i, DAY1, o2("v1")) for i in range(1, 6)) + tuple(
        equipment(i, DAY1.replace(day=21), o2("v3" if i <= 3 else "v1")) for i in range(1, 6))

    o2_result = by_family(forecast(records, FAMILIES, TARGETS))[F_O2]

    assert (o2_result.remaining_days, o2_result.completion_date) == (13, date(2026, 10, 4))


def test_all_at_or_above_is_complete_with_zero_days():
    o3_result = by_family(forecast(RECORDS, FAMILIES, TARGETS))[F_O3]

    assert (o3_result.status, o3_result.remaining_days) == (ForecastStatus.COMPLETE, 0)


def test_undetermined_left_is_not_reported_as_complete():
    records = RECORDS + (equipment(8, DAY2, o3(None)),)

    o3_result = by_family(forecast(records, FAMILIES, TARGETS))[F_O3]

    assert o3_result.status is ForecastStatus.NOT_CONFIRMED
    assert o3_result.reasons == (ForecastReason.UNDETERMINED_LEFT,)


def test_estimate_keeps_undetermined_warning():
    records = RECORDS + (equipment(8, DAY2, o2(None)),)

    o2_result = by_family(forecast(records, FAMILIES, TARGETS))[F_O2]

    assert o2_result.status is ForecastStatus.ESTIMATED
    assert o2_result.reasons == (ForecastReason.UNDETERMINED_LEFT,)


def test_family_without_target_is_not_estimated():
    records = (equipment(1, DAY1, PmVersion(None, M_C4, F_C4, "v4")),
               equipment(1, DAY2, PmVersion(None, M_C4, F_C4, "v4")))

    c4 = by_family(forecast(records, FAMILIES, TARGETS))[F_C4]

    assert (c4.status, c4.reasons) == (ForecastStatus.NOT_ESTIMABLE, (ForecastReason.TARGET_NOT_SET,))


def test_single_date_cannot_give_speed():
    result = forecast(RECORDS, FAMILIES, TARGETS, end=DAY1)

    assert by_family(result)[F_O2].reasons == (ForecastReason.SINGLE_DATE,)


def test_family_seen_on_one_date_only_is_single_date():
    records = (equipment(5, DAY1, o3("v3.1")), equipment(1, DAY2, o2("v1")))

    o2_result = by_family(forecast(records, FAMILIES, TARGETS))[F_O2]

    assert (o2_result.points, o2_result.reasons) == (1, (ForecastReason.SINGLE_DATE,))


@pytest.mark.parametrize("later", ["v1", "v2"])
def test_no_increase_cannot_give_speed(later):
    """N 이상 대수가 그대로이거나 줄면 예측하지 않는다."""
    records = (equipment(1, DAY1, o2("v3")), equipment(2, DAY1, o2("v1")),
               equipment(1, DAY2, o2(later)), equipment(2, DAY2, o2("v1")))

    o2_result = by_family(forecast(records, FAMILIES, TARGETS))[F_O2]

    assert (o2_result.status, o2_result.reasons) == (ForecastStatus.NOT_ESTIMABLE, (ForecastReason.NO_PROGRESS,))


def test_period_can_be_selected():
    later = RECORDS + (equipment(2, DAY3, o2("v3")),)

    result = forecast(later, FAMILIES, TARGETS, start=DAY2)

    assert (result.start_date, result.end_date) == (DAY2, DAY3)
    assert (by_family(result)[F_O2].start_applied, by_family(result)[F_O2].end_applied) == (2, 1)


def test_no_records_gives_empty_result():
    assert forecast([], FAMILIES, TARGETS).families == ()


# --- AI 설명 ---------------------------------------------------------------------

def test_facts_include_method_assumptions_and_limits_as_json():
    facts = forecast_facts(forecast(RECORDS, FAMILIES, TARGETS), {F_O2: "O2", F_O3: "O3"})

    o2_fact = facts["계열별 예측"][0]
    assert (o2_fact["계열"], o2_fact["예상 잔여 기간(일)"], o2_fact["참고 완료일"]) == ("O2", 90, "2026-12-30")
    assert (o2_fact["N 이상 증가"], o2_fact["하루 증가(대/일)"], o2_fact["표시"]) == (1, 0.03, "참고용 · 데이터 2장")
    assert facts["가정"] and facts["한계"] and "증가 속도" in facts["계산 방법"]
    assert json.loads(json.dumps(facts, ensure_ascii=False)) == facts


def test_explain_forecast_flags_numbers_not_in_facts():
    reply = {"answer": "O2는 남은 3건을 약 90일 뒤 마칠 것으로 보이며, 확률은 80%입니다.",
             "evidence": [], "assumptions": [], "unconfirmed": []}
    client = ExampleChatClient([json.dumps(reply, ensure_ascii=False)])

    report = explain_forecast(client, "O2 언제 끝나?", forecast(RECORDS, FAMILIES, TARGETS))

    assert report.result.ok
    assert report.unmatched_numbers == ("80",)
    assert '"계열별 예측"' in client.calls[0][1].content


def test_explain_forecast_failure_is_returned_without_exception():
    report = explain_forecast(example_client(AITask.ANALYSIS, "connection"), "질문", forecast(RECORDS, FAMILIES, TARGETS))

    assert report.result.status is AIStatus.FAILED and report.unmatched_numbers == ()
