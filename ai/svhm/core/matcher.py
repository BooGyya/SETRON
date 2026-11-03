# -*- coding: utf-8 -*-
"""D. 버전 맞추기 — 현황표 버전 글자를 릴리즈노트 사다리의 칸에 올린다.

4단계 (지표 정의서 v3):
  ① 글자 그대로 같은 칸이 있으면 EXACT          (앞뒤 공백·'(H)' 화성 표시는 떼고 비교)
  ② 없으면 점·밑줄·대소문자를 맞춰 하나만 맞으면 NORMALIZED (표기 보정됨)
  ③ 둘 이상 맞으면 AMBIGUOUS → 후보 제시, 사용자 선택
  ④ 없으면 UNREGISTERED. 이때 같은 대분류의 다른 릴리즈노트에만 있으면 '설비군 오기' 후보(16L v1060 사례),
     기본 이름이 사다리에 있으면 '릴리즈노트에 추가' 위치 제안(v1.1.1.6B_6_c 사례)
빈 칸은 MISSING. 사용자가 추가한 칸은 is_manual=True 로 기록(10/29 회신).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from svhm.core import refdata as R


def norm_version(text: str) -> str:
    """비교용 키: 소문자, 공백 제거, '(h)' 제거, 밑줄·대시 → 점, 앞 'v' 제거.
    점/밑줄을 '제거'하지 않고 '통일'하는 이유: v4.56.6.42 와 v4.56.64.2 가 섞이면 안 되므로."""
    s = str(text or "").strip().lower()
    s = re.sub(r"\(h\)", "", s)
    s = re.sub(r"\s+", "", s)
    s = s.replace("_", ".").replace("-", ".")
    return s[1:] if s.startswith("v") else s


def preclean(text: str) -> str:
    """①단계용: 공백 정리 + '(H)' 제거만. 글자는 보존."""
    s = re.sub(r"\(\s*H\s*\)", "", str(text or ""))
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class Rung:
    name: str
    base: str
    variant: str
    order: int
    lineage_order: int
    release_date: Optional[str] = None
    is_manual: bool = False

    @property
    def key(self) -> str:
        return norm_version(self.name)


@dataclass
class Ladder:
    family_id: int
    rungs: list[Rung] = field(default_factory=list)

    @property
    def name(self) -> str:
        return R.FAMILIES[self.family_id]["name"]

    def by_variant(self, variant: str) -> list[Rung]:
        return [r for r in self.rungs if r.variant == variant]

    def add_manual(self, name: str, after: Optional[str] = None) -> Rung:
        """사용자 수동 등록: after 칸 바로 뒤에 끼움 (순서 번호 사이값)."""
        base, variant = _split_variant(name)
        if after:
            idx = next(i for i, r in enumerate(self.rungs) if r.name == after)
            prev = self.rungs[idx]
            nxt = self.rungs[idx + 1] if idx + 1 < len(self.rungs) else None
            order = (prev.order + nxt.order) // 2 if nxt else prev.order + 10
            same = [r for r in self.rungs if r.variant == variant]
            j = same.index(prev) if prev in same else len(same) - 1
            lo = (same[j].lineage_order + same[j + 1].lineage_order) // 2 if j + 1 < len(same) else same[j].lineage_order + 10
            rung = Rung(name, base, variant, order, lo, None, True)
            self.rungs.insert(idx + 1, rung)
        else:
            last = self.rungs[-1] if self.rungs else None
            rung = Rung(name, base, variant, (last.order + 10) if last else 10,
                        (max([r.lineage_order for r in self.by_variant(variant)] or [0]) + 10), None, True)
            self.rungs.append(rung)
        return rung


def _split_variant(name: str) -> tuple[str, str]:
    for tok in ("DFB", "DEX"):
        m = re.search(rf"[_\-\s]{tok}$", name, flags=re.I)
        if m:
            return name[:m.start()].strip(), tok
    return name, ""


class LadderRegistry:
    """계열 → 사다리. 릴리즈노트 파서 결과(ReleaseSheetResult)로 채운다."""

    def __init__(self):
        self.ladders: dict[int, Ladder] = {}

    def add_from_release(self, family_id: int, release_result) -> Ladder:
        lad = Ladder(family_id)
        for v in release_result.versions:
            lad.rungs.append(Rung(v.name, v.base, v.variant, v.order, v.lineage_order,
                                  v.release_date.isoformat() if v.release_date else None))
        self.ladders[family_id] = lad
        return lad

    def get(self, family_id: int) -> Optional[Ladder]:
        return self.ladders.get(family_id)

    def find_elsewhere(self, key: str, category: str, exclude: int) -> list[tuple[int, Rung]]:
        out = []
        for fid in R.families_in_category(category):
            if fid == exclude or fid not in self.ladders:
                continue
            for r in self.ladders[fid].rungs:
                if r.key == key:
                    out.append((fid, r))
        return out


@dataclass
class MatchResult:
    status: str                           # EXACT / NORMALIZED / AMBIGUOUS / UNREGISTERED / MISSING / NO_LADDER
    input_text: str
    rung: Optional[Rung] = None
    candidates: list[Rung] = field(default_factory=list)
    group_suggestions: list[tuple[int, str]] = field(default_factory=list)   # (family_id, 버전 이름) — 설비군 오기 후보
    add_after: Optional[str] = None       # '릴리즈노트에 추가' 위치 제안
    note: str = ""

    @property
    def message(self) -> str:
        return {
            "EXACT": "릴리즈노트와 일치",
            "NORMALIZED": f"표기 보정됨 → {self.rung.name if self.rung else ''}",
            "AMBIGUOUS": "비슷한 버전이 여러 개 — 사용자 선택 필요: " + ", ".join(c.name for c in self.candidates),
            "UNREGISTERED": "릴리즈노트에 없는 버전(미등록)" + (f" — {self.note}" if self.note else ""),
            "MISSING": "버전 칸이 비어 있음",
            "NO_LADDER": "이 계열의 릴리즈노트가 없음",
        }[self.status]


def match_version(text: str, ladder: Optional[Ladder], registry: Optional[LadderRegistry] = None,
                  category: Optional[str] = None) -> MatchResult:
    raw = str(text or "")
    if not raw.strip():
        return MatchResult("MISSING", raw)
    if ladder is None or not ladder.rungs:
        return MatchResult("NO_LADDER", raw)
    cleaned = preclean(raw)
    # ① 그대로
    for r in ladder.rungs:
        if r.name == cleaned:
            return MatchResult("EXACT", raw, r)
    # ② 표기 보정
    key = norm_version(raw)
    hits = [r for r in ladder.rungs if r.key == key]
    if len(hits) == 1:
        return MatchResult("NORMALIZED", raw, hits[0], note=f"'{cleaned}' → '{hits[0].name}'")
    if len(hits) > 1:                                   # ③ 후보 여럿
        return MatchResult("AMBIGUOUS", raw, None, hits)
    # ④ 미등록 — 설비군 오기 후보 / 추가 위치 제안
    res = MatchResult("UNREGISTERED", raw)
    if registry and category:
        else_hits = registry.find_elsewhere(key, category, ladder.family_id)
        if else_hits:
            res.group_suggestions = [(fid, r.name) for fid, r in else_hits]
            res.note = "이 버전은 " + ", ".join(R.FAMILIES[f]["name"] for f, _ in else_hits) + " 릴리즈노트에만 있어요 — 설비군이 잘못 적힌 것 아닌가요?"
    base_key = re.sub(r"\.[a-z]\d*$", "", key)            # 'v1.1.1.6b.6.c' → 'v1.1.1.6b.6'
    if base_key != key:
        base_hit = next((r for r in ladder.rungs if r.key == base_key), None)
        if base_hit:
            res.add_after = base_hit.name
            res.note = (res.note + " / " if res.note else "") + f"기본 버전 '{base_hit.name}'은 있음 — 그 뒤에 '릴리즈노트에 추가' 가능"
    return res


def relative_position(rung: Rung, target: Rung, ladder: Ladder) -> int:
    """같은 줄기 안에서 설비 칸이 기준 칸보다 몇 칸 위(+)/아래(-)인지."""
    same = sorted(ladder.by_variant(rung.variant), key=lambda r: r.lineage_order)
    names = [r.name for r in same]
    return names.index(rung.name) - names.index(target.name)
