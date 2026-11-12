"""작업 단위 5: 물리 설비 타일·버전 함께 보기. VER4 히트맵(상세보기·간략히 보기, 카드형·목록형) 배치."""

from collections import OrderedDict
from dataclasses import replace

from PySide6.QtCore import QPoint, QSignalBlocker, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLayout, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from setron.features.risk.schemas import RiskStatus
from setron.ui.appearance import readable_color, set_color_style, surface_edge
from setron.ui.appearance_data import HEATMAP_FIELDS, HEATMAP_STANDARD, HEATMAP_TIERS, Appearance
from setron.ui.components import Card, StatusBadge, button, filter_checkbox, label
from setron.ui.dashboard import DashboardGrid, DashboardKpi
from setron.ui.dashboard_data import RiskRuleState
from setron.ui.equipment import ignore_width
from setron.features.version.groups import Tier
from setron.ui.heatmap_data import EquipmentSelection, GoalState, equipment_tiles
from setron.ui.heatmap_grouping import GROUPING_MODES, build_heatmap_groups, group_families, tiles_for_grouping
from setron.ui.heatmap_list import HeatmapListModel, HeatmapTreeView
from setron.ui.theme import ACCENT_SOFT, INK, LINE, PRIMARY, SECONDARY, font, icon, set_widget_font, svg_size
from setron.ui.view_controls import SegmentedControl


# 타일 채움색은 5단계: 진초록 N+1 이상 · 연초록 N · 연분홍 N-1 · 진분홍 N-2 이하 · 회색 비교 불가·HPA.
# 위험 후보는 빨강(N-2 이하)과 헷갈리지 않게 채움색 대신 굵은 빨간 테두리로 보인다 (범례 설명과 같은 규칙).
RISK_LINE = HEATMAP_STANDARD[False][-1]
RISK_FILL = "#fde2e7"
TIER_FILL = dict(zip(HEATMAP_TIERS, HEATMAP_STANDARD[False]))
HEATMAP_CELL_SIZE = QSize(140, 80)
HEATMAP_COMPACT_CELL_SIZE = QSize(140, 40)
DEFERRED_TILE_THRESHOLD = 200
CARD_CACHE_LIMIT = 3
CARD_CACHE_TILE_LIMIT = 6000


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


def heatmap_cell_colors(colors, key, risk=False):
    background = colors[key]
    ink = readable_color(colors["ink"], background)
    border = (readable_color(colors["heat_risk"], background, 3) if risk
              else surface_edge(background, colors["surface"], background))
    return background, ink, border


def tile_style(data, *, minimum_height=0, corner_radius=4, colors=None):
    """비교 단계는 채움색, 위험 후보는 테두리로 표시한다."""
    colors = Appearance().colors() if colors is None else colors
    risk = data.risk is RiskStatus.RISK and data.goal is not GoalState.EXCLUDED
    tier = Tier.UNKNOWN if data.goal is GoalState.EXCLUDED else data.tier
    background, ink, border = heatmap_cell_colors(colors, HEATMAP_TIERS[tier][0], risk)
    focus = readable_color(colors["accent"], background, 3)
    return (f"QPushButton#equipmentTile {{background:{background};border:{2 if risk else 1}px solid {border};"
            f"border-radius:{corner_radius}px;padding:0;min-height:{minimum_height}px;}}"
            f"QPushButton#equipmentTile QLabel {{color:{ink};}}"
            f"QPushButton#equipmentTile:hover {{border-color:{border if risk else focus};}}"
            f"QPushButton#equipmentTile:focus, QPushButton#equipmentTile:checked "
            f"{{outline:none;border:{3 if risk else 2}px solid {border if risk else focus};}}")


class TileButton(QPushButton):
    def __init__(self, data, *, expanded=False, families=None, cell_size=None, defer_content=False):
        super().__init__()
        self.cell_size = QSize(cell_size) if cell_size is not None else None
        families = data.families if families is None else families
        self._expanded = expanded
        self._content_ready = False
        self._families = families
        self.setObjectName("equipmentTile")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.set_data(data)
        self.setAccessibleDescription("Space 또는 클릭으로 설비 선택")
        self.code_label = self.version_label = None
        self.visual = (replace(data, observation_date=None, findings=()), families)
        if self.cell_size is not None:
            self.setFixedSize(self.cell_size)
        if not defer_content or self.cell_size is None:
            self._build_content()

    def _build_content(self):
        if self._content_ready:
            return
        self._content_ready = True
        expanded = self._expanded
        body = QVBoxLayout(self)
        padding_y = 10 if expanded else 6 if self.cell_size is not None else 8
        body.setContentsMargins(10, padding_y, 10, padding_y)
        body.setSpacing(4)
        self.code_label = label(self.data.code, 16, medium=True)
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addStretch()
        body.addWidget(self.code_label)
        if expanded:
            self.version_label = label("", 14, wrap=True)
            ignore_width(self.version_label)
            self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            body.addWidget(self.version_label)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        body.addStretch()
        self.update_appearance(self._families)
        body.activate()
        for child in self.findChildren(QWidget):
            child.show()

    def paintEvent(self, event):
        # Qt는 조상 스크롤 영역에 가려진 버튼을 그리지 않는다. 실제 버튼은 유지해
        # 접근성·키보드·상세 패널의 초점 복귀를 보존하고 무거운 본문만 늦게 만든다.
        self._build_content()
        super().paintEvent(event)

    def set_data(self, data):
        self.data = data
        description = data.description
        self.setAccessibleName(description)
        self.setToolTip(description)

    def update_appearance(self, families):
        self._families = families
        self.visual = (replace(self.data, observation_date=None, findings=()), families)
        if not self._content_ready:
            return
        text = _version_text(self.data, families, show_tier=False) if self.version_label is not None else ""
        if self.cell_size is not None:
            width = self.cell_size.width() - 26
            self.code_label.setText(self.code_label.fontMetrics().elidedText(self.data.code, Qt.TextElideMode.ElideRight, width))
            if self.version_label is not None:
                self.version_label.setWordWrap(False)
                text = self.version_label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width)
        if self.version_label is not None:
            self.version_label.setText(text)
        # Qt가 QSS의 최소 높이를 레이아웃 힌트보다 우선하므로 타일 본문을 기준으로 지정한다.
        height = 0 if self.cell_size is not None else self.layout().minimumSize().height()
        data = self.data
        radius = 0 if self.cell_size is not None else 4
        set_color_style(self, lambda colors: tile_style(data, minimum_height=height, corner_radius=radius, colors=colors))
        if self.cell_size is not None:
            self.setFixedSize(self.cell_size)

    def minimumSizeHint(self):
        if self.cell_size is not None:
            return self.cell_size
        return QSize(0, self.layout().minimumSize().height())

    def sizeHint(self):
        if self.cell_size is not None:
            return self.cell_size
        return self.layout().sizeHint()

    def hasHeightForWidth(self):
        return self.cell_size is None and self.layout().hasHeightForWidth()

    def heightForWidth(self, width):
        if self.cell_size is not None:
            return self.cell_size.height()
        height = self.layout().totalHeightForWidth(width)
        return height if height >= 0 else self.sizeHint().height()


class TileGrid(QWidget):
    """캔버스 폭에 맞춰 재배치하며 타일은 페이지의 세로 스크롤을 공유한다."""

    def __init__(self, tiles, *, expanded, target_width=None, max_columns=12, centered=False, spacing=None):
        super().__init__()
        self.tiles = tiles
        self.fixed_cells = bool(tiles) and all(getattr(tile, "cell_size", None) is not None for tile in tiles)
        self.max_columns = max_columns
        self.columns = 0
        self.target_width = target_width or (240 if expanded else max(72, *(tile.code_label.sizeHint().width() + 14 for tile in tiles)))
        self._fixed_column = self.fixed_cells and max_columns == 1 and spacing == 0
        self._center_host = QWidget() if centered and self.fixed_cells and not self._fixed_column else None
        if self._fixed_column:
            self.columns = 1
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            for index, tile in enumerate(tiles):
                tile.setParent(self)
                tile.move(0, index * tile.cell_size.height())
                tile.show()
            self.setFixedSize(tiles[0].cell_size.width(), len(tiles) * tiles[0].cell_size.height())
            return
        self.grid = QGridLayout(self._center_host if self._center_host is not None else self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8 if spacing is None else spacing)
        self.grid.setVerticalSpacing((8 if target_width else 14) if spacing is None else spacing)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        if self._center_host is not None:
            row = QHBoxLayout(self)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
            row.addStretch()
            row.addWidget(self._center_host, 0, Qt.AlignmentFlag.AlignTop)
            row.addStretch()
        # 세로는 내용 높이만 쓴다. 창이 내용보다 크면(4K 등) 남는 높이로 타일이 늘어났다. 같은 행 타일 높이는 맞춘다.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._arrange(1)

    def minimumSizeHint(self):
        if self._fixed_column:
            return self.size()
        return QSize(0, self.grid.minimumSize().height())

    def _arrange(self, columns):
        if columns == self.columns:
            return
        for tile in self.tiles:
            self.grid.removeWidget(tile)
        for index in range(max(columns, self.columns) + 1):
            stretch = (0 if self._center_host is not None else
                       int(index == columns if self.fixed_cells else index < columns))
            self.grid.setColumnStretch(index, stretch)
        for index, tile in enumerate(self.tiles):
            self.grid.addWidget(tile, index // columns, index % columns,
                                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop if self.fixed_cells else Qt.AlignmentFlag(0))
        self.columns = columns
        if self._center_host is not None:
            width = columns * self.tiles[0].cell_size.width() + (columns - 1) * self.grid.horizontalSpacing()
            self._center_host.setFixedWidth(width)
        self.setMinimumHeight(self.grid.minimumSize().height())
        self.updateGeometry()

    def resizeEvent(self, event):
        if self._fixed_column:
            super().resizeEvent(event)
            return
        limit = min(self.max_columns, len(self.tiles)) if self._center_host is not None else self.max_columns
        gap = self.grid.horizontalSpacing()
        self._arrange(max(1, min(limit, (event.size().width() + gap) // (self.target_width + gap))))
        super().resizeEvent(event)


class HeatmapColumnHeading(QFrame):
    """헤더 띠는 유지하고 글자만 현재 보이는 그룹 영역 중앙에 둔다."""

    def __init__(self, text):
        super().__init__()
        self.setObjectName("heatmapColumnHeading")
        self.setToolTip(text)
        self.caption = label(text, 14, medium=True, wrap=True)
        self.caption.setParent(self)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._visible_range = None

    def set_visible_range(self, left, right):
        self._visible_range = left, right
        self._position_caption()

    def _position_caption(self):
        left, right = self._visible_range if self._visible_range is not None else (0, self.width())
        left = max(0, min(self.width(), left))
        right = max(left, min(self.width(), right))
        self.caption.setGeometry(left, 4, right - left, max(0, self.height() - 8))
        self.caption.setVisible(right > left)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_caption()


class HeatmapGroupScroll(QScrollArea):
    """최상위 제목 아래의 열만 가로로 이동하고 설비 높이는 모두 표시한다."""

    def __init__(self, content, title):
        super().__init__()
        self.setObjectName("heatmapGroupScroll")
        self.setAccessibleName(f"{title} 하위 분류와 설비 · 가로 스크롤")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self._headers = content.findChildren(HeatmapColumnHeading)
        self.setWidget(content)
        if content.layout() is not None:
            content.layout().activate()
        self._sync_height()
        self._sync_header_positions()

    def minimumSizeHint(self):
        return QSize(0, self.height())

    def sizeHint(self):
        return QSize(0, self.height())

    def _sync_height(self):
        content = self.widget()
        if content is None:
            return
        frame = 2 * self.frameWidth()
        overflow = content.width() > max(0, self.width() - frame)
        height = content.height() + frame
        if overflow:
            height += self.horizontalScrollBar().sizeHint().height()
        if self.height() != height:
            self.setFixedHeight(height)
            self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_height()
        self._sync_header_positions()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._sync_header_positions()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_header_positions()
        QTimer.singleShot(0, self, self._sync_header_positions)

    def _sync_header_positions(self):
        viewport = self.viewport()
        for heading in getattr(self, "_headers", ()):
            origin = heading.mapTo(viewport, QPoint(0, 0))
            heading.set_visible_range(max(0, -origin.x()),
                                      min(heading.width(), viewport.width() - origin.x()))


class HeatmapLegend(QWidget):
    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(20)
        self.items = []
        for key, text in HEATMAP_FIELDS:
            if key == "heat_risk":
                text = "위험 후보"
            item = QWidget()
            item.setAccessibleName("범례 " + text)
            body = QHBoxLayout(item)
            body.setContentsMargins(0, 0, 0, 0)
            body.setSpacing(6)
            dot = QFrame()
            dot.setFixedSize(10, 10)
            if key == "heat_risk":
                set_color_style(dot, lambda colors: (
                    f"background:{colors['surface']};border:2px solid "
                    f"{readable_color(colors['heat_risk'], colors['surface'], 3)};border-radius:3px;"))
            else:
                set_color_style(dot, lambda colors, k=key: (
                    f"background:{colors[k]};border-radius:5px;"
                    f"border:1px solid {surface_edge(colors[k], colors['surface'], colors[k])};"))
            body.addWidget(dot)
            body.addWidget(label(text, 12, color=SECONDARY))
            row.addWidget(item)
            self.items.append(item)
        row.addStretch()


class HeatmapGroupCard(QFrame):
    def __init__(self, title, count, *, show_count=True, center_title=False):
        super().__init__()
        self.setObjectName("heatmapGroup")
        self.setStyleSheet(f"QFrame#heatmapGroup {{background:white;border:1px solid {LINE};border-radius:8px;}}")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(12, 12, 12, 12)
        self.body.setSpacing(8)
        heading = label(f"{title} / {count}대" if show_count else title, 18, medium=True, wrap=True)
        if center_title:
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(heading)
        self.body.addStretch()

    def add_area(self, site, area, tiles, *, expanded):
        heading = label(f"{site} · {area or '구역 미확인'} / {len(tiles)}대", 14, medium=True, wrap=True)
        ignore_width(heading)
        self.body.insertWidget(self.body.count() - 1, heading)
        grid = TileGrid(tiles, expanded=expanded, target_width=140)
        self.body.insertWidget(self.body.count() - 1, grid)
        return grid


class HeatmapPage(QWidget):
    equipment_selected = Signal(object)

    def __init__(self, state, source, *, detail_connected=False, show_summary=True, show_grouping=True):
        super().__init__()
        self.state, self.source, self.snapshot = state, source, None
        self.detail_connected = detail_connected
        self.selection_hint = ("타일 선택 → 설비 상세 · 이전 조회로 돌아오면 보기와 선택을 유지합니다." if detail_connected else
                               "타일 선택 시 설비 ID를 전달합니다. 설비 목록·상세는 6번 작업에서 연결합니다.")
        self.tiles, self.tile_buttons, self.tile_grids, self.tile_occurrences = (), {}, [], []
        self.visible_ids = ()
        self.selected_id = None
        self.grouping_mode = "site"
        self._show_grouping = show_grouping
        self._query_bound = True
        self._display_groups = ()
        self._selected_groups = None
        self._card_entries = {}
        self._card_cache = OrderedDict()
        self._defer_tile_content = False
        self.group_cards = []
        self.rebuilt_tiles = self.restyled_tiles = 0
        self._control_rows = None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
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
            "analysis": DashboardKpi("분석 설비", "32ac4.svg", accent=True, show_caption=False, rate_key="accent"),
            "met": DashboardKpi("목표 달성", "bbc12.svg", show_caption=False),
            "below": DashboardKpi("목표 미달", "e53b6.svg", show_caption=False),
            "unknown": DashboardKpi("확인 필요", "60dbd.svg", show_caption=False),
        }
        self.summary = DashboardGrid(tuple(self.metrics.values()), minimum_width=150, spacing=12)
        body.addWidget(self.summary)
        self.summary.setVisible(show_summary)

        self.content = Card(padding=20)
        self.content.body.setSpacing(12)
        self.content.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        toolbar_height = 38
        self.search = QLineEdit()
        self.search.setObjectName("heatmapSearch")
        self.search.setPlaceholderText("설비 검색")
        self.search.setAccessibleName("설비 검색 · 설비명·위치·버전 원문")
        self.search.addAction(icon("b2c35.svg"), QLineEdit.ActionPosition.LeadingPosition)
        set_widget_font(self.search, font(14))
        self.search.setFixedHeight(toolbar_height)
        self.search.setMinimumWidth(220)
        self.search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search.setStyleSheet(f"QLineEdit#heatmapSearch {{background:white;border:1px solid {LINE};border-radius:10px;"
                                  "padding:0 10px;}"
                                  f"QLineEdit#heatmapSearch:focus {{border:2px solid {PRIMARY};}}")
        self.search.textChanged.connect(self._render_tiles)
        self.view_toggle = button("간략히 보기")
        self.view_toggle.setObjectName("heatmapView")
        self.view_toggle.setMinimumWidth(104)
        self.hpa_button = button("HPA 표시")
        display_border = 1
        display_content_height = toolbar_height - 2 * display_border
        display_style = (
            f"QPushButton {{background:white;border:{display_border}px solid {LINE};border-radius:10px;"
            f"padding:0 10px;min-height:{display_content_height}px;max-height:{display_content_height}px;}}"
            f"QPushButton:checked {{background:{PRIMARY};border-color:{PRIMARY};color:white;}}"
            f"QPushButton:focus {{border-color:{PRIMARY};}}"
            f"QPushButton:checked:focus {{border-color:{INK};}}")
        for control in (self.hpa_button, self.view_toggle):
            control.setCheckable(True)
            control.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            control.setStyleSheet(display_style)
            control.setFixedHeight(toolbar_height)
        self.view_toggle.setChecked(True)
        self.view_toggle.toggled.connect(self._change_view)
        self.hpa_button.setToolTip("분석에서 제외한 HPA 설비를 별도 묶음으로 함께 표시합니다.")
        self.hpa_button.hide()
        self.hpa_button.toggled.connect(self._render_tiles)

        self.filter_bar = QWidget()
        self.filter_bar.setFixedHeight(toolbar_height)
        self.filter_bar.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        filter_row = QHBoxLayout(self.filter_bar)
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(16)
        self.risk_button = filter_checkbox("위험 후보")
        self.below_filter = filter_checkbox("목표 미달")
        for checkbox in (self.risk_button, self.below_filter):
            checkbox.setEnabled(False)
            checkbox.toggled.connect(self._render_tiles)
            filter_row.addWidget(checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
        segmented = QFrame()
        segmented.setObjectName("layoutSwitch")
        segmented.setFixedHeight(toolbar_height)
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
            set_widget_font(control, font(13, medium=True))
            control.setStyleSheet(
                "QPushButton#layoutOption {background:transparent;border:1px solid transparent;border-radius:7px;"
                "padding:0 10px;min-height:28px;max-height:28px;}"
                f"QPushButton#layoutOption:checked {{background:{ACCENT_SOFT};color:{PRIMARY};}}"
                f"QPushButton#layoutOption:focus {{border-color:{PRIMARY};}}")
            control.setFixedHeight(toolbar_height - 8)
            self.layout_buttons.addButton(control, index)
            segment_row.addWidget(control)
        self.layout_buttons.idClicked.connect(self._render_tiles)
        self.display_bar = QWidget()
        self.display_bar.setFixedHeight(toolbar_height)
        self.display_bar.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        display_row = QHBoxLayout(self.display_bar)
        display_row.setContentsMargins(0, 0, 0, 0)
        display_row.setSpacing(8)
        display_row.addWidget(self.hpa_button, 0, Qt.AlignmentFlag.AlignVCenter)
        display_row.addWidget(self.view_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        display_row.addWidget(segmented, 0, Qt.AlignmentFlag.AlignVCenter)
        controls_host = QWidget()
        self.controls_host = controls_host
        ignore_width(controls_host)
        self.controls = QGridLayout(controls_host)
        self.controls.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.controls.setContentsMargins(0, 0, 0, 0)
        self.controls.setHorizontalSpacing(10)
        self.controls.setVerticalSpacing(10)
        self.content.body.addWidget(controls_host)

        self.grouping_bar = QWidget()
        grouping_row = QHBoxLayout(self.grouping_bar)
        grouping_row.setContentsMargins(0, 0, 0, 0)
        grouping_row.setSpacing(12)
        self.grouping_tabs = SegmentedControl()
        self.grouping_tabs.setAccessibleName("히트맵 분류 기준")
        for _key, caption in GROUPING_MODES:
            self.grouping_tabs.addTab(caption)
        self.grouping_tabs.setCurrentIndex(next(index for index, (key, _) in enumerate(GROUPING_MODES)
                                               if key == self.grouping_mode))
        self.grouping_tabs.currentChanged.connect(self._change_grouping)
        grouping_row.addWidget(self.grouping_tabs)
        grouping_row.addStretch()
        self.content.body.addWidget(self.grouping_bar)

        self.legend = HeatmapLegend()
        self.content.body.addWidget(self.legend)
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
        self.tile_body.setSpacing(16)
        self.tile_body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.tile_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.content.body.addWidget(self.tile_host)
        self.list_model = HeatmapListModel(("설비명", "Site / 원본 구역", "모델", "버전", "목표 달성", "위험 판정"),
                                         numeric_columns=(3,), centered=True)
        self.list_table = HeatmapTreeView(self.list_model, "히트맵 설비 목록형", (220, 150, 190, 230, 120, 160))
        self._list_style = self.list_table.property("setronOriginalStyle") or self.list_table.styleSheet()
        self._compact_list = None
        self.list_table.activated.connect(self._select_row)
        self.list_table.clicked.connect(self._select_row)
        self.list_table.hide()
        self.content.body.addWidget(self.list_table)
        self.message = label("날짜나 필터를 선택하면 결과가 표시됩니다.", color=SECONDARY, wrap=True)
        ignore_width(self.message)
        self.content.body.addWidget(self.message)
        self.selection_notice = label(self.selection_hint, 12, color=SECONDARY, wrap=True)
        ignore_width(self.selection_notice)
        self.selection_notice.hide()
        self.content.body.addWidget(self.selection_notice)
        body.addWidget(self.content)
        body.addStretch()
        self._reflow()

    def _reflow(self):
        margins = self.content.body.contentsMargins() + self.content.contentsMargins()
        available = self.width() - margins.left() - margins.right()
        segment_width = sum(control.minimumWidth() for control in self.grouping_tabs.buttons)
        segment_width += 2 * self.grouping_tabs.margin + 2 * (self.grouping_tabs.count() - 1)
        columns = len(GROUPING_MODES) if available >= segment_width else 2
        if self.grouping_tabs._columns != columns:
            self.grouping_tabs.set_columns(columns)
        spacing = self.controls.horizontalSpacing()
        filter_width = self.filter_bar.minimumSizeHint().width()
        display_width = self.display_bar.minimumSizeHint().width()
        rows = (1 if available >= 220 + filter_width + display_width + 2 * spacing else
                2 if available >= 220 + filter_width + spacing else 3)
        self.controls.setColumnMinimumWidth(0, 220)
        if self._control_rows == rows:
            return
        for widget in (self.search, self.filter_bar, self.display_bar):
            self.controls.removeWidget(widget)
        if rows == 1:
            self.controls.addWidget(self.search, 0, 0, Qt.AlignmentFlag.AlignVCenter)
            self.controls.addWidget(self.filter_bar, 0, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.controls.addWidget(self.display_bar, 0, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        elif rows == 2:
            self.controls.addWidget(self.search, 0, 0, 1, 2, Qt.AlignmentFlag.AlignVCenter)
            self.controls.addWidget(self.filter_bar, 0, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.controls.addWidget(self.display_bar, 1, 0, 1, 3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        else:
            self.controls.addWidget(self.search, 0, 0, 1, 3, Qt.AlignmentFlag.AlignVCenter)
            self.controls.addWidget(self.filter_bar, 1, 0, 1, 3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.controls.addWidget(self.display_bar, 2, 0, 1, 3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for column in range(3):
            self.controls.setColumnStretch(column, int(column == 0))
        self._control_rows = rows

    def resizeEvent(self, event):
        self._reflow()
        super().resizeEvent(event)

    def _change_view(self, expanded):
        self.view_toggle.setText("간략히 보기" if expanded else "상세보기")
        self._render_tiles()

    def _change_grouping(self, index):
        self.set_grouping(GROUPING_MODES[index][0])

    def set_grouping(self, mode):
        """분류 방식만 바꾼다. 현재 스냅샷에서 다시 배치하며 자료를 조회하지 않는다."""
        if mode not in dict(GROUPING_MODES):
            raise ValueError("지원하지 않는 히트맵 그룹 기준입니다.")
        if mode != self.grouping_mode:
            self.grouping_mode = mode
            with QSignalBlocker(self.grouping_tabs):
                self.grouping_tabs.setCurrentIndex(next(index for index, (key, _) in enumerate(GROUPING_MODES)
                                                        if key == mode))
            self._render_tiles()

    def clear_snapshot(self):
        """검색·보기 설정은 유지하고 전달받은 표시 자료만 비운다."""
        self._clear()

    def _clear(self):
        self._clear_card_cache()
        with QSignalBlocker(self.below_filter):
            self.below_filter.setChecked(False)
        self.below_filter.setEnabled(False)
        self.snapshot, self.tiles, self.selected_id = None, (), None
        self._display_groups, self._selected_groups = (), None
        self.risk_button.setEnabled(False)
        self.risk_button.setChecked(False)
        self.risk_button.setToolTip("")
        self.risk_button.setAccessibleDescription("")
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
    def _risk_filter_disabled_reason(result):
        if result.risk_rule_state in (RiskRuleState.UNREGISTERED, RiskRuleState.ALL_DISABLED):
            return result.risk_rule_state.value
        if not result.summary.equipment_count:
            return "분석 대상 없음"
        unknown = result.risk_unknown
        if unknown == result.summary.equipment_count:
            return f"위험 미확인 {unknown}대"
        if unknown:
            return f"확인된 후보 0대 · 미확인 {unknown}대"
        return "위험 후보 0대"

    def accept_result(self, query, result):
        if (not self.state.is_current(query) or result.query is not query
                or not self.state.catalog.is_view_of(self.source.catalog)):
            return False
        return self.present_snapshot(result, query_bound=True)

    def present_snapshot(self, result, *, source=None, tiles=None, selected_groups=None, query_bound=False):
        """완성된 읽기 스냅샷을 공통 콘텐츠에 표시한다. 외부 재생 자료는 DB를 조회하지 않는다.

        selected_groups는 설비군 표시 범위이며 None이면 미분류·HPA를 포함한 전체다.
        query_bound는 이 페이지의 QueryState에 연결된 일반 조회 결과에서만 사용한다.
        """
        if source is not None:
            self.source = source
        self._query_bound = query_bound
        self._selected_groups = None if selected_groups is None else frozenset(selected_groups)
        self._display_groups = tuple(
            (option.key, option.label, tuple(dict.fromkeys(
                equipment_id for cell in result.groups if option.key in cell.group_keys
                for equipment_id in cell.equipment_ids)))
            for option in self.source.catalog.groups
            if self._selected_groups is None or option.key in self._selected_groups)
        all_tiles = equipment_tiles(result, self.source) if tiles is None else tuple(tiles)
        if self._selected_groups is None:
            self.tiles = all_tiles
        else:
            selected_ids = {equipment_id for _, _, ids in self._display_groups for equipment_id in ids}
            self.tiles = tuple(tile for tile in all_tiles if tile.equipment_id in selected_ids
                               and tile.goal is not GoalState.EXCLUDED)
        self.snapshot, self.selected_id = result, None
        self.selection_notice.setText(self.selection_hint)
        self.result_status.setText(f"{getattr(self.source, 'result_label', '예시 결과')} · "
                                   + ("조회 적용됨" if self.tiles else "조건에 맞는 설비 없음"))
        self.result_status.set_tone("info")
        query = result.query
        self.context.setText(f"적용 #{query.revision} · 일반 분석 기준일 {result.base_date} · {query.description}\n"
                             f"{result.source_label}\nHPA는 조건에 맞는 최신 자료와 기존 전달 범위의 관측이며 실제 관측일을 표시합니다.")
        disabled_reason = "" if result.risky_ids else self._risk_filter_disabled_reason(result)
        self.risk_button.setToolTip(disabled_reason)
        self.risk_button.setAccessibleDescription(disabled_reason)
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
        list_mode = self.layout_buttons.checkedId() == 1
        self.grouping_bar.setVisible(self._show_grouping)
        self.hpa_button.setVisible(not list_mode and any(t.goal is GoalState.EXCLUDED for t in self.tiles))
        self._reflow()
        self.group_cards = []
        self.tile_buttons, self.tile_grids, self.tile_occurrences = {}, [], []
        self.rebuilt_tiles = self.restyled_tiles = 0
        text = self.search.text().strip().casefold()
        risk_only = self.risk_button.isChecked()
        below_only = self.below_filter.isChecked()
        scoped = tuple(t for t in self.tiles if not below_only or t.goal is GoalState.BELOW)
        risk_filtered = tuple(t for t in scoped if not risk_only or t.risk is RiskStatus.RISK)
        filtered = tuple(t for t in risk_filtered if self._matches(t, text))
        grouping_filtered = tiles_for_grouping(filtered, self.grouping_mode)
        self.visible_ids = tuple(dict.fromkeys(t.equipment_id for t in grouping_filtered))
        self.view_count.setText("표시할 조회 결과가 없습니다." if self.snapshot is None else
                                f"{'목표 미달' if below_only else '전체'}{' · 위험 후보' if risk_only else ''}{' · 검색 적용' if text else ''}"
                                f" · 표시 {len(grouping_filtered)}대 / 선택 범위 {len(self.tiles)}대 · 요약은 선택 범위 전체 기준")
        self.message.setVisible(not grouping_filtered)
        if self.snapshot is not None:
            self.message.setText("선택한 설비군이 없습니다. 위 설비군 버튼에서 하나 이상 골라 주세요."
                                 if self._selected_groups == frozenset() else
                                 "조건에 맞는 설비가 없습니다. 날짜나 필터를 다시 선택해 주세요." if not self.tiles else
                                 "목표 미달 설비가 없습니다." if not scoped else
                                 "위험 후보가 없습니다. '위험 후보'를 해제해 주세요." if not risk_filtered else
                                 "표시할 세부라인이 없습니다." if filtered and not grouping_filtered else
                                 f"'{self.search.text().strip()}' 검색 결과가 없습니다. 검색어를 지우면 전체 설비가 보입니다.")
        self.list_table.setVisible(list_mode and bool(grouping_filtered))
        self.tile_host.setVisible(not list_mode)
        plan = ()
        if grouping_filtered and self.snapshot is not None:
            plan = build_heatmap_groups(grouping_filtered, self._display_groups, mode=self.grouping_mode,
                                        site_order=tuple(site.key for site in self.source.catalog.sites),
                                        show_hpa=list_mode or self.hpa_button.isChecked())
        if list_mode:
            if self.snapshot is None:
                self._clear_card_cache()
                self._sync_card_rows(())
            self._render_list(grouping_filtered, plan)
            return
        if self.snapshot is None:
            self._clear_card_cache()
            self._sync_card_rows(())
            return
        expanded = self.view_toggle.isChecked()
        cache_key = self.grouping_mode, expanded, self.hpa_button.isChecked()
        previous_cards = self._card_cache.pop(cache_key, {})
        self._card_entries = {}
        restore_positions = []
        self._defer_tile_content = len(grouping_filtered) > DEFERRED_TILE_THRESHOLD
        for node in plan:
            signature = (expanded, self._group_signature(node))
            previous = previous_cards.get(node.key)
            if previous is not None and previous[0] == signature:
                _, card, controls, grids = previous
                for control, (tile, families) in zip(controls, self._group_entries(node)):
                    visual = (replace(tile, observation_date=None, findings=()), families)
                    if control.data != tile:
                        control.set_data(tile)
                    if control.visual != visual:
                        control.update_appearance(families)
                        self.restyled_tiles += 1
                    control.setChecked(tile.equipment_id == self.selected_id)
                self.tile_occurrences.extend(controls)
                self.tile_grids.extend(grids)
                for control in controls:
                    self.tile_buttons.setdefault(control.data.equipment_id, control)
            else:
                start, grid_start = len(self.tile_occurrences), len(self.tile_grids)
                card = self._group_card(node, expanded)
                controls, grids = self.tile_occurrences[start:], self.tile_grids[grid_start:]
                self.rebuilt_tiles += len(controls)
                if previous is not None:
                    restore_positions.append((card.scroll, previous[1].scroll.horizontalScrollBar().value()))
            self._card_entries[node.key] = (signature, card, controls, grids)
            self.group_cards.append(card)
        self._card_cache[cache_key] = self._card_entries
        self._trim_card_cache()
        retained = {entry[1] for entries in self._card_cache.values() for entry in entries.values()}
        for _, card, _, _ in previous_cards.values():
            if card not in retained and self.tile_body.indexOf(card) < 0:
                card.hide()
                card.deleteLater()
        self._sync_card_rows(self.group_cards)
        for scroll, position in restore_positions:
            QTimer.singleShot(0, scroll, lambda scroll=scroll, position=position:
                              scroll.horizontalScrollBar().setValue(position))

    def _sync_card_rows(self, cards):
        """같은 카드는 부모·표시 상태를 유지하고 달라진 행만 삽입·제거한다."""
        desired = set(cards)
        retained = {entry[1] for entries in self._card_cache.values() for entry in entries.values()}
        for index in range(self.tile_body.count() - 1, -1, -1):
            card = self.tile_body.itemAt(index).widget()
            if card not in desired:
                self.tile_body.takeAt(index)
                card.hide()
                if card not in retained:
                    card.deleteLater()
        for index, card in enumerate(cards):
            current = self.tile_body.indexOf(card)
            if current == index:
                continue
            if current >= 0:
                self.tile_body.removeWidget(card)
            self.tile_body.insertWidget(index, card)
            if card.isHidden():
                card.show()

    def _clear_card_cache(self):
        for entries in self._card_cache.values():
            for _, card, _, _ in entries.values():
                if self.tile_body.indexOf(card) < 0:
                    card.hide()
                    card.deleteLater()
        self._card_cache.clear()
        self._card_entries = {}

    def _trim_card_cache(self):
        def tile_count():
            return sum(len(entry[2]) for entries in self._card_cache.values() for entry in entries.values())

        while len(self._card_cache) > 1 and (len(self._card_cache) > CARD_CACHE_LIMIT or
                                            tile_count() > CARD_CACHE_TILE_LIMIT):
            _, entries = self._card_cache.popitem(last=False)
            for _, card, _, _ in entries.values():
                if self.tile_body.indexOf(card) < 0:
                    card.hide()
                    card.deleteLater()

    @classmethod
    def _group_signature(cls, node):
        """그룹 구조가 같으면 재생 시 기존 카드와 고정 셀을 그대로 쓴다."""
        return (node.key, node.title, node.group_name,
                tuple(cls._group_signature(child) for child in node.children) if node.children
                else tuple(tile.equipment_id for tile in node.tiles))

    @classmethod
    def _group_entries(cls, node, group_name=None):
        group_name = node.group_name or group_name
        if node.children:
            for child in node.children:
                yield from cls._group_entries(child, group_name)
        else:
            for tile in node.tiles:
                yield tile, group_families(tile, group_name)

    def _group_card(self, node, expanded):
        card = HeatmapGroupCard(node.title, node.equipment_count, show_count=False, center_title=True)
        card.setProperty("groupKey", node.key)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        card.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        card.body.setContentsMargins(12, 4, 12, 12)
        card.body.setSpacing(0)
        card.body.takeAt(card.body.count() - 1)  # 셀 아래로 남는 공간을 늘리던 stretch 제거
        heading = card.body.itemAt(0).widget()
        heading.setObjectName("heatmapGroupHeading")
        heading.setFixedHeight(max(38, heading.fontMetrics().height() + 12))
        heading.setStyleSheet(f"background:#f7f9fc;border:0;border-bottom:1px solid {LINE};padding:6px 0;")
        content = self._group_content(node, expanded, node.group_name)
        card.scroll = HeatmapGroupScroll(content, node.title)
        card.body.addWidget(card.scroll)
        return card

    def _group_content(self, node, expanded, group_name):
        """헤더 행과 설비 열의 크기를 확정해 줄바꿈이나 높이 추정 없이 표시한다."""
        group_name = node.group_name or group_name
        if not node.children:
            return self._tile_column(node, expanded, group_name)

        def leaf_count(group):
            return sum(leaf_count(child) for child in group.children) if group.children else 1

        def depth(group):
            return 1 + max(depth(child) for child in group.children) if group.children else 0

        header_rows = depth(node)
        header_height = max(32, QFontMetrics(font(14, medium=True)).height() + 10)
        canvas = QWidget()
        grid = QGridLayout(canvas)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        tile_height = 0
        boundaries = {}

        def add_children(parent, row, column, inherited_group):
            nonlocal tile_height
            for child in parent.children:
                span = leaf_count(child)
                if column:
                    boundaries[column] = min(boundaries.get(column, row), row)
                rows = 1 if child.children else header_rows - row
                heading = HeatmapColumnHeading(child.title)
                heading.setProperty("groupKey", child.key)
                heading.setFixedSize(span * HEATMAP_CELL_SIZE.width(), rows * header_height)
                background = "#f7f9fc" if row % 2 == 0 else "#eef2f7"
                heading.setStyleSheet(f"QFrame#heatmapColumnHeading {{background:{background};border:0;"
                                     f"border-bottom:1px solid {LINE};}}")
                grid.addWidget(heading, row, column, rows, span, Qt.AlignmentFlag.AlignTop)
                child_group = child.group_name or inherited_group
                if child.children:
                    add_children(child, row + 1, column, child_group)
                else:
                    cells = self._tile_column(child, expanded, child_group)
                    grid.addWidget(cells, header_rows, column, 1, 1, Qt.AlignmentFlag.AlignTop)
                    tile_height = max(tile_height, cells.height())
                column += span

        add_children(node, 0, 0, group_name)
        canvas.setFixedSize(leaf_count(node) * HEATMAP_CELL_SIZE.width(), header_rows * header_height + tile_height)
        for column, row in boundaries.items():
            divider = QFrame(canvas)
            divider.setObjectName("heatmapGroupDivider")
            divider.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            divider.setStyleSheet(f"background:{LINE};border:0;")
            y = row * header_height
            divider.setGeometry(column * HEATMAP_CELL_SIZE.width(), y, 1, canvas.height() - y)
            divider.raise_()
        return canvas

    def _tile_column(self, node, expanded, group_name):
        buttons = []
        for tile in node.tiles:
            families = group_families(tile, group_name)
            size = HEATMAP_CELL_SIZE if expanded else HEATMAP_COMPACT_CELL_SIZE
            control = TileButton(tile, expanded=expanded, families=families, cell_size=size,
                                 defer_content=self._defer_tile_content)
            control.setChecked(tile.equipment_id == self.selected_id)
            control.clicked.connect(lambda checked=False, eid=tile.equipment_id, origin=control: self._select(eid, origin))
            self.tile_buttons.setdefault(tile.equipment_id, control)
            self.tile_occurrences.append(control)
            buttons.append(control)
        grid = TileGrid(buttons, expanded=expanded, target_width=HEATMAP_CELL_SIZE.width(), max_columns=1,
                        centered=True, spacing=0)
        size = HEATMAP_CELL_SIZE if expanded else HEATMAP_COMPACT_CELL_SIZE
        grid.setFixedSize(size.width(), len(buttons) * size.height())
        self.tile_grids.append(grid)
        return grid

    def _set_list_compact(self, compact):
        if self._compact_list is compact:
            return
        self._compact_list = compact
        table = self.list_table
        table.compact = compact
        table.setWordWrap(not compact)
        table.setTextElideMode(Qt.TextElideMode.ElideRight if compact else Qt.TextElideMode.ElideNone)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setStyleSheet(self._list_style + ("QTreeView::item {padding:3px 8px;}" if compact else ""))
        header = table.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        if compact:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnHidden(1, True)
        table.setColumnHidden(3, False)
        for column in (2, 5):
            table.setColumnHidden(column, compact)
        table.horizontalScrollBar().setValue(0)
        table.resize_columns()

    def _render_list(self, filtered, plan):
        compact = not self.view_toggle.isChecked()
        self._set_list_compact(compact)

        def row_values(tile, group_name):
            families = group_families(tile, group_name)
            empty_version = "분석 제외" if tile.goal is GoalState.EXCLUDED else "SW 미등록"
            versions = _version_text(tile, families, show_tier=False,
                                     separator=" / " if compact else "\n", empty=empty_version)
            models = dict.fromkeys(family.title.partition(" · 계열 ")[0] for family in families)
            names = group_name or tile.model_name or "\n".join(models) or "—"
            position = tile.site_label, tile.area or "구역 미확인"
            location = (" / ".join(" ".join(part.splitlines()) for part in position)
                        if compact else "\n".join(position))
            risk = tile.risk_label if compact else "\n".join(filter(None, (tile.risk_label, tile.risk_detail)))
            values = tile.code, location, names, versions, tile.goal.value, risk
            tooltips = {3: tile.description}
            if tile.goal is GoalState.UNKNOWN:
                tooltips[self.list_model.tone_column] = tile.description
            return values, tile.version_tone, tooltips

        self.list_table.set_groups(plan, row_values)
        self.list_table.select_equipment(self.selected_id, reveal=False)

    def _select_row(self, index):
        if index.isValid():
            equipment_id = index.data(Qt.ItemDataRole.UserRole)
            if equipment_id is not None:
                self._select(equipment_id, self.list_table)

    def _select(self, equipment_id, origin=None):
        if (self.snapshot is None or equipment_id not in self.visible_ids
                or self._query_bound and (self.state is None or not self.state.is_current(self.snapshot.query))):
            return
        self.select_equipment(equipment_id)
        tile = next(t for t in self.tiles if t.equipment_id == equipment_id)
        self.selection_notice.setText(f"선택: {tile.code} · {tile.goal.value} · {tile.risk_label}\n"
                                      + ("설비 상세로 이동합니다." if self.detail_connected else
                                         "설비 ID 전달 완료 · 목록·상세는 6번 작업에서 연결합니다."))
        self.selection_control = (origin if origin is not None else
                                  self.list_table if self.layout_buttons.checkedId() == 1 else
                                  self.tile_buttons.get(equipment_id))
        try:
            self.equipment_selected.emit(EquipmentSelection(self.snapshot.query, self.snapshot.base_date, equipment_id,
                                                            tile.observation_date))
        finally:
            self.selection_control = None

    def select_equipment(self, equipment_id):
        """카드·목록의 선택 표시를 맞춘다. 선택 신호나 조회는 발생시키지 않는다."""
        self.selected_id = equipment_id if equipment_id in self.visible_ids else None
        for tile in self.tile_occurrences:
            tile.setChecked(tile.data.equipment_id == self.selected_id)
        if self.selected_id is None:
            self.list_table.select_equipment(None)
        elif self.selected_id in self.list_model.ids and self.list_table.isVisible():
            self.list_table.select_equipment(self.selected_id)
