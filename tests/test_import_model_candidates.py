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
