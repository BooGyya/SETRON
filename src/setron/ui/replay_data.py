"""Replay 읽기 모델. 조회 때 자료·목표·규칙을 고정하고 실제 저장 날짜를 시점으로 쓴다.

각 시점은 대시보드와 같이 그 날짜까지 설비별 최신 관측으로 계산한다 (QA R-01).
"""

from copy import deepcopy
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import date, timedelta

from setron.features.aggregation.service import summarize
from setron.features.targets.groups import with_saved_groups
from setron.features.version.schemas import CompareStatus
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
    at: date | None = None  # 재생 간격(1주·1개월…)으로 만든 시점. 없으면 실제 저장 날짜(snapshot.base_date)

    @property
    def day(self):
        return self.at or self.snapshot.base_date

    @property
    def observed(self):
        """그날 실제로 저장된 일반 설비 수 (조회 조건 안)."""
        return sum(r.base_date == self.day for r in self.snapshot.selected_records if not r.is_hpa)

    @property
    def carried(self):
        """그날 저장되지 않아 이전 날짜의 마지막 관측을 이어받은 일반 설비 수."""
        return sum(r.base_date < self.day for r in self.snapshot.selected_records if not r.is_hpa)


def can_replay(frames):
    """재생하려면 설비가 있는 시점이 두 개 이상 필요하다."""
    return sum(frame.snapshot.summary.equipment_count > 0 for frame in frames) >= 2


# 재생 간격: None = 저장일마다(실제 저장 날짜), 나머지는 (일, 개월) 간격.
INTERVALS = {None: "저장일마다", "week": "1주", "month": "1개월", "quarter": "3개월"}
_STEPS = {"week": (7, 0), "month": (0, 1), "quarter": (0, 3)}


def _add_months(day, months):
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def interval_days(start, end, step):
    """시작 날짜부터 간격마다의 날짜. 마지막 간격이 종료 날짜에 딱 맞지 않으면 종료 날짜를 끝에 더한다."""
    days_step, months_step = _STEPS[step]
    result, k = [], 0
    while True:
        day = start + timedelta(days=days_step * k) if days_step else _add_months(start, months_step * k)
        if day > end:
            break
        result.append(day)
        k += 1
    if result and result[-1] != end:
        result.append(end)
    return tuple(result)


@dataclass(frozen=True)
class ReplayData:
    request: ReplayRequest
    source: DashboardSource
    frames: tuple[ReplayFrame, ...]
    options: tuple[str | None, ...]
    seed: ReplayFrame | None = None  # 시작 날짜가 저장 날짜가 아닐 때, 시작 날짜 시점의 상태(그 전 마지막 저장일 기준)

    @property
    def can_replay(self):
        return can_replay(self.frames)

    def frames_for(self, step):
        """재생 간격별 시점 (요청 2-1).

        - None: 실제 저장 날짜마다 (기존 동작).
        - 'week' · 'month' · 'quarter': 시작~종료를 간격으로 끊은 날짜마다. 각 시점은 그 날짜까지 설비별 최신 관측이므로
          그 날짜 이전 마지막 저장일의 프레임과 같은 내용이고, 날짜(at)만 다르다. 첫 저장 전 날짜는 뺀다.
        """
        if step is None:
            return self.frames
        known = tuple(sorted(((self.seed,) if self.seed else ()) + self.frames, key=lambda f: f.snapshot.base_date))
        result = []
        for day in interval_days(self.request.start, self.request.end, step):
            base = next((f for f in reversed(known) if f.snapshot.base_date <= day), None)
            if base is not None:
                result.append(replace(base, at=day))
        return tuple(result)


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
        source_label=source.source_label, risk_evaluator=frozen_risk if evaluator is not None else None,
        risk_rule_state=source.risk_rule_state)
    result.saved_groups = deepcopy(source.saved_groups)
    result.result_label = getattr(source, "result_label", "관측 자료")
    for name in ("applicability", "target_reasons", "model_names", "family_names"):
        if hasattr(source, name):
            setattr(result, name, deepcopy(getattr(source, name)))
    # 상세 패널의 전체 SW 위험도 조회 적용 시 고정한다. 재생·타일 클릭 때 원래 공급자를 호출하지 않는다.
    if evaluator is not None:
        for record in result.records:
            result.risk_status(record)
    # Replay도 대시보드와 같이 각 날짜까지 설비별 최신 관측을 쓴다 (QA R-01, 11-09 결정 ①).
    # 일부 사업장·시트만 저장한 날짜에 나머지 설비가 사라져 0%로 떨어지지 않게 한다.
    # 날짜 칩은 여전히 실제 저장된 날짜만이고, 그날 새로 저장된 설비 수는 ReplayFrame.observed로 따로 센다.
    result.carry_forward = True
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

    def frame_at(day):
        query = replace(request.query, selection=replace(request.query.selection, as_of=day), targets=targets)
        snapshot = frozen.snapshot(query)
        # 공통 히트맵의 '별도 최신 HPA'를 과거 프레임에 섞지 않는다.
        snapshot = replace(snapshot, hpa_lookup_records=(), hpa_lookup_locations=())
        analyzed = tuple(r for r in snapshot.selected_records if not r.is_hpa)
        return query, ReplayFrame(snapshot, record_tiles(analyzed, snapshot.summary, frozen))

    # 재생 간격 시점용: 시작 날짜가 저장 날짜가 아니고 그 전에 저장된 날짜가 있으면, 시작 날짜 시점 상태를 하나 더 만든다.
    seed = None
    if request.start not in days and any(day < request.start for day in frozen.analysis_dates):
        seed = frame_at(request.start)[1]
    frames, versions = [], set()
    for day in days:
        query, frame = frame_at(day)
        if frame.snapshot.base_date != day:
            raise ValueError("요청한 날짜와 실제 관측 날짜가 다릅니다.")
        frames.append(frame)
        all_versions = replace(query, selection=replace(query.selection, versions=None))
        for record in frozen._scope_records(frozen.snapshot_records(day), all_versions):
            if record.is_hpa:
                continue
            if not record.pms:
                versions.add(None)
            for pm in record.pms:
                versions.add(pm.version_raw if pm.version_raw and pm.version_raw.strip() else None)
    # 대소문자를 무시하고 정렬한다 (QA R-05). 원문은 합치지 않는다(끝 공백·대소문자도 다른 버전으로 유지).
    options = tuple(sorted((v for v in versions if v is not None), key=lambda v: (v.strip().casefold(), v)))
    options += (None,) if None in versions else ()
    return ReplayData(request, frozen, tuple(frames), options, seed)


def group_view(data, frame, groups):
    """설비군 칩에서 고른 설비군만으로 본 집계 (요청 2-2).

    param
    - groups: 고른 설비군 키 집합. None이면 전체(조회 결과 그대로, HPA 제외 대수 포함).

    return
    - (Summary, 이날 저장 대수, 이전 관측 이어받은 대수). 고른 설비군이 없으면 (None, 0, 0).
    """
    snapshot = frame.snapshot
    if groups is None:
        return snapshot.summary, frame.observed, frame.carried
    if not groups:
        return None, 0, 0
    records = data.source._scope_records(snapshot.selected_records, snapshot.query, group_keys=tuple(groups))
    summary = data.source._summary(records)
    analyzed = tuple(r for r in records if not r.is_hpa)
    return (summary, sum(r.base_date == frame.day for r in analyzed), sum(r.base_date < frame.day for r in analyzed))


def group_completion(cell):
    """한 설비군 칸의 완료 설비 수 (요청 3-2): 판정된 SW가 하나 이상이고 목표 N 미달이 없는 설비 / 판정된 설비."""
    determined = (CompareStatus.AT_OR_ABOVE, CompareStatus.BELOW)
    done = judged = 0
    for equipment in cell.summary.equipment:
        statuses = [family.status for family in equipment.families if family.status in determined]
        if statuses:
            judged += 1
            done += CompareStatus.BELOW not in statuses
    return done, judged


def replay_detail(data, frame, equipment_id):
    """현재 프레임의 전체 SW를 같은 고정 N·규칙으로 읽는다. 이력 편집/DB 저장은 제공하지 않는다."""
    if frame.snapshot not in {f.snapshot for f in data.frames + ((data.seed,) if data.seed else ())}:
        raise ValueError("현재 Replay에 없는 관측입니다.")
    selected = next(t for t in frame.tiles if t.equipment_id == equipment_id)
    source = data.source
    # 그날 저장되지 않은 설비는 이어받은 이전 관측(실제 관측일 유지)의 전체 SW를 보여 준다.
    record = next(r for r in source.snapshot_records(frame.snapshot.base_date) if r.equipment_id == equipment_id)
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
