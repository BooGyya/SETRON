"""BE-025 현황 출력이 실제 조회 범위를 집계·위험·기간·추이에 함께 적용하는지 검증한다."""

from dataclasses import replace
from datetime import date

import pytest

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aggregation.service import summarize
from setron.features.aging.schemas import DEFAULT_THRESHOLDS
from setron.features.export.service import build_status_report
from setron.features.management import ReportFilter
from setron.features.management.report import build_management_status_report
from setron.features.management.schemas import ManagementResult
from setron.features.release.refresh_schemas import DateJudgments, ReleaseRefresh, TargetValidity
from setron.features.risk.schemas import (
    EquipmentRisk, FindingBasis, MappingStatus, RiskFinding, RiskRule, RiskStatus, RiskSummary, RuleType,
)
from setron.features.storage.validation import StorageValidationError
from setron.features.targets.groups import setting_key
from setron.features.version.groups import Slot, VersionGroups
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

from test_management_service import edit, observe, service_for
from test_release_revision import all_rows, revision_storage


GROUPS = {1: "O2", 3: "C3", 4: "HPA"}
FAMILIES = {1: "O2 계열", 2: "C3 계열", 3: "HPA 계열"}


@pytest.fixture
def observed_result():
    """화성 혼합 설비·화성 HPA·평택 일반 설비의 두 실제 관측일을 구성한다."""
    families = {
        1: FamilyReleases(1, (ReleaseVersion(1, 1, "v1", 1, date(2020, 1, 1)),
                              ReleaseVersion(2, 1, "v2", 2, date(2026, 1, 1)))),
        2: FamilyReleases(2, (ReleaseVersion(3, 2, "x1", 1, date(2025, 1, 1)),)),
    }
    targets = (TargetValidity(1, 1, 2, True), TargetValidity(3, 2, 3, True))
    effective = {(1, 1): 2, (3, 2): 3}
    rule = RiskRule(9, RuleType.DIRECT_VERSION, 2, "확인된 C3 결함",
                    release_version_id=3, mapping_status=MappingStatus.VERIFIED)
    dates = []
    for day in (date(2026, 10, 1), date(2026, 10, 3)):
        first = PmVersion("PM1", 1, 1, "v1" if day.day == 1 else "v2", 1 if day.day == 1 else 2)
        second = PmVersion("PM2", 3, 2, "x1", 3)
        mixed = EquipmentRecord(1, "MIX-1", day, "화성", "1라인", "혼합 모델", (first, second))
        normal = EquipmentRecord(2, "NORMAL-2", day, "평택", None, "일반 모델", (PmVersion(None, 1, 1, "v1", 1),))
        hpa = EquipmentRecord(3, "HPA-3", day, "화성", "1라인", "HPA", (PmVersion(None, 4, 3, "h1"),), True)
        records = mixed, normal, hpa
        risks = RiskSummary((
            EquipmentRisk(mixed, RiskStatus.RISK,
                          (RiskFinding(rule, RiskStatus.RISK, FindingBasis.DIRECT_VERSION, second,
                                       families[2].get(3)),)),
            EquipmentRisk(normal, RiskStatus.NO_RISK, ()),
        ), hpa_excluded=1)
        dates.append(DateJudgments(day, summarize(records, families, effective), (), risks, records))
    return ManagementResult((), ReleaseRefresh(families, targets, tuple(dates), ()), (), DEFAULT_THRESHOLDS, 0)


def report(result, *, day=date(2026, 10, 1), filters=None, scope=()):
    """명시한 범위를 공통 보고서 어댑터로 전달한다."""
    return build_management_status_report(result, day, group_names=GROUPS, family_names=FAMILIES,
                                          filters=filters, scope=scope)


def test_hpa_only_date_keeps_exact_summary_without_extending_analysis_trend(observed_result):
    """HPA만 추가된 날짜를 공통 분석 추이에 넣지 않고 그날의 원본 조회는 유지한다."""
    day = date(2026, 10, 4)
    hpa = replace(observed_result.refresh.dates[0].records[-1], base_date=day)
    extra = DateJudgments(day, summarize((hpa,), observed_result.refresh.families, {}), (),
                          RiskSummary((), hpa_excluded=1), (hpa,))
    result = replace(observed_result, refresh=replace(observed_result.refresh,
                     dates=observed_result.refresh.dates + (extra,)))
    value = report(result)
    assert [point.base_date for point in value.trend] == [date(2026, 10, 1), date(2026, 10, 3)]
    hpa_day = report(result, day=day)
    assert hpa_day.summary.base_date == day
    assert hpa_day.summary.equipment_count == 0 and hpa_day.summary.hpa_excluded == 1
    assert day not in {point.base_date for point in hpa_day.trend}


def test_legacy_site_label_filters_actual_equipment_and_workbook(observed_result):
    """화성 조건을 표시하면서 평택을 합산하던 오류를 실제 XLSX 내용으로 검증한다."""
    value = report(observed_result, scope=(("사업장", "화성"),))
    assert value.summary.equipment_count == 1
    assert {e.record.site for e in value.summary.equipment} == {"화성"}
    assert value.summary.hpa_excluded == 1
    assert value.risks.risky == 1
    book = build_status_report(value)
    assert book["조회 조건"]["B3"].value == "화성"
    assert book["요약"]["B2"].value == 1
    assert book["요약"]["B3"].value == 1
    assert book["설비별 판정"].max_row == 3
    book.close()


def test_model_and_family_filter_recalculates_mixed_physical_count_risk_age_and_targets(observed_result):
    """혼합 1호기의 선택 PM만 비교하고 제외된 C3 결함·기간·N을 함께 제외한다."""
    value = report(observed_result, filters=ReportFilter(site_names=("화성",), model_ids=(1,), family_ids=(1,)))
    assert value.summary.equipment_count == 1
    assert value.summary.below == 1 and value.summary.compliant == 0
    assert value.summary.hpa_excluded == 0
    assert [family.family_id for family in value.summary.by_family] == [1]
    assert len(value.summary.equipment[0].record.pms) == 1
    assert value.risks.risky == 0
    assert value.risks.equipment[0].status is RiskStatus.NO_RISK
    assert value.risks.equipment[0].findings == ()
    assert len(value.ages) == 1 and len(value.ages[0].pms) == 1
    assert value.ages[0].oldest.pm.family_id == 1
    assert len(value.targets) == 1 and value.targets[0].model_name == "O2"
    assert value.family_names == {1: "O2 계열"}
    assert [trend.equipment_count for trend in value.trend] == [1, 1]
    assert [trend.below for trend in value.trend] == [1, 0]
    assert observed_result.refresh.dates[0].summary.equipment_count == 2
    assert observed_result.refresh.dates[0].risks.risky == 1


def test_multiple_sites_preserve_one_physical_equipment_and_hpa_exclusion(observed_result):
    """복수 사업장을 선택해도 혼합 PM을 물리 대수로 중복 계산하지 않는다."""
    value = report(observed_result, filters=ReportFilter(site_names=("화성", "평택")))
    assert value.summary.equipment_count == 2
    assert value.summary.compliant == 1 and value.summary.below == 2
    assert value.summary.hpa_excluded == value.risks.hpa_excluded == 1


def test_missing_line_and_empty_equipment_selection_are_distinct_from_all(observed_result):
    """라인 미지정은 None으로 좁히고 빈 설비 선택은 전체로 확대하지 않는다."""
    missing = report(observed_result, filters=ReportFilter(line_names=(None,)))
    assert [e.record.equipment_id for e in missing.summary.equipment] == [2]
    assert missing.summary.hpa_excluded == 0
    empty = report(observed_result, filters=ReportFilter(equipment_ids=()))
    assert empty.summary.equipment_count == 0 and empty.summary.base_date == date(2026, 10, 1)
    assert empty.summary.hpa_excluded == 0 and empty.risks.equipment == ()
    assert empty.ages == empty.targets == ()
    assert all(day.equipment_count == 0 for day in empty.trend)
    assert ("설비", "선택 없음") in empty.scope


def test_exact_date_range_never_fills_missing_observation(observed_result):
    """실제 기록 두 날짜 사이의 빈 날짜를 이전 값으로 채우지 않는다."""
    value = report(observed_result, day=date(2026, 10, 3),
                   filters=ReportFilter(start_date=date(2026, 10, 2), end_date=date(2026, 10, 3)))
    assert [day.base_date for day in value.trend] == [date(2026, 10, 3)]
    with pytest.raises(StorageValidationError, match="기록"):
        report(observed_result, day=date(2026, 10, 2))
    with pytest.raises(StorageValidationError, match="범위 밖"):
        report(observed_result, filters=ReportFilter(start_date=date(2026, 10, 3)))


@pytest.mark.parametrize("filters", [ReportFilter(equipment_ids=()), ReportFilter(model_ids=()), ReportFilter(family_ids=())])
def test_explicit_empty_id_selection_never_expands_to_all(observed_result, filters):
    """UI의 빈 설비·모델·계열 선택을 전체 선택으로 바꿔 출력하지 않는다."""
    value = report(observed_result, filters=filters)
    assert value.summary.equipment_count == 0
    assert value.summary.hpa_excluded == 0
    assert value.summary.base_date == date(2026, 10, 1)
    assert value.targets == value.ages == value.risks.equipment == ()


@pytest.mark.parametrize("filters", [
    "화성", ReportFilter(site_names=["화성"]), ReportFilter(site_names=("화성", "화성")),
    ReportFilter(model_ids=(True,)), ReportFilter(family_ids=(0,)), ReportFilter(equipment_ids=[1]),
    ReportFilter(line_names=(5,)), ReportFilter(start_date="2026-10-01"),
    ReportFilter(start_date=date(2026, 10, 3), end_date=date(2026, 10, 1)),
])
def test_invalid_report_filters_fail_without_generating_mislabelled_data(observed_result, filters):
    """잘못된 필터를 조건 문구만 붙인 전체 출력으로 대체하지 않는다."""
    with pytest.raises(StorageValidationError):
        report(observed_result, filters=filters)


@pytest.mark.parametrize("scope", [(("모델", "임의 모델"),), (("사업장", "전체"),),
                                  (("사업장", "화성"), ("Site", "평택"))])
def test_unsupported_or_ambiguous_legacy_labels_require_explicit_filter(observed_result, scope):
    """자유 형식 조건을 실제 필터로 임의 추정하지 않는다."""
    with pytest.raises(StorageValidationError):
        report(observed_result, scope=scope)


def test_labels_cannot_conflict_with_explicit_filter(observed_result):
    """화성 필터에 평택 조건 문구를 붙여 출력할 수 없다."""
    with pytest.raises(StorageValidationError, match="표기"):
        report(observed_result, filters=ReportFilter(site_names=("화성",)), scope=(("사업장", "평택"),))


def test_legacy_hpa_count_cannot_be_guessed_for_narrowed_scope(observed_result):
    """이전 반환 형식에 HPA 원본이 없으면 범위별 제외 대수를 0으로 꾸미지 않는다."""
    legacy = replace(observed_result, refresh=replace(observed_result.refresh,
        dates=tuple(replace(day, records=()) for day in observed_result.refresh.dates)))
    assert report(legacy).summary.hpa_excluded == 1
    with pytest.raises(StorageValidationError, match="HPA"):
        report(legacy, filters=ReportFilter(site_names=("화성",)))


def test_current_report_reads_new_saved_values_without_journal_or_ai(revision_storage):
    """저장 결과 캐시가 없어도 현재 DB의 수정값·범위를 읽고 추가 수정 이력은 만들지 않는다."""
    storage = revision_storage
    service, snapshot, first, _ = service_for(storage)
    other = observe(storage, first, code="EQ-2")
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": other.snapshot_id}, {"site": "평택"})
    service.save((edit(service, "equipment_snapshot", {"snapshot_id": snapshot.snapshot_id}, {"line": "2라인"}),))
    before = all_rows(storage, "import_batch")
    value = service.current_report(snapshot.base_date, filters=ReportFilter(site_names=("화성",)))
    assert value.summary.equipment_count == 1
    assert value.summary.equipment[0].record.line == "2라인"
    assert value.summary.equipment[0].record.site == "화성"
    assert all_rows(storage, "import_batch") == before


def test_status_report_ranks_priority_with_same_scope_risk_and_targets(observed_result):
    """BE-017: 현황 보고서에 같은 범위·위험 판정·목표 N으로 계산한 우선순위를 담는다."""
    value = report(observed_result)

    assert [e.record.equipment_code for e in value.priority.ranked] == ["MIX-1", "NORMAL-2"]  # 위험 → N-1
    assert value.priority.ranked[0].is_risk and value.priority.ranked[1].steps_behind == 1
    assert value.priority.hpa_excluded == 1 and value.priority.has_rules
    filtered = report(observed_result, filters=ReportFilter(site_names=("평택",)))
    assert [e.record.equipment_code for e in filtered.priority.ranked] == ["NORMAL-2"]


def test_saved_n_groups_replace_single_target_in_status_report(observed_result):
    """QA Q-10: 저장된 N 그룹이 있으면 현황 보고서도 화면처럼 그룹으로 판정·추이·우선순위·목표 표기를 계산한다."""
    single = report(observed_result)
    assert single.summary.by_family[0].compliant == 0          # 단일 목표 v2: 10-01의 v1 설비 2건은 미달

    groups = {(1, 1): VersionGroups({Slot.N: frozenset({1})})}   # v1을 N 칸에 넣은 그룹
    value = build_management_status_report(observed_result, date(2026, 10, 1), group_names=GROUPS,
                                           family_names=FAMILIES, saved_groups=groups)

    o2 = next(f for f in value.summary.by_family if f.family_id == 1)
    assert o2.compliant == 2 and o2.below == 0                  # v1 = N → 적용 완료
    assert all(point.by_family[0].below == 0 for point in value.trend)   # 추이도 같은 그룹 기준
    rows = {(t.model_name, t.family_name): t.version_raw for t in value.targets}
    assert rows[("O2", "O2 계열")] == "N 그룹 · N: v1"
    assert rows[("C3", "C3 계열")] == "x1"                # 그룹이 없는 모델·계열은 기존 단일 목표 유지
    assert all(e.steps_behind in (None, 0) for e in value.priority.ranked if e.record.equipment_code == "NORMAL-2")


def test_current_report_reads_saved_n_groups_in_same_transaction(revision_storage):
    """QA Q-10: 데이터 내보내기(현재 조회 현황)는 DB에 저장된 N 그룹을 같은 트랜잭션에서 읽어 반영한다."""
    storage = revision_storage
    service, snapshot, first, _ = service_for(storage)   # 단일 목표 v2, 설비 v1
    before = service.current_report(snapshot.base_date)
    assert before.summary.by_family[0].below == 1

    groups = VersionGroups({Slot.N: frozenset({first})})
    with storage.transaction() as tx:
        tx.insert("app_setting", {"setting_key": setting_key((1, 1)), "setting_value": {
            "schema": 1, "model_id": 1, "family_id": 1,
            "history": [{"saved_at": "2026-11-09T10:00:00", "memo": "", "groups": groups.to_dict()}]}})
    after = service.current_report(snapshot.base_date)

    assert after.summary.by_family[0].compliant == 1 and after.summary.by_family[0].below == 0
    assert after.targets[0].version_raw.startswith("N 그룹 · N: v1")
