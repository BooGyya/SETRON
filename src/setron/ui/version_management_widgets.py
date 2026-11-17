"""버전관리의 공통 필드·탭·읽기 표. 기존 Qt와 앱 토큰을 재사용한다."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QSizePolicy, QTabBar, QVBoxLayout, QWidget

from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, label
from setron.ui.equipment import ReadTableModel, ignore_width, read_table
from setron.ui.import_widgets import import_combo_style
from setron.ui.theme import LINE, PRIMARY, SECONDARY
from setron.ui.versions import _fit


def tabs(names, accessible):
    result = QTabBar()
    for name in names:
        result.addTab(name)
    result.setExpanding(False)
    result.setDrawBase(True)
    result.setUsesScrollButtons(True)
    result.setAccessibleName(accessible)
    result.setStyleSheet("QTabBar::tab {background:transparent;color:#66708a;padding:10px 4px;margin-right:24px;"
                        "border:0;border-radius:0;border-bottom:2px solid transparent;font-size:15px;}"
                        f"QTabBar::tab:selected {{color:{PRIMARY};border-bottom:2px solid {PRIMARY};font-weight:600;}}")
    return result


def text_field(name, text="", *, readonly=False):
    result = QLineEdit(text)
    result.setAccessibleName(name)
    result.setReadOnly(readonly)
    result.setFixedHeight(38)
    result.setMinimumWidth(0)
    result.setStyleSheet(f"QLineEdit {{background:#f1f3f7;border:1px solid #e5eaf3;border-radius:8px;padding:8px 12px;}}"
                        f"QLineEdit:focus {{border:1px solid {PRIMARY};}}")
    return result


def combo(name, choices=()):
    result = ComboBox()
    result.setAccessibleName(name)
    result.setMinimumWidth(0)
    result.setProperty("queryField", True)
    result.setStyleSheet(import_combo_style())
    result.setFixedHeight(38)
    for key, caption in choices:
        result.addItem(caption, key)
    return result


def card(title="", *, outlined=True):
    result = Card(padding=20)
    result.setProperty("outlined", outlined)
    result.body.setSpacing(12)
    result.body.setAlignment(Qt.AlignmentFlag.AlignTop)
    if title:
        result.body.addWidget(label(title, 18, medium=True, wrap=True))
    return result


def table(headings, name, widths=None):
    model = ReadTableModel(headings)
    view = read_table(model, name, widths or tuple(180 for _ in headings))
    view.setStyleSheet(f"QTableView {{background:white;border:1px solid {LINE};border-radius:8px;gridline-color:transparent;}}"
                      "QTableView::item {padding:10px;border-bottom:1px solid #e9ecf1;}"
                      "QHeaderView::section {background:#f9fafb;border:0;border-bottom:1px solid #d5d7da;padding:10px;color:#5b6880;}")
    view.verticalHeader().setMinimumSectionSize(52)
    return model, view


def set_rows(model, view, rows, ids=()):
    model.set_rows(rows, ids)
    _fit(view)


def columns(*widgets):
    """좁은 창에서는 세로로 쌓고, 일반 창에서는 피그마의 좌우 패널 비율을 유지한다."""
    return SplitPanels(widgets)


class SplitPanels(QWidget):
    def __init__(self, widgets):
        super().__init__()
        self.panels = widgets
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(20)
        self.box.setAlignment(Qt.AlignmentFlag.AlignTop)
        for child in widgets:
            child.setMinimumWidth(0)
            child.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            self.box.addWidget(child, 1)
            self.box.setAlignment(child, Qt.AlignmentFlag.AlignTop)

    def resizeEvent(self, event):
        from PySide6.QtWidgets import QBoxLayout
        self.box.setDirection(QBoxLayout.Direction.TopToBottom if event.size().width() < 820 else QBoxLayout.Direction.LeftToRight)
        super().resizeEvent(event)


def actions(*buttons):
    result = QWidget()
    row = QHBoxLayout(result)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(12)
    for item in buttons:
        if item is None:
            row.addStretch()
        else:
            row.addWidget(item, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    if all(item is not None for item in buttons):
        row.addStretch()
    return result


def note(text=""):
    result = label(text, 13, color=SECONDARY, wrap=True)
    ignore_width(result)
    return result
