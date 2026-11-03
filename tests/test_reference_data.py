"""멘토 확정 기준 자료(reference_data)와 메모리/파운드리 계열 선택 검증. DB 없이 실행한다."""

from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.reference import FamilyReference, ModelReference, ReferenceData, resolve_family
from setron.features.excel_import.schemas import ReviewRow
from setron.features.extraction.schemas import FamilyKey
from setron.features.reference_data import area_of, default_area, resolve_model_text, strip_foundry
from setron.features.version.service import FamilyReleases


def test_area_from_explicit_line_sheet_and_workbook():
    assert area_of({"production_area": "foundry"}) == "FOUNDRY"
    assert area_of({"site": "평택", "line": "S5"}) == "FOUNDRY"
    assert area_of({"site": "평택", "line": "P1F(3F)"}) == "MEMORY"           # 층 표기는 떼고 비교
    assert area_of({"site": "화성", "line": "NRD (MR2동)"}, "Foundry") == "FOUNDRY"  # 같은 라인명은 시트로 가른다
    assert area_of({}, "Release", "P_PJT_M-C3_Foundry_SW_Release_List_교육용.xlsx") == "FOUNDRY"
    assert area_of({}, "Release", "P_PJT_M-C4_SW_정기_Release_List_교육용.xlsx") is None
    assert default_area("화성", "S1 (기흥)") == "FOUNDRY"


def test_model_text_and_version_rule():
    assert resolve_model_text("Ox(inline)")[0] == "Inline Ox"
    assert resolve_model_text("M-C3")[0] == "M-C3"
    assert resolve_model_text("M-C4/C4+ / Smart UI", "v4.56.8.1_f1_DEX")[0] == "M-C4+"
    assert resolve_model_text("M-C4/C4+ / Smart UI", "v4.56.7.1_f1")[0] == "M-C4"
    assert strip_foundry("O2 (파운드리)") == ("O2", True) and strip_foundry("O2") == ("O2", False)


def _c3_reference():
    key = FamilyKey("POLY", "C3", "INTEGRATED", None, "C3")
    memory = FamilyReference(key, FamilyReleases(11, ()), owner_group_id=1)
    foundry = FamilyReference(key, FamilyReleases(12, ()), owner_group_id=1, production_area="FOUNDRY")
    models = (ModelReference(1, "M-C3", "POLY", None),)
    return ReferenceData(frozenset(), families=(memory, foundry), catalog_loaded=True, models=models)


def _row(**values):
    base = {"group_name": "Poly", "model_name": "M-C3", "family_name": "C3", "ui_type": "통합UI"}
    return ReviewRow(1, {**base, **values})


def test_c3_memory_and_foundry_families_are_chosen_by_area():
    reference = _c3_reference()
    kind = ExtractKind.EQUIPMENT_STATUS
    assert resolve_family(_row(site="평택", line="P1F"), kind, reference).releases.family_id == 11
    assert resolve_family(_row(site="평택", line="S5"), kind, reference).releases.family_id == 12
    assert resolve_family(_row(), kind, reference, sheet_name="Foundry").releases.family_id == 12
    assert resolve_family(_row(), kind, reference).releases.family_id == 11          # 판단 자료가 없으면 메모리 계열
    release = ExtractKind.RELEASE_NOTE
    assert resolve_family(_row(), release, reference,
                          workbook_name="P_PJT_M-C3_Foundry_SW_Release_List_교육용.xlsx").releases.family_id == 12


def test_model_derived_family_needs_no_source_cell():
    """원본 셀이 있는 'M-C3' 에서 정규화로 정해지는 SW 계열 'C3'·UI '통합UI'는 출처 셀 없이도 저장 검사를 통과한다."""
    from setron.features.excel_import.provenance import _derived
    from setron.features.excel_import.schemas import SaveRow
    raw = {"sheet": "Memory", "cells": {"D4": "M-C3"}}
    row = SaveRow((1,), {"model_name": "M-C3", "family_name": "C3", "ui_type": "INTEGRATED"},
                  {"model_name": {"sheet": "Memory", "cells": ["D4"]}}, ())
    kind = ExtractKind.EQUIPMENT_STATUS
    assert _derived(row, "family_name", raw, kind) and _derived(row, "ui_type", raw, kind)
    wrong = SaveRow((1,), {"model_name": "M-C3", "family_name": "C4"}, {"model_name": {"sheet": "Memory", "cells": ["D4"]}}, ())
    assert not _derived(wrong, "family_name", raw, kind)


def test_normalized_ui_and_structure_match_original_cell_spelling():
    """'M-C3 (구UI)' 셀에서 읽은 UI(LEGACY)·'Cluster Ox' 셀의 구조(CLUSTER)는 원본 표기로 대조해 통과한다."""
    from setron.features.excel_import.provenance import _matches_saved
    kind = ExtractKind.EQUIPMENT_STATUS
    values = {"model_name": "M-C3", "family_name": "C3", "ui_type": "LEGACY", "group_name": "POLY"}
    assert _matches_saved("ui_type", "LEGACY", "M-C3 (구UI)", values, kind, "D5")
    assert not _matches_saved("ui_type", "INTEGRATED", "M-C3 (구UI)", values, kind, "D5")    # 다른 값은 막는다
    kctc = {"model_name": "Cluster Ox", "group_name": "KCTC", "family_name": "Cluster Ox", "structure_type": "CLUSTER"}
    assert _matches_saved("structure_type", "CLUSTER", "Cluster Ox", kctc, kind, "D43")
    assert not _matches_saved("structure_type", "INLINE", "Cluster Ox", kctc, kind, "D43")
