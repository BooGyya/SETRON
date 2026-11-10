"""공통 조회 조건 계약. 선택(초안)과 적용 스냅샷을 분리하며 DB를 호출하지 않는다."""

from dataclasses import dataclass, replace
from datetime import date
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
class VersionFilter:
    """조회할 버전 원문들의 OR 조건. 목표 N·릴리즈 대응 표기는 변경하지 않는다."""

    versions: tuple[str, ...] = ()
    include_missing: bool = False

    def __post_init__(self):
        if isinstance(self.versions, str):
            raise ValueError("조회 버전은 문자열 목록이어야 합니다.")
        values = tuple(self.versions)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("조회 버전 원문이 올바르지 않습니다.")
        if type(self.include_missing) is not bool:
            raise ValueError("버전 미등록 포함 여부가 올바르지 않습니다.")
        object.__setattr__(self, "versions", tuple(sorted(set(values))))
        if not values and not self.include_missing:
            raise ValueError("조회할 버전을 하나 이상 선택해 주세요.")


def version_filter_caption(value: VersionFilter | None) -> str:
    return "조회 버전 · 전체" if value is None else f"조회 버전 · {len(value.versions) + value.include_missing}개 선택"


@dataclass(frozen=True)
class QuerySelection:
    site_key: str | None = None
    fab_key: SelectionKey = None
    line_key: SelectionKey = None
    group_key: str | None = None  # UI 설비군 키이며 version_family.family_id가 아님
    as_of: date | None = None  # 기준일. None은 최신 관측(오늘 기준), 날짜는 그날 이전 최신 관측
    versions: VersionFilter | None = None  # None: 모든 버전. 빈 선택을 전체로 취급하지 않는다.
    item_family_id: int | None = None  # Item 답변에서 이동한 명시적 SW 계열 범위. 전체 조건으로 초기화하면 해제.


@dataclass(frozen=True)
class QueryCatalog:
    sites: tuple[SiteOption, ...] = ()
    fabs: tuple[FabOption, ...] = ()
    lines: tuple[LineOption, ...] = ()
    groups: tuple[GroupOption, ...] = ()
    source_label: str = "등록된 조회 선택지"

    def is_view_of(self, catalog: "QueryCatalog") -> bool:
        """개인 설정으로 Line·설비군을 숨겨도 남은 선택지는 같은 매핑이어야 한다."""
        return (
            self.sites == catalog.sites
            and self.fabs == catalog.fabs
            and self.source_label == catalog.source_label
            and set(self.lines).issubset(catalog.lines)
            and set(self.groups).issubset(catalog.groups)
        )

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
class TargetN:
    model_id: int
    family_id: int
    release_id: int | None  # None은 명시적 목표 미지정, 판단 불가

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in (self.model_id, self.family_id)):
            raise ValueError("모델·계열 ID가 올바르지 않습니다.")
        if self.release_id is not None and (type(self.release_id) is not int or self.release_id <= 0):
            raise ValueError("목표 릴리즈 ID가 올바르지 않습니다.")


@dataclass(frozen=True)
class AppliedQuery:
    selection: QuerySelection
    revision: int
    catalog_revision: int
    description: str
    targets: tuple[TargetN, ...] | None = None  # None은 기존 공급자 기본 N 사용


class QueryState:
    def __init__(self, catalog: QueryCatalog):
        if not isinstance(catalog, QueryCatalog):
            raise ValueError("QueryCatalog가 필요합니다.")
        self.catalog = catalog
        self.draft = QuerySelection()
        self.applied: AppliedQuery | None = None
        self.target_draft: tuple[TargetN, ...] | None = None
        self._revision = 0
        self._catalog_revision = 0

    @property
    def applied_is_current(self) -> bool:
        return self.applied is not None and self.applied.catalog_revision == self._catalog_revision

    @property
    def has_pending_changes(self) -> bool:
        return (not self.applied_is_current or self.draft != self.applied.selection
                or self.target_draft != self.applied.targets)

    @property
    def catalog_revision(self) -> int:
        return self._catalog_revision

    def set_target_draft(self, targets: tuple[TargetN, ...] | None) -> None:
        if targets is None:
            self.target_draft = None
            return
        values = tuple(targets)
        if any(not isinstance(value, TargetN) for value in values):
            raise ValueError("목표 N 계약이 올바르지 않습니다.")
        if len({(t.model_id, t.family_id) for t in values}) != len(values):
            raise ValueError("모델·계열 목표가 중복되었습니다.")
        self.target_draft = tuple(sorted(values, key=lambda t: (t.model_id, t.family_id)))

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
        if selection.versions is not None and not isinstance(selection.versions, VersionFilter):
            raise ValueError("조회 버전 조건이 올바르지 않습니다.")
        if selection.item_family_id is not None and (type(selection.item_family_id) is not int or selection.item_family_id <= 0):
            raise ValueError("Item 조회 계열이 올바르지 않습니다.")
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

    def set_as_of(self, value: date | None) -> None:
        """기준일만 바꾼다. 오늘 이후 날짜는 최신 관측(None)과 같으므로 None으로 맞춘다."""
        if value is not None and value >= date.today():
            value = None
        self.set_draft(replace(self.draft, as_of=value))

    def select(self, field: str, value: SelectionKey) -> None:
        if field not in QuerySelection.__dataclass_fields__:
            raise ValueError(f"알 수 없는 조회 항목입니다: {field}")
        if getattr(self.draft, field) == value and not (field == "group_key" and self.draft.item_family_id is not None):
            return
        changes = {field: value}
        if field == "site_key":
            changes.update(fab_key=None, line_key=None)
        elif field == "fab_key":
            changes.update(line_key=None)
        elif field == "group_key":
            changes.update(item_family_id=None)
        self.set_draft(replace(self.draft, **changes))

    def set_draft(self, selection: QuerySelection) -> None:
        if selection.as_of is not None and type(selection.as_of) is not date:
            raise ValueError("기준일은 날짜여야 합니다.")
        self.validate(selection)
        self.draft = selection

    def reset_draft(self) -> None:
        """선택만 전체로 되돌린다. 이전 적용 조건은 조회 적용 전까지 유지한다."""
        self.draft = QuerySelection()

    def apply(self) -> AppliedQuery:
        self.validate(self.draft)
        self._revision += 1
        self.applied = AppliedQuery(self.draft, self._revision, self._catalog_revision,
                                    self.describe(self.draft), self.target_draft)
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
        captions.append("기준일 최신" if selection.as_of is None else f"기준일 {selection.as_of.isoformat()}")
        if selection.versions is not None:
            versions = [repr(value) for value in selection.versions.versions]
            if selection.versions.include_missing:
                versions.append("버전 미등록")
            captions.append("조회 버전 " + " | ".join(versions))
        if selection.item_family_id is not None:
            captions.append(f"Item 분석 계열 ID {selection.item_family_id}")
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
