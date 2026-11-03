"""정확한 날짜의 저장 관측을 조회하고 같은 트랜잭션에서 리플레이 자료를 구성한다."""

from collections.abc import Iterable
from dataclasses import replace
from datetime import date

from setron.features.query_options.schemas import QueryScope
from setron.features.query_options.service import QueryOptionsService
from setron.features.storage.inputs import require
from setron.features.version.schemas import ReleaseVersion

from .comparison import compare_frames
from .comparison_schemas import HistoryComparison
from .release_catalog import HistoryReleases
from .schemas import HistoryFrame, ReplayResult


class HistoryService:
    """선택한 날짜와 당시 범위만 읽는다. 최신 날짜 선택이나 빈 날짜 보충은 하지 않는다."""

    def __init__(self, storage):
        self.storage = storage
        self.query_options = QueryOptionsService(storage)

    def read(self, scope: QueryScope) -> HistoryFrame:
        """명시한 기준일의 원본 관측을 조회한다. 자료가 없으면 빈 프레임을 반환한다."""
        _dated_scope(scope)
        with self.storage.transaction() as tx:
            return self.read_in(tx, scope)

    def read_in(self, tx, scope: QueryScope) -> HistoryFrame:
        """호출자의 트랜잭션 안에서 정확한 날짜와 일치한 모델·계열 쌍을 함께 읽는다."""
        _dated_scope(scope)
        options = self.query_options.read_in(tx, scope)
        return HistoryFrame(scope.base_date, options.matches)

    def replay(self, dates: Iterable[date], scope: QueryScope | None = None) -> ReplayResult:
        """중복 날짜를 제거해 시간순으로 읽으며, 요청한 빈 날짜를 그대로 남긴다."""
        days, scope = _replay_request(dates, scope)
        with self.storage.transaction() as tx:
            return self.replay_in(tx, days, scope)

    def replay_in(self, tx, dates: Iterable[date], scope: QueryScope | None = None) -> ReplayResult:
        """모든 프레임을 호출자의 트랜잭션으로 읽는다. scope의 날짜는 각 요청 날짜로 대체한다."""
        days, scope = _replay_request(dates, scope)
        return ReplayResult(tuple(self.read_in(tx, replace(scope, base_date=day)) for day in days))

    def compare(self, before_date: date, after_date: date, scope: QueryScope | None = None) -> HistoryComparison:
        """두 날짜의 같은 설비·위치를 비교한다. 한쪽만 관측된 대상은 변경으로 세지 않는다."""
        scope = _comparison_request(before_date, after_date, scope)
        with self.storage.transaction() as tx:
            return self.compare_in(tx, before_date, after_date, scope)

    def compare_in(self, tx, before_date: date, after_date: date, scope: QueryScope | None = None) -> HistoryComparison:
        """두 날짜의 비교 대상 계열만 같은 트랜잭션으로 읽는다. 표기가 모호하면 방향 판정을 보류한다."""
        scope = _comparison_request(before_date, after_date, scope)
        before = self.read_in(tx, replace(scope, base_date=before_date))
        after = self.read_in(tx, replace(scope, base_date=after_date))
        family_ids = {family_id for frame in (before, after) for match in frame.matches
                      for _, family_id in match.model_family_pairs}
        families = {}
        for family_id in sorted(family_ids):
            releases = tuple(ReleaseVersion(
                row["release_version_id"], row["family_id"], row["version_raw"],
                row["version_order"], row["released_at"],
            ) for row in self.query_options.repository.rows(tx, "release_version", family_id=family_id))
            families[family_id] = HistoryReleases(family_id, releases)
        return compare_frames(before, after, families)


def _dated_scope(scope: QueryScope) -> None:
    """날짜가 생략된 조회를 거부해 현재 날짜나 최신 관측으로 추정하지 않는다."""
    require(isinstance(scope, QueryScope), "이력 조회 범위 형식이 올바르지 않습니다.")
    require(type(scope.base_date) is date, "이력 조회에는 기준일을 명시해야 합니다.")


def _comparison_request(before_date, after_date, scope):
    require(type(before_date) is date and type(after_date) is date, "비교 기준일은 날짜여야 합니다.")
    require(before_date < after_date, "이전 날짜는 이후 날짜보다 앞서야 합니다.")
    scope = QueryScope() if scope is None else scope
    require(isinstance(scope, QueryScope), "이력 조회 범위 형식이 올바르지 않습니다.")
    return scope


def _replay_request(dates: Iterable[date], scope: QueryScope | None) -> tuple[tuple[date, ...], QueryScope]:
    """날짜 목록과 조회 범위를 복사·검증한다. 단일 날짜도 자료 부족 결과로 조회할 수 있다."""
    require(isinstance(dates, Iterable) and not isinstance(dates, (str, bytes)), "리플레이 날짜 목록이 필요합니다.")
    days = tuple(dates)
    require(bool(days), "리플레이 날짜를 한 개 이상 지정해야 합니다.")
    require(all(type(day) is date for day in days), "리플레이 기준일은 날짜여야 합니다.")
    scope = QueryScope() if scope is None else scope
    require(isinstance(scope, QueryScope), "이력 조회 범위 형식이 올바르지 않습니다.")
    return tuple(sorted(set(days))), scope
