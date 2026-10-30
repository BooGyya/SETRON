"""대시보드 읽기 어댑터. 확정된 위치/설비군 매핑과 기존 BE 집계를 사용한다. DB 접근 없음."""

from dataclasses import dataclass, replace
from datetime import date
from typing import Mapping

from setron.features.aggregation.schemas import EquipmentRecord, Summary
from setron.features.aggregation.service import summarize
from setron.features.risk.schemas import RiskStatus
from setron.features.version.service import FamilyReleases
from setron.ui.query_state import UNASSIGNED, AppliedQuery, QueryCatalog, TargetN


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


@dataclass(frozen=True)
class TrendPoint:
    day: date
    summary: Summary
    missing_equipment: int


@dataclass(frozen=True)
class GroupTrend:
    group_key: str
    label: str
    points: tuple[TrendPoint, ...]


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
    selected_records: tuple[EquipmentRecord, ...]  # 히트맵도 같은 기준일·필터 결과를 사용
    target_values: tuple[TargetN, ...]  # 실제 계산에 사용한 N. 이후 초안과 분리


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


class DashboardSource:
    """기준일 위치로 선택한 설비 집합과 고정 N을 모든 보기·과거 관측에서 공유한다."""

    def __init__(self, *, catalog: QueryCatalog, bindings: tuple[GroupBinding, ...],
                 records: tuple[EquipmentRecord, ...], locations: Mapping[int, EquipmentLocation],
                 families: Mapping[int, FamilyReleases], targets: Mapping[tuple[int, int], int],
                 risks: Mapping[int, RiskStatus], source_label: str):
        self.catalog, self.records = catalog, tuple(records)
        self.locations, self.families = dict(locations), dict(families)
        self.targets, self.risks = dict(targets), dict(risks)
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
        for loc in self.locations.values():
            if loc.site_key not in sites:
                raise ValueError("등록되지 않은 Site 매핑입니다.")
            if loc.fab_key is not None and (loc.fab_key not in fabs or fabs[loc.fab_key].site_key != loc.site_key):
                raise ValueError("Fab 매핑의 부모 Site가 다릅니다.")
            if loc.line_key is not None and (loc.line_key not in lines or
                lines[loc.line_key].site_key != loc.site_key or lines[loc.line_key].fab_key != loc.fab_key):
                raise ValueError("Line 매핑의 부모 위치가 다릅니다.")
        if any(not isinstance(status, RiskStatus) for status in self.risks.values()):
            raise ValueError("위험 판정 상태 형식이 올바르지 않습니다.")
        self.dates = tuple(sorted({r.base_date for r in self.records}))

    def _scope_records(self, records, query, group_keys=None, position=None):
        selection = query.selection
        active = {selection.group_key} if selection.group_key is not None else set(self.bindings)
        if group_keys is not None:
            active &= set(group_keys)
        if not active:
            return ()
        model_ids = {model for key in active for model in self.bindings[key]}
        result = []
        for record in records:
            loc = self.locations[record.equipment_id]
            if selection.site_key is not None and loc.site_key != selection.site_key:
                continue
            if position is not None and (loc.site_key, loc.raw_area) != position:
                continue
            if any(value is not None and (actual is not None if value is UNASSIGNED else actual != value)
                   for value, actual in ((selection.fab_key, loc.fab_key), (selection.line_key, loc.line_key))):
                continue
            pms = tuple(pm for pm in record.pms if pm.group_id in model_ids)
            if record.is_hpa:
                if selection.group_key is None and group_keys is None:
                    result.append(record)
            elif pms or (not record.pms and selection.group_key is None and group_keys is None):
                result.append(replace(record, pms=pms))
        return tuple(result)

    def _summary(self, records):
        return summarize(records, self.families, self.targets)

    def _cell(self, label, keys, records):
        ids = tuple(r.equipment_id for r in records)
        return DashboardCell(label, keys, ids,
                             tuple(r.equipment_id for r in records if not r.is_hpa
                                   and self.risks.get(r.equipment_id) is RiskStatus.RISK), self._summary(records))

    def snapshot(self, query: AppliedQuery) -> DashboardSnapshot:
        if query.targets is not None:
            from setron.ui.target_data import target_map, validate_targets

            targets = target_map(validate_targets(self, query.targets))
            scoped = DashboardSource(catalog=self.catalog, bindings=tuple(GroupBinding(k, v) for k, v in self.bindings.items()),
                                     records=self.records, locations=self.locations, families=self.families, targets=targets,
                                     risks=self.risks, source_label=self.source_label)
            return scoped._snapshot(query)
        return self._snapshot(query)

    def _snapshot(self, query: AppliedQuery) -> DashboardSnapshot:
        from setron.ui.query_state import QueryState

        QueryState(self.catalog).validate(query.selection)
        if not self.dates:
            raise ValueError("기준 관측일이 없습니다.")
        day = self.dates[-1]
        current = tuple(r for r in self.records if r.base_date == day)
        selected = self._scope_records(current, query)
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
        positions = sorted({(self.locations[r.equipment_id].site_key, self.locations[r.equipment_id].raw_area)
                            for r in selected}, key=lambda p: (site_order[p[0]], p[1] is None, p[1] or ""))
        rows = []
        for position in (*positions, None):
            title = "전체" if position is None else f"{site_names[position[0]]} · {position[1] or '구역 미확인'}"
            cells = tuple(self._cell(name, tuple(self.bindings) if keys is None else keys,
                                    self._scope_records(current, query, keys, position)) for name, keys in columns)
            rows.append(DashboardRow(title, position, cells))
        trends = tuple(GroupTrend(o.key, o.label, self._observations(query, selected, (o.key,)))
                       for o in self.catalog.groups if query.selection.group_key in (None, o.key))
        return DashboardSnapshot(query, day, total.equipment_ids, total.risky_ids,
                                 sum(not r.is_hpa and self.risks.get(r.equipment_id, RiskStatus.UNKNOWN)
                                     is RiskStatus.UNKNOWN for r in selected),
                                 total.summary, tuple(rows), groups, trends,
                                 self._observations(query, selected), self.source_label, selected,
                                 tuple(TargetN(mid, fid, rid) for (mid, fid), rid in sorted(self.targets.items())))

    def _observations(self, query, baseline, group_keys=None):
        active = {query.selection.group_key} if query.selection.group_key else set(self.bindings)
        if group_keys is not None:
            active &= set(group_keys)
        model_ids = {mid for key in active for mid in self.bindings[key]}
        cohort = {r.equipment_id for r in baseline if not r.is_hpa and
                  (any(pm.group_id in model_ids for pm in r.pms) or (not r.pms and group_keys is None))}
        points = []
        for day in self.dates:
            observations = tuple(replace(r, pms=tuple(pm for pm in r.pms if pm.group_id in model_ids))
                                 for r in self.records if r.base_date == day and r.equipment_id in cohort)
            points.append(TrendPoint(day, self._summary(observations), len(cohort) - len(observations)))
        return tuple(points)
