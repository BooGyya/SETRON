"""현황 보고서 Excel 출력을 검증한다 (BE-021 완료 조건 중 현황·표·그래프 부분)."""

from datetime import date, datetime
from dataclasses import replace

import pytest
from openpyxl import Workbook, load_workbook

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aggregation.service import summarize, summarize_by_date
from setron.features.aging.schemas import DEFAULT_THRESHOLDS
from setron.features.aging.service import equipment_age
from setron.features.export.line_heatmap import HEATMAP_COLORS
from setron.features.export.schemas import StatusReport, TargetRow
from setron.features.export.service import build_status_report, save_status_report
from setron.features.export.xlsx_cells import OVERFLOW_MARKER, OVERFLOW_SHEET, ReportTextWriter
from setron.features.item.service import ItemCatalog
from setron.features.priority.service import prioritize
from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
from setron.features.risk.service import RiskAssessor
from setron.features.version.groups import Tier
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
        priority=prioritize(RECORDS, assessor, FAMILIES, DEFAULT_THRESHOLDS, targets=TARGETS),
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
    assert saved.sheetnames == ["조회 조건", "요약", "계열별 현황", "상태 구성비", "설비별 판정", "우선순위", "날짜별 추이", "구성 트리맵",
                               "라인별 히트맵"]


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

    assert column(sheet, 1)[:2] == ["O2", "O3"]
    assert [column(sheet, c)[0] for c in (2, 3, 4)] == [1, 2, 1]
    chart = sheet._charts[0]
    refs = [s.val.numRef.f for s in chart.series]
    assert refs == ["'계열별 현황'!$B$2:$B$3", "'계열별 현황'!$C$2:$C$3", "'계열별 현황'!$D$2:$D$3"]


def test_family_sheet_adds_tier_table_and_stacked_chart_below(saved):
    """적용률 표는 그대로 두고 아래에 단계 표를 둔다. 단계 합계는 N 이상·N 미만·판단 불가와 맞는다."""
    sheet = saved["계열별 현황"]
    start = next(r for r in range(1, sheet.max_row + 1) if sheet.cell(r, 1).value == "계열별 단계 (건)")
    header = [sheet.cell(start + 1, c).value for c in range(1, 7)]
    rows = {sheet.cell(r, 1).value: [sheet.cell(r, c).value for c in range(2, 7)] for r in (start + 2, start + 3)}

    assert header == ["계열", "N+1 이상", "N", "N-1", "N-2 이하", "비교 불가"]
    for r, name in ((2, "O2"), (3, "O3")):
        compliant, below, undetermined = (sheet.cell(r, c).value for c in (2, 3, 4))
        above, at, one, two, unknown = rows[name]
        assert (above + at, one + two, unknown) == (compliant, below, undetermined)
    stacked = sheet._charts[1]
    assert stacked.grouping == "stacked" and len(stacked.series) == 5
    assert [_rgb(s) for s in stacked.series] == [
        HEATMAP_COLORS[t] for t in (Tier.ABOVE, Tier.AT, Tier.ONE_BELOW, Tier.TWO_BELOW, Tier.UNKNOWN)]


def _rgb(shape):
    color = shape.graphicalProperties.solidFill.srgbClr
    return getattr(color, "val", color)


def test_composition_pie_chart_uses_all_units_including_undetermined(saved):
    sheet = saved["상태 구성비"]

    assert column(sheet, 1)[:5] == ["N+1 이상", "N", "N-1", "N-2 이하", "비교 불가"]
    assert sum(column(sheet, 2)[:5]) == 5  # 판단 불가까지 모든 설비·계열 건
    assert column(sheet, 2)[4] == 1
    pie = sheet._charts[0].series[0]
    assert pie.val.numRef.f == "'상태 구성비'!$B$2:$B$6"
    assert [_rgb(p) for p in pie.dPt] == [
        HEATMAP_COLORS[t] for t in (Tier.ABOVE, Tier.AT, Tier.ONE_BELOW, Tier.TWO_BELOW, Tier.UNKNOWN)]


def test_equipment_judgment_cell_is_filled_with_tier_color(saved):
    sheet = saved["설비별 판정"]
    judgment = [c.value for c in sheet[1]].index("판정") + 1
    colors = {sheet.cell(r, judgment).value: sheet.cell(r, judgment).fill.fgColor.rgb[-6:]
              for r in range(2, sheet.max_row + 1)}

    assert colors["판단 불가"] == HEATMAP_COLORS[Tier.UNKNOWN]
    assert set(colors.values()) <= set(HEATMAP_COLORS.values())


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

    assert values[0][4:6] == ("기준 N까지(칸)", "가장 낮은 버전")
    assert values[1][:3] == (1, "DM-002", "위험")
    assert values[1][4] == 2  # 위험 설비, N-2
    needs_check = values[values.index(("확인 필요 (순위 없음)",) + (None,) * 8) + 2]
    assert needs_check[:2] == ("DM-003", "미확인")  # 버전 없음 → 위험·버전 위치 모두 확인 필요
    assert "버전 위치" in needs_check[3]


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


def test_report_text_is_literal_in_conditions_equipment_targets_and_family_labels(tmp_path):
    """보고서의 모든 사용자 문자열을 수식·오류가 아닌 원문 텍스트로 재조회한다."""
    record = replace(RECORDS[0], equipment_code="=1+1", site="=2+2", line="",
                     model_name="=3+3", pms=(o2("=4+4"),))
    missing_line = replace(record, equipment_id=2, equipment_code="EQ-2", line=None)
    value = report(summary=summarize((record, missing_line), FAMILIES, TARGETS),
                   scope=(("=5+5", "=6+6"),), family_names={F_O2: "#N/A"},
                   targets=(TargetRow("=7+7", "=8+8", "=9+9"),))
    path = tmp_path / "원문현황.xlsx"
    assert save_status_report(value, path).saved
    saved = load_workbook(path)
    try:
        assert [saved["설비별 판정"].cell(2, c).value for c in range(1, 7)] == [
            "=1+1", "=2+2", "", "=3+3", "#N/A", "=4+4",
        ]
        assert saved["조회 조건"]["A3"].value == "=5+5"
        assert saved["조회 조건"]["B3"].value == "=6+6"
        assert saved["설비별 판정"]["C3"].value is None
        assert saved["계열별 현황"]["A2"].value == "#N/A"
        assert any(tuple(cell.value for cell in row) == ("=7+7", "=8+8", "=9+9")
                   for row in saved["조회 조건"])
        assert not any(cell.data_type in {"f", "e"} for sheet in saved for row in sheet for cell in row)
    finally:
        saved.close()


def test_report_long_scope_splits_only_above_cell_limit_and_keeps_chart_layout(tmp_path):
    """표·차트 참조를 유지하고 실제 참조 문구와 긴 값 분할을 좌표로 구분한다."""
    exact = "=" + "가" * 32766
    long = exact + "=1+2END"
    marker = OVERFLOW_MARKER + "조회 조건!B4"
    value = report(scope=(("한도", exact), ("긴 값", long), ("참조 문구 원문", marker)))
    path = tmp_path / "긴현황.xlsx"
    assert save_status_report(value, path).saved
    saved = load_workbook(path)
    try:
        assert saved.sheetnames == ["조회 조건", "요약", "계열별 현황", "상태 구성비", "설비별 판정",
                                    "우선순위", "날짜별 추이", "구성 트리맵", "라인별 히트맵", OVERFLOW_SHEET]
        assert saved["조회 조건"]["B3"].value == exact
        assert saved["조회 조건"]["B4"].value == saved["조회 조건"]["B5"].value == marker
        parts = list(saved[OVERFLOW_SHEET].iter_rows(min_row=2, values_only=True))
        assert [row[:6] for row in parts] == [
            ("조회 조건!B4", "조회 조건", 4, "값", 1, 2),
            ("조회 조건!B4", "조회 조건", 4, "값", 2, 2),
        ]
        assert "".join(row[6] for row in parts) == long
        assert all(cell.data_type == "s" for cell in saved[OVERFLOW_SHEET]["G"][1:])
        assert saved["요약"]["B4"].number_format == "0.0%"
        assert saved["날짜별 추이"]["A2"].number_format == "yyyy-mm-dd"
        assert saved["계열별 현황"]._charts[0].series[0].val.numRef.f == "'계열별 현황'!$B$2:$B$3"
        assert saved["날짜별 추이"]._charts[0].series[0].val.numRef.f == "'날짜별 추이'!$E$2:$E$3"
    finally:
        saved.close()


def test_overflow_sheet_name_collision_preserves_source_and_uses_unique_destination(tmp_path):
    """분할 시트와 이름이 같은 원본 시트도 덮어쓰거나 잘못 참조하지 않는다."""
    book = Workbook()
    book.active.title = OVERFLOW_SHEET
    text = ReportTextWriter(book)
    original = "=" + "A" * 40000
    text.write(book.active["B2"], original, "원문")
    text.finish()
    path = tmp_path / "이름충돌.xlsx"
    book.save(path)
    book.close()
    saved = load_workbook(path)
    try:
        assert saved.sheetnames == [OVERFLOW_SHEET, OVERFLOW_SHEET + "1"]
        assert saved[OVERFLOW_SHEET]["B2"].value == OVERFLOW_MARKER + OVERFLOW_SHEET + "!B2"
        parts = list(saved[OVERFLOW_SHEET + "1"].iter_rows(min_row=2, values_only=True))
        assert all(row[:4] == (OVERFLOW_SHEET + "!B2", OVERFLOW_SHEET, 2, "원문") for row in parts)
        assert "".join(row[6] for row in parts) == original
    finally:
        saved.close()


def test_unpreservable_report_text_returns_failure_without_replacing_existing_file(tmp_path):
    """원문을 담을 수 없으면 성공으로 잘라 저장하지 않고 기존 파일을 보존한다."""
    path = tmp_path / "현황.xlsx"
    path.write_bytes(b"existing workbook")
    result = save_status_report(report(scope=(("원문", "긴 값" * 20000 + "\x00"),)), path)
    assert not result.saved and "제어 문자" in result.message
    assert path.read_bytes() == b"existing workbook"
    assert list(tmp_path.iterdir()) == [path]


def test_failed_status_workbook_write_preserves_destination_and_cleans_own_temp(tmp_path, monkeypatch):
    """부분 XLSX 기록 실패에도 기존 출력과 무관한 임시파일을 유지한다."""
    path = tmp_path / "현황.xlsx"
    path.write_bytes(b"existing workbook")
    unrelated = tmp_path / ".현황.xlsx.other.tmp"
    unrelated.write_bytes(b"keep")

    def fail_save(book, temporary):
        temporary.write_bytes(b"partial workbook")
        raise OSError("검증용 기록 실패")

    monkeypatch.setattr(Workbook, "save", fail_save)
    result = save_status_report(report(), path)
    assert not result.saved and "기록 실패" in result.message
    assert path.read_bytes() == b"existing workbook"
    assert set(tmp_path.iterdir()) == {path, unrelated}
