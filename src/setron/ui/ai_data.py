"""C-4 AI 질문 조건 해석 계약. DB·Qt를 쓰지 않는다.

질문 표기가 공통 조회 선택지의 이름·키와 정확히 같을 때만 연결한다. 원본 구역명을 Fab/Line으로,
비슷한 철자를 다른 선택지로 추정하지 않는다. 공통 조회가 지원하지 않는 복수 선택·기준일·모델·버전·Item
조건은 미지원으로 남기며, 사용자가 직접 제외하기 전에는 조건을 줄여 적용하지 않는다.
"""

from dataclasses import dataclass
from enum import Enum

from setron.features.ai.schemas import QueryConditions, TargetView
from setron.ui.query_state import QueryCatalog, QuerySelection


@dataclass(frozen=True)
class FreshAnalysisData:
    """요청 시 다시 읽은 동일 조회의 화면 스냅샷과 AI 전달 자료."""

    facts: dict
    snapshot: object


class ConditionStatus(Enum):
    MATCHED = "적용 가능"
    NOT_FOUND = "선택지 없음"
    MULTIPLE = "복수 선택 미지원"
    UNSUPPORTED = "공통 조회 미지원"
    UNRESOLVED = "해석 불가"


@dataclass(frozen=True)
class ConditionRow:
    dimension: str
    raw: str
    status: ConditionStatus
    message: str
    field: str | None = None  # QuerySelection 필드
    key: str | None = None


VIEW_ROUTES = {TargetView.DASHBOARD: "dashboard", TargetView.HEATMAP: "heatmap"}
VIEW_LABELS = {TargetView.DASHBOARD: "대시보드", TargetView.HEATMAP: "버전 히트맵",
               TargetView.ITEM_MANAGER: "아이템 매니저", TargetView.ANALYSIS: "AI 분석"}
_UNSUPPORTED = (("models", "모델"), ("families", "SW 계열"), ("versions", "버전"), ("items", "Item"))


def _match(values, options, dimension, field, *, extra=lambda option: True, missing=None):
    if len(values) > 1:
        return [ConditionRow(dimension, raw, ConditionStatus.MULTIPLE,
                             f"공통 조회는 {dimension}를 하나만 선택할 수 있습니다. 하나만 남기거나 질문을 나눠 주세요.")
                for raw in values]
    rows = []
    for raw in values:
        found = [o for o in options if raw in (o.label, o.key) and extra(o)]
        if len(found) == 1:
            rows.append(ConditionRow(dimension, raw, ConditionStatus.MATCHED, f"{dimension} ‘{found[0].label}’ 선택지와 같습니다.",
                                     field, found[0].key))
        else:
            rows.append(ConditionRow(dimension, raw, ConditionStatus.NOT_FOUND,
                                     missing or f"등록된 {dimension} 선택지에 같은 이름이 없습니다."
                                     if not found else f"같은 이름의 {dimension} 선택지가 여러 개라 하나로 정하지 못했습니다."))
    return rows


def interpret(conditions: QueryConditions, catalog: QueryCatalog) -> tuple[ConditionRow, ...]:
    rows = _match(conditions.sites, catalog.sites, "Site", "site_key")
    site_key = rows[0].key if len(rows) == 1 and rows[0].status is ConditionStatus.MATCHED else None
    rows += _match(conditions.lines, catalog.lines, "Line", "line_key",
                   extra=lambda o: site_key is None or o.site_key == site_key,
                   missing="등록된 Line 선택지에 없습니다. 원본 구역명을 Fab/Line으로 추정하지 않습니다.")
    rows += _match(conditions.groups, catalog.groups, "설비군", "group_key")
    for attr, dimension in _UNSUPPORTED:
        rows += [ConditionRow(dimension, raw, ConditionStatus.UNSUPPORTED,
                              f"공통 조회 조건에 {dimension} 항목이 없습니다. 적용하려면 데이터 계약 협의가 필요합니다.")
                 for raw in getattr(conditions, attr)]
    if conditions.base_date:
        rows.append(ConditionRow("기준일", conditions.base_date, ConditionStatus.UNSUPPORTED,
                                 "공통 조회에서 기준일을 선택할 수 없습니다. 날짜 조건은 적용되지 않습니다."))
    rows += [ConditionRow("해석 불가", raw, ConditionStatus.UNRESOLVED, "AI가 조회 조건으로 해석하지 못했습니다.")
             for raw in conditions.unresolved]
    return tuple(rows)


def check_apply(rows: tuple[ConditionRow, ...], included: set[int]) -> str | None:
    """적용할 수 없으면 이유를, 적용할 수 있으면 None을 돌려준다."""
    chosen = [rows[i] for i in sorted(included) if 0 <= i < len(rows)]
    blocked = [r for r in chosen if r.status is not ConditionStatus.MATCHED]
    if blocked:
        return (f"적용할 수 없는 조건 {len(blocked)}개가 포함되어 있습니다. 조건을 그대로 적용할 수 없으므로 "
                "질문을 고치거나, 해당 조건을 직접 제외한 뒤 적용해 주세요.")
    if not chosen:
        return "적용할 조건이 없습니다."
    return None


def selection_for(rows: tuple[ConditionRow, ...], included: set[int]) -> QuerySelection:
    reason = check_apply(rows, included)
    if reason is not None:
        raise ValueError(reason)
    values = {rows[i].field: rows[i].key for i in included}
    return QuerySelection(site_key=values.get("site_key"), line_key=values.get("line_key"),
                          group_key=values.get("group_key"))


def facts_from_snapshot(snapshot) -> dict:
    """분석에 보내는 자료. 대시보드가 같은 적용 조건으로 계산한 값만 그대로 옮긴다."""
    summary = snapshot.summary
    return {
        "조회 조건": snapshot.query.description,
        "적용 번호": snapshot.query.revision,
        "기준일": snapshot.base_date.isoformat(),
        "설비 수(HPA 제외)": summary.equipment_count,
        "업그레이드 필요 설비": summary.upgrade_needed,
        "N 이상 건수": summary.compliant,
        "N 미만 건수": summary.below,
        "판단 불가 건수": summary.undetermined,
        "N 이상 적용률(%)": None if summary.rate is None else round(summary.rate * 100, 1),
        "HPA 제외 설비": summary.hpa_excluded,
        "위험 후보 설비": len(snapshot.risky_ids),
        "위험 미확인 설비": snapshot.risk_unknown,
        "자료 출처": snapshot.source_label,
    }
