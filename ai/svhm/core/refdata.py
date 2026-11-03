# -*- coding: utf-8 -*-
"""기준 데이터 — seed_번역표_장소표_v1.sql 과 같은 내용의 파이썬 판.
운영에서는 DB(model_alias / line_master / equipment_group / version_family)에서 읽고, 개발·테스트에서는 이 파일을 쓴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ----------------------------------------------------------------------------- 설비군·모델 (equipment_group)
GROUPS: dict[int, dict] = {
    1:  {"name": "C3",              "category": "POLY",  "parent": None},
    2:  {"name": "C4",              "category": "POLY",  "parent": None},
    3:  {"name": "C4 DFB",          "category": "POLY",  "parent": 2},
    4:  {"name": "C4+",             "category": "POLY",  "parent": 2},
    5:  {"name": "O2",              "category": "OXIDE", "parent": None},
    6:  {"name": "O3",              "category": "OXIDE", "parent": None},
    7:  {"name": "XMO3",            "category": "OXIDE", "parent": 6},
    8:  {"name": "O5",              "category": "OXIDE", "parent": None},
    9:  {"name": "O5 Prime",        "category": "OXIDE", "parent": 8},
    10: {"name": "KCTC Inline Ox",  "category": "KCTC",  "parent": None},
    11: {"name": "KCTC Inline O2",  "category": "KCTC",  "parent": None},
    12: {"name": "KCTC Cluster Ox", "category": "KCTC",  "parent": None},
    13: {"name": "KCTC Cluster O2", "category": "KCTC",  "parent": None},
    14: {"name": "HPA",             "category": "ETC",   "parent": None, "analysis_enabled": False},
}
# 파생 설비군 → 같은 릴리즈노트 안의 줄기(variant)
GROUP_VARIANT = {3: "DFB", 4: "DEX"}

# ----------------------------------------------------------------------------- 버전 계열 = 릴리즈노트 (version_family)
FAMILIES: dict[int, dict] = {
    1:  {"name": "C3 구UI (메모리)",     "group": 1,  "ui": "LEGACY",     "area": "MEMORY",  "structure": None},
    2:  {"name": "C3 통합UI (메모리)",   "group": 1,  "ui": "INTEGRATED", "area": "MEMORY",  "structure": None},
    3:  {"name": "C3 구UI (파운드리)",   "group": 1,  "ui": "LEGACY",     "area": "FOUNDRY", "structure": None, "unconfirmed": True},
    4:  {"name": "C3 통합UI (파운드리)", "group": 1,  "ui": "INTEGRATED", "area": "FOUNDRY", "structure": None},
    5:  {"name": "C4 (메모리)",          "group": 2,  "ui": "SMART",      "area": "MEMORY",  "structure": None},
    6:  {"name": "C4 (파운드리)",        "group": 2,  "ui": "SMART",      "area": "FOUNDRY", "structure": None},
    7:  {"name": "O2 (메모리)",          "group": 5,  "ui": "INTEGRATED", "area": "MEMORY",  "structure": None},
    8:  {"name": "O2 (파운드리)",        "group": 5,  "ui": "INTEGRATED", "area": "FOUNDRY", "structure": None},
    9:  {"name": "O3",                   "group": 6,  "ui": "INTEGRATED", "area": "COMMON",  "structure": None},
    10: {"name": "O5",                   "group": 8,  "ui": "SMART",      "area": "COMMON",  "structure": None},
    11: {"name": "KCTC Inline Ox",       "group": 10, "ui": None,         "area": "COMMON",  "structure": "INLINE"},
    12: {"name": "KCTC Inline O2",       "group": 11, "ui": None,         "area": "COMMON",  "structure": "INLINE"},
    13: {"name": "KCTC Cluster Ox",      "group": 12, "ui": None,         "area": "COMMON",  "structure": "CLUSTER"},
    14: {"name": "KCTC Cluster O2",      "group": 13, "ui": None,         "area": "COMMON",  "structure": "CLUSTER"},
}

# ----------------------------------------------------------------------------- 번역표 (model_alias)  raw_text → group, ui, structure, modification, resolve_by_version
ALIASES: list[dict] = [
    {"raw": "M-C3 (통합UI)",        "group": 1,  "ui": "INTEGRATED"},
    {"raw": "M-C3 (6각) (통합UI)",  "group": 1,  "ui": "INTEGRATED"},
    {"raw": "M-C3(5각) / 통합UI",   "group": 1,  "ui": "INTEGRATED"},
    {"raw": "M-C3 (구UI)",          "group": 1,  "ui": "LEGACY"},
    {"raw": "M-C3(6각) / 구UI",     "group": 1,  "ui": "LEGACY"},
    {"raw": "M-C3",                 "group": 1,  "ui": "INTEGRATED"},   # UI 미표기 → 통합UI (10/28)
    {"raw": "M-C4",                 "group": 2,  "ui": "SMART"},
    {"raw": "M-C4 / Smart UI",      "group": 2,  "ui": "SMART"},
    {"raw": "M-C4/C4+ / Smart UI",  "group": 2,  "ui": "SMART", "resolve_by_version": True},   # _DEX 있으면 C4+ (10/29)
    {"raw": "M-C4(DFB)",            "group": 3,  "ui": "SMART"},
    {"raw": "M-C4 DFB",             "group": 3,  "ui": "SMART"},
    {"raw": "M-C4+ / Smart UI",     "group": 4,  "ui": "SMART", "modification": "C4+"},
    {"raw": "M-C4+",                "group": 4,  "ui": "SMART", "modification": "C4+"},
    {"raw": "M-O2",                 "group": 5,  "ui": "INTEGRATED"},
    {"raw": "M-O3",                 "group": 6,  "ui": "INTEGRATED"},
    {"raw": "XMO3",                 "group": 7,  "ui": "INTEGRATED", "modification": "XMO3"},
    {"raw": "M-O5",                 "group": 8,  "ui": "SMART"},
    {"raw": "M-O5(Prime)(6각)",     "group": 9,  "ui": "SMART", "modification": "Prime"},
    {"raw": "M-O5 Prime",           "group": 9,  "ui": "SMART", "modification": "Prime"},
    {"raw": "Inline Ox",            "group": 10, "structure": "INLINE"},
    {"raw": "Ox(inline)",           "group": 10, "structure": "INLINE"},
    {"raw": "Inline Ox (CIP)",      "group": 10, "structure": "INLINE", "modification": "CIP"},
    {"raw": "Cluster Ox",           "group": 12, "structure": "CLUSTER"},
    {"raw": "Inline O2",            "group": 11, "structure": "INLINE"},
    {"raw": "Cluster O2",           "group": 13, "structure": "CLUSTER"},
    {"raw": "5각 Cluster O2",       "group": 13, "structure": "CLUSTER"},
    {"raw": "HPA",                  "group": 14},
]

# ----------------------------------------------------------------------------- 장소표 (line_master)  (site, sheet, line) → area
LINES: list[dict] = [
    *[{"site": "평택", "sheet": s, "line": l, "area": "MEMORY"} for s, l in
      [("P1", "P1F"), ("P1", "P1D"), ("P2", "P2F"), ("P2", "P2D"), ("P3", "P3F"), ("P3", "P3D"), ("P4", "P4F")]],
    *[{"site": "평택", "sheet": s, "line": s, "area": "FOUNDRY"} for s in ("S5", "V2", "P3-S5")],
    *[{"site": "화성", "sheet": "Memory", "line": l, "area": "MEMORY"} for l in
      ("NRD (MR2동)", "13L", "15L", "16L", "17L", "M1", "12L")],
    *[{"site": "화성", "sheet": "Foundry", "line": l, "area": "FOUNDRY"} for l in
      ("S1 (기흥)", "1L (기흥)", "5L (기흥)", "S3", "V1", "NRD-V (V1)", "MFAB", "NRD (MR2동)")],
]


def norm_text(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


_ALIAS_INDEX = {norm_text(a["raw"]): a for a in ALIASES}
_LINE_INDEX = {(l["site"], l["sheet"], norm_text(l["line"])): l for l in LINES}


def find_alias(raw: str) -> Optional[dict]:
    return _ALIAS_INDEX.get(norm_text(raw))


def find_line(site: str, sheet: str, line: str) -> Optional[dict]:
    return _LINE_INDEX.get((site, sheet, norm_text(line)))


def base_group(group_id: int) -> int:
    """개조형은 부모 설비군으로 (C4+ → C4, XMO3 → O3, O5 Prime → O5)"""
    g = GROUPS[group_id]
    return g["parent"] if g.get("parent") else group_id


def find_family(group_id: int, ui: Optional[str], area: Optional[str], structure: Optional[str]) -> Optional[int]:
    """(설비군, UI, 메모리/파운드리, 구조) → 계열. 정확히 → COMMON → UI 무시 순으로 느슨하게."""
    gid = base_group(group_id)
    cands = [fid for fid, f in FAMILIES.items() if f["group"] == gid]
    def pick(pred):
        hits = [fid for fid in cands if pred(FAMILIES[fid])]
        return hits[0] if len(hits) == 1 else (hits[0] if hits else None)
    for pred in (
        lambda f: (f["ui"] == ui or f["ui"] is None) and f["area"] == area and (f["structure"] == structure or f["structure"] is None),
        lambda f: (f["ui"] == ui or f["ui"] is None) and f["area"] == "COMMON" and (f["structure"] == structure or f["structure"] is None),
        lambda f: f["area"] == area and (f["structure"] == structure or f["structure"] is None),
        lambda f: f["area"] == "COMMON",
    ):
        fid = pick(pred)
        if fid:
            return fid
    return None


def families_in_category(category: str) -> list[int]:
    return [fid for fid, f in FAMILIES.items() if GROUPS[f["group"]]["category"] == category]
