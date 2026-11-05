"""검토 초안에 번역표 후보(:mod:`setron.features.extraction.model_candidates`)를 적용한다.

사용자가 적용을 고른 경우에만 쓴다. 적용한 값은 :func:`edit_value`로 넣어 사용자 수정과 같이 기록되고,
저장 전에 평소처럼 다시 검증된다. 제외한 행은 건드리지 않는다.
"""

from __future__ import annotations

import re

from setron.features.extraction.model_candidates import KCTC, KCTC_NOTE, candidate_changes, find_candidate

from setron.features.ai.schemas import ExtractKind

from .schemas import ImportDraft
from .service import edit_value


_SHEET_UI = {"구ui": "구UI", "통합ui": "통합UI"}
SHEET_UI_NOTE = "C3 릴리즈노트의 UI가 비어 있어 시트 이름(구UI/통합UI)으로 둠. 구UI·통합UI는 다른 릴리즈노트이니 시트를 확인하세요"


def _sheet_ui(draft: ImportDraft) -> str | None:
    """릴리즈노트 시트 이름이 정확히 '구UI'·'통합UI'이면 그 UI 표기. 아니면 None."""
    if draft.kind is not ExtractKind.RELEASE_NOTE:
        return None
    return _SHEET_UI.get(draft.sheet.sheet_name.replace(" ", "").casefold())


def model_candidate_rows(draft: ImportDraft) -> dict[int, dict[str, str]]:
    """행 ID → 후보를 적용하면 바뀌는 항목과 값. 바뀌는 것이 없는 행은 뺀다."""
    result = {}
    sheet_ui = _sheet_ui(draft)
    for row in draft.rows:
        if row.excluded:
            continue
        current = {name: row.value(name) for name in draft.fields}
        changes = candidate_changes(current, draft.fields, replace_invalid=True)
        # 사용자가 직접 입력한 설비군·계열은 그대로 둔다(빈 칸 채우기와 Oxide→KCTC 보정만 예외 없음).
        for name in ("group_name", "family_name"):
            if name in changes and name in row.edits and (current.get(name) or "").strip() \
                    and not (name == "group_name" and str(current.get(name)).strip().casefold() == "oxide"):
                changes.pop(name)
        model = changes.get("model_name", current.get("model_name"))
        if draft.kind is ExtractKind.RELEASE_NOTE and "ui_type" in changes \
                and not re.search(r"구\s*UI|통합\s*UI", str(current.get("model_name") or ""), re.IGNORECASE):
            # '미표기 M-C3 = 통합UI'는 설비 현황표 기본값이다. 릴리즈노트는 모델 글자에 UI가 적힌 경우만 따른다.
            changes.pop("ui_type")
        if (sheet_ui and "ui_type" in draft.fields and not (current.get("ui_type") or "").strip()
                and "ui_type" not in changes and (model or "").strip() in {"M-C3", "C3"}):
            changes["ui_type"] = sheet_ui
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
        if row.row_id in targets and "ui_type" in targets[row.row_id] and _sheet_ui(draft) and SHEET_UI_NOTE not in notes:
            notes.append(SHEET_UI_NOTE)
        if (row.row_id in targets and targets[row.row_id].get("group_name") == KCTC
                and str(row.value("group_name") or "").strip().casefold() == "oxide" and KCTC_NOTE not in notes):
            notes.append(KCTC_NOTE)
    return tuple(notes)
