"""버전(N) 기준 입력 화면의 자료 연결. 판정은 ``version.groups``, 저장·이력은 ``targets.groups``를 그대로 쓴다."""

from datetime import datetime

from setron.features.targets.groups import (
    GroupHistoryEntry, GroupSaveBlocked, SavedGroups, SaveResult, with_saved_groups,
)
from setron.features.version.groups import (
    SLOT_LABELS, TIER_LABELS, Slot, Tier, VersionGroups, check_groups, compare_with_groups, suggest_ladder,
    target_label, tier_of,
)
from setron.features.version.service import compare_version

__all__ = ["GroupSaveBlocked", "MemoryGroupStore", "SLOT_LABELS", "Slot", "TIER_COLORS", "TIER_LABELS", "Tier",
           "VersionGroups", "check_groups", "group_store_for", "release_tier", "suggest_ladder", "target_label",
           "with_saved_groups"]

# 팀 결정 색 단계: N+1 이상 진초록 · N 연초록 · N-1 노랑 · N-2 이하 빨강 · 비교 불가 회색 (배경, 글자)
TIER_COLORS = {Tier.ABOVE: ("#1f8a52", "white"), Tier.AT: ("#ddf4e5", "#246b4a"), Tier.ONE_BELOW: ("#fff0b7", "#8a6400"),
               Tier.TWO_BELOW: ("#e5484d", "white"), Tier.UNKNOWN: ("#eceef1", "#5e6b7a")}


class MemoryGroupStore:
    """DB가 없는 예시 실행용. ``GroupStore``와 같은 검사·결과 형식으로 앱 실행 동안만 이력을 둔다."""

    def __init__(self, source, *, clock=datetime.now):
        self.source, self.clock = source, clock
        self._history: dict[tuple[int, int], list[GroupHistoryEntry]] = {}

    def save(self, pair, groups: VersionGroups, family, *, memo: str = "") -> SaveResult:
        issues = check_groups(family, groups)
        if any(issue.blocks for issue in issues):
            raise GroupSaveBlocked(issues)
        history = self._history.setdefault(pair, [])
        if history and history[-1].groups.to_dict() == groups.to_dict():
            return SaveResult(SavedGroups(pair[0], pair[1], tuple(history)),
                              tuple(issue for issue in issues if not issue.blocks), changed=False)
        entry = GroupHistoryEntry(self.clock().replace(microsecond=0), memo.strip(), groups)
        history.append(entry)
        saved = SavedGroups(pair[0], pair[1], tuple(self._history[pair]))
        self.source.saved_groups = {**getattr(self.source, "saved_groups", {}), pair: groups}
        return SaveResult(saved, tuple(issue for issue in issues if not issue.blocks))

    def load(self, pair) -> SavedGroups | None:
        history = self._history.get(pair)
        return SavedGroups(pair[0], pair[1], tuple(history)) if history else None


def group_store_for(source):
    """공급자의 저장소. DB 공급자는 불러올 때 붙인 ``GroupStore``, 예시 자료는 앱 실행 중 메모리."""
    store = getattr(source, "group_store", None)
    if store is None:
        store = MemoryGroupStore(source)
        source.group_store = store
    return store


def release_tier(family, release_id, groups: VersionGroups | None, single_target: int | None) -> Tier:
    """릴리즈 하나를 그 계열 설비에 적용했을 때의 색 단계.

    그룹을 아직 정하지 않았으면(목표 1개) 기존 비교로 단계를 정한다. 같은 ``tier_of``라 화면 색이 같다.
    """
    if family is None:
        return Tier.UNKNOWN
    if groups is None:
        return tier_of(compare_version(family, None, single_target, release_version_id=release_id))
    return tier_of(compare_with_groups(family, None, groups, release_version_id=release_id))
