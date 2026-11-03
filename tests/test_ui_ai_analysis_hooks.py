"""AI 화면의 원인 설명(BE-019)·참고 예측(BE-020)·Word 보고서(BE-022)·질문 선택지(BE-018) 연결을 검증한다."""

import json

import pytest
from docx import Document
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.analysis.service import build_facts
from setron.infrastructure.ai_client import ExampleChatClient
from setron.ui.analysis_data import analysis_input, choice_parser, forecast_snapshot, query_choices
from setron.ui.app import create_window
from setron.ui.theme import install_theme

REPLY = {"answer": "전체 설비는 확인한 대로입니다.", "evidence": ["대시보드 숫자"], "assumptions": [], "unconfirmed": []}


@pytest.fixture
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


@pytest.fixture
def window(app):
    result = create_window(ai_client=ExampleChatClient([json.dumps(REPLY, ensure_ascii=False)] * 10))
    result.resize(1600, 960)
    result.show()
    result.navigate("dashboard")
    result.query_panel.apply_draft()
    result.navigate("ai")
    QTest.qWait(20)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def test_analysis_input_uses_dashboard_snapshot_numbers(window):
    source = window.pages["dashboard"].source
    snapshot = window.pages["dashboard"].snapshot

    data = analysis_input(source, snapshot)
    facts = build_facts(data)

    assert data.records == snapshot.selected_records
    assert facts["전체 현황"]["설비 수(HPA 제외)"] == snapshot.summary.equipment_count
    assert len(facts["날짜별 추이"]) == len(snapshot.observations)


def test_analyze_uses_explain_with_number_check_then_saves_word(window, tmp_path):
    page = window.pages["ai"]
    assert page.analyze()
    QTest.qWait(20)

    assert page.report is not None and page.report.facts["사업장별 현황"]
    assert page.number_check_label.text().startswith("답변의 숫자가 모두")
    assert page.report_button.isEnabled()

    assert page.run_forecast()
    assert page.forecast_report is not None and "참고용 · 데이터" in page.forecast_label.text()

    result = page.save_report(tmp_path / "보고서.docx")
    assert result.saved
    texts = [p.text for p in Document(str(tmp_path / "보고서.docx")).paragraphs]
    assert "참고 예측" in texts and REPLY["answer"] in texts


def test_unmatched_numbers_are_warned(app, tmp_path):
    reply = {**REPLY, "answer": "적용률은 99.9%입니다."}
    window = create_window(ai_client=ExampleChatClient([json.dumps(reply, ensure_ascii=False)]))
    window.navigate("dashboard")
    window.query_panel.apply_draft()
    page = window.pages["ai"]
    assert page.analyze()
    QTest.qWait(20)

    assert "99.9" in page.number_check_label.text()
    window.close()


def test_forecast_uses_all_dates_up_to_snapshot(window):
    source = window.pages["dashboard"].source
    snapshot = window.pages["dashboard"].snapshot
    reply = json.dumps({"answer": "참고", "evidence": [], "assumptions": [], "unconfirmed": []})

    report = forecast_snapshot(ExampleChatClient([reply]), "언제?", source, snapshot)

    assert report.facts["분석 기간"]["끝"] == snapshot.base_date.isoformat()
    assert report.facts["계열별 예측"]


def test_question_parser_sends_current_choices(window):
    client = ExampleChatClient([json.dumps({"conditions": {}, "unresolved": []})])
    catalog = window.query_state.catalog

    choice_parser(client, lambda: catalog)("화성 C4 보여줘")

    prompt = client.calls[0][0].content
    assert all(label in prompt for label in query_choices(catalog).sites)
