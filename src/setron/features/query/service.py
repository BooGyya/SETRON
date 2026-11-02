"""질문에서 찾은 조회 조건을 검사하고 해당 자료를 고른다 (명세 BE-018, FUN-041·042·062).

AI에는 수가 적은 선택지(사업장·라인·분류·모델·계열·저장된 기준일)를 함께 주고 그 안에서 고르게 한다.
프로그램은 AI가 고른 값이 정말 선택지에 있는지만 검사해 지어낸 값을 걸러 낸다.
버전과 Item은 수가 많아 AI가 질문의 표기를 그대로 뽑고, 프로그램이 등록 자료에서 찾는다.

정한 규칙
- 이름 대조는 앞뒤 공백과 대소문자만 무시한다. 그 밖의 표기 차이는 자동으로 잇지 않는다.
- 버전은 같은 글자의 등록 릴리즈만 확정한다. 표기만 다른 후보는 BE-003 후보 찾기로 보여 주고 확인받는다.
- Item은 검색 결과가 하나일 때만 확정한다.
- 기준일이 없으면 저장된 가장 최근 날짜를 쓰고, 그 날짜를 조건에 적어 화면에도 같은 날짜를 넘긴다.
- 확인할 조건이 남으면 자료를 조회하지 않는다. 조회는 읽기만 하며 DB 값을 바꾸지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aggregation.service import Targets, summarize
from setron.features.ai.schemas import QueryConditions
from setron.features.item.service import search_items
from setron.features.query.schemas import (
    ConditionIssue,
    IssueKind,
    QueryAnswer,
    QueryCatalog,
    ResolvedQuery,
)
from setron.features.version.service import FamilyReleases


def resolve_conditions(conditions: QueryConditions, catalog: QueryCatalog) -> ResolvedQuery:
    """AI가 찾은 조건을 실제 선택지와 대조해 확정한다. 확정하지 못한 조건은 ``issues``에 남긴다."""
    issues = [ConditionIssue("question", text, IssueKind.UNRESOLVED) for text in conditions.unresolved]

    def pick(field: str, values: Iterable[str], options: Iterable[tuple[str, object]]) -> tuple:
        """선택지 (이름, 값) 중 이름이 같은 것의 값을 고른다. 없으면 선택지 이름을 후보로 남긴다."""
        options = tuple(options)
        by_name = {_key(name): value for name, value in options}
        picked = []
        for text in values:
            if _key(text) in by_name:
                picked.append(by_name[_key(text)])
            else:
                issues.append(ConditionIssue(field, text, IssueKind.NOT_IN_CHOICES, tuple(n for n, _ in options)))
        return tuple(dict.fromkeys(picked))

    sites = pick("sites", conditions.sites, ((s, s) for s in catalog.sites))
    lines = pick("lines", conditions.lines, ((s, s) for s in catalog.lines))
    categories = pick("groups", conditions.groups, ((c, c) for c in catalog.categories))
    group_ids = pick("models", conditions.models,
                     ((m.name, m.group_id) for m in catalog.models if m.category != "ETC"))
    family_ids = pick("families", conditions.families, ((f.name, f.family_id) for f in catalog.families))

    base_date = _base_date(conditions.base_date, catalog, issues)
    release_ids = _releases(conditions.versions, family_ids, catalog, issues)
    item_ids = _items(conditions.items, catalog, issues)

    return ResolvedQuery(
        base_date=base_date,
        sites=sites,
        lines=lines,
        categories=categories,
        group_ids=group_ids,
        family_ids=family_ids,
        release_ids=release_ids,
        item_ids=item_ids,
        target_view=None if issues else conditions.target_view,
        issues=tuple(issues),
    )


def select_records(
    records: Iterable[EquipmentRecord],
    query: ResolvedQuery,
    catalog: QueryCatalog,
) -> tuple[EquipmentRecord, ...]:
    """확정한 조건에 맞는 설비만 고른다.

    모델·분류·계열·버전 조건이 있으면 조건에 맞는 PM만 남기고, 맞는 PM이 없는 설비는 뺀다.
    그래야 'O2 현황'을 물었을 때 혼합 설비의 O3 부분이 집계에 섞이지 않는다.
    """
    categories = {m.group_id: m.category for m in catalog.models}
    selected = []
    for record in records:
        if query.base_date and record.base_date != query.base_date:
            continue
        if query.sites and record.site not in query.sites:
            continue
        if query.lines and record.line not in query.lines:
            continue
        if not query.filters_pms:
            selected.append(record)
            continue
        pms = tuple(pm for pm in record.pms if _pm_matches(pm, query, categories, catalog.releases))
        if pms:
            selected.append(_with_pms(record, pms))
    return tuple(selected)


def answer_query(
    query: ResolvedQuery,
    records: Iterable[EquipmentRecord],
    catalog: QueryCatalog,
    targets: Targets,
) -> QueryAnswer:
    """확정한 조건으로 자료를 골라 공통 집계(BE-012)를 만든다. 확인할 조건이 남으면 조회하지 않는다."""
    if not query.ready:
        return QueryAnswer(query)
    return QueryAnswer(query, summarize(select_records(records, query, catalog), catalog.releases, targets))


# --- 내부 ----------------------------------------------------------------------

def _key(text: str) -> str:
    return text.strip().casefold()


def _base_date(text: str | None, catalog: QueryCatalog, issues: list[ConditionIssue]) -> date | None:
    stored = sorted(catalog.dates)
    if not text:
        return stored[-1] if stored else None
    try:
        value = date.fromisoformat(text.strip())
    except ValueError:
        issues.append(ConditionIssue("base_date", text, IssueKind.INVALID_DATE))
        return None
    if value not in catalog.dates:
        issues.append(ConditionIssue("base_date", text, IssueKind.DATE_NOT_STORED,
                                     tuple(d.isoformat() for d in stored)))
        return None
    return value


def _releases(
    versions: Iterable[str],
    family_ids: tuple[int, ...],
    catalog: QueryCatalog,
    issues: list[ConditionIssue],
) -> tuple[int, ...]:
    """질문의 버전을 등록 릴리즈에서 찾는다. 계열 조건이 있으면 그 계열 안에서만 찾는다."""
    families = [catalog.releases[f] for f in family_ids if f in catalog.releases] if family_ids \
        else list(catalog.releases.values())
    names = {f.family_id: f.name for f in catalog.families}
    found = []
    for version in versions:
        matches = [(family, release) for family in families if (release := family.match(version))]
        if len(matches) == 1:
            found.append(matches[0][1].release_version_id)
        elif matches:
            candidates = tuple(f"{names.get(r.family_id, r.family_id)} {r.version_raw}" for _, r in matches)
            issues.append(ConditionIssue("versions", version, IssueKind.AMBIGUOUS, candidates))
        else:
            candidates = tuple(c.release.version_raw for family in families for c in family.candidates(version))
            issues.append(ConditionIssue("versions", version, IssueKind.NOT_FOUND, candidates))
    return tuple(dict.fromkeys(found))


def _items(texts: Iterable[str], catalog: QueryCatalog, issues: list[ConditionIssue]) -> tuple[int, ...]:
    found = []
    for text in texts:
        matches = search_items(catalog.items, text)
        if len(matches) == 1:
            found.append(matches[0].item_id)
        else:
            kind = IssueKind.AMBIGUOUS if matches else IssueKind.NOT_FOUND
            issues.append(ConditionIssue("items", text, kind, tuple(m.content for m in matches)))
    return tuple(dict.fromkeys(found))


def _pm_matches(
    pm: PmVersion,
    query: ResolvedQuery,
    categories: Mapping[int, str],
    releases: Mapping[int, FamilyReleases],
) -> bool:
    if query.categories and categories.get(pm.group_id) not in query.categories:
        return False
    if query.group_ids and pm.group_id not in query.group_ids:
        return False
    if query.family_ids and pm.family_id not in query.family_ids:
        return False
    if query.release_ids:
        release_id = pm.release_version_id
        if release_id is None and pm.family_id in releases:
            release = releases[pm.family_id].match(pm.version_raw)
            release_id = release.release_version_id if release else None
        if release_id not in query.release_ids:
            return False
    return True


def _with_pms(record: EquipmentRecord, pms: tuple[PmVersion, ...]) -> EquipmentRecord:
    return EquipmentRecord(record.equipment_id, record.equipment_code, record.base_date, record.site,
                           record.line, record.model_name, pms, record.is_hpa)
