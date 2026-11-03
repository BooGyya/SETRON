# -*- coding: utf-8 -*-
"""C. 번역표 적용 — 현황표 글자 → 설비군 → 메모리/파운드리 → 계열(릴리즈노트).

  resolve_group(group_raw, version_texts)       : 설비군 글자 → 설비군 (resolve_by_version 이면 버전의 _DEX 로 C4+/C4 판정)
  resolve_area(site, sheet, line, hint)          : 장소표 기본값 + 설비별 예외('메모리버전임' 힌트)
  resolve_family(group_id, ui, area, structure)  : 계열. 개조형은 부모 설비군, 파생은 variant 로.
골든테스트 C1~C13.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from svhm.core import refdata as R


@dataclass
class GroupResolution:
    status: str                       # OK / GROUP_UNKNOWN / MULTI_GROUP
    group_id: Optional[int] = None
    group_name: str = ""
    category: str = ""
    ui: Optional[str] = None
    structure: Optional[str] = None
    modification: Optional[str] = None
    analysis_enabled: bool = True
    note: str = ""
    # PM 혼합(M-O2/O3): 라벨별 설비군
    per_label: dict[str, int] = field(default_factory=dict)


def _ui_from_version(text: str) -> Optional[str]:
    """UI 표기가 없을 때 버전 모양으로 추정: v1.1.1… → 구UI, v3.52… → 통합UI"""
    t = (text or "").strip().lower().lstrip("v")
    if t.startswith("1.1.1"):
        return "LEGACY"
    if t.startswith("3.5"):
        return "INTEGRATED"
    return None


def resolve_group(group_raw: str, version_texts: Optional[list[str]] = None) -> GroupResolution:
    raw = re.sub(r"\s+", " ", str(group_raw or "")).strip()
    versions = version_texts or []
    # PM 혼합 설비: 'M-O2/O3' → 라벨별 설비군
    if re.fullmatch(r"M-O2\s*/\s*O3", raw, flags=re.I):
        return GroupResolution("MULTI_GROUP", None, "O2 + O3", "OXIDE", "INTEGRATED", None, None, True,
                               "PM 혼합 설비 — O2 쪽 1대, O3 쪽 1대로 양쪽 집계", {"O2": 5, "O3": 6})
    a = R.find_alias(raw)
    if not a:
        return GroupResolution("GROUP_UNKNOWN", note=f"번역표에 없는 표기 '{raw}' — 설비군을 지정해 주세요")
    gid = a["group"]
    note = ""
    if a.get("resolve_by_version"):
        if any(re.search(r"[_\-]DEX\b", v or "", flags=re.I) for v in versions):
            gid, note = 4, "버전에 _DEX 가 있어 C4+ 로 판정"
        else:
            gid, note = 2, "버전에 _DEX 가 없어 일반 C4 로 판정"
    ui = a.get("ui")
    if gid == 1 and R.norm_text(raw) == R.norm_text("M-C3"):
        guess = next((u for u in (_ui_from_version(v) for v in versions) if u), None)
        if guess and guess != "INTEGRATED":
            ui, note = guess, f"UI 미표기 — 버전 모양으로 {guess} 추정 (기본값은 통합UI, 10/28)"
        else:
            note = "UI 미표기 → 통합UI (10/28 확정)"
    g = R.GROUPS[gid]
    return GroupResolution("OK", gid, g["name"], g["category"], ui, a.get("structure"), a.get("modification"),
                           g.get("analysis_enabled", True), note)


def resolve_area(site: Optional[str], sheet: str, line: Optional[str], hint: Optional[str] = None,
                 default: Optional[str] = None) -> tuple[Optional[str], str]:
    """(메모리/파운드리, 사유). hint = 설비별 예외('메모리버전임' → MEMORY)."""
    if hint in ("MEMORY", "FOUNDRY"):
        return hint, "설비별 예외(현황표 메모)로 지정"
    row = R.find_line(site or "", sheet, line or "") if line else None
    if row:
        return row["area"], f"장소표: {site} {sheet} {line}"
    if default:
        return default, f"장소표에 없는 라인 '{line}' — 시트 기준 기본값 {default}"
    return None, f"장소표에 없는 라인 '{line}'"


@dataclass
class FamilyResolution:
    status: str                       # OK / FAMILY_UNRESOLVED / FAMILY_UNCONFIRMED
    family_id: Optional[int] = None
    family_name: str = ""
    variant: str = ""                 # '' / DFB / DEX
    note: str = ""


def resolve_family(group_id: int, ui: Optional[str], area: Optional[str], structure: Optional[str]) -> FamilyResolution:
    fid = R.find_family(group_id, ui, area, structure)
    if not fid:
        return FamilyResolution("FAMILY_UNRESOLVED", note=f"계열을 못 찾음 (group={group_id}, ui={ui}, area={area}, structure={structure})")
    f = R.FAMILIES[fid]
    variant = R.GROUP_VARIANT.get(group_id, "")
    if f.get("unconfirmed"):
        return FamilyResolution("FAMILY_UNCONFIRMED", fid, f["name"], variant, "릴리즈노트 존재 여부 확인 중 — 메모리 릴리즈노트로 비교")
    return FamilyResolution("OK", fid, f["name"], variant)
