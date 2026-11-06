"""요청 → 답 묶음. 숫자는 모두 공통 집계(BE-012)·우선순위·예측에서 나온다. AI는 원인 설명 문장만 쓴다.

대시보드 조건을 바꾸지 않고 이 질문의 조건으로만 읽는다(읽기 전용).
"""

from __future__ import annotations

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
_TIER_ORDER = {Tier.TWO_BELOW: 0, Tier.ONE_BELOW: 1, Tier.AT: 2, Tier.ABOVE: 3}

_DIGIT_FINAL = {"0": True, "1": True, "2": False, "3": True, "4": False, "5": False, "6": True, "7": True, "8": True, "9": False}
_LETTER_FINAL = set("LMNRlmnr")          # 엘·엠·엔·알처럼 받침으로 끝나는 영문 글자


def _josa(word: str, pair: str) -> str:
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
    every = select_records(data.records, replace(resolved, base_date=None), data.catalog)
    if request.areas:
        every = tuple(r for r in every if area_of({"site": r.site, "line": r.line}) in request.areas)
    on_base = tuple(r for r in every if r.base_date == base)
    statuses = {r.equipment_id: equipment_status(r, data.catalog.releases, data.targets) for r in on_base if not r.is_hpa}
    groups = {g.group_id: g.model_name or g.name for g in data.catalog.groups}
    families = {f.family_id: f.name for f in data.catalog.families}
    parts, subject = [], []
    subject += list(resolved.sites)
    subject += [{"MEMORY": "메모리", "FOUNDRY": "파운드리"}[a] for a in request.areas]
    subject += list(resolved.lines)
    subject += [groups.get(i, str(i)) for i in resolved.group_ids]
    subject += [families.get(i, str(i)) for i in resolved.family_ids if families.get(i) not in subject]
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
    parts.append(f"기준일 {base.isoformat()}" if base else "기준일 없음")
    return _Scope(base, dates, every, on_base, statuses, sum(1 for r in on_base if r.is_hpa), issues, " · ".join(parts),
                  " ".join(subject) or "전체", tuple(resolved.sites), tuple(resolved.lines))


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
        by_line = Counter(record.line or "-" for record, _ in rows)
        spread = " · ".join(f"{line} {count}대" for line, count in sorted(by_line.items()))
        text = (f"{subject} 설비 {total}대 중 {_josa(VERDICT_LABELS[request.verdict], '은/는')} {len(codes)}대입니다."
                + (f" ({spread})" if codes else ""))
    else:
        text = (f"{subject} 설비는 {total}대입니다. 목표 N 이상 {summary.compliant}건 · 미만 {summary.below}건 · "
                f"확인 필요 {summary.undetermined}건.")
    table = Table("조회 결과", ("설비명", "사업장", "원본 구역", "모델", "설치 버전", "목표 N", "판정"), tuple(
        (record.equipment_code, record.site, record.line or "-", record.model_name, _installed(family),
         _target(data, record, family), _judgement(family)[0]) for record, family in rows),
        tuple(_judgement(family)[1] for _, family in rows))
    return text, [table], codes


def _key(record, group_by: GroupBy) -> str:
    return {GroupBy.LINE: record.line or "-", GroupBy.SITE: record.site, GroupBy.MODEL: record.model_name}[group_by]


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
    before_records = [r for r in scope.all_dates if r.base_date == previous]
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
        facts.append(f"{where} 설비 {count}대는 {reason}(으)로 판정할 수 없습니다.")
    earlier = sorted(d for d in scope.dates if scope.base and d < scope.base)
    if earlier and below:
        first = earlier[0]
        old = {(r.equipment_id, pm.family_id): (pm.version_raw or "").strip() for r in scope.all_dates
               if r.base_date == first for pm in r.pms}
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
        if result is not None and result.ok and result.data is not None and report.numbers_match:
            text, is_ai = result.data.answer, True
        elif result is not None and result.ok:
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
        if entry.risk.findings or steps is None or steps > 0:   # 목표 이상이고 위험도 없으면 올릴 필요가 없다
            needing.append((entry, steps))
    for entry, steps in needing[:10]:
        risky = bool(entry.risk.findings)
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
    notices = [] if result.has_rules else ["등록된 위험 기준이 없어 버전 위치(목표까지 남은 칸)로만 정렬했습니다 · 판단 기준 확정 전"]
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
        text = f"{_josa(name, '은/는')} 이미 모든 설비가 목표 N 이상입니다."
    else:
        text = f"{_josa(name, '은/는')} 이 기간에 목표 N 이상 설비가 늘지 않아 통일 시점을 예측할 수 없습니다."
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
    spec = ReportSpec(f"SETRON 표준화 보고서 {month:%Y-%m}", (
        "요약 KPI", "원본 구역별 달성률 그래프", "위험 후보", "업그레이드 우선순위", "통일 시점 예측"))
    return (f"{month:%Y년 %m월} 표준화 보고서 구성을 준비했습니다. 설비 {summary.equipment_count}대 · 목표 N 달성률 "
            f"{_pct(summary.rate)}. Word 또는 PDF로 저장할 수 있습니다."), spec


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
    scope = _scope(request, data)
    sentences, tables, charts, notices = [], [], [], []
    cause, cause_is_ai, report, equipment_ids, focus = None, False, None, (), None
    if scope.hpa:
        notices.append(f"HPA {scope.hpa}대는 분석에서 제외했습니다.")
    if not scope.records:
        return ChatAnswer(request, scope.understood, "조건에 맞는 설비가 없습니다.", scope.base, issues=scope.issues,
                          suggestions=SUGGESTIONS[:4])
    if any(f.pms and f.pms[0].comparison.reason is CompareReason.TARGET_NOT_SET
           for s in scope.statuses.values() for f in s.families):
        notices.append("목표 N이 정해지지 않은 계열이 있어 그 설비는 '확인 필요'로 셉니다 · 판단 기준 확정 전")
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
    if not equipment_ids:
        equipment_ids = tuple(sorted(scope.statuses))
    summary = " ".join(sentences) if sentences else (cause or "")
    return ChatAnswer(request, scope.understood,
                      summary, scope.base, tuple(tables), tuple(charts), cause, cause_is_ai, report,
                      tuple(dict.fromkeys(notices)), scope.issues, (), tuple(equipment_ids))
