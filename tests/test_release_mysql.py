"""실제 MySQL 5.6.19의 릴리즈·Item 저장 및 원본 보존 검증."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from threading import Barrier

import pytest
from sqlalchemy import func, select

from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.schemas import ItemInput, ItemLinkInput, ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.storage.service import StorageService
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.database import DatabaseSaveError
from test_schema_mysql import schema_database  # 기존 빈 DB 확인·스키마 생성·정리 절차 재사용

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


@pytest.fixture
def release_data(schema_database):
    """각 테스트에서 사용할 가상 모델·계열·노트와 Excel 원본을 등록한다.

    :param schema_database: 공통 스키마를 적용한 전용 테스트 Database.
    :return: 저장 서비스·릴리즈 서비스·원본 입력 작업·모델·계열·노트 등록 정보.
    """
    storage = StorageService(schema_database)
    groups, families, notes = {}, {}, {}
    raw = {"릴리즈": {"A1": " v1_n4 ", "B1": "2026/10/27", "C1": "원본 내용 A😀",
        "C2": "원본 내용 B", "D1": 7, "D2": 7, "E1": 123},
        "추가": {"A1": None, "B1": False}}
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": raw})
        for name, category, parent in (("O2", "OXIDE", None), ("O2 Plus", "OXIDE", "O2"),
                ("C3", "POLY", None), ("Inline O2", "KCTC", None)):
            groups[name] = tx.insert("equipment_group", {"group_name": name, "category": category,
                "parent_group_id": groups.get(parent)})["group_id"]
        for name, model, ui in (("O2", "O2", "INTEGRATED"), ("O2 분기", "O2", "INTEGRATED"),
                ("C3 구UI", "C3", "LEGACY"), ("C3 통합UI", "C3", "INTEGRATED")):
            families[name] = tx.insert("version_family", {"family_name": name, "group_id": groups[model],
                "ui_type": ui})["family_id"]
        for model in ("O2", "C3"):
            notes[model] = tx.insert("release_note", {"group_id": groups[model], "batch_id": batch["batch_id"]})["release_note_id"]
    return {"storage": storage, "service": ReleaseService(storage), "batch": batch,
            "groups": groups, "families": families, "notes": notes}


def excel_release(data):
    """가상 Excel 원본에 연결된 릴리즈 등록 요청을 생성한다.

    :param data: release_data가 준비한 등록 정보.
    :return: 원문·출시일·확인된 순서의 출처를 포함한 ReleaseInput.
    """
    return ReleaseInput(family_id=data["families"]["O2"], version_raw=" v1_n4 ",
        batch_id=data["batch"]["batch_id"], release_note_id=data["notes"]["O2"],
        released_at=date(2026, 10, 27), version_order=1, source_reference={
            field: {"sheet": "릴리즈", "cells": [cell]} for field, cell in (
                ("version_raw", "A1"), ("released_at", "B1"), ("version_order", "E1"))})


def excel_item(data, number=1):
    """가상 Excel 원본에 연결된 Item 등록 요청을 생성한다.

    :param data: release_data가 준비한 등록 정보.
    :param number: 사용할 원본 Item 위치 번호. 1 또는 2.
    :return: 내용과 원본 번호의 출처를 포함한 ItemInput.
    """
    raw = data["batch"]["raw_data"]["릴리즈"]
    return ItemInput(content=raw[f"C{number}"], item_no=raw[f"D{number}"], batch_id=data["batch"]["batch_id"],
        release_note_id=data["notes"]["O2"], source_reference={
            field: {"sheet": "릴리즈", "cells": [f"{column}{number}"]} for field, column in (("content", "C"), ("item_no", "D"))})


def counts(storage):
    """전체 취소 여부를 비교하기 위해 입력 작업·릴리즈·Item·연결 행 수를 조회한다.

    :param storage: 전용 테스트 DB의 StorageService.
    :return: 관리 테이블별 행 수 객체.
    """
    with storage.transaction() as tx:
        return {name: tx.session.scalar(select(func.count()).select_from(tx.repository.table(name)))
                for name in ("import_batch", "release_version", "item", "release_version_item")}


def test_release_and_multiple_items_survive_reload_with_separate_originals(release_data):
    """한 릴리즈에 같은 번호의 여러 Item을 연결하고 원본과 함께 다시 조회하는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    data = release_data
    detail = data["service"].register_release(excel_release(data), (excel_item(data), excel_item(data, 2)))
    release_id = detail.release.current["release_version_id"]
    reloaded = ReleaseService(StorageService(data["storage"].database)).get_release(release_id)
    assert reloaded == detail
    assert detail.release.has_original and detail.release.source_type == "EXCEL"
    assert detail.release.current["version_raw"] == " v1_n4 "
    assert [(cell.field, cell.address, cell.value) for cell in detail.release.original_cells] == [
        ("version_raw", "A1", " v1_n4 "), ("released_at", "B1", "2026/10/27"), ("version_order", "E1", 123)]
    assert len(detail.items) == 2
    assert [item.item.current["item_no"] for item in detail.items] == [7, 7]
    assert len({item.item.current["item_id"] for item in detail.items}) == 2
    assert all(item.item_state is ItemState.APPLIED and item.target_scope is TargetScope.UNKNOWN for item in detail.items)


def test_release_and_item_edits_keep_source_values_locations_batch_and_time(release_data):
    """직접 수정한 관리값과 보관 원본·위치·입력 시각이 분리되는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    data = release_data
    service = data["service"]
    before = service.register_release(excel_release(data), (excel_item(data),))
    release_id = before.release.current["release_version_id"]
    item_id = before.items[0].item.current["item_id"]
    service.update_release(release_id, {"version_raw": " V3_d5 ", "released_at": date(2026, 10, 30), "version_order": 4})
    service.update_item(item_id, {"content": "앱에서 수정한 내용", "item_no": 8})
    after = service.get_release(release_id)
    assert after.release.current["version_raw"] == " V3_d5 "
    assert after.release.current["edited_fields"] == ["version_raw", "released_at", "version_order"]
    assert after.items[0].item.current["content"] == "앱에서 수정한 내용"
    assert after.items[0].item.current["edited_fields"] == ["content", "item_no"]
    for original, edited in ((before.release, after.release), (before.items[0].item, after.items[0].item)):
        assert original.original_cells == edited.original_cells
        assert original.imported_at == edited.imported_at
        assert original.current["batch_id"] == edited.current["batch_id"]
        assert original.current["source_reference"] == edited.current["source_reference"]
    with data["storage"].transaction() as tx:
        assert tx.get("import_batch", {"batch_id": data["batch"]["batch_id"]}) == data["batch"]


def test_manual_bundle_reuses_one_new_batch_and_edits_do_not_create_batches(release_data):
    """직접 등록한 묶음은 한 입력 작업을 공유하고 기존값 수정은 새 입력 작업을 만들지 않는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    data = release_data
    before_count = counts(data["storage"])["import_batch"]
    detail = data["service"].register_release(ReleaseInput(family_id=data["families"]["O2"], version_raw="manual"),
        (ItemInput(content="A"), ItemInput(content="B")))
    batch_ids = {detail.release.current["batch_id"], *(item.item.current["batch_id"] for item in detail.items)}
    assert len(batch_ids) == 1 and counts(data["storage"])["import_batch"] == before_count + 1
    assert not detail.release.has_original and detail.release.source_type == "MANUAL"
    assert all(not item.item.has_original for item in detail.items)
    data["service"].update_item(detail.items[0].item.current["item_id"], {"content": "수정"})
    assert counts(data["storage"])["import_batch"] == before_count + 1


def test_new_item_can_be_added_to_existing_release(release_data):
    """기존 릴리즈에 Item을 추가 등록하고 상세에서 조회할 수 있는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    service = release_data["service"]
    detail = service.register_release(excel_release(release_data))
    release_id = detail.release.current["release_version_id"]
    item = service.register_item(release_id, ItemInput(content="직접 추가한 Item"))
    assert item.source_type == "MANUAL" and not item.has_original
    assert service.get_item(item.current["item_id"]) == item
    assert service.get_release(release_id).items[0].item == item


def test_shared_item_edits_propagate_to_explicit_links_only(release_data):
    """공용 Item의 관리값은 연결된 릴리즈에 반영하고 연결되지 않은 후속 버전에는 전파하지 않는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    data = release_data
    service = data["service"]
    first = service.register_release(excel_release(data), (excel_item(data),))
    item_id = first.items[0].item.current["item_id"]
    second = service.register_release(ReleaseInput(family_id=data["families"]["O2"], version_raw="v2", version_order=2))
    third = service.register_release(ReleaseInput(family_id=data["families"]["O2"], version_raw="v3", version_order=3))
    second_id = second.release.current["release_version_id"]
    linked = service.save_item_links(second_id, (ItemLinkInput(item_id=item_id, item_state=ItemState.APPLIED,
        target_scope=TargetScope.LISTED, target_group_ids=(data["groups"]["O2 Plus"],)),))
    assert linked.items[0].target_group_ids == (data["groups"]["O2 Plus"],)
    service.update_item(item_id, {"content": "공용 내용 수정"})
    for release_id in (first.release.current["release_version_id"], second_id):
        item = service.get_release(release_id).items[0].item
        assert item.current["content"] == "공용 내용 수정"
        assert item.original_cells == first.items[0].item.original_cells
    assert service.get_release(third.release.current["release_version_id"]).items == ()


def test_link_updates_keep_omitted_items_and_all_three_states_visible(release_data):
    """일부 연결의 상태만 수정해도 요청에 없는 Item과 미포함·미확인 연결이 모두 남는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    data = release_data
    service = data["service"]
    detail = service.register_release(excel_release(data), (ItemInput(content="A"), ItemInput(content="B"), ItemInput(content="C")))
    ids = [item.item.current["item_id"] for item in detail.items]
    updated = service.save_item_links(detail.release.current["release_version_id"], (
        ItemLinkInput(item_id=ids[0], item_state=ItemState.NOT_APPLIED, target_scope=TargetScope.ALL),
        ItemLinkInput(item_id=ids[1], item_state=ItemState.UNKNOWN),
    ))
    assert [item.item_state for item in updated.items] == [ItemState.NOT_APPLIED, ItemState.UNKNOWN, ItemState.APPLIED]
    assert len(updated.items) == 3
    assert service.save_item_links(detail.release.current["release_version_id"], ()) == updated


def test_invalid_later_item_rolls_back_new_batch_release_items_and_links(release_data):
    """후속 Item의 원본 셀이 잘못되면 이번 요청에서 만든 모든 행이 취소되는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    data = release_data
    before = counts(data["storage"])
    bad_item = replace(excel_item(data), source_reference={"content": {"sheet": "릴리즈", "cells": ["Z999"]}})
    with pytest.raises(StorageValidationError, match="시트·셀"):
        data["service"].register_release(ReleaseInput(family_id=data["families"]["O2"], version_raw="rollback"),
            (ItemInput(content="먼저 저장할 Item"), bad_item))
    assert counts(data["storage"]) == before


def test_sql_failure_in_later_item_rolls_back_the_whole_registration(release_data):
    """후속 Item의 실제 DB 저장 오류가 앞선 등록까지 취소하는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    data = release_data
    before = counts(data["storage"])
    with pytest.raises(DatabaseSaveError):
        data["service"].register_release(ReleaseInput(family_id=data["families"]["O2"], version_raw="sql-rollback"),
            (ItemInput(content="먼저 저장할 Item"), ItemInput(content="가" * 22000)))
    assert counts(data["storage"]) == before


@pytest.mark.parametrize("problem", ["other-category", "missing-model"])
def test_invalid_model_on_later_link_restores_earlier_link_updates(release_data, problem):
    """후속 연결의 모델 검증에 실패하면 앞선 Item 상태 변경도 취소되는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    :param problem: 다른 설비군 또는 존재하지 않는 모델 오류 유형.
    """
    data = release_data
    service = data["service"]
    detail = service.register_release(excel_release(data), (ItemInput(content="A"), ItemInput(content="B")))
    item_ids = [item.item.current["item_id"] for item in detail.items]
    model_id = data["groups"]["Inline O2"] if problem == "other-category" else 4294967295
    with pytest.raises((StorageValidationError, LookupError)):
        service.save_item_links(detail.release.current["release_version_id"], (
            ItemLinkInput(item_id=item_ids[0], item_state=ItemState.NOT_APPLIED),
            ItemLinkInput(item_id=item_ids[1], item_state=ItemState.APPLIED,
                target_scope=TargetScope.LISTED, target_group_ids=(model_id,)),
        ))
    assert service.get_release(detail.release.current["release_version_id"]) == detail


def test_exact_version_uniqueness_and_failed_edit_preserve_existing_data(release_data):
    """버전 대소문자·공백을 보존하고 중복 등록·수정 실패 시 기존 기록을 유지하는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    data = release_data
    service = data["service"]
    registered = [service.register_release(ReleaseInput(family_id=data["families"]["O2"], version_raw=raw))
                  for raw in ("v1", "V1", "v1 ")]
    assert [detail.release.current["version_raw"] for detail in registered] == ["v1", "V1", "v1 "]
    before = counts(data["storage"])
    with pytest.raises(DatabaseSaveError):
        service.register_release(ReleaseInput(family_id=data["families"]["O2"], version_raw="v1"))
    assert counts(data["storage"]) == before
    release_id = registered[1].release.current["release_version_id"]
    with pytest.raises(DatabaseSaveError):
        service.update_release(release_id, {"version_raw": "v1"})
    assert service.get_release(release_id) == registered[1]


def test_editing_source_metadata_is_rejected_without_changing_originals(release_data):
    """원본 위치와 입력 작업을 바꾸려는 요청을 거부하고 관리값·원본을 보존하는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    service = release_data["service"]
    detail = service.register_release(excel_release(release_data), (excel_item(release_data),))
    release_id = detail.release.current["release_version_id"]
    item_id = detail.items[0].item.current["item_id"]
    with pytest.raises(StorageValidationError):
        service.update_release(release_id, {"source_reference": None})
    with pytest.raises(StorageValidationError):
        service.update_item(item_id, {"content": "변경", "batch_id": 1})
    assert service.get_release(release_id) == detail


def test_group_family_selection_uses_saved_releases_and_preserves_c3_ui_separation(release_data):
    """저장한 모델·계열 선택지를 제공하고 C3 구UI·통합UI를 분리하는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    data = release_data
    service = data["service"]
    assert service.list_families(data["groups"]["C3"]) == ()
    assert service.list_releases(data["families"]["C3 구UI"]) == ()
    for name in ("C3 구UI", "C3 통합UI"):
        service.register_release(ReleaseInput(family_id=data["families"][name], version_raw="same-raw"))
    assert data["groups"]["C3"] in {row["group_id"] for row in service.list_groups()}
    families = service.list_families(data["groups"]["C3"])
    assert {row["family_id"] for row in families} == {data["families"]["C3 구UI"], data["families"]["C3 통합UI"]}
    for name in ("C3 구UI", "C3 통합UI"):
        assert service.list_releases(data["families"][name])[0].current["family_id"] == data["families"][name]


def test_release_list_keeps_unknown_and_duplicate_orders_without_guessing(release_data):
    """확인된 순서만 정렬에 사용하고 미확인·중복 순서를 임의로 보정하지 않는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    data = release_data
    for raw, order in (("a", 2), ("b", 1), ("c", None), ("d", 1)):
        data["service"].register_release(ReleaseInput(family_id=data["families"]["O2"], version_raw=raw, version_order=order))
    rows = data["service"].list_releases(data["families"]["O2"])
    assert [row.current["version_raw"] for row in rows] == ["b", "d", "a", "c"]
    assert [row.current["version_order"] for row in rows] == [1, 1, 2, None]


def test_missing_originals_are_explicit_and_new_excel_records_require_sources(release_data):
    """기존 출처 누락은 원본 없음으로 반환하고 새 Excel 등록에는 출처를 요구하는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    data = release_data
    value = replace(excel_release(data), source_reference=None)
    with pytest.raises(StorageValidationError):
        data["service"].register_release(value)
    with data["storage"].transaction() as tx:
        legacy = tx.insert("release_version", {"family_id": value.family_id, "version_raw": value.version_raw,
            "batch_id": data["batch"]["batch_id"]})
    detail = data["service"].get_release(legacy["release_version_id"])
    assert not detail.release.has_original and detail.release.original_cells == ()


def test_multiple_source_sheets_keep_null_and_false_cell_values(release_data):
    """여러 시트의 원본 위치와 None·False 셀 값을 그대로 보존하는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    value = replace(excel_release(release_data), source_reference={"version_raw": [
        {"sheet": "릴리즈", "cells": ["A1"]}, {"sheet": "추가", "cells": ["A1", "B1"]}]})
    detail = release_data["service"].register_release(value)
    assert [cell.value for cell in detail.release.original_cells] == [" v1_n4 ", None, False]


def test_release_note_from_other_equipment_category_is_rejected(release_data):
    """다른 설비군의 릴리즈 노트를 연결하면 등록을 취소하는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    data = release_data
    before = counts(data["storage"])
    with pytest.raises(StorageValidationError, match="설비군"):
        data["service"].register_release(replace(excel_release(data), release_note_id=data["notes"]["C3"]))
    assert counts(data["storage"]) == before


def test_concurrent_item_edits_keep_both_changes_and_edited_fields(release_data):
    """같은 Item의 서로 다른 항목을 동시에 수정해도 값과 수정 표시가 유실되지 않는지 확인한다.

    :param release_data: 전용 DB에 준비한 가상 등록 정보.
    """
    data = release_data
    detail = data["service"].register_release(excel_release(data), (excel_item(data),))
    item_id = detail.items[0].item.current["item_id"]
    barrier = Barrier(2, timeout=5)

    def edit(changes):
        """동시 시작 시점을 맞춘 뒤 별도 트랜잭션에서 Item을 수정한다.

        :param changes: 한 작업에서 수정할 관리 항목과 값.
        :return: 해당 수정 요청의 Item 조회 결과.
        """
        barrier.wait()
        return data["service"].update_item(item_id, changes)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(edit, changes) for changes in ({"content": "동시 수정"}, {"item_no": 9})]
        for future in futures:
            future.result(timeout=10)
    item = data["service"].get_item(item_id)
    assert item.current["content"] == "동시 수정" and item.current["item_no"] == 9
    assert set(item.current["edited_fields"]) == {"content", "item_no"}
    assert item.original_cells == detail.items[0].item.original_cells
