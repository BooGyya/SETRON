"""N 버전 그룹 (멘토 5차 요청 1번, 팀 결정 2026-11-05).

목표 N을 버전 하나가 아니라 N-2·N-1·N·N+1·N+2 다섯 칸으로 정하고, 각 칸에 사용자가 버전을 자유롭게
여러 개 넣는다. 핫픽스·버전 파편화로 색이 지나치게 갈라지는 것을 막기 위한 기능이다.

판정 규칙 (모두 이 파일에만 둔다. 달성률·포도송이 색·우선순위가 같은 결과를 쓴다)
1. 칸에 넣은 버전은 그 칸이다. 한 버전은 한 칸에만 들어간다 (두 칸이면 VersionGroups 생성 오류).
2. 넣지 않은 버전은 버전 순서상 바로 아래(더 오래된) 쪽에서 가장 가까운 '넣은 버전'의 칸을 따른다.
   예: N={v9}, N-2={v7} 이면 v8 은 N-2. 달성으로 잘못 세지 않도록 보수적으로 아래 칸을 따른다.
3. 넣은 버전보다 모두 최신이면 N+1 이상, 모두 오래됐으면 N-2 이하로 본다.
4. 순서가 거꾸로 묶여도 막지 않고 경고만 한다 (ORDER_CROSSED). 판정은 1~3을 그대로 따른다.
5. N 칸이 비면 목표 미지정(판단 불가), 순서를 모르는 버전은 칸에 넣지 않은 한 판단 불가다.
6. 달성 = N 칸 이상 (N·N+1·N+2). 색 단계(Tier): 진초록 N+1 이상 · 연초록 N · 노랑 N-1 · 빨강 N-2 이하 · 회색 판단 불가.

기존의 목표 1개(릴리즈 ID) 판정(service.compare_version)은 바꾸지 않는다. 그룹을 저장한 계열만 이 판정을 쓴다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum, IntEnum

from .schemas import CompareReason, CompareStatus, ReleaseVersion, VersionComparison


class Slot(IntEnum):
    """N 기준 칸. 값이 클수록 최신 쪽이다."""

    N_MINUS_2 = -2
    N_MINUS_1 = -1
    N = 0
    N_PLUS_1 = 1
    N_PLUS_2 = 2


SLOT_LABELS: dict[Slot, str] = {Slot.N_MINUS_2: "N-2", Slot.N_MINUS_1: "N-1", Slot.N: "N",
                                Slot.N_PLUS_1: "N+1", Slot.N_PLUS_2: "N+2"}
_SLOT_BY_LABEL = {label: slot for slot, label in SLOT_LABELS.items()}


class Tier(str, Enum):
    """포도송이 히트맵 색 단계 (팀 결정 2026-11-05)."""

    ABOVE = "ABOVE"            # N+1 이상 · 진초록
    AT = "AT"                  # N · 연초록
    ONE_BELOW = "ONE_BELOW"    # N-1 · 노랑
    TWO_BELOW = "TWO_BELOW"    # N-2 이하 · 빨강
    UNKNOWN = "UNKNOWN"        # 비교 불가 · 회색


TIER_LABELS: dict[Tier, str] = {Tier.ABOVE: "N+1 이상", Tier.AT: "N", Tier.ONE_BELOW: "N-1",
                                Tier.TWO_BELOW: "N-2 이하", Tier.UNKNOWN: "비교 불가"}


@dataclass(frozen=True)
class VersionGroups:
    """모델·계열 하나의 N 그룹. ``slots``는 칸 → 릴리즈 ID 묶음이다. 빈 칸은 생략할 수 있다.

    raises
    - ValueError: 한 릴리즈가 두 칸 이상에 들어 있을 때.
    """

    slots: Mapping[Slot, frozenset[int]] = field(default_factory=dict)

    def __post_init__(self):
        cleaned: dict[Slot, frozenset[int]] = {}
        owner: dict[int, Slot] = {}
        for slot, members in self.slots.items():
            slot = Slot(slot)
            members = frozenset(int(member) for member in members)
            for member in members:
                if member in owner:
                    raise ValueError(f"릴리즈 {member}가 {SLOT_LABELS[owner[member]]}와 {SLOT_LABELS[slot]}에 함께 있습니다. "
                                     "한 버전은 한 칸에만 넣을 수 있습니다.")
                owner[member] = slot
            if members:
                cleaned[slot] = members
        object.__setattr__(self, "slots", cleaned)

    @classmethod
    def single(cls, target_id: int) -> "VersionGroups":
        """기존 목표 N(릴리즈 하나)을 N 칸 하나짜리 그룹으로 옮긴다."""
        return cls({Slot.N: frozenset({target_id})})

    @classmethod
    def from_dict(cls, value: Mapping[str, Iterable[int]]) -> "VersionGroups":
        """저장 형식 ``{"N-2": [..], "N": [..], ...}``에서 만든다. 모르는 칸 이름은 ValueError."""
        unknown = set(value) - set(_SLOT_BY_LABEL)
        if unknown:
            raise ValueError(f"알 수 없는 N 칸: {sorted(unknown)}")
        return cls({_SLOT_BY_LABEL[label]: frozenset(ids) for label, ids in value.items()})

    def to_dict(self) -> dict[str, list[int]]:
        """``app_setting`` 저장용 JSON 형식. 칸 순서(N-2 → N+2), 각 칸은 릴리즈 ID 오름차순."""
        return {SLOT_LABELS[slot]: sorted(self.slots[slot]) for slot in sorted(self.slots)}

    def members(self, slot: Slot) -> frozenset[int]:
        return self.slots.get(Slot(slot), frozenset())

    def slot_of(self, release_version_id: int) -> Slot | None:
        """명시적으로 넣은 칸. 넣지 않았으면 None."""
        for slot, members in self.slots.items():
            if release_version_id in members:
                return slot
        return None

    @property
    def has_target(self) -> bool:
        return bool(self.members(Slot.N))


class GroupIssueCode(str, Enum):
    N_EMPTY = "N_EMPTY"                  # N 칸이 비어 목표 미지정
    MISSING_RELEASE = "MISSING_RELEASE"  # 그룹의 릴리즈가 계열에 없음(삭제·교체됨) → 무시
    ORDER_UNKNOWN = "ORDER_UNKNOWN"      # 그룹의 릴리즈 순서를 모름 → 그 버전만 판정, 기준점으로는 안 씀
    ORDER_CONFLICT = "ORDER_CONFLICT"    # 같은 순서 값이 겹침 → 기준점으로 안 씀
    ORDER_CROSSED = "ORDER_CROSSED"      # 오래된 버전이 더 높은 칸에 있음 (경고만)


@dataclass(frozen=True)
class GroupIssue:
    """N 그룹 확인 결과. ``blocks``가 True면 이 그룹으로 달성률을 계산할 수 없다."""

    code: GroupIssueCode
    message: str
    release_ids: tuple[int, ...] = ()
    blocks: bool = False


def _anchors(family, groups: VersionGroups) -> list[tuple[int, Slot]]:
    """판정 기준점: 그룹에 넣은 버전 중 계열에 있고 순서가 확인된(겹치지 않는) 것의 (순서, 칸)."""
    duplicated = set(family.sorted.duplicated_orders)
    found = []
    for slot, members in groups.slots.items():
        for member in members:
            release = family.get(member)
            if release is not None and release.version_order is not None and release.version_order not in duplicated:
                found.append((release.version_order, slot))
    return sorted(found)


def check_groups(family, groups: VersionGroups) -> tuple[GroupIssue, ...]:
    """N 그룹을 계열 릴리즈와 맞춰 보고 사용자에게 보여 줄 확인 항목을 만든다.

    param
    - family: 같은 계열의 릴리즈 목록 (``version.service.FamilyReleases``).
    - groups: 검사할 N 그룹.

    return
    - N 칸 비어 있음(차단), 없는 릴리즈·순서 모름·순서 겹침·거꾸로 묶음(경고) 목록.
    """
    issues: list[GroupIssue] = []
    if not groups.has_target:
        issues.append(GroupIssue(GroupIssueCode.N_EMPTY, "N 칸에 버전이 없어 목표 미지정입니다.", blocks=True))
    duplicated = set(family.sorted.duplicated_orders)
    known: list[tuple[int, Slot, ReleaseVersion]] = []
    for slot in sorted(groups.slots):
        for member in sorted(groups.slots[slot]):
            release = family.get(member)
            label = SLOT_LABELS[slot]
            if release is None:
                issues.append(GroupIssue(GroupIssueCode.MISSING_RELEASE,
                                         f"{label} 칸의 릴리즈 {member}가 이 계열에 없습니다(삭제·교체됨). 판정에서 뺍니다.",
                                         (member,)))
            elif release.version_order is None:
                issues.append(GroupIssue(GroupIssueCode.ORDER_UNKNOWN,
                                         f"{label} 칸의 {release.version_raw}는 버전 순서가 없어 다른 버전 판정의 기준으로 쓰지 않습니다.",
                                         (member,)))
            elif release.version_order in duplicated:
                issues.append(GroupIssue(GroupIssueCode.ORDER_CONFLICT,
                                         f"{label} 칸의 {release.version_raw}는 버전 순서가 다른 릴리즈와 겹쳐 기준으로 쓰지 않습니다.",
                                         (member,)))
            else:
                known.append((release.version_order, slot, release))
    known.sort()
    crossed = []
    for i, (order_a, slot_a, release_a) in enumerate(known):
        for order_b, slot_b, release_b in known[i + 1:]:
            if order_a < order_b and slot_a > slot_b:
                crossed.append((release_a, slot_a, release_b, slot_b))
    if crossed:
        a, slot_a, b, slot_b = crossed[0]
        more = f" 외 {len(crossed) - 1}건" if len(crossed) > 1 else ""
        issues.append(GroupIssue(
            GroupIssueCode.ORDER_CROSSED,
            f"버전 순서와 그룹 순서가 어긋납니다: 더 오래된 {a.version_raw}({SLOT_LABELS[slot_a]})가 "
            f"{b.version_raw}({SLOT_LABELS[slot_b]})보다 높은 칸에 있습니다{more}.",
            tuple(sorted({x.release_version_id for pair in crossed for x in (pair[0], pair[2])}))))
    return tuple(issues)


def classify(family, groups: VersionGroups, release: ReleaseVersion) -> tuple[Slot | None, CompareReason | None]:
    """등록된 릴리즈 하나의 칸을 정한다. 판단할 수 없으면 (None, 이유)."""
    explicit = groups.slot_of(release.release_version_id)
    if explicit is not None:
        return explicit, None
    if release.version_order is None:
        return None, CompareReason.ORDER_UNKNOWN
    if release.version_order in set(family.sorted.duplicated_orders):
        return None, CompareReason.ORDER_CONFLICT
    anchors = _anchors(family, groups)
    if not anchors:
        return None, CompareReason.TARGET_INVALID
    order = release.version_order
    if order > anchors[-1][0]:
        return Slot(min(max(anchors[-1][1] + 1, Slot.N_PLUS_1), Slot.N_PLUS_2)), None     # 규칙 3: 모두보다 최신
    older = [slot for anchor_order, slot in anchors if anchor_order < order]
    if not older:
        return Slot.N_MINUS_2, None                                                      # 규칙 3: 모두보다 오래됨
    return older[-1], None                                                               # 규칙 2: 바로 아래 칸


def compare_with_groups(family, version_raw: str | None, groups: VersionGroups, *,
                        release_version_id: int | None = None) -> VersionComparison:
    """설비에 적용된 버전 하나를 N 그룹과 비교한다. ``compare_version``과 같은 결과 형식이다.

    - 이상(AT_OR_ABOVE) = N 칸 이상, 미만(BELOW) = N-1·N-2 이하. ``steps_below``는 1(N-1) 또는 2(N-2 이하).
    - ``slot``에 칸(-2 ~ 2)을 담는다. 판단 불가면 None.
    - ``target``은 N 칸에서 가장 최신 릴리즈(화면 표시용)다.
    """
    def result(status, release=None, target=None, steps=None, reason=None, slot=None):
        return VersionComparison(version_raw, release, target, status, steps, reason, slot)

    if not family.releases:
        return result(CompareStatus.UNDETERMINED, reason=CompareReason.NO_RELEASE_DATA)
    release = (family.get(release_version_id) if release_version_id is not None
               else family.match(version_raw) if (version_raw or "").strip() else None)
    targets = [family.get(member) for member in groups.members(Slot.N)]
    targets = [t for t in targets if t is not None]
    if not groups.has_target:
        return result(CompareStatus.UNDETERMINED, release=release, reason=CompareReason.TARGET_NOT_SET)
    target = max(targets, key=lambda t: (t.version_order is not None, t.version_order or 0)) if targets else None
    if target is None:
        return result(CompareStatus.UNDETERMINED, release=release, reason=CompareReason.TARGET_INVALID)
    if release_version_id is not None:
        if release is None:
            return result(CompareStatus.UNDETERMINED, target=target, reason=CompareReason.OTHER_FAMILY)
    elif not (version_raw or "").strip():
        return result(CompareStatus.UNDETERMINED, target=target, reason=CompareReason.MISSING_VERSION)
    elif release is None:
        return result(CompareStatus.UNDETERMINED, target=target, reason=CompareReason.NOT_REGISTERED)
    slot, reason = classify(family, groups, release)
    if slot is None:
        return result(CompareStatus.UNDETERMINED, release, target, reason=reason)
    if slot >= Slot.N:
        return result(CompareStatus.AT_OR_ABOVE, release, target, slot=int(slot))
    return result(CompareStatus.BELOW, release, target, steps=-int(slot), slot=int(slot))


def tier_of(comparison: VersionComparison) -> Tier:
    """비교 결과의 색 단계. 그룹 판정이면 칸으로, 목표 1개 판정이면 같은 버전·단계 수로 정한다."""
    if comparison.status is CompareStatus.UNDETERMINED:
        return Tier.UNKNOWN
    if comparison.slot is not None:
        return (Tier.ABOVE if comparison.slot >= Slot.N_PLUS_1 else Tier.AT if comparison.slot == Slot.N
                else Tier.ONE_BELOW if comparison.slot == Slot.N_MINUS_1 else Tier.TWO_BELOW)
    if comparison.status is CompareStatus.AT_OR_ABOVE:
        same = (comparison.release is not None and comparison.target is not None
                and comparison.release.release_version_id == comparison.target.release_version_id)
        return Tier.AT if same else Tier.ABOVE
    # 목표 1개 판정에서 단계 수를 모르면(사이에 순서 미확정 릴리즈가 있음) N 미만은 확정이므로 노랑(N-1)으로 둔다.
    return Tier.TWO_BELOW if (comparison.steps_below or 1) >= 2 else Tier.ONE_BELOW


TIERS_WORST_FIRST: tuple[Tier, ...] = (Tier.TWO_BELOW, Tier.ONE_BELOW, Tier.AT, Tier.ABOVE)


def target_label(family, target) -> str:
    """목표 N 표기. AI 자료·보고서·화면이 같은 문구를 쓰도록 여기서만 만든다.

    param
    - family: 같은 계열의 릴리즈 목록 (``version.service.FamilyReleases``). 모르면 None.
    - target: 목표 릴리즈 ID 하나 또는 :class:`VersionGroups`.

    return
    - 릴리즈 하나면 버전 원문(없으면 "릴리즈 미확인").
    - N 그룹이면 최신 칸부터 칸별 버전 (예: "N 그룹 · N: v2.3 · N-1: v2.2"). 칸 안은 버전 순서대로 적고,
      계열에 없는 릴리즈는 "릴리즈 미확인"으로 적는다. N 칸이 비면 "N 그룹 · N 칸 비어 있음 (판단 불가)".
    """
    if not isinstance(target, VersionGroups):
        release = family.get(target) if family is not None and target is not None else None
        return release.version_raw if release else "릴리즈 미확인"
    if not target.has_target:
        return "N 그룹 · N 칸 비어 있음 (판단 불가)"
    parts = []
    for slot in sorted(target.slots, reverse=True):
        found = [family.get(member) for member in target.members(slot)] if family is not None else []
        names = [r.version_raw for r in sorted((r for r in found if r),
                                               key=lambda r: (r.version_order is None, r.version_order or 0))]
        names += ["릴리즈 미확인"] * (len(target.members(slot)) - len(names))
        parts.append(f"{SLOT_LABELS[slot]}: {', '.join(names)}")
    return "N 그룹 · " + " · ".join(parts)


def suggest_ladder(family, groups: VersionGroups) -> VersionGroups:
    """비어 있는 칸을 버전 순서상 바로 옆 버전 하나씩으로 채운 기본 후보 (사용자가 확인 후 저장).

    N 칸만 정한 경우 N-1·N-2·N+1·N+2 칸에 인접 버전을 하나씩 넣어, 기존 '한 단계 아래' 표시와 같은 모양을 만든다.
    이미 채운 칸과 다른 칸에 들어 있는 버전은 건드리지 않는다.
    """
    if not groups.has_target:
        return groups
    duplicated = set(family.sorted.duplicated_orders)
    ordered = [r for r in family.sorted.ordered if r.version_order not in duplicated]
    used = {member for members in groups.slots.values() for member in members}
    slots = {slot: set(members) for slot, members in groups.slots.items()}

    def orders(slot):
        found = [family.get(m) for m in slots.get(slot, ())]
        return [r.version_order for r in found if r is not None and r.version_order is not None
                and r.version_order not in duplicated]

    def nearest(limit, *, older):
        pool = [r for r in ordered if r.release_version_id not in used
                and (r.version_order < limit if older else r.version_order > limit)]
        return (pool[-1] if older else pool[0]) if pool else None

    if not orders(Slot.N):
        return groups
    for slot, previous, older in ((Slot.N_MINUS_1, Slot.N, True), (Slot.N_MINUS_2, Slot.N_MINUS_1, True),
                                  (Slot.N_PLUS_1, Slot.N, False), (Slot.N_PLUS_2, Slot.N_PLUS_1, False)):
        if slots.get(slot):
            continue
        reference = orders(previous) or orders(Slot.N)
        pick = nearest(min(reference) if older else max(reference), older=older)
        if pick is not None:
            slots[slot] = {pick.release_version_id}
            used.add(pick.release_version_id)
    return VersionGroups({slot: frozenset(members) for slot, members in slots.items()})
