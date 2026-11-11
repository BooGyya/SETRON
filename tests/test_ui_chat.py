"""AI 분석 대화 화면 (ui.chat_page) — 질문·답 쌓기, 이어 묻기, 중지, 자료 변경, 저장 중 잠금, 실제 앱 연결."""

import threading
from time import monotonic

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.chat import answer, recognize
from setron.ui.app import create_window
from setron.ui.chat_data import chat_catalog
from setron.ui.chat_page import AnswerCard, ChatPage
from setron.ui.theme import install_theme
from test_chat_questions import CATALOG, DATA


class EngineService:
    """테스트용: 질문 모음과 같은 시연 모양 자료로 엔진을 그대로 돌린다. gate가 있으면 열릴 때까지 기다린다."""

    def __init__(self, gate=None):
        self.gate, self.calls = gate, []

    def ask(self, question, previous=None):
        self.calls.append(question)
        if self.gate is not None:
            self.gate.wait(5)
        return answer(recognize(question, CATALOG, previous=previous), DATA)

    def export_data(self, result):
        from setron.features.chat.export import query_export
        return query_export(result, DATA)

    def report_parts(self, result):
        from setron.features.chat.answers import report_answers
        return report_answers(result.request, DATA)


@pytest.fixture
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


def wait(page, timeout=5):
    end = monotonic() + timeout
    while page.busy and monotonic() < end:
        QTest.qWait(10)
    QTest.qWait(10)
    assert not page.busy


@pytest.fixture
def make_page(app):
    pages = []

    def make(service=None, allowed=None):
        page = ChatPage(service or EngineService(), analysis_allowed=allowed)
        page.resize(1300, 900)
        page.show()
        pages.append(page)
        return page
    yield make
    for page in pages:
        page.close()
        page.deleteLater()
    app.processEvents()


def test_question_and_answer_stack_up(make_page):
    page = make_page()
    assert page.send("평택 O2 중 목표 N 미만인 설비만 보여줘")
    wait(page)
    assert len(page.cards) == 1
    card = page.cards[0]
    assert "평택 M-O2 설비 6대 중 목표 N 미만은 2대입니다." in card.summary.text()
    assert [t.text() for t in card.intent_tags] == ["자연어 검색"]
    table = card.tables[0]
    codes = sorted(table.item(r, 0).text() for r in range(table.rowCount()))
    assert codes == ["DM-009", "DM-029"] and table.item(0, table.columnCount() - 1).text() == "목표 미달"
    page.send("15L과 16L 달성률 비교해줘")
    wait(page)
    assert len(page.cards) == 2 and page.cards[1].charts                 # 앞 답은 그대로 남는다


def test_full_analysis_draws_bar_and_line_charts_and_cause(make_page):
    page = make_page()
    page.send("목표 N 달성률이 가장 낮은 원본 구역을 분석해줘")
    wait(page)
    card = page.cards[0]
    assert len(card.charts) == 2
    assert [t.text() for t in card.intent_tags] == ["표준화율 분석", "원인 분석", "업그레이드 우선순위", "버전 통일 예측"]
    assert any(t.horizontalHeaderItem(0).text() == "순위" for t in card.tables)


def test_follow_up_question_uses_previous_conditions(make_page):
    page = make_page()
    page.send("평택 O2 중 목표 N 미만인 설비만 보여줘")
    wait(page)
    page.send("그중에 P3만")
    wait(page)
    card = page.cards[1]
    assert card.answer.request.follow_up and card.answer.understood.startswith("앞 질문 이어서")
    table = card.tables[0]
    assert [table.item(r, 0).text() for r in range(table.rowCount())] == ["DM-029"]


def test_quick_chip_continues_from_previous_answer(make_page):
    page = make_page()
    page.send("평택 P3 목표 N 미만 설비 보여줘")
    wait(page)
    page.quick_chips[0].click()                                          # 원인 분석 → 그 결과의 원인
    wait(page)
    assert page.service.calls[-1] == "그 결과의 원인 분석해줘"
    assert page.cards[1].answer.request.follow_up and page.cards[1].answer.cause


def test_stop_discards_late_answer(make_page):
    gate = threading.Event()
    page = make_page(EngineService(gate))
    page.send("평택 O2 중 목표 N 미만인 설비만 보여줘")
    assert page.busy and not page.send_button.isEnabled()
    page.stop()
    assert not page.busy and page.send_button.isEnabled()
    gate.set()
    QTest.qWait(200)
    assert page.cards == []


def test_data_change_marks_answers_stale_and_drops_running_answer(make_page):
    page = make_page()
    page.send("평택 O2 중 목표 N 미만인 설비만 보여줘")
    wait(page)
    gate = threading.Event()
    page.service = EngineService(gate)
    page.send("15L과 16L 달성률 비교해줘")
    page.invalidate_data()
    gate.set()
    QTest.qWait(200)
    assert len(page.cards) == 1 and page.cards[0].stale.isVisible() and page.previous is None


def test_blocked_while_management_is_saving(make_page):
    allowed = {"value": False}
    page = make_page(allowed=lambda: allowed["value"])
    page.refresh()
    assert not page.send("평택 현황 보여줘") and not page.send_button.isEnabled() and page.status.isVisible()
    allowed["value"] = True
    page.refresh()
    assert page.send_button.isEnabled() and not page.status.isVisible()


def test_smalltalk_shows_suggestions_that_can_be_clicked(make_page):
    page = make_page()
    page.send("안녕")
    wait(page)
    assert page.cards[0].answer.suggestions


def test_real_app_uses_chat_page_with_dashboard_source(app):
    window = create_window(chat_enabled=True)
    window.resize(1500, 950)
    window.show()
    try:
        window.navigate("ai")
        page = window.pages["ai"]
        assert isinstance(page, ChatPage)
        catalog = chat_catalog(page.service.source)
        assert catalog.sites and catalog.groups and len({g.name for g in catalog.groups}) == len(catalog.groups)
        assert page.empty.isVisible() and not any(row.isVisible() for row in page.chip_rows)
        assert "설비" in page.empty.overview.text() and "기준일" in page.empty.overview.text()
        assert not window.notice.isVisible()                             # 대화 화면은 위쪽 배지 없이
        window.navigate("items")
        QTest.qWait(20)
        assert not window.notice.isVisible()                             # VER5 Item 화면도 중복 설명 배지 없이
        if "management" in window.nav:                                   # 11/11 develop: 관리 데이터는 데이터 관리로 합쳐짐
            window.navigate("management")
            QTest.qWait(20)
            assert window.notice.isVisible()                             # 숨김 목록에 없는 메뉴(관리 데이터)는 안내 유지
        window.navigate("ai")
        QTest.qWait(20)
        page.empty.cards[0].buttons[0].click()                           # 예시 카드: 평택 P3 목표 N 미만
        wait(page, 10)
        assert page.cards and page.cards[0].answer.summary
        assert not page.empty.isVisible() and all(row.isVisible() for row in page.chip_rows)
        page.send("어디부터 업그레이드해야 해?")
        wait(page, 10)
        assert isinstance(page.cards[-1], AnswerCard)
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_new_conversation_returns_to_start_screen(make_page):
    page = make_page()
    page.confirm = lambda text: True
    page.send("평택 O2 중 목표 N 미만인 설비만 보여줘")
    wait(page)
    assert page.cards and page.chip_rows[0].isVisible() and page.new_button.isVisible()
    assert page.new_conversation()
    QTest.qWait(20)
    assert page.cards == [] and page.previous is None and page.empty.isVisible()
    assert not page.chip_rows[0].isVisible()
    page.send("그중에 P3만")                                               # 이어 묻기 대상이 없으니 새 질문
    wait(page)
    assert not page.cards[0].answer.request.follow_up


def test_new_conversation_can_be_cancelled_and_stops_running_answer(make_page):
    gate = threading.Event()
    page = make_page(EngineService(gate))
    page.send("평택 O2 중 목표 N 미만인 설비만 보여줘")
    page.confirm = lambda text: False
    assert not page.new_conversation() and page.busy
    page.confirm = lambda text: True
    assert page.new_conversation() and not page.busy
    gate.set()
    QTest.qWait(200)
    assert page.cards == []


def test_cause_only_question_does_not_repeat_the_same_text(make_page):
    page = make_page()
    page.send("16L 원인 분석해줘")
    wait(page)
    result = page.cards[0].answer
    assert result.cause and result.summary != result.cause and "원인을 자료에서 정리했어요" in result.summary


def test_real_app_starter_questions_come_from_current_data(app):
    window = create_window(chat_enabled=True)
    window.show()
    try:
        page = window.pages["ai"]
        questions = [b.text() for card in page.empty.cards for b in card.buttons]
        assert len(questions) == 8 and [a.text() for a in page.faq_actions] == questions
        for question in questions:                                       # 예시는 지금 자료에서 항상 답이 나와야 한다
            result = page.service.ask(question)
            assert result.request.understood and not result.issues, (question, result.issues)
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()



def _saving_dialogs(page, folder, *, previous=False):
    """창을 띄우지 않고 저장 위치만 바꿔 저장 버튼을 누른 것처럼 처리한다."""
    opened = []

    def run(dialog):
        dialog.files.folder.setText(str(folder))
        if previous and hasattr(dialog, "include_previous"):
            dialog.include_previous.setChecked(True)
        opened.append(dialog)
        return dialog.save()
    page.open_dialog = run
    return opened


def test_search_answer_exports_excel(make_page, tmp_path):
    from openpyxl import load_workbook
    page = make_page()
    page.send("평택 O2 중 목표 N 미만인 설비만 보여줘")
    wait(page)
    card = page.cards[0]
    assert card.export_button is not None and card.report_button is None
    opened = _saving_dialogs(page, tmp_path)
    card.export_button.click()
    dialog = opened[0]
    assert dialog.range_buttons[next(iter(dialog.range_buttons))].text().startswith("조회 결과 2대")
    assert not dialog.columns[__import__("setron.features.export.query_result", fromlist=["QueryColumn"]).QueryColumn.SOURCE].isEnabled()
    path = dialog.result.path
    assert path.exists() and load_workbook(path)["조회 결과"].max_row == 3
    assert page.status.isVisible() and "Excel로 저장했어요" in page.status.text()


def test_analysis_answer_saves_word_with_previous_questions(make_page, tmp_path):
    from docx import Document
    page = make_page()
    page.send("평택 O2 중 목표 N 미만인 설비만 보여줘")
    wait(page)
    page.send("목표 N 달성률이 가장 낮은 원본 구역을 분석해줘")
    wait(page)
    card = page.cards[1]
    assert card.report_button is not None and card.export_button is None
    opened = _saving_dialogs(page, tmp_path, previous=True)
    card.report_button.click()
    dialog = opened[0]
    assert dialog.include_previous.text() == "이전 질문 1개 포함"
    document = Document(dialog.result.path)
    text = "\n".join(p.text for p in document.paragraphs)
    assert "1. 평택 M-O2 목표 N 미만 설비 현황" in text and "2. 목표 N 달성률 가장 낮은 원본 구역 분석" in text
    assert dialog.title.text() == "목표 N 달성률 가장 낮은 원본 구역 분석" and len(document.inline_shapes) == 2


def test_report_request_gives_word_card_with_all_sections(make_page, tmp_path):
    from docx import Document
    page = make_page()
    page.send("이번 달 표준화 보고서 만들어줘")
    wait(page)
    card = page.cards[0]
    assert card.report_button is not None and card.report_button.text() == "Word 다운로드"
    assert card.pdf_button is not None
    opened = _saving_dialogs(page, tmp_path)
    card.report_button.click()
    wait(page)
    dialog = opened[0]
    assert dialog.title.text() == "SETRON 표준화 보고서 2026-10"
    document = Document(dialog.result.path)
    headings = [p.text for p in document.paragraphs if p.style.name.startswith("Heading 1")]
    assert headings == ["1. 요약 KPI", "2. 구역별 목표 N 달성률", "3. 원인 분석", "4. 업그레이드 우선순위", "5. 통일 시점 예측"]
    assert len(document.inline_shapes) >= 2
    assert card.report_button.text() == "다운로드됨"


def test_answers_with_unresolved_conditions_have_no_save_buttons(make_page):
    page = make_page()
    page.send("중국 사업장 O2 현황 보여줘")
    wait(page)
    assert page.cards[0].export_button is None and page.cards[0].report_button is None


def test_zero_search_shows_zero_without_missing_data_or_full_view(make_page):
    from PySide6.QtWidgets import QPushButton
    page = make_page()
    page.send("화성 15L 목표 N 미만 설비 보여줘")
    wait(page)
    card = page.cards[0]
    assert card.answer.equipment_ids == ()
    assert card.result_count.text() == "조건에 맞는 설비 0대"
    assert card.expand_button is None
    assert card.empty_kind is None            # 자료 없음이 아니라, 정상 조회의 필터 결과 0건
    assert not any(w.text() == "데이터 가져오기" for w in card.findChildren(QPushButton))


def test_zero_search_excel_dialog_and_saved_file_keep_zero(make_page, tmp_path):
    from openpyxl import load_workbook
    from setron.features.export.query_result import ExportRange
    page = make_page()
    page.send("화성 15L 목표 N 미만 설비 보여줘")
    wait(page)
    opened = _saving_dialogs(page, tmp_path)
    page.cards[0].export_button.click()
    dialog = opened[0]
    assert dialog.range_buttons[ExportRange.SHOWN].text().startswith("조회 결과 0대")
    assert dialog.range_buttons[ExportRange.ALL].text().startswith("같은 조건의 전체 설비 2대")
    book = load_workbook(dialog.result.path, read_only=True)
    try:
        assert book["조회 결과"].max_row == 1
        assert not list(book["조회 결과"].iter_rows(min_row=2, values_only=True))
    finally:
        book.close()
