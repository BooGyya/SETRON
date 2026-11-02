"""실제 공통 MySQL 5.6.19 스키마에서 BE-002의 저장·재조회를 검증한다."""

import os
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import func, select

from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.storage.service import StorageService
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.database import DatabaseSaveError
from test_schema_mysql import schema_database  # 공통 스키마 생성·안전한 정리 fixture 재사용

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


def source(sheet="현황", cell="A1", field="version_raw"):
    """테스트용 필드 출처를 생성한다.

    param
    - sheet: 원본 시트 이름. 기본값은 '현황'.
    - cell: 원본 셀 주소. 기본값은 'A1'.
    - field: 출처를 연결할 필드 이름. 기본값은 'version_raw'.

    return
    - 필드 이름에 시트·셀 위치를 연결한 출처 객체.
    """
    return {field: {"sheet": sheet, "cells": [cell]}}


@pytest.fixture(scope="module")
def catalog(schema_database):
    """DB 통합 테스트에서 공유할 가상 모델·계열·릴리즈와 입력 작업을 등록한다.

    M-O5 파생형과 C3 UI별 계열을 포함하며 실제 업무 자료는 사용하지 않는다.

    param
    - schema_database: 공통 스키마를 적용한 전용 테스트 Database.

    return
    - 저장 서비스, 입력 작업 행, 모델 ID 목록, 계열 ID 목록, 릴리즈 ID 목록의 튜플.
    """
    storage = StorageService(schema_database)
    groups, families, releases = {}, {}, {}
    raw = {sheet: {f"A{number}": f"원문 {number}😀" for number in range(1, 9)} for sheet in ("현황", "추가")}
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": raw})
        for name, category, parent in (("M-O2", "OXIDE", None), ("M-O3", "OXIDE", None),
                ("C3", "POLY", None), ("M-O5", "OXIDE", None), ("M-O5 Prime", "OXIDE", "M-O5"),
                ("Inline O2", "KCTC", None)):
            groups[name] = tx.insert("equipment_group", {"group_name": name, "category": category,
                "parent_group_id": groups[parent] if parent else None})["group_id"]
        for name, model, ui in (("O2", "M-O2", "INTEGRATED"), ("O3", "M-O3", "INTEGRATED"),
                ("C3 구UI", "C3", "LEGACY"), ("C3 통합UI", "C3", "INTEGRATED"), ("KCTC O2", "Inline O2", None)):
            families[name] = tx.insert("version_family", {"group_id": groups[model], "family_name": name,
                "ui_type": ui})["family_id"]
            for version in ("v1", "v3"):
                releases[name, version] = tx.insert("release_version", {"family_id": families[name],
                    "version_raw": version, "batch_id": batch["batch_id"]})["release_version_id"]
    return storage, batch, groups, families, releases


def mixed(catalog, code):
    """PM1·2에 O3, PM3·4에 O2를 배정한 혼합 설비 현황을 생성한다.

    param
    - catalog: 테스트용 저장 서비스·입력 작업·모델·계열·릴리즈 등록 정보.
    - code: 생성할 현황의 호기 코드.

    return
    - 가상 혼합 설비의 SnapshotInput.
    """
    _, batch, groups, families, releases = catalog
    return SnapshotInput(equipment_code=code, base_date=date(2026, 10, 27), site="화성", line="C57",
        model_name="O3/O2 혼합 원문", batch_id=batch["batch_id"], source_reference=source(field="site"),
        software=tuple(SoftwareInput(family_id=families[family], version_raw="v1",
            release_version_id=releases[family, "v1"], source_reference=source(cell=f"A{start}"),
            pms=tuple(PmInput(pm_name=f"PM{number}", group_id=groups[model], model_name_raw=model,
                source_reference=source(cell=f"A{number}")) for number in (start, start + 1)))
            for family, model, start in (("O3", "M-O3", 1), ("O2", "M-O2", 3))))


def row_count(storage, name, **filters):
    """전용 테스트 DB에서 검색 조건에 맞는 행 수를 조회한다.

    param
    - storage: 테스트 DB에 연결된 StorageService.
    - name: 행 수를 조회할 테이블 이름.
    - filters: 컬럼명과 값으로 지정하는 검색 조건.

    return
    - 모든 검색 조건을 만족하는 행 수.
    """
    with storage.transaction() as tx:
        table = tx.repository.table(name)
        return tx.session.scalar(select(func.count()).select_from(table).filter_by(**filters))


def test_mixed_equipment_saves_two_families_four_pms_and_original_sources(catalog):
    """혼합 설비의 SW 두 행·PM 네 위치와 원문·출처가 저장 후에도 유지되는지 확인한다.

    param
    - catalog: 테스트용 저장 서비스·입력 작업·모델·계열·릴리즈 등록 정보.
    """
    storage, batch, _, _, _ = catalog
    value = mixed(catalog, "BE002-MIXED")
    service = EquipmentService(storage)
    saved, = service.save_snapshots((value,))
    loaded = service.get_snapshot(value.equipment_code, value.base_date)
    assert loaded == saved
    assert loaded.group_id is None
    assert loaded.production_area is None and loaded.production_area_label == "미확인"
    assert loaded.model_name == value.model_name
    assert [(sw.version_raw, [pm.pm_name for pm in sw.pms]) for sw in loaded.software] == [
        ("v1", ["PM1", "PM2"]), ("v1", ["PM3", "PM4"])]
    assert loaded.pm_mismatch_families == ()
    assert loaded.software[0].pms[0].source_reference == source()
    assert row_count(storage, "equipment_version", snapshot_id=saved.snapshot_id) == 2
    assert row_count(storage, "equipment_pm", snapshot_id=saved.snapshot_id) == 4
    with storage.transaction() as tx:
        assert tx.get("import_batch", {"batch_id": batch["batch_id"]}) == batch


def test_same_equipment_and_pm_positions_connect_across_dates_without_filling_gaps(catalog):
    """날짜별 호기·PM 연결과 당시 정보를 보존하고 기록이 없는 날짜는 비워두는지 확인한다.

    param
    - catalog: 테스트용 저장 서비스·입력 작업·모델·계열·릴리즈 등록 정보.
    """
    storage, _, _, _, releases = catalog
    value = mixed(catalog, "BE002-HISTORY")
    next_sw = replace(value.software[0], version_raw="v3", release_version_id=releases["O3", "v3"])
    later = replace(value, base_date=date(2026, 10, 29), site="평택", line="P57", model_name="개조 후 원문",
                    modification_type="CIP", software=(next_sw, value.software[1]))
    service = EquipmentService(storage)
    first, second = service.save_snapshots((value, later))
    assert first.equipment_id == second.equipment_id
    assert first.snapshot_id != second.snapshot_id
    before = service.get_snapshot(value.equipment_code, value.base_date)
    after = service.get_snapshot(value.equipment_code, later.base_date)
    assert (before.site, before.line, before.modification_type) == ("화성", "C57", None)
    assert (after.site, after.line, after.model_name, after.modification_type) == ("평택", "P57", "개조 후 원문", "CIP")
    assert before.software[0].pms[0].pm_name == after.software[0].pms[0].pm_name == "PM1"
    assert before.software[0].version_raw == "v1" and after.software[0].version_raw == "v3"
    assert service.get_snapshot(value.equipment_code, date(2026, 10, 28)) is None
    assert service.get_snapshot("NOT-SAVED", value.base_date) is None
    assert row_count(storage, "equipment", equipment_id=first.equipment_id) == 1


def test_same_family_pm_mismatch_is_nonblocking_and_unregistered_raw_is_preserved(catalog):
    """같은 계열 PM의 버전 차이를 허용하고 미등록 원문과 불일치 표시를 보존하는지 확인한다.

    param
    - catalog: 테스트용 저장 서비스·입력 작업·모델·계열·릴리즈 등록 정보.
    """
    storage, _, groups, families, _ = catalog
    base = mixed(catalog, "BE002-MISMATCH")
    value = replace(base, software=tuple(SoftwareInput(family_id=families["O2"], version_raw=raw,
        source_reference=source(), pms=(PmInput(pm_name=f"PM{number}", group_id=groups["M-O2"],
            source_reference=source()),)) for number, raw in enumerate(("v1", " V1_d5 "), 1)))
    saved, = EquipmentService(storage).save_snapshots((value,))
    assert saved.pm_mismatch_families == (families["O2"],)
    assert [sw.version_raw for sw in saved.software] == ["v1", " V1_d5 "]
    assert all(sw.release_version_id is None for sw in saved.software)


def test_exact_duplicate_sw_and_pm_preserve_sources_across_sheets(catalog):
    """여러 시트의 중복 SW·PM을 한 번만 저장하고 모든 출처를 보존하는지 확인한다.

    param
    - catalog: 테스트용 저장 서비스·입력 작업·모델·계열·릴리즈 등록 정보.
    """
    storage, *_ = catalog
    base = mixed(catalog, "BE002-DEDUP")
    first = base.software[0]
    duplicate = replace(first, source_reference=source("추가", "A5"),
        pms=(replace(first.pms[0], source_reference=source("추가", "A6")),))
    saved, = EquipmentService(storage).save_snapshots((replace(base, software=(first, duplicate)),))
    assert len(saved.software) == 1 and len(saved.software[0].pms) == 2
    assert saved.software[0].source_reference == {"version_raw": [source()["version_raw"], source("추가", "A5")["version_raw"]]}
    assert saved.software[0].pms[0].source_reference["version_raw"][1] == source("추가", "A6")["version_raw"]


@pytest.mark.parametrize("problem", ["release-family", "model-category", "source", "missing-model"])
def test_invalid_later_snapshot_rolls_back_the_entire_batch(catalog, problem):
    """후속 현황의 연결 정보나 출처가 잘못되면 앞선 현황까지 저장을 취소하는지 확인한다.

    param
    - catalog: 테스트용 저장 서비스·입력 작업·모델·계열·릴리즈 등록 정보.
    - problem: 재현할 오류 유형. 릴리즈 계열·모델 설비군·출처·미등록 모델 중 하나.
    """
    storage, _, groups, families, releases = catalog
    first = mixed(catalog, "BE002-ROLLBACK-FIRST-" + problem)
    second = mixed(catalog, "BE002-ROLLBACK-SECOND-" + problem)
    software = second.software[0]
    if problem == "release-family":
        software = replace(software, release_version_id=releases["O2", "v1"])
    elif problem == "model-category":
        software = replace(software, family_id=families["KCTC O2"], release_version_id=None)
    elif problem == "source":
        software = replace(software, source_reference=source(cell="Z999"))
    else:
        software = replace(software, pms=(replace(software.pms[0], group_id=4294967295),))
    second = replace(second, software=(software, second.software[1]))
    with pytest.raises((StorageValidationError, LookupError)):
        EquipmentService(storage).save_snapshots((first, second))
    for value in (first, second):
        assert EquipmentService(storage).get_snapshot(value.equipment_code, value.base_date) is None
        with storage.transaction() as tx:
            assert EquipmentService(storage).repository.find_equipment(tx, value.equipment_code) is None


def test_existing_equipment_date_requires_review_and_keeps_original_data(catalog):
    """기존 호기·기준일의 현황을 보존하고 DB가 중복 호기 코드 등록을 차단하는지 확인한다.

    param
    - catalog: 테스트용 저장 서비스·입력 작업·모델·계열·릴리즈 등록 정보.
    """
    storage, *_ = catalog
    service = EquipmentService(storage)
    value = mixed(catalog, "BE002-EXISTING")
    saved, = service.save_snapshots((value,))
    with pytest.raises(StorageValidationError, match="이미"):
        service.save_snapshots((replace(value, site="평택"),))
    assert service.get_snapshot(value.equipment_code, value.base_date) == saved
    with pytest.raises(DatabaseSaveError):
        with storage.transaction() as tx:
            tx.insert("equipment", {"equipment_code": value.equipment_code})


def test_only_confirmed_m5_alias_is_resolved_and_prime_and_categories_stay_distinct(catalog):
    """M5 별칭을 연결하면서 Prime 모델과 서로 다른 설비군을 구분하는지 확인한다.

    param
    - catalog: 테스트용 저장 서비스·입력 작업·모델·계열·릴리즈 등록 정보.
    """
    storage, _, groups, *_ = catalog
    service = EquipmentService(storage)
    assert service.resolve_model("M5", "OXIDE")["group_id"] == groups["M-O5"]
    prime = service.resolve_model("M-O5 Prime", "OXIDE")
    assert prime["group_id"] != groups["M-O5"] and prime["parent_group_id"] == groups["M-O5"]
    assert service.resolve_model("Inline O2", "KCTC")["group_id"] == groups["Inline O2"]
    for name, category in (("M5", "KCTC"), ("M5 Prime", "OXIDE"), ("m5", "OXIDE"), ("M-O2", "KCTC")):
        with pytest.raises(StorageValidationError):
            service.resolve_model(name, category)


def test_c3_geometry_does_not_create_families_and_ui_families_remain_separate(catalog):
    """C3의 형상과 관계없이 같은 UI는 계열을 공유하고 구UI·통합UI는 구분하는지 확인한다.

    param
    - catalog: 테스트용 저장 서비스·입력 작업·모델·계열·릴리즈 등록 정보.
    """
    storage, batch, groups, families, releases = catalog
    values = tuple(SnapshotInput(equipment_code="BE002-C3", base_date=date(2026, 10, day), site="화성",
        model_name="C3 " + geometry, group_id=groups["C3"], geometry_type=geometry, production_area=area,
        batch_id=batch["batch_id"], source_reference=source(), software=(SoftwareInput(family_id=families[family],
            version_raw="v1", release_version_id=releases[family, "v1"], source_reference=source()),))
        for day, geometry, family, area in ((27, "5각", "C3 통합UI", None),
            (28, "6각", "C3 통합UI", "MEMORY"), (29, "6각", "C3 구UI", "FOUNDRY")))
    saved = EquipmentService(storage).save_snapshots(values)
    assert len({row.equipment_id for row in saved}) == 1
    assert [row.geometry_type for row in saved] == ["5각", "6각", "6각"]
    assert saved[0].software[0].family_id == saved[1].software[0].family_id != saved[2].software[0].family_id
    with storage.transaction() as tx:
        assert tx.get("version_family", {"family_id": families["C3 구UI"]})["ui_type"] == "LEGACY"
        assert tx.get("version_family", {"family_id": families["C3 통합UI"]})["ui_type"] == "INTEGRATED"


def test_database_blocks_cross_date_pm_link(catalog):
    """다른 현황의 SW에 PM을 연결하면 DB 외래키 제약이 저장을 차단하는지 확인한다.

    param
    - catalog: 테스트용 저장 서비스·입력 작업·모델·계열·릴리즈 등록 정보.
    """
    storage, batch, groups, *_ = catalog
    first, second = EquipmentService(storage).save_snapshots((mixed(catalog, "BE002-FK-A"), mixed(catalog, "BE002-FK-B")))
    with pytest.raises(DatabaseSaveError):
        with storage.transaction() as tx:
            tx.insert("equipment_pm", {"snapshot_id": second.snapshot_id,
                "equipment_version_id": first.software[0].equipment_version_id, "pm_name": "PM9",
                "group_id": groups["M-O3"], "batch_id": batch["batch_id"]})
    assert row_count(storage, "equipment_pm", snapshot_id=second.snapshot_id) == 4
