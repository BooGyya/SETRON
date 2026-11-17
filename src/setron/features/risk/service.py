"""위험 설비를 판정한다 (명세 BE-015).

다음 중 하나라도 확인되면 위험이다 (REQ-008).
- 환경·안전 필수 Item(SAFETY_REQUIRED)이 적용되지 않음
- 결함 Item(DEFECT_ITEM)이 위험 범위의 버전에 포함됨. 시작 버전은 포함, 해제 버전부터 제외
- 직접 위험으로 지정한 버전(DIRECT_VERSION)을 사용함

자료가 부족하면 위험 없음으로 단정하지 않고 미확인으로 둔다. 다만 처리 기준은 다음과 같이 정했다.
- 결함 시작 버전이 없으면 범위 안인지 미확인이다. 해제 버전이 없으면 아직 해제되지 않은 것으로 본다.
- 대상 연결이 UNRESOLVED인 지정은 그 계열(모델)의 설비를 미확인으로 만든다.
- 등록된 위험 기준이 없으면 '위험 없음'으로 두되 결과에 기준이 없다는 표시를 남긴다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.item.schemas import Application, Basis, ItemState
from setron.features.item.service import ItemCatalog
from setron.features.risk.schemas import (
    EquipmentRisk,
    FindingBasis,
    MappingStatus,
    ORDERED_VERSION_RULE_TYPES,
    RiskFinding,
    RiskRule,
    RiskStatus,
    RiskSummary,
    RuleType,
    VERSION_RULE_TYPES,
)
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

_ITEM_UNCERTAIN = {Basis.NOT_IN_FAMILY, Basis.NO_RELEASE_DATA}


def validate_rule(rule: RiskRule, families: Mapping[int, FamilyReleases]) -> tuple[str, ...]:
    """위험 지정을 저장하기 전에 계열·대상·버전 범위가 맞는지 검사한다. 문제가 없으면 빈 tuple이다."""
    family = families.get(rule.family_id)
    if family is None:
        return (f"계열 {rule.family_id}의 릴리즈 정보가 없습니다.",)
    problems = []
    if rule.rule_type in (RuleType.SAFETY_REQUIRED, RuleType.DEFECT_ITEM) and rule.item_id is None:
        problems.append("대상 Item을 선택해 주세요.")
    if rule.rule_type in VERSION_RULE_TYPES and rule.release_version_id is None:
        problems.append("비교할 기준 버전을 선택해 주세요."
                        if rule.rule_type in ORDERED_VERSION_RULE_TYPES
                        else "위험으로 지정할 버전을 선택해 주세요.")
    named = {
        ("기준 버전" if rule.rule_type in ORDERED_VERSION_RULE_TYPES else "위험 버전"): rule.release_version_id,
        "시작 버전": rule.start_release_version_id,
        "해제 버전": rule.end_release_version_id,
    }
    for name, release_id in named.items():
        if release_id is not None and family.get(release_id) is None:
            problems.append(f"{name}이 같은 계열의 릴리즈에 없습니다. 다른 계열의 버전에는 지정할 수 없습니다.")
    if rule.rule_type in ORDERED_VERSION_RULE_TYPES:
        reference = family.get(rule.release_version_id)
        if reference is not None and (reference.version_order is None
                                      or reference.version_order in family.sorted.duplicated_orders):
            problems.append("기준 버전의 순서가 미확인 또는 중복입니다. 순서를 확인해 주세요.")
    start = family.get(rule.start_release_version_id) if rule.start_release_version_id else None
    end = family.get(rule.end_release_version_id) if rule.end_release_version_id else None
    if (
        start and end
        and start.version_order is not None and end.version_order is not None
        and start.version_order >= end.version_order
    ):
        problems.append("해제 버전은 시작 버전보다 뒤의 버전이어야 합니다.")
    return tuple(problems)


class RiskAssessor:
    """등록된 위험 지정으로 설비·버전의 위험 여부를 판정한다. 사용하지 않는 지정(`is_enabled` = false)은 뺀다."""

    def __init__(
        self,
        families: Mapping[int, FamilyReleases],
        catalog: ItemCatalog,
        rules: Iterable[RiskRule],
    ) -> None:
        self.families = families
        self.catalog = catalog
        self.rules = tuple(r for r in rules if r.is_enabled)

    def pm_findings(self, pm: PmVersion, record: EquipmentRecord | None = None) -> tuple[RiskFinding, ...]:
        """PM 하나에 해당하는 위험 지정을 모두 적용하고 위험·미확인 결과만 돌려준다."""
        findings = []
        for rule in self.rules:
            if rule.family_id != pm.family_id or (rule.group_id is not None and rule.group_id != pm.group_id):
                continue
            if rule.target_model_ids and pm.group_id not in rule.target_model_ids:
                continue
            if record is None and (rule.target_site is not None or rule.target_line is not None):
                findings.append(RiskFinding(rule, RiskStatus.UNKNOWN, FindingBasis.SCOPE_UNKNOWN, pm=pm))
                continue
            if record is not None and ((rule.target_site is not None and record.site != rule.target_site)
                                      or (rule.target_line is not None and record.line != rule.target_line)):
                continue
            finding = self._judge_pm(rule, pm)
            if finding is not None:
                findings.append(finding)
        return tuple(findings)

    def equipment_risk(self, record: EquipmentRecord) -> EquipmentRisk:
        """하나라도 위험이면 위험, 위험 없이 미확인이 있으면 미확인, 그 외는 위험 없음이다."""
        findings = tuple(f for pm in record.pms for f in self.pm_findings(pm, record))
        statuses = {f.status for f in findings}
        if RiskStatus.RISK in statuses:
            status = RiskStatus.RISK
        elif RiskStatus.UNKNOWN in statuses:
            status = RiskStatus.UNKNOWN
        else:
            status = RiskStatus.NO_RISK
        return EquipmentRisk(record, status, findings)

    def summarize(self, records: Iterable[EquipmentRecord]) -> RiskSummary:
        """위험 설비 집계 (FUN-005, FUN-039). HPA는 빼고 뺀 대수만 알린다."""
        records = tuple(records)
        return RiskSummary(
            tuple(self.equipment_risk(r) for r in records if not r.is_hpa),
            hpa_excluded=sum(r.is_hpa for r in records),
            has_rules=bool(self.rules),
        )

    def release_findings(self, release: ReleaseVersion) -> tuple[RiskFinding, ...]:
        """릴리즈 버전 하나의 위험 표시 (FUN-071, 히트맵 상세). 모델 범위는 보지 않는다."""
        findings = []
        for rule in self.rules:
            if rule.family_id != release.family_id:
                continue
            finding = self._judge_release(rule, release)
            if finding is not None:
                findings.append(finding)
        return tuple(findings)

    # --- PM 판정 -------------------------------------------------------------

    def _judge_pm(self, rule: RiskRule, pm: PmVersion) -> RiskFinding | None:
        def found(status, basis, release=None):
            return RiskFinding(rule, status, basis, pm=pm, release=release)

        if rule.mapping_status is MappingStatus.UNRESOLVED:
            return found(RiskStatus.UNKNOWN, FindingBasis.MAPPING_UNRESOLVED)

        if rule.rule_type is RuleType.SAFETY_REQUIRED:
            if rule.item_id is None:
                return found(RiskStatus.UNKNOWN, FindingBasis.TARGET_INCOMPLETE)
            applied = self.catalog.pm_application(rule.item_id, pm)
            if applied.application is Application.NOT_APPLIED:
                return found(RiskStatus.RISK, FindingBasis.SAFETY_NOT_APPLIED, applied.release)
            if applied.application is Application.UNKNOWN or applied.basis in _ITEM_UNCERTAIN:
                return found(RiskStatus.UNKNOWN, FindingBasis.SAFETY_UNKNOWN, applied.release)
            return None

        release = self._release_of(pm)
        if release is None:
            return found(RiskStatus.UNKNOWN, FindingBasis.VERSION_UNKNOWN)

        if rule.rule_type in VERSION_RULE_TYPES:
            condition = self._version_condition(rule, release)
            return found(*condition, release) if condition is not None else None

        if rule.item_id is None:
            return found(RiskStatus.UNKNOWN, FindingBasis.TARGET_INCOMPLETE, release)
        in_range = self._in_range(rule, release)
        if in_range is False:
            return None
        applied = self.catalog.pm_application(rule.item_id, pm)
        if applied.application is Application.NOT_APPLIED or (
            applied.application is Application.NOT_TARGET and applied.basis not in _ITEM_UNCERTAIN
        ):
            return None
        if in_range is None:
            return found(RiskStatus.UNKNOWN, FindingBasis.DEFECT_RANGE_UNKNOWN, release)
        if applied.application is Application.APPLIED:
            return found(RiskStatus.RISK, FindingBasis.DEFECT_IN_RANGE, release)
        return found(RiskStatus.UNKNOWN, FindingBasis.DEFECT_ITEM_UNKNOWN, release)

    # --- 릴리즈 판정 ---------------------------------------------------------

    def _judge_release(self, rule: RiskRule, release: ReleaseVersion) -> RiskFinding | None:
        def found(status, basis):
            return RiskFinding(rule, status, basis, release=release)

        if rule.mapping_status is MappingStatus.UNRESOLVED:
            return found(RiskStatus.UNKNOWN, FindingBasis.MAPPING_UNRESOLVED)
        if rule.rule_type in VERSION_RULE_TYPES:
            condition = self._version_condition(rule, release)
            return found(*condition) if condition is not None else None
        if rule.item_id is None:
            return found(RiskStatus.UNKNOWN, FindingBasis.TARGET_INCOMPLETE)

        state = self.catalog.version_state(rule.item_id, release)
        uncertain = state.state is ItemState.UNKNOWN or state.basis in _ITEM_UNCERTAIN
        if rule.rule_type is RuleType.SAFETY_REQUIRED:
            if uncertain:
                return found(RiskStatus.UNKNOWN, FindingBasis.SAFETY_UNKNOWN)
            if state.state is ItemState.NOT_APPLIED:
                return found(RiskStatus.RISK, FindingBasis.SAFETY_NOT_APPLIED)
            return None

        in_range = self._in_range(rule, release)
        if in_range is False or (not uncertain and state.state is ItemState.NOT_APPLIED):
            return None
        if in_range is None:
            return found(RiskStatus.UNKNOWN, FindingBasis.DEFECT_RANGE_UNKNOWN)
        if uncertain:
            return found(RiskStatus.UNKNOWN, FindingBasis.DEFECT_ITEM_UNKNOWN)
        return found(RiskStatus.RISK, FindingBasis.DEFECT_IN_RANGE)

    # --- 공통 ---------------------------------------------------------------

    def _version_condition(self, rule, release):
        if rule.release_version_id is None:
            return RiskStatus.UNKNOWN, FindingBasis.TARGET_INCOMPLETE
        if rule.rule_type is RuleType.DIRECT_VERSION:
            return ((RiskStatus.RISK, FindingBasis.DIRECT_VERSION)
                    if release.release_version_id == rule.release_version_id else None)
        family = self.families.get(rule.family_id)
        reference = family.get(rule.release_version_id) if family is not None else None
        if reference is None:
            return RiskStatus.UNKNOWN, FindingBasis.TARGET_INCOMPLETE
        current = family.get(release.release_version_id)
        if current is None:
            return RiskStatus.UNKNOWN, FindingBasis.VERSION_UNKNOWN
        if any(value.version_order is None or value.version_order in family.sorted.duplicated_orders
               for value in (current, reference)):
            return RiskStatus.UNKNOWN, FindingBasis.VERSION_ORDER_UNKNOWN
        # 릴리즈에서 확인된 순서만 비교한다. 기준 버전 자체는 미만·초과 양쪽에서 제외한다.
        if rule.rule_type is RuleType.VERSION_BELOW and current.version_order < reference.version_order:
            return RiskStatus.RISK, FindingBasis.VERSION_BELOW
        if rule.rule_type is RuleType.VERSION_ABOVE and current.version_order > reference.version_order:
            return RiskStatus.RISK, FindingBasis.VERSION_ABOVE
        return None

    def _release_of(self, pm: PmVersion) -> ReleaseVersion | None:
        family = self.families.get(pm.family_id)
        if family is None:
            return None
        if pm.release_version_id is not None:
            return family.get(pm.release_version_id)
        return family.match(pm.version_raw)

    def _in_range(self, rule: RiskRule, release: ReleaseVersion) -> bool | None:
        """버전이 [시작, 해제) 범위 안인지. 밖이 확실하면 False, 확인할 수 없으면 None이다."""
        family = self.families[rule.family_id]
        duplicated = set(family.sorted.duplicated_orders)

        def order_of(release_id):
            found = family.get(release_id) if release_id is not None else None
            if found is None or found.version_order is None or found.version_order in duplicated:
                return None
            return found.version_order

        order = order_of(release.release_version_id)
        if order is None:
            return None
        start = order_of(rule.start_release_version_id)
        end = order_of(rule.end_release_version_id)
        if end is not None and order >= end:
            return False
        if start is not None and order < start:
            return False
        if start is None:
            return None
        if rule.end_release_version_id is not None and end is None:
            return None
        return True
