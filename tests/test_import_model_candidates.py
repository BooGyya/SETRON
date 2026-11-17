"""데이터 파트 번역표 후보를 가져오기 검토에 적용하는 흐름을 검증한다."""

from setron.features.excel_import.candidates import apply_model_candidates, candidate_notes, model_candidate_rows
from setron.features.excel_import.service import set_excluded
from setron.features.extraction.model_candidates import CANDIDATES, candidate_changes, find_candidate
from test_excel_import_service import draft_for, eq_row, sheet

# 시드 v2 (database/seed_demo_catalog.sql): 멘토 확정 분류표의 14개 모델
SEED_MODELS = {"M-C3", "M-C4", "M-C4 DFB", "M-C4+", "M-O2", "M-O3", "XMO3", "M-O5", "M-O5 Prime",
               "Inline Ox", "Inline O2", "Cluster Ox", "Cluster O2", "HPA"}


def test_every_candidate_points_to_a_seed_model():
    """후보 모델은 모두 시연용 시드(database/seed_demo_catalog.sql)에 있는 이름이다."""
    assert {c.model_name for c in CANDIDATES} <= SEED_MODELS


def test_raw_names_match_ignoring_spaces_and_case():
    candidate = find_candidate("m-c3(6각)(통합ui)")
    assert (candidate.model_name, candidate.ui_type, candidate.family_name) == ("M-C3", "통합UI", "C3")
    assert find_candidate("M-C3 (구UI)").ui_type == "구UI"
    assert find_candidate("Ox(inline)").model_name == "Inline Ox"  # KCTC 4개 분리 (멘토 확정 2026-10-29)
    assert find_candidate("M-C4(DFB)").model_name == "M-C4 DFB" and find_candidate("XMO3").model_name == "XMO3"
    assert find_candidate(None) is None and find_candidate(" ") is None


def test_family_only_c4_label_maps_to_registered_model_with_note():
    """릴리즈노트 모델 칸의 'C4'처럼 계열 이름만 적힌 표기도 M-C4 후보를 내고 주의 문구를 붙인다."""
    candidate = find_candidate("c4")
    assert (candidate.model_name, candidate.family_name, candidate.group_name) == ("M-C4", "C4", "Poly")
    assert "계열 이름만" in candidate.note
    assert find_candidate("C4 Plus") is None  # 파생형 표기는 그대로 둔다


def test_only_model_name_is_replaced_and_other_values_fill_blanks():
    fields = ("model_name", "group_name", "family_name", "ui_type")
    changes = candidate_changes({"model_name": "M-C3 (6각) (통합UI)", "group_name": None,
                                 "family_name": "C3 직접입력", "ui_type": ""}, fields)

    assert changes == {"model_name": "M-C3", "group_name": "Poly", "ui_type": "통합UI"}
    assert candidate_changes({"model_name": "M-C4", "group_name": "Poly", "family_name": "C4"}, fields) == {}


def test_apply_records_user_edits_and_skips_excluded_rows():
    data = sheet(["2026-10-21", "화성", "E1", "PM1", "M-C3 (6각) (통합UI)", None, "v3", None],
                 ["2026-10-21", "화성", "E2", "PM1", "M-C3(6각) / 구UI", None, "v1", None],
                 ["2026-10-21", "화성", "E3", "PM1", "M-C4", "C4", "v4", None])
    draft = draft_for(data, [
        eq_row(2, equipment_code="E1", model_name="M-C3 (6각) (통합UI)", family_name=None, version_raw="v3"),
        eq_row(3, equipment_code="E2", model_name="M-C3(6각) / 구UI", family_name=None, version_raw="v1"),
        eq_row(4, equipment_code="E3", version_raw="v4"),
    ])
    draft = set_excluded(draft, 2, True)

    candidates = model_candidate_rows(draft)
    assert set(candidates) == {1, 3}
    assert candidates[3] == {"group_name": "Poly"}  # 등록 모델명이어도 빈 설비군은 채운다
    applied, count = apply_model_candidates(draft)

    row = applied.row(1)
    assert count == 2
    assert (row.value("model_name"), row.value("family_name")) == ("M-C3", "C3")
    assert row.original["model_name"] == "M-C3 (6각) (통합UI)"  # 원문은 그대로 남는다
    assert {"model_name", "family_name"} <= set(row.edits)
    assert applied.row(2).value("model_name") == "M-C3(6각) / 구UI"
    assert model_candidate_rows(applied) == {}


def test_notes_explain_uncertain_candidates():
    data = sheet(["2026-10-21", "화성", "E1", "PM1", "M-C3", None, "v3", None])
    draft = draft_for(data, [eq_row(2, equipment_code="E1", model_name="M-C3", family_name=None, version_raw="v3")])

    assert any("통합UI" in note for note in candidate_notes(draft))


def test_mixed_c4_label_is_resolved_by_version_suffix():
    """'M-C4/C4+' 표기는 같은 행의 SW 버전에 _DEX 가 있을 때만 C4+ (멘토 확정 2026-10-29)."""
    fields = ("model_name", "group_name", "family_name", "modification_type")
    plus = candidate_changes({"model_name": "M-C4/C4+ / Smart UI", "version_raw": "v4.56.8.1_f1_DEX"}, fields)
    base = candidate_changes({"model_name": "M-C4/C4+ / Smart UI", "version_raw": "v4.56.7.1_f1"}, fields)
    assert plus["model_name"] == "M-C4+" and plus["modification_type"] == "C4+"
    assert base["model_name"] == "M-C4" and "modification_type" not in base


def test_kctc_model_listed_under_oxide_becomes_kctc():
    """현황표는 KCTC 설비를 Oxide 묶음 아래 적는다. KCTC 모델이면 설비군을 KCTC로 바꾼다 (다른 모델의 Oxide는 유지)."""
    fields = ("model_name", "group_name", "family_name", "structure_type")
    kctc = candidate_changes({"model_name": "Inline Ox (CIP)", "group_name": "Oxide"}, fields)
    assert kctc["model_name"] == "Inline Ox" and kctc["group_name"] == "KCTC" and kctc["structure_type"] == "INLINE"
    assert "group_name" not in candidate_changes({"model_name": "M-O2", "group_name": "Oxide"}, fields)


def test_category_word_in_family_and_non_category_group_are_replaced():
    """릴리즈노트 제목 'KCTC Inline O2 …'에서 계열 칸에 KCTC, 설비군 칸에 항목명(Engine)이 들어온 경우를 고친다."""
    fields = ("model_name", "group_name", "family_name", "structure_type")
    changes = candidate_changes({"model_name": "Inline O2", "group_name": "Engine", "family_name": "KCTC"}, fields,
                                replace_invalid=True)
    assert changes["family_name"] == "Inline O2" and changes["group_name"] == "KCTC" and changes["structure_type"] == "INLINE"
    # 정상 값은 건드리지 않는다.
    assert candidate_changes({"model_name": "Inline O2", "group_name": "KCTC", "family_name": "Inline O2",
                              "structure_type": "INLINE"}, fields, replace_invalid=True) == {}
    # 기본값(사용자 입력 보호)에서는 이미 있는 값을 바꾸지 않는다.
    assert "family_name" not in candidate_changes({"model_name": "Inline O2", "family_name": "KCTC"}, fields)
    assert "group_name" not in candidate_changes({"model_name": "M-O2", "group_name": "Oxide"}, fields)


def test_family_field_that_is_not_a_family_name_is_replaced():
    """계열 칸에 'M-C4'·'P-PJT' 같은 제목 조각이 들어오면 모델의 계열로, 등록 계열 이름은 그대로 둔다."""
    fields = ("model_name", "group_name", "family_name")
    fix = dict(replace_invalid=True)
    assert candidate_changes({"model_name": "M-C4", "group_name": "Poly", "family_name": "M-C4"}, fields, **fix) == {"family_name": "C4"}
    assert candidate_changes({"model_name": "M-C3", "group_name": "Poly", "family_name": "C3 구UI"}, fields, **fix) == {}
    assert candidate_changes({"model_name": "M-O2", "group_name": "Oxide", "family_name": "O2 (파운드리)"}, fields, **fix) == {}


def test_model_name_inside_title_is_found_only_when_unique():
    """'P-PJT M-C4 SW. 정기 Release List' 안의 M-C4를 찾고, 모델이 둘 이상이면 고르지 않는다."""
    assert find_candidate("P-PJT M-C4 SW. 정기 Release List").model_name == "M-C4"
    assert find_candidate("P-PJT M-O5 Prime SW Release").model_name == "M-O5 Prime"      # 긴 이름 우선
    assert find_candidate("KCTC Cluster Ox SW Release List").model_name == "Cluster Ox"
    assert find_candidate("M-O2 / M-O3 공용") is None
    assert find_candidate("P-PJT SW Release List") is None


def test_c3_release_sheet_name_gives_ui_candidate_with_note():
    """C3 릴리즈노트에서 UI가 비어 있으면 시트 이름(구UI)을 후보로 두고 확인 문구를 붙인다. 설비 현황표에는 쓰지 않는다."""
    from dataclasses import replace
    from setron.features.ai.schemas import ExtractKind
    from setron.features.excel_import.candidates import SHEET_UI_NOTE
    data = sheet(["2026-10-21", "화성", "E1", "PM1", "M-C3", "C3", "v1", None])
    draft = draft_for(data, [eq_row(2, equipment_code="E1", model_name="M-C3", family_name="C3", version_raw="v1")])
    release = replace(draft, kind=ExtractKind.RELEASE_NOTE, sheet=replace(draft.sheet, sheet_name="구UI"))
    rows = model_candidate_rows(release)
    assert all(change.get("ui_type") == "구UI" for change in rows.values()) and rows
    assert SHEET_UI_NOTE in candidate_notes(release)
    # 시트 이름이 UI가 아니면 릴리즈노트 C3에 UI 후보를 내지 않는다('미표기=통합UI'는 현황표 전용).
    other = replace(release, sheet=replace(release.sheet, sheet_name="Release"))
    assert all("ui_type" not in change for change in model_candidate_rows(other).values())


def test_user_typed_family_is_kept_but_ai_value_is_fixed():
    """화면에서 사람이 직접 입력한 계열은 보호하고, AI가 넣은 'KCTC' 같은 값만 고친다."""
    from setron.features.excel_import.service import edit_value
    data = sheet(["2026-10-21", "화성", "E1", "PM1", "Inline O2", "KCTC", "v1", None],
                 ["2026-10-21", "화성", "E2", "PM1", "Inline O2", "KCTC", "v1", None])
    draft = draft_for(data, [eq_row(2, equipment_code="E1", model_name="Inline O2", family_name="KCTC", version_raw="v1"),
                             eq_row(3, equipment_code="E2", model_name="Inline O2", family_name="KCTC", version_raw="v1")])
    draft = edit_value(draft, 2, "family_name", "내가 쓴 계열")
    rows = model_candidate_rows(draft)
    assert rows[1]["family_name"] == "Inline O2"
    assert "family_name" not in rows.get(2, {})
