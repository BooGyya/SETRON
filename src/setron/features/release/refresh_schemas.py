"""릴리즈 변경 후 재판정에 사용하는 명시적 조회 조건과 반환 형식."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from setron.features.aggregation.schemas import Summary
from setron.features.item.schemas import ItemSummary
from setron.features.risk.schemas import RiskSummary
from setron.features.version.service import FamilyReleases


@dataclass(frozen=True)
class RefreshContext:
    """호출자가 확인한 목표와 적용 가능 릴리즈를 전달한다.

    ivar
    - targets: (모델 ID, 계열 ID)별 기존 목표. None은 미지정이며 저장하거나 자동 변경하지 않음.
    - applicability: (모델 ID, 계열 ID)별 적용 가능한 릴리즈 ID 튜플. 키가 없으면 미확인.
    - aliases: 계열 ID별 확인된 다른 버전 표기와 현재 릴리즈 원문의 대응.
    """

    targets: Mapping[tuple[int, int], int | None] = field(default_factory=dict)
    applicability: Mapping[tuple[int, int], tuple[int, ...]] = field(default_factory=dict)
    aliases: Mapping[int, Mapping[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetValidity:
    """선택한 목표를 유지한 채 재선택 필요 여부를 알린다.

    ivar
    - model_id, family_id: 목표를 구분하는 실제 모델과 계열 ID.
    - selected_release_id: 호출자가 전달한 기존 목표 ID. 잘못된 목표도 원래 값으로 반환.
    - valid: 같은 계열·확인된 순서·모델 적용 범위를 모두 만족하는지 여부.
    - reason: 미지정 또는 재선택이 필요한 이유. 유효하면 None.
    """

    model_id: int
    family_id: int
    selected_release_id: int | None
    valid: bool
    reason: str | None = None


@dataclass(frozen=True)
class DateJudgments:
    """한 관측일의 버전 비교·Item 적용·위험 판정을 함께 반환한다.

    ivar
    - base_date: 실제 저장된 현황의 기준일.
    - summary: 목표 유효성 검사를 반영한 공통 집계.
    - item_summaries: 저장된 Item별 설비 적용 판정.
    - risks: 수동 위험 지정과 미확인 연결을 반영한 판정. 기준 유무도 포함.
    """

    base_date: date
    summary: Summary
    item_summaries: tuple[ItemSummary, ...]
    risks: RiskSummary


@dataclass(frozen=True)
class ReleaseRefresh:
    """한 트랜잭션에서 읽은 릴리즈 자료와 전체 과거 현황의 재판정 결과.

    ivar
    - families: 현재 모든 계열의 릴리즈 자료.
    - targets: 전달받은 모델·계열별 기존 N의 유효성.
    - dates: 기록이 있는 기준일별 판정. 빈 날짜를 생성하지 않음.
    - unresolved_risk_ids: 연결이 미확인인 수동 위험 지정 ID. 비활성 지정도 포함.
    """

    families: Mapping[int, FamilyReleases]
    targets: tuple[TargetValidity, ...]
    dates: tuple[DateJudgments, ...]
    unresolved_risk_ids: tuple[int, ...]
