"""Item 배포 표시. 기존 반응형 타일 배치와 공통 표를 사용하며 업무 판정을 만들지 않는다."""

from PySide6.QtCore import QModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QTableView, QVBoxLayout, QWidget

from setron.features.item.schemas import APPLICATION_LABELS, Application
from setron.ui.components import label
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.heatmap import TileGrid
from setron.ui.theme import PRIMARY, SECONDARY, TONES, font
from setron.ui.versions import _fit


APPLICATION_TONES = {Application.APPLIED: "success", Application.NOT_APPLIED: "warning",
                     Application.UNKNOWN: "info", Application.NOT_TARGET: "neutral"}


class DistributionModel(ReadTableModel):
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and index.column() > 0 and role == Qt.ItemDataRole.BackgroundRole:
            return QColor(TONES[APPLICATION_TONES[tuple(Application)[index.column() - 1]]][0])
        return super().data(index, role)


class DistributionDelegate(QStyledItemDelegate):
    """공통 표의 QSS가 배경 역할을 가리는 환경에서도 상태 색상과 숫자를 함께 표시한다."""

    def paint(self, painter, option, index):
        if index.column() == 0:
            return super().paint(painter, option, index)
        state = tuple(Application)[index.column() - 1]
        background, ink, border = TONES[APPLICATION_TONES[state]]
        rect = option.rect.adjusted(3, 3, -3, -3)
        painter.save()
        painter.fillRect(rect, QColor(background))
        painter.setPen(QColor(PRIMARY if option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus) else border))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.setPen(QColor(ink))
        painter.setFont(font(13))
        painter.drawText(rect.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, index.data())
        painter.restore()


class ItemTile(QPushButton):
    def __init__(self, result):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setObjectName("itemTile")
        background, ink, border = TONES[APPLICATION_TONES[result.application]]
        self.setStyleSheet(f"QPushButton#itemTile {{background:{background};color:{ink};border:1px solid {background};border-radius:10px;}}"
                           f"QPushButton#itemTile:hover, QPushButton#itemTile:focus, QPushButton#itemTile:checked {{border:2px solid {PRIMARY};}}")
        body = QVBoxLayout(self)
        body.setContentsMargins(16, 14, 16, 14)
        self.code_label = label(result.record.equipment_code, 13, medium=True, wrap=True)
        self.code_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body.addWidget(self.code_label)
        body.addWidget(label(result.label, 12, color=ink))
        description = f"{result.record.site} · {result.record.equipment_code} · ID {result.record.equipment_id} · {result.label}"
        self.setAccessibleName(description)
        self.setAccessibleDescription("Space 또는 클릭으로 기존 설비 상세와 Item 근거 보기")
        self.setToolTip(description)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def minimumSizeHint(self):
        return QSize(0, self.layout().minimumSize().height())


class ItemDistribution(QWidget):
    scope_selected = Signal(object, object)
    equipment_selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.results, self.sites, self.tiles = (), (), {}
        self.generation = 0
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        body.addWidget(label("Site별 Item 적용 분포", 15, medium=True))
        body.addWidget(label("적용 / 미적용 / 미확인 / 대상 외 · 색상과 문구를 함께 표시합니다.\n"
                             "Site의 상태 셀 선택 → 해당 설비 범위 · 타일 선택 → 설비 상세와 Item 근거", 13, color=SECONDARY, wrap=True))
        self.model = DistributionModel(("Site", *(APPLICATION_LABELS[s] for s in Application)))
        self.table = read_table(self.model, "Site별 Item 적용 히트맵", (220, 145, 145, 145, 145))
        self.table.setItemDelegate(DistributionDelegate(self.table))
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectItems)
        self.table.clicked.connect(self.select_cell)
        self.table.activated.connect(self.select_cell)
        body.addWidget(self.table)
        self.tile_host = QWidget()
        self.tile_body = QVBoxLayout(self.tile_host)
        self.tile_body.setContentsMargins(0, 0, 0, 0)
        self.tile_body.setSpacing(14)
        body.addWidget(self.tile_host)
        self.empty = label("표시할 설비가 없습니다.", 13, wrap=True)
        body.addWidget(self.empty)

    def select_cell(self, index):
        if index.isValid() and 0 <= index.row() < len(self.sites):
            self.scope_selected.emit(self.sites[index.row()], tuple(Application)[index.column() - 1] if index.column() else None)

    def set_results(self, results, visible, selected_id=None):
        self.generation += 1
        generation = self.generation
        self.results = tuple(results)
        self.sites = tuple(sorted({e.record.site for e in results}))
        self.model.set_rows(tuple((site, *(f"{sum(e.record.site == site and e.application == state for e in results)}대"
                                           for state in Application)) for site in self.sites))
        self.table.setVisible(bool(self.sites))
        _fit(self.table)
        while self.tile_body.count():
            child = self.tile_body.takeAt(0).widget()
            if child:
                child.hide()
                child.deleteLater()
        self.tiles = {}
        for site in sorted({e.record.site for e in visible}):
            title = label(site, 14, medium=True, wrap=True)
            title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.tile_body.addWidget(title)
            tiles = []
            for result in visible:
                if result.record.site != site:
                    continue
                tile = ItemTile(result)
                eid = result.record.equipment_id
                tile.setChecked(eid == selected_id)
                tile.clicked.connect(lambda _checked=False, identity=eid, expected=generation:
                                     self.equipment_selected.emit(identity) if expected == self.generation else None)
                self.tiles[eid] = tile
                tiles.append(tile)
            self.tile_body.addWidget(TileGrid(tiles, expanded=True))
        self.empty.setVisible(not visible)

    def set_scope(self, site, state):
        index = (self.model.index(self.sites.index(site), tuple(Application).index(state) + 1 if state is not None else 0)
                 if site in self.sites else QModelIndex())
        self.table.setCurrentIndex(index)

    def select_equipment(self, equipment_id):
        for identity, tile in self.tiles.items():
            tile.setChecked(identity == equipment_id)
