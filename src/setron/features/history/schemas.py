"""기준일별 실제 관측과 빈 날짜를 구분하는 이력 조회 결과 (BE-013)."""

from dataclasses import dataclass
from datetime import date

from setron.features.equipment.schemas import StoredSnapshot
from setron.features.query_options.schemas import QueryMatch


@dataclass(frozen=True)
class HistoryFrame:
    """한 날짜의 조회 결과. 미관측 날짜는 matches가 빈 튜플이며 이전 값으로 채우지 않는다."""

    base_date: date
    matches: tuple[QueryMatch, ...] = ()

    @property
    def snapshots(self) -> tuple[StoredSnapshot, ...]:
        """당시 소속·PM·원문·출처를 보존한 전체 관측을 반환한다."""
        return tuple(match.snapshot for match in self.matches)

    @property
    def has_data(self) -> bool:
        """지정한 날짜와 범위에 실제 관측이 있는지 반환한다."""
        return bool(self.matches)


@dataclass(frozen=True)
class ReplayResult:
    """요청한 날짜를 시간순으로 유지한 리플레이 자료. 빈 프레임도 보존한다."""

    frames: tuple[HistoryFrame, ...]

    @property
    def can_replay(self) -> bool:
        """서로 다른 두 날짜 이상에서 실제 조회 결과가 있어야 리플레이할 수 있다."""
        return sum(frame.has_data for frame in self.frames) >= 2

    @property
    def message(self) -> str | None:
        """실제 관측 날짜가 부족하면 리플레이할 수 없는 이유를 안내한다."""
        return None if self.can_replay else "선택한 범위에 실제 관측이 있는 날짜가 2개 이상 필요합니다."
