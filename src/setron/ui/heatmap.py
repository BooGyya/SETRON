"""작업 단위 5: 물리 설비 타일·버전 함께 보기. 목록/상세와 목표 편집은 후속 작업."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QGridLayout, QHBoxLayout, QLayout, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import CompareStatus
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.dashboard import MetricCard
from setron.ui.dashboard_data import count_value, missing_sw_count, upgrade_caption
from setron.ui.heatmap_data import EquipmentSelection, GoalState, equipment_tiles
from setron.ui.theme import PRIMARY, SECONDARY, TONES


GOAL_TONES = {GoalState.MET: "success", GoalState.BELOW: "warning",
              GoalState.UNKNOWN: "neutral", GoalState.EXCLUDED: "neutral"}
FAMILY_TONES = {CompareStatus.AT_OR_ABOVE: "success", CompareStatus.BELOW: "warning",
                CompareStatus.UNDETERMINED: "neutral"}


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
        background, ink, border = TONES[GOAL_TONES[data.goal]]
        risk = data.risk is RiskStatus.RISK
        self.setStyleSheet(
            f"QPushButton#equipmentTile {{background:{background};border:{2 if risk else 1}px solid "
            f"{'#c9485e' if risk else border};border-left:3px solid {'#c9485e' if risk else border};"
            "border-radius:6px;padding:0;min-height:0;}"
            f"QPushButton#equipmentTile:hover {{border-color:{PRIMARY};}}"
            f"QPushButton#equipmentTile:focus, QPushButton#equipmentTile:checked {{outline:none;border:2px solid {PRIMARY};}}"
        )
        body = QVBoxLayout(self)
        body.setContentsMargins(6, 8, 6, 8)
        body.setSpacing(4)
        prefix = "! " if risk else "? " if data.risk is RiskStatus.UNKNOWN and data.goal is not GoalState.EXCLUDED else ""
        self.code_label = label(prefix + data.code, 12, color="#ad344d" if risk else ink)
        body.addWidget(self.code_label)
        if expanded:
            body.addWidget(label(data.goal.value + " · " + data.risk_label, 12, color=ink, wrap=True))
            if data.multi_sw or data.pm_mismatch:
                body.addWidget(label(" · ".join(s for s, enabled in (("복수 SW", data.multi_sw),
                                    ("PM 버전 불일치", data.pm_mismatch)) if enabled), 12, medium=True, wrap=True))
            for family in data.families:
                fill, color, line = TONES[FAMILY_TONES[family.status]]
                segment = QWidget()
                segment.setObjectName("familySegment")
                segment.setStyleSheet(f"QWidget#familySegment {{background:{fill};border-left:3px solid {line};border-radius:3px;}}")
                segment_body = QVBoxLayout(segment)
                segment_body.setContentsMargins(6, 4, 4, 4)
                segment_body.setSpacing(3)
                segment_body.addWidget(label(family.title, 14, color=color, wrap=True))
                for version in family.versions:
                    segment_body.addWidget(label(version, 11, color=color, wrap=True))
                body.addWidget(segment)
            if data.goal is GoalState.EXCLUDED:
                body.addWidget(label(f"{data.model_name or 'HPA'} · 실제 관측일 {data.observation_date}",
                                     12, color=SECONDARY, wrap=True))
                for version in data.raw_versions:
                    body.addWidget(label(version, 11, color=SECONDARY, wrap=True))
            if not data.families and data.goal is not GoalState.EXCLUDED:
                body.addWidget(label("SW 미등록 · 비교 불가", 12, color=SECONDARY, wrap=True))
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        body.addStretch()

    def minimumSizeHint(self):
        return QSize(0, self.layout().minimumSize().height())

    def sizeHint(self):
        return self.layout().sizeHint()


class TileGrid(QWidget):
    """캔버스 폭에 맞춰 재배치하며 타일은 페이지의 세로 스크롤을 공유한다."""

    def __init__(self, tiles, *, expanded):
        super().__init__()
        self.tiles = tiles
        self.columns = 0
        self.target_width = 240 if expanded else max(72, *(tile.code_label.sizeHint().width() + 14 for tile in tiles))
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(14)
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


class HeatmapPage(QWidget):
    equipment_selected = Signal(object)

    def __init__(self, state, source, *, detail_connected=False):
        super().__init__()
        self.state, self.source, self.snapshot = state, source, None
        self.detail_connected = detail_connected
        self.selection_hint = ("타일 선택 → 설비 상세 · 이전 조회로 돌아오면 보기와 선택을 유지합니다." if detail_connected else
                               "타일 선택 시 설비 ID를 전달합니다. 설비 목록·상세는 6번 작업에서 연결합니다.")
        self.tiles, self.tile_buttons, self.tile_grids = (), {}, []
        self.selected_id = None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)
        self.result_status = StatusBadge(f"조회 적용 전 · {getattr(source, 'result_label', '예시 결과')}", "info")
        body.addWidget(self.result_status)
        self.context = label("대시보드와 같은 조건을 사용합니다. 위에서 조회 적용을 눌러 주세요.", 13, color=SECONDARY, wrap=True)
        body.addWidget(self.context)
        self.content = Card()
        heading = QHBoxLayout()
        heading.addWidget(label("설비별 버전 적용 상태", 20, medium=True, wrap=True), 1)
        self.risk_button = button("위험 후보만 보기", asset="7880d.svg")
        self.risk_button.setProperty("dashboardRisk", True)
        self.risk_button.setCheckable(True)
        self.risk_button.setEnabled(False)
        self.risk_button.toggled.connect(self._render_tiles)
        heading.addWidget(self.risk_button)
        self.content.body.addLayout(heading)
        self.content.body.addWidget(label("초록: 목표 달성 · 노랑: 미달 · 회색: 정보 부족/분석 제외\n"
                                          "! 빨간 테두리: 위험 후보 · ? 위험 미확인 · 채움색과 위험은 별개입니다.",
                                          13, color=SECONDARY, wrap=True))
        toolbar = QHBoxLayout()
        self.view_buttons = QButtonGroup(self)
        for index, text in enumerate(("밀집 보기", "버전 함께 보기")):
            control = button(text)
            control.setCheckable(True)
            control.setChecked(index == 0)
            control.setProperty("queryChoice", True)
            control.setProperty("compactChoice", True)
            control.setFixedWidth(130 if index == 0 else 150)
            control.setStyleSheet(f"QPushButton:checked {{background:{PRIMARY};border-color:{PRIMARY};color:white;}}")
            self.view_buttons.addButton(control, index)
            toolbar.addWidget(control)
        toolbar.addStretch()
        self.content.body.addLayout(toolbar)
        self.view_buttons.idClicked.connect(self._render_tiles)
        self.view_count = label("조회 적용 전", 13, color=SECONDARY, wrap=True)
        self.content.body.addWidget(self.view_count)
        self.tile_host = QWidget()
        self.tile_body = QVBoxLayout(self.tile_host)
        self.tile_body.setContentsMargins(0, 0, 0, 0)
        self.tile_body.setSpacing(16)
        self.content.body.addWidget(self.tile_host)
        self.message = label("조회 적용 전입니다.", color=SECONDARY, wrap=True)
        self.content.body.addWidget(self.message)
        self.selection_notice = label(self.selection_hint,
                                      13, color=SECONDARY, wrap=True)
        self.content.body.addWidget(self.selection_notice)
        self.summary = QWidget()
        summary_body = QVBoxLayout(self.summary)
        summary_body.setContentsMargins(0, 0, 0, 0)
        summary_body.setSpacing(12)
        summary_body.addWidget(label("선택 범위 전체 요약", 13, medium=True, color=SECONDARY))
        self.metrics = {}
        for key, title, asset, tone in (("risk", "안전·결함 후보", "60dbd.svg", "risk"),
                                       ("rate", "목표 N 이상 달성률", "bbc12.svg", "achievement"),
                                       ("total", "전체 물리 설비", "32ac4.svg", "neutral"),
                                       ("upgrade", "업그레이드 필요", "e53b6.svg", "neutral")):
            card = MetricCard(title, asset, tone=tone)
            self.metrics[key] = card
            summary_body.addWidget(card)
        summary_body.addStretch()
        self.primary = QGridLayout()
        self.primary.setContentsMargins(0, 0, 0, 0)
        self.primary.setSpacing(24)
        body.addLayout(self.primary)
        self._wide = None
        self._reflow(False)

    def _reflow(self, wide):
        if self._wide is wide:
            return
        self.primary.removeWidget(self.content)
        self.primary.removeWidget(self.summary)
        if wide:
            self.summary.setFixedWidth(288)
        else:
            self.summary.setMaximumWidth(16777215)
            self.summary.setMinimumWidth(0)
        self.primary.addWidget(self.content, 0, 0)
        self.primary.addWidget(self.summary, 0 if wide else 1, 1 if wide else 0,
                               alignment=Qt.AlignmentFlag.AlignTop)
        self.primary.setColumnStretch(0, 1)
        self.primary.setColumnStretch(1, 0)
        self._wide = wide

    def resizeEvent(self, event):
        self._reflow(event.size().width() >= 1160)
        super().resizeEvent(event)

    def _clear(self):
        self.snapshot, self.tiles, self.selected_id = None, (), None
        self.risk_button.setEnabled(False)
        self.risk_button.setChecked(False)
        for card in self.metrics.values():
            card.value.setText("—")
            card.caption.setText("결과 없음")
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

    def show_no_observations(self, query):
        if not self.state.is_current(query):
            return
        self._clear()
        self.result_status.setText(f"{getattr(self.source, 'result_label', '예시 결과')} · 저장된 설비 현황 없음")
        self.result_status.set_tone("info")
        self.context.setText(f"적용 #{query.revision} · {query.description}\n{self.source.source_label}")
        self.message.setText("조회 가능한 설비 현황이 없습니다. 설비 현황표를 가져온 뒤 다시 조회해 주세요.")

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
        self.risk_button.setText(f"위험 후보 {len(result.risky_ids)}대만 보기")
        self.risk_button.setAccessibleName(self.risk_button.text())
        self.risk_button.setEnabled(bool(self.tiles))
        summary = result.summary
        values = {
            "total": (f"{len(self.tiles)}대", f"분석 {summary.equipment_count}대 · HPA 제외 {sum(t.goal is GoalState.EXCLUDED for t in self.tiles)}대"),
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
        self.metrics["rate"].caption.setText(values["rate"][1] + "\n단위: 설비·계열 건 · 판단 불가는 분모 제외")
        if summary.rate == 1 and (not summary.complete or missing_sw_count(summary)):
            self.metrics["rate"].caption.setText(self.metrics["rate"].caption.text() + " · 확인 필요")
        self._render_tiles()
        return True

    def _render_tiles(self, *_):
        while self.tile_body.count():
            widget = self.tile_body.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.tile_buttons, self.tile_grids = {}, []
        filtered = tuple(t for t in self.tiles if not self.risk_button.isChecked() or t.risk is RiskStatus.RISK)
        self.view_count.setText("표시할 조회 결과가 없습니다." if self.snapshot is None else
                                f"{'위험 후보만' if self.risk_button.isChecked() else '전체'} · 표시 {len(filtered)}대 / 선택 범위 {len(self.tiles)}대"
                                " · 요약은 선택 범위 전체 기준")
        self.message.setVisible(not filtered)
        if self.snapshot is not None:
            self.message.setText("위험 후보가 없습니다. 전체 보기로 돌아가려면 후보 버튼을 다시 눌러 주세요." if self.tiles else
                                 "조건에 맞는 설비가 없습니다. 조건 변경 후 조회 적용을 눌러 주세요.")
        expanded = self.view_buttons.checkedId() == 1
        for site in self.source.catalog.sites:
            records = [t for t in filtered if t.site_key == site.key]
            if not records:
                continue
            group = QWidget()
            layout = QVBoxLayout(group)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(12)
            layout.addWidget(label(f"{site.label} · {len(records)}대", 16, medium=True))
            buttons = []
            for tile in records:
                control = TileButton(tile, expanded=expanded)
                control.setChecked(tile.equipment_id == self.selected_id)
                control.clicked.connect(lambda checked=False, eid=tile.equipment_id: self._select(eid))
                self.tile_buttons[tile.equipment_id] = control
                buttons.append(control)
            grid = TileGrid(buttons, expanded=expanded)
            self.tile_grids.append(grid)
            layout.addWidget(grid)
            self.tile_body.addWidget(group)

    def _select(self, equipment_id):
        if self.snapshot is None or not self.state.is_current(self.snapshot.query) or equipment_id not in self.tile_buttons:
            return
        self.selected_id = equipment_id
        for eid, tile in self.tile_buttons.items():
            tile.setChecked(eid == equipment_id)
        tile = self.tile_buttons[equipment_id].data
        self.selection_notice.setText(f"선택: {tile.code} · {tile.goal.value} · {tile.risk_label}\n"
                                      + ("설비 상세로 이동합니다." if self.detail_connected else
                                         "설비 ID 전달 완료 · 목록·상세는 6번 작업에서 연결합니다."))
        self.equipment_selected.emit(EquipmentSelection(self.snapshot.query, self.snapshot.base_date, equipment_id,
                                                        tile.observation_date))
