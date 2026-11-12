"""작업 단위 4: 조회 적용과 연결되는 대시보드 한 페이지, 표/구성비/막대/추이 보기."""

from collections import Counter
from html import escape

from PySide6.QtCore import QDate, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout, QPushButton, QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QScrollArea,
    QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from setron.features.aggregation.service import summarize
from setron.features.risk.schemas import RISK_KIND_DESCRIPTIONS, RISK_KIND_LABELS, RiskKind, risk_kinds
from setron.features.version.schemas import CompareStatus
from setron.ui.appearance import readable_color, set_color_style, surface_edge
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.dashboard_charts import (
    DashboardChart, bar_plot, composition_cohorts, equipment_count_tooltip, series_button_style, series_color,
)
from setron.ui.dashboard_data import (
    DashboardDrilldown, DashboardSource, HpaClassificationConflict, NoObservationBefore, RiskRuleState, count_value,
    information_gap_reasons, missing_sw_count, trend_window,
)
from setron.ui.interval_selector import IntervalSelector
from setron.ui.query_panel import QueryPanel
from setron.ui.theme import ACCENT_SOFT, CANVAS, INK, LINE, PRIMARY, SECONDARY, font, icon, set_widget_font, svg_size
from setron.ui.view_controls import SegmentedControl, bulk_selection_toggle_button, set_bulk_selection_state

OUTLINE_BUTTON = (f"QPushButton {{background:white;border:1px solid {LINE};border-radius:8px;padding:0 16px;"
                  "min-height:32px;max-height:32px;}"
                  f"QPushButton:hover {{background:{ACCENT_SOFT};border-color:{PRIMARY};}}"
                  f"QPushButton:focus {{border:2px solid {PRIMARY};}}"
                  "QPushButton:disabled {color:#8b95a8;background:#f9fafb;border-color:#e9eef6;}")


def _outline_button(text, width=118):
    result = button(text)
    result.setStyleSheet(OUTLINE_BUTTON)
    result.setMinimumWidth(width)
    return result


def _divider():
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background:{LINE};border:none;")
    return line


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear_layout(item.layout())


def _equipment_state(status):
    """설비 한 대의 표시 상태. 집계 결과(계열별 판정)를 그대로 읽기만 한다."""
    if status is None or not status.families:
        return "미확인", "dashboard_unknown"
    if status.needs_upgrade:
        return "미달", "below"
    if any(f.status is CompareStatus.UNDETERMINED for f in status.families):
        return "확인 필요", "dashboard_unknown"
    return "달성", "met"


def dashboard_cell_colors(colors, key):
    background = colors[key]
    preferred = {"met": "#246b4a", "below": "#8a6400", "risk": "#ad344d"}.get(key, colors["muted"])
    if colors["ink"] != INK:
        preferred = colors["ink"]
    return background, readable_color(preferred, background)


def _dashboard_badge_style(colors, key, rules):
    background, ink = dashboard_cell_colors(colors, key)
    return f"background:{background};color:{ink};{rules}"


class DashboardGrid(QWidget):
    def __init__(self, widgets, *, minimum_width=260, maximum_columns=4, spacing=16,
                 alignment=Qt.AlignmentFlag(0), captions=None):
        super().__init__()
        self.widgets, self.minimum_width, self.maximum_columns = widgets, minimum_width, maximum_columns
        self._visible_widgets = tuple(widgets)
        self.spacing = spacing
        self.alignment = alignment
        self.captions = captions or {}
        self.columns = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(spacing)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._arrange(maximum_columns)

    def minimumSizeHint(self):
        return QSize(0, self.grid.minimumSize().height())

    def set_visible_widgets(self, widgets):
        visible = tuple(widgets)
        if visible == self._visible_widgets:
            return
        self._visible_widgets = visible
        for widget in self.widgets:
            widget.setVisible(widget in visible)
        self._arrange(self.columns, force=True)

    def _arrange(self, columns, *, force=False):
        if columns == self.columns and not force:
            return
        for widget in (*self.widgets, *self.captions.values()):
            self.grid.removeWidget(widget)
        for index in range(max(columns, self.columns)):
            self.grid.setColumnStretch(index, int(index < columns))
        for index, widget in enumerate(self._visible_widgets):
            row, column = divmod(index, columns)
            if self.captions:
                row *= 2
                if caption := self.captions.get(widget):
                    self.grid.addWidget(caption, row, column)
                row += 1
            self.grid.addWidget(widget, row, column, self.alignment)
        self.columns = columns
        self.updateGeometry()

    def resizeEvent(self, event):
        self._arrange(max(1, min(self.maximum_columns,
                                 (event.size().width() + self.spacing) // (self.minimum_width + self.spacing))))
        super().resizeEvent(event)


class SplitRow(QWidget):
    """넓으면 좌우(810:508), 좁으면 위아래로 쌓는 두 카드 줄."""

    def __init__(self, left, right, *, breakpoint=980):
        super().__init__()
        self.left, self.right, self.breakpoint = left, right, breakpoint
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(18)
        self.box.addWidget(left, 810)
        self.box.addWidget(right, 508)

    def resizeEvent(self, event):
        direction = (QBoxLayout.Direction.LeftToRight if event.size().width() >= self.breakpoint
                     else QBoxLayout.Direction.TopToBottom)
        if self.box.direction() != direction:
            self.box.setDirection(direction)
            self.box.setStretch(0, 810 if direction == QBoxLayout.Direction.LeftToRight else 0)
            self.box.setStretch(1, 508 if direction == QBoxLayout.Direction.LeftToRight else 0)
        super().resizeEvent(event)


class _DashboardViewControls(QWidget):
    """보기 탭과 추이 간격을 한 줄에 두고, 폭이 부족하면 간격만 다음 줄로 보낸다."""

    def __init__(self, tabs, interval_host, interval):
        super().__init__()
        self.tabs, self.interval_host, self.interval = tabs, interval_host, interval
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(16)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.stacked = None
        self._arrange(False)

    def minimumSizeHint(self):
        return QSize(0, self.grid.minimumSize().height())

    def _arrange(self, stacked):
        if self.stacked == stacked:
            return
        self.grid.removeWidget(self.tabs)
        self.grid.removeWidget(self.interval_host)
        self.grid.addWidget(self.tabs, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if stacked:
            self.grid.addWidget(self.interval_host, 1, 0, 1, 2)
        else:
            self.grid.addWidget(self.interval_host, 0, 1)
        self.grid.setColumnStretch(1, 1)
        self.stacked = stacked
        self.updateGeometry()

    def refresh(self):
        needed = self.tabs.sizeHint().width() + self.grid.horizontalSpacing() + self.interval.unwrapped_width()
        visible = not self.interval_host.isHidden()
        stacked = visible and self.width() < needed
        self.grid.setColumnMinimumWidth(1, self.interval.unwrapped_width() if visible and not stacked else 0)
        self._arrange(stacked)

    def resizeEvent(self, event):
        self.refresh()
        super().resizeEvent(event)


class _ChartSeriesControls(QWidget):
    """전체 선택 도구를 필터의 왼쪽에 두고 좁은 폭에서는 위에 놓는다."""

    def __init__(self, tools, series):
        super().__init__()
        self.tools, self.series = tools, series
        self.body = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(12)
        self.body.addWidget(tools, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.body.addWidget(series, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    def minimumSizeHint(self):
        return QSize(0, self.body.minimumSize().height())

    def resizeEvent(self, event):
        width = self.tools.sizeHint().width() + 12 + 2 * self.series.minimum_width + self.series.spacing
        direction = QBoxLayout.Direction.LeftToRight if event.size().width() >= width else QBoxLayout.Direction.TopToBottom
        self.body.setDirection(direction)
        super().resizeEvent(event)


class DashboardKpi(Card):
    """VER4 대시보드 상단의 간결한 지표 카드. 첫 카드(달성률)는 강조색으로 채운다."""

    def __init__(self, title, asset, *, accent=False, show_caption=True, rate_key="rate"):
        super().__init__(padding=18)
        self.body.setSpacing(3)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop if show_caption else Qt.AlignmentFlag.AlignVCenter)
        ink, muted = ("white", "white") if accent else (INK, SECONDARY)
        if accent:
            self.setProperty("appearanceRate", "background")
            self.setProperty("appearanceRateKey", rate_key)
            self.setStyleSheet(f"QFrame#card {{background:{PRIMARY};border:1px solid {PRIMARY};border-radius:12px;}}")
        else:
            self.setStyleSheet("QFrame#card {background:white;border:1px solid white;border-radius:12px;}")
        self.title = label(title, 12, color=muted)
        self.body.addWidget(self.title)
        # 원본 아이콘은 VER4 카드에 표시하지 않지만 다른 화면과 같은 자산 기록을 유지한다.
        self.picture = label("")
        self.picture.setProperty("assetFile", asset)
        self.picture.setPixmap(icon(asset).pixmap(svg_size(asset)))
        self.picture.setFixedSize(24, 24)
        self.picture.hide()
        self.value = label("—", 30, medium=True, color=ink)
        self.value.setProperty("rateValue", True)
        self.value.setMinimumHeight(40)
        self.body.addWidget(self.value)
        self.caption = None
        if show_caption:
            self.caption = label("조회 적용 전", 11, color=muted, wrap=True)
            self.body.addWidget(self.caption)
        else:
            self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)


class DashboardPage(QWidget):
    drilldown_requested = Signal(object)
    trend_period_changed = Signal(object, object)

    def __init__(self, state, source: DashboardSource):
        super().__init__()
        self.setObjectName("canvas")
        self.state, self.source, self.snapshot = state, source, None
        self.trend_snapshot, self.trend_step = None, 1
        self._trend_period_edited = False
        self._applied_trend_period = None  # 보기·간격을 바꿔도 검색으로 확정한 기간을 사용한다.
        self._applied_trend_query = None
        self._trend_reset_pending = False
        self.visible_groups = {o.key for o in source.catalog.groups}
        self.metrics = {}
        self.matrix_buttons, self.risk_buttons = {}, {}
        self.attention_buttons = {}
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        self.result_status = StatusBadge("조회 적용 전 · 예시 데이터", "info")
        body.addWidget(self.result_status)
        self.result_status.hide()  # 상태 문구는 본문 안내(self.message)로 대신 전달한다.
        cards = []
        for key, title, asset, accent in (
            ("rate", "목표 N 이상 달성률", "bbc12.svg", True),
            ("total", "분석 대상", "32ac4.svg", False),
            ("upgrade", "목표 미달", "e53b6.svg", False),
            ("check", "버전 확인 필요", "7880d.svg", False),
            ("risk", "안전·결함 위험 후보", "60dbd.svg", False),
        ):
            card = DashboardKpi(title, asset, accent=accent, show_caption=False)
            self.metrics[key] = card
            cards.append(card)
        self.kpi_grid = DashboardGrid(cards, minimum_width=200, maximum_columns=5, spacing=12)
        body.addWidget(self.kpi_grid)

        # 대시보드의 집계 보기 전환 카드
        self.content = Card(padding=20)
        self.content.body.setSpacing(12)
        self.tabs = SegmentedControl()
        self.tabs.setAccessibleName("대시보드 보기 방식")
        for title in ("히트맵", "원형 그래프", "막대 그래프", "꺾은선 그래프"):
            self.tabs.addTab(title)
        self.trend_filters = QWidget()
        period_body = QVBoxLayout(self.trend_filters)
        period_body.setContentsMargins(0, 0, 0, 0)
        start_field, self.trend_start = QueryPanel._date_field("시작 날짜")
        end_field, self.trend_end = QueryPanel._date_field("종료 날짜")
        self._trend_date_fields = (start_field, end_field)
        for field in self._trend_date_fields:
            field.setParent(self.trend_filters)
            field.hide()
        self.trend_interval = IntervalSelector(compact=True, custom_days=True)
        self.trend_interval.set_step(self.trend_step)
        period_body.addWidget(self.trend_interval)
        for control in (self.trend_start, self.trend_end):
            control.dateChanged.connect(lambda _day, edit=control: self._change_trend_date(edit))
            control.calendarWidget().clicked.connect(lambda day, edit=control: self._confirm_trend_date(edit, day))
            control.calendarWidget().activated.connect(lambda day, edit=control: self._confirm_trend_date(edit, day))
        self.trend_interval.step_changed.connect(self._set_trend_interval)
        self.view_controls = _DashboardViewControls(self.tabs, self.trend_filters, self.trend_interval)
        self.content.body.addWidget(self.view_controls)
        self.trend_notice = label("", 13, color=SECONDARY, wrap=True)
        self.trend_notice.hide()
        self.content.body.addWidget(self.trend_notice)
        self.matrix_legend = QWidget()
        legend_body = QHBoxLayout(self.matrix_legend)
        legend_body.setContentsMargins(0, 0, 0, 0)
        legend_body.setSpacing(16)
        legend_body.addWidget(label("목표 달성률", 12, color=SECONDARY))
        for text, key in (("목표 달성", "met"), ("목표 미달", "below"),
                          ("확인 필요", "dashboard_unknown"), ("위험 후보", "dashboard_risk_marker")):
            dot = QFrame()
            dot.setFixedSize(8, 8)
            set_color_style(dot, lambda colors, k=key: (
                f"background:{colors[k]};border-radius:4px;"
                f"border:1px solid {surface_edge(colors[k], colors['surface'], colors[k])};"))
            item = QHBoxLayout()
            item.setSpacing(6)
            item.addWidget(dot)
            item.addWidget(label(text, 12, color=SECONDARY))
            legend_body.addLayout(item)
        self.content.body.addWidget(self.matrix_legend, alignment=Qt.AlignmentFlag.AlignLeft)
        self.series_widgets = []
        for group in source.catalog.groups:
            check = button(group.label)
            check.setCheckable(True)
            check.setChecked(True)
            set_widget_font(check, font(13, medium=True))
            check.setStyleSheet(series_button_style(series_color(group.key)))
            check.setAccessibleName(group.label + " 차트 표시")
            check.toggled.connect(lambda checked, key=group.key, control=check:
                                  self._toggle_group(key, checked) if control.isEnabled() else None)
            self.series_widgets.append(check)
        self.series_grid = DashboardGrid(self.series_widgets, minimum_width=96, maximum_columns=9, spacing=8)
        self.series_tools = QWidget()
        tools_body = QHBoxLayout(self.series_tools)
        tools_body.setContentsMargins(0, 0, 0, 0)
        tools_body.setSpacing(8)
        self.toggle_all_button = bulk_selection_toggle_button()
        self.toggle_all_button.clicked.connect(self._toggle_all_groups)
        tools_body.addWidget(self.toggle_all_button)
        self.series_controls = _ChartSeriesControls(self.series_tools, self.series_grid)
        self.content.body.addWidget(self.series_controls)
        self.matrix_scroll = QScrollArea()
        self.matrix_scroll.setWidgetResizable(True)
        self.matrix_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content.body.addWidget(self.matrix_scroll)
        self.chart = DashboardChart()
        self.chart_row = QWidget()
        chart_body = QHBoxLayout(self.chart_row)
        chart_body.setContentsMargins(0, 0, 0, 0)
        chart_body.setSpacing(24)
        chart_body.addWidget(self.chart, 1)
        self.composition_legend = QWidget()
        self.composition_legend.setFixedWidth(260)
        self.composition_rows = QVBoxLayout(self.composition_legend)
        self.composition_rows.setContentsMargins(0, 0, 0, 0)
        self.composition_rows.setSpacing(12)
        chart_body.addWidget(self.composition_legend, 0, Qt.AlignmentFlag.AlignVCenter)
        chart_body.addStretch(0)
        self.content.body.addWidget(self.chart_row)
        self.data_toggle = _outline_button("수치표 보기")
        self.data_toggle.setCheckable(True)
        self.data_toggle.toggled.connect(self._show_data_table)
        self.content.body.addWidget(self.data_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.data_table = QTableWidget()
        self.data_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.data_table.setAccessibleName("대시보드 차트 원본 수치표")
        self.data_table.setMinimumHeight(260)
        self.content.body.addWidget(self.data_table)
        self.message = label("위에서 조회 조건을 선택하면 결과를 표시합니다.", 13, color=SECONDARY, wrap=True)
        self.content.body.addWidget(self.message)
        self.legend = label("초록 목표 달성 · 노랑 미달·확인 필요 · 회색 정보 부족 · 빨간 점은 위험 후보(목표 달성과 별도)",
                            11, color=SECONDARY, wrap=True)
        self.content.body.addWidget(self.legend)
        self.definition = label("달성률 = N 이상 / (N 이상 + N 미만), 단위는 설비·계열 건입니다. 판단 불가는 분모에서 제외하고 별도 표시합니다.\n"
                                "HPA는 전체 설비 수와 적용률·공통 분석에서 제외하며, 설비 목록에서 조회할 수 있습니다. 모델별 대수를 더해 물리 대수를 만들지 않습니다.",
                                11, color=SECONDARY, wrap=True)
        self.content.body.addWidget(self.definition)
        self.content.body.addStretch(1)

        # 오른쪽: 확인이 필요한 설비(위험 후보) 목록
        self.attention_card = Card(padding=20)
        self.attention_card.body.setSpacing(8)
        attention_heading = QHBoxLayout()
        attention_heading.setSpacing(8)
        attention_heading.addWidget(label("확인이 필요한 설비", 16, medium=True), 1)
        self.candidate_button = _outline_button("전체 0대")
        self.candidate_button.setAccessibleName("위험 후보 전체 보기")
        self.candidate_button.setToolTip("현재 적용 범위의 위험 후보만 설비 목록으로 엽니다.")
        self.candidate_button.setEnabled(False)
        self.candidate_button.clicked.connect(self._risk_selection)
        attention_heading.addWidget(self.candidate_button)
        self.attention_card.body.addLayout(attention_heading)
        self.attention_caption = label("안전·결함 위험 후보 · 목표 달성과 별도 판정", 12, color=SECONDARY, wrap=True)
        self.attention_card.body.addWidget(self.attention_caption)
        self.attention_list = QVBoxLayout()
        self.attention_list.setSpacing(8)
        self.attention_card.body.addLayout(self.attention_list)
        self.attention_card.body.addStretch(1)
        self.overview = SplitRow(self.content, self.attention_card)
        body.addWidget(self.overview)

        # 아래: 최근 관측 변화 / Site별 현황
        self.observation_card = Card(padding=20)
        self.observation_card.body.setSpacing(12)
        self.observation_card.body.addWidget(label("최근 관측 변화", 16, medium=True))
        self.observation_caption = label("조회 적용 전", 12, color=SECONDARY, wrap=True)
        self.observation_card.body.addWidget(self.observation_caption)
        self.observation_bars = QWidget()
        self.observation_bars_layout = QHBoxLayout(self.observation_bars)
        self.observation_bars_layout.setContentsMargins(0, 0, 0, 0)
        self.observation_bars_layout.setSpacing(20)
        self.observation_card.body.addWidget(self.observation_bars)
        # 날짜별 상세 수치. 막대에 마우스를 올리면 같은 내용을 볼 수 있다.
        self.observations = label("조회 적용 전", 11, color=SECONDARY, wrap=True)
        self.observations.hide()
        self.observation_card.body.addStretch(1)
        self.site_card = Card(padding=20)
        self.site_card.body.setSpacing(12)
        site_heading = QHBoxLayout()
        site_heading.setSpacing(8)
        site_heading.addWidget(label("Site별 현황", 16, medium=True), 1)
        self.site_button = _outline_button("전체 설비")
        self.site_button.setEnabled(False)
        self.site_button.clicked.connect(self._all_selection)
        site_heading.addWidget(self.site_button)
        self.site_card.body.addLayout(site_heading)
        self.site_list = QVBoxLayout()
        self.site_list.setSpacing(12)
        self.site_card.body.addLayout(self.site_list)
        self.site_card.body.addStretch(1)
        self.bottom = SplitRow(self.observation_card, self.site_card)
        body.addWidget(self.bottom)
        body.addStretch(1)
        self.tabs.currentChanged.connect(self._render_view)
        self._render_view()

    def invalidate(self, *_):
        self.snapshot = None
        self.result_status.setText("선택지 변경 · 재조회 필요")
        self.result_status.set_tone("warning")
        self._clear_values()
        self.message.setText("선택지가 변경되었습니다. 위에서 조회 조건을 다시 선택해 주세요.")

    def _clear_values(self):
        self.trend_snapshot = None
        for card in self.metrics.values():
            card.value.setText("—")
        self.candidate_button.setEnabled(False)
        self.candidate_button.setText("전체 0대")
        self.observations.setText("표시할 결과가 없습니다.")
        self._render_view()

    def _result_label(self):
        return getattr(self.source, "result_label", "예시 결과")

    def apply_query(self, query):
        if not self.state.is_current(query):
            return False
        self.snapshot = None
        self._clear_values()
        self.failure_reason = None
        self.result_status.setText(f"{self._result_label()} 계산 중")
        try:
            if not self.state.catalog.is_view_of(self.source.catalog):
                raise ValueError("조회 선택지와 데이터 공급자의 매핑이 다릅니다.")
            result = self.source.snapshot(query)
        except HpaClassificationConflict as error:
            self.result_status.setText("조회 보류 · HPA 분류 확인 필요")
            self.result_status.set_tone("danger")
            self.message.setTextFormat(Qt.TextFormat.PlainText)
            self.message.setText(str(error))
            return False
        except NoObservationBefore as error:
            self.result_status.setText("기준일 이전 자료 없음")
            self.result_status.set_tone("warning")
            self.message.setTextFormat(Qt.TextFormat.PlainText)
            self.message.setText(str(error))
            self.failure_reason = str(error)
            return False
        except (ValueError, RuntimeError):
            self.result_status.setText("결과 표시 실패")
            self.result_status.set_tone("danger")
            self.message.setText("선택지·관측 자료 연결을 확인할 수 없습니다. 데이터 연결과 조회 조건을 확인해 주세요.")
            return False
        return self.accept_result(query, result)

    def accept_result(self, query, result):
        if not self.state.is_current(query) or result.query is not query:
            return False
        self.snapshot = result
        if query is not self._applied_trend_query:
            if self._trend_reset_pending:
                self.trend_step = 1
                self.trend_interval.set_step(1)
                self._trend_reset_pending = False
            self._sync_trend_period(result)
            self._applied_trend_period = (self.trend_start.date().toPython(), self.trend_end.date().toPython())
            self._applied_trend_query = query
        summary = result.summary
        unresolved = sum(1 for status in summary.equipment if _equipment_state(status)[0] in ("미확인", "확인 필요"))
        self.result_status.setText(f"{self._result_label()} · " + ("조건에 맞는 설비 없음" if not result.equipment_ids else "조회 적용됨"))
        self.result_status.set_tone("info")
        values = {
            "total": f"{summary.equipment_count}대",
            "rate": "계산 불가" if summary.rate is None else f"{summary.rate:.1%}",
            "risk": count_value(len(result.risky_ids), result.risk_unknown, "미확인"),
            "upgrade": count_value(summary.upgrade_needed, summary.undetermined, "판단 불가"),
            "check": f"{unresolved}대",
        }
        for key, value in values.items():
            self.metrics[key].value.setText(value)
        self.candidate_button.setText(f"전체 {len(result.risky_ids)}대")
        self.candidate_button.setEnabled(bool(result.risky_ids))
        self._render_view()
        return True

    def _sync_trend_period(self, data):
        end = data.query.selection.as_of or data.base_date
        start = data.observations[0].day if data.observations else end
        for control, default in ((self.trend_start, start), (self.trend_end, end)):
            with QSignalBlocker(control):
                control.setMaximumDate(QDate(end.year, end.month, end.day))
                if not self._trend_period_edited:
                    control.setDate(QDate(default.year, default.month, default.day))
        self.trend_period_changed.emit(self.trend_start.date().toPython(), self.trend_end.date().toPython())

    def set_trend_period(self, start, end):
        self._trend_period_edited = True
        for control, value in ((self.trend_start, start), (self.trend_end, end)):
            with QSignalBlocker(control):
                control.setMaximumDate(QDate(end.year, end.month, end.day))
                control.setDate(QDate(value.year, value.month, value.day))
        self.trend_period_changed.emit(start, end)

    def _change_trend_date(self, control):
        if not control.calendarWidget().isVisible():
            self._trend_period_edited = True
            self.trend_period_changed.emit(self.trend_start.date().toPython(), self.trend_end.date().toPython())

    def _confirm_trend_date(self, control, day):
        with QSignalBlocker(control):
            control.setDate(day)
        self._trend_period_edited = True
        self.trend_period_changed.emit(self.trend_start.date().toPython(), self.trend_end.date().toPython())

    def _set_trend_interval(self, step):
        self.trend_step = step
        self.trend_interval.set_step(step)
        self._render_view()

    def reset_trend_period(self):
        self._trend_period_edited = False
        self._trend_reset_pending = True
        days = self.source.analysis_dates or self.source.dates
        start = min(days, default=self.trend_start.date().toPython())
        end = max(days, default=self.trend_end.date().toPython())
        for control, value in ((self.trend_start, start), (self.trend_end, end)):
            with QSignalBlocker(control):
                control.setMaximumDate(QDate(end.year, end.month, end.day))
                control.setDate(QDate(value.year, value.month, value.day))
        self.trend_period_changed.emit(start, end)

    def _select(self, title, ids, *, risk_only=False):
        if self.snapshot is None or not self.state.is_current(self.snapshot.query) or not ids:
            return
        request = DashboardDrilldown(self.snapshot.query, self.snapshot.base_date, title, tuple(ids), risk_only)
        self.drilldown_requested.emit(request)

    def _risk_selection(self):
        if self.snapshot:
            self._select("현재 적용 범위", self.snapshot.risky_ids, risk_only=True)

    def _all_selection(self):
        if self.snapshot:
            self._select("현재 적용 범위", self.snapshot.equipment_ids)

    def _toggle_group(self, key, checked):
        if checked:
            self.visible_groups.add(key)
        else:
            self.visible_groups.discard(key)
        if self.tabs.currentIndex() in (2, 3):
            self._render_view()

    def _set_all_groups(self, checked):
        for group, control in zip(self.source.catalog.groups, self.series_widgets):
            if control.isEnabled():
                if checked:
                    self.visible_groups.add(group.key)
                else:
                    self.visible_groups.discard(group.key)
        self._render_view()

    def _toggle_all_groups(self):
        available = [control for control in self.series_widgets if control.isEnabled()]
        self._set_all_groups(not all(control.isChecked() for control in available))

    def _render_view(self, *_):
        mode = self.tabs.currentIndex()
        data = self.snapshot
        trend_data, trend_problem = None, ""
        if mode == 3 and data is not None and self._applied_trend_period is not None:
            try:
                trend_data = trend_window(data, *self._applied_trend_period, self.trend_step)
            except ValueError:
                trend_problem = "시작 날짜는 종료 날짜와 같거나 앞서야 합니다."
        self.trend_snapshot = trend_data
        has_trend = trend_data is not None and any(
            point.summary.rate is not None for group in trend_data.trends for point in group.points)
        has_data = data is not None and bool(data.equipment_ids)
        has_analysis = has_data and bool(data.summary.equipment_count)
        self.trend_filters.setVisible(mode == 3)
        self.view_controls.refresh()
        self.trend_filters.setEnabled(data is not None)
        self.trend_notice.setVisible(mode == 3 and has_analysis and not has_trend)
        self.trend_notice.setText(trend_problem or "선택한 기간에 달성률을 계산할 수 있는 관측 자료가 없습니다.")
        bars = bar_plot(data) if has_analysis and mode == 2 else ((), {})
        self.matrix_scroll.setVisible(has_data and mode == 0)
        self.chart.setVisible(has_analysis and mode != 0)
        # Figma: 수치표 보기·정의 설명·위험 후보 목록·관측 변화·Site 요약 없이 보기 카드 하나만 넓게 쓴다.
        self.data_toggle.hide()
        self.candidate_button.hide()
        self.attention_card.hide()
        self.bottom.hide()
        self.legend.hide()
        self.definition.hide()
        self.matrix_legend.setVisible(has_data and mode == 0)
        self.composition_legend.setVisible(has_analysis and mode == 1)
        # 구성비는 도넛 옆에 상태 칩을 붙여 둔다(Figma). 다른 차트는 카드 너비를 다 쓴다.
        self.chart.setMaximumWidth(560 if mode == 1 else 16777215)
        self.chart_row.layout().setStretch(2, 1 if mode == 1 else 0)
        self.site_button.setEnabled(has_data)
        self.message.setVisible(not has_data or (not has_analysis and mode != 0))
        self.data_table.setVisible(has_analysis and mode != 0 and self.data_toggle.isChecked())
        self._style_series(mode, bars[1], trend_data)
        self._render_attention()
        self._render_observations(trend_data if mode == 3 else data)
        self._render_sites()
        if not has_data:
            if data is not None:
                self.message.setText("조건에 맞는 설비가 없습니다. 조회 조건을 변경하거나 초기화해 주세요.")
            self.chart.clear()
            return
        if not has_analysis and mode != 0:
            self.message.setText("분석 대상 설비가 없습니다. HPA는 전체 설비 수와 적용률·공통 분석에서 제외하며, 설비 목록에서 조회할 수 있습니다.")
            self.chart.clear()
            return
        if mode == 0:
            self._matrix()
        elif mode == 1:
            self.chart.composition(data)
            self._render_composition_legend(data)
        elif mode == 2:
            self.chart.bars(bars, self.visible_groups)
        else:
            if not has_trend:
                self.chart.clear()
                self.chart.hide()
                self.data_table.hide()
                return
            self.chart.trend(trend_data, self.visible_groups)
        self._show_data_table()

    def _style_series(self, mode, bar_groups, trend_data):
        """현재 차트에 그릴 값이 있는 설비군만 표시 여부를 선택할 수 있다."""
        trends = trend_data.trends if mode == 3 and trend_data is not None else ()
        available = bar_groups if mode == 2 else {group.group_key for group in trends
                                                 if any(point.summary.rate is not None for point in group.points)}
        visible = []
        for group, check in zip(self.source.catalog.groups, self.series_widgets):
            present = group.key in available
            if present:
                visible.append(check)
            check.setEnabled(present)
            with QSignalBlocker(check):
                check.setChecked(present and group.key in self.visible_groups)
        self.series_grid.set_visible_widgets(visible)
        self.series_grid.setVisible(bool(visible))
        self.series_controls.setVisible(bool(visible))
        self.series_tools.setEnabled(bool(visible))
        set_bulk_selection_state(self.toggle_all_button, bool(visible) and all(check.isChecked() for check in visible))

    def _render_composition_legend(self, data):
        _clear_layout(self.composition_rows)
        counts = (data.summary.compliant, data.summary.below, data.summary.undetermined)
        total = sum(counts)
        styles = (("달성", "composition_met_chip", "rate"), ("미달", "below", "ink"),
                  ("확인 필요", "composition_unknown_chip", "ink"))
        for (name, key, preferred), count, ids in zip(styles, counts, composition_cohorts(data.summary)):
            row = QHBoxLayout()
            row.setSpacing(16)
            chip = label(name, 16, medium=True)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setFixedSize(90, 30)
            set_color_style(chip, lambda colors, k=key, p=preferred: (
                f"background:{colors[k]};color:{readable_color(colors[p], colors[k])};"
                "border:none;border-radius:4px;"))
            row.addWidget(chip)
            share = f"{count / total:.1%}" if total else "—"
            count_label = label(f"{count}건", 12, color=INK)
            share_label = label(share, 12, color=INK)
            tooltip = equipment_count_tooltip(f"{name} · {count}건 ({share})", ids)
            for widget in (chip, count_label, share_label):
                widget.setToolTip(tooltip)
            row.addWidget(count_label, 0, Qt.AlignmentFlag.AlignRight)
            row.addWidget(share_label, 0, Qt.AlignmentFlag.AlignRight)
            row.addStretch(1)
            self.composition_rows.addLayout(row)

    def _area_label(self, equipment_id):
        for row in self.snapshot.rows:
            if row.position is not None and equipment_id in row.cells[-1].equipment_ids:
                return row.label
        return "구역 미확인"

    def _render_attention(self):
        _clear_layout(self.attention_list)
        self.attention_buttons.clear()
        data = self.snapshot
        if data is None:
            self.attention_list.addWidget(label("조회 적용 후 위험 후보를 표시합니다.", 12, color=SECONDARY, wrap=True))
            return
        if not data.risky_ids:
            text = "위험 후보가 없습니다." + (f" 위험 미확인 {data.risk_unknown}대는 별도로 확인해 주세요." if data.risk_unknown else "")
            self.attention_list.addWidget(label(text, 12, color=SECONDARY, wrap=True))
            return
        records = {r.equipment_id: r for r in data.selected_records}
        statuses = {s.record.equipment_id: s for s in data.summary.equipment}
        shown = data.risky_ids[:8]
        for equipment_id in shown:
            record = records.get(equipment_id)
            code = record.equipment_code if record else f"설비 {equipment_id}"
            groups = " / ".join(g.label for g in data.groups if equipment_id in g.equipment_ids) or "설비군 미확인"
            row = QWidget()
            row_body = QHBoxLayout(row)
            row_body.setContentsMargins(0, 4, 0, 4)
            row_body.setSpacing(12)
            marker = QFrame()
            marker.setFixedSize(4, 4)
            set_color_style(marker, lambda colors: (
                f"background:{readable_color(colors['dashboard_risk_marker'], colors['surface'], 3)};border-radius:2px;"))
            row_body.addWidget(marker, alignment=Qt.AlignmentFlag.AlignVCenter)
            identity = QVBoxLayout()
            identity.setSpacing(0)
            identity.addWidget(label(f"{code}  ·  {groups}", 13, medium=True))
            identity.addWidget(label(f"{self._area_label(equipment_id)}  ·  위험 후보", 11, color=SECONDARY))
            row_body.addLayout(identity, 1)
            state, color_key = _equipment_state(statuses.get(equipment_id))
            badge = label(state, 12, medium=True)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setMinimumSize(56, 28)
            set_color_style(badge, lambda colors, k=color_key: _dashboard_badge_style(
                colors, k, "border-radius:8px;padding:0 10px;"))
            row_body.addWidget(badge)
            open_button = button("↗")
            open_button.setFixedSize(28, 28)
            open_button.setAccessibleName(f"{code} 설비 목록에서 보기")
            open_button.setToolTip("이 설비만 설비 목록에서 엽니다.")
            open_button.setStyleSheet(f"QPushButton {{background:transparent;border:1px solid transparent;color:{SECONDARY};"
                                      "padding:0;min-height:0;font-size:15px;}"
                                      f"QPushButton:hover {{background:{ACCENT_SOFT};}}"
                                      f"QPushButton:focus {{border:2px solid {PRIMARY};}}")
            open_button.clicked.connect(lambda checked=False, title=code, ids=(equipment_id,): self._select(title, ids, risk_only=True))
            row_body.addWidget(open_button)
            self.attention_buttons[equipment_id] = open_button
            self.attention_list.addWidget(row)
            self.attention_list.addWidget(_divider())
        if len(data.risky_ids) > len(shown):
            self.attention_list.addWidget(label(f"외 {len(data.risky_ids) - len(shown)}대 · '전체 {len(data.risky_ids)}대'에서 모두 볼 수 있습니다.",
                                                11, color=SECONDARY, wrap=True))

    def _render_observations(self, data):
        _clear_layout(self.observation_bars_layout)
        sampled = self.tabs.currentIndex() == 3 and self.trend_step is not None
        description = ("간격 기준일 이전의 마지막 관측 집계" if sampled else
                       "관측 없는 자료는 이전 값으로 채우지 않음")
        self.observations.setText("표시할 결과가 없습니다." if data is None else
            "같은 기준일 설비 집합·목표 N 고정 · " + description + "\n" + "\n".join(
                f"{point.day} · N 이상 {point.summary.compliant}/{point.summary.compliant + point.summary.below}건 · "
                + (f"{point.summary.rate:.1%}" if point.summary.rate is not None else "계산 불가")
                + f" · 판단 불가 {point.summary.undetermined}건 · 미관측 {point.missing_equipment}대"
                + (f" · 마지막 관측일 {point.source_day}" if point.source_day and point.source_day != point.day else "")
                for point in data.observations))
        self.observation_bars.setToolTip(self.observations.text())
        if data is None or not data.observations:
            self.observation_caption.setText("조회 적용 후 저장 관측 시점별 달성률을 표시합니다." if data is None
                                             else "저장 관측이 없습니다.")
            return
        self.observation_caption.setText(f"선택 범위 {data.summary.equipment_count}대 · 저장 관측 시점별 달성률"
                                         " · " + description)
        points = data.observations[-6:]
        for index, point in enumerate(points):
            latest = index == len(points) - 1
            rate = point.summary.rate
            column = QVBoxLayout()
            column.setSpacing(3)
            column.addWidget(label("계산 불가" if rate is None else f"{rate:.1%}", 12, medium=True,
                                   color=PRIMARY if latest else SECONDARY))
            well = QWidget()
            well.setFixedHeight(60)
            well_body = QVBoxLayout(well)
            well_body.setContentsMargins(0, 0, 0, 0)
            well_body.addStretch(1)
            if rate is not None:
                bar = QFrame()
                bar.setFixedHeight(max(2, round(60 * rate)))
                bar.setStyleSheet(f"background:{PRIMARY if latest else ACCENT_SOFT};border-radius:6px;")
                well_body.addWidget(bar)
            column.addWidget(well)
            column.addWidget(label(point.day.strftime("%m.%d")
                                   + (f" · 미관측 {point.missing_equipment}대" if point.missing_equipment else ""),
                                   11, color=SECONDARY))
            self.observation_bars_layout.addLayout(column, 1)

    def _render_sites(self):
        _clear_layout(self.site_list)
        data = self.snapshot
        if data is None or not data.equipment_ids:
            self.site_list.addWidget(label("조회 적용 후 Site별 설비 수를 표시합니다." if data is None
                                           else "조건에 맞는 설비가 없습니다.", 12, color=SECONDARY, wrap=True))
            return
        names = {o.key: o.label for o in self.source.catalog.sites}
        statuses = {s.record.equipment_id: s for s in data.summary.equipment}
        risky = set(data.risky_ids)
        sites = {}
        for row in data.rows:
            if row.position is not None:
                sites.setdefault(row.position[0], set()).update(row.cells[-1].equipment_ids)
        for site_key, ids in sites.items():
            analyzed = [statuses[i] for i in ids if i in statuses]
            achieved = sum(_equipment_state(s)[0] == "달성" for s in analyzed)
            line = QGridLayout()
            line.setHorizontalSpacing(12)
            for column, (text, size, medium, width) in enumerate((
                (names.get(site_key, site_key), 13, True, 70), (f"분석 {len(analyzed)}대", 12, False, 110),
                (f"달성 {achieved}대", 12, False, 90), (f"위험 {len(risky & ids)}대", 12, False, 0),
            )):
                cell = label(text, size, medium=medium, color=INK if medium else SECONDARY)
                if width:
                    cell.setMinimumWidth(width)
                line.addWidget(cell, 0, column)
            line.setColumnStretch(3, 1)
            self.site_list.addLayout(line)
            self.site_list.addWidget(_divider())

    def _matrix(self):
        self.matrix_buttons.clear()
        self.risk_buttons.clear()
        data = self.snapshot
        content = QWidget()
        content.setObjectName("dashboardMatrix")
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        total_row = data.rows[-1]
        columns = (("Site / 설비군", total_row.cells[-1]), *((cell.label, cell) for cell in total_row.cells))
        for column, (title, cell) in enumerate(columns):
            heading = label(title, 13, medium=True)
            heading.setObjectName("dashboardMatrixHeading")
            heading.setMinimumHeight(40)
            heading.setContentsMargins(8, 0, 8, 0)
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            heading.setToolTip(self._matrix_totals_tooltip("전체" if column == 0 else title, cell.summary,
                                                           cell.equipment_ids, len(cell.risky_ids)))
            heading.setStyleSheet(f"QLabel#dashboardMatrixHeading {{background:{ACCENT_SOFT if column == 0 else CANVAS};border-radius:4px;}}")
            grid.addWidget(heading, 0, column)
            grid.setColumnMinimumWidth(column, 110 if column == 0 else 84)
            grid.setColumnStretch(column, 114 if column == 0 else 160)
        sites = {site.key: site.label for site in self.source.catalog.sites}
        site_ids = {}
        for row in data.rows:
            if row.position is not None:
                site_ids.setdefault(row.position[0], []).extend(row.cells[-1].equipment_ids)
        records = {record.equipment_id: record for record in data.selected_records}
        targets = data.effective_targets
        risky_ids = set(data.risky_ids)
        site_tooltips = {}
        for site, ids in site_ids.items():
            summary = summarize(tuple(records[eid] for eid in ids), self.source.families, targets)
            site_tooltips[site] = self._matrix_totals_tooltip(f"{sites[site]} 전체", summary, ids,
                                                            len(risky_ids.intersection(ids)))
        previous_site, grid_row = None, 1
        for row_index, row in enumerate(data.rows, 1):
            site, area = row.position if row.position is not None else (None, None)
            new_site = site is not None and site != previous_site
            if new_site and previous_site is not None:
                divider = _divider()
                divider.setObjectName("dashboardMatrixSiteDivider")
                grid.addWidget(divider, grid_row, 0, 1, len(columns))
                grid_row += 1
            location = QFrame()
            location.setObjectName("dashboardMatrixLocation")
            location.setStyleSheet(f"QFrame#dashboardMatrixLocation {{background:{CANVAS};border-radius:4px;}}")
            location_body = QVBoxLayout(location)
            location_body.setContentsMargins(8, 4, 8, 4)
            location_body.setSpacing(2)
            cell = row.cells[-1]
            row_tooltip = self._matrix_totals_tooltip(row.label, cell.summary, cell.equipment_ids, len(cell.risky_ids))
            location.setToolTip(row_tooltip)
            if site is None:
                location_title = "전체 / 전체"
                total_label = label(row.label, 13, medium=True, wrap=True)
                total_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                total_label.setToolTip(row_tooltip)
                location_body.addWidget(total_label)
            else:
                site_label = label(sites[site], 13, medium=True, wrap=True)
                site_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                site_label.setToolTip(site_tooltips[site])
                location_body.addWidget(site_label)
                area_text = area or "구역 미확인"
                location_title = f"{sites[site]} / {area_text}"
                area_label = label(area_text, 12, wrap=True)
                area_label.setObjectName("dashboardMatrixArea")
                area_label.setStyleSheet(f"QLabel#dashboardMatrixArea {{color:{SECONDARY};}}")
                area_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                area_label.setAccessibleName(f"Site {sites[site]} · 원본 구역 {area or '미확인'}")
                area_label.setToolTip(row_tooltip)
                location_body.addWidget(area_label)
            grid.addWidget(location, grid_row, 0)
            for column, cell in enumerate(row.cells, 1):
                grid.addWidget(self._matrix_cell(row, row_index, column, cell, location_title), grid_row, column)
            previous_site = site
            grid_row += 1
        old = self.matrix_scroll.takeWidget()
        if old:
            old.deleteLater()
        self.matrix_scroll.setWidget(content)
        self.matrix_scroll.setFixedHeight(content.sizeHint().height() + 12)

    def _matrix_totals_tooltip(self, title, summary, ids, candidate_count):
        rate = f"{summary.rate:.1%}" if summary.rate is not None else "계산 불가"
        risk = self.snapshot.risk_rule_state
        risk_text = (risk.value if risk in (RiskRuleState.UNREGISTERED, RiskRuleState.ALL_DISABLED)
                     else f"확인된 위험 후보 {candidate_count}대")
        return equipment_count_tooltip(
            f"{title}\n총 적용률 {rate}\n"
            f"N 이상 {summary.compliant}건 · N 미만 {summary.below}건 · 판단 불가 {summary.undetermined}건\n"
            f"분석 {summary.equipment_count}대 · HPA 제외 {summary.hpa_excluded}대 · SW 미등록 {missing_sw_count(summary)}대\n"
            f"{risk_text}", ids)

    def _risk_kind_lines(self, ids):
        """위험 후보를 적용 위험 / 미적용 위험으로 센 줄. 판정 근거가 없는 자료는 분류하지 않고 '근거 미연결'로 센다."""
        assessor = getattr(self.source, "assessor", None)
        records = {r.equipment_id: r for r in self.snapshot.selected_records}
        counts, missing = Counter(), 0
        for equipment_id in ids:
            record = records.get(equipment_id)
            kinds = (risk_kinds(assessor.equipment_risk(record).findings)
                     if assessor is not None and assessor.rules and record is not None else ())
            counts.update(kinds)
            missing += not kinds
        lines = [f"{RISK_KIND_LABELS[kind]} {counts[kind]}대 · {RISK_KIND_DESCRIPTIONS[kind]}" for kind in RiskKind if counts[kind]]
        if sum(counts.values()) > len(ids) - missing:
            lines.append("한 설비가 두 분류에 모두 해당하면 각각 셉니다.")
        if missing:
            lines.append(f"판정 근거 미연결 {missing}대")
        return lines

    def _matrix_cell(self, row, row_index, column, cell, location_title):
        """Figma 셀: 큰 달성률, 아래에 'N 이상/판단 가능 건' + 위험 후보·판단 불가 표시."""
        summary = cell.summary
        complete = summary.rate is not None and summary.complete and not missing_sw_count(summary)
        color_key = "dashboard_unknown" if summary.rate is None else "met" if complete else "below"
        if not cell.equipment_ids:
            text = "—"
        elif summary.hpa_excluded and not summary.equipment_count:
            text = "분석 제외"
        elif summary.rate is None:
            text = "정보 부족"
        else:
            text = f"{summary.rate * 100:.1f}".removesuffix(".0") + "%"
        main = button("")
        main.setObjectName("dashboardCell")
        def cell_style(colors):
            background, ink = dashboard_cell_colors(colors, color_key)
            active = readable_color(background, background, 1.2)
            return (f"QPushButton#dashboardCell {{background:{background};border:none;"
                    "border-radius:6px;padding:0;min-height:54px;}"
                    f"QPushButton#dashboardCell QLabel {{color:{ink};}}"
                    "QPushButton#dashboardCell:hover:enabled, QPushButton#dashboardCell:focus "
                    f"{{background:{active};outline:none;}}")
        set_color_style(main, cell_style)
        main.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        main.setEnabled(bool(cell.equipment_ids))
        main.setAccessibleName(f"{row.label} {cell.label} 전체 설비 {summary.equipment_count}대")
        title = f"{row.label} / {cell.label}"
        if summary.rate is not None:
            lines = (f"{location_title} / {cell.label}", f"N이상 {summary.compliant}건",
                     f"N미만 {summary.below}건", f"정보 부족 {summary.undetermined}건")
            tooltip = "<qt>" + "<br>".join(map(escape, lines)) + "</qt>"
        elif summary.equipment_count:
            tooltip = information_gap_reasons(summary)
        else:
            tooltip = equipment_count_tooltip(
                f"{title}\nN 이상 {summary.compliant}건 / N 미만 {summary.below}건 / 판단 불가 {summary.undetermined}건\n"
                f"분석 {summary.equipment_count}대 · HPA 제외 {summary.hpa_excluded}대", cell.equipment_ids)
        main.setToolTip(tooltip)
        main.clicked.connect(lambda checked=False: self._select(title, cell.equipment_ids))
        body = QVBoxLayout(main)
        body.setContentsMargins(4, 4, 4, 4)
        body.setSpacing(0)
        value = label(text, 15 if summary.rate is not None else 12, medium=True)
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(value, 1)
        self.matrix_buttons[row_index, column] = main
        if summary.rate is not None:
            detail = QHBoxLayout()
            detail.setSpacing(6)
            detail.addStretch(1)
            detail.addWidget(label(f"{summary.compliant}/{summary.compliant + summary.below}건", 11))
            if cell.risky_ids:
                # '후보 n' 대신 위험 후보 KPI와 같은 경고 아이콘 + 대수로 보인다.
                risk = button(str(len(cell.risky_ids)))
                risk.setIcon(icon("60dbd.svg"))
                risk.setIconSize(QSize(11, 10))
                risk.setProperty("dashboardRiskBadge", True)
                set_widget_font(risk, font(10, medium=True))
                set_color_style(risk, lambda colors: (
                    "QPushButton {" + _dashboard_badge_style(
                        colors, "risk", "border:1px solid transparent;border-radius:4px;"
                        "padding:0 5px;min-height:16px;max-height:16px;") + "}"
                    f"QPushButton:focus {{border-color:{readable_color(colors['accent'], colors['risk'], 3)};}}"))
                risk.setAccessibleName(f"{row.label} {cell.label} 위험 후보만 {len(cell.risky_ids)}대")
                risk.setToolTip(equipment_count_tooltip(
                    "\n".join((f"위험 후보 · {title}", *self._risk_kind_lines(cell.risky_ids))), cell.risky_ids))
                risk.clicked.connect(lambda checked=False: self._select(title, cell.risky_ids, risk_only=True))
                detail.addWidget(risk)
                self.risk_buttons[row_index, column] = risk
            elif summary.undetermined:
                detail.addWidget(label(f"미확인{summary.undetermined}", 11))
            detail.addStretch(1)
            body.addLayout(detail)
        for caption in main.findChildren(QLabel):
            caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        return main

    def _show_data_table(self, *_):
        mode = self.tabs.currentIndex()
        data = self.trend_snapshot if mode == 3 else self.snapshot
        visible = data is not None and bool(data.equipment_ids) and mode != 0 and self.data_toggle.isChecked()
        self.data_table.setVisible(visible)
        if not visible:
            return
        if mode == 1:
            counts = (data.summary.compliant, data.summary.below, data.summary.undetermined)
            total = sum(counts)
            headers = ("상태", "설비·계열 건수", "구성비")
            rows = [(name, str(value), f"{value / total:.1%}" if total else "—") for name, value in zip(("N 이상", "N 미만", "판단 불가"), counts)]
        elif mode == 2:
            headers = ("Site / 설비군", "설비군", "목표 N 이상 적용률", "N 이상/판단 가능", "판단 불가", "설비 대수")
            areas, groups = bar_plot(data)
            rows = [(area, label, f"{point.counts.rate:.1%}" if point.counts.rate is not None else
                     "계산 불가" if point.equipment_ids else "데이터 없음",
                     f"{point.counts.compliant}/{point.counts.compliant + point.counts.below}",
                     str(point.counts.undetermined), str(len(point.equipment_ids)))
                    for index, area in enumerate(areas) for key, (label, points) in groups.items()
                    if key in self.visible_groups for point in (points[index],)]
        else:
            headers = ("관측일", "설비군", "N 이상/판단 가능", "달성률", "판단 불가", "미관측 대수")
            rows = [(str(p.day), group.label, f"{p.summary.compliant}/{p.summary.compliant + p.summary.below}",
                     f"{p.summary.rate:.1%}" if p.summary.rate is not None else "—", str(p.summary.undetermined), str(p.missing_equipment))
                    for group in data.trends if group.group_key in self.visible_groups for p in group.points]
            if self.trend_step is not None:
                headers = ("기준일", "마지막 관측일", *headers[1:])
                rows = [(row[0], str(point.source_day or point.day), *row[1:]) for row, point in zip(
                    rows, (point for group in data.trends if group.group_key in self.visible_groups for point in group.points))]
        self.data_table.clear()
        self.data_table.setColumnCount(len(headers))
        self.data_table.setRowCount(len(rows))
        self.data_table.setHorizontalHeaderLabels(headers)
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                self.data_table.setItem(row_index, column, QTableWidgetItem(value))
        self.data_table.resizeColumnsToContents()
