"""검증된 교환 계보의 기준값과 현재 DB 값을 같은 UUID 표현으로 비교한다."""

from dataclasses import dataclass
from collections import defaultdict
import json

from setron.features.peer_sync import canonical_ids, key_token
from setron.features.peer_sync.comparison import (
    BaselineIndex, MISSING, UNAVAILABLE, decide, portable_row,
)


PROVENANCE = frozenset(("batch_id", "source_reference", "edited_fields"))


@dataclass(frozen=True)
class Comparison:
    base: object = UNAVAILABLE
    current: object = MISSING
    incoming: object = MISSING
    reason: str = "공통 기준을 확인할 수 없습니다."
    revisions: tuple[str, ...] = ()

    @property
    def found(self):
        return self.base is not UNAVAILABLE

    @property
    def decision(self):
        return decide(self.base, self.current, self.incoming)


class ReviewAutomation:
    def __init__(self, source, target, repository, source_state, target_state,
                 source_identity, target_identity):
        self.source, self.target, self.repository = source, target, repository
        self.source_state, self.target_state = source_state, target_state
        self.source_identity, self.target_identity = source_identity, target_identity
        self.aliases = canonical_ids(source_state, target_state)
        self.index = BaselineIndex(source_state, target_state)
        self.primary = {name: tuple(repository.table(name).primary_key.columns.keys()) for name in source}
        self.source_rows = {name: {key_token({field: row[field] for field in self.primary[name]}): row
                                  for row in rows} for name, rows in source.items()}
        self.target_rows = {name: {key_token({field: row[field] for field in self.primary[name]}): row
                                  for row in rows} for name, rows in target.items()}
        self.source_entities = self._entities(source_state)
        self.target_entities = self._entities(target_state)
        self.related_by_snapshot = defaultdict(set)
        self.events = {**source_state.events, **target_state.events}
        for state in (source_state, target_state):
            for event in state.events.values():
                if event["table"] in {"equipment_version", "equipment_pm"}:
                    reference = (event.get("after") or {}).get("snapshot_id")
                    if isinstance(reference, dict) and reference.get("$ref") is not None:
                        self.related_by_snapshot[self.canonical_id(reference["$ref"])].add(self.canonical_id(event["entity"]))

    def _entities(self, state):
        result = {}
        for entity in state.entities.values():
            identifier = self.aliases.get(entity["uuid"], entity["uuid"])
            previous = result.get(identifier)
            if previous is None or entity.get("key") is not None and not entity["deleted"]:
                result[identifier] = entity
        return result

    def identity(self, name, row, *, source=False):
        identities = self.source_identity if source else self.target_identity
        return identities.get((name, key_token({field: row[field] for field in self.primary[name]})))

    def canonical_id(self, identifier):
        return self.aliases.get(identifier, identifier)

    def payload(self, name, row, *, source=False):
        if row is None:
            return None
        return portable_row(name, row, self.repository,
                            self.source_identity if source else self.target_identity, self.aliases)

    def baseline(self, source_uuid, target_uuid=None):
        if source_uuid is None:
            return None
        return self.index.baseline_for(source_uuid, target_uuid)

    def _original(self, name, row):
        return self.source_rows[name].get(key_token({field: row[field] for field in self.primary[name]}), row)

    def field(self, name, current, incoming, field):
        incoming = self._original(name, incoming)
        base = self.baseline(self.identity(name, incoming, source=True), self.identity(name, current))
        previous = self.payload(name, current)
        following = self.payload(name, incoming, source=True)
        return Comparison(base.payload.get(field, MISSING) if base and base.found and isinstance(base.payload, dict)
                          else UNAVAILABLE,
                          previous.get(field, MISSING), following.get(field, MISSING),
                          base.reason if base else "공통 기준을 확인할 수 없습니다.",
                          base.revisions if base and base.found else ())

    def row(self, name, current, incoming):
        incoming = self._original(name, incoming)
        base = self.baseline(self.identity(name, incoming, source=True), self.identity(name, current))
        strip = lambda value: ({key: item for key, item in value.items()
                                if key not in PROVENANCE and key not in self.primary[name]}
                               if isinstance(value, dict) else value)
        return Comparison(strip(base.payload) if base and base.found else UNAVAILABLE,
                          strip(self.payload(name, current)), strip(self.payload(name, incoming, source=True)),
                          base.reason if base else "공통 기준을 확인할 수 없습니다.",
                          base.revisions if base and base.found else ())

    def restoration(self, name, incoming):
        incoming = self._original(name, incoming)
        identifier = self.identity(name, incoming, source=True)
        target = self.target_entities.get(self.canonical_id(identifier))
        base = self.baseline(identifier, target["uuid"] if target else None)
        if name == "equipment_snapshot" and self.incoming_group_changed(identifier):
            return Comparison(UNAVAILABLE, None, self.payload(name, incoming, source=True),
                              "로컬에서 삭제한 설비의 PM·SW 구성에 새 변경이 있어 복원 여부를 선택해야 합니다.")
        return Comparison(base.payload if base and base.found else UNAVAILABLE, None,
                          self.payload(name, incoming, source=True), base.reason if base else "공통 기준 없음",
                          base.revisions if base and base.found else ())

    def incoming_group_changed(self, snapshot_uuid):
        """삭제된 부모의 값이 같아도 구성원에 아직 받지 않은 변경이 있는지 확인한다."""
        for identifier in self.related_by_snapshot.get(self.canonical_id(snapshot_uuid), ()):
            source = self.source_entities.get(identifier)
            if source is None:
                continue
            target = self.target_entities.get(identifier)
            if target is None:
                return True
            pending, seen = [target["head"]], set()
            while pending:
                head = pending.pop()
                if head == source["head"]:
                    break
                if head not in seen:
                    seen.add(head)
                    pending.extend(self.events[head]["parents"])
            else:
                return True
        return False

    def deletion(self, name, current, source_entity):
        base = self.baseline(source_entity["uuid"], self.identity(name, current))
        return Comparison(base.payload if base and base.found else UNAVAILABLE,
                          self.payload(name, current), None,
                          base.reason if base else "공통 기준을 확인할 수 없습니다.",
                          base.revisions if base and base.found else ())

    def snapshot_removes_current(self, current, incoming):
        """가져온 묶음을 택했을 때 실제 현재 구성원 중 사라지는 UUID가 있는지 확인한다."""
        incoming = self._original("equipment_snapshot", incoming)
        members = []
        for source, all_rows, snapshot in ((True, self.source, incoming), (False, self.target, current)):
            identifiers = set()
            for table in ("equipment_version", "equipment_pm"):
                for row in all_rows[table]:
                    if row["snapshot_id"] != snapshot["snapshot_id"]:
                        continue
                    identifier = self.identity(table, row, source=source)
                    # UUID가 없는 로컬 행도 실제 삭제 대상이다. 상대와 같다고 추정하지 않는다.
                    identifiers.add((table, self.canonical_id(identifier)) if identifier else
                                    (source, table, key_token({field: row[field] for field in self.primary[table]})))
            members.append(identifiers)
        return bool(members[1] - members[0])

    def snapshot(self, current, incoming):
        """모든 기존 구성원의 공통 이력이 확인된 경우에만 PM·SW 전체 기준을 만든다."""
        incoming = self._original("equipment_snapshot", incoming)
        source_uuid = self.identity("equipment_snapshot", incoming, source=True)
        target_uuid = self.identity("equipment_snapshot", current)
        root = self.baseline(source_uuid, target_uuid)
        if not root or not root.found or not isinstance(root.payload, dict):
            return Comparison(reason=root.reason if root else "PM·SW 구성의 공통 기준을 확인할 수 없습니다.")
        owner = self.canonical_id(source_uuid)
        related = set(self.related_by_snapshot.get(owner, ()))
        base_rows, current_rows, incoming_rows = {}, {}, {}
        found = True
        for all_rows, identities, sid in ((self.source, self.source_identity, incoming["snapshot_id"]),
                                          (self.target, self.target_identity, current["snapshot_id"])):
            for table in ("equipment_version", "equipment_pm"):
                for row in all_rows[table]:
                    if row["snapshot_id"] != sid:
                        continue
                    identifier = identities.get((table, key_token({field: row[field] for field in self.primary[table]})))
                    if identifier is None:
                        # 아직 교환 이력에 등록하지 않은 로컬 추가도 사용자의 현재 구성이다.
                        # 이를 기준 당시 부재로 추정하면 상대 구성을 적용하면서 조용히 지울 수 있다.
                        found = False
                    else:
                        # 기존 구성원을 다른 현황으로 옮긴 뒤 아직 내보내지 않았을 수 있다.
                        related.add(self.canonical_id(identifier))
        for identifier in related:
            source = self.source_entities.get(identifier)
            target = self.target_entities.get(identifier)
            baseline = self.baseline(source["uuid"], target["uuid"]) if source and target else None
            if baseline is None or not baseline.found:
                found = False
            elif baseline.payload is not None and baseline.payload.get("snapshot_id") == {"$ref": owner}:
                base_rows[identifier] = self._semantic(baseline.payload)
            for entity, original_rows, destination, source_flag in (
                    (source, self.source_rows, incoming_rows, True), (target, self.target_rows, current_rows, False)):
                if entity is None:
                    continue
                row = original_rows[entity["table"]].get(key_token(entity["key"])) if entity["key"] is not None else None
                if row is not None:
                    payload = self.payload(entity["table"], row, source=source_flag)
                    reference = payload.get("snapshot_id")
                    if reference == {"$ref": owner}:
                        destination[identifier] = self._semantic(payload)
        current_payload = self.payload("equipment_snapshot", current)
        incoming_payload = self.payload("equipment_snapshot", incoming, source=True)
        packed = lambda group, members: {"group_id": group, "members": members}
        return Comparison(packed(root.payload.get("group_id"), base_rows) if found else UNAVAILABLE,
                          packed(current_payload.get("group_id"), current_rows),
                          packed(incoming_payload.get("group_id"), incoming_rows),
                          "구성원 전체의 공통 변경 이력" if found else "새 구성원 등의 공통 이력이 없어 묶음을 직접 선택해야 합니다.",
                          root.revisions if found else ())

    @staticmethod
    def _semantic(payload):
        return {key: value for key, value in payload.items() if key not in PROVENANCE}

    def settings(self, current_rows, incoming_rows, group):
        """N 기준의 공통 버전과 그 버전에 속한 저장 이력을 하나의 기준으로 비교한다."""
        source_current = next((row for row in incoming_rows.values() if row["setting_key"] == group), None)
        target_current = current_rows.get(group)
        if source_current is None or target_current is None:
            return Comparison(reason="N 그룹의 공통 기준을 확인할 수 없습니다.")
        # incoming_rows는 로컬 FK로 변환돼 있으므로 원본 키는 호출자의 사전에 보존한다.
        source_current = next((self.source_rows["app_setting"].get(key_token({"setting_key": original}))
                               for original, row in incoming_rows.items() if row["setting_key"] == group), None)
        if source_current is None:
            return Comparison(reason="N 그룹의 원본 기준을 확인할 수 없습니다.")
        uid = self.identity("app_setting", source_current, source=True)
        root = self.baseline(uid, self.identity("app_setting", target_current))
        source_payloads = {}
        for original in incoming_rows:
            row = self.source_rows["app_setting"].get(key_token({"setting_key": original}))
            if row:
                source_payloads[self._setting_address(self.payload("app_setting", row, source=True))] = self._semantic(self.payload("app_setting", row, source=True))
        target_payloads = {self._setting_address(self.payload("app_setting", row)): self._semantic(self.payload("app_setting", row))
                           for row in current_rows.values()}
        if not root or not root.found or not isinstance(root.payload, dict):
            return Comparison(UNAVAILABLE, target_payloads, source_payloads, root.reason if root else "공통 기준 없음")
        value = root.payload.get("setting_value")
        if not isinstance(value, dict):
            return Comparison(UNAVAILABLE, target_payloads, source_payloads, "N 그룹 공통 기준 형식 미확인")
        schema = value.get("schema")
        sequence = value.get("sequence")
        if type(schema) is not int or schema not in {1, 2, 3} or (
                schema != 1 and (type(sequence) is not int or not 1 <= sequence < 10 ** 20)):
            return Comparison(UNAVAILABLE, target_payloads, source_payloads, "N 그룹의 과거 기준 형식을 확인할 수 없습니다.")
        if schema == 1 and (not isinstance(value.get("history"), list) or not value["history"]):
            return Comparison(UNAVAILABLE, target_payloads, source_payloads, "N 그룹의 과거 저장 이력이 완전하지 않습니다.")
        if schema == 3 and (type(value.get("active_sequence")) is not int or not 1 <= value["active_sequence"] <= sequence):
            return Comparison(UNAVAILABLE, target_payloads, source_payloads, "N 그룹의 과거 활성 기준을 확인할 수 없습니다.")
        expected = 0 if schema == 1 else sequence - (schema == 2)
        base = {self._setting_address(root.payload): self._semantic(root.payload)}
        owner = root.payload.get("setting_key", {}).get("references")
        histories = {}
        for identifier, source in self.source_entities.items():
            if source["table"] != "app_setting" or identifier not in self.target_entities:
                continue
            target = self.target_entities[identifier]
            baseline = self.baseline(source["uuid"], target["uuid"])
            payload = baseline.payload if baseline.found else None
            key = payload.get("setting_key") if isinstance(payload, dict) else None
            if not isinstance(key, dict) or key.get("prefix") != "version_groups.history.v2" or key.get("references") != owner:
                continue
            suffix = key.get("suffix")
            if not isinstance(suffix, list) or len(suffix) != 1 or not isinstance(suffix[0], str) or len(suffix[0]) != 20 or not suffix[0].isascii() or not suffix[0].isdecimal():
                return Comparison(UNAVAILABLE, target_payloads, source_payloads, "N 그룹 과거 이력의 순번을 확인할 수 없습니다.")
            number = int(suffix[0])
            if 1 <= number <= expected:
                if number in histories:
                    return Comparison(UNAVAILABLE, target_payloads, source_payloads, "N 그룹 저장 이력의 공통 기준이 중복됩니다.")
                histories[number] = payload
        if len(histories) != expected:
            return Comparison(UNAVAILABLE, target_payloads, source_payloads, "N 그룹 저장 이력의 공통 기준이 완전하지 않습니다.")
        for payload in histories.values():
            base[self._setting_address(payload)] = self._semantic(payload)
        return Comparison(base, target_payloads, source_payloads, "N 기준과 저장 이력의 공통 버전", root.revisions)

    @staticmethod
    def _setting_address(payload):
        return json.dumps(payload.get("setting_key"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
