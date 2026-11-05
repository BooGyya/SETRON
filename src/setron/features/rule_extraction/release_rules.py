"""세로형 릴리즈노트를 규칙으로 읽는다. 데이터 파트 ``ai/svhm/excel/release_parser.py``를 앱 안으로 옮긴 것이다.

버전과 연결된 Item마다 행을 만든다(AI 추출 규칙과 같다). 값은 셀 원문(또는 그 부분 문자열)이고 위치는 그 셀이다.
- 버전 칸 'v4.56.6.41_e, v4.56.6.41_e _DFB (2026-01-14)' → 나열된 버전마다 같은 Item 행, 버전 표기는 원문 그대로
- 날짜는 버전 칸 '(2026.03.30)' 또는 '릴리즈일' 열에서
- 같은 버전이 다른 날짜로 또 나오면(재배포) 각 블록의 날짜를 그대로 두고, 검증·저장이 첫 출시일로 합친다
- NormalC4·DFB 같은 적용 표식 열이 있으면 표식이 적힌 모델마다 행을 만들고 기호는 그대로(modification_mark)
- 모델·설비군·UI는 제목 셀에 하나만 적혀 있을 때만 그 부분 문자열로 쓴다. SW 계열은 칸이 없으므로 비운다
가로형이거나 머리글을 찾지 못하면 None 을 돌려준다(→ AI 추출로 넘김).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from setron.features.reference_data import MODEL_CATEGORIES

from .status_rules import Pos, _clean, _norm, _text

ALIASES: dict[str, tuple[str, ...]] = {
    "version": ("버전명", "버전", "version", "ver.", "ver", "swver", "sw ver", "release"),
    "date": ("릴리즈일", "릴리즈 일", "출시일", "release date", "releasedate", "date", "일자"),
    "no": ("no", "no.", "번호", "순번"),
    "module": ("항목", "구분", "모듈", "module", "part"),
    "content": ("update내용", "update 내용", "변경사항", "변경 내용", "변경내용", "내용", "description", "desc"),
    "jira": ("jira", "remark", "항목id", "항목 id", "issue", "ticket"),
}
_DATE_IN_CELL = re.compile(r"\(\s*(\d{4}[-./]\d{1,2}[-./]\d{1,2})\s*\)\s*$")
_DATE = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}")
_VERSION_LIKE = re.compile(r"^[vV]?\d+(?:\.\d+)+")
_MODEL_TOKENS = sorted(MODEL_CATEGORIES, key=len, reverse=True)
_CATEGORY_WORDS = ("KCTC", "Poly", "Oxide", "HPA")


@dataclass
class ReleaseRow:
    values: dict[str, str | None]
    positions: dict[str, Pos]
    notes: list[tuple[str, str | None, Pos | None]] = field(default_factory=list)


def _alias_of(name: Any) -> str | None:
    n = _norm(name)
    if not n:
        return None
    for logical, aliases in ALIASES.items():
        if any(n == _norm(a) for a in aliases):
            return logical
    for logical in ("content", "version", "date", "jira", "module"):
        if any(len(_norm(a)) >= 2 and _norm(a) in n for a in ALIASES[logical]):
            return logical
    return None


def find_header(rows: list[list[Any]], max_scan: int = 15):
    """(머리글 행, 열 위치, 적용 표식 열) 또는 None. 표식 열은 'NormalC4'·'DFB'·'DEX' 머리글."""
    for i, row in enumerate(rows[:max_scan]):
        logical, flags = {}, {}
        for c, value in enumerate(row):
            if value in (None, ""):
                continue
            n = _norm(value)
            if n.startswith("normal") or n in ("dfb", "dex"):
                flags[c] = _text(value).strip()
                continue
            alias = _alias_of(value)
            if alias and alias not in logical:
                logical[alias] = c
        if "version" in logical and ("content" in logical or ("no" in logical and "module" in logical)):
            return i, logical, flags
    return None


def _title(rows: list[list[Any]], header: int) -> tuple[str, Pos] | None:
    """머리글 위쪽에서 모델 이름이 들어 있는 제목 셀."""
    for r in range(0, header):
        for c, value in enumerate(rows[r]):
            text = _text(value)
            if text.strip() and _find_model(text):
                return text, (r, c)
    return None


def _find_model(text: str) -> str | None:
    """제목 글자 안에 등록 모델 이름이 단어로 하나만 있으면 그 원문 부분 문자열."""
    found, rest = [], text
    for name in _MODEL_TOKENS:
        pattern = r"(?<![\w-])" + re.escape(name) + r"(?![\w+])"
        match = re.search(pattern, rest, flags=re.IGNORECASE)
        if match:
            found.append(match.group(0))
            rest = re.sub(pattern, " ", rest, flags=re.IGNORECASE)
    return found[0] if len(found) == 1 else None


def _single(text: str, words: tuple[str, ...]) -> str | None:
    hits = [w for w in words if re.search(r"(?<![\w])" + re.escape(w) + r"(?![\w])", text, flags=re.IGNORECASE)]
    if len(hits) != 1:
        return None
    return re.search(re.escape(hits[0]), text, flags=re.IGNORECASE).group(0)


def _versions(cell_text: str) -> tuple[list[str], str | None]:
    """'v4.56.6.41_e, v4.56.6.41_e _DFB (2026-01-14)' → (['v4.56.6.41_e', 'v4.56.6.41_e _DFB'], '2026-01-14')."""
    match = _DATE_IN_CELL.search(cell_text)
    body = cell_text[:match.start()] if match else cell_text
    names = [part.strip() for part in body.split(",") if part.strip()]
    return names, (match.group(1) if match else None)


def _item_no(value: Any) -> str | None:
    text = _text(value).strip()
    return text or None


def parse_release(rows: list[list[Any]]) -> list[ReleaseRow] | None:
    """시트 값에서 버전·Item 행을 읽는다. 세로형이 아니면 None."""
    width = max((len(r) for r in rows), default=0)
    rows = [list(r) + [None] * (width - len(r)) for r in rows]
    found = find_header(rows)
    if not found:
        return None
    h, cols, flags = found
    out: list[ReleaseRow] = []
    title = _title(rows, h)
    common: dict[str, str] = {}
    common_pos: dict[str, Pos] = {}
    if title:
        text, pos = title
        for name, value in (("model_name", _find_model(text)), ("group_name", _single(text, _CATEGORY_WORDS)),
                            ("ui_type", _single(text, ("구UI", "통합UI")))):
            if value:
                common[name], common_pos[name] = value, pos
    header_notes = [] if title else [("제목 셀에서 모델명을 찾지 못했습니다. 모델명을 입력하세요.", "model_name", None)]
    current: list[str] = []
    current_pos: Pos | None = None
    current_date: tuple[str, Pos] | None = None

    def cell(row, key):
        c = cols.get(key)
        return (row[c], c) if c is not None and c < len(row) else (None, None)

    for r in range(h + 1, len(rows)):
        row = rows[r]
        if all(v in (None, "") for v in row):
            continue
        version_value, version_col = cell(row, "version")
        version_text = _text(version_value)
        if version_text.strip():
            names, date_text = _versions(version_text)
            if names and all(_VERSION_LIKE.match(n) or n.lower().startswith("v") for n in names):
                current, current_pos = names, (r, version_col)
                current_date = (date_text, (r, version_col)) if date_text else None
                date_value, date_col = cell(row, "date")
                if current_date is None and _text(date_value).strip():
                    found_date = _DATE.search(_text(date_value))
                    if found_date:
                        current_date = (found_date.group(0), (r, date_col))
        content_value, content_col = cell(row, "content")
        if not _text(content_value).strip():
            continue
        if not current:
            out.append(ReleaseRow({}, {}, [("소속 버전 없이 Item만 있는 행입니다.", "content", (r, content_col))]))
            continue
        no_value, no_col = cell(row, "no")
        date_value, date_col = cell(row, "date")
        row_date = current_date
        if _text(date_value).strip() and (found_date := _DATE.search(_text(date_value))):
            row_date = (found_date.group(0), (r, date_col))
        marks = [(c, header, _text(row[c]).strip()) for c, header in sorted(flags.items()) if _text(row[c]).strip()]
        for name in current:
            base_values = {"version_raw": name, "item_no": _item_no(no_value), "content": _text(content_value),
                           "release_date": row_date[0] if row_date else None, **common, "family_name": None}
            base_positions = {"version_raw": current_pos, "content": (r, content_col), **common_pos}
            if base_values["item_no"] is not None:
                base_positions["item_no"] = (r, no_col)
            if row_date:
                base_positions["release_date"] = row_date[1]
            if not marks:
                out.append(ReleaseRow(dict(base_values), dict(base_positions)))
                continue
            for c, header, mark in marks:
                values = {**base_values, "target_models": header, "modification_mark": mark}
                positions = {**base_positions, "target_models": (h, c), "modification_mark": (r, c)}
                out.append(ReleaseRow(values, positions))
    if out and header_notes:
        out[0].notes.extend(header_notes)
    # 재배포(같은 버전·다른 출시일) 안내는 AI 추출과 같이 검증 단계(excel_import._release_issues)가 남긴다.
    return out if any(r.values for r in out) else None
