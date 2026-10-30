"""공통 조회 조건 계약. 선택(초안)과 적용 스냅샷을 분리하며 DB를 호출하지 않는다."""

from dataclasses import dataclass, replace
from enum import Enum


class SpecialSelection(Enum):
    UNASSIGNED = "unassigned"


UNASSIGNED = SpecialSelection.UNASSIGNED
SelectionKey = str | SpecialSelection | None  # None: 전체, UNASSIGNED: 미지정


@dataclass(frozen=True)
class SiteOption:
    key: str
    label: str


@dataclass(frozen=True)
class FabOption:
    key: str
    label: str
    site_key: str


@dataclass(frozen=True)
class LineOption:
    key: str
    label: str
    site_key: str
    fab_key: str | None  # Fab 미지정인 Line도 독립적으로 등록 가능


@dataclass(frozen=True)
class GroupOption:
    key: str
    label: str
    category: str = ""


@dataclass(frozen=True)
class QuerySelection:
    site_key: str | None = None
    fab_key: SelectionKey = None
    line_key: SelectionKey = None
    group_key: str | None = None  # UI 설비군 키이며 version_family.family_id가 아님


@dataclass(frozen=True)
class QueryCatalog:
    sites: tuple[SiteOption, ...] = ()
    fabs: tuple[FabOption, ...] = ()
    lines: tuple[LineOption, ...] = ()
    groups: tuple[GroupOption, ...] = ()
    source_label: str = "등록된 조회 선택지"

    def __post_init__(self):
        types = (SiteOption, FabOption, LineOption, GroupOption)
        for field, option_type in zip(("sites", "fabs", "lines", "groups"), types):
            options = tuple(getattr(self, field))
            object.__setattr__(self, field, options)
            if any(not isinstance(option, option_type) for option in options):
                raise ValueError(f"{field}: 올바른 선택지 형식이 아닙니다.")
            if any(not isinstance(o.key, str) or not o.key.strip()
                   or not isinstance(o.label, str) or not o.label.strip() for o in options):
                raise ValueError(f"{field}: 키와 이름이 필요합니다.")
            if len({o.key for o in options}) != len(options):
                raise ValueError(f"{field}: 중복 키입니다.")
        sites = {o.key for o in self.sites}
        fabs = {o.key: o for o in self.fabs}
        for fab in self.fabs:
            if fab.site_key not in sites:
                raise ValueError("Fab의 Site가 등록되지 않았습니다.")
        for line in self.lines:
            if line.site_key not in sites:
                raise ValueError("Line의 Site가 등록되지 않았습니다.")
            if line.fab_key is not None and (
                line.fab_key not in fabs or fabs[line.fab_key].site_key != line.site_key
            ):
                raise ValueError("Line의 Fab과 Site 관계가 올바르지 않습니다.")


@dataclass(frozen=True)
class AppliedQuery:
    selection: QuerySelection
    revision: int
    catalog_revision: int
    description: str


class QueryState:
    def __init__(self, catalog: QueryCatalog):
        if not isinstance(catalog, QueryCatalog):
            raise ValueError("QueryCatalog가 필요합니다.")
        self.catalog = catalog
        self.draft = QuerySelection()
        self.applied: AppliedQuery | None = None
        self._revision = 0
        self._catalog_revision = 0

    @property
    def applied_is_current(self) -> bool:
        return self.applied is not None and self.applied.catalog_revision == self._catalog_revision

    @property
    def has_pending_changes(self) -> bool:
        return not self.applied_is_current or self.draft != self.applied.selection

    def available_fabs(self, selection: QuerySelection | None = None) -> tuple[FabOption, ...]:
        selection = selection or self.draft
        return tuple(o for o in self.catalog.fabs
                     if selection.site_key is None or o.site_key == selection.site_key)

    def available_lines(self, selection: QuerySelection | None = None) -> tuple[LineOption, ...]:
        selection = selection or self.draft
        return tuple(o for o in self.catalog.lines
                     if (selection.site_key is None or o.site_key == selection.site_key)
                     and (selection.fab_key is None
                          or (selection.fab_key is UNASSIGNED and o.fab_key is None)
                          or o.fab_key == selection.fab_key))

    def validate(self, selection: QuerySelection) -> None:
        if not isinstance(selection, QuerySelection):
            raise ValueError("QuerySelection이 필요합니다.")
        for value, options, field in (
            (selection.site_key, self.catalog.sites, "Site"),
            (selection.group_key, self.catalog.groups, "설비군"),
        ):
            if value is not None and (not isinstance(value, str) or value not in {o.key for o in options}):
                raise ValueError(f"등록되지 않은 {field}입니다: {value}")
        for value, options, field in (
            (selection.fab_key, self.available_fabs(selection), "Fab"),
            (selection.line_key, self.available_lines(selection), "Line"),
        ):
            if value is not None and value is not UNASSIGNED and (
                not isinstance(value, str) or value not in {o.key for o in options}
            ):
                raise ValueError(f"등록되지 않았거나 상위 조건과 다른 {field}입니다: {value}")

    def select(self, field: str, value: SelectionKey) -> None:
        if field not in QuerySelection.__dataclass_fields__:
            raise ValueError(f"알 수 없는 조회 항목입니다: {field}")
        if getattr(self.draft, field) == value:
            return
        changes = {field: value}
        if field == "site_key":
            changes.update(fab_key=None, line_key=None)
        elif field == "fab_key":
            changes.update(line_key=None)
        self.set_draft(replace(self.draft, **changes))

    def set_draft(self, selection: QuerySelection) -> None:
        self.validate(selection)
        self.draft = selection

    def reset_draft(self) -> None:
        """선택만 전체로 되돌린다. 이전 적용 조건은 조회 적용 전까지 유지한다."""
        self.draft = QuerySelection()

    def apply(self) -> AppliedQuery:
        self.validate(self.draft)
        self._revision += 1
        self.applied = AppliedQuery(self.draft, self._revision, self._catalog_revision,
                                    self.describe(self.draft))
        return self.applied

    def is_current(self, query: AppliedQuery) -> bool:
        """향후 비동기 화면이 이전 적용 요청의 응답을 무시할 때 사용한다."""
        return query is self.applied and self.applied_is_current

    def replace_catalog(self, catalog: QueryCatalog) -> None:
        if not isinstance(catalog, QueryCatalog):
            raise ValueError("QueryCatalog가 필요합니다.")
        self.catalog = catalog
        self._catalog_revision += 1
        draft = self.draft
        if draft.site_key is not None and draft.site_key not in {o.key for o in catalog.sites}:
            draft = replace(draft, site_key=None, fab_key=None, line_key=None)
        if isinstance(draft.fab_key, str) and draft.fab_key not in {o.key for o in self.available_fabs(draft)}:
            draft = replace(draft, fab_key=None, line_key=None)
        if isinstance(draft.line_key, str) and draft.line_key not in {o.key for o in self.available_lines(draft)}:
            draft = replace(draft, line_key=None)
        if draft.group_key is not None and draft.group_key not in {o.key for o in catalog.groups}:
            draft = replace(draft, group_key=None)
        self.set_draft(draft)
        # 적용 스냅샷은 보존한다. 정규화된 초안을 자동으로 적용하면 조회 범위가 달라진다.

    def describe(self, selection: QuerySelection) -> str:
        captions = []
        for name, value, options in (
            ("Site", selection.site_key, self.catalog.sites),
            ("Fab", selection.fab_key, self.catalog.fabs),
            ("Line", selection.line_key, self.catalog.lines),
            ("설비군", selection.group_key, self.catalog.groups),
        ):
            title = "전체" if value is None else "미지정" if value is UNASSIGNED else next(
                o.label for o in options if o.key == value)
            captions.append(f"{name} {title}")
        return " · ".join(captions)


def preview_catalog() -> QueryCatalog:
    """VER3의 표시 선택지뿐이다. 실데이터·원본 구역의 Fab/Line 매핑을 만들지 않는다."""
    return QueryCatalog(
        sites=(SiteOption("pyeongtaek", "평택"), SiteOption("hwaseong", "화성"), SiteOption("giheung", "기흥")),
        groups=tuple(GroupOption(key, name, category) for key, name, category in (
            ("c3", "C3", "POLY"), ("c4", "C4", "POLY"),
            ("o2", "O2", "OXIDE"), ("o3", "O3", "OXIDE"), ("o5", "O5", "OXIDE"),
            ("inline-ox", "Inline Ox", "KCTC"), ("inline-o2", "Inline O2", "KCTC"),
            ("cluster-ox", "Cluster Ox", "KCTC"), ("cluster-o2", "Cluster O2", "KCTC"),
        )),
        source_label="VER3 선택지 미리보기 · DB 미연결 · Fab/Line 매핑 미등록",
    )
