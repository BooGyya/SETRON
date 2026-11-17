"""릴리즈노트 나열 순서로 버전 순서(`version_order`) 후보를 만든다 (데이터 파트 연동).

출처: 데이터 파트 `ai/svhm/excel/release_parser.py`는 릴리즈노트에 버전이 나열된 순서대로 10, 20, 30 …의 순서를
붙이고, 날짜가 앞 버전보다 빠르면 경고한다. 여기서는 같은 규칙을 저장된 릴리즈의 원본 위치(`version_raw` 셀의 행 번호)로
적용한다.

후보로만 쓴다 (팀 결정 2026-11-03).
- 결과는 관리 화면의 수정 목록에 들어가고, 사용자가 묶음을 검토해 저장해야 반영된다.
- 이미 순서가 확인된 릴리즈가 하나라도 있는 계열은 후보를 만들지 않는다 (확인된 순서와 섞지 않음).
- 모든 릴리즈가 같은 입력 작업·같은 시트의 버전 셀 위치를 가져야 한다. 하나라도 없으면 만들지 않는다.
- 같은 행에서 나온 버전(예: 일반·_DFB가 한 셀)은 나열된 셀 주소·원문 순서로 이어서 매긴다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

STEP = 10


@dataclass(frozen=True)
class OrderCandidate:
    release_version_id: int
    version_raw: str
    version_order: int
    row: int


@dataclass(frozen=True)
class OrderSuggestion:
    """계열 하나의 순서 후보. ``reason``이 있으면 후보를 만들지 않은 이유다."""

    family_id: int
    candidates: tuple[OrderCandidate, ...] = ()
    warnings: tuple[str, ...] = ()
    reason: str | None = None


def suggest_version_orders(family_id: int, releases: Iterable[Mapping[str, Any]]) -> OrderSuggestion:
    """저장된 릴리즈 행(`release_version` 현재값)으로 순서 후보를 만든다."""
    rows = [r for r in releases if r.get("family_id") == family_id]
    if not rows:
        return OrderSuggestion(family_id, reason="이 계열에 저장된 릴리즈가 없습니다.")
    if any(r.get("version_order") is not None for r in rows):
        return OrderSuggestion(family_id, reason="이미 순서가 확인된 릴리즈가 있어 후보를 만들지 않습니다. 관리 화면에서 직접 확인해 주세요.")
    located = []
    for r in rows:
        place = _position(r)
        if place is None:
            return OrderSuggestion(family_id, reason=f"{r['version_raw']}: 원본 버전 셀 위치가 없어 나열 순서를 알 수 없습니다.")
        located.append((place, r))
    sources = {(place[0], place[1]) for place, _ in located}
    if len(sources) > 1:
        return OrderSuggestion(family_id, reason="릴리즈가 여러 입력 작업·시트에서 왔습니다. 한 릴리즈노트 안에서만 순서를 정할 수 있습니다.")
    located.sort(key=lambda item: item[0][2:])
    candidates, warnings, last = [], [], None
    for index, ((_, _, row, _), r) in enumerate(located, start=1):
        candidates.append(OrderCandidate(r["release_version_id"], r["version_raw"], index * STEP, row))
        released = r.get("released_at")
        if isinstance(released, date):
            if last is not None and released < last[1]:
                warnings.append(f"{r['version_raw']}: 출시일 {released}이 앞 버전 {last[0]}({last[1]})보다 빠릅니다. 나열 순서를 확인해 주세요.")
            last = (r["version_raw"], released)
    return OrderSuggestion(family_id, tuple(candidates), tuple(warnings))


def _position(row: Mapping[str, Any]) -> tuple | None:
    """(입력 작업, 시트, 행 번호, 열 번호) — 버전 셀이 여러 개면 가장 앞선 셀."""
    reference = (row.get("source_reference") or {}).get("version_raw")
    locations = reference if isinstance(reference, list) else [reference] if reference else []
    best = None
    for location in locations:
        if not isinstance(location, Mapping):
            continue
        for cell in location.get("cells") or ():
            match = re.fullmatch(r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)", str(cell).strip())
            if not match:
                continue
            column = 0
            for letter in match[1].upper():
                column = column * 26 + ord(letter) - ord("A") + 1
            place = (location.get("batch_id", row.get("batch_id")), location.get("sheet"), int(match[2]), column)
            if best is None or place[2:] < best[2:]:
                best = place
    return best
