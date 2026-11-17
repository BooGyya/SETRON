"""카드형과 같은 그룹 트리를 사용하는 히트맵 목록 표시."""

from collections import OrderedDict
from math import floor

from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, QSignalBlocker, QSize, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QFontMetrics, QKeySequence, QPainter, QPainterPath, QPen, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication, QHeaderView, QSizePolicy, QStyledItemDelegate, QStyleOptionViewItem, QTreeView

from setron.ui.appearance import ui_color
from setron.ui.theme import INK, LINE, PRIMARY, SECONDARY, TONES, font, paint_font, set_widget_font


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
        self._content_revision = self._name_revision = 0
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
            if changed_rows:
                self._content_revision += 1
            if self.name_changed:
                self._name_revision += 1
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
        self._content_revision += 1
        self._name_revision += 1

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
            if role == Qt.ItemDataRole.ForegroundRole:
                return ui_color(INK if not index.parent().isValid() else SECONDARY)
            if role == Qt.ItemDataRole.FontRole:
                parent, depth = index.parent(), 0
                while parent.isValid():
                    depth += 1
                    parent = parent.parent()
                return paint_font(15 if depth == 0 else 14, medium=depth < 2)
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
    SIZE_CACHE_LIMIT = 32768

    def __init__(self, parent):
        super().__init__(parent)
        self._sizes, self._size_context = OrderedDict(), None
        parent.installEventFilter(self)
        model = parent.model()
        for signal in (model.dataChanged, model.modelReset, model.layoutChanged, model.rowsInserted,
                       model.rowsRemoved, model.columnsInserted, model.columnsRemoved):
            signal.connect(self._clear_sizes)

    def _clear_sizes(self, *_):
        self._sizes.clear()

    def eventFilter(self, watched, event):
        if watched is self.parent() and event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange,
                                                       QEvent.Type.ApplicationFontChange):
            self._clear_sizes()
        return super().eventFilter(watched, event)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.column() == 0:
            option.features &= ~QStyleOptionViewItem.ViewItemFeature.WrapText
            option.textElideMode = Qt.TextElideMode.ElideRight

    def sizeHint(self, option, index):
        view = self.parent()
        context = view.compact, view.wordWrap(), view.font().key()
        if context != self._size_context:
            self._clear_sizes()
            self._size_context = context
        key = QPersistentModelIndex(index), option.rect.width()
        cached = self._sizes.get(key)
        if cached is not None:
            self._sizes.move_to_end(key)
            return QSize(cached)
        size = super().sizeHint(option, index)
        minimum = 32 if index.data(HeatmapListModel.GROUP_ROLE) else 34 if view.compact else 46
        size.setHeight(max(minimum, size.height()))
        self._sizes[key] = QSize(size)
        if len(self._sizes) > self.SIZE_CACHE_LIMIT:
            self._sizes.popitem(last=False)
        return size


class HeatmapTreeView(QTreeView):
    """분류별 접기 상태를 시점 변경에도 보존하는 읽기 전용 목록."""

    def __init__(self, model, name, widths):
        super().__init__()
        self.base_widths, self.compact = tuple(widths), False
        self._collapsed_keys = set()
        self._expansion_depth = -1
        self._fitting_height = False
        self._mouse_press_active = False
        self._pressed_group = QPersistentModelIndex()
        self._geometry_pending = False
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.timeout.connect(self._update_geometry)
        self._resizing_columns = False
        self._row_heights, self._last_compact = {}, self.compact
        self._height_capped = False
        self._text_widths = OrderedDict()
        self._width_signature = self._name_width_signature = None
        self._measured_widths, self._name_width = {}, 220
        self.setModel(model)
        self.setAccessibleName(name)
        self.setAccessibleDescription("분류 이름이나 화살표를 눌러 펼치거나 접고 설비를 선택합니다. 좌우 방향키로 분류를 탐색합니다.")
        self.setMinimumWidth(0)
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        set_widget_font(self, font(13))
        self.setItemDelegate(_HeatmapTreeDelegate(self))
        self.setRootIsDecorated(True)
        self.setItemsExpandable(True)
        self.setExpandsOnDoubleClick(False)
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
        self.copy_name_action = QAction("설비명 복사", self)
        self.copy_name_action.setShortcut(QKeySequence.StandardKey.Copy)
        self.copy_name_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.copy_name_action.setEnabled(False)
        self.copy_name_action.triggered.connect(self.copy_equipment_name)
        self.addAction(self.copy_name_action)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        self.selectionModel().currentChanged.connect(self._sync_copy_action)
        model.modelAboutToBeReset.connect(self._cancel_mouse_press)
        model.rowsAboutToBeRemoved.connect(self._cancel_mouse_press)

    def _sync_copy_action(self, current, previous=None):
        self.copy_name_action.setEnabled(current.isValid() and current.data(Qt.ItemDataRole.UserRole) is not None)

    def copy_equipment_name(self):
        index = self.currentIndex()
        if not index.isValid() or index.data(Qt.ItemDataRole.UserRole) is None:
            return False
        QApplication.clipboard().setText(index.siblingAtColumn(0).data())
        return True

    def drawBranches(self, painter, rect, index):
        """곡선 연결선과 명시적인 펼침 화살표를 같은 클릭 영역에 그린다."""
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
        if has_children and (depth or self.rootIsDecorated()):
            # 플랫폼 스타일의 branch 상태 조합에 의존하면 연결선만 보일 수 있다.
            arrow, x = QPainterPath(), snap(current_x)
            if self.isExpanded(index):
                arrow.moveTo(x - 4, middle - 2)
                arrow.lineTo(x + 4, middle - 2)
                arrow.lineTo(x, middle + 3)
            else:
                arrow.moveTo(x - direction * 2, middle - 4)
                arrow.lineTo(x + direction * 3, middle)
                arrow.lineTo(x - direction * 2, middle + 4)
            arrow.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ui_color(SECONDARY))
            painter.drawPath(arrow)
        painter.restore()

    def _remember_expansion(self, index, expanded):
        self._expansion_depth = None
        key = index.data(HeatmapListModel.KEY_ROLE)
        if key is not None:
            key = tuple(key)
            if expanded:
                self._collapsed_keys.discard(key)
            else:
                self._collapsed_keys.add(key)
        # Qt가 트리의 펼침 상태를 변경하는 중에는 레이아웃을 다시 실행하지 않는다.
        self._schedule_geometry()

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton:
            self._mouse_press_active = True
            name_pressed = (index.isValid() and index.column() == 0 and index.data(HeatmapListModel.GROUP_ROLE)
                            and self.model().hasChildren(index) and self.visualRect(index).contains(event.position().toPoint()))
            self._pressed_group = QPersistentModelIndex(index) if name_pressed else QPersistentModelIndex()
        try:
            super().mousePressEvent(event)
        except Exception:
            self._cancel_mouse_press()
            raise

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        group = self._pressed_group
        released = self.indexAt(event.position().toPoint())
        toggle = (self._mouse_press_active and group.isValid() and released == group
                  and self.visualRect(released).contains(event.position().toPoint()))
        try:
            super().mouseReleaseEvent(event)
        finally:
            self._cancel_mouse_press()
        if toggle and group.isValid():
            # 이름 클릭은 같은 분류에서 버튼을 놓았을 때 완료한다. 화살표는 Qt가 처리한다.
            index = QModelIndex(group)
            self.setExpanded(index, not self.isExpanded(index))

    def _cancel_mouse_press(self, *_):
        if not hasattr(self, "_geometry_timer"):
            return
        self._mouse_press_active = False
        self._pressed_group = QPersistentModelIndex()
        if self._geometry_pending:
            self._schedule_geometry()

    def _schedule_geometry(self):
        self._geometry_pending = True
        if self._mouse_press_active:
            return
        if not self._geometry_timer.isActive():
            self._geometry_timer.start(0)

    def _update_geometry(self):
        if self._mouse_press_active:
            # 물리 클릭의 누름/놓음 사이에 높이를 바꾸면 바깥 스크롤이 클릭 대상을 옮긴다.
            return
        self._geometry_pending = False
        self.resize_columns()
        self.fit_height()

    def _apply_expansion_depth(self, depth):
        for item in self.model()._group_items.values():
            index, level = self.model().indexFromItem(item), 1
            parent = index.parent()
            while parent.isValid():
                level += 1
                parent = parent.parent()
            self.setExpanded(index, depth == -1 or level < depth)
        self._collapsed_keys = {key for key, item in self.model()._group_items.items()
                                if not self.isExpanded(self.model().indexFromItem(item))}

    def set_expansion_depth(self, depth):
        """-1은 전체 펼침, 0은 전체 접기, 양수는 표시할 분류 단계 수다."""
        if type(depth) is not int or depth < -1:
            raise ValueError("표시할 계층 단계를 확인해 주세요.")
        self._expansion_depth = depth
        with QSignalBlocker(self):
            self._apply_expansion_depth(depth)
        self.fit_height()

    def set_groups(self, nodes, row_factory):
        self._cancel_mouse_press()
        current = self.currentIndex()
        equipment_id = current.data(Qt.ItemDataRole.UserRole)
        preferred_key = current.data(HeatmapListModel.KEY_ROLE)
        preferred_key = tuple(preferred_key) if preferred_key is not None else None
        with QSignalBlocker(self):
            rebuilt, changed_rows = self.model().set_groups(nodes, row_factory)
            if rebuilt:
                if self._expansion_depth is not None:
                    self._apply_expansion_depth(self._expansion_depth)
                else:
                    # 설비 말단 행까지 펼침을 검사하지 않고 분류 노드만 연다.
                    for key, item in self.model()._group_items.items():
                        if key not in self._collapsed_keys:
                            self.setExpanded(self.model().indexFromItem(item), True)
                self.select_equipment(equipment_id, preferred_key=preferred_key, reveal=False)
                # Qt의 현재 행 복원이 조상을 자동으로 열어도 사용자가 접은 분류는 유지한다.
                for key in self._collapsed_keys:
                    index = self.model().group_index(key)
                    if index.isValid():
                        self.setExpanded(index, False)
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
                key = self._row_key(index)
                if self._height_capped and key not in self._row_heights:
                    continue  # 상한을 채운 앞부분 뒤의 행은 패널 높이를 바꾸지 않는다.
                if visible and self._row_heights.get(key) != self._row_height(index):
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
        model = self.model()
        limits = {3: (140, 160), 4: (100, 120)} if self.compact else {
            2: (120, 160), 3: (150, 200), 4: (100, 120), 5: (110, 150)}
        columns = (0, *(column for column in limits if not self.isColumnHidden(column)))
        sample = model._row_items[0] if model._row_items else ()
        fonts = {column: model.indexFromItem(sample[column]).data(Qt.ItemDataRole.FontRole) if sample else self.font()
                 for column in columns}
        font_keys = {column: value.key() for column, value in fonts.items()}
        signature = (id(model), model._content_revision, self.compact, tuple(font_keys.items()),
                     self.font().key(), self.indentation(), self.rootIsDecorated())
        if signature == self._width_signature:
            return self._measured_widths
        metrics = {column: QFontMetrics(value) for column, value in fonts.items()}

        def text_width(text, column):
            key = font_keys[column], text
            cached = self._text_widths.get(key)
            if cached is not None:
                self._text_widths.move_to_end(key)
                return cached
            width = metrics[column].horizontalAdvance(text)
            self._text_widths[key] = width
            if len(self._text_widths) > 8192:
                self._text_widths.popitem(last=False)
            return width

        name_signature = id(model), model._name_revision, font_keys[0], self.indentation(), self.rootIsDecorated()
        if name_signature != self._name_width_signature:
            name_width = 220
            for items in model._row_items:
                item, depth = items[0], int(self.rootIsDecorated())
                parent = item.parent()
                while parent is not None:
                    depth += 1
                    parent = parent.parent()
                name_width = max(name_width, text_width(item.text(), 0) + depth * self.indentation() + 20)
            self._name_width, self._name_width_signature = name_width, name_signature
        widths = {0: self._name_width}
        for column, (minimum, maximum) in limits.items():
            if self.isColumnHidden(column):
                continue
            required = self.fontMetrics().horizontalAdvance(self.model().headings[column]) + 20
            for items in model._row_items:
                if required >= maximum:
                    break
                item = items[column]
                required = max(required, *(text_width(line, column) + 20 for line in (item.text().splitlines() or [""])))
            widths[column] = max(minimum, min(maximum, required))
        self._width_signature, self._measured_widths = signature, widths
        return widths

    def resize_columns(self):
        model = self.model()
        if self._mouse_press_active:
            self._schedule_geometry()
            return False
        if self._resizing_columns or model is None or model._rebuilding:
            return False
        changed = False
        self._resizing_columns = True
        try:
            for column, width in self._column_widths().items():
                if column == 0:
                    width = min(width, max(220, min(360, round(self.viewport().width() * 0.42))))
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
        if self._mouse_press_active:
            self._schedule_geometry()
            return
        if self._fitting_height or self.model() is None or self.model()._rebuilding:
            return
        self._fitting_height = True
        try:
            height, model = self.header().height() + 2 * self.frameWidth() + 4, self.model()
            self._row_heights = {}

            def add_rows(parent=QModelIndex()):
                nonlocal height
                for row in range(model.rowCount(parent)):
                    if height >= 800:
                        return
                    index = model.index(row, 0, parent)
                    row_height = self._row_height(index)
                    self._row_heights[self._row_key(index)] = row_height
                    height += row_height
                    if self.isExpanded(index):
                        add_rows(index)

            add_rows()
            self._height_capped = height >= 800
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
            self._schedule_geometry()

    def viewportEvent(self, event):
        if event.type() in (QEvent.Type.UngrabMouse, QEvent.Type.Hide, QEvent.Type.WindowDeactivate):
            self._cancel_mouse_press()
        result = super().viewportEvent(event)
        if event.type() == QEvent.Type.Resize:
            self._schedule_geometry()
        return result

    def changeEvent(self, event):
        if event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.EnabledChange):
            self._cancel_mouse_press()
        super().changeEvent(event)
