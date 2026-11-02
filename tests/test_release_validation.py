"""릴리즈·Item 등록·수정 입력 검증 테스트."""

from dataclasses import replace
from datetime import date, datetime

import pytest

from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.schemas import ItemInput, ItemLinkInput, ReleaseInput
from setron.features.release.validation import prepare_changes, prepare_links, prepare_record
from setron.features.storage.validation import StorageValidationError


@pytest.mark.parametrize("changes", [
    {"family_id": None}, {"family_id": True}, {"family_id": 0}, {"family_id": "1"},
    {"version_raw": " "}, {"version_raw": "x" * 101}, {"batch_id": False}, {"release_note_id": -1},
    {"version_order": True}, {"version_order": 2147483648}, {"released_at": "2026-10-27"},
    {"released_at": datetime(2026, 10, 27)}, {"version_suffix": 1}, {"version_suffix": "x" * 51},
    {"item_mapping_status": "FIXED"}, {"item_mapping_status": []},
    {"edited_fields": ("batch_id",)}, {"edited_fields": ("version_raw", "version_raw")},
    {"edited_fields": ["version_raw"]}, {"source_reference": {"version_raw": {"sheet": "원본"}}},
])
def test_invalid_release_inputs_are_rejected(changes):
    """릴리즈 필수값·자료형·상태·수정 항목의 오류를 차단하는지 확인한다.

    param
    - changes: 잘못된 등록 항목과 값.
    """
    with pytest.raises(StorageValidationError):
        prepare_record(replace(ReleaseInput(family_id=1, version_raw="v1"), **changes))


@pytest.mark.parametrize("changes", [
    {"content": ""}, {"content": None}, {"content": "x" * 65536},
    {"item_no": "1"}, {"item_no": True}, {"item_no": -2147483649},
    {"batch_id": 0}, {"release_note_id": "1"}, {"edited_fields": ("item_id",)},
])
def test_invalid_item_inputs_are_rejected(changes):
    """Item 필수값·식별자·번호·수정 항목의 오류를 차단하는지 확인한다.

    param
    - changes: 잘못된 등록 항목과 값.
    """
    with pytest.raises(StorageValidationError):
        prepare_record(replace(ItemInput(content="원본 내용"), **changes))


def test_unknown_metadata_and_exact_version_originals_remain_unchanged():
    """미확인 메타데이터를 채우지 않고 버전 원문·출처·수정 항목을 보존하는지 확인한다."""
    source = {"version_raw": [{"sheet": "원본", "cells": ["A1"]}, {"sheet": "추가", "cells": ["B2"]}]}
    value = ReleaseInput(family_id=1, version_raw=" V1_n4 ", source_reference=source, edited_fields=("version_raw",))
    prepared = prepare_record(value)
    assert prepared["version_raw"] == " V1_n4 "
    assert prepared["released_at"] is None and prepared["version_order"] is None and prepared["batch_id"] is None
    assert prepared["edited_fields"] == ["version_raw"]
    prepared["source_reference"]["version_raw"][0]["cells"].append("Z9")
    assert source["version_raw"][0]["cells"] == ["A1"]


@pytest.mark.parametrize("table,changes", [
    ("release_version", {"release_version_id": 2}), ("release_version", {"family_id": 2}),
    ("release_version", {"batch_id": 2}), ("release_version", {"source_reference": None}),
    ("release_version", {"release_note_id": None}), ("release_version", {"edited_fields": []}),
    ("item", {"item_id": 2}), ("item", {"batch_id": 2}), ("item", {"source_reference": None}),
    ("item", {"is_enabled": False}), ("item", {"hidden": True}),
])
def test_source_identity_and_hidden_flags_cannot_be_edited(table, changes):
    """원본 정보·식별자·숨김 상태의 변경을 허용하지 않는지 확인한다.

    param
    - table: 수정 대상 관리 테이블.
    - changes: 편집할 수 없는 항목과 값.
    """
    current = prepare_record(ReleaseInput(family_id=1, version_raw="v1") if table == "release_version" else ItemInput(content="내용"))
    with pytest.raises(StorageValidationError):
        prepare_changes(table, current, changes)


def test_edit_marks_only_real_changes_and_keeps_previous_edit_history():
    """실제로 바뀐 필드만 표시하고 같은 값 저장·원문 복원 시에도 기존 수정 이력을 유지하는지 확인한다."""
    current = prepare_record(ReleaseInput(family_id=1, version_raw="v1", edited_fields=("released_at",)))
    changes = {"version_raw": "v3", "version_order": None}
    prepared = prepare_changes("release_version", current, changes)
    assert prepared["edited_fields"] == ["released_at", "version_raw"]
    assert current["version_raw"] == "v1" and changes == {"version_raw": "v3", "version_order": None}
    restored = prepare_changes("release_version", {**current, **prepared}, {"version_raw": "v1"})
    assert restored["edited_fields"] == ["released_at", "version_raw"]
    assert prepare_changes("release_version", current, {}) == {"edited_fields": ["released_at"]}


@pytest.mark.parametrize("changes", [{"content": " "}, {"item_no": True}])
def test_item_changes_are_validated_before_saving(changes):
    """Item 수정값도 등록과 같은 기준으로 검사하는지 확인한다.

    param
    - changes: 필수 내용 누락 또는 잘못된 번호 변경값.
    """
    with pytest.raises(StorageValidationError):
        prepare_changes("item", prepare_record(ItemInput(content="유지")), changes)


@pytest.mark.parametrize("changes", [
    {"item_id": True}, {"item_id": 0}, {"item_state": "FIXED"}, {"item_state": "APPLIED"},
    {"target_scope": "ALL"}, {"target_scope": TargetScope.LISTED},
    {"target_group_ids": (1,)}, {"target_scope": TargetScope.LISTED, "target_group_ids": [1]},
    {"target_scope": TargetScope.LISTED, "target_group_ids": (True,)},
    {"target_scope": TargetScope.LISTED, "target_group_ids": (1, 1)},
])
def test_invalid_item_link_inputs_are_rejected(changes):
    """Item 포함 상태·모델 범위·ID 목록의 오류를 차단하는지 확인한다.

    param
    - changes: 잘못된 Item 연결 항목과 값.
    """
    link = replace(ItemLinkInput(item_id=1, item_state=ItemState.APPLIED), **changes)
    with pytest.raises(StorageValidationError):
        prepare_links((link,))


def test_item_links_require_unique_ids_and_preserve_explicit_states():
    """중복 연결은 차단하고 명시한 포함 상태와 모델 목록은 변경하지 않는지 확인한다."""
    link = ItemLinkInput(item_id=1, item_state=ItemState.NOT_APPLIED, target_scope=TargetScope.LISTED, target_group_ids=(2, 3))
    assert prepare_links((link,)) == (link,)
    with pytest.raises(StorageValidationError):
        prepare_links((link, link))
    with pytest.raises(StorageValidationError):
        prepare_links([link])
    assert prepare_links(()) == ()


def test_confirmed_dates_and_duplicate_orders_are_not_reinterpreted():
    """확인된 날짜·순서를 그대로 저장 입력으로 전달하고 미확인 항목은 추정하지 않는지 확인한다."""
    first = prepare_record(ReleaseInput(family_id=1, version_raw="v1", released_at=date(2026, 10, 27), version_order=3))
    second = prepare_record(ReleaseInput(family_id=1, version_raw="v3", version_order=3))
    assert first["released_at"] == date(2026, 10, 27)
    assert first["version_order"] == second["version_order"] == 3
    assert second["released_at"] is None


@pytest.mark.parametrize("value", [
    ItemInput(content="잘못된 문자\ud800"),
    ReleaseInput(family_id=1, version_raw="v1\ud800"),
    ReleaseInput(family_id=1, version_raw="v1", version_suffix="\udfff"),
])
def test_text_that_cannot_be_encoded_is_reported_as_input_error(value):
    """DB 문자셋으로 표현할 수 없는 문자열을 저장 전에 검증 오류로 처리하는지 확인한다.

    param
    - value: 짝이 맞지 않는 유니코드 서로게이트를 포함한 등록 요청.
    """
    with pytest.raises(StorageValidationError, match="UTF-8"):
        prepare_record(value)
