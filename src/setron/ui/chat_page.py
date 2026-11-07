"""AI 분석 대화 화면 (VER4 와이어프레임 10-1 ~ 10-5, VER5 부품).

질문과 답이 위로 쌓이는 대화형 화면이다. 답은 ``features.chat`` 엔진의 답 묶음(ChatAnswer)을 그대로 그린다.
- 숫자는 모두 대시보드와 같은 자료의 집계 값이다. 대시보드 조건은 바꾸지 않는다(읽기 전용).
- 질문은 워커에서 처리하고, 처리 중에는 단계 표시와 '중지'를 보여 준다. 중지하거나 자료가 바뀌면 늦은 답을 버린다.
- "그중에 P3만", "그럼 화성은?"처럼 이어 물으면 바로 앞 질문의 조건을 이어받는다.
- 관리 데이터 저장 중에는 질문을 보내지 않는다. 자료가 바뀌면 이미 받은 답에 '이전 자료 기준'을 표시한다.
"""

from __future__ import annotations

from PySide6.QtCharts import (
    QBarCategoryAxis, QBarSet, QChart, QChartView, QDateTimeAxis, QLineSeries, QStackedBarSeries, QValueAxis,
)
from PySide6.QtCore import QDateTime, QEvent, QPoint, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from setron.features.chat.report import ReportPart
from setron.features.chat.schemas import INTENT_LABELS, BarChart, ChatAnswer, Intent, LineChart, Table
from setron.ui.ai_page import _STYLE, SPARKLE, _UserBubble, _sparkle, _tag
from setron.ui.chat_export import ExcelExportDialog, ReportDialog, chart_png
from setron.ui.components import button, label
from setron.ui.query_panel import _WrapRow
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.theme import INK, PRIMARY, SECONDARY, icon

# 처음 화면의 예시 카드: (분류, 설명, 질문 2개). 자주 하는 질문 칩도 같은 질문을 쓴다.
STARTERS = (
    ("조회", "설비 목록·대수", ("평택 P3 목표 N 미만 설비 보여줘", "화성 메모리 O3 설비 몇 대야?")),
    ("비교·변화", "구역별 달성률·지난 기준일 대비", ("15L과 16L 달성률 비교해줘", "지난달보다 달성률이 떨어진 라인은?")),
    ("원인·우선순위", "왜 낮은지·어디부터 올릴지", ("목표 N 달성률이 가장 낮은 원본 구역을 분석해줘", "어디부터 업그레이드해야 해?")),
    ("예측·보고서", "통일 시점·표준화 보고서", ("C3 통합UI는 언제 통일될까?", "이번 달 표준화 보고서 만들어줘")),
)
FAQ = tuple(question for _, _, questions in STARTERS for question in questions)
# 바로 분석: 앞 질문이 있으면 그 결과를 이어서, 없으면 전체 범위로 묻는다.
QUICK = (("원인 분석", "그 결과의 원인 분석해줘", "원인 분석해줘"),
         ("업그레이드 우선순위", "그 결과에서 어디부터 업그레이드해야 해?", "어디부터 업그레이드해야 해?"),
         ("버전 통일 시점 예측", "그 결과의 통일 시점 예측해줘", "목표 N 통일 시점 예측해줘"),
         ("보고서 생성", "그 결과로 보고서 만들어줘", "이번 달 표준화 보고서 만들어줘"))
STEPS = ("질문 이해", "데이터 조회", "표·그래프 만들기", "원인·우선순위 계산", "답변 작성")
_TONES = {"danger": ("#fdecec", "#c62828"), "warning": ("#fff5d9", "#8a5a00"), "success": ("#e7f5ec", "#1e7d3a")}
_CHAT_STYLE = _STYLE + f"""
QFrame#aiConversation {{ background: white; border: 1px solid #e3e6ec; border-radius: 12px; }}
QScrollArea#aiScroll, QWidget#aiMessages {{ background: transparent; }}
QLabel#aiHero {{ background: #ebeffd; border-radius: 28px; }}
QFrame#aiStarter {{ background: #f7f8fb; border: 1px solid #e3e6ec; border-radius: 12px; }}
QFrame#aiStarter:hover {{ border-color: {PRIMARY}; }}
QPushButton#aiStarterQuestion {{ background: white; border: 1px solid #e3e6ec; border-radius: 8px; padding: 0 12px;
    color: {INK}; text-align: left; min-height: 38px; max-height: 38px; }}
QPushButton#aiStarterQuestion:hover {{ background: #edf0fd; border-color: {PRIMARY}; }}
QLabel#chatNote {{ background: #fff5d9; color: #8a5a00; border-radius: 6px; padding: 4px 8px; }}
QLabel#chatIssue {{ background: #fdecec; color: #c62828; border-radius: 6px; padding: 4px 8px; }}
QLabel#chatStale {{ background: #eef0f3; color: {SECONDARY}; border-radius: 6px; padding: 3px 8px; }}
QTableWidget#chatTable {{ background: white; border: 1px solid #dee0e5; border-radius: 10px; gridline-color: #eef0f3; }}
"""


def _wrap(text: str, size: int = 13, color: str | None = None, *, bold: bool = False, name: str | None = None) -> QLabel:
    result = label(text, size, medium=bold, color=color, wrap=True)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    result.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    if name:
        result.setObjectName(name)
    return result


def _chip(text: str, on_click) -> QPushButton:
    chip = QPushButton(text)
    chip.setObjectName("aiQuick")
    chip.setCursor(Qt.CursorShape.PointingHandCursor)
    chip.clicked.connect(lambda *_: on_click(text))
    return chip


def table_widget(table: Table) -> QTableWidget:
    """답의 표. 판정 칸은 행 색(tones)으로 배지처럼 칠한다. 8행이 넘으면 표 안에서 스크롤한다."""
    widget = QTableWidget(len(table.rows), len(table.columns))
    widget.setObjectName("chatTable")
    widget.setHorizontalHeaderLabels(list(table.columns))
    widget.verticalHeader().setVisible(False)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setWordWrap(False)
    judge = len(table.columns) - 1
    for r, row in enumerate(table.rows):
        tone = table.tones[r] if r < len(table.tones) else None
        for c, value in enumerate(row):
            item = QTableWidgetItem(value)
            if c == judge and tone in _TONES:
                background, foreground = _TONES[tone]
                item.setBackground(QColor(background))
                item.setForeground(QColor(foreground))
            widget.setItem(r, c, item)
    header = widget.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(True)
    rows = min(len(table.rows), 8)
    widget.setFixedHeight(header.sizeHint().height() + max(rows, 1) * 30 + 4)
    return widget


def bar_chart(chart: BarChart) -> QChartView:
    """구역별 달성률 막대. 강조할 막대(가장 낮은 곳 등)는 다른 색으로 쌓아 그린다."""
    normal, picked = QBarSet("달성률"), QBarSet("강조")
    normal.setColor(QColor("#c9a54c"))
    picked.setColor(QColor("#b23a48"))
    for index, value in enumerate(chart.values):
        value = 0.0 if value is None else float(value)
        normal.append(0.0 if index in chart.highlight else value)
        picked.append(value if index in chart.highlight else 0.0)
    series = QStackedBarSeries()
    series.append(normal)
    series.append(picked)
    series.setLabelsVisible(True)
    view = _chart_view(chart.title)
    view.chart().legend().hide()                 # 강조는 색으로 보인다 (범례 '달성률·강조'는 혼란만 준다)
    view.chart().addSeries(series)
    axis_x = QBarCategoryAxis()
    axis_x.append(list(chart.labels))
    axis_y = QValueAxis()
    axis_y.setRange(0, 100)
    axis_y.setLabelFormat("%d%%")
    _attach(view.chart(), series, axis_x, axis_y)
    return view


def line_chart(chart: LineChart) -> QChartView:
    """노선도: 실적은 실선, 예측은 점선(마지막 실적 점에서 이어 그림). 전체 평균은 회색 점선."""
    view = _chart_view(chart.title)
    axis_x, axis_y = QDateTimeAxis(), QValueAxis()
    axis_x.setFormat("yy.MM")
    axis_y.setRange(0, 100)
    axis_y.setLabelFormat("%d%%")
    days = sorted({point.day for _, points in chart.series for point in points if point.rate is not None})
    axis_x.setTickCount(max(2, min(len(days), 8)))   # 기준일 수만큼만 눈금 (같은 달이 겹쳐 보이지 않게)
    colors = (QColor(PRIMARY), QColor("#9aa1ad"))
    for index, (name, points) in enumerate(chart.series):
        actual, projected = QLineSeries(), QLineSeries()
        actual.setName(name)
        projected.setName(f"{name} · 예측") if index == 0 else None
        last = None
        for point in points:
            if point.rate is None:
                continue
            x = QDateTime(point.day, QTime(0, 0)).toMSecsSinceEpoch()
            if point.projected:
                if last is not None and projected.count() == 0:
                    projected.append(*last)
                projected.append(x, point.rate)
            else:
                actual.append(x, point.rate)
                last = (x, point.rate)
        pen = QPen(colors[min(index, 1)])
        pen.setWidth(3 if index == 0 else 2)
        if index > 0:
            pen.setStyle(Qt.PenStyle.DashLine)
        actual.setPen(pen)
        dashed = QPen(colors[min(index, 1)])
        dashed.setWidth(3)
        dashed.setStyle(Qt.PenStyle.DashLine)
        projected.setPen(dashed)
        for series in (actual, projected):
            if series.count():
                view.chart().addSeries(series)
                _attach(view.chart(), series, axis_x, axis_y)
    return view


def _chart_view(title: str) -> QChartView:
    chart = QChart()
    chart.setTitle(title)
    chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
    chart.setBackgroundRoundness(10)
    view = QChartView(chart)
    view.setStyleSheet("background: transparent; border: none;")
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    view.setMinimumHeight(260)
    return view


def _attach(chart: QChart, series, axis_x, axis_y) -> None:
    if axis_x not in chart.axes():
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
    series.attachAxis(axis_x)
    series.attachAxis(axis_y)


class AnswerCard(QWidget):
    """답 카드 하나: 요청 인식 칩·조건 → 요약 → 이해 못 한 조건 → 표·그래프 → 원인 → 주의 → 추천 질문."""

    def __init__(self, result: ChatAnswer, on_question, on_export=None, on_report=None):
        super().__init__()
        self.answer = result
        self.export_button = self.report_button = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        avatar = _sparkle(36)
        avatar.setObjectName("aiAvatar")
        row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
        frame = QFrame()
        frame.setObjectName("aiAnswer")
        body = QVBoxLayout(frame)
        body.setContentsMargins(22, 18, 22, 18)
        body.setSpacing(12)
        header = QHBoxLayout()
        header.setSpacing(6)
        header.addWidget(label("요청 인식", 12, medium=True, color="rgba(20,32,51,0.6)"))
        self.intent_tags = [_tag(INTENT_LABELS[i]) for i in result.request.intents]
        for tag in self.intent_tags:
            header.addWidget(tag)
        header.addStretch(1)
        self.stale = _wrap("이전 자료 기준 · 다시 물어보세요", 12, name="chatStale")
        self.stale.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.stale.hide()
        header.addWidget(self.stale, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(header)
        self.summary = _wrap(result.summary, 14, INK)
        body.addWidget(self.summary)
        for issue in result.issues:
            body.addWidget(_wrap(f"이해했지만 반영하지 못한 조건 · {issue}", 12, name="chatIssue"))
        self.tables, self.charts = [], []
        charts = QHBoxLayout()
        charts.setSpacing(12)
        for chart in result.charts:
            view = bar_chart(chart) if isinstance(chart, BarChart) else line_chart(chart)
            self.charts.append(view)
            charts.addWidget(view, 1)
        if self.charts:
            body.addLayout(charts)
        if result.cause:
            title = QHBoxLayout()
            title.addWidget(label("원인 분석", 13, medium=True))
            title.addStretch(1)
            body.addLayout(title)
            body.addWidget(_wrap(result.cause, 13, INK))
        for table in result.tables:
            if result.cause and table.title.startswith("원인 근거"):
                continue                                   # 원인 문단과 같은 내용이라 표는 생략
            body.addWidget(label(table.title, 13, medium=True))
            if not table.rows:
                body.addWidget(_wrap("조건에 맞는 설비가 없습니다.", 13, SECONDARY))
                continue
            widget = table_widget(table)
            self.tables.append(widget)
            body.addWidget(widget)
        if result.report is not None:
            body.addWidget(self._report_card(result, on_report))
        for notice in result.notices:
            body.addWidget(_wrap(f"※ {notice}", 12, "#8a5a00"))
        if result.suggestions:
            body.addWidget(_WrapRow([_chip(question, on_question) for question in result.suggestions], 240))
        actions = QHBoxLayout()
        actions.addStretch(1)
        intents = set(result.request.intents)
        if on_export is not None and Intent.SEARCH in intents and not result.issues and result.tables:
            self.export_button = button("Excel 내보내기")
            self.export_button.clicked.connect(lambda *_: on_export(self))
            actions.addWidget(self.export_button)
        analysis = intents & {Intent.COMPARE, Intent.CHANGE, Intent.CAUSE, Intent.PRIORITY, Intent.FORECAST}
        if on_report is not None and analysis and not result.issues and result.report is None:
            self.report_button = button("보고서로 저장", primary=True)
            self.report_button.clicked.connect(lambda *_: on_report(self))
            actions.addWidget(self.report_button)
        if self.export_button or self.report_button:
            body.addLayout(actions)
        row.addWidget(frame, 1)

    def _report_card(self, result: ChatAnswer, on_report) -> QFrame:
        """'보고서 만들어줘' 답의 파일 카드 (화면 10-3): 파일 이름 · 구성 · Word로 저장."""
        card = QFrame()
        card.setObjectName("aiStarter")
        line = QHBoxLayout(card)
        line.setContentsMargins(16, 12, 16, 12)
        line.setSpacing(12)
        text = QVBoxLayout()
        month = result.base_date.strftime("%Y-%m") if result.base_date else ""
        text.addWidget(label(f"SETRON_표준화_보고서_{month}.docx", 14, medium=True))
        text.addWidget(_wrap(" · ".join(f"{i}. {s}" for i, s in enumerate(result.report.sections, 1)), 12, SECONDARY))
        line.addLayout(text, 1)
        if on_report is not None and not result.issues:
            self.report_button = button("Word로 저장", primary=True)
            self.report_button.clicked.connect(lambda *_: on_report(self))
            line.addWidget(self.report_button, 0, Qt.AlignmentFlag.AlignVCenter)
        return card

    def mark_stale(self):
        self.stale.show()


class PendingCard(QWidget):
    """분석 중: 단계 표시와 중지. 워커가 진행률을 알려 주지 않으므로 마지막 단계는 답이 와야 끝난다."""

    stop_requested = Signal()

    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        avatar = _sparkle(36)
        avatar.setObjectName("aiAvatar")
        row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
        frame = QFrame()
        frame.setObjectName("aiAnswer")
        body = QVBoxLayout(frame)
        body.setContentsMargins(22, 18, 22, 18)
        top = QHBoxLayout()
        self.title = label("분석 중입니다…", 14, medium=True, color=PRIMARY)
        top.addWidget(self.title)
        top.addStretch(1)
        self.stop_button = button("■ 중지")
        self.stop_button.clicked.connect(self.stop_requested.emit)
        top.addWidget(self.stop_button)
        body.addLayout(top)
        self.steps = [label(f"○ {step}", 13, color=SECONDARY) for step in STEPS]
        for step in self.steps:
            body.addWidget(step)
        row.addWidget(frame, 1)
        self.done = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)
        self.timer.start(350)
        self._advance()

    def _advance(self):
        if self.done < len(STEPS) - 1:
            self.steps[self.done].setText(f"✓ {STEPS[self.done]}")
            self.steps[self.done].setStyleSheet(f"color: {INK};")
            self.done += 1
            self.steps[self.done].setText(f"◐ {STEPS[self.done]}")
            self.steps[self.done].setStyleSheet(f"color: {PRIMARY};")

    def finish(self, text: str):
        self.timer.stop()
        self.stop_button.hide()
        self.title.setText(text)
        self.title.setStyleSheet(f"color: {SECONDARY};")


class StarterCard(QFrame):
    """처음 화면의 예시 카드: 분류 태그, 설명, 누르면 바로 보내는 질문 2개."""

    def __init__(self, title: str, caption: str, questions, on_question):
        super().__init__()
        self.setObjectName("aiStarter")
        body = QVBoxLayout(self)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_tag(title))
        head.addStretch(1)
        body.addLayout(head)
        self.buttons = []
        for question in questions:
            item = QPushButton(question)
            item.setObjectName("aiStarterQuestion")
            item.setCursor(Qt.CursorShape.PointingHandCursor)
            item.setMinimumHeight(40)
            item.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            item.clicked.connect(lambda *_, q=question: on_question(q))
            self.buttons.append(item)
            body.addWidget(item)


class EmptyState(QWidget):
    """질문 전 화면: 인사, 지금 자료 요약, 예시 카드 4개, 이어 묻기 안내."""

    def __init__(self, overview: str, on_question, starters=STARTERS):
        super().__init__()
        column = QVBoxLayout(self)
        column.setContentsMargins(24, 32, 24, 24)
        column.setSpacing(10)
        hero = QLabel()
        hero.setObjectName("aiHero")
        hero.setFixedSize(56, 56)
        hero.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero.setPixmap(icon(SPARKLE).pixmap(28, 28))
        column.addWidget(hero, 0, Qt.AlignmentFlag.AlignHCenter)
        title = label("무엇을 분석해 드릴까요?", 22, medium=True)
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(title)
        self.overview = label(overview, 13, color=SECONDARY)
        self.overview.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self.overview)
        column.addSpacing(12)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self.cards = [StarterCard(t, c, q, on_question) for t, c, q in starters]
        for index, card in enumerate(self.cards):
            grid.addWidget(card, index // 2, index % 2)
        holder = QWidget()
        holder.setMaximumWidth(920)
        holder.setLayout(grid)
        column.addWidget(holder, 0, Qt.AlignmentFlag.AlignHCenter)


class ChatPage(QWidget):
    """AI 분석 대화 화면. 앱 연결부는 기존 AI 화면과 같은 이름(refresh·invalidate_data·close·신호)을 쓴다.

    param
    - service: ``ChatService`` 같은 객체. ``ask(question, previous) -> ChatAnswer``,
      있으면 ``overview()``(처음 화면 자료 요약)·``connected``(회사 AI 연결 여부)도 쓴다.
    - analysis_allowed: False면 질문을 보내지 않는다(관리 데이터 저장 중 등).
    """

    navigate_requested = Signal(str)
    fresh_data_ready = Signal(object)          # 기존 AI 화면 연결 호환용 (대화 화면은 쓰지 않는다)

    def __init__(self, service, *, analysis_allowed=None):
        super().__init__()
        self.service = service
        self.analysis_allowed = analysis_allowed or (lambda: True)
        self.previous = None
        self.cards: list[AnswerCard] = []
        self.pending: PendingCard | None = None
        self.request = ReadRequest(self)
        self.request.completed.connect(self._completed)
        self.setStyleSheet(_CHAT_STYLE)
        page = QVBoxLayout(self)
        page.setContentsMargins(0, 4, 0, 0)
        conversation = QFrame()
        conversation.setObjectName("aiConversation")
        inside = QVBoxLayout(conversation)
        inside.setContentsMargins(0, 0, 0, 0)
        inside.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("aiScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder.setObjectName("aiMessages")
        self.messages = QVBoxLayout(holder)
        self.messages.setContentsMargins(24, 20, 24, 20)
        self.messages.setSpacing(18)
        self.confirm = lambda text: QMessageBox.question(self, "새 대화", text) == QMessageBox.StandardButton.Yes
        self.open_dialog = lambda dialog: dialog.exec() == QDialog.DialogCode.Accepted
        self.starters = self._starters()
        self.empty = EmptyState(self._overview(), self.send, self.starters)
        self.messages.addWidget(self.empty)
        self.messages.addStretch(1)
        self.scroll.setWidget(holder)
        self.scroll.setMinimumHeight(360)
        inside.addWidget(self.scroll, 1)
        area = QFrame()
        area.setObjectName("aiInputArea")
        bottom = QVBoxLayout(area)
        bottom.setContentsMargins(20, 12, 20, 16)
        bottom.setSpacing(8)
        self.quick_chips = []
        for title, with_previous, alone in QUICK:
            chip = QPushButton(title)
            chip.setObjectName("aiQuick")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda *_, a=with_previous, b=alone: self.send(a if self.previous else b))
            self.quick_chips.append(chip)
        self.faq_button = QPushButton("자주 하는 질문")
        self.faq_button.setObjectName("aiQuick")
        self.faq_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.faq_menu = QMenu(self.faq_button)
        self.faq_actions = [self.faq_menu.addAction(question)
                            for _, _, questions in self.starters for question in questions]
        for action in self.faq_actions:
            action.triggered.connect(lambda *_, q=action.text(): self.send(q))
        self.faq_button.setMenu(self.faq_menu)
        chips_row = QWidget()
        line = QHBoxLayout(chips_row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        heading = label("바로 분석", 12, medium=True, color=SECONDARY)
        line.addWidget(heading)
        for chip in self.quick_chips:
            line.addWidget(chip)
        line.addStretch(1)
        line.addWidget(self.faq_button)
        self.new_button = QPushButton("↺ 새 대화")
        self.new_button.setObjectName("aiQuick")
        self.new_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_button.setToolTip("지금 대화를 지우고 처음 화면으로 돌아갑니다")
        self.new_button.clicked.connect(lambda *_: self.new_conversation())
        line.addWidget(self.new_button)
        chips_row.hide()                                 # 첫 답이 나온 뒤 보인다 (처음엔 예시 카드)
        self.chip_rows = [chips_row]
        bottom.addWidget(chips_row)
        box = QFrame()
        box.setObjectName("aiInputBox")
        line = QHBoxLayout(box)
        line.setContentsMargins(14, 6, 6, 6)
        line.addWidget(_sparkle(18))
        self.input = QLineEdit()
        self.input.setPlaceholderText("질문을 입력하세요. 예) 화성 C3 버전은 언제 통일될까?")
        self.input.returnPressed.connect(lambda: self.send())
        line.addWidget(self.input, 1)
        self.send_button = button("전송", primary=True)
        self.send_button.clicked.connect(lambda *_: self.send())
        line.addWidget(self.send_button)
        bottom.addWidget(box)
        self.status = _wrap("", 12, SECONDARY)
        self.status.hide()
        self._blocked_notice = False
        bottom.addWidget(self.status)
        inside.addWidget(area)
        page.addWidget(conversation, 1)

    # --- 창 높이에 맞추기 ---------------------------------------------------------------------
    # 앱 창은 화면을 내용 높이만큼 쌓고 아래를 비운다. 대화 화면은 보이는 높이까지 카드를 늘린다.
    def _outer_scroll(self):
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea) and parent is not self.scroll:
                return parent
            parent = parent.parentWidget()
        return None

    def showEvent(self, event):
        super().showEvent(event)
        outer = self._outer_scroll()
        if outer is not None and not getattr(self, "_watching", False):
            outer.viewport().installEventFilter(self)
            self._watching = True
        QTimer.singleShot(0, self._fit_height)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._fit_height)
        return False

    def _fit_height(self):
        outer = self._outer_scroll()
        if outer is None or not self.isVisible() or outer.widget() is None:
            return
        top = self.mapTo(outer.widget(), QPoint(0, 0)).y()
        self.setMinimumHeight(max(560, outer.viewport().height() - top - 24))

    @property
    def notice_text(self) -> str:
        """앱 위쪽 안내 배지. 실제 AI 연결 여부를 그대로 보여 준다."""
        if getattr(self.service, "connected", False):
            return "AI 분석 · 실제 AI 연결 · 숫자는 대시보드와 같은 DB 집계 · 대시보드 조건을 바꾸지 않음"
        return "AI 분석 · AI 미연결(원인 설명·문장 다듬기 없이 집계로 답함) · 대시보드 조건을 바꾸지 않음"

    # --- 내보내기 · 보고서 ----------------------------------------------------------------
    def _export(self, card: "AnswerCard"):
        """답의 조회 결과를 Excel로 (화면 10-1)."""
        dialog = ExcelExportDialog(self.service.export_data(card.answer), self)
        if self.open_dialog(dialog) and dialog.result is not None:
            self._notice(f"Excel로 저장했어요 · {dialog.result.path}")
        return dialog

    def _report(self, card: "AnswerCard"):
        """답을 Word 보고서로 (화면 10-2). '보고서 만들어줘' 답이면 같은 범위의 여러 분석을 각 장으로 넣는다."""
        if card.answer.report is not None:
            answers = self.service.report_parts(card.answer)
            parts = [ReportPart(a, tuple(chart_png(self._chart(c), (900, 420)) for c in a.charts)) for a in answers]
            month = card.answer.base_date.strftime("%Y-%m") if card.answer.base_date else ""
            dialog = ReportDialog(parts, title=f"SETRON 표준화 보고서 {month}".strip(), parent=self)
        else:
            index = self.cards.index(card)
            previous = [ReportPart(c.answer, tuple(chart_png(v) for v in c.charts)) for c in self.cards[:index]
                        if c.answer.request.understood and not c.answer.issues]
            dialog = ReportDialog([ReportPart(card.answer, tuple(chart_png(v) for v in card.charts))],
                                  previous=previous, parent=self)
        if self.open_dialog(dialog) and dialog.result is not None:
            self._notice(f"보고서를 저장했어요 · {dialog.result.path}")
        return dialog

    @staticmethod
    def _chart(chart):
        return bar_chart(chart) if isinstance(chart, BarChart) else line_chart(chart)

    def _starters(self):
        try:
            return self.service.starters() or STARTERS
        except Exception:
            return STARTERS

    def new_conversation(self, *, ask: bool = True) -> bool:
        """대화를 지우고 처음 화면으로 돌아간다. 진행 중인 분석은 먼저 멈춘다 (답은 저장되지 않는다)."""
        if (self.cards or self.pending is not None) and ask and not self.confirm("지금 대화를 지우고 처음 화면으로 갈까요?"):
            return False
        if self.pending is not None:
            self.request.cancel()
            self.pending = None
        while self.messages.count() > 1:                  # 마지막은 아래로 밀어 주는 빈 칸
            item = self.messages.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.cards, self.previous = [], None
        self.starters = self._starters()
        self.empty = EmptyState(self._overview(), self.send, self.starters)
        self.messages.insertWidget(0, self.empty)
        for row in self.chip_rows:
            row.hide()
        self.input.clear()
        self._notice("")
        self._sync()
        return True

    def _overview(self) -> str:
        try:
            return self.service.overview()
        except Exception:
            return "SW 버전 현황을 평소 말투로 물어보세요."

    # --- 동작 -----------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self.pending is not None

    def prefill(self, question: str) -> bool:
        """다른 화면의 질문 초안만 전달한다. 기존 대화·조회 조건을 바꾸거나 자동 전송하지 않는다."""
        if self.busy:
            return False
        self.input.setText(question)
        self.input.setFocus()
        return True

    def send(self, text: str | None = None) -> bool:
        """질문을 보낸다. 처리 중이거나 관리 데이터 저장 중이면 보내지 않는다."""
        question = (text if text is not None else self.input.text()).strip()
        if not question or self.busy:
            return False
        if not self.analysis_allowed():
            self._notice("관리 데이터를 저장하거나 다시 읽는 중이라 지금은 분석할 수 없습니다. 잠시 후 다시 보내 주세요.")
            return False
        self._notice("")
        self.input.clear()
        self.empty.hide()
        bubble = _UserBubble()
        self._append(bubble)
        bubble.show_question(question)
        self.pending = PendingCard()
        self.pending.stop_requested.connect(self.stop)
        self._append(self.pending)
        service, previous = self.service, self.previous
        self.request.start(BackgroundRead(lambda: service.ask(question, previous)))
        self._sync()
        return True

    def stop(self):
        """진행 중인 질문의 답을 받지 않는다 (워커는 끝까지 돌지만 결과를 버린다)."""
        if self.pending is None:
            return
        self.request.cancel()
        self.pending.finish("중지했습니다. 다시 물어보세요.")
        self.pending = None
        self._sync()

    def invalidate_data(self):
        """자료가 바뀌었다: 진행 중인 답을 버리고, 이미 받은 답에 '이전 자료 기준'을 표시한다."""
        if self.pending is not None:
            self.request.cancel()
            self.pending.finish("자료가 바뀌어 중지했습니다. 다시 물어보세요.")
            self.pending = None
        for card in self.cards:
            card.mark_stale()
        self.previous = None
        self._sync()

    def refresh(self, *_):
        if self.empty.isVisible():
            self.empty.overview.setText(self._overview())
        self._sync()

    def close(self):
        self.request.cancel()
        return super().close()

    def _completed(self, result, error):
        pending, self.pending = self.pending, None
        if pending is not None:
            pending.timer.stop()
            pending.hide()
            pending.deleteLater()
        if error is not None or result is None:
            self._notice(f"답을 만들지 못했습니다: {error}" if error else "답을 만들지 못했습니다. 다시 물어보세요.")
        else:
            card = AnswerCard(result, self.send, self._export, self._report)
            self.cards.append(card)
            self._append(card)
            for row in self.chip_rows:
                row.show()
            self.previous = result.request if result.request.understood else self.previous
        self._sync()

    # --- 화면 ---------------------------------------------------------------------------
    def _append(self, widget):
        self.messages.insertWidget(self.messages.count() - 1, widget)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))

    def _notice(self, text: str):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _sync(self):
        allowed = self.analysis_allowed()
        for widget in (self.send_button, self.input, self.faq_button, *self.quick_chips):
            widget.setEnabled(allowed and not self.busy)
        if not allowed:
            self._notice("관리 데이터를 저장하거나 다시 읽는 중이라 지금은 분석할 수 없습니다.")
            self._blocked_notice = True
        elif self._blocked_notice:
            self._notice("")
            self._blocked_notice = False
