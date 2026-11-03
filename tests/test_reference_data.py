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
