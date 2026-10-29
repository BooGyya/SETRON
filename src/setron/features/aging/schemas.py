"""버전 노후도 계산의 입력과 결과 형식 (명세 BE-016).

노후도는 버전 출시일부터 조회 기준 날짜까지의 경과 기간으로 정한다. 실제 위험 판정(BE-015)이나
N 대비 상태(BE-003)와 따로 표시하며, 노후도만으로 위험 설비를 세지 않는다 (REQ-062).
기간 기준은 ERD `app_setting`의 `version_age_thresholds`에 저장한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.version.schemas import ReleaseVersion

SETTING_KEY = "version_age_thresholds"


class AgeUnit(str, Enum):
    YEAR = "YEAR"
    MONTH = "MONTH"
    DAY = "DAY"


UNIT_LABELS = {AgeUnit.YEAR: "년", AgeUnit.MONTH: "개월", AgeUnit.DAY: "일"}


class ThresholdError(ValueError):
    """기간 기준이 올바르지 않을 때 발생한다. ``problems``에 보완할 내용을 담는다."""

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__(" ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class AgeThresholds:
    """주의·위험으로 표시할 경과 기간. ``warning`` 이상이면 주의, ``danger`` 이상이면 위험이다."""

    warning: int
    danger: int
    unit: AgeUnit

    def to_setting(self) -> dict:
        return {"warning": self.warning, "danger": self.danger, "unit": self.unit.value}

    @property
    def description(self) -> str:
        u = UNIT_LABELS[self.unit]
        return (
            f"{self.warning}{u} 미만 안전, {self.warning}{u} 이상 {self.danger}{u} 미만 주의, "
            f"{self.danger}{u} 이상 위험"
        )


DEFAULT_THRESHOLDS = AgeThresholds(1, 2, AgeUnit.YEAR)


@dataclass(frozen=True)
class LoadedThresholds:
    """저장된 기간 기준을 읽은 결과.

    저장된 값이 없으면 기본값을 쓰고 ``is_default``가 True다. 저장된 값이 잘못되었으면
    ``thresholds``는 None이고 ``problems``에 이유가 있다. 이때 노후도는 '기준 설정 오류'로 표시한다.
    """

    thresholds: AgeThresholds | None
    problems: tuple[str, ...] = ()
    is_default: bool = False


class AgeStatus(str, Enum):
    SAFE = "SAFE"
    WARNING = "WARNING"
    DANGER = "DANGER"
    UNKNOWN = "UNKNOWN"                      # 출시일·버전을 확인할 수 없음
    RELEASED_AFTER_BASE_DATE = "RELEASED_AFTER_BASE_DATE"  # 출시일이 조회 날짜보다 나중
    SETTING_INVALID = "SETTING_INVALID"      # 저장된 기간 기준이 잘못됨


AGE_LABELS = {
    AgeStatus.SAFE: "안전",
    AgeStatus.WARNING: "주의",
    AgeStatus.DANGER: "위험",
    AgeStatus.UNKNOWN: "미확인",
    AgeStatus.RELEASED_AFTER_BASE_DATE: "자료 확인 필요",
    AgeStatus.SETTING_INVALID: "기준 설정 오류",
}

# 화면 색상 이름. 실제 색 값은 화면에서 정한다. 계산할 수 없는 상태는 색 대신 문자로 구분한다.
AGE_COLORS = {AgeStatus.SAFE: "GREEN", AgeStatus.WARNING: "ORANGE", AgeStatus.DANGER: "RED"}


class AgeReason(str, Enum):
    """미확인으로 둔 이유."""

    MISSING_VERSION = "MISSING_VERSION"
    NOT_REGISTERED = "NOT_REGISTERED"
    RELEASE_DATE_MISSING = "RELEASE_DATE_MISSING"


@dataclass(frozen=True)
class VersionAge:
    """버전 하나의 노후도. 경과 기간은 계산할 수 있을 때만 있다."""

    status: AgeStatus
    released_at: date | None
    base_date: date
    elapsed_days: int | None = None
    elapsed_parts: tuple[int, int, int] | None = None  # (년, 개월, 일)
    reason: AgeReason | None = None

    @property
    def label(self) -> str:
        return AGE_LABELS[self.status]

    @property
    def color(self) -> str | None:
        return AGE_COLORS.get(self.status)

    @property
    def elapsed_label(self) -> str | None:
        """'1년 3개월', '5개월', '12일'처럼 보여줄 경과 기간."""
        if self.elapsed_parts is None:
            return None
        years, months, days = self.elapsed_parts
        parts = [f"{years}년"] if years else []
        if months:
            parts.append(f"{months}개월")
        return " ".join(parts) if parts else f"{days}일"


@dataclass(frozen=True)
class PmAge:
    pm: PmVersion
    release: ReleaseVersion | None
    age: VersionAge


@dataclass(frozen=True)
class EquipmentAge:
    """설비 한 대의 노후도. 혼합 설비는 계열별 기간을 ``pms``에 모두 남기고,
    가장 오래된 버전(``oldest``)을 설비의 대표 노후도로 쓴다 (FUN-072, REQ-029)."""

    record: EquipmentRecord
    pms: tuple[PmAge, ...]
    oldest: PmAge | None

    @property
    def status(self) -> AgeStatus:
        if self.oldest is not None:
            return self.oldest.age.status
        statuses = {p.age.status for p in self.pms}
        if AgeStatus.SETTING_INVALID in statuses:
            return AgeStatus.SETTING_INVALID
        if AgeStatus.RELEASED_AFTER_BASE_DATE in statuses:
            return AgeStatus.RELEASED_AFTER_BASE_DATE
        return AgeStatus.UNKNOWN

    @property
    def has_unchecked(self) -> bool:
        """경과 기간을 계산하지 못한 PM이 있는지. 대표 노후도가 실제보다 낮을 수 있음을 알린다."""
        return any(p.age.elapsed_days is None for p in self.pms)
