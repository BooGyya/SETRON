"""설비 현황표(출석부)를 규칙으로 읽는다. 데이터 파트 ``ai/svhm/excel/status_parser.py``를 앱 안으로 옮긴 것이다.

AI 추출과 같은 결과 모양(항목 값 + 값을 읽은 셀 위치)을 만들어 같은 원본 대조·검토·저장 경로로 보낸다.
값은 셀 원문을 그대로 쓰고(공백 정리 없음), 위치는 값이 실제로 적힌 칸(병합이면 왼쪽 위 칸)이다.
위에서 내려 채운 라인·설비군도 그 값이 처음 적힌 칸을 위치로 남긴다.

처리하는 양식 (골든테스트 A1~A20)
- 한 행에 표가 옆으로 2~3개 (평택 Poly | Oxide | HPA) → 세그먼트 분할, 표 제목을 설비군 분류로
- 두 줄 머리글 (화성 'Version' ▸ 'SW' / 'Engine') → 'Version/SW' 복합 이름
- 시트마다 열이 밀림 (화성 Foundry 는 'No.' 없음) → 머리글 이름으로 열 찾기
- 세로 병합 (화성 Line·설비군, 평택 블록 제목) → 내려 채우기
- 블록 제목 'P1F(3F)\\n(61)' 과 블록마다 반복되는 머리글 → 블록 단위 처리 + 선언 대수 비교
- 버전 칸 글자('메모리버전임'), 빈 칸, 혼합형('O3: … / O2: …' + 'PM1, 2는 O3, PM3, 4는 O2')
- 설비호기 꼬리말 'ESJOF06 (JEP)', 같은 설비호기 두 번

머리글을 찾지 못하면 None 을 돌려준다(→ AI 추출로 넘김).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

Pos = tuple[int, int]          # values 의 0부터 시작하는 (행, 열)

ALIASES: dict[str, tuple[str, ...]] = {
    "equipment": ("설비호기", "설비명", "호기", "장비명", "equipment", "eqp", "설비id", "설비번호"),
    "version": ("version", "ver", "버전", "swver", "sw버전", "현재swver", "현재버전", "sw"),
    "group": ("설비군(상세)", "설비군", "group", "model", "기종", "모델"),
    "line": ("line", "라인", "fab", "공장"),
    "no": ("no", "no.", "번호", "순번"),
    "pm": ("pm",),
    "bay": ("bay",),
    "note": ("비고", "note", "remark", "메모"),
}
SUBHEADER_WORDS = {"sw", "engine", "flow", "bay", "version", "ip", "vtr개조", "wdt"}
EQUIPMENT_CODE_RE = re.compile(r"^(?P<code>[A-Za-z][A-Za-z0-9\-]{2,})\s*(?P<tail>\(.*\))?\s*$")
BLOCK_COUNT_RE = re.compile(r"\(\s*(\d+)\s*\)")
MULTI_VERSION_RE = re.compile(
    r"([A-Za-z0-9+\-]+)\s*[:：]\s*([vV]?\d[\w.\-+ ]*?)(?=\s*/\s*[A-Za-z0-9+\-]+\s*[:：]|\s*$)")
PM_SPEC_RE = re.compile(r"PM\s*([1-9][0-9]*(?:\s*,\s*[1-9][0-9]*)*)\s*(?:는|은|:|=)?\s*(O[235]|C[34])", re.I)
PM_VALUE_RE = re.compile(r"^\s*PM\s*[1-9][0-9]*\s*$", re.I)


def _text(value: Any) -> str:
    """셀 값을 원본 대조와 같은 방식으로 글자로 바꾼다 (extraction.service._text 와 같다)."""
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value)).lower()


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).strip()


def _looks_like_equipment(value: Any) -> bool:
    text = _clean(value)
    return bool(text) and bool(EQUIPMENT_CODE_RE.match(text)) and any(ch.isdigit() for ch in text)


@dataclass
class StatusRow:
    """추출한 설비 행 하나. ``values`` 는 항목별 셀 원문(또는 그 부분 문자열), ``positions`` 는 항목별 셀 위치."""

    values: dict[str, str | None]
    positions: dict[str, Pos]
    notes: list[tuple[str, str | None, Pos | None]] = field(default_factory=list)


@dataclass
class _Segment:
    index: int
    title: tuple[str, Pos] | None
    start: int
    end: int
    cols: dict[str, int]
    names: dict[int, str]


# --- 머리글 -------------------------------------------------------------------------------
def _alias_of(name: str) -> str | None:
    n = _norm(name)
    if not n:
        return None
    for logical in ("equipment", "group", "line", "no", "pm", "bay", "note", "version"):
        if any(n == _norm(a) for a in ALIASES[logical]):
            return logical
    for logical in ("equipment", "group", "line", "version"):
        if any(len(_norm(a)) >= 2 and _norm(a) in n for a in ALIASES[logical]):
            return logical
    return None


def _is_subheader_row(row: list[Any]) -> bool:
    values = [_norm(v) for v in row if v not in (None, "")]
    if not values or any(_looks_like_equipment(v) for v in row):
        return False
    if any(_alias_of(_text(v)) == "equipment" for v in row if v not in (None, "")):
        return False
    return sum(1 for v in values if v in SUBHEADER_WORDS) >= 2


def _compound_header(top: list[Any], sub: list[Any] | None) -> dict[int, str]:
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
    if n.startswith(("version", "버전", "현재")):
        score += 10
    return score


def find_header_rows(rows: list[list[Any]]) -> list[tuple[int, int, dict[int, str]]]:
    """(머리글 행, 자료 시작 행, 열 이름) 목록. 두 줄 머리글이면 자료 시작은 +2."""
    out, i = [], 0
    while i < len(rows):
        sub = rows[i + 1] if i + 1 < len(rows) and _is_subheader_row(rows[i + 1]) else None
        names = {c: n for c, n in _compound_header(rows[i], sub).items() if len(n) <= 25}
        logicals = {_alias_of(v) for v in names.values()} - {None}
        if "equipment" in logicals and ("version" in logicals or "group" in logicals):
            out.append((i, i + (2 if sub else 1), names))
            i += 2 if sub else 1
            continue
        i += 1
    return out


def _segments(names: dict[int, str], title_row: list[Any] | None, title_index: int | None) -> list[_Segment]:
    cols_sorted = sorted(names)
    eq_cols = [c for c in cols_sorted if _alias_of(names[c]) == "equipment"]
    if not eq_cols:
        return []
    no_cols = [c for c in cols_sorted if _alias_of(names[c]) == "no"]
    if len(eq_cols) == 1:
        starts = [cols_sorted[0]]
    elif len(no_cols) == len(eq_cols):
        starts = no_cols
    else:
        starts = [min(max((c for c in cols_sorted if c < e and _alias_of(names[c]) == "group"), default=e), e)
                  for e in eq_cols]
    segments = []
    for k, start in enumerate(starts):
        end = (starts[k + 1] - 1) if k + 1 < len(starts) else max(cols_sorted)
        seg_names = {c: n for c, n in names.items() if start <= c <= end}
        cols: dict[str, int] = {}
        candidates = [(c, _version_score(n)) for c, n in seg_names.items() if _version_score(n) > -100]
        if candidates:
            cols["version"] = max(candidates, key=lambda x: (x[1], -x[0]))[0]
        for logical in ("equipment", "line", "no", "pm", "bay", "note"):
            for c, n in sorted(seg_names.items()):
                if _alias_of(n) == logical and logical not in cols:
                    cols[logical] = c
        for c, n in sorted(seg_names.items()):
            key = _norm(n)
            if "engine" in key and "engine" not in cols:
                cols["engine"] = c
            if "epd" in key and "ip" not in key.replace("epd", "") and "epd" not in cols:
                cols["epd"] = c
        group_cols = [c for c, n in sorted(seg_names.items()) if _alias_of(n) == "group"]
        detail = [c for c in group_cols if "상세" in _norm(seg_names[c])]
        if detail:
            cols["group"] = detail[0]
            others = [c for c in group_cols if c != detail[0]]
            if others:
                cols["category"] = others[0]
        elif group_cols:
            cols["group"] = group_cols[0]
            if len(group_cols) > 1:
                cols["category"], cols["group"] = group_cols[0], group_cols[1]
        title = None
        if title_row is not None and title_index is not None:
            for c in range(start, end + 1):
                if c < len(title_row) and _clean(title_row[c]) and not _looks_like_equipment(title_row[c]):
                    title = (_text(title_row[c]), (title_index, c))
                    break
        segments.append(_Segment(k, title, start, end, cols, seg_names))
    return segments


# --- 보조 ---------------------------------------------------------------------------------
def _block_line(text: str) -> str | None:
    """'P1F(3F)\\n(61)' → 'P1F' (셀 원문의 부분 문자열)."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9\-]*)", first)
    return match.group(1) if match else None


def _pm_map(text: str) -> dict[str, list[str]]:
    """'PM1, 2는 O3, PM3, 4는 O2' → {'O3': ['PM1', '2'], 'O2': ['PM3', '4']} (원문 부분 문자열)."""
    result: dict[str, list[str]] = {}
    for match in PM_SPEC_RE.finditer(text):
        numbers = re.findall(r"[1-9][0-9]*", match.group(1))
        result.setdefault(match.group(2).upper(), []).extend(
            [f"PM{numbers[0]}" if f"PM{numbers[0]}" in text else numbers[0]] + numbers[1:])
    return result


# --- 본체 ---------------------------------------------------------------------------------
def parse_status(rows: list[list[Any]], merged: list[tuple[int, int, int, int]] = ()) -> list[StatusRow] | None:
    """시트 값(병합은 왼쪽 위 칸에만 값)에서 설비 행을 읽는다. 아는 양식이 아니면 None.

    param
    - rows: 0부터 시작하는 시트 값 표.
    - merged: 같은 좌표계의 병합 범위 (위, 왼쪽, 아래, 오른쪽). 내려 채우기는 병합 범위 안에서만 한다.
    """
    def merged_top(r: int, c: int) -> Pos | None:
        for top, left, bottom, right in merged:
            if top <= r <= bottom and left <= c <= right:
                return top, left
        return None
    width = max((len(r) for r in rows), default=0)
    rows = [list(r) + [None] * (width - len(r)) for r in rows]
    headers = find_header_rows(rows)
    if not headers:
        return None
    out: list[StatusRow] = []
    for hi, (h, data_start, names) in enumerate(headers):
        next_h = headers[hi + 1][0] if hi + 1 < len(headers) else len(rows)
        title_index = h - 1 if h >= 1 else None
        segments = _segments(names, rows[title_index] if title_index is not None else None, title_index)
        if not segments:
            continue
        seg_start = min(s.start for s in segments)
        label: tuple[str, Pos] | None = None
        for c in (c for c in range(seg_start) if c not in names):
            for r in range(h, max(-1, h - 3), -1):
                if r < len(rows) and _clean(rows[r][c]):
                    label = (_text(rows[r][c]), (r, c))
                    break
            if label:
                break
        declared = None
        if label:
            count = BLOCK_COUNT_RE.search(label[0])
            declared = int(count.group(1)) if count else None
        carry: dict[tuple[int, str], tuple[Any, Pos]] = {}
        extracted = 0
        for r in range(data_start, next_h):
            row = rows[r]
            if all(v in (None, "") for v in row):
                continue
            for seg in segments:
                def at(key):
                    c = seg.cols.get(key)
                    return (row[c], (r, c)) if c is not None and c < len(row) else (None, None)
                for key in ("category", "line"):
                    value, pos = at(key)
                    top = merged_top(*pos) if pos is not None else None
                    if top is not None:
                        # 병합 칸이면 그 병합 범위의 값만 쓴다. 비어 있는 병합(예: 화성 Foundry A41:A57)은 위 묶음 값을 끌고 오지 않는다.
                        value = rows[top[0]][top[1]]
                        if value in (None, ""):
                            carry.pop((seg.index, key), None)
                        else:
                            carry[(seg.index, key)] = (value, top)
                    elif value not in (None, ""):
                        carry[(seg.index, key)] = (value, pos)
                eq_value, eq_pos = at("equipment")
                group_value, group_pos = at("group")
                version_value, version_pos = at("version")
                eq_text = _text(eq_value)
                if not eq_text.strip():
                    continue          # 설비호기 없는 행(메모·빈 칸·버전만 적힌 칸)은 설비로 세지 않는다
                if not _looks_like_equipment(eq_value):
                    continue
                values: dict[str, str | None] = {"equipment_code": eq_text, "model_name": None, "group_name": None,
                                                 "line": None, "version_raw": None}
                positions: dict[str, Pos] = {"equipment_code": eq_pos}
                notes: list[tuple[str, str | None, Pos | None]] = []
                tail = EQUIPMENT_CODE_RE.match(_clean(eq_value))
                if tail and tail.group("tail"):
                    notes.append((f"설비호기 뒤에 꼬리말 {tail.group('tail')}이 있습니다. 호기만 남길지 확인하세요.",
                                  "equipment_code", eq_pos))
                if _text(group_value).strip():
                    values["model_name"], positions["model_name"] = _text(group_value).strip(), group_pos
                if (carried := carry.get((seg.index, "line"))) is not None:
                    values["line"], positions["line"] = _text(carried[0]).strip(), carried[1]
                elif label and (line := _block_line(label[0])):
                    values["line"], positions["line"] = line, label[1]
                if (carried := carry.get((seg.index, "category"))) is not None:
                    values["group_name"], positions["group_name"] = _text(carried[0]).strip(), carried[1]
                elif seg.title is not None:
                    values["group_name"], positions["group_name"] = seg.title[0].strip(), seg.title[1]
                for key, name in (("engine", "engine_version"), ("epd", "epd_version")):
                    value, pos = at(key)
                    if _text(value).strip():
                        values[name], positions[name] = _text(value), pos
                pm_value, pm_pos = at("pm")
                if PM_VALUE_RE.match(_text(pm_value)):
                    values["pm_name"], positions["pm_name"] = _text(pm_value).strip(), pm_pos
                version_text = _text(version_value)
                is_hpa = "hpa" in _norm(values["model_name"]) or "hpa" in _norm(values["group_name"])
                pairs = MULTI_VERSION_RE.findall(_clean(version_text))
                if version_pos is not None:
                    positions["version_raw"] = version_pos
                if not version_text.strip():
                    if not is_hpa:
                        notes.append(("버전 칸이 비어 있습니다.", "version_raw", version_pos))
                    out.append(StatusRow(values, positions, notes))
                elif not any(ch.isdigit() for ch in version_text):
                    notes.append((f"버전 칸에 버전 대신 '{version_text.strip()}'이(가) 적혀 있습니다. 실제 버전을 확인하세요.",
                                  "version_raw", version_pos))
                    out.append(StatusRow(values, positions, notes))
                elif len(pairs) >= 2:
                    out.extend(_mixed_rows(row, seg, r, values, positions, notes, pairs))
                else:
                    values["version_raw"] = version_text
                    out.append(StatusRow(values, positions, notes))
                extracted += 1
        if declared is not None and declared != extracted and label:
            out.append(StatusRow({}, {}, [(f"블록 '{_clean(label[0])}'의 선언 대수 {declared}대와 읽은 설비 {extracted}대가 다릅니다.",
                                           None, label[1])]))
    _mark_duplicates(out)
    return out


def _mixed_rows(row, seg, r, values, positions, notes, pairs) -> list[StatusRow]:
    """혼합형 'O3: v… / O2: v…' 를 모델별(PM 목록이 있으면 PM별) 행으로 나눈다. 모델·버전은 원문 부분 문자열."""
    spec_text, spec_pos = None, None
    for c in range(seg.start, min(seg.end, len(row) - 1) + 1):
        text = _text(row[c])
        if PM_SPEC_RE.search(text):
            spec_text, spec_pos = text, (r, c)
            break
    pm_by_model = _pm_map(spec_text) if spec_text else {}
    out = []
    for label, version in pairs:
        model = label.strip().upper()
        for pm in pm_by_model.get(model) or [None]:
            v, p = dict(values), dict(positions)
            v["model_name"] = model
            v["version_raw"] = version.strip()
            if pm is not None:
                v["pm_name"], p["pm_name"] = pm, spec_pos
            out.append(StatusRow(v, p, [] if out else list(notes)))
    if not pm_by_model:
        out[0].notes.append(("한 칸에 버전이 두 개인 혼합형 설비입니다. PM별 구성을 확인하세요.", "version_raw",
                             positions.get("version_raw")))
    return out


def _mark_duplicates(rows: list[StatusRow]) -> None:
    """같은 시트에서 같은 설비호기·PM 이 두 번 나오면 양쪽에 안내를 남긴다 (설비호기는 전 사업장 유일, 10/27)."""
    seen: dict[tuple[str, str | None], list[StatusRow]] = {}
    for row in rows:
        code = (row.values.get("equipment_code") or "").strip()
        if code:
            seen.setdefault((code, row.values.get("pm_name")), []).append(row)
    for (code, _), members in seen.items():
        if len(members) > 1:
            for row in members:
                row.notes.append((f"같은 설비호기 {code}가 시트에 {len(members)}번 나옵니다.", "equipment_code",
                                  row.positions.get("equipment_code")))
