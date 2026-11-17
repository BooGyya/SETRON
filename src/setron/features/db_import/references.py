"""덤프의 원본·설정 JSON 안에 저장된 식별자를 대상 DB 식별자로 변환한다."""

import json
from collections.abc import Callable
from copy import deepcopy


class ReferenceConflict(ValueError):
    """참조가 잘못된 행을 다른 대상에 연결하지 않도록 가져오기를 중단한다."""


_ID_TABLES = {
    "batch_id": "import_batch",
    "group_id": "equipment_group", "parent_group_id": "equipment_group", "model_id": "equipment_group",
    "equipment_id": "equipment", "family_id": "version_family", "release_note_id": "release_note",
    "release_version_id": "release_version", "release_id": "release_version",
    "start_release_version_id": "release_version", "end_release_version_id": "release_version",
    "item_id": "item", "snapshot_id": "equipment_snapshot", "equipment_version_id": "equipment_version",
    "pm_id": "equipment_pm", "risk_rule_id": "risk_rule",
}
_SETTING_IDS = {
    "version_groups.v1": ("model_id", "family_id"),
    "version_groups.history.v2": ("model_id", "family_id"),
    "target_applicability.v1": ("model_id", "family_id"),
    "shared_note_item_models.v1": ("release_id", "item_id"),
}


def rewrite_json(table: str, row: dict[str, object], resolve: Callable[[str, int], int], *,
                 ids_changed: bool) -> dict[str, object]:
    """DB 값 사본의 참조만 바꾼다. 연결을 확인할 수 없는 활성 참조는 차단한다."""
    result = dict(row)
    try:
        if row.get("source_reference") is not None:
            _rewrite_column(result, "source_reference", lambda value: _sources(value, resolve))
        if table == "release_version_item" and row.get("target_group_ids") is not None:
            _rewrite_column(result, "target_group_ids", lambda value: _id_list(value, "equipment_group", resolve))
        if table == "risk_rule":
            _rewrite_column(result, "target_reference", lambda value: _risk(value, resolve))
        if table == "import_batch" and row.get("source_type") == "MANUAL" and row.get("raw_data") is not None:
            _rewrite_column(result, "raw_data", lambda value: _journal(value, resolve))
        if table == "app_setting":
            key = row["setting_key"].decode("utf-8")
            prefix, pairs, suffix = _setting_parts(key)
            if pairs:
                converted = {field: _id(value, _ID_TABLES[field], resolve) for field, value in pairs.items()}
                result["setting_key"] = ":".join((prefix, *map(str, converted.values()), *suffix)).encode("utf-8")
                _rewrite_column(result, "setting_value", lambda value: _setting(prefix, pairs, converted, value,
                                                                                 resolve, ids_changed))
            elif key.startswith("demo_seed."):
                _rewrite_column(result, "setting_value", lambda value: _demo(value, resolve))
            elif key == "hierarchy.model_aliases.v1":
                _rewrite_column(result, "setting_value", lambda value: {
                    **value, "aliases": {alias: _id(group_id, "equipment_group", resolve)
                                          for alias, group_id in value.get("aliases", {}).items()}})
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ReferenceConflict(f"{table} 자료의 원본·설정 참조를 확인할 수 없습니다.") from error
    return result


def _rewrite_column(row, column, transform):
    original = json.loads(row[column])
    rewritten = transform(deepcopy(original))
    if rewritten != original:
        row[column] = json.dumps(rewritten, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _id(value, table, resolve):
    if type(value) is not int or not 0 <= value <= 4294967295:
        raise ValueError("잘못된 참조 ID")
    return resolve(table, value)


def _id_list(values, table, resolve):
    if not isinstance(values, list):
        raise ValueError("잘못된 참조 목록")
    mapped = [_id(value, table, resolve) for value in values]
    if len(set(mapped)) != len(mapped):
        raise ValueError("중복 참조")
    return mapped


def _sources(value, resolve, *, historical=False, batch_id=None):
    for field, source in tuple(value.items()):
        locations = source if isinstance(source, list) else [source]
        kept = []
        for location in locations:
            if not isinstance(location, dict):
                raise ValueError("잘못된 원본 위치")
            if "batch_id" in location:
                try:
                    location["batch_id"] = _id(location["batch_id"], "import_batch", resolve)
                except KeyError:
                    if historical:
                        continue
                    raise
            elif historical and type(batch_id) is not int:
                continue
            kept.append(location)
        if kept:
            value[field] = kept if isinstance(source, list) else kept[0]
        else:
            del value[field]
    return value


def _history_id(value, table, resolve):
    if type(value) is not int:
        return value
    try:
        return _id(value, table, resolve)
    except KeyError:
        # 삭제된 원본 ID도 이력에는 남긴다. 문자열로 보관해 현재 행을 가리키지 않게 한다.
        return f"원본 ID {value}"


def _history_ids(row, resolve):
    for field in row.keys() & _ID_TABLES.keys():
        row[field] = _history_id(row[field], _ID_TABLES[field], resolve)
    if row.get("source_reference") is not None:
        _sources(row["source_reference"], resolve, historical=True, batch_id=row.get("batch_id"))
    if row.get("target_group_ids") is not None:
        row["target_group_ids"] = [_history_id(i, "equipment_group", resolve)
                                   for i in row["target_group_ids"]]
    if row.get("target_reference") is not None:
        _risk(row["target_reference"], resolve, historical=True)
    return row


def _journal(value, resolve):
    edit = value.get("management_edit", {})
    for before in edit.get("before", []):
        _history_ids(before["current"], resolve)
        if before.get("software") is not None:
            _history_ids(before["software"], resolve)
    for change in edit.get("changes", []):
        _history_ids(change["key"], resolve)
        _history_ids(change["changes"], resolve)
    for applicability in edit.get("applicability", []):
        _history_ids(applicability, resolve)
        for candidate in applicability.get("candidates", []):
            _history_ids(candidate, resolve)
    created = value.get("management_create")
    if created is not None:
        _history_ids(created["values"], resolve)
    deleted = value.get("management_delete")
    if deleted is not None:
        _history_ids(deleted, resolve)
        for row in deleted.get("rows", []):
            _history_ids(row, resolve)
            data = row.get("data") or {}
            if data.get("snapshot") is not None:
                _history_ids(data["snapshot"], resolve)
            for record in (*data.get("versions", []), *data.get("pms", [])):
                _history_ids(record, resolve)
    release = value.get("management_delete_release")
    if release is not None:
        _history_ids(release, resolve)
        if release.get("release") is not None:
            _history_ids(release["release"], resolve)
        for entry in release.get("items", []):
            _history_ids(entry, resolve)
            _history_ids(entry["item"], resolve)
            _history_ids(entry["link"], resolve)
    for name in ("management_restore", "management_restore_release", "management_reorder"):
        restored = value.get(name)
        if restored is not None:
            _history_ids(restored, resolve)
            if name == "management_restore" and restored.get("source") is not None:
                restored["source"] = _history_id(restored["source"], "import_batch", resolve)
    risk = value.get("management_delete_risk")
    if risk is not None:
        _history_ids(risk["key"], resolve)
        _history_ids(risk["before"]["current"], resolve)
    return value


def _risk(value, resolve, *, historical=False):
    scope = value.get("item_manager")
    if scope is not None:
        if historical:
            scope["model_ids"] = [_history_id(i, "equipment_group", resolve) for i in scope["model_ids"]]
        else:
            scope["model_ids"] = _id_list(scope["model_ids"], "equipment_group", resolve)
    for field in ("family", "item", "direct_version", "start_version", "end_version"):
        evidence = value.get(field)
        if isinstance(evidence, dict):
            _history_ids(evidence, resolve)
            for membership in evidence.get("memberships", []):
                _history_ids(membership, resolve)
    return value


def _setting_parts(key):
    prefix, *values = key.split(":")
    names = _SETTING_IDS.get(prefix)
    if names is None:
        return prefix, {}, ()
    count = 3 if prefix == "version_groups.history.v2" else 2
    if len(values) != count or not all(value.isascii() and value.isdecimal() for value in values):
        raise ValueError("잘못된 설정 키")
    return prefix, dict(zip(names, map(int, values[:2]), strict=True)), values[2:]


def _groups(entry, resolve):
    groups = entry["groups"]
    for slot, members in groups.items():
        if not isinstance(members, list):
            raise ValueError("잘못된 N 그룹 목록")
        kept, missing = [], []
        for member in members:
            try:
                kept.append(_id(member, "release_version", resolve))
            except KeyError:
                missing.append(member)
        groups[slot] = kept
        if missing:
            archived = entry.setdefault("source_unmapped_groups", {}).setdefault(slot, [])
            archived.extend(member for member in missing if member not in archived)


def _setting(prefix, pairs, converted, value, resolve, ids_changed):
    if prefix == "version_groups.history.v2":
        _groups(value, resolve)
        return value
    if any(value[field] != identifier for field, identifier in pairs.items()):
        raise ValueError("설정 키와 자료의 참조 불일치")
    value.update(converted)
    if prefix == "version_groups.v1":
        for entry in value["history"] if value["schema"] == 1 else (value["current"],):
            _groups(entry, resolve)
    elif prefix == "target_applicability.v1":
        candidates = []
        for entry in value["candidates"]:
            confirmation = entry["confirmation"]
            try:
                confirmation["release_id"] = _id(confirmation["release_id"], "release_version", resolve)
            except KeyError:
                value.setdefault("source_unmapped_candidates", []).append(entry)
            else:
                candidates.append(entry)
        value["candidates"] = candidates
        if ids_changed:
            # 지문에 참조 행의 내용과 ID가 포함된다. 새 환경의 확인 근거로 승격하지 않는다.
            value["pair_fingerprint"] = ""
    else:
        for entry in value["entries"]:
            entry["model_id"] = _id(entry["model_id"], "equipment_group", resolve)
        if ids_changed:
            value["fingerprint"] = ""
    return value


def _demo(value, resolve):
    for i, key in enumerate(value.get("owned_settings", [])):
        prefix, pairs, suffix = _setting_parts(key)
        if pairs:
            mapped = (_id(identifier, _ID_TABLES[field], resolve) for field, identifier in pairs.items())
            value["owned_settings"][i] = ":".join((prefix, *map(str, mapped), *suffix))
    return value
