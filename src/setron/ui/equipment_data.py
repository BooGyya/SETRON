"""일반 분석의 전달 범위와 별도 최신 HPA 원본 조회를 구분하는 목록·상세 읽기 모델."""

from dataclasses import dataclass

from setron.features.aggregation.schemas import EquipmentRecord, EquipmentStatus
from setron.features.aggregation.service import summarize
from setron.features.risk.schemas import RiskStatus
from setron.ui.dashboard_data import DashboardSnapshot, DashboardSource
from setron.ui.heatmap_data import EquipmentTile, equipment_tiles, lookup_records, record_tiles
from setron.ui.target_data import target_map


@dataclass(frozen=True)
class EquipmentDetail:
    selected: EquipmentTile
    whole: EquipmentTile
    record: EquipmentRecord
    status: EquipmentStatus | None


@dataclass(frozen=True)
class EquipmentData:
    snapshot: DashboardSnapshot
    details: tuple[EquipmentDetail, ...]

    @property
    def equipment_ids(self):
        """일반 분석 관측과 별도 최신 HPA를 합친 조회 전용 호기 범위를 반환한다."""
        return tuple(detail.record.equipment_id for detail in self.details)


def equipment_data(snapshot: DashboardSnapshot, source: DashboardSource) -> EquipmentData:
    selected = equipment_tiles(snapshot, source)
    current = {r.equipment_id: r for r in source.records if r.base_date == snapshot.base_date}
    normal_ids = tuple(r.equipment_id for r in snapshot.selected_records if not r.is_hpa)
    if any(eid not in current for eid in normal_ids):
        raise ValueError("같은 기준일의 전체 SW 관측이 없습니다.")
    hpa = tuple(r for r in lookup_records(snapshot) if r.is_hpa)
    normal = tuple(current[eid] for eid in normal_ids)
    # 전체 판정은 같은 기준일/같은 호기의 모든 SW로 따로 계산한다. 선택 판정을 덮지 않는다.
    # 날짜가 다른 HPA는 공통 집계에 섞지 않고 원본 표시만 추가한다.
    summary = summarize(normal, source.families, target_map(snapshot.target_values))
    whole = {t.equipment_id: t for t in record_tiles(normal + hpa, summary, source,
             hpa_locations=dict(snapshot.hpa_lookup_locations))}
    records = {r.equipment_id: r for r in normal + hpa}
    statuses = {e.record.equipment_id: e for e in summary.equipment}
    return EquipmentData(snapshot, tuple(EquipmentDetail(tile, whole[tile.equipment_id], records[tile.equipment_id],
                                                        statuses.get(tile.equipment_id)) for tile in selected))


def validate_scope(data: EquipmentData, ids: tuple[int, ...]) -> bool:
    return len(ids) == len(set(ids)) and set(ids) <= set(data.equipment_ids)


def filtered_tiles(data, ids, *, text="", risk_only=False, goal=None, sort="code"):
    """표시 필터/정렬만 수행. 버전 문자열로 선후나 위험 순서를 추정하지 않는다."""
    if not validate_scope(data, ids) or sort not in ("code", "code-desc", "site"):
        raise ValueError("허용되지 않은 설비 범위 또는 정렬입니다.")
    scope, needle = set(ids), text.strip().casefold()
    tiles = tuple(detail.selected for detail in data.details if detail.selected.equipment_id in scope)
    result = [tile for tile in tiles if (not risk_only or tile.risk is RiskStatus.RISK)
              and (goal is None or tile.goal is goal) and (not needle or needle in tile.description.casefold())]
    key = (lambda t: (t.site_label, t.code.casefold(), t.equipment_id)) if sort == "site" else (
        lambda t: (t.code.casefold(), t.equipment_id))
    return tuple(sorted(result, key=key, reverse=sort == "code-desc"))
