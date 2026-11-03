"""조회 범위와 그 범위의 모델·계열별 목표 후보를 같은 저장 시점에 제공한다."""

from collections.abc import Mapping
from dataclasses import dataclass

from setron.features.release.refresh_schemas import RefreshContext
from setron.features.storage.inputs import require
from setron.features.targets.schemas import TargetCatalog
from setron.features.targets.service import TargetService

from .schemas import QueryOptions, QueryScope
from .service import QueryOptionsService


@dataclass(frozen=True)
class QueryCatalogResult:
    """조회 목록과 해당 목록의 N 후보·선택 유효성.

    선택 N은 요청에만 존재한다. 범위 밖의 다른 모델·계열에 대한 호출자의 선택값은
    변경하지 않으며, 표시 목록에는 이번 범위에서 관측된 실제 조합만 포함한다.
    전체 재판정용 목록에는 범위 밖의 기존 선택도 보존한다.
    """

    options: QueryOptions
    target_catalog: TargetCatalog
    refresh_catalog: TargetCatalog | None = None

    @property
    def refresh_context(self) -> RefreshContext:
        """같은 자료에서 확인한 N과 적용 범위를 BE-010 재판정에 전달한다.

        BE-010은 릴리즈를 변경한 뒤 같은 저장 트랜잭션에서 현재 근거를 다시 읽는다.
        화면의 후보 목록도 변경 뒤 다시 읽어 새 유효성과 재선택 안내를 표시한다.
        """
        catalog = self.target_catalog if self.refresh_catalog is None else self.refresh_catalog
        return RefreshContext(targets=catalog.targets,
                              applicability=catalog.applicability,
                              use_saved_applicability=True)


class QueryCatalogService:
    """화면·AI가 공유하는 BE-011 진입점. DB에 조회 조건이나 N을 저장하지 않는다."""

    def __init__(self, storage):
        self.storage = storage
        self.options = QueryOptionsService(storage)
        self.targets = TargetService(storage)

    def read(self, scope: QueryScope | None = None,
             targets: Mapping[tuple[int, int], int | None] | None = None) -> QueryCatalogResult:
        """정확한 날짜·복수 범위의 목록과 확인된 최신 N 초기값을 읽는다.

        ``targets``가 있으면 기존 선택을 전달해 유효성을 다시 검사한다. 잘못된
        선택도 다른 N으로 대체하지 않는다. 날짜를 고르지 않으면 날짜 목록만 읽는다.
        """
        with self.storage.transaction() as tx:
            return self.read_in(tx, scope, targets)

    def read_in(self, tx, scope: QueryScope | None = None,
                targets: Mapping[tuple[int, int], int | None] | None = None) -> QueryCatalogResult:
        """표시 범위를 제한하면서 전체 요청의 선택 N은 같은 시점에 보존한다."""
        require(targets is None or isinstance(targets, Mapping), "선택 N은 모델·계열별 mapping이어야 합니다.")
        chosen = None if targets is None else dict(targets)
        options = self.options.read_in(tx, scope)
        pairs = tuple(dict.fromkeys((*options.model_family_pairs, *(chosen or ()))))
        full_catalog = self.targets.read_in(tx, pairs, chosen)
        visible_pairs = set(options.model_family_pairs)
        catalog = TargetCatalog(tuple(pair for pair in full_catalog.pairs if pair.key in visible_pairs))
        return QueryCatalogResult(options, catalog, full_catalog)
