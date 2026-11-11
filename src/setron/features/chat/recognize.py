"""질문 → 요청 종류 + 조건 (요청 인식).

AI 없이도 같은 질문은 항상 같은 결과가 나오도록 규칙으로 먼저 읽는다. AI가 찾은 조건(QueryConditions)이
있으면 합친다. 선택지에 없는 사업장·설비군 이름(중국·O7 등)은 버리지 않고 unresolved로 남겨 화면에 보여 준다.
"""

from __future__ import annotations

import re
from dataclasses import replace
from collections.abc import Iterable
from datetime import date

from setron.features.ai.schemas import QueryConditions
from setron.features.forecast.service import FORECAST_WORDS

from .schemas import ChatRequest, GroupBy, Intent, Verdict

_WORDS: dict[Intent, tuple[str, ...]] = {
    Intent.REPORT: ("보고서", "리포트"),
    Intent.FORECAST: ("예측", "예상", "통일될", "통일 시점", "완료될",
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
                     "제일 잘", "가장 잘", "최신화율", "최신화 비율"),          # 멘토 용어: 최신화율 = 목표 N 달성률
    Intent.VERSION_INFO: ("최신 버전", "최신버전", "출시일", "릴리즈", "어떤 버전", "버전 목록", "버전별"),
    Intent.SEARCH: ("보여", "목록", "몇 대", "몇대", "대수", "뽑아", "찾아", "어디 있", "어디에 있", "리스트", "현황",
                    "알려", "쓰는 설비", "쓰고 있는", "적용된 설비",
                    "어때", "어떻게 돼", "어떻게 되", "상태", "상황", "괜찮", "요즘", "전반", "전체적으로", "요약"),
}
_ORDER = (Intent.SEARCH, Intent.COMPARE, Intent.CHANGE, Intent.CAUSE, Intent.PRIORITY, Intent.FORECAST,
          Intent.VERSION_INFO, Intent.REPORT, Intent.DATA_INFO, Intent.AGGREGATE)
# 범용 집계 (11/11 멘토링: 라인 × 설비군의 최신화율 · 파편화) — 규칙은 AI가 없을 때의 대체 · 정해진 문장용
_FRAGMENT = ("파편화", "파편", "흩어져", "흩어진", "흩어졌", "제각각", "들쭉날쭉", "분산")
_KINDS = ("버전 종류", "몇 가지 버전", "버전이 몇 가지", "버전 몇 가지", "버전 몇 개", "버전이 몇 개", "버전 수")
_BASELINE = ("기준으로", "기준 버전", "기준일 때", "기준이라고", "기준이면", "m 버전", "m버전", "엠 버전", "기준 삼", "기준으로 봤")
_FRESH = ("최신화율", "최신화", "적용률", "이상 비율", "달성률", "표준화율", "최신 비율")
_AGG_DIMS = (("라인", "line"), ("사업장", "site"), ("site", "site"), ("설비군", "model"), ("모델", "model"),
             ("계열", "family"), ("pm", "pm"), ("버전", "version"))
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
# 자료 자체에 대한 질문 (11/11): 다른 요청 종류보다 먼저 본다. '마지막 데이터가 언제야?'가 통일 예측으로 가던 문제.
_DATA_INFO = ("마지막 데이터", "최근 데이터", "최신 데이터", "마지막 자료", "최근 자료", "최신 자료", "마지막으로 저장",
              "마지막에 저장", "저장된 데이터", "저장된 자료", "데이터가 언제", "자료가 언제", "데이터는 언제", "자료는 언제",
              "언제까지 있", "언제 자료", "언제 데이터", "기준일 목록", "기준일이 언제", "기준일 언제", "기준일은 언제", "몇 달치",
              "몇달치", "몇 개월치", "몇개월치", "어떤 보고서", "무슨 보고서", "보고서 종류", "어떤 데이터", "무슨 데이터",
              "어떤 자료", "무슨 자료", "자료 현황", "데이터 현황", "자료 범위", "데이터 범위", "뭐 할 수 있", "무엇을 할 수",
              "뭘 할 수", "어떤 질문")
# '언제'는 통일·완료·목표 같은 말과 함께일 때만 통일 예측이다.
_WHICH = ("어떤", "어떠", "무슨", "종류", "뭐가 있", "뭐 있", "어떻게 만들")   # '어떤 보고서 만들 수 있어?' = 기능 안내
_WHEN = ("언제", "몇 달", "몇달")
_WHEN_WITH = ("통일", "완료", "끝", "올리", "도달", "목표", "업그레이드", "맞춰", "따라잡", "N 이상", "100%")
_FORECAST_ALONE = tuple(word for word in FORECAST_WORDS if word not in _WHEN)
# 지원하지 않는 보고서 기간: 현황표는 기준일 단위(지금 자료는 한 달에 한 번)로 저장돼 월간 보고서만 만들 수 있다.
_PERIOD_UNSUPPORTED = (("주간", "주간 보고서"), ("일주일", "주간 보고서"), ("이번 주", "주간 보고서"), ("지난주", "주간 보고서"),
                       ("지난 주", "주간 보고서"), ("일별", "일별 보고서"), ("일일", "일별 보고서"), ("하루", "일별 보고서"),
                       ("오늘", "일별 보고서"))
# 여러 달 보고서(11/11): 자료(한 달 간격)로 만들 수 있다. AI 분석은 월간만 바로 만들고, 기간 보고서는 보고서 메뉴로 안내한다.
_MONTH_RANGE = re.compile(r"(1[0-2]|[1-9])\s*월\s*(?:부터|에서|~|∼|-|–)\s*(1[0-2]|[1-9])\s*월")
_QUARTER = re.compile(r"([1-4])\s*분기")
_HALVES = (("상반기", (1, 6)), ("하반기", (7, 12)))
_WHOLE = ("연간", "일년", "1년", "올해", "한 해", "전체 기간", "여러 달", "통합", "누적", "기간 보고서", "기간별")
_EACH = ("각각", "월별로", "달마다", "한 달씩", "한달씩", "따로")


def _forecast_in_chat(text: str) -> bool:
    """AI 분석의 통일 예측 판단. '언제'·'몇 달'만으로는 예측으로 보지 않는다."""
    if _has(text, _FORECAST_ALONE):
        return True
    return _has(text, _WHEN) and _has(text, _WHEN_WITH)


def detect_period(question: str) -> tuple[tuple[int, ...], bool]:
    """보고서 질문의 여러 달 (해당하는 달 목록)과 '각각' 여부. 기간이 없으면 ((), False).

    '5월부터 7월' → (5, 6, 7) · '5월이랑 7월' → (5, 7) 각각 · '3분기' → (7, 8, 9) · '연간·통합' → 1~12월 전체
    """
    text = _norm(question)
    each = _has(text, _EACH)
    if (found := _MONTH_RANGE.search(question)) is not None:
        first, last = int(found.group(1)), int(found.group(2))
        months = tuple(range(first, last + 1)) if first <= last else (*range(first, 13), *range(1, last + 1))
        return months, each
    listed = tuple(dict.fromkeys(int(m) for m in re.findall(r"(?<!\d)(1[0-2]|[1-9])\s*월", question)))
    if len(listed) >= 2:                       # '5월·6월·7월 각각', '5월이랑 7월' → 나열한 달마다
        return tuple(sorted(listed)), True
    if (quarter := _QUARTER.search(question)) is not None:
        first = (int(quarter.group(1)) - 1) * 3 + 1
        return (first, first + 1, first + 2), each
    for word, (first, last) in _HALVES:
        if word in text:
            return tuple(range(first, last + 1)), each
    if _has(text, _WHOLE):
        return tuple(range(1, 13)), each
    return (), False


def _strip_baseline(conditions, aggregate):
    """기준 버전은 '그 버전 쓰는 설비' 조건이 아니다 — 조건(등록 버전 · 못 찾은 낱말)에서 뺀다."""
    if not aggregate or aggregate[0] != "baseline":
        return conditions
    baseline = aggregate[2].casefold()
    return replace(conditions,
                   versions=tuple(v for v in conditions.versions if v.casefold() != baseline),
                   unresolved=tuple(u for u in conditions.unresolved if u.rstrip(".,?").casefold() != baseline))


def detect_aggregate(question: str) -> tuple[str, ...]:
    """범용 집계 요청 (무엇을 셀지, 무엇별로, 기준 버전). 아니면 ()."""
    text = _norm(question)

    def by_word() -> str:
        for word, key in _AGG_DIMS:
            if f"{word}별" in text or f"{word}마다" in text:
                return key
        if "가장" in text or "어느" in text or "어디" in text:
            spots = [(text.find(word), key) for word, key in _AGG_DIMS if key != "version" and word in text]
            if spots:
                return min(spots)[1]
        return ""

    if _has(text, ("추이", "월별", "달마다", "기준일별", "흐름", "변화 추세")) and _has(text, _FRESH + ("달성", "대수", "설비 수")):
        return ("achievement", "date", "")                 # '달성률 추이' · '월별 달성률' → 기준일별
    if _has(text, _FRAGMENT):
        return ("fragmentation", by_word() or "line", "")
    if _has(text, _KINDS):
        return ("versions", by_word() or "line", "")
    based = _has(text, _BASELINE) or re.search(r"기준(?!일)", text) is not None
    fresh = _has(text, _FRESH) or _has(text, ("낮", "높", "비교", "순위", "얼마나", "어느 정도", "비율", "퍼센트"))
    based = based or (_has(text, ("이상", "이후", "넘는", "넘은")) and _has(text, _FRESH + ("비율", "퍼센트", "얼마나")))  # 'v4.59 이상 쓰는 비율'
    if based and fresh and (found := _VERSION.search(question)) is not None:
        return ("baseline", by_word(), found.group(0).rstrip(".,?"))
    if "버전별" in text and _has(text, ("몇 대", "몇대", "설비 수", "대수", "분포", "얼마나")):
        return ("count", "version", "")
    for word, key in _AGG_DIMS:
        if key in ("family", "pm") and (f"{word}별" in text or f"{word}마다" in text) and _has(text, _FRESH):
            return ("achievement", key, "")            # 계열·PM별 달성률은 비교(사업장·라인·모델)에 없어 집계로
    if (f"pm별" in text or "계열별" in text) and _has(text, ("몇 대", "몇대", "설비 수", "대수")):
        return ("count", "pm" if "pm별" in text else "family", "")
    if "버전" in text and "분포" in text:
        return ("count", "version", "")
    return ()


def unsupported_period(question: str) -> str:
    """보고서 질문에서 지원하지 않는 기간(주간·일별 등)을 찾는다. 없으면 빈 문자열."""
    text = _norm(question)
    return next((label for word, label in _PERIOD_UNSUPPORTED if word in text), "")
_COMPARE_NEGATIVE = ("제일 안 된", "가장 안 된", "덜 된")      # '안 된'이 미달 판정이 아니라 비교 표현인 경우


def is_smalltalk(question: str) -> bool:
    """인사·감사처럼 분석 질문이 아닌 말 (AI 분류를 부르지 않는다)."""
    return _has(_norm(question), _SMALLTALK)
_FOLLOW = ("그중", "그 중", "그럼", "거기서", "거기에서", "여기서", "이 중", "이중에", "그 설비", "그 구역", "그 라인",
           "그래?", "그래요", "그런 거", "그런거", "왜 그", "이유는", "원인은", "거기", "비교하면", "비교하자면", "보고서로",
           "결과를", "이 결과", "이걸", "이거", "엑셀로", "excel로", "csv로", "내보내", "뽑아줘", "저장해",
           "방금", "그거", "그것", "이어서", "그 결과")
_KNOWN_SITE_WORDS = ("화성", "평택", "기흥", "천안", "온양", "중국", "미국", "시안", "오스틴", "청주", "이천")
_SITE_ALIASES = {"pyeongtaek": "평택", "pyungtaek": "평택", "hwaseong": "화성", "hwasung": "화성", "giheung": "기흥", "cheonan": "천안"}
_KOREAN_SOUNDS = (("씨", "C"), ("오", "O"))                    # '씨4' → C4, '오2' → O2
_KOREAN_DIGITS = {"투": "2", "쓰리": "3", "포": "4", "파이브": "5"}   # '오투' → O2
_LINE_TOKEN = re.compile(r"(?<![A-Za-z0-9.])((?:P|S|V|M)\d{1,2}F?|\d{1,2}L|NRD|MFAB)(?![A-Za-z0-9._])")  # 'V1.2.1.4'의 V1은 라인이 아니다
_GROUP_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:M-)?([OC][0-9])(?![0-9])")
_VERSION = re.compile(r"(?<![\w.])[vV]\d[0-9A-Za-z._\-+]*")   # 영문·숫자·기호만 ('v3.52_20을'의 조사를 붙여 읽지 않게)
_VERSION_SPAN = re.compile(r"(?<![\w.])[vV]\d+[._][0-9A-Za-z._\-+]*")    # 점·밑줄이 있어야 버전 (V1 라인과 구분)
_DATE = re.compile(r"(20\d\d)[-./](\d{1,2})[-./](\d{1,2})")
_MONTH = re.compile(r"(?<!\d)(1[0-2]|[1-9])\s*월")
_YEAR_MONTH = re.compile(r"(20\d\d)\s*년\s*(1[0-2]|[1-9])\s*월")
_YEAR_ONLY = re.compile(r"(20\d\d)\s*년(?!\s*(?:1[0-2]|[1-9])\s*월)")
_MONTH_DAY = re.compile(r"(?<![\d/.])(1[0-2]|[1-9])/(\d{1,2})(?![\d/])")
_RELATIVE = (("지지난달", 2), ("지지난 달", 2), ("두 달 전", 2), ("2달 전", 2), ("2개월 전", 2), ("세 달 전", 3), ("3달 전", 3),
             ("3개월 전", 3), ("네 달 전", 4), ("4달 전", 4), ("지난달", 1), ("지난 달", 1), ("저번달", 1), ("저번 달", 1),
             ("전달", 1), ("한 달 전", 1), ("1달 전", 1), ("1개월 전", 1))
_CHANGE_MARKERS = ("보다", "대비", "비교", "이후", "부터", "떨어", "올라", "늘", "줄", "변화", "바뀐", "변경", "나아", "나빠", "개선",
                   "추이", "흐름")
_CODE = re.compile(r"(?<![A-Za-z0-9-])(?:DEMO-\d{3,}|[A-Z]{4,6}\d[A-Z0-9]*|[A-Z]{2,5}\d{2,}[A-Z0-9]*|[A-Z]{1,4}-\d{3,}[A-Z0-9]*)(?![A-Za-z0-9])")   # ESOC6 · ESPA02 · FC40102 · DM-009 (XMO3 · MC4 같은 모델은 아님)
_OFF_TOPIC = ("날씨", "점심", "저녁 메뉴", "아침 메뉴", "맛집", "영화", "주식", "로또", "농담", "노래", "게임", "하는 법", "방법 알려",
              "팁 알려", "비법", "잘하는 법", "여행")
_UNSUPPORTED_INFO = (("공정", "공정 정보"), ("담당자", "담당자 정보"), ("엔지니어", "담당자 정보"), ("일정", "일정 정보"),
                     ("스케줄", "일정 정보"), ("비용", "비용 정보"), ("고장", "고장 이력"), ("장애", "고장 이력"),
                     ("위험한 설비", "위험 후보 거르기"), ("위험 설비", "위험 후보 거르기"), ("위험 후보", "위험 후보 거르기"))
_TIERS = ((re.compile(r"n\s*\+\s*1"), "ACHIEVED"), (re.compile(r"n\s*-\s*[12]"), "BELOW"))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _has(text: str, words: Iterable[str]) -> bool:
    flat = text.replace(" ", "")
    return any(word.casefold() in text or word.casefold().replace(" ", "") in flat for word in words)


def detect_intents(question: str) -> tuple[Intent, ...]:
    """질문 표현으로 요청 종류를 정한다. 종류를 콕 집지 않은 '분석해줘'는 비교·원인·우선순위·예측을 함께 한다."""
    text = _norm(question)
    if _has(text, _OFF_TOPIC):
        return ()                             # 날씨 · 점심 · '~하는 법' 같은 말은 분석 요청이 아니다 (AI가 있으면 범위 밖으로 안내)
    if _has(text, _DATA_INFO) and re.search(r"(최근|최신|마지막)\s*(자료|데이터)\s*(로|를|의)?\s*기준", text) is None:
        return (Intent.DATA_INFO,)            # 자료 자체에 대한 질문은 분석 요청보다 먼저 (11/11). '최근 자료 기준 …'은 조건이다
    found = {intent for intent, words in _WORDS.items() if _has(text, words)}
    if _forecast_in_chat(text):
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
        if re.search(r"[가-힣]", alias):                            # 한글 이름: '평택P1F' · '평택에서'처럼 뒤에 무엇이 붙어도 단어
            pattern = r"(?<![가-힣])" + re.escape(alias.casefold())
        else:
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
    # 버전처럼 생긴 자리는 먼저 빼 둔다 — 'V1.2.1.4'의 V1을 V1 라인으로, 'v3.52'의 일부를 다른 이름으로 읽지 않게 (11/11)
    reserved = [m.span() for m in _VERSION_SPAN.finditer(question)]
    codes = [m.group(0) for m in _CODE.finditer(question)]
    reserved += [m.span() for m in _CODE.finditer(question)]
    taken: list[tuple[int, int]] = list(reserved)
    group_aliases = [alias for g in catalog.groups for alias in (
        (g.name, g.name), (g.model_name, g.name), (re.sub(r"^M-", "", g.model_name), g.name),
        (g.model_name.replace("-", ""), g.name))]                 # 'MC4'
    for g in catalog.groups:                                       # 한글 발음: '씨4' · '오투'
        short = re.sub(r"^M-", "", g.model_name)
        for sound, letter in _KOREAN_SOUNDS:
            if short[:1] == letter and short[1:2].isdigit():
                group_aliases.append((sound + short[1:], g.name))
                for word, digit in _KOREAN_DIGITS.items():
                    if short[1:] == digit:
                        group_aliases.append((sound + word, g.name))
    # 계열 이름이 설비군 이름과 같으면(예: O2) 더 넓은 설비군으로 본다. 'C3 통합UI'처럼 다른 이름만 계열로 잡는다.
    same = {alias.casefold() for alias, _ in group_aliases}
    families = _match(text, [(f.name, f.name) for f in catalog.families if f.name.casefold() not in same], taken)
    groups = _match(text, group_aliases, taken)
    lines = _match(text, [alias for line in catalog.lines for alias in (
        (line, line), (re.split(r"\s*\(", line)[0], line))], taken)
    sites = _match(text, [(site, site) for site in catalog.sites]
                   + [(alias, site) for alias, site in _SITE_ALIASES.items() if site in catalog.sites], taken)
    unresolved = []
    for word in _KNOWN_SITE_WORDS:
        if word in question and word not in catalog.sites and word not in sites:
            inside = [line for line in catalog.lines if word in line]     # '기흥'은 '1L (기흥)'처럼 라인 이름 속 지역
            if inside and not any(line in lines for line in inside):
                lines.extend(line for line in inside if line not in lines)
            elif not inside:
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
    names = [span for span in taken if span not in reserved]       # 라인 · 모델 · 계열 · 사업장 이름으로 찾은 자리
    for match in _VERSION.finditer(question):
        start, end = match.span()
        if any(s < end and start < e for s, e in names):
            continue                                                 # '평택 V2 라인'의 V2는 라인 이름이지 버전이 아니다
        raw = match.group(0).rstrip(".,")
        if raw.casefold() in releases:
            versions.append(releases[raw.casefold()])
        else:
            unresolved.append(raw)
    base_date = None
    if (found := _DATE.search(question)) is not None:
        base_date = date(*(int(part) for part in found.groups())).isoformat()
    elif (ym := _YEAR_MONTH.search(question)) is not None:
        same = [d for d in catalog.dates if d.year == int(ym.group(1)) and d.month == int(ym.group(2))]
        if same:
            base_date = max(same).isoformat()
        else:
            unresolved.append(f"{ym.group(1)}년 {ym.group(2)}월")
    elif (year := _YEAR_ONLY.search(question)) is not None and not any(d.year == int(year.group(1)) for d in catalog.dates):
        unresolved.append(f"{year.group(1)}년")
    elif (md := _MONTH_DAY.search(question)) is not None:
        same = [d for d in catalog.dates if d.month == int(md.group(1)) and d.day == int(md.group(2))]
        if same:
            base_date = max(same).isoformat()
        else:
            unresolved.append(md.group(0))
    elif (month := _MONTH.search(question)) is not None and catalog.dates:
        same = [d for d in catalog.dates if d.month == int(month.group(1))]
        if same:
            base_date = max(same).isoformat()
        else:
            unresolved.append(month.group(0))
    return QueryConditions(sites=tuple(sites), lines=tuple(lines), groups=tuple(groups), families=tuple(families),
                           versions=tuple(versions), base_date=base_date, unresolved=tuple(dict.fromkeys(unresolved)),
                           items=tuple(codes))                      # items 칸에 설비 호기를 실어 보낸다 (recognize가 codes로 옮김)


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
        items=_union(rule.items, ai.items), base_date=rule.base_date or ai.base_date, target_view=ai.target_view,
        unresolved=_union(rule.unresolved, ai.unresolved))
    intents = detect_intents(question)
    # 범용 집계(파편화 · 버전 종류 · 기준 최신화율)는 이어 묻기 판단보다 먼저 본다 — 요청 종류 단어가 없다고
    # '평택에서 가장 파편화된 라인은?'을 앞 질문의 이어 묻기로 읽던 문제 (11/11)
    aggregate = detect_aggregate(question) if Intent.DATA_INFO not in intents else ()
    if aggregate:
        intents = (Intent.AGGREGATE,)
    verdict, areas, group_by = detect_verdict(question), detect_areas(question), detect_group_by(question)
    if Intent.CHANGE in intents and Intent.COMPARE in intents and group_by is None and "별" not in _norm(question):
        intents = tuple(i for i in intents if i is not Intent.COMPARE)       # '지난달이랑 비교하면?'은 기간 변화
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
        inherited = aggregate or (previous.aggregate if not intents else ())    # '그중에 P3만'은 앞 집계를 이어받는다
        return ChatRequest(question=question, intents=intents or previous.intents,
                           conditions=_strip_baseline(conditions, inherited),
                           areas=areas or previous.areas, verdict=verdict or previous.verdict,
                           group_by=group_by or previous.group_by, understood=True, follow_up=True,
                           aggregate=inherited, codes=tuple(conditions.items) or previous.codes)
    if Intent.COMPARE in intents and verdict is Verdict.BELOW and _has(text, _COMPARE_NEGATIVE):
        verdict = None                        # '제일 안 된 데'는 미달 설비 목록이 아니라 가장 낮은 곳 비교
    if Intent.COMPARE in intents and group_by is None and len(conditions.groups) + len(conditions.models) > 1 \
            and len(conditions.sites) <= 1 and not conditions.lines:
        group_by = GroupBy.MODEL              # 'O2랑 C3 중에 뭐가 더 안 좋아?'는 모델끼리 비교
    if intents == (Intent.REPORT,) and not conditions.base_date and _has(text, _WHICH):
        intents = (Intent.DATA_INFO,)         # 보고서를 만들어 달라는 게 아니라 어떤 보고서가 되는지 묻는 질문
    if not intents and (has_conditions or verdict or areas):
        intents = (Intent.SEARCH,)
    if not intents and conditions.versions:
        intents = (Intent.VERSION_INFO,)
    conditions = _strip_baseline(conditions, aggregate)
    codes = tuple(conditions.items)
    conditions = replace(conditions, items=())
    if Intent.CHANGE in intents and not _has(text, _CHANGE_MARKERS):
        intents = tuple(i for i in intents if i is not Intent.CHANGE) or (Intent.SEARCH,)   # '지난달 현황'은 그 달의 현황
    if not conditions.base_date and Intent.CHANGE not in intents:
        back = next((n for word, n in _RELATIVE if word in text), None)        # '두 달 전 달성률' → 그 기준일
        if back is not None and catalog.dates:
            ordered = sorted(catalog.dates)
            if len(ordered) > back:
                conditions = replace(conditions, base_date=ordered[-1 - back].isoformat())
            else:
                conditions = replace(conditions, unresolved=(*conditions.unresolved, f"{back}달 전"))
    for pattern, tier in _TIERS:                                                # 'N-2 이하' · 'N+1' → 미만 · 이상
        if pattern.search(text) and verdict is None:
            verdict = Verdict.BELOW if tier == "BELOW" else Verdict.ACHIEVED
    unsupported_info = next((label for word, label in _UNSUPPORTED_INFO if word in text), "")
    understood = bool(intents) or not _has(text, _SMALLTALK)
    period, each = detect_period(question) if Intent.REPORT in intents else ((), False)
    unsupported = unsupported_period(question) if Intent.REPORT in intents and not period else ""
    if unsupported_info and not unsupported:
        unsupported = unsupported_info
        intents = intents or (Intent.SEARCH,)
    topic = "reports" if intents == (Intent.DATA_INFO,) and "보고서" in text else ""
    return ChatRequest(question=question, intents=intents, conditions=conditions, areas=areas, verdict=verdict,
                       group_by=group_by, understood=understood and bool(intents), unsupported=unsupported,
                       topic=topic, period=period, each_month=each, aggregate=aggregate, codes=codes)
