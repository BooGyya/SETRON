"""위험 설비 판정의 입력과 결과 형식 (명세 BE-015).

입력은 ERD `risk_rule`(:class:`RiskRule`)이다. `target_reference.item_manager`의 명시적 범위는
모델·Site·Line 제한으로 읽으며 나머지 원본 재연결 근거와 표시 정보는 위험 판단에 사용하지 않는다.
위험 여부는 N 대비 격차나 노후도와 따로 판단한다 (REQ-008).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.version.schemas import ReleaseVersion


class RuleType(str, Enum):
    SAFETY_REQUIRED = "SAFETY_REQUIRED"  # 환경·안전 필수 Item: 미적용이면 위험
    DEFECT_ITEM = "DEFECT_ITEM"          # 결함 Item: 시작~해제 범위 버전에 포함되면 위험
    DIRECT_VERSION = "DIRECT_VERSION"    # 사용자가 직접 위험으로 지정한 버전


class RiskKind(str, Enum):
    """위험 후보를 '버전을 썼을 때' 기준으로 두 갈래로 나눈 표시 분류. 판정 자체는 바꾸지 않는다.

    - APPLIED(적용 위험): 결함 Item이 든 버전이나 위험 버전을 사용(적용)해서 위험이다.
    - NOT_APPLIED(미적용 위험): 적용되어 있어야 할 필수 Item이 그 버전에 적용되지 않아 위험이다.
    """

    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"


RULE_KINDS = {
    RuleType.DEFECT_ITEM: RiskKind.APPLIED,
    RuleType.DIRECT_VERSION: RiskKind.APPLIED,
    RuleType.SAFETY_REQUIRED: RiskKind.NOT_APPLIED,
}
RISK_KIND_LABELS = {RiskKind.APPLIED: "적용 위험", RiskKind.NOT_APPLIED: "미적용 위험"}
RISK_KIND_DESCRIPTIONS = {
    RiskKind.APPLIED: "결함 Item·위험 버전을 사용 중",
    RiskKind.NOT_APPLIED: "필수 Item이 적용되지 않음",
}


class MappingStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class RiskRule:
    """위험 지정 하나 (ERD `risk_rule`).

    - ``group_id``: 특정 모델에만 적용할 때의 모델 ID. None이면 계열 전체다.
    - ``start_release_version_id``: 결함 위험 시작 버전(포함). None이면 미확인이다.
    - ``end_release_version_id``: 결함 위험 해제 버전(제외). None이면 아직 해제되지 않은 것으로 본다.
    - ``mapping_status``: UNRESOLVED이면 대상 연결이 불확실하므로 해당 계열 설비를 미확인으로 둔다.
    - ``target_model_ids``·``target_site``·``target_line``: Item 관리에서 선택한 추가 범위. 비어 있으면 전체다.
      Line은 원문으로만 비교하며 Fab으로 추정하지 않는다. 이름·분류·메모는 표시용이다.
    """

    risk_rule_id: int
    rule_type: RuleType
    family_id: int
    risk_reason: str
    group_id: int | None = None
    item_id: int | None = None
    release_version_id: int | None = None
    start_release_version_id: int | None = None
    end_release_version_id: int | None = None
    is_enabled: bool = True
    mapping_status: MappingStatus = MappingStatus.UNRESOLVED
    target_model_ids: tuple[int, ...] = ()
    target_site: str | None = None
    target_line: str | None = None
    display_name: str = ""
    category: str = ""
    memo: str = ""


class RiskStatus(str, Enum):
    RISK = "RISK"
    NO_RISK = "NO_RISK"
    UNKNOWN = "UNKNOWN"


RISK_LABELS = {
    RiskStatus.RISK: "위험",
    RiskStatus.NO_RISK: "위험 없음",
    RiskStatus.UNKNOWN: "미확인",
}


class FindingBasis(str, Enum):
    """위험 또는 미확인으로 판정한 근거."""

    SAFETY_NOT_APPLIED = "SAFETY_NOT_APPLIED"
    SAFETY_UNKNOWN = "SAFETY_UNKNOWN"
    DEFECT_IN_RANGE = "DEFECT_IN_RANGE"
    DEFECT_RANGE_UNKNOWN = "DEFECT_RANGE_UNKNOWN"
    DEFECT_ITEM_UNKNOWN = "DEFECT_ITEM_UNKNOWN"
    DIRECT_VERSION = "DIRECT_VERSION"
    VERSION_UNKNOWN = "VERSION_UNKNOWN"
    TARGET_INCOMPLETE = "TARGET_INCOMPLETE"
    MAPPING_UNRESOLVED = "MAPPING_UNRESOLVED"
    SCOPE_UNKNOWN = "SCOPE_UNKNOWN"


BASIS_MESSAGES = {
    FindingBasis.SAFETY_NOT_APPLIED: "환경·안전 필수 Item이 적용되지 않았습니다.",
    FindingBasis.SAFETY_UNKNOWN: "환경·안전 필수 Item의 적용 여부를 확인할 수 없습니다.",
    FindingBasis.DEFECT_IN_RANGE: "결함 Item이 위험 범위의 버전에 포함되어 있습니다.",
    FindingBasis.DEFECT_RANGE_UNKNOWN: "결함 위험 범위(시작·해제 버전) 안인지 확인할 수 없습니다.",
    FindingBasis.DEFECT_ITEM_UNKNOWN: "결함 Item의 포함 여부를 확인할 수 없습니다.",
    FindingBasis.DIRECT_VERSION: "위험 버전으로 지정된 버전입니다.",
    FindingBasis.VERSION_UNKNOWN: "버전이 비어 있거나 릴리즈에 없어 위험 기준을 확인할 수 없습니다.",
    FindingBasis.TARGET_INCOMPLETE: "위험 지정의 대상 Item 또는 버전이 비어 있습니다.",
    FindingBasis.MAPPING_UNRESOLVED: "위험 지정의 대상 연결이 확인되지 않았습니다.",
    FindingBasis.SCOPE_UNKNOWN: "위치 범위를 판정할 설비 관측이 없습니다.",
}


@dataclass(frozen=True)
class RiskFinding:
    """위험 지정 하나를 PM(또는 릴리즈 버전) 하나에 적용한 결과. 위험·미확인일 때만 남긴다."""

    rule: RiskRule
    status: RiskStatus
    basis: FindingBasis
    pm: PmVersion | None = None
    release: ReleaseVersion | None = None

    @property
    def message(self) -> str:
        return BASIS_MESSAGES[self.basis]

    @property
    def kind(self) -> RiskKind:
        return RULE_KINDS[self.rule.rule_type]


def risk_kinds(findings) -> tuple[RiskKind, ...]:
    """위험(RISK)으로 판정된 근거의 분류를 적용 위험 → 미적용 위험 순으로 중복 없이 돌려준다. 미확인은 넣지 않는다."""
    found = {f.kind for f in findings if f.status is RiskStatus.RISK}
    return tuple(kind for kind in RiskKind if kind in found)


@dataclass(frozen=True)
class EquipmentRisk:
    """설비 한 대의 위험 판정. 사유가 여러 개여도 한 대이며 사유는 ``findings``에 모두 남긴다."""

    record: EquipmentRecord
    status: RiskStatus
    findings: tuple[RiskFinding, ...]

    @property
    def label(self) -> str:
        return RISK_LABELS[self.status]

    @property
    def reasons(self) -> tuple[RiskFinding, ...]:
        return tuple(f for f in self.findings if f.status is RiskStatus.RISK)


@dataclass(frozen=True)
class RiskSummary:
    """위험 설비 집계. ``has_rules``가 False이면 등록된 위험 기준이 없어 '위험 없음'이 확인된 결과가 아니다."""

    equipment: tuple[EquipmentRisk, ...]
    hpa_excluded: int = 0
    has_rules: bool = True

    def counts(self, site: str | None = None) -> dict[RiskStatus, int]:
        counter = Counter(e.status for e in self.equipment if site is None or e.record.site == site)
        return {s: counter.get(s, 0) for s in RiskStatus}

    @property
    def risky(self) -> int:
        return self.counts()[RiskStatus.RISK]

    @property
    def sites(self) -> tuple[str, ...]:
        return tuple(sorted({e.record.site for e in self.equipment}))
