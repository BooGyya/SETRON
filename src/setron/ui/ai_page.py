"""C-4 AI 분석. 질문 해석 → 조건 확인 → 사용자 확인 후 공통 조회 적용·화면 이동, 현재 조회 결과 기반 분석.

질문만으로 DB를 바꾸지 않는다. AI가 해석한 조건은 선택지와 대조해 보여주고, 사용자가 확인했을 때만
공통 조회 초안을 바꾸고 적용한다. 예제 응답(EXAMPLE)과 실제 API(REAL) 결과를 구분해 표시한다.
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QSizePolicy, QVBoxLayout, QWidget

from setron.features.ai.examples import example_client
from setron.features.ai.schemas import AITask
from setron.features.ai.service import analyze, parse_question
from setron.infrastructure.ai_client import ResponseSource
from setron.ui.ai_data import VIEW_LABELS, VIEW_ROUTES, ConditionStatus, check_apply, interpret, selection_for
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.query_panel import _WrapRow
from setron.ui.theme import SECONDARY

SOURCE_LABELS = {
    ResponseSource.EXAMPLE: "예제 응답(EXAMPLE) · 실제 AI 결과 아님 · 질문·자료와 무관한 고정 응답",
    ResponseSource.REAL: "실제 AI API 응답(REAL)",
    None: "AI 응답 출처 미확인",
}


def _section(card, title):
    card.body.addWidget(label(title, 14, medium=True))
    text = label("", 13, wrap=True)
    text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    card.body.addWidget(text)
    return text


def _bullets(values, empty):
    return "\n".join(f"• {value}" for value in values) if values else empty


class AIPage(QWidget):
    """``parser(question)``·``analyzer(question, facts)``는 :class:`AIResult`를 돌려준다.

    ``applier(selection)``는 공통 조회 초안을 바꾸고 적용한 :class:`AppliedQuery`를 돌려주며,
    ``facts_provider()``는 현재 적용 조건의 대시보드 계산값(없으면 None)을 돌려준다.
    ``parser``·``analyzer``가 None이면 회사 AI 연결 전 기본값인 예제 응답을 쓴다.
    """

    navigate_requested = Signal(str)

    def __init__(self, query_state, parser, analyzer, applier, facts_provider, *, confirm=None):
        super().__init__()
        self.state = query_state
        self.parser = parser or (lambda question: parse_question(example_client(AITask.QUERY_PARSE), question))
        self.analyzer = analyzer or (lambda question, facts: analyze(example_client(AITask.ANALYSIS), question, facts))
        self.applier, self.facts_provider = applier, facts_provider
        self.confirm = confirm or self._ask
        self.conditions, self.rows, self.parsed_catalog_revision = None, (), None
        self.parse_source, self.applied_query, self.answer = None, None, None
        self.busy, self.generation = None, 0
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)
        self.status = StatusBadge("질문 대기", "info")
        body.addWidget(self.status)
        self.notice = label("회사 AI API 연결 전입니다. 결과는 예제 응답이며 실제 분석이 아닙니다.", 13, color=SECONDARY, wrap=True)
        self.notice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body.addWidget(self.notice)

        ask = Card()
        ask.body.addWidget(label("질문", 20, medium=True))
        ask.body.addWidget(label("질문으로 DB 값을 바꾸지 않습니다. 해석된 조건은 확인한 뒤에만 공통 조회에 적용합니다.",
                                 13, color=SECONDARY, wrap=True))
        self.question = QLineEdit()
        self.question.setAccessibleName("AI 질문")
        self.question.setPlaceholderText("예: 화성 C4 설비 버전 현황 보여줘")
        self.question.setFixedHeight(40)
        self.question.setStyleSheet("QLineEdit {background:#f7f9fc;border:1px solid #e5eaf3;border-radius:10px;padding:8px;}")
        self.question.returnPressed.connect(self.parse)
        ask.body.addWidget(self.question)
        self.parse_button = button("질문 해석", primary=True)
        self.parse_button.clicked.connect(self.parse)
        ask.body.addWidget(_WrapRow([self.parse_button], 200))
        body.addWidget(ask)

        condition = Card()
        condition.body.addWidget(label("해석된 조건", 20, medium=True))
        self.parse_badge = StatusBadge("해석 전", "neutral")
        condition.body.addWidget(self.parse_badge)
        condition.body.addWidget(label("체크한 조건만 적용합니다. 적용할 수 없는 조건은 자동으로 빼지 않으므로, "
                                       "그대로 두면 적용이 막힙니다. 제외할 조건은 직접 체크를 해제해 주세요.",
                                       13, color=SECONDARY, wrap=True))
        self.condition_list = QListWidget()
        self.condition_list.setAccessibleName("해석된 조건 목록")
        self.condition_list.setWordWrap(True)
        self.condition_list.setFixedHeight(220)
        self.condition_list.setStyleSheet("QListWidget {background:white;border:1px solid #e9eef6;border-radius:8px;}"
                                          "QListWidget::item {padding:8px;border-bottom:1px solid #e9eef6;}")
        self.condition_list.itemChanged.connect(lambda *_: self._render_apply())
        condition.body.addWidget(self.condition_list)
        self.view_label = label("", 13, color=SECONDARY, wrap=True)
        condition.body.addWidget(self.view_label)
        self.apply_reason = label("", 13, wrap=True)
        self.apply_reason.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        condition.body.addWidget(self.apply_reason)
        self.apply_button = button("확인 후 조건 적용", primary=True)
        self.apply_button.clicked.connect(self.apply_conditions)
        condition.body.addWidget(_WrapRow([self.apply_button], 220))
        self.apply_result = label("", 13, wrap=True)
        condition.body.addWidget(self.apply_result)
        body.addWidget(condition)

        analysis = Card()
        analysis.body.addWidget(label("현재 조회 결과 분석", 20, medium=True))
        self.facts_label = label("", 13, color=SECONDARY, wrap=True)
        self.facts_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        analysis.body.addWidget(self.facts_label)
        actions = QHBoxLayout()
        self.analyze_button = button("현재 적용 조건으로 분석")
        self.analyze_button.clicked.connect(self.analyze)
        self.analysis_badge = StatusBadge("분석 전", "neutral")
        actions.addWidget(self.analyze_button)
        actions.addWidget(self.analysis_badge)
        actions.addStretch()
        analysis.body.addLayout(actions)
        self.answer_label = _section(analysis, "답변")
        self.evidence_label = _section(analysis, "근거 · 자료로 확인")
        self.assumption_label = _section(analysis, "추정 · 자료로 확인하지 않음")
        self.unconfirmed_label = _section(analysis, "미확인")
        body.addWidget(analysis)
        self.refresh()

    def _set_status(self, text, tone, notice=None):
        self.status.setText(text)
        self.status.set_tone(tone)
        if notice is not None:
            self.notice.setText(notice)

    def refresh(self):
        """공통 조회 적용·선택지 변경 뒤 분석 가능 여부와 적용 가능 여부를 다시 표시한다."""
        facts = self.facts_provider()
        self.facts_label.setText("분석에 보낼 자료 · 대시보드와 같은 적용 조건의 계산값\n"
                                 + "\n".join(f"{key}: {'계산 불가' if value is None else value}" for key, value in facts.items())
                                 if facts else "조회 적용으로 대시보드 결과를 계산한 뒤 분석할 수 있습니다.")
        self.parse_button.setEnabled(self.busy is None)
        self.analyze_button.setEnabled(self.busy is None and facts is not None)
        self._render_apply()

    # --- 질문 해석 ---------------------------------------------------------

    def parse(self):
        question = self.question.text().strip()
        if self.busy is not None or not question:
            return False
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
        facts = self.facts_provider()
        if self.busy is not None or facts is None:
            return False
        self.generation += 1
        self.busy = "analysis"
        self.answer = None
        for widget in (self.answer_label, self.evidence_label, self.assumption_label, self.unconfirmed_label):
            widget.setText("")
        self._set_status("분석 중", "info")
        self.refresh()
        generation = self.generation
        QTimer.singleShot(0, lambda: self._analyze(generation, question, facts))
        return True

    def _analyze(self, generation, question, facts):
        if generation != self.generation:
            return
        self.busy = None
        try:
            result = self.analyzer(question, facts)
        except Exception:
            result = None
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
        self._set_status("분석 완료", "info", f"{facts['조회 조건']} · 기준일 {facts['기준일']} 자료로 요청했습니다.")
        self.refresh()
