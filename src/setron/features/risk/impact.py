"""위험 규칙을 저장하기 전에 영향을 미리 계산한다 (명세 BE-015, 화면 17·19·50 "영향 미리보기").

규칙 초안 하나를 기존 규칙과 함께 적용해 보고, 저장하지 않은 채로 다음을 돌려준다.

- 초안이 위험·미확인으로 표시하는 릴리즈 (버전 순서대로). 예: ".43 포함 · .44 제외"
- 초안 때문에 위험·미확인이 되는 설비와, 등록 전후의 설비 위험 상태

판정 규칙은 :class:`RiskAssessor`와 같다. 미리보기는 판정 결과를 바꾸지 않으며 DB에 쓰지 않는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord
from setron.features.item.service import ItemCatalog
from setron.features.risk.schemas import FindingBasis, MappingStatus, RiskFinding, RiskRule, RiskStatus
from setron.features.risk.service import RiskAssessor, validate_rule
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

DRAFT_RULE_ID = 0  # 저장 전 초안의 임시 ID. 저장된 규칙 ID는 1부터다.


@dataclass(frozen=True)
class ReleaseImpact:
    """초안이 릴리즈 하나에 주는 표시. 위험·미확인일 때만 남긴다."""

    release: ReleaseVersion
    status: RiskStatus
    basis: FindingBasis


@dataclass(frozen=True)
class EquipmentImpact:
    """초안이 설비 한 대에 주는 영향.

    - ``findings``: 초안 규칙만 적용한 결과(위험·미확인).
    - ``before``·``after``: 기존 규칙만 / 기존 규칙 + 초안으로 판정한 설비 위험 상태.
    """

    record: EquipmentRecord
    findings: tuple[RiskFinding, ...]
    before: RiskStatus
    after: RiskStatus

    @property
    def status(self) -> RiskStatus:
        """초안만 보았을 때의 상태. 하나라도 위험이면 위험, 아니면 미확인이다."""
        return RiskStatus.RISK if any(f.status is RiskStatus.RISK for f in self.findings) else RiskStatus.UNKNOWN

    @property
    def changed(self) -> bool:
        return self.before is not self.after


@dataclass(frozen=True)
class RuleImpact:
    """위험 규칙 초안의 영향 미리보기.

    ``problems``가 있으면 저장할 수 없는 초안이며 릴리즈·설비 영향은 계산하지 않는다.
    ``mapping_unresolved``이면 대상 연결을 확인하기 전이라 해당 계열 설비가 모두 미확인으로 표시된다.
    """

    rule: RiskRule
    base_date: date | None
    problems: tuple[str, ...] = ()
    releases: tuple[ReleaseImpact, ...] = ()
    equipment: tuple[EquipmentImpact, ...] = ()
    hpa_excluded: int = 0

    @property
    def saveable(self) -> bool:
        return not self.problems

    @property
    def mapping_unresolved(self) -> bool:
        return self.rule.mapping_status is MappingStatus.UNRESOLVED

    def count(self, status: RiskStatus, site: str | None = None) -> int:
        """초안만 보았을 때 ``status``인 설비 수. ``site``를 주면 그 사업장만 센다."""
        return sum(1 for e in self.equipment if e.status is status and (site is None or e.record.site == site))

    @property
    def newly_risky(self) -> int:
        """등록하면 새로 위험이 되는 설비 수 (기존 규칙으로는 위험이 아니었음)."""
        return sum(1 for e in self.equipment if e.before is not RiskStatus.RISK and e.after is RiskStatus.RISK)

    @property
    def already_risky(self) -> int:
        """초안이 위험으로 보지만 기존 규칙으로도 이미 위험이던 설비 수."""
        return sum(1 for e in self.equipment if e.status is RiskStatus.RISK and e.before is RiskStatus.RISK)

    def by_site(self) -> dict[str, Counter]:
        """사업장별 초안 상태 건수. 화면의 "평택 3 · 화성 2" 같은 요약에 쓴다."""
        result: dict[str, Counter] = {}
        for e in self.equipment:
            result.setdefault(e.record.site, Counter())[e.status] += 1
        return dict(sorted(result.items()))


def preview_rule_impact(
    draft: RiskRule,
    families: Mapping[int, FamilyReleases],
    catalog: ItemCatalog,
    rules: Iterable[RiskRule],
    records: Iterable[EquipmentRecord],
    *,
    base_date: date | None = None,
    problems: Iterable[str] = (),
) -> RuleImpact:
    """초안 ``draft``를 저장했다고 가정하고 릴리즈·설비 영향을 계산한다. 아무것도 저장하지 않는다.

    param
    - draft: 저장 전 규칙. ID는 :data:`DRAFT_RULE_ID`로 바꿔 기존 규칙과 구분한다.
    - families, catalog, rules: 현재 계열별 릴리즈, Item 연결, 저장된 위험 규칙.
    - records: 영향을 볼 설비 관측(보통 최신 기준일). HPA는 빼고 뺀 대수만 센다.
    - base_date: 화면에 표시할 관측 기준일.
    - problems: 호출자가 먼저 찾은 저장 불가 사유(DB 연결 검사 등). 계열·범위 검사 결과와 합친다.

    return
    - 영향 미리보기. 저장 불가 사유가 있으면 영향은 비워 둔다.
    """
    draft = replace(draft, risk_rule_id=DRAFT_RULE_ID)
    rules = tuple(r for r in rules if r.risk_rule_id != DRAFT_RULE_ID)
    records = tuple(records)
    hpa = sum(r.is_hpa for r in records)
    found = tuple(dict.fromkeys((*problems, *validate_rule(draft, families))))
    if found:
        return RuleImpact(draft, base_date, found, hpa_excluded=hpa)
    if not draft.is_enabled:
        return RuleImpact(draft, base_date, hpa_excluded=hpa)

    only = RiskAssessor(families, catalog, (draft,))
    before = RiskAssessor(families, catalog, rules)
    after = RiskAssessor(families, catalog, (*rules, draft))
    family = families[draft.family_id]
    sorted_releases = family.sorted
    releases = tuple(
        ReleaseImpact(release, finding.status, finding.basis)
        for release in (*sorted_releases.ordered, *sorted_releases.unordered)
        for finding in only.release_findings(release)
    )
    equipment = []
    for record in records:
        if record.is_hpa:
            continue
        findings = only.equipment_risk(record).findings
        if not findings:
            continue
        equipment.append(EquipmentImpact(record, findings, before.equipment_risk(record).status,
                                         after.equipment_risk(record).status))
    equipment.sort(key=lambda e: (e.status is not RiskStatus.RISK, e.record.site, e.record.equipment_code))
    return RuleImpact(draft, base_date, (), releases, tuple(equipment), hpa)
