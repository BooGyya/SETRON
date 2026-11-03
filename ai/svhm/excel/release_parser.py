# -*- coding: utf-8 -*-
"""릴리즈노트 파서 → 버전 사다리(ladder) + 변경 항목.

입력은 2차원 셀 값 리스트(병합 셀은 좌상단만 값). 세로형(멘토 권장)만 지원하며, 가로형은 needs_ai_mapping 으로 돌려준다.
골든테스트 B1~B11:
  B1  헤더 행 자동 탐지 (C4 2행, O5 3행, O3 4행)            B2  한 셀 두 버전 'v4.56.6.42, v4.56.6.42 _DFB (날짜)' → 항목 공유
  B3  NormalC4/DFB O·X → 항목 적용 범위                     B4  날짜가 별도 열('릴리즈일'), 열 이름 'Ver.'·'변경사항'
  B5  날짜 '(2026.02.16)' 점 구분                           B6  'v1.1.1.6B_6 + Hotfix' 는 버전 이름 그대로
  B7  '_DEX' 파생 분리                                      B8  'Prime' 항목 표시
  B9  P0 / '환경안전' → 위험 아이템 후보                      B10 가로형 → AI 열 매핑 필요
  B11 version_order = 나열 순서, 날짜 역전 시 경고
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Any, Optional

ALIASES: dict[str, tuple[str, ...]] = {
    "version":  ("버전명", "버전", "version", "ver.", "ver", "swver", "sw ver", "release"),
    "date":     ("릴리즈일", "릴리즈 일", "출시일", "release date", "releasedate", "date", "일자"),
    "no":       ("no", "no.", "번호", "순번"),
    "module":   ("항목", "구분", "모듈", "module", "part"),
    "content":  ("update내용", "update 내용", "변경사항", "변경 내용", "변경내용", "내용", "description", "desc"),
    "jira":     ("jira", "remark", "항목id", "항목 id", "issue", "ticket"),
    "owner":    ("담당", "담당자", "owner"),
    "priority": ("우선순위", "priority", "중요도"),
    "hotfix":   ("hotfix 여부", "hotfix여부", "hotfix", "핫픽스"),
    "line":     ("접수라인", "접수 라인", "line"),
    "note":     ("비고", "note", "memo"),
    "disable":  ("비활성화가능여부", "비활성화 가능 여부"),
    "hw":       ("hw연관", "hw 연관"),
    "customer": ("고객사연관", "고객사 연관"),
}
VARIANT_TOKENS = ("DFB", "DEX")
SAFETY_WORDS = ("환경안전", "interlock", "인터락", "emergency", "safety")
_DATE_RE = re.compile(r"\(\s*(\d{4})[-./](\d{1,2})[-./](\d{1,2})\s*\)")
_VERSION_LIKE = re.compile(r"^[vV]?\d+(?:\.\d+)+")
_PRIME_RE = re.compile(r"\bprime\b", re.I)


def _norm(s: Any) -> str:
    return re.sub(r"\s+", "", str(s)).lower() if s not in (None, "") else ""


def _clean(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s)).strip() if s not in (None, "") else ""


def _alias_of(name: Any) -> Optional[str]:
    n = _norm(name)
    if not n:
        return None
    for logical, aliases in ALIASES.items():
        for a in aliases:
            if n == _norm(a):
                return logical
    for logical in ("content", "version", "date", "jira", "priority", "hotfix", "module"):
        for a in ALIASES[logical]:
            an = _norm(a)
            if len(an) >= 2 and an in n:
                return logical
    return None


def _truthy_flag(v: Any) -> Optional[bool]:
    s = _norm(v)
    if s in ("o", "y", "yes", "true", "1", "적용", "가능", "○", "◯"):
        return True
    if s in ("x", "n", "no", "false", "0", "미적용", "불가", "×"):
        return False
    return None


def _to_date(v: Any) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    m = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", str(v or ""))
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


# ----------------------------------------------------------------------------- 결과 구조
@dataclass
class ReleaseItem:
    no: Optional[int]
    module: str
    content: str
    jira: str = ""
    owner: str = ""
    priority: str = ""
    applies_to: list[str] = field(default_factory=lambda: ["ALL"])   # ALL / NORMAL / DFB / DEX / UNKNOWN(빈 칸) / NONE(전부 X)
    is_safety_candidate: bool = False
    is_prime: bool = False
    hotfix_flag: Optional[bool] = None
    extras: dict = field(default_factory=dict)
    source_row: int = -1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReleaseVersion:
    name: str                 # 릴리즈노트에 적힌 버전 이름 (날짜 제거, 공백 정리). '+ Hotfix' 포함 가능
    base: str                 # 파생 토큰을 뗀 이름
    variant: str              # '' / DFB / DEX
    release_date: Optional[date]
    order: int                # 시트 안 나열 순서 (10, 20, 30 …) — 수동 삽입 여지
    lineage_order: int        # 같은 파생 줄기(base/DFB/DEX) 안에서의 순서
    item_rows: list[int]      # 이 버전에 속한 항목의 source_row
    note: str = ""
    source_row: int = -1

    def to_dict(self) -> dict:
        d = asdict(self); d["release_date"] = self.release_date.isoformat() if self.release_date else None
        return d


@dataclass
class ReleaseSheetResult:
    sheet: str
    title: str
    header_row: int
    columns: dict[str, int]          # logical -> 1-based col
    flag_columns: dict[str, int]     # NORMAL/DFB/DEX -> 1-based col (있을 때만)
    versions: list[ReleaseVersion]
    items: list[ReleaseItem]
    warnings: list[str]
    needs_ai_mapping: bool = False
    hint: str = ""

    def ladder(self, variant: str = "") -> list[str]:
        return [v.name for v in self.versions if v.variant == variant]

    def summary(self) -> str:
        if self.needs_ai_mapping:
            return f"{self.sheet} 시트: 세로형 릴리즈노트가 아니에요 → AI에게 열 위치를 물어보는 단계로 ({self.hint})"
        var = {}
        for v in self.versions:
            var[v.variant or "기본"] = var.get(v.variant or "기본", 0) + 1
        var_txt = ", ".join(f"{k} {n}개" for k, n in var.items())
        safety = sum(1 for i in self.items if i.is_safety_candidate)
        prime = sum(1 for i in self.items if i.is_prime)
        msg = f"{self.sheet} 시트: 버전 {len(self.versions)}개({var_txt}), 변경 항목 {len(self.items)}건"
        if safety: msg += f", 위험 아이템 후보 {safety}건"
        if prime: msg += f", Prime 전용 {prime}건"
        if self.warnings: msg += f", 경고 {len(self.warnings)}"
        return msg


# ----------------------------------------------------------------------------- 헤더
def find_header(rows: list[list[Any]], max_scan: int = 15) -> Optional[tuple[int, dict[str, int], dict[str, int]]]:
    for i, row in enumerate(rows[:max_scan]):
        logical = {}
        flags = {}
        for c, v in enumerate(row):
            if v in (None, ""):
                continue
            n = _norm(v)
            if n.startswith("normal"):
                flags["NORMAL"] = c; continue
            if n in ("dfb", "dex"):
                flags[n.upper()] = c; continue
            a = _alias_of(v)
            if a and a not in logical:
                logical[a] = c
        if "version" in logical and ("content" in logical or ("no" in logical and "module" in logical)):
            return i, logical, flags
    return None


def _horizontal_hint(rows: list[list[Any]]) -> str:
    for r in rows[:8]:
        n = sum(1 for v in r if v not in (None, "") and _VERSION_LIKE.match(_clean(v)))
        if n >= 3:
            return f"버전처럼 보이는 값 {n}개가 한 행에 가로로 나열됨 (가로형)"
    return "버전명/내용 제목줄을 찾지 못함"


# ----------------------------------------------------------------------------- 버전 셀
def split_version_cell(text: Any) -> tuple[list[str], Optional[date]]:
    """'v4.56.6.42, v4.56.6.42 _DFB (2026-01-23)' → (['v4.56.6.42', 'v4.56.6.42_DFB'], 2026-01-23)
    'v1.1.1.6B_6 + Hotfix (2026-03-08)'           → (['v1.1.1.6B_6 + Hotfix'], 2026-03-08)   # 이름 그대로"""
    raw = str(text)
    m = _DATE_RE.search(raw)
    d = date(int(m[1]), int(m[2]), int(m[3])) if m else None
    body = _DATE_RE.sub("", raw)
    body = re.sub(r"\s*_\s*(DFB|DEX)\b", r"_\1", body, flags=re.I)   # 'v… _DFB' → 'v…_DFB'
    names = []
    for part in re.split(r"[,;/]", body):
        p = _clean(part)
        if p:
            names.append(p)
    return names, d


def split_variant(name: str) -> tuple[str, str]:
    """'v4.56.6.42_DFB' → ('v4.56.6.42', 'DFB'); 'v4.56.8.1_f1_DEX' → ('v4.56.8.1_f1', 'DEX')"""
    for tok in VARIANT_TOKENS:
        m = re.search(rf"[_\-\s]{tok}$", name, flags=re.I)
        if m:
            return name[:m.start()].strip(), tok
    return name, ""


# ----------------------------------------------------------------------------- 본체
def parse_release_rows(rows: list[list[Any]], sheet: str = "") -> ReleaseSheetResult:
    width = max((len(r) for r in rows), default=0)
    rows = [list(r) + [None] * (width - len(r)) for r in rows]
    title = _clean(rows[0][0]) if rows and rows[0] and rows[0][0] else ""
    found = find_header(rows)
    if not found:
        return ReleaseSheetResult(sheet, title, -1, {}, {}, [], [], ["제목줄을 찾지 못함"], True, _horizontal_hint(rows))
    h, cols, flags = found
    warnings: list[str] = []
    versions: list[ReleaseVersion] = []
    items: list[ReleaseItem] = []
    name_to_version: dict[str, ReleaseVersion] = {}
    cur: list[ReleaseVersion] = []
    order = 0
    lineage_counter: dict[str, int] = {}
    last_date: Optional[date] = None

    def cell(row, key):
        c = cols.get(key)
        return row[c] if c is not None and c < len(row) else None

    for r in range(h + 1, len(rows)):
        row = rows[r]
        if all(v in (None, "") for v in row):
            continue
        vcell = cell(row, "version")
        if vcell not in (None, ""):
            names, d = split_version_cell(vcell)
            dcol = cell(row, "date")
            if d is None and dcol not in (None, ""):
                d = _to_date(dcol)
            cur = []
            for nm in names:
                if nm in name_to_version:           # 같은 버전이 또 나오면 이어 붙임
                    cur.append(name_to_version[nm]); continue
                base, variant = split_variant(nm)
                order += 10
                lineage_counter[variant] = lineage_counter.get(variant, 0) + 10
                v = ReleaseVersion(nm, base, variant, d, order, lineage_counter[variant], [], _clean(cell(row, "note")), r + 1)
                versions.append(v); name_to_version[nm] = v; cur.append(v)
            if d and last_date and d < last_date:
                warnings.append(f"{r + 1}행 {names[0]}: 날짜 {d}가 앞 버전({last_date})보다 빠름 — 나열 순서 확인")
            if d:
                last_date = d
        content = _clean(cell(row, "content"))
        if not content:
            continue
        if not cur:
            warnings.append(f"{r + 1}행: 소속 버전 없이 항목만 있음 → 건너뜀"); continue
        no_raw = cell(row, "no")
        try:
            no = int(str(no_raw).strip()) if no_raw not in (None, "") else None
        except ValueError:
            no = None
        applies = []
        blank = False
        for var, c in flags.items():
            f = _truthy_flag(row[c])
            if f is True:
                applies.append(var)
            elif f is None:
                blank = True
        if not applies:
            # 플래그 열이 없으면 전체 적용. 있는데 O 가 하나도 없으면: 빈 칸이 섞였으면 미확인, 전부 X 면 적용 없음
            applies = ["ALL"] if not flags else (["UNKNOWN"] if blank else ["NONE"])
        priority = _clean(cell(row, "priority"))
        content_l = content.lower()
        item = ReleaseItem(
            no=no, module=_clean(cell(row, "module")), content=content, jira=_clean(cell(row, "jira")),
            owner=_clean(cell(row, "owner")), priority=priority, applies_to=applies,
            is_safety_candidate=(priority.upper() == "P0") or any(w in content_l for w in SAFETY_WORDS),
            is_prime=bool(_PRIME_RE.search(content)),
            hotfix_flag=_truthy_flag(cell(row, "hotfix")),
            extras={k: _clean(cell(row, k)) for k in ("line", "note", "disable", "hw", "customer") if cols.get(k) is not None and _clean(cell(row, k))},
            source_row=r + 1,
        )
        items.append(item)
        for v in cur:
            v.item_rows.append(r + 1)
    if not versions:
        warnings.append("버전이 하나도 없음")
    return ReleaseSheetResult(sheet, title, h + 1, {k: c + 1 for k, c in cols.items()}, {k: c + 1 for k, c in flags.items()},
                              versions, items, warnings)


# ----------------------------------------------------------------------------- 입력 어댑터
def rows_from_openpyxl(path: str, sheet: Optional[str] = None) -> list[list[Any]]:
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def rows_from_xlwings(sheet_name: Optional[str] = None) -> list[list[Any]]:
    import xlwings as xw
    book = xw.books.active
    sht = book.sheets[sheet_name] if sheet_name else book.sheets.active
    vals = sht.used_range.value
    return vals if isinstance(vals[0], list) else [vals]


def parse_release_workbook(path: str, sheets: Optional[list[str]] = None) -> list[ReleaseSheetResult]:
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    return [parse_release_rows([list(r) for r in wb[s].iter_rows(values_only=True)], s) for s in (sheets or wb.sheetnames)]
