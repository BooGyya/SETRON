"""BE-008: 기본 검증·PM 충돌·기존값 비교·확인된 순서·전체 묶음 보류."""

from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.excel_import.reference import FamilyReference, ReferenceData, StoredRelease
from setron.features.excel_import.schemas import ImportBlockedError, ImportDraft, IssueLevel, ReviewRow, SheetSnapshot
from setron.features.excel_import.service import (
    build_save_request, draft_from_review, edit_value, has_errors, revert_row, validate, validate_batch,
)
from setron.features.extraction.normalization import normalize
from setron.features.extraction.schemas import CellSource, ExtractionIssue, ExtractionReview, FamilyKey, ReviewRow as ExtractRow
from setron.features.version.schemas import CompareReason, CompareStatus, ReleaseVersion
from setron.features.version.service import FamilyReleases


def row(number=1, **changes):
    """고정 PM 위치를 가진 합성 검토 행을 만든다.

    param
    - number: 추출 행 ID와 원본 Excel 행 번호.
    - changes: 기본값에서 교체할 공통 항목.

    return
    - 출처 위치와 원문을 포함한 검토 행.
    """
    values = dict(base_date="2026-11-02", site="화성", equipment_code="E-1", group_name="OXIDE",
                  model_name="M-O2", family_name="O2", pm_name="PM1", version_raw="v1")
    values.update(changes)
    return ReviewRow(number, values, {name: f"{chr(65 + i)}{number}" for i, name in enumerate(values)})


def draft(*rows, sheet="현황", book="합성.xlsx", kind=ExtractKind.EQUIPMENT_STATUS):
    """행 원문에 일치하는 시트 초안을 만든다.

    param
    - rows: 시트에 넣을 검토 행.
    - sheet, book: 원본 위치를 구분할 합성 이름.
    - kind: 설비 현황 또는 릴리즈 노트.

    return
    - 정상 원본 셀을 가진 가져오기 초안.
    """
    cells = {r.sources[name]: value for r in rows for name, value in r.original.items()}
    return ImportDraft(kind, book, SheetSnapshot(sheet, 1, 1, max([r.row_id for r in rows], default=1), 26, cells), tuple(rows))


def extraction_review(**changes):
    """BE-007의 실제 정규화를 거친 합성 추출 결과를 만든다.

    param
    - changes: 기본 설비 행에서 교체할 원본 필드와 값.

    return
    - 원문·정규값·행별 추출 문제를 따로 보관한 한 행의 ExtractionReview.
    """
    raw = row(**changes).original
    sources = {name: CellSource("현황", f"A{i}", value) for i, (name, value) in enumerate(raw.items(), 1)}
    values, key, issues = normalize(raw, ExtractKind.EQUIPMENT_STATUS, 1, sources)
    return ExtractionReview(ExtractKind.EQUIPMENT_STATUS, "합성.xlsx", "현황",
        (ExtractRow(values, raw, sources, key, issues),), issues,
        {"현황": {source.address: source.value for source in sources.values()}})


def family(*, family_id=1, name="O2", model="M-O2", category="OXIDE", ui=None, structure=None, orders=(1, 2)):
    """문자열 정렬과 독립적인 확정 순서를 가진 합성 계열 자료를 만든다.

    param
    - family_id, name, model, category, ui, structure: 계열 ID와 업무 구분값.
    - orders: v1과 v2에 지정할 확인 순서. None은 미확인.

    return
    - 모델별 목표 N을 v2로 지정한 계열 자료.
    """
    releases = FamilyReleases(family_id, [ReleaseVersion(i, family_id, f"v{i}", order)
                                         for i, order in enumerate(orders, 1)])
    return FamilyReference(FamilyKey(category, name, ui, structure, model), releases, {model: 2})


def reference(*families, snapshots=(), releases=()):
    """DB를 변경하지 않고 주입할 기준 자료를 만든다.

    param
    - families: 계열별 릴리즈와 모델별 목표 N.
    - snapshots, releases: 정확한 날짜의 현황과 기존 릴리즈·Item 관리값.

    return
    - 조회 완료 상태의 합성 기준 자료.
    """
    return ReferenceData(frozenset({"E-1"}), families=tuple(families), snapshots=snapshots,
                         stored_releases=releases, catalog_loaded=True)


@pytest.mark.parametrize("version", ["v1_anything", "v1_d5_n4", "v1_한글 패치", "v1 HF1", "v1(H)"])
def test_unrestricted_hotfix_is_saved_without_inventing_order(version):
    """접미사 규칙 없이도 원문을 보존하고 미등록 버전의 비교만 판단 불가로 둔다.

    param
    - version: 형태가 서로 다른 합성 핫픽스 원문.
    """
    source = draft(row(version_raw=version))
    ref = reference(family())
    issues = validate(source, ref)
    assert not has_errors(issues)
    assert any(i.code == "RELEASE_UNREGISTERED" for i in issues)
    comparison = next(i.comparison for i in issues if i.comparison)
    assert comparison.status is CompareStatus.UNDETERMINED and comparison.reason is CompareReason.NOT_REGISTERED
    assert build_save_request(source, "r", ref).rows[0].values["version_raw"] == version


@pytest.mark.parametrize("orders,reason", [((None, 2), CompareReason.ORDER_UNKNOWN), ((2, 2), CompareReason.ORDER_CONFLICT)])
def test_unknown_or_conflicting_order_never_blocks_storage(orders, reason):
    """릴리즈 등록 여부와 선후관계의 미확인을 분리한다.

    param
    - orders: 미확인 또는 중복된 확인 순서.
    - reason: 비교 불가를 설명할 근거.
    """
    ref = reference(family(orders=orders))
    issues = validate(draft(row()), ref)
    assert not has_errors(issues)
    comparison = next(i.comparison for i in issues if i.comparison)
    assert comparison.status is CompareStatus.UNDETERMINED
    assert comparison.reason in {reason, CompareReason.TARGET_INVALID}


def test_confirmed_order_overrides_version_name_and_targets_are_per_model():
    """버전 이름의 숫자 크기보다 확인 순서를 사용하고 다른 모델의 N을 빌리지 않는다."""
    ref = reference(family(orders=(20, 10)))
    comparison = next(i.comparison for i in validate(draft(row()), ref) if i.comparison)
    assert comparison.status is CompareStatus.AT_OR_ABOVE
    other_model = draft(row(model_name="M-O2 개조"))
    comparison = next(i.comparison for i in validate(other_model, ref) if i.comparison)
    assert comparison.reason is CompareReason.TARGET_NOT_SET


@pytest.mark.parametrize("field,value", [("version_raw", None), ("equipment_code", " "), ("site", None),
                                         ("base_date", "2026-02-30"), ("version_raw", "v\n1"),
                                         ("version_raw", "x" * 101), ("version_raw", "#N/A"),
                                         ("version_raw", 123), ("version_raw", "\ud800")])
def test_basic_error_in_hpa_holds_the_whole_batch(field, value):
    """HPA를 포함한 한 행의 오류만 있어도 정상 행까지 전체 저장 요청을 보류한다.

    param
    - field, value: 필수값·형식·길이·Excel 오류·인코딩의 잘못된 입력.
    """
    source = draft(row(), row(2, **{**dict(equipment_code="H-1", group_name="HPA", model_name="HPA", family_name="HPA"), field: value}))
    assert has_errors(validate(source))
    with pytest.raises(ImportBlockedError):
        build_save_request(source, "r")


def test_optional_area_stays_null_and_does_not_limit_model():
    """Memory·Foundry 공란을 임의로 보완하거나 모델 소속 제한으로 사용하지 않는다."""
    for area in (None, "Memory", "Foundry"):
        source = draft(row(production_area=area))
        assert not has_errors(validate(source, reference(family())))
        assert build_save_request(source, "r").rows[0].values["production_area"] == (area.upper() if area else None)


def test_missing_optional_area_in_one_pm_is_not_a_conflicting_value():
    """다른 PM에 생산 영역이 있어도 공란 자체를 충돌로 막거나 자동 보완하지 않는다."""
    source = draft(row(production_area=None), row(2, pm_name="PM2", production_area="Memory"))
    assert not has_errors(validate(source))
    request = build_save_request(source, "r")
    assert [r.values["production_area"] for r in request.rows] == [None, "MEMORY"]
    conflict = edit_value(source, 1, "production_area", "Foundry")
    assert has_errors(validate(conflict))


def test_same_fixed_pm_conflicts_across_different_families():
    """같은 위치에 O2·O3를 중복 배정한 문제를 계열별 정상 차이와 구분한다."""
    source = draft(row(), row(2, model_name="M-O3", family_name="O3", version_raw="v9"))
    errors = [i for i in validate(source) if i.code == "PM_CONFLICT"]
    assert {i.row_id for i in errors} == {1, 2} and all(i.blocks_save for i in errors)


def test_different_pm_models_and_families_are_normal():
    """혼합 설비의 PM별 모델·계열 차이를 오류나 PM 버전 불일치로 취급하지 않는다."""
    source = draft(row(), row(2, pm_name="PM2", model_name="M-O3", family_name="O3", version_raw="v9"))
    issues = validate(source)
    assert not has_errors(issues)
    assert not any(i.code == "PM_VERSION_MISMATCH" for i in issues)


def test_same_family_different_pm_versions_are_nonblocking():
    """같은 계열의 서로 다른 PM 버전은 비차단 예외로 보존한다."""
    source = draft(row(), row(2, pm_name="PM2", version_raw="v2"))
    issues = validate(source)
    assert not has_errors(issues)
    assert len([i for i in issues if i.code == "PM_VERSION_MISMATCH"]) == 2
    assert len(build_save_request(source, "r").rows) == 2


def test_partial_pm_assignment_blocks_the_batch():
    """PM 위치가 일부 행에만 있으면 나머지 위치를 추정하지 않고 차단한다."""
    assert any(i.code == "PM_LINK_MISSING" for i in validate(draft(row(), row(2, pm_name=None))))


def test_cross_sheet_conflict_has_both_file_locations_and_is_revalidated():
    """시트별 정상이더라도 같은 위치의 상충은 파일·시트를 보존해 묶음 전체에서 찾는다."""
    first = draft(row(), sheet="Memory")
    second = draft(row(version_raw="v2"), sheet="Foundry", book="다른.xlsx")
    assert not has_errors(validate(first)) and not has_errors(validate(second))
    conflicts = [i for i in validate_batch((first, second)) if i.code == "PM_CONFLICT"]
    assert {(i.workbook_name, i.sheet_name) for i in conflicts} == {("합성.xlsx", "Memory"), ("다른.xlsx", "Foundry")}
    assert all(i.excel_position == (1, 8) for i in conflicts)
    fixed = edit_value(second, 1, "version_raw", "v1")
    assert not has_errors(validate_batch((first, fixed)))


def test_existing_same_date_same_pm_reports_old_and_new_without_mutation():
    """전체 사업장의 동일 호기를 연결하고 같은 날짜·PM의 관리값만 비교한다."""
    old = SnapshotInput(equipment_code="E-1", base_date=date(2026, 11, 2), site="평택", model_name="M-O2", batch_id=1,
                        software=(SoftwareInput(family_id=1, version_raw="v2", pms=(PmInput(pm_name="PM1", group_id=1),)),))
    source = draft(row())
    before = deepcopy((source, old))
    issues = validate(source, reference(family(), snapshots=(old,)))
    changes = {i.field: (i.previous_value, i.current_value) for i in issues if i.code == "STORED_VALUE_CHANGED"}
    assert changes["site"] == ("평택", "화성") and changes["version_raw"] == ("v2", "v1")
    assert not has_errors(issues) and not any(i.code == "NEW_EQUIPMENT" for i in issues)
    assert (source, old) == before
    other_date = replace(old, base_date=date(2026, 11, 1))
    assert not any(i.code == "STORED_VALUE_CHANGED" for i in validate(source, reference(family(), snapshots=(other_date,))))


def test_existing_different_pm_is_not_a_conflicting_version():
    """같은 날짜라도 다른 PM의 버전을 현재 PM의 기존값으로 비교하지 않는다."""
    old = SnapshotInput(equipment_code="E-1", base_date=date(2026, 11, 2), site="화성", model_name="M-O2", batch_id=1,
                        software=(SoftwareInput(family_id=1, version_raw="v2", pms=(PmInput(pm_name="PM2", group_id=1),)),))
    issues = validate(draft(row()), reference(family(), snapshots=(old,)))
    assert not any(i.field == "version_raw" and i.code == "STORED_VALUE_CHANGED" for i in issues)
    assert not has_errors(issues) and any(i.code == "STORED_PM_VERSION_DIFFERENCE" for i in issues)


def test_c3_ui_and_kctc_category_are_resolved_separately():
    """C3 UI와 KCTC 구조를 구분하고 이름이 같은 다른 분류의 릴리즈를 연결하지 않는다."""
    legacy = family(name="C3", category="POLY", model="M-C3", ui="LEGACY")
    integrated = family(family_id=2, name="C3", category="POLY", model="M-C3", ui="INTEGRATED")
    source = draft(row(group_name="POLY", model_name="M-C3", family_name="C3", ui_type="통합UI", geometry_type="6각"))
    issues = validate(source, reference(legacy, integrated))
    assert not has_errors(issues)
    assert next(i.comparison for i in issues if i.comparison).release.family_id == 2
    kctc = draft(row(group_name="KCTC", model_name="Inline O2", structure_type="INLINE"))
    assert any(i.code == "FAMILY_UNRESOLVED" for i in validate(kctc, reference(family())))


def test_confirmed_alias_is_family_scoped_and_never_rewrites_raw():
    """확정된 KCTC 표기 대응만 연결하고 다른 점·밑줄 표기는 일괄 치환하지 않는다."""
    versions = FamilyReleases(3, (ReleaseVersion(1, 3, "v999.3.7", 1),), {"v999.3_7": "v999.3.7"})
    binding = FamilyReference(FamilyKey("KCTC", "O2", structure_type="INLINE"), versions, {"Inline O2": 1})
    source = draft(row(group_name="KCTC", model_name="Inline O2", structure_type="INLINE", version_raw="v999.3_7"))
    issues = validate(source, reference(binding))
    assert not has_errors(issues) and not any(i.code == "RELEASE_UNREGISTERED" for i in issues)
    prepared = build_save_request(source, "r", reference(binding)).rows[0]
    assert prepared.values["version_raw"] == "v999.3_7"
    assert prepared.family_id == 3 and prepared.release_version_id == 1
    assert any(i.code == "RELEASE_UNREGISTERED" for i in validate(edit_value(source, 1, "version_raw", "v999_3_7"), reference(binding)))


def test_release_items_are_grouped_by_family_and_existing_values_are_visible():
    """다른 계열의 같은 Item 번호를 합치지 않고 기존 내용 변경을 따로 표시한다."""
    first = row(item_no="1", content="새 내용", release_date="2026-11-01")
    second = row(2, family_name="O3", model_name="M-O3", item_no="1", content="다른 계열 내용", release_date="2026-10-01")
    source = draft(first, second, kind=ExtractKind.RELEASE_NOTE)
    ref = reference(family(), family(family_id=2, name="O3", model="M-O3"),
                    releases=(StoredRelease(1, "v1", {"item_no": "1", "content": "기존 내용", "release_date": "2026-11-01"}),))
    issues = validate(source, ref)
    assert not has_errors(issues)
    assert [(i.previous_value, i.current_value) for i in issues if i.code == "STORED_VALUE_CHANGED"] == [("기존 내용", "새 내용")]


def test_extraction_bridge_preserves_raw_and_rechecks_user_corrections():
    """BE-007 원문·정규값·확인 사유를 보존하면서 수정 후 전체 재검증을 지원한다."""
    values = row().original
    values = {**values, "version_raw": None}
    sources = {name: CellSource("현황", f"A{i}", value) for i, (name, value) in enumerate(values.items(), 1)}
    review = ExtractionReview(ExtractKind.EQUIPMENT_STATUS, "합성.xlsx", "현황",
        (ExtractRow(values, {**values, "version_raw": "잘못 추출한 값"}, sources, FamilyKey("OXIDE", "O2")),),
        (ExtractionIssue("SW 원본 확인 필요", "version_raw", "A9", 1),),
        {"현황": {source.address: source.value for source in sources.values()}})
    before = deepcopy(review)
    source = draft_from_review(review)
    assert has_errors(validate(source))
    corrected = edit_value(source, 1, "version_raw", "v1_자유 표기")
    assert not has_errors(validate(corrected))
    assert corrected.rows[0].original["version_raw"] == "잘못 추출한 값" and review == before
    assert build_save_request(corrected, "r").rows[0].edited_fields == ("version_raw",)


def test_global_extraction_note_does_not_replace_field_validation():
    """행별 오류가 아닌 AI의 전체 확인 메모를 지울 수 없는 차단 오류로 만들지 않는다."""
    values = row().original
    sources = {name: CellSource("현황", f"A{i}", value) for i, (name, value) in enumerate(values.items(), 1)}
    review = ExtractionReview(ExtractKind.EQUIPMENT_STATUS, "합성.xlsx", "현황",
        (ExtractRow(values, values, sources, FamilyKey("OXIDE", "O2")),),
        (ExtractionIssue("추출한 항목 전체를 검토하세요."),),
        {"현황": {source.address: source.value for source in sources.values()}})
    issues = validate(draft_from_review(review))
    assert not has_errors(issues)
    assert any(i.level is IssueLevel.REVIEW and i.message == "추출한 항목 전체를 검토하세요." for i in issues)


def test_no_drafts_is_a_blocking_result():
    """빈 묶음을 저장 가능한 검증 결과로 반환하지 않는다."""
    assert has_errors(validate_batch(()))


@pytest.mark.parametrize("kind", [ExtractKind.EQUIPMENT_STATUS, ExtractKind.RELEASE_NOTE])
def test_c3_ui_inferred_from_family_survives_save_request(kind):
    """계열명에서 확인한 C3 UI를 저장 요청에서도 보존해 다른 UI의 행이 합쳐지지 않게 한다.

    param
    - kind: 같은 정규화 계약을 사용하는 설비 현황과 릴리즈 노트.
    """
    source = draft(
        row(group_name="POLY", model_name="M-C3", family_name="C3 구UI", item_no="1", content="같은 Item"),
        row(2, equipment_code="E-2", group_name="POLY", model_name="M-C3", family_name="C3 통합UI",
            item_no="1", content="같은 Item"), kind=kind,
    )
    before = deepcopy(source)
    assert not has_errors(validate(source))
    request = build_save_request(source, "c3")
    assert [(r.values["family_name"], r.values["ui_type"]) for r in request.rows] == [
        ("C3", "LEGACY"), ("C3", "INTEGRATED"),
    ]
    assert [r.row_ids for r in request.rows] == [(1,), (2,)]
    assert [r.source_reference["family_name"]["cells"] for r in request.rows] == [["F1"], ["F2"]]
    assert source == before


def test_c3_true_duplicates_merge_only_within_the_same_ui():
    """서로 다른 UI는 보존하면서 같은 UI를 뜻하는 표기의 실제 중복만 합친다."""
    source = draft(
        row(group_name="POLY", model_name="M-C3", family_name="C3 구UI", item_no="1", content="Item"),
        row(2, group_name="POLY", model_name="M-C3", family_name="C3 통합UI", item_no="1", content="Item"),
        row(3, group_name="POLY", model_name="M-C3", family_name="C3", ui_type="구UI", item_no="1", content="Item"),
        kind=ExtractKind.RELEASE_NOTE,
    )
    request = build_save_request(source, "c3-duplicates")
    assert [(r.row_ids, r.values["ui_type"]) for r in request.rows] == [((1, 3), "LEGACY"), ((2,), "INTEGRATED")]
    assert request.rows[0].source_reference["family_name"]["cells"] == ["F1", "F3"]
    contradictory = draft(row(group_name="POLY", model_name="M-C3", family_name="C3 구UI", ui_type="통합UI"))
    with pytest.raises(ImportBlockedError):
        build_save_request(contradictory, "conflict")


def test_empty_dependent_fields_keep_confirmed_category_and_structure():
    """모델에서 확인한 분류·KCTC 구조도 공란 선반환 때문에 유실되지 않게 한다."""
    source = draft(row(group_name=None, model_name="Inline O2", structure_type=None))
    request = build_save_request(source, "kctc")
    assert request.rows[0].values["group_name"] == "KCTC"
    assert request.rows[0].values["structure_type"] == "INLINE"


@pytest.mark.parametrize("empty", [None, "", "  "])
def test_explicit_clear_resolves_invalid_optional_area_and_revert_restores_error(empty):
    """자동으로 None이 된 잘못된 선택값을 명시 공란 수정으로 해결하고 원복 시 오류를 복구한다.

    param
    - empty: 사용자가 공란을 전달하는 세 가지 입력 형태.
    """
    review = extraction_review(production_area="잘못된 생산 영역")
    before = deepcopy(review)
    source = draft_from_review(review)
    assert source.row(1).value("production_area") is None
    assert has_errors(validate(source))
    corrected = edit_value(source, 1, "production_area", empty)
    assert corrected.row(1).edits == {"production_area": None}
    assert not has_errors(validate(corrected))
    request = build_save_request(corrected, "clear")
    assert request.rows[0].values["production_area"] is None
    assert request.rows[0].edited_fields == ("production_area",)
    restored = revert_row(corrected, 1)
    assert restored == source and has_errors(validate(restored))
    assert review == before and corrected.row(1).original["production_area"] == "잘못된 생산 영역"


def test_optional_clear_does_not_resolve_other_errors_or_erase_normal_revert_semantics():
    """선택값 보완은 해당 오류만 해제하고 필수값 오류와 정상 값 복귀의 의미를 유지한다."""
    source = draft_from_review(extraction_review(production_area="잘못된 값", version_raw=None))
    corrected = edit_value(source, 1, "production_area", None)
    errors = [i for i in validate(corrected) if i.blocks_save]
    assert errors and all(i.field == "version_raw" for i in errors)
    with pytest.raises(ImportBlockedError):
        build_save_request(corrected, "still-invalid")
    normal = draft_from_review(extraction_review(production_area="Memory"))
    changed = edit_value(normal, 1, "production_area", "Foundry")
    assert edit_value(changed, 1, "production_area", "MEMORY").row(1).edits == {}
    blank = draft_from_review(extraction_review(production_area=None))
    assert edit_value(blank, 1, "production_area", None).row(1).edits == {}
