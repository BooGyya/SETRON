"""데이터 관리 조회·설비군 등록을 실제 MySQL 5.6.19에서 확인한다 (11-10 데이터 관리 개편).

가져오기 → 현황표(원본 셀 연결) → 관리 저장 → 변경 이력(파일·시트·셀·전후) 흐름과 설비군 등록 규칙.
"""

import os
from datetime import date

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.data_admin import WORKBOOK_KEY, DataAdminService
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.management.schemas import RecordEdit
from setron.features.management.service import ManagementService
from setron.features.storage.validation import StorageValidationError
from test_import_save_mysql import EQUIPMENT, SHEET, request, row, storage  # noqa: F401
from test_schema_mysql import schema_database  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


def _save(storage, code="QA-DA-1"):
    rows = [row(1, {"equipment_code": "C3", "line": "B3", "version_raw": "D3"}, **EQUIPMENT, equipment_code=code,
                model_name="M-C4", family_name="C4", version_raw="v4.56.6.43")]
    return DatabaseImportSaver(storage).save(request(ExtractKind.EQUIPMENT_STATUS, rows, f"req-{code}"))


def test_sheet_grid_binds_cells_and_history_shows_file_sheet_cell_before_after(storage):
    receipt = _save(storage)
    service = DataAdminService(storage)
    with storage.transaction() as tx:
        raw = tx.get("import_batch", {"batch_id": receipt.batch_id})["raw_data"]
    assert raw[WORKBOOK_KEY] == "가상.xlsx"  # 앞으로 가져오는 자료는 파일 이름을 함께 보관한다
    sheet = next(s for s in service.status_sheets() if s.batch_id == receipt.batch_id)
    assert (sheet.base_date, sheet.site, sheet.sheet, sheet.workbook, sheet.equipment_count) == (
        date(2026, 10, 21), "화성", SHEET, "가상.xlsx", 1)
    grid = service.sheet_grid(receipt.batch_id)
    assert grid.sheet == SHEET and grid.cells["D3"] == "v4.56.6.43"
    assert grid.bindings["D3"].table == "equipment_version" and grid.bindings["D3"].editable
    assert grid.bindings["B3"].table == "equipment_snapshot" and grid.bindings["B3"].field == "line"
    assert grid.bindings["C3"].table == "equipment" and grid.bindings["C3"].current == "QA-DA-1"

    management = ManagementService(storage)
    binding = grid.bindings["D3"]
    record = management.get_record(binding.table, binding.key)
    management.save((RecordEdit(binding.table, binding.key, record.revision,
                                {"version_raw": "v4.56.6.44", "release_version_id": None}),))
    entry = service.history()[0]
    assert (entry.kind, entry.table, entry.target, entry.field, entry.before, entry.after) == (
        "수정", "equipment_version", "QA-DA-1", "SW Ver", "v4.56.6.43", "v4.56.6.44")
    assert (entry.workbook, entry.sheet, entry.cell) == ("가상.xlsx", SHEET, "D3") and entry.at is not None
    assert service.sheet_grid(receipt.batch_id).bindings["D3"].current == "v4.56.6.44"  # 원본 셀은 그대로, 현재값은 수정값


def test_create_group_validates_name_category_and_parent_and_records_history(storage):
    service = DataAdminService(storage)
    groups, _ = service.equipment_overview()
    c4 = next(g for g in groups if g.name == "M-C4")
    created = service.create_group("M-C4 QA개조", "POLY", c4.group_id)
    assert created.parent_id == c4.group_id and created.parent_name == "M-C4"
    groups, _ = service.equipment_overview()
    assert any(g.group_id == created.group_id and g.parent_id == c4.group_id for g in groups)
    for args, message in ((("m-c4 qa개조", "POLY", None), "이미 있는"), (("QA2", "OXIDE", c4.group_id), "같은 분류"),
                          (("", "POLY", None), "이름"), (("QA3", "HPA", None), "분류")):
        with pytest.raises((ValueError, StorageValidationError), match=message):
            service.create_group(*args)
    entry = next(e for e in service.history() if e.table == "equipment_group")
    assert entry.kind == "등록" and entry.target == "M-C4 QA개조"
    family = next(f for f in service.release_families() if f.group_id == c4.group_id)
    assert (created.group_id, "M-C4 QA개조") in family.variants  # 개조형은 상위 계열 아래에 보인다


def test_delete_rows_removes_one_equipment_observation_and_leaves_marks_and_history(storage):
    receipt = _save(storage, "QA-DA-DEL")
    service = DataAdminService(storage)
    grid = service.sheet_grid(receipt.batch_id)
    sid = grid.owners["D3"]
    assert grid.owners["B3"] == grid.owners["C3"] == sid  # 같은 행의 칸은 같은 설비 현황
    assert service.delete_rows(receipt.batch_id, [sid]) == 1
    after = service.sheet_grid(receipt.batch_id)
    assert "D3" not in after.bindings and after.deleted["D3"] == "삭제됨 · QA-DA-DEL"
    assert after.cells["D3"] == "v4.56.6.43"  # 가져온 원본 칸은 남는다
    assert all(s.batch_id != receipt.batch_id for s in service.status_sheets())
    _, equipment = service.equipment_overview()
    assert next(e for e in equipment if e.code == "QA-DA-DEL").snapshot_count == 0  # 설비 호기는 남긴다
    with pytest.raises(StorageValidationError, match="이미 지웠거나"):
        service.delete_rows(receipt.batch_id, [sid])
    entry = next(e for e in service.history() if e.kind == "삭제")
    assert (entry.table, entry.target, entry.field, entry.after) == ("equipment_snapshot", "QA-DA-DEL", "현황표 행", "삭제")
    assert (entry.workbook, entry.sheet, entry.cell) == ("가상.xlsx", SHEET, "B3~D3") and "v4.56.6.43" in entry.before


def test_registered_site_and_new_group_appear_in_query_choices(storage):
    from setron.ui.database_source import load_dashboard_source

    service = DataAdminService(storage)
    assert service.add_site(" 용인QA ") == "용인QA" and "용인QA" in service.sites()
    for name in ("용인qa", "화성"):
        if name == "화성":
            _save(storage, "QA-DA-SITE")  # 관측된 Site와 같은 이름도 거부
        with pytest.raises(StorageValidationError, match="이미 있는"):
            service.add_site(name)
    assert next(e for e in service.history() if e.table == "site").target == "용인QA"
    groups, _ = service.equipment_overview()
    c4 = next(g for g in groups if g.name == "M-C4")
    base = service.create_group("QA신규", "OXIDE")
    variant = service.create_group("M-C4 QA2", "POLY", c4.group_id)
    source = load_dashboard_source(storage)
    assert "용인QA" in [s.label for s in source.catalog.sites]
    option = next(o for o in source.catalog.groups if o.label == "QA신규")
    assert option.key == f"group-{base.group_id}" and option.category == "OXIDE"
    categories = [o.category for o in source.catalog.groups]
    assert categories == sorted(categories, key=["POLY", "OXIDE", "KCTC"].index)  # 같은 분류 뒤에 들어간다
    bindings = source.bindings
    assert base.group_id in bindings[option.key] and variant.group_id in bindings["c4"]  # 개조형은 상위 설비군에 묶인다
