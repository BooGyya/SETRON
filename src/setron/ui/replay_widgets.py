"""VER5 Replay의 데이터 꺾은선과 설비군→Line→설비 묶음. 기존 타일 부품을 재사용한다."""

from dataclasses import replace
from functools import partial

from PySide6.QtCharts import QBarCategoryAxis, QLineSeries, QScatterSeries, QValueAxis
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from setron.ui.appearance import ui_color
from setron.ui.components import button, label
from setron.ui.dashboard_charts import DashboardChart, series_color, trend_tooltip
from setron.ui.equipment import FlowGrid
from setron.ui.heatmap import HeatmapGroupCard, HeatmapLegend, TileButton
from setron.ui.heatmap_data import GoalState
from setron.ui.theme import INK, LINE, PRIMARY, SECONDARY, font, set_widget_font
from setron.ui.view_controls import SegmentedControl

# 그래프 시리즈 이름. 범례는 숨겨져 있고, 테스트·접근성에서 선의 역할을 구분하는 데 쓴다.
CURRENT_MARKER = "현재 시점"
FUTURE_SUFFIX = " · 이후"
CURRENT_SUFFIX = " · 현재"
POINT_SUFFIX = " · 시점"  # 실제 시점마다 찍는 점. 누르기·마우스 올리기는 이 점에서 받는다.


class ReplayPlaybackControls(QFrame):
    """보기와 스크롤 위치에 관계없이 공유하는 재생 도구 모음."""

    speed_changed = Signal(float)
    SPEEDS = (0.5, 1, 2, 4)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("replayPlaybackControls")
        self.setAccessibleName("리플레이 재생 도구")
        self.setStyleSheet(f"QFrame#replayPlaybackControls {{background:white;border:1px solid {LINE};border-radius:14px;}}")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(8)
        style = (f"QPushButton {{background:white;color:{INK};border:1px solid {LINE};border-radius:8px;"
                 "padding:0;min-height:38px;max-height:38px;}"
                 f"QPushButton:hover, QPushButton:focus {{background:#e8ebfc;color:{PRIMARY};border-color:{PRIMARY};}}"
                 f"QPushButton:pressed {{background:{PRIMARY};color:white;border-color:{PRIMARY};}}"
                 "QPushButton:disabled {background:#f9fafb;color:#a9b1bf;border-color:#e4e7ed;}")
        self.play_button, self.pause_button, self.restart_button = button("▶"), button("‖"), button("↺")
        for control, name in ((self.play_button, "재생"), (self.pause_button, "일시정지"), (self.restart_button, "처음부터")):
            control.setAccessibleName(name)
            control.setToolTip(name)
            set_widget_font(control, font(16, medium=True))
            control.setStyleSheet(style)
            control.setFixedSize(44, 40)
            row.addWidget(control)
        divider = QFrame()
        divider.setFixedSize(1, 28)
        divider.setStyleSheet(f"background:{LINE};border:0;")
        row.addWidget(divider)
        self.speed_tabs = SegmentedControl()
        self.speed_tabs.setAccessibleName("재생 속도")
        for speed in self.SPEEDS:
            self.speed_tabs.addTab(f"{speed:g}×")
        self.speed_tabs.setCurrentIndex(1)
        self.speed_tabs.currentChanged.connect(lambda index: self.speed_changed.emit(self.SPEEDS[index]))
        row.addWidget(self.speed_tabs)


class ReplayChart(DashboardChart):
    date_selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(300)
        self.setMaximumHeight(300)
        self.setAccessibleName("설비군별 목표 적용 추이 · 저장된 관측일의 달성률")
        self.values, self.days = {}, ()
        self.current = None

    def clear(self):
        super().clear()
        self.values, self.days = {}, ()
        self.current = None
        self.setAccessibleDescription("")

    def _hover(self, chart, series, label, days, summaries, points, position, hovered):
        text = ""
        if hovered:
            index, _ = min(points, key=lambda point: abs(point[0] - position.x()))
            text = trend_tooltip(days[index], label, summaries[index])
        self._hover_tooltip(chart, series, text)

    def show_data(self, data, visible_groups, current=None, frames=None):
        """설비군별 꺾은선 (계단형은 11-10 요청으로 되돌림).

        current(재생 중인 날짜 번호)까지는 진한 실선, 이후는 흐린 점선, 현재 날짜에는 세로선과 큰 점을 둔다.
        실제 시점마다 점을 찍고, 점을 누르면 그 날짜로 가고 마우스를 올리면 대시보드와 같은 집계를 보인다.
        frames를 주지 않으면 실제 저장 날짜(data.frames)를 쓴다. current가 None이면 마지막 시점을 현재로 본다.
        """
        frames = data.frames if frames is None else frames
        chart = self._chart()
        self.values, self.days = {}, ()
        self.setAccessibleDescription("")
        if not frames:
            return
        current = len(frames) - 1 if current is None else max(0, min(current, len(frames) - 1))
        self.current = current
        days = self.days = tuple(frame.day for frame in frames)
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
            values, summaries, segments, segment = [], [], [], []
            for i, frame in enumerate(frames):
                cell = next((g for g in frame.snapshot.groups if g.group_keys == (group.key,)), None)
                rate = cell.summary.rate if cell is not None else None
                values.append(rate)
                summaries.append(cell.summary if cell is not None else None)
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

            def connect(series, points):
                series.clicked.connect(lambda point: self.date_selected.emit(round(point.x())))
                series.hovered.connect(partial(self._hover, chart, series, group.label, days, summaries, points))

            for points in segments:
                past = [p for p in points if p[0] <= current]
                future = [p for p in points if p[0] >= current]
                # 현재 날짜 점은 실선 쪽에 이미 있으므로 이후 쪽이 그 점 하나뿐이면 그리지 않는다.
                if len(future) == 1 and future[0][0] == current:
                    future = []
                for part, name, pen_color, width, style, size in (
                        (past, group.label, color, 2.5, Qt.PenStyle.SolidLine, 7),
                        (future, f"{group.label}{FUTURE_SUFFIX}", faded, 1.5, Qt.PenStyle.DashLine, 6)):
                    if not part:
                        continue
                    if len(part) > 1:
                        line = QLineSeries()
                        line.setName(name)
                        line.setPen(QPen(pen_color, width, style))
                        for px, py in part:
                            line.append(px, py)
                        add(line)
                        connect(line, part)
                    dots = QScatterSeries()
                    dots.setName(name + POINT_SUFFIX)
                    dots.setColor(pen_color)
                    dots.setBorderColor(pen_color)
                    dots.setMarkerSize(size)
                    for px, py in part:
                        dots.append(px, py)
                    add(dots)
                    connect(dots, part)
            # 현재 날짜 점을 크게 표시한다. 판단 불가인 날은 점을 찍지 않는다(0%로 그리지 않음).
            if values[current] is not None:
                dot = QScatterSeries()
                dot.setName(f"{group.label}{CURRENT_SUFFIX}")
                dot.setColor(color)
                dot.setBorderColor(ui_color("white"))
                dot.setMarkerSize(12)
                dot.append(current, values[current] * 100)
                add(dot)
                connect(dot, [(current, values[current] * 100)])
        self.setAccessibleDescription(f"현재 {days[current]} · " + " · ".join(
            f"{group}: " + ", ".join(f"{day} {'확인 필요' if value is None else f'{value:.1%}'}"
                                     for day, value in zip(days, values))
            for group, values in self.values.items()))


class ReplayHeatmap(QWidget):
    equipment_selected = Signal(int, object)

    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        self.legend = HeatmapLegend()
        root.addWidget(self.legend)
        self.body = QVBoxLayout()
        root.addLayout(self.body)
        self.tile_buttons, self.tile_occurrences, self.group_cards = {}, [], []
        self.selected_tile = None
        self.visible_ids = ()
        self._titles, self._cards, self._grid = None, [], None  # 날짜를 넘길 때 다시 쓰는 카드 (QA R-04)
        self.rebuilt_tiles = 0  # 마지막 show_frame에서 새로 만든 타일 수 (재사용 확인용)
        self.restyled_tiles = 0  # 위젯은 그대로 두고 색·글자만 바꾼 타일 수

    def clear(self):
        self.select_tile(None)
        self.tile_buttons, self.tile_occurrences, self.group_cards = {}, [], []
        self.visible_ids = ()
        self._titles, self._cards, self._grid = None, [], None
        while self.body.count():
            widget = self.body.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()

    def _tile(self, tile, families):
        control = TileButton(tile, expanded=True, families=families)
        control.clicked.connect(lambda: self.equipment_selected.emit(control.data.equipment_id, control))
        # 타일에 보이는 내용만 비교한다. 관측일·위험 근거는 상세에서만 쓰므로 바뀌어도 다시 그리지 않는다.
        control.visual = (replace(tile, observation_date=None, findings=()), families)
        self.rebuilt_tiles += 1
        return control

    def select_tile(self, tile):
        if self.selected_tile is not None and self.selected_tile is not tile:
            self.selected_tile.setChecked(False)
        self.selected_tile = tile
        if tile is not None:
            tile.setChecked(True)

    def _card(self, title, lines):
        """설비군 카드 하나. 돌려주는 slots는 카드 안 타일 자리(TileGrid, 번호) 목록이다."""
        card = HeatmapGroupCard(title, sum(len(items) for _, items in lines))
        slots = []
        for (site, line), items in lines:
            tile_grid = card.add_area(site, line, [self._tile(tile, families) for tile, families in items], expanded=True)
            slots.extend((tile_grid, index) for index in range(len(items)))
        return card, slots

    def _update_tiles(self, slots, lines):
        """같은 자리 타일은 위젯을 다시 쓰고 색·버전·툴팁만 바꾼다."""
        entries = [(tile, families) for _, items in lines for tile, families in items]
        for (grid, index), (tile, families) in zip(slots, entries):
            old = grid.tiles[index]
            visual = (replace(tile, observation_date=None, findings=()), families)
            if old.data.code == tile.code:
                old.set_data(tile)
                if old.visual != visual:
                    old.update_appearance(families)
                    old.visual = visual
                    self.restyled_tiles += 1
            else:
                control = self._tile(tile, families)
                grid.grid.replaceWidget(old, control)
                grid.tiles[index] = control
                old.hide()
                old.deleteLater()

    @staticmethod
    def _card_key(title, lines):
        return title, tuple((key, tuple(tile.equipment_id for tile, _ in items)) for key, items in lines)

    def _plan(self, data, frame, selected=None):
        """[(설비군 제목, [((Site, Line), [(타일, 보일 계열)])])]

        selected: 설비군 칩에서 고른 설비군 키 (요청 2-2). None이면 전체이고, 이때만 SW 미등록·HPA 카드도 보인다.
        """
        tiles = frame.tiles
        groups, covered = [], set()
        for group in data.source.catalog.groups:
            cell = next((g for g in frame.snapshot.groups if g.group_keys == (group.key,)), None)
            ids = set(cell.equipment_ids) if cell is not None else set()
            members = tuple(t for t in tiles if t.equipment_id in ids and t.goal is not GoalState.EXCLUDED)
            if members:
                covered.update(t.equipment_id for t in members)
                if selected is None or group.key in selected:
                    groups.append((group.label, members))
        unknown = tuple(t for t in tiles if t.equipment_id not in covered and t.goal is not GoalState.EXCLUDED)
        if unknown and selected is None:
            groups.append(("SW 미등록", unknown))
        hpa = tuple(t for t in tiles if t.goal is GoalState.EXCLUDED)
        if hpa and selected is None:
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

    def show_frame(self, data, frame, selected=None):
        """날짜를 넘길 때 설비군 구성이 같으면 카드를 다시 쓰고, 설비가 바뀐 카드만 새로 만든다 (QA R-04).

        selected: 설비군 칩에서 고른 설비군 키. None이면 전체.
        """
        self.select_tile(None)
        self.rebuilt_tiles = self.restyled_tiles = 0
        plan = self._plan(data, frame, selected)
        tiles = tuple({tile.equipment_id: tile for _, lines in plan for _, items in lines for tile, _ in items}.values())
        empty = ("선택한 설비군이 없습니다. 위 설비군 버튼에서 하나 이상 골라 주세요." if selected is not None and not selected
                 else "선택한 관측일에 조건에 맞는 설비가 없습니다.")
        titles = (bool(tiles), tuple(title for title, _ in plan), empty)
        if self._grid is not None and titles == self._titles:
            for card, (title, lines) in zip(self._cards, plan):
                key = self._card_key(title, lines)
                if key == card["key"]:
                    self._update_tiles(card["slots"], lines)
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
            self._grid = FlowGrid(340, spacing=16, max_columns=3)
            for title, lines in plan:
                widget, slots = self._card(title, lines)
                self._cards.append(dict(key=self._card_key(title, lines), widget=widget, slots=slots))
            self._grid.set_widgets([card["widget"] for card in self._cards])
            self.body.addWidget(self._grid)
            self._titles = titles
            if not tiles:
                self.body.addWidget(label(empty, 13, color=SECONDARY, wrap=True))
        # 물리 설비 ID (복수 SW 설비는 여러 카드에 보여도 한 번만). 원래 프레임 타일 순서를 유지한다.
        shown = {tile.equipment_id for tile in tiles}
        self.visible_ids = tuple(t.equipment_id for t in frame.tiles if t.equipment_id in shown)
        self.group_cards = [card["widget"] for card in self._cards]
        self.tile_buttons, self.tile_occurrences = {}, []
        for card in self._cards:
            for grid, index in card["slots"]:
                control = grid.tiles[index]
                self.tile_buttons.setdefault(control.data.equipment_id, control)
                self.tile_occurrences.append(control)
