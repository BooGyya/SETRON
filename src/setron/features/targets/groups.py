"""모델·계열별 N 그룹을 저장하고 저장 이력을 남긴다 (멘토 5차 요청 1번, 팀 결정 2026-11-05).

판정 규칙은 ``version.groups``에 있고, 여기서는 저장·읽기·이력만 맡는다.

저장 형식 (``app_setting``, 키 ``version_groups.v1:{모델 ID}:{계열 ID}``)::

    {"schema": 1, "model_id": 1, "family_id": 10,
     "history": [{"saved_at": "2026-11-06T15:30:00", "memo": "...", "groups": {"N-1": [102], "N": [103]}}, ...]}

정한 규칙
- 저장할 때마다 이력 끝에 한 건을 덧붙인다. 지금 쓰는 그룹은 이력의 마지막 건이다. 이력은 지우지 않는다.
- 저장 전에 ``check_groups``로 검사한다. 막는 문제(N 칸 비어 있음)가 있으면 저장하지 않고,
  나머지(없는 릴리즈·순서 모름·순서 겹침·거꾸로 묶음)는 경고로 돌려주고 저장한다.
- 기존 목표 N(릴리즈 하나)은 ``VersionGroups.single``로 N 칸 하나짜리 그룹으로 옮긴다.
  이미 저장된 그룹이 있으면 덮어쓰지 않는다.
- 앱 판정에는 ``with_saved_groups``로 저장된 그룹을 목표 N 대신 넣는다. 그룹이 없는 모델·계열은 기존 목표를 그대로 쓴다.

명세에 없어 다음과 같이 정했다 (팀 확인 필요)
- 저장된 그룹이 화면에서 고른 목표 N(릴리즈 하나)보다 우선한다.
- 이력 개수에는 상한을 두지 않는다 (``setting_value``는 LONGTEXT). 이력을 어디까지 보여 줄지는 미정이다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from setron.features.storage.inputs import identifier, require
from setron.features.storage.validation import StorageValidationError, decode_record
from setron.features.version.groups import GroupIssue, VersionGroups, check_groups

SCHEMA = 1
MEMO_LIMIT = 1000

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


@dataclass(frozen=True)
class SavedGroups:
    """모델·계열 하나의 저장된 N 그룹. ``groups``는 지금 쓰는 그룹(이력의 마지막 건)이다."""

    model_id: int
    family_id: int
    history: tuple[GroupHistoryEntry, ...]

    @property
    def groups(self) -> VersionGroups:
        return self.history[-1].groups

    @property
    def saved_at(self) -> datetime:
        return self.history[-1].saved_at


@dataclass(frozen=True)
class SaveResult:
    """저장 결과와 저장은 했지만 확인이 필요한 경고."""

    saved: SavedGroups
    warnings: tuple[GroupIssue, ...]


def setting_key(pair: Pair) -> str:
    return f"version_groups.v1:{pair[0]}:{pair[1]}"


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
        require(family is not None and family.family_id == pair[1], "같은 계열의 릴리즈 목록으로 검사해야 합니다.")
        require(isinstance(memo, str) and len(memo) <= MEMO_LIMIT, f"메모는 {MEMO_LIMIT}자 이하의 문자열이어야 합니다.")
        issues = check_groups(family, groups)
        if any(issue.blocks for issue in issues):
            raise GroupSaveBlocked(issues)

        key = {"setting_key": setting_key(pair)}
        current = _locked(tx, key)
        history = _payload_history(current["setting_value"], pair) if current is not None else []
        history.append({"saved_at": self.clock().replace(microsecond=0).isoformat(),
                        "memo": memo.strip(), "groups": groups.to_dict()})
        payload = {"schema": SCHEMA, "model_id": pair[0], "family_id": pair[1], "history": history}
        if current is None:
            tx.insert("app_setting", {**key, "setting_value": payload})
        else:
            tx.update("app_setting", key, {"setting_value": payload})
        return SaveResult(_saved(pair, payload), tuple(issue for issue in issues if not issue.blocks))

    def migrate_single(self, pair: Pair, release_id: int, family, *,
                       memo: str = "기존 목표 N(릴리즈 하나)에서 옮김") -> SaveResult | None:
        """기존 목표 N 하나를 N 칸 하나짜리 그룹으로 저장한다. 이미 그룹이 있으면 그대로 두고 None."""
        identifier(release_id, "release_id")
        with self.storage.transaction() as tx:
            if self.load_in(tx, pair) is not None:
                return None
            return self.save_in(tx, pair, VersionGroups.single(release_id), family, memo=memo)

    def load(self, pair: Pair) -> SavedGroups | None:
        """저장된 그룹과 이력. 없으면 None."""
        with self.storage.transaction() as tx:
            return self.load_in(tx, pair)

    def load_in(self, tx, pair: Pair) -> SavedGroups | None:
        pair = _pair(pair)
        try:
            payload = tx.get("app_setting", {"setting_key": setting_key(pair)})["setting_value"]
        except LookupError:
            return None
        return _saved(pair, {"history": _payload_history(payload, pair)})

    def load_many(self, pairs: Iterable[Pair]) -> dict[Pair, VersionGroups]:
        """여러 모델·계열의 지금 그룹을 한 트랜잭션에서 읽는다. 그룹이 없는 쌍은 넣지 않는다.

        raises
        - StorageValidationError: 저장된 형식이 깨진 쌍이 있을 때. 잘못된 그룹으로 판정하지 않도록 멈춘다.
        """
        with self.storage.transaction() as tx:
            found = {pair: self.load_in(tx, pair) for pair in dict.fromkeys(pairs)}
        return {pair: saved.groups for pair, saved in found.items() if saved is not None}


def _pair(value) -> Pair:
    require(isinstance(value, tuple) and len(value) == 2, "모델·계열은 (model_id, family_id) 쌍이어야 합니다.")
    identifier(value[0], "model_id")
    identifier(value[1], "family_id")
    return value


def _locked(tx, key):
    """저장 행을 잠그고 읽는다. 없으면 None. 이력을 덧붙이는 사이 다른 저장이 끼어들지 않게 한다."""
    try:
        row = tx.repository.get(tx.session, "app_setting", key, for_update=True)
    except LookupError:
        return None
    return decode_record("app_setting", row)


def _payload_history(payload, pair: Pair) -> list[dict]:
    """저장 값에서 이력 목록을 꺼낸다. 형식이 깨졌으면 덮어쓰지 않도록 오류로 알린다."""
    broken = f"모델 {pair[0]}·계열 {pair[1]}의 저장된 N 그룹 형식이 잘못됐습니다. 저장값을 확인해 주세요."
    if not (isinstance(payload, dict) and payload.get("schema") == SCHEMA
            and payload.get("model_id") == pair[0] and payload.get("family_id") == pair[1]
            and isinstance(payload.get("history"), list) and payload["history"]):
        raise StorageValidationError(broken)
    for entry in payload["history"]:
        try:
            _entry(entry)
        except (TypeError, ValueError, KeyError) as exc:
            raise StorageValidationError(broken) from exc
    return [dict(entry) for entry in payload["history"]]


def _entry(value) -> GroupHistoryEntry:
    if not isinstance(value, dict) or not isinstance(value.get("memo"), str) or not isinstance(value.get("groups"), dict):
        raise ValueError("이력 항목 형식")
    if any(not isinstance(ids, list) or any(type(member) is not int for member in ids)
           for ids in value["groups"].values()):
        raise ValueError("칸의 릴리즈 ID 형식")
    return GroupHistoryEntry(datetime.fromisoformat(value["saved_at"]), value["memo"],
                             VersionGroups.from_dict(value["groups"]))


def _saved(pair: Pair, payload) -> SavedGroups:
    return SavedGroups(pair[0], pair[1], tuple(_entry(entry) for entry in payload["history"]))
