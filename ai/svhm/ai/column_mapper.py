# -*- coding: utf-8 -*-
"""E1. AI 열 매핑 — 규칙 파서가 제목줄을 못 찾은 시트만 AI 에게 '구조'를 묻는다.

보내는 것: 시트 상단 N행을 '행번호 | 셀값 | 셀값 …' 텍스트로 (실데이터는 회사 서버에서만. GMS 엔 연습 데이터만)
받는 것(ImportProfile): doc_type(STATUS/RELEASE), orientation(vertical/horizontal), header_row, column_map{논리이름: 열번호},
                        table_count(나란한 표 수), block_label_col, notes
쓰는 법: 현황표 → 제목줄을 표준 이름으로 바꿔 끼운 뒤 규칙 파서 재호출 / 가로형 릴리즈노트 → 전치(transpose) 후 릴리즈노트 파서 재호출
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from svhm.ai.llm_client import LLMClient

STATUS_LOGICAL = ["equipment", "version", "group", "line", "no", "pm", "bay", "note"]
RELEASE_LOGICAL = ["version", "date", "no", "module", "content", "jira", "owner", "priority", "hotfix", "note"]
CANON_HEADER = {"equipment": "설비호기", "version": "Version", "group": "설비군", "line": "Line", "no": "No", "pm": "PM", "bay": "Bay", "note": "비고",
                "date": "릴리즈일", "module": "항목", "content": "Update내용", "jira": "JIRA", "owner": "담당", "priority": "우선순위", "hotfix": "Hotfix 여부"}


class ImportProfile(BaseModel):
    doc_type: Literal["STATUS", "RELEASE", "UNKNOWN"] = "UNKNOWN"
    orientation: Literal["vertical", "horizontal"] = "vertical"
    header_row: Optional[int] = None                      # 1-based
    column_map: dict[str, int] = Field(default_factory=dict)   # 논리이름 → 1-based 열 (첫 번째 표 기준)
    table_count: int = 1
    table_starts: list[int] = Field(default_factory=list)      # 나란한 표의 시작 열(1-based)
    block_label_col: Optional[int] = None
    confidence: float = 0.0
    notes: str = ""


SYSTEM_PROMPT = """너는 엑셀 시트의 '구조'만 읽어내는 분석기다. 값의 뜻을 해석하거나 요약하지 않는다. JSON 만 출력한다.

입력은 시트 상단 몇 행을 '행번호 | 열1 | 열2 | …' 로 적은 것이다. 빈 칸은 ''.
해야 할 일:
1. doc_type: 설비 현황표(설비호기·버전·설비군이 행마다 있음)면 STATUS, 릴리즈노트(버전·변경 내용 목록)면 RELEASE, 모르면 UNKNOWN
2. orientation: 세로형(제목줄 아래로 데이터)이면 vertical, 버전이 한 행에 가로로 늘어서 있으면 horizontal
3. header_row: 제목줄 행번호(1부터). 두 줄 제목이면 윗줄
4. column_map: 논리 이름 → 열번호(1부터). STATUS 는 equipment(설비호기/설비명), version(현재 SW 버전; 'Version/SW' 처럼 세부가 있으면 SW 쪽, Engine·EPD·IP 열은 아님), group(설비군/모델명), line, no, pm, bay, note 중 있는 것만.
   RELEASE 는 version, date, no, module(항목/구분), content(변경 내용), jira, owner, priority, hotfix, note 중 있는 것만. horizontal 이면 column_map 은 비우고 header_row 에 버전이 적힌 행번호를 쓴다.
5. table_count: 같은 제목이 한 행에 여러 번 반복되면 나란한 표의 수, table_starts 에 각 표의 시작 열번호
6. block_label_col: 표 왼쪽에 'P1F(3F)' 같은 블록 제목만 있는 열이 있으면 그 열번호, 없으면 null
7. confidence 0~1, notes 에 판단 근거 한 줄

출력 예:
{"doc_type":"STATUS","orientation":"vertical","header_row":2,"column_map":{"no":2,"bay":3,"group":4,"equipment":5,"pm":6,"version":7},"table_count":2,"table_starts":[2,13],"block_label_col":1,"confidence":0.9,"notes":"2행에 제목이 두 번 반복"}
"""


def rows_to_text(rows: list[list[Any]], max_rows: int = 20, max_cols: int = 30) -> str:
    lines = []
    for i, r in enumerate(rows[:max_rows], 1):
        cells = ["" if v is None else str(v).replace("\n", " ").strip()[:40] for v in r[:max_cols]]
        if any(cells):
            lines.append(f"{i} | " + " | ".join(cells))
    return "\n".join(lines)


def _mock_profile(system: str, user: str) -> dict:
    """오프라인 mock: 흔한 모양 두 가지만 흉내."""
    txt = user
    if "P1과 유사" in txt or len(txt.splitlines()) <= 3:
        return {"doc_type": "UNKNOWN", "orientation": "vertical", "header_row": None, "column_map": {}, "confidence": 0.2, "notes": "자리표시 시트"}
    for line in txt.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) > 3 and sum(1 for p in parts[1:] if p.lower().startswith("v") and any(ch.isdigit() for ch in p)) >= 3:
            return {"doc_type": "RELEASE", "orientation": "horizontal", "header_row": int(parts[0]), "column_map": {}, "confidence": 0.8, "notes": "버전이 가로로 나열"}
    return {"doc_type": "UNKNOWN", "orientation": "vertical", "header_row": None, "column_map": {}, "confidence": 0.1, "notes": "mock"}


def ask_profile(rows: list[list[Any]], client: Optional[LLMClient] = None) -> ImportProfile:
    client = client or LLMClient(mock_responder=_mock_profile)
    if client.mock_responder is None:
        client.mock_responder = _mock_profile
    return client.complete_json(SYSTEM_PROMPT, "시트 상단:\n" + rows_to_text(rows) + "\nJSON:", schema=ImportProfile)


# ----------------------------------------------------------------------------- 프로파일을 파서에 적용
def apply_status_profile(rows: list[list[Any]], profile: ImportProfile) -> list[list[Any]]:
    """AI 가 알려준 제목줄을 표준 이름(설비호기/Version/설비군…)으로 바꿔 끼워 규칙 파서가 읽게 한다."""
    if profile.header_row is None or not profile.column_map:
        return rows
    width = max(len(r) for r in rows)
    out = [list(r) + [None] * (width - len(r)) for r in rows]
    h = profile.header_row - 1
    new_header = [None] * width
    starts = profile.table_starts or [min(profile.column_map.values())]
    base = min(profile.column_map.values())
    for start in starts:
        offset = start - base
        for logical, col in profile.column_map.items():
            c = col - 1 + offset
            if 0 <= c < width:
                new_header[c] = CANON_HEADER.get(logical, logical)
    out[h] = new_header
    return out


def transpose_horizontal_release(rows: list[list[Any]], version_row_1based: int) -> list[list[Any]]:
    """가로형 릴리즈노트(버전이 열 방향) → 세로형 행 목록 [버전명, No, 항목, Update내용]. 날짜가 바로 아랫줄에 있으면 붙인다."""
    width = max(len(r) for r in rows)
    rows = [list(r) + [None] * (width - len(r)) for r in rows]
    vr = version_row_1based - 1
    out = [["릴리즈노트(가로형 변환)"], ["버전명", "No", "항목", "Update내용"]]
    for c in range(1, width):
        ver = rows[vr][c]
        if ver in (None, ""):
            continue
        date_cell = rows[vr + 1][c] if vr + 1 < len(rows) else None
        name = f"{ver} ({date_cell})" if date_cell not in (None, "") and any(ch.isdigit() for ch in str(date_cell)) else str(ver)
        n = 0
        for r in range(vr + 2, len(rows)):
            t = rows[r][c]
            if t in (None, "") or not str(t).strip():
                continue
            s = str(t).strip()
            if s.isdigit():
                continue
            n += 1
            mod = ""
            if s.startswith("[") and "]" in s:
                mod, s = s[1:s.index("]")], s[s.index("]") + 1:].strip()
            out.append([name if n == 1 else None, n, mod, s])
    return out
