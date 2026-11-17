"""정해진 양식 규칙 추출 (features.rule_extraction) — 교육용 현황표·릴리즈노트 전부를 앱 가져오기와 같은 경로로 검증한다.

경로: 원본 스냅샷(병합 포함) → SheetData → 규칙 추출 → 원본 셀 대조 → 초안(스냅샷 교체) → 시트 공통 정보
      → 번역표 후보 → 저장 전 검증 → 저장 직전 원본 검사. AI·DB 없이 돈다.
자료: ai/tests/data (데이터 파트 교육용 파일). 없으면 건너뛴다. SETRON_EDU_DATA로 위치를 바꿀 수 있다.
"""

import os
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from setron.features.ai.schemas import AIResult, AIStatus, AITask, ExtractKind
from setron.features.excel_import.candidates import GIHEUNG_NOTE, apply_model_candidates, candidate_notes, model_candidate_rows
from setron.features.excel_import.provenance import validate_save_request_sources
from setron.features.excel_import.schemas import SheetSnapshot
from setron.features.excel_import.service import build_save_request, draft_from_review, edit_value, validate
from setron.features.excel_import.source_sheet import sheet_from_snapshot
from setron.features.extraction.service import review_extraction_result
from setron.features.rule_extraction import rule_extract
from setron.infrastructure.ai_client import ResponseSource
from setron.ui.import_extraction import extract_for_review

DATA = Path(os.environ.get("SETRON_EDU_DATA", Path(__file__).resolve().parents[1] / "ai" / "tests" / "data"))
HWASEONG = "화성_설비현황표_SEMES_ETCH_교육용_2026-10-05.xlsx"
PYEONGTAEK = "평택설비현황표_교육용_2026-10-05.xlsx"


def _path(name):
    path = DATA / name
    if not path.exists():
        pytest.skip(f"교육용 자료 없음: {path}")
    return path


def _snapshot(path, sheet_name):
    ws = load_workbook(path, data_only=True)[sheet_name]
    r0, c0, r1, c1 = ws.min_row, ws.min_column, ws.max_row, ws.max_column
    cells = {f"{get_column_letter(c)}{r}": ws.cell(r, c).value for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}
    return SheetSnapshot(sheet_name, r0, c0, r1 - r0 + 1, c1 - c0 + 1, cells, tuple(str(m) for m in ws.merged_cells.ranges))


def _draft(name, sheet_name, kind, common=None):
    """가져오기 화면과 같은 순서로 검토 초안을 만들고 공통 정보·번역표 후보까지 적용한다."""
    path = _path(name)
    snapshot = _snapshot(path, sheet_name)
    sheet = sheet_from_snapshot(path.name, snapshot)
    result = rule_extract(kind, sheet)
    assert result is not None and result.source is ResponseSource.RULE
    review = review_extraction_result(kind, sheet, result)
    assert review.ok, review.message
    draft = replace(draft_from_review(review.data, source=result.source), sheet=deepcopy(snapshot))
    for row in draft.rows:
        for field, value in (common or {}).items():
            if not (row.value(field) or "").strip():
                draft = edit_value(draft, row.row_id, field, value)
    draft, _ = apply_model_candidates(draft)
    return draft


def _errors(draft):
    return [issue for issue in validate(draft, None) if issue.blocks_save]


STATUS = [(HWASEONG, "Memory", "화성", 85, 0), (HWASEONG, "Foundry", "화성", 50, 3)] + [
    (PYEONGTAEK, sheet, "평택", rows, blank)
    for sheet, rows, blank in (("P1", 77, 0), ("P2", 47, 0), ("P3", 38, 0), ("P4", 24, 0), ("S5", 18, 5), ("V2", 17, 0),
                               ("P3-S5", 15, 0))]


@pytest.mark.parametrize("name, sheet_name, site, rows, blank_versions", STATUS)
def test_status_sheets_reach_save_with_only_known_traps(name, sheet_name, site, rows, blank_versions):
    """화성 135행·평택 236행. 저장을 막는 것은 버전 칸이 빈 행뿐이다.
    화성 Foundry 메모리버전임 2·빈 칸 1, 평택 S5 HPA 5대(HPA도 SW 버전 필수 — 실제 현황표 확인 전까지 유지)."""
    draft = _draft(name, sheet_name, ExtractKind.EQUIPMENT_STATUS, {"base_date": "2026-10-05", "site": site})
    assert len(draft.rows) == rows
    errors = _errors(draft)
    assert {issue.field for issue in errors} <= {"version_raw"}
    assert len({issue.row_id for issue in errors}) == blank_versions
    if not errors:
        validate_save_request_sources(build_save_request(draft, f"rule-{sheet_name}"))


def test_mixed_pm_equipment_is_split_per_pm_and_model():
    """화성 Foundry ESON81 'O3: v4.50.1.1_a / O2: v3.53_F2' + 'PM1, 2는 O3, PM3, 4는 O2' → PM 4행."""
    draft = _draft(HWASEONG, "Foundry", ExtractKind.EQUIPMENT_STATUS, {"base_date": "2026-10-05", "site": "화성"})
    rows = [(r.value("pm_name"), r.value("model_name"), r.value("version_raw")) for r in draft.rows
            if r.value("equipment_code") == "ESON81"]
    assert rows == [("PM1", "M-O3", "v4.50.1.1_a"), ("PM2", "M-O3", "v4.50.1.1_a"),
                    ("PM3", "M-O2", "v3.53_F2"), ("PM4", "M-O2", "v3.53_F2")]


def test_empty_merged_category_does_not_inherit_the_block_above():
    """화성 Foundry A41:A57 은 값 없는 병합이다. O5 Prime 설비가 위 A4:A37 의 POLY 를 이어받지 않는다."""
    draft = _draft(HWASEONG, "Foundry", ExtractKind.EQUIPMENT_STATUS, {"base_date": "2026-10-05", "site": "화성"})
    prime = [r for r in draft.rows if r.value("model_name") == "M-O5 Prime"]
    assert len(prime) == 3 and all((r.value("group_name") or "").upper() == "OXIDE" for r in prime)


def test_hpa_rows_are_kept_with_version_cell_positions():
    """평택 S5 HPA 5대는 빠뜨리지 않고 읽으며, 빈 버전 칸의 실제 위치를 남겨 사용자가 보완할 수 있게 한다."""
    draft = _draft(PYEONGTAEK, "S5", ExtractKind.EQUIPMENT_STATUS, {"base_date": "2026-10-05", "site": "평택"})
    hpa = [r for r in draft.rows if r.value("model_name") == "HPA"]
    assert len(hpa) == 5 and all(r.sources.get("version_raw") for r in hpa)
    assert not [i for i in validate(draft, None) if i.code in {"HPA_COVERAGE_MISSING", "HPA_COVERAGE_UNRESOLVED"}]


RELEASES = [("P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx", s, s, 0) for s in ("Inline Ox", "Inline O2", "Cluster Ox", "Cluster O2")] + [
    ("P_PJT_M-O2_SW_정기_Release_List_교육용.xlsx", "Memory", "M-O2", 0),
    ("P_PJT_M-O2_Foundry_SW_Release_List_교육용.xlsx", "Foundry", "M-O2", 0),
    ("P_PJT_M-C3_SW_정기_Release_List_교육용.xlsx", "구UI", "M-C3", 0),
    ("P_PJT_M-C3_SW_정기_Release_List_교육용.xlsx", "통합UI", "M-C3", 3),
    ("P_PJT_M-C3_Foundry_SW_Release_List_교육용.xlsx", "통합UI", "M-C3", 0),
    ("P_PJT_M-C4_SW_정기_Release_List_교육용.xlsx", "Release List", "M-C4", 0),
    ("P_PJT_M-C4_Foundry_SW_Release_List_교육용.xlsx", "Release List", "M-C4", 0),
    ("P_PJT_M-O3_SW_정기_Release_List_교육용.xlsx", "Release", "M-O3", 0),
    ("P_PJT_M-O5_SW_정기_Release_List_교육용.xlsx", "Release List", "M-O5", 0),
]


@pytest.mark.parametrize("name, sheet_name, model, rereleases", RELEASES)
def test_release_sheets_reach_save(name, sheet_name, model, rereleases):
    """교육용 릴리즈노트 13개 시트가 규칙 추출로 저장 직전 검사까지 통과한다. C3 통합UI 재배포 3개는 안내로 합친다."""
    draft = _draft(name, sheet_name, ExtractKind.RELEASE_NOTE)
    assert not _errors(draft), Counter(i.message for i in _errors(draft)).most_common(3)
    validate_save_request_sources(build_save_request(draft, f"rule-{sheet_name}"))
    first = draft.rows[0]
    assert first.value("model_name") == model and first.value("family_name")
    if model == "M-C3":
        assert first.value("ui_type")       # 구UI·통합UI는 다른 릴리즈노트 — 반드시 정해진다
    assert sum(1 for i in validate(draft, None) if i.code == "RERELEASE") == rereleases


def test_c4_comma_versions_and_model_marks_are_kept_as_written():
    """C4 정기 첫 버전 칸 'v4.56.6.41_e, v4.56.6.41_e _DFB (…)' 의 두 버전과 NormalC4·DFB 표식을 원문 그대로 둔다."""
    draft = _draft("P_PJT_M-C4_SW_정기_Release_List_교육용.xlsx", "Release List", ExtractKind.RELEASE_NOTE)
    versions = {r.value("version_raw") for r in draft.rows}
    assert {"v4.56.6.41_e", "v4.56.6.41_e _DFB"} <= versions
    assert {r.value("target_models") for r in draft.rows} - {None}


def test_unknown_layouts_go_to_ai():
    """가로형 릴리즈노트와 종류가 다른 시트는 규칙 양식이 아니다 → None (AI 추출로 넘김)."""
    horizontal = _path("P_PJT_M-O2_SW_정기_Release_List_교육용_가로형_AI테스트용.xlsx")
    sheet = sheet_from_snapshot(horizontal.name, _snapshot(horizontal, load_workbook(horizontal).sheetnames[0]))
    assert rule_extract(ExtractKind.RELEASE_NOTE, sheet) is None
    status = _path(HWASEONG)
    memory = sheet_from_snapshot(status.name, _snapshot(status, "Memory"))
    assert rule_extract(ExtractKind.RELEASE_NOTE, memory) is None
    release = _path("P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx")
    assert rule_extract(ExtractKind.EQUIPMENT_STATUS,
                        sheet_from_snapshot(release.name, _snapshot(release, "Inline O2"))) is None


def test_import_uses_rules_first_and_falls_back_to_ai():
    """가져오기 추출: 정해진 양식이면 AI를 부르지 않고, 아니면 AI 추출 공급자로 넘긴다."""
    status = _path(HWASEONG)
    calls = []

    def ai(kind, sheet):
        calls.append(sheet.sheet_name)
        return AIResult(AITask.EXCEL_EXTRACT, AIStatus.INVALID_RESPONSE, "AI 호출됨")

    ruled = extract_for_review(ExtractKind.EQUIPMENT_STATUS, status.name, _snapshot(status, "Memory"),
                               sheet_extractor=ai, rules=rule_extract)
    assert ruled.ok and ruled.source is ResponseSource.RULE and not calls
    horizontal = _path("P_PJT_M-O2_SW_정기_Release_List_교육용_가로형_AI테스트용.xlsx")
    name = load_workbook(horizontal).sheetnames[0]
    fallback = extract_for_review(ExtractKind.RELEASE_NOTE, horizontal.name, _snapshot(horizontal, name),
                                  sheet_extractor=ai, rules=rule_extract)
    assert calls == [name] and not fallback.ok


def test_giheung_line_site_becomes_hwaseong_candidate():
    """AI가 'S1\n(기흥)' 라인에서 사업장을 '기흥'으로 읽으면 번역표 후보가 '화성'으로 바꾼다 (멘토 확인 2026-11-05)."""
    draft = _draft(HWASEONG, "Foundry", ExtractKind.EQUIPMENT_STATUS, {"base_date": "2026-10-05"})
    target = next(r for r in draft.rows if "기흥" in (r.value("line") or ""))

    def with_site(base, *, typed):
        rows = []
        for r in base.rows:
            if r.row_id == target.row_id:
                edits = {k: v for k, v in r.edits.items() if k != "site"}
                r = replace(r, normalized={**r.normalized, "site": "기흥"}, edits={**edits, "site": "기흥"} if typed else edits)
            rows.append(r)
        return replace(base, rows=tuple(rows))

    ai_value = with_site(draft, typed=False)                 # AI가 읽은 값
    assert ai_value.row(target.row_id).value("site") == "기흥"
    assert model_candidate_rows(ai_value)[target.row_id]["site"] == "화성"
    assert GIHEUNG_NOTE in candidate_notes(ai_value)
    typed = with_site(draft, typed=True)                     # 사람이 직접 입력한 값은 바꾸지 않는다
    assert "site" not in model_candidate_rows(typed).get(target.row_id, {})


# --- 11/08: 교육용 원본(날짜 없는 파일)의 빈 양식은 AI로 넘기지 않는다 -----------------------------------------
class _NoAI:
    """AI 추출을 부르면 실패시킨다 (빈 양식이 AI로 넘어가지 않는지 확인)."""

    def __call__(self, kind, sheet):
        raise AssertionError("빈 양식이 AI 추출로 넘어갔습니다")


def _original(name, sheet_name):
    path = _path(name)
    snapshot = _snapshot(path, sheet_name)
    return path, snapshot, sheet_from_snapshot(path.name, snapshot)


@pytest.mark.parametrize("name, sheet_name", [("평택설비현황표_교육용.xlsx", "P1"),
                                              ("화성_설비현황표_SEMES_ETCH_교육용.xlsx", "Foundry")])
def test_empty_original_status_templates_stop_without_ai(name, sheet_name):
    from setron.ui.import_extraction import extract_for_review, rule_extractor
    path, snapshot, sheet = _original(name, sheet_name)
    result = rule_extract(ExtractKind.EQUIPMENT_STATUS, sheet)
    assert result is not None and not result.ok and result.source is ResponseSource.RULE
    assert "설비호기·버전 칸이 비어" in result.message
    review = extract_for_review(ExtractKind.EQUIPMENT_STATUS, path.name, snapshot, sheet_extractor=_NoAI(),
                                rules=rule_extractor)
    assert not review.ok and review.source is ResponseSource.RULE


def test_note_only_sheet_stops_without_ai():
    from setron.ui.import_extraction import extract_for_review, rule_extractor
    path, snapshot, _ = _original("평택설비현황표_교육용.xlsx", "P2")           # 'P2F', 'P1과 유사' 메모뿐
    review = extract_for_review(ExtractKind.EQUIPMENT_STATUS, path.name, snapshot, sheet_extractor=_NoAI(),
                                rules=rule_extractor)
    assert not review.ok and "읽을 내용이 거의 없는 시트" in review.message


def test_original_memory_still_rules_and_horizontal_note_still_goes_to_ai():
    _, _, memory = _original("화성_설비현황표_SEMES_ETCH_교육용.xlsx", "Memory")
    result = rule_extract(ExtractKind.EQUIPMENT_STATUS, memory)
    assert result is not None and result.ok and len(result.data.rows) == 85
    path = _path("P_PJT_M-O2_SW_정기_Release_List_교육용_가로형_AI테스트용.xlsx")
    from openpyxl import load_workbook
    horizontal = sheet_from_snapshot(path.name, _snapshot(path, load_workbook(path, read_only=True).sheetnames[0]))
    assert rule_extract(ExtractKind.RELEASE_NOTE, horizontal) is None                 # 처음 보는 양식은 그대로 AI
