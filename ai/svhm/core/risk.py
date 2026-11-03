# -*- coding: utf-8 -*-
"""위험 판정 · 노후도 색 · 업그레이드 우선순위 (지표 정의서 v3, 멘토 10/21 확정)

위험 설비 = 둘 중 하나라도 해당
  ① SAFETY_REQUIRED : 사용자가 등록한 '환경안전 필수 아이템'이 들어간 버전(start)보다 낮은 설비 (아직 미적용)
  ② DEFECT          : 사용자가 등록한 '결함 버전/아이템' 구간 [start, end) 안의 버전을 쓰는 설비 (end 없으면 열린 구간)
규칙은 릴리즈노트에 없는 경우가 많으므로 사용자가 직접 등록한다 (ERD risk_rule 과 같은 모양).

노후도 색 = 버전 출시일부터 기준일까지의 경과: 1년 미만 초록 / 1년 이상 주황 / 2년 이상 빨강 (설정값)
우선순위 = 위험 설비 → 버전 가장 낮은 설비 → 낮은 순 (동률은 출시일 오래된 순)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from svhm.core.matcher import LadderRegistry, Rung, relative_position
from svhm.core.pipeline import EquipmentMatch, VersionMatch


@dataclass
class RiskRule:
    rule_id: int
    rule_type: str                 # SAFETY_REQUIRED / DEFECT
    family_id: int
    start_version: str             # 포함 (SAFETY: 아이템이 들어간 버전 / DEFECT: 결함 시작 버전)
    end_version: Optional[str]     # 제외 (DEFECT 해제 버전). SAFETY 는 사용 안 함
    reason: str
    item_ref: str = ""             # 아이템 내용/JIRA (표시용)
    variant: str = ""              # '' / DFB / DEX — 같은 줄기만 판정
    enabled: bool = True


@dataclass
class AgeColor:
    thresholds_days: tuple[int, int] = (365, 730)   # (주황, 빨강) — 설정값

    def color(self, release_date: Optional[date], as_of: date) -> tuple[str, Optional[int]]:
        if not release_date:
            return "회색(출시일 없음)", None
        d = (as_of - release_date).days
        if d >= self.thresholds_days[1]:
            return "빨강(2년 이상)", d
        if d >= self.thresholds_days[0]:
            return "주황(1년 이상)", d
        return "초록(1년 미만)", d


@dataclass
class VersionRisk:
    is_risk: bool
    reasons: list[str] = field(default_factory=list)
    rule_ids: list[int] = field(default_factory=list)
    age_color: str = ""
    age_days: Optional[int] = None


def _rung_by_name(reg: LadderRegistry, family_id: int, name: str) -> Optional[Rung]:
    lad = reg.get(family_id)
    return next((r for r in lad.rungs if r.name == name), None) if lad else None


def assess_version(vm: VersionMatch, reg: LadderRegistry, rules: list[RiskRule], as_of: date,
                   age: AgeColor = AgeColor()) -> VersionRisk:
    rung = vm.result.rung
    if rung is None:
        return VersionRisk(False, ["버전이 연결되지 않아 위험 판정 보류"], [], "회색(미연결)", None)
    lad = reg.get(vm.family_id)
    color, days = age.color(date.fromisoformat(rung.release_date) if rung.release_date else None, as_of)
    out = VersionRisk(False, [], [], color, days)
    for rule in rules:
        if not rule.enabled or rule.family_id != vm.family_id or (rule.variant or "") != (rung.variant or ""):
            continue
        start = _rung_by_name(reg, rule.family_id, rule.start_version)
        if not start:
            continue
        pos = relative_position(rung, start, lad)
        if rule.rule_type == "SAFETY_REQUIRED" and pos < 0:
            out.is_risk = True; out.rule_ids.append(rule.rule_id)
            out.reasons.append(f"환경안전 필수 아이템 미적용: '{rule.item_ref or rule.reason}'은 {rule.start_version}부터 포함 — 현재 {rung.name} ({-pos}칸 아래)")
        elif rule.rule_type == "DEFECT" and pos >= 0:
            end = _rung_by_name(reg, rule.family_id, rule.end_version) if rule.end_version else None
            if end is None or relative_position(rung, end, lad) < 0:
                out.is_risk = True; out.rule_ids.append(rule.rule_id)
                rng = f"{rule.start_version} ~ {rule.end_version + ' 전' if rule.end_version else '해제 전'}"
                out.reasons.append(f"결함 버전 사용 중: {rng} — {rule.reason}")
    return out


@dataclass
class EquipmentRisk:
    match: EquipmentMatch
    per_version: list[VersionRisk]

    @property
    def is_risk(self) -> bool:
        return any(v.is_risk for v in self.per_version)

    @property
    def reasons(self) -> list[str]:
        return [r for v in self.per_version for r in v.reasons if v.is_risk]

    @property
    def worst_color(self) -> str:
        order = ["빨강", "주황", "초록", "회색"]
        cols = [v.age_color for v in self.per_version] or ["회색(미연결)"]
        for key in order:
            hit = next((c for c in cols if c.startswith(key)), None)
            if hit:
                return hit
        return cols[0]


def assess_all(matches: list[EquipmentMatch], reg: LadderRegistry, rules: list[RiskRule], as_of: date,
               age: AgeColor = AgeColor()) -> list[EquipmentRisk]:
    return [EquipmentRisk(m, [assess_version(v, reg, rules, as_of, age) for v in m.versions]) for m in matches if m.analysis_enabled]


def steps_below_target(vm: VersionMatch, reg: LadderRegistry, n_by_family: dict) -> Optional[int]:
    """기준 N 까지 남은 칸 수 (양수 = N 아래, 0 = N, 음수 = N 위). N 이 없으면 None."""
    rung = vm.result.rung
    if not rung:
        return None
    n_name = n_by_family.get((vm.family_id, rung.variant))
    lad = reg.get(vm.family_id)
    n_rung = next((r for r in lad.rungs if r.name == n_name), None) if (lad and n_name) else None
    if not n_rung:
        return None
    return -relative_position(rung, n_rung, lad)


def priority_list(risks: list[EquipmentRisk], reg: LadderRegistry, n_by_family: Optional[dict] = None
                  ) -> list[tuple[int, EquipmentRisk, str]]:
    """① 위험 → ② 기준 N 에서 가장 많이 뒤처진 설비 → ③ 덜 뒤처진 순, 동률은 출시일 오래된 순.
    N 이 없으면 릴리즈노트 안의 칸 번호(낮은 순)로 대신한다. 반환: (순위, 설비, 사유)"""
    n_by_family = n_by_family or {}

    def key(er: EquipmentRisk):
        connected = [v for v in er.match.versions if v.result.rung]
        if not connected:
            return (1, 0, 10**9, "9999-12-31")
        steps = [steps_below_target(v, reg, n_by_family) for v in connected]
        steps = [x for x in steps if x is not None]
        behind = -max(steps) if steps else 0                      # 많이 뒤처질수록 앞 (음수화)
        v = min(connected, key=lambda v: v.result.rung.lineage_order)
        return (0 if er.is_risk else 1, behind, v.result.rung.lineage_order, v.result.rung.release_date or "9999-12-31")

    ranked = sorted((er for er in risks if any(v.result.rung for v in er.match.versions)), key=key)
    out = []
    for i, er in enumerate(ranked, 1):
        if er.is_risk:
            why = "위험: " + " / ".join(er.reasons)
        else:
            parts = []
            for v in er.match.versions:
                if v.result.rung:
                    st = steps_below_target(v, reg, n_by_family)
                    parts.append(f"{v.result.rung.name}" + (f" (기준까지 {st}칸)" if st is not None else ""))
            why = "버전 낮음: " + ", ".join(parts)
        out.append((i, er, why))
    return out
