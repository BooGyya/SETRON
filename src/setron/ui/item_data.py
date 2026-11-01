"""B-3 읽기 계약. 한 관측일의 자료만 받고 기존 Item 업무 판정을 재사용한다."""

from dataclasses import dataclass
from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord
from setron.features.item.schemas import Basis, TargetScope
from setron.features.item.service import ItemCatalog, search_items
from setron.ui.version_data import VersionCatalog


BASIS_LABELS = {
    Basis.STORED: "저장된 버전별 Item 상태",
    Basis.NOT_STORED: "이 버전의 저장 행 없음",
    Basis.NOT_IN_FAMILY: "이 계열의 대상 Item 아님",
    Basis.NOT_LISTED: "지정 모델 범위 밖",
    Basis.NO_RELEASE_DATA: "릴리즈 자료 없음",
    Basis.MISSING_VERSION: "관측 SW 버전 없음",
    Basis.NOT_REGISTERED: "등록 릴리즈 대응 없음",
    Basis.SCOPE_UNKNOWN: "모델 적용 범위 미확인",
}


@dataclass(frozen=True)
class ItemManagerData:
    versions: VersionCatalog
    records: tuple[EquipmentRecord, ...] = ()
    base_date: date | None = None
    observations_connected: bool = False
    observation_label: str = "설비 관측 자료 미연결"

    def __post_init__(self):
        if not isinstance(self.versions, VersionCatalog):
            raise ValueError("Item 릴리즈 자료 형식이 올바르지 않습니다.")
        if self.records and not self.observations_connected:
            raise ValueError("미연결 관측 자료를 사용할 수 없습니다.")
        if self.observations_connected and self.base_date is None:
            raise ValueError("관측 기준일이 필요합니다.")
        if any(r.base_date != self.base_date for r in self.records):
            raise ValueError("서로 다른 관측일을 합칠 수 없습니다.")
        if len({r.equipment_id for r in self.records}) != len(self.records):
            raise ValueError("같은 기준일의 설비 ID가 중복되었습니다.")

    def search(self, query=""):
        items = tuple(e.item for e in self.versions.items)
        found = search_items(items, query) if query.strip() else sorted(
            items, key=lambda i: (i.item_no is None, i.item_no or 0, i.item_id))
        entries = {e.item.item_id: e for e in self.versions.items}
        return tuple(entries[i.item_id] for i in found)

    def catalog(self):
        return ItemCatalog(self.versions.families(), self.versions.version_items)

    def scope_label(self, row):
        if row is None or row.target_scope is TargetScope.UNKNOWN:
            return "미확인"
        if row.target_scope is TargetScope.ALL:
            return "전체 모델 (해당 계열)"
        names = {p.model_id: p.model_name for p in self.versions.pairs}
        return ", ".join(names[mid] for mid in row.target_group_ids) or "지정 모델 없음"


def item_data_from_source(versions, source):
    """A와 같은 최신 관측일만 선택한다. 없는 날짜의 설비를 이전 관측으로 채우지 않는다."""
    if not source.dates:
        return ItemManagerData(versions, observation_label=source.source_label + " · 관측 기준일 없음")
    day = source.dates[-1]
    return ItemManagerData(versions, tuple(r for r in source.records if r.base_date == day),
                           day, True, source.source_label)
