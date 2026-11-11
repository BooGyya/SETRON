"""카드형과 같은 그룹 트리를 사용하는 히트맵 목록 표시."""

from PySide6.QtCore import Qt

from setron.ui.appearance import ui_color
from setron.ui.equipment import ReadTableModel
from setron.ui.theme import ACCENT_SOFT, PRIMARY, paint_font


class HeatmapListModel(ReadTableModel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.group_rows = frozenset()

    def set_rows(self, rows, ids=(), tones=(), *, tooltips=None, group_rows=()):
        self.group_rows = frozenset(group_rows)
        super().set_rows(rows, ids, tones, tooltips=tooltips)

    def flags(self, index):
        if index.isValid() and index.row() in self.group_rows:
            return Qt.ItemFlag.ItemIsEnabled
        return super().flags(index)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and index.row() in self.group_rows:
            if role == Qt.ItemDataRole.UserRole:
                return None
            if role == Qt.ItemDataRole.BackgroundRole:
                return ui_color(ACCENT_SOFT)
            if role == Qt.ItemDataRole.ForegroundRole:
                return ui_color(PRIMARY)
            if role == Qt.ItemDataRole.FontRole:
                return paint_font(14, medium=True)
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignCenter
        return super().data(index, role)


def grouped_list_sections(nodes, path=(), group_name=None):
    """카드의 leaf 순서와 설비군 범위를 목록에서도 그대로 사용한다."""
    for node in nodes:
        current_path = (*path, node.title)
        current_group = node.group_name or group_name
        if node.children:
            yield from grouped_list_sections(node.children, current_path, current_group)
        elif node.tiles:
            yield current_path, current_group, node.tiles
