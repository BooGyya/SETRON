"""일반 분석의 전달 범위와 별도 최신 HPA 원본 조회를 구분하는 목록·상세 읽기 모델."""

from dataclasses import dataclass, field

from setron.features.aggregation.schemas import EquipmentRecord, EquipmentStatus
from setron.features.aggregation.service import summarize
from setron.features.risk.schemas import RiskStatus
from setron.ui.dashboard_data import DashboardSnapshot, DashboardSource
from setron.ui.heatmap_data import EquipmentTile, equipment_tiles, lookup_records, record_tiles


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
    # 업그레이드 우선순위(BE-017)와 노후도(BE-016). 설비 ID → PriorityEntry. 순위가 없으면 '확인 필요'.
    priority: dict = field(default_factory=dict)
    thresholds: object = None

    @property
    def equipment_ids(self):
        """일반 분석 관측과 별도 최신 HPA를 합친 조회 전용 호기 범위를 반환한다."""
        return tuple(detail.record.equipment_id for detail in self.details)


def equipment_data(snapshot: DashboardSnapshot, source: DashboardSource) -> EquipmentData:
    selected = equipment_tiles(snapshot, source)
    # 화면 스냅샷과 같은 설비별 최신 관측 (QA Q-01). 실제 관측일은 기록마다 다를 수 있다.
    current = {r.equipment_id: r for r in source.current_records(snapshot.base_date)}
    normal_ids = tuple(r.equipment_id for r in snapshot.selected_records if not r.is_hpa)
    if any(eid not in current for eid in normal_ids):
        raise ValueError("기준일까지 저장된 전체 SW 관측이 없습니다.")
    hpa = tuple(r for r in lookup_records(snapshot) if r.is_hpa)
    normal = tuple(current[eid] for eid in normal_ids)
    # 전체 판정은 같은 기준일/같은 호기의 모든 SW로 따로 계산한다. 선택 판정을 덮지 않는다.
    # 날짜가 다른 HPA는 공통 집계에 섞지 않고 원본 표시만 추가한다.
    summary = summarize(normal, source.families, snapshot.effective_targets)
    whole = {t.equipment_id: t for t in record_tiles(normal + hpa, summary, source,
             hpa_locations=dict(snapshot.hpa_lookup_locations))}
    records = {r.equipment_id: r for r in normal + hpa}
    statuses = {e.record.equipment_id: e for e in summary.equipment}
    thresholds = age_thresholds(source)
    return EquipmentData(snapshot, tuple(EquipmentDetail(tile, whole[tile.equipment_id], records[tile.equipment_id],
                                                        statuses.get(tile.equipment_id)) for tile in selected),
                         upgrade_priority(snapshot, source, normal, thresholds), thresholds)


def age_thresholds(source):
    """노후도 기간 기준. DB 공급자는 저장된 기준(잘못되면 None), 합성 자료는 기본값(1년·2년)."""
    from setron.features.aging.schemas import DEFAULT_THRESHOLDS
    return getattr(source, "age_thresholds", DEFAULT_THRESHOLDS)


def upgrade_priority(snapshot, source, records, thresholds):
    """화면과 같은 위험 판정·목표 N으로 우선순위를 정한다 (BE-017). 위험 판정을 다시 하지 않는다."""
    from setron.features.priority.service import prioritize_from_risks
    from setron.features.risk.schemas import EquipmentRisk, RiskStatus, RiskSummary

    risks = getattr(source, "risks", {}) or {}
    assessor = getattr(source, "assessor", None)
    summary = RiskSummary(tuple(EquipmentRisk(r, risks.get(r.equipment_id, RiskStatus.UNKNOWN), ()) for r in records),
                          has_rules=bool(assessor is not None and assessor.rules))
    result = prioritize_from_risks(records, summary, source.families, thresholds, snapshot.base_date,
                                   targets=snapshot.effective_targets)
    return {entry.record.equipment_id: entry for entry in (*result.ranked, *result.needs_check)}


def validate_scope(data: EquipmentData, ids: tuple[int, ...]) -> bool:
    return len(ids) == len(set(ids)) and set(ids) <= set(data.equipment_ids)


def filtered_tiles(data, ids, *, text="", risk_only=False, goal=None, sort="code"):
    """표시 필터/정렬만 수행. 버전 문자열로 선후나 위험 순서를 추정하지 않는다."""
    if not validate_scope(data, ids) or sort not in ("code", "code-desc", "site", "priority"):
        raise ValueError("허용되지 않은 설비 범위 또는 정렬입니다.")
    scope, needle = set(ids), text.strip().casefold()
    tiles = tuple(detail.selected for detail in data.details if detail.selected.equipment_id in scope)
    result = [tile for tile in tiles if (not risk_only or tile.risk is RiskStatus.RISK)
              and (goal is None or tile.goal is goal) and (not needle or needle in tile.description.casefold())]
    if sort == "priority":
        # 순위가 있는 설비를 순위대로, 근거가 부족한 '확인 필요'와 HPA는 그 뒤에 설비명 순으로 둔다.
        def key(t):
            entry = data.priority.get(t.equipment_id)
            rank = entry.rank if entry is not None else None
            return (rank is None, rank or 0, t.code.casefold(), t.equipment_id)
        return tuple(sorted(result, key=key))
    key = (lambda t: (t.site_label, t.code.casefold(), t.equipment_id)) if sort == "site" else (
        lambda t: (t.code.casefold(), t.equipment_id))
    return tuple(sorted(result, key=key, reverse=sort == "code-desc"))
