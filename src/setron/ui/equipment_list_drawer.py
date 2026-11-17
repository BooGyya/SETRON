"""대시보드에서 같은 패널로 설비 목록과 상세를 확인한다."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from setron.ui.equipment_drawer import EquipmentDrawer


class EquipmentListDrawer(EquipmentDrawer):
    back_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent, object_name="equipmentListDrawer", accessible_name="설비 목록 패널")
        self.list_equipment = None
        self.back_button = self._header_button("arrow-left.svg", "패널의 설비 목록으로 돌아가기")
        self.back_button.clicked.connect(self.back_requested)
        self.header.insertWidget(self.header.indexOf(self.close_button) + 1, self.back_button)
        self.list_scroll = QScrollArea()
        self.list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.panel_layout.addWidget(self.list_scroll, 1)
        content = QWidget()
        self.list_body = QVBoxLayout(content)
        self.list_body.setContentsMargins(0, 0, 0, 0)
        self.list_body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.list_scroll.setWidget(content)
        self.panel_hidden.connect(self._release_list)
        self.show_list()

    def open_list(self, equipment, *, return_focus=None):
        if self.list_equipment is not None and self.list_equipment is not equipment:
            self.close_panel(restore_focus=False)
        self.list_equipment, self.return_focus = equipment, return_focus
        equipment.mount_list(self.list_body)
        self.show_list()
        self.show_panel()
        self.list_scroll.verticalScrollBar().setValue(0)

    def show_detail(self, request, detail, rows):
        if not self.is_open or self.list_equipment is None:
            return
        self.list_scroll.hide()
        self.scroll.show()
        self.back_button.show()
        super().open_detail(request, detail, rows, return_focus=self.return_focus)

    def show_list(self):
        self.clear_detail()
        self.title.setText("설비 목록")
        self.back_button.hide()
        self.scroll.hide()
        self.list_scroll.show()
        if self.is_open:
            self.panel.setFocus(Qt.FocusReason.OtherFocusReason)

    def _release_list(self):
        if self.list_equipment is not None:
            self.list_equipment.restore_list()
            self.list_equipment = None
