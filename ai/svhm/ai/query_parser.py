# -*- coding: utf-8 -*-
"""E2. 자연어 질문 → 조회 조건(QueryCondition) JSON.

"평택 O2 중 두 칸 이상 뒤처진 설비만 보여줘"
→ {sites:[평택], groups:[O2], relative:{op:"below", steps:2}, chart:"treemap", intent:"query"}

원칙
  - AI 는 '말을 조회 조건으로 바꾸는 일'만 한다. 숫자 계산은 코드가 한다.
  - 설비군·사이트·라인 어휘는 refdata 에서 뽑아 프롬프트에 넣는다 (O2 ≠ KCTC Inline O2 같은 함정 포함).
  - 애매하면 need_clarification=True + 되물을 질문. 억지로 추정하지 않는다.
  - 결과는 코드가 다시 검증한다 (모르는 설비군·사이트 → 되묻기).
이 JSON 이 프론트 체크박스·백엔드 API 가 공유하는 '조회 조건 객체' 그 자체다.
"""
from __future__ import annotations

import json, re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from svhm.core import refdata as R
from svhm.ai.llm_client import LLMClient

# ----------------------------------------------------------------------------- 어휘
SITES = ["평택", "화성"]
GROUP_NAMES = [g["name"] for gid, g in R.GROUPS.items() if g.get("analysis_enabled", True)]
LINES = sorted({l["line"] for l in R.LINES})
GROUP_SYNONYMS = {
    "C3": ["C3", "MC3", "M-C3", "씨3"], "C4": ["C4", "MC4", "M-C4", "씨4"], "C4 DFB": ["DFB", "C4 DFB"], "C4+": ["C4+", "C4 플러스", "C4플러스"],
    "O2": ["O2", "MO2", "M-O2", "오투"], "O3": ["O3", "MO3", "M-O3"], "O5": ["O5", "MO5", "M-O5"], "O5 Prime": ["Prime", "프라임", "O5 Prime"],
    "XMO3": ["XMO3"], "KCTC Inline Ox": ["Inline Ox", "인라인 Ox"], "KCTC Inline O2": ["Inline O2", "인라인 O2"],
    "KCTC Cluster Ox": ["Cluster Ox", "클러스터 Ox"], "KCTC Cluster O2": ["Cluster O2", "클러스터 O2"],
}
CATEGORY_WORDS = {"POLY": ["Poly", "폴리"], "OXIDE": ["Oxide", "옥사이드"], "KCTC": ["KCTC", "케이씨티씨"]}


class TargetSpec(BaseModel):
    mode: Literal["latest", "version", "unspecified"] = "unspecified"   # latest=각 릴리즈노트 최신 칸, version=지정
    versions: dict[str, str] = Field(default_factory=dict)              # {"C4": "v4.56.6.43"} 설비군별 지정 N


class RelativeSpec(BaseModel):
    op: Literal["any", "below", "at_or_above", "equal", "steps_below_at_least"] = "any"
    steps: Optional[int] = None                                         # steps_below_at_least 일 때 칸 수


class QueryCondition(BaseModel):
    intent: Literal["query", "item", "why", "forecast", "report", "priority", "unknown"] = "query"
    sites: list[str] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)
    areas: list[Literal["MEMORY", "FOUNDRY"]] = Field(default_factory=list)
    categories: list[Literal["POLY", "OXIDE", "KCTC"]] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    base_date: Optional[str] = None            # 'latest' 또는 YYYY-MM-DD, None=최신
    compare_date: Optional[str] = None         # 변화 비교용
    target: TargetSpec = Field(default_factory=TargetSpec)
    relative: RelativeSpec = Field(default_factory=RelativeSpec)
    risk_only: bool = False
    item_keyword: Optional[str] = None         # intent=item 일 때 기능/아이템 검색어
    chart: Literal["auto", "table", "pie", "bar", "treemap", "timeline"] = "auto"
    need_clarification: bool = False
    clarification_question: Optional[str] = None
    notes: str = ""

    @field_validator("sites")
    @classmethod
    def _sites(cls, v):
        return [s for s in v if s in SITES]

    @field_validator("groups")
    @classmethod
    def _groups(cls, v):
        return [g for g in v if g in GROUP_NAMES]


SYSTEM_PROMPT = f"""너는 반도체 설비 SW 버전 관리 프로그램의 '질문 해석기'다.
사용자의 한국어 질문을 아래 JSON 스키마의 '조회 조건'으로 바꾼다. 계산·판단은 하지 않는다. 오직 JSON 만 출력한다.

[스키마]
{{
 "intent": "query|item|why|forecast|report|priority|unknown",
 "sites": ["평택"|"화성"], "lines": [라인명], "areas": ["MEMORY"|"FOUNDRY"], "categories": ["POLY"|"OXIDE"|"KCTC"],
 "groups": [설비군명], "base_date": "latest"|"YYYY-MM-DD"|null, "compare_date": "YYYY-MM-DD"|null,
 "target": {{"mode": "latest|version|unspecified", "versions": {{"설비군명": "버전"}}}},
 "relative": {{"op": "any|below|at_or_above|equal|steps_below_at_least", "steps": 정수|null}},
 "risk_only": true|false, "item_keyword": 문자열|null,
 "chart": "auto|table|pie|bar|treemap|timeline",
 "need_clarification": true|false, "clarification_question": 문자열|null, "notes": ""
}}

[어휘 — 여기 있는 이름만 쓴다]
사이트: {", ".join(SITES)}
설비군: {", ".join(GROUP_NAMES)}
  · 'O2'는 Oxide 의 M-O2 를 뜻한다. 'Inline O2', 'Cluster O2' 는 이름에 O2 가 있어도 KCTC 설비군이다. 사용자가 그냥 'O2' 라고 하면 groups=["O2"].
  · 'KCTC' 라고만 하면 categories=["KCTC"] (설비군 4개 전부). 'Poly'/'Oxide' 도 categories 로.
  · 'C4+', 'DFB', 'Prime', 'XMO3' 는 각각 별도 설비군명.
라인(일부): {", ".join(LINES)}
메모리/파운드리: 평택 S5·V2·P3-S5 와 화성 Foundry 는 FOUNDRY, 나머지는 MEMORY. 사용자가 '파운드리'라고 하면 areas=["FOUNDRY"].

[intent 규칙]
query: 현황·대수·적용률·목록을 보여달라 / item: 특정 기능·아이템이 어느 버전·어느 설비에 들어갔는지 (item_keyword 필수)
why: 왜 버전이 제각각인지·왜 안 올라가는지 원인 / forecast: 언제 다 되는지·예상 완료 / report: 보고서·요약 문서 / priority: 뭐부터 올려야 하는지

[relative 규칙]
'뒤처진', '아직 안 된', '미적용', 'N 미만' → op=below. '두 칸 이상 뒤처진' → op=steps_below_at_least, steps=2.
'적용된', 'N 이상' → op=at_or_above. 기준 버전을 말하면 target.mode=version 과 versions 에 넣고, '최신'이면 target.mode=latest.

[chart 규칙] 설비 한 대씩·히트맵·어느 설비 → treemap / 비율·몇 퍼센트 → pie / 사이트·라인별 비교 → bar / 변화·추이·날짜별 → timeline / 목록·표 → table / 모르면 auto

[되묻기] 설비군·사이트가 어휘에 없거나(예: 'C5', '구미'), 질문이 '그거', '아까 그 라인'처럼 가리키는 대상이 없으면 need_clarification=true 로 하고 clarification_question 에 한 문장으로 되묻는다. 나머지 필드는 알 수 있는 데까지 채운다.

[예시]
"평택 O2 중 두 칸 이상 뒤처진 설비만 보여줘" → {{"intent":"query","sites":["평택"],"groups":["O2"],"relative":{{"op":"steps_below_at_least","steps":2}},"chart":"treemap"}}
"화성 파운드리 C3 적용률 파이로" → {{"intent":"query","sites":["화성"],"areas":["FOUNDRY"],"groups":["C3"],"chart":"pie"}}
"GAS MIX 인터락 기능 몇 버전부터 들어갔어?" → {{"intent":"item","item_keyword":"GAS MIX 인터락"}}
"P1D 라인 C4 가 왜 버전이 제각각이야" → {{"intent":"why","sites":["평택"],"lines":["P1D"],"groups":["C4"]}}
"KCTC 는 언제 다 올라가?" → {{"intent":"forecast","categories":["KCTC"],"target":{{"mode":"latest"}},"chart":"timeline"}}
"C5 설비 현황" → {{"intent":"query","need_clarification":true,"clarification_question":"'C5' 설비군은 목록에 없습니다. C3, C4, C4 DFB, C4+ 중 어느 것인가요?"}}
"""


def _mock_rule_based(system: str, user: str) -> dict:
    """오프라인 mock: 키워드로 대충 채움 (테스트 전용)."""
    q = user
    out: dict = {"intent": "query", "sites": [], "lines": [], "areas": [], "categories": [], "groups": [],
                 "target": {"mode": "unspecified", "versions": {}}, "relative": {"op": "any", "steps": None}, "chart": "auto"}
    for s in SITES:
        if s in q: out["sites"].append(s)
    if "파운드리" in q: out["areas"].append("FOUNDRY")
    if "메모리" in q: out["areas"].append("MEMORY")
    for cat, words in CATEGORY_WORDS.items():
        if any(w in q for w in words) and not re.search(r"(Inline|Cluster)", q): out["categories"].append(cat)
    for g, syns in GROUP_SYNONYMS.items():
        if any(re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z0-9+])", q) for w in syns):
            out["groups"].append(g)
    if "O2" in out["groups"] and any(g.startswith("KCTC") and g.endswith("O2") for g in out["groups"]):
        out["groups"].remove("O2")
    for l in sorted(LINES, key=len, reverse=True):          # 긴 이름 먼저 (P3-S5 가 S5 보다 먼저)
        if re.search(rf"(?<![A-Za-z0-9\-]){re.escape(l)}(?![A-Za-z0-9\-])", q) and not any(l in x for x in out["lines"]):
            out["lines"].append(l)
    if any(w in q for w in ("왜", "원인")): out["intent"] = "why"
    elif any(w in q for w in ("언제", "예상", "완료")): out["intent"] = "forecast"
    elif "보고서" in q or "요약" in q: out["intent"] = "report"
    elif any(w in q for w in ("뭐부터", "우선순위", "먼저")): out["intent"] = "priority"
    elif any(w in q for w in ("기능", "들어갔")) and "안 깔린" not in q: out["intent"] = "item"; out["item_keyword"] = re.sub(r"\s*(기능|이|가|은|는|들어간).*$", "", q).strip()
    KOR_NUM = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5}
    m = re.search(r"(\d+|한|두|세|네|다섯)\s*칸 이상 뒤처", q)
    if m: out["relative"] = {"op": "steps_below_at_least", "steps": int(m.group(1)) if m.group(1).isdigit() else KOR_NUM[m.group(1)]}
    elif any(w in q for w in ("뒤처", "미적용", "안 된", "미만")): out["relative"] = {"op": "below", "steps": None}
    elif any(w in q for w in ("적용된", "이상")): out["relative"] = {"op": "at_or_above", "steps": None}
    if "최신" in q: out["target"] = {"mode": "latest", "versions": {}}
    mv = re.search(r"(v\d[\w.]*)", q)
    if mv and out["groups"]: out["target"] = {"mode": "version", "versions": {out["groups"][0]: mv.group(1)}}
    if "위험" in q or ("환경안전" in q and "안 깔린" in q): out["risk_only"] = True
    md = re.search(r"(\d{1,2})월\s*(\d{1,2})일", q)
    if md: out["base_date"] = f"2026-{int(md.group(1)):02d}-{int(md.group(2)):02d}"
    if any(w in q for w in ("파이", "비율", "퍼센트")): out["chart"] = "pie"
    elif any(w in q for w in ("추이", "변화", "날짜별")): out["chart"] = "timeline"
    elif "비교" in q or "별로" in q: out["chart"] = "bar"
    elif any(w in q for w in ("설비만", "히트맵", "어느 설비", "트리맵")): out["chart"] = "treemap"
    elif "표" in q or "목록" in q: out["chart"] = "table"
    if re.search(r"C5|구미|그거|아까", q):
        out["need_clarification"] = True; out["clarification_question"] = "대상을 정확히 알려주세요."
    return out


def parse_query(text: str, client: Optional[LLMClient] = None) -> QueryCondition:
    client = client or LLMClient(mock_responder=_mock_rule_based)
    if client.mock_responder is None:
        client.mock_responder = _mock_rule_based
    qc = client.complete_json(SYSTEM_PROMPT, f"질문: {text}\nJSON:", schema=QueryCondition)
    # ---- 코드 쪽 검증: 모르는 설비군·사이트가 걸러졌는데 질문에 설비군 같은 말이 있으면 되묻기
    if not qc.groups and not qc.categories and re.search(r"\b[A-Z]\d\b|설비군", text) and not qc.need_clarification:
        unknown = re.findall(r"\b[A-Z]\d\+?\b", text)
        if unknown and not any(u in GROUP_NAMES for u in unknown):
            qc.need_clarification = True
            qc.clarification_question = f"'{', '.join(unknown)}' 설비군은 목록에 없습니다. {', '.join(GROUP_NAMES[:6])} … 중 어느 것인가요?"
    if qc.intent == "item" and not qc.item_keyword:
        qc.need_clarification = True
        qc.clarification_question = qc.clarification_question or "어떤 기능(아이템)을 찾을지 알려주세요."
    return qc


def to_pretty(qc: QueryCondition) -> str:
    return json.dumps(qc.model_dump(exclude_defaults=True), ensure_ascii=False, indent=1)
