"""목표 N 선택 계약. 같은 모델·계열의 확인된 릴리즈만 새 목표로 허용한다. 저장 없음."""

from collections import Counter
from dataclasses import dataclass

from setron.features.version.schemas import ReleaseVersion
from setron.ui.dashboard_data import family_name, model_name
from setron.ui.query_state import TargetN


MODEL_APPLICABILITY_UNCONFIRMED = "모델 적용 근거 미확인 · 재확인 필요"


@dataclass(frozen=True)
class TargetOption:
    release: ReleaseVersion
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class TargetPair:
    model_id: int
    family_id: int
    group_key: str
    label: str
    current_id: int | None
    options: tuple[TargetOption, ...]

    @property
    def key(self):
        return self.model_id, self.family_id


def target_pairs(source) -> tuple[TargetPair, ...]:
    groups = {mid: group for group in source.catalog.groups for mid in source.bindings[group.key]}
    known = set(source.targets) | {(pm.group_id, pm.family_id) for record in source.records if not record.is_hpa for pm in record.pms}
    result = []
    for model_id, family_id in sorted(known):
        if model_id not in groups:
            raise ValueError("목표의 모델·설비군 연결이 미등록입니다.")
        family = source.families.get(family_id)
        releases = () if family is None else family.releases
        ids = Counter(r.release_version_id for r in releases)
        orders = Counter(r.version_order for r in releases if r.version_order is not None)
        options = []
        for release in releases:
            reason = ("다른 계열" if release.family_id != family_id else "릴리즈 ID 중복" if ids[release.release_version_id] > 1 else
                      "순서 미확인" if type(release.version_order) is not int else
                      "순서 중복" if orders[release.version_order] > 1 else None)
            if (reason is None and hasattr(source, "applicability")
                    and release.release_version_id not in source.applicability.get((model_id, family_id), ())):
                reason = MODEL_APPLICABILITY_UNCONFIRMED
            options.append(TargetOption(release, reason))
        options.sort(key=lambda o: (o.unavailable_reason is not None,
                                   o.release.version_order is None, o.release.version_order or 0, o.release.release_version_id))
        group = groups[model_id]
        result.append(TargetPair(model_id, family_id, group.key,
                                 f"{group.label} · {(getattr(source, 'model_names', None) or {}).get(model_id, f'모델 {model_id}')}"
                                 f" · 계열 {family_name(source, family_id)}",
                                 source.targets.get((model_id, family_id)), tuple(options)))
    return tuple(result)


def initial_targets(source) -> tuple[TargetN, ...]:
    return tuple(TargetN(p.model_id, p.family_id, p.current_id) for p in target_pairs(source))


def target_map(targets):
    return {(t.model_id, t.family_id): t.release_id for t in targets if t.release_id is not None}


def validate_targets(source, targets):
    values = tuple(targets)
    pairs = {pair.key: pair for pair in target_pairs(source)}
    if any(not isinstance(t, TargetN) for t in values) or len(values) != len(pairs):
        raise ValueError("모든 모델·계열의 목표를 중복 없이 전달해 주세요.")
    keys = {(t.model_id, t.family_id) for t in values}
    if keys != set(pairs):
        raise ValueError("목표의 모델·계열 범위가 다르거나 중복되었습니다.")
    for target in values:
        pair = pairs[target.model_id, target.family_id]
        eligible = {o.release.release_version_id for o in pair.options if o.unavailable_reason is None}
        # 기존 잘못된 N을 임의의 정상 N으로 바꾸지 않는다. 그대로 두면 기존 서비스가 판단 불가로 처리한다.
        if target.release_id not in (None, pair.current_id) and target.release_id not in eligible:
            raise ValueError(f"{pair.label}: 같은 계열의 순서가 확인된 릴리즈를 선택해 주세요.")
    return tuple(sorted(values, key=lambda t: (t.model_id, t.family_id)))


def describe_targets(source, targets):
    if targets is None:
        return "공급자 기본 기준"
    pairs = {p.key: p for p in target_pairs(source)}
    counts = Counter(p.group_key for p in pairs.values())
    names = {g.key: g.label for g in source.catalog.groups}
    parts = []
    for target in targets:
        pair = pairs.get((target.model_id, target.family_id))
        family = source.families.get(target.family_id)
        release = family.get(target.release_id) if family is not None and target.release_id is not None else None
        raw = "미지정" if target.release_id is None else release.version_raw if release is not None else "릴리즈 미확인"
        caption = ((names[pair.group_key] if counts[pair.group_key] == 1 else pair.label) if pair else
                   f"{model_name(source, target.model_id)} · 계열 {family_name(source, target.family_id)}")
        parts.append(f"{caption}: {raw}")
    return f"{len(parts)}개 모델·계열 · " + " / ".join(parts) if parts else "설정 가능한 모델·계열 없음"
