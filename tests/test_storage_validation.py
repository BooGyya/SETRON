"""이전 DB의 CHECK·JSON 규칙이 앱 저장 경로에서 유지되는지 검증한다."""

import math

import pytest

from setron.features.storage.validation import StorageValidationError, decode_record, encode_record


@pytest.mark.parametrize(
    ("table", "values"),
    [
        ("import_batch", {"source_type": "EXCEL", "raw_data": None}),
        ("import_batch", {"source_type": "UNKNOWN"}),
        ("equipment_group", {"category": "HPA", "analysis_enabled": True}),
        ("equipment_group", {"category": "OTHER"}),
        ("equipment_group", {"category": "POLY", "analysis_enabled": 2}),
        ("equipment", {"equipment_code": "  "}),
        ("version_family", {"ui_type": "other"}),
        ("release_version", {"version_raw": "v1", "edited_fields": ["a", "a"]}),
        ("release_version", {"version_raw": "v1", "edited_fields": [""]}),
        ("release_version", {"version_raw": "v1", "item_mapping_status": "APPLIED"}),
        ("item", {"source_reference": [], "edited_fields": []}),
        ("equipment_snapshot", {"production_area": "GUESSED"}),
        ("equipment_version", {"version_raw": ""}),
        ("equipment_pm", {"source_reference": []}),
        ("release_version_item", {"item_state": "FIXED"}),
        ("release_version_item", {"item_state": "UNKNOWN", "target_scope": "ALL", "target_group_ids": [1]}),
        ("release_version_item", {"item_state": "APPLIED", "target_scope": "LISTED", "target_group_ids": []}),
        ("release_version_item", {"item_state": "APPLIED", "target_scope": "LISTED", "target_group_ids": [1, 1]}),
        ("release_version_item", {"item_state": "APPLIED", "target_scope": "LISTED", "target_group_ids": [True]}),
        ("release_version_item", {"item_state": "APPLIED", "target_scope": "LISTED", "target_group_ids": [4294967296]}),
        ("risk_rule", {"rule_type": "OTHER", "target_reference": {}}),
        ("risk_rule", {"rule_type": "DIRECT_VERSION", "target_reference": None}),
        ("risk_rule", {"rule_type": "DIRECT_VERSION", "target_reference": {}, "is_enabled": 2}),
        ("app_setting", {"setting_key": "missing"}),
        ("app_setting", {"setting_key": "nonfinite", "setting_value": math.nan}),
    ],
)
def test_invalid_records_are_rejected(table, values):
    """관리 테이블의 상태값·JSON·필수값 규칙에 어긋나는 입력이 거부되는지 검증한다.

    param
    - table: 입력 검증을 적용할 관리 테이블 이름.
    - values: 해당 테이블의 저장 규칙에 어긋나는 입력값.
    """
    with pytest.raises(StorageValidationError):
        encode_record(table, values, inserting=True)


def test_json_and_exact_text_round_trip_without_changing_source():
    """입력 사전을 바꾸지 않고 버전 원문과 JSON을 저장 형식으로 변환·복원하는지 검증한다."""
    original = {
        "version_raw": "v1.0_가😀 ",
        "source_reference": {"content": {"sheet": "원본😀", "cells": ["A1", "B2"]}},
    }
    encoded = encode_record("release_version", original, inserting=True)
    decoded = decode_record("release_version", encoded)
    assert decoded["version_raw"] == original["version_raw"]
    assert decoded["source_reference"] == original["source_reference"]
    assert decoded["edited_fields"] == []
    assert "edited_fields" not in original


@pytest.mark.parametrize("key", [None, "ETOJ-3257", "etoj-3257", "TEAM_2-1", "A" * 48 + "-1"])
def test_item_jira_is_nullable_preserves_case_and_has_no_global_uniqueness(key):
    original = {"content": "원문", "jira_key": key}
    encoded = encode_record("item", original, inserting=True)
    assert encoded["jira_key"] == key
    assert encode_record("item", original, inserting=True)["jira_key"] == key
    assert original == {"content": "원문", "jira_key": key}
    assert encode_record("item", {"content": "원문"}, inserting=True)["jira_key"] is None


@pytest.mark.parametrize("key", ["", " ETOJ-1", "ETOJ-1 ", "ETOJ-1\n", "ETOJ", "ETOJ-x", "1TEAM-1", "가-1", "A" * 49 + "-1", 123])
def test_item_jira_rejects_bad_format_or_over_fifty_characters(key):
    with pytest.raises(StorageValidationError, match="jira_key"):
        encode_record("item", {"content": "원문", "jira_key": key}, inserting=True)


@pytest.mark.parametrize(
    "value",
    [
        {"1": "원래 값", 1: "다른 값"},
        {"true": "원래 값", True: "다른 값"},
        {"null": "원래 값", None: "다른 값"},
        {"1.5": "원래 값", 1.5: "다른 값"},
        {"nested": {"1": "원래 값", 1: "다른 값"}},
        {"rows": [{"1": "원래 값", 1: "다른 값"}]},
        {"rows": ({"1": "원래 값", 1: "다른 값"},)},
    ],
    ids=["integer", "boolean", "null", "float", "nested-object", "nested-list", "nested-tuple"],
)
def test_json_object_keys_must_be_strings_at_every_depth(value):
    """중첩된 위치에서도 문자열이 아닌 JSON 객체 키를 거부하는지 검증한다.

    param
    - value: 최상위 또는 중첩 위치에 문자열이 아닌 객체 키를 포함한 값.
    """
    with pytest.raises(StorageValidationError, match="키.*문자열"):
        encode_record("app_setting", {"setting_key": "invalid", "setting_value": value}, inserting=True)


def test_valid_nested_json_keeps_string_keys_and_shared_values():
    """문자열 키와 여러 위치에서 참조한 값을 포함한 JSON 내용이 보존되는지 검증한다."""
    shared = {"1": "한글😀", "true": True, "null": None, "values": [1, 1.5, False]}
    value = {"first": shared, "second": [shared]}
    encoded = encode_record("app_setting", {"setting_key": "valid", "setting_value": value}, inserting=True)
    assert decode_record("app_setting", encoded)["setting_value"] == value


def test_circular_json_is_rejected_as_a_validation_error():
    """자기 자신을 참조하는 JSON 입력이 저장 검증 오류로 거부되는지 검증한다."""
    value = {}
    value["self"] = value
    with pytest.raises(StorageValidationError):
        encode_record("app_setting", {"setting_key": "circular", "setting_value": value}, inserting=True)


def test_windows_runtime_config_is_independent_of_docker():
    """Windows 설정 예시가 로컬 MySQL에 접속하고 테스트·관리자 설정을 제외하는지 검증한다."""
    from pathlib import Path

    from setron.infrastructure.database import DatabaseConfig

    path = Path(__file__).resolve().parents[1] / "setron.env.example"
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value.strip("'")
    config = DatabaseConfig.from_env(values)
    assert (config.host, config.port) == ("127.0.0.1", 3306)
    assert not any(key.startswith("SETRON_TEST_") or "ROOT_PASSWORD" in key for key in values)
