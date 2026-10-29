"""버전 노후도 계산과 기간 기준 검사·저장을 검증한다 (BE-016 완료 조건)."""

from contextlib import contextmanager
from datetime import date

import pytest

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aging.repository import AgingSettingsRepository
from setron.features.aging.schemas import (
    DEFAULT_THRESHOLDS,
    SETTING_KEY,
    AgeReason,
    AgeStatus,
    AgeThresholds,
    AgeUnit,
    ThresholdError,
)
from setron.features.aging.service import assess_age, equipment_age, parse_thresholds, threshold_problems
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

RELEASED = date(2025, 10, 29)


@pytest.mark.parametrize(
    ("base_date", "status", "label"),
    [
        (date(2025, 10, 29), AgeStatus.SAFE, "0일"),
        (date(2026, 10, 28), AgeStatus.SAFE, "11개월"),
        (date(2026, 10, 29), AgeStatus.WARNING, "1년"),
        (date(2027, 10, 28), AgeStatus.WARNING, "1년 11개월"),
        (date(2027, 10, 29), AgeStatus.DANGER, "2년"),
        (date(2029, 1, 1), AgeStatus.DANGER, "3년 2개월"),
    ],
    ids=["출시 당일", "1년 하루 전", "1년 되는 날 주의", "2년 하루 전", "2년 되는 날 위험", "3년 이상"],
)
def test_default_thresholds_split_at_one_and_two_years(base_date, status, label):
    result = assess_age(RELEASED, base_date, DEFAULT_THRESHOLDS)

    assert result.status is status
    assert result.elapsed_label == label


def test_status_labels_and_colors():
    result = assess_age(RELEASED, date(2026, 10, 29), DEFAULT_THRESHOLDS)

    assert (result.label, result.color) == ("주의", "ORANGE")


def test_leap_day_release_turns_one_year_on_feb_28():
    released = date(2024, 2, 29)

    assert assess_age(released, date(2025, 2, 27), DEFAULT_THRESHOLDS).status is AgeStatus.SAFE
    assert assess_age(released, date(2025, 2, 28), DEFAULT_THRESHOLDS).status is AgeStatus.WARNING


@pytest.mark.parametrize(
    ("thresholds", "base_date", "status"),
    [
        (AgeThresholds(6, 12, AgeUnit.MONTH), date(2026, 4, 29), AgeStatus.WARNING),
        (AgeThresholds(6, 12, AgeUnit.MONTH), date(2026, 4, 28), AgeStatus.SAFE),
        (AgeThresholds(30, 90, AgeUnit.DAY), date(2026, 1, 27), AgeStatus.DANGER),
    ],
    ids=["6개월 주의", "6개월 하루 전", "90일 위험"],
)
def test_changed_thresholds_are_applied(thresholds, base_date, status):
    assert assess_age(RELEASED, base_date, thresholds).status is status


def test_missing_release_date_is_unknown():
    result = assess_age(None, date(2026, 10, 29), DEFAULT_THRESHOLDS)

    assert (result.status, result.reason) == (AgeStatus.UNKNOWN, AgeReason.RELEASE_DATE_MISSING)
    assert result.color is None and result.elapsed_label is None


def test_release_after_base_date_needs_data_check():
    result = assess_age(date(2026, 11, 1), date(2026, 10, 29), DEFAULT_THRESHOLDS)

    assert result.status is AgeStatus.RELEASED_AFTER_BASE_DATE
    assert result.label == "자료 확인 필요"


def test_invalid_stored_setting_still_shows_elapsed_period():
    result = assess_age(RELEASED, date(2026, 10, 29), None)

    assert result.status is AgeStatus.SETTING_INVALID
    assert result.elapsed_label == "1년"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"warning": -1, "danger": 2, "unit": "YEAR"}, "주의 기간은 1 이상"),
        ({"warning": 0, "danger": 2, "unit": "YEAR"}, "주의 기간은 1 이상"),
        ({"warning": 2, "danger": 1, "unit": "YEAR"}, "위험 기간은 주의 기간보다 길어야"),
        ({"warning": 1, "danger": 1, "unit": "YEAR"}, "위험 기간은 주의 기간보다 길어야"),
        ({"warning": 1.5, "danger": 2, "unit": "YEAR"}, "정수로 입력"),
        ({"warning": True, "danger": 2, "unit": "YEAR"}, "정수로 입력"),
        ({"warning": 1, "danger": 2, "unit": "WEEK"}, "기간 단위"),
        ({"warning": 1, "danger": 2}, "기간 단위"),
        ([1, 2], "형식이 올바르지 않습니다"),
    ],
    ids=["음수", "0", "역전", "같음", "소수", "참거짓", "없는 단위", "단위 누락", "형식 오류"],
)
def test_invalid_thresholds_are_rejected(value, message):
    assert any(message in p for p in threshold_problems(value))
    with pytest.raises(ThresholdError):
        parse_thresholds(value)


def test_schema_default_setting_is_valid():
    assert parse_thresholds({"warning": 1, "danger": 2, "unit": "YEAR"}) == DEFAULT_THRESHOLDS
    assert DEFAULT_THRESHOLDS.description == "1년 미만 안전, 1년 이상 2년 미만 주의, 2년 이상 위험"


# --- 설비 노후도 ---------------------------------------------------------------

F_O2, F_O3 = 10, 20
FAMILIES = {
    F_O2: FamilyReleases(F_O2, [
        ReleaseVersion(101, F_O2, "v2.1", 1, date(2023, 10, 1)),
        ReleaseVersion(102, F_O2, "v2.2", 2, None),
    ]),
    F_O3: FamilyReleases(F_O3, [ReleaseVersion(201, F_O3, "v3.1", 1, date(2026, 5, 1))]),
}


def equipment(*pms):
    return EquipmentRecord(1, "EQ1", date(2026, 10, 29), "화성", "16L", "M-O2/O3", tuple(pms))


def test_mixed_equipment_uses_oldest_family_and_keeps_each_period():
    record = equipment(
        PmVersion("PM1", 2, F_O3, "v3.1"),
        PmVersion("PM3", 1, F_O2, "v2.1"),
    )

    result = equipment_age(record, FAMILIES, DEFAULT_THRESHOLDS)

    assert result.oldest.pm.pm_name == "PM3"
    assert result.status is AgeStatus.DANGER
    assert [p.age.elapsed_label for p in result.pms] == ["5개월", "3년"]
    assert not result.has_unchecked


@pytest.mark.parametrize(
    ("version", "reason"),
    [("v2.2", AgeReason.RELEASE_DATE_MISSING), ("v9.9", AgeReason.NOT_REGISTERED), (None, AgeReason.MISSING_VERSION)],
    ids=["출시일 없음", "릴리즈 미등록", "버전 누락"],
)
def test_pm_without_release_date_is_unknown(version, reason):
    result = equipment_age(equipment(PmVersion(None, 1, F_O2, version)), FAMILIES, DEFAULT_THRESHOLDS)

    assert result.status is AgeStatus.UNKNOWN
    assert result.pms[0].age.reason is reason
    assert result.oldest is None


def test_unchecked_pm_is_flagged_next_to_oldest():
    record = equipment(PmVersion("PM1", 2, F_O3, "v3.1"), PmVersion("PM3", 1, F_O2, "v2.2"))

    result = equipment_age(record, FAMILIES, DEFAULT_THRESHOLDS)

    assert result.status is AgeStatus.SAFE
    assert result.has_unchecked


def test_base_date_defaults_to_snapshot_date_and_can_be_overridden():
    record = equipment(PmVersion(None, 2, F_O3, "v3.1"))

    assert equipment_age(record, FAMILIES, DEFAULT_THRESHOLDS).status is AgeStatus.SAFE
    later = equipment_age(record, FAMILIES, DEFAULT_THRESHOLDS, base_date=date(2027, 5, 1))
    assert later.status is AgeStatus.WARNING


# --- 기간 기준 저장 (가짜 저장 서비스) -------------------------------------------

class FakeStorage:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})

    @contextmanager
    def transaction(self):
        yield self

    def get(self, table, key):
        if key["setting_key"] not in self.rows:
            raise LookupError("해당 기록이 없습니다.")
        return {"setting_key": key["setting_key"], "setting_value": self.rows[key["setting_key"]]}

    def insert(self, table, values):
        self.rows[values["setting_key"]] = values["setting_value"]

    def update(self, table, key, changes):
        self.rows[key["setting_key"]] = changes["setting_value"]


def test_load_uses_default_when_setting_row_is_missing():
    loaded = AgingSettingsRepository(FakeStorage()).load()

    assert loaded.thresholds == DEFAULT_THRESHOLDS
    assert loaded.is_default


def test_load_reports_invalid_stored_setting():
    loaded = AgingSettingsRepository(FakeStorage({SETTING_KEY: {"warning": 3, "danger": 2, "unit": "YEAR"}})).load()

    assert loaded.thresholds is None
    assert loaded.problems


def test_saved_thresholds_are_used_on_next_load():
    storage = FakeStorage({SETTING_KEY: DEFAULT_THRESHOLDS.to_setting()})
    repository = AgingSettingsRepository(storage)

    repository.save({"warning": 6, "danger": 18, "unit": "MONTH"})

    assert repository.load().thresholds == AgeThresholds(6, 18, AgeUnit.MONTH)


def test_invalid_thresholds_are_not_saved():
    storage = FakeStorage({SETTING_KEY: DEFAULT_THRESHOLDS.to_setting()})

    with pytest.raises(ThresholdError):
        AgingSettingsRepository(storage).save({"warning": 2, "danger": 1, "unit": "YEAR"})

    assert storage.rows[SETTING_KEY] == DEFAULT_THRESHOLDS.to_setting()
