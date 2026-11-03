"""화면과 AI 조회가 함께 사용할 날짜·복수 범위 선택 계약."""

from dataclasses import dataclass
from datetime import date

from setron.features.equipment.schemas import StoredSnapshot
from setron.features.storage.inputs import identifier, require, required_text


def _validate_line(value):
    """저장 계약과 동일하게 빈 라인도 원문 그대로 조회·선택할 수 있게 한다."""
    require(value is None or isinstance(value, str) and len(value) <= 50,
            "라인은 50자 이내의 문자열 또는 None이어야 합니다.")


@dataclass(frozen=True)
class LineOption:
    """사업장별 라인 선택지. None·빈 문자열·공백을 포함한 저장 원문을 보존한다."""

    site: str
    line: str | None

    def __post_init__(self):
        required_text(self.site, "라인의 사업장", 50)
        _validate_line(self.line)


@dataclass(frozen=True)
class QueryScope:
    """항목 안에서는 OR, 항목 사이에서는 AND로 조회한다.

    빈 튜플은 해당 항목의 전체 범위다. ``lines=(None,)``은 라인 미확인만 뜻한다.
    ``site_lines``는 사업장·라인 조합을 정확히 선택한다. ``lines``와 함께 지정하면
    두 조건을 모두 만족해야 한다.
    ``base_date=None``은 날짜 목록만 요청하며 임의의 최신 날짜를 선택하지 않는다.
    모델과 계열을 함께 지정하면 같은 SW/PM의 실제 연결이 두 조건을 모두 만족해야 한다.
    """

    base_date: date | None = None
    sites: tuple[str, ...] = ()
    lines: tuple[str | None, ...] = ()
    categories: tuple[str, ...] = ()
    model_ids: tuple[int, ...] = ()
    family_ids: tuple[int, ...] = ()
    site_lines: tuple[LineOption, ...] = ()

    def __post_init__(self):
        require(self.base_date is None or type(self.base_date) is date, "조회 기준일은 날짜여야 합니다.")
        for field in ("sites", "lines", "categories", "model_ids", "family_ids"):
            values = getattr(self, field)
            require(isinstance(values, (tuple, list)), f"{field}: 복수 선택 목록이 필요합니다.")
            for value in values:
                if field in {"model_ids", "family_ids"}:
                    identifier(value, field)
                elif field == "lines":
                    _validate_line(value)
                else:
                    required_text(value, field, 50)
                    if field == "categories":
                        require(value in {"POLY", "OXIDE", "KCTC", "HPA"}, "등록되지 않은 설비군입니다.")
            canonical = sorted(set(values), key=lambda value: (value is not None, value))
            object.__setattr__(self, field, tuple(canonical))
        require(isinstance(self.site_lines, (tuple, list)), "site_lines: 사업장·라인 선택 목록이 필요합니다.")
        require(all(isinstance(value, LineOption) for value in self.site_lines),
                "site_lines: LineOption 형식의 사업장·라인 조합이 필요합니다.")
        object.__setattr__(self, "site_lines", tuple(sorted(set(self.site_lines),
            key=lambda value: (value.site, value.line is not None, value.line))))


@dataclass(frozen=True)
class ModelOption:
    model_id: int
    name: str
    category: str
    parent_model_id: int | None
    analysis_enabled: bool


@dataclass(frozen=True)
class FamilyOption:
    family_id: int
    name: str
    ui_type: str | None
    owner_model_id: int


@dataclass(frozen=True)
class QueryMatch:
    """일치한 원본 현황과 조회에 포함된 모델·계열 쌍.

    ``snapshot``은 출처 보존을 위해 전체 관측을 반환한다. 혼합형의 SW를 집계할 때는
    ``model_family_pairs``에 있는 연결만 사용해야 한다.
    """

    snapshot: StoredSnapshot
    model_family_pairs: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class QueryOptions:
    """정확한 선택 날짜·범위의 목록. 기록이 없으면 날짜 외의 목록은 모두 비어 있다."""

    scope: QueryScope
    dates: tuple[date, ...] = ()
    sites: tuple[str, ...] = ()
    lines: tuple[LineOption, ...] = ()
    categories: tuple[str, ...] = ()
    models: tuple[ModelOption, ...] = ()
    families: tuple[FamilyOption, ...] = ()
    model_family_pairs: tuple[tuple[int, int], ...] = ()
    matches: tuple[QueryMatch, ...] = ()

    @property
    def snapshot_ids(self) -> tuple[int, ...]:
        return tuple(match.snapshot.snapshot_id for match in self.matches)
