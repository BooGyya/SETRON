"""요청 → 답 묶음. 숫자는 모두 공통 집계(BE-012)·우선순위·예측에서 나온다. AI는 원인 설명 문장만 쓴다.

대시보드 조건을 바꾸지 않고 이 질문의 조건으로만 읽는다(읽기 전용).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from setron.features.aggregation.service import equipment_status, summarize
from setron.features.analysis.schemas import AnalysisInput
from setron.features.forecast.service import forecast, forecast_line
from setron.features.item.service import ItemCatalog
from setron.features.priority.service import prioritize
from setron.features.query.service import resolve_conditions, select_records
from setron.features.reference_data import area_of
from setron.features.risk.service import RiskAssessor
from setron.features.version.groups import Tier, target_label, tier_of
from setron.features.version.schemas import CompareReason, CompareStatus

from .schemas import (
    GROUP_BY_LABELS, VERDICT_LABELS, BarChart, ChatAnswer, ChatRequest, GroupBy, Intent, LineChart,
    LinePoint, ReportSpec, Table, Verdict,
)

SUGGESTIONS = (
    "화성 메모리 O3 설비 몇 대야?", "평택 P3 목표 N 미만 설비 보여줘", "확인 필요 설비 목록 뽑아줘",
    "목표 N 달성률이 가장 낮은 원본 구역을 분석해줘", "지난달보다 달성률이 떨어진 라인은?",
    "어디부터 업그레이드해야 해?", "C3 통합UI는 언제 통일될까?", "이번 달 표준화 보고서 만들어줘",
)
_REASONS = {
    CompareReason.MISSING_VERSION: "현황표에 버전이 비어 있음", CompareReason.NOT_REGISTERED: "릴리즈노트에 없는 버전",
    CompareReason.TARGET_NOT_SET: "목표 N 미지정", CompareReason.NO_RELEASE_DATA: "릴리즈노트 없음",
    CompareReason.ORDER_UNKNOWN: "버전 순서 미확정", CompareReason.ORDER_CONFLICT: "버전 순서 겹침",
    CompareReason.TARGET_INVALID: "목표 N 확인 필요", CompareReason.OTHER_FAMILY: "다른 계열 릴리즈",
}
REPORT_SECTIONS = ("요약 KPI", "구역별 목표 N 달성률", "원인 분석", "업그레이드 우선순위", "통일 시점 예측")
_TIER_ORDER = {Tier.TWO_BELOW: 0, Tier.ONE_BELOW: 1, Tier.AT: 2, Tier.ABOVE: 3}

_DIGIT_FINAL = {"0": True, "1": True, "2": False, "3": True, "4": False, "5": False, "6": True, "7": True, "8": True, "9": False}
_LETTER_FINAL = set("LMNRlmnr")          # 엘·엠·엔·알처럼 받침으로 끝나는 영문 글자


def josa(word: str, pair: str) -> str:
    """이름 뒤 조사(은/는, 이/가, 을/를). 영문·숫자는 읽는 소리로 받침을 정한다 (15L은, C3 통합UI는, O2는)."""
    tail = word.rstrip(" )]")[-1:] if word else ""
    if "가" <= tail <= "힣":
        final = (ord(tail) - 0xAC00) % 28 != 0
    elif tail.isdigit():
        final = _DIGIT_FINAL[tail]
    else:
        final = tail in _LETTER_FINAL
    first, second = pair.split("/")
    return word + (first if final else second)



@dataclass(frozen=True)
class ChatData:
    """답을 만들 자료. 화면이 DB에서 읽어 넘긴다.

    - ``records``: 모든 기준일의 설비 현황 (기간 변화·예측에 쓴다).
    - ``catalog``: ``query.schemas.QueryCatalog`` (선택지 + 계열별 릴리즈).
    - ``explainer``: (질문, AnalysisInput) → AnalysisReport. 없으면 원인을 자료 근거 문장으로만 쓴다.
    """

    records: tuple
    catalog: object
    targets: Mapping
    family_names: Mapping[int, str] = field(default_factory=dict)
    assessor: RiskAssessor | None = None
    thresholds: object | None = None
    explainer: Callable | None = None
    risks: Mapping[int, object] = field(default_factory=dict)   # equipment_id → RiskStatus (대시보드와 같은 판정)


@dataclass
class _Scope:
    base: date | None
    dates: tuple[date, ...]
    all_dates: tuple                 # 조건에 맞는 설비 (모든 기준일)
    records: tuple                   # 조건에 맞는 설비 (기준일)
    statuses: dict                   # equipment_id → EquipmentStatus (기준일, HPA 제외)
    hpa: int
    issues: tuple[str, ...]
    understood: str
    subject: str                     # 요약 문장의 주어 (예: "평택 M-O2")
    sites: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()
    at: Callable | None = None       # 기준일 d → 조건에 맞는 설비별 최신 관측 (이전 기준일 비교용)
    problems: tuple = ()             # 못 쓴 조건 (query.schemas.ConditionIssue) — 안내를 종류별로 짧게


def latest_until(records, as_of: date | None) -> tuple:
    """기준일(포함)까지 설비마다 가장 최근 관측 하나 (대시보드 ``current_records``와 같은 규칙, QA Q-01).

    현황표는 사업장·시트별로 다른 날 들어오므로, 그날 딱 맞는 기록만 세면 다른 날 넣은 사업장 설비가 사라진다.
    실제 관측일(base_date)은 그대로 둔다.
    """
    latest = {}
    for record in records:
        if as_of is not None and record.base_date > as_of:
            continue
        previous = latest.get(record.equipment_id)
        if previous is None or record.base_date > previous.base_date:
            latest[record.equipment_id] = record
    return tuple(latest[equipment_id] for equipment_id in sorted(latest))


def _pct(rate: float | None) -> str:
    return "-" if rate is None else f"{rate * 100:.1f}%"


def _family_name(data: ChatData, family_id: int) -> str:
    if family_id in data.family_names:
        return data.family_names[family_id]
    for choice in data.catalog.families:
        if choice.family_id == family_id:
            return choice.name
    return f"계열 {family_id}"


def _scope(request: ChatRequest, data: ChatData) -> _Scope:
    resolved = resolve_conditions(request.conditions, data.catalog)
    issues = tuple(f"'{issue.value}': {issue.message}" for issue in resolved.issues)
    dates = tuple(sorted({r.base_date for r in data.records}))
    base = resolved.base_date or (dates[-1] if dates else None)
    wanted_codes = {code.casefold() for code in request.codes}

    def pick(records):
        chosen = select_records(records, replace(resolved, base_date=None), data.catalog)
        if request.areas:
            chosen = tuple(r for r in chosen if not r.is_hpa and area_of({
                "production_area": r.production_area, "site": r.site, "line": r.line}) in request.areas)
        if wanted_codes:                                   # 질문 속 설비 호기 (DEMO-0121 · ESPA02)
            chosen = tuple(r for r in chosen if (r.equipment_code or "").casefold() in wanted_codes)
        return tuple(chosen)

    def at(day):
        # 대시보드와 같은 순서: 기준일까지 설비별 최신 관측을 먼저 고르고 조건으로 거른다
        # (반대로 하면 예전에 .41이었다 지금 .43인 설비가 '.41 쓰는 설비'로 남는다).
        return pick(latest_until(data.records, day))

    every = pick(data.records)                           # 모든 기준일 (추이·예측)
    on_base = at(base)
    statuses = {r.equipment_id: equipment_status(r, data.catalog.releases, data.targets) for r in on_base if not r.is_hpa}
    groups = {g.group_id: g.model_name or g.name for g in data.catalog.groups}
    families = {f.family_id: f.name for f in data.catalog.families}
    parts, subject = [], []
    subject += list(resolved.sites)
    subject += [{"MEMORY": "메모리", "FOUNDRY": "파운드리"}[a] for a in request.areas]
    subject += list(resolved.lines)
    subject += [groups.get(i, str(i)) for i in resolved.group_ids]
    subject += [families.get(i, str(i)) for i in resolved.family_ids if families.get(i) not in subject]
    subject += list(request.codes)
    if request.codes:
        parts.append("설비 = " + ", ".join(request.codes))
    if resolved.sites:
        parts.append("Site = " + ", ".join(resolved.sites))
    if request.areas:
        parts.append("영역 = " + ", ".join({"MEMORY": "메모리", "FOUNDRY": "파운드리"}[a] for a in request.areas))
    if resolved.lines:
        parts.append("원본 구역 = " + ", ".join(resolved.lines))
    if resolved.group_ids:
        parts.append("설비군 = " + ", ".join(groups.get(i, str(i)) for i in resolved.group_ids))
    if resolved.family_ids:
        parts.append("계열 = " + ", ".join(families.get(i, str(i)) for i in resolved.family_ids))
    if resolved.release_ids:
        raws = {r.release_version_id: r.version_raw for f in data.catalog.releases.values() for r in f.releases}
        parts.append("버전 = " + ", ".join(raws.get(i, str(i)) for i in resolved.release_ids))
    if request.verdict:
        parts.append("판정 = " + VERDICT_LABELS[request.verdict])
    if not parts:
        parts.append("범위: 전체 Site")
    if request.follow_up:
        parts.insert(0, "앞 질문 이어서")
    parts.append(f"기준일 {base.isoformat()}" if base else "기준일 없음")
    return _Scope(base, dates, every, on_base, statuses, sum(1 for r in on_base if r.is_hpa), issues, " · ".join(parts),
                  " ".join(subject) or "전체", tuple(resolved.sites), tuple(resolved.lines), at,
                  tuple(resolved.issues))


def _tier(family_status) -> Tier:
    if family_status.status is CompareStatus.UNDETERMINED:
        return Tier.UNKNOWN
    tiers = [tier_of(pm.comparison) for pm in family_status.pms]
    known = [t for t in tiers if t in _TIER_ORDER]
    return min(known, key=_TIER_ORDER.get) if known else Tier.UNKNOWN


def _judgement(family_status) -> tuple[str, str]:
    if family_status.status is CompareStatus.BELOW:
        return "목표 미달", "danger"
    if family_status.status is CompareStatus.UNDETERMINED:
        reasons = {pm.comparison.reason for pm in family_status.pms if pm.comparison.reason}
        return "확인 필요 · " + ", ".join(sorted(_REASONS.get(r, "판단 불가") for r in reasons)) if reasons else "확인 필요", "warning"
    return "목표 달성", "success"


def _installed(family_status) -> str:
    raws = sorted({(pm.pm.version_raw or "").strip() for pm in family_status.pms} - {""})
    return ", ".join(raws) if raws else "미입력"


def _target(data: ChatData, record, family_status) -> str:
    pm = family_status.pms[0].pm
    target = data.targets.get((pm.group_id, family_status.family_id))
    if target is None:
        return "미지정"
    return target_label(data.catalog.releases.get(family_status.family_id), target)


def _rows(scope: _Scope, verdict: Verdict | None):
    wanted = {Verdict.BELOW: CompareStatus.BELOW, Verdict.ACHIEVED: CompareStatus.AT_OR_ABOVE,
              Verdict.CHECK: CompareStatus.UNDETERMINED}.get(verdict)
    found = []
    for record in scope.records:
        status = scope.statuses.get(record.equipment_id)
        for family in status.families if status else ():
            if wanted is None or family.status is wanted:
                found.append((record, family))
    severity = {CompareStatus.BELOW: 0, CompareStatus.UNDETERMINED: 1, CompareStatus.AT_OR_ABOVE: 2}
    return sorted(found, key=lambda pair: (severity[pair[1].status], pair[0].site, pair[0].line or "", pair[0].equipment_code))


# --- 요청 종류별 ---------------------------------------------------------------------
def _search(request: ChatRequest, data: ChatData, scope: _Scope) -> tuple[str, list, list[int]]:
    rows = _rows(scope, request.verdict)
    total = len(scope.statuses)
    codes = sorted({record.equipment_id for record, _ in rows})
    subject = scope.subject
    summary = summarize([r for r in scope.records if not r.is_hpa], data.catalog.releases, data.targets)
    if request.verdict:
        by_line = Counter(record.line for record, _ in rows if _has_line(record))
        spread = " · ".join(f"{line} {count}대" for line, count in sorted(by_line.items()))
        text = (f"{subject} 설비 {total}대 중 {josa(VERDICT_LABELS[request.verdict], '은/는')} {len(codes)}대입니다."
                + (f" ({spread})" if codes and spread else ""))
    else:
        text = (f"{subject} 설비는 {total}대입니다. 목표 N 이상 {summary.compliant}건 · 미만 {summary.below}건 · "
                f"확인 필요 {summary.undetermined}건.")
    table = Table("조회 결과", ("설비명", "사업장", "원본 구역", "모델", "설치 버전", "목표 N", "판정"), tuple(
        (record.equipment_code, record.site, record.line or "-", record.model_name, _installed(family),
         _target(data, record, family), _judgement(family)[0]) for record, family in rows),
        tuple(_judgement(family)[1] for _, family in rows))
    return text, [table], codes


_NO_LINE = "구역 미확인"


def _has_line(record) -> bool:
    return bool((record.line or "").strip().strip("-").strip())


def _key(record, group_by: GroupBy) -> str:
    line = record.line if _has_line(record) else _NO_LINE
    return {GroupBy.LINE: line, GroupBy.SITE: record.site, GroupBy.MODEL: record.model_name}[group_by]


def _grouped(scope: _Scope, group_by: GroupBy, records=None) -> dict[str, list]:
    grouped = defaultdict(list)
    lines_by_site = defaultdict(set)
    for record in scope.records:
        lines_by_site[record.line].add(record.site)
    for record in (records if records is not None else scope.records):
        if record.is_hpa:
            continue
        key = _key(record, group_by)
        if group_by is GroupBy.LINE and len(lines_by_site[record.line]) > 1:
            key = f"{record.site} {key}"
        grouped[key].append(record)
    return dict(sorted(grouped.items()))


def _compare(request: ChatRequest, data: ChatData, scope: _Scope):
    group_by = request.group_by or (GroupBy.SITE if len(scope.sites) > 1 and not scope.lines else GroupBy.LINE)
    if group_by is GroupBy.LINE and not any(_has_line(r) for r in scope.records):
        group_by = GroupBy.SITE              # 원본 구역이 적힌 설비가 없으면 사업장별로 비교한다
    rates = {}
    for key, records in _grouped(scope, group_by).items():
        rates[key] = summarize(records, data.catalog.releases, data.targets)
    known = {k: s.rate for k, s in rates.items() if s.rate is not None}
    label = GROUP_BY_LABELS[group_by]
    highest = "가장 높" in request.question.replace("제일", "가장")
    if not known:
        return f"{label}별로 판단 가능한 설비가 없어 달성률을 비교할 수 없습니다.", [], [], None
    pick = max(known.values()) if highest else min(known.values())
    picked = [k for k, v in known.items() if v == pick]
    word = "높은" if highest else "낮은"
    detail = rates[picked[0]]
    text = (f"목표 N 달성률이 가장 {word} {label}은 {'·'.join(picked)}"
            f"({'각 ' if len(picked) > 1 else ''}{_pct(pick)}, 판단 가능 {detail.compliant + detail.below}대 중 {detail.compliant}대)입니다.")
    labels = tuple(rates)
    chart = BarChart(f"{label}별 목표 N 달성률", labels, tuple(None if s.rate is None else round(s.rate * 100, 1)
                                                               for s in rates.values()),
                     tuple(i for i, key in enumerate(labels) if key in picked))
    table = Table(f"{label}별 달성률", (label, "판단 가능", "달성", "미달", "확인 필요", "달성률"), tuple(
        (key, str(s.compliant + s.below), str(s.compliant), str(s.below), str(s.undetermined), _pct(s.rate))
        for key, s in rates.items()), tuple("danger" if key in picked and not highest else None for key in labels))
    return text, [table], [chart], (group_by, picked[0])


def _change(request: ChatRequest, data: ChatData, scope: _Scope):
    previous = max((d for d in scope.dates if scope.base and d < scope.base), default=None)
    if previous is None:
        return "이전 기준일 자료가 없어 변화를 비교할 수 없습니다.", []
    group_by = request.group_by or GroupBy.LINE
    before_records = list(scope.at(previous))           # 이전 기준일도 같은 규칙 (설비별 최신 → 조건)
    before = {k: summarize(v, data.catalog.releases, data.targets) for k, v in _grouped(scope, group_by, before_records).items()}
    after = {k: summarize(v, data.catalog.releases, data.targets) for k, v in _grouped(scope, group_by).items()}
    rows, dropped = [], []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        rb, ra = (b.rate if b else None), (a.rate if a else None)
        delta = None if rb is None or ra is None else (ra - rb) * 100
        if delta is not None and delta < 0:
            dropped.append((key, rb, ra))
        rows.append((key, _pct(rb), _pct(ra), "-" if delta is None else f"{delta:+.1f}%p"))
    word = "떨어진" if not any(w in request.question for w in ("올라간", "올랐", "늘어난")) else "오른"
    if word == "오른":
        risen = [(k, before[k].rate, after[k].rate) for k in after if k in before and before[k].rate is not None
                 and after[k].rate is not None and after[k].rate > before[k].rate]
        text = (f"{previous.isoformat()}보다 달성률이 오른 {GROUP_BY_LABELS[group_by]}은 "
                + (", ".join(f"{k}({_pct(b)} → {_pct(a)})" for k, b, a in risen) if risen else "없습니다") + ".")
    else:
        text = (f"{previous.isoformat()}보다 달성률이 떨어진 {GROUP_BY_LABELS[group_by]}은 "
                + (", ".join(f"{k}({_pct(b)} → {_pct(a)})" for k, b, a in dropped) + "입니다." if dropped else "없습니다."))
    old = {(r.equipment_id, pm.family_id): (pm.version_raw or "").strip() for r in before_records for pm in r.pms}
    changed = []
    for record in scope.records:
        for pm in record.pms:
            was = old.get((record.equipment_id, pm.family_id))
            now = (pm.version_raw or "").strip()
            if was is not None and was != now:
                changed.append((record.equipment_code, record.line or "-", _family_name(data, pm.family_id),
                                was or "미입력", now or "미입력"))
    text += f" 같은 기간 버전이 바뀐 설비·계열은 {len(changed)}건입니다."
    tables = [Table(f"{GROUP_BY_LABELS[group_by]}별 달성률 변화", (GROUP_BY_LABELS[group_by], previous.isoformat(),
                    scope.base.isoformat(), "변화"), tuple(rows))]
    if changed:
        tables.append(Table("버전이 바뀐 설비", ("설비명", "원본 구역", "계열", "이전", "지금"), tuple(sorted(changed))))
    return text, tables


def _cause(request: ChatRequest, data: ChatData, scope: _Scope, focus) -> tuple[str, bool, list, list[str]]:
    records = scope.records
    where = scope.subject
    if focus is not None:
        group_by, key = focus
        records = _grouped(scope, group_by).get(key, [])
        where = key
    statuses = [scope.statuses[r.equipment_id] for r in records if r.equipment_id in scope.statuses]
    below = Counter((_family_name(data, f.family_id), _installed(f)) for s in statuses for f in s.families
                    if f.status is CompareStatus.BELOW)
    unknown = Counter(_REASONS.get(pm.comparison.reason, "판단 불가") for s in statuses for f in s.families
                      if f.status is CompareStatus.UNDETERMINED for pm in f.pms[:1])
    facts = []
    for (family, version), count in below.most_common(3):
        facts.append(f"{where}의 {family} 설비 {count}대가 {version}에 머물러 있습니다.")
    for reason, count in unknown.most_common(2):
        facts.append(f"{where} 설비 {count}대는 판정할 수 없습니다 ({reason}).")
    earlier = sorted(d for d in scope.dates if scope.base and d < scope.base)
    if earlier and below:
        first = earlier[0]
        first_keys = {(r.equipment_id, r.base_date) for r in scope.at(first)}
        old = {(r.equipment_id, pm.family_id): (pm.version_raw or "").strip() for r in scope.all_dates
               if (r.equipment_id, r.base_date) in first_keys for pm in r.pms}
        stuck = sum(1 for r in records for pm in r.pms
                    if r.equipment_id in scope.statuses and old.get((r.equipment_id, pm.family_id)) == (pm.version_raw or "").strip()
                    and any(f.family_id == pm.family_id and f.status is CompareStatus.BELOW
                            for f in scope.statuses[r.equipment_id].families))
        facts.append(f"{first.isoformat()} 이후 버전이 바뀌지 않은 미달 설비·계열은 {stuck}건입니다.")
    if not facts:
        facts.append(f"{where}에는 목표 미달이나 판정할 수 없는 설비가 없습니다.")
    table = Table("원인 근거 (자료에서 확인)", ("근거",), tuple((f"{i}. {fact}",) for i, fact in enumerate(facts, 1)))
    text, is_ai = " ".join(f"{'①②③④⑤'[i]} {fact}" for i, fact in enumerate(facts[:5])), False
    notices = ["원인은 자료에서 찾은 근거입니다. 실제 사유는 현장 확인이 필요합니다."]
    if data.explainer is not None and records:
        report = data.explainer(request.question, AnalysisInput(
            records=tuple(records), families=data.catalog.releases, targets=data.targets, family_names=data.family_names,
            scope=tuple((part.split(" = ")[0], part.split(" = ")[-1]) for part in scope.understood.split(" · ")),
            history=tuple(r for r in scope.all_dates if r.base_date <= scope.base)))
        result = getattr(report, "result", None)
        real = getattr(result, "source", None) is not None and getattr(result.source, "value", result.source) == "REAL"
        if result is not None and result.ok and real and result.data is not None and report.numbers_match:
            text, is_ai = result.data.answer, True
        elif result is not None and result.ok and real:
            notices.append("AI 설명에 자료에 없는 숫자가 있어 자료 근거 문장으로 대신 보여 줍니다.")
    return text, is_ai, [table], notices


def _priority(request: ChatRequest, data: ChatData, scope: _Scope, focus) -> tuple[str, list, list[str]]:
    records = scope.records
    if focus is not None:
        records = _grouped(scope, focus[0]).get(focus[1], [])
    assessor = data.assessor or RiskAssessor(data.catalog.releases, ItemCatalog(data.catalog.releases, ()), ())
    result = prioritize(records, assessor, data.catalog.releases, data.thresholds, scope.base, targets=data.targets)
    rows, tones = [], []
    needing = []
    for entry in result.ranked:
        steps = max((p.steps_behind for p in entry.positions if p.steps_behind is not None), default=None)
        if _risky(data, entry) or steps is None or steps > 0:   # 목표 이상이고 위험도 없으면 올릴 필요가 없다
            needing.append((entry, steps))
    needing.sort(key=lambda pair: (not _risky(data, pair[0]), pair[0].rank))   # 위험 후보 먼저, 그다음 기존 순위
    for entry, steps in needing[:10]:
        risky = _risky(data, entry)
        if steps is None and not risky:
            rows.append((str(entry.rank), entry.record.equipment_code, entry.record.line or "-",
                         ", ".join(sorted({p.release.version_raw for p in entry.positions})) or "-",
                         "목표 N 미지정 · 버전 위치로만 정렬", "확인 필요"))
            tones.append("warning")
            continue
        level = "높음" if risky or (steps or 0) >= 2 else "중간" if (steps or 0) == 1 else "낮음"
        current = ", ".join(sorted({p.release.version_raw for p in entry.positions})) or "-"
        target = (_target(data, entry.record, scope.statuses[entry.record.equipment_id].families[0])
                  if entry.record.equipment_id in scope.statuses and scope.statuses[entry.record.equipment_id].families else "-")
        reason = ("위험 후보 · " if risky else "") + (f"목표까지 {steps}칸" if steps and steps > 0 else "목표 이상")
        rows.append((str(entry.rank), entry.record.equipment_code, entry.record.line or "-", f"{current} → {target}",
                     reason, level))
        tones.append("danger" if level == "높음" else "warning" if level == "중간" else None)
    for entry in result.needs_check[:5]:
        rows.append(("-", entry.record.equipment_code, entry.record.line or "-", "확인 필요", "버전 위치를 알 수 없음", "확인 필요"))
        tones.append("warning")
    has_rules = result.has_rules or any(getattr(v, "value", v) == "RISK" for v in data.risks.values())
    notices = [] if has_rules else ["등록된 위험 기준이 없어 버전 위치(목표까지 남은 칸)로만 정렬했습니다."]
    if not needing:
        return "업그레이드가 필요한 설비가 없습니다.", [Table("업그레이드 우선순위", ("순위", "설비명", "원본 구역", "현재 → 목표 N", "근거", "우선도"), tuple(rows), tuple(tones))], notices
    top = [e for e in result.ranked if any((p.steps_behind or 0) > 0 for p in e.positions)][:2]
    statuses = [scope.statuses[r.equipment_id] for r in records if r.equipment_id in scope.statuses]
    units = [f for s in statuses for f in s.families]
    compliant = sum(f.status is CompareStatus.AT_OR_ABOVE for f in units)
    determined = sum(f.status is not CompareStatus.UNDETERMINED for f in units)
    lifted = sum(1 for e in top for f in scope.statuses.get(e.record.equipment_id).families if f.status is CompareStatus.BELOW) \
        if top else 0
    first = needing[0][0]
    text = f"업그레이드가 가장 급한 설비는 {first.record.equipment_code}({rows[0][4]})입니다."
    if top and determined:
        where = focus[1] if focus else (scope.subject)
        text += (f" {'·'.join(str(e.rank) for e in top)}순위 {len(top)}대를 목표 N으로 올리면 {where} 달성률은 "
                 f"{_pct(compliant / determined)} → {_pct(min(compliant + lifted, determined) / determined)}가 됩니다.")
    return text, [Table("업그레이드 우선순위 추천", ("순위", "설비명", "원본 구역", "현재 → 목표 N", "근거", "우선도"),
                        tuple(rows), tuple(tones))], notices


def _risky(data: ChatData, entry) -> bool:
    status = data.risks.get(entry.record.equipment_id)
    return bool(entry.risk.findings) or getattr(status, "value", status) == "RISK"


def _forecast(request: ChatRequest, data: ChatData, scope: _Scope, focus) -> tuple[str, list, list[str]]:
    every = [r for r in scope.all_dates if not r.is_hpa and (scope.base is None or r.base_date <= scope.base)]
    records, where = every, None
    if focus is not None:
        group_by, key = focus
        records = [r for r in every if key in (_key(r, group_by), f"{r.site} {_key(r, group_by)}")]
        where = key
    result = forecast(records, data.catalog.releases, data.targets)
    if not result.families:
        return "예측할 자료가 없습니다.", [], ["통일 예측은 참고용입니다."]
    family = max(result.families, key=lambda f: (f.end_below, len(f.history), -f.family_id))
    name = _family_name(data, family.family_id)
    whole = forecast(every, data.catalog.releases, data.targets) if focus is not None else result

    def points(values) -> tuple[LinePoint, ...]:
        found = []
        for p in values:
            determined = p.applied + p.below
            found.append(LinePoint(p.day, None if not determined else round(p.applied / determined * 100, 1), p.projected))
        return tuple(found)

    label = f"{where} {name}" if where else name
    series = [(f"{label} (실선 실적 · 점선 예측)", points(forecast_line(family)))]
    if whole.overall:
        series.append(("전체 평균", points(whole.overall)))
    chart = LineChart(f"{label} 목표 N 달성률 추이 및 통일 예측 (노선도)", tuple(series), "예측은 지금까지의 증가 속도로 그린 참고선입니다.")
    name = label
    if family.remaining_days is not None and family.end_date is not None:
        when = family.end_date + timedelta(days=family.remaining_days)
        text = (f"현재 속도라면 {name} 미달 {family.end_below}대가 모두 목표 N에 이르는 시점은 약 {when:%Y-%m}로 "
                f"예상됩니다 (참고).")
    elif family.end_below == 0:
        text = f"{josa(name, '은/는')} 이미 모든 설비가 목표 N 이상입니다."
    else:
        text = f"{josa(name, '은/는')} 이 기간에 목표 N 이상 설비가 늘지 않아 통일 시점을 예측할 수 없습니다."
    return text, [chart], ["통일 예측은 지금까지의 속도로 계산한 참고값입니다."]


def _version_info(request: ChatRequest, data: ChatData, scope: _Scope) -> tuple[str, list]:
    family_ids = sorted({pm.family_id for r in scope.records for pm in r.pms}) or sorted(data.catalog.releases)
    rows, sentences = [], []
    for family_id in family_ids:
        family = data.catalog.releases.get(family_id)
        if family is None or not family.sorted.ordered:
            continue
        latest = family.sorted.ordered[-1]
        users = Counter((pm.version_raw or "").strip() for r in scope.records for pm in r.pms if pm.family_id == family_id)
        sentences.append(f"{_family_name(data, family_id)} 최신 버전은 {latest.version_raw}"
                         f"{f'({latest.released_at.isoformat()} 출시)' if latest.released_at else ''}이며 "
                         f"이 버전을 쓰는 설비는 {users.get(latest.version_raw.strip(), 0)}대입니다.")
        for release in reversed(family.sorted.ordered[-5:]):
            rows.append((_family_name(data, family_id), release.version_raw,
                         release.released_at.isoformat() if release.released_at else "-",
                         str(users.get(release.version_raw.strip(), 0))))
    if not sentences:
        return "버전 순서가 확정된 릴리즈가 없습니다.", []
    return " ".join(sentences[:3]), [Table("최근 버전별 설비 수", ("계열", "버전", "출시일", "설비 수"), tuple(rows))]


def _report(request: ChatRequest, data: ChatData, scope: _Scope) -> tuple[str, ReportSpec]:
    month = scope.base or date.today()
    summary = summarize([r for r in scope.records if not r.is_hpa], data.catalog.releases, data.targets)
    spec = ReportSpec(f"SETRON 표준화 보고서 {month:%Y-%m}", REPORT_SECTIONS)
    return (f"{month:%Y년 %m월} 표준화 보고서 구성을 준비했습니다. 설비 {summary.equipment_count}대 · 목표 N 달성률 "
            f"{_pct(summary.rate)}. Word로 저장할 수 있습니다."), spec


REPORT_INTENTS = (Intent.COMPARE, Intent.CAUSE, Intent.PRIORITY, Intent.FORECAST)


def report_answers(request: ChatRequest, data: ChatData) -> tuple[ChatAnswer, ...]:
    """'보고서 만들어줘': 같은 범위로 요약·구역별 달성률·원인·우선순위·통일 예측을 차례로 만든다 (보고서 각 장)."""
    from dataclasses import replace as _replace
    scope = _scope(request, data)
    summary = summarize([r for r in scope.records if not r.is_hpa], data.catalog.releases, data.targets)
    kpi = (f"{scope.subject} 설비 {summary.equipment_count}대 · 목표 N 달성률 {_pct(summary.rate)} "
           f"(목표 이상 {summary.compliant}건 · 미만 {summary.below}건 · 확인 필요 {summary.undetermined}건) · "
           f"업그레이드 필요 {summary.upgrade_needed}대")
    parts = [ChatAnswer(_replace(request, intents=(), question="요약 KPI"), scope.understood, kpi, scope.base,
                        issues=scope.issues, title="요약 KPI")]
    for intent, title in zip(REPORT_INTENTS, REPORT_SECTIONS[1:]):
        parts.append(_replace(answer(_replace(request, intents=(intent,), question=title), data), title=title))
    return tuple(parts)


def _title(request: ChatRequest, scope: _Scope, compared: GroupBy | None) -> str:
    """보고서다운 제목. 질문 문장 대신 요청 종류와 범위로 만든다 (기준일은 보고서 부제에 적는다)."""
    subject = "" if scope.subject == "전체" else f"{scope.subject} "
    intents = set(request.intents)
    question = request.question.replace("제일", "가장")
    if Intent.REPORT in intents:
        return f"SETRON 표준화 보고서 {scope.base:%Y-%m}" if scope.base else "SETRON 표준화 보고서"
    if Intent.COMPARE in intents:
        label = GROUP_BY_LABELS[compared or request.group_by or GroupBy.LINE]
        if "가장 높" in question:
            return f"{subject}목표 N 달성률 가장 높은 {label} 분석"
        if "가장 낮" in question or "어디가" in question or Intent.CAUSE in intents:
            return f"{subject}목표 N 달성률 가장 낮은 {label} 분석"
        return f"{subject}{label}별 목표 N 달성률 비교"
    if Intent.CHANGE in intents:
        return f"{subject}목표 N 달성률 변화 분석"
    if Intent.CAUSE in intents:
        return f"{subject}목표 N 미달 원인 분석"
    if Intent.PRIORITY in intents:
        return f"{subject}업그레이드 우선순위"
    if Intent.FORECAST in intents:
        return f"{subject}버전 통일 시점 예측"
    if Intent.VERSION_INFO in intents:
        return f"{subject}버전 현황"
    if request.verdict is not None:
        return f"{subject}{VERDICT_LABELS[request.verdict]} 설비 현황"
    return f"{subject}설비 현황"


def _listing(values, limit: int) -> str:
    values = list(values)
    return "·".join(values[:limit]) + (f" 외 {len(values) - limit}개" if len(values) > limit else "")


UNSUPPORTED_TITLE = "지원하지 않는 요청"
PERIOD_TITLE = "기간 보고서"
# 보고서 메뉴(ui/report_data.REPORT_TYPES)의 기간 보고서 — 이름과 키가 바뀌면 test_chat_planner가 알려 준다
PERIOD_REPORTS = (("status", "버전 표준화 리포트", "기간 동안의 표준화율 · N 적용률 추이"),
                  ("change", "설비별 변경 이력 리포트", "기간 안의 설비별 버전 변경"))
_DATE_FIELDS = ("base_date", "date", "dates")
_DATE_LIKE = re.compile(r"(?:(?<!\d)(?:1[0-2]|[1-9])\s*월|20\d\d[-./]\d{1,2}|20\d\d\s*년|\d{1,2}/\d{1,2}|\d+달 전)")
_VERSION_LIKE = re.compile(r"v\d+[._][\w.\-]*", re.I)
_MODEL_LIKE = re.compile(r"M-[A-Z0-9]+(?:\s*[A-Z]+)?|[OC]\d{1,2}", re.I)   # M-C9, M-O5 Prime, O7
_LINE_LIKE = re.compile(r"(?:[A-Z]\d{1,2}[A-Z]?|\d{1,2}L|[A-Z]{2,5})(?:-[A-Z]\d)?", re.I)


def _dates(data: ChatData) -> tuple[date, ...]:
    return tuple(sorted({r.base_date for r in data.records}))


def _latest_report(data: ChatData) -> str:
    dates = _dates(data)
    return f"{dates[-1].month}월 보고서 만들어줘" if dates else "이번 달 표준화 보고서 만들어줘"


def _date_range(data: ChatData) -> str:
    dates = _dates(data)
    if not dates:
        return "저장된 기준일이 아직 없어요"
    return f"저장된 기준일은 {dates[0].isoformat()} ~ {dates[-1].isoformat()} ({len(dates)}개)이고, 가장 최근은 {dates[-1].isoformat()}이에요"


def _missing_short(data: ChatData, problems) -> str | None:
    """못 찾은 조건의 종류에 맞춰 짧게 안내한다 (11/11: 사업장·라인·모델·기준일을 전부 늘어놓던 문제)."""
    if not problems:
        return None
    first = problems[0]
    value = str(getattr(first, "value", "")).strip("'")
    field_name = getattr(first, "field", "")
    if field_name in _DATE_FIELDS or _DATE_LIKE.search(value):
        return f"{value} 자료는 아직 없어요. {_date_range(data)}."
    catalog = data.catalog
    choices = {"sites": ("사업장", catalog.sites), "lines": ("라인", catalog.lines),
               "groups": ("모델", tuple(g.model_name or g.name for g in catalog.groups)),
               "families": ("계열", tuple(f.name for f in catalog.families))}
    if _VERSION_LIKE.fullmatch(value):
        return f"'{value}'은(는) 등록된 릴리즈 버전에서 찾지 못했어요. 버전 목록은 데이터 관리 → 릴리즈노트에서 볼 수 있어요."
    from .recognize import similar_names

    similar = [name for _, name in similar_names(value, catalog)]
    if len(similar) > 1:                              # 'Inline' → Inline Ox · Inline O2: 어느 것인지 묻는다
        return f"'{value}'은(는) 여러 이름에 해당해요: {' · '.join(similar[:6])}. 어느 것인지 적어 주세요."
    if field_name not in choices:                     # 질문 속 낱말: 글자 모양으로 종류를 짐작한다
        field_name = ("groups" if _MODEL_LIKE.fullmatch(value)                       # O7 · c2 · M-C9
                      else "lines" if _LINE_LIKE.fullmatch(value) and any(ch.isdigit() for ch in value)   # S9 · V3 · P9
                      else "")
        if not field_name and (value.isupper() or re.fullmatch(r"[A-Za-z]\d{1,2}", value)):           # KCTC 같은 약어
            return (f"'{value}'은(는) 지금 자료에서 찾지 못했어요. 모델: {_listing(choices['groups'][1], 6)} / "
                    f"라인: {_listing(choices['lines'][1], 6)}.")
    kind, names = choices.get(field_name, (None, ()))
    if kind and names:
        return f"'{value}'은(는) 지금 자료에 없는 {kind}이에요. 있는 {kind}: {_listing(names, 8)}."
    return f"'{value}'은(는) 지금 자료에서 찾지 못했어요. 사업장: {_listing(catalog.sites, 6) or '없음'}."


def _months_text(months) -> str:
    months = list(months)
    if len(months) >= 3 and months == list(range(months[0], months[0] + len(months))):
        return f"{months[0]}월~{months[-1]}월"
    return "·".join(f"{m}월" for m in months)


def period_answer(request: ChatRequest, data: ChatData) -> ChatAnswer:
    """여러 달 보고서: AI 분석은 월간만 바로 만들고, 기간을 묶은 보고서는 보고서 메뉴로 안내한다 (11/11).

    '각각'이면 달마다 월간 보고서 버튼을 준다. 자료가 없는 달은 빼고, 하나도 없으면 없다고 말한다.
    """
    dates = _dates(data)
    wanted = set(request.period)
    whole = len(wanted) == 12
    chosen = [d for d in dates if d.month in wanted]
    asked = "여러 달" if whole else _months_text(request.period)
    if not chosen:
        return ChatAnswer(request, asked, f"{asked} 자료는 아직 없어요. {_date_range(data)}.", title=PERIOD_TITLE,
                          suggestions=(_latest_report(data), *SUGGESTIONS[:2]))
    months = list(dict.fromkeys(d.month for d in chosen))
    monthly = tuple(f"{m}월 보고서 만들어줘" for m in months)
    available = _months_text(months)
    if request.each_month or len(months) == 1:
        if len(months) == 1:
            text = f"{asked} 중 자료가 있는 달은 {available}뿐이에요. 그 달의 월간 보고서는 AI 분석에서 바로 만들 수 있어요."
        elif set(months) == wanted:
            text = f"월간 보고서는 한 달씩 만들 수 있어요. {asked} 모두 자료가 있어요. 아래에서 고르세요."
        else:
            text = f"월간 보고서는 한 달씩 만들 수 있어요. {asked} 중 자료가 있는 달은 {available}이에요. 아래에서 고르세요."
        return ChatAnswer(request, asked, text, chosen[-1], title=PERIOD_TITLE, suggestions=monthly[:6])
    start, end = chosen[0].isoformat(), chosen[-1].isoformat()
    lead = "여러 달을 묶은 통합 보고서는" if whole else f"{asked} 보고서처럼 여러 달을 묶은 보고서는"
    summary = (f"{lead} 보고서 메뉴에서 만들 수 있어요. "
               f"'{PERIOD_REPORTS[0][1]}'는 {PERIOD_REPORTS[0][2]}를, '{PERIOD_REPORTS[1][1]}'는 {PERIOD_REPORTS[1][2]}을 담아요. "
               f"아래 버튼을 누르면 기간을 {start} ~ {end}로 채워서 열어요. AI 분석에서는 한 달씩 월간 보고서를 바로 만들 수 있어요.")
    actions = tuple((f"{name} 만들기", f"reports:{key}:{start}:{end}") for key, name, _ in PERIOD_REPORTS)
    return ChatAnswer(request, asked, summary, chosen[-1], title=PERIOD_TITLE, actions=actions, suggestions=monthly[-3:])


def unsupported_answer(request: ChatRequest, data: ChatData) -> ChatAnswer:
    """지원하지 않는 요청은 다른 것으로 바꿔 답하지 않고, 없다고 말하고 대신 할 수 있는 것을 권한다.

    - 주간 · 일별 보고서: 자료가 한 달 간격이라 없다.
    - 공정 · 담당자 · 일정 · 비용 · 고장 이력: 저장하지 않는 정보.
    - 위험 후보 거르기: AI 분석에서는 아직 못 거른다 — 히트맵 · 아이템 매니저로 안내.
    """
    label = request.unsupported
    if label == "위험 후보 거르기":
        summary = ("AI 분석에서는 아직 위험 후보로 설비를 거르지 못해요. 버전 히트맵의 '위험 후보만' 필터나 아이템 매니저의 "
                   "위험 기준에서 볼 수 있어요.")
        return ChatAnswer(request, label, summary, title=UNSUPPORTED_TITLE, actions=(("버전 히트맵 열기", "heatmap"),),
                          suggestions=("어디부터 업그레이드해야 해?", "요즘 어때?"))
    if not label.endswith("보고서"):
        summary = (f"{label}는 지금 저장하지 않는 정보라 답할 수 없어요. 지금 자료는 설비 현황표(사업장 · 라인 · 모델 · "
                   f"PM · 설치 버전)와 릴리즈노트(버전 · Item)예요.")
        return ChatAnswer(request, label, summary, title=UNSUPPORTED_TITLE, suggestions=SUGGESTIONS[:3])
    summary = (f"{label}는 아직 만들 수 없어요. 설비 현황표가 한 달 간격으로 저장돼 그보다 짧은 기간의 "
               f"자료가 없어요. 월간 보고서는 만들 수 있어요. {_date_range(data)}.")
    return ChatAnswer(request, request.unsupported, summary, title=UNSUPPORTED_TITLE,
                      suggestions=(_latest_report(data), "요즘 어때?", *SUGGESTIONS[:2]))


def missing_date_answer(request: ChatRequest, data: ChatData, label: str) -> ChatAnswer:
    """저장되지 않은 날짜·달을 물은 질문: 다른 날짜로 바꿔 답하지 않고, 있는 기준일을 알려 준다."""
    follow = (_latest_report(data),) if Intent.REPORT in request.intents or "보고서" in request.question else ()
    return ChatAnswer(replace(request, understood=True), label, f"{label} 자료는 아직 없어요. {_date_range(data)}.",
                      suggestions=(*follow, *SUGGESTIONS[:4 - len(follow)]))


def data_info_answer(request: ChatRequest, data: ChatData) -> ChatAnswer:
    """자료 안내. 보고서를 물으면 만들 수 있는 보고서, 아니면 최신 기준일 · 기준일 목록 (11/11: 묻는 것에 바로 답한다).

    숫자는 저장된 자료에서 센다.
    """
    dates = _dates(data)
    if not dates:
        return ChatAnswer(request, "자료 안내", "아직 저장된 설비 현황이 없어요. 데이터 가져오기에서 현황표를 먼저 저장해 주세요.",
                          title="자료 안내", suggestions=SUGGESTIONS[:2])
    current = latest_until(data.records, dates[-1])
    sites = sorted({r.site for r in current if r.site})
    months = sorted({(d.year, d.month) for d in dates})
    span = f"{months[0][1]}월~{months[-1][1]}월" if len(months) > 1 else f"{months[0][1]}월"
    if request.topic == "reports":
        reports = (
            ("월간 표준화 보고서", "한 달 · 전체 사업장·라인 · 5장",
             f"AI 분석: '{dates[-1].month}월 보고서 만들어줘' ({span})"),
            (PERIOD_REPORTS[0][1], PERIOD_REPORTS[0][2], "보고서 메뉴에서 기간 지정"),
            (PERIOD_REPORTS[1][1], PERIOD_REPORTS[1][2], "보고서 메뉴에서 기간 지정"),
            ("분석 답 보고서", "지금 답의 표 · 그래프 · 원인", "답 카드의 '보고서로 저장'"),
            ("설비 목록 Excel", "조회 결과 설비 목록 · 조회 조건", "목록 답의 'Excel 내보내기'"),
        )
        summary = (f"만들 수 있는 보고서는 {len(reports)}가지예요. AI 분석에서는 월간 표준화 보고서(한 달 · "
                   f"{'·'.join(sites) or '전체'} 전체 사업장·라인)를 바로 만들 수 있고, 여러 달을 묶은 기간 보고서는 "
                   f"보고서 메뉴의 '{PERIOD_REPORTS[0][1]}' · '{PERIOD_REPORTS[1][1]}'에서 기간을 정해 만들 수 있어요.")
        return ChatAnswer(request, "만들 수 있는 보고서", summary, dates[-1],
                          tables=(Table("만들 수 있는 보고서", ("보고서", "내용", "만드는 방법"), reports),),
                          title="자료 안내", actions=(("보고서 메뉴 열기", "reports"),),
                          suggestions=(f"{dates[-1].month}월 보고서 만들어줘",
                                       f"{months[0][1]}월부터 {months[-1][1]}월까지 보고서 만들 수 있어?"))
    summary = (f"가장 최근 자료는 {dates[-1].isoformat()}이에요. 저장된 기준일은 {dates[0].isoformat()} ~ "
               f"{dates[-1].isoformat()} ({len(dates)}개)이고, 그 날짜까지 설비는 {len(current)}대"
               f"({'·'.join(sites) or '사업장 미확인'})예요.")
    by_date = []
    for day in sorted(dates, reverse=True):
        rows = [r for r in data.records if r.base_date == day]
        names = sorted({r.site for r in rows if r.site})
        by_date.append((day.isoformat(), "·".join(names) or "-", f"{len({r.equipment_id for r in rows})}대"))
    targets = sum(1 for value in data.targets.values() if value)
    notices = (f"목표 N이 정해진 계열·모델 {targets}개" if targets else "목표 N이 정해진 계열이 아직 없어요",)
    return ChatAnswer(request, "자료 안내", summary, dates[-1],
                      tables=(Table("저장된 기준일", ("기준일", "사업장", "설비 수"), tuple(by_date)),),
                      notices=notices, title="자료 안내",
                      suggestions=(_latest_report(data), "어떤 보고서 만들 수 있어?", "지난달보다 달성률이 떨어진 라인은?"))


def aggregate_answer(request: ChatRequest, data: ChatData) -> ChatAnswer:
    """범용 집계 (11/11): '무엇을 셀지 · 무엇별로 · 어떤 조건으로'를 조합해 DB 자료로 센다. 숫자는 대시보드와 같은 계산."""
    from . import aggregate as agg
    spec = agg.Spec.of(request.aggregate)
    scope = _scope(request, data)
    if spec is None or not scope.records:
        short = _missing_short(data, scope.problems)
        if spec is not None and not scope.problems:                      # 조건은 다 맞는데 설비가 없다
            return ChatAnswer(request, scope.understood, f"{scope.subject}에는 조건에 맞는 설비가 없어요.", scope.base,
                              suggestions=SUGGESTIONS[:3])
        return ChatAnswer(request, scope.understood, short or _missing(data), scope.base, issues=scope.issues,
                          suggestions=SUGGESTIONS[:4])
    found = agg.resolved(request, data)
    by = spec.by or ("line" if spec.measure in ("fragmentation", "versions", "baseline") else "")
    dim = agg.DIMENSIONS.get(by, "")
    notices = [f"HPA {scope.hpa}대는 분석에서 제외했습니다."] if scope.hpa else []
    name = lambda fid: data.family_names.get(fid, str(fid))
    # 분포 이름: 같은 계열을 여러 설비군이 쓰면 설비군을 붙인다 ('C4 (M-C4 DFB)', 11/12 멘토 피드백 2)
    label = lambda s_: f"{name(s_.family_id)} ({s_.model})" if s_.shared and s_.model else name(s_.family_id)
    if spec.measure == "baseline":
        family_id, release = agg.baseline_family(spec.baseline, data, found.family_ids, found.group_ids)
        if release is None:
            wanted = list(found.family_ids) or [f.family_id for f in data.catalog.families
                                                if getattr(f, "group_id", None) in found.group_ids]
            wanted = [fid for fid in wanted if data.catalog.releases.get(fid) is not None
                      and data.catalog.releases[fid].releases] or ([family_id] if family_id is not None else [])

            def latest_of(fid):
                family = data.catalog.releases.get(fid)
                if family is None or not family.releases:
                    return ""
                ordered = sorted(family.releases, key=lambda r: (r.version_order is None, r.version_order or 0))
                return ", ".join(r.version_raw for r in ordered[-3:][::-1])
            recent = " / ".join(f"{name(fid)}: {latest_of(fid)}" for fid in wanted if latest_of(fid))
            if wanted and recent:
                where = " · ".join(name(fid) for fid in wanted)
                text = f"{where}에 '{spec.baseline}' 버전은 등록돼 있지 않아 기준으로 쓸 수 없어요. 최근 버전 — {recent}."
            else:
                text = f"'{spec.baseline}'은(는) 등록된 릴리즈 버전에서 찾지 못해 기준으로 쓸 수 없어요."
            return ChatAnswer(request, scope.understood, text, scope.base, title="기준 버전 없음",
                              suggestions=SUGGESTIONS[:3])
        rates = agg.baseline_rates(scope.records, data, family_id, release, by)
        whole = rates.get("전체")
        if whole is None:
            return ChatAnswer(request, scope.understood,
                              f"{scope.subject}에는 {name(family_id)} 설비가 없어 기준 버전으로 셀 수 없어요.", scope.base,
                              suggestions=SUGGESTIONS[:3])
        groups = {k: v for k, v in rates.items() if k != "전체"}
        label = f"{name(family_id)} {release.version_raw}"
        summary = (f"{label}을(를) 기준으로 보면 {scope.subject} 최신화율은 {_pct(whole.rate)}예요 "
                   f"(기준 이상 {whole.compliant}대 · 미만 {whole.below}대 · 확인 필요 {whole.undetermined}대).")
        known = {k: v.rate for k, v in groups.items() if v.rate is not None}
        if len(known) > 1 and min(known.values()) < max(known.values()):
            low = min(known, key=known.get)
            summary += f" {dim}별로는 {low}이(가) {_pct(known[low])}로 가장 낮아요."
        elif len(known) > 1:
            summary += f" {dim}별로 모두 같아요."
        rows = tuple((k, str(v.compliant + v.below), str(v.compliant), str(v.below), str(v.undetermined), _pct(v.rate))
                     for k, v in (groups or {"전체": whole}).items())
        chart = BarChart(f"{dim or '전체'}별 {label} 이상 비율", tuple(r[0] for r in rows),
                         tuple(None if (groups or {"전체": whole})[r[0]].rate is None
                               else round((groups or {"전체": whole})[r[0]].rate * 100, 1) for r in rows))
        notices += ["저장된 목표 N이 아니라 질문의 기준 버전으로 계산했어요. 최신화율 = 기준 이상 ÷ (이상 + 미만), "
                    "버전을 알 수 없는 설비는 '확인 필요'로 따로 셉니다."]
        return ChatAnswer(request, scope.understood, summary, scope.base,
                          tables=(Table(f"{dim or '전체'}별 기준 버전 이상 비율",
                                        (dim or "범위", "판단 대수", "기준 이상", "기준 미만", "확인 필요", "최신화율"), rows),),
                          charts=(chart,) if len(rows) > 1 else (), notices=tuple(notices),
                          title=f"{label} 기준 최신화율")
    items = agg.units(scope.all_dates if by == "date" else scope.records, data, found.family_ids)
    if not items:
        return ChatAnswer(request, scope.understood, f"{scope.subject}에는 셀 수 있는 설비가 없어요.", scope.base,
                          suggestions=SUGGESTIONS[:3])
    grouped: dict[str, list] = defaultdict(list)
    for unit in items:
        grouped[agg._key(unit, by, data) if by else "전체"].append(unit)
    missing = sum(1 for u in items if not u.version)
    if missing and spec.measure in ("fragmentation", "versions"):
        notices.append(f"버전이 비어 있는 설비 {missing}대는 버전 분포에서 뺐어요.")
    if spec.measure in ("fragmentation", "versions"):
        scored = {k: agg.fragmentation(v) for k, v in grouped.items()}
        order = sorted(scored, key=lambda k: (-scored[k][0], -sum(s.total for s in scored[k][1]), k))
        if spec.measure == "versions":
            order = sorted(scored, key=lambda k: (-max((s.kinds for s in scored[k][1]), default=0), k))
        ranking = tuple((str(i + 1), k, str(sum(s.total for s in scored[k][1])), f"{scored[k][0]:.2f}",
                         (lambda s: f"{label(s)} · {s.kinds}가지" if s else "-")(
                             max(scored[k][1], key=lambda s: (s.index, s.total), default=None)))
                        for i, k in enumerate(order))
        detail = tuple((k, label(s), str(s.total), str(s.kinds), s.top, _pct(s.top_share), f"{s.index:.2f}")
                       for k in order for s in sorted(scored[k][1], key=lambda s: (-s.index, -s.total)))
        top = order[0]
        score, spreads = scored[top]
        worst = max(spreads, key=lambda s: (s.index, s.total), default=None)
        if len(order) == 1 and worst is not None:                 # 묶음이 하나면 순위가 아니라 그 묶음의 분포를 바로 말한다
            kinds = ", ".join(f"{label(s_)} {s_.kinds}가지" for s_ in sorted(spreads, key=lambda s_: -s_.kinds))
            summary = (f"{scope.subject}은(는) {kinds} 버전을 써요. {label(worst)}은(는) 설비 {worst.total}대 중 "
                       f"가장 많이 쓰는 {worst.top}이(가) {_pct(worst.top_share)}예요 (파편화 지수 {score:.2f}).")
            if spec.measure == "fragmentation":
                notices.append("파편화 지수 = 같은 계열에서 설비 두 대를 골랐을 때 버전이 다를 확률 (0 = 모두 같음) · 판단 기준 협의 전 초안")
        elif spec.measure == "fragmentation":
            if worst is None or score == 0:
                summary = f"{scope.subject} 설비는 {dim or '범위'}마다 계열별로 모두 같은 버전을 써서 파편화된 곳이 없어요."
            else:
                summary = (f"{scope.subject}에서 가장 파편화된 {dim or '범위'}은(는) {top}이에요. {label(worst)} 설비 "
                           f"{worst.total}대가 {worst.kinds}가지 버전으로 나뉘어 있고, 가장 많이 쓰는 {worst.top}도 "
                           f"{_pct(worst.top_share)}예요 (파편화 지수 {score:.2f}).")
            notices.append("파편화 지수 = 같은 계열에서 설비 두 대를 골랐을 때 버전이 다를 확률 (0 = 모두 같음, 1에 가까울수록 "
                           "흩어짐). 여러 계열은 설비 수로 가중 평균했어요 · 판단 기준 협의 전 초안")
        else:
            most = max(spreads, key=lambda s: s.kinds, default=None)
            summary = (f"{scope.subject}에서 버전 종류가 가장 많은 {dim or '범위'}은(는) {top}이에요 "
                       f"({label(most)} {most.kinds}가지)." if most else f"{scope.subject}에는 버전 정보가 없어요.")
        chart = BarChart(f"{dim or '범위'}별 파편화 지수 (0~100)", tuple(order),
                         tuple(round(scored[k][0] * 100, 1) for k in order), highlight=(0,))
        return ChatAnswer(request, scope.understood, summary, scope.base,
                          tables=(Table(f"{dim or '범위'}별 파편화 순위", ("순위", dim or "범위", "설비 수", "파편화 지수",
                                                                        "가장 흩어진 계열"), ranking),
                                  Table("계열별 버전 분포", (dim or "범위", "계열", "설비 수", "버전 종류", "대표 버전",
                                                            "대표 비율", "파편화 지수"), detail)),
                          charts=(chart,) if len(order) > 1 else (), notices=tuple(notices),
                          title=f"{scope.subject} {dim}별 {agg.MEASURES[spec.measure]}".strip(),
                          equipment_ids=tuple(sorted({u.record.equipment_id for u in items})))
    if spec.measure == "achievement":
        records_by: dict[str, dict] = defaultdict(dict)
        for unit in items:
            records_by[agg._key(unit, by, data) if by else "전체"][unit.record.equipment_id] = unit.record
        rates = {k: summarize(list(v.values()), data.catalog.releases, data.targets) for k, v in sorted(records_by.items())}
        rows = tuple((k, str(v.compliant + v.below), str(v.compliant), str(v.below), str(v.undetermined), _pct(v.rate))
                     for k, v in rates.items())
        known = {k: v.rate for k, v in rates.items() if v.rate is not None}
        if by == "date" and known:
            first, last = min(known), max(known)
            summary = (f"{scope.subject} 목표 N 달성률 추이: {first} {_pct(known[first])} → {last} {_pct(known[last])}"
                       f" ({len(known)}개 기준일).")
        else:
            summary = (f"{scope.subject} {dim}별 목표 N 달성률에서 가장 낮은 곳은 {min(known, key=known.get)}"
                       f"({_pct(min(known.values()))})이에요." if known else f"{scope.subject}에는 판단 가능한 설비가 없어요.")
        return ChatAnswer(request, scope.understood, summary, scope.base,
                          tables=(Table(f"{dim or '범위'}별 목표 N 달성률", (dim or "범위", "판단 대수", "달성", "미달",
                                                                          "확인 필요", "달성률"), rows),),
                          charts=(BarChart(f"{dim or '범위'}별 목표 N 달성률", tuple(r[0] for r in rows),
                                           tuple(None if v.rate is None else round(v.rate * 100, 1) for v in rates.values())),)
                          if len(rows) > 1 else (), notices=tuple(notices), title=f"{scope.subject} {dim}별 목표 N 달성률")
    counts = {k: len({u.record.equipment_id for u in v}) for k, v in grouped.items()}
    total = len({u.record.equipment_id for u in items})
    order = sorted(counts, key=lambda k: (-counts[k], k))
    rows = tuple((k, f"{counts[k]}대", _pct(counts[k] / total if total else None)) for k in order)
    summary = f"{scope.subject} 설비는 {total}대예요."
    if by:
        summary += f" {dim}별로는 {order[0]}이(가) {counts[order[0]]}대로 가장 많아요."
    return ChatAnswer(request, scope.understood, summary, scope.base,
                      tables=(Table(f"{dim or '범위'}별 설비 수", (dim or "범위", "설비 수", "비율"), rows),),
                      notices=tuple(notices), title=f"{scope.subject} {dim}별 설비 수".strip(),
                      equipment_ids=tuple(sorted({u.record.equipment_id for u in items})))


_HPA_WORD = re.compile(r"(?<![a-z])hpa(?![a-z])")


def _has_any(text: str, words) -> bool:
    return any(word in text for word in words)


def sibling_notice(request: ChatRequest, data: ChatData) -> list[str]:
    """'O2' · 'C4'처럼 짧은 모델 이름이 다른 모델 이름에도 들어 있으면(Inline O2 · Cluster O2 / C4 DFB · C4+)
    그 모델들은 포함하지 않았다고 알린다 (11/12). DFB는 독립 설비군이라 특히 헷갈리기 쉽다."""
    def key(text):
        return re.sub(r"[\s\-_()]", "", re.sub(r"^M-", "", text or "")).casefold()     # '+'는 남긴다 (C4 ≠ C4+)

    notices = []
    for name in request.conditions.groups:
        mine = key(name)
        others = sorted({g.model_name or g.name for g in data.catalog.groups
                         if key(g.name) != mine and key(g.model_name) != mine and mine and mine in key(g.model_name or g.name)})
        if others:
            notices.append(f"'{name}'에는 같은 이름이 들어간 다른 모델({' · '.join(others)})은 포함하지 않았어요. "
                           "함께 보려면 그 모델 이름을 적어 주세요.")
    return notices


def hpa_answer(request: ChatRequest, data: ChatData) -> ChatAnswer:
    """HPA 설비 자체를 묻는 질문. HPA는 목표 N 집계에서 빼지만 저장 · 조회는 하므로 대수와 목록을 답한다 (11/12)."""
    scope = _scope(request, data)
    if scope.problems:
        short = _missing_short(data, scope.problems)
        return ChatAnswer(request, scope.understood, short or _missing(data), scope.base, issues=scope.issues,
                          suggestions=SUGGESTIONS[:3])
    rows = sorted((r for r in scope.records if r.is_hpa), key=lambda r: (r.site or "", r.line or "", r.equipment_code or ""))
    subject = scope.subject.replace("전체", "").strip()
    if not rows:
        return ChatAnswer(request, scope.understood, f"{subject + ' ' if subject else ''}HPA 설비는 없어요.".strip(), scope.base,
                          notices=("HPA는 목표 N 집계에서 제외하고 저장 · 조회만 해요.",), suggestions=SUGGESTIONS[:3])
    table = Table("HPA 설비", ("설비명", "사업장", "라인", "설치 버전"),
                  tuple((r.equipment_code or "-", r.site or "-", r.line or "-",
                         " / ".join(sorted({p.version_raw for p in r.pms if p.version_raw})) or "-") for r in rows))
    summary = (f"{subject + ' ' if subject else ''}HPA 설비는 {len(rows)}대예요. HPA는 목표 N 달성률 집계에서 제외하고 "
               f"저장 · 조회만 해요.")
    return ChatAnswer(request, scope.understood, summary, scope.base, tables=(table,),
                      equipment_ids=tuple(r.equipment_id for r in rows), title="HPA 설비",
                      suggestions=("HPA 빼고 전체 현황", *SUGGESTIONS[:2]))


def _missing(data: ChatData) -> str:
    """조건을 못 쓴 질문의 답: 답하지 않은 이유와 지금 자료에 있는 선택지."""
    catalog = data.catalog
    parts = []
    if catalog.sites:
        parts.append(f"사업장 {_listing(catalog.sites, 6)}")
    if catalog.lines:
        parts.append(f"라인 {_listing(catalog.lines, 10)}")
    if catalog.groups:
        parts.append(f"모델 {_listing((g.model_name or g.name for g in catalog.groups), 8)}")
    if catalog.dates:
        parts.append(f"기준일 {_listing((d.isoformat() for d in sorted(catalog.dates, reverse=True)), 4)}")
    available = " / ".join(parts) if parts else "저장된 설비 현황이 없습니다"
    return f"질문의 조건 중 지금 자료에서 찾지 못한 것이 있어 답하지 않았어요. 지금 자료: {available}."


def answer(request: ChatRequest, data: ChatData) -> ChatAnswer:
    """요청 하나에 대한 답 묶음을 만든다.

    param
    - request: ``recognize`` 결과.
    - data: DB에서 읽은 자료 (읽기 전용).

    return
    - 요약·표·그래프·원인·우선순위·보고서 카드. 숫자는 모두 집계 값이다.
    """
    if not request.understood or not request.intents:
        return ChatAnswer(request, "요청을 찾지 못함", "분석할 내용을 찾지 못했어요. 아래 같은 질문을 해 보세요.",
                          suggestions=SUGGESTIONS)
    if Intent.DATA_INFO in request.intents:
        return data_info_answer(request, data)
    if request.unsupported and not request.unsupported.endswith("보고서"):
        return unsupported_answer(request, data)
    if request.codes:
        known = {r.equipment_code.casefold() for r in data.records if r.equipment_code}
        missing = [code for code in request.codes if code.casefold() not in known]
        if missing:
            return ChatAnswer(request, " · ".join(request.codes),
                              f"설비 {', '.join(missing)}은(는) 저장된 현황에 없어요. 호기 이름을 다시 확인해 주세요.",
                              suggestions=SUGGESTIONS[:3])
    if _HPA_WORD.search(request.question.casefold()) and not _has_any(request.question, ("빼고", "제외", "없이", "말고")):
        return hpa_answer(request, data)
    if Intent.AGGREGATE in request.intents:
        result = aggregate_answer(request, data)
        return replace(result, notices=(*result.notices, *sibling_notice(request, data)))
    tier_note = ("N+1 · N-1 · N-2 같은 단계로 거르는 건 아직 지원하지 않아 목표 N 이상 · 미만으로 보여줘요. 단계별 색은 버전 히트맵에서 "
                 "볼 수 있어요.") if re.search(r"n\s*[+\-]\s*[12]", request.question.casefold()) else ""
    if request.period:
        return period_answer(request, data)
    if request.unsupported:
        return unsupported_answer(request, data)
    scope = _scope(request, data)
    sentences, tables, charts, notices = [], [], [], ([tier_note] if tier_note else [])
    notices += sibling_notice(request, data)
    if _HPA_WORD.search(request.question.casefold()):
        notices.append("HPA는 원래 목표 N 집계에서 제외해요 (저장 · 조회만).")
    cause, cause_is_ai, report, equipment_ids, focus = None, False, None, (), None
    if scope.hpa:
        notices.append(f"HPA {scope.hpa}대는 분석에서 제외했습니다.")
    if scope.issues:
        # 말한 조건을 못 쓰면 더 넓은 범위로 답하지 않는다 (평택을 물었는데 전체 숫자를 주면 오해를 부른다).
        short = _missing_short(data, scope.problems)
        follow = (_latest_report(data),) if short and Intent.REPORT in request.intents else ()
        return ChatAnswer(request, scope.understood, short or _missing(data), scope.base, issues=scope.issues,
                          suggestions=(*follow, *SUGGESTIONS[:4 - len(follow)]))
    if not scope.records:
        return ChatAnswer(request, scope.understood, "조건에 맞는 설비가 없습니다.", scope.base, issues=scope.issues,
                          suggestions=SUGGESTIONS[:4])
    if any(f.pms and f.pms[0].comparison.reason is CompareReason.TARGET_NOT_SET
           for s in scope.statuses.values() for f in s.families):
        notices.append("목표 N이 정해지지 않은 계열이 있어 그 설비는 '확인 필요'로 셉니다.")
    for intent in request.intents:
        if intent is Intent.SEARCH:
            text, found, equipment_ids = _search(request, data, scope)
            sentences.append(text); tables += found
        elif intent is Intent.COMPARE:
            text, found, chart, focus = _compare(request, data, scope)
            sentences.append(text); tables += found; charts += chart
        elif intent is Intent.CHANGE:
            text, found = _change(request, data, scope)
            sentences.append(text); tables += found
        elif intent is Intent.CAUSE:
            cause, cause_is_ai, found, extra = _cause(request, data, scope, focus)
            tables += found; notices += extra
        elif intent is Intent.PRIORITY:
            text, found, extra = _priority(request, data, scope, focus)
            sentences.append(text); tables += found; notices += extra
        elif intent is Intent.FORECAST:
            text, chart, extra = _forecast(request, data, scope, focus)
            sentences.append(text); charts += chart; notices += extra
        elif intent is Intent.VERSION_INFO:
            text, found = _version_info(request, data, scope)
            sentences.append(text); tables += found
        elif intent is Intent.REPORT:
            text, report = _report(request, data, scope)
            sentences.append(text)
    # 검색의 빈 ID는 정상적인 0건 결과다. 검색을 요청하지 않은 분석만 전체 범위를 사용한다.
    if Intent.SEARCH not in request.intents:
        equipment_ids = tuple(sorted(scope.statuses))
    if sentences:
        summary = " ".join(sentences)
    elif cause:
        # 원인만 물은 질문: 같은 글을 두 번 보이지 않게 요약은 한 줄 안내, 자세한 내용은 원인 칸에만
        summary = f"{scope.subject} 범위의 목표 N 미달·판정 불가 원인을 자료에서 정리했어요."
    else:
        summary = ""
    return ChatAnswer(request, scope.understood,
                      summary, scope.base, tuple(tables), tuple(charts), cause, cause_is_ai, report,
                      tuple(dict.fromkeys(notices)), scope.issues, (), tuple(equipment_ids),
                      title=_title(request, scope, focus[0] if focus else None))


_josa = josa      # 이전 이름 (테스트 호환)
