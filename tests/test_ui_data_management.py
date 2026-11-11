"""데이터 관리 화면(11-10 개편, 11-11 2·3차 수정) 프론트 검증. DB 대신 가짜 게이트웨이를 쓴다.

11-11 3차: 현황표는 고친 뒤 [저장]으로 한꺼번에 저장, [되돌리기]는 바로 앞 단계, 행 추가(필수 칸이 차면 저장 · 없는 설비군은
그 자리에서 등록) · 정렬, Site 추가 뒤 빈 현황표, 설비군 분류는 묶음 첫 줄에만, 릴리즈 버전·Item 추가,
변경 이력 오른쪽 세부 · 새로 읽기 없음, 모든 표 열 폭 조절·기억.
"""

from datetime import date, datetime
from types import SimpleNamespace

from PySide6.QtWidgets import QDialog, QLabel, QMessageBox

from setron.features.data_admin import CellBinding, EquipmentRow, GroupRow, HistoryEntry, ReleaseFamily, ReleaseRow, SheetGrid, StatusSheet
from setron.features.management.schemas import RecordEdit
from setron.ui import data_management_page as module
from setron.ui.app import create_window
from setron.ui.data_management_page import DataManagementPage
from test_ui_import_ver5 import own, until  # noqa: F401

SHEET = StatusSheet(7, date(2026, 11, 1), "화성", "현황 2026-11-01", "마스터.xlsx", datetime(2026, 11, 10, 9, 38), 2)


def _bind(table, key, field, value, code, label):
    return CellBinding(table, key, field, value, True, f"{code} · {label}")


# 3행(EC2 · 15L)과 4행(EC1 · 13L)은 엑셀 순서가 거꾸로라 Line 순 정렬을 확인할 수 있다. 5행은 지운 행.
GRID = SheetGrid(
    7, "현황 2026-11-01", "마스터.xlsx",
    {"A1": None, "B1": None, "A2": "No", "B2": "Line", "C2": "설비군", "D2": "호기", "E2": "SW Ver",
     "A3": 1, "B3": "15L", "C3": "M-C4", "D3": "EC2", "E3": "v1.3",
     "A4": 2, "B4": "13L", "C4": "M-C4", "D4": "EC1", "E4": "v1.2",
     "A5": 3, "B5": "17L", "C5": "M-C4", "D5": "EC3", "E5": "v1.4"},
    {"B3": _bind("equipment_snapshot", {"snapshot_id": 12}, "line", "15L", "EC2", "Line"),
     "C3": _bind("equipment_snapshot", {"snapshot_id": 12}, "model_name", "M-C4", "EC2", "모델"),
     "D3": _bind("equipment", {"equipment_id": 6}, "equipment_code", "EC2", "EC2", "호기"),
     "E3": _bind("equipment_version", {"equipment_version_id": 22}, "version_raw", "v1.3", "EC2", "SW Ver"),
     "B4": _bind("equipment_snapshot", {"snapshot_id": 11}, "line", "13L", "EC1", "Line"),
     "C4": _bind("equipment_snapshot", {"snapshot_id": 11}, "model_name", "M-C4", "EC1", "모델"),
     "D4": _bind("equipment", {"equipment_id": 5}, "equipment_code", "EC1", "EC1", "호기"),
     "E4": _bind("equipment_version", {"equipment_version_id": 21}, "version_raw", "v1.2", "EC1", "SW Ver")},
    {"B3": 12, "C3": 12, "D3": 12, "E3": 12, "B4": 11, "C4": 11, "D4": 11, "E4": 11},
    {"B5": "삭제됨 · EC3", "C5": "삭제됨 · EC3", "D5": "삭제됨 · EC3", "E5": "삭제됨 · EC3"},
    {"B": "line", "C": "model_name", "D": "equipment_code", "E": "version_raw"}, ("M-C4", "M-O2"))
GROUPS = (GroupRow(1, "M-C4", "POLY", None, None, 2), GroupRow(2, "M-C4 DFB", "POLY", 1, "M-C4", 1),
          GroupRow(4, "C7", "POLY", None, None, 0), GroupRow(3, "M-O2", "OXIDE", None, None, 1))
EQUIPMENT = (EquipmentRow(5, "EC1", (1,), "M-C4", "M-C4", "화성", "13L", date(2026, 11, 1), 3),
             EquipmentRow(6, "EC2", (2,), "M-C4 DFB", "M-C4 DFB", "화성", "15L", date(2026, 11, 1), 3),
             EquipmentRow(7, "EO1", (3,), "M-O2", "M-O2", "평택", "P1", date(2026, 11, 1), 1),
             EquipmentRow(8, "OLD", (), "미지정", "", "", "", None, 0))
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
        results = {"delete_rows": 900, "restore_rows": 1, "add_row": 77, "create_sheet": 8, "add_site": args[0] if args else None,
                   "add_release_version": 102, "add_release_item": 502, "sites": tuple(self.sites),
                   "status_sheets": (SHEET,), "sheet_grid": GRID, "equipment_overview": (GROUPS, EQUIPMENT),
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


def test_three_fixed_tabs_and_status_subtabs_load_lazily(own):
    gateway = FakeGateway()
    page = page_with(own, gateway)
    assert [page.tabs.tabText(i) for i in range(3)] == ["설비현황", "릴리즈노트", "변경 이력"]
    assert [b.text() for b in page.status_buttons] == ["현황표", "설비", "위치", "가져온 엑셀 삭제"]
    page.enter()
    settle(page)
    assert gateway.calls == ["status_sheets", "sheet_grid"]  # 처음에는 보이는 현황표만 읽는다


def test_sheet_hides_empty_rows_sorts_data_rows_and_marks_header_and_deleted(own):
    page, sheet = sheet_page(own)
    assert "마스터.xlsx" in sheet.source.text() and sheet.sheet_combo.currentText().startswith("2026-11-01 · 화성")
    # 빈 1행은 빼고, 데이터 행은 Line 순(13L → 15L → 17L). 행 번호는 엑셀 그대로
    assert [sheet.table.verticalHeaderItem(r).text() for r in range(sheet.table.rowCount())] == ["2", "4", "3", "5"]
    assert sheet.table.item(0, 0).font().bold()  # 머리글 행
    assert set(sheet.originals) == {"B3", "C3", "D3", "E3", "B4", "C4", "D4", "E4"}
    assert sheet.items["D5"].font().strikeOut() and "삭제됨 · EC3" in sheet.items["D5"].toolTip()
    assert sum(sheet.table.columnWidth(c) for c in range(5)) >= sheet.table.viewport().width() - 5  # 표 폭을 채운다
    assert sheet.table.horizontalHeader().sectionResizeMode(0) == module.QHeaderView.ResizeMode.Interactive
    assert [b.text() for b in (sheet.undo_button, sheet.add_button, sheet.delete_button, sheet.save_button)] == [
        "되돌리기", "＋ 행 추가", "선택 행 삭제", "저장"]
    assert not sheet.save_button.isEnabled() and not sheet.undo_button.isEnabled() and sheet.add_button.isEnabled()


def test_edits_are_kept_until_save_then_saved_together_without_questions(own, monkeypatch):
    asked = answer(monkeypatch)
    gateway = FakeGateway()
    saved = []
    page, sheet = sheet_page(own, gateway)
    page.saved.connect(saved.append)
    sheet.items["E4"].setText("v1.2a")
    sheet.items["B4"].setText("14L")
    assert not gateway.saved and sheet.save_button.text() == "저장 (2칸)"  # 고치기만 해서는 저장하지 않는다
    assert sheet.items["E4"].background().color() == module.SAVING
    assert sheet.save()
    settle(page)
    assert asked == [] and len(gateway.saved) == 1  # 한꺼번에 한 번 저장, 확인 창 없음
    edits, context = gateway.saved[0]
    assert all(isinstance(e, RecordEdit) for e in edits) and context == {"n": 1}
    by_table = {e.table: e for e in edits}
    assert by_table["equipment_version"].changes == {"version_raw": "v1.2a", "release_version_id": None}
    assert by_table["equipment_snapshot"].changes == {"line": "14L"}
    assert not sheet.edits and sheet.originals["E4"] == "v1.2a" and sheet.items["E4"].background().color() == module.SAVED
    assert saved and "2칸을 저장했습니다" in sheet.notice.text() and sheet.steps[-1][0] == "saved"
    assert not page.history_panel.loaded


def test_undo_goes_back_one_step_at_a_time(own, monkeypatch):
    answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    sheet.items["E4"].setText("v9")
    sheet.items["E4"].setText("v10")
    assert sheet.undo()
    assert sheet.items["E4"].text() == "v9" and sheet.edits == {"E4": "v9"}   # 바로 앞 단계만
    assert sheet.undo()
    assert sheet.items["E4"].text() == "v1.2" and not sheet.edits and not sheet.undo_button.isEnabled()
    sheet.items["E4"].setText("v9")
    sheet.save()
    settle(page)
    assert sheet.undo()           # 저장한 수정 되돌리기 = 저장 전 값으로 다시 저장
    settle(page)
    assert gateway.saved[-1][0][0].changes == {"version_raw": "v1.2", "release_version_id": None}
    assert sheet.items["E4"].text() == "v1.2" and sheet.originals["E4"] == "v1.2" and not sheet.steps
    sheet.table.selectRow(1)      # 4행 EC1 삭제 → 되돌리면 되살린다
    assert sheet.delete_selected()
    settle(page)
    assert gateway.done["delete_rows"][-1] == ((7, (11,)), {}) and sheet.steps[-1] == ("delete", 900, ["EC1"])
    assert sheet.undo()
    settle(page)
    assert gateway.done["restore_rows"] == [((900,), {})] and not sheet.steps and "되살렸습니다" in sheet.notice.text()


def test_delete_selected_rows_asks_once(own, monkeypatch):
    asked = answer(monkeypatch, QMessageBox.StandardButton.No)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    sheet.table.selectRow(1)          # 4행 (EC1)
    assert sheet.selected_rows() == {11: "EC1"} and sheet.delete_button.isEnabled()
    assert not sheet.delete_selected() and "delete_rows" not in gateway.calls and "EC1" in asked[0]
    sheet.items["E3"].setText("v7")   # 저장하지 않은 수정이 있으면 행을 지우지 않는다
    assert not sheet.delete_button.isEnabled()


def test_add_row_saves_when_required_cells_are_filled_and_registers_new_group(own, monkeypatch):
    asked = answer(monkeypatch)
    monkeypatch.setattr(module.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("OXIDE", True)))
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    assert sheet.add_row()
    assert sheet.table.rowCount() == 5 and sheet.table.verticalHeaderItem(4).text() == "＋6"
    assert set(sheet.new_row["cells"]) == {"B6", "C6", "D6", "E6"} and not sheet.add_button.isEnabled()
    assert sheet.items["D6"].background().color() == module.NEW_ROW  # 비어 있는 필수 칸
    sheet.items["B6"].setText("19L")
    sheet.items["C6"].setText("C8")      # 없는 설비군
    sheet.items["D6"].setText("EC9")
    assert "add_row" not in gateway.calls  # 필수 칸이 아직 비어 있다
    sheet.items["E6"].setText("v2.0")
    settle(page)
    assert "C8" in asked[-1] and gateway.created == [("C8", "OXIDE", None)]
    assert gateway.done["add_row"] == [((7, {"line": "19L", "model_name": "C8", "equipment_code": "EC9",
                                              "version_raw": "v2.0"}, 6), {})]
    assert sheet.steps[-1] == ("add", 77, "EC9") and sheet.new_row is None and "새 설비군 'C8' 등록" in sheet.notice.text()
    assert sheet.undo()                  # 추가한 행 되돌리기 = 그 행 지우기 ('삭제됨' 표시 없이)
    settle(page)
    assert gateway.done["delete_rows"][-1] == ((7, (77,)), {"undo_add": True})


def test_new_row_can_be_undone_before_it_is_saved(own):
    page, sheet = sheet_page(own)
    sheet.add_row()
    sheet.items["D6"].setText("EC9")
    assert sheet.undo() and sheet.items["D6"].text() == "" and sheet.new_row is not None
    assert sheet.undo() and sheet.new_row is None and sheet.table.rowCount() == 4


def test_failed_save_keeps_edits_and_reports_reason(own):
    gateway = FakeGateway()
    gateway.save = lambda edits, context=None: (_ for _ in ()).throw(ValueError("검토 후 자료가 바뀌었습니다"))
    failed = []
    page, sheet = sheet_page(own, gateway)
    page.save_failed.connect(lambda: failed.append(True))
    sheet.items["E4"].setText("v9")
    sheet.save()
    settle(page)
    assert failed and "바뀌었습니다" in sheet.notice.text() and sheet.edits == {"E4": "v9"} and sheet.save_button.isEnabled()


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
    assert gateway.created == [("M-C7", "POLY", None)] and added and "대시보드·히트맵" in panel.notice.text()


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
    tree = page.location_panel.tree
    assert [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())] == ["기흥  (등록 · 설비 없음)", "평택", "화성"]
    assert page.location_panel.add_site()
    settle(page)
    assert gateway.done["add_site"] == [(("용인",), {})]
    assert gateway.done["create_sheet"] == [(("용인", date(2026, 11, 15)), {})]
    assert page.status_stack.currentIndex() == 0 and "빈 현황표를 만들었습니다" in page.sheet_panel.notice.text()


def test_release_has_no_risk_saves_edits_right_away_and_adds_version_and_item(own, monkeypatch):
    asked = answer(monkeypatch)
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_tab(1)
    settle(page)
    panel = page.release_panel
    assert not hasattr(panel, "risk_button") and "위험" not in module.RELEASE_COLUMNS
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0).child(0))
    settle(page)
    assert panel.family_title.text() == "C4 · M-C4 DFB" and panel.table.rowCount() == 3
    assert panel.table.item(1, 3).font().bold() and not panel.table.item(0, 3).font().bold()  # 개조형 적용 Item 강조
    panel._toggle_version(0, 0)
    assert panel.table.isRowHidden(1) and panel.table.item(0, 0).text() == "▶ v4.60.1.1"
    panel._toggle_version(0, 0)
    panel.table.item(1, 3).setText("보호동작 오류 수정")
    settle(page)
    panel.table.item(1, 4).setText("전체")
    settle(page)
    assert asked == [] and [e[0][0].changes for e in gateway.saved] == [
        {"content": "보호동작 오류 수정"}, {"target_scope": "ALL", "target_group_ids": None}]
    forms = iter([("v4.60.1.3", "2026-06-01"), ("3", "새 기능", "M-C4")])

    class Form:
        def __init__(self, *_):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return next(forms)

    monkeypatch.setattr(module, "_FormDialog", Form)
    assert panel.add_version()
    settle(page)
    assert gateway.done["add_release_version"] == [((10, "v4.60.1.3", date(2026, 6, 1)), {})]
    panel.table.selectRow(2)
    assert panel.add_item_button.isEnabled() and panel.add_item()
    settle(page)
    assert gateway.done["add_release_item"] == [((101, "새 기능", 3, [1]), {})]


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


def test_history_list_with_detail_panel_rereads_on_every_visit(own):
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_tab(2)
    settle(page)
    panel = page.history_panel
    assert [panel.table.horizontalHeaderItem(c).text() for c in range(8)] == list(panel.COLUMNS)
    assert [panel.table.item(0, c).text() for c in range(8)] == [
        module.HistoryPanel._when(datetime(2026, 11, 10, 14, 2)), "수정", "마스터.xlsx", "현황 2026-11-01 · D3", "EC1",
        "SW Ver", "v1.1", "v1.2"]
    assert module.HistoryPanel._when(datetime(datetime.now().year, 11, 10, 14, 2)) == "11-10 14:02"  # 올해는 짧게
    assert panel.table.item(1, 2).text() == "—" and panel.count.text() == "3건"
    assert not [b for b in panel.findChildren(type(page.sheet_panel.save_button)) if b.text() == "새로 읽기"]
    assert panel.detail_title.text() == "변경을 고르세요" and not panel.after_value.isVisibleTo(panel)
    panel.table.selectRow(0)
    assert panel.detail_title.text() == "EC1 · SW Ver" and "D3" in panel.detail.text() and "마스터.xlsx" in panel.detail.text()
    assert "2026-11-10 14:02" in panel.detail.text()  # 세부 정보에는 전체 일시
    assert panel.before_value.text() == "v1.1" and panel.after_value.text() == "v1.2"
    panel.kind.setCurrentIndex(panel.kind.findText("설비군"))
    assert panel.table.rowCount() == 1 and panel.table.item(0, 4).text() == "M-C7"
    reads = gateway.calls.count("history")
    page.select_tab(0)
    page.select_tab(2)
    settle(page)
    assert gateway.calls.count("history") == reads + 1  # 탭에 들어올 때마다 다시 읽는다


def test_column_widths_are_resizable_and_remembered(own):
    store = Store({module.COLUMN_KEY + "history": [150, 60, 200, 160, 120, 90, 110, 110]})
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
