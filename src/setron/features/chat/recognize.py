"""질문 → 요청 종류 + 조건 (요청 인식).

AI 없이도 같은 질문은 항상 같은 결과가 나오도록 규칙으로 먼저 읽는다. AI가 찾은 조건(QueryConditions)이
있으면 합친다. 선택지에 없는 사업장·설비군 이름(중국·O7 등)은 버리지 않고 unresolved로 남겨 화면에 보여 준다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date

from setron.features.ai.schemas import QueryConditions
from setron.features.forecast.service import is_forecast_question

from .schemas import ChatRequest, GroupBy, Intent, Verdict

_WORDS: dict[Intent, tuple[str, ...]] = {
    Intent.REPORT: ("보고서", "리포트"),
    Intent.FORECAST: ("예측", "예상", "언제", "통일될", "통일 시점", "완료될",
                      "끝나", "끝날", "다 올리", "마무리", "완료 예정"),                  # 11/11 말투 넓히기 (A)
    Intent.CHANGE: ("지난달", "지난 달", "전월", "이전 기준일", "이전보다", "변화", "떨어진", "떨어졌", "올라간", "올랐",
                    "줄어든", "늘어난", "바뀐", "변경된", "변경 이력", "이후",
                    "나아졌", "좋아졌", "나빠졌", "지난번", "저번", "전보다", "늘었", "줄었", "개선됐", "개선되었"),
    Intent.CAUSE: ("왜", "원인", "이유", "때문"),
    Intent.PRIORITY: ("우선순위", "어디부터", "먼저", "업그레이드 순서", "순서대로", "급한",
                      "뭐부터", "무엇부터", "어떤 것부터", "어느 것부터", "어떤 설비부터", "부터 손", "손봐", "손 봐",
                      "손볼", "고쳐야", "시급", "급하"),
    Intent.COMPARE: ("비교", "가장 낮", "가장 높", "제일 낮", "제일 높", "어디가", "구역별", "라인별", "사업장별",
                     "모델별", "설비군별", "달성률", "적용률", "표준화율",
                     "더 안 좋", "제일 안 좋", "가장 안 좋", "더 나쁘", "더 나쁜", "제일 나쁜", "가장 나쁜", "뒤처", "뒤쳐",
                     "제일 안 된", "가장 안 된", "덜 된", "꼴찌", "뭐가 더", "어느 쪽", "어디 쪽", "더 잘 된", "더 잘된",
                     "제일 잘", "가장 잘"),
    Intent.VERSION_INFO: ("최신 버전", "최신버전", "출시일", "릴리즈", "어떤 버전", "버전 목록", "버전별"),
    Intent.SEARCH: ("보여", "목록", "몇 대", "몇대", "대수", "뽑아", "찾아", "어디 있", "어디에 있", "리스트", "현황",
                    "알려", "쓰는 설비", "쓰고 있는", "적용된 설비",
                    "어때", "어떻게 돼", "어떻게 되", "상태", "상황", "괜찮", "요즘", "전반", "전체적으로", "요약"),
}
_ORDER = (Intent.SEARCH, Intent.COMPARE, Intent.CHANGE, Intent.CAUSE, Intent.PRIORITY, Intent.FORECAST,
          Intent.VERSION_INFO, Intent.REPORT)
_ANALYZE = ("분석해", "분석 해", "분석하")
_EXPLICIT_LIST = ("목록", "리스트", "뽑아", "몇 대", "몇대", "설비 보여", "설비만")
_COMPARE_STRONG = ("비교", "가장 낮", "가장 높", "제일 낮", "제일 높", "어디가", "구역별", "라인별", "사업장별", "모델별", "설비군별")          # 종류를 콕 집지 않은 '분석' → 종합 분석
_VERDICT: tuple[tuple[Verdict, tuple[str, ...]], ...] = (
    (Verdict.CHECK, ("확인 필요", "판단 불가", "비교 불가", "미입력", "버전 없는", "버전이 없는", "누락")),
    (Verdict.BELOW, ("미만", "미달", "업그레이드 필요", "업그레이드가 필요", "안 된", "못 한")),
    (Verdict.ACHIEVED, ("n 이상", "목표 이상", "달성한", "달성 설비", "충족")),
)
_GROUP_BY = ((GroupBy.LINE, ("구역", "라인")), (GroupBy.SITE, ("사업장", "site")), (GroupBy.MODEL, ("모델별", "설비군별")))
_SMALLTALK = ("안녕", "고마", "감사", "수고", "반가")
_COMPARE_NEGATIVE = ("제일 안 된", "가장 안 된", "덜 된")      # '안 된'이 미달 판정이 아니라 비교 표현인 경우


def is_smalltalk(question: str) -> bool:
    """인사·감사처럼 분석 질문이 아닌 말 (AI 분류를 부르지 않는다)."""
    return _has(_norm(question), _SMALLTALK)
_FOLLOW = ("그중", "그 중", "그럼", "거기서", "거기에서", "여기서", "이 중", "이중에", "그 설비", "그 구역", "그 라인",
           "방금", "그거", "그것", "이어서", "그 결과")
_KNOWN_SITE_WORDS = ("화성", "평택", "기흥", "천안", "온양", "중국", "미국", "시안", "오스틴", "청주", "이천")
_LINE_TOKEN = re.compile(r"(?<![A-Za-z0-9])((?:P|S|V|M)\d{1,2}F?|\d{1,2}L|NRD|MFAB)(?![A-Za-z0-9])")
_GROUP_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:M-)?([OC][0-9])(?![0-9])")
_VERSION = re.compile(r"(?<![\w.])[vV]\d[\w.\-+]*")
_DATE = re.compile(r"(20\d\d)[-./](\d{1,2})[-./](\d{1,2})")
_MONTH = re.compile(r"(?<!\d)(1[0-2]|[1-9])\s*월")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _has(text: str, words: Iterable[str]) -> bool:
    flat = text.replace(" ", "")
    return any(word.casefold() in text or word.casefold().replace(" ", "") in flat for word in words)


def detect_intents(question: str) -> tuple[Intent, ...]:
    """질문 표현으로 요청 종류를 정한다. 종류를 콕 집지 않은 '분석해줘'는 비교·원인·우선순위·예측을 함께 한다."""
    text = _norm(question)
    found = {intent for intent, words in _WORDS.items() if _has(text, words)}
    if is_forecast_question(question):
        found.add(Intent.FORECAST)
    if _has(text, _ANALYZE) and not found & {Intent.CAUSE, Intent.PRIORITY, Intent.FORECAST, Intent.CHANGE}:
        found |= {Intent.COMPARE, Intent.CAUSE, Intent.PRIORITY, Intent.FORECAST}
    analysis = {Intent.COMPARE, Intent.CHANGE, Intent.CAUSE, Intent.PRIORITY, Intent.FORECAST, Intent.REPORT}
    if Intent.REPORT in found or (found & analysis and not _has(text, _EXPLICIT_LIST)):
        found.discard(Intent.SEARCH)          # 분석 질문의 '보여줘·알려줘'는 목록 요청이 아니다
    if Intent.CHANGE in found and not _has(text, _COMPARE_STRONG):
        found.discard(Intent.COMPARE)         # '지난달보다 달성률이 떨어진'은 변화 질문이지 구역 비교가 아니다
    return tuple(intent for intent in _ORDER if intent in found)


def detect_verdict(question: str) -> Verdict | None:
    text = _norm(question)
    for verdict, words in _VERDICT:
        if _has(text, words):
            return verdict
    return None


def detect_areas(question: str) -> tuple[str, ...]:
    text = _norm(question)
    return tuple(area for area, word in (("MEMORY", "메모리"), ("FOUNDRY", "파운드리")) if word in text)


def detect_group_by(question: str) -> GroupBy | None:
    text = _norm(question)
    for group_by, words in _GROUP_BY:
        if _has(text, words):
            return group_by
    return None


def _match(text: str, names: Iterable[tuple[str, str]], taken: list[tuple[int, int]]) -> list[str]:
    """(별칭, 대표 이름) 목록 중 질문에 단어로 들어 있는 것. 긴 별칭부터 찾고, 찾은 자리는 다시 쓰지 않는다."""
    found: list[str] = []
    for alias, name in sorted(names, key=lambda pair: len(pair[0]), reverse=True):
        if not alias.strip():
            continue
        pattern = r"(?<![A-Za-z0-9])" + re.escape(alias.casefold()) + r"(?![A-Za-z0-9])"
        for match in re.finditer(pattern, text):
            span = match.span()
            if any(start < span[1] and span[0] < end for start, end in taken):
                continue
            taken.append(span)
            if name not in found:
                found.append(name)
    return found


def extract_conditions(question: str, catalog) -> QueryConditions:
    """시연 DB 선택지(사업장·라인·모델·계열·버전·기준일)의 이름이 질문에 들어 있으면 조건으로 쓴다.

    param
    - question: 사용자 질문.
    - catalog: ``query.schemas.QueryCatalog``.

    return
    - 조건. 값은 선택지 이름 그대로이며, 선택지에 없는 사업장·설비군 이름은 ``unresolved``에 남긴다.
    """
    text = question.casefold()
    taken: list[tuple[int, int]] = []
    group_aliases = [alias for g in catalog.groups for alias in (
        (g.name, g.name), (g.model_name, g.name), (re.sub(r"^M-", "", g.model_name), g.name))]
    # 계열 이름이 설비군 이름과 같으면(예: O2) 더 넓은 설비군으로 본다. 'C3 통합UI'처럼 다른 이름만 계열로 잡는다.
    same = {alias.casefold() for alias, _ in group_aliases}
    families = _match(text, [(f.name, f.name) for f in catalog.families if f.name.casefold() not in same], taken)
    groups = _match(text, group_aliases, taken)
    lines = _match(text, [alias for line in catalog.lines for alias in (
        (line, line), (re.split(r"\s*\(", line)[0], line))], taken)
    sites = _match(text, [(site, site) for site in catalog.sites], taken)
    unresolved = []
    for word in _KNOWN_SITE_WORDS:
        if word in question and word not in catalog.sites and word not in sites:
            unresolved.append(word)
    for match in _LINE_TOKEN.finditer(question):             # 라인처럼 생겼는데 선택지에 없으면 보여 준다 (조용히 무시하지 않음)
        start, end = match.span()
        if not any(s < end and start < e for s, e in taken):
            unresolved.append(match.group(0))
            taken.append((start, end))
    for match in _GROUP_TOKEN.finditer(question):
        start, end = match.span()
        if not any(s < end and start < e for s, e in taken):
            unresolved.append(match.group(0))
    versions = []
    releases = {r.version_raw.strip().casefold(): r.version_raw.strip()
                for family in catalog.releases.values() for r in family.releases}
    for match in _VERSION.finditer(question):
        raw = match.group(0).rstrip(".,")
        if raw.casefold() in releases:
            versions.append(releases[raw.casefold()])
        else:
            unresolved.append(raw)
    base_date = None
    if (found := _DATE.search(question)) is not None:
        base_date = date(*(int(part) for part in found.groups())).isoformat()
    elif (month := _MONTH.search(question)) is not None and catalog.dates:
        same = [d for d in catalog.dates if d.month == int(month.group(1))]
        if same:
            base_date = max(same).isoformat()
        else:
            unresolved.append(month.group(0))
    return QueryConditions(sites=tuple(sites), lines=tuple(lines), groups=tuple(groups), families=tuple(families),
                           versions=tuple(versions), base_date=base_date, unresolved=tuple(dict.fromkeys(unresolved)))


def _union(*values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(v for value in values for v in value))


def recognize(question: str, catalog, ai_conditions: QueryConditions | None = None,
              previous: ChatRequest | None = None) -> ChatRequest:
    """질문 하나를 요청 종류와 조건으로 나눈다.

    param
    - question: 사용자 질문.
    - catalog: ``query.schemas.QueryCatalog``.
    - ai_conditions: AI가 찾은 조건(있으면 규칙 결과와 합친다).
    - previous: 바로 앞 질문의 이해 결과. "그중에 P3만", "그럼 화성은?"처럼 이어 묻는 질문이면 앞 조건을 이어받고
      새로 말한 조건만 바꾼다. 사업장을 바꾸면 앞의 라인은 버린다(라인은 사업장마다 다르다).

    return
    - 요청 종류·조건·판정·영역·묶는 기준.
    """
    rule = extract_conditions(question, catalog)
    ai = ai_conditions or QueryConditions()
    conditions = QueryConditions(
        sites=_union(rule.sites, ai.sites), lines=_union(rule.lines, ai.lines), groups=_union(rule.groups, ai.groups),
        models=_union(ai.models), families=_union(rule.families, ai.families), versions=_union(rule.versions, ai.versions),
        items=_union(ai.items), base_date=rule.base_date or ai.base_date, target_view=ai.target_view,
        unresolved=_union(rule.unresolved, ai.unresolved))
    intents = detect_intents(question)
    verdict, areas, group_by = detect_verdict(question), detect_areas(question), detect_group_by(question)
    has_conditions = any((conditions.sites, conditions.lines, conditions.groups, conditions.families,
                          conditions.versions, conditions.items, conditions.base_date))
    text = _norm(question)
    follow = previous is not None and previous.understood and (
        _has(text, _FOLLOW) or (not intents and (has_conditions or verdict or areas)))
    if follow:
        old = previous.conditions
        site_changed = bool(conditions.sites) and set(conditions.sites) != set(old.sites)
        conditions = QueryConditions(
            sites=conditions.sites or old.sites,
            lines=conditions.lines or (() if site_changed else old.lines),
            groups=conditions.groups or old.groups, models=conditions.models or old.models,
            families=conditions.families or old.families, versions=conditions.versions or old.versions,
            items=conditions.items or old.items, base_date=conditions.base_date or old.base_date,
            target_view=conditions.target_view, unresolved=conditions.unresolved)
        return ChatRequest(question=question, intents=intents or previous.intents, conditions=conditions,
                           areas=areas or previous.areas, verdict=verdict or previous.verdict,
                           group_by=group_by or previous.group_by, understood=True, follow_up=True)
    if Intent.COMPARE in intents and verdict is Verdict.BELOW and _has(text, _COMPARE_NEGATIVE):
        verdict = None                        # '제일 안 된 데'는 미달 설비 목록이 아니라 가장 낮은 곳 비교
    if Intent.COMPARE in intents and group_by is None and len(conditions.groups) + len(conditions.models) > 1 \
            and len(conditions.sites) <= 1 and not conditions.lines:
        group_by = GroupBy.MODEL              # 'O2랑 C3 중에 뭐가 더 안 좋아?'는 모델끼리 비교
    if not intents and (has_conditions or verdict or areas):
        intents = (Intent.SEARCH,)
    if not intents and conditions.versions:
        intents = (Intent.VERSION_INFO,)
    understood = bool(intents) or not _has(text, _SMALLTALK)
    return ChatRequest(question=question, intents=intents, conditions=conditions, areas=areas, verdict=verdict,
                       group_by=group_by, understood=understood and bool(intents))
