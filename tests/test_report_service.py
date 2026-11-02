"""AI 분석 Word 보고서를 검증한다 (BE-022 완료 조건)."""

import json
from datetime import datetime

import pytest
from docx import Document
from docx.document import Document as DocxDocument

from setron.features.ai.examples import example_client
from setron.features.ai.schemas import AITask
from setron.features.analysis.service import explain
from setron.features.forecast.service import explain_forecast, forecast
from setron.features.report.schemas import WordReport
from setron.features.report.service import save_word_report
from setron.infrastructure.ai_client import ExampleChatClient
from test_analysis_service import data
from test_forecast_service import FAMILIES as FORECAST_FAMILIES
from test_forecast_service import RECORDS as FORECAST_RECORDS
from test_forecast_service import TARGETS as FORECAST_TARGETS

REPLY = {
    "answer": "평택은 위험 설비 1대가 있고 전체 적용률은 40.0%입니다.",
    "evidence": ["평택 위험 설비 1대", "화성 N 미만 2건"],
    "assumptions": ["Interlock 추가가 적용을 늦췄을 수 있습니다."],
    "unconfirmed": [],
}


def client(reply):
    return ExampleChatClient([json.dumps(reply, ensure_ascii=False)])


def analysis(reply=REPLY):
    return explain(client(reply), "사업장별 현황과 위험 알려줘", data())


def forecast_report(answer="O2는 남은 3건을 약 90일 뒤 마칠 것으로 보입니다."):
    result = forecast(FORECAST_RECORDS, FORECAST_FAMILIES, FORECAST_TARGETS)
    reply = {"answer": answer, "evidence": [], "assumptions": [], "unconfirmed": []}
    return explain_forecast(client(reply), "언제 끝나?", result, {10: "O2", 20: "O3"})


def saved(tmp_path, **overrides):
    values = dict(question="사업장별 현황과 위험 알려줘", analysis=analysis(), created_at=datetime(2026, 11, 3, 10, 0))
    values.update(overrides)
    path = tmp_path / "보고서.docx"
    result = save_word_report(WordReport(**values), path)
    assert result.saved, result.message
    return Document(str(path))


def paragraphs(document: DocxDocument) -> list[str]:
    return [p.text for p in document.paragraphs]


def table(document: DocxDocument, first_header: str, second_header: str) -> list[list[str]]:
    for t in document.tables:
        header = [c.text for c in t.rows[0].cells]
        if header[:2] == [first_header, second_header]:
            return [[c.text for c in row.cells] for row in t.rows[1:]]
    raise AssertionError(f"표가 없습니다: {first_header}, {second_header}")


def test_sections_are_in_order(tmp_path):
    headings = [p.text for p in saved(tmp_path).paragraphs if p.style.name.startswith(("Heading", "Title"))]

    assert headings[:4] == ["SETRON AI 분석 보고서", "질문과 조회 조건", "분석 결과", "근거 (자료로 확인한 내용)"]
    assert headings[-2:] == ["위험", "숫자 확인"]


def test_source_question_and_conditions_are_written(tmp_path):
    document = saved(tmp_path)

    assert "자료 출처: 예제 응답 (실제 AI 결과 아님)" in paragraphs(document)
    assert "질문: 사업장별 현황과 위험 알려줘" in paragraphs(document)
    assert ["기준일", "2026-10-21"] in table(document, "항목", "값")
    assert table(document, "계열", "목표 N") == [["O2", "v2.3"], ["O3", "v3.1"]]


def test_answer_is_split_into_evidence_assumptions_and_unconfirmed(tmp_path):
    texts = paragraphs(saved(tmp_path))

    assert REPLY["answer"] in texts
    evidence = texts.index("근거 (자료로 확인한 내용)")
    assert texts[evidence + 1:evidence + 3] == REPLY["evidence"]
    assert texts[texts.index("추정") + 1] == REPLY["assumptions"][0]
    assert texts[texts.index("미확인 (자료로 확인할 수 없는 내용)") + 1] == "없음"


def test_number_tables_copy_the_facts_sent_to_ai(tmp_path):
    document = saved(tmp_path)
    facts = analysis().facts

    sites = table(document, "사업장", "설비 수(HPA 제외)")
    pyeongtaek = next(s for s in facts["사업장별 현황"] if s["사업장"] == "평택")
    assert sites[0] == ["평택", str(pyeongtaek["설비 수(HPA 제외)"]), str(pyeongtaek["N 이상"]),
                        str(pyeongtaek["N 미만"]), str(pyeongtaek["판단 불가"]), str(pyeongtaek["N 이상 적용률(%)"])]
    assert table(document, "사업장", "위험 설비") == [["평택", "1", "1"], ["화성", "1", "0"]]
    assert "가상 결함 버전: DM-002, DM-005" in paragraphs(document)


def test_matching_numbers_are_confirmed(tmp_path):
    assert "AI 답변의 숫자가 모두 보낸 자료에 있습니다." in paragraphs(saved(tmp_path))


def test_numbers_not_in_facts_are_warned(tmp_path):
    reply = {**REPLY, "answer": "전체 적용률은 55%입니다."}

    texts = paragraphs(saved(tmp_path, analysis=analysis(reply)))

    assert any(t.startswith("AI 답변에 보낸 자료에 없는 숫자가 있습니다") and t.endswith("55") for t in texts)


def test_forecast_section_has_reference_note_table_and_limits(tmp_path):
    document = saved(tmp_path, forecast=forecast_report())
    texts = paragraphs(document)

    assert "참고 예측" in texts
    assert "예측은 저장된 현황으로 계산한 참고용 값이며 실제 완료 시점을 보장하지 않습니다." in texts
    o2 = table(document, "계열", "상태")[0]
    assert o2 == ["O2", "참고 예측", "1", "2", "3", "0", "90.0", "2026-12-30"]
    assert texts[texts.index("가정") + 1].startswith("기간 동안의 기존 설비 패치 속도")
    assert "O2는 남은 3건을 약 90일 뒤 마칠 것으로 보입니다." in texts


def test_forecast_section_is_omitted_without_forecast(tmp_path):
    assert "참고 예측" not in paragraphs(saved(tmp_path))


def test_ai_failure_keeps_numbers_and_explains_failure(tmp_path):
    failed = explain(example_client(AITask.ANALYSIS, "timeout"), "질문", data())

    document = saved(tmp_path, analysis=failed)
    texts = paragraphs(document)

    assert any(t.startswith("AI 분석을 받지 못했습니다") for t in texts)
    assert "자료 출처: AI 응답 없음" in texts
    assert table(document, "사업장", "위험 설비")


def test_file_in_use_reports_reason(tmp_path, monkeypatch):
    def locked(self, path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(DocxDocument, "save", locked)
    result = save_word_report(WordReport(question="q", analysis=analysis()), tmp_path / "보고서.docx")

    assert not result.saved and "Word에서 열려 있는지" in result.message


@pytest.mark.parametrize("name", ["보고서.pdf", "보고서.doc"])
def test_non_docx_name_is_rejected(tmp_path, name):
    result = save_word_report(WordReport(question="q", analysis=analysis()), tmp_path / name)

    assert not result.saved and ".docx" in result.message
