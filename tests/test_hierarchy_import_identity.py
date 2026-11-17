"""관리 이름 변경 뒤에도 원문과 등록 모델·계열 ID를 함께 보존한다."""

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.data_admin import DataAdminService
from setron.features.excel_import.provenance import validate_save_request_sources
from setron.features.excel_import.reference import FamilyReference, ModelReference, ReferenceData, family_key, resolve_family
from setron.features.excel_import.repository import ImportValidationRepository
from setron.features.excel_import.schemas import ImportBlockedError, SaveRequest, SaveRow
from setron.features.excel_import.service import build_save_request, has_errors, validate, validate_batch
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.validation import StorageValidationError
from setron.features.extraction.schemas import FamilyKey
from setron.features.version.service import FamilyReleases

from test_import_validation import draft, row
from test_release_revision import all_rows, revision_storage  # noqa: F401


@pytest.mark.parametrize("original,current,use_original", [
    ("M-C3", "이름을 바꾼 C3", True), ("M-C3", "이름을 바꾼 C3", False),
    ("Inline Ox", "이름을 바꾼 Inline", True), ("Inline Ox", "이름을 바꾼 Inline", False),
])
def test_renamed_builtins_keep_family_identity_and_original_source(revision_storage, original, current, use_original):
    storage = revision_storage
    admin = DataAdminService(storage)
    if original == "M-C3":
        with storage.transaction() as tx:
            tx.update("equipment_group", {"group_id": 3}, {"group_name": original})
            tx.update("version_family", {"family_id": 2}, {"ui_type": "INTEGRATED"})
        group_id, family_id, category, family_name = 3, 2, "POLY", "C3"
    else:
        created = admin.create_group(original, "KCTC")
        group_id, category, family_name = created.group_id, "KCTC", original
        family_id = next(f.family_id for f in admin.release_families() if f.group_id == group_id)
    admin.update_groups([(group_id, current, None)])
    input_model = original if use_original else current
    source = draft(row(model_name=input_model, group_name=category, family_name=family_name))
    with storage.transaction() as tx:
        reference = ImportValidationRepository().load(tx, (source,))
    family = resolve_family(source.rows[0], ExtractKind.EQUIPMENT_STATUS, reference)
    assert family is not None and family.releases.family_id == family_id
    key = family_key(source.rows[0], ExtractKind.EQUIPMENT_STATUS, reference)
    if original == "M-C3":
        assert key.ui_type == "INTEGRATED"
    else:
        assert key.structure_type == "INLINE"
    request = build_save_request(source, "managed-identity", reference)
    assert request.rows[0].values["model_name"] == input_model
    assert request.rows[0].values["family_name"] == family_name
    validate_save_request_sources(request)
    DatabaseImportSaver(storage).save_many((request,))
    saved = all_rows(storage, "equipment_snapshot")[0]
    assert saved["group_id"] == group_id and saved["model_name"] == input_model
    assert all_rows(storage, "equipment_version")[0]["family_id"] == family_id
    batches = all_rows(storage, "import_batch")
    original_batch = next(batch for batch in batches if batch["batch_id"] == saved["batch_id"])
    assert input_model in str(original_batch["raw_data"])


def test_custom_kctc_same_name_family_alias_is_narrow_and_keeps_ids(revision_storage):
    storage = revision_storage
    admin = DataAdminService(storage)
    group = admin.create_group("사용자 모델", "KCTC")
    family_id = next(f.family_id for f in admin.release_families() if f.group_id == group.group_id)
    admin.update_groups([(group.group_id, "사용자 모델 새 이름", None)])
    source = draft(row(model_name="사용자 모델", family_name="사용자 모델", group_name="KCTC"))
    with storage.transaction() as tx:
        reference = ImportValidationRepository().load(tx, (source,))
        matched = resolve_family(source.rows[0], ExtractKind.EQUIPMENT_STATUS, reference)
        assert matched is not None and matched.releases.family_id == family_id
        saver = DatabaseImportSaver(storage)
        model = tx.get("equipment_group", {"group_id": group.group_id})
        assert saver._family(tx, model, source.rows[0].original, SaveRow((1,), source.rows[0].original, None, ()))["family_id"] == family_id
        wrong = {**source.rows[0].original, "family_name": "모르는 계열"}
        with pytest.raises(StorageValidationError, match="하나로 정할 수 없습니다"):
            saver._family(tx, model, wrong, SaveRow((1,), wrong, None, ()))
    request = build_save_request(source, "custom-alias", reference)
    validate_save_request_sources(request)
    DatabaseImportSaver(storage).save_many((request,))
    assert all_rows(storage, "equipment_version")[0]["family_id"] == family_id


def test_variant_keeps_renamed_parent_family_identity(revision_storage):
    storage = revision_storage
    admin = DataAdminService(storage)
    parent = admin.create_group("Inline Ox", "KCTC")
    child = admin.create_group("사용자 개조 모델", "KCTC", parent.group_id)
    family_id = next(f.family_id for f in admin.release_families() if f.group_id == parent.group_id)
    admin.update_groups([(parent.group_id, "상위 모델 새 이름", None)])
    source = draft(row(model_name=child.name, family_name="Inline Ox", group_name="KCTC"))
    with storage.transaction() as tx:
        reference = ImportValidationRepository().load(tx, (source,))
    family = resolve_family(source.rows[0], ExtractKind.EQUIPMENT_STATUS, reference)
    assert family is not None and family.releases.family_id == family_id
    request = build_save_request(source, "parent-alias", reference)
    validate_save_request_sources(request)
    DatabaseImportSaver(storage).save_many((request,))
    assert all_rows(storage, "equipment_snapshot")[0]["group_id"] == child.group_id
    assert all_rows(storage, "equipment_version")[0]["family_id"] == family_id


def test_source_backed_custom_labels_keep_evidence_and_store_current_codes(revision_storage, monkeypatch):
    from setron.features import production_area, reference_data

    monkeypatch.setattr(reference_data, "REGISTERED_AREAS", dict(reference_data.REGISTERED_AREAS))
    monkeypatch.setattr(production_area, "_managed_labels", dict(production_area._managed_labels))
    storage = revision_storage
    admin = DataAdminService(storage)
    category, area = admin.add_category("사용자 공정 분류"), admin.add_area("사용자 생산 구역")
    group = admin.create_group("원래 모델", category)
    family_id = next(f.family_id for f in admin.release_families() if f.group_id == group.group_id)
    admin.update_groups([(group.group_id, "현재 모델 이름", None)])
    admin.add_site("화성")
    admin.add_line("화성", "사용자 Line", area)
    source = draft(row(model_name="원래 모델", family_name="원래 모델", group_name="사용자 공정 분류",
                       line="사용자 Line", production_area="사용자 생산 구역"))
    with storage.transaction() as tx:
        reference = ImportValidationRepository().load(tx, (source,))
    request = build_save_request(source, "source-backed-labels", reference)
    values = request.rows[0].values
    assert values["group_name"] == "사용자 공정 분류"
    assert values["production_area"] == "사용자 생산 구역"
    assert request.rows[0].edited_fields == ()
    validate_save_request_sources(request)
    DatabaseImportSaver(storage).save_many((request,))
    saved = all_rows(storage, "equipment_snapshot")[0]
    assert saved["group_id"] == group.group_id and saved["production_area"] == area
    assert all_rows(storage, "equipment_version")[0]["family_id"] == family_id


@pytest.mark.parametrize("old_spelling", [True, False])
def test_short_c3_model_and_same_named_family_can_both_be_renamed(revision_storage, old_spelling):
    storage = revision_storage
    with storage.transaction() as tx:
        tx.update("version_family", {"family_id": 2}, {"ui_type": "INTEGRATED"})
    DataAdminService(storage).update_groups([(3, "현재 C3 계열명", None)])
    spelling = "C3" if old_spelling else "현재 C3 계열명"
    source = draft(row(model_name=spelling, family_name=spelling, group_name="POLY"))
    with storage.transaction() as tx:
        reference = ImportValidationRepository().load(tx, (source,))
    family = resolve_family(source.rows[0], ExtractKind.EQUIPMENT_STATUS, reference)
    assert family is not None and family.releases.family_id == 2
    request = build_save_request(source, "short-c3-alias", reference)
    validate_save_request_sources(request)
    DatabaseImportSaver(storage).save_many((request,))
    assert all_rows(storage, "equipment_version")[0]["family_id"] == 2


def test_different_custom_category_labels_on_two_pms_block_ui_and_saver(revision_storage):
    storage = revision_storage
    admin = DataAdminService(storage)
    first_category = admin.add_category("첫 사용자 공정")
    second_category = admin.add_category("다른 사용자 공정")
    admin.create_group("첫 PM 모델", first_category)
    admin.create_group("다른 PM 모델", second_category)
    first = row(1, model_name="첫 PM 모델", family_name="첫 PM 모델", group_name="첫 사용자 공정", pm_name="PM1")
    second = row(2, model_name="다른 PM 모델", family_name="다른 PM 모델", group_name="다른 사용자 공정", pm_name="PM2")
    source = draft(first, second)
    with storage.transaction() as tx:
        reference = ImportValidationRepository().load(tx, (source,))
    assert any(issue.blocks_save and "행마다 다릅니다" in issue.message for issue in validate(source, reference))
    assert has_errors(validate_batch((draft(first, sheet="첫 시트"), draft(second, sheet="다른 시트")), reference))
    with pytest.raises(ImportBlockedError):
        build_save_request(source, "category-conflict", reference)
    save_rows = tuple(SaveRow((review.row_id,), dict(review.original),
                             {name: {"sheet": source.sheet.sheet_name, "cells": [address]}
                              for name, address in review.sources.items()}, ()) for review in source.rows)
    request = SaveRequest("raw-category-conflict", ExtractKind.EQUIPMENT_STATUS, source.workbook_name,
                          {"sheet": source.sheet.sheet_name, "cells": dict(source.sheet.cells)}, save_rows, 0, None)
    with pytest.raises(StorageValidationError, match="행마다 다릅니다"):
        DatabaseImportSaver(storage).save_many((request,))
    assert not all_rows(storage, "equipment_snapshot") and not all_rows(storage, "equipment")


def test_old_and_current_spellings_of_same_model_do_not_create_false_pm_conflicts(revision_storage):
    storage = revision_storage
    admin = DataAdminService(storage)
    group = admin.create_group("과거 모델 이름", "POLY")
    admin.update_groups([(group.group_id, "현재 모델 이름", None)])
    first = row(1, model_name="과거 모델 이름", family_name="과거 모델 이름", group_name="POLY", pm_name="PM1")
    second = row(2, model_name="현재 모델 이름", family_name="현재 모델 이름", group_name="POLY", pm_name="PM1")
    source = draft(first, second)
    with storage.transaction() as tx:
        reference = ImportValidationRepository().load(tx, (source,))
    issues = validate(source, reference)
    assert not has_errors(issues)
    assert not any(issue.code in {"MODEL_CONFLICT", "PM_CONFLICT"} for issue in issues)
    request = build_save_request(source, "same-pm-aliases", reference)
    validate_save_request_sources(request)
    DatabaseImportSaver(storage).save_many((request,))
    pms = all_rows(storage, "equipment_pm")
    assert len(pms) == 1 and pms[0]["group_id"] == group.group_id
    assert pms[0]["model_name_raw"] == "과거 모델 이름"
    model_sources = pms[0]["source_reference"]["model_name"]
    assert set(model_sources["cells"]) == {first.sources["model_name"], second.sources["model_name"]}


def test_same_pm_different_model_ids_remains_blocked_before_save(revision_storage):
    storage = revision_storage
    admin = DataAdminService(storage)
    parent = admin.create_group("상위 PM 모델", "POLY")
    child = admin.create_group("서로 다른 PM 모델", "POLY", parent.group_id)
    first = row(1, model_name=parent.name, family_name=parent.name, group_name="POLY", pm_name="PM1")
    second = row(2, model_name=child.name, family_name=parent.name, group_name="POLY", pm_name="PM1")
    source = draft(first, second)
    save_rows = tuple(SaveRow((review.row_id,), dict(review.original),
                             {name: {"sheet": source.sheet.sheet_name, "cells": [address]}
                              for name, address in review.sources.items()}, ()) for review in source.rows)
    request = SaveRequest("different-model-same-pm", ExtractKind.EQUIPMENT_STATUS, source.workbook_name,
                          {"sheet": source.sheet.sheet_name, "cells": dict(source.sheet.cells)}, save_rows, 0, None)
    with pytest.raises(StorageValidationError, match="같은 PM"):
        DatabaseImportSaver(storage).save_many((request,))
    assert not all_rows(storage, "equipment_snapshot") and not all_rows(storage, "equipment_pm")


def test_family_resolution_uses_same_reference_area_labels_without_global_state():
    key = FamilyKey("OXIDE", "O2", None, None, "M-O2")
    reference = ReferenceData(frozenset(), models=(ModelReference(1, "M-O2", "OXIDE"),),
        area_labels={"MEMORY": "저장 공정", "FOUNDRY": "주조 공정"}, families=(
            FamilyReference(key, FamilyReleases(1, ()), owner_group_id=1),
            FamilyReference(key, FamilyReleases(2, ()), owner_group_id=1, production_area="FOUNDRY")))
    source = row(model_name="M-O2", group_name="OXIDE", family_name="O2", production_area="주조 공정")
    assert resolve_family(source, ExtractKind.EQUIPMENT_STATUS, reference).releases.family_id == 2
