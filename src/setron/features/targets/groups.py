"""모델·계열별 N 그룹을 저장하고 저장 이력을 남긴다 (멘토 5차 요청 1번, 팀 결정 2026-11-05).

판정 규칙은 ``version.groups``에 있고, 여기서는 저장·읽기·이력만 맡는다.

기존 ``app_setting`` 키에는 마지막 저장 순번과 활성 기준을 둔다. 모든 저장은 순번이 붙은 별도 행으로
남겨 기본키 범위에서 필요한 이력만 읽는다. 이전 형식은 이력 조회나 다음 변경 저장 때 옮긴다.

정한 규칙
- 새 기준을 저장할 때만 이력 끝에 한 건을 덧붙인다. 과거 기준으로 되돌릴 때는 활성 기준만 바꾼다.
- 저장 전에 ``check_groups``로 검사한다. 막는 문제(N 칸 비어 있음)가 있으면 저장하지 않고,
  나머지(없는 릴리즈·순서 모름·순서 겹침·거꾸로 묶음)는 경고로 돌려주고 저장한다.
- 기존 목표 N(릴리즈 하나)은 ``VersionGroups.single``로 N 칸 하나짜리 그룹으로 옮긴다.
  이미 저장된 그룹이 있으면 덮어쓰지 않는다.
- 앱 판정에는 ``with_saved_groups``로 저장된 그룹을 목표 N 대신 넣는다. 그룹이 없는 모델·계열은 기존 목표를 그대로 쓴다.

명세에 없어 다음과 같이 정했다 (팀 확인 필요)
- 저장된 그룹이 화면에서 고른 목표 N(릴리즈 하나)보다 우선한다.
- 과거 이력은 최신순으로 요청한 범위만 읽는다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from setron.features.storage.inputs import identifier, require
from setron.features.storage.validation import StorageValidationError, decode_record, encode_record
from setron.features.version.groups import GroupIssue, VersionGroups, check_groups

LEGACY_SCHEMA = 1
PREVIOUS_SCHEMA = 2
SCHEMA = 3
MEMO_LIMIT = 1000
HISTORY_LIMIT = 100
SEQUENCE_WIDTH = 20
HISTORY_KEY_PREFIX = "version_groups.history.v2:"

Pair = tuple[int, int]  # (모델 ID, 계열 ID)


class GroupSaveBlocked(ValueError):
    """막는 문제가 있어 N 그룹을 저장하지 않았다. ``issues``에 전체 확인 결과가 있다."""

    def __init__(self, issues: tuple[GroupIssue, ...]):
        self.issues = issues
        super().__init__(" ".join(issue.message for issue in issues if issue.blocks))


@dataclass(frozen=True)
class GroupHistoryEntry:
    """저장 한 번의 기록."""

    saved_at: datetime
    memo: str
    groups: VersionGroups
    sequence: int


@dataclass(frozen=True)
class SavedGroups:
    """모델·계열 하나의 저장 이력과 지금 쓰는 기준."""

    model_id: int
    family_id: int
    history: tuple[GroupHistoryEntry, ...]
    active: GroupHistoryEntry | None = None

    @property
    def current(self) -> GroupHistoryEntry:
        return self.active if self.active is not None else self.history[-1]

    @property
    def groups(self) -> VersionGroups:
        return self.current.groups

    @property
    def saved_at(self) -> datetime:
        return self.current.saved_at


@dataclass(frozen=True)
class SaveResult:
    """저장 또는 되돌리기 결과와 확인이 필요한 경고."""

    saved: GroupHistoryEntry
    warnings: tuple[GroupIssue, ...]
    changed: bool = True


@dataclass(frozen=True)
class GroupHistoryPage:
    """현재 기준과 최신순 저장 이력. 활성 기준도 저장 순서대로 포함한다."""

    current: GroupHistoryEntry | None
    entries: tuple[GroupHistoryEntry, ...]
    next_cursor: int | None


@dataclass(frozen=True)
class _Current:
    sequence: int
    entry: GroupHistoryEntry
    schema: int = SCHEMA
    legacy_history: list[dict] | None = None


def setting_key(pair: Pair) -> str:
    return f"version_groups.v1:{pair[0]}:{pair[1]}"


def history_key_bounds() -> tuple[bytes, bytes]:
    """전체 과거 이력 기본키의 시작(포함)과 끝(제외)."""
    return HISTORY_KEY_PREFIX.encode("utf-8"), (HISTORY_KEY_PREFIX[:-1] + ";").encode("utf-8")


def with_saved_groups(targets: Mapping[Pair, int | VersionGroups | None],
                      saved: Mapping[Pair, VersionGroups]) -> dict[Pair, int | VersionGroups]:
    """집계(``summarize``)에 넘길 목표. 저장된 그룹이 있는 모델·계열은 그룹으로 바꾼다.

    param
    - targets: 지금까지 쓰던 목표 N (모델·계열 → 릴리즈 ID). None(목표 미지정)은 뺀다.
    - saved: ``GroupStore.load_many``로 읽은 저장 그룹.

    return
    - 새 목표 mapping. 입력은 바꾸지 않는다.
    """
    result: dict[Pair, int | VersionGroups] = {pair: target for pair, target in targets.items() if target is not None}
    result.update(saved)
    return result


class GroupStore:
    """N 그룹 저장 서비스. ``storage``는 ``transaction()``을 주는 공통 저장 서비스(``features/storage``)다."""

    def __init__(self, storage, *, clock: Callable[[], datetime] = datetime.now) -> None:
        self.storage = storage
        self.clock = clock

    def save(self, pair: Pair, groups: VersionGroups, family, *, memo: str = "") -> SaveResult:
        """검사 후 저장하고 이력에 한 건을 덧붙인다.

        param
        - pair: (모델 ID, 계열 ID).
        - groups: 저장할 N 그룹.
        - family: 같은 계열의 릴리즈 목록 (``version.service.FamilyReleases``). 검사에 쓴다.
        - memo: 저장 사유 등 사용자가 남기는 메모. 비워도 된다.

        raises
        - GroupSaveBlocked: N 칸이 비어 있는 등 막는 문제가 있을 때. 저장하지 않는다.
        - StorageValidationError: 모델·계열 ID나 메모가 잘못됐거나, 저장된 이력 형식이 깨졌을 때.
        """
        with self.storage.transaction() as tx:
            return self.save_in(tx, pair, groups, family, memo=memo)

    def save_in(self, tx, pair: Pair, groups: VersionGroups, family, *, memo: str = "") -> SaveResult:
        """열린 트랜잭션에서 저장한다. 같은 모델·계열의 저장 행을 잠그고 이력을 덧붙인다."""
        pair = _pair(pair)
        require(isinstance(groups, VersionGroups), "N 그룹(VersionGroups)이 필요합니다.")
        require(isinstance(memo, str) and len(memo) <= MEMO_LIMIT, f"메모는 {MEMO_LIMIT}자 이하의 문자열이어야 합니다.")
        warnings = _checked_groups(pair, groups, family)
        current = _lock_current(tx, pair)
        if current is not None and current.entry.groups == groups:
            return SaveResult(current.entry, warnings, changed=False)
        sequence = current.sequence + 1 if current is not None else 1
        entry = GroupHistoryEntry(self.clock().replace(microsecond=0), memo.strip(), groups, sequence)
        _append(tx, pair, current, entry)
        return SaveResult(entry, warnings)

    def restore(self, pair: Pair, sequence: int, family) -> SaveResult:
        """기존 저장 건을 활성 기준으로 적용한다. 저장 이력과 저장 시각은 그대로 둔다."""
        pair = _pair(pair)
        require(type(sequence) is int and sequence > 0, "되돌릴 이력 순번이 올바르지 않습니다.")
        with self.storage.transaction() as tx:
            current = _lock_current(tx, pair)
            require(current is not None and sequence <= current.sequence, "되돌릴 저장 이력이 없습니다.")
            if sequence == current.entry.sequence:
                return SaveResult(current.entry, _checked_groups(pair, current.entry.groups, family), changed=False)
            _materialize_history(tx, pair, current)
            entry = _read_history(tx, pair, sequence + 1, 1)[0]
            warnings = _checked_groups(pair, entry.groups, family)
            _write_current(tx, pair, current.sequence, entry, inserting=False)
            return SaveResult(entry, warnings)

    def migrate_single(self, pair: Pair, release_id: int, family, *,
                       memo: str = "기존 목표 N(릴리즈 하나)에서 옮김") -> SaveResult | None:
        """기존 목표 N 하나를 N 칸 하나짜리 그룹으로 저장한다. 이미 그룹이 있으면 그대로 두고 None."""
        pair = _pair(pair)
        identifier(release_id, "release_id")
        with self.storage.transaction() as tx:
            if _lock_current(tx, pair) is not None:
                return None
            return self.save_in(tx, pair, VersionGroups.single(release_id), family, memo=memo)

    def load(self, pair: Pair) -> SavedGroups | None:
        """전체 이력이 필요한 호출에서만 사용한다. 화면은 현재 기준과 이력 페이지를 따로 읽는다."""
        with self.storage.transaction() as tx:
            return self.load_in(tx, pair)

    def load_in(self, tx, pair: Pair) -> SavedGroups | None:
        pair = _pair(pair)
        current = _read_current(tx, pair)
        if current is None:
            return None
        if current.legacy_history is not None:
            history = tuple(_checked_entry(entry, pair, sequence)
                            for sequence, entry in enumerate(current.legacy_history, 1))
        elif current.schema == PREVIOUS_SCHEMA:
            older = _read_history(tx, pair, current.sequence, current.sequence - 1)
            history = (*reversed(older), current.entry)
        else:
            history = tuple(reversed(_read_history(tx, pair, current.sequence + 1, current.sequence)))
            if history[current.entry.sequence - 1] != current.entry:
                raise _broken(pair)
        return SavedGroups(*pair, history, current.entry)

    def load_current(self, pair: Pair) -> GroupHistoryEntry | None:
        """현재 기준만 읽는다. 과거 이력 행을 조회하지 않는다."""
        with self.storage.transaction() as tx:
            return self.load_current_in(tx, pair)

    def load_current_in(self, tx, pair: Pair) -> GroupHistoryEntry | None:
        current = _read_current(tx, _pair(pair))
        return current.entry if current is not None else None

    def history_page(self, pair: Pair, *, cursor: int | None = None, limit: int = 10) -> GroupHistoryPage:
        """현재 기준과 지정한 순번 직전부터 과거 이력을 최대 ``limit``건 읽는다."""
        pair = _pair(pair)
        require(type(limit) is int and 1 <= limit <= HISTORY_LIMIT, "이력 조회 건수가 올바르지 않습니다.")
        require(cursor is None or (type(cursor) is int and cursor > 0), "이력 조회 위치가 올바르지 않습니다.")
        with self.storage.transaction() as tx:
            current = _read_current(tx, pair)
            if current is None or current.schema == SCHEMA:
                return _page(tx, pair, current, cursor, limit)

        # 다시 잠근 뒤 현재값을 검사한다. 앞서 읽은 구 형식과 사이에 일어난 저장을 덮어쓰지 않는다.
        with self.storage.transaction() as tx:
            current = _lock_current(tx, pair)
            if current is not None and current.schema != SCHEMA:
                _materialize_history(tx, pair, current)
                _write_current(tx, pair, current.sequence, current.entry, inserting=False)
            return _page(tx, pair, current, cursor, limit)

    def load_many(self, pairs: Iterable[Pair]) -> dict[Pair, VersionGroups]:
        """여러 모델·계열의 지금 그룹을 한 트랜잭션에서 읽는다. 그룹이 없는 쌍은 넣지 않는다.

        raises
        - StorageValidationError: 저장된 형식이 깨진 쌍이 있을 때. 잘못된 그룹으로 판정하지 않도록 멈춘다.
        """
        keys = {setting_key(pair): pair for pair in map(_pair, pairs)}
        if not keys:
            return {}
        with self.storage.transaction() as tx:
            table = tx.repository.table("app_setting")
            query = select(table).where(table.c.setting_key.in_([key.encode("utf-8") for key in keys]))
            found = {}
            for values in tx.session.execute(query).mappings():
                row = decode_record("app_setting", dict(values))
                pair = keys[row["setting_key"]]
                found[pair] = _current(row, pair).entry.groups
        return found


def _pair(value) -> Pair:
    require(isinstance(value, tuple) and len(value) == 2, "모델·계열은 (model_id, family_id) 쌍이어야 합니다.")
    identifier(value[0], "model_id")
    identifier(value[1], "family_id")
    return value


def _checked_groups(pair, groups, family):
    require(family is not None and family.family_id == pair[1], "같은 계열의 릴리즈 목록으로 검사해야 합니다.")
    issues = check_groups(family, groups)
    if any(issue.blocks for issue in issues):
        raise GroupSaveBlocked(issues)
    return tuple(issue for issue in issues if not issue.blocks)


def _read_current(tx, pair):
    try:
        row = tx.get("app_setting", {"setting_key": setting_key(pair)})
    except LookupError:
        return None
    return _current(row, pair)


def _lock_current(tx, pair):
    """현재 설정 행이 없어도 같은 계열의 첫 저장·이관이 동시에 진행되지 않게 한다."""
    try:
        tx.repository.get(tx.session, "version_family", {"family_id": pair[1]}, for_update=True)
    except LookupError as exc:
        raise StorageValidationError("N 그룹의 계열이 등록되어 있지 않습니다.") from exc
    try:
        row = tx.repository.get(tx.session, "app_setting", {"setting_key": setting_key(pair)}, for_update=True)
    except LookupError:
        return None
    return _current(decode_record("app_setting", row), pair)


def _current(row, pair):
    payload = row["setting_value"]
    if isinstance(payload, dict) and (payload.get("model_id"), payload.get("family_id")) == pair:
        schema = payload.get("schema")
        if schema == LEGACY_SCHEMA:
            history = payload.get("history")
            if isinstance(history, list) and history:
                return _Current(len(history), _checked_entry(history[-1], pair, len(history)), schema, history)
        sequence = payload.get("sequence")
        if type(sequence) is int and 1 <= sequence < 10 ** SEQUENCE_WIDTH:
            active = sequence if schema == PREVIOUS_SCHEMA else payload.get("active_sequence")
            if schema in (PREVIOUS_SCHEMA, SCHEMA) and type(active) is int and 1 <= active <= sequence:
                return _Current(sequence, _checked_entry(payload.get("current"), pair, active), schema)
    raise _broken(pair)


def _history_key(pair, sequence):
    return f"{HISTORY_KEY_PREFIX}{pair[0]}:{pair[1]}:{sequence:0{SEQUENCE_WIDTH}d}"


def _entry_payload(entry):
    return {"saved_at": entry.saved_at.isoformat(), "memo": entry.memo, "groups": entry.groups.to_dict()}


def _write_current(tx, pair, sequence, active, *, inserting):
    require(sequence < 10 ** SEQUENCE_WIDTH, "N 그룹 저장 순번의 범위를 벗어났습니다.")
    key = {"setting_key": setting_key(pair)}
    payload = {"schema": SCHEMA, "model_id": pair[0], "family_id": pair[1],
               "sequence": sequence, "active_sequence": active.sequence, "current": _entry_payload(active)}
    changes = {"setting_value": payload}
    if inserting:
        tx.insert("app_setting", {**key, **changes})
    else:
        tx.update("app_setting", key, changes)


def _insert_legacy(tx, pair, history):
    if not history:
        return
    rows = []
    for sequence, raw in enumerate(history, 1):
        _checked_entry(raw, pair, sequence)
        rows.append(encode_record("app_setting", {"setting_key": _history_key(pair, sequence),
                                                  "setting_value": raw}, inserting=True))
    tx.session.execute(tx.repository.table("app_setting").insert(), rows)


def _insert_entry(tx, pair, entry):
    tx.insert("app_setting", {"setting_key": _history_key(pair, entry.sequence),
                              "setting_value": _entry_payload(entry)})


def _materialize_history(tx, pair, current):
    if current.legacy_history is not None:
        _insert_legacy(tx, pair, current.legacy_history)
    elif current.schema == PREVIOUS_SCHEMA:
        _insert_entry(tx, pair, current.entry)


def _append(tx, pair, current, entry):
    if current is not None:
        _materialize_history(tx, pair, current)
    _insert_entry(tx, pair, entry)
    _write_current(tx, pair, entry.sequence, entry, inserting=current is None)


def _page(tx, pair, current, cursor, limit):
    if current is None:
        return GroupHistoryPage(None, (), None)
    before = min(cursor, current.sequence + 1) if cursor is not None else current.sequence + 1
    entries = _read_history(tx, pair, before, min(limit, before - 1))
    remaining = before - len(entries)
    return GroupHistoryPage(current.entry, entries, remaining if remaining > 1 else None)


def _read_history(tx, pair, before, count):
    if not count:
        return ()
    table = tx.repository.table("app_setting")
    lower = _history_key(pair, before - count).encode("utf-8")
    upper = _history_key(pair, before - 1).encode("utf-8")
    query = (select(table).where(table.c.setting_key.between(lower, upper))
             .order_by(table.c.setting_key.desc()).limit(count))
    rows = tx.session.execute(query).mappings().all()
    if len(rows) != count:
        raise _broken(pair)
    entries = []
    for sequence, values in zip(range(before - 1, before - count - 1, -1), rows):
        row = decode_record("app_setting", dict(values))
        if row["setting_key"] != _history_key(pair, sequence):
            raise _broken(pair)
        entries.append(_checked_entry(row["setting_value"], pair, sequence))
    return tuple(entries)


def _broken(pair):
    return StorageValidationError(f"모델 {pair[0]}·계열 {pair[1]}의 저장된 N 그룹 형식이 잘못됐습니다. 저장값을 확인해 주세요.")


def _checked_entry(value, pair, sequence):
    try:
        return _entry(value, sequence)
    except (TypeError, ValueError, KeyError) as exc:
        raise _broken(pair) from exc


def _entry(value, sequence) -> GroupHistoryEntry:
    if not isinstance(value, dict) or not isinstance(value.get("memo"), str) or not isinstance(value.get("groups"), dict):
        raise ValueError("이력 항목 형식")
    if any(not isinstance(ids, list) or any(type(member) is not int for member in ids)
           for ids in value["groups"].values()):
        raise ValueError("칸의 릴리즈 ID 형식")
    return GroupHistoryEntry(datetime.fromisoformat(value["saved_at"]), value["memo"],
                             VersionGroups.from_dict(value["groups"]), sequence)
