"""공통 변경 계보를 찾아 실제 현재값과 가져온값을 3방향 비교한다."""

from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
import math

from .service import PeerSyncError, SyncState, _payload, canonical_ids


class _Absent(Enum):
    MISSING = "missing_field"
    UNAVAILABLE = "unavailable_baseline"


MISSING = _Absent.MISSING
UNAVAILABLE = _Absent.UNAVAILABLE


@dataclass(frozen=True)
class Baseline:
    found: bool
    payload: object = UNAVAILABLE
    revisions: tuple[str, ...] = ()
    reason: str = "identity_unavailable"


@dataclass(frozen=True)
class Decision:
    choice: str | None
    kind: str


def _require(condition, message):
    if not condition:
        raise PeerSyncError(message)


def canonical_value(value, aliases=None):
    """로컬 숫자 ID와 무관한 값. NULL·누락과 JSON 자료형을 서로 구분한다."""
    aliases = aliases or {}
    if value is MISSING or value is UNAVAILABLE:
        return value
    if isinstance(value, bytes):
        return {"$binary": value.hex()}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            identifier = value["$ref"]
            _require(isinstance(identifier, str), "공통 기준의 UUID 참조가 올바르지 않습니다.")
            return {"$ref": aliases.get(identifier, identifier)}
        _require(all(isinstance(key, str) for key in value), "공통 기준 값의 필드 이름이 올바르지 않습니다.")
        return {key: canonical_value(item, aliases) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical_value(item, aliases) for item in value]
    return value


def _typed(value):
    # Python의 True == 1, 1 == 1.0 비교로 다른 JSON 값이 같다고 판단하지 않는다.
    if value is MISSING:
        return ("missing",)
    if value is UNAVAILABLE:
        return ("unavailable",)
    if value is None:
        return ("null",)
    if type(value) is bool:
        return "boolean", value
    if type(value) is int:
        return "integer", value
    if type(value) is float:
        _require(math.isfinite(value), "공통 기준 값에 유한하지 않은 숫자가 있습니다.")
        return "number", value
    if isinstance(value, str):
        return "string", value
    if isinstance(value, dict):
        return "object", tuple((key, _typed(item)) for key, item in sorted(value.items()))
    if isinstance(value, list):
        return "array", tuple(_typed(item) for item in value)
    raise PeerSyncError("공통 기준 값에 지원하지 않는 자료형이 있습니다.")


def equivalent(first, second, aliases=None):
    """UUID 별칭과 값의 자료형을 함께 반영한 비교."""
    return _typed(canonical_value(first, aliases)) == _typed(canonical_value(second, aliases))


def decide(base, current, incoming, *, aliases=None):
    """동일·한쪽 변경만 자동 결정한다. 기준 없음과 양쪽의 다른 변경은 명시 선택한다."""
    if isinstance(base, Baseline):
        base = base.payload if base.found else UNAVAILABLE
    if equivalent(current, incoming, aliases):
        return Decision("CURRENT", "unchanged" if base is not UNAVAILABLE and equivalent(base, current, aliases)
                        else "same_change")
    if base is UNAVAILABLE:
        return Decision(None, "no_baseline")
    if equivalent(current, base, aliases):
        return Decision("INCOMING", "incoming_only")
    if equivalent(incoming, base, aliases):
        return Decision("CURRENT", "current_only")
    return Decision(None, "conflict")


def portable_row(name, row, repository, identities, aliases=None):
    """검토 시점의 실제 DB 행을 이벤트와 같은 UUID 기반 값으로 변환한다."""
    if row is None:
        return None
    return canonical_value(_payload(name, row, repository, identities), aliases)


def _validate_state(state):
    _require(isinstance(state, SyncState) and isinstance(state.entities, dict) and isinstance(state.events, dict),
             "공통 기준의 변경 이력 형식이 올바르지 않습니다.")
    for identifier, entity in state.entities.items():
        _require(isinstance(entity, dict) and entity.get("uuid") == identifier and
                 isinstance(identifier, str) and isinstance(entity.get("table"), str) and
                 isinstance(entity.get("head"), str) and isinstance(entity.get("aliases"), list) and
                 all(isinstance(alias, str) for alias in entity["aliases"]),
                 "공통 기준의 항목 이력 형식이 올바르지 않습니다.")
    for identifier, event in state.events.items():
        _require(isinstance(event, dict) and event.get("uuid", identifier) == identifier and
                 isinstance(identifier, str) and isinstance(event.get("entity"), str) and
                 isinstance(event.get("table"), str) and isinstance(event.get("parents"), list) and
                 all(isinstance(parent, str) for parent in event["parents"]) and "after" in event and
                 (event["after"] is None or isinstance(event["after"], dict)),
                 "공통 기준의 변경 이력 형식이 올바르지 않습니다.")
    aliases = canonical_ids(state)
    for entity in state.entities.values():
        _require(isinstance(entity, dict) and entity.get("head") in state.events,
                 "공통 기준의 현재 변경 이력이 빠져 있습니다.")
        head = state.events[entity["head"]]
        _require(head.get("table") == entity.get("table") and
                 aliases.get(head.get("entity"), head.get("entity")) == aliases.get(entity["uuid"], entity["uuid"]),
                 "공통 기준의 현재 변경 이력이 다른 항목을 가리킵니다.")
    for identifier, event in state.events.items():
        _require(isinstance(event, dict) and event.get("uuid", identifier) == identifier and
                 isinstance(event.get("parents"), list), "공통 기준의 변경 이력 형식이 올바르지 않습니다.")
        _require(len(event["parents"]) == len(set(event["parents"])), "공통 기준의 부모 변경 이력이 중복되었습니다.")
        for parent_id in event["parents"]:
            _require(parent_id in state.events, "공통 기준의 부모 변경 이력이 빠져 있습니다.")
            parent = state.events[parent_id]
            _require(parent.get("table") == event.get("table") and
                     aliases.get(parent.get("entity"), parent.get("entity")) == aliases.get(event.get("entity"), event.get("entity")),
                     "공통 기준의 부모 변경 이력이 다른 항목을 가리킵니다.")


class BaselineIndex:
    """한 검토에서 계보를 한 번 검사하고 항목·head별 조상 탐색 결과를 재사용한다."""

    def __init__(self, source_state, target_state):
        self.source, self.target = source_state, target_state
        _validate_state(source_state)
        _validate_state(target_state)
        self.aliases = canonical_ids(source_state, target_state)
        self.events = dict(target_state.events)
        for identifier, event in source_state.events.items():
            _require(identifier not in self.events or equivalent(self.events[identifier], event),
                     "같은 변경 UUID의 내용이 다릅니다. 파일을 다시 확인해 주세요.")
            self.events[identifier] = event
        self.children = defaultdict(set)
        indegree = {}
        tables = {}
        for identifier, event in self.events.items():
            identity = self.aliases.get(event["entity"], event["entity"])
            _require(tables.setdefault(identity, event["table"]) == event["table"],
                     "공통 기준의 UUID 별칭이 다른 종류의 항목을 연결합니다.")
            indegree[identifier] = len(event["parents"])
            for parent in event["parents"]:
                self.children[parent].add(identifier)
        pending = deque(identifier for identifier, count in indegree.items() if not count)
        visited = 0
        while pending:
            identifier = pending.popleft()
            visited += 1
            for child in self.children[identifier]:
                indegree[child] -= 1
                if not indegree[child]:
                    pending.append(child)
        _require(visited == len(self.events), "공통 기준의 변경 이력이 순환합니다.")
        self._ancestors_cache = {}
        self._baseline_cache = {}
        self._entity_indexes = {}
        for state in (source_state, target_state):
            by_identity = defaultdict(list)
            for entity in state.entities.values():
                identity = self.aliases.get(entity["uuid"], entity["uuid"])
                by_identity[identity].append(entity)
            selected = {}
            for identity, candidates in by_identity.items():
                priorities = [(0 if entity.get("key") is not None and not entity.get("deleted") else
                               1 if entity.get("key") is not None else 2, entity) for entity in candidates]
                priority = min(value[0] for value in priorities)
                candidates = [entity for value, entity in priorities if value == priority]
                _require(len({(entity["head"], entity["table"]) for entity in candidates}) == 1,
                         "공통 기준의 같은 UUID에 현재 변경 이력이 여러 개 있습니다.")
                selected[identity] = candidates[0]
            self._entity_indexes[id(state)] = selected

    def _entity(self, state, identifier):
        if identifier is None:
            return None
        _require(isinstance(identifier, str), "공통 기준의 항목 UUID가 올바르지 않습니다.")
        canonical = self.aliases.get(identifier, identifier)
        return self._entity_indexes[id(state)].get(canonical)

    def ancestors(self, head):
        """head 자신을 포함한 조상 집합. 재귀 없이 긴 변경 이력을 탐색한다."""
        if head not in self._ancestors_cache:
            found, pending = set(), [head]
            while pending:
                identifier = pending.pop()
                _require(identifier in self.events, "공통 기준의 변경 이력이 빠져 있습니다.")
                if identifier in found:
                    continue
                found.add(identifier)
                pending.extend(self.events[identifier]["parents"])
            self._ancestors_cache[head] = frozenset(found)
        return self._ancestors_cache[head]

    def baseline_for(self, source_uuid, target_uuid=None):
        source = self._entity(self.source, source_uuid)
        target = self._entity(self.target, source_uuid if target_uuid is None else target_uuid)
        if source is None or target is None:
            return Baseline(False)
        if (source["table"] != target["table"] or
                self.aliases.get(source["uuid"], source["uuid"]) != self.aliases.get(target["uuid"], target["uuid"])):
            return Baseline(False, reason="identity_mismatch")
        heads = source["head"], target["head"]
        if heads in self._baseline_cache:
            return deepcopy(self._baseline_cache[heads])
        common = self.ancestors(heads[0]) & self.ancestors(heads[1])
        if not common:
            result = Baseline(False, reason="no_common_ancestor")
        else:
            # 공통 조상 집합은 부모에 대해 닫혀 있다. 공통 자식이 없는 노드만 최대 조상이다.
            maximal = tuple(sorted(identifier for identifier in common
                                   if not self.children[identifier].intersection(common)))
            values = [canonical_value(self.events[identifier]["after"], self.aliases) for identifier in maximal]
            if all(equivalent(values[0], value) for value in values[1:]):
                result = Baseline(True, deepcopy(values[0]), maximal,
                                  "common" if len(maximal) == 1 else "equivalent_common_ancestors")
            else:
                result = Baseline(False, UNAVAILABLE, maximal, "ambiguous_common_ancestors")
        self._baseline_cache[heads] = result
        return deepcopy(result)


def baseline_for(source_state, target_state, source_uuid, target_uuid=None):
    """단일 항목용 편의 함수. 여러 항목은 BaselineIndex를 한 번 만들어 사용한다."""
    return BaselineIndex(source_state, target_state).baseline_for(source_uuid, target_uuid)
