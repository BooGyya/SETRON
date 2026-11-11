"""AI가 먼저 질문을 '계획'하고, 규칙이 검사·대체한다 (11/11).

직접 입력한 질문은 AI에게 "어떤 도구를, 어떤 조건으로 쓸지"만 JSON으로 받는다. AI는 답·숫자를 만들지 않는다.

- 도구는 앱이 실제로 할 수 있는 일(조회·비교·변화·원인·우선순위·예측·버전 정보·월간 보고서·자료 안내)뿐이다.
- AI가 고른 이름은 선택지(사업장·라인·모델·계열)에 있는 것만 남긴다. 버전처럼 생긴 값은 규칙이 다시 확인한다.
- 날짜는 저장된 기준일에 있는지 본다. 없으면 '그 자료는 아직 없다'고 답한다 (다른 날짜로 바꾸지 않는다).
- 앱에 없는 기능(주간 보고서 등)은 '지원하지 않음'으로, 둘 이상으로 읽히는 질문은 '확인 질문'으로 돌려받는다.
- 통과한 계획은 규칙이 다시 읽을 수 있는 짧은 질문(예: '평택 업그레이드 우선순위')으로 바꿔 ``recognize``에 넣는다.
  그래서 조건 해석·검증은 규칙 한 곳에서만 일어나고, 숫자는 언제나 DB 계산이다.
- AI가 없거나 실패·형식 오류면 None — 화면은 규칙만으로 지금처럼 답한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

from setron.infrastructure.ai_client import ChatMessage, ResponseSource

from .schemas import Intent, Verdict

OUT_OF_SCOPE = "OUT_OF_SCOPE"
OUT_OF_SCOPE_TITLE = "요청 범위 밖"
CLARIFY_TITLE = "확인 질문"
OUT_OF_SCOPE_MESSAGE = ("SETRON 자료(설비 SW 버전 현황 · 릴리즈 · 목표 N)에 대한 질문에만 답할 수 있어요. "
                        "아래 같은 질문을 해 보세요.")

# 도구 이름 → 요청 종류 · 규칙이 확실히 알아듣는 표현 (recognize의 단어 표와 맞춤)
TOOLS = {
    "search": (Intent.SEARCH, "현황 보여줘", "설비 현황 · 목록 · 대수 · 상태"),
    "compare": (Intent.COMPARE, "달성률 비교", "사업장 · 라인 · 모델끼리 달성률 비교, 가장 낮거나 높은 곳"),
    "change": (Intent.CHANGE, "지난달 대비 변화", "이전 기준일 대비 변화, 나아졌는지 · 나빠졌는지"),
    "cause": (Intent.CAUSE, "원인 분석", "목표 N 미달 · 판단 불가의 원인, 왜"),
    "priority": (Intent.PRIORITY, "업그레이드 우선순위", "업그레이드 우선순위, 무엇부터"),
    "forecast": (Intent.FORECAST, "버전 통일 시점 예측", "버전 통일 · 목표 N 도달 시점 예측"),
    "version_info": (Intent.VERSION_INFO, "최신 버전", "최신 버전 · 버전 목록 · 출시일"),
    "report": (Intent.REPORT, "보고서 만들어줘",
               "보고서 만들기. 한 달이면 date, 여러 달 · 분기 · 상반기 · 연간 · 통합이면 date_range에 시작 · 끝 달, "
               "달을 나열하면 months, '각각' 원하면 each_month를 true로"),
    "aggregate": (Intent.AGGREGATE, "",
                  "집계: 파편화(버전이 흩어진 정도) · 버전 종류 수 · 버전별 대수 · 'X 버전을 기준으로 한 최신화율'처럼 "
                  "measure(count · versions · fragmentation · achievement · baseline)와 by(site · line · model · family · "
                  "version · pm)를 고르고, baseline에는 질문 속 기준 버전을 그대로 적는다"),
    "data_overview": (Intent.DATA_INFO, "가장 최근 자료 알려줘",
                      "자료 자체: 가장 최근 기준일, 기준일 목록, 몇 달치 있는지, 저장 현황, 만들 수 있는 보고서 · 기능. "
                      "보고서 종류 전체를 물으면 topic을 'reports'로. 특정 달 · 기간의 보고서가 되는지 물으면 data_overview가 아니라 report"),
}
INTENT_PHRASE = {intent: phrase for intent, phrase, _ in TOOLS.values()}
_BY_INTENT_NAME = {intent.name: name for name, (intent, _, _) in TOOLS.items()}
VERDICT_PHRASE = {Verdict.BELOW: "목표 N 미만", Verdict.ACHIEVED: "목표 N 이상", Verdict.CHECK: "확인 필요"}
GROUP_PHRASE = {"SITE": "사업장별", "LINE": "라인별", "MODEL": "모델별"}
_SPECIAL = (
    ("unsupported", "앱에 없는 기능 (주간 · 일별 보고서처럼 한 달보다 짧은 기간, 설비 제어, 자료에 없는 사람 · 일정 정보 등). "
                    "'unsupported'에 그 기능 이름을 짧게 적는다"),
    ("clarify", "두 가지 이상으로 읽혀 도구를 정할 수 없을 때. 'clarify'에 확인 질문, 'options'에 고를 수 있는 질문 2~3개"),
    ("out_of_scope", "설비 SW 버전 현황과 관계없는 질문 (날씨, 잡담, 일반 지식)"),
)
_VERSION = re.compile(r"^[vV]\d[\w.\-+]*$")
_DAY = re.compile(r"^(20\d\d)-(\d{1,2})-(\d{1,2})$")
_MONTH = re.compile(r"^(20\d\d)-(\d{1,2})$")
_PERIOD_WORDS = ("통합", "여러 달", "연간", "일년", "1년", "분기", "상반기", "하반기", "기간", "부터", "~", "누적")
_MAX_NAMES = 60               # 선택지가 너무 길면 앞쪽만 (토큰 절약)
_MAX_DATES = 24


@dataclass(frozen=True)
class Plan:
    """AI 계획을 검사한 결과. 하나만 채워진다.

    - ``question``: 규칙이 다시 읽을 짧은 질문 (보통의 경우).
    - ``out_of_scope``: 자료와 무관한 질문.
    - ``unsupported``: 앱에 없는 기능 이름 (예: '주간 보고서').
    - ``clarify`` · ``options``: 확인 질문과 고를 수 있는 질문.
    - ``missing_date``: 저장되지 않은 날짜 · 달 (예: '11월').
    """

    question: str = ""
    out_of_scope: bool = False
    unsupported: str = ""
    clarify: str = ""
    options: tuple[str, ...] = field(default_factory=tuple)
    missing_date: str = ""


@dataclass(frozen=True)
class Classified:
    """(예전 이름) 분류 결과. ``question``은 규칙이 다시 읽을 짧은 질문, ``out_of_scope``면 범위 밖."""

    question: str = ""
    out_of_scope: bool = False


def _choices(catalog) -> dict[str, list[str]]:
    groups = []
    for group in getattr(catalog, "analysis_groups", ()) or ():
        groups.extend(name for name in (group.name, getattr(group, "model_name", None)) if name)
    return {
        "sites": list(dict.fromkeys(catalog.sites))[:_MAX_NAMES],
        "lines": list(dict.fromkeys(catalog.lines))[:_MAX_NAMES],
        "models": list(dict.fromkeys(groups))[:_MAX_NAMES],
        "families": list(dict.fromkeys(f.name for f in catalog.families))[:_MAX_NAMES],
    }


def _dates(catalog) -> list[date]:
    return sorted(set(getattr(catalog, "dates", ()) or ()))


def _previous_text(previous) -> str:
    if previous is None or not getattr(previous, "understood", False):
        return "없음"
    from .schemas import INTENT_LABELS
    kinds = "·".join(INTENT_LABELS.get(i, i.value) for i in previous.intents) or "-"
    c = previous.conditions
    names = [*c.sites, *c.lines, *(getattr(g, "name", g) for g in c.groups), *(getattr(f, "name", f) for f in c.families)]
    return f"'{previous.question}' (요청: {kinds}, 조건: {', '.join(map(str, names)) or '전체'})"


def _prompt(catalog, *, previous=None, today: date | None = None) -> str:
    choices = _choices(catalog)
    dates = [d.isoformat() for d in _dates(catalog)][-_MAX_DATES:]
    tools = "\n".join(f"- {name}: {text}" for name, (_, _, text) in TOOLS.items())
    special = "\n".join(f"- {name}: {text}" for name, text in _SPECIAL)
    return (
        "너는 반도체 설비 SW 버전 현황 프로그램의 질문 분류기다. 질문에 답하지 말고 분류만 하라. "
        "숫자를 계산하거나 지어내지 마라. 계산은 프로그램이 한다.\n"
        f"tool은 아래 중 정확히 하나:\n{tools}\n{special}\n"
        "names에는 질문이 가리키는 이름을 아래 선택지에서 글자 그대로만 고른다. 선택지에 없으면 넣지 않는다. "
        "버전(v로 시작)은 질문에 적힌 그대로 넣어도 된다.\n"
        f"선택지: {json.dumps(choices, ensure_ascii=False)}\n"
        f"저장된 기준일(설비 현황표 날짜): {json.dumps(dates)}\n"
        f"오늘 날짜: {(today or date.today()).isoformat()}. '오늘·현재·지금·최근·이번 달'은 따로 날짜를 적지 말고 null로 둔다"
        "(프로그램이 가장 최근 기준일을 쓴다). 특정 달·날짜를 말하면 date에 'YYYY-MM' 또는 'YYYY-MM-DD'로 적는다.\n"
        "용어: 최신화율 · 적용률 · 표준화율 = 목표 N 이상 달성률(기준 버전을 말하지 않으면 compare), "
        "M 버전 = 기준 버전, 파편화 = 같은 계열 안에서 버전이 여러 가지로 흩어진 정도, 설비군 = 모델.\n"
        "verdict는 질문이 목표 N 미만 설비만 원하면 BELOW, 목표 이상만이면 ACHIEVED, 확인 필요만이면 CHECK, 아니면 null.\n"
        "group_by는 비교 기준이 분명하면 SITE · LINE · MODEL 중 하나, 아니면 null.\n"
        f"직전 질문: {_previous_text(previous)}. 이번 질문이 직전 결과를 이어 묻는 말('그중에', '그럼 화성은?')이면 follow_up을 true로.\n"
        '출력은 JSON 한 줄만: {"tool": "...", "names": [], "date": null, "verdict": null, "group_by": null, '
        '"follow_up": false, "unsupported": null, "clarify": null, "options": [], "topic": null, "date_range": null, '
        '"months": [], "each_month": false, "measure": null, "by": null, "baseline": null}'
    )


def _parse(text: str) -> dict | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.S)
    try:
        value = json.loads(match.group(0) if match else text)
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, dict) else None


def _date_phrase(raw, catalog) -> tuple[str, str]:
    """AI가 적은 날짜 → (규칙이 읽을 표현, 없는 날짜 표시). 저장된 기준일에 없으면 두 번째에 담는다."""
    text = str(raw or "").strip()
    if not text or text.lower() == "null":
        return "", ""
    dates = _dates(catalog)
    if (day := _DAY.match(text)) is not None:
        wanted = date(*(int(part) for part in day.groups()))
        return (wanted.isoformat(), "") if wanted in dates else ("", wanted.isoformat())
    if (month := _MONTH.match(text)) is not None:
        year, number = int(month.group(1)), int(month.group(2))
        if any(d.year == year and d.month == number for d in dates):
            return f"{number}월", ""
        return "", f"{number}월"
    return "", ""


_BY_WORD = {"site": "사업장", "line": "라인", "model": "모델", "family": "계열", "version": "버전", "pm": "PM"}


def _aggregate_phrase(value: dict) -> str:
    """집계 계획 → 규칙이 같은 집계로 읽는 표현 (recognize.detect_aggregate와 맞춤)."""
    measure = str(value.get("measure") or "").strip().lower()
    by = _BY_WORD.get(str(value.get("by") or "").strip().lower(), "")
    each = f"{by}별 " if by else ""
    if measure == "fragmentation":
        return f"{each}파편화"
    if measure == "versions":
        return f"{each}버전 종류"
    if measure == "count":
        return f"{each or '버전별 '}설비 몇 대"
    if measure == "achievement":
        return f"{each}달성률 비교"
    if measure == "baseline":
        baseline = str(value.get("baseline") or "").strip()
        if not _VERSION.match(baseline if baseline[:1] in "vV" else f"v{baseline}"):
            return ""
        baseline = baseline if baseline[:1] in "vV" else f"v{baseline}"
        return f"{baseline} 기준 최신화율 {each}".strip()
    return ""


def _month_number(raw) -> int | None:
    text = str(raw or "").strip()
    if (month := _MONTH.match(text)) is not None:
        return int(month.group(2))
    if (day := _DAY.match(text)) is not None:
        return int(day.group(2))
    return int(text) if text.isdigit() and 1 <= int(text) <= 12 else None


def _span_phrase(value: dict) -> str:
    """보고서 계획의 여러 달 → 규칙이 기간으로 읽는 표현 ('5월부터 7월까지', '5월·7월 각각')."""
    each = value.get("each_month") is True
    months = [m for m in (_month_number(v) for v in (value.get("months") or [])) if m]
    if len(months) >= 2:
        return "·".join(f"{m}월" for m in dict.fromkeys(months)) + " 각각"
    span = value.get("date_range")
    if isinstance(span, (list, tuple)) and len(span) == 2:
        first, last = _month_number(span[0]), _month_number(span[1])
        if first and last:
            return f"{first}월부터 {last}월까지" + (" 각각" if each else "")
    return ""


def plan(client, question: str, catalog, *, previous=None, today: date | None = None) -> Plan | None:
    """질문 하나를 AI로 계획하고 검사한다.

    param
    - client: 회사 AI 클라이언트(``ChatClient``). None이면 None.
    - question: 사용자 질문.
    - catalog: ``query.schemas.QueryCatalog`` (고를 수 있는 이름 · 저장된 기준일).
    - previous: 바로 앞 질문의 이해 결과 (이어 묻기 판단용).
    - today: 오늘 날짜 (기본: 실행한 날).

    return
    - ``Plan`` / AI가 없거나 실패 · 형식 오류 · 모르는 도구면 None (규칙으로 대신 답한다).
    """
    if client is None or not question.strip():
        return None
    try:
        reply = client.complete([ChatMessage("system", _prompt(catalog, previous=previous, today=today)),
                                 ChatMessage("user", question.strip())])
    except Exception:
        return None
    if getattr(reply, "source", ResponseSource.REAL) is not ResponseSource.REAL:
        return None                                       # 예제 응답은 질문과 무관하다
    value = _parse(getattr(reply, "content", ""))
    if value is None:
        return None
    tool = str(value.get("tool") or value.get("intent") or "").strip()
    tool = _BY_INTENT_NAME.get(tool.upper(), tool.lower())   # 예전 형식('PRIORITY')도 받는다
    if tool in ("out_of_scope", OUT_OF_SCOPE.lower()):
        return Plan(out_of_scope=True)
    if tool == "unsupported":
        label = str(value.get("unsupported") or "그 기능").strip()[:40]
        if any(word in label for word in _PERIOD_WORDS):
            return Plan(question=f"{label} 만들어줘")      # 여러 달 보고서는 있는 기능(보고서 메뉴) — 규칙이 기간으로 읽는다
        return Plan(unsupported=label)
    if tool == "clarify":
        text = str(value.get("clarify") or "").strip()
        options = tuple(str(o).strip() for o in (value.get("options") or []) if str(o).strip())[:3]
        return Plan(clarify=text[:200], options=options) if text else None
    if tool not in TOOLS:
        return None
    intent, phrase, _ = TOOLS[tool]
    if intent is Intent.AGGREGATE:
        phrase = _aggregate_phrase(value)
        if not phrase:
            return None
    if intent is Intent.DATA_INFO and str(value.get("topic") or "").strip().lower() == "reports":
        phrase = "어떤 보고서 만들 수 있어?"
    if intent is Intent.REPORT:
        span = _span_phrase(value)
        if span:
            return Plan(question=f"{span} 보고서 만들어줘")
    allowed = {name for names in _choices(catalog).values() for name in names}
    raw = value.get("names") if isinstance(value.get("names"), list) else []
    names = [str(n).strip() for n in raw if str(n).strip() in allowed or _VERSION.match(str(n).strip())]
    when, missing = _date_phrase(value.get("date"), catalog)
    if missing and intent is not Intent.DATA_INFO:
        return Plan(missing_date=missing)
    verdict = next((v for v in VERDICT_PHRASE if v.name == str(value.get("verdict") or "").strip().upper()), None)
    group = GROUP_PHRASE.get(str(value.get("group_by") or "").strip().upper(), "")
    parts = ["그중에"] if value.get("follow_up") is True and previous is not None else []
    parts += [*dict.fromkeys(names)]
    if when and intent is not Intent.DATA_INFO:
        parts.append(when)
    if verdict is not None and intent is Intent.SEARCH:
        parts.append(VERDICT_PHRASE[verdict])
    if group and intent is Intent.COMPARE:
        parts.append(group)
    parts.append(phrase)
    return Plan(question=" ".join(parts))


def classify(client, question: str, catalog) -> Classified | None:
    """(예전 이름) 규칙이 못 알아들은 질문 하나를 AI로 분류한다. ``plan``의 결과를 예전 모양으로 돌려준다."""
    result = plan(client, question, catalog)
    if result is None:
        return None
    if result.out_of_scope:
        return Classified(out_of_scope=True)
    return Classified(question=result.question) if result.question else None
