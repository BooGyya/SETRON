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
    ((1, "M-C4", "POLY"), (2, "M-C4 DFB", "POLY"), (3, "M-O2", "OXIDE")), ("13L", "15L", "17L"),
    latest_versions={(1, "MEMORY"): "v1.4", (1, "FOUNDRY"): "v1.4F", (3, "MEMORY"): "v3.6"})
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
    assert sheet.sheet_combo.currentText() == "2026-11-01 · 화성 · 3대 · 마스터.xlsx"  # Site · 날짜를 두 번 쓰지 않는다
    assert [sheet.table.horizontalHeaderItem(c).text() for c in range(8)] == [
        "설비 호기", "Line", "대분류", "설비군", "모델명", "메모리/파운드리", "SW 버전", "PM"]
    assert codes(sheet) == ["EC1", "EC2", "EC4"]  # Line → 설비군 → 호기 (DB 순서와 다름)
    assert [cell(sheet, 0, c).text() for c in range(7)] == ["EC1", "13L", "POLY", "M-C4", "M-C4", "메모리", "v1.2"]
    editable_flag = module.Qt.ItemFlag.ItemIsEditable
    assert not cell(sheet, 0, 2).flags() & editable_flag and not cell(sheet, 0, 4).flags() & editable_flag  # 대분류 · 모델명 자동
    assert "원본 D4 · EC1" in cell(sheet, 0, 0).toolTip() and "원본 셀 없음" in cell(sheet, 0, 1).toolTip()
    assert not sheet.table.isColumnHidden(7) and cell(sheet, 2, 7).text() == "PM1"
    editable = module.Qt.ItemFlag.ItemIsEditable
    assert not cell(sheet, 2, 3).flags() & editable and not cell(sheet, 2, 6).flags() & editable  # PM 설비
    assert "버전 관리에서" in cell(sheet, 2, 6).toolTip() and cell(sheet, 2, 1).flags() & editable
    assert not hasattr(sheet, "raw_button") and not [b for b in sheet.findChildren(type(sheet.save_button))
                                                     if b.text() == "원본 보기"]  # [원본 보기]는 두지 않는다 (11-11)
    assert [b.text() for b in (sheet.add_button, sheet.delete_button, sheet.save_button)] == ["＋ 행 추가", "선택 행 삭제", "저장"]
    for control, name in ((sheet.undo_button, "되돌리기"), (sheet.redo_button, "다시 실행")):  # 글자 대신 아이콘 (11-12)
        assert control.text() == "" and not control.icon().isNull() and control.accessibleName() == name
        assert "Ctrl+" in control.toolTip() and not control.isEnabled()
    assert not sheet.save_button.isEnabled() and sheet.add_button.isEnabled()
    assert sheet.table.horizontalHeader().sectionResizeMode(0) == module.QHeaderView.ResizeMode.Interactive


def test_edits_regroup_adds_and_deletes_wait_for_save_then_go_together(own, monkeypatch):
    asked = answer(monkeypatch)
    gateway = FakeGateway()
    saved = []
    page, sheet = sheet_page(own, gateway)
    page.saved.connect(saved.append)
    cell(sheet, 0, 6).setText("v1.2a")      # EC1 SW 버전
    cell(sheet, 0, 1).setText("19L")        # EC1 Line
    cell(sheet, 0, 5).setText("파운드리")    # EC1 메모리/파운드리
    cell(sheet, 0, 3).setText("M-O2")       # EC1 설비군 → regroup
    assert cell(sheet, 0, 6).background().color() == module.SAVING and "v1.2 → v1.2a" in cell(sheet, 0, 6).toolTip()
    sheet.table.selectRow(1)                # EC2 삭제 표시
    assert sheet.delete_selected()
    assert cell(sheet, 1, 0).background().color() == module.DELETE_ROW and "삭제 예정" in cell(sheet, 1, 0).toolTip()
    assert sheet.add_row()                  # 새 행
    new = sheet.table.rowCount() - 1
    for column, value in ((0, "EC9"), (1, "19L"), (3, "M-C4"), (5, "파운드리"), (6, "v2.0")):  # 같은 새 라인 19L은 같은 메모리/파운드리
        cell(sheet, new, column).setText(value)
    assert cell(sheet, new, 2).text() == "POLY" and cell(sheet, new, 4).text() == "M-C4"  # 대분류 · 모델명은 설비군에서
    assert sheet.notice.y() < sheet.table.y()                  # 안내 줄은 표 위(버튼 아래)에 있다
    assert not gateway.saved and "apply_rows" not in gateway.calls  # 저장 전에는 DB에 아무것도 쓰지 않는다
    assert sheet.save_button.text() == "저장 (6건)"
    assert sheet.save()
    settle(page)
    assert len(asked) == 1 and "EC2" in asked[0]    # 행 삭제만 한 번 묻는다
    assert gateway.done["add_line"] == [(("화성", "19L", "FOUNDRY"), {})]   # 새 라인은 묻지 않고 그 줄의 값으로 등록
    edits = edits_by_table(gateway)
    assert edits["equipment_version"] == ({"equipment_version_id": 21}, {"version_raw": "v1.2a", "release_version_id": None})
    assert edits["equipment_snapshot"] == ({"snapshot_id": 11}, {"line": "19L", "production_area": "FOUNDRY"})
    assert gateway.done["apply_rows"] == [((7, [({"equipment_code": "EC9", "line": "19L", "group_name": "M-C4", "model_name": "",
                                                   "production_area": "FOUNDRY", "version_raw": "v2.0"}, None)], (12,), [(11, 3)]), {})]
    step = sheet.steps[-1]
    assert step[0] == "saved" and step[1]["regroup"] == [(11, 1)] and step[1]["added"] == [77] and step[1]["journal"] == 900
    assert not sheet.pending and saved and "4칸 수정 · 1행 추가 · 1행 삭제" in sheet.notice.text() and "라인 19L" in sheet.notice.text()


def test_one_value_per_equipment_is_shared_and_edit_on_deleted_row_is_dropped(own, monkeypatch):
    answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    cell(sheet, 1, 6).setText("v9")          # EC2 SW 버전
    sheet.table.selectRow(1)
    sheet.delete_selected()
    sheet.save()
    settle(page)
    assert not gateway.saved and gateway.done["apply_rows"][0][0][2] == (12,)


def test_bad_values_are_rejected_and_restored(own):
    page, sheet = sheet_page(own)
    cell(sheet, 0, 3).setText("없는설비군")
    assert "설비 탭에서 먼저 추가" in sheet.notice.text() and cell(sheet, 0, 3).text() == "M-C4" and not sheet.edits
    cell(sheet, 0, 0).setText("")
    assert "비울 수 없습니다" in sheet.notice.text() and cell(sheet, 0, 0).text() == "EC1" and not sheet.edits


def test_undo_goes_back_one_step_then_undoes_the_whole_last_save(own, monkeypatch):
    answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    cell(sheet, 0, 6).setText("v9")
    cell(sheet, 0, 6).setText("v10")
    assert sheet.undo() and cell(sheet, 0, 6).text() == "v9" and sheet.edits == {("version", 21): "v9"}
    sheet.table.selectRow(1)
    sheet.delete_selected()
    assert sheet.undo() and not sheet.deletes and cell(sheet, 1, 0).background().color() != module.DELETE_ROW
    sheet.add_row()
    cell(sheet, 3, 0).setText("EC9")
    assert sheet.undo() and sheet.new_rows[0]["values"]["equipment_code"] == ""
    assert sheet.undo() and not sheet.new_rows and sheet.table.rowCount() == 3
    cell(sheet, 0, 3).setText("M-O2")
    sheet.save()
    settle(page)
    assert sheet.undo()        # 마지막 저장 전체(칸 수정 · 설비군 변경)를 되돌린다
    settle(page)
    assert gateway.done["undo_rows"] == [((7, [], None, [(11, 1)]), {})]
    assert gateway.saved[-1][0][0].changes == {"version_raw": "v1.2", "release_version_id": None}
    assert not sheet.steps and "마지막 저장을 되돌렸습니다" in sheet.notice.text()


def test_redo_reapplies_undone_steps_until_something_else_changes(own, monkeypatch):
    answer(monkeypatch)
    page, sheet = sheet_page(own)
    cell(sheet, 0, 6).setText("v9")
    sheet.table.selectRow(1)
    sheet.delete_selected()
    assert sheet.undo() and sheet.undo() and not sheet.edits and not sheet.deletes
    assert sheet.redo_button.isEnabled()
    assert sheet.redo() and sheet.edits == {("version", 21): "v9"} and cell(sheet, 0, 6).text() == "v9"
    assert sheet.redo() and sheet.deletes == {12: "EC2"} and not sheet.redo_button.isEnabled()
    assert sheet.undo()                       # 삭제 표시를 다시 풀고
    cell(sheet, 0, 1).setText("19L")          # 다른 칸을 고치면 다시 실행할 수 없다
    assert not sheet.redo() and "다시 실행할 수 없습니다" in sheet.notice.text() and not sheet.redo_button.isEnabled()
    assert len(sheet.findChildren(module.QShortcut)) == 3  # Ctrl+Z · Ctrl+Y · Ctrl+Shift+Z


def test_redo_saves_an_undone_save_again(own, monkeypatch):
    asked = answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    cell(sheet, 0, 6).setText("v9")
    sheet.table.selectRow(1)                  # EC2 (설비 6) 삭제
    sheet.delete_selected()
    assert sheet.save()
    settle(page)
    assert sheet.undo()                       # 저장 전체를 되돌린다
    settle(page)
    assert sheet.redo_button.isEnabled() and sheet.redo()   # 같은 변경으로 다시 저장 (지운 행은 설비로 다시 찾는다)
    settle(page)
    assert len(asked) == 2 and len(gateway.done["apply_rows"]) == 2 and gateway.done["apply_rows"][-1][0][2] == (12,)
    assert gateway.saved[-1][0][0].changes == {"version_raw": "v9", "release_version_id": None}
    assert sheet.steps[-1][0] == "saved" and not sheet.redo_steps and not sheet.pending


def test_new_row_needs_required_cells_and_unknown_group_is_registered_on_save(own, monkeypatch):
    asked = answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    assert sheet.add_row()
    assert sheet.table.verticalHeaderItem(3).text() == "＋" and cell(sheet, 3, 0).background().color() == module.NEW_ROW
    cell(sheet, 3, 0).setText("EC9")
    assert not sheet.save() and "필수 칸" in sheet.notice.text()
    editable = module.Qt.ItemFlag.ItemIsEditable
    assert not cell(sheet, 3, 2).flags() & editable          # 설비군을 적기 전에는 대분류를 고를 수 없다
    cell(sheet, 3, 3).setText("C8")
    cell(sheet, 3, 6).setText("v2.0")
    assert not sheet.save() and "메모리/파운드리" in sheet.notice.text()   # 메모리/파운드리도 필수 (비우면 임의로 정하지 않는다)
    cell(sheet, 3, 5).setText("메모리")
    assert cell(sheet, 3, 2).flags() & editable and cell(sheet, 3, 2).background().color() == module.NEW_ROW
    assert not sheet.save() and "대분류" in sheet.notice.text()   # 새 설비군은 대분류를 골라야 한다
    cell(sheet, 3, 2).setText("OXIDE")
    assert sheet.save()
    settle(page)
    assert "C8" in asked[0] and "OXIDE" in asked[0] and gateway.created == [("C8", "OXIDE", None)]
    assert "새로 등록: C8" in sheet.notice.text()
    assert gateway.done["apply_rows"][0][0][1][0][0]["model_name"] == ""   # 모델명은 서버가 설비군 이름으로


def test_empty_sheet_is_created_from_sheet_tab_for_an_existing_site(own, monkeypatch):
    gateway = FakeGateway()
    gateway.sites = ["China"]
    page, sheet = sheet_page(own, gateway)
    assert sheet.create_button.text() == "＋ 빈 현황표" and sheet.create_button.isEnabled()
    shown = []

    class Accept:
        def __init__(self, _parent, sites, site=None):
            shown.append((sites, site))

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return "China", date(2026, 11, 12), "MEMORY"

    monkeypatch.setattr(module, "CreateSheetDialog", Accept)
    assert sheet.create_sheet()
    settle(page)
    assert shown == [(["China", "화성"], "화성")]  # 등록 Site + 현황표가 있는 Site, 지금 보는 Site를 먼저 고름
    assert gateway.done["create_sheet"] == [(("China", date(2026, 11, 12), "MEMORY"), {})]
    assert page.status_stack.currentIndex() == 0 and "China · 2026-11-12 빈 현황표를 만들었습니다" in sheet.notice.text()


def test_line_of_another_site_is_warned_before_save(own, monkeypatch):
    from dataclasses import replace

    gateway = FakeGateway()
    gateway.data_admin = (lambda original: lambda method, *a, **k: replace(SHEET_ROWS, elsewhere={"c4h": ("China",)})
                          if method == "sheet_rows" else original(method, *a, **k))(gateway.data_admin)
    page, sheet = sheet_page(own, gateway)
    sheet.add_row()
    for column, value in ((0, "CC4H101"), (1, "C4H"), (3, "M-C4"), (5, "메모리"), (6, "v1.0")):
        cell(sheet, 3, column).setText(value)
    assert sheet.other_site_lines() == {"C4H": ("China",)}
    asked = answer(monkeypatch, QMessageBox.StandardButton.No)
    assert not sheet.save()
    assert "China 라인" in asked[0] and "화성 설비로 저장" in asked[0] and "[＋ 빈 현황표]" in sheet.notice.text()
    assert sheet.pending and "apply_rows" not in gateway.done
    asked = answer(monkeypatch)
    assert sheet.save()
    settle(page)
    assert len(asked) == 1 and gateway.done["apply_rows"][0][0][1][0][0]["line"] == "C4H"
    cell(sheet, 0, 1).setText("15L")         # 이 Site의 라인으로 고치면 묻지 않는다
    assert sheet.other_site_lines() == {}


def test_help_button_opens_guide_drawer_and_steps_jump_to_screens(own):
    page, sheet = sheet_page(own)
    assert page.help_button.text() == "?" and page.help_button.toolTip()
    for index in range(4):                    # 설비현황의 어느 하위 탭에서도 [?]가 보인다
        page.select_status(index)
        settle(page)
        assert page.help_button.isVisibleTo(page)
    page.help_button.click()                  # 가져온 엑셀 삭제 탭에서 연다
    assert sheet.guide.is_open
    texts = [w.text() for w in sheet.guide.panel.findChildren(QLabel)]
    assert "새 위치에 설비 넣기" in texts and any("SW 버전" in t and "필수" in t for t in texts)
    assert set(sheet.guide_buttons) == {"location", "create", "add"}   # 3단계: 빈 현황표 → 행 추가 → 저장 · 확인
    sheet.guide_buttons["location"].click()
    assert not sheet.guide.is_open and page.status_stack.currentIndex() == 2
    settle(page)
    sheet.show_guide()
    sheet.guide_buttons["add"].click()       # 현황표 탭으로 돌아가 새 행을 만든다
    assert page.status_stack.currentIndex() == 0 and len(sheet.new_rows) == 1 and not sheet.guide.is_open
    sheet.show_guide()
    page.select_tab(1)                        # 다른 탭으로 가면 닫힌다
    assert not sheet.guide.is_open


def test_create_sheet_dialog_checks_the_date(own):
    dialog = own(module.CreateSheetDialog(None, ["China", "화성"], "화성"))
    assert dialog.site.currentData() == "화성" and dialog.site.isEditable()
    dialog.base_date.setText("11월 12일")
    dialog._accept()
    assert "YYYY-MM-DD" in dialog.error.text() and dialog.result() != QDialog.DialogCode.Accepted
    dialog.site.setCurrentIndex(0)
    dialog.base_date.setText(" 2026-11-12 ")
    dialog._accept()
    assert "메모리/파운드리" in dialog.error.text() and dialog.result() != QDialog.DialogCode.Accepted   # 필수
    dialog.areas.buttons()[1].setChecked(True)
    dialog._accept()
    assert dialog.values() == ("China", date(2026, 11, 12), "FOUNDRY")


def test_sort_follows_header_clicks(own):
    page, sheet = sheet_page(own)
    assert "Line → 설비군 → 설비 호기" in sheet.sort_label.text() and not sheet.sort_reset.isVisibleTo(sheet)
    sheet._header_clicked(6)   # SW 버전 오름차순
    assert codes(sheet) == ["EC4", "EC1", "EC2"] and "SW 버전 오름차순" in sheet.sort_label.text()
    assert sheet.table.horizontalHeaderItem(6).text() == "SW 버전 ▲"
    sheet._header_clicked(6)   # 내림차순
    assert codes(sheet) == ["EC2", "EC1", "EC4"] and sheet.sort_reset.isVisibleTo(sheet)
    sheet._header_clicked(6)   # 기본 정렬로
    assert codes(sheet) == ["EC1", "EC2", "EC4"] and sheet.sort is None


def test_failed_save_keeps_changes_and_reports_reason(own):
    gateway = FakeGateway()
    gateway.save = lambda edits, context=None: (_ for _ in ()).throw(ValueError("검토 후 자료가 바뀌었습니다"))
    failed = []
    page, sheet = sheet_page(own, gateway)
    page.save_failed.connect(lambda: failed.append(True))
    cell(sheet, 0, 6).setText("v9")
    sheet.save()
    settle(page)
    assert failed and "바뀌었습니다" in sheet.notice.text() and sheet.edits == {("version", 21): "v9"}
    assert sheet.save_button.isEnabled()


def test_group_change_sets_model_and_category_and_fixes_a_mismatched_model(own, monkeypatch):
    from dataclasses import replace

    answer(monkeypatch)
    gateway = FakeGateway()
    mismatched = replace(SHEET_ROWS.rows[1], model_name="M-O2")   # EC1: 설비군 M-C4인데 모델명 M-O2
    rows = (SHEET_ROWS.rows[0], mismatched, SHEET_ROWS.rows[2])
    gateway.data_admin = (lambda original: lambda method, *a, **k: replace(SHEET_ROWS, rows=rows)
                          if method == "sheet_rows" else original(method, *a, **k))(gateway.data_admin)
    page, sheet = sheet_page(own, gateway)
    assert cell(sheet, 0, 4).text() == "M-O2" and "설비군(M-C4)과 다릅니다" in cell(sheet, 0, 4).toolTip()
    editable = module.Qt.ItemFlag.ItemIsEditable
    assert cell(sheet, 0, 4).flags() & editable and not cell(sheet, 1, 4).flags() & editable  # 다른 줄만 맞출 수 있다
    cell(sheet, 0, 4).setText("M-O3")          # 설비군과 다른 이름으로는 못 바꾼다
    assert "설비군과 같아야" in sheet.notice.text() and not sheet.edits
    cell(sheet, 0, 4).setText("M-C4")          # 설비군 이름으로 맞춘다
    assert sheet.edits == {("model", 11): "M-C4"} and cell(sheet, 0, 4).text() == "M-C4"
    assert sheet.undo() and not sheet.edits and cell(sheet, 0, 4).text() == "M-O2"   # 한 단계로 되돌린다
    cell(sheet, 0, 3).setText("M-O2")          # 다른 설비군이면 대분류 · 모델명이 따라 바뀐다
    assert sheet.edits == {("group", 11): 3} and [cell(sheet, 0, c).text() for c in (2, 3, 4)] == ["OXIDE", "M-O2", "M-O2"]
    sheet.save()
    settle(page)
    assert gateway.done["apply_rows"][0][0][3] == [(11, 3)] and not gateway.saved   # 모델명은 서버가 설비군으로 맞춘다


def test_unregistered_line_is_registered_with_the_row_area_or_save_is_blocked(own, monkeypatch):
    answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    cell(sheet, 0, 1).setText("NEW1")         # EC1(메모리)을 처음 보는 라인으로 → 묻지 않고 메모리로 등록
    assert sheet.unregistered_lines() == ["NEW1"] and "NEW1" in sheet._line_choices()   # 적은 라인은 목록에도 보인다
    assert sheet.save()
    settle(page)
    assert gateway.done["add_line"] == [(("화성", "NEW1", "MEMORY"), {})]
    sheet.add_row()                            # 새 행: 메모리/파운드리를 비우면 새 라인이라 저장을 막는다
    new = sheet.table.rowCount() - 1
    for column, value in ((0, "EC9"), (1, "NEW2"), (3, "M-C4")):
        cell(sheet, new, column).setText(value)
    assert cell(sheet, new, 5).background().color() == module.NEW_ROW   # 메모리/파운드리 필수 (모르는 라인이라 비어 있음)
    assert not sheet.save() and "메모리/파운드리" in sheet.notice.text() and sheet.pending
    cell(sheet, new, 5).setText("파운드리")
    assert sheet.save()
    settle(page)
    assert gateway.done["add_line"][-1] == (("화성", "NEW2", "FOUNDRY"), {})


def test_new_row_gets_sheet_area_and_latest_version_of_the_group(own, monkeypatch):
    from dataclasses import replace

    answer(monkeypatch)
    gateway = FakeGateway()
    gateway.data_admin = (lambda original: lambda method, *a, **k: replace(
        SHEET_ROWS, default_area="FOUNDRY", line_areas={"13L": "MEMORY"})
        if method == "sheet_rows" else original(method, *a, **k))(gateway.data_admin)
    page, sheet = sheet_page(own, gateway)
    sheet.add_row()
    new = sheet.table.rowCount() - 1
    assert cell(sheet, new, 5).text() == "파운드리"                   # 빈 현황표에서 고른 메모리/파운드리
    cell(sheet, new, 1).setText("13L")                               # 메모리/파운드리를 아는 라인 → 그 값으로 보이게 채움
    assert cell(sheet, new, 5).text() == "메모리" and "라인의 메모리/파운드리로 자동" in cell(sheet, new, 5).toolTip()
    cell(sheet, new, 1).setText("99L")                               # 모르는 라인 → 현황표 기본값
    assert cell(sheet, new, 5).text() == "파운드리"
    cell(sheet, new, 3).setText("M-C4")
    assert cell(sheet, new, 6).text() == "v1.4F" and "최신 버전으로 자동" in cell(sheet, new, 6).toolTip()
    cell(sheet, new, 5).setText("메모리")                             # 메모리로 바꾸면 메모리 계열 최신으로
    assert cell(sheet, new, 6).text() == "v1.4"
    cell(sheet, new, 6).setText("v1.2")                              # 직접 적은 버전은
    cell(sheet, new, 3).setText("M-O2")                              # 설비군을 바꿔도 그대로
    assert cell(sheet, new, 6).text() == "v1.2"
    assert sheet.undo() and cell(sheet, new, 3).text() == "M-C4" and cell(sheet, new, 6).text() == "v1.2"


def test_new_site_typed_in_empty_sheet_dialog_is_registered_with_the_sheet(own, monkeypatch):
    asked = answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)

    class Accept:
        def __init__(self, *_):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return "베트남", date(2026, 11, 12), "FOUNDRY"

    monkeypatch.setattr(module, "CreateSheetDialog", Accept)
    assert sheet.create_sheet()
    settle(page)
    assert "'베트남'은(는) 아직 없는 Site" in asked[0]
    assert gateway.done["add_site"] == [(("베트남",), {})]
    assert gateway.done["create_sheet"] == [(("베트남", date(2026, 11, 12), "FOUNDRY"), {})]
    assert "새 Site '베트남'" in sheet.notice.text()


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


def test_site_added_then_empty_sheet_is_created_and_stays_on_location(own, monkeypatch):
    answer(monkeypatch)
    monkeypatch.setattr(module.QInputDialog, "getText", staticmethod(lambda *a, **k: ("용인", True)))

    class Accept:
        def __init__(self, *_):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return "용인", date(2026, 11, 15), "MEMORY"

    monkeypatch.setattr(module, "CreateSheetDialog", Accept)
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_status(2)
    settle(page)
    assert page.location_panel.add_site()
    settle(page)
    assert gateway.done["add_site"] == [(("용인",), {})]
    assert gateway.done["create_sheet"] == [(("용인", date(2026, 11, 15), "MEMORY"), {})]
    assert page.status_stack.currentIndex() == 2 and "빈 현황표를 만들었습니다" in page.location_panel.notice.text()  # 위치 탭에 머문다
    assert page.sheet_panel.pending_batch == 8 and not page.sheet_panel.loaded  # 현황표 탭에 가면 만든 현황표를 고른다


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
    assert panel.redo_button.isEnabled() and panel.redo()     # 되돌린 저장을 같은 변경으로 다시 저장
    settle(page)
    plan = gateway.done["apply_release"][-1][0][1]
    assert len(asked) == 2 and plan["delete_items"] == [(100, 501)] and plan["orders"] == {100: [500]}
    assert [v["version_raw"] for v in plan["add_versions"]] == ["v4.60.1.3"] and plan["add_items"][0]["content"] == "새 기능"
    assert gateway.saved[-1][0][0].changes == {"content": "Recipe 확인 수정"}
    assert panel.steps[-1][0] == "saved" and not panel.redo_steps


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
    assert not panel.undo_button.isEnabled() and panel.redo_button.isEnabled()
    assert panel.redo() and panel.edits and not panel.deleted and panel.table.item(0, 3).text() == "Recipe 확인 수정"
    assert panel.redo() and panel.deleted and not panel.redo_button.isEnabled()
    assert panel.redo_button.text() == "" and panel.redo_button.accessibleName() == "다시 실행"


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
    settle(page)  # 계열 조회 완료 안내가 아래의 검증용 여러 줄 텍스트를 덮지 않게 한다.
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


def test_sheet_choice_label_does_not_repeat_site_and_date():
    manual = StatusSheet(8, date(2026, 11, 11), "USA", "USA 2026-11-11 (직접 입력)", None, None, 1)
    unnamed = StatusSheet(9, date(2026, 11, 11), "평택", "평택 2026-11-01", None, None, 25)
    plain = StatusSheet(10, date(2026, 11, 1), "화성", "현황 2026-11-01", None, None, 50)
    assert manual.label == "2026-11-11 · USA · 1대 · 직접 입력"
    assert unnamed.label == "2026-11-11 · 평택 · 25대 · 2026-11-01"  # 파일 이름이 없으면 Site만 뺀 시트 이름
    assert plain.label == "2026-11-01 · 화성 · 50대 · 현황"


def test_new_row_without_area_does_not_fill_version_or_save(own, monkeypatch):
    answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)                            # 기본값 없는 현황표
    sheet.add_row()
    new = sheet.table.rowCount() - 1
    for column, value in ((0, "EC9"), (1, "99L"), (3, "M-C4")):
        cell(sheet, new, column).setText(value)
    assert cell(sheet, new, 5).text() == "" and cell(sheet, new, 6).text() == ""   # 메모리를 가정해 버전을 채우지 않는다
    assert not sheet.save() and "메모리/파운드리" in sheet.notice.text() and "apply_rows" not in gateway.done
    cell(sheet, new, 5).setText("메모리")
    assert cell(sheet, new, 6).text() == "v1.4"                       # 정해진 뒤에 최신 버전을 채운다


def test_selected_new_row_still_shows_required_and_filled_colors(own):
    page, sheet = sheet_page(own)
    sheet.add_row()
    new = sheet.table.rowCount() - 1
    cell(sheet, new, 1).setText("13L")                     # 노란 칸 (채움)
    sheet.table.selectRow(new)                             # 입력 중인 줄을 고른 상태
    sheet.table.viewport().repaint()
    image = sheet.table.viewport().grab().toImage()

    def color(column):
        rect = sheet.table.visualRect(sheet.table.model().index(new, column))
        return image.pixelColor(rect.center())

    required, filled = color(0), color(1)                  # 설비 호기(분홍 필수) · Line(노랑)
    assert required != filled                              # 고른 줄도 칸 색이 구분된다 (선택 색이 덮지 않음)
    assert required.red() > required.green() and filled.green() > filled.blue()   # 분홍 · 노랑 기운이 남아 있다
    assert isinstance(sheet.table.itemDelegate(), module._KeepColorsDelegate)
    assert isinstance(page.release_panel.table.itemDelegate(), module._KeepColorsDelegate)

