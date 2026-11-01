"""B-1 읽기 계약. 원본 출처를 업무 객체와 묶으며 DB·Qt·저장은 사용하지 않는다."""

from dataclasses import dataclass

from setron.features.aggregation.schemas import PmVersion
from setron.features.item.schemas import (
    APPLICATION_LABELS, Item, ItemState, TargetScope, VersionItem,
)
from setron.features.item.service import ItemCatalog
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases, sort_releases


@dataclass(frozen=True)
class ModelFamily:
    model_id: int
    model_name: str
    family_id: int
    family_name: str


@dataclass(frozen=True)
class SourceReference:
    """필드별 원본 위치. 값이 없으면 확인되지 않은 것이며 파일을 자동으로 열지 않는다."""

    field: str
    document: str | None = None
    revision: str | None = None
    sheet: str | None = None
    cells: str | None = None
    batch_id: int | None = None


@dataclass(frozen=True)
class ReleaseEntry:
    release: ReleaseVersion
    model_ids: tuple[int, ...]
    sources: tuple[SourceReference, ...] = ()
    items_verified: bool = False


@dataclass(frozen=True)
class ItemEntry:
    item: Item
    sources: tuple[SourceReference, ...] = ()


@dataclass(frozen=True)
class VersionCatalog:
    pairs: tuple[ModelFamily, ...]
    releases: tuple[ReleaseEntry, ...]
    items: tuple[ItemEntry, ...] = ()
    version_items: tuple[VersionItem, ...] = ()
    source_label: str = "자료 공급자 미연결"

    def __post_init__(self):
        pairs = {(p.model_id, p.family_id) for p in self.pairs}
        if len(pairs) != len(self.pairs):
            raise ValueError("모델·계열 연결이 중복되었습니다.")
        for attr, name in (("model", "모델"), ("family", "계열")):
            labels = {}
            for pair in self.pairs:
                key, value = getattr(pair, attr + "_id"), getattr(pair, attr + "_name")
                if not value.strip() or (key in labels and labels[key] != value):
                    raise ValueError(f"{name} 이름이 비어 있거나 같은 ID의 이름이 다릅니다.")
                labels[key] = value
        releases = {e.release.release_version_id: e for e in self.releases}
        items = {e.item.item_id: e for e in self.items}
        if len(releases) != len(self.releases) or len(items) != len(self.items):
            raise ValueError("릴리즈 또는 Item ID가 중복되었습니다.")
        for entry in self.releases:
            r = entry.release
            if not entry.model_ids or len(set(entry.model_ids)) != len(entry.model_ids):
                raise ValueError("릴리즈의 모델 연결이 없거나 중복되었습니다.")
            if any((mid, r.family_id) not in pairs for mid in entry.model_ids):
                raise ValueError("등록되지 않은 모델·계열의 릴리즈입니다.")
            if r.version_order is not None and type(r.version_order) is not int:
                raise ValueError("확인된 순서는 정수 또는 미확인이어야 합니다.")
        seen = set()
        models = {p.model_id for p in self.pairs}
        for row in self.version_items:
            key = row.release_version_id, row.item_id
            if key in seen or row.release_version_id not in releases or row.item_id not in items:
                raise ValueError("버전별 Item 연결이 중복되었거나 대상이 없습니다.")
            seen.add(key)
            if not isinstance(row.state, ItemState) or not isinstance(row.target_scope, TargetScope):
                raise ValueError("Item 상태 또는 모델 범위가 올바르지 않습니다.")
            if not set(row.target_group_ids) <= models:
                raise ValueError("Item 범위에 등록되지 않은 모델이 있습니다.")
            family_id = releases[row.release_version_id].release.family_id
            if any((mid, family_id) not in pairs for mid in row.target_group_ids):
                raise ValueError("Item 범위의 모델이 릴리즈 계열에 연결되지 않았습니다.")
            if len(set(row.target_group_ids)) != len(row.target_group_ids):
                raise ValueError("Item 모델 범위가 중복되었습니다.")
            if row.target_scope is not TargetScope.LISTED and row.target_group_ids:
                raise ValueError("모델 목록은 LISTED 범위에만 지정할 수 있습니다.")
        # 원문 중복 등 기존 서비스의 계열 검증도 적용한다.
        self.families()

    def families(self):
        return {fid: FamilyReleases(fid, tuple(e.release for e in self.releases if e.release.family_id == fid))
                for fid in sorted({p.family_id for p in self.pairs})}

    def entry(self, release_id):
        return next((e for e in self.releases if e.release.release_version_id == release_id), None)

    def list_releases(self, model_id, family_id, query="", *, descending=True):
        """확인된 순서만 정렬. 순서 미확인은 항상 마지막이며 동일 순서는 선후를 주장하지 않는다."""
        candidates = {e.release.release_version_id: e for e in self.releases
                      if model_id in e.model_ids and e.release.family_id == family_id}
        ordered = sort_releases(e.release for e in candidates.values())
        releases = (*sorted(ordered.ordered, key=lambda r: (-r.version_order if descending else r.version_order,
                                                           r.release_version_id)), *ordered.unordered)
        keyword = query.strip().casefold()
        return tuple(candidates[r.release_version_id] for r in releases
                     if not keyword or keyword in r.version_raw.casefold()
                     or any(keyword in (ref.document or "").casefold() for ref in candidates[r.release_version_id].sources))

    def order_label(self, release):
        if release.version_order is None:
            return "순서 미확인"
        family = self.families()[release.family_id]
        suffix = " · 중복, 선후 미확인" if release.version_order in family.sorted.duplicated_orders else " · 확인됨"
        return str(release.version_order) + suffix

    def item_rows(self, release_id, model_id):
        """같은 계열의 Item을 표시하되 저장 행이 없으면 미확인으로 남긴다."""
        entry = self.entry(release_id)
        if entry is None or model_id not in entry.model_ids:
            return ()
        catalog = ItemCatalog(self.families(), self.version_items)
        r = entry.release
        family_ids = {e.release.release_version_id for e in self.releases if e.release.family_id == r.family_id}
        item_ids = {row.item_id for row in self.version_items if row.release_version_id in family_ids}
        result = []
        labels = {p.model_id: p.model_name for p in self.pairs}
        for item in sorted((i for i in self.items if i.item.item_id in item_ids),
                           key=lambda i: (i.item.item_no is None, i.item.item_no or 0, i.item.item_id)):
            state = catalog.version_state(item.item.item_id, r)
            row = state.source
            scope = ("미확인" if row is None or row.target_scope is TargetScope.UNKNOWN else
                     "전체 모델" if row.target_scope is TargetScope.ALL else
                     ", ".join(labels[mid] for mid in row.target_group_ids) or "지정 모델 없음")
            application = catalog.pm_application(item.item.item_id, PmVersion(None, model_id, r.family_id, r.version_raw, release_id))
            result.append((item, "미확인 · 저장 행 없음" if row is None else
                           {ItemState.APPLIED: "포함", ItemState.NOT_APPLIED: "미포함", ItemState.UNKNOWN: "미확인"}[row.state],
                           scope, APPLICATION_LABELS[application.application]))
        return tuple(result)

    def included_count(self, release_id):
        return sum(row.release_version_id == release_id and row.state is ItemState.APPLIED for row in self.version_items)


def catalog_from_dashboard(source):
    """기존 공급자의 확인된 모델/계열과 릴리즈를 재사용. 출처·출시일·Item을 추정하지 않는다."""
    groups = {mid: group.label for group in source.catalog.groups for mid in source.bindings[group.key]}
    known = set(source.targets) | {(pm.group_id, pm.family_id) for record in source.records if not record.is_hpa for pm in record.pms}
    pairs = tuple(ModelFamily(mid, f"{groups[mid]} · 모델 {mid}", fid, f"SW 계열 {fid}")
                  for mid, fid in sorted(known) if mid in groups)
    entries = tuple(ReleaseEntry(r, tuple(p.model_id for p in pairs if p.family_id == fid))
                    for fid, family in source.families.items() if any(p.family_id == fid for p in pairs)
                    for r in family.releases)
    return VersionCatalog(pairs, entries, source_label=source.source_label + " · 릴리즈 출처/Item 미연결")
