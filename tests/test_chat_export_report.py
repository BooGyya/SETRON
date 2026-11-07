"""대화 답 → Excel 내보내기 자료(10-1) · Word 보고서(10-2·10-3)."""

import base64

from docx import Document
from openpyxl import load_workbook

from setron.features.chat import answer, recognize
from setron.features.chat.answers import report_answers
from setron.features.chat.export import conditions_of, query_export
from setron.features.chat.report import ChatReport, ReportOptions, ReportPart, default_report_title, save_chat_report
from setron.features.export.query_result import ExportRange, QueryColumn, range_counts, save_query_result
from setron.features.export.schemas import ExportStatus
from test_chat_questions import CATALOG, DATA, ask

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")


def test_excel_export_ranges_and_conditions(tmp_path):
    result = ask("평택 O2 중 목표 N 미만인 설비만 보여줘")
    export = query_export(result, DATA)
    assert range_counts(export) == {ExportRange.SHOWN: 2, ExportRange.ALL: 6}
    assert conditions_of(result) == (("Site", "평택"), ("설비군", "M-O2"), ("판정", "목표 N 미만"), ("기준일", "2026-10-21"))
    assert export.conditions == (("Site", "평택"), ("설비군", "M-O2"), ("판정", "목표 N 미만"))      # 기준일은 백엔드가 넣음
    from setron.features.export.query_result import default_filename
    assert default_filename(export) == "AI조회_평택_M-O2_목표N미만_2026-10-21.xlsx"
    path = tmp_path / "조회.xlsx"
    saved = save_query_result(export, path, scope=ExportRange.ALL,
                              columns=[c for c in QueryColumn if c is not QueryColumn.SOURCE])
    assert saved.status is ExportStatus.SAVED
    book = load_workbook(path)
    assert book.sheetnames == ["조회 결과", "조회 조건"]
    assert book["조회 결과"].max_row == 1 + 6 and book["조회 결과"]["A1"].value == "설비명"


def test_word_report_has_question_summary_tables_cause_and_pictures(tmp_path):
    first = ask("평택 O2 중 목표 N 미만인 설비만 보여줘")
    second = ask("목표 N 달성률이 가장 낮은 원본 구역을 분석해줘")
    report = ChatReport("P3 분석", (ReportPart(first), ReportPart(second, (PNG, PNG))))
    path = tmp_path / "보고서.docx"
    assert save_chat_report(report, path).status is ExportStatus.SAVED
    document = Document(path)
    text = "\n".join(p.text for p in document.paragraphs)
    assert "1. 평택 M-O2 목표 N 미만 설비 현황" in text and "2. 목표 N 달성률 가장 낮은 원본 구역 분석" in text
    assert "질문: 평택 O2 중 목표 N 미만인 설비만 보여줘" in text
    assert "원인 분석 (자료 근거)" in text and "업그레이드 우선순위 추천" in text
    assert len(document.inline_shapes) == 2 and len(document.tables) >= 4
    lean = ChatReport("짧게", (ReportPart(second, (PNG,)),), ReportOptions(charts=False, tables=False, cause=False))
    assert save_chat_report(lean, tmp_path / "짧게.docx").status is ExportStatus.SAVED
    short = Document(tmp_path / "짧게.docx")
    assert len(short.inline_shapes) == 0 and len(short.tables) == 1          # 요청 인식 조건 표만


def test_report_rejects_wrong_suffix(tmp_path):
    report = ChatReport("x", (ReportPart(ask("평택 현황 보여줘")),))
    assert save_chat_report(report, tmp_path / "x.pdf").status is ExportStatus.FAILED


def test_monthly_report_sections():
    request = recognize("이번 달 표준화 보고서 만들어줘", CATALOG)
    parts = report_answers(request, DATA)
    assert [p.request.question for p in parts] == ["요약 KPI", "구역별 목표 N 달성률", "원인 분석", "업그레이드 우선순위", "통일 시점 예측"]
    assert parts[0].summary.startswith("전체 설비 12대 · 목표 N 달성률") and parts[1].charts and parts[4].charts
    assert parts[2].cause and any(t.title == "업그레이드 우선순위 추천" for t in parts[3].tables)


def test_report_like_titles_from_request_kind_and_scope():
    titles = {question: ask(question).title for question in (
        "목표 N 달성률이 가장 낮은 원본 구역을 분석해줘", "화성 O2 목표 N 미만 설비 보여줘", "15L과 16L 달성률 비교해줘",
        "지난달보다 달성률이 떨어진 라인은?", "P3 원인 분석해줘", "어디부터 업그레이드해야 해?",
        "C3 통합UI는 언제 통일될까?", "C3 통합UI 최신 버전은?", "이번 달 표준화 보고서 만들어줘", "평택 현황 보여줘")}
    assert list(titles.values()) == [
        "목표 N 달성률 가장 낮은 원본 구역 분석", "화성 M-O2 목표 N 미만 설비 현황", "15L 16L 원본 구역별 목표 N 달성률 비교",
        "목표 N 달성률 변화 분석", "P3 목표 N 미달 원인 분석", "업그레이드 우선순위", "C3 통합UI 버전 통일 시점 예측",
        "C3 통합UI 버전 현황", "SETRON 표준화 보고서 2026-10", "평택 설비 현황"]
    assert default_report_title(ask("목표 N 달성률이 가장 낮은 원본 구역을 분석해줘")) == "목표 N 달성률 가장 낮은 원본 구역 분석"


def test_word_reports_use_korean_font(tmp_path):
    from docx.oxml.ns import qn
    report = ChatReport("한글", (ReportPart(ask("평택 현황 보여줘")),))
    save_chat_report(report, tmp_path / "한글.docx")
    document = Document(tmp_path / "한글.docx")
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "List Bullet"):
        fonts = document.styles[name].element.rPr.find(qn("w:rFonts"))
        assert fonts.get(qn("w:eastAsia")) == "맑은 고딕" and fonts.get(qn("w:eastAsiaTheme")) is None, name
    from setron.features.report.service import build_word_report  # 예전 AI 화면 Word 보고서도 같은 글꼴
    import inspect
    assert "use_korean_font(document)" in inspect.getsource(build_word_report)
