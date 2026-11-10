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
