"""QA Q-07: 판단 불가를 이유별로 나눠 보여 준다. 목표 N이 없는 것을 버전 문제로 오해하지 않게 한다."""

from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aggregation.service import summarize
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.dashboard_data import undetermined_breakdown

DAY = date(2026, 10, 21)
FAMILIES = {10: FamilyReleases(10, (ReleaseVersion(101, 10, "v1", 1), ReleaseVersion(102, 10, "v2", 2))),
            20: FamilyReleases(20, (ReleaseVersion(201, 20, "x1", 1),))}


def record(eid, family, version, release=None):
    return EquipmentRecord(eid, f"EQ-{eid}", DAY, "시험", None, "모델", (PmVersion(None, 1, family, version, release),))


def test_reasons_are_counted_by_what_the_user_must_fix():
    records = (record(1, 10, "v1", 101), record(2, 10, "v2", 102),      # 계열 10: 목표 없음 → 목표 N 미지정 2
               record(3, 20, None),                                      # 계열 20: 버전 비어 있음
               record(4, 20, "x9"))                                      # 계열 20: 노트에 없는 버전
    summary = summarize(records, FAMILIES, {(1, 20): 201})

    assert summary.undetermined == 4
    assert undetermined_breakdown(summary) == "목표 N 미지정 2 · 버전 미입력 1 · 노트에 없는 버전 1"


def test_no_undetermined_gives_empty_text():
    summary = summarize((record(1, 10, "v2", 102),), FAMILIES, {(1, 10): 102})
    assert undetermined_breakdown(summary) == ""
