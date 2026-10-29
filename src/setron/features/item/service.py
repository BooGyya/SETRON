"""Item이 포함된 버전과 적용 설비를 찾는다 (명세 BE-014).

포함 판정 규칙 (REQ-026, FUN-034):
1. 그 버전의 Item 상태가 저장되어 있으면 그대로 쓴다.
2. 없으면 같은 계열에서 순서가 확인된 가장 가까운 이전 버전의 상태를 이어받는다 (누적 포함).
   v2에서 A, v3에서 B가 추가되면 v3에는 A·B가 모두 포함된다. 이전 버전에서 명시적으로 빠졌으면 미적용이다.
3. 처음 저장된 버전보다 앞선 버전은 아직 추가되지 않은 것이므로 미적용이다.
4. 순서를 모르거나 겹쳐서 선후를 알 수 없으면 미확인이다. 다른 계열의 Item은 이어받지 않는다.

모델 범위는 판정에 쓴 저장 행의 ``target_scope``로 정한다. LISTED 목록에 없는 모델은 대상 외,
범위를 모르면 미확인이다. Item 적용 여부는 결함 위험 해제(BE-015)와 따로 판단한다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.item.schemas import (
    Application,
    Basis,
    EquipmentApplication,
    Item,
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


def search_items(items: Iterable[Item], query: str) -> tuple[Item, ...]:
    """Item 내용이나 원본 항목 번호로 검색한다. 대소문자를 가리지 않는다."""
    keyword = query.strip().casefold()
    if not keyword:
        return ()
    found = [
        item for item in items
        if keyword in item.content.casefold() or (item.item_no is not None and keyword == str(item.item_no))
    ]
    return tuple(sorted(found, key=lambda i: (i.item_no is None, i.item_no or 0, i.item_id)))


class ItemCatalog:
    """릴리즈 순서와 버전별 Item 상태를 묶어 Item 포함·적용 여부를 판정한다.

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

        explicit = rows.get(release.release_version_id)
        if explicit is not None:
            return VersionItemState(release, explicit.state, Basis.EXPLICIT, explicit)

        if release.version_order is None:
            return VersionItemState(release, ItemState.UNKNOWN, Basis.ORDER_UNKNOWN)
        anchors = [(family.get(rid), row) for rid, row in rows.items()]
        if any(r.version_order is None for r, _ in anchors):
            return VersionItemState(release, ItemState.UNKNOWN, Basis.ORDER_UNKNOWN)
        duplicated = set(family.sorted.duplicated_orders)
        if release.version_order in duplicated or any(r.version_order in duplicated for r, _ in anchors):
            return VersionItemState(release, ItemState.UNKNOWN, Basis.ORDER_CONFLICT)

        earlier = [(r, row) for r, row in anchors if r.version_order < release.version_order]
        if not earlier:
            first = min(anchors, key=lambda pair: pair[0].version_order)[1]
            return VersionItemState(release, ItemState.NOT_APPLIED, Basis.NOT_YET_ADDED, first)
        _, nearest = max(earlier, key=lambda pair: pair[0].version_order)
        return VersionItemState(release, nearest.state, Basis.INHERITED, nearest)

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

    def summarize(self, item: Item, records: Iterable[EquipmentRecord]) -> ItemSummary:
        """선택한 Item의 설비별 적용 현황 (FUN-034, FUN-035). HPA는 빼고 뺀 대수만 알린다."""
        records = tuple(records)
        return ItemSummary(
            item,
            tuple(self.equipment_application(item.item_id, r) for r in records if not r.is_hpa),
            hpa_excluded=sum(r.is_hpa for r in records),
        )
