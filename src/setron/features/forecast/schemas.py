"""버전 통일 참고 예측의 결과 형식 (명세 BE-020, FUN-040).

예측은 참고용 보조 기능이다 (멘토링 3차). 계열별로 기간 처음과 끝의 N 이상 대수를 비교해 하루 증가 속도를
구하고, 끝에 남은 N 미만 대수에 단순 비례해 잔여 기간을 계산한다 ("2대 올리는 데 10일이면 40대는 그에 비례").
신규·전입 설비가 N 이상으로 들어온 것도 증가에 포함한다 (데이터 파트 `ai/svhm/core/forecast.py`와 같은 규칙).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class ForecastStatus(str, Enum):
    COMPLETE = "COMPLETE"              # 남은 N 미만·판단 불가가 없음. 잔여 0일
    NOT_CONFIRMED = "NOT_CONFIRMED"    # N 미만은 없지만 판단 불가가 남아 완료로 단정할 수 없음
    ESTIMATED = "ESTIMATED"            # 증가 속도로 잔여 기간을 계산함
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
    NO_PROGRESS = "NO_PROGRESS"
    UNDETERMINED_LEFT = "UNDETERMINED_LEFT"


REASON_MESSAGES = {
    ForecastReason.SINGLE_DATE: "이 계열의 현황이 한 날짜뿐이라 예측할 수 없습니다. 날짜가 다른 현황표가 2장 이상 필요합니다.",
    ForecastReason.TARGET_NOT_SET: "이 계열의 목표 N이 지정되지 않아 예측하지 않습니다.",
    ForecastReason.NO_PROGRESS: "기간 동안 N 이상 대수가 늘지 않아 예측할 수 없습니다.",
    ForecastReason.UNDETERMINED_LEFT: "판단 불가 건이 남아 있어 전체 완료로 단정하지 않습니다.",
}

ASSUMPTIONS = (
    "기간 처음과 끝 사이의 N 이상 대수 증가 속도가 앞으로도 그대로 이어진다고 가정합니다.",
    "목표 N은 기간 내내 같으며, 기간 끝 현황의 N 미만 대수를 남은 대수로 셉니다.",
)
LIMITS = (
    "저장된 현황 날짜만 비교하므로 실제 패치 날짜와 다를 수 있는 참고용 계산입니다.",
    "신규·전입 설비가 N 이상으로 들어온 것도 증가에 포함되고, N 미만으로 내려간 설비가 있으면 증가가 그만큼 줄어듭니다.",
    "다른 계열의 증가 속도를 섞지 않습니다.",
)


@dataclass(frozen=True)
class ForecastPoint:
    """노선도 그래프의 점 하나 (화면 10 "달성률 추이 및 통일 예측").

    ``projected``가 False이면 저장된 현황의 실제 대수(실선), True이면 증가 속도로 이어 그린 예측(점선)이다.
    """

    day: date
    applied: int
    below: int
    undetermined: int = 0
    projected: bool = False

    @property
    def rate(self) -> float | None:
        """N 이상 적용률 = N 이상 ÷ (N 이상 + N 미만). 판단 불가는 분모에서 뺀다. 계산할 수 없으면 None."""
        total = self.applied + self.below
        return self.applied / total if total else None


@dataclass(frozen=True)
class FamilyForecast:
    """계열 하나의 참고 예측. 건수 단위는 설비 1대 × 계열 1건이다.

    - ``start_date``·``end_date``: 기간 안에서 이 계열이 처음·마지막으로 관측된 날짜.
    - ``points``: 이 계열이 관측된 날짜 수 (화면의 "데이터 n장").
    - ``gained``: 끝 N 이상 대수 − 처음 N 이상 대수. ``rate_per_day``는 이를 기간 일수로 나눈 값.
    - ``end_below``: 끝에 남은 N 미만 대수. 잔여 기간 계산의 남은 대수다.
    - ``remaining_days``: 남은 대수를 처리하는 데 걸릴 일수(반올림). 예측하지 않으면 None.
    - ``history``: 이 계열이 관측된 날짜별 실제 대수. 노선도의 실선이다.
    """

    family_id: int
    start_date: date
    end_date: date
    points: int
    start_applied: int
    end_applied: int
    end_below: int
    end_undetermined: int
    status: ForecastStatus
    rate_per_day: float | None = None
    remaining_days: int | None = None
    completion_date: date | None = None
    reasons: tuple[ForecastReason, ...] = ()
    history: tuple[ForecastPoint, ...] = ()

    @property
    def label(self) -> str:
        return FORECAST_LABELS[self.status]

    @property
    def period_days(self) -> int:
        return (self.end_date - self.start_date).days

    @property
    def gained(self) -> int:
        return self.end_applied - self.start_applied

    @property
    def note(self) -> str:
        return f"참고용 · 데이터 {self.points}장"

    @property
    def messages(self) -> tuple[str, ...]:
        return tuple(REASON_MESSAGES[r] for r in self.reasons)


@dataclass(frozen=True)
class ForecastResult:
    """요청한 기간·범위의 계열별 참고 예측. 날짜가 없으면 ``start_date``·``end_date``는 None이다.

    ``overall``은 모든 계열을 합친 날짜별 실제 대수다. 노선도의 "전체" 비교선으로 쓴다.
    """

    start_date: date | None
    end_date: date | None
    families: tuple[FamilyForecast, ...]
    overall: tuple[ForecastPoint, ...] = ()
