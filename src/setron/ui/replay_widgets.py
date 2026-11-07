"""VER5 Replay의 데이터 꺾은선과 설비군→Line→설비 묶음. 기존 타일 부품을 재사용한다."""

from PySide6.QtCharts import QBarCategoryAxis, QLineSeries, QScatterSeries, QValueAxis
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from setron.ui.components import label
from setron.ui.dashboard_charts import DashboardChart, series_color
from setron.ui.equipment import ignore_width
from setron.ui.heatmap import GroupCards, TileButton, TileGrid
from setron.ui.heatmap_data import GoalState
from setron.ui.theme import LINE, SECONDARY


class ReplayChart(DashboardChart):
    date_selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(300)
        self.setMaximumHeight(300)
        self.setAccessibleName("설비군별 목표 적용 추이 · 저장된 관측일의 달성률")
        self.values = {}

    def clear(self):
        super().clear()
        self.values = {}
        self.setAccessibleDescription("")

    def show_data(self, data, visible_groups):
        chart = self._chart()
        self.values = {}
        self.setAccessibleDescription("")
        if not data.frames:
            return
        days = tuple(frame.day for frame in data.frames)
        x, y = QBarCategoryAxis(), QValueAxis()
        pattern = "%Y-%m-%d" if days[0].year != days[-1].year else "%m-%d"
        x.append([d.strftime(pattern) for d in days])
        x.setGridLineVisible(False)
        y.setRange(0, 100)
        y.setTickCount(5)
        y.setLabelFormat("%.0f%%")
        for axis, alignment in ((x, Qt.AlignmentFlag.AlignBottom), (y, Qt.AlignmentFlag.AlignLeft)):
            self._axis(axis)
            chart.addAxis(axis, alignment)
        for group in data.source.catalog.groups:
            if group.key not in visible_groups:
                continue
            values, segments, segment = [], [], []
            for i, frame in enumerate(data.frames):
                cell = next((g for g in frame.snapshot.groups if g.group_keys == (group.key,)), None)
                rate = cell.summary.rate if cell is not None else None
                values.append(rate)
                if rate is None:
                    if segment:
                        segments.append(segment)
                    segment = []
                else:
                    segment.append((i, rate * 100))
            if segment:
                segments.append(segment)
            self.values[group.key] = tuple(values)
            for points in segments:
                series = QLineSeries() if len(points) > 1 else QScatterSeries()
                series.setName(group.label)
                series.setColor(QColor(series_color(group.key)))
                if isinstance(series, QLineSeries):
                    series.setPen(QPen(series.color(), 2.5))
                    series.setPointsVisible(True)
                else:
                    series.setMarkerSize(6)
                for px, py in points:
                    series.append(px, py)
                chart.addSeries(series)
                series.attachAxis(x)
                series.attachAxis(y)
                series.clicked.connect(lambda point: self.date_selected.emit(round(point.x())))
        self.setAccessibleDescription(" · ".join(
            f"{group}: " + ", ".join(f"{day} {'확인 필요' if value is None else f'{value:.1%}'}"
                                     for day, value in zip(days, values))
            for group, values in self.values.items()))


class ReplayHeatmap(QWidget):
    equipment_selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(12)
        self.tile_buttons, self.tile_occurrences, self.group_cards = {}, [], []
        self.visible_ids = ()

    def clear(self):
        self.tile_buttons, self.tile_occurrences, self.group_cards = {}, [], []
        self.visible_ids = ()
        while self.body.count():
            widget = self.body.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()

    def show_frame(self, data, frame):
        self.clear()
        tiles = frame.tiles
        self.visible_ids = tuple(t.equipment_id for t in tiles)
        groups, covered = [], set()
        for group in data.source.catalog.groups:
            cell = next((g for g in frame.snapshot.groups if g.group_keys == (group.key,)), None)
            ids = set(cell.equipment_ids) if cell is not None else set()
            members = tuple(t for t in tiles if t.equipment_id in ids and t.goal is not GoalState.EXCLUDED)
            if members:
                groups.append((group.label, members))
                covered.update(t.equipment_id for t in members)
        unknown = tuple(t for t in tiles if t.equipment_id not in covered and t.goal is not GoalState.EXCLUDED)
        if unknown:
            groups.append(("SW 미등록", unknown))
        hpa = tuple(t for t in tiles if t.goal is GoalState.EXCLUDED)
        if hpa:
            groups.append(("HPA · 분석 제외", hpa))
        grid = GroupCards(340, spacing=12, max_columns=3)
        site_order = {site.label: i for i, site in enumerate(data.source.catalog.sites)}
        for title, members in groups:
            card = QFrame()
            card.setObjectName("replayGroup")
            card.setStyleSheet(f"QFrame#replayGroup {{background:white;border:1px solid {LINE};border-radius:8px;}}")
            body = QVBoxLayout(card)
            body.setContentsMargins(12, 12, 12, 12)
            body.setSpacing(12)
            body.addWidget(label(f"{title} · {len(members)}대", 16, medium=True))
            lines = {}
            for tile in members:
                lines.setdefault((tile.site_label, tile.line_label or tile.area), []).append(tile)
            for (site, line), line_tiles in sorted(lines.items(), key=lambda item: (
                    site_order.get(item[0][0], len(site_order)), item[0][0], item[0][1] or "")):
                heading = label(f"{site} · {line or '구역 미확인'} · {len(line_tiles)}대", 12, medium=True, wrap=True)
                ignore_width(heading)
                body.addWidget(heading)
                controls = []
                for tile in line_tiles:
                    families = tuple(f for f in tile.families if title in f.title.split(" · ")[0].split(" / ")) or tile.families
                    control = TileButton(tile, expanded=True, families=families, group_name=title)
                    control.setMinimumHeight(88)
                    control.clicked.connect(lambda checked=False, eid=tile.equipment_id: self.equipment_selected.emit(eid))
                    self.tile_buttons.setdefault(tile.equipment_id, control)
                    self.tile_occurrences.append(control)
                    controls.append(control)
                body.addWidget(TileGrid(controls, expanded=True, target_width=160))
            self.group_cards.append(card)
        grid.set_widgets(self.group_cards)
        self.body.addWidget(grid)
        if not tiles:
            message = label("선택한 관측일에 조건에 맞는 설비가 없습니다.", 13, color=SECONDARY, wrap=True)
            self.body.addWidget(message)
