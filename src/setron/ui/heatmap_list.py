"""카드형과 같은 그룹 트리를 사용하는 히트맵 목록 표시."""

from math import floor

from PySide6.QtCore import QEvent, QModelIndex, QRect, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QHeaderView, QSizePolicy, QStyle, QStyleOption, QStyledItemDelegate, QTreeView

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
        self._structure_signature, self._row_items = None, ()
        self.name_changed = False
        self._rebuilding = False
        self.setHorizontalHeaderLabels(self.headings)

    @classmethod
    def _signature(cls, node):
        return (node.key, node.title, node.group_name,
                tuple(cls._signature(child) for child in node.children) if node.children
                else tuple(tile.equipment_id for tile in node.tiles))

    @classmethod
    def _entries(cls, nodes, group_name=None):
        for node in nodes:
            current = node.group_name or group_name
            if node.children:
                yield from cls._entries(node.children, current)
            else:
                for tile in node.tiles:
                    yield tile, current

    def set_groups(self, nodes, row_factory):
        """구조가 같으면 기존 셀 중 달라진 값만 갱신한다. (재구성 여부, 글자가 바뀐 행)을 반환한다."""
        nodes = tuple(nodes)
        self.name_changed = False
        signature = tuple(self._signature(node) for node in nodes)
        if signature == self._structure_signature:
            rows, ids, tones, changed_rows = [], [], [], []
            for (tile, group_name), items in zip(self._entries(nodes), self._row_items):
                values, tone, tooltips = row_factory(tile, group_name)
                text_changed = False
                for column, (item, value) in enumerate(zip(items, values)):
                    if item.text() != value:
                        item.setText(value)
                        text_changed = True
                        self.name_changed |= column == 0
                    tooltip = tooltips.get(column, value)
                    if item.toolTip() != tooltip:
                        item.setToolTip(tooltip)
                    if column == self.tone_column and item.data(self.TONE_ROLE) != tone:
                        item.setData(tone, self.TONE_ROLE)
                if text_changed:
                    changed_rows.append(items[0])
                rows.append(values)
                ids.append(tile.equipment_id)
                tones.append(tone)
            self.rows, self.ids, self.tones = tuple(rows), tuple(ids), tuple(tones)
            return False, tuple(changed_rows)

        self._rebuilding = True
        try:
            self._replace_groups(nodes, row_factory, signature)
        finally:
            self._rebuilding = False
        return True, ()

    def _replace_groups(self, nodes, row_factory, signature):
        # clear()는 열과 숨김 상태까지 지우며 resize 이벤트가 삭제된 셀을 읽게 한다.
        # 참조를 먼저 비우고 행만 교체해 헤더와 열 설정을 그대로 유지한다.
        self._equipment_items, self._group_items = {}, {}
        self._structure_signature, self._row_items = None, ()
        self.rows, self.ids, self.tones = (), (), ()
        self.removeRows(0, self.rowCount())
        rows, ids, tones, row_items = [], [], [], []

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
                    if column == self.tone_column:
                        item.setData(tone, self.TONE_ROLE)
                    item.setData(node.key, self.KEY_ROLE)
                    item.setToolTip(tooltips.get(column, value))
                    children.append(item)
                items[0].appendRow(children)
                self._equipment_items.setdefault(tile.equipment_id, []).append(children[0])
                row_items.append(tuple(children))
                rows.append(values)
                ids.append(tile.equipment_id)
                tones.append(tone)

        root = self.invisibleRootItem()
        for node in nodes:
            append(root, node)
        self.rows, self.ids, self.tones = tuple(rows), tuple(ids), tuple(tones)
        self._structure_signature, self._row_items = signature, tuple(row_items)
        self.name_changed = True

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
        self._resizing_columns = False
        self._row_heights, self._last_compact = {}, self.compact
        self.setModel(model)
        self.setAccessibleName(name)
        self.setAccessibleDescription("분류를 펼치거나 접고 설비를 선택합니다. 좌우 방향키로 분류를 탐색합니다.")
        self.setMinimumWidth(0)
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.header().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        for column, width in enumerate(widths):
            self.setColumnWidth(column, width)
        self.setStyleSheet("QTreeView {background:white;border:0;selection-background-color:#edf0fd;selection-color:#18243c;}"
                           "QHeaderView {background:#f7f9fc;}"
                           "QHeaderView::section {background:#f7f9fc;color:#5b6880;border:0;"
                           "border-right:1px solid #e3e8ef;padding:8px;}"
                           "QHeaderView::section:last {border-right:0;}"
                           "QTreeView::item {padding:8px;border:0;}"
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
        dpr = max(0.01, self.devicePixelRatioF())

        def snap(value):
            return (floor(value * dpr) + 0.5) / dpr

        def axis(level):
            return snap(current_x - direction * (depth - level) * indent)

        def has_next(item):
            return item.row() + 1 < self.model().rowCount(item.parent())

        top, middle, bottom = float(rect.top()), snap(rect.top() + rect.height() / 2), float(rect.bottom() + 1)
        has_children = self.model().hasChildren(index)
        radius, arrow_gap = min(5.0, indent / 3), 4.0
        painter.save()
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor(ui_color(LINE))
        color.setAlphaF(0.8)
        pen = QPen(color, 1.0)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        branches = QPainterPath()
        # 형제가 더 있는 조상의 연결선은 하위 행을 지나 다음 형제까지 이어진다.
        for level, ancestor in enumerate(ancestors):
            if level > 0 and has_next(ancestor):
                x = axis(level - 1)
                branches.moveTo(x, top)
                branches.lineTo(x, bottom)
        if depth:
            x = axis(depth - 1)
            branches.moveTo(x, top)
            branches.lineTo(x, middle - radius)
            branches.quadTo(x, middle, x + direction * radius, middle)
            end = axis(depth) - direction * arrow_gap if has_children else axis(depth) + direction * (indent / 2 - 3)
            branches.lineTo(end, middle)
            if has_next(index):
                branches.moveTo(x, middle - radius)
                branches.lineTo(x, bottom)
        if has_children and self.isExpanded(index):
            branches.moveTo(axis(depth), middle + arrow_gap)
            branches.lineTo(axis(depth), bottom)
        painter.drawPath(branches)
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
            rebuilt, changed_rows = self.model().set_groups(nodes, row_factory)
            if rebuilt:
                self.expandAll()
                for key in self._collapsed_keys:
                    index = self.model().group_index(key)
                    if index.isValid():
                        self.setExpanded(index, False)
        if rebuilt:
            self.select_equipment(equipment_id, preferred_key=preferred_key, reveal=False)
        columns_changed = self.resize_columns() if rebuilt or changed_rows else False
        resize = rebuilt or columns_changed or self.compact != self._last_compact
        if not resize:
            for item in changed_rows:
                index = self.model().indexFromItem(item)
                parent, visible = index.parent(), True
                while parent.isValid():
                    if not self.isExpanded(parent):
                        visible = False
                        break
                    parent = parent.parent()
                if visible and self._row_heights.get(self._row_key(index)) != self._row_height(index):
                    resize = True
                    break
        if resize:
            self.fit_height()
        self._last_compact = self.compact

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

    def _column_widths(self):
        name_width = 220
        for items in self.model()._equipment_items.values():
            for item in items:
                index, depth = self.model().indexFromItem(item), int(self.rootIsDecorated())
                parent = index.parent()
                while parent.isValid():
                    depth += 1
                    parent = parent.parent()
                text_width = QFontMetrics(index.data(Qt.ItemDataRole.FontRole)).horizontalAdvance(item.text())
                name_width = max(name_width, text_width + depth * self.indentation() + 20)
        limits = {3: (140, 160), 4: (100, 120)} if self.compact else {
            2: (120, 160), 3: (150, 200), 4: (100, 120), 5: (110, 150)}
        widths = {0: name_width}
        for column, (minimum, maximum) in limits.items():
            if self.isColumnHidden(column):
                continue
            required = self.fontMetrics().horizontalAdvance(self.model().headings[column]) + 20
            for items in self.model()._row_items:
                item = items[column]
                index = self.model().indexFromItem(item)
                metrics = QFontMetrics(index.data(Qt.ItemDataRole.FontRole))
                required = max(required, *(metrics.horizontalAdvance(line) + 20 for line in (item.text().splitlines() or [""])))
            widths[column] = max(minimum, min(maximum, required))
        return widths

    def resize_columns(self):
        model = self.model()
        if self._resizing_columns or model is None or model._rebuilding:
            return False
        changed = False
        self._resizing_columns = True
        try:
            for column, width in self._column_widths().items():
                if self.columnWidth(column) != width:
                    self.setColumnWidth(column, width)
                    changed = True
        finally:
            self._resizing_columns = False
        return changed

    @staticmethod
    def _row_key(index):
        return tuple(index.data(HeatmapListModel.KEY_ROLE)), index.data(Qt.ItemDataRole.UserRole)

    def _row_height(self, index):
        return max(self.sizeHintForIndex(index.siblingAtColumn(column)).height()
                   for column in range(self.model().columnCount(index.parent())) if not self.isColumnHidden(column))

    def fit_height(self):
        if self._fitting_height or self.model() is None or self.model()._rebuilding:
            return
        self._fitting_height = True
        try:
            self.executeDelayedItemsLayout()
            height, model = self.header().height() + 2 * self.frameWidth() + 4, self.model()
            self._row_heights = {}

            def add_rows(parent=QModelIndex()):
                nonlocal height
                for row in range(model.rowCount(parent)):
                    index = model.index(row, 0, parent)
                    row_height = self._row_height(index)
                    self._row_heights[self._row_key(index)] = row_height
                    height += row_height
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

    def viewportEvent(self, event):
        result = super().viewportEvent(event)
        if event.type() == QEvent.Type.Resize:
            self.resize_columns()
            self.fit_height()
        return result
