"""Replay 읽기 모델. 조회 때 자료·목표·규칙을 고정하고 실제 저장 날짜만 사용한다."""

from copy import deepcopy
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import date

from setron.features.aggregation.service import summarize
from setron.features.targets.groups import with_saved_groups
from setron.ui.dashboard_data import DashboardSnapshot, DashboardSource, GroupBinding, family_name
from setron.ui.equipment_data import EquipmentDetail
from setron.ui.heatmap_data import EquipmentTile, record_tiles
from setron.ui.query_state import AppliedQuery, QueryState, TargetN
from setron.ui.target_data import target_map, target_pairs, validate_targets


@dataclass(frozen=True)
class ReplayRequest:
    start: date
    end: date
    query: AppliedQuery

    def __post_init__(self):
        if type(self.start) is not date or type(self.end) is not date or self.start > self.end:
            raise ValueError("시작 날짜와 종료 날짜를 확인해 주세요.")
        if not isinstance(self.query, AppliedQuery):
            raise ValueError("Replay 조회 조건이 필요합니다.")


@dataclass(frozen=True)
class ReplayFrame:
    snapshot: DashboardSnapshot
    tiles: tuple[EquipmentTile, ...]

    @property
    def day(self):
        return self.snapshot.base_date


@dataclass(frozen=True)
class ReplayData:
    request: ReplayRequest
    source: DashboardSource
    frames: tuple[ReplayFrame, ...]
    options: tuple[str | None, ...]

    @property
    def can_replay(self):
        return sum(frame.snapshot.summary.equipment_count > 0 for frame in self.frames) >= 2


def freeze_source(source):
    """기존 읽기 공급자가 제공한 명시적 매핑을 복사한다. 원본 구역으로 Fab/Line을 추정하지 않는다."""
    evaluator, cache = source.risk_evaluator, {}
    def frozen_risk(record):
        key = (record.base_date, record.equipment_id,
               tuple((p.pm_name, p.group_id, p.family_id, p.version_raw) for p in record.pms))
        if key not in cache:
            cache[key] = evaluator(record)
        return cache[key]
    result = DashboardSource(
        catalog=source.catalog, bindings=tuple(GroupBinding(k, v) for k, v in source.bindings.items()),
        records=tuple(source.records), locations=dict(source.locations), families=deepcopy(source.families),
        targets=dict(source.targets), risks=dict(source.risks),
        source_label=source.source_label, risk_evaluator=frozen_risk if evaluator is not None else None)
    result.saved_groups = deepcopy(source.saved_groups)
    result.result_label = getattr(source, "result_label", "관측 자료")
    for name in ("applicability", "target_reasons", "model_names", "family_names"):
        if hasattr(source, name):
            setattr(result, name, deepcopy(getattr(source, name)))
    # 상세 패널의 전체 SW 위험도 조회 적용 시 고정한다. 재생·타일 클릭 때 원래 공급자를 호출하지 않는다.
    if evaluator is not None:
        for record in result.records:
            result.risk_status(record)
    # Replay 프레임은 그날 실제 저장된 관측만 재생한다. 이웃 날짜 관측으로 채우지 않는다 (QA Q-01 범위 밖).
    result.carry_forward = False
    return result


def effective_targets(source, requested):
    """기존 모델 적용 근거 계약을 유지한다. 무효인 N을 정상 목표로 승격하지 않는다."""
    if requested is None:
        return None
    if not hasattr(source, "applicability"):
        return validate_targets(source, requested)
    # DB 읽기 공급자의 align_targets/snapshot과 같은 선택지 정렬·무효 N 제외.
    chosen = {(t.model_id, t.family_id): t.release_id for t in requested}
    targets, invalid = [], []
    for pair in target_pairs(source):
        release_id = chosen.get(pair.key, pair.current_id)
        eligible = {option.release.release_version_id for option in pair.options
                    if option.unavailable_reason is None}
        if release_id is not None and release_id not in eligible:
            invalid.append(pair.label)
            release_id = None
        targets.append(TargetN(pair.model_id, pair.family_id, release_id))
    if invalid:
        source.source_label += " · 목표 N 재선택·적용 근거 재확인 필요: " + " / ".join(invalid)
    return tuple(targets)


def load_replay(source, request):
    """외부 읽기는 조회 적용 때 한 번만. 이후 타이머는 반환된 메모리 자료만 읽는다."""
    if not isinstance(request, ReplayRequest):
        raise ValueError("Replay 요청이 올바르지 않습니다.")
    # 공유 DB 읽기 공급자의 refresh와 복사 사이에 다른 조회가 끼어들지 않게 한다.
    with getattr(source, "_lock", nullcontext()):
        if hasattr(source, "refresh"):
            source.refresh()
        frozen = freeze_source(source)
    QueryState(frozen.catalog).validate(request.query.selection)
    targets = effective_targets(frozen, request.query.targets)
    if targets is not None:
        frozen.targets = target_map(targets)
    days = tuple(day for day in frozen.analysis_dates if request.start <= day <= request.end)
    frames, versions = [], set()
    for day in days:
        query = replace(request.query, selection=replace(request.query.selection, as_of=day), targets=targets)
        snapshot = frozen.snapshot(query)
        if snapshot.base_date != day:
            raise ValueError("요청한 날짜와 실제 관측 날짜가 다릅니다.")
        # 공통 히트맵의 '별도 최신 HPA'를 과거 프레임에 섞지 않는다.
        snapshot = replace(snapshot, hpa_lookup_records=(), hpa_lookup_locations=())
        analyzed = tuple(r for r in snapshot.selected_records if not r.is_hpa)
        frames.append(ReplayFrame(snapshot, record_tiles(analyzed, snapshot.summary, frozen)))
        all_versions = replace(query, selection=replace(query.selection, versions=None))
        for record in frozen._scope_records(tuple(r for r in frozen.records if r.base_date == day), all_versions):
            if record.is_hpa:
                continue
            if not record.pms:
                versions.add(None)
            for pm in record.pms:
                versions.add(pm.version_raw if pm.version_raw and pm.version_raw.strip() else None)
    options = tuple(sorted(v for v in versions if v is not None)) + ((None,) if None in versions else ())
    return ReplayData(request, frozen, tuple(frames), options)


def replay_detail(data, frame, equipment_id):
    """현재 프레임의 전체 SW를 같은 고정 N·규칙으로 읽는다. 이력 편집/DB 저장은 제공하지 않는다."""
    if frame not in data.frames:
        raise ValueError("현재 Replay에 없는 관측입니다.")
    selected = next(t for t in frame.tiles if t.equipment_id == equipment_id)
    source = data.source
    record = next(r for r in source.records if r.base_date == frame.day and r.equipment_id == equipment_id)
    summary = summarize((record,), source.families, with_saved_groups(source.targets, source.saved_groups))
    whole = record_tiles((record,), summary, source)[0]
    status = next((s for s in summary.equipment if s.record.equipment_id == equipment_id), None)
    detail = EquipmentDetail(selected, whole, record, status)
    names = {mid: group.label for group in source.catalog.groups for mid in source.bindings[group.key]}
    rows = []
    if status is not None:
        for family in status.families:
            for item in family.pms:
                pm, comparison = item.pm, item.comparison
                rows.append((pm.pm_name or "모듈 미확정 · 공통 SW",
                             f"{names.get(pm.group_id, '모델 미확인')} · 계열 {family_name(source, family.family_id)}",
                             pm.version_raw if pm.version_raw is not None else "미입력",
                             comparison.target.version_raw if comparison.target else "목표 미확인",
                             comparison.label, comparison.message or "확인된 릴리즈 순서로 비교"))
    elif record.is_hpa:
        rows = [(pm.pm_name or "공통 SW", record.model_name or "HPA", pm.version_raw or "미입력",
                 "—", "분석 제외", "HPA는 N 비교에서 제외") for pm in record.pms]
    evidence = (("11605.svg", "실제 관측일", str(frame.day)),
                ("f5960.svg", "관측 출처", frame.snapshot.source_label),
                ("8cc7b.svg", "비교 목표 N", "Replay 조회 적용 시 고정한 목표"),
                ("f8090.svg", "위치", whole.location))
    return detail, tuple(rows), evidence
