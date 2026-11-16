"""관리 계층 변경이 모든 기준일의 DB 자료와 관계를 함께 갱신하는지 검증한다."""

import os
from datetime import date

import pytest

from setron.features.data_admin import DataAdminService, registered_lines
from setron.features.hierarchy import area_labels, category_labels, line_parts, model_aliases
from setron.features.storage.validation import StorageValidationError
from test_schema_mysql import schema_database, storage  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="전용 MySQL 테스트 DB 필요")


def observations(storage, site, line, code, model, *, area="MEMORY"):
    """한 설비의 서로 다른 두 기준일 관측을 실제 관리 입력 경로로 만든다."""
    admin = DataAdminService(storage)
    result = []
    for day in (date(2026, 10, 1), date(2026, 11, 1)):
        batch = admin.create_sheet(site, day, area)
        result.append(admin.add_row(batch, {"equipment_code": code, "group_name": model,
                                           "line": line, "version_raw": "v1"}, None))
    return result


def test_managed_labels_keep_codes_and_refuse_deleting_used_values(storage):
    admin = DataAdminService(storage)
    category = admin.add_category("사용자 설비 분류")
    area = admin.add_area("사용자 위치 분류")
    assert len(category) == 10 and category.startswith("C")
    assert len(area) <= 20 and area.startswith("A")
    group = admin.create_group("분류 검증 모델", category)
    admin.add_site("분류 검증 Site")
    admin.add_line("분류 검증 Site", "CUSTOM", area)
    ids = observations(storage, "분류 검증 Site", "CUSTOM", "CUSTOM-CODES", "분류 검증 모델", area=area)
    admin.rename_category(category, "설비 분류 새 이름")
    admin.rename_area(area, "위치 분류 새 이름")
    with storage.transaction() as tx:
        assert category_labels(tx)[category] == "설비 분류 새 이름"
        assert area_labels(tx)[area] == "위치 분류 새 이름"
        assert tx.get("equipment_group", {"group_id": group.group_id})["category"] == category
        assert next(e for e in registered_lines(tx) if e["line"] == "CUSTOM")["fab"] == area
        assert all(tx.get("equipment_snapshot", {"snapshot_id": sid})["production_area"] == area for sid in ids)
    with pytest.raises(StorageValidationError, match="사용 중"):
        admin.delete_category(category)
    with pytest.raises(StorageValidationError, match="사용 중"):
        admin.delete_area(area)
    unused = admin.add_category("안 쓰는 분류")
    admin.delete_category(unused)
    assert unused not in admin.hierarchy_options()["categories"]
    with pytest.raises(StorageValidationError, match="등록된"):
        admin.create_group("미등록 코드 모델", "C000000000")


def test_custom_subline_rename_reparent_and_move_update_all_dates(storage):
    admin = DataAdminService(storage)
    admin.create_group("이동 모델", "POLY")
    for site in ("출발 Site", "도착 Site"):
        admin.add_site(site)
    admin.add_line("출발 Site", "P1", "MEMORY")
    admin.add_line("출발 Site", "임의 세부라인", "MEMORY", "P1")
    ids = observations(storage, "출발 Site", "임의 세부라인", "MOVE-ALL-DATES", "이동 모델")
    assert admin.rename_line("출발 Site", "임의 세부라인", "새 세부라인") == 2
    assert admin.rename_line("출발 Site", "P1", "상위 새 이름") == 0
    assert next(e for e in admin.lines() if e.line == "새 세부라인").parent_line == "상위 새 이름"
    with pytest.raises(StorageValidationError, match="세부 Line"):
        admin.delete_line("출발 Site", "상위 새 이름")
    assert admin.set_line_fab("출발 Site", "상위 새 이름", "FOUNDRY") == 2
    assert admin.move_line("출발 Site", "상위 새 이름", "도착 Site", "MEMORY") == 2
    with storage.transaction() as tx:
        for sid in ids:
            value = tx.get("equipment_snapshot", {"snapshot_id": sid})
            assert (value["site"], value["line"], value["production_area"]) == ("도착 Site", "새 세부라인", "MEMORY")
        entries = registered_lines(tx)
        assert line_parts("도착 Site", "새 세부라인", entries) == ("상위 새 이름", "새 세부라인")
    admin.add_line("도착 Site", "새 상위", "FOUNDRY")
    assert admin.reparent_line("도착 Site", "새 세부라인", "새 상위") == 2
    assert next(e for e in admin.lines() if e.line == "새 세부라인").fab == "FOUNDRY"


def test_parent_line_merge_keeps_descendants_and_transaction_rolls_back(storage):
    admin = DataAdminService(storage)
    admin.add_site("병합 Site")
    admin.add_line("병합 Site", "이전 Line", "MEMORY")
    admin.add_line("병합 Site", "하위 A", "MEMORY", "이전 Line")
    admin.add_line("병합 Site", "대상 Line", "FOUNDRY")
    with pytest.raises(StorageValidationError):
        admin.apply_locations([{"kind": "line", "site": "병합 Site", "old": "이전 Line", "new": "대상 Line"},
                               {"kind": "line", "site": "병합 Site", "old": "없는 Line", "new": "실패"}])
    assert next(e for e in admin.lines() if e.line == "하위 A").parent_line == "이전 Line"
    admin.apply_locations([{"kind": "line", "site": "병합 Site", "old": "이전 Line", "new": "대상 Line"}])
    child = next(e for e in admin.lines() if e.line == "하위 A")
    assert (child.parent_line, child.fab) == ("대상 Line", "FOUNDRY")
    with pytest.raises(StorageValidationError, match="순환"):
        admin.reparent_line("병합 Site", "대상 Line", "하위 A")


def test_legacy_line_hierarchy_is_materialized_once_and_root_override_persists(storage):
    admin = DataAdminService(storage)
    admin.add_site("기존 Site")
    with storage.transaction() as tx:
        value = {"site": "기존 Site", "line": "P1D", "fab": "MEMORY"}
        tx.update("app_setting", {"setting_key": "registered_lines.v1"},
                  {"setting_value": {"lines": [*registered_lines(tx), value]}})
    assert next(e for e in admin.lines() if e.line == "P1D").parent_line == "P1"
    admin.rename_line("기존 Site", "P1", "사용자 상위")
    with storage.transaction() as tx:
        assert line_parts("기존 Site", "P1D", registered_lines(tx)) == ("사용자 상위", "P1D")
    admin.reparent_line("기존 Site", "P1D", None)
    admin.set_line_fab("기존 Site", "P1D", "FOUNDRY")
    with storage.transaction() as tx:
        assert line_parts("기존 Site", "P1D", registered_lines(tx)) == ("P1D", None)


def test_builtin_group_rename_preserves_ids_aliases_and_all_snapshot_names(storage):
    admin = DataAdminService(storage)
    group = admin.create_group("M-C4", "POLY")
    admin.add_site("모델 변경 Site")
    admin.add_line("모델 변경 Site", "L1", "MEMORY")
    ids = observations(storage, "모델 변경 Site", "L1", "RENAME-MODEL", "M-C4")
    assert admin.update_groups([(group.group_id, "새 모델명", None)]) == 1
    with storage.transaction() as tx:
        assert model_aliases(tx)["M-C4"] == group.group_id
        assert tx.get("equipment_group", {"group_id": group.group_id})["group_name"] == "새 모델명"
        assert all(tx.get("equipment_snapshot", {"snapshot_id": sid})["model_name"] == "새 모델명" for sid in ids)
    with pytest.raises(StorageValidationError, match="이전 이름"):
        admin.create_group("M-C4", "POLY")


def test_group_reparent_checks_cycles_and_existing_software_owner(storage):
    admin = DataAdminService(storage)
    first = admin.create_group("소속 A", "OXIDE")
    second = admin.create_group("소속 B", "OXIDE")
    child = admin.create_group("소속 하위", "OXIDE", first.group_id)
    admin.add_site("소속 Site")
    admin.add_line("소속 Site", "O1", "MEMORY")
    observations(storage, "소속 Site", "O1", "PARENT-FAMILY", "소속 하위")
    with pytest.raises(StorageValidationError, match="기존 SW"):
        admin.set_group_parent(child.group_id, second.group_id)
    with pytest.raises(StorageValidationError, match="순환"):
        admin.set_group_parent(first.group_id, child.group_id)


def test_cross_site_line_move_refuses_split_equipment_history(storage):
    admin = DataAdminService(storage)
    admin.create_group("분리 이력 모델", "KCTC")
    admin.add_site("분리 이력 출발")
    admin.add_site("분리 이력 도착")
    admin.add_line("분리 이력 출발", "L1", "MEMORY")
    admin.add_line("분리 이력 출발", "L2", "MEMORY")
    ids = observations(storage, "분리 이력 출발", "L1", "SPLIT-HISTORY", "분리 이력 모델")
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": ids[0]}, {"line": "L2"})
    with pytest.raises(StorageValidationError, match="다른 Line"):
        admin.move_line("분리 이력 출발", "L1", "분리 이력 도착", "MEMORY")
    with storage.transaction() as tx:
        assert all(tx.get("equipment_snapshot", {"snapshot_id": sid})["site"] == "분리 이력 출발" for sid in ids)


def test_direct_row_rejects_area_conflict_with_registered_hierarchy(storage):
    admin = DataAdminService(storage)
    admin.create_group("충돌 검증 모델", "POLY")
    admin.add_site("충돌 검증 Site")
    admin.add_line("충돌 검증 Site", "관리 Line", "MEMORY")
    batch = admin.create_sheet("충돌 검증 Site", date(2026, 11, 16))
    with pytest.raises(StorageValidationError, match="위치 분류와 다릅니다"):
        admin.add_row(batch, {"equipment_code": "AREA-CONFLICT", "group_name": "충돌 검증 모델",
                              "line": "관리 Line", "production_area": "FOUNDRY", "version_raw": "v1"}, None)
    assert not admin.sheet_rows(batch).rows


def test_display_labels_cannot_shadow_another_code_or_reserved_area_alias(storage):
    admin = DataAdminService(storage)
    admin.rename_area("FOUNDRY", "주조 공정")
    admin.rename_category("OXIDE", "산화 공정")
    for method, code, name in ((admin.rename_area, "MEMORY", "FOUNDRY"),
                               (admin.rename_area, "MEMORY", "파운드리"),
                               (admin.rename_category, "POLY", "OXIDE")):
        with pytest.raises(StorageValidationError, match="내부 코드나 예약 이름"):
            method(code, name)
    with pytest.raises(StorageValidationError, match="예약 이름"):
        admin.add_area("메모리")
    admin.rename_area("FOUNDRY", "Foundry")
    admin.rename_category("OXIDE", "OXIDE")
