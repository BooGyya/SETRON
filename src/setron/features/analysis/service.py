"""AI 원인·현황 설명에 보낼 자료를 만들고, 답변의 숫자를 자료와 대조한다 (명세 BE-019).

자료 구성 (FUN-036·038·039)
- 조회 조건: 기준일, 조회 범위, 계열별 목표 N
- 전체 현황과 사업장별 현황: 사업장마다 공통 집계(``summarize``)를 다시 실행한 값
- 버전 분포: 계열별 설치 버전마다 설비 수와 판정
- 위험: 사업장별 위험·미확인 설비 수와 사유별 설비
- 통일 지연 근거: N 미만 설비의 설치 버전과 목표 N 사이에서, 목표 N에는 포함되고 설치 버전에는
  포함되지 않은 Item. 저장된 버전별 Item 상태만 쓴다 (BE-014)
- 날짜별 추이: 날짜별 집계가 있을 때만
- 버전 변경 이력: 여러 날짜 관측이 있을 때, 같은 설비·계열의 이웃한 두 관측일 사이에 설치 버전이 바뀐 기록.
  두 날짜 모두 관측된 경우만 비교하고(관측 없는 날을 채우지 않음), 방향은 확인된 릴리즈 순서로만 정한다.

정한 규칙
- 적용률은 판단 불가를 분모에서 뺀 값이며, 계산할 수 없으면 None(null)으로 보낸다.
- 숫자 검사는 답변·근거·추정·미확인 문장의 숫자가 자료 어딘가에 있는지만 본다. 버전 표기(v4.56.6.43,
  41_DFB 같은 접미사 포함)와 0·1은 검사하지 않는다. 계산이 맞는지까지는 보지 않는다.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from setron.features.aggregation.schemas import FamilyStatus, RateCount, Summary
from setron.features.aggregation.service import summarize
from setron.features.ai.schemas import AnalysisAnswer
from setron.features.ai.service import analyze
from setron.features.analysis.schemas import AnalysisInput, AnalysisReport
from setron.features.item.schemas import ItemState
from setron.features.risk.schemas import RiskStatus
from setron.features.version.groups import SLOT_LABELS, VersionGroups
from setron.features.version.schemas import CompareStatus, ReleaseVersion
from setron.infrastructure.ai_client import ChatClient

STATUS_LABELS = {
    CompareStatus.AT_OR_ABOVE: "N 이상",
    CompareStatus.BELOW: "N 미만",
    CompareStatus.UNDETERMINED: "판단 불가",
}
MAX_GAP_ITEMS = 20  # 한 계열·설치 버전에 보내는 Item 수 상한. 넘으면 개수만 알린다.
MAX_CHANGES = 30  # 버전 변경 이력에 보내는 기록 수 상한. 넘으면 개수만 알린다.

_VERSION = re.compile(r"\bv?\d+(?:[._]\d+){2,}[\w+\-]*|\bv\d[\w.+\-]*", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def build_facts(data: AnalysisInput) -> dict[str, Any]:
    """AI에 보낼 자료를 만든다. 모든 숫자는 화면과 같은 공통 계산에서 나온다."""
    summary = summarize(data.records, data.families, data.targets)
    facts: dict[str, Any] = {
        "조회 조건": _conditions(data, summary),
        "전체 현황": _counts(summary),
        "사업장별 현황": _sites(data),
        "버전 분포": _distribution(data, summary),
        "위험": _risks(data),
        "통일 지연 근거": _gaps(data, summary),
    }
    if len(data.trend) > 1:
        facts["날짜별 추이"] = [{"기준일": s.base_date.isoformat(), **_rates(s)} for s in data.trend]
    if data.history:
        facts["버전 변경 이력"] = version_changes(data.history, data.families, data.family_names)
    return facts


def version_changes(records, families, family_names=None) -> dict[str, Any]:
    """같은 설비·계열의 이웃한 두 관측일 사이 설치 버전 변경 (BE-019·FUN-038).

    PM이 여러 개면 설치 버전 묶음(정렬한 원문)으로 비교한다. 방향은 두 버전 모두 확인된 순서가 있을 때만
    '올림'·'내림'으로, 아니면 '순서 미확인'으로 둔다. HPA는 분석에서 빼므로 넣지 않는다.
    """
    names = family_names or {}
    seen: dict[tuple[int, int], list[tuple[Any, tuple[str, ...]]]] = {}
    codes = {}
    for record in sorted((r for r in records if not r.is_hpa), key=lambda r: (r.equipment_id, r.base_date)):
        codes[record.equipment_id] = record.equipment_code
        by_family: dict[int, set[str]] = {}
        for pm in record.pms:
            by_family.setdefault(pm.family_id, set()).add(pm.version_raw or "미입력")
        for family_id, raws in by_family.items():
            seen.setdefault((record.equipment_id, family_id), []).append((record.base_date, tuple(sorted(raws))))
    rows, counts = [], {"올림": 0, "내림": 0, "순서 미확인": 0}
    for (equipment_id, family_id), observed in sorted(seen.items()):
        family = families.get(family_id)
        for (before_day, before), (after_day, after) in zip(observed, observed[1:]):
            if before == after:
                continue
            direction = _direction(family, before, after)
            counts[direction] += 1
            rows.append({"설비": codes[equipment_id], "계열": names.get(family_id, f"계열 {family_id}"),
                         "이전 관측일": before_day.isoformat(), "이전 버전": " / ".join(before),
                         "다음 관측일": after_day.isoformat(), "다음 버전": " / ".join(after), "방향": direction})
    days = sorted({day for observed in seen.values() for day, _ in observed})
    return {"비교 기간": {"처음": days[0].isoformat(), "끝": days[-1].isoformat()} if days else None,
            "관측일 수": len(days), "변경 건수": len(rows), **{f"{key} 건수": value for key, value in counts.items()},
            "변경": rows[:MAX_CHANGES], "보내지 않은 변경 건수": max(0, len(rows) - MAX_CHANGES)}


def _direction(family, before, after) -> str:
    def order(raws):
        orders = []
        for raw in raws:
            release = family.match(raw) if family is not None else None
            if release is None or release.version_order is None:
                return None
            orders.append(release.version_order)
        return min(orders)
    old, new = order(before), order(after)
    if old is None or new is None or old == new:
        return "순서 미확인"
    return "올림" if new > old else "내림"


def check_numbers(answer: AnalysisAnswer, facts: Mapping[str, Any]) -> tuple[str, ...]:
    """답변에 나온 숫자 중 자료에 없는 것을 돌려준다. 순서를 지키고 중복은 한 번만 적는다."""
    known = {_number_key(n) for n in _numbers_in(facts)}
    texts = (answer.answer, *answer.evidence, *answer.assumptions, *answer.unconfirmed)
    unmatched = [n for text in texts for n in _numbers_in_text(text)
                 if n not in ("0", "1") and _number_key(n) not in known]
    return tuple(dict.fromkeys(unmatched))


def explain(client: ChatClient, question: str, data: AnalysisInput) -> AnalysisReport:
    """자료를 만들어 AI에 설명을 요청하고, 답변의 숫자를 자료와 대조한다."""
    facts = build_facts(data)
    result = analyze(client, question, facts)
    unmatched = check_numbers(result.data, facts) if result.ok else ()
    return AnalysisReport(result, facts, unmatched)


# --- 자료 구역 ------------------------------------------------------------------

def _conditions(data: AnalysisInput, summary: Summary) -> dict[str, Any]:
    targets = []
    for (group_id, family_id), target in sorted(data.targets.items(), key=lambda item: item[0]):
        targets.append({"계열": _family_name(data, family_id),
                        "목표 N": _target_text(data.families.get(family_id), target)})
    return {
        "기준일": summary.base_date.isoformat() if summary.base_date else None,
        "조회 범위": dict(data.scope),
        "계열별 목표 N": targets,
        "적용률 계산": "N 이상 ÷ (N 이상 + N 미만). 판단 불가는 분모에서 뺀다.",
    }


def _counts(summary: Summary) -> dict[str, Any]:
    return {
        "설비 수(HPA 제외)": summary.equipment_count,
        "HPA 제외 설비": summary.hpa_excluded,
        "업그레이드 필요 설비": summary.upgrade_needed,
        **_rates(summary),
    }


def _rates(count: RateCount) -> dict[str, Any]:
    return {
        "N 이상": count.compliant,
        "N 미만": count.below,
        "판단 불가": count.undetermined,
        "N 이상 적용률(%)": None if count.rate is None else round(count.rate * 100, 1),
    }


def _sites(data: AnalysisInput) -> list[dict[str, Any]]:
    by_site = defaultdict(list)
    for record in data.records:
        by_site[record.site].append(record)
    result = []
    for site in sorted(by_site):
        summary = summarize(by_site[site], data.families, data.targets)
        result.append({
            "사업장": site,
            **_counts(summary),
            "계열별": [{"계열": _family_name(data, c.family_id), **_rates(c)} for c in summary.by_family],
        })
    return result


def _distribution(data: AnalysisInput, summary: Summary) -> list[dict[str, Any]]:
    by_family: dict[int, Counter] = defaultdict(Counter)
    for equipment in summary.equipment:
        for family in equipment.families:
            by_family[family.family_id][(_installed(family), STATUS_LABELS[family.status])] += 1
    return [
        {"계열": _family_name(data, family_id),
         "버전별 설비 수": [{"설치 버전": version, "판정": label, "설비 수": count}
                        for (version, label), count in sorted(counter.items())]}
        for family_id, counter in sorted(by_family.items())
    ]


def _risks(data: AnalysisInput) -> dict[str, Any]:
    if data.assessor is None:
        return {"상태": "미계산"}
    risk = data.assessor.summarize(data.records)
    by_site: dict[str, Counter] = defaultdict(Counter)
    by_reason: dict[str, list[str]] = defaultdict(list)
    for equipment in risk.equipment:
        by_site[equipment.record.site][equipment.status] += 1
        for reason in dict.fromkeys(f.rule.risk_reason for f in equipment.reasons):
            by_reason[reason].append(equipment.record.equipment_code)
    return {
        "위험 기준 등록": risk.has_rules,
        "판정 규칙": "환경·안전 필수 Item 미적용, 결함 Item·위험 버전 적용이면 위험. 미확인은 위험 없음이 아니다.",
        "사업장별": [{"사업장": site, "위험 설비": c[RiskStatus.RISK], "미확인 설비": c[RiskStatus.UNKNOWN]}
                  for site, c in sorted(by_site.items())],
        "사유별": [{"사유": reason, "설비": codes} for reason, codes in by_reason.items()],
    }


def _target_text(family, target) -> str:
    """목표 N 표기. 릴리즈 하나면 버전 원문, N 그룹이면 칸별 버전 (예: "N 그룹 · N: v2, v3 · N-1: v1")."""
    if not isinstance(target, VersionGroups):
        release = family.get(target) if family else None
        return release.version_raw if release else "릴리즈 미확인"
    if not target.has_target:
        return "N 그룹 · N 칸 비어 있음 (판단 불가)"
    parts = []
    for slot in sorted(target.slots, reverse=True):
        found = [family.get(member) for member in target.members(slot)] if family else []
        names = [r.version_raw for r in sorted((r for r in found if r), key=lambda r: (r.version_order is None,
                                                                                 r.version_order or 0))]
        missing = len(target.members(slot)) - len(names)
        names += ["릴리즈 미확인"] * missing
        parts.append(f"{SLOT_LABELS[slot]}: {', '.join(names)}")
    return "N 그룹 · " + " · ".join(parts)


def _gaps(data: AnalysisInput, summary: Summary) -> list[dict[str, Any]]:
    """N 미만 설비가 목표 N까지 받지 못한 Item. 목표 N에는 포함, 설치 버전에는 미포함인 것만 적는다."""
    if data.item_catalog is None or not data.items:
        return []
    groups: dict[tuple[int, int, int], set[int]] = defaultdict(set)
    for equipment in summary.equipment:
        for family in equipment.families:
            for pm in family.pms:
                c = pm.comparison
                if c.status is CompareStatus.BELOW and c.release and c.target:
                    key = (family.family_id, c.release.release_version_id, c.target.release_version_id)
                    groups[key].add(equipment.record.equipment_id)
    result = []
    for (family_id, installed_id, target_id), equipment_ids in sorted(groups.items()):
        family = data.families[family_id]
        installed, target = family.get(installed_id), family.get(target_id)
        missing = [item for item in data.items if _applied(data, item.item_id, target)
                   and not _applied(data, item.item_id, installed)]
        result.append({
            "계열": _family_name(data, family_id),
            "설치 버전": installed.version_raw,
            "목표 N": target.version_raw,
            "설비 수": len(equipment_ids),
            "목표 N에만 포함된 Item 수": len(missing),
            "목표 N에만 포함된 Item": [
                {"Item 번호": item.item_no, "내용": item.content, "근거": f"item_id {item.item_id}"}
                for item in missing[:MAX_GAP_ITEMS]
            ],
        })
    return result


# --- 보조 -----------------------------------------------------------------------

def _family_name(data: AnalysisInput, family_id: int) -> str:
    return data.family_names.get(family_id, f"계열 {family_id}")


def _installed(family: FamilyStatus) -> str:
    raws = dict.fromkeys((p.pm.version_raw or "").strip() or "(버전 없음)" for p in family.pms)
    return " / ".join(raws)


def _applied(data: AnalysisInput, item_id: int, release: ReleaseVersion) -> bool:
    return data.item_catalog.version_state(item_id, release).state is ItemState.APPLIED


def _numbers_in(value: Any) -> Iterable[str]:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        yield str(value)
    elif isinstance(value, str):
        yield from _NUMBER.findall(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _numbers_in(key)
            yield from _numbers_in(item)
    elif isinstance(value, Iterable):
        for item in value:
            yield from _numbers_in(item)


def _numbers_in_text(text: str) -> list[str]:
    return _NUMBER.findall(_VERSION.sub(" ", text))


def _number_key(text: str) -> str:
    """60, 60.0, 060을 같은 숫자로 본다."""
    return json.dumps(float(text))
