"""C-4: 질문 조건의 정확한 선택지 대조, 미지원 조건 보존, 확인 후 적용·이동, 근거·추정·미확인 분리."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.ai.examples import example_client
from setron.features.ai.schemas import AIResult, AIStatus, AITask, AnalysisAnswer, QueryConditions, TargetView
from setron.features.ai.service import analyze, parse_question
from setron.infrastructure.ai_client import ResponseSource
from setron.ui.ai_data import ConditionStatus, check_apply, facts_from_snapshot, interpret, selection_for
from setron.ui.app import create_window
from setron.ui.query_state import FabOption, GroupOption, LineOption, QueryCatalog, QuerySelection, SiteOption
from setron.ui.theme import install_theme

CATALOG = QueryCatalog(
    sites=(SiteOption("hs", "화성"), SiteOption("pt", "평택")),
    fabs=(FabOption("f1", "Fab1", "hs"),),
    lines=(LineOption("hs-l1", "L1", "hs", "f1"), LineOption("pt-l1", "L1", "pt", None)),
    groups=(GroupOption("c4", "C4", "POLY"),),
)


def statuses(rows):
    return [(r.dimension, r.raw, r.status) for r in rows]


def test_only_exact_options_match_and_line_is_not_inferred():
    rows = interpret(QueryConditions(sites=("화성",), lines=("L1",), groups=("C-4",)), CATALOG)
    assert statuses(rows) == [("Site", "화성", ConditionStatus.MATCHED), ("Line", "L1", ConditionStatus.MATCHED),
                              ("설비군", "C-4", ConditionStatus.NOT_FOUND)]
    assert rows[1].key == "hs-l1"  # 같은 이름이어도 Site에 맞는 Line만
    ambiguous = interpret(QueryConditions(lines=("L1",)), CATALOG)
    assert ambiguous[0].status is ConditionStatus.NOT_FOUND and "여러 개" in ambiguous[0].message
    raw_area = interpret(QueryConditions(lines=("P1",)), CATALOG)
    assert "추정하지 않습니다" in raw_area[0].message


def test_multiple_dates_and_unsupported_items_are_kept_not_shortened():
    rows = interpret(QueryConditions(sites=("화성", "평택"), models=("M-C4",), versions=("v1",), items=("12",),
                                     base_date="2026-10-21", unresolved=("3층",)), CATALOG)
    assert [r.status for r in rows] == [ConditionStatus.MULTIPLE] * 2 + [ConditionStatus.UNSUPPORTED] * 4 + [ConditionStatus.UNRESOLVED]
    everything = set(range(len(rows)))
    assert "적용할 수 없는 조건 7개" in check_apply(rows, everything)
    with pytest.raises(ValueError):
        selection_for(rows, everything)
    assert check_apply(rows, set()) == "적용할 조건이 없습니다."


def test_selection_uses_only_user_included_matched_rows():
    rows = interpret(QueryConditions(sites=("화성",), groups=("C4",), versions=("v1",)), CATALOG)
    assert check_apply(rows, {0, 1, 2}) is not None
    assert selection_for(rows, {0, 1}) == QuerySelection(site_key="hs", group_key="c4")
    assert selection_for(rows, {1}) == QuerySelection(group_key="c4")


@pytest.fixture
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


@pytest.fixture
def calls():
    return {"analysis": []}


@pytest.fixture
def window(app, calls):
    def analyzer(question, facts):
        calls["analysis"].append(facts)
        return AIResult(AITask.ANALYSIS, AIStatus.OK, "ok",
                        AnalysisAnswer("요약", ("근거 A",), ("추정 B",), ("미확인 C",)), ResponseSource.REAL)
    result = create_window(ai_analyzer=analyzer)
    result.resize(1600, 960)
    result.show()
    result.navigate("ai")
    QTest.qWait(20)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


@pytest.fixture
def page(window):
    result = window.pages["ai"]
    result.confirms = []
    result.confirm = lambda text: result.confirms.append(text) or True
    return result


def ask(page, question="화성 P1D C4 버전 보여줘"):
    page.question.setText(question)
    assert page.parse()
    QTest.qWait(10)


def uncheck_blocked(page):
    for index, row in enumerate(page.rows):
        if row.status is not ConditionStatus.MATCHED:
            page.condition_list.item(index).setCheckState(Qt.CheckState.Unchecked)


def test_parse_shows_example_source_and_changes_nothing(window, page):
    draft, applied = window.query_state.draft, window.query_state.applied
    ask(page)
    assert "EXAMPLE" in page.parse_badge.text() and "실제 AI 결과 아님" in page.parse_badge.text()
    assert statuses(page.rows) == [("Site", "화성", ConditionStatus.MATCHED), ("Line", "P1D", ConditionStatus.NOT_FOUND),
                                   ("설비군", "C4", ConditionStatus.MATCHED), ("버전", "v4.56.6.43", ConditionStatus.UNSUPPORTED)]
    assert window.query_state.draft == draft and window.query_state.applied is applied
    assert not page.apply_button.isEnabled() and "직접 제외" in page.apply_reason.text()
    assert not page.apply_conditions() and page.confirms == []
    assert "대시보드로 이동" in page.view_label.text()


def test_confirmed_conditions_are_applied_and_screen_uses_same_conditions(window, page):
    targets = window.query_state.target_draft
    ask(page)
    uncheck_blocked(page)
    assert page.apply_button.isEnabled()
    assert page.apply_conditions()
    assert "Line · P1D" in page.confirms[0] and "버전 · v4.56.6.43" in page.confirms[0]
    query = window.query_state.applied
    assert query.selection == QuerySelection(site_key="hwaseong", group_key="c4")
    assert query.targets == targets and window.query_state.target_draft == targets
    assert window.route == "dashboard"
    dashboard = window.pages["dashboard"]
    assert dashboard.snapshot.query is query  # 답변 조건과 이동한 화면 조건이 같다
    assert page.applied_query is query and f"적용 #{query.revision}" in page.apply_result.text()


def test_cancel_confirmation_keeps_query(window, page):
    ask(page)
    uncheck_blocked(page)
    page.confirm = lambda text: False
    assert not page.apply_conditions()
    assert window.query_state.applied is None and window.route == "ai"


def test_pending_target_change_or_catalog_change_blocks_apply(window, page):
    window.navigate("dashboard")
    window.query_panel.apply_draft()
    window.navigate("ai")
    ask(page)
    uncheck_blocked(page)
    assert page.apply_button.isEnabled()
    window.query_panel.set_target_draft(None)
    assert not page.apply_button.isEnabled() and "목표 N" in page.apply_reason.text()
    window.query_panel.set_target_draft(window.query_state.applied.targets)
    assert page.apply_button.isEnabled()
    window.query_panel.set_catalog(window.query_state.catalog)
    assert not page.apply_button.isEnabled() and "다시 해석" in page.apply_reason.text()


def test_parse_failure_and_stale_reply(window, page):
    page.parser = lambda question: parse_question(example_client(AITask.QUERY_PARSE, "rate_limit"), question)
    ask(page)
    assert page.status.tone == "danger" and "호출 제한" in page.notice.text() and page.rows == ()
    page.parser = lambda question: AIResult(AITask.QUERY_PARSE, AIStatus.OK, "ok",
                                            QueryConditions(groups=(question,), target_view=TargetView.HEATMAP), ResponseSource.REAL)
    page.question.setText("C4")
    assert page.parse()
    # 응답 전에는 새 해석·분석 요청을 받지 않아 결과가 섞이지 않는다.
    assert not page.parse() and not page.analyze() and not page.parse_button.isEnabled()
    QTest.qWait(10)
    assert "REAL" in page.parse_badge.text() and page.rows[0].status is ConditionStatus.MATCHED
    page.apply_conditions()
    assert window.route == "heatmap" and window.query_state.applied.selection == QuerySelection(group_key="c4")


def test_analysis_uses_same_numbers_as_dashboard_and_separates_sections(window, page, calls):
    assert not page.analyze_button.isEnabled() and not page.analyze()
    window.navigate("dashboard")
    window.query_panel.apply_draft()
    window.navigate("ai")
    assert page.analyze_button.isEnabled()
    snapshot = window.pages["dashboard"].snapshot
    assert page.analyze()
    QTest.qWait(10)
    facts = calls["analysis"][0]
    assert facts == facts_from_snapshot(snapshot)
    assert facts["설비 수(HPA 제외)"] == snapshot.summary.equipment_count and facts["적용 번호"] == snapshot.query.revision
    assert str(snapshot.summary.equipment_count) in page.facts_label.text()
    assert page.evidence_label.text() == "• 근거 A"
    assert page.assumption_label.text() == "• 추정 B" and page.unconfirmed_label.text() == "• 미확인 C"
    assert "REAL" in page.analysis_badge.text()
    window.query_panel.set_catalog(window.query_state.catalog)  # 적용 결과가 더 이상 현재가 아님
    assert not page.analyze_button.isEnabled()


def test_example_analysis_evidence_is_not_presented_as_verified(window, page):
    page.analyzer = lambda question, facts: analyze(example_client(AITask.ANALYSIS), question, facts)
    window.navigate("dashboard")
    window.query_panel.apply_draft()
    window.navigate("ai")
    page.analyze()
    QTest.qWait(10)
    assert "EXAMPLE" in page.analysis_badge.text()
    assert "확인한 근거가 아닙니다" in page.evidence_label.text()


@pytest.mark.parametrize("size", [(1600, 960), (1100, 800), (960, 600)])
def test_responsive_without_main_horizontal_scroll(window, page, size):
    ask(page)
    window.resize(*size)
    QTest.qWait(60)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert page.condition_list.width() <= page.width()
