"""관리 저장 결과를 같은 조회 범위의 현황 XLSX 입력으로 바꾼다."""

from dataclasses import dataclass, replace
from datetime import date

from setron.features.aggregation.service import summarize
from setron.features.aging.service import equipment_age
from setron.features.export.schemas import StatusReport, TargetRow
from setron.features.risk.schemas import EquipmentRisk, RiskStatus, RiskSummary
from setron.features.storage.inputs import identifier, require, required_text

from .schemas import ManagementResult


@dataclass(frozen=True)
class ReportFilter:
    """원문·DB ID로 명시한 출력 범위.

    모델·계열 선택은 PM을 먼저 좁힌 뒤 설비 대수와 적용률을 다시 계산한다.
    ID 선택은 None이 전체이고 빈 튜플은 선택 없음이다. 빈 화면을 전체로 내보내지 않는다.
    사업장·라인의 빈 튜플은 전체다.
    line_names의 None은 라인 미지정이며 빈 튜플(전체)과 구분한다.
    날짜 경계는 추이에도 적용하고 기록이 없는 날짜를 만들어 채우지 않는다.
    """

    site_names: tuple[str, ...] = ()
    line_names: tuple[str | None, ...] = ()
    model_ids: tuple[int, ...] | None = None
    family_ids: tuple[int, ...] | None = None
    equipment_ids: tuple[int, ...] | None = None
    start_date: date | None = None
    end_date: date | None = None


def build_management_status_report(result, base_date, *, group_names, family_names, scope=(), filters=None):
    """전체 저장 결과에서 출력 범위를 먼저 적용한 뒤 표·차트·위험·노후도를 산출한다.

    이전 호출의 단일 사업장/Site·라인/Line 표기는 정확한 원문 필터로 해석한다.
    자유 형식 조건을 단순히 붙이는 것은 허용하지 않는다. 새 호출자는 ReportFilter를 쓴다.
    """
    require(isinstance(result, ManagementResult), "ManagementResult 형식이 필요합니다.")
    return build_status_report_from_refresh(result.refresh, result.thresholds, base_date,
        group_names=group_names, family_names=family_names, scope=scope, filters=filters)


def build_status_report_from_refresh(refresh, thresholds, base_date, *, group_names, family_names, scope=(), filters=None):
    """같은 관측 시점의 재판정·기간 설정을 출력 범위로 좁힌다."""
    require(type(base_date) is date, "출력 기준 날짜가 필요합니다.")
    selected = _resolve_filter(filters, scope)
    _validate_filter(selected)
    require(_in_dates(base_date, selected), "출력 기준일이 선택한 날짜 범위 밖입니다.")
    observed = next((day for day in refresh.dates if day.base_date == base_date), None)
    require(observed is not None, "선택한 기준일의 기록이 없습니다.")
    targets = {(target.model_id, target.family_id): target.selected_release_id
               for target in refresh.targets if target.valid and target.selected_release_id is not None}
    summaries, records_by_date = {}, {}
    for day in refresh.dates:
        if not _in_dates(day.base_date, selected):
            continue
        complete_records = getattr(day, "records", ())
        if not complete_records:
            require(not _has_filter(selected) or day.summary.hpa_excluded == 0,
                    "범위별 HPA 제외 대수를 확인할 원본 관측이 없습니다. 최신 자료를 다시 조회해 주세요.")
            complete_records = tuple(equipment.record for equipment in day.summary.equipment)
        records = tuple(record for value in complete_records if (record := _filter_record(value, selected)) is not None)
        summary = (day.summary if records == tuple(complete_records) else
                   replace(summarize(records, refresh.families, targets), base_date=day.base_date))
        if not getattr(day, "records", ()) and not _has_filter(selected):
            summary = replace(summary, hpa_excluded=day.summary.hpa_excluded)
        summaries[day.base_date], records_by_date[day.base_date] = summary, records

    summary = summaries[base_date]
    records = records_by_date[base_date]
    pairs = {(pm.group_id, pm.family_id) for record in records if not record.is_hpa for pm in record.pms}
    rows = []
    for target in refresh.targets:
        if (target.model_id, target.family_id) not in pairs:
            continue
        family = refresh.families.get(target.family_id)
        release = family.get(target.selected_release_id) if family is not None else None
        rows.append(TargetRow(group_names.get(target.model_id, f"모델 {target.model_id}"),
                              family_names.get(target.family_id, f"계열 {target.family_id}"),
                              release.version_raw if release else None))
    ages = tuple(equipment_age(equipment.record, refresh.families, thresholds)
                 for equipment in summary.equipment)
    analysis_days = {day.base_date for day in refresh.dates
                     if any(not record.is_hpa for record in getattr(day, "records", ()))
                     or day.summary.equipment_count > 0}
    return StatusReport(
        summary=summary, scope=_labels(selected, group_names, family_names, refresh),
        targets=tuple(rows), family_names={fid: name for fid, name in family_names.items()
                                         if selected.family_ids is None or fid in selected.family_ids},
        risks=_filter_risks(observed.risks, records, summary.hpa_excluded),
        ages=ages, thresholds=thresholds,
        trend=tuple(summaries[day] for day in sorted(summaries) if day in analysis_days),
    )


def _validate_filter(value):
    require(isinstance(value, ReportFilter), "ReportFilter 형식이 필요합니다.")
    for field in ("site_names", "line_names", "model_ids", "family_ids", "equipment_ids"):
        values = getattr(value, field)
        if field.endswith("_ids") and values is None:
            continue
        require(isinstance(values, tuple), f"{field}: 선택값 튜플이 필요합니다.")
        for item in values:
            if field.endswith("_ids"):
                identifier(item, field)
            elif field == "site_names":
                required_text(item, field, 50)
            else:
                require(item is None or isinstance(item, str) and len(item) <= 50,
                        "라인 원문 또는 미지정(None)이 필요합니다.")
        require(len(set(values)) == len(values), f"{field}: 선택값이 중복됐습니다.")
    for field in ("start_date", "end_date"):
        require(getattr(value, field) is None or type(getattr(value, field)) is date,
                f"{field}: 날짜 또는 None이 필요합니다.")
    require(value.start_date is None or value.end_date is None or value.start_date <= value.end_date,
            "출력 시작일은 종료일보다 늦을 수 없습니다.")


def _resolve_filter(filters, scope):
    require(isinstance(scope, tuple), "조회 조건 표시는 항목·값 튜플이어야 합니다.")
    require(filters is None or isinstance(filters, ReportFilter), "ReportFilter 형식이 필요합니다.")
    legacy = {}
    aliases = {"사업장": "site_names", "Site": "site_names", "라인": "line_names", "Line": "line_names"}
    for entry in scope:
        require(isinstance(entry, tuple) and len(entry) == 2 and all(isinstance(part, str) for part in entry),
                "조회 조건은 항목·값 문자열 쌍이어야 합니다.")
        label, raw = entry
        require(label in aliases, "문자열 조건을 임의로 출력할 수 없습니다. 명시적인 ReportFilter를 사용해 주세요.")
        field = aliases[label]
        require(field not in legacy, "조회 조건 항목이 중복됐습니다. 복수 범위는 ReportFilter로 지정해 주세요.")
        require(raw not in {"전체", "All", "ALL"}, "전체 범위는 빈 ReportFilter로 지정해 주세요.")
        legacy[field] = (raw,)
    if filters is None:
        return ReportFilter(**legacy)
    for field, values in legacy.items():
        require(getattr(filters, field) == values, "조건 표기와 실제 출력 범위가 다릅니다.")
    return filters


def _has_filter(value):
    return any((value.site_names, value.line_names,
                value.model_ids is not None, value.family_ids is not None, value.equipment_ids is not None))


def _in_dates(day, value):
    return (value.start_date is None or day >= value.start_date) and (value.end_date is None or day <= value.end_date)


def _filter_record(record, value):
    if value.site_names and record.site not in value.site_names:
        return None
    if value.line_names and record.line not in value.line_names:
        return None
    if value.equipment_ids is not None and record.equipment_id not in value.equipment_ids:
        return None
    pms = tuple(pm for pm in record.pms
                if (value.model_ids is None or pm.group_id in value.model_ids)
                and (value.family_ids is None or pm.family_id in value.family_ids))
    if (value.model_ids is not None or value.family_ids is not None) and not pms:
        return None
    return replace(record, pms=pms)


def _filter_risks(original, records, hpa_excluded):
    scoped = {record.equipment_id: record for record in records if not record.is_hpa}
    if hpa_excluded == original.hpa_excluded and scoped == {e.record.equipment_id: e.record for e in original.equipment}:
        return original
    results = []
    for equipment in original.equipment:
        record = scoped.get(equipment.record.equipment_id)
        if record is None:
            continue
        findings = tuple(finding for finding in equipment.findings
                         if (finding.pm in record.pms if finding.pm is not None
                             else any(pm.family_id == finding.rule.family_id
                                      and (finding.rule.group_id is None or pm.group_id == finding.rule.group_id)
                                      for pm in record.pms)))
        statuses = {finding.status for finding in findings}
        status = (RiskStatus.RISK if RiskStatus.RISK in statuses else
                  RiskStatus.UNKNOWN if RiskStatus.UNKNOWN in statuses else RiskStatus.NO_RISK)
        results.append(EquipmentRisk(record, status, findings))
    return RiskSummary(tuple(results), hpa_excluded=hpa_excluded, has_rules=original.has_rules)


def _labels(value, groups, families, refresh):
    labels = []
    if value.site_names:
        labels.append(("사업장", " · ".join(value.site_names)))
    if value.line_names:
        labels.append(("라인", " · ".join("미지정" if name is None else name or "빈 값" for name in value.line_names)))
    if value.model_ids is not None:
        labels.append(("모델", " · ".join(groups.get(gid, f"모델 {gid}") for gid in value.model_ids) or "선택 없음"))
    if value.family_ids is not None:
        labels.append(("계열", " · ".join(families.get(fid, f"계열 {fid}") for fid in value.family_ids) or "선택 없음"))
    if value.equipment_ids is not None:
        codes = {record.equipment_id: record.equipment_code for day in refresh.dates
                 for record in (getattr(day, "records", ()) or tuple(e.record for e in day.summary.equipment))}
        labels.append(("설비", " · ".join(codes.get(eid, f"설비 {eid}") for eid in value.equipment_ids) or "선택 없음"))
    if value.start_date is not None:
        labels.append(("시작일", value.start_date.isoformat()))
    if value.end_date is not None:
        labels.append(("종료일", value.end_date.isoformat()))
    return tuple(labels)
