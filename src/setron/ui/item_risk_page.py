"""VER5 Item 관리의 위험 기준 저장 경계. 화면에 붙이지 않고 DB 연결(gateway)과 등록 팝업 호출만 맡는다."""

from PySide6.QtWidgets import QWidget

from setron.ui.item_risk_dialog import ItemRiskDialog


class ItemRiskPage(QWidget):
    def __init__(self):
        super().__init__()
        self.data, self.gateway, self.context_provider, self.submit = None, None, None, None
        self.submit_edit, self.submit_delete = None, None

    def configure(self, gateway, context_provider, submit, *, submit_edit=None, submit_delete=None):
        self.gateway, self.context_provider, self.submit = gateway, context_provider, submit
        self.submit_edit, self.submit_delete = submit_edit, submit_delete

    def set_data(self, data):
        self.data = data

    def open_add(self, checked=False, *, item_id=None, family_id=None):
        if self.data is None or self.gateway is None or self.submit is None:
            return False
        dialog = ItemRiskDialog(self.window(), self.data, self.gateway, self.context_provider, self.submit,
                                family_id=family_id, item_id=item_id)
        return bool(dialog.exec())

    def open_edit(self, parent, data, record):
        if self.gateway is None or self.submit_edit is None:
            return False
        dialog = ItemRiskDialog(parent, data, self.gateway, self.context_provider,
                                lambda values: self.submit_edit(record, values),
                                family_id=record.current["family_id"], item_id=record.current.get("item_id"),
                                edit_record=record)
        return bool(dialog.exec())
