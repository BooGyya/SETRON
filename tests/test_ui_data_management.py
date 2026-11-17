"""데이터 관리 화면(11-10 개편, 11-11 2·3·6차 수정) 프론트 검증. DB 대신 가짜 게이트웨이를 쓴다.

11-11 6차: 현황표는 DB에 들어간 값을 가져오기 3단계 항목(호기 · Line · 설비군 · 모델명 · Memory/Foundry · SW 버전)으로
보이고, 고친 뒤 [저장]으로 한꺼번에 저장한다(설비군 변경은 regroup). [원본 보기]는 두지 않는다(11-11 요청).
설비군 · 위치를 표에서 고쳐 [저장]하는 흐름(11-12), Site · 라인 등록과 Memory/Foundry 변경도 확인한다.
"""

from datetime import date, datetime
from types import SimpleNamespace

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QScrollArea, QWidget

from setron.features.data_admin import (
    EquipmentRow, GroupRow, HistoryEntry, LineRow, ReleaseFamily, ReleaseRow, SheetRow, SheetRows, StatusSheet,
)
from setron.ui import data_management_page as module
from setron.ui.app import create_window
from setron.ui.data_management_page import DataManagementPage
from test_ui_import_ver5 import own, until  # noqa: F401

C = module.COLUMN  # 현황표 열 번호 (11-16: 가져오기 검토 표와 같은 순서)
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
GROUPS = (GroupRow(1, "M-C4", "POLY", None, None, 2, builtin=True), GroupRow(2, "M-C4 DFB", "POLY", 1, "M-C4", 1, builtin=True),
          GroupRow(4, "C7", "POLY", None, None, 0), GroupRow(3, "M-O2", "OXIDE", None, None, 1, builtin=True))
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
                   "update_groups": 1, "apply_locations": {"line": 2, "move": 1},
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

    def _save_changes(self, changes, context):
        if not changes:
            return None
        from setron.features.management.schemas import RecordEdit
        return self.save(tuple(RecordEdit(table, key, self.get(table, key).revision, values)
                               for table, key, values in changes), context)

    def save_sheet(self, batch_id, *, changes=(), context=None, registers=(), creates=(), adds=(),
                   deletes=(), regroup=(), base_date=None):
        for line, area in registers:
            self.data_admin("add_line", SHEET.site, line, area)
        for name, category in creates:
            self.data_admin("create_group", name, category, None)
        saved = self._save_changes(changes, context)
        rows = self.data_admin("apply_rows", batch_id, adds, deletes, regroup, base_date=base_date) \
            if adds or deletes or regroup else {"added": [], "journal": None, "regroup": []}
        return SimpleNamespace(records=getattr(saved, "records", ()), refresh=None, rows=rows)

    def save_release(self, family_id, plan, *, changes=(), context=None):
        saved = self._save_changes(changes, context)
        undo = self.data_admin("apply_release", family_id, plan) if any(plan.values()) else None
        return SimpleNamespace(records=getattr(saved, "records", ()), refresh=None, undo=undo)

    def undo_sheet(self, batch_id, *, changes=(), context=None, added=(), journal=None, regroup=()):
        if added or journal is not None or regroup:
            self.data_admin("undo_rows", batch_id, added, journal, regroup)
        return self._save_changes(changes, context) or SimpleNamespace(records=(), refresh=None)

    def undo_release_changes(self, undo, *, changes=(), context=None):
        if undo:
            self.data_admin("undo_release", undo)
        return self._save_changes(changes, context) or SimpleNamespace(records=(), refresh=None)


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
    return [cell(sheet, r, C["equipment_code"]).text() for r in range(sheet.table.rowCount())]


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
    # 11-16: 가져오기 검토 표(AI 추출 결과)와 같은 열 · 같은 순서 (Engine · EPD 버전은 DB에 칸이 없어 없음)
    # 11-17: 자동으로 정해지는 열은 제목에 '(자동)'을 붙인다
    assert [sheet.table.horizontalHeaderItem(c).text() for c in range(sheet.table.columnCount())] == [
        "기준일 (자동)", "Site (자동)", "Line", "대분류 (자동)", "설비군", "모델명 (자동)", "Memory/Foundry", "설비 호기", "PM",
        "공정", "SW 계열", "SW 버전", "UI 구분 (자동)", "형상", "개조 정보"]
    assert codes(sheet) == ["EC1", "EC2", "EC4"]  # Line → 설비군 → 호기 (DB 순서와 다름)
    assert [cell(sheet, 0, c).text() for c in range(sheet.table.columnCount())] == [
        "2026-11-01", "화성", "13L", "POLY", "M-C4", "M-C4", "Memory", "EC1", "", "", "C4", "v1.2", "", "", ""]
    editable_flag = module.Qt.ItemFlag.ItemIsEditable
    for fixed in ("base_date", "site", "category", "model_name", "ui_type"):   # 현황표 · 설비군 · SW 계열이 정하는 칸
        assert not cell(sheet, 0, C[fixed]).flags() & editable_flag, fixed
    for free in ("line", "group", "production_area", "equipment_code", "process_name", "version_raw", "geometry_type",
                 "modification_type"):
        assert cell(sheet, 0, C[free]).flags() & editable_flag, free
    assert not cell(sheet, 0, C["pm"]).flags() & editable_flag             # PM 없는 설비는 PM 칸을 고치지 않는다
    assert not cell(sheet, 0, C["family"]).flags() & editable_flag         # 가짜 자료에는 고를 SW 계열이 없다
    assert "원본 D4 · EC1" in cell(sheet, 0, C["equipment_code"]).toolTip() and "원본 셀 없음" in cell(sheet, 0, C["line"]).toolTip()
    assert not sheet.table.isColumnHidden(C["pm"]) and cell(sheet, 2, C["pm"]).text() == "PM1"
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
    cell(sheet, 0, C["version_raw"]).setText("v1.2a")      # EC1 SW 버전
    cell(sheet, 0, C["line"]).setText("19L")        # EC1 Line
    cell(sheet, 0, C["production_area"]).setText("Foundry")    # EC1 Memory/Foundry
    cell(sheet, 0, C["group"]).setText("M-O2")       # EC1 설비군 → regroup
    assert cell(sheet, 0, C["version_raw"]).background().color() == module.SAVING and "v1.2 → v1.2a" in cell(sheet, 0, C["version_raw"]).toolTip()
    sheet.table.selectRow(1)                # EC2 삭제 표시
    assert sheet.delete_selected()
    assert cell(sheet, 1, C["equipment_code"]).background().color() == module.DELETE_ROW and "삭제 예정" in cell(sheet, 1, C["equipment_code"]).toolTip()
    assert sheet.add_row()                  # 새 행
    new = sheet.table.rowCount() - 1
    for column, value in ((C["equipment_code"], "EC9"), (C["line"], "19L"), (C["group"], "M-C4"), (C["production_area"], "Foundry"), (C["version_raw"], "v2.0")):  # 같은 새 라인 19L은 같은 Memory/Foundry
        cell(sheet, new, column).setText(value)
    assert cell(sheet, new, C["category"]).text() == "POLY" and cell(sheet, new, C["model_name"]).text() == "M-C4"  # 대분류 · 모델명은 설비군에서
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
                                                   "production_area": "FOUNDRY", "version_raw": "v2.0", "pm_name": "",
                                                   "family_id": None, "process_name": "", "geometry_type": "",
                                                   "modification_type": ""}, None)], (12,), [(11, 3)]),
                                          {"base_date": date(2026, 11, 1)})]   # 현황표 = 입력 작업 · 기준일
    step = sheet.steps[-1]
    assert step[0] == "saved" and step[1]["regroup"] == [(11, 1)] and step[1]["added"] == [77] and step[1]["journal"] == 900
    assert not sheet.pending and saved and "4칸 수정 · 1행 추가 · 1행 삭제" in sheet.last_note and "라인 19L" in sheet.last_note


def test_one_value_per_equipment_is_shared_and_edit_on_deleted_row_is_dropped(own, monkeypatch):
    answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    cell(sheet, 1, C["version_raw"]).setText("v9")          # EC2 SW 버전
    sheet.table.selectRow(1)
    sheet.delete_selected()
    sheet.save()
    settle(page)
    assert not gateway.saved and gateway.done["apply_rows"][0][0][2] == (12,)


def test_bad_values_are_rejected_and_restored(own):
    page, sheet = sheet_page(own)
    cell(sheet, 0, C["group"]).setText("없는설비군")
    assert "설비 탭에서 먼저 추가" in sheet.last_note and cell(sheet, 0, C["group"]).text() == "M-C4" and not sheet.edits
    cell(sheet, 0, C["equipment_code"]).setText("")
    assert "비울 수 없습니다" in sheet.last_note and cell(sheet, 0, C["equipment_code"]).text() == "EC1" and not sheet.edits


def test_undo_goes_back_one_step_then_undoes_the_whole_last_save(own, monkeypatch):
    answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    cell(sheet, 0, C["version_raw"]).setText("v9")
    cell(sheet, 0, C["version_raw"]).setText("v10")
    assert sheet.undo() and cell(sheet, 0, C["version_raw"]).text() == "v9" and sheet.edits == {("version", 21): "v9"}
    sheet.table.selectRow(1)
    sheet.delete_selected()
    assert sheet.undo() and not sheet.deletes and cell(sheet, 1, C["equipment_code"]).background().color() != module.DELETE_ROW
    sheet.add_row()
    cell(sheet, 3, C["equipment_code"]).setText("EC9")
    assert sheet.undo() and sheet.new_rows[0]["values"]["equipment_code"] == ""
    assert sheet.undo() and not sheet.new_rows and sheet.table.rowCount() == 3
    cell(sheet, 0, C["group"]).setText("M-O2")
    sheet.save()
    settle(page)
    assert sheet.undo()        # 마지막 저장 전체(칸 수정 · 설비군 변경)를 되돌린다
    settle(page)
    assert gateway.done["undo_rows"] == [((7, [], None, [(11, 1)]), {})]
    assert gateway.saved[-1][0][0].changes == {"version_raw": "v1.2", "release_version_id": None}
    assert not sheet.steps and "마지막 저장을 되돌렸습니다" in sheet.last_note


def test_redo_reapplies_undone_steps_until_something_else_changes(own, monkeypatch):
    answer(monkeypatch)
    page, sheet = sheet_page(own)
    cell(sheet, 0, C["version_raw"]).setText("v9")
    sheet.table.selectRow(1)
    sheet.delete_selected()
    assert sheet.undo() and sheet.undo() and not sheet.edits and not sheet.deletes
    assert sheet.redo_button.isEnabled()
    assert sheet.redo() and sheet.edits == {("version", 21): "v9"} and cell(sheet, 0, C["version_raw"]).text() == "v9"
    assert sheet.redo() and sheet.deletes == {12: "EC2"} and not sheet.redo_button.isEnabled()
    assert sheet.undo()                       # 삭제 표시를 다시 풀고
    cell(sheet, 0, C["line"]).setText("19L")          # 다른 칸을 고치면 다시 실행할 수 없다
    assert not sheet.redo() and "다시 실행할 수 없습니다" in sheet.last_note and not sheet.redo_button.isEnabled()
    assert len(sheet.findChildren(module.QShortcut)) == 3  # Ctrl+Z · Ctrl+Y · Ctrl+Shift+Z


def test_redo_saves_an_undone_save_again(own, monkeypatch):
    asked = answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    cell(sheet, 0, C["version_raw"]).setText("v9")
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
    assert sheet.table.verticalHeaderItem(3).text() == "＋" and cell(sheet, 3, C["equipment_code"]).background().color() == module.NEW_ROW
    cell(sheet, 3, C["equipment_code"]).setText("EC9")
    assert not sheet.save() and "필수 칸" in sheet.last_note
    editable = module.Qt.ItemFlag.ItemIsEditable
    assert not cell(sheet, 3, C["category"]).flags() & editable          # 설비군을 적기 전에는 대분류를 고를 수 없다
    cell(sheet, 3, C["group"]).setText("C8")
    cell(sheet, 3, C["version_raw"]).setText("v2.0")
    assert not sheet.save() and "Memory/Foundry" in sheet.last_note   # Memory/Foundry도 필수 (비우면 임의로 정하지 않는다)
    cell(sheet, 3, C["production_area"]).setText("Memory")
    assert cell(sheet, 3, C["category"]).flags() & editable and cell(sheet, 3, C["category"]).background().color() == module.NEW_ROW
    assert not sheet.save() and "대분류" in sheet.last_note   # 새 설비군은 대분류를 골라야 한다
    cell(sheet, 3, C["category"]).setText("OXIDE")
    assert sheet.save()
    settle(page)
    assert "C8" in asked[0] and "OXIDE" in asked[0] and gateway.created == [("C8", "OXIDE", None)]
    assert "새로 등록: C8" in sheet.last_note
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
    assert page.status_stack.currentIndex() == 0 and "China · 2026-11-12 빈 현황표를 만들었습니다" in sheet.last_note


def test_line_of_another_site_is_warned_before_save(own, monkeypatch):
    from dataclasses import replace

    gateway = FakeGateway()
    gateway.data_admin = (lambda original: lambda method, *a, **k: replace(SHEET_ROWS, elsewhere={"c4h": ("China",)})
                          if method == "sheet_rows" else original(method, *a, **k))(gateway.data_admin)
    page, sheet = sheet_page(own, gateway)
    sheet.add_row()
    for column, value in ((C["equipment_code"], "CC4H101"), (C["line"], "C4H"), (C["group"], "M-C4"), (C["production_area"], "Memory"), (C["version_raw"], "v1.0")):
        cell(sheet, 3, column).setText(value)
    assert sheet.other_site_lines() == {"C4H": ("China",)}
    asked = answer(monkeypatch, QMessageBox.StandardButton.No)
    assert not sheet.save()
    assert "China 라인" in asked[0] and "화성 설비로 저장" in asked[0] and "[＋ 빈 현황표]" in sheet.last_note
    assert sheet.pending and "apply_rows" not in gateway.done
    asked = answer(monkeypatch)
    assert sheet.save()
    settle(page)
    assert len(asked) == 1 and gateway.done["apply_rows"][0][0][1][0][0]["line"] == "C4H"
    cell(sheet, 0, C["line"]).setText("15L")         # 이 Site의 라인으로 고치면 묻지 않는다
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
    assert type(dialog.base_date).__name__ == "DatePickerEdit" and dialog.base_date.open_on_click  # 리플레이와 같은 달력 (11-12)
    dialog.site.setCurrentIndex(0)
    dialog.base_date.setDate(QDate(2026, 11, 12))
    dialog._accept()
    assert "Memory/Foundry" in dialog.error.text() and dialog.result() != QDialog.DialogCode.Accepted   # 필수
    dialog.areas.buttons()[1].setChecked(True)
    dialog._accept()
    assert dialog.values() == ("China", date(2026, 11, 12), "FOUNDRY")


def test_sort_follows_header_clicks(own):
    page, sheet = sheet_page(own)
    assert "Line → 설비군 → 설비 호기" in sheet.sort_label.text() and not sheet.sort_reset.isVisibleTo(sheet)
    sheet.table.selectColumn(C["version_raw"])
    sheet._header_clicked(C["version_raw"])   # 11-17: 첫 클릭은 열 전체 선택만 (정렬하지 않음)
    assert codes(sheet) == ["EC1", "EC2", "EC4"] and sheet.sort is None
    sheet._header_clicked(C["version_raw"])   # 한 번 더 누르면 SW 버전 오름차순
    assert codes(sheet) == ["EC4", "EC1", "EC2"] and "SW 버전 오름차순" in sheet.sort_label.text()
    assert sheet.table.horizontalHeaderItem(C["version_raw"]).text() == "SW 버전 ▲"
    sheet._header_clicked(C["version_raw"])   # 내림차순
    assert codes(sheet) == ["EC2", "EC1", "EC4"] and sheet.sort_reset.isVisibleTo(sheet)
    sheet._header_clicked(C["version_raw"])   # 기본 정렬로
    assert codes(sheet) == ["EC1", "EC2", "EC4"] and sheet.sort is None
    sheet.table.setCurrentCell(0, C["line"])  # 다른 칸을 고른 뒤 제목을 누르면 다시 선택부터
    sheet._header_clicked(C["version_raw"])
    assert sheet.sort is None


def test_failed_save_keeps_changes_and_reports_reason(own):
    gateway = FakeGateway()
    gateway.save = lambda edits, context=None: (_ for _ in ()).throw(ValueError("검토 후 자료가 바뀌었습니다"))
    failed = []
    page, sheet = sheet_page(own, gateway)
    page.save_failed.connect(lambda: failed.append(True))
    cell(sheet, 0, C["version_raw"]).setText("v9")
    sheet.save()
    settle(page)
    assert failed and "바뀌었습니다" in sheet.last_note and sheet.edits == {("version", 21): "v9"}
    assert sheet.save_button.isEnabled()


def test_group_change_sets_model_and_category_and_fixes_a_mismatched_model(own, monkeypatch):
    from dataclasses import replace

    answer(monkeypatch)
    gateway = FakeGateway()
    mismatched = replace(SHEET_ROWS.rows[1], model_name="M-O2", model_mismatch=True)   # EC1: 설비군 M-C4인데 모델명 M-O2
    original = replace(SHEET_ROWS.rows[0], model_name="M-C4 / Smart UI")                # EC2: 엑셀 원문 (같은 설비군)
    rows = (original, mismatched, SHEET_ROWS.rows[2])
    gateway.data_admin = (lambda original: lambda method, *a, **k: replace(SHEET_ROWS, rows=rows)
                          if method == "sheet_rows" else original(method, *a, **k))(gateway.data_admin)
    page, sheet = sheet_page(own, gateway)
    assert cell(sheet, 0, C["model_name"]).text() == "M-O2" and "설비군(M-C4)과 다릅니다" in cell(sheet, 0, C["model_name"]).toolTip()
    assert cell(sheet, 1, C["model_name"]).text() == "M-C4 / Smart UI" and "다릅니다" not in cell(sheet, 1, C["model_name"]).toolTip()   # 원문은 맞는 값
    editable = module.Qt.ItemFlag.ItemIsEditable
    assert cell(sheet, 0, C["model_name"]).flags() & editable and not cell(sheet, 1, C["model_name"]).flags() & editable  # 다른 줄만 맞출 수 있다
    cell(sheet, 0, C["model_name"]).setText("M-O3")          # 설비군과 다른 이름으로는 못 바꾼다
    assert "설비군과 같아야" in sheet.last_note and not sheet.edits
    cell(sheet, 0, C["model_name"]).setText("M-C4")          # 설비군 이름으로 맞춘다
    assert sheet.edits == {("model", 11): "M-C4"} and cell(sheet, 0, C["model_name"]).text() == "M-C4"
    assert sheet.undo() and not sheet.edits and cell(sheet, 0, C["model_name"]).text() == "M-O2"   # 한 단계로 되돌린다
    cell(sheet, 0, C["group"]).setText("M-O2")          # 다른 설비군이면 대분류 · 모델명이 따라 바뀐다
    assert sheet.edits == {("group", 11): 3} and [cell(sheet, 0, c).text() for c in (C["category"], C["group"], C["model_name"])] == ["OXIDE", "M-O2", "M-O2"]
    sheet.save()
    settle(page)
    assert gateway.done["apply_rows"][0][0][3] == [(11, 3)] and not gateway.saved   # 모델명은 서버가 설비군으로 맞춘다


def test_unregistered_line_is_registered_with_the_row_area_or_save_is_blocked(own, monkeypatch):
    answer(monkeypatch)
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    cell(sheet, 0, C["line"]).setText("NEW1")         # EC1(Memory)을 처음 보는 라인으로 → 묻지 않고 Memory로 등록
    assert sheet.unregistered_lines() == ["NEW1"] and "NEW1" in sheet._line_choices()   # 적은 라인은 목록에도 보인다
    assert sheet.save()
    settle(page)
    assert gateway.done["add_line"] == [(("화성", "NEW1", "MEMORY"), {})]
    sheet.add_row()                            # 새 행: Memory/Foundry를 비우면 새 라인이라 저장을 막는다
    new = sheet.table.rowCount() - 1
    for column, value in ((C["equipment_code"], "EC9"), (C["line"], "NEW2"), (C["group"], "M-C4")):
        cell(sheet, new, column).setText(value)
    assert cell(sheet, new, C["production_area"]).background().color() == module.NEW_ROW   # Memory/Foundry 필수 (모르는 라인이라 비어 있음)
    assert not sheet.save() and "Memory/Foundry" in sheet.last_note and sheet.pending
    cell(sheet, new, C["production_area"]).setText("Foundry")
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
    assert cell(sheet, new, C["production_area"]).text() == "Foundry"                   # 빈 현황표에서 고른 Memory/Foundry
    cell(sheet, new, C["line"]).setText("13L")                               # Memory/Foundry를 아는 라인 → 그 값으로 보이게 채움
    assert cell(sheet, new, C["production_area"]).text() == "Memory" and "라인의 Memory/Foundry로 자동" in cell(sheet, new, C["production_area"]).toolTip()
    cell(sheet, new, C["line"]).setText("99L")                               # 모르는 라인 → 현황표 기본값
    assert cell(sheet, new, C["production_area"]).text() == "Foundry"
    cell(sheet, new, C["group"]).setText("M-C4")
    assert cell(sheet, new, C["version_raw"]).text() == "v1.4F" and "최신 버전으로 자동" in cell(sheet, new, C["version_raw"]).toolTip()
    cell(sheet, new, C["production_area"]).setText("Memory")                             # Memory로 바꾸면 Memory 계열 최신으로
    assert cell(sheet, new, C["version_raw"]).text() == "v1.4"
    cell(sheet, new, C["version_raw"]).setText("v1.2")                              # 직접 적은 버전은
    cell(sheet, new, C["group"]).setText("M-O2")                              # 설비군을 바꿔도 그대로
    assert cell(sheet, new, C["version_raw"]).text() == "v1.2"
    assert sheet.undo() and cell(sheet, new, C["group"]).text() == "M-C4" and cell(sheet, new, C["version_raw"]).text() == "v1.2"


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
    assert "새 Site '베트남'" in sheet.last_note


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
    assert gateway.created == [("M-C7", "POLY", None)] and added


def test_group_table_edits_wait_for_save_including_builtin_names(own, monkeypatch):
    """설비군 이름 · 대분류를 표에서 고치고 저장한다. 기본 설비군도 ID를 유지하며 이름을 바꾼다."""
    answer(monkeypatch)
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_status(1)
    settle(page)
    panel = page.equipment_panel
    table = panel.group_table
    assert not hasattr(panel, "independent_button") and not hasattr(panel, "rename_button")

    def editable(row, column):
        return bool(table.item(row, column).flags() & Qt.ItemFlag.ItemIsEditable)

    assert editable(0, 1) and editable(1, 1) and editable(2, 1)
    assert editable(1, 0) and not editable(2, 0)                        # 대분류: 기본형만 (개조형은 따라감)
    table.item(0, 1).setText("C7X")                                     # C7 이름 (저장 전)
    assert table.item(0, 1).background().color() == module.SAVING and "update_groups" not in gateway.calls
    table.item(1, 1).setText("M-C4 새이름")
    assert panel.pending[1]["name"] == "M-C4 새이름" and table.item(1, 1).text() == "M-C4 새이름"
    row = next(r for r in range(table.rowCount()) if table.item(r, 1).text() == "M-O2")
    table.item(row, 0).setText("KCTC")                                  # M-O2 대분류 (저장 전)
    assert panel.save_button.text() == "저장 (3)" and not panel.add_group_button.isEnabled()
    assert panel.undo() and panel.save_button.text() == "저장 (2)" and panel.redo()
    assert panel.save()
    settle(page)
    assert gateway.done["update_groups"] == [(([(4, "C7X", None), (1, "M-C4 새이름", None), (3, None, "KCTC")],), {})] and not panel.pending
    table.selectRow(0)
    assert panel.delete_group()
    settle(page)
    assert gateway.done["delete_group"] == [((4,), {})]


def test_location_tree_groups_lines_by_memory_foundry_and_edits_them(own, monkeypatch):
    """11-12: 트리에서 이름을 바로 고치고(있는 라인 이름이면 합치기), 설비 표 Line 칸으로 설비를 옮긴 뒤 [저장]."""
    asked = answer(monkeypatch)
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_status(2)
    settle(page)
    panel = page.location_panel
    tree = panel.tree
    assert not hasattr(panel, "rename_button")
    assert [(tree.topLevelItem(i).text(0), tree.topLevelItem(i).text(1)) for i in range(tree.topLevelItemCount())] == [
        ("기흥", "등록 · 설비 없음"), ("평택", "1대"), ("화성", "2대")]
    pyeongtaek = tree.topLevelItem(1)
    assert [pyeongtaek.child(i).text(0) for i in range(pyeongtaek.childCount())] == ["Memory", "Foundry"]
    assert (pyeongtaek.child(1).child(0).text(0), pyeongtaek.child(1).child(0).text(1)) == ("S3", "등록")
    tree.setCurrentItem(pyeongtaek.child(0).child(0))   # 평택 · Memory · P1
    assert panel.fab_button.text() == "Foundry로 변경" and panel.equipment_table.rowCount() == 1
    assert panel.change_fab()
    settle(page)
    assert "설비 1대" in asked[-1] and gateway.done["set_line_fab"] == [(("평택", "P1", "FOUNDRY"), {})]

    # 1) 화성 15L을 이미 있는 13L 이름으로 → 합치기 (묻고, 저장 전)
    hwaseong = tree.topLevelItem(2)
    assert [hwaseong.child(0).child(i).text(0) for i in range(2)] == ["13L", "15L"]
    hwaseong.child(0).child(1).setText(0, "13L")
    until(lambda: panel.ops)
    assert "합칠까요" in asked[-1] and panel.ops[-1] == {"kind": "line", "site": "화성", "old": "15L", "new": "13L"}
    memory = tree.topLevelItem(2).child(0)
    assert memory.childCount() == 1 and memory.child(0).text(1) == "2대"
    assert memory.child(0).background(0).color() == module.SAVING
    # 2) 평택 설비 EO1을 설비 표 Line 칸에서 S3로 옮기기
    tree.setCurrentItem(tree.topLevelItem(1))
    assert panel.equipment_table.item(0, 0).text() == "EO1" and panel._line_choices() == ["P1", "S3"]
    panel.equipment_table.selectRow(0)
    panel.equipment_table.item(0, 3).setText("S3")
    assert panel.ops[-1] == {"kind": "move", "site": "평택", "to": "S3", "equipment": [[7, "P1"]]}
    assert panel.equipment_table.item(0, 3).text() == "S3"
    assert panel.equipment_table.item(0, 3).background().color() == module.SAVING
    # 3) 평택 P1 → 새 이름 P1A
    tree.topLevelItem(1).child(0).child(0).setText(0, "P1A")
    until(lambda: len(panel.ops) == 3)
    assert not panel.add_site_button.isEnabled() and not panel.delete_button.isEnabled()  # 저장 전에는 바로 저장 작업 잠금
    assert panel.save_button.text() == "저장 (3)" and "apply_locations" not in gateway.calls
    assert panel.undo() and len(panel.ops) == 2 and panel.redo() and len(panel.ops) == 3
    assert panel.save()
    settle(page)
    assert gateway.done["apply_locations"] == [(([
        {"kind": "line", "site": "화성", "old": "15L", "new": "13L"},
        {"kind": "move", "site": "평택", "to": "S3", "equipment": [[7, "P1"]]},
        {"kind": "line", "site": "평택", "old": "P1", "new": "P1A"}],), {})] and not panel.ops

    tree.setCurrentItem(tree.topLevelItem(1).child(1).child(0))  # 평택 · Foundry · S3
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
    assert gateway.done["add_line"] == [(("평택", "P4", "MEMORY"), {})]
    hwaseong = tree.topLevelItem(2)
    assert hwaseong.child(2).text(0) == "Memory/Foundry 모름" and hwaseong.child(2).isExpanded()
    tree.setCurrentItem(hwaseong.child(2).child(0))           # 화성 · 모름 · 19L → 어느 쪽인지 고른다
    assert panel.fab_button.text() == "Memory/Foundry 지정"
    monkeypatch.setattr(module.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Foundry", True)))
    assert panel.change_fab()
    settle(page)
    assert gateway.done["set_line_fab"][-1] == (("화성", "19L", "FOUNDRY"), {})


def test_location_rejects_existing_site_name_and_unknown_line(own):
    page = page_with(own)
    page.select_status(2)
    settle(page)
    panel = page.location_panel
    panel.tree.topLevelItem(1).setText(0, "화성")                 # Site는 합칠 수 없다
    until(lambda: panel.last_note)
    assert "이미 있는 Site" in panel.last_note and not panel.ops and panel.tree.topLevelItem(1).text(0) == "평택"
    panel.tree.setCurrentItem(panel.tree.topLevelItem(1))
    panel.equipment_table.item(0, 3).setText("없는라인")           # 그 Site에 없는 라인으로는 못 옮긴다
    assert "없는 라인" in panel.last_note and not panel.ops and panel.equipment_table.item(0, 3).text() == "P1"


def test_tab_line_ends_at_last_tab_and_deletion_leaves_its_old_scroll(own):
    """11-12: 탭 밑줄은 '변경 이력'에서 끝나고, 옮겨 붙인 삭제 화면은 원래 스크롤 영역 폭에 묶이지 않는다."""
    page = page_with(own)
    assert page.tabs.width() < page.width() // 2 and not page.tabs.usesScrollButtons()  # ◀ ▶ 버튼 없음
    scroll = own(QScrollArea())
    scroll.setWidgetResizable(True)
    deletion = QWidget()
    scroll.setWidget(deletion)
    page.attach_deletion(deletion)
    assert scroll.widget() is None and deletion.parentWidget() is page.delete_host
    for text in ("DB에 들어간 값을 가져오기 3단계", "최신 버전이 위에 옵니다", "줄을 누르면 오른쪽에서",
                 "설비군을 추가하면 DB에 저장되고", "Site · 라인을 추가하면 DB에 저장되고"):
        assert not any(text in w.text() for w in page.findChildren(QLabel)), text   # 설명 문구 삭제 (11-12)


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
    assert page.status_stack.currentIndex() == 2 and page.location_panel.isVisible()  # 위치 탭에 머문다
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
    # 노란색은 옮긴 Item(보호동작 수정)의 No 칸에만. 밀려난 Item은 번호만 바뀌어 칠하지 않는다 (11-12)
    assert panel.table.item(0, 2).background().color() == module.SAVING
    assert panel.table.item(1, 2).background().color() != module.SAVING
    assert not panel.move_item(2, 0) and "같은 버전" in panel.last_note
    forms = iter([("v4.60.1.3", "2026-06-01"), ("새 기능", "M-C4")])

    class Form:
        def __init__(self, *_):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return next(forms)

    monkeypatch.setattr(module, "_FormDialog", Form)
    monkeypatch.setattr(module, "_VersionDialog", Form)   # 버전 추가는 달력이 있는 창 (11-12)
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
    assert panel.steps[-1][0] == "saved" and not panel.pending and "저장했습니다" in panel.last_note
    assert panel.undo()                                         # 마지막 저장 전체를 되돌린다
    settle(page)
    assert gateway.done["undo_release"] == [(({"versions": [102]},), {})]
    assert gateway.saved[-1][0][0].changes == {"content": "Recipe 확인"}
    assert not panel.steps and "되돌렸습니다" in panel.last_note
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
    assert "모르는 설비군" in panel.last_note and panel.table.item(0, 4).text() == "M-C4"
    panel.table.item(0, 1).setText("4월 7일")
    assert "날짜" in panel.last_note and panel.table.item(0, 1).text() == "2026-04-06"
    panel.table.cellClicked.emit(0, 1)                           # Date 칸은 한 번만 눌러도 달력 편집기가 열린다
    assert panel.table.state() == module.QAbstractItemView.State.EditingState
    assert panel.table.horizontalHeader().sectionResizeMode(3) == module.QHeaderView.ResizeMode.Stretch  # Content가 남는 폭
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
    for column, value in ((C["equipment_code"], "EC9"), (C["line"], "99L"), (C["group"], "M-C4")):
        cell(sheet, new, column).setText(value)
    assert cell(sheet, new, C["production_area"]).text() == "" and cell(sheet, new, C["version_raw"]).text() == ""   # Memory를 가정해 버전을 채우지 않는다
    assert not sheet.save() and "Memory/Foundry" in sheet.last_note and "apply_rows" not in gateway.done
    cell(sheet, new, C["production_area"]).setText("Memory")
    assert cell(sheet, new, C["version_raw"]).text() == "v1.4"                       # 정해진 뒤에 최신 버전을 채운다


def test_selected_new_row_still_shows_required_and_filled_colors(own):
    page, sheet = sheet_page(own)
    sheet.add_row()
    new = sheet.table.rowCount() - 1
    cell(sheet, new, C["line"]).setText("13L")                     # 노란 칸 (채움)
    sheet.table.selectRow(new)                             # 입력 중인 줄을 고른 상태
    sheet.table.viewport().repaint()
    image = sheet.table.viewport().grab().toImage()

    def color(column):
        rect = sheet.table.visualRect(sheet.table.model().index(new, column))
        return image.pixelColor(rect.center())

    required, filled = color(C["equipment_code"]), color(C["line"])                  # 설비 호기(분홍 필수) · Line(노랑)
    assert required != filled                              # 고른 줄도 칸 색이 구분된다 (선택 색이 덮지 않음)
    assert required.red() > required.green() and filled.green() > filled.blue()   # 분홍 · 노랑 기운이 남아 있다
    assert isinstance(sheet.table.itemDelegate(), module._KeepColorsDelegate)
    assert isinstance(page.release_panel.table.itemDelegate(), module._KeepColorsDelegate)


def test_sheet_is_opened_by_batch_and_base_date():
    from datetime import date as day

    assert module._sheet_key(7, day(2026, 10, 21)) == "7|2026-10-21" and module._parse_key("7|2026-10-21") == (7, day(2026, 10, 21))
    assert module._parse_key(module._sheet_key(8, None)) == (8, None)


def test_sheet_rows_request_carries_the_chosen_base_date(own):
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    assert gateway.done["sheet_rows"][-1] == ((7, date(2026, 11, 1)), {})   # 시연 seed처럼 한 입력 작업에 기준일이 여럿이어도 고른 날만
    assert sheet.sheet_combo.currentData() == "7|2026-11-01"



# --- 11-16: 가져오기 검토 표와 같은 열 · PM 한 줄 · DB 값 모두 수정 ------------------------------------------------
def _full(sid, eid, vid, code, line, gid, group, version, family, fid, *, ui=None, pm="", pm_id=None, process=""):
    return SheetRow(sid, eid, vid, code, line, gid, group, group, "MEMORY", version, family, pm, bool(pm), {}, {},
                    pm_id=pm_id, family_id=fid, ui_type=ui, process_name=process)


FULL_ROWS = SheetRows(
    7, "현황 2026-11-01", "마스터.xlsx", "화성", date(2026, 11, 1),
    (_full(11, 5, 21, "EC1", "13L", 1, "M-C4", "v1.2", "C4", 10),
     _full(16, 10, 26, "EC6", "13L", 5, "M-C3", "v3.52_20", "C3 통합UI", 31, ui="INTEGRATED"),
     _full(15, 9, 25, "EC5", "15L", 3, "M-O2", "v3.5", "O2", 20, pm="PM1", pm_id=31, process="V6 CS"),
     _full(15, 9, 25, "EC5", "15L", 3, "M-O2", "v3.5", "O2", 20, pm="PM2", pm_id=32, process="V6 CS")),
    ((1, "M-C4", "POLY"), (3, "M-O2", "OXIDE"), (5, "M-C3", "POLY")), ("13L", "15L"),
    families={1: ((10, "C4", None),), 3: ((20, "O2", None), (22, "O2(파운드리)", None)),
              5: ((30, "C3 구UI", "LEGACY"), (31, "C3 통합UI", "INTEGRATED"))})


class FullGateway(FakeGateway):
    def data_admin(self, method, *args, **kwargs):
        result = super().data_admin(method, *args, **kwargs)
        return FULL_ROWS if method == "sheet_rows" else result


def saved_edits(gateway):
    return sorted((e.table, sorted(e.key.items()), e.changes) for e in gateway.saved[0][0])


def test_pm_lines_family_ui_and_details_are_shown_and_editable(own, monkeypatch):
    answer(monkeypatch)
    gateway = FullGateway()
    page, sheet = sheet_page(own, gateway)
    assert codes(sheet) == ["EC6", "EC1", "EC5", "EC5"]                       # Line → 설비군(M-C3 < M-C4) → PM마다 한 줄
    assert [cell(sheet, r, C["pm"]).text() for r in (2, 3)] == ["PM1", "PM2"]
    assert cell(sheet, 0, C["ui_type"]).text() == "통합UI" and cell(sheet, 2, C["process_name"]).text() == "V6 CS"
    editable = module.Qt.ItemFlag.ItemIsEditable
    assert all(cell(sheet, 2, C[f]).flags() & editable for f in ("group", "family", "version_raw"))  # PM 줄도 고친다
    assert not cell(sheet, 2, C["pm"]).flags() & editable and "고정 위치" in cell(sheet, 2, C["pm"]).toolTip()
    cell(sheet, 0, C["family"]).setText("C3 구UI")                          # SW 계열을 고르면 UI 구분이 따라간다
    assert cell(sheet, 0, C["ui_type"]).text() == "구UI"
    cell(sheet, 0, C["family"]).setText("O2")                               # 다른 설비군의 계열은 못 고른다
    assert "이 설비군의 계열이 아닙니다" in sheet.last_note and cell(sheet, 0, C["family"]).text() == "C3 구UI"
    cell(sheet, 2, C["process_name"]).setText("12.9/14.0")                  # 공정은 설비의 값이라 같은 설비 줄이 함께
    assert cell(sheet, 3, C["process_name"]).text() == "12.9/14.0"
    cell(sheet, 3, C["version_raw"]).setText("v3.6")                        # PM2만의 버전
    cell(sheet, 1, C["geometry_type"]).setText("6각")
    assert sheet.save()
    settle(page)
    assert saved_edits(gateway) == [
        ("equipment_pm", [("pm_id", 32)], {"version_raw": "v3.6", "release_version_id": None}),
        ("equipment_snapshot", [("snapshot_id", 11)], {"geometry_type": "6각"}),
        ("equipment_snapshot", [("snapshot_id", 15)], {"process_name": "12.9/14.0"}),
        ("equipment_version", [("equipment_version_id", 26)], {"family_id": 30, "release_version_id": None})]
    assert "apply_rows" not in gateway.done                                # 설비군을 바꾸지 않았으니 행 저장은 없다


def test_pm_group_change_picks_the_matching_family_and_saves_on_the_pm(own, monkeypatch):
    answer(monkeypatch)
    gateway = FullGateway()
    page, sheet = sheet_page(own, gateway)
    cell(sheet, 2, C["group"]).setText("M-C4")                              # PM1만 M-C4로 → 계열 C4 하나라 자동
    assert cell(sheet, 2, C["family"]).text() == "C4" and cell(sheet, 3, C["group"]).text() == "M-O2"
    assert sheet.save()
    settle(page)
    assert saved_edits(gateway) == [("equipment_pm", [("pm_id", 31)],
                                     {"group_id": 1, "family_id": 10, "release_version_id": None})]
    assert "apply_rows" not in gateway.done                                # PM 설비군은 관리 저장 (regroup 아님)


def test_new_row_takes_pm_process_family_and_details(own, monkeypatch):
    answer(monkeypatch)
    gateway = FullGateway()
    page, sheet = sheet_page(own, gateway)
    sheet.add_row()
    new = sheet.table.rowCount() - 1
    editable = module.Qt.ItemFlag.ItemIsEditable
    assert not cell(sheet, new, C["family"]).flags() & editable             # 설비군을 고르기 전에는 계열을 못 고른다
    assert cell(sheet, new, C["base_date"]).text() == "2026-11-01" and cell(sheet, new, C["site"]).text() == "화성"
    for field, value in (("equipment_code", "EC9"), ("line", "13L"), ("production_area", "Memory"), ("group", "M-C3"),
                         ("family", "C3 구UI"),
                         ("pm", "PM1"), ("process_name", "SI_AAA"), ("version_raw", "v3.52_13"),
                         ("geometry_type", "5각"), ("modification_type", "CIP")):
        cell(sheet, new, C[field]).setText(value)
    assert cell(sheet, new, C["ui_type"]).text() == "구UI"
    cell(sheet, new, C["group"]).setText("M-C4")                            # 설비군을 바꾸면 고른 계열은 비운다
    assert cell(sheet, new, C["family"]).text() == ""
    cell(sheet, new, C["group"]).setText("M-C3")
    cell(sheet, new, C["family"]).setText("C3 구UI")
    cell(sheet, new, C["version_raw"]).setText("v3.52_13")
    assert sheet.save()
    settle(page)
    values = gateway.done["apply_rows"][0][0][1][0][0]
    assert {k: values[k] for k in ("pm_name", "family_id", "process_name", "geometry_type", "modification_type")} == {
        "pm_name": "PM1", "family_id": 30, "process_name": "SI_AAA", "geometry_type": "5각", "modification_type": "CIP"}
