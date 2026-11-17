"""데이터 가져오기 검토 표에서 여러 칸을 한 번에 바꾼다 (최종 멘토링 11-16: '설비명을 한 번에 바꿀 수 있나').

- :func:`apply_edits`: (행 ID, 항목, 새 값) 목록을 한 번에 반영한다. 한 칸씩 고칠 때(:func:`edit_value`)와 같은 규칙.
- :func:`replace_plan`: 한 항목에서 찾을 글자를 바꿀 글자로 바꾸는 변경 목록 (화면 이름: 일괄 변경).
- :func:`paste_plan`: Excel에서 복사한 표 글자를 고른 칸부터 채우는 변경 목록 (붙여넣기).

원본 추출값은 그대로 두고 수정값만 따로 기록한다. 제외한 행은 바꾸지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .schemas import ImportDraft
from .service import _replace_row, edited_row

Change = tuple[int, str, str]   # (추출 행 ID, 항목 이름, 새 값). 빈 글자는 값을 비운다.


def apply_edits(draft: ImportDraft, changes: Iterable[Change]) -> tuple[ImportDraft, int]:
    """여러 칸의 수정값을 한 번에 기록한다.

    param
    - draft: 현재 검토 초안.
    - changes: (행 ID, 항목, 새 값). 같은 칸이 여러 번 나오면 마지막 값.

    return
    - (새 초안, 실제로 값이 바뀐 칸 수). 제외한 행 · 없는 항목 · 같은 값은 세지 않는다.
    """
    from .review_locations import refresh_hpa_coverage

    latest = {(row_id, name): text for row_id, name, text in changes}
    changed = 0
    for (row_id, name), text in latest.items():
        if name not in draft.fields:
            continue
        try:
            row = draft.row(row_id)
        except LookupError:
            continue
        if row.excluded:
            continue
        new = edited_row(draft, row, name, text)
        if new.value(name) == row.value(name) and new.edits == row.edits:
            continue
        draft = _replace_row(draft, new)
        changed += 1
    return (refresh_hpa_coverage(draft) if changed else draft), changed


def replace_plan(draft: ImportDraft, field: str, find: str, replacement: str, *, whole_cell: bool = False,
                 row_ids: Sequence[int] | None = None) -> list[Change]:
    """한 항목에서 찾을 글자를 바꿀 글자로 바꾸는 변경 목록을 만든다.

    param
    - field: 바꿀 항목 (예: 설비 호기, 모델명).
    - find: 찾을 글자. 대소문자를 구분한다. 비어 있으면 빈 칸을 찾는다(칸 전체 일치일 때만).
    - replacement: 바꿀 글자. 빈 글자면 찾은 부분을 지운다.
    - whole_cell: True면 칸 전체가 찾을 글자와 같을 때만 바꾼다. False면 칸 안의 일부 글자를 모두 바꾼다.
    - row_ids: 이 행들에서만. None이면 제외하지 않은 모든 행.

    return
    - 실제로 값이 달라지는 칸의 변경 목록.
    """
    if field not in draft.fields:
        return []
    wanted = None if row_ids is None else set(row_ids)
    plan = []
    for row in draft.rows:
        if row.excluded or wanted is not None and row.row_id not in wanted:
            continue
        current = row.value(field)
        text = "" if current is None else str(current)
        if whole_cell:
            if text.strip() != find.strip():
                continue
            new = replacement
        else:
            if not find or find not in text:
                continue
            new = text.replace(find, replacement)
        if new != text:
            plan.append((row.row_id, field, new))
    return plan


def parse_clipboard(text: str) -> list[list[str]]:
    """Excel · 표에서 복사한 글자를 줄(\\n)과 칸(\\t)으로 나눈다. 끝의 빈 줄은 버린다."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    return [line.split("\t") for line in lines]


def paste_plan(grid: list[list[str]], start_row: int, start_column: int, row_ids: Sequence[int],
               columns: Sequence[str | None], selected: Iterable[tuple[int, int]] = ()) -> list[Change]:
    """복사한 표 글자를 검토 표 칸에 채우는 변경 목록을 만든다.

    param
    - grid: :func:`parse_clipboard` 결과.
    - start_row, start_column: 붙여넣기를 시작할 표 위치(고른 칸의 왼쪽 위).
    - row_ids: 표 줄 순서의 추출 행 ID.
    - columns: 표 열 순서의 항목 이름. 고칠 수 없는 열(행 번호 · 상태 등)은 None.
    - selected: 고른 칸 (줄, 열). 값이 하나뿐이고 여러 칸을 골랐으면 고른 칸 모두에 같은 값.

    return
    - (행 ID, 항목, 새 값) 목록. 표 밖 · 고칠 수 없는 열은 건너뛴다.
    """
    selected = list(selected)
    if len(grid) == 1 and len(grid[0]) == 1 and len(selected) > 1:
        value = grid[0][0]
        return [(row_ids[r], columns[c], value) for r, c in selected
                if 0 <= r < len(row_ids) and 0 <= c < len(columns) and columns[c]]
    plan = []
    for dr, values in enumerate(grid):
        r = start_row + dr
        if not 0 <= r < len(row_ids):
            break
        for dc, value in enumerate(values):
            c = start_column + dc
            if 0 <= c < len(columns) and columns[c]:
                plan.append((row_ids[r], columns[c], value))
    return plan
