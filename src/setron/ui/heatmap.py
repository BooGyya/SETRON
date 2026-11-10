"""작업 단위 5: 물리 설비 타일·버전 함께 보기. VER4 히트맵(상세보기·간략히 보기, 카드형·목록형) 배치."""

from PySide6.QtCore import QSignalBlocker, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLayout, QLineEdit, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from setron.features.risk.schemas import RiskStatus
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.dashboard import DashboardGrid, DashboardKpi
from setron.ui.dashboard_data import RiskRuleState
from setron.ui.equipment import FlowGrid, ReadTableModel, fit_rows, ignore_width, read_table
from setron.features.version.groups import Tier
from setron.ui.heatmap_data import EquipmentSelection, GoalState, equipment_tiles
from setron.ui.theme import ACCENT_SOFT, ASSETS, INK, LINE, PRIMARY, SECONDARY, SOFT, font, icon, svg_size


# 타일 채움색은 5단계: 진초록 N+1 이상 · 연초록 N · 연분홍 N-1 · 진분홍 N-2 이하 · 회색 비교 불가·HPA.
# 위험 후보는 빨강(N-2 이하)과 헷갈리지 않게 채움색 대신 굵은 빨간 테두리로 보인다 (범례 설명과 같은 규칙).
RISK_LINE = "#c9485e"
RISK_INK = "#ad344d"
RISK_FILL = "#fde2e7"
GOAL_FILL = {GoalState.MET: "#ddf4e5", GoalState.BELOW: "#fff0b7", GoalState.UNKNOWN: "#eceef1",
             GoalState.EXCLUDED: "#eceef1"}
TIER_FILL = {Tier.ABOVE: "#a9dbbd", Tier.AT: "#ddf4e5", Tier.ONE_BELOW: "#fce4e7", Tier.TWO_BELOW: "#f6c5cd",
             Tier.UNKNOWN: "#eceef1"}
TIER_LEGEND = ((Tier.ABOVE, "N+1 이상"), (Tier.AT, "N"), (Tier.ONE_BELOW, "N-1"), (Tier.TWO_BELOW, "N-2 이하"),
               (Tier.UNKNOWN, "비교 불가 · HPA"))
GOAL_SHORT = {GoalState.MET: "달성", GoalState.BELOW: "미달", GoalState.UNKNOWN: "확인 필요",
              GoalState.EXCLUDED: "분석 제외"}


def _tile_label(text, size, *, medium=False, color=INK):
    result = label(text, size, medium=medium, color=color, wrap=True)
    ignore_width(result)
    return result


def _version_text(data, families, *, show_tier=True, separator=" / ", empty="SW 미등록"):
    """원문 버전을 중복 없이 표시한다. 필요할 때만 비교 단계를 붙인다."""
    if data.goal is GoalState.EXCLUDED:
        parts = [v.split(" · ", 1)[-1] for v in data.raw_versions]
    else:
        parts = []
        for family in families:
            for version in family.versions:
                raw, gap = version.split(" · ", 1)[-1].rsplit(" · ", 1)
                parts.append(raw if not show_tier or raw == "미입력" or gap == "N 이상" else f"{raw} · {gap}")
    return separator.join(dict.fromkeys(parts)) or empty


def tile_style(data, *, minimum_height=0):
    """타일 채움색·테두리 스타일. 위험 후보는 굵은 빨간 테두리."""
    risk = data.risk is RiskStatus.RISK and data.goal is not GoalState.EXCLUDED
    background = GOAL_FILL[data.goal] if data.goal is GoalState.EXCLUDED else TIER_FILL[data.tier]
    border = f"2px solid {RISK_LINE}" if risk else f"1px solid {background}"
    return (f"QPushButton#equipmentTile {{background:{background};border:{border};"
            f"border-radius:4px;padding:0;min-height:{minimum_height}px;}}"
            f"QPushButton#equipmentTile:hover {{border-color:{PRIMARY};}}"
            f"QPushButton#equipmentTile:focus, QPushButton#equipmentTile:checked {{outline:none;border:2px solid {PRIMARY};}}")


def tile_detail_text(data, group_name):
    """펼친 타일 둘째 줄: 구역 · 설비군(복수 SW) · 달성 여부."""
    name = group_name or (data.model_name if data.goal is GoalState.EXCLUDED else None)
    if data.multi_sw and group_name:
        name = f"{group_name} · 복수 SW"
    return " · ".join([data.area or "구역 미확인"] + ([name] if name else []) + [GOAL_SHORT[data.goal]])


class TileButton(QPushButton):
    def __init__(self, data, *, expanded=False, families=None, group_name=None, show_context=True):
        super().__init__()
        self.data = data
        families = data.families if families is None else families
        self.setObjectName("equipmentTile")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setAccessibleName(data.description)
        self.setAccessibleDescription("Space 또는 클릭으로 설비 선택")
        self.setToolTip(data.description)
        risk = data.risk is RiskStatus.RISK and data.goal is not GoalState.EXCLUDED
        # Replay가 같은 자리 타일의 글자만 바꿀 때 쓴다 (펼친 타일만 있음).
        self.version_label = self.detail_label = None
        body = QVBoxLayout(self)
        body.setContentsMargins(10, 8 if not expanded else 10, 10, 8 if not expanded else 10)
        body.setSpacing(4)
        self.code_label = label(data.code, 13 if show_context else 16, medium=True, color=INK)
        self.marker = "위험" if expanded and risk else ""
        if show_context:
            identity = QHBoxLayout()
            identity.setSpacing(4)
            identity.addWidget(self.code_label)
            identity.addStretch()
            if self.marker:
                identity.addWidget(label(self.marker, 10, medium=True, color=RISK_INK),
                                   0, Qt.AlignmentFlag.AlignTop)
            body.addLayout(identity)
        else:
            body.addStretch()
            self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            body.addWidget(self.code_label)
        if expanded:
            versions = _version_text(data, families, show_tier=show_context)
            self.version_label = _tile_label(versions, 11 if show_context else 14)
            body.addWidget(self.version_label)
            if show_context:
                self.detail_label = _tile_label(tile_detail_text(data, group_name), 10, color=SECONDARY)
                body.addWidget(self.detail_label)
            else:
                self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        body.addStretch()
        # Qt가 QSS의 최소 높이를 레이아웃 힌트보다 우선하므로 타일 본문을 기준으로 지정한다.
        self.style_text = tile_style(data, minimum_height=body.minimumSize().height())
        self.setStyleSheet(self.style_text)

    def minimumSizeHint(self):
        return QSize(0, self.layout().minimumSize().height())

    def sizeHint(self):
        return self.layout().sizeHint()

    def hasHeightForWidth(self):
        return self.layout().hasHeightForWidth()

    def heightForWidth(self, width):
        height = self.layout().totalHeightForWidth(width)
        return height if height >= 0 else self.sizeHint().height()


class GroupCards(FlowGrid):
    """설비군 카드 묶음(최대 3열). 카드 높이가 달라도 위쪽에 맞춘다."""

    def _arrange(self, columns):
        if columns == self.columns:
            return
        for widget in self.items:
            self.grid.removeWidget(widget)
        for index in range(max(columns, self.columns, 1)):
            self.grid.setColumnStretch(index, int(index < columns))
        for index, widget in enumerate(self.items):
            self.grid.addWidget(widget, index // columns, index % columns, Qt.AlignmentFlag.AlignTop)
            widget.show()
        self.columns = columns
        self.updateGeometry()


class TileGrid(QWidget):
    """캔버스 폭에 맞춰 재배치하며 타일은 페이지의 세로 스크롤을 공유한다."""

    def __init__(self, tiles, *, expanded, target_width=None):
        super().__init__()
        self.tiles = tiles
        self.columns = 0
        self.target_width = target_width or (240 if expanded else max(72, *(tile.code_label.sizeHint().width() + 14 for tile in tiles)))
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(8 if target_width else 14)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        # 세로는 내용 높이만 쓴다. 창이 내용보다 크면(4K 등) 남는 높이로 타일이 늘어났다. 같은 행 타일 높이는 맞춘다.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._arrange(1)

    def minimumSizeHint(self):
        return QSize(0, self.grid.minimumSize().height())

    def _arrange(self, columns):
        if columns == self.columns:
            return
        for tile in self.tiles:
            self.grid.removeWidget(tile)
        for index in range(max(columns, self.columns)):
            self.grid.setColumnStretch(index, int(index < columns))
        for index, tile in enumerate(self.tiles):
            self.grid.addWidget(tile, index // columns, index % columns)
        self.columns = columns
        self.setMinimumHeight(self.grid.minimumSize().height())
        self.updateGeometry()

    def resizeEvent(self, event):
        self._arrange(max(1, min(12, (event.size().width() + 8) // (self.target_width + 8))))
        super().resizeEvent(event)


class HeatmapPage(QWidget):
    equipment_selected = Signal(object)

    def __init__(self, state, source, *, detail_connected=False):
        super().__init__()
        self.state, self.source, self.snapshot = state, source, None
        self.detail_connected = detail_connected
        self.selection_hint = ("타일 선택 → 설비 상세 · 이전 조회로 돌아오면 보기와 선택을 유지합니다." if detail_connected else
                               "타일 선택 시 설비 ID를 전달합니다. 설비 목록·상세는 6번 작업에서 연결합니다.")
        self.tiles, self.tile_buttons, self.tile_grids, self.tile_occurrences = (), {}, [], []
        self.visible_ids = ()
        self.selected_id = None
        self._control_rows = None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        status_row = QHBoxLayout()
        status_row.setSpacing(12)
        self.result_status = StatusBadge("조회 적용 전 · 예시 데이터", "info")
        status_row.addWidget(self.result_status, 0, Qt.AlignmentFlag.AlignTop)
        self.context = label("대시보드와 같은 조회 조건을 사용합니다.", 12, color=SECONDARY, wrap=True)
        ignore_width(self.context)
        status_row.addWidget(self.context, 1)
        body.addLayout(status_row)
        # 화면 단순화: 결과 상태 배지·적용 번호 줄은 숨긴다(오류는 본문 안내로 표시).
        self.result_status.hide()
        self.context.hide()

        self.metrics = {
            "analysis": DashboardKpi("분석 설비", "32ac4.svg", accent=True, show_caption=False),
            "met": DashboardKpi("목표 달성", "bbc12.svg", show_caption=False),
            "below": DashboardKpi("목표 미달", "e53b6.svg", show_caption=False),
            "unknown": DashboardKpi("확인 필요", "60dbd.svg", show_caption=False),
        }
        self.summary = DashboardGrid(tuple(self.metrics.values()), minimum_width=150, spacing=12)
        body.addWidget(self.summary)

        self.content = Card(padding=20)
        self.content.body.setSpacing(12)
        self.search = QLineEdit()
        self.search.setObjectName("heatmapSearch")
        self.search.setPlaceholderText("설비 검색")
        self.search.setAccessibleName("설비 검색 · 설비명·위치·버전 원문")
        self.search.addAction(icon("b2c35.svg"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setFont(font(14))
        self.search.setFixedHeight(38)
        self.search.setMinimumWidth(136)
        self.search.setStyleSheet(f"QLineEdit#heatmapSearch {{background:white;border:1px solid {LINE};border-radius:10px;"
                                  "padding:0 10px;}"
                                  f"QLineEdit#heatmapSearch:focus {{border:2px solid {PRIMARY};}}")
        self.search.textChanged.connect(self._render_tiles)
        self.view_toggle = button("간략히 보기")
        self.view_toggle.setCheckable(True)
        self.view_toggle.setChecked(True)
        self.view_toggle.setObjectName("heatmapView")
        self.view_toggle.setFixedSize(104, 38)
        self.view_toggle.setStyleSheet(
            f"QPushButton#heatmapView {{background:white;border:1px solid {LINE};border-radius:10px;"
            "padding:0 10px;min-height:36px;max-height:36px;}"
            f"QPushButton#heatmapView:checked {{background:{PRIMARY};border-color:{PRIMARY};color:white;}}"
            f"QPushButton#heatmapView:focus {{border-color:{PRIMARY};}}"
            f"QPushButton#heatmapView:checked:focus {{border-color:{INK};}}")
        self.view_toggle.toggled.connect(self._change_view)

        self.filter_bar = QWidget()
        filter_row = QHBoxLayout(self.filter_bar)
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)
        self.risk_button = QCheckBox("위험 후보만")
        self.risk_button.setIcon(icon("7880d.svg"))
        self.risk_button.setIconSize(svg_size("7880d.svg"))
        self.below_filter = QCheckBox("목표 미달만")
        checkbox_style = (
            "QCheckBox {spacing:6px;padding:0 4px;}"
            f"QCheckBox::indicator {{width:14px;height:14px;border:1px solid {SECONDARY};border-radius:4px;background:white;}}"
            "QCheckBox:disabled {color:#8b95a8;}"
            f"QCheckBox::indicator:disabled {{background:{SOFT};border-color:{LINE};}}")
        white_check = (ASSETS / "checkbox-tick.svg").as_posix()
        for checkbox, checked_color, checked_image in (
                (self.risk_button, PRIMARY, ""), (self.below_filter, "#3655c7", f"image:url({white_check});")):
            checkbox.setFont(font(13, medium=True))
            checkbox.setFixedHeight(38)
            checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
            checkbox.setStyleSheet(checkbox_style + f"QCheckBox::indicator:checked {{"
                                   f"background:{checked_color};border-color:{checked_color};{checked_image}}}")
            checkbox.setEnabled(False)
            checkbox.toggled.connect(self._render_tiles)
            filter_row.addWidget(checkbox)
        segmented = QFrame()
        segmented.setObjectName("layoutSwitch")
        segmented.setFixedHeight(38)
        segmented.setStyleSheet(f"QFrame#layoutSwitch {{background:white;border:1px solid {LINE};border-radius:10px;}}")
        segment_row = QHBoxLayout(segmented)
        segment_row.setContentsMargins(3, 3, 3, 3)
        segment_row.setSpacing(0)
        self.layout_buttons = QButtonGroup(self)
        for index, (text, asset) in enumerate((("카드형", "9a076.svg"), ("목록형", "73136.svg"))):
            control = QPushButton(text)
            control.setObjectName("layoutOption")
            control.setIcon(icon(asset))
            control.setIconSize(svg_size(asset))
            control.setCheckable(True)
            control.setChecked(index == 0)
            control.setCursor(Qt.CursorShape.PointingHandCursor)
            control.setFont(font(13, medium=True))
            control.setStyleSheet(
                "QPushButton#layoutOption {background:transparent;border:1px solid transparent;border-radius:7px;"
                "padding:0 10px;min-height:28px;max-height:28px;}"
                f"QPushButton#layoutOption:checked {{background:{ACCENT_SOFT};color:{PRIMARY};}}"
                f"QPushButton#layoutOption:focus {{border-color:{PRIMARY};}}")
            self.layout_buttons.addButton(control, index)
            segment_row.addWidget(control)
        self.layout_buttons.idClicked.connect(self._render_tiles)
        self.display_bar = QWidget()
        display_row = QHBoxLayout(self.display_bar)
        display_row.setContentsMargins(0, 0, 0, 0)
        display_row.setSpacing(8)
        display_row.addWidget(self.view_toggle)
        display_row.addWidget(segmented)
        controls_host = QWidget()
        ignore_width(controls_host)
        self.controls = QGridLayout(controls_host)
        self.controls.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.controls.setContentsMargins(0, 0, 0, 0)
        self.controls.setHorizontalSpacing(10)
        self.controls.setVerticalSpacing(10)
        self.content.body.addWidget(controls_host)

        legend = QHBoxLayout()
        legend.setSpacing(20)
        legend_items = [(TIER_FILL[tier], name) for tier, name in TIER_LEGEND] + [(None, "위험 후보")]
        for color, text in legend_items:
            item = QHBoxLayout()
            item.setSpacing(6)
            dot = QFrame()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background:{color};border-radius:5px;" if color else
                              f"background:white;border:2px solid {RISK_LINE};border-radius:3px;")
            item.addWidget(dot)
            item.addWidget(label(text, 12, color=SECONDARY))
            legend.addLayout(item)
        legend.addStretch()
        self.content.body.addLayout(legend)
        legend_note = label("채움색은 목표 N 대비 버전 차이, 굵은 테두리는 위험 판정으로 서로 별개입니다. "
                            "5단계는 11/05 팀 결정(멘토 확인)입니다. 위험 상태는 상세 패널에서 확인 · 회색: 비교 불가·HPA 분석 제외 · "
                            "파란 테두리: 마우스를 올렸거나 선택한 설비", 11,
                            color=SECONDARY, wrap=True)
        ignore_width(legend_note)
        legend_note.hide()  # 범례 설명 문장은 화면에 표시하지 않는다.
        self.content.body.addWidget(legend_note)
        self.view_count = label("조회 적용 전", 12, color=SECONDARY, wrap=True)
        ignore_width(self.view_count)
        self.view_count.hide()
        self.content.body.addWidget(self.view_count)
        self.tile_host = QWidget()
        self.tile_body = QVBoxLayout(self.tile_host)
        self.tile_body.setContentsMargins(0, 4, 0, 0)
        self.tile_body.setSpacing(20)
        self.content.body.addWidget(self.tile_host)
        self.list_model = ReadTableModel(("설비명", "Site / 원본 구역", "모델", "설비 버전", "SW N 판정", "위험 판정"),
                                         numeric_columns=(3,), centered=True)
        self.list_table = read_table(self.list_model, "히트맵 설비 목록형", (110, 150, 190, 230, 120, 160))
        self._list_style = self.list_table.property("setronOriginalStyle") or self.list_table.styleSheet()
        self._compact_list = False
        self.list_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_table.activated.connect(lambda index: self._select_row(index.row()))
        self.list_table.clicked.connect(lambda index: self._select_row(index.row()))
        self.list_table.hide()
        self.content.body.addWidget(self.list_table)
        self.message = label("날짜나 필터를 선택하면 결과가 표시됩니다.", color=SECONDARY, wrap=True)
        ignore_width(self.message)
        self.content.body.addWidget(self.message)
        self.risk_footer = label("", 11, color=SECONDARY)
        self.content.body.addWidget(self.risk_footer)
        self.selection_notice = label(self.selection_hint, 12, color=SECONDARY, wrap=True)
        ignore_width(self.selection_notice)
        self.selection_notice.hide()
        self.content.body.addWidget(self.selection_notice)
        body.addWidget(self.content)
        self.hpa_button = button("특수 설비 HPA 표시")
        self.hpa_button.setCheckable(True)
        self.hpa_button.setFixedSize(280, 34)
        self.hpa_button.setToolTip("분석에서 제외한 HPA 설비를 별도 묶음으로 함께 표시합니다.")
        self.hpa_button.toggled.connect(self._render_tiles)
        body.addWidget(self.hpa_button, 0, Qt.AlignmentFlag.AlignLeft)
        body.addStretch()
        self._reflow()

    def _reflow(self):
        margins = self.content.body.contentsMargins() + self.content.contentsMargins()
        available = self.width() - margins.left() - margins.right()
        spacing = self.controls.horizontalSpacing()
        trailing_width = self.filter_bar.minimumSizeHint().width() + self.display_bar.minimumSizeHint().width() + spacing
        rows = (1 if available >= self.search.minimumWidth() + spacing + trailing_width else
                2 if available >= trailing_width else 3)
        if self._control_rows == rows:
            return
        for widget in (self.search, self.filter_bar, self.display_bar):
            self.controls.removeWidget(widget)
        if rows == 1:
            self.controls.addWidget(self.search, 0, 0)
            self.controls.addWidget(self.filter_bar, 0, 1)
            self.controls.addWidget(self.display_bar, 0, 2, Qt.AlignmentFlag.AlignRight)
        else:
            self.controls.addWidget(self.search, 0, 0, 1, 3)
            self.controls.addWidget(self.filter_bar, 1, 0, 1, rows, Qt.AlignmentFlag.AlignLeft)
            if rows == 2:
                self.controls.addWidget(self.display_bar, 1, 2, Qt.AlignmentFlag.AlignRight)
            else:
                self.controls.addWidget(self.display_bar, 2, 0, 1, 3, Qt.AlignmentFlag.AlignRight)
        self.controls.setColumnStretch(0, 1)
        self._control_rows = rows

    def resizeEvent(self, event):
        self._reflow()
        super().resizeEvent(event)

    def _change_view(self, expanded):
        self.view_toggle.setText("간략히 보기" if expanded else "상세보기")
        self._render_tiles()

    def _clear(self):
        with QSignalBlocker(self.below_filter):
            self.below_filter.setChecked(False)
        self.below_filter.setEnabled(False)
        self.snapshot, self.tiles, self.selected_id = None, (), None
        self.risk_button.setEnabled(False)
        self.risk_button.setChecked(False)
        self.risk_button.setText("위험 후보만")
        self.risk_button.setAccessibleName("위험 후보만")
        for card in self.metrics.values():
            card.value.setText("—")
        self.selection_notice.setText(self.selection_hint)
        self._render_tiles()

    def begin_query(self, query):
        if not self.state.is_current(query):
            return False
        self._clear()
        self.context.setText(f"적용 #{query.revision} · {query.description}")
        self.result_status.setText(f"{getattr(self.source, 'result_label', '예시 결과')} 계산 중")
        self.result_status.set_tone("info")
        self.message.setText("결과를 계산하고 있습니다.")
        return True

    def fail_query(self, query, reason=None):
        if not self.state.is_current(query):
            return
        self._clear()
        self.result_status.setText("결과 표시 실패 · 다시 적용해 주세요")
        self.result_status.set_tone("danger")
        self.message.setText(reason or "관측 자료·선택지 연결을 확인하고 조회 조건을 다시 선택해 주세요.")

    def invalidate(self, *_):
        self._clear()
        self.result_status.setText("선택지 변경 · 재조회 필요")
        self.result_status.set_tone("warning")
        self.context.setText("선택지가 변경되어 이전 결과를 지웠습니다.")
        self.message.setText("조회 조건을 다시 선택해 주세요.")

    @staticmethod
    def _risk_filter_label(result):
        if result.risk_rule_state in (RiskRuleState.UNREGISTERED, RiskRuleState.ALL_DISABLED):
            return result.risk_rule_state.value
        if not result.summary.equipment_count:
            return "분석 대상 없음"
        candidates, unknown = len(result.risky_ids), result.risk_unknown
        if unknown == result.summary.equipment_count:
            return f"위험 미확인 {unknown}대"
        if candidates:
            label = f"위험 후보만 ({candidates}대)"
            if unknown:
                label += f" · 미확인 {unknown}대"
            return label
        if unknown:
            return f"확인된 후보 0대 · 미확인 {unknown}대"
        return "위험 후보 0대"

    def accept_result(self, query, result):
        if (not self.state.is_current(query) or result.query is not query
                or not self.state.catalog.is_view_of(self.source.catalog)):
            return False
        self.tiles = equipment_tiles(result, self.source)
        self.snapshot, self.selected_id = result, None
        self.selection_notice.setText(self.selection_hint)
        self.result_status.setText(f"{getattr(self.source, 'result_label', '예시 결과')} · "
                                   + ("조회 적용됨" if self.tiles else "조건에 맞는 설비 없음"))
        self.result_status.set_tone("info")
        self.context.setText(f"적용 #{query.revision} · 일반 분석 기준일 {result.base_date} · {query.description}\n"
                             f"{result.source_label}\nHPA는 조건에 맞는 최신 자료와 기존 전달 범위의 관측이며 실제 관측일을 표시합니다.")
        filter_label = self._risk_filter_label(result)
        self.risk_button.setText(filter_label)
        self.risk_button.setAccessibleName(filter_label)
        self.risk_button.setEnabled(bool(result.risky_ids))
        self.below_filter.setEnabled(bool(result.summary.equipment_count))
        if not result.risky_ids:
            with QSignalBlocker(self.risk_button):
                self.risk_button.setChecked(False)
        goals = [t.goal for t in self.tiles]
        values = {
            "analysis": result.summary.equipment_count,
            "met": goals.count(GoalState.MET),
            "below": goals.count(GoalState.BELOW),
            "unknown": goals.count(GoalState.UNKNOWN),
        }
        for key, value in values.items():
            self.metrics[key].value.setText(f"{value}대")
        self._render_tiles()
        return True

    def _matches(self, tile, text):
        return not text or any(text in part.casefold() for part in (tile.code, tile.location, tile.description))

    def _render_tiles(self, *_):
        self._reflow()
        while self.tile_body.count():
            widget = self.tile_body.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.tile_buttons, self.tile_grids, self.tile_occurrences = {}, [], []
        text = self.search.text().strip().casefold()
        risk_only = self.risk_button.isChecked()
        below_only = self.below_filter.isChecked()
        scoped = tuple(t for t in self.tiles if not below_only or t.goal is GoalState.BELOW)
        risk_filtered = tuple(t for t in scoped if not risk_only or t.risk is RiskStatus.RISK)
        filtered = tuple(t for t in risk_filtered if self._matches(t, text))
        self.visible_ids = tuple(t.equipment_id for t in filtered)
        self.view_count.setText("표시할 조회 결과가 없습니다." if self.snapshot is None else
                                f"{'목표 미달' if below_only else '전체'}{' · 위험 후보만' if risk_only else ''}{' · 검색 적용' if text else ''}"
                                f" · 표시 {len(filtered)}대 / 선택 범위 {len(self.tiles)}대 · 요약은 선택 범위 전체 기준")
        self.message.setVisible(not filtered)
        if self.snapshot is not None:
            self.message.setText("조건에 맞는 설비가 없습니다. 날짜나 필터를 다시 선택해 주세요." if not self.tiles else
                                 "목표 미달 설비가 없습니다." if not scoped else
                                 "위험 후보가 없습니다. '위험 후보만'을 해제해 주세요." if not risk_filtered else
                                 f"'{self.search.text().strip()}' 검색 결과가 없습니다. 검색어를 지우면 전체 설비가 보입니다.")
        list_mode = self.layout_buttons.checkedId() == 1
        self.list_table.setVisible(list_mode and bool(filtered))
        self.tile_host.setVisible(not list_mode)
        risky = [t for t in self.tiles if t.risk is RiskStatus.RISK]
        self.risk_footer.setVisible(bool(filtered) and bool(risky))
        self.risk_footer.setText(f"위험 후보 {len(risky)}대 · 달성 설비 "
                                 f"{sum(t.goal is GoalState.MET for t in risky)}대 포함")
        if list_mode:
            self._render_list(filtered)
            return
        expanded = self.view_toggle.isChecked()
        has_tiles = bool(filtered)
        self.hpa_button.setVisible(any(t.goal is GoalState.EXCLUDED for t in self.tiles))
        if not has_tiles:
            return
        cards = [self._group_card(title, members, expanded, name)
                 for title, members, name in self._groups(filtered)]
        container = FlowGrid(340, spacing=16, max_columns=3)
        container.set_widgets(cards)
        self.tile_body.addWidget(container)

    def _groups(self, filtered):
        """(제목, 설비, 타일에 쓸 설비군 이름). 복수 SW 설비는 해당 설비군마다 나온다."""
        result, covered = [], set()
        analyzed = [t for t in filtered if t.goal is not GoalState.EXCLUDED]
        for option, cell in zip(self.source.catalog.groups, self.snapshot.groups):
            ids = set(cell.equipment_ids)
            members = [t for t in analyzed if t.equipment_id in ids]
            covered |= ids
            if members:
                result.append((option.label, members, option.label))
        rest = [t for t in analyzed if t.equipment_id not in covered]
        if rest:
            result.append(("설비군 미확인", rest, None))
        hpa = [t for t in filtered if t.goal is GoalState.EXCLUDED]
        if hpa and self.hpa_button.isChecked():
            result.append(("HPA · 분석 제외", hpa, None))
        return result

    def _group_card(self, title, members, expanded, group_name):
        card = QFrame()
        card.setObjectName("heatmapGroup")
        card.setStyleSheet(f"QFrame#heatmapGroup {{background:white;border:1px solid {LINE};border-radius:8px;}}")
        card_body = QVBoxLayout(card)
        card_body.setContentsMargins(12, 12, 12, 12)
        card_body.setSpacing(8)
        card_body.addWidget(label(f"{title} / {len(members)}대", 18, medium=True, wrap=True))
        order = {o.key: index for index, o in enumerate(self.source.catalog.sites)}
        lines = {}
        for tile in members:
            lines.setdefault((tile.site_key, tile.area), []).append(tile)
        for (site_key, area), tiles in sorted(lines.items(), key=lambda item: (order.get(item[0][0], 99),
                                                                               item[0][1] is None, item[0][1] or "")):
            card_body.addWidget(label(f"{tiles[0].site_label} · {area or '구역 미확인'} / {len(tiles)}대",
                                      14, medium=True, wrap=True))
            buttons = []
            for tile in tiles:
                families = tuple(f for f in tile.families
                                 if group_name is None or group_name in f.title.split(" · ")[0].split(" / ")) or tile.families
                control = TileButton(tile, expanded=expanded, families=families, show_context=False)
                control.setChecked(tile.equipment_id == self.selected_id)
                control.clicked.connect(lambda checked=False, eid=tile.equipment_id: self._select(eid))
                self.tile_buttons.setdefault(tile.equipment_id, control)
                self.tile_occurrences.append(control)
                buttons.append(control)
            grid = TileGrid(buttons, expanded=expanded, target_width=140)
            self.tile_grids.append(grid)
            card_body.addWidget(grid)
        card_body.addStretch()
        return card

    def _set_list_compact(self, compact):
        if self._compact_list is compact:
            return
        self._compact_list = compact
        table = self.list_table
        table.setWordWrap(not compact)
        table.setTextElideMode(Qt.TextElideMode.ElideRight if compact else Qt.TextElideMode.ElideNone)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff if compact
                                          else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.verticalHeader().setMinimumSectionSize(34 if compact else 56)
        table.setStyleSheet(self._list_style + ("QTableView::item {padding:3px 8px;}" if compact else ""))
        header = table.horizontalHeader()
        header.setStretchLastSection(not compact)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch if compact else QHeaderView.ResizeMode.Interactive)
        for column in (2, 3):
            table.setColumnHidden(column, compact)
        table.horizontalScrollBar().setValue(0)
        if not compact:
            table.resize_columns()

    def _render_list(self, filtered):
        compact = not self.view_toggle.isChecked()
        self._set_list_compact(compact)
        rows, tones = [], []
        for tile in filtered:
            empty_version = "분석 제외" if tile.goal is GoalState.EXCLUDED else "SW 미등록"
            versions = _version_text(tile, tile.families, show_tier=False, separator="\n", empty=empty_version)
            models = dict.fromkeys(family.title.partition(" · 계열 ")[0] for family in tile.families)
            names = tile.model_name or "\n".join(models) or "—"
            position = tile.site_label, tile.area or "구역 미확인"
            location = (" / ".join(" ".join(part.splitlines()) for part in position)
                        if compact else "\n".join(position))
            risk = tile.risk_label if compact else "\n".join(filter(None, (tile.risk_label, tile.risk_detail)))
            rows.append((tile.code, location, names, versions, tile.goal.value, risk))
            tones.append(tile.version_tone)
        self.list_model.set_rows(rows, tuple(t.equipment_id for t in filtered), tones)
        if rows:
            fit_rows(self.list_table, compact=compact)
            if self.selected_id in self.list_model.ids:
                self.list_table.selectRow(self.list_model.ids.index(self.selected_id))

    def _select_row(self, row):
        if 0 <= row < len(self.list_model.ids):
            self._select(self.list_model.ids[row])

    def _select(self, equipment_id):
        if self.snapshot is None or not self.state.is_current(self.snapshot.query) or equipment_id not in self.visible_ids:
            return
        self.selected_id = equipment_id
        for tile in self.tile_occurrences:
            tile.setChecked(tile.data.equipment_id == equipment_id)
        tile = next(t for t in self.tiles if t.equipment_id == equipment_id)
        self.selection_notice.setText(f"선택: {tile.code} · {tile.goal.value} · {tile.risk_label}\n"
                                      + ("설비 상세로 이동합니다." if self.detail_connected else
                                         "설비 ID 전달 완료 · 목록·상세는 6번 작업에서 연결합니다."))
        self.equipment_selected.emit(EquipmentSelection(self.snapshot.query, self.snapshot.base_date, equipment_id,
                                                        tile.observation_date))
