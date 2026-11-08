"""작업 단위 4: 조회 적용과 연결되는 대시보드 한 페이지, 표/구성비/막대/추이 보기."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QBoxLayout, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLayout, QScrollArea,
    QSizePolicy, QTabBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from setron.features.version.schemas import CompareStatus
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.dashboard_charts import DashboardChart, series_color
from setron.ui.dashboard_data import (
    DashboardDrilldown, DashboardSource, HpaClassificationConflict, NoObservationBefore, count_value, missing_sw_count,
    upgrade_caption,
)
from setron.ui.query_state import UNASSIGNED, version_filter_caption
from setron.ui.theme import ACCENT_SOFT, CANVAS, INK, LINE, PRIMARY, SECONDARY, SOFT, font, icon, svg_size

# Figma VER4 대시보드 셀 색 (--met / --below / --soft / --v4-red). 이 화면에서만 쓴다.
MET = "#ddf4e5"
BELOW = "#fff0b7"
RISK_RED = "#db4054"
OUTLINE_BUTTON = (f"QPushButton {{background:white;border:1px solid {LINE};border-radius:8px;padding:0 16px;"
                  "min-height:32px;max-height:32px;}"
                  f"QPushButton:hover {{background:{ACCENT_SOFT};border-color:{PRIMARY};}}"
                  f"QPushButton:focus {{border:2px solid {PRIMARY};}}"
                  "QPushButton:disabled {color:#8b95a8;background:#f9fafb;border-color:#e9eef6;}")
TAB_STYLE = (f"QTabBar::tab {{background:white;color:{INK};border:1px solid {LINE};border-radius:8px;"
             "min-width:84px;min-height:32px;max-height:32px;padding:0 8px;margin-right:6px;}"
             f"QTabBar::tab:selected {{background:{PRIMARY};color:white;border-color:{PRIMARY};}}"
             f"QTabBar::tab:hover:!selected {{background:{ACCENT_SOFT};}}"
             f"QTabBar:focus {{outline:none;}}")


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
        return "미확인", SOFT
    if status.needs_upgrade:
        return "미달", BELOW
    if any(f.status is CompareStatus.UNDETERMINED for f in status.families):
        return "확인 필요", SOFT
    return "달성", MET


class DashboardGrid(QWidget):
    def __init__(self, widgets, *, minimum_width=260, maximum_columns=4, spacing=16):
        super().__init__()
        self.widgets, self.minimum_width, self.maximum_columns = widgets, minimum_width, maximum_columns
        self.spacing = spacing
        self.columns = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(spacing)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._arrange(maximum_columns)

    def minimumSizeHint(self):
        return QSize(0, self.grid.minimumSize().height())

    def _arrange(self, columns):
        if columns == self.columns:
            return
        for widget in self.widgets:
            self.grid.removeWidget(widget)
        for index in range(max(columns, self.columns)):
            self.grid.setColumnStretch(index, int(index < columns))
        for index, widget in enumerate(self.widgets):
            self.grid.addWidget(widget, index // columns, index % columns)
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


class MetricCard(Card):
    """히트맵·설비 목록 화면이 함께 쓰는 지표 카드(아이콘 포함)."""

    def __init__(self, title, asset, *, tone="neutral"):
        super().__init__(padding=16 if tone == "neutral" else 20)
        self.setProperty("metricTone", tone)
        self.setMinimumHeight(176)
        self.body.setSpacing(6)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        row = QHBoxLayout()
        row.addWidget(label(title, 14, medium=True, color=SECONDARY, wrap=True), 1)
        self.picture = label("")
        self.picture.setProperty("assetFile", asset)
        self.picture.setPixmap(icon(asset).pixmap(svg_size(asset)))
        self.picture.setFixedSize(28 if tone != "neutral" else 24, 28 if tone != "neutral" else 24)
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setStyleSheet(f"background:{'#e7b83d' if asset == 'e53b6.svg' else '#fce2e7' if tone == 'risk' else '#edf0fd'};border-radius:8px;")
        row.addWidget(self.picture)
        self.body.addLayout(row)
        self.value = label("—", 46 if tone != "neutral" else 34, medium=True,
                           color=PRIMARY if tone == "achievement" else "#ad344d" if tone == "risk" else INK, numeric=True)
        if tone == "achievement":
            self.value.setProperty("appearanceRate", "foreground")
        number_font = self.value.font()
        number_font.setWeight(QFont.Weight.DemiBold)
        self.value.setFont(number_font)
        self.body.addWidget(self.value)
        self.caption = label("조회 적용 전", 13, color=SECONDARY, wrap=True)
        self.body.addWidget(self.caption)


class DashboardKpi(Card):
    """VER4 대시보드 상단의 간결한 지표 카드. 첫 카드(달성률)는 강조색으로 채운다."""

    def __init__(self, title, asset, *, accent=False):
        super().__init__(padding=18)
        self.body.setSpacing(3)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        ink, muted = ("white", "white") if accent else (INK, SECONDARY)
        if accent:
            self.setProperty("appearanceRate", "background")
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
        self.caption = label("조회 적용 전", 11, color=muted, wrap=True)
        self.body.addWidget(self.caption)


class DashboardPage(QWidget):
    drilldown_requested = Signal(object)

    def __init__(self, state, source: DashboardSource, *, drilldown_connected=False):
        super().__init__()
        self.setObjectName("canvas")
        self.state, self.source, self.snapshot = state, source, None
        self.drilldown_connected = drilldown_connected
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
        # Figma: 조회 패널 아래 '적용됨' 배지 + 실제 계산에 쓴 조건 한 줄. 결과가 있을 때만 보인다.
        self.applied_row = QWidget()
        applied_body = QHBoxLayout(self.applied_row)
        applied_body.setContentsMargins(0, 0, 0, 0)
        applied_body.setSpacing(10)
        applied_body.addWidget(StatusBadge("적용됨", "info"))
        self.applied_text = label("", 13, color=INK, wrap=True)
        applied_body.addWidget(self.applied_text, 1)
        self.applied_row.hide()
        body.addWidget(self.applied_row)
        cards = []
        for key, title, asset, accent in (
            ("rate", "목표 N 이상 달성률", "bbc12.svg", True),
            ("total", "분석 대상", "32ac4.svg", False),
            ("upgrade", "목표 미달", "e53b6.svg", False),
            ("check", "버전 확인 필요", "7880d.svg", False),
            ("risk", "안전·결함 위험 후보", "60dbd.svg", False),
        ):
            card = DashboardKpi(title, asset, accent=accent)
            self.metrics[key] = card
            cards.append(card)
        self.kpi_grid = DashboardGrid(cards, minimum_width=200, maximum_columns=5, spacing=12)
        body.addWidget(self.kpi_grid)

        # 왼쪽: 원본 구역별 목표 달성(보기 전환) 카드
        self.content = Card(padding=20)
        self.content.body.setSpacing(12)
        heading = QWidget()
        heading_body = QHBoxLayout(heading)
        heading_body.setContentsMargins(0, 0, 0, 0)
        heading_body.setSpacing(8)
        self.title = label("원본 구역별 목표 달성", 16, medium=True, wrap=True)
        heading_body.addWidget(self.title, 1)
        self.list_button = _outline_button("설비 목록 →")
        self.list_button.setEnabled(False)
        self.list_button.setToolTip("현재 적용 범위 전체를 설비 목록으로 엽니다.")
        self.list_button.clicked.connect(self._all_selection)
        heading_body.addWidget(self.list_button, alignment=Qt.AlignmentFlag.AlignTop)
        self.content.body.addWidget(heading)
        self.tabs = QTabBar()
        self.tabs.setAccessibleName("대시보드 보기 방식")
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setFont(font(14, medium=True))
        self.tabs.setStyleSheet(TAB_STYLE)
        for title in ("표", "구성비", "막대", "적용 추이"):
            self.tabs.addTab(title)
        self.content.body.addWidget(self.tabs)
        self.matrix_legend = QWidget()
        legend_body = QHBoxLayout(self.matrix_legend)
        legend_body.setContentsMargins(0, 0, 0, 0)
        legend_body.setSpacing(16)
        legend_body.addWidget(label("목표 달성률", 12, color=SECONDARY))
        for text, color in (("목표 달성", MET), ("미달 · 확인 필요", BELOW), ("위험 후보", RISK_RED)):
            dot = QFrame()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background:{color};border-radius:4px;")
            item = QHBoxLayout()
            item.setSpacing(6)
            item.addWidget(dot)
            item.addWidget(label(text, 12, color=SECONDARY))
            legend_body.addLayout(item)
        legend_body.addStretch(1)
        self.content.body.addWidget(self.matrix_legend)
        self.description = label("", 12, color=SECONDARY, wrap=True)
        self.risk_note = QWidget()
        risk_body = QHBoxLayout(self.risk_note)
        risk_body.setContentsMargins(0, 0, 0, 0)
        risk_body.setSpacing(6)
        risk_body.addWidget(label("⚠", 13, color=RISK_RED))
        risk_body.addWidget(self.description, 1)
        self.content.body.addWidget(self.risk_note)
        self.series_widgets = []
        for index, group in enumerate(source.catalog.groups):
            color = series_color(group.key, index)
            check = QCheckBox(group.label)
            check.setChecked(True)
            check.setFont(font(13, medium=True))
            check.setAccessibleName(group.label + " 추이 표시")
            check.setStyleSheet(
                f"QCheckBox {{color:{INK};background:white;border:1px solid {color};border-radius:8px;padding:6px 12px;}}"
                f"QCheckBox:!checked {{color:#8b95a8;background:{SOFT};border-color:{LINE};}}"
                f"QCheckBox:focus {{border:2px solid {PRIMARY};}}"
                "QCheckBox::indicator {width:0;height:0;border:none;background:transparent;image:none;margin:0;}")
            check.toggled.connect(lambda checked, key=group.key: self._toggle_group(key, checked))
            self.series_widgets.append(check)
        self.series_grid = DashboardGrid(self.series_widgets, minimum_width=96, maximum_columns=9, spacing=8)
        self.content.body.addWidget(self.series_grid)
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
        self.chart_note = label("설비 대수 · 복수 SW는 설비군별 집계", 12, color=SECONDARY)
        self.content.body.addWidget(self.chart_note)
        self.data_toggle = _outline_button("수치표 보기")
        self.data_toggle.setCheckable(True)
        self.data_toggle.toggled.connect(self._show_data_table)
        self.content.body.addWidget(self.data_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.data_table = QTableWidget()
        self.data_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.data_table.setAccessibleName("대시보드 차트 원본 수치표")
        self.data_table.setMinimumHeight(260)
        self.content.body.addWidget(self.data_table)
        self.message = label("조회 적용 전입니다. 위의 공통 조건에서 조회 적용을 눌러 주세요.", 13, color=SECONDARY, wrap=True)
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
        self.selection_notice = label("", 13, color=SECONDARY, wrap=True)
        self.selection_notice.hide()
        body.addWidget(self.selection_notice)

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
        self.context = label("", 11, color=SECONDARY, wrap=True)
        body.addWidget(self.context)
        body.addStretch(1)
        self.tabs.currentChanged.connect(self._render_view)
        self._render_view()

    def invalidate(self, *_):
        self.snapshot = None
        self.result_status.setText("선택지 변경 · 재조회 필요")
        self.result_status.set_tone("warning")
        self._clear_values()
        self.message.setText("선택지가 변경되었습니다. 조회 적용으로 결과를 다시 확인해 주세요.")

    def _clear_values(self):
        for card in self.metrics.values():
            card.value.setText("—")
            card.caption.setText("결과 없음")
        self.candidate_button.setEnabled(False)
        self.candidate_button.setText("전체 0대")
        self.selection_notice.clear()
        self.selection_notice.hide()
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
        self.context.clear()
        self.applied_row.hide()
        try:
            if self.state.catalog != self.source.catalog:
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
            self.result_status.setText("결과 표시 실패 · 다시 적용해 주세요")
            self.result_status.set_tone("danger")
            self.message.setText("선택지·관측 자료 연결을 확인할 수 없습니다. 조건과 데이터 연결을 확인하고 다시 적용해 주세요.")
            return False
        return self.accept_result(query, result)

    def accept_result(self, query, result):
        if not self.state.is_current(query) or result.query is not query:
            return False
        self.snapshot = result
        summary = result.summary
        no_sw = missing_sw_count(summary)
        unresolved = sum(1 for status in summary.equipment if _equipment_state(status)[0] in ("미확인", "확인 필요"))
        self.result_status.setText(f"{self._result_label()} · " + ("조건에 맞는 설비 없음" if not result.equipment_ids else "조회 적용됨"))
        self.result_status.set_tone("info")
        self.context.setText(f"{result.base_date:%Y.%m.%d} 기준 · 목표 N {len(result.target_values)}계열")
        self.applied_text.setText(self._applied_caption(result))
        self.applied_row.show()
        values = {
            "total": (f"{summary.equipment_count}대", f"HPA 제외 {summary.hpa_excluded}대"),
            "rate": ("계산 불가" if summary.rate is None else f"{summary.rate:.1%}",
                     f"{summary.compliant}/{summary.compliant + summary.below}건 · 판단 불가 {summary.undetermined}건"
                     + f" · SW 미등록 {no_sw}대"
                     + (" · 확인 필요" if summary.rate == 1 and (not summary.complete or no_sw) else "")),
            "risk": (count_value(len(result.risky_ids), result.risk_unknown, "미확인"),
                     f"목표 달성과 별도 · 위험 미확인 {result.risk_unknown}대"),
            "upgrade": (count_value(summary.upgrade_needed, summary.undetermined, "판단 불가"),
                        upgrade_caption("한 계열이라도 N 미만인 물리 설비 · 중복 제거", summary.undetermined)),
            "check": (f"{unresolved}대", f"판단 불가 {summary.undetermined}건 · SW 미등록 {no_sw}대"),
        }
        for key, (value, caption) in values.items():
            self.metrics[key].value.setText(value)
            self.metrics[key].caption.setText(caption)
        self.candidate_button.setText(f"전체 {len(result.risky_ids)}대")
        self.candidate_button.setEnabled(bool(result.risky_ids))
        self.observations.setText("같은 기준일 설비 집합·목표 N 고정 · 관측 없는 자료는 이전 값으로 채우지 않음\n" + "\n".join(
            f"{point.day} · N 이상 {point.summary.compliant}/{point.summary.compliant + point.summary.below}건 · "
            + (f"{point.summary.rate:.1%}" if point.summary.rate is not None else "계산 불가")
            + f" · 판단 불가 {point.summary.undetermined}건 · 미관측 {point.missing_equipment}대"
            for point in result.observations))
        self._render_view()
        return True

    def _applied_caption(self, result):
        """실제 계산에 쓴 기준일·위치·설비군·목표 N 개수. 없는 정보는 만들지 않는다."""
        selection, catalog = result.query.selection, self.source.catalog
        def name(value, options):
            return "미지정" if value is UNASSIGNED else next((o.label for o in options if o.key == value), str(value))
        places = [f"{title} {name(value, options)}" for title, value, options in (
            ("Site", selection.site_key, catalog.sites), ("Fab", selection.fab_key, catalog.fabs),
            ("Line", selection.line_key, catalog.lines)) if value is not None]
        parts = [f"기준일 {result.base_date}", " · ".join(places) if places else "전체 Site/Fab/Line"]
        if selection.group_key is not None:
            parts.append(f"설비군 {name(selection.group_key, catalog.groups)}")
        parts.append(f"계열별 N {len(result.target_values)}개")
        if selection.versions is not None:
            parts.append(version_filter_caption(selection.versions))
        return " · ".join(parts)

    def _select(self, title, ids, *, risk_only=False):
        if self.snapshot is None or not self.state.is_current(self.snapshot.query) or not ids:
            return
        request = DashboardDrilldown(self.snapshot.query, self.snapshot.base_date, title, tuple(ids), risk_only)
        self.selection_notice.setText(f"선택: {title} · {'위험 후보만' if risk_only else '해당 범위 전체'} {len(ids)}대\n"
                                      + ("선택 범위의 설비 목록으로 이동합니다." if self.drilldown_connected else
                                         "설비 목록 화면은 6번 작업에서 연결합니다. 현재는 조회 범위 전달까지만 구현했습니다."))
        self.selection_notice.show()
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

    def _render_view(self, *_):
        mode = self.tabs.currentIndex()
        data = self.snapshot
        has_data = data is not None and bool(data.equipment_ids)
        has_analysis = has_data and bool(data.summary.equipment_count)
        self.matrix_scroll.setVisible(has_data and mode == 0)
        self.chart.setVisible(has_analysis and mode != 0)
        self.series_grid.setVisible(has_analysis and mode in (2, 3))
        for group, check in zip(self.source.catalog.groups, self.series_widgets):
            check.setVisible(data is None or data.query.selection.group_key in (None, group.key))
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
        self.chart_note.setVisible(has_analysis and mode == 2)
        self.list_button.setEnabled(has_data)
        self.site_button.setEnabled(has_data)
        self.message.setVisible(not has_data or (not has_analysis and mode != 0))
        self.data_table.setVisible(has_analysis and mode != 0 and self.data_toggle.isChecked())
        titles = ("원본 구역별 목표 달성", "목표 도달 상태 구성비", "라인 · 설비군별 설비 대수", "설비군별 목표 적용 추이")
        self.title.setText(titles[mode])
        self.description.setText(self._subtitle(mode))
        self.risk_note.setVisible(has_analysis and mode != 0 and bool(self.description.text()))
        self.risk_note.layout().itemAt(0).widget().setVisible(mode == 1)
        self._style_series(mode)
        self._render_attention()
        self._render_observations()
        self._render_sites()
        if not has_data:
            if data is not None:
                self.message.setText("조건에 맞는 설비가 없습니다. 선택 조건을 변경한 뒤 조회 적용을 눌러 주세요.")
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
            self.chart.bars(data, self._select, self.visible_groups)
        else:
            self.chart.trend(data, self.visible_groups)
        self._show_data_table()

    def _subtitle(self, mode):
        data = self.snapshot
        if data is None or mode == 0:
            return ""
        if mode == 1:
            if not data.risky_ids:
                return ""
            statuses = {s.record.equipment_id: s for s in data.summary.equipment}
            met = sum(_equipment_state(statuses.get(i))[0] == "달성" for i in data.risky_ids)
            return f"안전·결함 위험 후보 {len(data.risky_ids)}대 · 목표 달성 {met}대와 중복"
        if mode == 2:
            return "라인 미지정 · 현재 원본 구역 기준"
        selection = data.query.selection
        scope = "전체 설비" if selection == type(selection)(as_of=selection.as_of) else "선택 범위 설비"
        defaults = getattr(self.source, "targets", None)
        used = {(t.model_id, t.family_id): t.release_id for t in data.target_values}
        basic = data.query.targets is None or (defaults is not None and used == dict(defaults))
        return f"{scope} · " + ("기본 목표 N" if basic else "선택한 목표 N")

    def _style_series(self, mode):
        """막대는 점 범례, 적용 추이는 테두리 버튼 범례(Figma). 클릭하면 해당 설비군 표시를 켜고 끈다."""
        for index, (group, check) in enumerate(zip(self.source.catalog.groups, self.series_widgets)):
            color = series_color(group.key, index)
            if mode == 2:
                style = (f"QCheckBox {{color:{INK};background:transparent;border:none;padding:2px 4px;spacing:6px;}}"
                         f"QCheckBox:!checked {{color:#8b95a8;}}"
                         f"QCheckBox::indicator {{width:8px;height:8px;border-radius:4px;background:{color};border:none;}}"
                         f"QCheckBox::indicator:unchecked {{background:{LINE};}}")
            else:
                style = (f"QCheckBox {{color:{INK};background:white;border:1px solid {color};border-radius:8px;padding:6px 12px;}}"
                         f"QCheckBox:!checked {{color:#8b95a8;background:{SOFT};border-color:{LINE};}}"
                         f"QCheckBox:focus {{border:2px solid {PRIMARY};}}"
                         "QCheckBox::indicator {width:0;height:0;border:none;background:transparent;image:none;margin:0;}")
            if check.styleSheet() != style:
                check.setStyleSheet(style)

    def _render_composition_legend(self, data):
        _clear_layout(self.composition_rows)
        counts = (data.summary.compliant, data.summary.below, data.summary.undetermined)
        total = sum(counts)
        for (name, background, ink), count in zip((("달성", ACCENT_SOFT, PRIMARY), ("미달", BELOW, INK),
                                                   ("확인 필요", "#f3f4f6", INK)), counts):
            row = QHBoxLayout()
            row.setSpacing(16)
            chip = label(name, 12, medium=True, color=ink)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setFixedSize(72, 22)
            chip.setStyleSheet(f"background:{background};border-radius:4px;")
            row.addWidget(chip)
            row.addWidget(label(f"{count}건", 12, color=INK), 0, Qt.AlignmentFlag.AlignRight)
            row.addWidget(label(f"{count / total:.1%}" if total else "—", 12, color=INK), 0, Qt.AlignmentFlag.AlignRight)
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
            marker.setStyleSheet(f"background:{RISK_RED};border-radius:2px;")
            row_body.addWidget(marker, alignment=Qt.AlignmentFlag.AlignVCenter)
            identity = QVBoxLayout()
            identity.setSpacing(0)
            identity.addWidget(label(f"{code}  ·  {groups}", 13, medium=True))
            identity.addWidget(label(f"{self._area_label(equipment_id)}  ·  위험 후보", 11, color=SECONDARY))
            row_body.addLayout(identity, 1)
            state, background = _equipment_state(statuses.get(equipment_id))
            badge = label(state, 12, medium=True)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setMinimumSize(56, 28)
            badge.setStyleSheet(f"background:{background};color:{INK};border-radius:8px;padding:0 10px;")
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

    def _render_observations(self):
        _clear_layout(self.observation_bars_layout)
        data = self.snapshot
        self.observation_bars.setToolTip(self.observations.text())
        if data is None or not data.observations:
            self.observation_caption.setText("조회 적용 후 저장 관측 시점별 달성률을 표시합니다." if data is None
                                             else "저장 관측이 없습니다.")
            return
        self.observation_caption.setText(f"선택 범위 {data.summary.equipment_count}대 · 저장 관측 시점별 달성률"
                                         " · 관측 없는 자료는 이전 값으로 채우지 않음")
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
        columns = ("Site / 원본 구역", *(cell.label for cell in data.rows[-1].cells))
        for column, title in enumerate(columns):
            heading = label(title, 13, medium=True, color=INK)
            heading.setMinimumHeight(40)
            heading.setContentsMargins(8, 0, 8, 0)
            heading.setStyleSheet(f"background:{CANVAS};border-radius:4px;")
            grid.addWidget(heading, 0, column)
            grid.setColumnMinimumWidth(column, 110 if column == 0 else 84)
            grid.setColumnStretch(column, 114 if column == 0 else 160)
        for row_index, row in enumerate(data.rows, 1):
            name = label(row.label, 13, medium=row.position is None, color=INK, wrap=True)
            name.setContentsMargins(8, 0, 0, 0)
            grid.addWidget(name, row_index, 0)
            for column, cell in enumerate(row.cells, 1):
                grid.addWidget(self._matrix_cell(row, row_index, column, cell), row_index, column)
        old = self.matrix_scroll.takeWidget()
        if old:
            old.deleteLater()
        self.matrix_scroll.setWidget(content)
        self.matrix_scroll.setFixedHeight(content.sizeHint().height() + 12)

    def _matrix_cell(self, row, row_index, column, cell):
        """Figma 셀: 큰 달성률, 아래에 'N 이상/판단 가능 건' + 위험 후보·판단 불가 표시."""
        summary = cell.summary
        complete = summary.rate is not None and summary.complete and not missing_sw_count(summary)
        background = SOFT if summary.rate is None else MET if complete else BELOW
        ink = "#246b4a" if background == MET else "#8a6400" if background == BELOW else SECONDARY
        frame = QFrame()
        frame.setObjectName("dashboardCell")
        frame.setMinimumHeight(56)
        frame.setStyleSheet(f"QFrame#dashboardCell {{background:{background};border:none;border-radius:6px;}}")
        frame.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body = QVBoxLayout(frame)
        body.setContentsMargins(4, 4, 4, 4)
        body.setSpacing(0)
        if not cell.equipment_ids:
            text = "—"
        elif summary.hpa_excluded and not summary.equipment_count:
            text = "분석 제외"
        elif summary.rate is None:
            text = "정보 부족"
        else:
            text = f"{summary.rate * 100:.1f}".removesuffix(".0") + "%"
        main = button(text)
        main.setFont(font(15 if summary.rate is not None else 12, medium=True))
        main.setProperty("dashboardCellButton", True)
        main.setStyleSheet(f"QPushButton {{color:{ink};background:transparent;border:1px solid transparent;"
                           "border-radius:6px;padding:0 4px;min-height:24px;}"
                           f"QPushButton:hover {{border-color:{PRIMARY};}}"
                           f"QPushButton:focus {{border:2px solid {PRIMARY};}}"
                           f"QPushButton:disabled {{color:{SECONDARY};}}")
        main.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        main.setEnabled(bool(cell.equipment_ids))
        main.setAccessibleName(f"{row.label} {cell.label} 전체 설비 {summary.equipment_count}대")
        main.setToolTip(
            (f"달성률 {summary.rate:.1%} · " if summary.rate is not None else "")
            + f"N 이상 {summary.compliant}건 / N 미만 {summary.below}건 / 판단 불가 {summary.undetermined}건\n"
            f"분석 {summary.equipment_count}대 · HPA 제외 {summary.hpa_excluded}대 · 해당 범위 전체 선택")
        main.clicked.connect(lambda checked=False, title=f"{row.label} / {cell.label}", ids=cell.equipment_ids:
                             self._select(title, ids))
        body.addWidget(main)
        self.matrix_buttons[row_index, column] = main
        if summary.rate is not None:
            detail = QHBoxLayout()
            detail.setSpacing(6)
            detail.addStretch(1)
            detail.addWidget(label(f"{summary.compliant}/{summary.compliant + summary.below}건", 11, color=ink))
            if cell.risky_ids:
                risk = button(f"후보 {len(cell.risky_ids)}")
                risk.setProperty("dashboardRiskBadge", True)
                risk.setFont(font(10, medium=True))
                risk.setStyleSheet("QPushButton {color:#ad344d;background:#fce2e7;border:1px solid transparent;"
                                   "border-radius:4px;padding:0 5px;min-height:16px;max-height:16px;}"
                                   f"QPushButton:focus {{border:1px solid {PRIMARY};}}")
                risk.setAccessibleName(f"{row.label} {cell.label} 위험 후보만 {len(cell.risky_ids)}대")
                risk.setToolTip(f"위험 후보 {len(cell.risky_ids)}대만 선택")
                risk.clicked.connect(lambda checked=False, title=f"{row.label} / {cell.label}", ids=cell.risky_ids:
                                     self._select(title, ids, risk_only=True))
                detail.addWidget(risk)
                self.risk_buttons[row_index, column] = risk
            elif summary.undetermined:
                detail.addWidget(label(f"미확인{summary.undetermined}", 11, color=ink))
            detail.addStretch(1)
            body.addLayout(detail)
        return frame

    def _show_data_table(self, *_):
        data, mode = self.snapshot, self.tabs.currentIndex()
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
            headers = ("Site / 원본 구역", "설비군", "물리 대수")
            rows = [(row.label, group.label, str(len(set(group.equipment_ids).intersection(row.cells[-1].equipment_ids))))
                    for row in data.rows if row.position is not None and row.cells[-1].summary.equipment_count
                    for group in data.groups if group.group_keys[0] in self.visible_groups]
        else:
            headers = ("관측일", "설비군", "N 이상/판단 가능", "달성률", "판단 불가", "미관측 대수")
            rows = [(str(p.day), group.label, f"{p.summary.compliant}/{p.summary.compliant + p.summary.below}",
                     f"{p.summary.rate:.1%}" if p.summary.rate is not None else "—", str(p.summary.undetermined), str(p.missing_equipment))
                    for group in data.trends if group.group_key in self.visible_groups for p in group.points]
        self.data_table.clear()
        self.data_table.setColumnCount(len(headers))
        self.data_table.setRowCount(len(rows))
        self.data_table.setHorizontalHeaderLabels(headers)
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                self.data_table.setItem(row_index, column, QTableWidgetItem(value))
        self.data_table.resizeColumnsToContents()
