"""앱 입력값을 MySQL 5.6의 저장 형식으로 변환하고 기존 CHECK 규칙을 검사한다."""

import json
from copy import deepcopy


class StorageValidationError(ValueError):
    """입력값이 저장 형식이나 허용 범위를 벗어났을 때 발생한다."""


TABLE_NAMES = (
    "import_batch", "equipment_group", "equipment", "version_family", "release_note",
    "release_version", "item", "equipment_snapshot", "equipment_version", "equipment_pm",
    "release_version_item", "risk_rule", "app_setting",
)
# JSON을 LONGTEXT로 저장하므로 필드별로 허용하는 구조를 앱에서 검사한다.
JSON_COLUMNS = {
    "import_batch": {"raw_data": "object_optional"},
    "release_version": {"source_reference": "object_optional", "edited_fields": "field_names"},
    "item": {"source_reference": "object_optional", "edited_fields": "field_names"},
    "equipment_snapshot": {"source_reference": "object_optional"},
    "equipment_version": {"source_reference": "object_optional"},
    "equipment_pm": {"source_reference": "object_optional"},
    "release_version_item": {"target_group_ids": "group_ids"},
    "risk_rule": {"target_reference": "object"},
    "app_setting": {"setting_value": "json"},
}
# 대소문자와 뒤쪽 공백까지 구분할 원문 필드다. 숫자는 최대 글자 수를 뜻한다.
BINARY_COLUMNS = {
    "equipment": {"equipment_code": 50},
    "release_version": {"version_raw": 100},
    "equipment_version": {"version_raw": 100},
    "equipment_pm": {"pm_name": 50},
    "app_setting": {"setting_key": 100},
}
# MySQL 5.6에서 기본값을 둘 수 없는 JSON 목록 등은 저장 전에 앱에서 채운다.
DEFAULTS = {
    "equipment_group": {"analysis_enabled": True},
    "release_version": {"item_mapping_status": "UNKNOWN", "edited_fields": []},
    "item": {"edited_fields": []},
    "release_version_item": {"target_scope": "UNKNOWN", "target_group_ids": None},
    "risk_rule": {"is_enabled": True, "mapping_status": "UNRESOLVED"},
}
ENUMS = {
    "import_batch": {"source_type": {"EXCEL", "MANUAL"}},
    "equipment_group": {"category": {"POLY", "OXIDE", "KCTC", "HPA"}},
    "version_family": {"ui_type": {None, "LEGACY", "INTEGRATED", "SMART"}},
    "release_version": {"item_mapping_status": {"UNKNOWN", "VERIFIED"}},
    "equipment_snapshot": {"production_area": {None, "MEMORY", "FOUNDRY"}},
    "release_version_item": {
        "item_state": {"APPLIED", "NOT_APPLIED", "UNKNOWN"},
        "target_scope": {"ALL", "LISTED", "UNKNOWN"},
    },
    "risk_rule": {
        "rule_type": {"SAFETY_REQUIRED", "DEFECT_ITEM", "DIRECT_VERSION"},
        "mapping_status": {"VERIFIED", "UNRESOLVED"},
    },
}


def fail(table, field, reason):
    raise StorageValidationError(f"{table}.{field}: {reason}")


def _validate_json_keys(table, field, value):
    """JSON 변환 과정에서 키가 문자열로 바뀌어 원본값이 사라지는 것을 막는다."""
    pending = [value]
    visited = set()
    while pending:
        current = pending.pop()
        if not isinstance(current, (dict, list, tuple)) or id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, dict):
            if any(not isinstance(key, str) for key in current):
                fail(table, field, "JSON 객체의 키는 문자열이어야 합니다.")
            pending.extend(current.values())
        else:
            pending.extend(current)


def json_text(table, field, value, rule):
    """필드가 허용하는 JSON 구조를 검사하고 저장용 문자열로 변환한다."""
    if value is None and rule in {"object_optional", "group_ids"}:
        return None
    if rule in {"object", "object_optional"} and not isinstance(value, dict):
        fail(table, field, "객체 형태여야 합니다.")
    if rule == "field_names":
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            fail(table, field, "비어 있지 않은 필드명 목록이어야 합니다.")
        if len(set(value)) != len(value):
            fail(table, field, "필드명이 중복됐습니다.")
    if rule == "group_ids":
        if not isinstance(value, list) or not value or any(
            type(item) is not int or not 1 <= item <= 4294967295 for item in value
        ):
            fail(table, field, "양의 모델 ID 목록이어야 합니다.")
        if len(set(value)) != len(value):
            fail(table, field, "모델 ID가 중복됐습니다.")
    _validate_json_keys(table, field, value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, UnicodeError):
        fail(table, field, "JSON으로 저장할 수 없는 값입니다.")


def encode_record(table, values, *, inserting=False):
    """입력값에 기본값과 검증 규칙을 적용하고 DB에 전달할 형식으로 변환한다."""
    if table not in TABLE_NAMES:
        raise StorageValidationError("지원하지 않는 관리 테이블입니다.")
    result = deepcopy(DEFAULTS.get(table, {})) if inserting else {}
    result.update(deepcopy(values))
    for field, allowed in ENUMS.get(table, {}).items():
        value = result.get(field)
        if not isinstance(value, (str, type(None))) or value not in allowed:
            fail(table, field, "허용된 값이 아닙니다.")
    for field in ("analysis_enabled", "is_enabled"):
        if field in result and (not isinstance(result[field], (bool, int)) or result[field] not in (0, 1)):
            fail(table, field, "참 또는 거짓이어야 합니다.")
    if table == "equipment_group" and result.get("category") == "HPA" and result.get("analysis_enabled") != 0:
        fail(table, "analysis_enabled", "HPA는 공통 분석에서 제외해야 합니다.")
    if table == "import_batch" and result.get("source_type") == "EXCEL" and result.get("raw_data") is None:
        fail(table, "raw_data", "Excel 원본값이 필요합니다.")
    if table == "release_version_item":
        ids, scope = result.get("target_group_ids"), result.get("target_scope")
        if (scope == "LISTED" and ids is None) or (scope != "LISTED" and ids is not None):
            fail(table, "target_group_ids", "지정 모델 범위와 모델 목록이 맞지 않습니다.")
    for field in ("equipment_code", "version_raw"):
        if field in BINARY_COLUMNS.get(table, {}):
            value = result.get(field)
            if not isinstance(value, str) or not value.strip():
                fail(table, field, "비어 있지 않은 원문이 필요합니다.")
    for field, max_chars in BINARY_COLUMNS.get(table, {}).items():
        if field not in result:
            continue
        value = result[field]
        if not isinstance(value, str) or len(value) > max_chars:
            fail(table, field, f"{max_chars}자 이내의 문자열이어야 합니다.")
        try:
            result[field] = value.encode("utf-8")
        except UnicodeError:
            fail(table, field, "UTF-8로 저장할 수 없는 문자열입니다.")
    for field, rule in JSON_COLUMNS.get(table, {}).items():
        if rule == "json" and field not in result:
            fail(table, field, "필수값이 없습니다.")
        if field in result or rule in {"object", "field_names", "json"}:
            result[field] = json_text(table, field, result.get(field), rule)
    return result


def decode_record(table, values):
    """DB에서 읽은 JSON과 UTF-8 바이트를 앱에서 사용할 값으로 복원한다."""
    result = dict(values)
    for field in BINARY_COLUMNS.get(table, {}):
        if isinstance(result.get(field), (bytes, bytearray, memoryview)):
            result[field] = bytes(result[field]).decode("utf-8")
    for field in JSON_COLUMNS.get(table, {}):
        value = result.get(field)
        if value is not None:
            try:
                result[field] = json.loads(value)
            except (TypeError, ValueError):
                fail(table, field, "저장된 JSON을 읽을 수 없습니다.")
    return result
