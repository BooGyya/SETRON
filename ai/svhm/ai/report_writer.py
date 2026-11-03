# -*- coding: utf-8 -*-
"""E3. 보고서 문장 생성 · 파편화 원인 분석 — 숫자는 코드가, 글은 AI 가.

입력은 전부 코드가 계산한 값(집계·위험·우선순위·예측·버전별 대수·릴리즈노트 항목 플래그)이고,
AI 는 그 숫자를 '설명'만 한다. 응답의 숫자가 입력에 없는 값이면 경고한다(숫자 지어내기 방지).
출력은 JSON {title, summary, sections[{heading, paragraphs[]}], cautions[]} → Word 변환은 별도.
"""
from __future__ import annotations

import json, re
from typing import Any, Optional

from pydantic import BaseModel, Field

from svhm.ai.llm_client import LLMClient


class ReportSection(BaseModel):
    heading: str
    paragraphs: list[str] = Field(default_factory=list)


class Report(BaseModel):
    title: str
    summary: str
    sections: list[ReportSection] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    number_warnings: list[str] = Field(default_factory=list)   # 코드가 채움


REPORT_SYSTEM = """너는 반도체 설비 SW 버전 관리 담당자를 위한 보고서 작성자다.
아래 [데이터]는 프로그램이 계산한 숫자다. 너는 숫자를 새로 계산하거나 추정하지 않는다. 데이터에 없는 숫자·날짜·설비명을 쓰지 않는다.
한국어, 간결한 업무 문체. 마크다운 없이 JSON 만 출력한다.

출력 스키마:
{"title": "...", "summary": "3문장 이내 핵심 요약",
 "sections": [{"heading": "현황", "paragraphs": ["..."]}, {"heading": "위험 설비", "paragraphs": ["..."]},
              {"heading": "우선순위 권고", "paragraphs": ["..."]}, {"heading": "완료 전망", "paragraphs": ["..."]}],
 "cautions": ["데이터 한계·주의점 (예: 미등록 n대는 확인 필요, 예측은 참고용)"]}
예측 문장에는 반드시 '참고용'과 데이터 장 수를 적는다. 미등록·누락 설비가 있으면 cautions 에 적는다."""

WHY_SYSTEM = """너는 반도체 설비 SW 버전 관리 담당자에게 '왜 같은 설비군·라인에 여러 버전이 섞여 있는지'를 설명하는 분석자다.
아래 [데이터]에는 버전별 설비 수, 각 버전의 출시일, 그 버전으로 올리는 데 필요한 조건(HW 변경 필요 여부, 고객사 연관, 핫픽스 여부), 위험 규칙이 있다.
데이터에 있는 사실만으로 원인을 2~4개 제시하고, 각 원인이 어느 버전·몇 대에 해당하는지 적는다. 데이터에 없는 이유는 '확인 필요'로만 적는다.
숫자·설비명을 지어내지 않는다. 한국어. JSON 만 출력:
{"title": "...", "summary": "...", "sections": [{"heading": "원인 1: ...", "paragraphs": ["..."]}], "cautions": ["..."]}"""


def _numbers_in(obj: Any) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", json.dumps(obj, ensure_ascii=False, default=str)))


def check_numbers(report: Report, data: dict) -> list[str]:
    """응답에 입력 데이터에 없는 숫자가 있으면 경고 (순위 번호·연도 같은 작은 수는 제외)."""
    allowed = _numbers_in(data)
    found = _numbers_in(report.model_dump(exclude={"number_warnings"}))
    bad = sorted(n for n in found - allowed if not (len(n) <= 1 or n.isdigit() and 1 <= int(n) <= 12 and False))
    bad = [n for n in bad if not (n.isdigit() and int(n) <= 10)]   # 1~10 은 '3문장', '2개' 같은 글 숫자일 수 있어 제외
    return [f"입력에 없는 숫자 '{n}' 가 보고서에 등장 — 확인 필요" for n in bad]


def _mock_report(system: str, user: str) -> dict:
    data = json.loads(user.split("[데이터]", 1)[1].strip()) if "[데이터]" in user else {}
    s = data.get("summary", {})
    return {"title": data.get("title", "보고서"),
            "summary": f"대상 {s.get('total', 0)}대 중 {s.get('applied', 0)}대 적용({s.get('rate', 0)}%).",
            "sections": [{"heading": "현황", "paragraphs": [f"업그레이드 필요 {s.get('need_upgrade', 0)}대, 미등록 {s.get('unregistered', 0)}대."]}],
            "cautions": ["예측은 참고용(데이터 %s장)" % data.get("forecast", {}).get("points", 0)]}


def write_report(data: dict, client: Optional[LLMClient] = None, kind: str = "report") -> Report:
    """data 예: {"title":..., "scope":..., "summary":{total,applied,need_upgrade,unregistered,missing,rate},
                "risk":{count, by_rule:[...]} , "priority_top":[...], "forecast":{message, points}, "snapshots":[...]}"""
    client = client or LLMClient(mock_responder=_mock_report)
    if client.mock_responder is None:
        client.mock_responder = _mock_report
    system = WHY_SYSTEM if kind == "why" else REPORT_SYSTEM
    rep = client.complete_json(system, "[데이터]\n" + json.dumps(data, ensure_ascii=False, indent=1, default=str), schema=Report)
    rep.number_warnings = check_numbers(rep, data)
    return rep


def report_to_markdown(rep: Report) -> str:
    out = [f"# {rep.title}", "", rep.summary, ""]
    for s in rep.sections:
        out += [f"## {s.heading}"] + s.paragraphs + [""]
    if rep.cautions:
        out += ["## 주의", *[f"- {c}" for c in rep.cautions], ""]
    if rep.number_warnings:
        out += ["## [검증] 숫자 경고", *[f"- {w}" for w in rep.number_warnings], ""]
    return "\n".join(out)
