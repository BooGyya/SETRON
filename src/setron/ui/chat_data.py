"""AI 분석 대화의 자료 연결 (VER4 와이어프레임 10-1 ~ 10-5).

대시보드와 같은 자료 출처(source)·목표 N·저장한 N 그룹·위험 판정으로 답을 만들어, 숫자가 대시보드와 같다.
질문 이해는 규칙으로만 한다(같은 질문 → 같은 조건). AI는 원인 설명과 요약 문장 다듬기에만 쓴다.
대시보드 조건은 바꾸지 않는다(읽기 전용).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace

from setron.features.analysis.service import explain
from collections import Counter

from setron.features.chat import ChatAnswer, ChatData, ChatRequest, answer, polish, recognize
from setron.features.chat.answers import SUGGESTIONS, josa, latest_until, report_answers
from setron.features.chat.classify import OUT_OF_SCOPE_MESSAGE, OUT_OF_SCOPE_TITLE, classify
from setron.features.chat.recognize import is_smalltalk
from setron.features.chat.export import query_export
from setron.features.query.schemas import FamilyChoice, GroupChoice, QueryCatalog
from setron.features.targets.groups import with_saved_groups
from setron.ui.analysis_data import family_names


def chat_catalog(source) -> QueryCatalog:
    """자료 출처의 설비 기록에서 엔진 선택지를 만든다. 사업장·라인은 기록에 적힌 그대로 쓴다."""
    records = source.records
    models: dict[int, str] = {}
    for single_first in (True, False):          # 혼합 설비(모델 2개)보다 모델 1개짜리 기록의 이름을 먼저 쓴다
        for record in records:
            if (len(record.pms) == 1) != single_first:
                continue
            for pm in record.pms:
                models.setdefault(pm.group_id, record.model_name)
    owners: dict[int, int] = {}
    for record in records:
        for pm in record.pms:
            owners.setdefault(pm.family_id, pm.group_id)
    names = _names(source, models, owners)
    groups = tuple(GroupChoice(group_id, re.sub(r"^M-", "", name), name, "") for group_id, name in sorted(models.items()))
    families = tuple(FamilyChoice(family_id, names.get(family_id, f"계열 {family_id}"), owners[family_id])
                     for family_id in sorted(source.families) if family_id in owners)
    return QueryCatalog(sites=tuple(sorted({r.site for r in records if r.site})),
                        lines=tuple(sorted({r.line for r in records if r.line})),
                        groups=groups, families=families, dates=tuple(source.dates), releases=dict(source.families))


def _names(source, models: dict[int, str], owners: dict[int, int]) -> dict[int, str]:
    """계열 이름. 저장된 이름이 없으면('계열 1002') 그 계열을 쓰는 모델 이름으로 보여 준다."""
    names = dict(family_names(source))
    for family_id, name in names.items():
        if name.startswith("계열 ") and owners.get(family_id) in models:
            names[family_id] = models[owners[family_id]]
    return names


class ChatService:
    """질문 하나 → 답 묶음. 화면 밖(워커)에서 부른다.

    param
    - source: 대시보드 자료 출처 (``DashboardSource``).
    - targets_provider: 지금 적용된 목표 N(모델·계열 → 릴리즈 ID). 저장한 N 그룹은 이 위에 덮어 쓴다.
    - client: 회사 AI 클라이언트. 없으면 원인은 자료 근거 문장, 요약은 엔진 문장 그대로.
    """

    def __init__(self, source, targets_provider: Callable[[], dict], client=None):
        self.source, self.targets_provider, self.client = source, targets_provider, client

    def data(self) -> ChatData:
        source = self.source
        targets = with_saved_groups(dict(self.targets_provider() or {}), getattr(source, "saved_groups", {}) or {})
        explainer = (lambda question, data: explain(self.client, question, data)) if self.client is not None else None
        catalog = chat_catalog(source)
        return ChatData(records=tuple(source.records), catalog=catalog, targets=targets,
                        family_names={f.family_id: f.name for f in catalog.families}, explainer=explainer,
                        risks=dict(getattr(source, "risks", {}) or {}))

    @property
    def connected(self) -> bool:
        """회사 AI가 연결돼 있으면 원인 설명·요약 다듬기를 쓴다."""
        return self.client is not None

    def overview(self) -> str:
        """처음 화면의 자료 요약 (예: '기준일 2026-10-21 · 설비 300대 · 평택·화성 · 기준일 6개')."""
        records = tuple(self.source.records)
        if not records:
            return "저장된 설비 현황이 없습니다. 데이터 가져오기에서 현황표를 먼저 넣어 주세요."
        latest = max(r.base_date for r in records)
        current = latest_until(records, latest)          # 대시보드와 같은 기준: 설비별 최신 관측
        sites = sorted({r.site for r in current if r.site})
        dates = len({r.base_date for r in records})
        return (f"기준일 {latest.isoformat()} · 설비 {len({r.equipment_id for r in current})}대 · {'·'.join(sites)}"
                + (f" · 기준일 {dates}개" if dates > 1 else ""))

    def starters(self) -> tuple[tuple[str, str, tuple[str, str]], ...] | None:
        """처음 화면 예시 질문을 지금 자료로 만든다 (누르면 항상 답이 나오게).

        가장 설비가 많은 사업장·그 사업장의 라인 2개·가장 많은 모델·가장 많은 계열 이름을 쓴다. 자료가 없으면 None.
        """
        records = [r for r in self.source.records if not r.is_hpa]
        if not records:
            return None
        latest = max(r.base_date for r in records)
        current = latest_until(records, latest)
        site = Counter(r.site for r in current if r.site).most_common(1)[0][0]
        here = [r for r in current if r.site == site]
        lines = [line for line, _ in Counter(r.line for r in here if (r.line or "").strip(" -")).most_common(2)]
        catalog = chat_catalog(self.source)
        group_names = {g.group_id: g.name for g in catalog.groups}
        family_names_ = {f.family_id: f.name for f in catalog.families}
        model = Counter(group_names.get(pm.group_id) for r in here for pm in r.pms).most_common(1)[0][0]
        family = Counter(family_names_.get(pm.family_id) for r in current for pm in r.pms).most_common(1)[0][0]
        search = f"{site} {lines[0]} 목표 N 미만 설비 보여줘" if lines else f"{site} 목표 N 미만 설비 보여줘"
        compare = (f"{josa(lines[0], '과/와')} {lines[1]} 달성률 비교해줘" if len(lines) > 1
                   else "사업장별 달성률 비교해줘")
        return (("조회", "설비 목록·대수", (search, f"{site} {model} 설비 몇 대야?")),
                ("비교·변화", "구역별 달성률·지난 기준일 대비", (compare, "지난달보다 달성률이 떨어진 라인은?")),
                ("원인·우선순위", "왜 낮은지·어디부터 올릴지", ("목표 N 달성률이 가장 낮은 원본 구역을 분석해줘", "어디부터 업그레이드해야 해?")),
                ("예측·보고서", "통일 시점·표준화 보고서", (f"{josa(family, '은/는')} 언제 통일될까?", "이번 달 표준화 보고서 만들어줘")))

    reports_progress = True          # 화면 대기 카드에 실제 진행 단계를 알려 준다

    def ask(self, question: str, previous: ChatRequest | None = None, progress=None) -> ChatAnswer:
        """질문 → 답. 규칙으로 먼저 이해하고, 못 알아들은 질문만 AI에게 '요청 종류·조건' 분류를 부탁한다.

        AI는 분류만 한다. 숫자·표는 언제나 DB 계산이고, AI가 바꿔 준 질문도 규칙이 다시 확인한다.

        param
        - progress: 실제 단계가 끝날 때마다 끝난 단계 수를 받는 함수 (워커 스레드에서 불림).
          2 = 질문 이해·데이터 조회 끝, 4 = 표·그래프·원인·우선순위 계산 끝(이제 답변 작성).
        """
        step = progress or (lambda done: None)
        data = self.data()
        request = recognize(question, data.catalog, previous=previous)
        interpreted = ""
        if not request.understood and self.client is not None and not is_smalltalk(question):
            guess = classify(self.client, question, data.catalog)
            if guess is not None and guess.out_of_scope:
                return ChatAnswer(request, OUT_OF_SCOPE_TITLE, OUT_OF_SCOPE_MESSAGE, suggestions=SUGGESTIONS[:4],
                                  title=OUT_OF_SCOPE_TITLE)
            if guess is not None and guess.question:
                retry = recognize(guess.question, data.catalog, previous=previous)
                if retry.understood:
                    request, interpreted = replace(retry, question=question), guess.question
        step(2)                                            # 질문 이해 · 데이터 조회 끝
        result = answer(request, data)
        step(4)                                            # 표 · 그래프 · 원인 · 우선순위 끝 → 답변 작성
        if interpreted:
            result = replace(result, notices=(f"질문을 ‘{interpreted}’(으)로 이해했어요.", *result.notices))
        if not request.understood:
            return result                                   # 정해진 안내 문장은 AI로 다듬지 않는다
        return polish(result, self.client)

    def export_data(self, result: ChatAnswer):
        """답 → Excel 내보내기 자료. 원본 출처(셀 위치)는 자료 출처에 없어 비워 둔다."""
        return query_export(result, self.data())

    def report_parts(self, result: ChatAnswer) -> tuple[ChatAnswer, ...]:
        """'보고서 만들어줘' 답 → 보고서 각 장(요약 KPI·구역별 달성률·원인·우선순위·통일 예측)."""
        return report_answers(result.request, self.data())
