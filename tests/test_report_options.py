"""보고서로 저장 창(화면 10-2)의 제목·포함 항목·그래프 그림 옵션을 검증한다 (BE-022)."""

import base64
from datetime import datetime

from docx import Document

from setron.features.aging.schemas import DEFAULT_THRESHOLDS
from setron.features.priority.service import prioritize
from setron.features.report.schemas import DEFAULT_SECTIONS, PreviousAnswer, ReportSection, WordReport
from setron.features.report.service import available_sections, build_word_report, default_title, save_word_report
from setron.features.risk.service import RiskAssessor
from test_analysis_service import CATALOG, FAMILIES, NAMES, RECORDS, TARGETS
from test_export_service import DIRECT_V21
from test_report_service import analysis, forecast_report, paragraphs, table

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
QUESTION = "사업장별 현황과 위험 알려줘"


def word(**overrides):
    values = dict(question=QUESTION, analysis=analysis(), created_at=datetime(2026, 11, 6, 9, 0))
    values.update(overrides)
    return WordReport(**values)


def headings(document):
    return [p.text for p in document.paragraphs if p.style.name.startswith(("Heading", "Title"))]


def priority():
    assessor = RiskAssessor(FAMILIES, CATALOG, [DIRECT_V21])
    return prioritize(RECORDS, assessor, FAMILIES, DEFAULT_THRESHOLDS, targets=TARGETS)


def test_default_sections_skip_previous_and_list_only_available_items():
    report = word()

    assert ReportSection.PREVIOUS not in DEFAULT_SECTIONS
    assert available_sections(report) == (ReportSection.SUMMARY, ReportSection.LOCATION_RATES, ReportSection.CAUSE)
    texts = paragraphs(build_word_report(report))
    assert "포함 항목: 요약 · 요청 인식 조건, 원본 구역별 목표 N 달성률, 원인 분석" in texts


def test_forecast_priority_and_previous_become_available_when_given():
    report = word(forecast=forecast_report(), priority=priority(),
                  previous=(PreviousAnswer("평택 O2 목표 N 미만 설비", analysis()),))

    assert available_sections(report) == tuple(ReportSection)


def test_unchecking_cause_keeps_answer_but_drops_details_and_risk():
    document = build_word_report(word(sections=(ReportSection.SUMMARY,)))

    names = headings(document)
    assert names[1:3] == ["질문과 조회 조건", "분석 결과"]
    assert "근거 (자료로 확인한 내용)" not in names and "위험" not in names and "사업장별 현황" not in names
    assert "전체 현황" in names and names[-1] == "숫자 확인"


def test_cause_only_omits_question_section():
    names = headings(build_word_report(word(sections=(ReportSection.CAUSE,))))

    assert "질문과 조회 조건" not in names and "전체 현황" not in names
    assert {"분석 결과", "근거 (자료로 확인한 내용)", "위험"} <= set(names)


def test_without_summary_and_cause_there_is_no_answer_section():
    names = headings(build_word_report(word(sections=(ReportSection.LOCATION_RATES,))))

    assert names == ["SETRON AI 분석 보고서", "사용한 수치", "사업장별 현황", "숫자 확인"]


def test_priority_table_lists_ranked_equipment_and_needs_check():
    expected = priority()
    document = build_word_report(word(priority=expected, family_names=NAMES, sections=(ReportSection.PRIORITY,)))

    rows = table(document, "순위", "설비")
    assert [r[1] for r in rows] == [e.record.equipment_code for e in expected.ranked]
    first = expected.ranked[0]
    assert rows[0][2] == NAMES[first.lowest.pm.family_id] and rows[0][5] == first.risk.label
    if expected.needs_check:
        assert [r[0] for r in table(document, "설비", "위험")] == [e.record.equipment_code for e in expected.needs_check]


def test_selected_item_without_data_is_skipped():
    names = headings(build_word_report(word(sections=(ReportSection.PRIORITY, ReportSection.PREVIOUS))))

    assert names == ["SETRON AI 분석 보고서", "숫자 확인"]


def test_previous_answers_are_added_only_when_chosen():
    previous = (PreviousAnswer("평택 O2 목표 N 미만 설비", analysis()),)

    assert "이전 질문" not in headings(build_word_report(word(previous=previous)))
    names = headings(build_word_report(word(previous=previous, sections=(*DEFAULT_SECTIONS, ReportSection.PREVIOUS))))
    start = names.index("이전 질문")
    assert names[start + 1] == "질문: 평택 O2 목표 N 미만 설비"
    assert names[-1] == "숫자 확인" and start < len(names) - 1


def test_screen_chart_images_are_embedded_under_tables(tmp_path):
    path = tmp_path / "그림.docx"
    report = word(charts={ReportSection.LOCATION_RATES: PNG, ReportSection.CAUSE: PNG})

    assert save_word_report(report, path).saved
    assert len(Document(str(path)).inline_shapes) == 1  # 원인 분석은 그림 자리가 없어 넣지 않는다


def test_broken_chart_image_is_reported_not_embedded():
    document = build_word_report(word(charts={ReportSection.LOCATION_RATES: b"not png"}))

    assert len(document.inline_shapes) == 0
    assert "원본 구역별 목표 N 달성률 그래프 그림을 읽을 수 없어 넣지 못했습니다." in paragraphs(document)


def test_title_is_written_and_default_title_uses_question_and_base_date():
    document = build_word_report(word(title="P3 목표 N 달성률 분석"))

    assert headings(document)[0] == "P3 목표 N 달성률 분석"
    base = analysis().facts["조회 조건"]["기준일"]
    assert default_title(QUESTION, analysis()) == f"{QUESTION} ({base})"
    long = default_title("가" * 60, analysis())
    assert long.startswith("가" * 39 + "…")
