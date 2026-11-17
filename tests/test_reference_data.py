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


def test_c3_default_ui_only_for_equipment_status():
    """UI 미표기 M-C3 → 통합UI 는 설비 현황표에만. 릴리즈노트 C3는 UI를 확인해야 한다(구UI 노트가 통합UI로 저장되지 않게)."""
    from setron.features.extraction.normalization import normalize
    equipment = normalize({"model_name": "M-C3"}, ExtractKind.EQUIPMENT_STATUS, 1, {})
    release = normalize({"model_name": "M-C3", "version_raw": "v1.1.1.6B_6", "content": "x"}, ExtractKind.RELEASE_NOTE, 1, {})
    assert equipment[0]["ui_type"] == "INTEGRATED" and equipment[1] is not None
    assert release[0]["ui_type"] is None and release[1] is None
    assert any(issue.field == "ui_type" for issue in release[2])


def test_comma_listed_versions_in_one_cell():
    """C4 정기 노트의 'v4.56.6.41_e, v4.56.6.41_e _DFB (2026-01-14)' 셀에서 나열된 버전 하나만 허용한다."""
    from setron.features.extraction.service import _matches
    cell = "v4.56.6.41_e, v4.56.6.41_e _DFB (2026-01-14)"
    assert _matches("version_raw", "v4.56.6.41_e", cell, {})
    assert _matches("version_raw", "v4.56.6.41_e _DFB", cell, {})
    assert not _matches("version_raw", "v4.56.6.41", cell, {})
    assert not _matches("version_raw", "v4.56.6.41_e, v4", cell, {})


def test_structure_derived_from_model_read_inside_title_cell():
    """릴리즈노트 모델을 제목 셀 'KCTC Inline O2 SW Release List'에서 읽어도 구조 INLINE 을 모델로 확인한다."""
    from setron.features.excel_import.provenance import _derived
    from setron.features.excel_import.schemas import SaveRow
    raw = {"sheet": "Inline O2", "cells": {"A1": "KCTC Inline O2 SW Release List"}}
    row = SaveRow((1,), {"model_name": "Inline O2", "group_name": "KCTC", "family_name": "Inline O2",
                         "structure_type": "INLINE"},
                  {"model_name": {"sheet": "Inline O2", "cells": ["A1"]}}, ("family_name",))
    assert _derived(row, "structure_type", raw, ExtractKind.RELEASE_NOTE)
    wrong = SaveRow((1,), {**row.values, "structure_type": "CLUSTER"}, row.source_reference, ("family_name",))
    assert not _derived(wrong, "structure_type", raw, ExtractKind.RELEASE_NOTE)


def test_c3_ui_comes_from_model_cell_before_default():
    """'M-C3 (구UI)' 셀에서 모델만 'M-C3'로 읽고 UI를 비워도 통합UI 기본값이 아니라 구UI 로 둔다."""
    from setron.features.extraction.normalization import normalize
    from setron.features.extraction.schemas import CellSource
    kind = ExtractKind.EQUIPMENT_STATUS
    legacy = normalize({"model_name": "M-C3"}, kind, 1, {"model_name": CellSource("Memory", "D5", "M-C3 (구UI)")})
    plain = normalize({"model_name": "M-C3"}, kind, 1, {"model_name": CellSource("Memory", "D4", "M-C3")})
    assert legacy[0]["ui_type"] == "LEGACY" and plain[0]["ui_type"] == "INTEGRATED"
    both = normalize({"model_name": "M-C3"}, ExtractKind.RELEASE_NOTE, 1, {"model_name": CellSource(
        "통합UI", "A1", "P-PJT M-C3 SW Foundry Release List — 통합UI 기준 ※ 구UI 파운드리 파일 존재 여부는 확인 중")})
    assert both[0]["ui_type"] is None          # 두 표기가 함께 있으면 정하지 않는다(시트 이름 후보로 확인)
    # 저장 직전 검사도 같은 판단: 구UI 셀인데 통합UI로 저장하려 하면 막는다.
    from setron.features.excel_import.provenance import _derived
    from setron.features.excel_import.schemas import SaveRow
    raw = {"sheet": "Memory", "cells": {"D5": "M-C3 (구UI)"}}
    bad = SaveRow((1,), {"model_name": "M-C3", "family_name": "C3", "ui_type": "INTEGRATED"},
                  {"model_name": {"sheet": "Memory", "cells": ["D5"]}}, ())
    assert not _derived(bad, "ui_type", raw, kind)
