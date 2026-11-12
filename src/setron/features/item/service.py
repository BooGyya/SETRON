"""Item이 포함된 버전과 적용 설비를 찾는다 (명세 BE-014).

포함 판정 규칙 (REQ-026, FUN-034):
1. 명시적으로 저장된 버전별 상태를 우선한다.
2. 2026-11-07 사용자 확인: 같은 계열의 업데이트는 누적된다. 행이 없는 후속 버전은
   확인된 순서상의 직전 포함 상태와 모델 범위를 이어받는다. 최초 포함 이전은 미적용이다.
   순서 미확인·중복·명시적 미확인/미포함 뒤의 공백은 추정하지 않는다.
3. 이 계열의 릴리즈에 행이 하나도 없는 Item은 이 계열의 대상이 아니다. 다른 계열의 Item은 가져오지 않는다.

모델 범위는 판정에 쓴 저장 행의 ``target_scope``로 정한다. LISTED 목록에 없는 모델은 대상 외,
범위를 모르면 미확인이다. Item 적용 여부는 결함 위험 해제(BE-015)와 따로 판단한다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.item.schemas import (
    Application,
    ApplicationCount,
    Basis,
    EquipmentApplication,
    FamilyInclusion,
    Item,
    ItemOverview,
    ItemState,
    ItemSummary,
    PmApplication,
    TargetScope,
    VersionItem,
    VersionItemState,
)
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

_STATE_TO_APPLICATION = {
    ItemState.APPLIED: Application.APPLIED,
    ItemState.NOT_APPLIED: Application.NOT_APPLIED,
    ItemState.UNKNOWN: Application.UNKNOWN,
}


def search_items(items: Iterable[Item], query: str, *, match_words: bool = False) -> tuple[Item, ...]:
    """Item 내용·JIRA 이슈 코드·원본 항목 번호로 검색한다. 대소문자를 가리지 않는다."""
    keyword = query.strip().casefold()
    if not keyword:
        return ()
    words = keyword.split() if match_words else (keyword,)
    found = [
        item for item in items
        if all(word in (item.content + " " + (item.jira_key or "")).casefold() for word in words)
        or (item.item_no is not None and keyword == str(item.item_no))
    ]
    return tuple(sorted(found, key=lambda i: (i.item_no is None, i.item_no or 0, i.item_id)))


class ItemCatalog:
    """릴리즈 목록과 저장된 버전별 Item 상태를 묶어 Item 포함·적용 여부를 판정한다.

    ``families``는 계열 ID별 릴리즈 목록(BE-003), ``version_items``는 저장된 버전별 Item 상태다.
    """

    def __init__(
        self,
        families: Mapping[int, FamilyReleases],
        version_items: Iterable[VersionItem],
    ) -> None:
        self.families = families
        release_family = {
            r.release_version_id: fid for fid, family in families.items() for r in family.releases
        }
        # item_id → family_id → release_version_id → 저장 행
        self._rows: dict[int, dict[int, dict[int, VersionItem]]] = defaultdict(lambda: defaultdict(dict))
        for row in version_items:
            fid = release_family.get(row.release_version_id)
            if fid is None:
                raise ValueError(f"릴리즈 {row.release_version_id}가 어느 계열에도 없습니다 (Item {row.item_id}).")
            self._rows[row.item_id][fid][row.release_version_id] = row

    def item_versions(self, item_id: int) -> tuple[VersionItemState, ...]:
        """Item이 저장된 계열의 모든 릴리즈에 대해 포함 여부를 계열·순서대로 돌려준다 (FUN-033)."""
        result = []
        for fid in sorted(self._rows.get(item_id, {})):
            family = self.families[fid]
            for release in (*family.sorted.ordered, *family.sorted.unordered):
                result.append(self.version_state(item_id, release))
        return tuple(result)

    def version_state(self, item_id: int, release: ReleaseVersion) -> VersionItemState:
        """릴리즈 버전 하나에 Item이 포함되는지 판정한다. 모델 범위는 아직 적용하지 않는다."""
        family = self.families.get(release.family_id)
        rows = self._rows.get(item_id, {}).get(release.family_id, {})
        if family is None or not rows:
            return VersionItemState(release, ItemState.NOT_APPLIED, Basis.NOT_IN_FAMILY)

        stored = rows.get(release.release_version_id)
        if stored is not None:
            return VersionItemState(release, stored.state, Basis.STORED, stored)
        current = family.get(release.release_version_id)
        if current is None or current.version_order is None:
            return VersionItemState(release, ItemState.UNKNOWN, Basis.NOT_STORED)
        ordered = [(family.get(rid), row) for rid, row in rows.items()]
        if any(r.version_order is None for r, _ in ordered):
            return VersionItemState(release, ItemState.UNKNOWN, Basis.NOT_STORED)
        previous = [(r, row) for r, row in ordered if r.version_order < current.version_order]
        if previous:
            origin, source = max(previous, key=lambda pair: pair[0].version_order)
            if (source.state is ItemState.APPLIED and not any(
                    origin.version_order <= order <= current.version_order
                    for order in family.sorted.duplicated_orders)):
                return VersionItemState(release, ItemState.APPLIED, Basis.INHERITED, source)
        else:
            origin, source = min(ordered, key=lambda pair: pair[0].version_order)
            if (source.state is ItemState.APPLIED and not any(
                    current.version_order <= order <= origin.version_order
                    for order in family.sorted.duplicated_orders)):
                return VersionItemState(release, ItemState.NOT_APPLIED, Basis.BEFORE_INCLUDED, source)
        return VersionItemState(release, ItemState.UNKNOWN, Basis.NOT_STORED)

    def pm_application(self, item_id: int, pm: PmVersion) -> PmApplication:
        """PM 하나에 Item이 적용됐는지 판정한다. 모델 범위까지 반영한다."""

        def result(application, basis, release=None):
            return PmApplication(pm, release, application, basis)

        family = self.families.get(pm.family_id)
        if family is None or not family.releases:
            return result(Application.UNKNOWN, Basis.NO_RELEASE_DATA)
        rows = self._rows.get(item_id, {}).get(pm.family_id, {})
        if not rows:
            return result(Application.NOT_TARGET, Basis.NOT_IN_FAMILY)
        if all(
            row.target_scope is TargetScope.LISTED and pm.group_id not in row.target_group_ids
            for row in rows.values()
        ):
            return result(Application.NOT_TARGET, Basis.NOT_LISTED)

        if pm.release_version_id is not None:
            release = family.get(pm.release_version_id)
            if release is None:
                return result(Application.UNKNOWN, Basis.NOT_REGISTERED)
        elif not (pm.version_raw or "").strip():
            return result(Application.UNKNOWN, Basis.MISSING_VERSION)
        else:
            release = family.match(pm.version_raw)
            if release is None:
                return result(Application.UNKNOWN, Basis.NOT_REGISTERED)

        state = self.version_state(item_id, release)
        if state.source is None:
            return result(_STATE_TO_APPLICATION[state.state], state.basis, release)
        scope = state.source.target_scope
        if scope is TargetScope.UNKNOWN:
            return result(Application.UNKNOWN, Basis.SCOPE_UNKNOWN, release)
        if scope is TargetScope.LISTED and pm.group_id not in state.source.target_group_ids:
            return result(Application.NOT_TARGET, Basis.NOT_LISTED, release)
        return result(_STATE_TO_APPLICATION[state.state], state.basis, release)

    def equipment_application(self, item_id: int, record: EquipmentRecord) -> EquipmentApplication:
        """설비 한 대의 판정. 대상 PM 중 하나라도 미적용이면 미적용, 모두 적용이면 적용, 그 외는 미확인이다.

        모든 PM이 대상 외이면(또는 PM 기록이 없으면) 설비도 대상 외다.
        """
        pms = tuple(self.pm_application(item_id, pm) for pm in record.pms)
        targets = [p.application for p in pms if p.application is not Application.NOT_TARGET]
        if not targets:
            application = Application.NOT_TARGET
        elif Application.NOT_APPLIED in targets:
            application = Application.NOT_APPLIED
        elif all(a is Application.APPLIED for a in targets):
            application = Application.APPLIED
        else:
            application = Application.UNKNOWN
        return EquipmentApplication(record, application, pms)

    def overview(self, item: Item, records: Iterable[EquipmentRecord]) -> ItemOverview:
        """AI 답변 카드용 요약: 계열별 포함 버전과 대상 설비의 적용 현황(전체·사업장별·미적용 목록)."""
        families = []
        for fid in sorted(self._rows.get(item.item_id, {})):
            sorted_releases = self.families[fid].sorted
            states = {s.release.release_version_id: s for s in self.item_versions(item.item_id)
                      if s.release.family_id == fid}

            def applied(release):
                state = states.get(release.release_version_id)
                return state is not None and state.state is ItemState.APPLIED

            ordered = tuple(r for r in sorted_releases.ordered if applied(r))
            unordered = tuple(r for r in sorted_releases.unordered if applied(r))
            first = ordered[0] if ordered and ordered[0].version_order not in sorted_releases.duplicated_orders else None
            families.append(FamilyInclusion(fid, first, ordered + unordered))

        summary = self.summarize(item, records)
        by_site = tuple((site, _application_count(e for e in summary.equipment if e.record.site == site))
                        for site in summary.sites)
        missing = tuple(sorted((e for e in summary.equipment if e.application is Application.NOT_APPLIED),
                               key=lambda e: (e.record.site, e.record.line or "", e.record.equipment_code)))
        return ItemOverview(item, tuple(families), _application_count(summary.equipment), by_site,
                            missing, summary.hpa_excluded)

    def summarize(self, item: Item, records: Iterable[EquipmentRecord]) -> ItemSummary:
        """선택한 Item의 설비별 적용 현황 (FUN-034, FUN-035). HPA는 빼고 뺀 대수만 알린다."""
        records = tuple(records)
        return ItemSummary(
            item,
            tuple(self.equipment_application(item.item_id, r) for r in records if not r.is_hpa),
            hpa_excluded=sum(r.is_hpa for r in records),
        )


def _application_count(equipment: Iterable[EquipmentApplication]) -> ApplicationCount:
    """판정별 대수. 대상 외도 따로 센다 (적용률 분모에는 넣지 않는다)."""
    counts = {a: 0 for a in Application}
    for e in equipment:
        counts[e.application] += 1
    return ApplicationCount(counts[Application.APPLIED], counts[Application.NOT_APPLIED], counts[Application.UNKNOWN],
                            counts[Application.NOT_TARGET])
