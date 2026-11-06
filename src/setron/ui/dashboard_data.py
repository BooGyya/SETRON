"""대시보드 읽기 어댑터. 확정된 위치/설비군 매핑과 기존 BE 집계를 사용한다. DB 접근 없음."""

from dataclasses import dataclass, replace
from datetime import date
from typing import Callable, Mapping

from setron.features.aggregation.schemas import EquipmentRecord, Summary
from setron.features.aggregation.service import summarize
from setron.features.risk.schemas import RiskStatus
from setron.features.version.service import FamilyReleases
from setron.ui.query_state import UNASSIGNED, AppliedQuery, QueryCatalog, TargetN


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
    selected_records: tuple[EquipmentRecord, ...]  # 공통 분석과 드릴다운의 같은 기준일·필터 결과
    target_values: tuple[TargetN, ...]  # 실제 계산에 사용한 N. 이후 초안과 분리
    hpa_lookup_records: tuple[EquipmentRecord, ...] = ()  # 공통 분석과 분리한 호기별 최신 원본 관측
    hpa_lookup_locations: tuple[tuple[int, EquipmentLocation], ...] = ()


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
        return names[family_id]
    for pair in getattr(getattr(source, "version_catalog", None), "pairs", ()):
        if pair.family_id == family_id:
            return pair.family_name
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

    def __init__(self, *, catalog: QueryCatalog, bindings: tuple[GroupBinding, ...],
                 records: tuple[EquipmentRecord, ...], locations: Mapping[int, EquipmentLocation],
                 families: Mapping[int, FamilyReleases], targets: Mapping[tuple[int, int], int],
                 risks: Mapping[int, RiskStatus], source_label: str,
                 risk_evaluator: Callable[[EquipmentRecord], RiskStatus] | None = None):
        self.catalog, self.records = catalog, tuple(records)
        self.locations, self.families = dict(locations), dict(families)
        self.targets, self.risks = dict(targets), dict(risks)
        self.risk_evaluator = risk_evaluator
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
        self.analysis_dates = tuple(sorted({r.base_date for r in self.records if not r.is_hpa}))

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
        if selection.group_key is not None:
            return (), ()
        sites = {option.label: option.key for option in self.catalog.sites}
        records, locations = [], []
        for record in latest:
            previous = self.locations[record.equipment_id]
            site_key = sites.get(record.site, previous.site_key)
            stale_mapping = site_key != previous.site_key or previous.raw_area != record.line
            location = EquipmentLocation(site_key, record.line,
                                         None if stale_mapping else previous.fab_key,
                                         None if stale_mapping else previous.line_key)
            if selection.site_key is not None and selection.site_key != location.site_key:
                continue
            if any(value is not None and (actual is not None if value is UNASSIGNED else actual != value)
                   for value, actual in ((selection.fab_key, location.fab_key), (selection.line_key, location.line_key))):
                continue
            records.append(record)
            locations.append((record.equipment_id, location))
        return tuple(records), tuple(locations)

    def scoped_records(self, query: AppliedQuery) -> tuple[EquipmentRecord, ...]:
        """모든 관측일에서 조회 조건(위치·설비군)에 맞는 설비 관측. 날짜별 예측(BE-020) 입력으로 쓴다."""
        return self._scope_records(self.records, query)

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
                                     risks=self.risks, source_label=self.source_label, risk_evaluator=self.risk_evaluator)
            return scoped._snapshot(query)
        return self._snapshot(query)

    def _snapshot(self, query: AppliedQuery) -> DashboardSnapshot:
        from setron.ui.query_state import QueryState

        QueryState(self.catalog).validate(query.selection)
        if not self.dates:
            raise ValueError("기준 관측일이 없습니다.")
        # HPA만 새 날짜에 저장되어도 일반 설비의 분석 기준일을 밀어내지 않는다.
        # HPA만 있는 자료는 최신 관측을 조회할 수 있게 하되 집계는 0대로 유지한다.
        candidates = _until(self.analysis_dates or self.dates, query.selection.as_of)
        if not candidates:
            raise NoObservationBefore(query.selection.as_of)
        day = candidates[-1]
        current = tuple(r for r in self.records if r.base_date == day)
        selected = self._scope_records(current, query)
        hpa_lookup, hpa_locations = self._hpa_lookup(query)
        latest_location_ids = {equipment_id for equipment_id, _ in hpa_locations}
        hpa_locations += tuple((r.equipment_id, replace(self.locations[r.equipment_id], raw_area=r.line))
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
                                 sum(not r.is_hpa and self.risk_status(r)
                                     is RiskStatus.UNKNOWN for r in selected),
                                 total.summary, tuple(rows), groups, trends,
                                 self._observations(query, selected), self.source_label, selected,
                                 tuple(TargetN(mid, fid, rid) for (mid, fid), rid in sorted(self.targets.items())),
                                 hpa_lookup, hpa_locations)

    def _observations(self, query, baseline, group_keys=None):
        active = {query.selection.group_key} if query.selection.group_key else set(self.bindings)
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
