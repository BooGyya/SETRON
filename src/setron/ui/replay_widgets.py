"""VER5 Replay의 데이터 꺾은선과 설비군→Line→설비 묶음. 기존 타일 부품을 재사용한다."""

from dataclasses import replace

from PySide6.QtCharts import QBarCategoryAxis, QLineSeries, QScatterSeries, QValueAxis
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from setron.ui.appearance import ui_color
from setron.ui.components import label
from setron.ui.dashboard_charts import DashboardChart, series_color
from setron.ui.equipment import ignore_width
from setron.features.risk.schemas import RiskStatus
from setron.ui.heatmap import GroupCards, TileButton, TileGrid, _version_text, tile_detail_text, tile_style
from setron.ui.heatmap_data import GoalState
from setron.ui.theme import LINE, SECONDARY

# 그래프 시리즈 이름. 범례는 숨겨져 있고, 테스트·접근성에서 선의 역할을 구분하는 데 쓴다.
CURRENT_MARKER = "현재 시점"
FUTURE_SUFFIX = " · 이후"
CURRENT_SUFFIX = " · 현재"


class ReplayChart(DashboardChart):
    date_selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(300)
        self.setMaximumHeight(300)
        self.setAccessibleName("설비군별 목표 적용 추이 · 저장된 관측일의 달성률")
        self.values = {}
        self.current = None

    def clear(self):
        super().clear()
        self.values = {}
        self.current = None
        self.setAccessibleDescription("")

    def show_data(self, data, visible_groups, current=None):
        """current(재생 중인 날짜 번호)까지는 진한 실선, 이후는 흐린 점선으로 그리고 현재 날짜에 세로선을 둔다.

        재생할 때마다 실선이 오른쪽으로 자라고 세로선이 움직인다. 이후 날짜의 점도 남겨 두어 눌러서 이동할 수 있다.
        current가 None이면 마지막 날짜를 현재로 본다.
        """
        chart = self._chart()
        self.values = {}
        self.setAccessibleDescription("")
        if not data.frames:
            return
        current = len(data.frames) - 1 if current is None else max(0, min(current, len(data.frames) - 1))
        self.current = current
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

        def add(series):
            chart.addSeries(series)
            series.attachAxis(x)
            series.attachAxis(y)

        # 현재 날짜 세로선. 선보다 먼저 넣어 선 아래에 깔린다.
        marker = QLineSeries()
        marker.setName(CURRENT_MARKER)
        marker.setPen(QPen(ui_color(SECONDARY), 1.5, Qt.PenStyle.DashLine))
        marker.append(current, 0)
        marker.append(current, 100)
        add(marker)
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
            color = ui_color(series_color(group.key))
            faded = QColor(color)
            faded.setAlpha(90)
            for points in segments:
                past = [p for p in points if p[0] <= current]
                future = [p for p in points if p[0] >= current]
                # 현재 날짜 점은 실선 쪽에 이미 있으므로 이후 쪽이 그 점 하나뿐이면 그리지 않는다.
                if len(future) == 1 and future[0][0] == current:
                    future = []
                for part, name, pen_color, width, style in (
                        (past, group.label, color, 2.5, Qt.PenStyle.SolidLine),
                        (future, f"{group.label}{FUTURE_SUFFIX}", faded, 1.5, Qt.PenStyle.DashLine)):
                    if not part:
                        continue
                    series = QLineSeries() if len(part) > 1 else QScatterSeries()
                    series.setName(name)
                    series.setColor(pen_color)
                    if isinstance(series, QLineSeries):
                        series.setPen(QPen(pen_color, width, style))
                        series.setPointsVisible(True)
                    else:
                        series.setMarkerSize(6)
                    for px, py in part:
                        series.append(px, py)
                    add(series)
                    series.clicked.connect(lambda point: self.date_selected.emit(round(point.x())))
            # 현재 날짜 점을 크게 표시한다. 판단 불가인 날은 점을 찍지 않는다(0%로 그리지 않음).
            if values[current] is not None:
                dot = QScatterSeries()
                dot.setName(f"{group.label}{CURRENT_SUFFIX}")
                dot.setColor(color)
                dot.setBorderColor(ui_color("white"))
                dot.setMarkerSize(12)
                dot.append(current, values[current] * 100)
                add(dot)
                dot.clicked.connect(lambda point: self.date_selected.emit(round(point.x())))
        self.setAccessibleDescription(f"현재 {days[current]} · " + " · ".join(
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
        self._titles, self._cards, self._grid = None, [], None  # 날짜를 넘길 때 다시 쓰는 카드 (QA R-04)
        self.rebuilt_tiles = 0  # 마지막 show_frame에서 새로 만든 타일 수 (재사용 확인용)
        self.restyled_tiles = 0  # 위젯은 그대로 두고 색·글자만 바꾼 타일 수

    def clear(self):
        self.tile_buttons, self.tile_occurrences, self.group_cards = {}, [], []
        self.visible_ids = ()
        self._titles, self._cards, self._grid = None, [], None
        while self.body.count():
            widget = self.body.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()

    def _tile(self, tile, families, title):
        control = TileButton(tile, expanded=True, families=families, group_name=title)
        control.setMinimumHeight(88)
        control.clicked.connect(lambda checked=False, eid=tile.equipment_id: self.equipment_selected.emit(eid))
        # 타일에 보이는 내용만 비교한다. 관측일·위험 근거는 상세에서만 쓰므로 바뀌어도 다시 그리지 않는다.
        control.visual = (replace(tile, observation_date=None, findings=()), families, title)
        self.rebuilt_tiles += 1
        return control

    def _card(self, title, lines):
        """설비군 카드 하나. 돌려주는 slots는 카드 안 타일 자리(TileGrid, 번호) 목록이다."""
        card = QFrame()
        card.setObjectName("replayGroup")
        card.setStyleSheet(f"QFrame#replayGroup {{background:white;border:1px solid {LINE};border-radius:8px;}}")
        body = QVBoxLayout(card)
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(12)
        body.addWidget(label(f"{title} / {sum(len(items) for _, items in lines)}대", 18, medium=True))
        slots = []
        for (site, line), items in lines:
            heading = label(f"{site} · {line or '구역 미확인'} / {len(items)}대", 14, medium=True, wrap=True)
            ignore_width(heading)
            body.addWidget(heading)
            tile_grid = TileGrid([self._tile(tile, families, title) for tile, families in items],
                                 expanded=True, target_width=160)
            slots.extend((tile_grid, index) for index in range(len(items)))
            body.addWidget(tile_grid)
        return card, slots

    def _update_tiles(self, slots, title, lines):
        """같은 자리 타일은 위젯을 다시 쓰고 색·글자만 바꾼다. '위험' 표시처럼 모양이 바뀐 타일만 새로 만든다."""
        entries = [(tile, families) for _, items in lines for tile, families in items]
        for (grid, index), (tile, families) in zip(slots, entries):
            old = grid.tiles[index]
            visual = (replace(tile, observation_date=None, findings=()), families, title)
            marker = "위험" if tile.risk is RiskStatus.RISK and tile.goal is not GoalState.EXCLUDED else ""
            if old.visual == visual or (old.marker == marker and old.data.code == tile.code
                                        and old.version_label is not None):
                if old.visual != visual:
                    style = tile_style(tile)
                    if style != old.style_text:
                        old.style_text = style
                        old.setStyleSheet(style)
                    old.version_label.setText(_version_text(tile, families))
                    old.detail_label.setText(tile_detail_text(tile, title))
                    old.visual = visual
                    self.restyled_tiles += 1
                old.data = tile
                old.setToolTip(tile.description)
                old.setAccessibleName(tile.description)
            else:
                control = self._tile(tile, families, title)
                grid.grid.replaceWidget(old, control)
                grid.tiles[index] = control
                old.hide()
                old.deleteLater()

    @staticmethod
    def _card_key(title, lines):
        return title, tuple((key, tuple(tile.equipment_id for tile, _ in items)) for key, items in lines)

    def _plan(self, data, frame):
        """[(설비군 제목, [((Site, Line), [(타일, 보일 계열)])])]"""
        tiles = frame.tiles
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
        site_order = {site.label: i for i, site in enumerate(data.source.catalog.sites)}
        plan = []
        for title, members in groups:
            lines = {}
            for tile in members:
                lines.setdefault((tile.site_label, tile.line_label or tile.area), []).append(tile)
            ordered = []
            for key, line_tiles in sorted(lines.items(), key=lambda item: (
                    site_order.get(item[0][0], len(site_order)), item[0][0], item[0][1] or "")):
                ordered.append((key, [(tile, tuple(f for f in tile.families
                                                   if title in f.title.split(" · ")[0].split(" / ")) or tile.families)
                                      for tile in line_tiles]))
            plan.append((title, ordered))
        return plan

    def show_frame(self, data, frame):
        """날짜를 넘길 때 설비군 구성이 같으면 카드를 다시 쓰고, 설비가 바뀐 카드만 새로 만든다 (QA R-04)."""
        self.rebuilt_tiles = self.restyled_tiles = 0
        tiles = frame.tiles
        plan = self._plan(data, frame)
        titles = (bool(tiles), tuple(title for title, _ in plan))
        if self._grid is not None and titles == self._titles:
            for card, (title, lines) in zip(self._cards, plan):
                key = self._card_key(title, lines)
                if key == card["key"]:
                    self._update_tiles(card["slots"], title, lines)
                    continue
                widget, slots = self._card(title, lines)
                index = self._grid.items.index(card["widget"])
                self._grid.grid.replaceWidget(card["widget"], widget)
                self._grid.items[index] = widget
                card["widget"].hide()
                card["widget"].deleteLater()
                widget.show()
                card.update(key=key, widget=widget, slots=slots)
        else:
            self.clear()
            self._grid = GroupCards(340, spacing=12, max_columns=3)
            for title, lines in plan:
                widget, slots = self._card(title, lines)
                self._cards.append(dict(key=self._card_key(title, lines), widget=widget, slots=slots))
            self._grid.set_widgets([card["widget"] for card in self._cards])
            self.body.addWidget(self._grid)
            self._titles = titles
            if not tiles:
                message = label("선택한 관측일에 조건에 맞는 설비가 없습니다.", 13, color=SECONDARY, wrap=True)
                self.body.addWidget(message)
        self.visible_ids = tuple(t.equipment_id for t in tiles)
        self.group_cards = [card["widget"] for card in self._cards]
        self.tile_buttons, self.tile_occurrences = {}, []
        for card in self._cards:
            for grid, index in card["slots"]:
                control = grid.tiles[index]
                self.tile_buttons.setdefault(control.data.equipment_id, control)
                self.tile_occurrences.append(control)
