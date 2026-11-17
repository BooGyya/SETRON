"""교환 시점의 UUID·변경 계보를 같은 DB 트랜잭션에 기록한다.

업무 테이블 스키마는 바꾸지 않는다. 숫자 PK는 각 PC의 로컬 주소이며, 이벤트의
참조는 UUID로 보관한다. 교환 사이의 세부 사용자 작업은 기존 MANUAL 이력에 남는다.
"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, select

from setron.features.storage.validation import JSON_COLUMNS, TABLE_NAMES

PREFIX = b"peer_sync.v1:"
_RESERVED = b"peer_sync."
_IDS = {
    "batch_id": "import_batch", "group_id": "equipment_group", "model_id": "equipment_group",
    "parent_group_id": "equipment_group", "equipment_id": "equipment", "family_id": "version_family",
    "release_note_id": "release_note", "release_version_id": "release_version", "release_id": "release_version",
    "start_release_version_id": "release_version", "end_release_version_id": "release_version",
    "item_id": "item", "snapshot_id": "equipment_snapshot", "equipment_version_id": "equipment_version",
    "pm_id": "equipment_pm", "risk_rule_id": "risk_rule",
}


class PeerSyncError(ValueError):
    """손상되거나 서로 모순되는 동등 교환 메타데이터."""


@dataclass
class SyncState:
    entities: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)


def _require(condition, message="동등 교환의 UUID·변경 이력을 확인할 수 없습니다."):
    if not condition:
        raise PeerSyncError(message)


def _uuid(value):
    _require(isinstance(value, str))
    try:
        _require(str(UUID(value)) == value)
    except (ValueError, AttributeError):
        raise PeerSyncError("동등 교환 UUID 형식이 올바르지 않습니다.") from None
    return value


def _pack(value):
    if isinstance(value, bytes):
        return {"$binary": value.hex()}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, dict):
        return {str(k): _pack(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_pack(v) for v in value]
    return value


def _unpack(value):
    if isinstance(value, dict):
        if set(value) == {"$binary"}:
            return bytes.fromhex(value["$binary"])
        if set(value) == {"$datetime"}:
            return datetime.fromisoformat(value["$datetime"])
        if set(value) == {"$date"}:
            return date.fromisoformat(value["$date"])
        return {k: _unpack(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_unpack(v) for v in value]
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def key_token(key):
    """정수·바이너리 등 PK 자료형을 보존하는 안정된 주소."""
    return _json(_pack(key))


def is_sync_key(key):
    return isinstance(key, (bytes, str)) and (key.encode() if isinstance(key, str) else key).startswith(_RESERVED)


def local_peer_id(path=None):
    """DB 복사·SQL 병합과 무관한 이 PC의 ID. 테스트는 path 또는 peer_id를 주입한다."""
    if path is None and os.environ.get("SETRON_PEER_ID"):
        return _uuid(os.environ["SETRON_PEER_ID"])
    path = Path(path) if path is not None else Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local")) / "SETRON" / "peer-id"
    if path.exists():
        try:
            return _uuid(path.read_text(encoding="ascii").strip())
        except (OSError, UnicodeError, PeerSyncError):
            raise PeerSyncError("이 PC의 교환 식별자 파일을 읽을 수 없습니다. 기존 파일을 확인해 주세요.") from None
    path.parent.mkdir(parents=True, exist_ok=True)
    value = str(uuid4())
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(value)
    except FileExistsError:
        return local_peer_id(path)
    return value


def _metadata_rows(rows):
    return [row for row in rows.get("app_setting", ()) if is_sync_key(row.get("setting_key"))]


def read_state(rows):
    """파일 또는 DB의 예약 설정을 읽는다. 변경 이력은 불변이며 참조 계보도 검사한다."""
    state = SyncState()
    try:
        for row in _metadata_rows(rows):
            key = row["setting_key"].encode() if isinstance(row["setting_key"], str) else row["setting_key"]
            _require(key.startswith(PREFIX), "지원하지 않는 동등 교환 메타데이터 버전입니다.")
            kind, identifier = key[len(PREFIX):].decode("ascii").split(":", 1)
            _uuid(identifier)
            value = json.loads(row["setting_value"]) if isinstance(row["setting_value"], str) else deepcopy(row["setting_value"])
            _require(isinstance(value, dict) and value.get("schema") == 1)
            if kind == "entity":
                _require(value.get("uuid") == identifier and value.get("table") in TABLE_NAMES)
                _require(type(value.get("deleted")) is bool and isinstance(value.get("aliases"), list))
                for alias in value["aliases"]:
                    _uuid(alias)
                _uuid(value.get("head"))
                _require(value.get("key") is None or isinstance(value["key"], dict))
                _require(identifier not in state.entities)
                state.entities[identifier] = value
            elif kind == "event":
                _require(value.get("uuid") == identifier and value.get("table") in TABLE_NAMES)
                _uuid(value.get("entity")); _uuid(value.get("peer"))
                _require(isinstance(value.get("parents"), list) and len(set(value["parents"])) == len(value["parents"]))
                for parent in value["parents"]:
                    _uuid(parent)
                _require(value.get("operation") in {"create", "update", "delete", "resolve"})
                _require(value.get("after") is None or isinstance(value.get("after"), dict))
                _require(value.get("digest") == _digest(value.get("after")))
                _require(identifier not in state.events)
                state.events[identifier] = value
            else:
                raise PeerSyncError("알 수 없는 동등 교환 메타데이터 항목입니다.")
        for entity in state.entities.values():
            _require(entity["head"] in state.events)
            event = state.events[entity["head"]]
            _require(event["table"] == entity["table"])
            _require((event["after"] is None) == entity["deleted"])
        aliases = canonical_ids(state)
        tables = {}
        bindings = {}
        for entity in state.entities.values():
            identity = aliases[entity["uuid"]]
            _require(tables.setdefault(identity, entity["table"]) == entity["table"],
                     "동등 교환 별칭이 서로 다른 종류의 항목을 연결합니다.")
            head = state.events[entity["head"]]
            _require(aliases.get(head["entity"], head["entity"]) == identity,
                     "동등 교환의 현재 변경 이력이 다른 항목을 가리킵니다.")
            if entity["key"] is not None and not entity["deleted"]:
                address = entity["table"], _json(entity["key"])
                _require(bindings.setdefault(identity, address) == address,
                         "동등 교환의 같은 UUID가 서로 다른 현재 항목을 가리킵니다.")
        for event in state.events.values():
            identity = aliases.get(event["entity"], event["entity"])
            _require(tables.setdefault(identity, event["table"]) == event["table"])
            for parent_id in event["parents"]:
                _require(parent_id in state.events, "동등 교환의 부모 변경 이력이 빠져 있습니다.")
                parent = state.events[parent_id]
                _require(parent["table"] == event["table"] and
                         aliases.get(parent["entity"], parent["entity"]) == identity,
                         "동등 교환의 부모 변경 이력이 다른 항목을 가리킵니다.")
        visiting, visited = set(), set()
        for identifier in state.events:
            pending = [(identifier, False)]
            while pending:
                current, completed = pending.pop()
                if completed:
                    visiting.remove(current)
                    visited.add(current)
                    continue
                if current in visited:
                    continue
                _require(current not in visiting, "동등 교환 변경 이력이 순환합니다.")
                visiting.add(current)
                pending.append((current, True))
                pending.extend((parent, False) for parent in reversed(state.events[current]["parents"]))
        return state
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, PeerSyncError):
            raise
        raise PeerSyncError("동등 교환의 UUID·변경 이력 형식이 올바르지 않습니다.") from None


def canonical_ids(*states):
    """독립 초기화된 항목의 별칭 집합을 PC 순서와 무관하게 합친다."""
    parent = {}
    def find(value):
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]
    for state in states:
        for entity in state.entities.values():
            for identifier in (entity["uuid"], *entity["aliases"]):
                first, second = find(entity["uuid"]), find(identifier)
                parent[max(first, second)] = min(first, second)
    return {identifier: find(identifier) for identifier in parent}


def identity_index(rows):
    state = read_state(rows)
    result = {}
    for entity in state.entities.values():
        if entity["key"] is not None and not entity["deleted"]:
            address = entity["table"], _json(entity["key"])
            _require(address not in result, "동등 교환 UUID가 같은 로컬 항목을 중복 참조합니다.")
            result[address] = entity["uuid"]
    return result


def read_rows(session, repository, *, lock=False):
    rows = {}
    for name in TABLE_NAMES:
        table = repository.table(name)
        query = select(table).order_by(*table.primary_key.columns)
        if lock:
            query = query.with_for_update()
        rows[name] = tuple(dict(row) for row in session.execute(query).mappings())
    return rows


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _business_rows(rows):
    return {name: tuple(row for row in records if name != "app_setting" or
                       not (is_sync_key(row["setting_key"]) or row["setting_key"].startswith(b"sql_import.batch.v1:")))
            for name, records in rows.items()}


def _address(name, row, repository):
    return name, key_token({column.name: row[column.name] for column in repository.table(name).primary_key})


def _ref(table, identifier, identities):
    if identifier is None:
        return None
    uuid = identities.get((table, key_token({table_key: identifier}))) if (table_key := _PRIMARY.get(table)) else None
    return {"$ref": uuid} if uuid else {"$missing": table, "source_id": _pack(identifier)}


_PRIMARY = {"import_batch": "batch_id", "equipment_group": "group_id", "equipment": "equipment_id",
            "version_family": "family_id", "release_note": "release_note_id", "release_version": "release_version_id",
            "item": "item_id", "equipment_snapshot": "snapshot_id", "equipment_version": "equipment_version_id",
            "equipment_pm": "pm_id", "risk_rule": "risk_rule_id"}


def _portable_json(value, identities, *, field=None, semantic=True):
    if semantic and field in _IDS and isinstance(value, str) and value.startswith("원본 ID ") and value[6:].isdecimal():
        return {"$missing": _IDS[field], "source_id": int(value[6:])}
    if field in _IDS and (type(value) is int) and semantic:
        return _ref(_IDS[field], value, identities)
    if isinstance(value, dict):
        if semantic and field == "management_restore":
            value = dict(value)
            if value.get("source") is not None:
                source = value["source"]
                value["source"] = (_ref("import_batch", source, identities) if type(source) is int else
                                   {"$missing": "import_batch", "source_id": int(source[6:])}
                                   if isinstance(source, str) and source.startswith("원본 ID ") and source[6:].isdecimal() else source)
        if semantic and field == "groups":
            return {key: [_ref("release_version", item, identities) for item in members]
                    for key, members in value.items()}
        return {key: _portable_json(item, identities, field=key, semantic=semantic) for key, item in value.items()}
    if isinstance(value, list):
        if semantic and field == "target_group_ids":
            return [_ref("equipment_group", item, identities) for item in value]
        if semantic and field == "model_ids":
            return [_ref("equipment_group", item, identities) for item in value]
        if semantic and field == "groups":
            return [_ref("release_version", item, identities) for item in value]
        return [_portable_json(item, identities, field=field, semantic=semantic) for item in value]
    return _pack(value)


def _payload(name, row, repository, identities):
    table = repository.table(name)
    primary = set(table.primary_key.columns.keys())
    foreign = {fk.parent.name: fk.column.table.name for fk in table.foreign_keys
               if _PRIMARY.get(fk.column.table.name) == fk.column.name}
    result = {}
    for column, value in row.items():
        if isinstance(value, str) and column in table.c:
            kind = table.c[column].type
            if isinstance(kind, DateTime):
                value = datetime.fromisoformat(value)
            elif isinstance(kind, Date):
                value = date.fromisoformat(value)
        if column in primary and column not in foreign:
            if name == "app_setting":
                text = value.decode("utf-8") if isinstance(value, bytes) else value
                prefix, *parts = text.split(":")
                pair = {"version_groups.v1": ("equipment_group", "version_family"),
                        "version_groups.history.v2": ("equipment_group", "version_family"),
                        "target_applicability.v1": ("equipment_group", "version_family"),
                        "shared_note_item_models.v1": ("release_version", "item")}.get(prefix)
                if pair is not None and len(parts) >= 2 and all(part.isdecimal() for part in parts[:2]):
                    result[column] = {"prefix": prefix, "references": [_ref(parent, int(part), identities)
                                      for parent, part in zip(pair, parts[:2])], "suffix": parts[2:]}
                else:
                    result[column] = _pack(value)
            continue
        if column in foreign:
            result[column] = _ref(foreign[column], value, identities)
        elif column in JSON_COLUMNS.get(name, {}) and value is not None:
            decoded = json.loads(value) if isinstance(value, str) else value
            if name == "app_setting" and row["setting_key"] in {b"hierarchy.model_aliases.v1", "hierarchy.model_aliases.v1"}:
                decoded = {**decoded, "aliases": {alias: _ref("equipment_group", identifier, identities)
                                                  for alias, identifier in decoded.get("aliases", {}).items()}}
            result[column] = _portable_json(decoded, identities, semantic=column != "raw_data" or row.get("source_type") == "MANUAL")
        else:
            result[column] = _pack(value)
    return result


def _canonical_payload(value, aliases):
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            return {"$ref": aliases.get(value["$ref"], value["$ref"])}
        return {key: _canonical_payload(item, aliases) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_payload(item, aliases) for item in value]
    return value


def validate_exchange(rows, repository):
    """들어온 파일의 UUID·head가 실제 자료와 일치하는지 검증한다. 이전 SQL은 허용한다.

    로컬 DB에는 교환 후 새 편집이 있을 수 있으므로 preview의 원본 파일에만 적용한다.
    """
    state = read_state(rows)
    if not state.entities and not state.events:
        return state
    index = identity_index(rows)
    aliases = canonical_ids(state)
    actual = {_address(name, row, repository): row for name, records in _business_rows(rows).items() for row in records}
    _require(set(index) == set(actual), "교환 파일의 항목 UUID와 실제 자료 구성이 다릅니다. 파일을 다시 내보내 주세요.")
    for address, identifier in index.items():
        entity = state.entities[identifier]
        payload = _payload(address[0], actual[address], repository, index)
        head = state.events[entity["head"]]
        _require(_canonical_payload(payload, aliases) == _canonical_payload(head["after"], aliases),
                 "교환 파일의 변경 이력과 실제 값이 다릅니다. 파일을 다시 내보내 주세요.")
    return state


def _ancestors(identifier, events):
    found, pending = set(), [identifier]
    while pending:
        value = pending.pop()
        if value in found:
            continue
        found.add(value)
        pending.extend(events[value]["parents"])
    return found


def _mapped_key(entity, id_maps, repository):
    if entity["key"] is None:
        return None
    values = _unpack(entity["key"])
    table = repository.table(entity["table"])
    if entity["table"] == "app_setting":
        values["setting_key"] = id_maps.get("app_setting", {}).get(values["setting_key"], values["setting_key"])
    foreign = {fk.parent.name: fk.column.table.name for fk in table.foreign_keys
               if _PRIMARY.get(fk.column.table.name) == fk.column.name}
    try:
        for column in table.primary_key:
            if type(values[column.name]) is int:
                parent = foreign.get(column.name, entity["table"])
                values[column.name] = id_maps[parent][values[column.name]]
        return values
    except KeyError:
        return None


def reconcile(session, repository, *, rows=None, peer_id=None, incoming_rows=None, id_maps=None, choices=None):
    """잠근 현재 자료와 교환 이력을 합친 뒤 예약 설정을 같은 트랜잭션에 저장한다.

    incoming_rows의 실제 자료 적용은 호출자가 먼저 수행한다. 여기서는 원본 이벤트를
    불변으로 합치고, 선택한 현재 결과에 두 부모를 가진 해결 이벤트를 남긴다.
    """
    rows = read_rows(session, repository, lock=True) if rows is None else rows
    state = read_state(rows)
    peer = _uuid(peer_id) if peer_id is not None else local_peer_id()
    business = _business_rows(rows)
    current = {_address(name, row, repository): row for name, records in business.items() for row in records}
    index = {(entity["table"], _json(entity["key"])): entity for entity in state.entities.values()
             if entity["key"] is not None}
    incoming_heads = {}
    if incoming_rows is not None:
        incoming = read_state(incoming_rows)
        for identifier, event in incoming.events.items():
            if identifier in state.events:
                _require(state.events[identifier] == event, "같은 변경 UUID의 내용이 다릅니다. 파일을 다시 확인해 주세요.")
            else:
                state.events[identifier] = deepcopy(event)
        known = canonical_ids(state, incoming)
        # 처음 들어오는 대형 DB도 entity마다 전체 기존 이력을 다시 탐색하지 않는다.
        # 별칭의 같은 집합에 보관 이력과 현재 항목이 함께 있으면 실제 현재 주소를 우선한다.
        def priority(entity):
            address = (entity["table"], _json(entity["key"])) if entity["key"] is not None else None
            return (address in current and not entity["deleted"], entity["key"] is not None)
        by_identity = {}
        for entity in state.entities.values():
            identifier = known.get(entity["uuid"], entity["uuid"])
            if identifier not in by_identity or priority(entity) > priority(by_identity[identifier]):
                by_identity[identifier] = entity
        for source in incoming.entities.values():
            mapped = _mapped_key(source, id_maps or {}, repository)
            target = index.get((source["table"], key_token(mapped))) if mapped is not None else None
            if target is None:
                target = by_identity.get(known.get(source["uuid"], source["uuid"]))
            if target is None and mapped is not None and (source["table"], key_token(mapped)) in current:
                target = deepcopy(source)
                target["key"] = _pack(mapped)
                state.entities[target["uuid"]] = target
                index[(target["table"], key_token(mapped))] = target
            if target is None:
                # 미적용·삭제 항목의 계보도 다시 다른 PC에 전달한다. 로컬 주소는 붙이지 않는다.
                target = deepcopy(source)
                target["key"] = None
                state.entities[target["uuid"]] = target
            if target is not None:
                _require(target["table"] == source["table"])
                old_address = (target["table"], _json(target["key"])) if target["key"] is not None else None
                if mapped is not None and (source["table"], key_token(mapped)) in current and (
                        target["key"] is None or target["deleted"] or old_address not in current):
                    target["key"] = _pack(mapped)
                    index[(target["table"], key_token(mapped))] = target
                target["aliases"] = sorted((set(target["aliases"]) | set(source["aliases"]) | {source["uuid"]}) - {target["uuid"]})
                incoming_heads.setdefault(target["uuid"], set()).add(source["head"])
                by_identity[known.get(source["uuid"], source["uuid"])] = target
    # 새 항목 모두에 UUID를 먼저 배정해야 같은 스냅샷의 FK도 UUID로 정규화된다.
    for address, row in current.items():
        entity = index.get(address)
        if entity is None or (entity["deleted"] and entity["uuid"] not in incoming_heads):
            identifier = str(uuid4())
            entity = {"schema": 1, "uuid": identifier, "table": address[0], "key": json.loads(address[1]),
                      "aliases": [], "head": None, "deleted": False}
            state.entities[identifier] = entity
            index[address] = entity
    identities = {address: entity["uuid"] for address, entity in index.items() if address in current}
    aliases = canonical_ids(state)
    operation_values = choices.get("values", {}) if isinstance(choices, dict) else {}
    operation_choices_digest = _digest(_pack(operation_values))
    operation_choices_count = len(operation_values)
    for entity in tuple(state.entities.values()):
        if entity["key"] is None:
            continue
        address = entity["table"], _json(entity["key"])
        row = current.get(address)
        # 같은 로컬 PK를 재사용한 다른 항목은 이전 tombstone와 별개다.
        if row is not None and index.get(address) is not entity:
            continue
        payload = _payload(entity["table"], row, repository, identities) if row is not None else None
        heads = set(incoming_heads.get(entity["uuid"], ()))
        if entity["head"] is not None:
            heads.add(entity["head"])
        minimal = {head for head in heads if not any(head != other and head in _ancestors(other, state.events) for other in heads)}
        matched = [head for head in minimal if _canonical_payload(state.events[head]["after"], aliases) == _canonical_payload(payload, aliases)]
        receipt = (choices or {}).get("entities", {}).get(entity["uuid"]) if isinstance(choices, dict) else None
        recorded_receipt = False
        if receipt is not None:
            _require(isinstance(receipt, dict) and receipt.get("choice") in {"CURRENT", "INCOMING"} and
                     isinstance(receipt.get("source_group_digest"), str))
            for head in heads:
                for ancestor in _ancestors(head, state.events):
                    selected = state.events[ancestor].get("selection", {}).get("entities", {})
                    if any(item == receipt for item in selected.values()):
                        recorded_receipt = True
                        break
        if len(minimal) == 1 and matched and (receipt is None or recorded_receipt):
            entity["head"] = matched[0]
            entity["deleted"] = payload is None
            continue
        identifier = str(uuid4())
        event = {"schema": 1, "uuid": identifier, "peer": peer, "entity": entity["uuid"], "table": entity["table"],
                 "parents": sorted(minimal), "operation": "resolve" if receipt is not None or len(minimal) > 1 or incoming_heads.get(entity["uuid"]) else
                 "create" if not minimal else "delete" if payload is None else "update",
                 "after": payload, "digest": _digest(payload), "recorded_at": datetime.now(timezone.utc).isoformat()}
        if choices is not None and event["operation"] == "resolve":
            selected = choices
            if isinstance(choices, dict) and "entities" in choices:
                selected = {"values": choices.get("fields", {}).get(entity["uuid"], {}),
                            "entities": {entity["uuid"]: receipt} if receipt else {},
                            "operation_choices_digest": operation_choices_digest,
                            "operation_choices_count": operation_choices_count}
            event["selection"] = _pack(selected)
        state.events[identifier] = event
        entity["head"], entity["deleted"] = identifier, payload is None
    _persist(session, repository, rows, state)
    return state


def _persist(session, repository, rows, state):
    # 서로 정상인 두 파일도 별칭을 합친 결과는 모순될 수 있다. 쓰기 전에 합집합을 검사한다.
    read_state({"app_setting": [
        {"setting_key": PREFIX + kind.encode() + b":" + identifier.encode(), "setting_value": value}
        for kind, records in (("event", state.events), ("entity", state.entities))
        for identifier, value in records.items()
    ]})
    existing = {row["setting_key"]: row["setting_value"] for row in rows["app_setting"]}
    for kind, values in (("event", state.events), ("entity", state.entities)):
        for identifier, value in values.items():
            key = PREFIX + kind.encode() + b":" + identifier.encode()
            serialized = _json(value)
            if key not in existing:
                repository.insert(session, "app_setting", {"setting_key": key, "setting_value": serialized})
            elif json.loads(existing[key]) != value:
                _require(kind != "event", "저장된 변경 UUID의 내용이 바뀌었습니다.")
                repository.update(session, "app_setting", {"setting_key": key}, {"setting_value": serialized})
