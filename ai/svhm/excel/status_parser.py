# -*- coding: utf-8 -*-
"""설비 현황표(출석부) 파서.

입력은 '2차원 셀 값 리스트'(병합 셀은 좌상단만 값). openpyxl(파일)·xlwings(열린 Excel) 어느 쪽이든 같은 코드.
처리하는 양식 특징 (골든테스트 A1~A20):
  - 한 행에 표가 옆으로 2~3개 (평택 Poly | Oxide | HPA)            → 세그먼트 분할
  - 두 줄 헤더 (화성 'Version' ▸ 'SW' / 'Engine')                    → 복합 헤더 이름 'Version/SW'
  - 시트마다 열이 밀림 (화성 Foundry 는 'No.' 없음)                  → 헤더 이름으로 열 찾기
  - 세로 병합 (화성 Line·설비군, 평택 블록 제목)                      → fill-down
  - 블록 제목 'P1F(3F)\\n(61)' 과 블록마다 반복되는 헤더               → 블록 단위 처리 + 선언 대수 비교
  - 메모·빈 행·표 제목 행(Poly/Oxide)                                 → 건너뜀
  - 버전 칸 글자('메모리버전임'), 빈 칸, 한 칸 두 버전('O3: … / O2: …') → 이슈 표시
  - 설비호기 꼬리말 'ESJOF06 (JEP)', 같은 설비호기 두 번               → 분리·중복 이슈
헤더를 못 찾으면 예외 대신 needs_ai_mapping=True 로 돌려준다(규칙 → AI 열 매핑 흐름).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

# ----------------------------------------------------------------------------- 헤더 별칭
ALIASES: dict[str, tuple[str, ...]] = {
    "equipment": ("설비호기", "설비명", "호기", "장비명", "equipment", "eqp", "설비id", "설비번호"),
    "version":   ("version", "ver", "버전", "swver", "sw버전", "현재swver", "현재버전", "sw"),
    "group":     ("설비군(상세)", "설비군", "group", "model", "기종", "모델"),
    "category":  ("설비군",),                        # 화성 A열 (Poly/Oxide) — group 과 구분은 아래 로직에서
    "line":      ("line", "라인", "fab", "공장"),
    "no":        ("no", "no.", "번호", "순번"),
    "pm":        ("pm",),
    "bay":       ("bay",),
    "note":      ("비고", "note", "remark", "메모"),
}
SUBHEADER_WORDS = {"sw", "engine", "flow", "bay", "version", "ip", "vtr개조", "wdt"}
EQUIPMENT_CODE_RE = re.compile(r"^(?P<code>[A-Za-z][A-Za-z0-9\-]{2,})\s*(?P<tail>\(.*\))?\s*$")
BLOCK_LABEL_RE = re.compile(r"^\s*(?P<line>[A-Za-z0-9][A-Za-z0-9\-]*)\s*(?:\(\s*(?P<floor>[^)]*?)\s*\))?")
BLOCK_COUNT_RE = re.compile(r"\(\s*(\d+)\s*\)")
MULTI_VERSION_RE = re.compile(r"([A-Za-z0-9+\-]+)\s*[:：]\s*([vV]?\d[\w.\-+ ]*?)(?=\s*/\s*[A-Za-z0-9+\-]+\s*[:：]|\s*$)")
FOUNDRY_SHEETS_PT = {"S5", "V2", "P3-S5"}

# 이슈 코드 → 사람이 읽는 설명 (검토 화면·출력용). 코드는 프로그램용으로 유지.
ISSUE_LABELS: dict[str, str] = {
    "VERSION_MISSING":     "버전 칸이 비어 있음",
    "VERSION_TEXT":        "버전 칸에 숫자 없이 글자만 있음",
    "MULTI_VERSION":       "한 칸에 버전이 두 개 (PM 혼합 설비)",
    "EQUIPMENT_TAIL":      "설비호기 뒤에 꼬리말이 붙어 있음",
    "EQUIPMENT_MISSING":   "설비호기 없이 버전만 적혀 있음",
    "DUPLICATE_EQUIPMENT": "같은 설비호기가 다른 곳에도 있음",
    "ANALYSIS_EXCLUDED":   "집계·그래프 제외 대상 (HPA)",
}


def issue_text(codes: list[str]) -> str:
    return "; ".join(ISSUE_LABELS.get(c, c) for c in codes)


def _norm(s: Any) -> str:
    return re.sub(r"\s+", "", str(s)).lower() if s not in (None, "") else ""


def _clean(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s)).strip() if s not in (None, "") else ""


def _looks_like_equipment(v: Any) -> bool:
    return bool(v) and bool(EQUIPMENT_CODE_RE.match(_clean(v))) and any(ch.isdigit() for ch in str(v))


# ----------------------------------------------------------------------------- 결과 구조
@dataclass
class VersionEntry:
    label: str          # '' 이면 단일 버전, PM 혼합이면 'O3'/'O2'
    text: str


@dataclass
class EquipmentRow:
    site: Optional[str]
    sheet: str
    block_label: Optional[str]
    line: Optional[str]
    floor: Optional[str]
    process_group: Optional[str]     # 표 제목(Poly/Oxide/HPA) 또는 화성 '설비군' 열
    group_raw: str                   # 현황표에 적힌 설비군 글자 그대로 (번역표 key)
    equipment_raw: str
    equipment_code: str
    equipment_tail: Optional[str]    # '(JEP)' 같은 꼬리말
    version_raw: str
    versions: list[VersionEntry]
    production_area: Optional[str]   # MEMORY / FOUNDRY (장소표 기본값)
    source: dict                     # {'sheet','row','col_equipment','col_version'} (1-based)
    issues: list[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Segment:
    index: int
    title: Optional[str]
    col_start: int
    col_end: int
    cols: dict[str, int]             # logical -> 0-based col
    header_names: dict[int, str]     # col -> 복합 헤더 이름


@dataclass
class Block:
    label: Optional[str]
    line: Optional[str]
    floor: Optional[str]
    declared_count: Optional[int]
    header_row: int                  # 1-based
    extracted_count: int = 0
    warning: Optional[str] = None


@dataclass
class SheetParseResult:
    sheet: str
    site: Optional[str]
    rows: list[EquipmentRow]
    blocks: list[Block]
    layout: dict
    warnings: list[str]
    skipped: dict
    needs_ai_mapping: bool = False

    def summary(self) -> str:
        """사람이 읽는 한 줄 요약."""
        if self.needs_ai_mapping:
            hint = self.layout.get("hint")
            return f"{self.sheet} 시트: 표의 제목줄을 찾지 못했어요 → AI에게 열 위치를 물어보는 단계로 넘깁니다" + (f" ({hint})" if hint else "")
        n_issue = sum(1 for r in self.rows if r.issues)
        slots = self.skipped.get("no_equipment_slot", 0)
        seg = self.layout.get("segments", 1)
        msg = f"{self.sheet} 시트: 설비 {len(self.rows)}대 읽음"
        msg += f" (확인 필요 {n_issue}대)" if n_issue else " (확인 필요 없음)"
        msg += f" · 표 묶음 {len(self.blocks)}개"
        msg += f" · 한 줄에 표 {seg}개 나란히" if seg > 1 else ""
        if slots:
            msg += f" · 설비호기가 비어 있는 자리 {slots}개는 건너뜀"
        if not self.rows and slots:
            msg += " ← 설비호기·버전이 안 채워진 빈 양식으로 보여요"
        return msg

    def human_warnings(self) -> list[str]:
        out = []
        for b in self.blocks:
            if b.warning:
                name = f"{b.line} {b.floor}" if b.floor else (b.line or b.label)
                out.append(f"'{name}' 묶음: 제목에는 {b.declared_count}대라고 적혀 있는데 실제로 읽은 설비는 {b.extracted_count}대예요 (경고만, 저장은 가능)")
        for w in self.warnings:
            if "선언 대수" not in w:
                out.append(w)
        return out


# ----------------------------------------------------------------------------- 헤더 탐지
def _alias_of(name: str) -> Optional[str]:
    n = _norm(name)
    if not n:
        return None
    for logical in ("equipment", "group", "line", "no", "pm", "bay", "note", "version"):
        for a in ALIASES[logical]:
            an = _norm(a)
            if n == an:
                return logical
    # 부분 일치 (복합 헤더 'Version/SW', '현재 SW VER' 등)
    for logical in ("equipment", "group", "line", "version"):
        for a in ALIASES[logical]:
            an = _norm(a)
            if len(an) >= 2 and an in n:
                return logical
    return None


def _is_subheader_row(row: list[Any]) -> bool:
    """두 줄 헤더의 아랫줄인지: 'SW/Engine/Flow/Bay…' 같은 세부 이름만 있고, 그 자체로 헤더(설비호기 열 포함)는 아님."""
    vals = [_norm(v) for v in row if v not in (None, "")]
    if not vals or any(_looks_like_equipment(v) for v in row):
        return False
    if any(_alias_of(v) == "equipment" for v in row if v not in (None, "")):
        return False
    return sum(1 for v in vals if v in SUBHEADER_WORDS) >= 2


def _compound_header(top: list[Any], sub: Optional[list[Any]]) -> dict[int, str]:
    """두 줄 헤더를 'Version/SW' 처럼 합친다. 윗줄 병합 셀은 오른쪽으로 채운다(아랫줄에 값이 있을 때만)."""
    width = max(len(top), len(sub) if sub else 0)
    names: dict[int, str] = {}
    last_top = None
    for c in range(width):
        t = top[c] if c < len(top) else None
        s = sub[c] if sub and c < len(sub) else None
        if t not in (None, ""):
            last_top = _clean(t)
            names[c] = last_top if s in (None, "") else f"{last_top}/{_clean(s)}"
        elif s not in (None, "") and last_top:
            names[c] = f"{last_top}/{_clean(s)}"
        elif s not in (None, ""):
            names[c] = _clean(s)
    return names


def _version_score(name: str) -> int:
    n = _norm(name)
    if "version" not in n and "ver" not in n and "버전" not in n and not n.endswith("/sw"):
        return -100
    score = 0
    if "engine" in n or "epd" in n or "/ip" in n or n.endswith("ip"):
        score -= 50
    if "/sw" in n or "swver" in n or "sw버전" in n:
        score += 20
    if n.startswith("version") or n.startswith("버전") or n.startswith("현재"):
        score += 10
    return score


def find_header_rows(rows: list[list[Any]]) -> list[tuple[int, int, dict[int, str]]]:
    """(헤더 행 index, 데이터 시작 index, 열이름 dict) 목록. 두 줄 헤더면 데이터 시작은 +2."""
    out = []
    i = 0
    while i < len(rows):
        row = rows[i]
        sub = rows[i + 1] if i + 1 < len(rows) and _is_subheader_row(rows[i + 1]) else None
        names = _compound_header(row, sub)
        names = {c: n for c, n in names.items() if len(n) <= 25}      # 제목은 짧다. 긴 문장(릴리즈 항목 등)은 제목 후보에서 제외
        logicals = {_alias_of(v) for v in names.values()} - {None}
        if "equipment" in logicals and ("version" in logicals or "group" in logicals):
            out.append((i, i + (2 if sub else 1), names))
            i += 2 if sub else 1
            continue
        i += 1
    return out


# ----------------------------------------------------------------------------- 세그먼트(나란한 표) 분할
def build_segments(names: dict[int, str], title_row: Optional[list[Any]]) -> list[Segment]:
    cols_sorted = sorted(names)
    eq_cols = [c for c in cols_sorted if _alias_of(names[c]) == "equipment"]
    if not eq_cols:
        return []
    no_cols = [c for c in cols_sorted if _alias_of(names[c]) == "no"]
    if len(eq_cols) == 1:
        starts = [cols_sorted[0]]
    elif len(no_cols) == len(eq_cols):
        starts = no_cols
    else:  # 경계 추정: 각 설비호기 열에서 왼쪽으로 가장 가까운 '설비군' 열, 그 바로 앞
        starts = []
        for e in eq_cols:
            g = max((c for c in cols_sorted if c < e and _alias_of(names[c]) == "group"), default=e)
            starts.append(min(g, e))
    segs: list[Segment] = []
    for k, start in enumerate(starts):
        end = (starts[k + 1] - 1) if k + 1 < len(starts) else max(cols_sorted)
        seg_names = {c: n for c, n in names.items() if start <= c <= end}
        cols: dict[str, int] = {}
        # version: 점수 최고 열
        vcands = [(c, _version_score(n)) for c, n in seg_names.items()]
        vcands = [x for x in vcands if x[1] > -100]
        if vcands:
            cols["version"] = max(vcands, key=lambda x: (x[1], -x[0]))[0]
        for logical in ("equipment", "line", "no", "pm", "bay", "note"):
            for c, n in sorted(seg_names.items()):
                if _alias_of(n) == logical and logical not in cols:
                    cols[logical] = c
        # group: '설비군(상세)' 우선, 그다음 '설비군'. '설비군' 이 둘이면 첫 번째는 category
        gcols = [c for c, n in sorted(seg_names.items()) if _alias_of(n) == "group"]
        detail = [c for c in gcols if "상세" in _norm(seg_names[c])]
        if detail:
            cols["group"] = detail[0]
            others = [c for c in gcols if c != detail[0]]
            if others:
                cols["category"] = others[0]
        elif gcols:
            cols["group"] = gcols[0]
            if len(gcols) > 1:
                cols["category"] = gcols[0]; cols["group"] = gcols[1]
        title = None
        if title_row:
            for c in range(start, end + 1):
                if c < len(title_row) and _clean(title_row[c]) and not _looks_like_equipment(title_row[c]):
                    title = _clean(title_row[c]); break
        segs.append(Segment(k, title, start, end, cols, seg_names))
    return segs


# ----------------------------------------------------------------------------- 보조
def parse_block_label(text: str) -> tuple[Optional[str], Optional[str], Optional[int], str]:
    """'P1F(3F)\\n(61)\\n*신설비' → ('P1F', '3F', 61, '*신설비')"""
    raw = str(text)
    first = raw.strip().splitlines()[0] if raw.strip() else ""
    m = re.match(r"^\s*(?P<line>[A-Za-z0-9][A-Za-z0-9\-]*)", first)
    line = m.group("line") if m else None
    fm = re.search(r"\(\s*(\d+\s*F)\s*\)", raw)
    floor = re.sub(r"\s+", "", fm.group(1)) if fm else None
    cm = BLOCK_COUNT_RE.search(raw)
    count = int(cm.group(1)) if cm else None
    note = " ".join(l.strip() for l in raw.splitlines()[1:]
                    if l.strip() and not BLOCK_COUNT_RE.fullmatch(l.strip()) and not re.fullmatch(r"\(\s*\d+\s*F\s*\)", l.strip()))
    return line, floor, count, note


def split_versions(text: str) -> tuple[list[VersionEntry], list[str]]:
    """버전 칸 → (버전 목록, 이슈). 'O3: v4.50.1.1_a / O2: v3.52_F15' → 2개."""
    t = _clean(text)
    if not t:
        return [], ["VERSION_MISSING"]
    if not any(ch.isdigit() for ch in t):
        return [], ["VERSION_TEXT"]            # '메모리버전임' 같은 글자
    pairs = MULTI_VERSION_RE.findall(t)
    if len(pairs) >= 2:
        return [VersionEntry(lbl.strip(), ver.strip()) for lbl, ver in pairs], ["MULTI_VERSION"]
    return [VersionEntry("", t)], []


def default_area_resolver(site: Optional[str], sheet: str, line: Optional[str]) -> Optional[str]:
    if sheet.lower() in ("memory", "foundry"):
        return sheet.upper()
    if sheet in FOUNDRY_SHEETS_PT:
        return "FOUNDRY"
    if re.match(r"^P\d", sheet or ""):
        return "MEMORY"
    return None


def guess_site(sheet_names: list[str]) -> Optional[str]:
    low = {s.lower() for s in sheet_names}
    if low & {"memory", "foundry"}:
        return "화성"
    if any(re.match(r"^(p\d|s5|v2|p3-s5)$", s) for s in low):
        return "평택"
    return None



def _collect_extras(row: list[Any], seg: "Segment", area_hint: Optional[str]) -> dict:
    """매핑되지 않은 칸도 버리지 않는다: 헤더가 있으면 헤더 이름으로, 없으면 unlabeled 로 보존(메모 등)."""
    mapped = set(seg.cols.values())
    extras: dict = {}
    for k in ("no", "pm", "bay", "note"):
        c = seg.cols.get(k)
        if c is not None and c < len(row) and _clean(row[c]):
            extras[k] = _clean(row[c])
    unlabeled = []
    for c in range(seg.col_start, min(seg.col_end, len(row) - 1) + 1):
        if c in mapped or row[c] in (None, ""):
            continue
        name = seg.header_names.get(c)
        if name:
            extras[name] = _clean(row[c])
        else:
            unlabeled.append(_clean(row[c]))
    if unlabeled:
        extras["unlabeled"] = unlabeled
    if area_hint:
        extras["area_override_hint"] = area_hint
    return extras

# ----------------------------------------------------------------------------- 본체
def parse_status_rows(rows: list[list[Any]], sheet: str, site: Optional[str] = None,
                      area_resolver: Callable[[Optional[str], str, Optional[str]], Optional[str]] = default_area_resolver,
                      ) -> SheetParseResult:
    width = max((len(r) for r in rows), default=0)
    rows = [list(r) + [None] * (width - len(r)) for r in rows]
    headers = find_header_rows(rows)
    if not headers:
        filled = [str(v).strip() for r in rows for v in r if v not in (None, "")]
        if len(filled) <= 5:
            hint = f"거의 빈 시트: 내용 {len(filled)}칸 {filled[:3]} — 자리표시 시트로 보임"
        else:
            hint = f"내용 {len(filled)}칸 있으나 설비호기/버전 제목을 못 찾음"
        return SheetParseResult(sheet, site, [], [], {"segments": 0, "hint": hint}, ["헤더 행을 찾지 못함: " + hint], {}, needs_ai_mapping=True)

    out: list[EquipmentRow] = []
    blocks: list[Block] = []
    warnings: list[str] = []
    skipped = {"empty_row": 0, "no_equipment_slot": 0, "stray_text": 0}
    first_seg_start = None

    for hi, (h, data_start, names) in enumerate(headers):
        next_h = headers[hi + 1][0] if hi + 1 < len(headers) else len(rows)
        title_row = rows[h - 1] if h >= 1 else None
        segs = build_segments(names, title_row)
        if not segs:
            warnings.append(f"{h + 1}행 헤더에 설비호기 열 없음")
            continue
        seg_start = min(s.col_start for s in segs)
        first_seg_start = seg_start if first_seg_start is None else min(first_seg_start, seg_start)
        label_cols = [c for c in range(seg_start) if c not in names]      # 세그먼트 왼쪽의 헤더 없는 열 = 블록 제목 열
        # 블록 제목: 헤더 바로 위 행부터 위로 올라가며 label_col 에서 찾기 (평택은 h-1 행)
        label_text = None
        for c in label_cols:
            for r in range(h, max(-1, h - 3), -1):
                if r < len(rows) and _clean(rows[r][c]):
                    label_text = str(rows[r][c]); break
            if label_text:
                break
        line_b, floor_b, declared, note_b = parse_block_label(label_text) if label_text else (None, None, None, "")
        block = Block(label=_clean(label_text) if label_text else None, line=line_b, floor=floor_b,
                      declared_count=declared, header_row=h + 1)
        blocks.append(block)

        # fill-down 대상: category/line 열 (세그먼트별)
        carry: dict[tuple[int, str], Any] = {}
        for r in range(data_start, next_h):
            row = rows[r]
            if all(v in (None, "") for v in row):
                skipped["empty_row"] += 1
                continue
            # 블록 제목 행(평택 'Poly'/'Oxide' 제목 행이 다음 블록 시작)은 next_h 가 h-1 이 아니라 h 이므로
            # 여기서 만나면 안 되지만, 안전하게 '설비호기 없음 + 세그먼트 제목만' 인 행은 건너뜀
            for seg in segs:
                def cell(key):
                    c = seg.cols.get(key)
                    return row[c] if c is not None and c < len(row) else None
                for key in ("category", "line"):
                    v = cell(key)
                    if v not in (None, ""):
                        carry[(seg.index, key)] = v
                eq = _clean(cell("equipment"))
                grp = _clean(cell("group"))
                ver_cell = cell("version")
                ver = "" if ver_cell in (None, "") else str(ver_cell).strip()   # 앞뒤 공백·줄바꿈은 조용히 제거
                if not eq:
                    if grp or ver:
                        if ver:
                            issues = ["EQUIPMENT_MISSING"]
                        else:
                            skipped["no_equipment_slot"] += 1
                            continue
                    else:
                        continue
                else:
                    issues = []
                if eq and not _looks_like_equipment(eq):
                    skipped["stray_text"] += 1
                    continue
                m = EQUIPMENT_CODE_RE.match(eq)
                code = m.group("code") if m else eq
                tail = (m.group("tail") or None) if m else None
                if tail:
                    issues.append("EQUIPMENT_TAIL")
                versions, vissues = split_versions(ver)
                issues += vissues
                area_hint = None
                if "VERSION_TEXT" in vissues:            # '메모리버전임' → 메모리 릴리즈노트 예외 후보 (장소표 override)
                    if "메모리" in ver: area_hint = "MEMORY"
                    elif "파운드리" in ver: area_hint = "FOUNDRY"
                line_v = carry.get((seg.index, "line"))
                line_val = _clean(line_v) if line_v not in (None, "") else block.line
                cat_v = carry.get((seg.index, "category"))
                process_group = seg.title or (_clean(cat_v) if cat_v not in (None, "") else None)
                if "hpa" in _norm(grp) or "hpa" in _norm(process_group):
                    issues = [i for i in issues if i != "VERSION_MISSING"]   # HPA 는 릴리즈노트가 없어 버전 비어 있는 게 정상
                    issues.append("ANALYSIS_EXCLUDED")
                out.append(EquipmentRow(
                    site=site, sheet=sheet, block_label=block.label, line=line_val, floor=block.floor,
                    process_group=process_group, group_raw=grp, equipment_raw=eq, equipment_code=code,
                    equipment_tail=tail, version_raw=ver, versions=versions,
                    production_area=area_resolver(site, sheet, line_val),
                    source={"sheet": sheet, "row": r + 1,
                            "col_equipment": seg.cols.get("equipment", -1) + 1,
                            "col_version": seg.cols.get("version", -1) + 1},
                    issues=issues,
                    extras=_collect_extras(row, seg, area_hint),
                ))
                block.extracted_count += 1
        if block.declared_count is not None and block.declared_count != block.extracted_count:
            block.warning = f"선언 대수 {block.declared_count} ≠ 읽어낸 설비 {block.extracted_count}"
            warnings.append(f"블록 {block.label!r}: {block.warning}")

    layout = {"header_rows": [h + 1 for h, _, _ in headers], "two_line_header": any(d - h == 2 for h, d, _ in headers),
              "segments": max((len(build_segments(n, None)) for _, _, n in headers), default=0),
              "columns": {k: v + 1 for k, v in build_segments(headers[0][2], None)[0].cols.items()} if headers else {}}
    return SheetParseResult(sheet, site, out, blocks, layout, warnings, skipped)


def mark_duplicates(results: list[SheetParseResult]) -> list[tuple[str, list[dict]]]:
    """파일(여러 시트) 전체에서 같은 설비호기 → 양쪽 행에 DUPLICATE_EQUIPMENT 표시. (설비호기는 전 사업장 유일, 10/27)"""
    seen: dict[str, list[EquipmentRow]] = {}
    for res in results:
        for row in res.rows:
            if row.equipment_code:
                seen.setdefault(row.equipment_code, []).append(row)
    dups = []
    for code, lst in seen.items():
        if len(lst) > 1:
            for row in lst:
                if "DUPLICATE_EQUIPMENT" not in row.issues:
                    row.issues.append("DUPLICATE_EQUIPMENT")
            dups.append((code, [r.source for r in lst]))
    return dups


# ----------------------------------------------------------------------------- 입력 어댑터
def rows_from_openpyxl(path: str, sheet: str) -> list[list[Any]]:
    from openpyxl import load_workbook
    ws = load_workbook(path, data_only=True, read_only=True)[sheet]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def rows_from_xlwings(sheet_name: Optional[str] = None) -> list[list[Any]]:
    """사용자가 열어 둔 Excel 창에서 읽기 (Windows + Excel 필요). 병합 셀은 좌상단만 값."""
    import xlwings as xw
    book = xw.books.active
    sht = book.sheets[sheet_name] if sheet_name else book.sheets.active
    vals = sht.used_range.value
    return vals if isinstance(vals[0], list) else [vals]


def parse_workbook(path: str, site: Optional[str] = None, sheets: Optional[list[str]] = None) -> dict:
    """파일의 모든 시트를 읽고 파일 단위 중복 검사까지. 반환: {'site','results':[SheetParseResult],'duplicates':[...]}"""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    names = sheets or wb.sheetnames
    site = site or guess_site(wb.sheetnames)
    results = [parse_status_rows([list(r) for r in wb[s].iter_rows(values_only=True)], s, site) for s in names]
    dups = mark_duplicates(results)
    return {"site": site, "results": results, "duplicates": dups}
