"""두 관측 날짜의 버전 변경 사실과 확인 가능한 릴리즈 방향을 구분한다 (BE-013)."""

from dataclasses import dataclass
from datetime import date
from enum import Enum

from setron.features.equipment.schemas import StoredPm, StoredSnapshot, StoredSoftware

from .schemas import HistoryFrame


class ChangeStatus(str, Enum):
    CHANGED = "CHANGED"
    UNCHANGED = "UNCHANGED"
    UNDETERMINED = "UNDETERMINED"


class VersionDirection(str, Enum):
    UPGRADE = "UPGRADE"
    DOWNGRADE = "DOWNGRADE"
    UNCHANGED = "UNCHANGED"
    UNKNOWN = "UNKNOWN"


class ChangeReason(str, Enum):
    MISSING_BEFORE_SNAPSHOT = "MISSING_BEFORE_SNAPSHOT"
    MISSING_AFTER_SNAPSHOT = "MISSING_AFTER_SNAPSHOT"
    MISSING_BEFORE_POSITION = "MISSING_BEFORE_POSITION"
    MISSING_AFTER_POSITION = "MISSING_AFTER_POSITION"
    OUTSIDE_BEFORE_SCOPE = "OUTSIDE_BEFORE_SCOPE"
    OUTSIDE_AFTER_SCOPE = "OUTSIDE_AFTER_SCOPE"
    TRACKING_CHANGED = "TRACKING_CHANGED"
    FAMILY_CHANGED = "FAMILY_CHANGED"
    RELEASE_UNCONFIRMED = "RELEASE_UNCONFIRMED"
    ORDER_UNKNOWN = "ORDER_UNKNOWN"
    ORDER_CONFLICT = "ORDER_CONFLICT"


REASON_MESSAGES = {
    ChangeReason.MISSING_BEFORE_SNAPSHOT: "비교 범위에 이전 날짜의 설비 기록이 없습니다.",
    ChangeReason.MISSING_AFTER_SNAPSHOT: "비교 범위에 이후 날짜의 설비 기록이 없습니다.",
    ChangeReason.MISSING_BEFORE_POSITION: "이전 날짜에 같은 PM 위치 또는 계열의 기록이 없습니다.",
    ChangeReason.MISSING_AFTER_POSITION: "이후 날짜에 같은 PM 위치 또는 계열의 기록이 없습니다.",
    ChangeReason.OUTSIDE_BEFORE_SCOPE: "이전 날짜의 해당 PM 또는 계열은 조회 범위 밖입니다.",
    ChangeReason.OUTSIDE_AFTER_SCOPE: "이후 날짜의 해당 PM 또는 계열은 조회 범위 밖입니다.",
    ChangeReason.TRACKING_CHANGED: "두 날짜의 PM 구분 방식이 달라 같은 적용 위치로 연결할 수 없습니다.",
    ChangeReason.FAMILY_CHANGED: "같은 PM 위치의 버전 계열이 달라 버전 변경을 비교할 수 없습니다.",
    ChangeReason.RELEASE_UNCONFIRMED: "버전 원문은 달라졌지만 대응 릴리즈가 확인되지 않아 최신·이전 방향은 미확인입니다.",
    ChangeReason.ORDER_UNKNOWN: "버전은 달라졌지만 확인된 릴리즈 순서가 없어 최신·이전 방향은 미확인입니다.",
    ChangeReason.ORDER_CONFLICT: "릴리즈 순서가 중복되어 최신·이전 방향은 미확인입니다.",
}


@dataclass(frozen=True)
class VersionObservation:
    """비교 한쪽의 실제 현황·SW·PM. 날짜별 ID와 원문·원본 출처를 그대로 보존한다."""

    snapshot: StoredSnapshot
    software: StoredSoftware
    pm: StoredPm | None = None


@dataclass(frozen=True)
class VersionChange:
    """두 날짜에 기록이 모두 있는 같은 위치·계열만 변경 여부를 판단한다.

    미등록 원문 차이도 CHANGED이며 방향은 UNKNOWN이다. 관측 날짜는 실제 패치 시각이 아니다.
    """

    equipment_id: int
    equipment_code: str
    pm_name: str | None
    before_date: date
    after_date: date
    before: VersionObservation | None
    after: VersionObservation | None
    status: ChangeStatus
    direction: VersionDirection = VersionDirection.UNKNOWN
    reason: ChangeReason | None = None

    @property
    def message(self) -> str | None:
        return REASON_MESSAGES[self.reason] if self.reason else None

    @property
    def raw_changed(self) -> bool | None:
        """원문 차이는 같은 버전으로 확인된 표기 차이도 포함한다. 한쪽 부재는 None이다."""
        if self.before is None or self.after is None:
            return None
        return self.before.software.version_raw != self.after.software.version_raw


@dataclass(frozen=True)
class HistoryComparison:
    """비교 날짜·당시 현황·전체 비교 결과. 미관측을 신규 설비나 패치 실적으로 단정하지 않는다."""

    before: HistoryFrame
    after: HistoryFrame
    changes: tuple[VersionChange, ...]

    @property
    def changed(self) -> tuple[VersionChange, ...]:
        return tuple(change for change in self.changes if change.status is ChangeStatus.CHANGED)

    @property
    def changed_equipment_ids(self) -> tuple[int, ...]:
        """여러 PM이 바뀌어도 같은 실제 설비 ID는 한 번만 반환한다."""
        return tuple(sorted({change.equipment_id for change in self.changed}))
