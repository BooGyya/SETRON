"""교육용 릴리즈노트 전부를 'AI가 셀 그대로 읽었다'고 가정하고 저장 직전 검사까지 통과하는지 확인한다.

추출 대조(①) → 정규화·번역표 후보(②) → 저장 전 검증(③) → 원본 대조(④)가 같은 판단을 하는지 보는
네 관문 일치 테스트다. 한 관문에만 규칙을 넣으면 여기서 깨진다. AI·DB 없이 돈다.
자료: ai/tests/data (데이터 파트 교육용 릴리즈노트). 없으면 건너뛴다. SETRON_EDU_DATA로 위치를 바꿀 수 있다.
"""

import os
import re
from collections import Counter
from pathlib import Path

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from setron.features.ai.schemas import (AIResult, AIStatus, AITask, ExtractedRow, ExtractKind, ExtractResult,
                                        ResponseSource)
from setron.features.excel_import.candidates import apply_model_candidates
from setron.features.excel_import.provenance import validate_save_request_sources
from setron.features.excel_import.schemas import SheetSnapshot
from setron.features.excel_import.service import build_save_request, draft_from_review, validate
from setron.features.excel_import.source_sheet import sheet_from_snapshot
from setron.features.extraction.service import review_extraction_result

DATA = Path(os.environ.get("SETRON_EDU_DATA", Path(__file__).resolve().parents[1] / "ai" / "tests" / "data"))
VERSION = re.compile(r"^\s*(v[^\s(]+(?:\s*\+\s*Hotfix)?(?:\s+_D(?:FB|EX))?)")
DATE = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}")

CASES = [("P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx", s, s) for s in ("Inline Ox", "Inline O2", "Cluster Ox", "Cluster O2")] + [
    ("P_PJT_M-O2_SW_정기_Release_List_교육용.xlsx", "Memory", "M-O2"),
    ("P_PJT_M-O2_Foundry_SW_Release_List_교육용.xlsx", "Foundry", "M-O2"),
    ("P_PJT_M-C3_SW_정기_Release_List_교육용.xlsx", "구UI", "M-C3"),
    ("P_PJT_M-C3_Foundry_SW_Release_List_교육용.xlsx", "통합UI", "M-C3"),
    ("P_PJT_M-C4_SW_정기_Release_List_교육용.xlsx", "Release List", "M-C4"),
    ("P_PJT_M-C4_Foundry_SW_Release_List_교육용.xlsx", "Release List", "M-C4"),
    ("P_PJT_M-O3_SW_정기_Release_List_교육용.xlsx", "Release", "M-O3"),
    ("P_PJT_M-O5_SW_정기_Release_List_교육용.xlsx", "Release List", "M-O5"),
]
# 데이터 함정: 같은 버전이 다른 출시일로 두 번 나온다(예: v3.52_13 2026-02-02 / 2026-02-06). 저장을 막아야 한다.
TRAPS = [("P_PJT_M-C3_SW_정기_Release_List_교육용.xlsx", "통합UI", "M-C3", "출시일이 행마다 다릅니다")]


def _ai_shaped_draft(path, sheet_name, model):
    """시트를 읽어 AI 응답과 같은 모양(버전·출시일·Item·내용, 모델은 제목 셀 부분 문자열, 계열 없음)의 초안을 만든다."""
    ws = load_workbook(path)[sheet_name]
    width = ws.max_column
    cells = {f"{get_column_letter(c)}{r}": ws.cell(r, c).value for r in range(1, ws.max_row + 1)
             for c in range(1, width + 1) if ws.cell(r, c).value is not None}
    header = next(r for r in range(1, 10) if any(str(ws.cell(r, c).value or "").strip() == "No" for c in range(1, width + 1)))
    names = {str(ws.cell(header, c).value or "").strip(): c for c in range(1, width + 1)}
    no_col = get_column_letter(names["No"])
    text_col = get_column_letter(next(names[k] for k in ("Update내용", "Description", "변경사항") if k in names))
    date_col = get_column_letter(names["릴리즈일"]) if "릴리즈일" in names else None
    blocks = {r: f"A{m.min_row}" for m in ws.merged_cells.ranges if m.min_col == 1 and m.min_row > header
              for r in range(m.min_row, m.max_row + 1)}
    title = next(a for a, v in cells.items() if isinstance(v, str) and model in v)
    rows, current = [], None
    for r in range(header + 1, ws.max_row + 1):
        source = blocks.get(r, f"A{r}")
        if isinstance(cells.get(source), str) and VERSION.match(cells[source]):
            current = (source, cells[source])
        if current is None or cells.get(f"{no_col}{r}") is None or cells.get(f"{text_col}{r}") is None:
            continue
        version = re.sub(r"\s*\(\d{4}[-./]\d{1,2}[-./]\d{1,2}\)\s*$", "", current[1].split(",")[0]).strip()
        values = {"version_raw": version, "item_no": str(cells[f"{no_col}{r}"]), "content": str(cells[f"{text_col}{r}"]),
                  "model_name": model, "family_name": None}
        sources = {"version_raw": current[0], "item_no": f"{no_col}{r}", "content": f"{text_col}{r}", "model_name": title}
        if (found := DATE.search(current[1])) is not None:
            values["release_date"], sources["release_date"] = found[0], current[0]
        elif date_col and cells.get(f"{date_col}{r}") is not None:
            raw_date = cells[f"{date_col}{r}"]
            values["release_date"] = raw_date if isinstance(raw_date, str) else raw_date.strftime("%Y-%m-%d")
            sources["release_date"] = f"{date_col}{r}"
        rows.append(ExtractedRow(values, sources))
    snapshot = SheetSnapshot(sheet_name, 1, 1, ws.max_row, width, cells, tuple(str(m) for m in ws.merged_cells.ranges))
    sheet = sheet_from_snapshot(path.name, snapshot)
    response = AIResult(AITask.EXCEL_EXTRACT, AIStatus.OK, "",
                        ExtractResult(ExtractKind.RELEASE_NOTE, sheet_name, tuple(rows)), ResponseSource.REAL)
    review = review_extraction_result(ExtractKind.RELEASE_NOTE, sheet, response)
    assert review.ok, review.message
    draft, _ = apply_model_candidates(draft_from_review(review.data, source=ResponseSource.REAL))
    return draft


@pytest.mark.parametrize("name, sheet_name, model", CASES)
def test_release_note_reaches_save(name, sheet_name, model):
    path = DATA / name
    if not path.exists():
        pytest.skip(f"교육용 자료 없음: {path}")
    draft = _ai_shaped_draft(path, sheet_name, model)
    errors = Counter(f"{issue.field}: {issue.message}" for issue in validate(draft, None) if issue.blocks_save)
    assert not errors, errors.most_common(3)
    validate_save_request_sources(build_save_request(draft, f"edu-{name}-{sheet_name}"))
    first = draft.rows[0]
    assert first.value("family_name") and first.value("group_name")
    if model == "M-C3":                      # C3 릴리즈노트는 UI가 반드시 정해져야 한다(구UI·통합UI는 다른 노트)
        assert first.value("ui_type") in {"LEGACY", "INTEGRATED", "구UI", "통합UI"}


@pytest.mark.parametrize("name, sheet_name, model, message", TRAPS)
def test_known_data_traps_are_blocked(name, sheet_name, model, message):
    path = DATA / name
    if not path.exists():
        pytest.skip(f"교육용 자료 없음: {path}")
    draft = _ai_shaped_draft(path, sheet_name, model)
    assert any(message in issue.message for issue in validate(draft, None) if issue.blocks_save)
