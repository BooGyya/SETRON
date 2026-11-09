"""히트맵 표시 어댑터. 비교/위험을 재계산하지 않고 공통 스냅샷을 해석한다."""

from dataclasses import dataclass
from datetime import date
from enum import Enum

from setron.features.risk.schemas import RiskFinding, RiskStatus
from setron.features.version.groups import TIERS_WORST_FIRST, Tier, tier_of
from setron.features.version.schemas import CompareStatus
from setron.ui.dashboard_data import DashboardSnapshot, DashboardSource, family_name
from setron.ui.query_state import AppliedQuery


class GoalState(str, Enum):
    MET = "목표 달성"
    BELOW = "목표 미달"
    UNKNOWN = "정보 부족"
    EXCLUDED = "HPA · 분석 제외"


# 요구사항 3.2: 기준 버전 적용 설비는 초록, 버전이 많이 낮은 설비는 빨강. 임계값은 "추가 정의 필요"라서
# 확정 전까지 N-2 이하를 빨강(많이 낮음)으로 둔다. 기준이 정해지면 이 값만 바꾼다.
FAR_BELOW_STEPS = 2


def compare_tone(status: CompareStatus, steps_below: int | None) -> str:
    """버전 비교 표시 색 (11/05 팀 결정 5단계와 같은 기준). N 이상 초록, N-1·단계 미확인 노랑, N-2 이하 빨강,
    판단 불가 회색. 단계를 모르는 N 미만은 '많이 낮음'으로 단정하지 않고 노랑으로 둔다.
    """
    if status is CompareStatus.AT_OR_ABOVE:
        return "success"
    if status is CompareStatus.UNDETERMINED:
        return "neutral"
    if steps_below is not None and steps_below >= FAR_BELOW_STEPS:
        return "danger"
    return "warning"


# 5단계 색 (11/05 팀 결정·멘토 확인): 진초록 N+1 이상 · 연초록 N · 노랑 N-1 · 빨강 N-2 이하 · 회색 비교 불가
TIER_TONES = {Tier.ABOVE: "strong", Tier.AT: "success", Tier.ONE_BELOW: "warning", Tier.TWO_BELOW: "danger",
              Tier.UNKNOWN: "neutral"}


def worst_tier(tiers) -> Tier:
    """여러 PM·계열 중 가장 낮은 단계. 판정 가능한 단계가 없으면 비교 불가."""
    known = [t for t in tiers if t in TIERS_WORST_FIRST]
    return min(known, key=TIERS_WORST_FIRST.index) if known else Tier.UNKNOWN


def _max_steps(comparisons) -> int | None:
    steps = [c.steps_below for c in comparisons if c.status is CompareStatus.BELOW and c.steps_below is not None]
    return max(steps) if steps else None


@dataclass(frozen=True)
class EquipmentSelection:
    query: AppliedQuery
    base_date: date  # 공통 분석 기준일. HPA의 실제 관측일과 구분한다.
    equipment_id: int
    observation_date: date | None = None


@dataclass(frozen=True)
class TileFamily:
    family_id: int
    title: str
    status: CompareStatus
    versions: tuple[str, ...]
    explanations: tuple[str, ...]
    steps_below: int | None = None  # 이 계열 PM 중 확인된 가장 큰 N 미만 단계. 표시 색에만 쓴다.
    tier: Tier = Tier.UNKNOWN       # 5단계 색 (tier_of). 판단 불가 PM이 있으면 비교 불가

    @property
    def tone(self) -> str:
        return compare_tone(self.status, self.steps_below)


@dataclass(frozen=True)
class EquipmentTile:
    equipment_id: int
    code: str
    site_key: str
    site_label: str
    location: str
    goal: GoalState
    risk: RiskStatus
    families: tuple[TileFamily, ...]
    multi_sw: bool
    pm_mismatch: bool
    observation_date: date | None = None
    raw_versions: tuple[str, ...] = ()
    model_name: str | None = None
    # 화면 표시용 위치 조각. location 문구와 같은 원천이며 판정에는 쓰지 않는다.
    fab_label: str | None = None
    line_label: str | None = None
    area: str | None = None
    steps_below: int | None = None  # 설비 전체에서 확인된 가장 큰 N 미만 단계. 표시 색에만 쓴다.
    tier: Tier = Tier.UNKNOWN       # 설비 전체 5단계 (계열 중 가장 낮은 단계). 확인 필요·HPA는 비교 불가

    findings: tuple[RiskFinding, ...] = ()

    @property
    def risk_detail(self):
        return format_findings(self.findings) or ("세부 근거 미연결" if self.risk is RiskStatus.RISK else "")

    @property
    def version_tone(self) -> str:
        """타일 채움색 (5단계). 목표 판정과 같은 기준이며 위험 판정(테두리)과는 섞지 않는다."""
        if self.goal is GoalState.EXCLUDED:
            return "neutral"
        return TIER_TONES[self.tier]

    @property
    def risk_label(self):
        if self.goal is GoalState.EXCLUDED:
            return "위험 분석 제외"
        return {RiskStatus.RISK: "위험 후보", RiskStatus.NO_RISK: "등록 규칙상 후보 아님",
                RiskStatus.UNKNOWN: "위험 미확인"}[self.risk]

    @property
    def description(self):
        parts = [self.code, self.location, self.goal.value, self.risk_label]
        if self.goal is GoalState.EXCLUDED:
            parts.append(f"{self.model_name or 'HPA'} · 실제 관측일 {self.observation_date}")
            parts.extend(self.raw_versions)
        if self.findings:
            parts.append(self.risk_detail)
        if self.multi_sw:
            parts.append("복수 SW · 타일/물리 대수는 1대")
        if self.pm_mismatch:
            parts.append("같은 계열 PM 버전 불일치")
        for family in self.families:
            parts.append(family.title)
            parts.extend(family.versions)
            parts.extend(family.explanations)
        if not self.families and self.goal is not GoalState.EXCLUDED:
            parts.append("SW 미등록 · 목표 비교 불가")
        return "\n".join(parts)


def format_findings(findings):
    lines = []
    for finding in findings:
        rule = finding.rule
        location = f" · {finding.pm.pm_name or '공통 SW'}" if finding.pm else ""
        lines.append(f"규칙 {rule.risk_rule_id}{location} · {rule.risk_reason} · {finding.message}")
    return "\n".join(dict.fromkeys(lines))


def equipment_tiles(snapshot: DashboardSnapshot, source: DashboardSource) -> tuple[EquipmentTile, ...]:
    """PM 수/계열 수로 설비를 늘리지 않으며, 선택된 PM의 기존 비교 결과를 모두 남긴다."""
    return record_tiles(lookup_records(snapshot), snapshot.summary, source,
                        hpa_locations=dict(snapshot.hpa_lookup_locations))


def lookup_records(snapshot: DashboardSnapshot):
    """일반 선택 SW는 유지하고 HPA만 별도 최신 원본으로 연결한다.

    param
    - snapshot: 같은 날짜의 분석 자료와 별도 HPA 원본 조회 자료.

    return
    - 설비 ID 중복 없이 구성한 표시 전용 관측. 공통 집계에 전달하지 않는다.
    """
    latest = {record.equipment_id: record for record in snapshot.hpa_lookup_records}
    result = []
    for record in snapshot.selected_records:
        if not record.is_hpa:
            result.append(record)
        else:
            # 최신 관측이 현재 조건 밖이면 기존 전달 범위의 같은 날짜 관측을 유지한다.
            result.append(latest.pop(record.equipment_id, record))
    return tuple(result) + tuple(latest.values())


def record_tiles(records, summary, source: DashboardSource, *, hpa_locations=None) -> tuple[EquipmentTile, ...]:
    """선택 SW와 설비 전체의 표시 규칙을 공유한다. summary는 기존 집계 서비스 결과다."""
    analyzed = {item.record.equipment_id: item for item in summary.equipment}
    sites, fabs, lines = ({o.key: o.label for o in options}
                         for options in (source.catalog.sites, source.catalog.fabs, source.catalog.lines))
    models = {mid: group.label for group in source.catalog.groups for mid in source.bindings[group.key]}
    result = []
    for record in records:
        loc = (hpa_locations[record.equipment_id] if record.is_hpa and hpa_locations is not None
               and record.equipment_id in hpa_locations
               else source.locations[record.equipment_id])
        status = analyzed.get(record.equipment_id)
        families = () if status is None else status.families
        if record.is_hpa:
            goal = GoalState.EXCLUDED
        elif any(f.status is CompareStatus.BELOW for f in families):
            goal = GoalState.BELOW
        elif not families or any(f.status is CompareStatus.UNDETERMINED for f in families):
            goal = GoalState.UNKNOWN
        else:
            goal = GoalState.MET
        items = []
        for family in families:
            names = " / ".join(dict.fromkeys(models[p.pm.group_id] for p in family.pms))
            versions, explanations = [], []
            for p in family.pms:
                comparison = p.comparison
                # 원문은 정규화하거나 릴리즈 원문으로 덮어쓰지 않는다.
                raw = p.pm.version_raw if p.pm.version_raw is not None else "미입력"
                versions.append(f"{p.pm.pm_name or '공통'} · {raw} · {comparison.label}")
                target = comparison.target.version_raw if comparison.target else "미확인"
                explanations.append(f"{p.pm.pm_name or '공통'} 목표 N: {target}"
                                    + (f" · {comparison.message}" if comparison.message else ""))
            family_tier = (Tier.UNKNOWN if family.status is CompareStatus.UNDETERMINED
                           else worst_tier(tier_of(p.comparison) for p in family.pms))
            items.append(TileFamily(family.family_id, f"{names} · 계열 {family_name(source, family.family_id)}", family.status,
                                    tuple(versions), tuple(explanations),
                                    _max_steps(p.comparison for p in family.pms), family_tier))
        raw_area = record.line if record.is_hpa else loc.raw_area
        location = (f"{sites[loc.site_key]} · Fab {fabs.get(loc.fab_key, '미지정')} · "
                    f"Line {lines.get(loc.line_key, '미지정')} · 원본 구역 {raw_area or '미확인'}")
        raw_versions = tuple(f"{pm.pm_name or '공통'} · {pm.version_raw if pm.version_raw is not None else '미입력'}"
                             for pm in record.pms) if record.is_hpa else ()
        assessor = getattr(source, "assessor", None)
        assessment = assessor.equipment_risk(record) if assessor and assessor.rules and not record.is_hpa else None
        risk = assessment.status if assessment else source.risk_status(record)
        result.append(EquipmentTile(record.equipment_id, record.equipment_code, loc.site_key, sites[loc.site_key],
                                    location, goal, risk, tuple(items),
                                    len({p.group_id for p in record.pms}) > 1 or len(families) > 1,
                                    any(f.pm_mismatch for f in families), record.base_date, raw_versions,
                                    record.model_name if record.is_hpa else None,
                                    fabs.get(loc.fab_key), lines.get(loc.line_key), raw_area or None,
                                    _max_steps(p.comparison for f in families for p in f.pms),
                                    Tier.UNKNOWN if goal in (GoalState.EXCLUDED, GoalState.UNKNOWN)
                                    else worst_tier(f.tier for f in items), assessment.findings if assessment else ()))
    return tuple(result)
