"""사용자가 등록한 계층과 표시 이름을 데이터 관리 화면에서 편집한다."""

from copy import deepcopy
from dataclasses import dataclass, replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from setron.ui import data_management_page as module
from test_ui_data_management import EQUIPMENT, GROUPS, LINES, FakeGateway, page_with, settle
from test_ui_import_ver5 import own, until  # noqa: F401


@dataclass(frozen=True)
class ManagedLine:
    site: str
    line: str
    fab: str | None
    equipment_count: int
    registered: bool
    parent_line: str | None = None


class HierarchyGateway(FakeGateway):
    def __init__(self):
        super().__init__()
        self.options = {"categories": {"POLY": "건식 식각", "OXIDE": "산화막", "KCTC": "KCTC", "HPA": "HPA",
                                       "CAT1": "검사 장비"},
                        "areas": {"MEMORY": "메모리 공정", "FOUNDRY": "Foundry", "AREA1": "개발 공정"}}
        self.lines = [ManagedLine(r.site, r.line, r.fab, r.equipment_count, r.registered) for r in LINES]
        self.lines.append(ManagedLine("평택", "검사 A", "MEMORY", 1, True, "P1"))
        self.equipment = (*EQUIPMENT, replace(EQUIPMENT[2], equipment_id=9, code="EQ-SUB", line="검사 A"))

    def hierarchy_options(self):
        return deepcopy(self.options)

    def data_admin(self, method, *args, **kwargs):
        if method == "lines":
            return tuple(self.lines)
        if method == "equipment_overview":
            return GROUPS, self.equipment
        if method in {"add_line", "move_line", "rename_area", "rename_category", "add_area", "add_category",
                      "set_group_parent"}:
            self.done.setdefault(method, []).append((args, kwargs))
            if method == "add_line":
                site, line, fab = args
                self.lines.append(ManagedLine(site, line, fab, 0, True, kwargs.get("parent_line")))
                return line
            if method == "move_line":
                site, line, to_site, to_fab = args
                names = {line, *(r.line for r in self.lines if r.site == site and r.parent_line == line)}
                self.lines = [replace(r, site=to_site, fab=to_fab,
                                      parent_line=kwargs.get("parent_line") if r.line == line else r.parent_line)
                              if r.site == site and r.line in names else r for r in self.lines]
                self.equipment = tuple(replace(e, site=to_site) if e.site == site and e.line in names else e
                                       for e in self.equipment)
                return 2
            if method == "set_group_parent":
                return 1
            kind = "areas" if method.endswith("area") else "categories"
            if method.startswith("rename"):
                self.options[kind][args[0]] = args[1]
                return args[1]
            self.options[kind]["NEW"] = args[0]
            return "NEW"
        return super().data_admin(method, *args, **kwargs)


def location_page(own):
    gateway = HierarchyGateway()
    page = page_with(own, gateway)
    page.select_status(2)
    settle(page)
    return page, page.location_panel, gateway


def test_arbitrary_subline_is_nested_and_parent_details_include_descendants(own):
    page, panel, _ = location_page(own)
    panel._select(("line", "평택", "P1"))
    parent = panel.tree.currentItem()
    assert parent.parent().text(0) == "메모리 공정"
    assert parent.childCount() == 1 and parent.child(0).text(0) == "검사 A"
    assert parent.text(1) == "2대" and panel.equipment_table.rowCount() == 2
    panel.tree.setCurrentItem(parent.child(0))
    assert panel.equipment_table.rowCount() == 1 and panel.equipment_table.item(0, 0).text() == "EQ-SUB"
    assert not panel.add_subline_button.isEnabled() and panel.move_button.isEnabled()
    assert not panel.fab_button.isEnabled() and not panel.change_fab()
    assert "상위 Line" in panel.last_note


def test_add_subline_sends_explicit_parent_without_name_inference(own, monkeypatch):
    page, panel, gateway = location_page(own)
    panel._select(("line", "평택", "P1"))
    monkeypatch.setattr(module.QInputDialog, "getText", staticmethod(lambda *a, **k: ("신규 검사", True)))
    panel.add_subline_button.trigger()
    settle(page)
    assert gateway.done["add_line"] == [(("평택", "신규 검사", "MEMORY"), {"parent_line": "P1"})]
    assert panel.selection()[:3] == ("line", "평택", "신규 검사")
    assert panel.tree.currentItem().parent().text(0) == "P1"


def test_parent_rename_preview_keeps_children_and_undo_restores_tree(own):
    page, panel, _ = location_page(own)
    panel._select(("line", "평택", "P1"))
    panel.tree.currentItem().setText(0, "생산 1")
    until(lambda: bool(panel.ops))
    panel._select(("line", "평택", "검사 A"))
    assert panel.tree.currentItem().parent().text(0) == "생산 1"
    assert panel.undo()
    panel._select(("line", "평택", "검사 A"))
    assert panel.tree.currentItem().parent().text(0) == "P1"


def test_parent_cannot_be_merged_into_its_child_in_preview(own):
    page, panel, _ = location_page(own)
    panel._select(("line", "평택", "P1"))
    panel.tree.currentItem().setText(0, "검사 A")
    until(lambda: bool(panel.last_note))
    assert not panel.ops and "합칠 수 없습니다" in panel.last_note
    panel._select(("line", "평택", "P1"))
    assert panel.tree.currentItem().child(0).text(0) == "검사 A"


def test_move_line_sends_new_site_area_and_parent_and_reloads(own, monkeypatch):
    page, panel, gateway = location_page(own)
    panel._select(("line", "평택", "검사 A"))

    class Accept:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return "화성", "AREA1", None

    monkeypatch.setattr(module, "MoveLineDialog", Accept)
    assert panel.move_line()
    settle(page)
    assert gateway.done["move_line"] == [(("평택", "검사 A", "화성", "AREA1"), {"parent_line": None})]
    assert panel.selection() == ("line", "화성", "검사 A", "AREA1")
    assert "개발 공정" in panel.detail_title.text()


def test_classification_labels_edit_using_stable_codes_and_builtin_group_names(own):
    gateway = HierarchyGateway()
    page = page_with(own, gateway)
    page.select_status(1)
    settle(page)
    panel = page.equipment_panel
    table = panel.group_table
    row = next(r for r, group in enumerate(panel.ordered) if group.group_id == 1)
    assert table.item(row, 0).toolTip() == "건식 식각"
    assert table.item(row, 1).flags() & Qt.ItemFlag.ItemIsEditable
    table.item(row, 0).setText("검사 장비")
    assert panel.pending[1]["category"] == "CAT1"
    assert panel.save()
    settle(page)
    assert gateway.done["update_groups"][-1] == (([(1, None, "CAT1")],), {})


def test_registry_rename_preserves_area_code_and_refreshes_display(own):
    page, panel, gateway = location_page(own)
    dialog = own(module.RegistryDialog(panel, "area"))
    dialog.choices.setCurrentIndex(dialog.choices.findData("MEMORY"))
    dialog.name.setText("양산 공정")
    assert dialog._apply("rename")
    settle(page)
    assert gateway.done["rename_area"] == [(("MEMORY", "양산 공정"), {})]
    assert page.area_labels["MEMORY"] == "양산 공정"
    panel._select(("line", "평택", "검사 A"))
    assert "양산 공정" in panel.detail_title.text()


def test_custom_area_can_be_selected_in_sheet_and_saved_with_new_line(own):
    gateway = HierarchyGateway()
    page = page_with(own, gateway)
    page.enter()
    settle(page)
    sheet = page.sheet_panel
    assert sheet.add_row()
    row = sheet.table.rowCount() - 1
    for column, value in ((0, "EQ-NEW"), (1, "새 검사 라인"), (3, "M-C4"), (5, "개발 공정"), (6, "v2")):
        sheet.table.item(row, column).setText(value)
    assert sheet.new_rows[-1]["values"]["production_area"] == "AREA1"
    assert sheet.table.item(row, 5).text() == "개발 공정"
    assert sheet.save()
    settle(page)
    assert gateway.done["add_line"][-1] == (("화성", "새 검사 라인", "AREA1"), {})
    assert gateway.done["apply_rows"][-1][0][1][0][0]["production_area"] == "AREA1"


def test_line_with_children_cannot_be_nested_a_level_deeper_in_move_dialog(own):
    page, panel, gateway = location_page(own)
    dialog = own(module.MoveLineDialog(panel, panel.site_names(), page.area_labels, panel.line_rows,
                                      site="평택", line="P1", fab="MEMORY"))
    assert dialog.parent_line.count() == 1 and dialog.parent_line.currentData() is None


def test_hpa_display_name_can_be_renamed_while_hpa_is_not_a_new_group_choice(own):
    gateway = HierarchyGateway()
    page = page_with(own, gateway)
    page.select_status(1)
    settle(page)
    panel = page.equipment_panel
    dialog = own(module.RegistryDialog(panel, "category"))
    dialog.choices.setCurrentIndex(dialog.choices.findData("HPA"))
    dialog.name.setText("분석 제외 설비")
    assert dialog._apply("rename")
    settle(page)
    assert gateway.done["rename_category"] == [(("HPA", "분석 제외 설비"), {})]
    group_dialog = own(module.AddGroupDialog(panel, panel.groups))
    assert group_dialog.category.findData("HPA") == -1


def test_location_commands_remain_visible_without_overlap_in_narrow_tree_card(own):
    page, panel, _ = location_page(own)
    page.resize(1100, 900)
    card = panel.tree.parentWidget()
    card.setMaximumWidth(320)
    until(lambda: card.width() <= 320)
    for control in (panel.areas_button, panel.add_button, panel.delete_button,
                    panel.undo_button, panel.redo_button, panel.save_button):
        assert control.isVisible() and card.rect().contains(control.geometry())
        assert not control.geometry().intersects(panel.tree.geometry())
    footer = (panel.delete_button, panel.undo_button, panel.redo_button, panel.save_button)
    for index, control in enumerate(footer):
        assert control.geometry().top() > panel.tree.geometry().bottom()
        assert all(not control.geometry().intersects(other.geometry()) for other in footer[index + 1:])
    assert panel.add_button.menu() is panel.additions_menu


def test_location_actions_explain_scope_and_pending_changes_lock_other_commands(own):
    page, panel, _ = location_page(own)
    panel._select(("site", "평택"))
    assert panel.context_actions.isHidden()
    panel._select(("line", "평택", "P1"))
    assert panel.move_button.isVisible() and panel.fab_button.isVisible()
    assert not panel.delete_button.isEnabled() and "하위" in panel.delete_button.toolTip()
    panel._select(("line", "평택", "검사 A"))
    assert panel.move_button.isVisible() and not panel.fab_button.isVisible()
    assert not panel.add_subline_button.isEnabled() and "상위 Line" in panel.add_subline_button.toolTip()
    panel.tree.currentItem().setText(0, "검사 B")
    until(lambda: bool(panel.ops))
    assert panel.save_button.isEnabled() and panel.undo_button.isEnabled()
    assert not panel.add_button.isEnabled() and "저장" in panel.add_button.toolTip()
    assert not panel.areas_button.isEnabled() and "저장 전 변경" in panel.location_hint.text()
