"""작업 단위 5: 물리 설비 타일·버전 함께 보기. VER4 히트맵(상세보기·간략히 보기, 카드형·목록형) 배치."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLayout, QLineEdit, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from setron.features.risk.schemas import RiskStatus
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.dashboard_data import count_value, missing_sw_count, upgrade_caption
from setron.ui.equipment import FlowGrid, ReadTableModel, fit_rows, ignore_width, read_table
from setron.ui.heatmap_data import FAR_BELOW_STEPS, EquipmentSelection, GoalState, equipment_tiles
from setron.ui.theme import INK, LINE, PRIMARY, SECONDARY, TONES, font, icon, svg_size


# 채움색 규칙은 heatmap_data.compare_tone/version_tone 한 곳에서 정한다. 위험은 채움색과 별개로 굵은 테두리.
RISK_LINE = "#c9485e"
RISK_INK = "#ad344d"
GOAL_SHORT = {GoalState.MET: "달성", GoalState.BELOW: "미달", GoalState.UNKNOWN: "확인 필요",
              GoalState.EXCLUDED: "분석 제외"}


def _tile_label(text, size, *, medium=False, color=INK):
    result = label(text, size, medium=medium, color=color, wrap=True)
    ignore_width(result)
    return result


class TileButton(QPushButton):
    def __init__(self, data, *, expanded=False):
        super().__init__()
        self.data = data
        self.setObjectName("equipmentTile")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setAccessibleName(data.description)
        self.setAccessibleDescription("Space 또는 클릭으로 설비 선택")
        self.setToolTip(data.description)
        # 채움색은 버전 비교(초록 N 이상 · 노랑 N-1/확인 필요 · 빨강 N-2 이하 · 회색 HPA),
        # 테두리는 위험 판정. 둘은 서로 덮지 않는다.
        background = TONES[data.version_tone][0]
        risk = data.risk is RiskStatus.RISK
        self.setStyleSheet(
            f"QPushButton#equipmentTile {{background:{background};border:{2 if risk else 1}px solid "
            f"{RISK_LINE if risk else LINE};border-radius:4px;padding:0;min-height:0;}}"
            f"QPushButton#equipmentTile:hover {{border-color:{PRIMARY};}}"
            f"QPushButton#equipmentTile:focus, QPushButton#equipmentTile:checked {{outline:none;border:2px solid {PRIMARY};}}"
        )
        body = QVBoxLayout(self)
        body.setContentsMargins(10, 8 if not expanded else 10, 10, 8 if not expanded else 10)
        body.setSpacing(3)
        identity = QHBoxLayout()
        identity.setSpacing(3)
        prefix = "! " if risk else "? " if data.risk is RiskStatus.UNKNOWN and data.goal is not GoalState.EXCLUDED else ""
        self.code_label = label(prefix + data.code, 13, medium=True, color=RISK_INK if risk else INK)
        identity.addWidget(self.code_label)
        identity.addStretch()
        if risk:
            identity.addWidget(label("위험", 10, medium=True, color=RISK_INK), 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(identity)
        if expanded:
            for family in data.families:
                line = TONES[family.tone][2]
                segment = QWidget()
                segment.setObjectName("familySegment")
                segment.setStyleSheet(f"QWidget#familySegment {{background:transparent;border-left:2px solid {line};}}")
                segment_body = QVBoxLayout(segment)
                segment_body.setContentsMargins(6, 0, 0, 0)
                segment_body.setSpacing(1)
                for version in family.versions:
                    segment_body.addWidget(_tile_label(version, 11))
                segment_body.addWidget(_tile_label(family.title, 10, color=SECONDARY))
                body.addWidget(segment)
            if data.multi_sw or data.pm_mismatch:
                body.addWidget(_tile_label(" · ".join(s for s, enabled in (("복수 SW", data.multi_sw),
                                           ("PM 버전 불일치", data.pm_mismatch)) if enabled), 10, medium=True))
            if data.goal is GoalState.EXCLUDED:
                for version in data.raw_versions:
                    body.addWidget(_tile_label(version, 11))
                body.addWidget(_tile_label(f"{data.model_name or 'HPA'} · 실제 관측일 {data.observation_date}", 10,
                                           color=SECONDARY))
            if not data.families and data.goal is not GoalState.EXCLUDED:
                body.addWidget(_tile_label("SW 미등록 · 비교 불가", 11, color=SECONDARY))
            # 등록 규칙상 후보 아님은 테두리 없음으로 표시하고 설명(툴팁·접근성 이름)에 남긴다.
            footer = [data.area or "구역 미확인", GOAL_SHORT[data.goal]]
            if data.risk is not RiskStatus.NO_RISK and data.goal is not GoalState.EXCLUDED:
                footer.append(data.risk_label)
            body.addWidget(_tile_label(" · ".join(footer), 10))
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        body.addStretch()

    def minimumSizeHint(self):
        return QSize(0, self.layout().minimumSize().height())

    def sizeHint(self):
        return self.layout().sizeHint()

    def hasHeightForWidth(self):
        return self.layout().hasHeightForWidth()

    def heightForWidth(self, width):
        height = self.layout().totalHeightForWidth(width)
        return height if height >= 0 else self.sizeHint().height()


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
        self.updateGeometry()

    def resizeEvent(self, event):
        self._arrange(max(1, min(12, (event.size().width() + 8) // (self.target_width + 8))))
        super().resizeEvent(event)


class StripMetric(QWidget):
    """요약 띠의 한 항목. 보조 설명(caption)은 띠 아래 근거 줄에 함께 표시한다."""

    def __init__(self, title, color=INK):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.title = label(title, 12, color=SECONDARY)
        row.addWidget(self.title)
        self.value = label("—", 22, medium=True, color=color)
        row.addWidget(self.value)
        row.addStretch()
        self.caption = label("", 11, color=SECONDARY, wrap=True)
        ignore_width(self.caption)


class HeatmapPage(QWidget):
    equipment_selected = Signal(object)

    def __init__(self, state, source, *, detail_connected=False):
        super().__init__()
        self.state, self.source, self.snapshot = state, source, None
        self.detail_connected = detail_connected
        self.selection_hint = ("타일 선택 → 설비 상세 · 이전 조회로 돌아오면 보기와 선택을 유지합니다." if detail_connected else
                               "타일 선택 시 설비 ID를 전달합니다. 설비 목록·상세는 6번 작업에서 연결합니다.")
        self.tiles, self.tile_buttons, self.tile_grids = (), {}, []
        self.visible_ids = ()
        self.selected_id = None
        self._compact = None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        status_row = QHBoxLayout()
        status_row.setSpacing(12)
        self.result_status = StatusBadge("조회 적용 전 · 예시 데이터", "info")
        status_row.addWidget(self.result_status, 0, Qt.AlignmentFlag.AlignTop)
        self.context = label("대시보드와 같은 조건을 사용합니다. 위에서 조회 적용을 눌러 주세요.", 12, color=SECONDARY, wrap=True)
        ignore_width(self.context)
        status_row.addWidget(self.context, 1)
        body.addLayout(status_row)
        # 화면 단순화: 결과 상태 배지·적용 번호 줄은 숨긴다(오류는 본문 안내로 표시).
        self.result_status.hide()
        self.context.hide()

        # 요약 띠: 분석 대수 · 목표 달성/미달/확인 필요 · 위험 후보 · 달성률 · 업그레이드.
        self.summary = Card(padding=12)
        self.summary.body.setSpacing(8)
        self.metrics = {}
        strip = FlowGrid(150, spacing=20)
        for key, title, color in (("total", "전체 설비", INK), ("met", "목표 달성", TONES["success"][1]),
                                  ("below", "목표 미달", TONES["warning"][1]), ("unknown", "확인 필요", TONES["warning"][1]),
                                  ("risk", "위험 후보", RISK_INK), ("rate", "목표 N 달성률", PRIMARY),
                                  ("upgrade", "업그레이드 필요", INK)):
            self.metrics[key] = StripMetric(title, color)
        strip.set_widgets(list(self.metrics.values()))
        self.summary.body.addWidget(strip)
        notes = QVBoxLayout()
        notes.setSpacing(2)
        for key in ("total", "rate", "risk", "upgrade"):
            self.metrics[key].caption.setText(f"{self.metrics[key].title.text()}: 조회 적용 전")
            notes.addWidget(self.metrics[key].caption)
        self.summary.body.addLayout(notes)
        body.addWidget(self.summary)

        self.content = Card(padding=20)
        self.content.body.setSpacing(12)
        self.search = QLineEdit()
        self.search.setObjectName("heatmapSearch")
        self.search.setPlaceholderText("설비 검색")
        self.search.setAccessibleName("설비 검색 · 설비명·위치·버전 원문")
        self.search.addAction(icon("b2c35.svg"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setFont(font(15))
        self.search.setFixedHeight(46)
        self.search.setStyleSheet(f"QLineEdit#heatmapSearch {{background:white;border:1px solid {LINE};border-radius:12px;"
                                  "padding:0 10px;}"
                                  f"QLineEdit#heatmapSearch:focus {{border:2px solid {PRIMARY};}}")
        self.search.textChanged.connect(self._render_tiles)
        self.content.body.addWidget(self.search)

        self.view_buttons = QButtonGroup(self)
        self.view_bar = QWidget()
        view_row = QHBoxLayout(self.view_bar)
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(10)
        # id 1 = 상세보기(버전 함께 보기), id 0 = 간략히 보기(밀집 보기). 화면 순서는 VER4와 같다.
        for index, text in ((1, "상세보기"), (0, "간략히 보기")):
            control = button(text)
            control.setCheckable(True)
            control.setChecked(index == 1)
            control.setObjectName("heatmapView")
            control.setFont(font(15, medium=True))
            control.setStyleSheet(
                f"QPushButton#heatmapView {{background:white;border:1px solid {LINE};border-radius:10px;padding:0 22px;"
                "min-height:40px;max-height:40px;}"
                f"QPushButton#heatmapView:checked {{background:{PRIMARY};border-color:{PRIMARY};color:white;}}"
                f"QPushButton#heatmapView:focus {{border:2px solid {PRIMARY};}}")
            self.view_buttons.addButton(control, index)
            view_row.addWidget(control)
        divider = QFrame()
        divider.setFixedSize(1, 30)
        divider.setStyleSheet(f"background:{LINE};")
        view_row.addWidget(divider)
        view_row.addStretch()
        self.view_buttons.idClicked.connect(self._render_tiles)

        self.filter_bar = QWidget()
        filter_row = QHBoxLayout(self.filter_bar)
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(10)
        self.risk_button = QCheckBox("위험 후보만")
        self.risk_button.setIcon(icon("7880d.svg"))
        self.risk_button.setIconSize(svg_size("7880d.svg"))
        self.risk_button.setFont(font(15, medium=True))
        self.risk_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.risk_button.setStyleSheet(
            "QCheckBox {spacing:8px;padding:0 6px;}"
            "QCheckBox::indicator {width:16px;height:16px;border:2px solid rgba(20,32,51,0.55);border-radius:5px;background:white;}"
            f"QCheckBox::indicator:checked {{background:{PRIMARY};border-color:{PRIMARY};}}"
            "QCheckBox:disabled {color:#8b95a8;}")
        self.risk_button.setEnabled(False)
        self.risk_button.toggled.connect(self._render_tiles)
        filter_row.addWidget(self.risk_button)
        segmented = QFrame()
        segmented.setObjectName("layoutSwitch")
        segmented.setStyleSheet(f"QFrame#layoutSwitch {{background:white;border:1px solid {LINE};border-radius:12px;}}")
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
            control.setFont(font(15, medium=True))
            control.setStyleSheet(
                "QPushButton#layoutOption {background:transparent;border:none;border-radius:9px;padding:0 18px;"
                "min-height:34px;max-height:34px;}"
                f"QPushButton#layoutOption:checked {{background:rgba(60,92,233,0.1);color:{PRIMARY};}}"
                f"QPushButton#layoutOption:focus {{border:2px solid {PRIMARY};}}")
            self.layout_buttons.addButton(control, index)
            segment_row.addWidget(control)
        self.layout_buttons.idClicked.connect(self._render_tiles)
        filter_row.addWidget(segmented)
        self.controls = QGridLayout()
        self.controls.setContentsMargins(0, 0, 0, 0)
        self.controls.setHorizontalSpacing(10)
        self.controls.setVerticalSpacing(10)
        self.content.body.addLayout(self.controls)

        legend = QHBoxLayout()
        legend.setSpacing(20)
        for color, text in ((TONES["success"][2], "목표 N 이상"), ("#e7b83d", "N-1 · 확인 필요"),
                            (TONES["danger"][2], f"N-{FAR_BELOW_STEPS} 이하 · 많이 낮음 (임시 기준)"),
                            (RISK_LINE, "위험 후보 · 굵은 테두리·위험 표시")):
            item = QHBoxLayout()
            item.setSpacing(6)
            dot = QFrame()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background:{color};border-radius:5px;")
            item.addWidget(dot)
            item.addWidget(label(text, 12, color=SECONDARY))
            legend.addLayout(item)
        legend.addStretch()
        self.content.body.addLayout(legend)
        legend_note = label("채움색은 목표 N 대비 버전 차이, 굵은 테두리는 위험 판정으로 서로 별개입니다. "
                            "많이 낮음 기준은 확정 전 임시값입니다. ? 위험 미확인 · 회색: HPA 분석 제외 · "
                            "파란 테두리: 마우스를 올렸거나 선택한 설비", 11,
                            color=SECONDARY, wrap=True)
        ignore_width(legend_note)
        legend_note.hide()  # 범례 설명 문장은 화면에 표시하지 않는다.
        self.content.body.addWidget(legend_note)
        self.view_count = label("조회 적용 전", 12, color=SECONDARY, wrap=True)
        ignore_width(self.view_count)
        self.content.body.addWidget(self.view_count)
        self.tile_host = QWidget()
        self.tile_body = QVBoxLayout(self.tile_host)
        self.tile_body.setContentsMargins(0, 4, 0, 0)
        self.tile_body.setSpacing(20)
        self.content.body.addWidget(self.tile_host)
        self.list_model = ReadTableModel(("설비명", "Site / 원본 구역", "모델 · 계열", "설비 버전", "SW N 판정", "위험 판정"),
                                         numeric_columns=(3,))
        self.list_table = read_table(self.list_model, "히트맵 설비 목록형", (110, 150, 190, 230, 120, 160))
        self.list_table.activated.connect(lambda index: self._select_row(index.row()))
        self.list_table.clicked.connect(lambda index: self._select_row(index.row()))
        self.list_table.hide()
        self.content.body.addWidget(self.list_table)
        self.message = label("조회 적용 전입니다.", color=SECONDARY, wrap=True)
        ignore_width(self.message)
        self.content.body.addWidget(self.message)
        self.selection_notice = label(self.selection_hint, 12, color=SECONDARY, wrap=True)
        ignore_width(self.selection_notice)
        self.content.body.addWidget(self.selection_notice)
        body.addWidget(self.content)
        body.addStretch()
        self._reflow(False)

    def _reflow(self, compact):
        if self._compact is compact:
            return
        self.controls.removeWidget(self.view_bar)
        self.controls.removeWidget(self.filter_bar)
        self.controls.addWidget(self.view_bar, 0, 0)
        self.controls.addWidget(self.filter_bar, 1 if compact else 0, 0 if compact else 1,
                                alignment=Qt.AlignmentFlag.AlignLeft if compact else Qt.AlignmentFlag.AlignRight)
        self.controls.setColumnStretch(0, 1)
        self._compact = compact

    def resizeEvent(self, event):
        self._reflow(event.size().width() < 780)
        super().resizeEvent(event)

    def _clear(self):
        self.snapshot, self.tiles, self.selected_id = None, (), None
        self.risk_button.setEnabled(False)
        self.risk_button.setChecked(False)
        self.risk_button.setText("위험 후보만")
        for card in self.metrics.values():
            card.value.setText("—")
            card.caption.clear()
        for key in ("total", "rate", "risk", "upgrade"):
            self.metrics[key].caption.setText(f"{self.metrics[key].title.text()}: 결과 없음")
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

    def fail_query(self, query):
        if not self.state.is_current(query):
            return
        self._clear()
        self.result_status.setText("결과 표시 실패 · 다시 적용해 주세요")
        self.result_status.set_tone("danger")
        self.message.setText("관측 자료·선택지 연결을 확인하고 조회 적용으로 다시 시도해 주세요.")

    def invalidate(self, *_):
        self._clear()
        self.result_status.setText("선택지 변경 · 재조회 필요")
        self.result_status.set_tone("warning")
        self.context.setText("선택지가 변경되어 이전 결과를 지웠습니다.")
        self.message.setText("조회 적용으로 결과를 다시 확인해 주세요.")

    def accept_result(self, query, result):
        if not self.state.is_current(query) or result.query is not query or self.state.catalog != self.source.catalog:
            return False
        self.tiles = equipment_tiles(result, self.source)
        self.snapshot, self.selected_id = result, None
        self.selection_notice.setText(self.selection_hint)
        self.result_status.setText(f"{getattr(self.source, 'result_label', '예시 결과')} · "
                                   + ("조회 적용됨" if self.tiles else "조건에 맞는 설비 없음"))
        self.result_status.set_tone("info")
        self.context.setText(f"적용 #{query.revision} · 일반 분석 기준일 {result.base_date} · {query.description}\n"
                             f"{result.source_label}\nHPA는 조건에 맞는 최신 자료와 기존 전달 범위의 관측이며 실제 관측일을 표시합니다.")
        self.risk_button.setText(f"위험 후보만 ({len(result.risky_ids)}대)")
        self.risk_button.setAccessibleName(f"위험 후보 {len(result.risky_ids)}대만 보기")
        self.risk_button.setEnabled(bool(self.tiles))
        summary = result.summary
        goals = [t.goal for t in self.tiles]
        values = {
            "total": (f"{len(self.tiles)}대", f"분석 {summary.equipment_count}대 · HPA 제외 {goals.count(GoalState.EXCLUDED)}대"),
            "met": (f"{goals.count(GoalState.MET)}", ""),
            "below": (f"{goals.count(GoalState.BELOW)}", ""),
            "unknown": (f"{goals.count(GoalState.UNKNOWN)}", ""),
            "rate": ("계산 불가" if summary.rate is None else f"{summary.rate:.1%}",
                     f"{summary.compliant}/{summary.compliant + summary.below}건 · 판단 불가 {summary.undetermined}건 · SW 미등록 {missing_sw_count(summary)}대"),
            "risk": (count_value(len(result.risky_ids), result.risk_unknown, "미확인"),
                     f"목표 달성과 별도 · 위험 미확인 {result.risk_unknown}대"),
            "upgrade": (count_value(summary.upgrade_needed, summary.undetermined, "판단 불가"),
                        upgrade_caption("하나라도 N 미만인 물리 설비 · 중복 제거", summary.undetermined)),
        }
        for key, (value, caption) in values.items():
            self.metrics[key].value.setText(value)
            self.metrics[key].caption.setText(caption)
        self.metrics["rate"].caption.setText(values["rate"][1] + " · 단위: 설비·계열 건 · 판단 불가는 분모 제외")
        if summary.rate == 1 and (not summary.complete or missing_sw_count(summary)):
            self.metrics["rate"].caption.setText(self.metrics["rate"].caption.text() + " · 확인 필요")
        for key in ("total", "rate", "risk", "upgrade"):
            metric = self.metrics[key]
            metric.caption.setText(f"{metric.title.text()}: {metric.caption.text()}")
        self._render_tiles()
        return True

    def _matches(self, tile, text):
        return not text or any(text in part.casefold() for part in (tile.code, tile.location, tile.description))

    def _render_tiles(self, *_):
        while self.tile_body.count():
            widget = self.tile_body.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.tile_buttons, self.tile_grids = {}, []
        text = self.search.text().strip().casefold()
        risk_only = self.risk_button.isChecked()
        risk_filtered = tuple(t for t in self.tiles if not risk_only or t.risk is RiskStatus.RISK)
        filtered = tuple(t for t in risk_filtered if self._matches(t, text))
        self.visible_ids = tuple(t.equipment_id for t in filtered)
        self.view_count.setText("표시할 조회 결과가 없습니다." if self.snapshot is None else
                                f"{'위험 후보만' if risk_only else '전체'}{' · 검색 적용' if text else ''}"
                                f" · 표시 {len(filtered)}대 / 선택 범위 {len(self.tiles)}대 · 요약은 선택 범위 전체 기준")
        self.message.setVisible(not filtered)
        if self.snapshot is not None:
            self.message.setText("조건에 맞는 설비가 없습니다. 조건 변경 후 조회 적용을 눌러 주세요." if not self.tiles else
                                 "위험 후보가 없습니다. 전체 보기로 돌아가려면 '위험 후보만'을 해제해 주세요." if not risk_filtered else
                                 f"'{self.search.text().strip()}' 검색 결과가 없습니다. 검색어를 지우면 전체 설비가 보입니다.")
        list_mode = self.layout_buttons.checkedId() == 1
        self.list_table.setVisible(list_mode and bool(filtered))
        self.tile_host.setVisible(not list_mode)
        if list_mode:
            self._render_list(filtered)
            return
        expanded = self.view_buttons.checkedId() == 1
        for site in self.source.catalog.sites:
            records = [t for t in filtered if t.site_key == site.key]
            if not records:
                continue
            group = QWidget()
            layout = QVBoxLayout(group)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            heading = QWidget()
            heading.setObjectName("siteHeading")
            heading.setStyleSheet(f"QWidget#siteHeading {{border-bottom:1px solid {LINE};}}")
            heading_row = QHBoxLayout(heading)
            heading_row.setContentsMargins(0, 0, 0, 6)
            heading_row.setSpacing(10)
            heading_row.addWidget(label(site.label, 15, medium=True))
            heading_row.addWidget(label(f"{len(records)}대", 12, color=SECONDARY))
            areas = " · ".join(dict.fromkeys(t.area for t in records if t.area))
            area_label = label(f"원본 구역 {areas}" if areas else "원본 구역 미확인", 11, color=SECONDARY)
            ignore_width(area_label)
            heading_row.addWidget(area_label, 1)
            layout.addWidget(heading)
            buttons = []
            for tile in records:
                control = TileButton(tile, expanded=expanded)
                control.setChecked(tile.equipment_id == self.selected_id)
                control.clicked.connect(lambda checked=False, eid=tile.equipment_id: self._select(eid))
                self.tile_buttons[tile.equipment_id] = control
                buttons.append(control)
            target = 150 if expanded else max(96, *(b.code_label.sizeHint().width() + 46 for b in buttons))
            grid = TileGrid(buttons, expanded=expanded, target_width=target)
            self.tile_grids.append(grid)
            layout.addWidget(grid)
            self.tile_body.addWidget(group)

    def _render_list(self, filtered):
        rows, tones = [], []
        for tile in filtered:
            versions = "\n".join(tile.raw_versions or tuple(v for f in tile.families for v in f.versions)) or (
                "분석 제외" if tile.goal is GoalState.EXCLUDED else "SW 미등록")
            names = tile.model_name or "\n".join(f.title for f in tile.families) or "—"
            rows.append((tile.code, f"{tile.site_label}\n{tile.area or '구역 미확인'}", names, versions, tile.goal.value,
                         tile.risk_label + ("\n세부 근거 미연결" if tile.risk is RiskStatus.RISK else "")))
            tones.append(tile.version_tone)
        self.list_model.set_rows(rows, tuple(t.equipment_id for t in filtered), tones)
        if rows:
            fit_rows(self.list_table)
            if self.selected_id in self.list_model.ids:
                self.list_table.selectRow(self.list_model.ids.index(self.selected_id))

    def _select_row(self, row):
        if 0 <= row < len(self.list_model.ids):
            self._select(self.list_model.ids[row])

    def _select(self, equipment_id):
        if self.snapshot is None or not self.state.is_current(self.snapshot.query) or equipment_id not in self.visible_ids:
            return
        self.selected_id = equipment_id
        for eid, tile in self.tile_buttons.items():
            tile.setChecked(eid == equipment_id)
        tile = next(t for t in self.tiles if t.equipment_id == equipment_id)
        self.selection_notice.setText(f"선택: {tile.code} · {tile.goal.value} · {tile.risk_label}\n"
                                      + ("설비 상세로 이동합니다." if self.detail_connected else
                                         "설비 ID 전달 완료 · 목록·상세는 6번 작업에서 연결합니다."))
        self.equipment_selected.emit(EquipmentSelection(self.snapshot.query, self.snapshot.base_date, equipment_id,
                                                        tile.observation_date))
