"""관리한 분류가 가져오기·내보내기·공통 조회에서도 같은 코드로 연결되는지 확인한다."""

from datetime import date

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.data_admin import DataAdminService
from setron.features.equipment.repository import EquipmentRepository
from setron.features.equipment.location import validate_location
from setron.features.equipment.schemas import SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.repository import ImportValidationRepository
from setron.features.excel_import.reference import resolve_family
from setron.features.excel_import.schemas import ReviewRow
from setron.features.export.management import build_management_workbook
from setron.features.hierarchy import AREAS_KEY, CATEGORIES_KEY, MODEL_ALIASES_KEY
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.query_options import QueryOptionsService, QueryScope
from setron.features.storage.validation import StorageValidationError
from test_import_save_mysql import request, row
from test_management_service import observe
from test_release_revision import all_rows, release, revision_storage  # noqa: F401


def put_setting(tx, key, value):
    tx.insert("app_setting", {"setting_key": key, "setting_value": value})


def test_custom_codes_labels_and_renamed_model_round_trip_through_excel_services(revision_storage):
    storage = revision_storage
    release(storage)
    with storage.transaction() as tx:
        put_setting(tx, CATEGORIES_KEY, {"labels": {"C123456789": "신규 공정"}})
        put_setting(tx, AREAS_KEY, {"labels": {"MEMORY": "메모리 생산", "A123456789": "시험 생산"}})
        put_setting(tx, MODEL_ALIASES_KEY, {"aliases": {"이전 모델": 1}})
        tx.update("equipment_group", {"group_id": 1}, {"group_name": "현재 모델", "category": "C123456789"})
    values = {"equipment_code": "CUSTOM-IMPORT", "base_date": "2026-10-11", "site": "화성", "line": "검사 A",
              "model_name": "현재 모델", "group_name": "신규 공정", "family_name": "O2", "version_raw": "v1",
              "production_area": "A123456789"}
    saver = DatabaseImportSaver(storage)
    saver.save(request(ExtractKind.EQUIPMENT_STATUS,
                       [row(1, {"equipment_code": "A2", "version_raw": "B2"}, **values)], "custom-hierarchy"))
    snapshot = all_rows(storage, "equipment_snapshot")[0]
    assert (snapshot["group_id"], snapshot["production_area"]) == (1, "A123456789")
    book = build_management_workbook(storage, production_area="A123456789")
    cells = list(book["설비현황표"].values)
    assert cells[1][5:7] == ("현재 모델", "시험 생산")
    with storage.transaction() as tx:
        reference = ImportValidationRepository().load(tx, ())
    old = ReviewRow(1, {"model_name": "이전 모델", "group_name": "신규 공정", "family_name": "O2"})
    family = resolve_family(old, ExtractKind.EQUIPMENT_STATUS, reference)
    assert family is not None and family.owner_group_id == 1


@pytest.mark.parametrize(("raw_line", "child"), [("검사 A", "검사 A"), ("P1F(3F)", "P1F")])
def test_parent_query_includes_only_configured_descendants_and_keeps_source(revision_storage, raw_line, child):
    storage = revision_storage
    first = release(storage)
    snapshot = observe(storage, first)
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": snapshot.snapshot_id}, {"line": raw_line})
        put_setting(tx, "registered_lines.v1", {"lines": [
            {"site": "화성", "line": "상위 Line", "fab": "MEMORY", "parent_line": None},
            {"site": "화성", "line": child, "fab": "MEMORY", "parent_line": "상위 Line"},
        ]})
    before = all_rows(storage, "import_batch")
    options = QueryOptionsService(storage).read(QueryScope(date(2026, 10, 1), lines=("상위 Line",)))
    assert options.snapshot_ids == (snapshot.snapshot_id,)
    assert options.matches[0].snapshot.line == raw_line
    assert QueryOptionsService(storage).read(QueryScope(date(2026, 10, 1), lines=(child,))).snapshot_ids == options.snapshot_ids
    assert all_rows(storage, "import_batch") == before


def test_category_display_rename_preserves_code_and_exclusion_semantics(revision_storage):
    admin = DataAdminService(revision_storage)
    admin.rename_category("HPA", "분석 제외 설비")
    with revision_storage.transaction() as tx:
        hpa = EquipmentRepository().rows(tx, "equipment_group", category="HPA")
    assert hpa and all(not group["analysis_enabled"] for group in hpa)
    assert admin.hierarchy_options()["categories"]["HPA"] == "분석 제외 설비"


def test_managed_line_area_cannot_be_overridden_using_legacy_floor_notation(revision_storage):
    with revision_storage.transaction() as tx:
        put_setting(tx, "registered_lines.v1", {"lines": [
            {"site": "화성", "line": "P1F", "fab": "MEMORY", "parent_line": "P1"},
        ]})
        with pytest.raises(StorageValidationError, match="위치 탭"):
            validate_location(tx, "화성", "P1F(3f)", "FOUNDRY")


def test_conflicting_import_cannot_restore_an_old_managed_area(revision_storage):
    storage = revision_storage
    release(storage)
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL"})["batch_id"]
        put_setting(tx, "registered_lines.v1", {"lines": [
            {"site": "화성", "line": "관리 Line", "fab": "MEMORY", "parent_line": None},
        ]})
    incoming = SnapshotInput(equipment_code="AREA-CONFLICT", base_date=date(2026, 11, 1), site="화성",
        line="관리 Line", model_name="O2", group_id=1, batch_id=batch, production_area="FOUNDRY",
        software=(SoftwareInput(family_id=1, version_raw="v1"),))
    with pytest.raises(StorageValidationError, match="위치 탭"):
        EquipmentService(storage).save_snapshots((incoming,))
    assert all_rows(storage, "equipment_snapshot") == []
    assert all_rows(storage, "equipment") == []


def test_managed_line_area_precedes_old_sheet_name():
    from setron.features import reference_data as reference
    before = dict(reference.REGISTERED_AREAS)
    try:
        reference.use_registered_lines([{"site": "화성", "line": "사용자 라인", "fab": "FOUNDRY"}])
        assert reference.default_area("화성", "사용자 라인", "Memory") == "FOUNDRY"
    finally:
        reference.REGISTERED_AREAS = before
