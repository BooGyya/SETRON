"""원본 DB의 내부 ID를 대상 DB의 업무 키와 참조 관계에 맞춰 변환한다."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import Date, DateTime

from setron.features.storage.validation import JSON_COLUMNS, TABLE_NAMES, decode_record, encode_record

from .dump import DUMP_COLUMNS, DumpFormatError, allowed_columns, project_row, with_legacy_jira
from .group_settings import group_history_prune
from .references import ReferenceConflict, rewrite_json


class SqlImportError(ValueError):
    """SQL 가져오기 화면에 표시할 오류."""


@dataclass(frozen=True)
class Write:
    action: str
    table: str
    key: dict
    row: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MergePlan:
    mode: str
    destination: str
    revision: str
    target_rows: int
    writes: tuple[Write, ...]
    summary: str

    @property
    def changes(self):
        return len(self.writes)


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
    def __init__(self, source, target, next_ids, repository, mode):
        self.target, self.next_ids, self.mode = target, next_ids, mode
        self.tables = {name: repository.table(name) for name in TABLE_NAMES}
        if any(tuple(table.columns.keys()) not in allowed_columns(name)
               for name, table in self.tables.items()):
            raise SqlImportError("대상 DB의 테이블 구조와 지원하는 SETRON 백업 형식이 다릅니다.")
        try:
            source = with_legacy_jira(source, tuple(self.tables["item"].columns.keys()))
        except DumpFormatError as error:
            raise SqlImportError(str(error)) from None
        self.primary = {name: tuple(table.primary_key.columns.keys()) for name, table in self.tables.items()}
        self.foreign = {name: tuple(sorted((fk.parent.name, fk.column.table.name, fk.column.name)
                                          for fk in table.foreign_keys)) for name, table in self.tables.items()}
        self.source = {name: tuple(_normalized(self.tables[name], row, repository) for row in source[name])
                       for name in TABLE_NAMES}
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
        self._validate_source()
        for row in target["equipment_snapshot"]:
            self.sites.setdefault(row["equipment_id"], set()).add(row["site"].strip().casefold())

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
        sites = {}
        for row in self.source["equipment_snapshot"]:
            site = row["site"].strip().casefold()
            if sites.setdefault(row["equipment_id"], site) != site:
                raise SqlImportError("SQL 파일에 같은 호기의 서로 다른 사업장이 있습니다.")

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
        if len(self.primary[name]) == 1 and isinstance(original[0], int):
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
            if key.startswith(ARCHIVE_KEY):
                continue
            try:
                incoming[key] = self._remap_row("app_setting", row)
            except ReferenceConflict:
                self._count("skip", "app_setting", note="references")
        try:
            suppressed, deleted = group_history_prune(incoming.values(), self.target["app_setting"], self.mode)
        except ValueError as error:
            raise SqlImportError(str(error)) from None
        for original_key, row in tuple(incoming.items()):
            if row["setting_key"] in suppressed:
                del incoming[original_key]
                self._count("skip", "app_setting", note="group_history")
        return incoming, deleted

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
                if name == "equipment_snapshot":
                    (self.closed_snapshots if self.mode == "add" else self.replaced_snapshots).add(mapped_key[0])
                if name in SNAPSHOT_CHILDREN:
                    self.incoming_children[name].add(mapped_key)
                if _payload(name, match, pk) == _payload(name, row, pk) or name == "import_batch":
                    self._count("same", name)
                elif self.mode == "overwrite":
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

    def _snapshot_examples(self):
        affected = self.skipped_snapshots.copy()
        affected.update(write.row["snapshot_id"] for write in self.writes
                        if write.table in ("equipment_snapshot", *SNAPSHOT_CHILDREN))
        examples = []
        for identifier in sorted(affected)[:5]:
            row = self.current["equipment_snapshot"][identifier,]
            code = self.current["equipment"][row["equipment_id"],]["equipment_code"].decode("utf-8")
            examples.append(f"{code} ({row['base_date']})")
        if not examples:
            return None
        suffix = f" 외 {len(affected) - len(examples):,}건" if len(affected) > len(examples) else ""
        return "검토 대상 설비 현황 · " + " / ".join(examples) + suffix

    def plan(self, destination, name):
        for name in TABLE_NAMES:
            self._process_table(name)
        _ordered_groups(tuple(self.current["equipment_group"].values()))
        self._prune_snapshot_children()
        self._finalize_batches()
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
        return MergePlan(self.mode, destination, fingerprint(self.target, self.next_ids), target_rows,
                         tuple(self.writes), "\n".join(summary))


def build_plan(source, target, next_ids, repository, mode, destination, name=None):
    return _Planner(source, target, next_ids, repository, mode).plan(destination, name)
