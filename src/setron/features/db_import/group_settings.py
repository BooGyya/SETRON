"""N 그룹의 현재 기준과 저장 이력을 한 묶음으로 병합한다."""

import json
from collections import defaultdict

from setron.features.targets.groups import HISTORY_KEY_PREFIX, LEGACY_SCHEMA, PREVIOUS_SCHEMA, SCHEMA, SEQUENCE_WIDTH

_CURRENT = b"version_groups.v1:"
_HISTORY = HISTORY_KEY_PREFIX.encode("ascii")
_INCOMPLETE = "SQL 파일의 N 그룹 설정과 저장 이력이 일치하지 않습니다."


def group_history_prune(incoming_settings, target_settings, mode):
    """같은 모델·계열에서 건너뛸 원본 키와 삭제할 대상 이력 키를 돌려준다."""
    incoming, incoming_history = _by_group(incoming_settings)
    target, target_history = _by_group(target_settings)
    suppressed, deleted = set(), set()

    for key, history in incoming_history.items():
        if key not in incoming:
            suppressed.update(row["setting_key"] for row in history.values())
    for key, current in incoming.items():
        source_history = incoming_history.get(key, {})
        _check_complete(current, source_history)
        source_keys = {row["setting_key"] for row in source_history.values()}
        target_keys = {row["setting_key"] for row in target_history.get(key, {}).values()}
        if key not in target and target_keys:
            # 기준이 없는 이력과 새 기준을 섞으면 과거 순번의 근거가 뒤섞일 수 있다.
            suppressed.add(key)
            suppressed.update(source_keys)
        elif key in target:
            if mode == "add":
                suppressed.update(source_keys - target_keys)
            else:
                deleted.update(target_keys - source_keys)
    return suppressed, deleted


def _by_group(rows):
    current, history = {}, defaultdict(dict)
    for row in rows:
        key = row["setting_key"]
        if key.startswith(_CURRENT):
            if key in current:
                raise ValueError(_INCOMPLETE)
            current[key] = row
        elif key.startswith(_HISTORY):
            pair, _, sequence = key[len(_HISTORY):].rpartition(b":")
            if len(sequence) != SEQUENCE_WIDTH or not sequence.isdigit():
                raise ValueError(_INCOMPLETE)
            entries = history[_CURRENT + pair]
            number = int(sequence)
            if number in entries:
                raise ValueError(_INCOMPLETE)
            entries[number] = row
    return current, history


def _check_complete(current, history):
    payload = json.loads(current["setting_value"])
    if not isinstance(payload, dict) or type(payload.get("schema")) is not int:
        raise ValueError(_INCOMPLETE)
    schema = payload["schema"]
    if schema == LEGACY_SCHEMA:
        legacy = payload.get("history")
        if not isinstance(legacy, list) or not legacy or history:
            raise ValueError(_INCOMPLETE)
        return
    sequence = payload.get("sequence")
    if (schema not in (PREVIOUS_SCHEMA, SCHEMA) or type(sequence) is not int
            or not 1 <= sequence < 10 ** SEQUENCE_WIDTH or not isinstance(payload.get("current"), dict)):
        raise ValueError(_INCOMPLETE)
    expected = sequence - (schema == PREVIOUS_SCHEMA)
    if len(history) != expected or any(not 1 <= number <= expected for number in history):
        raise ValueError(_INCOMPLETE)
    if schema == SCHEMA:
        active = payload.get("active_sequence")
        if (type(active) is not int or active not in history
                or json.loads(history[active]["setting_value"]) != payload["current"]):
            raise ValueError(_INCOMPLETE)
