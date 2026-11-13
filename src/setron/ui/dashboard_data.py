"""대시보드 읽기 어댑터. 확정된 위치/설비군 매핑과 기존 BE 집계를 사용한다. DB 접근 없음."""

from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
from typing import Callable, Mapping

from setron.features.aggregation.schemas import EquipmentRecord, Summary
from setron.features.aggregation.service import Targets, summarize
from setron.features.production_area import production_area_text
from setron.features.risk.schemas import RiskStatus
from setron.features.targets.groups import with_saved_groups
from setron.features.version.groups import VersionGroups
from setron.features.version.schemas import CompareReason, CompareStatus
from setron.features.version.service import FamilyReleases
from setron.ui.observation_interval import INTERVALS, interval_days
from setron.ui.query_state import AppliedQuery, QueryCatalog, TargetN, matches_selection, selection_keys
from setron.ui.version_filter import matches_versions


class HpaClassificationConflict(ValueError):
    """같은 호기의 상충하는 분류 이력을 원문 관측일과 함께 검토하도록 알린다."""

    def __init__(self, records: tuple[EquipmentRecord, ...]):
        self.observations = tuple(sorted({
            (record.equipment_code, record.base_date, record.is_hpa) for record in records
        }))
        details = "\n".join(f"{code} · {day.isoformat()} · {'HPA' if is_hpa else '일반 설비'}"
                            for code, day, is_hpa in self.observations)
        super().__init__("같은 호기에 HPA와 일반 설비 분류 이력이 함께 있어 조회를 보류했습니다. "
                         "원문 분류를 검토하세요.\n" + details)



class NoObservationBefore(ValueError):
    """선택한 기준일 이전에 저장된 관측이 없다. 다른 날짜의 결과로 대신하지 않는다."""

    def __init__(self, day):
        super().__init__(f"{day.isoformat()} 이전에 저장된 관측 자료가 없습니다. 기준일을 바꾸거나 '오늘'을 눌러 주세요.")


def _until(dates, as_of):
    """기준일(포함) 이전 날짜만 남긴다. as_of가 없으면 전체."""
    return tuple(dates) if as_of is None else tuple(d for d in dates if d <= as_of)

@dataclass(frozen=True)
class GroupBinding:
    group_key: str
    model_ids: tuple[int, ...]  # UI 설비군 → 모델 ID. 계열 ID로 추정하지 않는다.


@dataclass(frozen=True)
class EquipmentLocation:
    site_key: str
    raw_area: str | None = None
    fab_key: str | None = None
    line_key: str | None = None


def _matches_location(selection, location: EquipmentLocation) -> bool:
    return (matches_selection(selection.site_key, location.site_key)
            and matches_selection(selection.fab_key, location.fab_key)
            and matches_selection(selection.line_key, location.line_key))


@dataclass(frozen=True)
class DashboardCell:
    label: str
    group_keys: tuple[str, ...]
    equipment_ids: tuple[int, ...]
    risky_ids: tuple[int, ...]
    summary: Summary


@dataclass(frozen=True)
class DashboardRow:
    label: str
    position: tuple[str, str | None] | None  # None은 전체 행, (Site, None)은 원본 구역 미확인
    cells: tuple[DashboardCell, ...]


def denominator_equipment_ids(summary: Summary) -> tuple[int, ...]:
    """적용률 분모에 비교 가능한 계열이 하나 이상 포함된 물리 설비."""
    determined = (CompareStatus.AT_OR_ABOVE, CompareStatus.BELOW)
    return tuple(equipment.record.equipment_id for equipment in summary.equipment
                 if any(family.status in determined for family in equipment.families))


@dataclass(frozen=True)
class TrendPoint:
    day: date
    summary: Summary
    missing_equipment: int
    source_day: date | None = None  # 간격 시점의 집계가 나온 실제 관측일. 원래 관측점은 None.

    @property
    def denominator_equipment_ids(self) -> tuple[int, ...]:
        return denominator_equipment_ids(self.summary)


@dataclass(frozen=True)
class GroupTrend:
    group_key: str
    label: str
    points: tuple[TrendPoint, ...]


class RiskRuleState(Enum):
    UNREGISTERED = "위험 규칙 미등록"
    ALL_DISABLED = "위험 규칙 모두 비활성"
    ACTIVE = "위험 규칙 사용 중"


@dataclass(frozen=True)
class DashboardSnapshot:
    query: AppliedQuery
    base_date: date
    equipment_ids: tuple[int, ...]
    risky_ids: tuple[int, ...]
    risk_unknown: int
    summary: Summary
    rows: tuple[DashboardRow, ...]
    groups: tuple[DashboardCell, ...]
    trends: tuple[GroupTrend, ...]
    observations: tuple[TrendPoint, ...]
    source_label: str
    selected_records: tuple[EquipmentRecord, ...]  # 공통 분석과 드릴다운의 같은 기준일·필터 결과
    target_values: tuple[TargetN, ...]  # 적용 시점의 개별 목표 N. 저장 그룹은 별도로 보존
    hpa_lookup_records: tuple[EquipmentRecord, ...] = ()  # 공통 분석과 분리한 호기별 최신 원본 관측
    hpa_lookup_locations: tuple[tuple[int, EquipmentLocation], ...] = ()
    saved_groups: tuple[tuple[tuple[int, int], VersionGroups], ...] = ()
    risk_rule_state: RiskRuleState | None = None  # 규칙 목록을 제공하지 않는 자료는 판정 결과만 표시한다.

    @property
    def effective_targets(self) -> Targets:
        """드릴다운에서도 조회 당시와 같은 모델·계열별 기준으로 비교한다."""
        single = {(target.model_id, target.family_id): target.release_id for target in self.target_values}
        return with_saved_groups(single, dict(self.saved_groups))


def trend_window(snapshot: DashboardSnapshot, start: date, end: date, step=None) -> DashboardSnapshot:
    """기준 설비 집합과 집계는 유지하고 추이의 표시 기간·시점만 선택한다.

    저장일마다는 실제 관측점만 남긴다. 양의 정수 간격은 지정 일수마다 시점을 만든다.
    간격 시점은 그 시점 이전 마지막 관측을 사용하며,
    마지막 관측의 달성률이 계산 불가이면 그대로 둔다. 이전의 계산 가능한 관측으로 건너뛰지 않는다.
    """
    if type(start) is not date or type(end) is not date or start > end:
        raise ValueError("시작 날짜와 종료 날짜를 확인해 주세요.")
    custom_days = isinstance(step, int) and not isinstance(step, bool) and step > 0
    if step is not None and not custom_days and (not isinstance(step, str) or step not in INTERVALS):
        raise ValueError("관측 간격을 확인해 주세요.")
    days = tuple(point.day for point in snapshot.observations)
    if step is None:
        selected = tuple((day, index) for index, day in enumerate(days) if start <= day <= end)
    else:
        selected = tuple((day, index) for day in interval_days(start, end, step)
                         if (index := bisect_right(days, day) - 1) >= 0)

    def points_for(points):
        return tuple(points[index] if step is None else
                     replace(points[index], day=day, source_day=points[index].source_day or points[index].day)
                     for day, index in selected)

    return replace(snapshot, observations=points_for(snapshot.observations),
                   trends=tuple(replace(group, points=points_for(group.points)) for group in snapshot.trends))


@dataclass(frozen=True)
class DashboardDrilldown:
    query: AppliedQuery
    base_date: date
    label: str
    equipment_ids: tuple[int, ...]
    risk_only: bool = False


def missing_sw_count(summary: Summary) -> int:
    """계열 자체가 등록되지 않아 건수 분모에도 잡히지 않는 물리 설비를 별도 표시한다."""
    return sum(not equipment.families for equipment in summary.equipment)


# 판단 불가 이유를 사용자가 고칠 수 있는 단위로 묶는다 (QA Q-07). 목표 N 미지정을 버전 문제로 오해하지 않게 한다.
UNDETERMINED_GROUPS = (
    ("목표 N 미지정", (CompareReason.TARGET_NOT_SET, CompareReason.TARGET_INVALID)),
    ("릴리즈 노트 없음", (CompareReason.NO_RELEASE_DATA,)),
    ("버전 미입력", (CompareReason.MISSING_VERSION,)),
    ("릴리즈 노트에 없는 버전", (CompareReason.NOT_REGISTERED, CompareReason.OTHER_FAMILY)),
    ("순서 미확인", (CompareReason.ORDER_UNKNOWN, CompareReason.ORDER_CONFLICT)),
)


def undetermined_breakdown(summary: Summary) -> str:
    """판단 불가 건(설비×계열)의 사유와 개수를 한 줄씩 표시한다."""
    counts = dict.fromkeys((name for name, _ in UNDETERMINED_GROUPS), 0)
    names = {reason: name for name, reasons in UNDETERMINED_GROUPS for reason in reasons}
    for equipment in summary.equipment:
        for family in equipment.families:
            if family.status is not CompareStatus.UNDETERMINED:
                continue
            reason = next((p.comparison.reason for p in family.pms
                           if p.comparison.status is CompareStatus.UNDETERMINED and p.comparison.reason), None)
            if reason in names:
                counts[names[reason]] += 1
    return "\n".join(f"{name} {count}개" for name, count in counts.items() if count)


def information_gap_reasons(summary: Summary) -> str:
    """달성률에 포함되지 않은 SW 미등록 설비와 판단 불가 건의 사유를 표시한다."""
    reasons = []
    missing = missing_sw_count(summary)
    if missing:
        reasons.append(f"SW 미등록 {missing}대")
    breakdown = undetermined_breakdown(summary)
    if breakdown:
        reasons.append(breakdown)
    return "\n".join(reasons)


def count_value(count: int, unknown: int, unknown_label: str) -> str:
    """요약 카드의 큰 숫자. 0대인데 판단하지 못한 대상이 있으면 '0대' 대신 ``unknown_label``을 보인다.

    0대가 '문제 없음'으로 읽히지 않게 한다. 1대 이상이면 확인된 대수를 그대로 보인다(미확인 수는 설명 줄에 적는다).
    """
    return unknown_label if count == 0 and unknown > 0 else f"{count}대"


def upgrade_caption(base: str, undetermined: int) -> str:
    return base + (f" · 판단 불가 {undetermined}건" if undetermined else "")


def family_name(source, family_id) -> str:
    """화면 표시용 계열 이름. DB 공급자는 이름을 갖고, 이름이 없는 합성 자료는 번호를 그대로 쓴다."""
    names = getattr(source, "family_names", None) or {}
    if family_id in names:
        return production_area_text(names[family_id])
    for pair in getattr(getattr(source, "version_catalog", None), "pairs", ()):
        if pair.family_id == family_id:
            return production_area_text(pair.family_name)
    return str(family_id)


def model_name(source, model_id) -> str:
    """화면 표시용 모델 이름. 등록 모델 이름 → 연결된 설비군 이름 → '모델 n' 순서로 찾는다."""
    names = getattr(source, "model_names", None) or {}
    if model_id in names:
        return names[model_id]
    labels = {group.key: group.label for group in source.catalog.groups}
    for key, model_ids in source.bindings.items():
        if model_id in model_ids:
            return labels.get(key, f"모델 {model_id}")
    return f"모델 {model_id}"


class DashboardSource:
    """기준일 위치로 선택한 설비 집합과 고정 N을 모든 보기·과거 관측에서 공유한다."""

    result_label = "예시 결과"  # 화면 결과 배지. DB 공급자는 "DB 자료"로 바꾼다.
    # 기준일 조회에서 그날 관측되지 않은 일반 설비를 이전 마지막 관측으로 채운다 (QA Q-01, 11-09 결정).
    # Version Replay는 날짜별 저장 현황을 그대로 재생하므로 False로 둔다.
    carry_forward = True

    def __init__(self, *, catalog: QueryCatalog, bindings: tuple[GroupBinding, ...],
                 records: tuple[EquipmentRecord, ...], locations: Mapping[int, EquipmentLocation],
                 families: Mapping[int, FamilyReleases], targets: Mapping[tuple[int, int], int],
                 risks: Mapping[int, RiskStatus], source_label: str,
                 risk_evaluator: Callable[[EquipmentRecord], RiskStatus] | None = None,
                 risk_rule_state: RiskRuleState | None = None,
                 locations_by_date: Mapping[tuple[date, int], EquipmentLocation] | None = None):
        self.catalog, self.records = catalog, tuple(records)
        self.locations, self.families = dict(locations), dict(families)
        self.locations_by_date = dict(locations_by_date or {})
        self.targets, self.risks = dict(targets), dict(risks)
        self.risk_evaluator = risk_evaluator
        self.risk_rule_state = risk_rule_state
        # 모델·계열별로 저장한 N 그룹(버전(N) 기준 입력). 있으면 목표 N 하나 대신 이 그룹으로 판정한다.
        self.saved_groups = {}
        self.source_label = source_label
        self.bindings = {b.group_key: tuple(b.model_ids) for b in bindings}
        group_keys = {o.key for o in catalog.groups}
        if len(self.bindings) != len(bindings) or set(self.bindings) != group_keys:
            raise ValueError("모든 UI 설비군에 중복 없는 모델 매핑이 필요합니다.")
        models = [model for values in self.bindings.values() for model in values]
        if len(models) != len(set(models)):
            raise ValueError("모델 ID를 여러 UI 설비군에 중복 등록할 수 없습니다.")
        seen = set()
        sites, fabs, lines = ({o.key: o for o in options} for options in (catalog.sites, catalog.fabs, catalog.lines))
        for record in self.records:
            identity = record.base_date, record.equipment_id
            if identity in seen:
                raise ValueError("같은 관측일의 설비가 중복되었습니다.")
            seen.add(identity)
            if record.equipment_id not in self.locations:
                raise ValueError("설비의 명시적 위치 매핑이 필요합니다.")
            if any(pm.group_id not in models for pm in record.pms) and not record.is_hpa:
                raise ValueError("등록되지 않은 모델 매핑입니다.")
        for loc in (*self.locations.values(), *self.locations_by_date.values()):
            if loc.site_key not in sites:
                raise ValueError("등록되지 않은 Site 매핑입니다.")
            if loc.fab_key is not None and (loc.fab_key not in fabs or fabs[loc.fab_key].site_key not in (None, loc.site_key)):
                raise ValueError("Fab 매핑의 부모 Site가 다릅니다.")
            if loc.line_key is not None and (loc.line_key not in lines or
                lines[loc.line_key].site_key != loc.site_key or lines[loc.line_key].fab_key != loc.fab_key):
                raise ValueError("Line 매핑의 부모 위치가 다릅니다.")
        if any(not isinstance(status, RiskStatus) for status in self.risks.values()):
            raise ValueError("위험 판정 상태 형식이 올바르지 않습니다.")
        self.dates = tuple(sorted({r.base_date for r in self.records}))
        self.analysis_dates = tuple(sorted({r.base_date for r in self.records if not r.is_hpa}))

    def location_for(self, record):
        """관측일에 저장된 생산 영역·Line을 쓰고, 기존 공급자는 명시적 매핑을 유지한다."""
        return self.locations_by_date.get((record.base_date, record.equipment_id), self.locations[record.equipment_id])

    def current_records(self, as_of: date | None = None) -> tuple[EquipmentRecord, ...]:
        """기준일(포함) 이전 관측 중 설비마다 가장 최근 관측을 돌려준다 (QA Q-01).

        현황표는 사업장·시트 단위로 따로 들어오므로, 가장 최근 날짜 하나만 고르면 그날 넣지 않은
        사업장·시트의 설비가 사라진다. 설비별로 마지막 관측을 쓰고 실제 관측일(base_date)은 그대로 둔다.

        return
        - 설비 ID 오름차순. HPA도 같은 규칙으로 포함한다(집계에서는 제외 대수로만 센다).
        """
        latest = {}
        for record in self.records:
            if as_of is not None and record.base_date > as_of:
                continue
            previous = latest.get(record.equipment_id)
            if previous is None or record.base_date > previous.base_date:
                latest[record.equipment_id] = record
        return tuple(latest[equipment_id] for equipment_id in sorted(latest))

    def snapshot_records(self, day: date) -> tuple[EquipmentRecord, ...]:
        """스냅샷에 넣을 관측: 분석 기준일(day)까지 설비별 최신 관측 (QA Q-01).

        day는 기준일 이전 가장 최근 일반 관측일이다. 그보다 뒤에 관측된 HPA는 지금처럼 별도 최신 HPA
        목록(``_hpa_lookup``)으로만 보여 주고 집계·세부 목록에 넣지 않는다.
        ``carry_forward``가 False인 공급자(Version Replay)는 그날 실제 관측만 쓴다(이웃 날짜 복사 없음).
        """
        if not self.carry_forward:
            return tuple(r for r in self.records if r.base_date == day)
        return self.current_records(day)

    def latest_hpa_records(self, until: date | None = None) -> tuple[EquipmentRecord, ...]:
        """공통 분석 기준일과 독립적으로 호기별 최신 HPA 원본 관측을 조회한다.

        return
        - 설비 ID 오름차순의 최신 HPA 관측. 실제 관측일·원본 구역·SW를 바꾸지 않는다.

        raises
        - HpaClassificationConflict: 같은 설비 ID의 이력에 일반 설비와 HPA 분류가 함께 있는 경우.
          분류가 바뀐 이유를 추정하거나 두 관측을 합치지 않고 원문 검토를 요청한다.
        """
        hpa_ids, analysis_ids, latest = set(), set(), {}
        for record in self.records:
            equipment_id = record.equipment_id
            if record.is_hpa:
                hpa_ids.add(equipment_id)
                if until is not None and record.base_date > until:
                    continue  # 기준일 이후 관측은 그 날짜의 화면에 섞지 않는다.
                previous = latest.get(equipment_id)
                if previous is None or record.base_date > previous.base_date:
                    latest[equipment_id] = record
            else:
                analysis_ids.add(equipment_id)
        conflicts = hpa_ids & analysis_ids
        if conflicts:
            raise HpaClassificationConflict(tuple(record for record in self.records
                                                   if record.equipment_id in conflicts))
        return tuple(latest[equipment_id] for equipment_id in sorted(latest))

    def _hpa_lookup(self, query):
        """최신 원본의 사업장·구역과 유효한 명시적 위치 매핑을 고정한다.

        param
        - query: 현재 적용한 공통 조건. 특정 일반 설비군을 고르면 HPA는 제외한다.

        return
        - 조건에 맞는 원본 관측과 불변 위치 대응 튜플. 새 구역의 Fab·Line은 추정하지 않는다.
        """
        latest = self.latest_hpa_records(query.selection.as_of)
        selection = query.selection
        if selection.group_key is not None or selection.item_family_id is not None:
            return (), ()
        sites = {option.label: option.key for option in self.catalog.sites}
        records, locations = [], []
        for record in latest:
            previous = self.location_for(record)
            site_key = sites.get(record.site, previous.site_key)
            stale_mapping = site_key != previous.site_key or previous.raw_area != record.line
            location = EquipmentLocation(site_key, record.line,
                                         None if stale_mapping else previous.fab_key,
                                         None if stale_mapping else previous.line_key)
            if not _matches_location(selection, location):
                continue
            if not matches_versions(record, selection.versions):
                continue
            records.append(record)
            locations.append((record.equipment_id, location))
        return tuple(records), tuple(locations)

    def scoped_records(self, query: AppliedQuery) -> tuple[EquipmentRecord, ...]:
        """같은 조회 설비의 관측 이력. 버전 필터는 기준일에서만 코호트를 정한다.

        과거 버전이 지금 선택한 원문과 달라도 이력을 제거하지 않는다. AI 변경 근거·예측도
        화면의 날짜별 추이와 같은 물리 설비 집합을 읽으며, 미관측을 0으로 만들지 않는다.
        """
        if query.selection.versions is None:
            return self._scope_records(self.records, query)
        candidates = _until(self.analysis_dates or self.dates, query.selection.as_of)
        if not candidates:
            return ()
        cohort = {r.equipment_id for r in self._scope_records(self.snapshot_records(candidates[-1]), query)}
        history_query = replace(query, selection=replace(query.selection, versions=None))
        return tuple(r for r in self._scope_records(self.records, history_query) if r.equipment_id in cohort)

    def _scope_records(self, records, query, group_keys=None, position=None):
        selection = query.selection
        active = self._selected_groups(selection)
        # 버전은 조회 설비군 범위의 SW 중 하나가 일치하면 설비 전체를 포함한다.
        # 표의 개별 셀로 나눌 때 다시 다른 버전 코호트를 만들지 않는다.
        scope_models = {model for key in active for model in self.bindings[key]}
        if group_keys is not None:
            active &= set(group_keys)
        if not active:
            return ()
        model_ids = {model for key in active for model in self.bindings[key]}
        result = []
        for record in records:
            if selection.item_family_id is not None:
                pms = tuple(pm for pm in record.pms if pm.family_id == selection.item_family_id)
                if record.is_hpa or not pms:
                    continue
                record = replace(record, pms=pms)
            loc = self.location_for(record)
            if not _matches_location(selection, loc):
                continue
            if position is not None and (loc.site_key, loc.raw_area) != position:
                continue
            if selection.versions is not None:
                version_record = replace(record, pms=tuple(pm for pm in record.pms if pm.group_id in scope_models))
                if not matches_versions(record if record.is_hpa else version_record, selection.versions):
                    continue
            pms = tuple(pm for pm in record.pms if pm.group_id in model_ids)
            if record.is_hpa:
                if selection.group_key is None and group_keys is None:
                    result.append(record)
            elif pms or (not record.pms and selection.group_key is None and group_keys is None):
                result.append(replace(record, pms=pms))
        return tuple(result)

    def _selected_groups(self, selection):
        return set(selection_keys(selection.group_key) or self.bindings)

    def _summary(self, records):
        return summarize(records, self.families, with_saved_groups(self.targets, self.saved_groups))

    def risk_status(self, record):
        """현재 표시하는 PM 범위로 위험을 판정한다. 고정 예시 자료는 기존 표를 사용한다.

        param
        - record: 모델 필터를 적용한 선택 SW 또는 같은 날짜의 전체 SW 관측.

        return
        - 해당 관측 범위의 위험 상태. HPA는 분석 제외이므로 미확인.
        """
        if record.is_hpa:
            return RiskStatus.UNKNOWN
        return (self.risk_evaluator(record) if self.risk_evaluator is not None
                else self.risks.get(record.equipment_id, RiskStatus.UNKNOWN))

    def _cell(self, label, keys, records):
        ids = tuple(r.equipment_id for r in records)
        return DashboardCell(label, keys, ids,
                             tuple(r.equipment_id for r in records if not r.is_hpa
                                   and self.risk_status(r) is RiskStatus.RISK), self._summary(records))

    def snapshot(self, query: AppliedQuery) -> DashboardSnapshot:
        if query.targets is not None:
            from setron.ui.target_data import target_map, validate_targets

            targets = target_map(validate_targets(self, query.targets))
            scoped = DashboardSource(catalog=self.catalog, bindings=tuple(GroupBinding(k, v) for k, v in self.bindings.items()),
                                     records=self.records, locations=self.locations, families=self.families, targets=targets,
                                     risks=self.risks, source_label=self.source_label, risk_evaluator=self.risk_evaluator,
                                     risk_rule_state=self.risk_rule_state, locations_by_date=self.locations_by_date)
            scoped.saved_groups = self.saved_groups
            scoped.carry_forward = self.carry_forward
            return scoped._snapshot(query)
        return self._snapshot(query)

    def _snapshot(self, query: AppliedQuery) -> DashboardSnapshot:
        from setron.ui.query_state import QueryState

        QueryState(self.catalog).validate(query.selection)
        if query.selection.item_family_id is not None and query.selection.item_family_id not in self.families:
            raise ValueError("Item 분석 계열이 현재 자료에 없습니다. 다시 조회해 주세요.")
        if not self.dates:
            raise ValueError("기준 관측일이 없습니다.")
        # HPA만 새 날짜에 저장되어도 일반 설비의 분석 기준일을 밀어내지 않는다.
        # HPA만 있는 자료는 최신 관측을 조회할 수 있게 하되 집계는 0대로 유지한다.
        candidates = _until(self.analysis_dates or self.dates, query.selection.as_of)
        if not candidates:
            raise NoObservationBefore(query.selection.as_of)
        day = candidates[-1]  # 화면에 보이는 기준일 = 기준일 이전 가장 최근 일반 관측일
        current = self.snapshot_records(day)
        selected = self._scope_records(current, query)
        hpa_lookup, hpa_locations = self._hpa_lookup(query)
        latest_location_ids = {equipment_id for equipment_id, _ in hpa_locations}
        hpa_locations += tuple((r.equipment_id, replace(self.location_for(r), raw_area=r.line))
                               for r in selected if r.is_hpa and r.equipment_id not in latest_location_ids)
        total = self._cell("전체", tuple(self.bindings), selected)
        groups = tuple(self._cell(o.label, (o.key,), self._scope_records(current, query, (o.key,)))
                       for o in self.catalog.groups)
        columns = [(o.label, (o.key,)) for o in self.catalog.groups if o.category != "KCTC"]
        kctc = tuple(o.key for o in self.catalog.groups if o.category == "KCTC")
        if kctc:
            columns.append(("KCTC", kctc))
        columns.append(("전체", None))
        site_names = {o.key: o.label for o in self.catalog.sites}
        site_order = {o.key: index for index, o in enumerate(self.catalog.sites)}
        positions = sorted({(self.location_for(r).site_key, self.location_for(r).raw_area)
                            for r in selected}, key=lambda p: (site_order[p[0]], p[1] is None, p[1] or ""))
        rows = []
        for position in (*positions, None):
            title = "전체" if position is None else f"{site_names[position[0]]} · {position[1] or '구역 미확인'}"
            cells = tuple(self._cell(name, tuple(self.bindings) if keys is None else keys,
                                    self._scope_records(current, query, keys, position)) for name, keys in columns)
            rows.append(DashboardRow(title, position, cells))
        trends = tuple(GroupTrend(o.key, o.label, self._observations(query, selected, (o.key,)))
                       for o in self.catalog.groups if matches_selection(query.selection.group_key, o.key))
        return DashboardSnapshot(query, day, total.equipment_ids, total.risky_ids,
                                 sum(not r.is_hpa and self.risk_status(r)
                                     is RiskStatus.UNKNOWN for r in selected),
                                 total.summary, tuple(rows), groups, trends,
                                 self._observations(query, selected), self.source_label, selected,
                                 tuple(TargetN(mid, fid, rid) for (mid, fid), rid in sorted(self.targets.items())),
                                 hpa_lookup, hpa_locations, tuple(deepcopy(self.saved_groups).items()), self.risk_rule_state)

    def _observations(self, query, baseline, group_keys=None):
        active = self._selected_groups(query.selection)
        if group_keys is not None:
            active &= set(group_keys)
        model_ids = {mid for key in active for mid in self.bindings[key]}
        cohort = {r.equipment_id for r in baseline if not r.is_hpa and
                  (any(pm.group_id in model_ids for pm in r.pms) or (not r.pms and group_keys is None))}
        points = []
        for day in _until(self.analysis_dates, query.selection.as_of):
            observations = tuple(replace(r, pms=tuple(pm for pm in r.pms if pm.group_id in model_ids))
                                 for r in self.records if not r.is_hpa and r.base_date == day
                                 and r.equipment_id in cohort)
            points.append(TrendPoint(day, self._summary(observations), len(cohort) - len(observations)))
        return tuple(points)
