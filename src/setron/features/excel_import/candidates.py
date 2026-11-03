"""검토 초안에 번역표 후보(:mod:`setron.features.extraction.model_candidates`)를 적용한다.

사용자가 적용을 고른 경우에만 쓴다. 적용한 값은 :func:`edit_value`로 넣어 사용자 수정과 같이 기록되고,
저장 전에 평소처럼 다시 검증된다. 제외한 행은 건드리지 않는다.
"""

from __future__ import annotations

from setron.features.extraction.model_candidates import KCTC, KCTC_NOTE, candidate_changes, find_candidate

from .schemas import ImportDraft
from .service import edit_value


def model_candidate_rows(draft: ImportDraft) -> dict[int, dict[str, str]]:
    """행 ID → 후보를 적용하면 바뀌는 항목과 값. 바뀌는 것이 없는 행은 뺀다."""
    result = {}
    for row in draft.rows:
        if row.excluded:
            continue
        current = {name: row.value(name) for name in draft.fields}
        changes = candidate_changes(current, draft.fields)
        if changes:
            result[row.row_id] = changes
    return result


def apply_model_candidates(draft: ImportDraft) -> tuple[ImportDraft, int]:
    """번역표 후보를 모든 해당 행에 적용한다. (새 초안, 바뀐 행 수)를 돌려준다."""
    rows = model_candidate_rows(draft)
    for row_id, changes in rows.items():
        for name, value in changes.items():
            draft = edit_value(draft, row_id, name, value)
    return draft, len(rows)


def candidate_notes(draft: ImportDraft) -> tuple[str, ...]:
    """후보를 쓸 행에 붙은 주의 문구(중복 없이). 화면 안내용."""
    targets = model_candidate_rows(draft)
    notes = []
    for row in draft.rows:
        candidate = find_candidate(row.value("model_name")) if row.row_id in targets else None
        if candidate and candidate.note and candidate.note not in notes:
            notes.append(candidate.note)
        if (row.row_id in targets and targets[row.row_id].get("group_name") == KCTC
                and str(row.value("group_name") or "").strip().casefold() == "oxide" and KCTC_NOTE not in notes):
            notes.append(KCTC_NOTE)
    return tuple(notes)
