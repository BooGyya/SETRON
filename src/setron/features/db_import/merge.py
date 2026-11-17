"""원본 DB의 내부 ID를 대상 DB의 업무 키와 참조 관계에 맞춰 변환한다."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from copy import deepcopy
from datetime import date, datetime

from sqlalchemy import Date, DateTime

from setron.features.storage.validation import JSON_COLUMNS, TABLE_NAMES, decode_record, encode_record

from .dump import DUMP_COLUMNS, DumpFormatError, allowed_columns, project_row, with_legacy_jira
from .group_settings import group_history_prune
from .hierarchy import HIERARCHY_KEYS, merge_hierarchy_setting, validate_hierarchy
from .references import ReferenceConflict, rewrite_json
from .validation import validate_merge


class SqlImportError(ValueError):
    """SQL 가져오기 화면에 표시할 오류."""


@dataclass(frozen=True)
class Write:
    action: str
    table: str
    key: dict
    row: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Difference:
    id: str
    table: str
    label: str
    key: dict
    field: str
    current: object
    incoming: object
    choice: str | None = None
    grouped: bool = False
    baseline: object = None
    has_baseline: bool = False
    automatic: bool = False
    reason: str = ""
    requires_confirmation: bool = False


@dataclass(frozen=True)
class MergePlan:
    mode: str
    destination: str
    revision: str
    target_rows: int
    writes: tuple[Write, ...]
    summary: str
    differences: tuple[Difference, ...] = ()
    choices: dict = field(default_factory=dict)
    id_maps: dict = field(default_factory=dict, repr=False)
    _source: dict | None = field(default=None, repr=False, compare=False)
    sync_changes: int = 0
    _receipts: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def changes(self):
        return len(self.writes) + self.sync_changes

    @property
    def unresolved(self):
        return sum(value.choice is None for value in self.differences)

    @property
    def automatic_count(self):
        return sum(value.automatic for value in self.differences)

    @property
    def conflict_count(self):
        return self.unresolved


# 자동 증가 ID는 DB별 값이다. 업무 키가 없는 이력·문서 등은 내용과 연결이 같은 행만 재사용한다.
BUSINESS_KEYS = {
    "equipment_group": ("group_name",),
    "equipment": ("equipment_code",),
    "version_family": ("group_id", "family_name", "ui_type"),
    "release_version": ("family_id", "version_raw"),
    "equipment_snapshot": ("equipment_id", "base_date"),
    "equipment_version": ("snapshot_id", "family_id", "version_raw"),
    "equipment_pm": ("snapshot_id", "pm_name"),
    "release_version_item": ("release_version_id", "item_id"),
    "app_setting": ("setting_key",),
}
SNAPSHOT_CHILDREN = ("equipment_version", "equipment_pm")
ARCHIVE_KEY = b"sql_import.batch.v1:"


def _json_default(value):
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, (date, datetime)):
        return {"date": value.isoformat()}
    raise TypeError(type(value).__name__)


def _serialized(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def fingerprint(rows, next_ids):
    digest = hashlib.sha256()
    for name in TABLE_NAMES:
        for row in rows[name]:
            digest.update(_serialized((name, row)).encode("utf-8"))
    digest.update(_serialized(next_ids).encode("utf-8"))
    return digest.hexdigest()


def _normalized(table, row, repository):
    try:
        values = project_row(table.name, row, tuple(table.columns.keys()))
    except DumpFormatError as error:
        raise SqlImportError(str(error)) from None
    for column in table.columns:
        value = values[column.name]
        if isinstance(value, str):
            try:
                if isinstance(column.type, DateTime):
                    values[column.name] = datetime.fromisoformat(value)
                elif isinstance(column.type, Date):
                    values[column.name] = date.fromisoformat(value)
            except ValueError:
                raise SqlImportError(f"{table.name}.{column.name}의 날짜 형식이 올바르지 않습니다.") from None
    # 기존 관리 서비스와 같은 원문·JSON·상태값 검증을 적용한다.
    validated = encode_record(table.name, decode_record(table.name, values))
    try:
        validated = project_row(table.name, validated, tuple(table.columns.keys()))
    except DumpFormatError as error:
        raise SqlImportError(str(error)) from None
    repository.validate_columns(table, validated, inserting=True)
    return validated


def _key(row, columns):
    return tuple(row[column] for column in columns)


def _business_key(row, columns):
    return tuple(row[c].strip().casefold() if c in {"group_name", "family_name"} else row[c] for c in columns)


def _payload(table, row, pk):
    return tuple((column, _serialized(json.loads(value)) if value is not None and column in JSON_COLUMNS.get(table, {})
                  else value) for column, value in sorted(row.items()) if column not in pk)


def _display(value):
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _decoded_values(name, values):
    return {key: json.loads(value) if key in JSON_COLUMNS.get(name, {}) and value is not None
            else value.decode("utf-8") if isinstance(value, bytes) else value for key, value in values.items()}


def _mixed_sources(current, incoming, selected_fields):
    old, new = (json.loads(row.get("source_reference") or "{}") for row in (current, incoming))
    result = {}
    for field_name in old.keys() | new.keys():
        owner, locations = (incoming, new.get(field_name)) if field_name in selected_fields else (current, old.get(field_name))
        if locations is None:
            continue
        copied = deepcopy(locations)
        for location in copied if isinstance(copied, list) else (copied,):
            if owner.get("batch_id") is not None:
                location.setdefault("batch_id", owner["batch_id"])
        result[field_name] = copied
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")) if result else None


def _ordered_groups(rows):
    by_id = {row["group_id"]: row for row in rows}
    children = defaultdict(list)
    for row in rows:
        parent = row["parent_group_id"]
        if parent is not None and parent not in by_id:
            raise SqlImportError("설비군의 상위 모델을 찾을 수 없습니다.")
        children[parent].append(row)
    pending = deque(children[None])
    result = []
    while pending:
        row = pending.popleft()
        result.append(row)
        pending.extend(children[row["group_id"]])
    if len(result) != len(rows):
        raise SqlImportError("설비군의 상위 모델이 순환합니다. 모델 연결을 확인해 주세요.")
    return result


class _Planner:
    def __init__(self, source, target, next_ids, repository, mode, choices=None):
        self.target, self.next_ids, self.mode = target, next_ids, mode
        self.repository = repository
        self.choices = dict(choices or {})
        if any(value not in {"CURRENT", "INCOMING"} for value in self.choices.values()):
            raise SqlImportError("비교 항목의 적용 값을 선택해 주세요.")
        self.differences = []
        self.receipts = defaultdict(dict)
        self.chosen_settings = set()
        self.reviewed_setting_keys = set()
        self.restoring_snapshots = set()
        self.review_originals = {}
        self.tables = {name: repository.table(name) for name in TABLE_NAMES}
        if any(tuple(table.columns.keys()) not in allowed_columns(name)
               for name, table in self.tables.items()):
            raise SqlImportError("대상 DB의 테이블 구조와 지원하는 SETRON 백업 형식이 다릅니다.")
        # UUID head는 파일에 실제 저장된 구조를 설명한다. 호환 열 보강 전 원본으로 검증한다.
        from setron.features.peer_sync import canonical_ids, identity_index, read_state, validate_exchange
        self.raw_source = deepcopy(source)
        validate_exchange(self.raw_source, repository)
        try:
            source = with_legacy_jira(source, tuple(self.tables["item"].columns.keys()))
        except DumpFormatError as error:
            raise SqlImportError(str(error)) from None
        self.primary = {name: tuple(table.primary_key.columns.keys()) for name, table in self.tables.items()}
        self.foreign = {name: tuple(sorted((fk.parent.name, fk.column.table.name, fk.column.name)
                                          for fk in table.foreign_keys)) for name, table in self.tables.items()}
        self.source = {name: tuple(_normalized(self.tables[name], row, repository) for row in source[name])
                       for name in TABLE_NAMES}
        self.exchange_digest = hashlib.sha256(_serialized(self.raw_source).encode("utf-8")).hexdigest()
        self.current = {name: {_key(row, self.primary[name]): row for row in target[name]} for name in TABLE_NAMES}
        self.source_by_pk = {name: {_key(row, self.primary[name]): row for row in self.source[name]}
                             for name in TABLE_NAMES}
        self.ids = defaultdict(dict)
        self.writes = []
        self.counts = Counter()
        self.details = defaultdict(Counter)
        self.closed_snapshots = set()
        self.replaced_snapshots = set()
        self.skipped_snapshots = set()
        self.incoming_children = defaultdict(set)
        self.sites = {}
        self.rewritten_batches = []
        self.remapped = False
        self.retained = set()
        self.children_cache = {}
        self.child_rows = {}
        for source_flag, all_rows in ((False, self.target), (True, self.source)):
            indexed = {name: defaultdict(list) for name in SNAPSHOT_CHILDREN}
            for name in SNAPSHOT_CHILDREN:
                for row in all_rows[name]:
                    indexed[name][row["snapshot_id"]].append(row)
            self.child_rows[source_flag] = indexed
        self._validate_source()
        self.source_identity = identity_index(self.raw_source)
        self.target_identity = identity_index(target)
        self.source_state, self.target_state = read_state(self.raw_source), read_state(target)
        canonical = canonical_ids(self.source_state, self.target_state)
        self.canonical_identity = canonical
        self.peer_events = {**self.source_state.events, **self.target_state.events}
        for identifier in self.source_state.events.keys() & self.target_state.events.keys():
            if self.source_state.events[identifier] != self.target_state.events[identifier]:
                raise SqlImportError("같은 변경 UUID의 내용이 다릅니다. 파일을 다시 확인해 주세요.")
        self.target_entities = {canonical.get(entity["uuid"], entity["uuid"]): entity
                                for entity in self.target_state.entities.values()}
        self.source_entities = {(entity["table"], _serialized(entity["key"])): entity
                                for entity in self.source_state.entities.values() if entity["key"] is not None}
        self.uuid_targets = defaultdict(dict)
        from setron.features.peer_sync import key_token
        for name, rows in target.items():
            for row in rows:
                uid = self.target_identity.get((name, key_token({c: row[c] for c in self.primary[name]})))
                if uid:
                    identifier = canonical.get(uid, uid)
                    if identifier in self.uuid_targets[name]:
                        raise SqlImportError("같은 UUID가 여러 현재 항목에 연결되어 있습니다.")
                    self.uuid_targets[name][identifier] = row
        for entity in self.target_state.entities.values():
            identifier = canonical.get(entity["uuid"], entity["uuid"])
            row = self.uuid_targets[entity["table"]].get(identifier)
            if row is not None and entity["key"] is not None and key_token(entity["key"]) == key_token(
                    {c: row[c] for c in self.primary[entity["table"]]}):
                self.target_entities[identifier] = entity
        for row in target["equipment_snapshot"]:
            self.sites.setdefault(row["equipment_id"], set()).add(row["site"].strip().casefold())
        from .automatic import ReviewAutomation
        self.automation = ReviewAutomation(self.raw_source, target, repository, self.source_state,
                                           self.target_state, self.source_identity, self.target_identity)

    def _validate_source(self):
        for name, rows in self.source.items():
            pk = self.primary[name]
            if len(self.source_by_pk[name]) != len(rows):
                raise SqlImportError(f"{name}의 기본 키가 중복됩니다.")
            for row in rows:
                for column in pk:
                    value = row[column]
                    if value is None or (isinstance(value, int) and value <= 0):
                        raise SqlImportError(f"{name}의 기본 키가 올바르지 않습니다.")
                for column, parent, remote in self.foreign[name]:
                    value = row[column]
                    if value is not None and self.primary[parent] == (remote,) and (value,) not in self.source_by_pk[parent]:
                        raise SqlImportError(f"{name}의 {column}이 파일에 없는 항목을 참조합니다.")
                if name == "equipment_pm" and row["equipment_version_id"] is not None:
                    version = self.source_by_pk["equipment_version"].get((row["equipment_version_id"],))
                    if version is None or version["snapshot_id"] != row["snapshot_id"]:
                        raise SqlImportError("PM의 적용 버전이 파일의 같은 설비 현황에 속하지 않습니다.")
                if name == "equipment_version" and row["release_version_id"] is not None:
                    release = self.source_by_pk["release_version"][(row["release_version_id"],)]
                    if release["family_id"] != row["family_id"]:
                        raise SqlImportError("적용 SW와 연결 릴리즈의 버전 계열이 다릅니다.")
                if name == "risk_rule":
                    for field in ("release_version_id", "start_release_version_id", "end_release_version_id"):
                        if row[field] is not None:
                            release = self.source_by_pk["release_version"][(row[field],)]
                            if release["family_id"] != row["family_id"]:
                                raise SqlImportError("위험 정보와 연결 릴리즈의 버전 계열이 다릅니다.")
        sites = {}
        for row in self.source["equipment_snapshot"]:
            site = row["site"].strip().casefold()
            if sites.setdefault(row["equipment_id"], site) != site:
                raise SqlImportError("SQL 파일에 같은 호기의 서로 다른 사업장이 있습니다.")
        try:
            validate_hierarchy(self.source)
        except ValueError as error:
            raise SqlImportError(str(error)) from None

    def resolve(self, name, source_id):
        return self.ids[name][source_id]

    def _count(self, status, name, *, note=None):
        self.counts[status] += 1
        self.details[name][status] += 1
        if note:
            self.counts[note] += 1

    def _write(self, action, name, row):
        pk = self.primary[name]
        self.writes.append(Write(action, name, {column: row[column] for column in pk}, row))
        self._count(action, name)

    def _index(self, name, columns):
        existing = defaultdict(deque)
        for row in self.target[name]:
            key = _business_key(row, columns) if columns else _payload(name, row, self.primary[name])
            existing[key].append(row)
        return existing

    def _link(self, name, original, mapped):
        if len(self.primary[name]) == 1 and (isinstance(original[0], int) or name == "app_setting"):
            self.ids[name][original[0]] = mapped[0]
            self.remapped |= original != mapped

    def _remap_row(self, name, source_row):
        row = dict(source_row)
        for column, parent, remote in self.foreign[name]:
            if row[column] is not None and self.primary[parent] == (remote,):
                row[column] = self.resolve(parent, source_row[column])
        if name != "import_batch":
            row = rewrite_json(name, row, self.resolve, ids_changed=self.remapped or bool(self.retained))
        return row

    def _snapshot_allowed(self, row):
        site = row["site"].strip().casefold()
        sites = self.sites.setdefault(row["equipment_id"], set())
        if sites and sites != {site}:
            return False
        sites.add(site)
        return True

    def _archive_marker(self, row):
        value = _serialized((row["batch_id"], _payload("import_batch", row, self.primary["import_batch"])))
        return ARCHIVE_KEY + hashlib.sha256(value.encode("utf-8")).hexdigest().encode("ascii")

    def _archived_batch(self, row):
        marker = self.current["app_setting"].get((self._archive_marker(row),))
        if marker is None:
            return None
        value = json.loads(marker["setting_value"])
        return self.current["import_batch"].get((value,)) if type(value) is int else None

    def _item_matches(self):
        """Item 번호가 같아도 연결된 릴리즈 집합까지 유일하게 일치할 때만 동일 항목이다."""
        def by_release(rows, links, resolve_release):
            releases = defaultdict(set)
            for link in links:
                release = resolve_release(link["release_version_id"])
                releases[link["item_id"]].add(release)
            result = defaultdict(list)
            for row in rows:
                versions = releases[row["item_id"]]
                if row["item_no"] is not None and versions and None not in versions:
                    result[row["item_no"], frozenset(versions)].append(row)
            return result

        target = by_release(self.target["item"], self.target["release_version_item"], lambda value: value)
        source = by_release(self.source["item"], self.source["release_version_item"], self.ids["release_version"].get)
        return {rows[0]["item_id"]: target[key][0] for key, rows in source.items()
                if len(rows) == 1 and len(target.get(key, ())) == 1}

    def _setting_changes(self, rows):
        incoming = {}
        for row in rows:
            key = row["setting_key"]
            from setron.features.peer_sync import is_sync_key
            if key.startswith(ARCHIVE_KEY) or is_sync_key(key):
                continue
            try:
                mapped = self._remap_row("app_setting", row)
                incoming[key] = (mapped if self.mode == "review" else merge_hierarchy_setting(mapped,
                    self.current["app_setting"].get((mapped["setting_key"],)), self.mode))
            except ReferenceConflict:
                if key in HIERARCHY_KEYS:
                    raise SqlImportError("계층 설정의 참조를 확인할 수 없어 SQL을 병합할 수 없습니다.") from None
                self._count("skip", "app_setting", note="references")
            except ValueError as error:
                raise SqlImportError(str(error)) from None
        try:
            suppressed, deleted = self._review_group_settings(incoming) if self.mode == "review" else group_history_prune(
                incoming.values(), self.target["app_setting"], self.mode)
        except ValueError as error:
            raise SqlImportError(str(error)) from None
        for original_key, row in tuple(incoming.items()):
            if row["setting_key"] in suppressed:
                del incoming[original_key]
                self._count("skip", "app_setting", note="group_history")
        return incoming, deleted

    def _review_group_settings(self, incoming):
        """현재 N 기준과 그 순번의 저장 이력을 한 선택으로 유지하거나 바꾼다."""
        orphaned, _ = group_history_prune(incoming.values(), (), "overwrite")
        def pair(key):
            if key.startswith(b"version_groups.v1:"):
                return key
            if key.startswith(b"version_groups.history.v2:"):
                return b"version_groups.v1:" + key[len(b"version_groups.history.v2:"):].rpartition(b":")[0]
            return None
        sources, targets = defaultdict(dict), defaultdict(dict)
        for original, row in incoming.items():
            if row["setting_key"] in orphaned:
                continue
            if group := pair(row["setting_key"]):
                sources[group][original] = row
        for row in self.target["app_setting"]:
            if group := pair(row["setting_key"]):
                targets[group][row["setting_key"]] = row
        suppressed, deleted = set(orphaned), set()
        for group, rows in sources.items():
            previous = targets.get(group)
            if not previous:
                continue
            self.reviewed_setting_keys.update(previous)
            self.reviewed_setting_keys.update(row["setting_key"] for row in rows.values())
            current = {key.decode("utf-8"): json.loads(row["setting_value"]) for key, row in previous.items()}
            new = {row["setting_key"].decode("utf-8"): json.loads(row["setting_value"]) for row in rows.values()}
            if current == new:
                self.chosen_settings.update(row["setting_key"] for row in rows.values())
                continue
            from setron.features.peer_sync import key_token
            identifier = self.target_identity.get(("app_setting", key_token({"setting_key": group})))
            target = self.target_entities.get(self.canonical_identity.get(identifier, identifier)) if identifier else None
            if target is not None and self._has_group_receipt(target["head"], self.exchange_digest):
                suppressed.update(row["setting_key"] for row in rows.values())
                continue  # 같은 파일에 대해 이미 검토한 N 묶음 선택을 다시 기록하지 않는다.
            display = lambda values: {"현재 기준" if key == group.decode("utf-8") else
                                      "저장 이력 " + str(int(key.rpartition(":")[2])): value for key, value in values.items()}
            row = previous.get(group) or {"setting_key": group}
            comparison = self.automation.settings(previous, rows, group)
            incoming_keys = {row["setting_key"] for row in rows.values()}
            if self._choose("app_setting", row, "N 그룹·저장 이력", display(current), display(new),
                            grouped=True, comparison=comparison,
                            requires_confirmation=bool(set(previous) - incoming_keys)):
                self.chosen_settings.update(incoming_keys)
                deleted.update(set(previous) - incoming_keys)
            else:
                suppressed.update(row["setting_key"] for row in rows.values())
        return suppressed, deleted

    def _label(self, name, row):
        if name == "equipment_snapshot":
            equipment = self.current["equipment"].get((row["equipment_id"],), {})
            code = equipment.get("equipment_code", row["equipment_id"])
            return f"{_display(code)} · {row['base_date']}"
        if name == "app_setting":
            key = row["setting_key"].decode("utf-8")
            for prefix, caption in (("version_groups.v1:", "N 그룹"), ("target_applicability.v1:", "적용 가능 버전"),
                                    ("shared_note_item_models.v1:", "공유 Item 적용 모델")):
                if key.startswith(prefix):
                    values = key[len(prefix):].split(":")
                    if len(values) >= 2 and all(value.isdecimal() for value in values[:2]):
                        tables = ("release_version", "item") if prefix.startswith("shared") else ("equipment_group", "version_family")
                        return " · ".join((caption, *(self._reference_name(table, int(value)) for table, value in zip(tables, values))))
        for field_name in ("equipment_code", "group_name", "family_name", "version_raw", "item_no", "setting_key"):
            if row.get(field_name) is not None:
                return _display(row[field_name])
        return name + " · " + "/".join(_display(row[c]) for c in self.primary[name])

    def _known_source(self, name, original):
        from setron.features.peer_sync import key_token
        uid = self.source_identity.get((name, key_token({c: original[c] for c in self.primary[name]})))
        source = self.source_state.entities.get(uid)
        target = self.target_entities.get(self.canonical_identity.get(uid, uid)) if uid else None
        known = source is not None and target is not None and self._known_head(source["head"], target["head"])
        if known and name == "equipment_snapshot" and not target["deleted"]:
            matched = self.uuid_targets[name].get(self.canonical_identity.get(uid, uid))
            if matched is None:
                return not self.automation.incoming_group_changed(uid)
            group = self.resolve("equipment_group", original["group_id"]) if original["group_id"] is not None else None
            same = matched is not None and matched["group_id"] == group and self._children(
                matched["snapshot_id"]) == self._children(original["snapshot_id"], source=True)
            return same or self._has_group_receipt(target["head"], self._group_digest(original))
        return known

    def _has_group_receipt(self, head, digest):
        pending, seen = [head], set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            event = self.peer_events[current]
            selection = event.get("selection") or {}
            receipts = selection.get("entities", {})
            if selection.get("source_group_digest") == digest or any(
                    value.get("source_group_digest") == digest for value in receipts.values()):
                return True
            pending.extend(event["parents"])
        return False

    def _group_digest(self, snapshot):
        from setron.features.peer_sync import key_token
        uid = self.source_identity.get(("equipment_snapshot", key_token({"snapshot_id": snapshot["snapshot_id"]})))
        if not uid:
            value = ("legacy", self._children(snapshot["snapshot_id"], source=True), snapshot["group_id"])
        else:
            members = []
            for entity in self.source_state.entities.values():
                name = entity["table"]
                if name not in SNAPSHOT_CHILDREN:
                    continue
                key = entity.get("key") or {}
                live = self.source_by_pk[name].get(tuple(key.get(c) for c in self.primary[name]))
                related = live is not None and live["snapshot_id"] == snapshot["snapshot_id"]
                if entity["deleted"]:
                    pending, seen = [entity["head"]], set()
                    while pending and not related:
                        head = pending.pop()
                        if head in seen:
                            continue
                        seen.add(head)
                        event = self.source_state.events[head]
                        reference = (event.get("after") or {}).get("snapshot_id") or {}
                        related = reference.get("$ref") == uid
                        pending.extend(event["parents"])
                if related:
                    members.append((entity["uuid"], entity["head"]))
            group_uid = self.source_identity.get(("equipment_group", key_token({"group_id": snapshot["group_id"]})))
            value = (uid, sorted(members), group_uid)
        return hashlib.sha256(_serialized(value).encode("utf-8")).hexdigest()

    def _known_head(self, incoming, current):
        pending, seen = [current], set()
        while pending:
            head = pending.pop()
            if head == incoming:
                return True
            if head not in seen:
                seen.add(head)
                pending.extend(self.peer_events[head]["parents"])
        return False

    def _choose(self, name, row, field_name, current, incoming, *, grouped=False,
                comparison=None, allow_automatic=True, requires_confirmation=False):
        pk = {c: row[c] for c in self.primary[name]}
        token = _serialized((name, pk, field_name))
        identifier = hashlib.sha256(token.encode("utf-8")).hexdigest()
        choice = self.choices.get(identifier)
        if comparison is None:
            from setron.features.peer_sync import key_token
            original = self.review_originals.get((name, key_token(pk)))
            if original is not None:
                comparison = (self.automation.row(name, row, original) if grouped else
                              self.automation.field(name, row, original, field_name))
        automatic = False
        has_baseline = bool(comparison and comparison.found)
        reason = comparison.reason if comparison else "공통 기준을 확인할 수 없어 직접 선택해야 합니다."
        reason = {"no_common_ancestor": "공통 변경 이력이 없어 직접 선택해야 합니다.",
                  "ambiguous_common_ancestors": "공통 기준이 여러 갈래로 달라 직접 선택해야 합니다.",
                  "identity_unavailable": "항목의 공통 식별 이력이 없어 직접 선택해야 합니다.",
                  "identity_mismatch": "같은 항목의 이력인지 확인할 수 없어 직접 선택해야 합니다.",
                  "common": "공통 변경 이력을 확인했습니다.",
                  "equivalent_common_ancestors": "같은 값을 가진 공통 변경 이력을 확인했습니다."}.get(reason, reason)
        if has_baseline:
            from setron.features.peer_sync import MISSING
            if comparison.current is not MISSING and comparison.incoming is not MISSING:
                decision = comparison.decision
                reasons = {"current_only": "현재 PC에서만 변경되어 현재값을 유지합니다.",
                           "incoming_only": "다른 PC에서만 변경되어 가져온값을 적용합니다.",
                           "same_change": "양쪽이 같은 값으로 변경했습니다.",
                           "unchanged": "공통 기준과 같은 값입니다.",
                           "conflict": "양쪽에서 다르게 변경되어 적용 값을 선택해야 합니다."}
                reason = reasons.get(decision.kind, reason)
                if (choice is None and allow_automatic and decision.choice is not None and
                        not (requires_confirmation and decision.choice == "INCOMING")):
                    choice, automatic = decision.choice, True
        if identifier in self.choices:
            reason = "사용자가 선택한 적용 값입니다."
        elif not allow_automatic or requires_confirmation and choice != "CURRENT":
            reason = "삭제 항목은 개별 확인 후 반영합니다."
        self.differences.append(Difference(identifier, name, self._label(name, row), pk, field_name,
                                           self._public_value(current, field_name),
                                           self._public_value(incoming, field_name), choice, grouped,
                                           self._baseline_public(comparison.base) if has_baseline else None,
                                           has_baseline, automatic, reason, requires_confirmation))
        if choice is not None:
            from setron.features.peer_sync import key_token
            receipt = self.receipts[name].setdefault(key_token(pk), {
                "source_group_digest": self.exchange_digest, "choice": choice, "fields": {}})
            receipt.setdefault("fields", {})[field_name] = choice
            receipt.setdefault("decisions", {})[field_name] = {
                "choice": choice, "automatic": automatic, "reason": reason,
                "baseline_revisions": list(comparison.revisions) if comparison and comparison.found else [],
            }
        return choice == "INCOMING"

    def _baseline_public(self, value):
        from setron.features.peer_sync import MISSING
        if value is MISSING:
            return "해당 항목 없음"
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                identity = self.canonical_identity.get(value["$ref"], value["$ref"])
                for table, rows in self.uuid_targets.items():
                    row = rows.get(identity)
                    if row is not None:
                        return self._reference_name(table, next(iter({key: row[key] for key in self.primary[table]}.values())))
                return "과거 연결 자료"
            if set(value) == {"$binary"}:
                return bytes.fromhex(value["$binary"]).decode("utf-8", errors="replace")
            if set(value) in ({"$date"}, {"$datetime"}):
                return next(iter(value.values()))
            if "members" in value and "group_id" in value:
                return {"대표 모델": self._baseline_public(value["group_id"]),
                        "PM·SW 구성": [self._baseline_public(item) for item in value["members"].values()]}
            if value and all(isinstance(item, dict) and isinstance(item.get("setting_key"), dict)
                             for item in value.values()):
                settings = {}
                for item in value.values():
                    key = item["setting_key"]
                    suffix = key.get("suffix") or []
                    caption = "저장 이력 " + str(int(suffix[0])) if key.get("prefix") == "version_groups.history.v2" and suffix else "현재 기준"
                    settings[caption] = self._baseline_public(item.get("setting_value"))
                return settings
            labels = {"group_id": "모델", "parent_group_id": "상위 모델", "family_id": "계열",
                      "model_id": "모델",
                      "release_version_id": "릴리즈", "equipment_version_id": "연결 SW",
                      "version_raw": "버전", "pm_name": "PM", "model_name_raw": "모델 원문",
                      "setting_value": "설정값", "line": "라인", "process_name": "공정"}
            return {labels.get(key, key): self._baseline_public(item) for key, item in value.items()
                    if key not in {"snapshot_id", "batch_id", "source_reference", "edited_fields"}}
        if isinstance(value, (list, tuple)):
            return [self._baseline_public(item) for item in value]
        return value

    def _reference_name(self, table, identifier):
        if identifier is None:
            return None
        row = self.current[table].get((identifier,))
        if row is None:
            return "연결 자료 없음"
        if table == "version_family":
            group = self.current["equipment_group"].get((row["group_id"],), {})
            return " · ".join(str(value) for value in (group.get("group_name"), row["family_name"], row.get("ui_type")) if value)
        if table == "item":
            return f"{row.get('item_no') or '-'}. {row['content']}"
        if table == "release_note":
            return str(row.get("release_date") or "날짜 미지정")
        for column in ("group_name", "equipment_code", "version_raw", "pm_name"):
            if column in row:
                return _display(row[column])
        return "연결 자료"

    def _public_value(self, value, field_name=None):
        references = {"group_id": "equipment_group", "model_id": "equipment_group", "parent_group_id": "equipment_group",
                      "family_id": "version_family", "equipment_id": "equipment", "release_version_id": "release_version",
                      "release_id": "release_version", "start_release_version_id": "release_version",
                      "end_release_version_id": "release_version", "release_note_id": "release_note", "item_id": "item"}
        labels = {"group_id": "모델", "model_id": "모델", "parent_group_id": "상위 모델", "family_id": "계열",
                  "equipment_id": "설비", "release_version_id": "릴리즈", "release_id": "릴리즈",
                  "start_release_version_id": "시작 버전", "end_release_version_id": "해제 버전",
                  "release_note_id": "릴리즈 노트", "item_id": "Item", "계열 ID": "계열", "모델 ID": "모델", "릴리즈 ID": "릴리즈"}
        private = {"batch_id", "source_reference", "edited_fields", "fingerprint", "pair_fingerprint", "schema",
                   "snapshot_id", "equipment_version_id", "pm_id", "risk_rule_id", "setting_key"}
        if field_name in references and (value is None or type(value) is int):
            return self._reference_name(references[field_name], value)
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key in private or key.endswith("_fingerprint"):
                    continue
                if key in {"계열 ID", "모델 ID", "릴리즈 ID"}:
                    target = {"계열 ID": "version_family", "모델 ID": "equipment_group", "릴리즈 ID": "release_version"}[key]
                    result[labels[key]] = self._reference_name(target, item)
                elif key == "groups" and isinstance(item, dict):
                    result["N 그룹"] = {slot: [self._reference_name("release_version", identifier) for identifier in members]
                                          for slot, members in item.items()}
                else:
                    result[labels.get(key, key)] = self._public_value(item, key)
            return result
        if isinstance(value, (list, tuple)):
            if field_name in {"target_group_ids", "model_ids"}:
                return [self._reference_name("equipment_group", item) for item in value]
            return [self._public_value(item) for item in value]
        return value.decode("utf-8") if isinstance(value, bytes) else deepcopy(value)

    def _children(self, snapshot_id, *, source=False):
        cache_key = source, snapshot_id
        if cache_key in self.children_cache:
            return self.children_cache[cache_key]
        rows = {name: self.child_rows[source][name][snapshot_id] for name in SNAPSHOT_CHILDREN}
        versions = {row["equipment_version_id"]: row for row in rows["equipment_version"]}
        result = []
        for sw in versions.values():
            family = self.resolve("version_family", sw["family_id"]) if source else sw["family_id"]
            release = sw["release_version_id"]
            if source and release is not None:
                release = self.resolve("release_version", release)
            pms = []
            for pm in rows["equipment_pm"]:
                if pm["snapshot_id"] == snapshot_id and pm["equipment_version_id"] == sw["equipment_version_id"]:
                    group = self.resolve("equipment_group", pm["group_id"]) if source else pm["group_id"]
                    pms.append({"PM": _display(pm["pm_name"]), "모델 ID": group, "모델": pm["model_name_raw"]})
            result.append({"계열 ID": family, "버전": _display(sw["version_raw"]), "릴리즈 ID": release,
                           "PM": sorted(pms, key=_serialized)})
        for pm in rows["equipment_pm"]:
            if pm["snapshot_id"] == snapshot_id and pm["equipment_version_id"] is None:
                group = self.resolve("equipment_group", pm["group_id"]) if source else pm["group_id"]
                result.append({"PM": _display(pm["pm_name"]), "모델 ID": group, "모델": pm["model_name_raw"],
                               "버전": None})
        result = sorted(result, key=_serialized)
        self.children_cache[cache_key] = result
        return result

    def _review_row(self, name, match, incoming, original):
        """사용자 선택을 값과 원본 위치에 함께 적용한다. 미선택은 임시로 기존값이다."""
        pk = self.primary[name]
        from setron.features.peer_sync import key_token
        self.review_originals[name, key_token({column: match[column] for column in pk})] = original
        if name in {"release_version_item", "risk_rule", "app_setting", "version_family"}:
            old = {key: value for key, value in match.items() if key not in pk}
            new = {key: value for key, value in incoming.items() if key not in pk}
            if _payload(name, match, pk) == _payload(name, incoming, pk):
                return dict(match)
            if self._choose(name, match, "연결·속성", _decoded_values(name, old), _decoded_values(name, new), grouped=True):
                return {**incoming, **{c: match[c] for c in pk}}
            self.retained.add(name)
            return dict(match)
        selected = dict(match)
        incoming_fields = set()
        excluded = {*pk, "batch_id", "source_reference", "edited_fields"}
        if name == "equipment_snapshot":
            old_children = self._children(match["snapshot_id"])
            new_children = self._children(original["snapshot_id"], source=True)
            changed = old_children != new_children or match["group_id"] != incoming["group_id"]
            from setron.features.peer_sync import key_token
            uid = self.target_identity.get((name, key_token({"snapshot_id": match["snapshot_id"]})))
            target = self.target_entities.get(self.canonical_identity.get(uid, uid)) if uid else None
            if changed and target is not None and self._has_group_receipt(target["head"], self._group_digest(original)):
                changed = False
            comparison = self.automation.snapshot(match, original)
            replace_children = changed and self._choose(name, match, "PM·SW 구성", old_children, new_children,
                                                       grouped=True, comparison=comparison,
                                                       requires_confirmation=self.automation.snapshot_removes_current(match, original))
            if changed and self.differences[-1].choice is not None:
                from setron.features.peer_sync import key_token
                self.receipts[name][key_token({"snapshot_id": match["snapshot_id"]})].update({
                    "source_group_digest": self._group_digest(original), "choice": self.differences[-1].choice})
            (self.replaced_snapshots if replace_children else self.closed_snapshots).add(match["snapshot_id"])
            if replace_children:
                selected["group_id"] = incoming["group_id"]
                incoming_fields.add("group_id")
            excluded.add("group_id")
        for column, value in incoming.items():
            if column in excluded or match.get(column) == value:
                continue
            if self._choose(name, match, column, match.get(column), value):
                selected[column] = value
                incoming_fields.add(column)
        if incoming_fields:
            if "batch_id" in selected:
                selected["batch_id"] = incoming.get("batch_id", match.get("batch_id"))
            if "source_reference" in incoming:
                selected["source_reference"] = _mixed_sources(match, incoming, incoming_fields)
            if "edited_fields" in incoming:
                old_edits = set(json.loads(match.get("edited_fields") or "[]"))
                new_edits = set(json.loads(incoming.get("edited_fields") or "[]"))
                selected["edited_fields"] = json.dumps(sorted((old_edits - incoming_fields) | (new_edits & incoming_fields)))
        if _payload(name, selected, pk) != _payload(name, incoming, pk):
            self.retained.add(name)
        return selected

    def _process_table(self, name):
        rows = _ordered_groups(self.source[name]) if name == "equipment_group" else self.source[name]
        pk = self.primary[name]
        business = BUSINESS_KEYS.get(name)
        existing = self._index(name, business)
        item_matches = self._item_matches() if name == "item" else {}
        used = set()
        seen = set()
        current = self.current[name]
        settings, stale_history = self._setting_changes(rows) if name == "app_setting" else ({}, ())
        auto = len(pk) == 1 and bool(rows) and isinstance(rows[0][pk[0]], int)
        next_id = max(self.next_ids.get(name, 1), max((key[0] for key in current), default=0) + 1) if auto else 0
        spare_id = max(next_id, max((row[pk[0]] for row in rows), default=0) + 1) if auto else 0
        for original in rows:
            original_key = _key(original, pk)
            restoring_child = name in SNAPSHOT_CHILDREN and original["snapshot_id"] in self.restoring_snapshots
            if self.mode == "review" and not restoring_child and self._known_source(name, original):
                from setron.features.peer_sync import key_token
                uid = self.source_identity.get((name, key_token({c: original[c] for c in pk})))
                archived = self.target_entities.get(self.canonical_identity.get(uid, uid)) if uid else None
                if archived is not None and (archived["deleted"] or
                        self.canonical_identity.get(uid, uid) not in self.uuid_targets[name]):
                    self._count("skip", name)
                    continue
            if name == "app_setting":
                row = settings.get(original_key[0])
                if row is None:
                    continue
            else:
                try:
                    row = self._remap_row(name, original)
                except (KeyError, ReferenceConflict):
                    if name in SNAPSHOT_CHILDREN and self.ids["equipment_snapshot"].get(original["snapshot_id"]) in self.replaced_snapshots:
                        raise SqlImportError("덮어쓸 설비 현황의 PM·적용 버전 참조를 확인할 수 없습니다.") from None
                    self._count("skip", name, note="references")
                    continue
            lookup = _business_key(row, business) if business else _payload(name, row, pk)
            matches = existing.get(lookup)
            if business and (lookup in seen or (matches and len(matches) > 1)):
                raise SqlImportError(f"{name}에 같은 항목이 여러 개 있어 병합할 대상을 식별할 수 없습니다.")
            seen.add(lookup)
            match = self._archived_batch(original) if name == "import_batch" else None
            if name == "item":
                match = item_matches.get(original["item_id"])
            from setron.features.peer_sync import key_token
            uid = self.source_identity.get((name, key_token({c: original[c] for c in pk})))
            if uid:
                identified = self.uuid_targets[name].get(self.canonical_identity.get(uid, uid))
                if identified is not None:
                    if len(pk) == 1 and isinstance(original[pk[0]], int) or _key(identified, pk) == _key(row, pk):
                        match = identified
                elif self.mode == "review" and self.canonical_identity.get(uid, uid) in self.target_entities and not restoring_child:
                    if not self._choose(name, row, "삭제된 항목 복원", "현재 PC에서 삭제됨",
                                        _decoded_values(name, row), grouped=True,
                                        comparison=self.automation.restoration(name, original)):
                        self._count("skip", name)
                        continue
                    if name == "equipment_snapshot":
                        self.restoring_snapshots.add(original["snapshot_id"])
            if match is not None and _key(match, pk) in used:
                match = None
            while matches and _key(matches[0], pk) in used:
                matches.popleft()
            if match is None and matches:
                match = matches.popleft()
            if name == "equipment_snapshot" and not self._snapshot_allowed(row):
                self._count("skip", name, note="sites")
                continue
            if name in SNAPSHOT_CHILDREN and row["snapshot_id"] in self.closed_snapshots:
                identical = match is not None and _payload(name, match, pk) == _payload(name, row, pk)
                self._count("same" if identical else "skip", name)
                if not identical:
                    self.skipped_snapshots.add(row["snapshot_id"])
                if match is not None:
                    self._link(name, original_key, _key(match, pk))
                continue
            if match is not None:
                mapped_key = _key(match, pk)
                used.add(mapped_key)
                self._link(name, original_key, mapped_key)
                if self.mode == "review" and self._known_source(name, original) and not (
                        name in SNAPSHOT_CHILDREN and row["snapshot_id"] in self.replaced_snapshots):
                    if name == "equipment_snapshot":
                        self.closed_snapshots.add(mapped_key[0])
                    self._count("same", name)
                    continue
                if name == "equipment_snapshot":
                    if self.mode != "review":
                        (self.closed_snapshots if self.mode == "add" else self.replaced_snapshots).add(mapped_key[0])
                if name in SNAPSHOT_CHILDREN:
                    self.incoming_children[name].add(mapped_key)
                if _payload(name, match, pk) == _payload(name, row, pk) or name == "import_batch":
                    if name == "equipment_snapshot" and self.mode == "review":
                        row = self._review_row(name, match, row, original)
                    self._count("same", name)
                elif self.mode == "review" and name in SNAPSHOT_CHILDREN and row["snapshot_id"] in self.replaced_snapshots:
                    row.update(zip(pk, mapped_key))
                    self._write("update", name, row)
                    current[mapped_key] = row
                elif self.mode == "review" and name == "app_setting" and row["setting_key"] in self.chosen_settings:
                    self._write("update", name, row)
                    current[mapped_key] = row
                elif self.mode == "review":
                    selected = self._review_row(name, match, row, original)
                    if _payload(name, match, pk) != _payload(name, selected, pk):
                        self._write("update", name, selected)
                        current[mapped_key] = selected
                    else:
                        self._count("skip", name)
                elif self.mode == "overwrite" or (name == "app_setting" and row["setting_key"] in HIERARCHY_KEYS):
                    row.update(zip(pk, mapped_key))
                    self._write("update", name, row)
                    current[mapped_key] = row
                else:
                    self.retained.add(name)
                    self._count("skip", name)
                    if name == "equipment_snapshot":
                        self.skipped_snapshots.add(mapped_key[0])
                continue
            mapped_key = _key(row, pk)
            if auto and (mapped_key in current or mapped_key[0] < next_id):
                mapped_key = (spare_id,)
                spare_id += 1
                if mapped_key[0] > 4294967295:
                    raise SqlImportError(f"{name}의 새 ID를 할당할 수 없습니다.")
                row[pk[0]] = mapped_key[0]
            self._link(name, original_key, mapped_key)
            self._write("insert", name, row)
            current[mapped_key] = row
            if name in SNAPSHOT_CHILDREN:
                self.incoming_children[name].add(mapped_key)
            if name == "import_batch" and original["source_type"] == "MANUAL":
                self.rewritten_batches.append((original, row))
        for key in sorted(stale_history):
            self._write("delete", name, current.pop((key,)))

    def _finalize_batches(self):
        for original, row in self.rewritten_batches:
            try:
                updated = rewrite_json("import_batch", row, self.resolve, ids_changed=self.remapped)
            except ReferenceConflict as error:
                raise SqlImportError(str(error)) from None
            if updated["raw_data"] != row["raw_data"]:
                row.update(updated)
                key = self._archive_marker(original)
                if (key,) not in self.current["app_setting"]:
                    value = {"setting_key": key, "setting_value": str(row["batch_id"])}
                    self.writes.append(Write("insert", "app_setting", {"setting_key": key}, value))
                    self.current["app_setting"][key,] = value

    def _prune_snapshot_children(self):
        # 기존 현황을 덮을 때는 파일의 PM·적용 버전 구성을 한 묶음으로 맞춘다.
        for name in reversed(SNAPSHOT_CHILDREN):
            for row in self.target[name]:
                if (row["snapshot_id"] in self.replaced_snapshots and
                        _key(row, self.primary[name]) not in self.incoming_children[name]):
                    self._write("delete", name, row)

    def _review_deletions(self):
        if self.mode != "review":
            return
        candidates = []
        # 이전 버전은 삭제 후 복원을 새 UUID로 기록했다. 같은 파일의 현재 살아 있는
        # 업무 행이 이미 이 로컬 행으로 매칭됐다면 과거 incarnation의 tombstone으로
        # 그 복원 결과를 다시 삭제하지 않는다.
        active_targets = set()
        active_business = defaultdict(set)
        for table, rows in self.source.items():
            for value in rows:
                if table in BUSINESS_KEYS:
                    try:
                        mapped = self._remap_row(table, value)
                        active_business[table].add(_business_key(mapped, BUSINESS_KEYS[table]))
                    except (KeyError, ReferenceConflict):
                        pass
                try:
                    key = []
                    for column in self.primary[table]:
                        remote = next((parent for field, parent, remote in self.foreign[table]
                                       if field == column and self.primary[parent] == (remote,)), table)
                        key.append(self.ids[remote][value[column]])
                    active_targets.add((table, tuple(key)))
                except KeyError:
                    continue
        for source in self.source_state.entities.values():
            if not source["deleted"]:
                continue
            identifier = self.canonical_identity.get(source["uuid"], source["uuid"])
            target = self.target_entities.get(identifier)
            row = self.uuid_targets[source["table"]].get(identifier)
            if target is None or row is None or self._known_head(source["head"], target["head"]):
                continue
            if source["table"] == "app_setting" and row["setting_key"] in self.reviewed_setting_keys:
                continue  # N 기준과 저장 이력의 삭제는 검토한 묶음 선택을 따른다.
            if (source["table"], _key(row, self.primary[source["table"]])) in active_targets:
                continue
            if source["table"] in BUSINESS_KEYS and _business_key(row, BUSINESS_KEYS[source["table"]]) in active_business[source["table"]]:
                continue
            candidates.append((source["table"], row))
        snapshots = {row["snapshot_id"] for name, row in candidates if name == "equipment_snapshot"}
        for name, row in candidates:
            if name in SNAPSHOT_CHILDREN and (row["snapshot_id"] in snapshots or
                    row["snapshot_id"] in self.closed_snapshots or row["snapshot_id"] in self.replaced_snapshots):
                continue
            source = next(entity for entity in self.source_state.entities.values()
                          if entity["deleted"] and entity["table"] == name and
                          self.uuid_targets[name].get(self.canonical_identity.get(entity["uuid"], entity["uuid"])) is row)
            if not self._choose(name, row, "삭제", _decoded_values(name, row), "삭제", grouped=True,
                                comparison=self.automation.deletion(name, row, source), allow_automatic=False,
                                requires_confirmation=True):
                continue
            if name == "equipment_snapshot":
                for child in reversed(SNAPSHOT_CHILDREN):
                    for value in self.target[child]:
                        if value["snapshot_id"] == row["snapshot_id"]:
                            self._delete_row(child, value)
            self._delete_row(name, row)

    def _delete_row(self, name, row):
        key = _key(row, self.primary[name])
        if key in self.current[name]:
            self._write("delete", name, row)
            self.current[name].pop(key)

    def _validate_final(self):
        final = {name: {_key(row, self.primary[name]): row for row in rows} for name, rows in self.target.items()}
        for write in self.writes:
            key = _key(write.key, self.primary[write.table])
            if write.action == "delete":
                final[write.table].pop(key, None)
            else:
                final[write.table][key] = write.row
        for name, values in final.items():
            for constraint in self.tables[name].foreign_key_constraints:
                elements = tuple(constraint.elements)
                parent = elements[0].column.table.name
                keys = {tuple(row[element.column.name] for element in elements) for row in final[parent].values()}
                for row in values.values():
                    key = tuple(row[element.parent.name] for element in elements)
                    if all(value is not None for value in key) and key not in keys:
                        raise SqlImportError("선택한 항목의 연결 자료가 남아 있지 않습니다. 관련 자료의 적용 선택을 함께 확인해 주세요.")
            business = BUSINESS_KEYS.get(name)
            if business:
                seen = set()
                for row in values.values():
                    key = _business_key(row, business)
                    if key in seen:
                        raise SqlImportError("선택 결과에 같은 항목이 중복됩니다. 이름과 연결을 다시 확인해 주세요.")
                    seen.add(key)

    def _snapshot_examples(self):
        affected = self.skipped_snapshots.copy()
        affected.update(write.row["snapshot_id"] for write in self.writes
                        if write.table in ("equipment_snapshot", *SNAPSHOT_CHILDREN))
        examples = []
        for identifier in sorted(affected)[:5]:
            row = self.current["equipment_snapshot"].get((identifier,)) or next(
                value for value in self.target["equipment_snapshot"] if value["snapshot_id"] == identifier)
            equipment = self.current["equipment"].get((row["equipment_id"],)) or next(
                value for value in self.target["equipment"] if value["equipment_id"] == row["equipment_id"])
            code = equipment["equipment_code"].decode("utf-8")
            examples.append(f"{code} ({row['base_date']})")
        if not examples:
            return None
        suffix = f" 외 {len(affected) - len(examples):,}건" if len(affected) > len(examples) else ""
        return "검토 대상 설비 현황 · " + " / ".join(examples) + suffix

    def plan(self, destination, name):
        for table_name in TABLE_NAMES:
            self._process_table(table_name)
        self._review_deletions()
        self._prune_snapshot_children()
        self._finalize_batches()
        if self.mode == "review":
            deletes = [write for write in self.writes if write.action == "delete"]
            group_order = {row["group_id"]: index for index, row in enumerate(_ordered_groups(self.target["equipment_group"]))}
            deletes.sort(key=lambda write: (TABLE_NAMES.index(write.table), group_order.get(write.key.get("group_id"), 0)), reverse=True)
            self.writes = [write for write in self.writes if write.action != "delete"] + deletes
        if not any(value.choice is None for value in self.differences):
            _ordered_groups(tuple(self.current["equipment_group"].values()))
            validate_merge(self.target, self.writes, self.repository)
            if self.mode == "review":
                self._validate_final()
                try:
                    group_history_prune(tuple(self.current["app_setting"].values()), (), "overwrite")
                except ValueError as error:
                    raise SqlImportError(str(error)) from None
        unknown = set(self.choices) - {value.id for value in self.differences}
        if unknown:
            raise SqlImportError("검토 목록에 없는 적용 선택입니다. 변경 사항을 다시 검토해 주세요.")
        target_rows = sum(len(rows) for rows in self.target.values())
        labels = (("추가", "insert"), ("갱신", "update"), ("연결 자료 제거", "delete"),
                  ("동일", "same"), ("건너뜀", "skip"))
        target = f"대상 DB {name}" if name else "대상 DB"
        summary = [f"{target} · 기존 {target_rows:,}행 · 파일 {sum(map(len, self.source.values())):,}행",
                   " · ".join(f"{label} {self.counts[key]:,}행" for label, key in labels)]
        summary.extend(f"{name}  " + " · ".join(f"{label} {counts[key]:,}" for label, key in labels if counts[key])
                       for name, counts in self.details.items() if any(counts[key] for key in ("insert", "update", "delete", "skip")))
        if self.counts["sites"]:
            summary.append(f"호기의 고정 사업장이 다른 현황 {self.counts['sites']:,}건은 연결 자료와 함께 건너뜁니다.")
        if examples := self._snapshot_examples():
            summary.append(examples)
        if self.counts["references"]:
            summary.append(f"연결 근거를 확인할 수 없는 자료 {self.counts['references']:,}행은 건너뜁니다.")
        if self.counts["group_history"]:
            summary.append(f"대상 DB의 N 그룹 저장 이력을 유지하기 위해 {self.counts['group_history']:,}행을 건너뜁니다.")
        if any(write.action in {"insert", "update"} and write.table == "app_setting" and
               write.row["setting_key"].startswith((b"target_applicability.v1:", b"shared_note_item_models.v1:"))
               for write in self.writes) and (self.remapped or self.retained):
            summary.append("가져온 목표 N·공유 Item 적용 근거는 다시 확인이 필요합니다.")
        if self.mode == "review":
            pending = sum(value.choice is None for value in self.differences)
            summary.append(f"값 비교 {len(self.differences):,}건 · 자동 판단 {sum(value.automatic for value in self.differences):,}건 · 선택 필요 {pending:,}건")
        sync_changes = int(self.mode == "review" and (any(value.choice is not None for value in self.differences) or
            bool(set(self.source_state.events) - set(self.target_state.events))))
        return MergePlan(self.mode, destination, fingerprint(self.target, self.next_ids), target_rows,
                         tuple(self.writes), "\n".join(summary), tuple(self.differences), dict(self.choices),
                         deepcopy(dict(self.ids)), deepcopy(self.raw_source), sync_changes, deepcopy(dict(self.receipts)))


def build_plan(source, target, next_ids, repository, mode, destination, name=None, *, choices=None):
    return _Planner(source, target, next_ids, repository, mode, choices).plan(destination, name)
