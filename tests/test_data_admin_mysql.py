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
    for args, message in ((("m-c4 qa개조", "POLY", None), "개조형으로 등록돼"), (("M-C4", "POLY", None), "이미 있는"), (("QA2", "OXIDE", c4.group_id), "같은 분류"),
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
    journal = service.delete_rows(receipt.batch_id, [sid])
    assert isinstance(journal, int)
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


def test_restore_rows_brings_back_a_deleted_row_once(storage):
    receipt = _save(storage, "QA-DA-RES")
    service = DataAdminService(storage)
    sid = service.sheet_grid(receipt.batch_id).owners["D3"]
    journal = service.delete_rows(receipt.batch_id, [sid])
    assert service.restore_rows(journal) == 1
    grid = service.sheet_grid(receipt.batch_id)
    assert grid.bindings["D3"].current == "v4.56.6.43" and "D3" not in grid.deleted  # 되살리면 '삭제됨' 표시도 없어진다
    with pytest.raises(StorageValidationError, match="이미 되살린"):
        service.restore_rows(journal)
    assert next(e for e in service.history() if e.kind == "되돌리기").target == "QA-DA-RES"


def test_sheet_without_model_column_has_no_add_row_columns(storage):
    receipt = _save(storage, "QA-DA-ADD0")
    grid = DataAdminService(storage).sheet_grid(receipt.batch_id)
    assert grid.columns == {}  # 설비군(모델) 열을 알 수 없는 시트는 행 추가를 막는다 (화면의 [＋ 행 추가] 꺼짐)


def test_empty_sheet_then_add_row_and_release_version_item(storage):
    from setron.features.data_admin.service import SHEET_KEY

    service = DataAdminService(storage)
    service.add_site("QA빈현황")
    batch = service.create_sheet("QA빈현황", date(2026, 11, 2))
    sheet = next(s for s in service.status_sheets() if s.batch_id == batch)
    assert (sheet.site, sheet.base_date, sheet.equipment_count) == ("QA빈현황", date(2026, 11, 2), 0)
    with pytest.raises(StorageValidationError, match="이미 있습니다"):
        service.create_sheet("QA빈현황", date(2026, 11, 2))
    grid = service.sheet_grid(batch)
    assert grid.columns == {"B": "line", "C": "model_name", "D": "equipment_code", "E": "version_raw"}
    assert grid.cells["D1"] == "설비호기" and SHEET_KEY not in grid.sheet
    with pytest.raises(StorageValidationError, match="없습니다"):
        service.add_row(batch, {"model_name": "없는설비군", "equipment_code": "QA-E1", "version_raw": "v1"}, 2)
    sid = service.add_row(batch, {"line": "L1", "model_name": "M-C4", "equipment_code": "QA-E1", "version_raw": "v9.9.9"}, 2)
    grid = service.sheet_grid(batch)
    assert grid.owners["D2"] == sid and grid.bindings["E2"].current == "v9.9.9" and grid.bindings["B2"].current == "L1"
    assert next(s for s in service.status_sheets() if s.batch_id == batch).equipment_count == 1
    entry = next(e for e in service.history() if e.kind == "추가")
    assert (entry.target, entry.cell) == ("QA-E1", "B2~E2") and "QA빈현황" in entry.after
    with pytest.raises(StorageValidationError, match="이미 있습니다"):
        service.add_row(batch, {"model_name": "M-C4", "equipment_code": "QA-E1", "version_raw": "v1"}, 3)
    extra = service.add_row(batch, {"model_name": "M-C4", "equipment_code": "QA-E2", "version_raw": "v1"}, 3)
    service.delete_rows(batch, [extra], undo_add=True)  # 추가 취소는 '삭제됨' 표시를 남기지 않는다
    assert not service.sheet_grid(batch).deleted

    family = next(f for f in service.release_families() if f.family_id == _c4_family(storage))
    release = service.add_release_version(family.family_id, "v9.9.9", date(2026, 11, 3))
    with pytest.raises(StorageValidationError, match="이미 있는 버전"):
        service.add_release_version(family.family_id, "v9.9.9")
    with storage.transaction() as tx:
        linked = tx.get("equipment_version", {"equipment_version_id": grid.bindings["E2"].key["equipment_version_id"]})
    assert linked["release_version_id"] == release  # 같은 원문의 설비 SW를 새 버전에 잇는다
    item = service.add_release_item(release, "QA 새 Item", 1)
    rows = [r for r in service.release_rows(family.family_id) if r.release_version_id == release]
    assert [(r.item_id, r.content, r.applied_models, r.released_at) for r in rows] == [(item, "QA 새 Item", "전체", "2026-11-03")]


def _c4_family(storage):
    from setron.features.equipment.repository import EquipmentRepository

    repository = EquipmentRepository()
    with storage.transaction() as tx:
        group = next(g for g in repository.rows(tx, "equipment_group") if g["group_name"] == "M-C4")
        families = repository.rows(tx, "version_family", group_id=group["group_id"])
    return next(f["family_id"] for f in families if "파운드리" not in f["family_name"])


def test_apply_rows_adds_and_deletes_together_and_undo_rows_reverses_both(storage):
    service = DataAdminService(storage)
    service.add_site("QA일괄")
    batch = service.create_sheet("QA일괄", date(2026, 11, 4))
    first = service.add_row(batch, {"model_name": "M-C4", "equipment_code": "QA-B1", "version_raw": "v1"}, 2)
    result = service.apply_rows(batch, [({"line": "L2", "model_name": "M-C4", "equipment_code": "QA-B2",
                                          "version_raw": "v2"}, 3)], [first])
    grid = service.sheet_grid(batch)
    assert result["journal"] and len(result["added"]) == 1 and grid.owners.get("D3") == result["added"][0]
    assert "D2" not in grid.bindings and grid.deleted.get("D2") == "삭제됨 · QA-B1"
    with pytest.raises(StorageValidationError):  # 한 트랜잭션: 하나라도 틀리면 아무것도 바꾸지 않는다
        service.apply_rows(batch, [({"model_name": "없는설비군", "equipment_code": "QA-B3", "version_raw": "v3"}, 4)],
                           [result["added"][0]])
    assert service.sheet_grid(batch).owners.get("D3") == result["added"][0]
    service.undo_rows(batch, result["added"], result["journal"])
    grid = service.sheet_grid(batch)
    assert grid.owners.get("D2") and "D3" not in grid.owners and not grid.deleted


def test_release_rows_newest_first_reorder_and_delete(storage):
    service = DataAdminService(storage)
    family = _c4_family(storage)
    old = service.add_release_version(family, "v8.0.0.1", date(2026, 1, 1))
    new = service.add_release_version(family, "v8.0.0.2", date(2026, 2, 1))
    order = [r.version_raw for r in service.release_rows(family) if r.first_of_version]
    assert order.index("v8.0.0.2") < order.index("v8.0.0.1")  # 최신 버전이 위
    a = service.add_release_item(new, "첫째")
    b = service.add_release_item(new, "둘째")
    rows = [r for r in service.release_rows(family) if r.release_version_id == new]
    assert [(r.item_no, r.content) for r in rows] == [("1", "첫째"), ("2", "둘째")]  # 번호는 맨 뒤 다음
    service.reorder_items(new, [b, a])
    rows = [r for r in service.release_rows(family) if r.release_version_id == new]
    assert [(r.item_no, r.content) for r in rows] == [("1", "둘째"), ("2", "첫째")]
    with pytest.raises(StorageValidationError, match="바뀌었습니다"):
        service.reorder_items(new, [a])
    assert service.delete_release_rows(new, a).startswith("v8.0.0.2 · Item")
    assert [r.content for r in service.release_rows(family) if r.release_version_id == new] == ["둘째"]
    service.delete_release_rows(old)
    assert all(r.release_version_id != old for r in service.release_rows(family))
    kinds = [(e.kind, e.field) for e in service.history()[:4]]
    assert ("삭제", "릴리즈 버전") in kinds and ("삭제", "릴리즈 Item") in kinds and ("수정", "Item 순서") in kinds


def test_apply_release_in_one_transaction_and_undo_release_brings_deleted_rows_back(storage):
    service = DataAdminService(storage)
    family = _c4_family(storage)
    base = service.add_release_version(family, "v9.1.0.1", date(2026, 3, 1))
    first = service.add_release_item(base, "남길 Item")
    second = service.add_release_item(base, "지울 Item")
    old = service.add_release_version(family, "v9.1.0.0", date(2026, 2, 1))
    service.add_release_item(old, "버전과 함께 지울 Item")
    undo = service.apply_release(family, {
        "add_versions": [{"temp": "v", "version_raw": "v9.1.0.2", "released_at": "2026-04-01"}],
        "add_items": [{"temp": "a", "release": "v", "content": "새 버전 Item", "group_ids": None},
                      {"temp": "b", "release": base, "content": "기존 버전 새 Item", "group_ids": None}],
        "orders": {base: ["b", first, second]}, "delete_items": [(base, second)], "delete_versions": [old]})
    rows = service.release_rows(family)
    assert [(r.item_no, r.content) for r in rows if r.release_version_id == base] == [("1", "기존 버전 새 Item"), ("2", "남길 Item")]
    assert all(r.release_version_id != old for r in rows)
    added = next(r.release_version_id for r in rows if r.version_raw == "v9.1.0.2")
    assert undo["versions"] == [added] and len(undo["journals"]) == 2 and base in undo["orders"]
    with pytest.raises(StorageValidationError):  # 한 트랜잭션: 틀린 요청은 아무것도 바꾸지 않는다
        service.apply_release(family, {"add_versions": [{"temp": "x", "version_raw": "v9.1.0.3"}],
                                       "delete_items": [(base, 999999)]})
    assert all(r.version_raw != "v9.1.0.3" for r in service.release_rows(family))
    service.undo_release(undo)
    rows = service.release_rows(family)
    assert all(r.version_raw != "v9.1.0.2" for r in rows)  # 추가한 버전은 지운다
    restored = next(r.release_version_id for r in rows if r.version_raw == "v9.1.0.0")
    assert [r.content for r in rows if r.release_version_id == restored] == ["버전과 함께 지울 Item"]  # 지운 버전은 되살린다
    assert sorted(r.content for r in rows if r.release_version_id == base) == ["남길 Item", "지울 Item"]
    kinds = [(e.kind, e.after) for e in service.history()[:6]]
    assert ("되돌리기", "되살림") in kinds and ("삭제", "추가 취소") in kinds


# --- 11-11 6차: DB 값 기준 현황표 · 설비군 · Site · 라인 등록 관리 ------------------------------------------
def test_sheet_rows_show_db_values_with_source_cells_and_site_lines(storage):
    receipt = _save(storage, "QA-DA-ROWS")
    data = DataAdminService(storage).sheet_rows(receipt.batch_id)
    assert (data.site, data.base_date, data.workbook) == ("화성", date(2026, 10, 21), "가상.xlsx")
    row = next(r for r in data.rows if r.equipment_code == "QA-DA-ROWS")
    assert (row.line, row.group_name, row.model_name, row.production_area, row.version_raw) == (
        "13L", "M-C4", "M-C4", "MEMORY", "v4.56.6.43")
    assert row.sources == {"equipment_code": "C3", "line": "B3", "version_raw": "D3"}
    assert row.source_values["version_raw"] == "v4.56.6.43" and not row.has_pm
    assert "13L" in data.lines and {"POLY", "OXIDE", "KCTC"} >= {c for _, _, c in data.groups}
    service = DataAdminService(storage)
    service.add_site("QA타지")
    service.add_line("QA타지", "QT1", "MEMORY")
    data = service.sheet_rows(receipt.batch_id)
    assert data.elsewhere["qt1"] == ("QA타지",) and "13l" not in data.elsewhere  # 다른 Site에만 있는 라인 (잘못 넣기 안내)


def test_apply_rows_regroups_with_family_and_undo_rows_puts_it_back(storage):
    service = DataAdminService(storage)
    service.add_site("QA재분류")
    batch = service.create_sheet("QA재분류", date(2026, 11, 5))
    keep = service.add_row(batch, {"group_name": "M-C4", "equipment_code": "QA-RG1", "version_raw": "v1"}, None)
    moved = service.add_row(batch, {"group_name": "M-C4", "equipment_code": "QA-RG2", "version_raw": "v1"}, None)
    target = service.create_group("QA재분류군", "POLY")
    result = service.apply_rows(batch, regroup=[(moved, target.group_id)])
    c4 = next(g for g in service.equipment_overview()[0] if g.name == "M-C4")
    assert result == {"added": [], "journal": None, "regroup": [(moved, c4.group_id)]}
    rows = {r.snapshot_id: r for r in service.sheet_rows(batch).rows}
    assert (rows[moved].group_name, rows[moved].model_name, rows[moved].family_name) == ("QA재분류군",) * 3
    assert rows[keep].group_name == "M-C4" and not rows[keep].sources  # 행 번호 없이 더한 행은 원본 칸이 없다
    entry = service.history()[0]
    assert (entry.target, entry.before, entry.after) == ("설비 1대", "M-C4", "QA재분류군")
    service.undo_rows(batch, regroup=result["regroup"])
    back = next(r for r in service.sheet_rows(batch).rows if r.snapshot_id == moved)
    assert (back.group_name, back.model_name, back.family_name) == ("M-C4", "M-C4", rows[keep].family_name)
    with pytest.raises(StorageValidationError, match="쓰이고 있어"):
        service.delete_group(c4.group_id)


def test_new_group_gets_its_family_and_unused_group_can_be_renamed_or_deleted(storage):
    service = DataAdminService(storage)
    created = service.create_group("QA계열군", "KCTC")
    assert [f.name for f in service.release_families() if f.group_id == created.group_id] == ["QA계열군"]
    assert service.rename_group(created.group_id, "QA계열군2") == "QA계열군2"
    assert [f.name for f in service.release_families() if f.group_id == created.group_id] == ["QA계열군2"]
    assert service.delete_group(created.group_id) == "QA계열군2"
    assert all(f.group_id != created.group_id for f in service.release_families())
    c4 = next(g for g in service.equipment_overview()[0] if g.name == "M-C4")
    variant = service.create_group("M-C4 QA독립", "POLY", c4.group_id)
    assert not [f for f in service.release_families() if f.group_id == variant.group_id]  # 개조형은 상위 계열을 쓴다
    with pytest.raises(StorageValidationError, match="개조형으로 등록돼"):
        service.create_group("m-c4 qa독립", "POLY")
    assert service.set_independent(variant.group_id, True)
    assert next(g for g in service.equipment_overview()[0] if g.group_id == variant.group_id).independent
    with pytest.raises(StorageValidationError, match="기본형"):
        service.set_independent(c4.group_id, True)


def test_lines_are_registered_under_memory_or_foundry_and_judge_new_imports(storage):
    from setron.features.reference_data import default_area

    service = DataAdminService(storage)
    service.add_site("QA라인")
    assert service.add_line("QA라인", " QL1 ", "FOUNDRY") == "QL1"
    assert next(r for r in service.lines() if r.site == "QA라인") == module_line("QA라인", "QL1", "FOUNDRY", 0, True)
    assert default_area("QA라인", "QL1") == "FOUNDRY"  # 가져오기 판정에 바로 쓰인다
    for args, message in ((("QA라인", "ql1", "MEMORY"), "이미 있는"), (("없는Site", "X1", "MEMORY"), "없는 Site"),
                          (("QA라인", "X2", "HPA"), "메모리 또는 파운드리")):
        with pytest.raises(StorageValidationError, match=message):
            service.add_line(*args)
    assert service.rename_line("QA라인", "QL1", "QL2") == 0
    assert service.set_line_fab("QA라인", "QL2", "MEMORY") == 0 and default_area("QA라인", "QL2") == "MEMORY"
    service.delete_line("QA라인", "QL2")
    assert all(r.site != "QA라인" for r in service.lines())
    _save(storage, "QA-DA-LINE")
    with pytest.raises(StorageValidationError, match="설비 현황이 있어"):
        service.delete_line("화성", "13L")
    with pytest.raises(StorageValidationError):
        service.delete_site("화성")
    assert service.rename_site("QA라인", "QA라인2") == 0 and "QA라인2" in service.sites()
    service.delete_site("QA라인2")
    assert "QA라인2" not in service.sites()
    kinds = [e.kind for e in service.history()[:3]]
    assert kinds and all(kinds)


def module_line(*values):
    from setron.features.data_admin import LineRow

    return LineRow(*values)


def test_new_row_on_a_registered_foundry_line_gets_the_foundry_family(storage):
    service = DataAdminService(storage)
    service.add_site("QA파운드리")
    service.add_line("QA파운드리", "QF1", "FOUNDRY")
    service.add_line("QA파운드리", "QM1", "MEMORY")
    batch = service.create_sheet("QA파운드리", date(2026, 11, 6))
    foundry = service.add_row(batch, {"group_name": "M-C4", "line": "QF1", "equipment_code": "QA-F1", "version_raw": "v1"}, None)
    memory = service.add_row(batch, {"group_name": "M-C4", "line": "QM1", "equipment_code": "QA-M1", "version_raw": "v1"}, None)
    rows = {r.snapshot_id: r for r in service.sheet_rows(batch).rows}
    assert rows[foundry].production_area == "FOUNDRY" and "(파운드리)" in rows[foundry].family_name
    assert rows[memory].production_area == "MEMORY" and "(파운드리)" not in rows[memory].family_name
    # 같은 현황표의 메모리 설비 계열을 파운드리 새 행에 그대로 쓰지 않는다
    second = service.add_row(batch, {"group_name": "M-C4", "line": "QF1", "equipment_code": "QA-F2", "version_raw": "v1"}, None)
    assert "(파운드리)" in next(r for r in service.sheet_rows(batch).rows if r.snapshot_id == second).family_name
