"""버전 통일 참고 예측의 결과 형식 (명세 BE-020, FUN-040).

예측은 참고용 보조 기능이다 (멘토링 3차). 계열별로 기간의 처음과 끝 현황을 비교해 기존 설비의
패치 속도를 구하고, 남은 N 미만 건수에 단순 비례해 잔여 기간을 계산한다. 신규·전입 설비는
패치 실적으로 세지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class ForecastStatus(str, Enum):
    COMPLETE = "COMPLETE"              # 남은 N 미만·판단 불가가 없음. 잔여 0일
    NOT_CONFIRMED = "NOT_CONFIRMED"    # N 미만은 없지만 판단 불가가 남아 완료로 단정할 수 없음
    ESTIMATED = "ESTIMATED"            # 패치 속도로 잔여 기간을 계산함
    NOT_ESTIMABLE = "NOT_ESTIMABLE"    # 근거가 부족해 예측하지 않음


FORECAST_LABELS = {
    ForecastStatus.COMPLETE: "완료",
    ForecastStatus.NOT_CONFIRMED: "완료 단정 불가",
    ForecastStatus.ESTIMATED: "참고 예측",
    ForecastStatus.NOT_ESTIMABLE: "예측 불가",
}


class ForecastReason(str, Enum):
    """예측하지 않았거나 완료로 단정하지 않은 이유."""

    SINGLE_DATE = "SINGLE_DATE"
    TARGET_NOT_SET = "TARGET_NOT_SET"
    NO_PATCH = "NO_PATCH"
    NOT_AT_START = "NOT_AT_START"
    UNDETERMINED_LEFT = "UNDETERMINED_LEFT"


REASON_MESSAGES = {
    ForecastReason.SINGLE_DATE: "기간 안에 저장된 현황이 한 날짜뿐이라 패치 속도를 구할 수 없습니다.",
    ForecastReason.TARGET_NOT_SET: "이 계열의 목표 N이 지정되지 않아 예측하지 않습니다.",
    ForecastReason.NO_PATCH: "기간 동안 기존 설비가 N 이상으로 바뀐 기록이 없어 패치 속도를 구할 수 없습니다.",
    ForecastReason.NOT_AT_START: "기간 시작 날짜에 이 계열의 설비가 없어 기존 설비의 변화를 볼 수 없습니다.",
    ForecastReason.UNDETERMINED_LEFT: "판단 불가 건이 남아 있어 전체 완료로 단정하지 않습니다.",
}

ASSUMPTIONS = (
    "기간 동안의 기존 설비 패치 속도가 앞으로도 그대로 이어진다고 가정합니다.",
    "목표 N은 기간 내내 같으며, 기간 끝의 설비 집합을 기준으로 남은 건수를 셉니다.",
)
LIMITS = (
    "저장된 현황 날짜만 비교하므로 실제 패치 날짜와 다를 수 있는 참고용 계산입니다.",
    "신규·전입 설비는 패치 실적으로 세지 않습니다.",
    "다른 계열의 패치 속도를 섞지 않습니다.",
)


@dataclass(frozen=True)
class FamilyForecast:
    """계열 하나의 참고 예측. 건수 단위는 설비 1대 × 계열 1건이다.

    - ``patched``: 기간 처음에 N 미만이던 기존 설비 중 끝에 N 이상이 된 건수 (패치 실적).
    - ``regressed``: 기간 처음에 N 이상이던 기존 설비 중 끝에 N 미만이 된 건수.
    - ``new_units``: 기간 끝에만 있는 신규·전입 건수. ``new_compliant``는 그중 N 이상.
    - ``removed``: 기간 처음에만 있던 이탈 건수.
    - ``remaining_days``: 남은 N 미만 건을 처리하는 데 걸릴 일수. 예측하지 않으면 None.
    """

    family_id: int
    start_date: date
    end_date: date
    start_below: int
    end_compliant: int
    end_below: int
    end_undetermined: int
    patched: int
    regressed: int
    new_units: int
    new_compliant: int
    removed: int
    status: ForecastStatus
    remaining_days: float | None = None
    completion_date: date | None = None
    reasons: tuple[ForecastReason, ...] = ()

    @property
    def label(self) -> str:
        return FORECAST_LABELS[self.status]

    @property
    def period_days(self) -> int:
        return (self.end_date - self.start_date).days

    @property
    def messages(self) -> tuple[str, ...]:
        return tuple(REASON_MESSAGES[r] for r in self.reasons)


@dataclass(frozen=True)
class ForecastResult:
    """요청한 기간·범위의 계열별 참고 예측. 날짜가 없으면 ``start_date``·``end_date``는 None이다."""

    start_date: date | None
    end_date: date | None
    families: tuple[FamilyForecast, ...]
