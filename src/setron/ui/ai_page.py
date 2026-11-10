"""C-4 AI 분석. 질문 해석 → 조건 확인 → 사용자 확인 후 공통 조회 적용·화면 이동, 현재 조회 결과 기반 분석.

질문만으로 DB를 바꾸지 않는다. AI가 해석한 조건은 선택지와 대조해 보여주고, 사용자가 확인했을 때만
공통 조회 초안을 바꾸고 적용한다. 예제 응답(EXAMPLE)과 실제 API(REAL) 결과를 구분해 표시한다.
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from setron.features.ai.examples import example_client
from setron.features.ai.schemas import AITask
from setron.features.ai.service import analyze, parse_question
from setron.infrastructure.ai_client import ResponseSource
from setron.ui.ai_data import FreshAnalysisData, VIEW_LABELS, VIEW_ROUTES, ConditionStatus, check_apply, interpret, selection_for
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.query_panel import _WrapRow
from setron.ui.theme import INK, PRIMARY, SECONDARY, font, icon, svg_size, set_widget_font
from setron.ui.read_worker import ReadRequest

SOURCE_LABELS = {
    ResponseSource.EXAMPLE: "예제 응답(EXAMPLE) · 실제 AI 결과 아님 · 질문·자료와 무관한 고정 응답",
    ResponseSource.REAL: "실제 AI API 응답(REAL)",
    None: "AI 응답 출처 미확인",
}


SPARKLE = "ver5/active-ai.svg"
# 바로 분석 칩은 질문 입력란만 채운다. 해석·적용·분석은 사용자가 전송·버튼을 눌러야 실행된다.
QUICK_QUESTIONS = (
    ("원인 분석", "현재 조회 결과에서 목표 N 미달 원인을 분석해줘"),
    ("업그레이드 우선순위", "업그레이드가 먼저 필요한 설비를 알려줘"),
    ("버전 통일 시점 예측", "목표 N 통일은 언제쯤 끝날까?"),
    ("보고서 생성", "현재 조회 결과로 보고서를 만들어줘"),
)
_STYLE = f"""
QFrame#aiInputArea {{ background: white; border: none; border-top: 1px solid #dee0e5;
    border-bottom-left-radius: 10px; border-bottom-right-radius: 10px; }}
QFrame#aiAnswer {{ background: #f4f5f8; border: none; border-radius: 14px; border-top-left-radius: 4px; }}
QLabel#aiAvatar {{ background: #ebeffd; border-radius: 18px; }}
QLabel#aiBubble {{ background: {PRIMARY}; color: white; border-radius: 16px; border-bottom-right-radius: 4px;
    padding: 11px 18px; }}
QLabel#aiTag {{ background: #ebeffd; color: {PRIMARY}; border-radius: 6px; padding: 3px 8px; }}
QFrame#aiInputBox {{ background: white; border: 1.5px solid {PRIMARY}; border-radius: 12px; }}
QFrame#aiInputBox QLineEdit {{ background: transparent; border: none; color: {INK}; }}
QPushButton#aiQuick {{ background: white; border: 1px solid #dee0e5; border-radius: 16px; padding: 6px 12px;
    color: {INK}; min-height: 0; }}
QPushButton#aiQuick:hover {{ background: #edf0fd; border-color: {PRIMARY}; }}
QListWidget#aiConditions {{ background: white; border: 1px solid #dee0e5; border-radius: 10px; color: {INK}; }}
QListWidget#aiConditions::item {{ padding: 9px 12px; border-bottom: 1px solid #eef0f3; }}
"""


def _text(size=13, color=None):
    text = label("", size, color=color, wrap=True)
    text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return text


def _section(card, title):
    heading = label(title, 13, medium=True)
    heading.setVisible(False)
    card.body.addWidget(heading)
    text = _OptionalText(heading=heading)
    text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    card.body.addWidget(text)
    return text


def _bullets(values, empty):
    return "\n".join(f"• {value}" for value in values) if values else empty


def _sparkle(size=18):
    result = QLabel()
    result.setPixmap(icon(SPARKLE).pixmap(svg_size(SPARKLE)))
    result.setAlignment(Qt.AlignmentFlag.AlignCenter)
    result.setFixedSize(size, size)
    return result


def _tag(text):
    result = QLabel(text)
    result.setObjectName("aiTag")
    set_widget_font(result, font(12, medium=True))
    result.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return result


class _UserBubble(QWidget):
    """사용자 질문 말풍선(오른쪽 정렬). 질문 문구만 그대로 보여준다."""

    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        self.text = QLabel()
        self.text.setObjectName("aiBubble")
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        set_widget_font(self.text, font(15, medium=True))
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.text, 0, Qt.AlignmentFlag.AlignRight)
        self.hide()

    def show_question(self, question):
        self.text.setText(question)
        self._fit(self.width())
        self.setVisible(bool(question))

    def _fit(self, available):
        # 한 줄에 들어가면 글자 폭만큼, 길면 줄의 70%까지 넓히고 줄바꿈한다.
        natural = self.text.fontMetrics().horizontalAdvance(self.text.text()) + 40
        limit = max(200, int(available * 0.7)) if available > 0 else 640
        self.text.setWordWrap(natural > limit)
        self.text.setFixedWidth(min(natural, limit))

    def resizeEvent(self, event):
        self._fit(event.size().width())
        super().resizeEvent(event)


class _OptionalText(QLabel):
    """내용이 있을 때만 보이는 문단. 비어 있는 섹션이 답변 블록에 빈 공간을 만들지 않게 한다."""

    def __init__(self, size=13, color=None, heading=None):
        super().__init__("")
        self.heading = heading
        self.setTextFormat(Qt.TextFormat.PlainText)
        set_widget_font(self, font(size))
        self.setWordWrap(True)
        if color:
            self.setStyleSheet(f"color: {color};")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setVisible(False)

    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))
        if self.heading is not None:
            self.heading.setVisible(bool(text))


class _AnswerBlock(QWidget):
    """AI 답변 블록: 반짝이 아바타 + 회색 본문. ``body``는 Card와 같은 방식으로 위젯을 붙인다."""

    def __init__(self, intent):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        avatar = _sparkle(36)
        avatar.setObjectName("aiAvatar")
        row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
        self.frame = QFrame()
        self.frame.setObjectName("aiAnswer")
        self.body = QVBoxLayout(self.frame)
        self.body.setContentsMargins(22, 18, 22, 18)
        self.body.setSpacing(12)
        self.header = QHBoxLayout()
        self.header.setSpacing(6)
        caption = label("요청 인식", 12, medium=True, color="rgba(20,32,51,0.6)")
        self.header.addWidget(caption)
        self.header.addWidget(_tag(intent))
        self.body.addLayout(self.header)
        row.addWidget(self.frame, 1)

    def add_header(self, badge):
        """오른쪽 출처 배지. 좁은 창에서는 배지 문구를 줄바꿈해 가로 스크롤을 만들지 않는다."""
        self.badge = badge
        badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.header.addStretch(1)
        self.header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

    def resizeEvent(self, event):
        badge = getattr(self, "badge", None)
        if badge is not None:
            badge.setWordWrap(event.size().width() < 1000)
        super().resizeEvent(event)


class AIPage(QWidget):
    """``parser(question)``·``analyzer(question, facts)``는 :class:`AIResult`를 돌려준다.

    ``applier(selection)``는 공통 조회 초안을 바꾸고 적용한 :class:`AppliedQuery`를 돌려주며,
    ``facts_provider()``는 현재 적용 조건의 대시보드 계산값(없으면 None)을 돌려준다.
    ``parser``·``analyzer``가 None이면 회사 AI 연결 전 기본값인 예제 응답을 쓴다.
    ``fresh_loader``가 있으면 분석 요청마다 워커에서 최신 화면·AI 자료를 읽는다.
    조회 실패나 조건 변경 시 이전 캐시로 분석하지 않는다.
    """

    navigate_requested = Signal(str)
    fresh_data_ready = Signal(object)

    def __init__(self, query_state, parser, analyzer, applier, facts_provider, *, confirm=None, fresh_loader=None,
                 analysis_allowed=None, explainer=None, forecaster=None, snapshot_provider=None, report_saver=None):
        super().__init__()
        self.state = query_state
        self.parser = parser or (lambda question: parse_question(example_client(AITask.QUERY_PARSE), question))
        self.analyzer = analyzer or (lambda question, facts: analyze(example_client(AITask.ANALYSIS), question, facts))
        self.applier, self.facts_provider = applier, facts_provider
        self.fresh_loader = fresh_loader
        # 원인 설명(BE-019)·참고 예측(BE-020)·Word 보고서(BE-022). 없으면 기존 분석만 한다.
        self.explainer, self.forecaster = explainer, forecaster
        self.snapshot_provider = snapshot_provider or (lambda: None)
        self.report_saver = report_saver
        self.report, self.forecast_report, self.report_question, self.analysis_snapshot = None, None, None, None
        self.analysis_allowed = analysis_allowed or (lambda: True)
        self.fresh_request = ReadRequest(self)
        self.fresh_request.completed.connect(self._fresh_loaded)
        self.analysis_ticket = None
        self.analysis_query = None
        self.confirm = confirm or self._ask
        self.conditions, self.rows, self.parsed_catalog_revision = None, (), None
        self.parse_source, self.applied_query, self.answer = None, None, None
        self.busy, self.generation = None, 0
        self.setStyleSheet(_STYLE)
        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)
        # 하나의 대화 카드: 위쪽 대화(질문 말풍선·AI 답변 블록), 아래쪽 바로 분석·질문 입력.
        chat = Card(padding=0)
        chat.body.setSpacing(0)
        page.addWidget(chat)
        thread = QWidget()
        body = QVBoxLayout(thread)
        body.setContentsMargins(24, 24, 24, 24)
        body.setSpacing(20)
        chat.body.addWidget(thread)

        state_row = QHBoxLayout()
        state_row.setSpacing(10)
        self.status = StatusBadge("질문 대기", "info")
        state_row.addWidget(self.status, 0, Qt.AlignmentFlag.AlignTop)
        self.notice = _text(13, SECONDARY)
        self.notice.setText("회사 AI API 연결 전입니다. 결과는 예제 응답이며 실제 분석이 아닙니다.")
        state_row.addWidget(self.notice, 1)
        body.addLayout(state_row)

        # 질문 해석 → 조건 확인 블록
        self.question_bubble = _UserBubble()
        body.addWidget(self.question_bubble)
        condition = _AnswerBlock("조건 해석")
        self.parse_badge = StatusBadge("해석 전", "neutral")
        condition.add_header(self.parse_badge)
        rule = _text(13)
        rule.setText("질문으로 DB 값을 바꾸지 않습니다. 해석된 조건은 확인한 뒤에만 공통 조회에 적용합니다. "
                     "체크한 조건만 적용하며, 적용할 수 없는 조건은 자동으로 빼지 않으므로 그대로 두면 적용이 막힙니다. "
                     "제외할 조건은 직접 체크를 해제해 주세요.")
        condition.body.addWidget(rule)
        self.condition_list = QListWidget()
        self.condition_list.setObjectName("aiConditions")
        self.condition_list.setAccessibleName("해석된 조건 목록")
        self.condition_list.setWordWrap(True)
        self.condition_list.setFixedHeight(200)
        set_widget_font(self.condition_list, font(13))
        self.condition_list.itemChanged.connect(lambda *_: self._render_apply())
        condition.body.addWidget(self.condition_list)
        self.view_label = _OptionalText(13, SECONDARY)
        condition.body.addWidget(self.view_label)
        self.apply_reason = _text(12, "rgba(20,32,51,0.6)")
        self.apply_button = button("확인 후 조건 적용", primary=True)
        self.apply_button.setFixedHeight(40)
        self.apply_button.clicked.connect(self.apply_conditions)
        apply_row = QHBoxLayout()
        apply_row.setSpacing(12)
        apply_row.addWidget(self.apply_reason, 1)
        apply_row.addWidget(self.apply_button, 0, Qt.AlignmentFlag.AlignVCenter)
        condition.body.addLayout(apply_row)
        self.apply_result = _OptionalText(13)
        condition.body.addWidget(self.apply_result)
        body.addWidget(condition)

        # 현재 조회 결과 분석 블록
        self.analysis_bubble = _UserBubble()
        body.addWidget(self.analysis_bubble)
        analysis = _AnswerBlock("현재 조회 분석")
        self.analysis_badge = StatusBadge("분석 전", "neutral")
        analysis.add_header(self.analysis_badge)
        self.facts_label = _text(12, "rgba(20,32,51,0.6)")
        analysis.body.addWidget(self.facts_label)
        self.answer_label = _section(analysis, "답변")
        self.evidence_label = _section(analysis, "근거 · 자료로 확인")
        self.assumption_label = _section(analysis, "추정 · 자료로 확인하지 않음")
        self.unconfirmed_label = _section(analysis, "미확인")
        self.number_check_label = _section(analysis, "숫자 확인")
        self.forecast_label = _section(analysis, "참고 예측")
        self.analyze_button = button("현재 적용 조건으로 분석")
        self.analyze_button.clicked.connect(self.analyze)
        self.forecast_button = button("통일 예측 (참고)")
        self.forecast_button.setToolTip("현재 조회 범위의 날짜별 저장 현황으로 목표 N 통일 완료 시점을 단순 비례로 계산합니다.")
        self.forecast_button.clicked.connect(self.run_forecast)
        self.report_button = button("Word 보고서 저장", primary=True)
        self.report_button.setToolTip("질문·조건·답변(근거·추정·미확인)·사용한 수치·참고 예측을 .docx로 저장합니다.")
        self.report_button.clicked.connect(lambda: self.save_report())
        for action in (self.analyze_button, self.forecast_button, self.report_button):
            action.setFixedHeight(40)
        analysis.body.addWidget(_WrapRow([self.analyze_button, self.forecast_button, self.report_button], 190))
        body.addWidget(analysis)
        body.addStretch(1)

        # 바로 분석 · 질문 입력
        composer = QFrame()
        composer.setObjectName("aiInputArea")
        compose = QVBoxLayout(composer)
        compose.setContentsMargins(24, 14, 24, 18)
        compose.setSpacing(10)
        quick_row = QHBoxLayout()
        quick_row.setSpacing(8)
        quick_row.addWidget(label("바로 분석", 12, medium=True, color="rgba(20,32,51,0.6)"))
        self.quick_buttons = []
        for text, question in QUICK_QUESTIONS:
            chip = QPushButton(text)
            chip.setObjectName("aiQuick")
            set_widget_font(chip, font(12, medium=True))
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"질문 입력란에 ‘{question}’을 채웁니다. 전송해야 해석합니다.")
            chip.clicked.connect(lambda checked=False, value=question: self._prefill(value))
            self.quick_buttons.append(chip)
        quick_row.addWidget(_WrapRow(self.quick_buttons, 150), 1)
        compose.addLayout(quick_row)
        box = QFrame()
        box.setObjectName("aiInputBox")
        input_row = QHBoxLayout(box)
        input_row.setContentsMargins(18, 8, 8, 8)
        input_row.setSpacing(10)
        input_row.addWidget(_sparkle(18))
        self.question = QLineEdit()
        self.question.setAccessibleName("AI 질문")
        self.question.setPlaceholderText("질문을 입력하세요. 예) 화성 C4 설비 버전 현황 보여줘")
        set_widget_font(self.question, font(15))
        self.question.setMinimumWidth(80)
        self.question.setFixedHeight(40)
        self.question.returnPressed.connect(self.parse)
        input_row.addWidget(self.question, 1)
        self.parse_button = button("전송", primary=True)
        self.parse_button.setAccessibleName("질문 전송 · 조건 해석")
        self.parse_button.setToolTip("질문을 조회 조건으로 해석합니다. 확인 전에는 아무 조건도 적용하지 않습니다.")
        self.parse_button.setFixedSize(152, 40)
        self.parse_button.clicked.connect(self.parse)
        input_row.addWidget(self.parse_button)
        compose.addWidget(box)
        chat.body.addWidget(composer)
        self.refresh()

    def _prefill(self, question):
        """바로 분석 칩: 질문 입력란만 채운다. 해석·적용·분석은 실행하지 않는다."""
        self.question.setText(question)
        self.question.setFocus()

    def _set_status(self, text, tone, notice=None):
        self.status.setText(text)
        self.status.set_tone(tone)
        if notice is not None:
            self.notice.setText(notice)

    def refresh(self):
        """공통 조회 적용·선택지 변경 뒤 분석 가능 여부와 적용 가능 여부를 다시 표시한다."""
        facts = self.facts_provider()
        self.facts_label.setText("분석에 보낼 자료 · 대시보드와 같은 적용 조건의 계산값\n"
                                 + " · ".join(f"{key}: {'계산 불가' if value is None else value}" for key, value in facts.items())
                                 if facts else "대시보드에서 날짜나 필터를 선택한 뒤 분석할 수 있습니다.")
        self.parse_button.setEnabled(self.busy is None)
        available = facts is not None or (self.fresh_loader is not None and self.state.applied_is_current)
        self.analyze_button.setEnabled(self.busy is None and available and self.analysis_allowed())
        self.forecast_button.setVisible(self.forecaster is not None)
        self.forecast_button.setEnabled(self.busy is None and self.forecaster is not None and self.analysis_allowed()
                                        and self._current_snapshot() is not None)
        self.report_button.setVisible(self.report_saver is not None)
        self.report_button.setEnabled(self.busy is None and self.report is not None)
        self._render_apply()

    def _current_snapshot(self):
        if self.analysis_snapshot is not None and self.state.is_current(self.analysis_snapshot.query):
            return self.analysis_snapshot
        snapshot = self.snapshot_provider()
        return snapshot if snapshot is not None and self.state.is_current(snapshot.query) else None

    def run_forecast(self):
        """현재 조회 범위로 참고 예측을 계산하고 설명을 받는다 (BE-020). 숫자는 계산, 설명만 AI가 맡는다."""
        snapshot = self._current_snapshot()
        if self.busy is not None or self.forecaster is None or snapshot is None or not self.analysis_allowed():
            return False
        question = self.question.text().strip() or "목표 N 통일은 언제쯤 끝날까?"
        try:
            report = self.forecaster(question, snapshot)
        except Exception:
            self.forecast_label.setText("예측을 계산하지 못했습니다. 조회 적용 후 다시 시도해 주세요.")
            return False
        self.forecast_report = report
        lines = []
        for row in report.facts.get("계열별 예측", []):
            line = f"• {row['계열']}: {row['상태']} · {row['표시']}"
            if row["예상 잔여 기간(일)"] is not None and row["참고 완료일"]:
                line += f" · 남은 {row['끝 N 미만(남은 대수)']}대 · 약 {row['예상 잔여 기간(일)']}일 · {row['참고 완료일']}"
            lines.append(line + "".join(f" · {reason}" for reason in row["사유"]))
        answer = report.result.data.answer if report.result.ok else f"예측 설명을 받지 못했습니다: {report.result.message}"
        self.forecast_label.setText("\n".join(lines or ["예측할 계열이 없습니다."]) + "\n" + answer)
        self.refresh()
        return True

    def save_report(self, path=None):
        """분석(과 참고 예측) 결과를 Word로 저장한다 (BE-022). AI를 다시 호출하지 않는다."""
        if self.report is None or self.report_saver is None:
            return None
        from setron.features.report.schemas import WordReport
        report = WordReport(question=self.report_question, analysis=self.report, forecast=self.forecast_report)
        if path is not None:
            return self.report_saver(report, path)
        from datetime import datetime
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        default = Path.home() / f"setron_ai_report_{datetime.now():%Y%m%d_%H%M}.docx"
        path, _ = QFileDialog.getSaveFileName(self, "Word 보고서 저장", str(default), "Word 문서 (*.docx)")
        if not path:
            return None
        result = self.report_saver(report, path)
        (QMessageBox.information if result.saved else QMessageBox.warning)(
            self, "Word 보고서 저장", f"{result.path} 에 저장했습니다." if result.saved else f"저장하지 못했습니다. {result.message}")
        return result

    # --- 질문 해석 ---------------------------------------------------------

    def parse(self):
        question = self.question.text().strip()
        if self.busy is not None or not question:
            return False
        self.question_bubble.show_question(question)
        self.generation += 1
        self.busy = "parse"
        self.conditions, self.rows, self.parse_source = None, (), None
        self.condition_list.clear()
        self.apply_result.setText("")
        self._set_status("질문 해석 중", "info")
        self.refresh()
        generation = self.generation
        QTimer.singleShot(0, lambda: self._parse(generation, question))
        return True

    def _parse(self, generation, question):
        if generation != self.generation:
            return
        self.busy = None
        try:
            result = self.parser(question)
        except Exception:
            result = None
        if result is None or not result.ok:
            self.parse_badge.setText("해석 실패")
            self.parse_badge.set_tone("danger")
            self._set_status("질문 해석 실패", "danger",
                             result.message if result is not None else "질문을 해석하지 못했습니다. 다시 시도하세요.")
            self.refresh()
            return
        self.conditions, self.parse_source = result.data, result.source
        self.parsed_catalog_revision = self.state.catalog_revision
        self.rows = interpret(result.data, self.state.catalog)
        self.parse_badge.setText(SOURCE_LABELS.get(result.source, SOURCE_LABELS[None]))
        self.parse_badge.set_tone("warning" if result.source is not ResponseSource.REAL else "info")
        self.condition_list.blockSignals(True)
        for row in self.rows:
            item = QListWidgetItem(f"[{row.status.value}] {row.dimension} · {row.raw}\n{row.message}")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.condition_list.addItem(item)
        self.condition_list.blockSignals(False)
        view = result.data.target_view
        route = VIEW_ROUTES.get(view)
        self.view_label.setText(f"AI가 제안한 화면: {VIEW_LABELS.get(view, '없음')} · "
                                + (f"적용 후 {VIEW_LABELS[view]}로 이동합니다." if route else "적용 후 이 화면에 머뭅니다."))
        self._set_status("조건 확인 필요", "warning", "해석된 조건을 확인해 주세요. 아직 아무 조건도 적용하지 않았습니다.")
        self.refresh()

    # --- 조건 적용 ---------------------------------------------------------

    def included(self):
        return {i for i in range(self.condition_list.count())
                if self.condition_list.item(i).checkState() == Qt.CheckState.Checked}

    def apply_block_reason(self):
        if self.conditions is None:
            return "질문을 해석하면 조건을 확인할 수 있습니다."
        if self.parsed_catalog_revision != self.state.catalog_revision:
            return "조회 선택지가 바뀌었습니다. 질문을 다시 해석해 주세요."
        applied = self.state.applied
        if applied is not None and self.state.target_draft != applied.targets:
            return "목표 N 변경이 적용 대기 중입니다. 공통 조회 패널에서 먼저 적용하거나 되돌린 뒤 사용해 주세요."
        return check_apply(self.rows, self.included())

    def _render_apply(self):
        # 해석 전에는 빈 조건 목록 대신 안내 문구만 보인다.
        self.condition_list.setVisible(self.condition_list.count() > 0)
        reason = self.apply_block_reason() if self.busy is None else "처리 중입니다."
        self.apply_button.setEnabled(reason is None)
        self.apply_reason.setText(reason or "적용할 수 있습니다. 적용 전에 확인 창이 표시됩니다.")

    def _ask(self, text):
        answer = QMessageBox.question(self, "AI 조건 적용 확인", text,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def apply_conditions(self):
        if self.busy is not None or self.apply_block_reason() is not None:
            return False
        included = self.included()
        selection = selection_for(self.rows, included)
        excluded = [f"{r.dimension} · {r.raw} ({r.status.value})" for i, r in enumerate(self.rows) if i not in included]
        view = self.conditions.target_view
        route = VIEW_ROUTES.get(view)
        text = (f"적용할 조건: {self.state.describe(selection)}\n"
                f"직접 제외한 조건: {', '.join(excluded) if excluded else '없음'}\n"
                f"이동: {VIEW_LABELS[view] if route else '이동 없음'}\n\n"
                "선택 중이던 공통 조회 조건은 위 조건으로 바뀌고 바로 조회 적용됩니다. 목표 N은 바뀌지 않습니다.\n적용할까요?")
        if not self.confirm(text):
            self._set_status("적용 취소", "info", "조건을 적용하지 않았습니다.")
            return False
        query = self.applier(selection)
        self.applied_query = query
        self.apply_result.setText(f"적용 #{query.revision} · {query.description}"
                                  + (f"\n제외한 조건: {', '.join(excluded)}" if excluded else ""))
        self._set_status("조건 적용됨", "success", "AI 해석 조건을 확인 후 공통 조회에 적용했습니다.")
        self.refresh()
        if route:
            self.navigate_requested.emit(route)
        return True

    # --- 분석 -------------------------------------------------------------

    def analyze(self):
        question = self.question.text().strip() or "현재 조회 결과를 요약해 줘"
        if self.busy is not None or not self.analysis_allowed():
            return False
        if self.fresh_loader is not None:
            try:
                loader = self.fresh_loader()
            except Exception:
                loader = None
            if loader is None:
                self._set_status("조회 확인 필요", "warning", "현재 적용 조건으로 자료를 다시 조회한 뒤 분석해 주세요.")
                return False
            self.analysis_bubble.show_question(question)
            self.generation += 1
            self.analysis_ticket = (self.generation, self.state.applied, question)
            self.busy = "facts"
            self._clear_analysis()
            self._set_status("최신 자료 조회 중", "info")
            self.refresh()
            return self.fresh_request.start(loader)
        facts = self.facts_provider()
        if facts is None:
            return False
        self.generation += 1
        self._begin_analysis(question, facts)
        return True

    def _clear_analysis(self):
        self.answer = None
        self.report, self.forecast_report, self.report_question, self.analysis_snapshot = None, None, None, None
        for widget in (self.answer_label, self.evidence_label, self.assumption_label, self.unconfirmed_label,
                       self.number_check_label, self.forecast_label):
            widget.setText("")

    def invalidate_data(self):
        """저장 시작·성공 후 이전 자료와 실행 대기 중인 분석을 무효화한다. AI는 실행하지 않는다."""
        self.generation += 1
        self.fresh_request.cancel()
        self.analysis_ticket = None
        self.analysis_query = None
        self.busy = None
        self._clear_analysis()
        self.analysis_badge.setText("자료 변경 · 다음 요청에서 최신값 확인")
        self.analysis_badge.set_tone("info")
        self.refresh()

    def _fresh_loaded(self, data, error):
        ticket, self.analysis_ticket = self.analysis_ticket, None
        if ticket is None or self.busy != "facts":
            return
        generation, query, question = ticket
        self.busy = None
        if generation != self.generation or not self.state.is_current(query):
            self._set_status("조회 조건 변경", "warning", "분석 요청 중 조회 조건이 바뀌었습니다. 다시 요청해 주세요.")
            self.refresh()
            return
        if error is not None or not isinstance(data, FreshAnalysisData):
            self._set_status("최신 자료 조회 실패", "danger", "최신 저장 자료를 읽지 못해 분석을 실행하지 않았습니다. DB 연결을 확인해 주세요.")
            self.refresh()
            return
        self.fresh_data_ready.emit(data)
        if generation != self.generation:
            return
        if not self.state.is_current(query):
            self._set_status("조회 조건 변경", "warning", "분석 준비 중 조회 조건이 바뀌었습니다. 다시 요청해 주세요.")
            self.refresh()
            return
        self._begin_analysis(question, data.facts, query=query, snapshot=data.snapshot)

    def _begin_analysis(self, question, facts, *, query=None, snapshot=None):
        self.analysis_bubble.show_question(question)
        self.analysis_query = query if query is not None else self.state.applied
        self.busy = "analysis"
        self._clear_analysis()
        self._set_status("분석 중", "info")
        self.refresh()
        generation = self.generation
        snapshot = snapshot if snapshot is not None else self.snapshot_provider()
        QTimer.singleShot(0, lambda: self._analyze(generation, question, facts, snapshot))

    def _analyze(self, generation, question, facts, snapshot=None):
        if generation != self.generation:
            return
        self.busy = None
        query = self.analysis_query
        if query is not None and not self.state.is_current(query):
            self._set_status("조회 조건 변경", "warning", "조회 조건이 바뀌어 이전 자료의 분석을 실행하지 않았습니다.")
            self.refresh()
            return
        report = None
        try:
            if self.explainer is not None and snapshot is not None:
                report = self.explainer(question, snapshot)
                result = report.result
            else:
                result = self.analyzer(question, facts)
        except Exception:
            result = None
        if generation != self.generation or (query is not None and not self.state.is_current(query)):
            self._set_status("자료 변경", "warning", "요청 후 자료나 조회 조건이 바뀌었습니다. 최신 자료로 다시 요청해 주세요.")
            self.refresh()
            return
        if result is None or not result.ok:
            self.analysis_badge.setText("분석 실패")
            self.analysis_badge.set_tone("danger")
            self._set_status("분석 실패", "danger",
                             result.message if result is not None else "분석 결과를 처리하지 못했습니다. 다시 시도하세요.")
            self.refresh()
            return
        self.answer = result.data
        self.analysis_badge.setText(SOURCE_LABELS.get(result.source, SOURCE_LABELS[None]))
        self.analysis_badge.set_tone("warning" if result.source is not ResponseSource.REAL else "info")
        self.answer_label.setText(f"질문: {question}\n{result.data.answer}")
        evidence = _bullets(result.data.evidence, "자료로 확인한 근거가 없습니다.")
        if result.source is not ResponseSource.REAL:
            evidence += "\n(예제 응답의 고정 문장입니다. 위 분석 자료로 확인한 근거가 아닙니다.)"
        self.evidence_label.setText(evidence)
        self.assumption_label.setText(_bullets(result.data.assumptions, "추정 없음"))
        self.unconfirmed_label.setText(_bullets(result.data.unconfirmed, "미확인 항목 없음"))
        if report is not None:
            self.report, self.report_question, self.analysis_snapshot = report, question, snapshot
            self.number_check_label.setText(
                "답변의 숫자가 모두 보낸 자료에 있습니다." if report.numbers_match else
                "보낸 자료에 없는 숫자가 있습니다. 대시보드 수치와 대조해 주세요: " + ", ".join(report.unmatched_numbers))
        self._set_status("분석 완료", "info", f"{facts['조회 조건']} · 기준일 {facts['기준일']} 자료로 요청했습니다.")
        self.refresh()
        # 완료 시점·남은 기간을 묻는 질문이면 같은 조회 범위로 참고 예측도 함께 보인다 (BE-018 → BE-020).
        from setron.features.forecast.service import is_forecast_question
        if self.forecaster is not None and is_forecast_question(question):
            self.run_forecast()
