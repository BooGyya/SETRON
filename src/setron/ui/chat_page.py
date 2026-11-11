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
from PySide6.QtCore import QDateTime, QEvent, QObject, QPoint, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget, QProgressBar,
)

from setron.features.chat.answers import PERIOD_TITLE, UNSUPPORTED_TITLE
from setron.features.chat.classify import CLARIFY_TITLE, OUT_OF_SCOPE_TITLE
from setron.features.chat.report import ReportPart
from setron.features.chat.schemas import INTENT_LABELS, BarChart, ChatAnswer, Intent, LineChart, Table
from setron.ui.appearance import ui_color
from setron.ui.ai_page import _STYLE, SPARKLE, _UserBubble, _sparkle, _tag
from setron.ui.chat_export import ExcelExportDialog, ReportDialog, chart_png
from setron.ui.chat_components import AnswerActions, asset_label, avatar, flow_row, SavedFileToast, VerdictDelegate, tron_pixmap
from setron.ui.chat_report_preview import PdfPreviewDialog
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
QToolButton#chatBasisToggle {{ border: none; background: transparent; color: {SECONDARY}; padding: 2px 0; font-weight: 500; }}
QToolButton#chatBasisToggle:hover {{ color: {INK}; }}
QLabel#chatBasisLine {{ color: {SECONDARY}; padding: 1px 0 1px 18px; }}
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
    """실제 텍스트는 유지하고 판정 칸만 둥근 배지로 그린다. 긴 표는 내부 스크롤한다."""
    widget = QTableWidget(len(table.rows), len(table.columns))
    widget.setObjectName("chatTable")
    widget.setHorizontalHeaderLabels(list(table.columns))
    widget.setStyleSheet("QHeaderView::section {background:#f4f5f8; color:#5b6880; border:0; border-bottom:1px solid #dee0e5; padding:6px;}")
    widget.verticalHeader().setVisible(False)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setWordWrap(False)
    widget.setItemDelegate(VerdictDelegate(widget))
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    widget.verticalHeader().setDefaultSectionSize(36)
    judge = len(table.columns) - 1
    for r, row in enumerate(table.rows):
        tone = table.tones[r] if r < len(table.tones) else None
        for c, value in enumerate(row):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            if c == 0:
                weight = item.font()
                weight.setBold(True)
                item.setFont(weight)
            if c == judge and tone in VerdictDelegate.COLORS:
                item.setData(Qt.ItemDataRole.UserRole, tone)
            if c:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            widget.setItem(r, c, item)
    header = widget.horizontalHeader()
    header.setMinimumSectionSize(90)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    rows = min(len(table.rows), 8)
    widget.setFixedHeight(header.sizeHint().height() + max(rows, 1) * 36 + 18)
    return widget


def bar_chart(chart: BarChart) -> QChartView:
    """구역별 달성률 막대. 강조할 막대(가장 낮은 곳 등)는 다른 색으로 쌓아 그린다."""
    normal, picked = QBarSet("달성률"), QBarSet("강조")
    normal.setColor(ui_color("#c9a54c"))
    picked.setColor(ui_color("#b23a48"))
    for index, value in enumerate(chart.values):
        value = 0.0 if value is None else float(value)
        normal.append(0.0 if index in chart.highlight else value)
        picked.append(value if index in chart.highlight else 0.0)
    series = QStackedBarSeries()
    series.append(normal)
    series.append(picked)
    series.setLabelsVisible(all(value is not None for value in chart.values))
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
    colors = (ui_color(PRIMARY), ui_color("#9aa1ad"))
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

    def __init__(self, result: ChatAnswer, on_question, on_export=None, on_report=None, on_pdf=None, on_navigate=None):
        super().__init__()
        self.answer = result
        self.on_navigate = on_navigate
        self.export_button = self.report_button = self.pdf_button = None
        self.result_count = self.expand_button = self.footer = None
        self.is_stale = False
        search_tables = tuple(t for t in result.tables if t.title == "조회 결과")
        empty_search = (Intent.SEARCH in result.request.intents and not result.issues
                        and bool(search_tables) and not any(t.rows for t in search_tables))
        unavailable = (bool(result.request.intents) and not empty_search and not result.equipment_ids
                       and not result.charts and not result.report and not result.cause
                       and (not result.tables or not any(t.rows for t in result.tables)))
        guide = result.title in (CLARIFY_TITLE, UNSUPPORTED_TITLE, PERIOD_TITLE)     # 자료가 없어서가 아닌 안내
        unavailable = unavailable or (result.request.understood and guide)
        self.empty_kind = "unrecognized" if not result.request.understood else "unavailable" if unavailable else None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        if self.empty_kind is None:
            row.addWidget(avatar(), 0, Qt.AlignmentFlag.AlignTop)
        frame = QFrame()
        frame.setObjectName("aiAnswer")
        body = QVBoxLayout(frame)
        body.setContentsMargins(22, 18, 22, 18)
        body.setSpacing(12)
        if self.empty_kind == "unrecognized":
            # 못 알아들은 질문은 빨간 안내 (11/11). 자료와 무관한 질문은 조건 문제가 아니라 '답할 수 없는 질문'.
            banner = ("답할 수 없는 질문 · 설비 SW 버전 자료와 관련 없는 질문이에요" if result.title == OUT_OF_SCOPE_TITLE
                      else "조건 인식 실패 · 조건을 정확히 이해하지 못했어요")
            body.addWidget(_wrap(banner, 13, name="chatIssue"))
        elif self.empty_kind == "unavailable":
            banner = {CLARIFY_TITLE: "확인 질문 · 어떤 뜻인지 아래에서 골라 주세요",
                      UNSUPPORTED_TITLE: "지원하지 않는 요청 · 대신 할 수 있는 것을 안내해요",
                      PERIOD_TITLE: "기간 보고서 · 아래 버튼으로 만들 수 있어요"}.get(
                result.title, "자료 없음 · 조건은 이해했지만 해당 자료가 없어요")
            body.addWidget(_wrap(banner, 13, name="chatStale"))
        self.intent_tags = [_tag(INTENT_LABELS[i]) for i in result.request.intents]
        recognition = flow_row([label("요청 인식", 12, medium=True, color=SECONDARY), *self.intent_tags])
        recognition.setVisible(self.empty_kind is None)
        body.addWidget(recognition)
        # 조건 줄은 화면에서 숨긴다(11/08 팀 결정: 칩만으로 충분). 답 묶음·전체 보기·Excel 조건 시트에는 그대로 쓴다.
        self.conditions = _wrap(result.understood, 12, SECONDARY)
        self.conditions.hide()
        body.addWidget(self.conditions)
        self.stale = _wrap("이전 자료 기준 · 다시 물어보세요", 12, name="chatStale")
        self.stale.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.stale.hide()
        body.addWidget(self.stale)
        self.summary = _wrap(result.summary, 14, INK)
        body.addWidget(self.summary)
        for issue in result.issues:
            body.addWidget(_wrap(f"이해했지만 반영하지 못한 조건 · {issue}", 12, name="chatIssue"))
        self.tables, self.charts = [], []
        chart_cards = []
        for chart in result.charts:
            view = bar_chart(chart) if isinstance(chart, BarChart) else line_chart(chart)
            self.charts.append(view)
            holder = QFrame()
            holder.setStyleSheet("QFrame {background: white; border-radius: 10px;}")
            column = QVBoxLayout(holder)
            column.setContentsMargins(8, 8, 8, 8)
            column.addWidget(view)
            note = getattr(chart, "note", "")
            if isinstance(chart, BarChart) and any(v is None for v in chart.values):
                note = " · ".join(filter(None, (note, "자료 없음: " + ", ".join(n for n, v in zip(chart.labels, chart.values) if v is None))))
            if note:
                column.addWidget(_wrap(note, 11, SECONDARY))
            chart_cards.append(holder)
        if len(chart_cards) == 1:
            body.addWidget(chart_cards[0])          # 하나면 전체 폭 (줄 배치의 '마지막 칸은 오른쪽에 작게' 규칙을 피함)
        elif self.charts:
            body.addWidget(_WrapRow(chart_cards, 360, fields=True))
        if result.cause:
            title = QHBoxLayout()
            title.addWidget(label("원인 분석", 13, medium=True))   # 출처 꼬리표 없이 (Word 보고서에는 출처 표시 유지)
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
            body.addWidget(self._report_card(result, on_report, on_pdf))
        self.basis_toggle, self.basis_body = self._basis(result.notices)
        if self.basis_toggle is not None:
            body.addWidget(self.basis_toggle, 0, Qt.AlignmentFlag.AlignLeft)
            body.addWidget(self.basis_body)
        if result.suggestions:
            body.addWidget(_WrapRow([_chip(question, on_question) for question in result.suggestions], 240))
        self.action_buttons = []
        if result.actions and on_navigate is not None:          # 화면 이동 (예: 보고서 메뉴를 기간을 채워 열기)
            for index, (text, route) in enumerate(result.actions):
                action = button(text, primary=index == 0)
                action.clicked.connect(lambda checked=False, r=route: on_navigate(r))
                self.action_buttons.append(action)
            body.addWidget(flow_row(self.action_buttons))
        self.import_button = None
        if (unavailable or result.issues) and result.request.understood and on_navigate is not None and not guide:
            import_button = self.import_button = button("데이터 가져오기")      # 자료가 정말 없을 때만
            import_button.clicked.connect(lambda: on_navigate("import"))
            body.addWidget(import_button, 0, Qt.AlignmentFlag.AlignLeft)
        result_actions, file_actions = [], []
        if Intent.SEARCH in result.request.intents and search_tables and not result.issues:
            self.result_count = label(f"조건에 맞는 설비 {len(set(result.equipment_ids))}대", 12, color=SECONDARY)
            self.result_count.setToolTip("Excel 내보내기도 이 응답의 설비 ID를 기준으로 합니다.")
            result_actions.append(self.result_count)
            if result.equipment_ids:
                self.expand_button = button("전체 보기 >")
                self.expand_button.clicked.connect(self._all_rows)
                result_actions.append(self.expand_button)
        intents = set(result.request.intents)
        if on_export is not None and Intent.SEARCH in intents and not result.issues and result.tables:
            self.export_button = button("Excel 내보내기")
            self.export_button.clicked.connect(lambda *_: on_export(self))
            file_actions.append(self.export_button)
        analysis = intents & {Intent.COMPARE, Intent.CHANGE, Intent.CAUSE, Intent.PRIORITY, Intent.FORECAST}
        if on_report is not None and analysis and not result.issues and result.report is None:
            self.report_button = button("보고서로 저장", primary=True)
            self.report_button.clicked.connect(lambda *_: on_report(self))
            file_actions.append(self.report_button)
        if result_actions or file_actions:
            self.footer = AnswerActions(result_actions, file_actions)
            body.addWidget(self.footer)
        row.addWidget(frame, 1)

    def _basis(self, notices) -> tuple[QToolButton | None, QFrame | None]:
        """참고 문장을 '판단 근거 N건' 한 줄로 접어 둔다 (11/11: 노란 상자가 답보다 눈에 띄던 문제).

        누르면 펼쳐져 회색 글씨 목록으로 보인다. 처음에는 접혀 있다.
        """
        if not notices:
            return None, None
        toggle = QToolButton()
        toggle.setObjectName("chatBasisToggle")
        toggle.setCheckable(True)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.RightArrow)
        toggle.setText(f"판단 근거 {len(notices)}건")
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setStyleSheet(f"QToolButton {{border: none; background: transparent; color: {SECONDARY}; padding: 2px 0;}}"
                             f"QToolButton:hover {{color: {INK};}}")          # 어디에 놓여도 상자 없는 글자 버튼
        holder = QFrame()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        for notice in notices:
            column.addWidget(_wrap(notice, 12, name="chatBasisLine"))
        holder.hide()

        def flip(opened):
            toggle.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)
            holder.setVisible(opened)
        toggle.toggled.connect(flip)
        return toggle, holder

    def _report_card(self, result: ChatAnswer, on_report, on_pdf) -> QFrame:
        """'보고서 만들어줘' 답의 파일 카드 (화면 10-3): 파일 이름 · 구성 · Word로 저장."""
        card = QFrame()
        card.setObjectName("aiStarter")
        line = QHBoxLayout(card)
        line.setContentsMargins(16, 12, 16, 12)
        line.setSpacing(12)
        line.addWidget(asset_label("d5f0a.svg", slot=52))
        text = QVBoxLayout()
        month = result.base_date.strftime("%Y-%m") if result.base_date else ""
        text.addWidget(_wrap(f"SETRON_표준화_보고서_{month}.docx", 14, bold=True))
        text.addWidget(_wrap(" · ".join(f"{i}. {s}" for i, s in enumerate(result.report.sections, 1)), 12, SECONDARY))
        text.addWidget(_wrap("저장한 보고서는 보고서 탭에서 확인할 수 있습니다.", 11, SECONDARY))
        if self.on_navigate is not None:
            reports = button("보고서 목록 보기")
            reports.clicked.connect(lambda: self.on_navigate("reports"))
            text.addWidget(reports, 0, Qt.AlignmentFlag.AlignLeft)
        line.addLayout(text, 1)
        if on_report is not None and not result.issues:
            self.report_button = button("Word 다운로드", primary=True)
            self.report_button.clicked.connect(lambda *_: on_report(self))
            line.addWidget(self.report_button, 0, Qt.AlignmentFlag.AlignVCenter)
        if on_pdf is not None and not result.issues:
            self.pdf_button = button("PDF")
            self.pdf_button.clicked.connect(lambda: on_pdf(self))
            line.addWidget(self.pdf_button)
        return card

    def _all_rows(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("AI 분석 · 조회 결과 전체 보기")
        dialog.resize(900, 600)
        body = QVBoxLayout(dialog)
        body.addWidget(_wrap(self.answer.understood, 12, SECONDARY))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        for table in self.answer.tables:
            layout.addWidget(label(table.title, 14, medium=True))
            widget = table_widget(table)
            widget.setMinimumHeight(300)
            widget.setMaximumHeight(16777215)
            layout.addWidget(widget)
        scroll.setWidget(content)
        body.addWidget(scroll, 1)
        close = button("닫기")
        close.clicked.connect(dialog.accept)
        body.addWidget(close)
        dialog.exec()

    def mark_stale(self):
        self.is_stale = True
        self.stale.show()
        for widget in (self.export_button, self.report_button, self.pdf_button):
            if widget is not None:
                widget.setEnabled(False)
                widget.setToolTip("자료가 바뀌었습니다. 다시 질문한 뒤 저장해 주세요.")


class _ProgressRelay(QObject):
    """워커 스레드의 진행 알림을 화면 스레드로 넘긴다 (위젯은 화면 스레드에서만 바꾼다)."""

    step = Signal(int)


class PendingCard(QWidget):
    """분석 중: 단계 표시와 중지.

    진행을 알려 주는 서비스면 실제로 끝난 단계만 ✓로 바꾼다(가짜 진행률은 만들지 않는다).
    알려 주지 않는 서비스면 단계는 그대로 두고 답이 와야 끝난다.
    """

    stop_requested = Signal()

    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(avatar(), 0, Qt.AlignmentFlag.AlignTop)
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
        self.note = _wrap("응답을 기다리고 있습니다.", 11, SECONDARY)
        body.addWidget(self.note)
        self.finished = False
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        body.addWidget(self.progress)
        skeletons = []
        for _ in range(2):
            skeleton = QFrame()
            skeleton.setMinimumWidth(230)
            skeleton.setFixedHeight(150)
            bars = QVBoxLayout(skeleton)
            for width in (140, 220, 180):
                bar = QFrame()
                bar.setStyleSheet("background: #e5e8ee; border-radius: 4px;")
                bar.setFixedHeight(16)
                bar.setFixedWidth(width)
                bars.addWidget(bar)
            skeletons.append(skeleton)
        self.skeletons = _WrapRow(skeletons, 260)
        body.addWidget(self.skeletons)
        row.addWidget(frame, 1)
        self.done = 0
        self.timer = QTimer(self)

    def advance(self, done: int):
        """실제로 끝난 단계 수를 받아 표시한다. 끝난 단계 ✓, 지금 단계 ◐, 남은 단계 ○."""
        if self.finished:
            return                                         # 중지했거나 답이 온 뒤 늦게 온 알림은 버린다
        done = max(0, min(done, len(STEPS)))
        for index, (widget, name) in enumerate(zip(self.steps, STEPS)):
            if index < done:
                widget.setText(f"✓ {name}")
                widget.setStyleSheet(f"color: {INK};")
            elif index == done:
                widget.setText(f"◐ {name} 중")
                widget.setStyleSheet(f"color: {PRIMARY};")
            else:
                widget.setText(f"○ {name}")
                widget.setStyleSheet(f"color: {SECONDARY};")
        self.note.setText(f"{done}/{len(STEPS)}단계 완료" if done else "질문을 이해하고 있습니다.")

    def finish(self, text: str):
        self.finished = True
        self.timer.stop()
        self.stop_button.hide()
        self.progress.hide()
        self.skeletons.hide()
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


def _dress_hero(empty) -> None:
    """처음 화면 가운데 별 아이콘을 손 흔드는 트론으로 바꾼다 (11/11).

    처음 화면(EmptyState) 코드는 디자인 잠금 테스트로 보호되므로 그대로 두고, 이름(aiHero)으로 찾아 그림만 바꾼다.
    그림 파일이 없으면 예전 별 아이콘을 그대로 둔다.
    """
    hero = empty.findChild(QLabel, "aiHero")
    picture = tron_pixmap("tron-welcome", 104)
    if hero is None or picture is None:
        return
    hero.setFixedSize(124, 112)
    hero.setPixmap(picture)
    hero.setProperty("tronImage", "tron/tron-welcome.png")
    hero.setStyleSheet("background: transparent;")


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
    report_saved = Signal(object)             # 실제 생성된 경로. 분석 엔진·DB 저장 계약과 별개다.
    fresh_data_ready = Signal(object)          # 기존 AI 화면 연결 호환용 (대화 화면은 쓰지 않는다)

    def __init__(self, service, *, analysis_allowed=None):
        super().__init__()
        self.service = service
        quick = getattr(service, "quick_questions", None)
        if isinstance(quick, set):                  # 바로 분석·예시 질문은 AI 계획 없이 규칙으로 즉시 답한다
            quick.update(text for _, with_previous, alone in QUICK for text in (with_previous, alone))
            quick.update(FAQ)
        self.analysis_allowed = analysis_allowed or (lambda: True)
        self.previous = None
        self._conversation_mode = False
        self.cards: list[AnswerCard] = []
        self.pending: PendingCard | None = None
        self.request = ReadRequest(self)
        self.request.completed.connect(self._completed)
        self.report_request = ReadRequest(self)
        self.report_request.completed.connect(self._report_prepared)
        self._report_context = None
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
        _dress_hero(self.empty)
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
        self.example_chips = [_chip(question, self.send) for _, _, questions in self.starters for question in questions[:1]]
        # 입력창 위는 '바로 분석 · 자주 하는 질문 메뉴 · 새 대화' 한 줄만 둔다(11/08 팀 결정). 예시 칩 줄은 쓰지 않는다.
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
        from setron.ui.chat_components import FlowLayout
        line = FlowLayout(chips_row)
        heading = label("바로 분석", 12, medium=True, color=SECONDARY)
        line.addWidget(heading)
        for chip in self.quick_chips:
            line.addWidget(chip)
        line.addWidget(self.faq_button)
        self.new_button = QPushButton("새 대화")
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
        self.input_icon = _sparkle(18)
        line.addWidget(self.input_icon)
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
        self.retry_button = button("다시 시도")
        self.retry_button.hide()
        self.retry_button.clicked.connect(lambda: self.send(self.last_question))
        bottom.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignRight)
        self.saved_toast = None
        self.last_question = ""
        self.bottom = bottom
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._conversation_mode and event.size().width() != event.oldSize().width():
            QTimer.singleShot(0, self._fit_height)

    def _fit_height(self):
        outer = self._outer_scroll()
        if outer is None or not self.isVisible() or outer.widget() is None:
            return
        top = self.mapTo(outer.viewport(), QPoint(0, 0)).y() + outer.verticalScrollBar().value()
        available = outer.viewport().height() - top - 24
        if self._conversation_mode:
            # 대화 중에는 질문 입력줄이 바깥 스크롤 아래로 밀려나지 않게 고정한다.
            self.setFixedHeight(max(360, available))
        else:
            self.setMaximumHeight(16777215)
            self.setMinimumHeight(max(560, available))

    @property
    def notice_text(self) -> str:
        """앱 위쪽 안내 배지. 실제 AI 연결 여부를 그대로 보여 준다."""
        if getattr(self.service, "connected", False):
            return "AI 분석 · 실제 AI 연결 · 숫자는 대시보드와 같은 DB 집계 · 대시보드 조건을 바꾸지 않음"
        return "AI 분석 · AI 미연결(원인 설명·문장 다듬기 없이 집계로 답함) · 대시보드 조건을 바꾸지 않음"

    # --- 내보내기 · 보고서 ----------------------------------------------------------------
    def _export(self, card: "AnswerCard"):
        """답의 조회 결과를 Excel로 (화면 10-1)."""
        if card.is_stale:
            self._notice("자료가 바뀌었습니다. 다시 질문한 뒤 내보내 주세요.")
            return None
        try:
            dialog = ExcelExportDialog(self.service.export_data(card.answer), self)
        except Exception:
            self._notice("내보낼 자료를 준비하지 못했습니다. 자료를 다시 조회해 주세요.")
            return None
        if self.open_dialog(dialog) and dialog.result is not None:
            self._notice(f"Excel로 저장했어요 · {dialog.result.path}")
            self._saved(dialog.saved_paths, "Excel 내보내기 완료")
        return dialog

    def _report_dialog(self, card: "AnswerCard"):
        """답을 Word 보고서로 (화면 10-2). '보고서 만들어줘' 답이면 같은 범위의 여러 분석을 각 장으로 넣는다."""
        if card.is_stale:
            self._notice("자료가 바뀌었습니다. 다시 질문한 뒤 보고서를 만들어 주세요.")
            return None
        if card.answer.report is not None:
            answers = card.report_parts
            parts = [ReportPart(a, tuple(self._chart_image(c) for c in a.charts)) for a in answers]
            month = card.answer.base_date.strftime("%Y-%m") if card.answer.base_date else ""
            dialog = ReportDialog(parts, title=f"SETRON 표준화 보고서 {month}".strip(), parent=self)
        else:
            index = self.cards.index(card)
            previous = [ReportPart(c.answer, tuple(chart_png(v) for v in c.charts)) for c in self.cards[:index]
                        if c.answer.request.understood and not c.answer.issues and not c.is_stale]
            dialog = ReportDialog([ReportPart(card.answer, tuple(chart_png(v) for v in card.charts))],
                                  previous=previous, parent=self)
        return dialog

    def _report(self, card: "AnswerCard"):
        if self._defer_report(card):
            return None
        try:
            dialog = self._report_dialog(card)
        except Exception:
            self._notice("보고서 자료를 준비하지 못했습니다. 잠시 후 다시 시도해 주세요.")
            return None
        if dialog is None:
            return None
        if self.open_dialog(dialog) and dialog.result is not None:
            self._notice(f"보고서를 저장했어요 · {dialog.result.path}")
            self._saved(dialog.saved_paths, self._saved_title(dialog.saved_paths), report=True)
            if card.answer.report is not None and any(p.suffix == ".docx" for p in dialog.saved_paths):
                card.report_button.setText("다운로드됨")
                card.report_button.setIcon(icon("ai-ver5/62b26.svg"))
                from setron.ui.theme import svg_size
                card.report_button.setIconSize(svg_size("ai-ver5/62b26.svg"))
                card.report_button.setProperty("primary", False)
                card.report_button.setProperty("soft", True)
                card.report_button.style().unpolish(card.report_button)
                card.report_button.style().polish(card.report_button)
        return dialog

    @staticmethod
    def _saved_title(paths):
        suffixes = {p.suffix.lower() for p in paths}
        return "Word 다운로드 완료" if suffixes == {".docx"} else "PDF 다운로드 완료" if suffixes == {".pdf"} else "보고서 저장 완료"

    def _pdf(self, card):
        if self._defer_report(card, pdf=True):
            return None
        try:
            source = self._report_dialog(card)
            if source is None:
                return None
            dialog = PdfPreviewDialog(source.report(), filename=source.files.path(".pdf").name, parent=self)
            source.deleteLater()
            if self.open_dialog(dialog) and dialog.result is not None and dialog.result.saved:
                self._saved((dialog.result.path,), "PDF 다운로드 완료", report=True)
            return dialog
        except Exception:
            self._notice("PDF 미리보기를 준비하지 못했습니다. 다시 시도해 주세요.")
            return None

    def _defer_report(self, card, *, pdf=False):
        """회사 AI를 다시 부를 수 있는 보고서 각 장의 준비는 UI 스레드에서 실행하지 않는다."""
        if card.is_stale or not self.analysis_allowed():
            self._notice("자료가 바뀌었거나 저장 중입니다. 다시 조회한 뒤 보고서를 만들어 주세요.")
            return True
        if self.busy:
            self._notice("진행 중인 분석이 끝나거나 중지된 뒤 다시 선택해 주세요.")
            return True
        if card.answer.report is None or getattr(card, "report_parts", None) is not None:
            return False
        self._report_context = (card, pdf)
        for widget in (card.report_button, card.pdf_button):
            if widget is not None:
                widget.setEnabled(False)
        self.pending = PendingCard()
        self.pending.title.setText("보고서 자료를 준비하고 있습니다…")
        self.pending.stop_requested.connect(self.stop)
        self._append(self.pending)
        service, answer = self.service, card.answer
        self.report_request.start(BackgroundRead(lambda: service.report_parts(answer)))
        self._sync()
        return True

    def _restore_report_actions(self):
        context, self._report_context = self._report_context, None
        if context is not None:
            card, _ = context
            for widget in (card.report_button, card.pdf_button):
                if widget is not None:
                    widget.setEnabled(not card.is_stale)
        return context

    def _report_prepared(self, result, error):
        context = self._restore_report_actions()
        pending, self.pending = self.pending, None
        if pending is not None:
            pending.hide()
            pending.deleteLater()
        self._sync()
        if context is None:
            return
        card, pdf = context
        if card not in self.cards or card.is_stale:
            return
        if error is not None or not result or not all(isinstance(value, ChatAnswer) for value in result):
            self._notice("보고서 자료를 준비하지 못했습니다. 연결 상태를 확인하고 Word 또는 PDF를 다시 선택해 주세요.")
            return
        card.report_parts = tuple(result)
        self._pdf(card) if pdf else self._report(card)

    def _saved(self, paths, title, *, report=False):
        if not paths or not all(p.is_file() for p in paths):
            return
        if self.saved_toast is not None:
            self.messages.removeWidget(self.saved_toast)
            self.saved_toast.deleteLater()
        self.saved_toast = SavedFileToast(paths, title, self)
        self._append(self.saved_toast)
        self.saved_toast.show()
        if report:
            self.report_saved.emit(tuple(paths))

    def _chart_image(self, chart):
        view = self._chart(chart)
        try:
            return chart_png(view, (900, 420))
        finally:
            view.deleteLater()

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
            self.report_request.cancel()
            self._restore_report_actions()
            self.pending.timer.stop()
            self.pending = None
        while self.messages.count() > 1:                  # 마지막은 아래로 밀어 주는 빈 칸
            item = self.messages.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.cards, self.previous = [], None
        self._conversation_mode = False
        self.setMaximumHeight(16777215)
        self.saved_toast = None
        self.starters = self._starters()
        self.empty = EmptyState(self._overview(), self.send, self.starters)
        _dress_hero(self.empty)
        self.messages.insertWidget(0, self.empty)
        for row in self.chip_rows:
            row.hide()
        self.input.clear()
        self.scroll.setMinimumHeight(360)
        self.input_icon.setPixmap(icon(SPARKLE).pixmap(18, 18))
        self.retry_button.hide()
        self._notice("")
        self._sync()
        self._fit_height()
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
        self.retry_button.hide()
        self.last_question = question
        self._conversation_mode = True
        self.input.clear()
        self.scroll.setMinimumHeight(160)
        self.input_icon.setPixmap(icon("9d5d2.svg").pixmap(18, 18))
        self.empty.hide()
        bubble = _UserBubble()
        self._append(bubble)
        bubble.show_question(question)
        self.pending = PendingCard()
        self.pending.stop_requested.connect(self.stop)
        self._append(self.pending)
        self._fit_height()
        service, previous = self.service, self.previous
        if getattr(service, "reports_progress", False):
            old = getattr(self, "_relay", None)
            if old is not None:
                old.deleteLater()                            # 이전 질문의 전달자 정리 (늦은 알림은 report가 무시)
            relay = self._relay = _ProgressRelay(self)      # 화면 스레드에서 만든 전달자 (워커는 신호만 보낸다)
            relay.step.connect(self.pending.advance)
            self.pending.advance(0)

            def report(done, relay=relay):
                try:
                    relay.step.emit(done)
                except RuntimeError:                         # 화면이 먼저 사라진 경우
                    pass
            self.request.start(BackgroundRead(lambda: service.ask(question, previous, progress=report)))
        else:
            self.request.start(BackgroundRead(lambda: service.ask(question, previous)))
        self._sync()
        return True

    def stop(self):
        """진행 중인 질문의 답을 받지 않는다 (워커는 끝까지 돌지만 결과를 버린다)."""
        if self.pending is None:
            return
        self.request.cancel()
        self.report_request.cancel()
        self._restore_report_actions()
        self.pending.finish("중지했습니다. 다시 물어보세요.")
        self.pending = None
        self._sync()

    def invalidate_data(self):
        """자료가 바뀌었다: 진행 중인 답을 버리고, 이미 받은 답에 '이전 자료 기준'을 표시한다."""
        if self.pending is not None:
            self.request.cancel()
            self.report_request.cancel()
            self._restore_report_actions()
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
        self.report_request.cancel()
        return super().close()

    def _completed(self, result, error):
        pending, self.pending = self.pending, None
        if pending is not None:
            pending.timer.stop()
            pending.hide()
            pending.deleteLater()
        if error is not None or result is None:
            self._notice("답을 만들지 못했습니다. 연결 상태를 확인하고 다시 시도해 주세요.")
            self.retry_button.show()
        else:
            card = AnswerCard(result, self.send, self._export, self._report, self._pdf, self.navigate_requested.emit)
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
        self.input.setEnabled(allowed)     # 분석 중에도 다음 질문 초안은 입력할 수 있다. 자동 전송하지 않는다.
        self.input.setPlaceholderText("분석 중에도 다음 질문을 입력할 수 있어요" if self.busy else
                                      "질문을 입력하세요. 예) 화성 C3 버전은 언제 통일될까?")
        for widget in (self.send_button, self.faq_button, self.retry_button, *self.quick_chips, *self.example_chips):
            widget.setEnabled(allowed and not self.busy)
        if not allowed:
            self._notice("관리 데이터를 저장하거나 다시 읽는 중이라 지금은 분석할 수 없습니다.")
            self._blocked_notice = True
        elif self._blocked_notice:
            self._notice("")
            self._blocked_notice = False
