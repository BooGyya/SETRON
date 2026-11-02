"""현황 보고서 Excel 출력을 검증한다 (BE-021 완료 조건 중 현황·표·그래프 부분)."""

from datetime import date, datetime

import pytest
from openpyxl import Workbook, load_workbook

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aggregation.service import summarize, summarize_by_date
from setron.features.aging.schemas import DEFAULT_THRESHOLDS
from setron.features.aging.service import equipment_age
from setron.features.export.schemas import StatusReport, TargetRow
from setron.features.export.service import build_status_report, save_status_report
from setron.features.item.service import ItemCatalog
from setron.features.priority.service import prioritize
from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
from setron.features.risk.service import RiskAssessor
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

M_O2, M_O3, M_HPA = 1, 2, 9
F_O2, F_O3 = 10, 20
DAY1, DAY2 = date(2026, 9, 21), date(2026, 10, 21)

FAMILIES = {
    F_O2: FamilyReleases(F_O2, [
        ReleaseVersion(101, F_O2, "v2.1", 1, date(2024, 1, 10)),
        ReleaseVersion(102, F_O2, "v2.2", 2, date(2025, 6, 1)),
        ReleaseVersion(103, F_O2, "v2.3", 3, date(2026, 7, 1)),
    ]),
    F_O3: FamilyReleases(F_O3, [ReleaseVersion(201, F_O3, "v3.1", 1, date(2026, 1, 1))]),
}
TARGETS = {(M_O2, F_O2): 103, (M_O3, F_O3): 201}
NAMES = {F_O2: "O2", F_O3: "O3"}
DIRECT_V21 = RiskRule(1, RuleType.DIRECT_VERSION, F_O2, "가상 결함 버전", release_version_id=101,
                      mapping_status=MappingStatus.VERIFIED)


def equipment(eid, *pms, day=DAY2, hpa=False):
    return EquipmentRecord(eid, f"DM-{eid:03d}", day, "화성", "16L", "M-O2", tuple(pms), is_hpa=hpa)


def o2(version, name=None):
    return PmVersion(name, M_O2, F_O2, version)


RECORDS = (
    equipment(1, o2("v2.3")),                                            # N 이상
    equipment(2, o2("v2.1")),                                            # N-2, 위험
    equipment(3, o2(None)),                                              # 판단 불가
    equipment(4, o2("v2.2", "PM1"), PmVersion("PM2", M_O3, F_O3, "v3.1")),  # 혼합: O2 N-1, O3 N 이상
    equipment(5, o2("v2.3"), hpa=True),
)
EARLIER = (equipment(1, o2("v2.2"), day=DAY1), equipment(2, o2("v2.1"), day=DAY1))


def report(**overrides):
    summary = summarize(RECORDS, FAMILIES, TARGETS)
    assessor = RiskAssessor(FAMILIES, ItemCatalog(FAMILIES, []), [DIRECT_V21])
    values = dict(
        summary=summary,
        scope=(("Site", "화성"), ("Line", "16L")),
        targets=(TargetRow("M-O2", "O2", "v2.3"), TargetRow("M-O3", "O3", "v3.1"), TargetRow("M-O5", "O5", None)),
        family_names=NAMES,
        risks=assessor.summarize(RECORDS),
        ages=tuple(equipment_age(r, FAMILIES, DEFAULT_THRESHOLDS) for r in RECORDS if not r.is_hpa),
        thresholds=DEFAULT_THRESHOLDS,
        priority=prioritize(RECORDS, assessor, FAMILIES, DEFAULT_THRESHOLDS),
        trend=summarize_by_date(EARLIER + RECORDS, FAMILIES, TARGETS),
        created_at=datetime(2026, 11, 2, 9, 30),
    )
    values.update(overrides)
    return StatusReport(**values)


def column(sheet, col):
    return [sheet.cell(r, col).value for r in range(2, sheet.max_row + 1)]


@pytest.fixture
def saved(tmp_path):
    path = tmp_path / "현황.xlsx"
    result = save_status_report(report(), path)
    assert result.saved, result.message
    return load_workbook(path)


def test_sheets_are_created_in_order(saved):
    assert saved.sheetnames == ["조회 조건", "요약", "계열별 현황", "상태 구성비", "설비별 판정", "우선순위", "날짜별 추이"]


def test_conditions_show_base_date_scope_and_each_target(saved):
    values = [[c.value for c in row] for row in saved["조회 조건"].iter_rows()]

    assert ["기준일", "2026-10-21"] in [v[:2] for v in values]
    assert ["Site", "화성"] in [v[:2] for v in values]
    assert ["M-O2", "O2", "v2.3"] in values
    assert ["M-O5", "O5", "미지정 (판단 불가)"] in values


def test_summary_numbers_match_aggregation(saved):
    summary = summarize(RECORDS, FAMILIES, TARGETS)
    sheet = saved["요약"]
    values = dict(zip(column(sheet, 1), column(sheet, 2)))

    assert values["물리 설비 수"] == summary.equipment_count == 4
    assert values["분석 제외 (HPA)"] == 1
    assert values["N 이상 적용률"] == pytest.approx(summary.rate) == pytest.approx(2 / 4)  # 판단 불가 1건 제외
    assert values["업그레이드 필요"] == 2
    assert values["판단 불가 (건)"] == 1
    assert values["위험 후보 설비"] == 1


def test_family_sheet_has_counts_and_a_bar_chart_linked_to_cells(saved):
    sheet = saved["계열별 현황"]

    assert column(sheet, 1) == ["O2", "O3"]
    assert [column(sheet, c)[0] for c in (2, 3, 4)] == [1, 2, 1]
    chart = sheet._charts[0]
    refs = [s.val.numRef.f for s in chart.series]
    assert refs == ["'계열별 현황'!$B$2:$B$3", "'계열별 현황'!$C$2:$C$3", "'계열별 현황'!$D$2:$D$3"]


def test_composition_pie_chart_uses_all_units_including_undetermined(saved):
    sheet = saved["상태 구성비"]

    assert column(sheet, 2)[:3] == [2, 2, 1]
    assert sheet._charts[0].series[0].val.numRef.f == "'상태 구성비'!$B$2:$B$4"


def test_equipment_sheet_has_one_row_per_equipment_and_family(saved):
    sheet = saved["설비별 판정"]
    rows = {(r[0], r[4]): r for r in sheet.iter_rows(min_row=2, values_only=True)}

    assert len(rows) == 5  # 혼합 설비는 O2·O3 두 줄, HPA는 없음
    assert rows[("DM-002", "O2")][7] == "N-2"
    assert rows[("DM-002", "O2")][10:12] == ("위험", "가상 결함 버전")
    assert rows[("DM-003", "O2")][7] == "판단 불가"
    assert rows[("DM-003", "O2")][8]  # 사유가 비어 있지 않음
    assert rows[("DM-004", "O2")][5] == "PM1 v2.2"
    assert rows[("DM-004", "O3")][7] == "N 이상"
    assert rows[("DM-001", "O2")][12:14] == ("안전", "3개월")


def test_priority_sheet_lists_ranked_and_needs_check(saved):
    values = [r for r in saved["우선순위"].iter_rows(values_only=True)]

    assert values[1][:3] == (1, "DM-002", "위험")
    needs_check = values[values.index(("확인 필요 (순위 없음)",) + (None,) * 6) + 2]
    assert needs_check[:2] == ("DM-003", "미확인")  # 버전 없음 → 위험·기간 모두 확인 필요
    assert "출시일" in needs_check[3]


def test_trend_sheet_has_line_chart_and_keeps_only_recorded_dates(saved):
    sheet = saved["날짜별 추이"]

    assert [d.date() for d in column(sheet, 1)] == [DAY1, DAY2]
    assert sheet._charts[0].series[0].val.numRef.f == "'날짜별 추이'!$E$2:$E$3"


def test_rate_without_determined_units_is_blank_not_zero():
    only_missing = summarize([equipment(3, o2(None))], FAMILIES, TARGETS)
    book = build_status_report(StatusReport(summary=only_missing))

    assert book["요약"]["B4"].value is None
    assert "계산 불가" in book["요약"]["C4"].value


def test_missing_results_are_marked_not_calculated():
    book = build_status_report(StatusReport(summary=summarize(RECORDS, FAMILIES, TARGETS)))

    assert "날짜별 추이" not in book.sheetnames
    assert book["설비별 판정"]["K2"].value == "미계산"
    assert book["우선순위"]["A1"].value == "우선순위를 계산하지 않았습니다."


def test_file_in_use_reports_reason(tmp_path, monkeypatch):
    def locked(self, filename):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Workbook, "save", locked)
    result = save_status_report(report(), tmp_path / "현황.xlsx")

    assert not result.saved
    assert "Excel에서 열려 있는지" in result.message


def test_non_xlsx_name_is_rejected(tmp_path):
    result = save_status_report(report(), tmp_path / "현황.xls")

    assert not result.saved and ".xlsx" in result.message
