"""질문에서 찾은 조회 조건을 검사하고 자료를 고르는 데 쓰는 형식 (명세 BE-018).

AI가 질문을 조회 조건(:class:`QueryConditions`)으로 바꾸면, 프로그램이 실제 선택지와 대조해
:class:`ResolvedQuery`로 확정한다. 확정한 조건 하나를 답변 자료 조회와 화면 이동에 함께 써서
답변에 쓴 조건과 이동한 화면의 조건이 같게 한다 (FUN-062).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from setron.features.aggregation.schemas import Summary
from setron.features.ai.schemas import QueryChoices, TargetView
from setron.features.item.schemas import Item
from setron.features.version.service import FamilyReleases


@dataclass(frozen=True)
class GroupChoice:
    """설비군 하나 (ERD `equipment_group`).

    ``name``은 화면 조회 칩의 설비군 이름(예: C4, Inline Ox), ``model_name``은 모델명(예: M-C4)이다.
    둘 다 같은 설비군을 가리킨다. ``category``는 POLY / OXIDE / KCTC / ETC이며 ETC(HPA)는 선택지에서 뺀다.
    """

    group_id: int
    name: str
    model_name: str
    category: str


@dataclass(frozen=True)
class FamilyChoice:
    """버전 계열 하나 (ERD `version_family`). ``name``은 화면 이름이다. 예: O2 Memory, C3 구UI."""

    family_id: int
    name: str
    group_id: int


@dataclass(frozen=True, kw_only=True)
class QueryCatalog:
    """질문 조건을 대조할 실제 선택지. Repository가 DB의 기준 자료로 만든다.

    - ``dates``: 현황이 저장된 기준일. 저장되지 않은 날짜는 다른 날짜로 대신하지 않는다.
    - ``releases``: 계열별 등록 릴리즈. 질문의 버전을 찾을 때 쓴다.
    - ``items``: 등록 Item. 질문의 Item을 찾을 때 쓴다.
    """

    sites: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()
    groups: tuple[GroupChoice, ...] = ()
    families: tuple[FamilyChoice, ...] = ()
    dates: tuple[date, ...] = ()
    releases: Mapping[int, FamilyReleases] = field(default_factory=dict)
    items: tuple[Item, ...] = ()

    @property
    def analysis_groups(self) -> tuple[GroupChoice, ...]:
        """분석 대상 설비군. HPA 같은 분석 제외 분류(ETC)는 뺀다."""
        return tuple(g for g in self.groups if g.category != "ETC")

    def choices(self) -> QueryChoices:
        """AI에 함께 줄 선택지."""
        groups = self.analysis_groups
        return QueryChoices(
            sites=self.sites,
            lines=self.lines,
            groups=tuple(g.name for g in groups),
            models=tuple(g.model_name for g in groups),
            families=tuple(f.name for f in self.families),
            base_dates=tuple(d.isoformat() for d in sorted(self.dates)),
        )


class IssueKind(str, Enum):
    """사용자 확인이 필요한 이유."""

    UNRESOLVED = "UNRESOLVED"          # AI가 해석하지 못했거나 후보 중 고르지 못함
    NOT_IN_CHOICES = "NOT_IN_CHOICES"  # 선택지에 없는 값
    NOT_FOUND = "NOT_FOUND"            # 등록된 버전·Item에서 찾지 못함
    AMBIGUOUS = "AMBIGUOUS"            # 해당하는 버전·Item이 여러 개
    DATE_NOT_STORED = "DATE_NOT_STORED"  # 현황이 저장되지 않은 기준일
    INVALID_DATE = "INVALID_DATE"      # 날짜 형식이 아님


ISSUE_MESSAGES = {
    IssueKind.UNRESOLVED: "질문에서 조건을 확정하지 못했습니다.",
    IssueKind.NOT_IN_CHOICES: "등록된 선택지에 없는 값입니다.",
    IssueKind.NOT_FOUND: "등록된 자료에서 찾지 못했습니다.",
    IssueKind.AMBIGUOUS: "해당하는 값이 여러 개입니다. 하나를 골라 주세요.",
    IssueKind.DATE_NOT_STORED: "이 날짜에 저장된 현황이 없습니다. 다른 날짜로 대신하지 않습니다.",
    IssueKind.INVALID_DATE: "날짜 형식(YYYY-MM-DD)이 아닙니다.",
}


@dataclass(frozen=True)
class ConditionIssue:
    """사용자에게 확인받을 조건 하나. ``candidates``는 고를 수 있는 후보 이름이다."""

    field: str
    value: str
    kind: IssueKind
    candidates: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        return ISSUE_MESSAGES[self.kind]


@dataclass(frozen=True, kw_only=True)
class ResolvedQuery:
    """선택지와 대조해 확정한 조회 조건. 비어 있는 조건은 '전체'다.

    ``issues``가 남아 있으면 조회하지 않고 사용자 확인을 받는다. 이때 ``target_view``는 None이라
    임의 조건으로 화면을 옮기지 않는다.
    """

    base_date: date | None = None
    sites: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()
    group_ids: tuple[int, ...] = ()
    family_ids: tuple[int, ...] = ()
    release_ids: tuple[int, ...] = ()
    item_ids: tuple[int, ...] = ()
    target_view: TargetView | None = None
    issues: tuple[ConditionIssue, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.issues

    @property
    def filters_pms(self) -> bool:
        """설비군·계열·버전 조건이 있어 PM 단위로 걸러야 하는지."""
        return bool(self.group_ids or self.family_ids or self.release_ids)


@dataclass(frozen=True)
class QueryAnswer:
    """질문 하나의 조회 결과. ``summary``는 확인할 조건이 남아 있으면 None이다.

    ``query``는 답변에 쓴 조건이며 화면 이동에도 그대로 넘긴다.
    """

    query: ResolvedQuery
    summary: Summary | None = None
