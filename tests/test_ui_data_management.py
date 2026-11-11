"""데이터 관리 화면(11-10 개편, 11-11 2·3·6차 수정) 프론트 검증. DB 대신 가짜 게이트웨이를 쓴다.

11-11 6차: 현황표는 DB에 들어간 값을 가져오기 3단계 항목(호기 · Line · 설비군 · 모델명 · 메모리/파운드리 · SW 버전)으로
보이고, 고친 뒤 [저장]으로 한꺼번에 저장한다(설비군 변경은 regroup). [원본 보기]는 두지 않는다(11-11 요청).
설비군 이름 변경 · 삭제 · 독립 보기, Site · 라인 등록과 메모리/파운드리 변경도 확인한다.
"""

from datetime import date, datetime
from types import SimpleNamespace

from PySide6.QtWidgets import QDialog, QLabel, QMessageBox

from setron.features.data_admin import (
    EquipmentRow, GroupRow, HistoryEntry, LineRow, ReleaseFamily, ReleaseRow, SheetRow, SheetRows, StatusSheet,
)
from setron.ui import data_management_page as module
from setron.ui.app import create_window
from setron.ui.data_management_page import DataManagementPage
from test_ui_import_ver5 import own, until  # noqa: F401

SHEET = StatusSheet(7, date(2026, 11, 1), "화성", "현황 2026-11-01", "마스터.xlsx", datetime(2026, 11, 10, 9, 38), 3)


def _row(sid, eid, vid, code, line, version, *, area="MEMORY", pm="", cells=None):
    cells = cells or {}
    return SheetRow(sid, eid, vid, code, line, 1, "M-C4", "M-C4", area, version, "C4", pm, bool(pm),
                    {k: a for k, (a, _) in cells.items()}, {k: v for k, (_, v) in cells.items()})


# DB 순서는 EC2(15L) · EC1(13L) · EC4(17L, PM 있음)라 Line 순 정렬을 확인할 수 있다.
SHEET_ROWS = SheetRows(
    7, "현황 2026-11-01", "마스터.xlsx", "화성", date(2026, 11, 1),
    (_row(12, 6, 22, "EC2", "15L", "v1.3", cells={"equipment_code": ("D3", "EC2"), "version_raw": ("E3", "v1.3")}),
     _row(11, 5, 21, "EC1", "13L", "v1.2", cells={"equipment_code": ("D4", "EC1"), "version_raw": ("E4", "v1.2")}),
     _row(14, 8, 24, "EC4", "17L", "v1.1", pm="PM1")),
    ((1, "M-C4", "POLY"), (2, "M-C4 DFB", "POLY"), (3, "M-O2", "OXIDE")), ("13L", "15L", "17L"))
GROUPS = (GroupRow(1, "M-C4", "POLY", None, None, 2), GroupRow(2, "M-C4 DFB", "POLY", 1, "M-C4", 1),
          GroupRow(4, "C7", "POLY", None, None, 0), GroupRow(3, "M-O2", "OXIDE", None, None, 1))
EQUIPMENT = (EquipmentRow(5, "EC1", (1,), "M-C4", "M-C4", "화성", "13L", date(2026, 11, 1), 3),
             EquipmentRow(6, "EC2", (2,), "M-C4 DFB", "M-C4 DFB", "화성", "15L", date(2026, 11, 1), 3),
             EquipmentRow(7, "EO1", (3,), "M-O2", "M-O2", "평택", "P1", date(2026, 11, 1), 1),
             EquipmentRow(8, "OLD", (), "미지정", "", "", "", None, 0))
LINES = (LineRow("평택", "P1", "MEMORY", 1, False), LineRow("평택", "S3", "FOUNDRY", 0, True),
         LineRow("화성", "13L", "MEMORY", 1, False), LineRow("화성", "15L", "MEMORY", 1, False),
         LineRow("화성", "19L", None, 0, True))
FAMILIES = (ReleaseFamily(10, "C4", 1, 2, ((2, "M-C4 DFB"),)), ReleaseFamily(11, "O2", 3, 0, ()))
ROWS = (ReleaseRow(100, "v4.60.1.1", "2026-04-06", 500, "1", "Recipe 확인", "M-C4", (1,), None, 4, True),
        ReleaseRow(100, "v4.60.1.1", "2026-04-06", 501, "2", "보호동작 수정", "M-C4, M-C4 DFB", (1, 2), "결함", 5, False),
        ReleaseRow(101, "v4.60.1.2", "2026-05-06", None, "", "", "", (), None, 7, True))
HISTORY = (HistoryEntry(40, datetime(2026, 11, 10, 14, 2), "수정", "equipment_version", {"equipment_version_id": 21}, "EC1",
                        "SW Ver", "v1.1", "v1.2", "마스터.xlsx", "현황 2026-11-01", "D3"),
           HistoryEntry(39, datetime(2026, 1, 1, 9, 0), "등록", "equipment_group", {}, "M-C7", "설비군", "", "새로 등록",
                        None, None, None),
           HistoryEntry(38, datetime(2026, 1, 1, 8, 0), "삭제", "equipment_snapshot", {"snapshot_id": 13}, "EC3",
                        "현황표 행", "화성 · 17L · 2026-11-01", "삭제", "마스터.xlsx", "현황 2026-11-01", "B5~E5"))


class FakeGateway:
    def __init__(self):
        self.calls, self.saved, self.created, self.sites = [], [], [], ["기흥"]
        self.done = {}  # 메서드 → 받은 인자 목록

    def data_admin(self, method, *args, **kwargs):
        self.calls.append(method)
        self.done.setdefault(method, []).append((args, kwargs))
        if method == "create_group":
            self.created.append(args)
            return GroupRow(9, args[0], args[1], args[2], None, 0)
        results = {"create_sheet": 8, "add_site": args[0] if args else None,
                   "apply_rows": {"added": [77] if len(args) > 1 and args[1] else [],
                                  "journal": 900 if len(args) > 2 and args[2] else None,
                                  "regroup": [(sid, 1) for sid, _ in args[3]] if len(args) > 3 else []},
                   "undo_rows": None, "delete_release_rows": "v4.60.1.1", "reorder_items": None,
                   "apply_release": {"versions": [102]}, "undo_release": None,
                   "add_release_version": 102, "add_release_item": 502, "sites": tuple(self.sites),
                   "status_sheets": (SHEET,), "sheet_rows": SHEET_ROWS,
                   "equipment_overview": (GROUPS, EQUIPMENT), "lines": LINES,
                   "rename_group": 0, "delete_group": None, "set_independent": None, "add_line": "P4",
                   "rename_site": 3, "rename_line": 2, "set_line_fab": 1, "delete_site": None, "delete_line": None,
                   "release_families": FAMILIES, "release_rows": ROWS, "history": HISTORY}
        if method == "add_site":
            self.sites.append(args[0])
        return results[method]

    def get(self, table, key):
        return SimpleNamespace(revision=f"rev-{table}-{sorted(key.items())}")

    def save(self, edits, context=None):
        self.saved.append((edits, context))
        return SimpleNamespace(records=(), audit_batch_id=41)


class Store(dict):
    """열 폭 기억용 가짜 설정 (사용자 PC 설정을 건드리지 않는다)."""

    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


def page_with(own, gateway=None, store=None):
    page = own(DataManagementPage(gateway or FakeGateway(), context_provider=lambda: {"n": 1},
                                  column_store=store if store is not None else Store()))
    page.resize(1400, 900)
    page.show()
    return page


def settle(page):
    until(lambda: not page.saving and not any(p.request.busy for p in page.panels))


def answer(monkeypatch, reply=QMessageBox.StandardButton.Yes):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[2]) or reply))
    return asked


def sheet_page(own, gateway=None):
    page = page_with(own, gateway)
    page.enter()
    settle(page)
    return page, page.sheet_panel


def cell(sheet, row, column):
    return sheet.table.item(row, column)


def codes(sheet):
    return [cell(sheet, r, 0).text() for r in range(sheet.table.rowCount())]


def edits_by_table(gateway, index=0):
    return {e.table: (e.key, e.changes) for e in gateway.saved[index][0]}


def test_three_fixed_tabs_and_status_subtabs_load_lazily(own):
    gateway = FakeGateway()
    page = page_with(own, gateway)
    assert [page.tabs.tabText(i) for i in range(3)] == ["설비현황", "릴리즈노트", "변경 이력"]
    assert [b.text() for b in page.status_buttons] == ["현황표", "설비", "위치", "가져온 엑셀 삭제"]
    page.enter()
    settle(page)
    assert gateway.calls == ["status_sheets", "sheet_rows"]  # 처음에는 보이는 현황표만 읽는다


def test_sheet_shows_db_values_in_import_columns_sorted_by_line_group_code(own):
    page, sheet = sheet_page(own)
    assert "화성 · 기준일 2026-11-01" in sheet.source.text() and "마스터.xlsx" in sheet.source.text()
    assert [sheet.table.horizontalHeaderItem(c).text() for c in range(7)] == [
        "설비 호기", "Line", "설비군", "모델명", "메모리/파운드리", "SW 버전", "PM"]
    assert codes(sheet) == ["EC1", "EC2", "EC4"]  # Line → 설비군 → 호기 (DB 순서와 다름)
    assert [cell(sheet, 0, c).text() for c in range(6)] == ["EC1", "13L", "M-C4", "M-C4", "메모리", "v1.2"]
    assert "원본 D4 · EC1" in cell(sheet, 0, 0).toolTip() and "원본 셀 없음" in cell(sheet, 0, 1).toolTip()
    assert not sheet.table.isColumnHidden(6) and cell(sheet, 2, 6).text() == "PM1"
    editable = module.Qt.ItemFlag.ItemIsEditable
    assert not cell(sheet, 2, 2).flags() & editable and not cell(sheet, 2, 5).flags() & editable  # PM 설비
    assert "버전 관리에서" in cell(sheet, 2, 5).toolTip() and cell(sheet, 2, 1).flags() & editable
    assert not hasattr(sheet, "raw_button") and not [b for b in sheet.findChildren(type(sheet.save_button))
                                                     if b.text() == "원본 보기"]  # [원본 보기]는 두지 않는다 (11-11)
    assert [b.text() for b in (sheet.undo_button, sheet.add_button, sheet.delete_button, sheet.save_button)] \
        == ["되돌리기", "＋ 행 추가", "선택 행 삭제", "저장"]
    assert not sheet.save_button.isEnabled() and not sheet.undo_button.isEnabled() and sheet.add_button.isEnabled()
    assert sheet.table.horizontalHeader().sectionResizeMode(0) == module.QHeaderView.ResizeMode.Interactive


def test_edits_regroup_adds_and_deletes_wait_for_save_then_go_together(own, monkeypatch):
    asked = answer(monkeypatch)
    gateway = FakeGateway()
    saved = []
    page, sheet = sheet_page(own, gateway)
    page.saved.connect(saved.append)
    cell(sheet, 0, 5).setText("v1.2a")      # EC1 SW 버전
    cell(sheet, 0, 1).setText("19L")        # EC1 Line
    cell(sheet, 0, 4).setText("파운드리")    # EC1 메모리/파운드리
    cell(sheet, 0, 2).setText("M-O2")       # EC1 설비군 → regroup
    assert cell(sheet, 0, 5).background().color() == module.SAVING and "v1.2 → v1.2a" in cell(sheet, 0, 5).toolTip()
    sheet.table.selectRow(1)                # EC2 삭제 표시
    assert sheet.delete_selected()
    assert cell(sheet, 1, 0).background().color() == module.DELETE_ROW and "삭제 예정" in cell(sheet, 1, 0).toolTip()
    assert sheet.add_row()                  # 새 행
    new = sheet.table.rowCount() - 1
    for column, value in ((0, "EC9"), (1, "19L"), (2, "M-C4"), (4, "메모리"), (5, "v2.0")):
        cell(sheet, new, column).setText(value)
    assert not gateway.saved and "apply_rows" not in gateway.calls  # 저장 전에는 DB에 아무것도 쓰지 않는다
    assert sheet.save_button.text() == "저장 (6건)"
    assert sheet.save()
    settle(page)
    assert len(asked) == 1 and "EC2" in asked[0]    # 행 삭제만 한 번 묻는다
    edits = edits_by_table(gateway)
    assert edits["equipment_version"] == ({"equipment_version_id": 21}, {"version_raw": "v1.2a", "release_version_id": None})
    assert edits["equipment_snapshot"] == ({"snapshot_id": 11}, {"line": "19L", "production_area": "FOUNDRY"})
    assert gateway.done["apply_rows"] == [((7, [({"equipment_code": "EC9", "line": "19L", "group_name": "M-C4", "model_name": "",
                                                   "production_area": "MEMORY", "version_raw": "v2.0"}, None)], (12,), [(11, 3)]), {})]
    step = sheet.steps[-1]
    assert step[0] == "saved" and step[1]["regroup"] == [(11, 1)] and step[1]["added"] == [77] and step[1]["journal"] == 900
    assert not sheet.pending and saved and "4칸 수정 · 1행 추가 · 1행 삭제" in sheet.notice.text()


def test_one_value_per_equipment_is_shared_and_edit_on_deleted_row_is_dropped(own, monkeypatch):
    answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    cell(sheet, 1, 5).setText("v9")          # EC2 SW 버전
    sheet.table.selectRow(1)
    sheet.delete_selected()
    sheet.save()
    settle(page)
    assert not gateway.saved and gateway.done["apply_rows"][0][0][2] == (12,)


def test_bad_values_are_rejected_and_restored(own):
    page, sheet = sheet_page(own)
    cell(sheet, 0, 2).setText("없는설비군")
    assert "설비 탭에서 먼저 추가" in sheet.notice.text() and cell(sheet, 0, 2).text() == "M-C4" and not sheet.edits
    cell(sheet, 0, 0).setText("")
    assert "비울 수 없습니다" in sheet.notice.text() and cell(sheet, 0, 0).text() == "EC1" and not sheet.edits


def test_undo_goes_back_one_step_then_undoes_the_whole_last_save(own, monkeypatch):
    answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    cell(sheet, 0, 5).setText("v9")
    cell(sheet, 0, 5).setText("v10")
    assert sheet.undo() and cell(sheet, 0, 5).text() == "v9" and sheet.edits == {("version", 21): "v9"}
    sheet.table.selectRow(1)
    sheet.delete_selected()
    assert sheet.undo() and not sheet.deletes and cell(sheet, 1, 0).background().color() != module.DELETE_ROW
    sheet.add_row()
    cell(sheet, 3, 0).setText("EC9")
    assert sheet.undo() and sheet.new_rows[0]["values"]["equipment_code"] == ""
    assert sheet.undo() and not sheet.new_rows and sheet.table.rowCount() == 3
    cell(sheet, 0, 2).setText("M-O2")
    sheet.save()
    settle(page)
    assert sheet.undo()        # 마지막 저장 전체(칸 수정 · 설비군 변경)를 되돌린다
    settle(page)
    assert gateway.done["undo_rows"] == [((7, [], None, [(11, 1)]), {})]
    assert gateway.saved[-1][0][0].changes == {"version_raw": "v1.2", "release_version_id": None}
    assert not sheet.steps and "마지막 저장을 되돌렸습니다" in sheet.notice.text()


def test_new_row_needs_required_cells_and_unknown_group_is_registered_on_save(own, monkeypatch):
    asked = answer(monkeypatch)
    monkeypatch.setattr(module.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("OXIDE", True)))
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    assert sheet.add_row()
    assert sheet.table.verticalHeaderItem(3).text() == "＋" and cell(sheet, 3, 0).background().color() == module.NEW_ROW
    cell(sheet, 3, 0).setText("EC9")
    assert not sheet.save() and "필수 칸" in sheet.notice.text()
    cell(sheet, 3, 2).setText("C8")
    cell(sheet, 3, 5).setText("v2.0")
    assert sheet.save()
    settle(page)
    assert "C8" in asked[0] and gateway.created == [("C8", "OXIDE", None)] and "새 설비군 C8 등록" in sheet.notice.text()


def test_sort_follows_header_clicks(own):
    page, sheet = sheet_page(own)
    assert "Line → 설비군 → 설비 호기" in sheet.sort_label.text() and not sheet.sort_reset.isVisibleTo(sheet)
    sheet._header_clicked(5)   # SW 버전 오름차순
    assert codes(sheet) == ["EC4", "EC1", "EC2"] and "SW 버전 오름차순" in sheet.sort_label.text()
    assert sheet.table.horizontalHeaderItem(5).text() == "SW 버전 ▲"
    sheet._header_clicked(5)   # 내림차순
    assert codes(sheet) == ["EC2", "EC1", "EC4"] and sheet.sort_reset.isVisibleTo(sheet)
    sheet._header_clicked(5)   # 기본 정렬로
    assert codes(sheet) == ["EC1", "EC2", "EC4"] and sheet.sort is None


def test_failed_save_keeps_changes_and_reports_reason(own):
    gateway = FakeGateway()
    gateway.save = lambda edits, context=None: (_ for _ in ()).throw(ValueError("검토 후 자료가 바뀌었습니다"))
    failed = []
    page, sheet = sheet_page(own, gateway)
    page.save_failed.connect(lambda: failed.append(True))
    cell(sheet, 0, 5).setText("v9")
    sheet.save()
    settle(page)
    assert failed and "바뀌었습니다" in sheet.notice.text() and sheet.edits == {("version", 21): "v9"}
    assert sheet.save_button.isEnabled()


def test_equipment_groups_show_category_once_per_group_and_list_model_site_line(own, monkeypatch):
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_status(1)
    settle(page)
    panel = page.equipment_panel
    table = panel.group_table
    assert [table.item(r, 0).text() for r in range(table.rowCount())] == ["POLY", "", "", "OXIDE"]  # 분류는 첫 줄에만
    assert [table.item(r, 1).text().strip() for r in range(table.rowCount())] == ["C7", "M-C4", "↳ M-C4 DFB", "M-O2"]
    assert table.item(1, 0).toolTip() == "POLY" and table.item(0, 0).font().bold()
    headers = [panel.equipment_table.horizontalHeaderItem(c).text() for c in range(panel.equipment_table.columnCount())]
    assert headers == ["설비 ID", "모델", "Site", "Line", "설치 기록", "최근 기준일"]
    table.selectRow(2)
    assert panel.equipment_table.rowCount() == 1 and panel.equipment_table.item(0, 0).text() == "EC2"

    class Accept:
        def __init__(self, *_):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return "M-C7", "POLY", None

    monkeypatch.setattr(module, "AddGroupDialog", Accept)
    added = []
    page.group_added.connect(added.append)
    assert panel.add_group()
    settle(page)
    assert gateway.created == [("M-C7", "POLY", None)] and added and "SW 계열도 만들었고" in panel.notice.text()


def test_group_rename_delete_and_independent_view(own, monkeypatch):
    answer(monkeypatch)
    monkeypatch.setattr(module.QInputDialog, "getText", staticmethod(lambda *a, **k: ("C7X", True)))
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_status(1)
    settle(page)
    panel = page.equipment_panel
    panel.group_table.selectRow(0)           # C7 (기본 설비군)
    assert not panel.independent_button.isEnabled()
    assert panel.rename_group()
    settle(page)
    assert gateway.done["rename_group"] == [((4, "C7X"), {})] and "'C7' → 'C7X'" in panel.notice.text()
    panel.group_table.selectRow(0)
    assert panel.delete_group()
    settle(page)
    assert gateway.done["delete_group"] == [((4,), {})]
    panel.group_table.selectRow(2)           # M-C4 DFB (개조형)
    assert panel.independent_button.isEnabled() and panel.independent_button.text() == "독립 설비군으로 보기"
    assert panel.toggle_independent()
    settle(page)
    assert gateway.done["set_independent"] == [((2, True), {})] and "독립 설비군으로 봅니다" in panel.notice.text()


def test_location_tree_groups_lines_by_memory_foundry_and_edits_them(own, monkeypatch):
    asked = answer(monkeypatch)
    monkeypatch.setattr(module.QInputDialog, "getText", staticmethod(lambda *a, **k: ("P1A", True)))
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_status(2)
    settle(page)
    panel = page.location_panel
    tree = panel.tree
    assert [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())] == [
        "기흥  (등록 · 설비 없음)", "평택  · 1대", "화성  · 2대"]
    pyeongtaek = tree.topLevelItem(1)
    assert [pyeongtaek.child(i).text(0) for i in range(pyeongtaek.childCount())] == ["메모리", "파운드리"]
    assert pyeongtaek.child(1).child(0).text(0) == "S3  · 0대  (등록)"
    tree.setCurrentItem(pyeongtaek.child(0).child(0))   # 평택 · 메모리 · P1
    assert panel.fab_button.text() == "파운드리로 변경" and panel.equipment_table.rowCount() == 1
    assert panel.change_fab()
    settle(page)
    assert "설비 1대" in asked[-1] and gateway.done["set_line_fab"] == [(("평택", "P1", "FOUNDRY"), {})]
    tree.setCurrentItem(tree.topLevelItem(1).child(0).child(0))
    assert panel.rename()
    settle(page)
    assert gateway.done["rename_line"] == [(("평택", "P1", "P1A"), {})] and "설비 현황 2건" in panel.notice.text()
    tree.setCurrentItem(tree.topLevelItem(1).child(1).child(0))  # 평택 · 파운드리 · S3
    assert panel.delete()
    settle(page)
    assert gateway.done["delete_line"] == [(("평택", "S3"), {})]

    class Accept:
        def __init__(self, *_, **__):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return "평택", "P4", "MEMORY"

    monkeypatch.setattr(module, "AddLineDialog", Accept)
    assert panel.add_line()
    settle(page)
    assert gateway.done["add_line"] == [(("평택", "P4", "MEMORY"), {})] and "라인 'P4'" in panel.notice.text()
    hwaseong = tree.topLevelItem(2)
    assert hwaseong.child(2).text(0) == "메모리/파운드리 모름" and hwaseong.child(2).isExpanded()
    tree.setCurrentItem(hwaseong.child(2).child(0))           # 화성 · 모름 · 19L → 어느 쪽인지 고른다
    assert panel.fab_button.text() == "메모리/파운드리 지정"
    monkeypatch.setattr(module.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("파운드리", True)))
    assert panel.change_fab()
    settle(page)
    assert gateway.done["set_line_fab"][-1] == (("화성", "19L", "FOUNDRY"), {})


def test_add_line_dialog_needs_a_name_and_a_site(own):
    dialog = own(module.AddLineDialog(None, ["평택", "화성"], site="화성", fab="FOUNDRY"))
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.name.setText(" 16L ")
    dialog._accept()
    assert dialog.values() == ("화성", "16L", "FOUNDRY")


def test_add_group_dialog_requires_same_category_for_variant(own):
    dialog = own(module.AddGroupDialog(None, GROUPS))
    dialog.name.setText("M-C4 X")
    dialog.category.setCurrentIndex(dialog.category.findData("OXIDE"))
    dialog.parent_combo.setCurrentIndex(dialog.parent_combo.findData(1))
    dialog._accept()
    assert "같은 분류" in dialog.error.text() and dialog.result() != QDialog.DialogCode.Accepted


def test_site_added_then_empty_sheet_is_created_and_opened(own, monkeypatch):
    answer(monkeypatch)
    monkeypatch.setattr(module.QInputDialog, "getText", staticmethod(
        lambda *a, **k: ("용인", True) if a[1] == "Site 추가" else ("2026-11-15", True)))
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_status(2)
    settle(page)
    assert page.location_panel.add_site()
    settle(page)
    assert gateway.done["add_site"] == [(("용인",), {})]
    assert gateway.done["create_sheet"] == [(("용인", date(2026, 11, 15)), {})]
    assert page.status_stack.currentIndex() == 0 and "빈 현황표를 만들었습니다" in page.sheet_panel.notice.text()


def test_release_changes_wait_for_save_then_go_together_and_undo(own, monkeypatch):
    asked = answer(monkeypatch)
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_tab(1)
    settle(page)
    panel = page.release_panel
    assert not hasattr(panel, "risk_button") and "위험" not in module.RELEASE_COLUMNS
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0).child(0))
    settle(page)
    assert [panel.table.verticalHeaderItem(r).text() for r in range(3)] == ["1", "2", "3"]  # 줄 번호는 1부터
    assert panel.table.item(1, 3).font().bold() and not panel.table.item(0, 3).font().bold()  # 개조형 적용 Item 강조
    panel.table.item(0, 3).setText("Recipe 확인 수정")          # 칸 수정 (저장 전, Item 500)
    assert panel.move_item(1, 0)                                # 2번 Item을 1번 자리로 (저장 전)
    assert [panel.table.item(r, 2).text() for r in range(2)] == ["1", "2"] and panel.table.item(0, 3).text() == "보호동작 수정"
    assert not panel.move_item(2, 0) and "같은 버전" in panel.notice.text()
    forms = iter([("v4.60.1.3", "2026-06-01"), ("새 기능", "M-C4")])

    class Form:
        def __init__(self, *_):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return next(forms)

    monkeypatch.setattr(module, "_FormDialog", Form)
    assert panel.add_version()                                  # 새 버전은 맨 위
    assert panel.table.item(0, 0).text() == "▼ v4.60.1.3" and panel.table.item(0, 0).background().color() == module.SAVING
    panel.table.selectRow(3)                                    # v4.60.1.2 줄
    assert panel.add_item()
    panel.table.selectRow(1)                                    # v4.60.1.1의 첫 Item(보호동작 수정, 501)
    assert panel.delete_item()
    assert panel.table.item(1, 3).background().color() == module.DELETE_ROW
    assert not gateway.saved and "apply_release" not in gateway.calls  # 저장 전에는 DB에 쓰지 않는다
    assert panel.save_button.text() == "저장 (5건)"
    assert panel.save()
    settle(page)
    assert len(asked) == 1 and "Item 1개" in asked[0]              # 삭제가 있을 때만 한 번 묻는다
    assert gateway.saved[0][0][0].table == "item" and gateway.saved[0][0][0].changes == {"content": "Recipe 확인 수정"}
    assert gateway.done["apply_release"] == [((10, {
        "add_versions": [{"temp": "new-version-1", "version_raw": "v4.60.1.3", "released_at": "2026-06-01"}],
        "add_items": [{"temp": "new-item-2", "release": 101, "content": "새 기능", "group_ids": [1]}],
        "orders": {100: [500]}, "delete_items": [(100, 501)], "delete_versions": []}), {})]
    assert panel.steps[-1][0] == "saved" and not panel.pending and "저장했습니다" in panel.notice.text()
    assert panel.undo()                                         # 마지막 저장 전체를 되돌린다
    settle(page)
    assert gateway.done["undo_release"] == [(({"versions": [102]},), {})]
    assert gateway.saved[-1][0][0].changes == {"content": "Recipe 확인"}
    assert not panel.steps and "되돌렸습니다" in panel.notice.text()


def test_release_pending_steps_undo_one_by_one(own):
    page = page_with(own)
    page.select_tab(1)
    settle(page)
    panel = page.release_panel
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    settle(page)
    panel.table.item(0, 3).setText("Recipe 확인 수정")
    panel.table.selectRow(2)
    panel.delete_version()
    assert panel.table.item(2, 0).background().color() == module.DELETE_ROW and panel.save_button.text() == "저장 (2건)"
    assert panel.undo() and not panel.deleted
    assert panel.undo() and not panel.edits and panel.table.item(0, 3).text() == "Recipe 확인"
    assert not panel.undo_button.isEnabled()


def test_release_bad_model_name_or_date_is_rejected_and_restored(own):
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_tab(1)
    settle(page)
    page.release_panel.tree.setCurrentItem(page.release_panel.tree.topLevelItem(0))
    settle(page)
    panel = page.release_panel
    panel.table.item(0, 4).setText("없는모델")
    assert "모르는 설비군" in panel.notice.text() and panel.table.item(0, 4).text() == "M-C4"
    panel.table.item(0, 1).setText("4월 7일")
    assert "날짜" in panel.notice.text() and panel.table.item(0, 1).text() == "2026-04-06"
    settle(page)
    assert not gateway.saved


def test_history_shows_basic_columns_and_detail_slides_in_from_the_right(own):
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_tab(2)
    settle(page)
    panel = page.history_panel
    assert [panel.table.horizontalHeaderItem(c).text() for c in range(panel.table.columnCount())] == [
        "수정 일시", "구분", "대상", "항목", "수정 전", "수정 후"]
    assert [panel.table.item(0, c).text() for c in range(6)] == [
        module.HistoryPanel._when(datetime(2026, 11, 10, 14, 2)), "수정", "EC1", "SW Ver", "v1.1", "v1.2"]
    assert module.HistoryPanel._when(datetime(datetime.now().year, 11, 10, 14, 2)) == "11-10 14:02"  # 올해는 짧게
    assert panel.count.text() == "3건" and not panel.drawer.is_open
    assert not [b for b in panel.findChildren(type(page.sheet_panel.save_button)) if b.text() == "새로 읽기"]
    panel.table.selectRow(0)
    assert panel.drawer.is_open and panel.detail_title.text() == "EC1 · SW Ver"
    assert "D3" in panel.detail.text() and "마스터.xlsx" in panel.detail.text() and "2026-11-10 14:02" in panel.detail.text()
    assert panel.before_value.text() == "v1.1" and panel.after_value.text() == "v1.2"
    page.select_tab(0)
    assert not panel.drawer.is_open  # 다른 탭으로 가면 닫힌다
    reads = gateway.calls.count("history")
    page.select_tab(2)
    settle(page)
    assert gateway.calls.count("history") == reads + 1  # 탭에 들어올 때마다 다시 읽는다
    panel.kind.setCurrentIndex(panel.kind.findText("설비군"))
    assert panel.table.rowCount() == 1 and panel.table.item(0, 2).text() == "M-C7"


def test_column_widths_are_resizable_and_remembered(own):
    store = Store({module.COLUMN_KEY + "history": [150, 60, 200, 160, 120, 110]})
    page = page_with(own, store=store)
    page.select_tab(2)
    settle(page)
    header = page.history_panel.table.horizontalHeader()
    assert header.sectionResizeMode(0) == module.QHeaderView.ResizeMode.Interactive
    assert [header.sectionSize(c) for c in range(3)] == [150, 60, 200]  # 기억한 폭
    for table in (page.equipment_panel.group_table, page.equipment_panel.equipment_table,
                  page.location_panel.equipment_table, page.release_panel.table):
        assert table.horizontalHeader().sectionResizeMode(0) == module.QHeaderView.ResizeMode.Interactive


def test_multiline_labels_get_full_height(own):
    page = page_with(own)
    page.resize(1400, 1400)  # 앱에서는 창 전체가 스크롤되므로 화면 최소 높이보다 크게 띄운다
    page.select_tab(1)
    info = page.release_panel.notice
    info.setText("첫 줄\n둘째 줄\n셋째 줄\n넷째 줄")
    until(lambda: info.height() >= info.heightForWidth(info.width()) > info.fontMetrics().height() * 3)
    assert all(not w.wordWrap() or w.sizePolicy().horizontalPolicy().name == "Ignored" for w in page.findChildren(QLabel))


def test_app_sidebar_has_single_data_menu_and_delete_moved_from_import(own):
    window = own(create_window())
    visible = [key for key, nav in window.nav.items() if nav.isVisible() or nav.isVisibleTo(window)]
    assert "data" in visible and "versions" not in visible
    assert window.navigate("data") and window.header.title.text() == "데이터 관리"
    page = window.pages["data"]
    assert page.unavailable.isVisible() or page.unavailable.isVisibleTo(window)  # 합성 예시에는 DB가 없다
    importer = window.pages["import"]
    assert not importer.tabs.isTabVisible(1) and page.deletion is importer.deletion
    page.select_tab(0)
    page.select_status(3)
    assert importer.deletion.isVisibleTo(window)
    assert window.navigate("versions")  # 다른 화면의 내부 이동은 그대로 (관리 데이터 화면은 develop에서 없어짐)
