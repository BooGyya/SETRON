"""대시보드 조회 결과를 AI 원인 설명·참고 예측·Word 보고서 입력으로 바꾼다 (BE-019·020·022 화면 연결).

화면과 같은 숫자를 쓰도록 이미 계산된 대시보드 스냅샷과 공급자 자료만 옮기고 다시 추정하지 않는다.
- 설비·목표 N·추이: 스냅샷의 선택 설비, 계산에 쓴 목표 N, 날짜별 관측
- 위험·Item: DB 공급자가 함께 둔 판정기·Item 자료. 합성 예시 공급자에는 없으므로 '미계산'으로 보낸다.
- 계열 이름: DB 공급자의 릴리즈 목록 이름. 없으면 "계열 n"으로 적는다.
"""

from __future__ import annotations

from setron.features.ai.schemas import QueryChoices
from setron.features.analysis.schemas import AnalysisInput, AnalysisReport
from setron.features.analysis.service import explain
from setron.features.forecast.service import explain_forecast, forecast
from setron.infrastructure.ai_client import ChatClient


def snapshot_targets(snapshot) -> dict[tuple[int, int], int]:
    """스냅샷 계산에 실제로 쓴 목표 N (모델 ID, 계열 ID) → 릴리즈 ID."""
    return {(t.model_id, t.family_id): t.release_id for t in snapshot.target_values if t.release_id is not None}


def family_names(source) -> dict[int, str]:
    catalog = getattr(source, "version_catalog", None)
    names = {pair.family_id: pair.family_name for pair in getattr(catalog, "pairs", ())}
    return {family_id: names.get(family_id, f"계열 {family_id}") for family_id in source.families}


def analysis_input(source, snapshot) -> AnalysisInput:
    """같은 조회 조건·기준일의 원인 설명 입력."""
    catalog = getattr(source, "version_catalog", None)
    return AnalysisInput(
        records=tuple(snapshot.selected_records),
        families=source.families,
        targets=snapshot_targets(snapshot),
        family_names=family_names(source),
        scope=(("조회 조건", snapshot.query.description), ("기준일", snapshot.base_date.isoformat())),
        assessor=getattr(source, "assessor", None),
        item_catalog=getattr(source, "item_catalog", None),
        items=tuple(entry.item for entry in getattr(catalog, "items", ())),
        trend=tuple(point.summary for point in snapshot.observations),
        # 같은 조회 범위의 기준일 이하 관측으로 버전 변경 이력을 만든다 (BE-019).
        history=_history(source, snapshot),
    )


def _history(source, snapshot):
    scoped = getattr(source, "scoped_records", None)
    if scoped is None:
        return ()
    return tuple(r for r in scoped(snapshot.query) if r.base_date <= snapshot.base_date)


def explain_snapshot(client: ChatClient, question: str, source, snapshot) -> AnalysisReport:
    """원인 설명을 요청하고 답변 숫자를 보낸 자료와 대조한다 (BE-019)."""
    return explain(client, question, analysis_input(source, snapshot))


def forecast_snapshot(client: ChatClient, question: str, source, snapshot) -> AnalysisReport:
    """같은 조회 조건의 모든 관측일로 참고 예측을 계산하고 설명을 요청한다 (BE-020).

    기간 끝은 스냅샷 기준일이다. 기준일 뒤에 저장된 관측은 쓰지 않는다.
    """
    records = source.scoped_records(snapshot.query)
    result = forecast(records, source.families, snapshot_targets(snapshot), end=snapshot.base_date)
    return explain_forecast(client, question, result, family_names(source))


def query_choices(catalog) -> QueryChoices:
    """질문 해석 때 AI가 고를 수 있는 현재 화면 선택지 (BE-018)."""
    return QueryChoices(
        sites=tuple(o.label for o in catalog.sites),
        lines=tuple(o.label for o in catalog.lines),
        groups=tuple(o.label for o in catalog.groups),
    )


def analysis_hooks(source, snapshot_provider, client: ChatClient | None = None) -> dict:
    """AI 화면에 넘길 원인 설명·예측·보고서 저장 함수. ``client``가 없으면 예제 응답을 쓴다."""
    from setron.features.ai.examples import example_client
    from setron.features.ai.schemas import AITask
    from setron.features.report.service import save_word_report

    def chat():
        return client if client is not None else example_client(AITask.ANALYSIS)

    return {
        "explainer": lambda question, snapshot: explain_snapshot(chat(), question, source, snapshot),
        "forecaster": lambda question, snapshot: forecast_snapshot(chat(), question, source, snapshot),
        "snapshot_provider": snapshot_provider,
        "report_saver": lambda report, path: save_word_report(report, path),
    }


def choice_parser(client: ChatClient, catalog_provider):
    """질문 해석 때 현재 화면 선택지를 함께 보내는 해석 함수 (BE-018)."""
    from setron.features.ai.service import parse_question

    return lambda question: parse_question(client, question, query_choices(catalog_provider()))
