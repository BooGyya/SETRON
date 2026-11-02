"""릴리즈·Item 저장 요청 검증."""

from copy import deepcopy
from dataclasses import asdict
from datetime import date

from setron.features.item.schemas import ItemState, TargetScope
from setron.features.storage.inputs import identifier, merge_sources, require, required_text
from setron.features.storage.validation import StorageValidationError

from .schemas import ItemInput, ItemLinkInput, ReleaseInput

RELEASE_EDITABLE = frozenset({"version_raw", "version_suffix", "released_at", "version_order", "item_mapping_status"})
ITEM_EDITABLE = frozenset({"item_no", "content"})


def optional_integer(value, field):
    """선택 정수 항목의 자료형과 DB 저장 범위를 검사한다.

    param
    - value: 검사할 정수 또는 None.
    - field: 오류 메시지에 표시할 항목 이름.

    raises
    - StorageValidationError: 값이 None이 아니면서 32비트 부호 있는 정수도 아닌 경우.
    """
    require(value is None or type(value) is int and -2147483648 <= value <= 2147483647,
            f"{field}: 정수 또는 None이 필요합니다.")


def validate_values(table, values):
    """릴리즈 또는 Item의 현재 관리값을 검사한다.

    param
    - table: release_version 또는 item.
    - values: 등록하거나 수정할 관리값 객체.

    raises
    - StorageValidationError: 필수값·자료형·길이·상태값이 잘못됐거나 UTF-8로 저장할 수 없는 경우.
    """
    if table == "release_version":
        required_text(values["version_raw"], "version_raw", 100)
        suffix = values.get("version_suffix")
        require(suffix is None or isinstance(suffix, str) and len(suffix) <= 50,
                "version_suffix: 50자 이내 문자열 또는 None이 필요합니다.")
        require(values.get("released_at") is None or type(values["released_at"]) is date,
                "released_at: 날짜 또는 None이 필요합니다.")
        optional_integer(values.get("version_order"), "version_order")
        status = values.get("item_mapping_status")
        require(isinstance(status, str) and status in {"UNKNOWN", "VERIFIED"}, "Item 매핑 확인 상태가 잘못됐습니다.")
    else:
        required_text(values["content"], "content", 65535)
        optional_integer(values.get("item_no"), "item_no")
    text_fields = ("version_raw", "version_suffix") if table == "release_version" else ("content",)
    for field in text_fields:
        if values.get(field) is not None:
            try:
                values[field].encode("utf-8")
            except UnicodeError:
                raise StorageValidationError(f"{field}: UTF-8로 저장할 수 없는 문자열입니다.") from None


def prepare_record(value):
    """등록 요청을 검증하고 저장에 사용할 관리값 객체를 구성한다.

    param
    - value: ReleaseInput 또는 ItemInput.

    return
    - 원본 위치와 수정 항목을 정리한 관리값 복사본.

    raises
    - StorageValidationError: 요청 형식이나 입력 항목이 잘못된 경우.
    """
    require(type(value) in {ReleaseInput, ItemInput}, "릴리즈 또는 Item 등록 요청이 필요합니다.")
    table = "release_version" if isinstance(value, ReleaseInput) else "item"
    values = asdict(value)
    if table == "release_version":
        identifier(values["family_id"], "family_id")
    for field in ("batch_id", "release_note_id"):
        if field in values and values[field] is not None:
            identifier(values[field], field)
    validate_values(table, values)
    editable = RELEASE_EDITABLE if table == "release_version" else ITEM_EDITABLE
    names = value.edited_fields
    require(isinstance(names, tuple) and all(isinstance(name, str) for name in names), "수정 항목은 필드명 튜플이어야 합니다.")
    require(len(set(names)) == len(names) and set(names) <= editable, "수정 항목에 중복되거나 편집할 수 없는 필드가 있습니다.")
    values["edited_fields"] = list(names)
    values["source_reference"] = merge_sources(value.source_reference)
    return values


def prepare_changes(table, current, changes):
    """수정 가능한 항목만 반영하고 실제로 바뀐 필드 이름을 기록한다.

    param
    - table: release_version 또는 item.
    - current: 잠금 후 조회한 현재 DB 행.
    - changes: 변경할 필드 이름과 값.

    return
    - 검증한 변경값과 누적 edited_fields 객체.

    raises
    - StorageValidationError: 원본 정보·식별자를 수정하거나 변경값이 잘못된 경우.
    """
    editable = RELEASE_EDITABLE if table == "release_version" else ITEM_EDITABLE
    require(isinstance(changes, dict) and set(changes) <= editable, "편집할 수 없는 필드가 포함됐습니다.")
    result = deepcopy(changes)
    combined = {**current, **result}
    validate_values(table, combined)
    changed = [name for name in changes if current[name] != changes[name]]
    result["edited_fields"] = list(dict.fromkeys([*current["edited_fields"], *changed]))
    return result


def prepare_links(links):
    """릴리즈별 Item 포함 상태와 모델 범위의 형식을 검사한다.

    param
    - links: ItemLinkInput 튜플.

    return
    - 입력 객체와 분리한 ItemLinkInput 튜플.

    raises
    - StorageValidationError: ID·상태·범위가 잘못됐거나 Item이 중복된 경우.
    """
    require(isinstance(links, tuple), "Item 연결은 튜플로 전달해야 합니다.")
    for link in links:
        require(isinstance(link, ItemLinkInput), "ItemLinkInput 형식이 필요합니다.")
        identifier(link.item_id, "item_id")
        require(isinstance(link.item_state, ItemState), "Item 포함 상태가 잘못됐습니다.")
        require(isinstance(link.target_scope, TargetScope), "모델 적용 범위가 잘못됐습니다.")
        require(isinstance(link.target_group_ids, tuple), "모델 ID는 튜플로 전달해야 합니다.")
        for model_id in link.target_group_ids:
            identifier(model_id, "target_group_ids")
        require(len(set(link.target_group_ids)) == len(link.target_group_ids), "모델 ID가 중복됐습니다.")
        require(bool(link.target_group_ids) == (link.target_scope is TargetScope.LISTED), "모델 적용 범위와 ID 목록이 맞지 않습니다.")
    require(len({link.item_id for link in links}) == len(links), "Item 연결이 중복됐습니다.")
    return deepcopy(links)
