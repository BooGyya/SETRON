"""AI 분석의 범용 집계 도구 (11/11 멘토링: 라인 × 설비군의 최신화율 · 파편화).

질문마다 도구를 만들지 않고 "무엇을 셀지 · 무엇별로 · 어떤 조건으로"를 조합한다.
AI(또는 규칙)는 이 세 가지만 고르고, 숫자는 대시보드와 같은 DB 자료 · 같은 판정 함수로 센다.

무엇을 셀지 (MEASURES)
- count: 설비 수
- versions: 버전 종류 수 (같은 계열 안에서만 비교한다 — C3 버전과 C4 버전은 서로 비교할 수 없다)
- fragmentation: 파편화 지수 = 같은 계열에서 설비 두 대를 골랐을 때 버전이 다를 확률 (0 = 모두 같음).
  묶음 점수는 계열별 지수를 설비 수로 가중 평균한다. 판정 기준은 멘토 협의 전 초안이라 이 파일 한 곳에서만 정한다.
- achievement: 목표 N 달성률 (저장된 목표 N · N 그룹, 대시보드와 같음)
- baseline: 기준 버전 이상 비율 — 질문 속 버전을 목표 자리에 넣고 대시보드와 같은 판정(compare_version)

무엇별로 (DIMENSIONS): site · line · model · family · version · pm
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace

from setron.features.aggregation.service import summarize
from setron.features.query.service import resolve_conditions

MEASURES = {"count": "설비 수", "versions": "버전 종류 수", "fragmentation": "파편화",
            "achievement": "목표 N 달성률", "baseline": "기준 버전 이상 비율",
            "risk": "위험도"}   # 명세서 AI 기능 '사이트별 위험도 계산' (11/16): 위험 후보 + 목표에서 2단계 이상 뒤처짐
DIMENSIONS = {"site": "사업장", "line": "라인", "model": "모델", "family": "계열", "version": "버전", "pm": "PM",
              "date": "기준일"}
NO_VERSION = "버전 없음"


@dataclass(frozen=True)
class Spec:
    measure: str
    by: str = ""
    baseline: str = ""

    @classmethod
    def of(cls, values) -> "Spec | None":
        values = tuple(values or ())
        if not values or values[0] not in MEASURES:
            return None
        by = values[1] if len(values) > 1 and values[1] in DIMENSIONS else ""
        return cls(values[0], by, values[2] if len(values) > 2 else "")


@dataclass(frozen=True)
class Unit:
    """설비 한 대 × 계열 하나. 버전은 PM 버전을 모은 것 (PM마다 다르면 '/'로 잇는다)."""

    record: object
    family_id: int
    version: str


@dataclass(frozen=True)
class Spread:
    """한 묶음 · 한 계열의 버전 분포."""

    family_id: int
    total: int
    kinds: int
    top: str
    top_share: float
    index: float
    model: str = ""             # 같은 계열을 여러 설비군이 쓰면(C4 · C4 DFB · C4+) 설비군별로 따로 센다
    shared: bool = False        # 이 계열을 다른 설비군도 쓰는지 (표시 이름에 설비군을 붙인다)


def _version(family, pm) -> str:
    raw = (pm.version_raw or "").strip()
    if not raw:
        return ""
    release = family.match(raw) if family is not None else None
    return release.version_raw if release is not None else raw


def units(records, data, family_ids=()) -> list[Unit]:
    """설비 × 계열 단위. ``family_ids``가 있으면 그 계열만. HPA는 뺀다."""
    found = []
    for record in records:
        if record.is_hpa:
            continue
        versions: dict[int, list[str]] = defaultdict(list)
        for pm in record.pms:
            if family_ids and pm.family_id not in family_ids:
                continue
            versions[pm.family_id].append(_version(data.catalog.releases.get(pm.family_id), pm))
        for family_id, names in versions.items():
            unique = sorted(set(name for name in names if name))
            found.append(Unit(record, family_id, "/".join(unique) if unique else ""))
    return found


def _key(unit: Unit, by: str, data) -> str:
    record = unit.record
    if by == "site":
        return record.site or "미확인"
    if by == "line":
        return f"{record.site} {record.line}" if record.line else f"{record.site} (라인 없음)"
    if by == "model":
        return record.model_name or "미확인"
    if by == "family":
        return data.family_names.get(unit.family_id, str(unit.family_id))
    if by == "version":
        return f"{data.family_names.get(unit.family_id, unit.family_id)} {unit.version or NO_VERSION}"
    if by == "date":
        return record.base_date.isoformat()
    if by == "pm":
        names = sorted({pm.pm_name or "PM 미확인" for pm in record.pms if pm.family_id == unit.family_id})
        return "·".join(names) or "PM 미확인"
    return "전체"


def spread(items: list[Unit], family_id: int, model: str | None = None) -> Spread | None:
    """한 계열(· 한 설비군)의 버전 분포. 버전 원문이 비어 있는 설비는 분포에서 뺀다(모르는 값을 한 종류로 세지 않는다).

    ``model``을 주면 그 설비군만 센다. M-C4 DFB는 C4 노트를 쓰지만 버전 줄기(_DFB)가 달라 독립 설비군으로 본다
    (11/12 멘토 피드백 2) — 함께 세면 파편화가 부풀려진다.
    """
    versions = [u.version for u in items if u.family_id == family_id and u.version
                and (model is None or (u.record.model_name or "") == model)]
    if not versions:
        return None
    counts = Counter(versions)
    total = len(versions)
    top, top_count = max(counts.items(), key=lambda item: (item[1], item[0]))
    index = 1 - sum((n / total) ** 2 for n in counts.values())
    return Spread(family_id, total, len(counts), top, top_count / total, round(index, 3), model or "")


def fragmentation(items: list[Unit]) -> tuple[float, list[Spread]]:
    """묶음 점수(계열 · 설비군별 지수의 설비 수 가중 평균)와 계열 · 설비군별 분포."""
    pairs = sorted({(u.family_id, u.record.model_name or "") for u in items})
    models = Counter(family_id for family_id, _ in pairs)
    spreads = [replace(s, shared=models[s.family_id] > 1)
               for s in (spread(items, fid, model) for fid, model in pairs) if s is not None]
    total = sum(s.total for s in spreads)
    score = sum(s.index * s.total for s in spreads) / total if total else 0.0
    return round(score, 3), spreads


def baseline_family(text: str, data, family_ids=(), group_ids=()):
    """질문 속 기준 버전 → (계열 ID, 릴리즈). 조건의 계열 · 모델 안에서 먼저 찾고, 없으면 전체 계열에서 찾는다."""
    preferred = list(family_ids)
    for group_id in group_ids:
        preferred += [f.family_id for f in data.catalog.families if getattr(f, "group_id", None) == group_id]
    order = list(dict.fromkeys(preferred)) + [fid for fid in data.catalog.releases if fid not in preferred]
    for family_id in order:
        family = data.catalog.releases.get(family_id)
        if family is None:
            continue
        release = family.match(text)
        if release is None:
            candidates = family.candidates(text)
            release = candidates[0].release if candidates else None
        if release is not None:
            return family_id, release
    return (preferred[0] if preferred else None), None


def resolved(request, data):
    return resolve_conditions(request.conditions, data.catalog)


def baseline_rates(records, data, family_id, release, by) -> dict[str, object]:
    """기준 버전 이상 비율: 그 계열 PM만 남기고, 목표 자리에 기준 릴리즈를 넣어 대시보드와 같은 summarize로 센다."""
    targets = {}
    kept = []
    for record in records:
        pms = tuple(pm for pm in record.pms if pm.family_id == family_id)
        if not pms or record.is_hpa:
            continue
        kept.append(replace(record, pms=pms))
        for pm in pms:
            targets[(pm.group_id, family_id)] = release.release_version_id
    groups: dict[str, list] = defaultdict(list)
    for record in kept:
        unit = Unit(record, family_id, "")
        groups[_key(unit, by, data) if by and by not in ("version",) else "전체"].append(record)
    result = {"전체": summarize(kept, data.catalog.releases, targets)} if kept else {}
    if by and by != "version":
        result.update({key: summarize(items, data.catalog.releases, targets) for key, items in sorted(groups.items())})
    return result
