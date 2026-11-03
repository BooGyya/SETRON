"""작업 단위 4: 조회 적용과 연결되는 대시보드 한 페이지, 표/구성비/막대/추이 보기."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLayout, QScrollArea,
    QTabBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.dashboard_charts import DashboardChart, series_color
from setron.ui.dashboard_data import DashboardDrilldown, DashboardSource, HpaClassificationConflict, missing_sw_count
from setron.ui.theme import INK, PRIMARY, SECONDARY, TONES, font, icon, svg_size


class DashboardGrid(QWidget):
    def __init__(self, widgets, *, minimum_width=260, maximum_columns=4):
        super().__init__()
        self.widgets, self.minimum_width, self.maximum_columns = widgets, minimum_width, maximum_columns
        self.columns = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(16)
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
        self._arrange(max(1, min(self.maximum_columns, (event.size().width() + 16) // (self.minimum_width + 16))))
        super().resizeEvent(event)


class MetricCard(Card):
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
        number_font = self.value.font()
        number_font.setWeight(QFont.Weight.DemiBold)
        self.value.setFont(number_font)
        self.body.addWidget(self.value)
        self.caption = label("조회 적용 전", 13, color=SECONDARY, wrap=True)
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
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)
        self.result_status = StatusBadge("조회 적용 전 · 예시 데이터", "info")
        body.addWidget(self.result_status)
        cards = []
        for key, title, asset, tone in (
            ("total", "선택 범위 물리 설비", "32ac4.svg", "neutral"),
            ("rate", "목표 N 이상 달성률", "bbc12.svg", "achievement"),
            ("risk", "안전·결함 후보", "60dbd.svg", "risk"),
            ("upgrade", "업그레이드 필요", "e53b6.svg", "neutral"),
        ):
            card = MetricCard(title, asset, tone=tone)
            self.metrics[key] = card
            cards.append(card)
        self.kpi_grid = DashboardGrid(cards)
        body.addWidget(self.kpi_grid)
        self.context = label("조회 적용을 누르면 선택 범위의 예시 결과를 표시합니다. 실제 회사 자료가 아닙니다.",
                             13, color=SECONDARY, wrap=True)
        body.addWidget(self.context)
        self.tabs = QTabBar()
        self.tabs.setAccessibleName("대시보드 보기 방식")
        self.tabs.setExpanding(False)
        for title in ("현황표", "구성비", "막대", "적용 추이"):
            self.tabs.addTab(title)
        body.addWidget(self.tabs)
        self.content = Card()
        body.addWidget(self.content)
        heading = QWidget()
        heading_body = QHBoxLayout(heading)
        heading_body.setContentsMargins(0, 0, 0, 0)
        heading_body.setSpacing(16)
        captions = QWidget()
        caption_body = QVBoxLayout(captions)
        caption_body.setContentsMargins(0, 0, 0, 0)
        caption_body.setSpacing(4)
        self.title = label("원본 구역별 관측 SW 적용 현황", 20, medium=True, wrap=True)
        caption_body.addWidget(self.title)
        self.description = label("일반 셀은 해당 범위 전체, 위험 후보 버튼은 해당 범위의 후보만 선택합니다.",
                                 13, color=SECONDARY, wrap=True)
        caption_body.addWidget(self.description)
        heading_body.addWidget(captions, 1)
        self.candidate_button = button("위험 후보 보기", asset="7880d.svg")
        self.candidate_button.setProperty("dashboardRisk", True)
        self.candidate_button.setEnabled(False)
        self.candidate_button.clicked.connect(self._risk_selection)
        heading_body.addWidget(self.candidate_button, alignment=Qt.AlignmentFlag.AlignTop)
        self.content.body.addWidget(heading)
        self.legend = label("목표 달성 / 미달 · 확인 필요 / 정보 부족   ·   위험 후보는 목표 달성과 별도 표시", 13, color=SECONDARY, wrap=True)
        self.content.body.addWidget(self.legend)
        self.series_widgets = []
        for index, group in enumerate(source.catalog.groups):
            check = QCheckBox(group.label)
            check.setChecked(True)
            check.setAccessibleName(group.label + " 추이 표시")
            check.setStyleSheet(f"QCheckBox {{color:{series_color(group.key, index)};padding:4px;}}")
            check.toggled.connect(lambda checked, key=group.key: self._toggle_group(key, checked))
            self.series_widgets.append(check)
        self.series_grid = DashboardGrid(self.series_widgets, minimum_width=120, maximum_columns=9)
        self.content.body.addWidget(self.series_grid)
        self.matrix_scroll = QScrollArea()
        self.matrix_scroll.setWidgetResizable(True)
        self.matrix_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content.body.addWidget(self.matrix_scroll)
        self.chart = DashboardChart()
        self.content.body.addWidget(self.chart)
        self.data_toggle = button("수치표 보기")
        self.data_toggle.setCheckable(True)
        self.data_toggle.toggled.connect(self._show_data_table)
        self.content.body.addWidget(self.data_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.data_table = QTableWidget()
        self.data_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.data_table.setAccessibleName("대시보드 차트 원본 수치표")
        self.data_table.setMinimumHeight(260)
        self.content.body.addWidget(self.data_table)
        self.message = label("조회 적용 전입니다. 위의 공통 조건에서 조회 적용을 눌러 주세요.", color=SECONDARY, wrap=True)
        self.content.body.addWidget(self.message)
        self.definition = label("달성률 = N 이상 / (N 이상 + N 미만), 단위는 설비·계열 건입니다. 판단 불가는 분모에서 제외하고 별도 표시합니다.\n"
                                "HPA는 전체 설비 수와 적용률·공통 분석에서 제외하며, 설비 목록에서 조회할 수 있습니다. 모델별 대수를 더해 물리 대수를 만들지 않습니다.",
                                12, color=SECONDARY, wrap=True)
        self.content.body.addWidget(self.definition)
        self.selection_notice = label("", 13, color=SECONDARY, wrap=True)
        body.addWidget(self.selection_notice)
        self.observation_card = Card()
        self.observation_card.body.addWidget(label("선택 범위의 저장 관측", 16, medium=True))
        self.observations = label("조회 적용 전", 13, color=SECONDARY, wrap=True)
        self.observation_card.body.addWidget(self.observations)
        body.addWidget(self.observation_card)
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
        self.selection_notice.clear()
        self.observations.setText("표시할 결과가 없습니다.")
        self._render_view()

    def apply_query(self, query):
        if not self.state.is_current(query):
            return False
        self.snapshot = None
        self._clear_values()
        self.result_status.setText("예시 결과 계산 중")
        self.context.setText("적용 #" + str(query.revision) + " · " + query.description)
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
        self.result_status.setText("예시 결과 · " + ("조건에 맞는 설비 없음" if not result.equipment_ids else "조회 적용됨"))
        self.result_status.set_tone("info")
        self.context.setText(f"적용 #{query.revision} · 기준 관측일 {result.base_date} · {query.description}\n{result.source_label}")
        values = {
            "total": (f"{summary.equipment_count}대", f"HPA 제외 {summary.hpa_excluded}대"),
            "rate": ("계산 불가" if summary.rate is None else f"{summary.rate:.1%}",
                     f"{summary.compliant}/{summary.compliant + summary.below}건 · 판단 불가 {summary.undetermined}건"
                     + f" · SW 미등록 {no_sw}대"
                     + (" · 확인 필요" if summary.rate == 1 and (not summary.complete or no_sw) else "")),
            "risk": (f"{len(result.risky_ids)}대", f"목표 달성과 별도 · 위험 미확인 {result.risk_unknown}대"),
            "upgrade": (f"{summary.upgrade_needed}대", "한 계열이라도 N 미만인 물리 설비 · 중복 제거"),
        }
        for key, (value, caption) in values.items():
            self.metrics[key].value.setText(value)
            self.metrics[key].caption.setText(caption)
        self.candidate_button.setText(f" 위험 후보 {len(result.risky_ids)}대 보기")
        self.candidate_button.setEnabled(bool(result.risky_ids))
        self.observations.setText("같은 기준일 설비 집합·목표 N 고정 · 관측 없는 자료는 이전 값으로 채우지 않음\n" + "\n".join(
            f"{point.day} · N 이상 {point.summary.compliant}/{point.summary.compliant + point.summary.below}건 · "
            + (f"{point.summary.rate:.1%}" if point.summary.rate is not None else "계산 불가")
            + f" · 판단 불가 {point.summary.undetermined}건 · 미관측 {point.missing_equipment}대"
            for point in result.observations))
        self._render_view()
        return True

    def _select(self, title, ids, *, risk_only=False):
        if self.snapshot is None or not self.state.is_current(self.snapshot.query) or not ids:
            return
        request = DashboardDrilldown(self.snapshot.query, self.snapshot.base_date, title, tuple(ids), risk_only)
        self.selection_notice.setText(f"선택: {title} · {'위험 후보만' if risk_only else '해당 범위 전체'} {len(ids)}대\n"
                                      + ("선택 범위의 설비 목록으로 이동합니다." if self.drilldown_connected else
                                         "설비 목록 화면은 6번 작업에서 연결합니다. 현재는 조회 범위 전달까지만 구현했습니다."))
        self.drilldown_requested.emit(request)

    def _risk_selection(self):
        if self.snapshot:
            self._select("현재 적용 범위", self.snapshot.risky_ids, risk_only=True)

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
        self.data_toggle.setVisible(has_analysis and mode != 0)
        self.candidate_button.setVisible(mode in (0, 1))
        self.observation_card.setVisible(mode == 0)
        self.legend.setVisible(mode == 0)
        self.message.setVisible(not has_data or (not has_analysis and mode != 0))
        self.data_table.setVisible(has_analysis and mode != 0 and self.data_toggle.isChecked())
        titles = ("원본 구역별 관측 SW 적용 현황", "목표 도달 상태 구성비", "원본 구역 · 설비군별 설비 대수", "설비군별 목표 적용 추이")
        descriptions = (
            "원본 구역은 Fab·Line이 아닙니다. 일반 셀과 위험 후보 버튼은 서로 다른 범위를 선택합니다.",
            "단위: 설비·계열 건 · 구성비의 분모에는 판단 불가도 포함합니다. 위험 후보는 별도입니다.",
            "단위: 설비군별 물리 대수 · 복수 SW는 설비군마다 표시하므로 합산은 물리 총대수와 다를 수 있습니다. 범례 선택은 차트 표시만 바꿉니다.",
            "같은 기준일 설비 집합·목표 N 고정 · 미관측/분모 없음은 선을 끊습니다. 범례 선택은 차트 표시만 바꿉니다.",
        )
        self.title.setText(titles[mode])
        self.description.setText(descriptions[mode])
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
        elif mode == 2:
            self.chart.bars(data, self._select, self.visible_groups)
        else:
            self.chart.trend(data, self.visible_groups)
        self._show_data_table()

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
        for column, title in enumerate(("Site / 원본 구역", *(cell.label for cell in data.rows[-1].cells))):
            heading = label(title, medium=True, color=SECONDARY)
            heading.setStyleSheet("background:#f7f9fc;border-radius:6px;padding:8px;")
            heading.setMinimumHeight(24)
            grid.addWidget(heading, 0, column)
            grid.setColumnMinimumWidth(column, 166 if column == 0 else 148)
            grid.setColumnStretch(column, 1)
        for row_index, row in enumerate(data.rows, 1):
            grid.addWidget(label(row.label, medium=row.position is None), row_index, 0)
            for column, cell in enumerate(row.cells, 1):
                summary = cell.summary
                tone = "neutral" if summary.rate is None else "success" if summary.complete and not missing_sw_count(summary) else "warning"
                background, ink, border = TONES[tone]
                frame = QFrame()
                frame.setObjectName("dashboardCell")
                frame.setStyleSheet(f"QFrame#dashboardCell {{background:{background};border:1px solid {border};border-radius:10px;}}")
                cell_body = QVBoxLayout(frame)
                cell_body.setContentsMargins(4, 4, 4, 4)
                cell_body.setSpacing(2)
                rate = "—" if not cell.equipment_ids else "분석 제외" if summary.hpa_excluded and not summary.equipment_count else "정보 부족" if summary.rate is None else f"{summary.rate:.1%}"
                text = rate + (f"\n{summary.compliant}/{summary.compliant + summary.below}건 · {summary.equipment_count}대" if cell.equipment_ids else "")
                main = button(text)
                main.setFont(font(14, numeric=True))
                main.setProperty("dashboardCellButton", True)
                main.setStyleSheet(f"QPushButton {{color:{ink};background:transparent;text-align:left;border:1px solid transparent;padding:2px;min-height:40px;}} QPushButton:focus {{border-color:{PRIMARY};}}")
                main.setEnabled(bool(cell.equipment_ids))
                main.setAccessibleName(f"{row.label} {cell.label} 전체 설비 {summary.equipment_count}대")
                main.setToolTip(f"N 이상 {summary.compliant}건 / N 미만 {summary.below}건 / 판단 불가 {summary.undetermined}건\nHPA 제외 {summary.hpa_excluded}대 · 해당 범위 전체 선택")
                main.clicked.connect(lambda checked=False, title=f"{row.label} / {cell.label}", ids=cell.equipment_ids: self._select(title, ids))
                cell_body.addWidget(main)
                self.matrix_buttons[row_index, column] = main
                if cell.risky_ids:
                    risk = button(f" 후보 {len(cell.risky_ids)}", asset="5ced8.svg")
                    risk.setProperty("dashboardRiskBadge", True)
                    risk.setAccessibleName(f"{row.label} {cell.label} 위험 후보만 {len(cell.risky_ids)}대")
                    risk.clicked.connect(lambda checked=False, title=f"{row.label} / {cell.label}", ids=cell.risky_ids: self._select(title, ids, risk_only=True))
                    cell_body.addWidget(risk, alignment=Qt.AlignmentFlag.AlignRight)
                    self.risk_buttons[row_index, column] = risk
                elif summary.undetermined:
                    cell_body.addWidget(label(f"판단 불가 {summary.undetermined}건", 11, color=SECONDARY))
                frame.setMinimumHeight(86)
                grid.addWidget(frame, row_index, column)
        old = self.matrix_scroll.takeWidget()
        if old:
            old.deleteLater()
        self.matrix_scroll.setWidget(content)
        self.matrix_scroll.setFixedHeight(content.sizeHint().height() + 16)

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
