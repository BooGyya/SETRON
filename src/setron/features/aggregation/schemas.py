"""설비 수·업그레이드 필요 수·N 이상 적용률 집계의 입력과 결과 형식 (명세 BE-012).

입력은 한 기준 날짜의 설비 현황(:class:`EquipmentRecord`)이다. ERD의 `equipment_snapshot`,
`equipment_pm`, `equipment_version`을 Repository가 묶어서 만든다. 결과는 대시보드·히트맵·AI가
함께 쓰는 :class:`Summary`이며, 같은 입력이면 어느 화면에서나 같은 숫자가 나온다 (FUN-050).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from setron.features.version.schemas import CompareStatus, VersionComparison


@dataclass(frozen=True)
class PmVersion:
    """설비 한 대의 PM 하나에 적용된 SW 버전.

    - ``pm_name``: PM 고정 위치(PM1, PM2 …). PM 구분이 없는 일반 설비는 None이다.
    - ``group_id``: 이 PM의 모델. PM 구분이 없으면 설비의 대표 모델이다.
    - ``release_version_id``: 저장된 대응 릴리즈. 없으면 ``version_raw``로 찾는다.
    """

    pm_name: str | None
    group_id: int
    family_id: int
    version_raw: str | None
    release_version_id: int | None = None


@dataclass(frozen=True)
class EquipmentRecord:
    """한 기준 날짜의 설비 한 대. ``is_hpa``는 분석 제외 모델(`analysis_enabled` = false)인지다."""

    equipment_id: int
    equipment_code: str
    base_date: date
    site: str
    line: str | None
    model_name: str
    pms: tuple[PmVersion, ...]
    is_hpa: bool = False


@dataclass(frozen=True)
class PmComparison:
    pm: PmVersion
    comparison: VersionComparison


@dataclass(frozen=True)
class FamilyStatus:
    """설비 한 대 × 버전 계열 하나의 상태. 적용률 계산의 한 건이다.

    PM 중 하나라도 N 미만이면 BELOW, 모두 확인되어 N 이상이면 AT_OR_ABOVE, 그 외는 UNDETERMINED다.
    ``pm_mismatch``는 같은 계열 PM들의 버전 원문이 서로 다른 경우로, 집계에서 빼지 않고 표시만 한다.
    """

    equipment_id: int
    family_id: int
    status: CompareStatus
    pms: tuple[PmComparison, ...]
    pm_mismatch: bool


@dataclass(frozen=True)
class EquipmentStatus:
    """히트맵 사각형 하나에 해당하는 설비 상태. ``families``가 사각형 안의 계열별 영역이다."""

    record: EquipmentRecord
    families: tuple[FamilyStatus, ...]

    @property
    def needs_upgrade(self) -> bool:
        return any(f.status is CompareStatus.BELOW for f in self.families)


@dataclass(frozen=True)
class ModelCount:
    """모델별 대수와 업그레이드 필요 수. 혼합 설비는 각 모델에 한 대씩 들어간다."""

    group_id: int
    equipment_count: int
    upgrade_needed: int


@dataclass(frozen=True)
class RateCount:
    """설비·계열 건수와 N 이상 적용률. ``rate``는 판단 가능한 건이 없으면 None(계산 불가)이다."""

    compliant: int
    below: int
    undetermined: int

    @property
    def rate(self) -> float | None:
        determined = self.compliant + self.below
        return self.compliant / determined if determined else None

    @property
    def complete(self) -> bool:
        """전체 완료 여부. 판단 불가가 남아 있으면 판단 가능한 범위가 100%여도 완료가 아니다."""
        return self.rate == 1 and self.undetermined == 0


@dataclass(frozen=True, kw_only=True)
class FamilyCount(RateCount):
    """계열 하나의 건수. 날짜별 추이의 막대(``compliant``)와 꺾은선(``rate``)이 같은 값에서 나온다."""

    family_id: int


@dataclass(frozen=True, kw_only=True)
class Summary(RateCount):
    """한 기준 날짜·조회 범위의 공통 집계 결과.

    - ``equipment_count``: HPA를 뺀 실제 호기 수. 같은 호기는 한 대다.
    - ``upgrade_needed``: 한 계열이라도 N 미만인 호기 수. 같은 호기는 한 번만 센다.
    - ``hpa_excluded``: 집계에서 뺀 HPA 호기 수.
    - ``compliant``/``below``/``undetermined``: 설비·계열 건수. PM 개수로 늘리지 않는다.
    """

    base_date: date | None
    equipment_count: int = 0
    upgrade_needed: int = 0
    hpa_excluded: int = 0
    by_model: tuple[ModelCount, ...] = ()
    by_family: tuple[FamilyCount, ...] = ()
    equipment: tuple[EquipmentStatus, ...] = ()

    @property
    def has_data(self) -> bool:
        return bool(self.equipment_count or self.hpa_excluded)
