"""카드형과 같은 그룹 트리를 사용하는 히트맵 목록 표시."""

from PySide6.QtCore import QModelIndex, QPointF, QRect, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QHeaderView, QStyle, QStyleOption, QStyledItemDelegate, QTreeView

from setron.ui.appearance import ui_color
from setron.ui.theme import ACCENT_SOFT, LINE, PRIMARY, TONES, font, paint_font, set_widget_font


class HeatmapListModel(QStandardItemModel):
    """공통 분류 노드를 부모로, 설비 ID를 최하위 행으로 보관한다."""

    GROUP_ROLE = Qt.ItemDataRole.UserRole + 1
    KEY_ROLE = Qt.ItemDataRole.UserRole + 2
    TONE_ROLE = Qt.ItemDataRole.UserRole + 3

    def __init__(self, headings, *, numeric_columns=(), centered=False, tone_column=4):
        super().__init__()
        self.headings = tuple(headings)
        self.numeric_columns, self.tone_column = numeric_columns, tone_column
        self.alignment = Qt.AlignmentFlag.AlignCenter if centered else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        self.rows, self.ids, self.tones = (), (), ()
        self._equipment_items, self._group_items = {}, {}
        self.setHorizontalHeaderLabels(self.headings)

    def set_groups(self, nodes, row_factory):
        """카드와 같은 트리를 유지하며 각 설비군의 SW 표시 범위도 전달한다."""
        self.clear()
        self.setHorizontalHeaderLabels(self.headings)
        self._equipment_items, self._group_items = {}, {}
        rows, ids, tones = [], [], []

        def append(parent, node, group_name=None):
            group_name = node.group_name or group_name
            items = [QStandardItem(node.title), *(QStandardItem("") for _ in self.headings[1:])]
            for item in items:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setData(True, self.GROUP_ROLE)
                item.setData(node.key, self.KEY_ROLE)
                item.setToolTip(node.title)
            parent.appendRow(items)
            self._group_items[node.key] = items[0]
            if node.children:
                for child in node.children:
                    append(items[0], child, group_name)
                return
            for tile in node.tiles:
                values, tone, tooltips = row_factory(tile, group_name)
                children = []
                for column, value in enumerate(values):
                    item = QStandardItem(value)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    item.setData(tile.equipment_id, Qt.ItemDataRole.UserRole)
                    item.setData(tone, self.TONE_ROLE)
                    item.setData(node.key, self.KEY_ROLE)
                    item.setToolTip(tooltips.get(column, value))
                    children.append(item)
                items[0].appendRow(children)
                self._equipment_items.setdefault(tile.equipment_id, []).append(children[0])
                rows.append(values)
                ids.append(tile.equipment_id)
                tones.append(tone)

        root = self.invisibleRootItem()
        for node in nodes:
            append(root, node)
        self.rows, self.ids, self.tones = tuple(rows), tuple(ids), tuple(tones)

    def equipment_index(self, equipment_id, preferred_key=None):
        items = self._equipment_items.get(equipment_id, ())
        preferred = next((item for item in items if tuple(item.data(self.KEY_ROLE)) == preferred_key), None)
        return self.indexFromItem(preferred if preferred is not None else items[0]) if items else QModelIndex()

    def group_index(self, key):
        item = self._group_items.get(key)
        return self.indexFromItem(item) if item is not None else QModelIndex()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        group = super().data(index, self.GROUP_ROLE)
        if group:
            if role == Qt.ItemDataRole.BackgroundRole:
                return ui_color(ACCENT_SOFT)
            if role == Qt.ItemDataRole.ForegroundRole:
                return ui_color(PRIMARY)
            if role == Qt.ItemDataRole.FontRole:
                return paint_font(14, medium=True)
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        else:
            if role == Qt.ItemDataRole.FontRole:
                return paint_font(14 if index.column() == 0 else 13, medium=index.column() == 0,
                                  numeric=index.column() in self.numeric_columns)
            if role == Qt.ItemDataRole.ForegroundRole and index.column() == self.tone_column:
                tone = super().data(index, self.TONE_ROLE)
                if tone in TONES:
                    return ui_color(TONES[tone][1])
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter if index.column() == 0 else self.alignment
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return super().data(index, Qt.ItemDataRole.DisplayRole)
        return super().data(index, role)


class _HeatmapTreeDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        minimum = 34 if index.data(HeatmapListModel.GROUP_ROLE) or self.parent().compact else 56
        size.setHeight(max(minimum, size.height()))
        return size


class HeatmapTreeView(QTreeView):
    """분류별 접기 상태를 시점 변경에도 보존하는 읽기 전용 목록."""

    def __init__(self, model, name, widths):
        super().__init__()
        self.base_widths, self.compact = tuple(widths), False
        self._collapsed_keys = set()
        self._fitting_height = False
        self.setModel(model)
        self.setAccessibleName(name)
        self.setAccessibleDescription("분류를 펼치거나 접고 설비를 선택합니다. 좌우 방향키로 분류를 탐색합니다.")
        self.setMinimumWidth(0)
        self.setMinimumHeight(260)
        set_widget_font(self, font(13))
        self.setItemDelegate(_HeatmapTreeDelegate(self))
        self.setRootIsDecorated(True)
        self.setItemsExpandable(True)
        self.setExpandsOnDoubleClick(True)
        self.setIndentation(22)
        self.setUniformRowHeights(False)
        self.setAllColumnsShowFocus(True)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTreeView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QTreeView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QTreeView.ScrollMode.ScrollPerPixel)
        self.header().setMinimumHeight(40)
        self.header().setStretchLastSection(True)
        self.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.header().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        for column, width in enumerate(widths):
            self.setColumnWidth(column, width)
        self.setStyleSheet("QTreeView {background:white;border:0;selection-background-color:#edf0fd;selection-color:#18243c;}"
                           "QHeaderView::section {background:#f7f9fc;color:#5b6880;border:0;padding:8px;}"
                           "QTreeView::item {padding:8px;border-bottom:1px solid #e9eef6;}"
                           f"QTreeView:focus {{border:1px solid {PRIMARY};}}")
        self.collapsed.connect(lambda index: self._remember_expansion(index, False))
        self.expanded.connect(lambda index: self._remember_expansion(index, True))

    def drawBranches(self, painter, rect, index):
        """댓글처럼 부모에서 자식으로 잇고 기본 펼침 화살표는 그대로 그린다."""
        ancestors, parent = [], index.parent()
        while parent.isValid():
            ancestors.append(parent)
            parent = parent.parent()
        ancestors.reverse()
        depth, indent = len(ancestors), float(self.indentation())
        direction = -1 if self.layoutDirection() == Qt.LayoutDirection.RightToLeft else 1
        current_x = rect.left() + indent / 2 if direction < 0 else rect.right() + 1 - indent / 2
        dpr = max(1.0, self.devicePixelRatioF())

        def snap(value):
            return (round(value * dpr - 0.5) + 0.5) / dpr

        def axis(level):
            return snap(current_x - direction * (depth - level) * indent)

        def has_next(item):
            return item.row() + 1 < self.model().rowCount(item.parent())

        top, middle, bottom = snap(rect.top()), snap(rect.top() + rect.height() / 2), snap(rect.bottom() + 1)
        has_children = self.model().hasChildren(index)
        radius, arrow_gap = min(5.0, indent / 3), 6.0
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor(ui_color(LINE))
        color.setAlphaF(0.8)
        pen = QPen(color, 1.0)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # 형제가 더 있는 조상의 연결선은 하위 행을 지나 다음 형제까지 이어진다.
        for level, ancestor in enumerate(ancestors):
            if level > 0 and has_next(ancestor):
                x = axis(level - 1)
                painter.drawLine(QPointF(x, top), QPointF(x, bottom))
        if depth:
            x = axis(depth - 1)
            if has_next(index):
                painter.drawLine(QPointF(x, top), QPointF(x, bottom))
            path = QPainterPath(QPointF(x, top))
            path.lineTo(x, middle - radius)
            path.quadTo(x, middle, x + direction * radius, middle)
            end = axis(depth) - direction * arrow_gap if has_children else axis(depth) + direction * (indent / 2 - 3)
            path.lineTo(end, middle)
            painter.drawPath(path)
        if has_children and self.isExpanded(index):
            painter.drawLine(QPointF(axis(depth), middle + arrow_gap), QPointF(axis(depth), bottom))
        painter.restore()
        if has_children and (depth or self.rootIsDecorated()):
            option = QStyleOption()
            option.initFrom(self)
            option.rect = QRect(round(current_x - indent / 2), rect.top(), round(indent), rect.height())
            # Item/Sibling은 기본 점선을 그리므로 화살표 상태만 넘긴다.
            option.state &= ~(QStyle.StateFlag.State_Item | QStyle.StateFlag.State_Sibling)
            option.state |= QStyle.StateFlag.State_Children
            if self.isExpanded(index):
                option.state |= QStyle.StateFlag.State_Open
            self.style().drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorBranch, option, painter, self)

    def _remember_expansion(self, index, expanded):
        key = index.data(HeatmapListModel.KEY_ROLE)
        if key is not None:
            key = tuple(key)
            if expanded:
                self._collapsed_keys.discard(key)
            else:
                self._collapsed_keys.add(key)
        self.fit_height()

    def set_groups(self, nodes, row_factory):
        current = self.currentIndex()
        equipment_id = current.data(Qt.ItemDataRole.UserRole)
        preferred_key = current.data(HeatmapListModel.KEY_ROLE)
        preferred_key = tuple(preferred_key) if preferred_key is not None else None
        with QSignalBlocker(self):
            self.model().set_groups(nodes, row_factory)
            self.expandAll()
            for key in self._collapsed_keys:
                index = self.model().group_index(key)
                if index.isValid():
                    self.setExpanded(index, False)
        self.select_equipment(equipment_id, preferred_key=preferred_key, reveal=False)
        self.fit_height()

    def select_equipment(self, equipment_id, *, preferred_key=None, reveal=True):
        current = self.currentIndex()
        if equipment_id is not None and current.isValid() and current.data(Qt.ItemDataRole.UserRole) == equipment_id:
            index = current
        else:
            index = self.model().equipment_index(equipment_id, preferred_key)
        if not index.isValid():
            self.clearSelection()
            self.setCurrentIndex(QModelIndex())
            return
        if reveal:
            parent = index.parent()
            while parent.isValid():
                self.setExpanded(parent, True)
                parent = parent.parent()
        self.setCurrentIndex(index)

    def resize_columns(self):
        if self.compact:
            return
        ratio = max(1, self.viewport().width() / sum(self.base_widths))
        for column, width in enumerate(self.base_widths):
            self.setColumnWidth(column, int(width * ratio))

    def fit_height(self):
        if self._fitting_height or self.model() is None:
            return
        self._fitting_height = True
        try:
            self.executeDelayedItemsLayout()
            height, model = self.header().height() + 2 * self.frameWidth() + 4, self.model()

            def add_rows(parent=QModelIndex()):
                nonlocal height
                for row in range(model.rowCount(parent)):
                    index = model.index(row, 0, parent)
                    height += max(self.sizeHintForIndex(model.index(row, column, parent)).height()
                                  for column in range(model.columnCount(parent)) if not self.isColumnHidden(column))
                    if self.isExpanded(index):
                        add_rows(index)

            add_rows()
            policy, scrollbar = self.horizontalScrollBarPolicy(), self.horizontalScrollBar()
            if (policy == Qt.ScrollBarPolicy.ScrollBarAlwaysOn or
                    policy == Qt.ScrollBarPolicy.ScrollBarAsNeeded and
                    (scrollbar.maximum() > scrollbar.minimum() or self.header().length() > self.viewport().width())):
                height += scrollbar.sizeHint().height()
            self.setFixedHeight(min(800, max(80 if self.compact else 260, height)))
        finally:
            self._fitting_height = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.resize_columns()
            self.fit_height()
