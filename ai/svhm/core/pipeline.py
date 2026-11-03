# -*- coding: utf-8 -*-
"""A(현황표) + B(릴리즈노트) + C(번역표) + D(버전 맞추기) 를 한 줄로 잇는다.

  build_registry(mapping)          : {family_id: (파일, 시트)} → LadderRegistry
  match_equipment(row, registry)   : EquipmentRow → EquipmentMatch (버전마다 MatchResult)
  summarize(matches, n_by_family)  : 기준 버전 N 으로 적용/업그레이드 필요/미등록/누락/제외 집계 (지표 정의서 E1~E5)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

from svhm.core import refdata as R
from svhm.core.translate import resolve_group, resolve_area, resolve_family
from svhm.core.matcher import LadderRegistry, Ladder, MatchResult, match_version, relative_position
from svhm.excel.release_parser import parse_release_workbook
from svhm.excel.status_parser import EquipmentRow


def build_registry(mapping: dict[int, tuple[str, Optional[str]]]) -> LadderRegistry:
    """{family_id: (파일, 시트)} → 사다리 모음. 못 읽은 파일은 registry.skipped 에 이유와 함께 남긴다."""
    import os
    reg = LadderRegistry()
    reg.skipped = {}
    for fid, (path, sheet) in mapping.items():
        if not os.path.exists(path):
            reg.skipped[fid] = f"파일 없음: {path}"; continue
        res = parse_release_workbook(path, [sheet] if sheet else None)[0]
        if res.needs_ai_mapping:
            reg.skipped[fid] = f"{os.path.basename(path)}[{res.sheet}] — {res.hint} → 세로형 파일로 교체하거나 AI 열 매핑 단계로"
            continue
        reg.add_from_release(fid, res)
    return reg


@dataclass
class VersionMatch:
    label: str                 # '' 또는 PM 라벨(O2/O3)
    group_id: Optional[int]
    family_id: Optional[int]
    family_name: str
    variant: str               # 설비군이 요구하는 줄기 (DFB/DEX/'')
    result: MatchResult
    variant_mismatch: bool = False

    def to_dict(self) -> dict:
        d = {"label": self.label, "group_id": self.group_id, "family_id": self.family_id, "family_name": self.family_name,
             "variant": self.variant, "status": self.result.status, "input": self.result.input_text,
             "rung": self.result.rung.name if self.result.rung else None,
             "order": self.result.rung.order if self.result.rung else None,
             "candidates": [c.name for c in self.result.candidates],
             "group_suggestions": [(f, R.FAMILIES[f]["name"], n) for f, n in self.result.group_suggestions],
             "add_after": self.result.add_after, "message": self.result.message, "variant_mismatch": self.variant_mismatch}
        return d


@dataclass
class EquipmentMatch:
    equipment_code: str
    site: Optional[str]
    sheet: str
    line: Optional[str]
    group_raw: str
    group_status: str
    group_id: Optional[int]
    group_name: str
    category: str
    area: Optional[str]
    area_note: str
    analysis_enabled: bool
    versions: list[VersionMatch]
    issues: list[str]                 # 파서 이슈 그대로
    messages: list[str] = field(default_factory=list)   # 사람이 읽는 문장
    source: dict = field(default_factory=dict)

    @property
    def overall(self) -> str:
        if not self.analysis_enabled:
            return "EXCLUDED"
        if self.group_status != "OK" and self.group_status != "MULTI_GROUP":
            return "GROUP_UNKNOWN"
        if not self.versions:
            return "MISSING"
        st = [v.result.status for v in self.versions]
        for s in ("NO_LADDER", "AMBIGUOUS", "UNREGISTERED", "MISSING"):
            if s in st:
                return s
        return "MATCHED"

    def to_dict(self) -> dict:
        d = asdict(self); d["versions"] = [v.to_dict() for v in self.versions]; d["overall"] = self.overall
        return d


def match_equipment(row: EquipmentRow, registry: LadderRegistry) -> EquipmentMatch:
    texts = [v.text for v in row.versions]
    g = resolve_group(row.group_raw, texts)
    area, area_note = resolve_area(row.site, row.sheet, row.line, row.extras.get("area_override_hint"), row.production_area)
    msgs = []
    if g.note:
        msgs.append(g.note)
    vm: list[VersionMatch] = []
    if g.status == "GROUP_UNKNOWN":
        msgs.append(g.note)
    else:
        entries = row.versions or []
        if not entries:
            entries = []
        for ve in entries:
            gid = g.per_label.get(ve.label.upper(), g.group_id) if g.status == "MULTI_GROUP" else g.group_id
            if gid is None:
                continue
            gi = R.GROUPS[gid]
            ui = g.ui if g.status != "MULTI_GROUP" else "INTEGRATED"
            fam = resolve_family(gid, ui, area, g.structure)
            ladder = registry.get(fam.family_id) if fam.family_id else None
            if fam.status == "FAMILY_UNCONFIRMED" and (ladder is None or not ladder.rungs):
                # 파운드리 구UI 파일 없음 → 메모리 구UI 로 비교 (확인 중). 집계도 그 릴리즈노트 기준
                alt = R.find_family(gid, ui, "MEMORY", g.structure)
                if alt and registry.get(alt):
                    ladder = registry.get(alt)
                    fam.family_id, fam.family_name = alt, R.FAMILIES[alt]["name"] + " (파운드리 파일 없어 대신 사용)"
                msgs.append(fam.note)
            res = match_version(ve.text, ladder, registry, gi["category"])
            mismatch = bool(res.rung and fam.variant and res.rung.variant != fam.variant)
            if mismatch:
                msgs.append(f"설비군은 {gi['name']}인데 버전 줄기는 {res.rung.variant or '기본'} — 확인 필요")
            vm.append(VersionMatch(ve.label, gid, fam.family_id, fam.family_name, fam.variant, res, mismatch))
            if res.status not in ("EXACT",):
                msgs.append((f"[{ve.label}] " if ve.label else "") + res.message)
    if not row.versions and g.status != "GROUP_UNKNOWN":
        msgs.append("버전 칸이 비어 있음" if "VERSION_MISSING" in row.issues else ("버전 칸에 글자만 있음: " + row.version_raw if "VERSION_TEXT" in row.issues else ""))
    return EquipmentMatch(row.equipment_code, row.site, row.sheet, row.line, row.group_raw, g.status, g.group_id, g.group_name,
                          g.category, area, area_note, g.analysis_enabled, vm, list(row.issues),
                          [m for m in msgs if m], row.source)


# ----------------------------------------------------------------------------- E. 집계 (기준 버전 N)
@dataclass
class Summary:
    total: int = 0            # 대상 설비(집계 제외 뺀 것)
    applied: int = 0          # N 이상
    need_upgrade: int = 0     # N 미만(확인된 것)
    unregistered: int = 0
    missing: int = 0
    ambiguous: int = 0
    excluded: int = 0         # HPA 등
    no_n: int = 0             # 그 계열에 N 이 지정되지 않음
    def rate(self) -> Optional[float]:
        return round(self.applied / self.total * 100, 1) if self.total else None


def summarize(matches: list[EquipmentMatch], registry: LadderRegistry, n_by_family: dict[tuple[int, str], str]) -> Summary:
    """n_by_family: {(family_id, variant): 기준 버전 이름}. PM 혼합 설비는 버전마다 1대로 양쪽에 셈."""
    s = Summary()
    for m in matches:
        if not m.analysis_enabled:
            s.excluded += 1; continue
        if not m.versions:
            s.total += 1; s.missing += 1; continue
        for v in m.versions:
            s.total += 1
            st = v.result.status
            if st in ("MISSING",):
                s.missing += 1; continue
            if st in ("UNREGISTERED", "NO_LADDER"):
                s.unregistered += 1; continue
            if st == "AMBIGUOUS":
                s.ambiguous += 1; continue
            n_name = n_by_family.get((v.family_id, v.result.rung.variant))
            if not n_name:
                s.no_n += 1; continue
            ladder = registry.get(v.family_id)
            n_rung = next((r for r in ladder.rungs if r.name == n_name), None)
            if not n_rung:
                s.no_n += 1; continue
            if relative_position(v.result.rung, n_rung, ladder) >= 0:
                s.applied += 1
            else:
                s.need_upgrade += 1
    return s
