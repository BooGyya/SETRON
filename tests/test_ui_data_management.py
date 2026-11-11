"""데이터 관리 화면(11-10 개편) 프론트 검증. DB 대신 가짜 게이트웨이를 쓴다."""

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
GRID = SheetGrid(7, "현황 2026-11-01", "마스터.xlsx",
                 {"A2": "No", "B2": "Line", "C2": "호기", "D2": "SW Ver", "A3": 1, "B3": "13L", "C3": "EC1", "D3": "v1.2"},
                 {"B3": CellBinding("equipment_snapshot", {"snapshot_id": 11}, "line", "13L", True, "EC1 · Line"),
                  "C3": CellBinding("equipment", {"equipment_id": 5}, "equipment_code", "EC1", True, "EC1 · 호기"),
                  "D3": CellBinding("equipment_version", {"equipment_version_id": 21}, "version_raw", "v1.2", True, "EC1 · SW Ver"),
                  "A3": CellBinding("equipment_snapshot", {"snapshot_id": 11}, "base_date", "2026-11-01", False, "EC1 · 기준일")})
GROUPS = (GroupRow(1, "M-C4", "POLY", None, None, 2), GroupRow(2, "M-C4 DFB", "POLY", 1, "M-C4", 1),
          GroupRow(3, "M-O2", "OXIDE", None, None, 1))
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
                        None, None, None))


class FakeGateway:
    def __init__(self):
        self.calls, self.saved, self.created = [], [], []

    def data_admin(self, method, *args):
        self.calls.append(method)
        if method == "create_group":
            self.created.append(args)
            return GroupRow(9, args[0], args[1], args[2], None, 0)
        return {"status_sheets": (SHEET,), "sheet_grid": GRID, "equipment_overview": (GROUPS, EQUIPMENT),
                "release_families": FAMILIES, "release_rows": ROWS, "history": HISTORY}[method]

    def get(self, table, key):
        return SimpleNamespace(revision=f"rev-{table}-{sorted(key.items())}")

    def save(self, edits, context=None):
        self.saved.append((edits, context))
        return SimpleNamespace(records=(), audit_batch_id=41)


def page_with(own, gateway=None, **kwargs):
    page = own(DataManagementPage(gateway or FakeGateway(), site_store={}, context_provider=lambda: {"n": 1}, **kwargs))
    page.resize(1400, 900)
    page.show()
    return page


def settle(page):
    panels = (page.sheet_panel, page.equipment_panel, page.location_panel, page.release_panel, page.history_panel)
    until(lambda: not page.saving and not any(p.request.busy for p in panels))


def yes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


def test_three_fixed_tabs_and_status_subtabs_load_lazily(own):
    gateway = FakeGateway()
    page = page_with(own, gateway)
    assert [page.tabs.tabText(i) for i in range(3)] == ["설비현황", "릴리즈노트", "변경 이력"]
    assert [b.text() for b in page.status_buttons] == ["현황표", "설비", "위치", "저장 자료 삭제"]
    page.enter()
    settle(page)
    assert gateway.calls == ["status_sheets", "sheet_grid"]  # 처음에는 보이는 현황표만 읽는다
    page.select_tab(2)
    settle(page)
    assert "history" in gateway.calls and len(page.history_panel.entries) == 2
    assert page.tabs.tabText(2) == "변경 이력"


def test_sheet_grid_keeps_excel_layout_and_only_bound_cells_are_editable(own):
    page = page_with(own)
    page.enter()
    settle(page)
    sheet = page.sheet_panel
    assert "마스터.xlsx" in sheet.source.text() and sheet.sheet_combo.currentText().startswith("2026-11-01 · 화성")
    assert [sheet.table.horizontalHeaderItem(c).text() for c in range(4)] == ["A", "B", "C", "D"]
    assert sheet.table.verticalHeaderItem(0).text() == "2"  # 원래 행 번호
    assert set(sheet.originals) == {"B3", "C3", "D3"}  # 기준일(A3)·머리글은 고칠 수 없다
    assert sheet.table.item(1, 3).text() == "v1.2" and not sheet.save_button.isEnabled()


def test_editing_cells_groups_changes_and_saves_with_fresh_revisions(own, monkeypatch):
    yes(monkeypatch)
    gateway = FakeGateway()
    saved = []
    page = page_with(own, gateway)
    page.saved.connect(saved.append)
    page.enter()
    settle(page)
    sheet = page.sheet_panel
    sheet.table.item(1, 3).setText("v1.3")   # D3 SW Ver
    sheet.table.item(1, 1).setText("15L")    # B3 Line
    assert sheet.save_button.text() == "수정 저장 (2칸)" and sheet.save_button.isEnabled()
    sheet.table.item(1, 1).setText("13L")    # 원래 값으로 되돌리면 수정에서 빠진다
    assert sheet.save_button.text() == "수정 저장 (1칸)"
    sheet.table.item(1, 1).setText("15L")
    assert sheet.save()
    settle(page)
    edits, context = gateway.saved[0]
    assert isinstance(edits, tuple) and all(isinstance(e, RecordEdit) for e in edits) and context == {"n": 1}
    by_table = {e.table: e for e in edits}
    assert by_table["equipment_version"].changes == {"version_raw": "v1.3", "release_version_id": None}  # 연결은 다시 판정
    assert by_table["equipment_snapshot"].changes == {"line": "15L"}
    assert by_table["equipment_snapshot"].revision.startswith("rev-equipment_snapshot")
    assert saved and "저장했습니다" in sheet.notice.text() and not page.history_panel.loaded


def test_failed_save_keeps_edits_and_reports_reason(own, monkeypatch):
    yes(monkeypatch)
    gateway = FakeGateway()
    gateway.save = lambda edits, context=None: (_ for _ in ()).throw(ValueError("검토 후 자료가 바뀌었습니다"))
    failed = []
    page = page_with(own, gateway)
    page.save_failed.connect(lambda: failed.append(True))
    page.enter()
    settle(page)
    page.sheet_panel.table.item(1, 3).setText("v9")
    page.sheet_panel.save()
    settle(page)
    assert failed and "바뀌었습니다" in page.sheet_panel.notice.text() and page.sheet_panel.edits == {"D3": "v9"}


def test_equipment_groups_filter_list_and_add_group(own, monkeypatch):
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_status(1)
    settle(page)
    panel = page.equipment_panel
    assert panel.group_table.rowCount() == 2  # 기본형만, 개조형은 '개조형' 칸에
    assert panel.group_table.item(0, 2).text() == "M-C4 DFB" and panel.group_table.item(0, 3).text() == "3대"
    panel.group_table.selectRow(0)
    assert panel.equipment_table.rowCount() == 2  # C4 + 개조형 C4 DFB 설비
    panel.group_table.clearSelection()
    rows = {panel.equipment_table.item(r, 0).text(): panel.equipment_table.item(r, 2).text()
            for r in range(panel.equipment_table.rowCount())}
    assert len(rows) == 4 and rows["OLD"] == "관측 없음"

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
    assert gateway.created == [("M-C7", "POLY", None)] and added and added[0].name == "M-C7"


def test_add_group_dialog_requires_same_category_for_variant(own):
    dialog = own(module.AddGroupDialog(None, GROUPS))
    dialog.name.setText("M-C4 X")
    dialog.category.setCurrentIndex(dialog.category.findData("OXIDE"))
    dialog.parent_combo.setCurrentIndex(dialog.parent_combo.findData(1))
    dialog._accept()
    assert "같은 분류" in dialog.error.text() and dialog.result() != QDialog.DialogCode.Accepted


def test_location_tree_from_observations_and_display_only_site(own, monkeypatch):
    store = {}
    page = own(DataManagementPage(FakeGateway(), site_store=SimpleNamespace(
        value=lambda key, default=None: store.get(key, default), setValue=lambda key, value: store.__setitem__(key, value))))
    page.show()
    page.select_status(2)
    settle(page)
    tree = page.location_panel.tree
    sites = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert sites == ["평택", "화성"]  # 관측이 없는 설비(OLD)는 위치에 넣지 않는다
    monkeypatch.setattr(module.QInputDialog, "getText", staticmethod(lambda *a, **k: ("기흥", True)))
    assert page.location_panel.add_site()
    assert store[module.SITE_KEY] == ["기흥"] and "표시 전용" in tree.topLevelItem(0).text(0)
    monkeypatch.setattr(module.QInputDialog, "getText", staticmethod(lambda *a, **k: ("화성", True)))
    assert not page.location_panel.add_site() and "이미 있는" in page.location_panel.notice.text()


def test_release_grid_versions_with_items_toggle_edit_and_risk(own, monkeypatch):
    yes(monkeypatch)
    gateway = FakeGateway()
    opened = []
    page = page_with(own, gateway, risk_opener=lambda family, item: opened.append((family, item)) or True)
    page.select_tab(1)
    settle(page)
    panel = page.release_panel
    assert panel.tree.topLevelItem(0).child(0).text(0).startswith("↳ M-C4 DFB")  # 개조형은 상위 계열 아래
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0).child(0))
    settle(page)
    assert panel.family_title.text() == "C4 · M-C4 DFB" and panel.table.rowCount() == 3
    assert panel.table.item(0, 0).text() == "▼ v4.60.1.1" and panel.table.item(1, 0).text() == ""
    assert panel.table.item(1, 3).font().bold() and not panel.table.item(0, 3).font().bold()  # 개조형 적용 Item 강조
    assert panel.table.item(1, 5).text() == "결함"
    panel._toggle_version(0, 0)
    assert panel.table.isRowHidden(1) and panel.table.item(0, 0).text() == "▶ v4.60.1.1"
    panel._toggle_version(0, 0)
    assert not panel.table.isRowHidden(1)
    panel.table.item(1, 3).setText("보호동작 오류 수정")
    panel.table.item(1, 4).setText("M-C4")
    panel.table.item(0, 1).setText("2026-04-07")
    assert panel.release_save.text() == "수정 저장 (3칸)"
    assert panel.save()
    settle(page)
    edits = {e.table: e for e in gateway.saved[0][0]}
    assert edits["item"].changes == {"content": "보호동작 오류 수정"}
    assert edits["release_version_item"].changes == {"target_scope": "LISTED", "target_group_ids": [1]}
    assert edits["release_version"].changes == {"released_at": date(2026, 4, 7)}
    panel.table.selectRow(1)
    assert panel.risk_button.isEnabled() and "보호동작" in panel.item_title.text()
    assert panel.open_risk() and opened == [(10, 501)]


def test_release_bad_model_name_or_date_is_rejected_before_save(own):
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_tab(1)
    settle(page)
    page.release_panel.tree.setCurrentItem(page.release_panel.tree.topLevelItem(0))
    settle(page)
    page.release_panel.table.item(0, 4).setText("없는모델")
    assert not page.release_panel.save() and "모르는 설비군" in page.release_panel.notice.text() and not gateway.saved


def test_history_shows_time_file_cell_and_before_after_with_filters(own):
    page = page_with(own)
    page.select_tab(2)
    settle(page)
    panel = page.history_panel
    assert panel.table.rowCount() == 2
    assert [panel.table.item(0, c).text() for c in range(6)] == [
        "2026-11-10 14:02", "마스터.xlsx · 현황 2026-11-01 · D3", "EC1", "SW Ver", "v1.1", "v1.2"]
    assert panel.table.item(1, 1).text() == "파일 이름 없음"
    panel.table.selectRow(0)
    assert "D3" in panel.detail.text() and panel.before_after.item(0, 2).text() == "v1.2"
    panel.kind.setCurrentIndex(panel.kind.findText("설비군"))
    assert panel.table.rowCount() == 1 and panel.table.item(0, 2).text() == "M-C7"
    panel.kind.setCurrentIndex(0)
    panel.period.setCurrentIndex(0)  # 최근 7일
    assert panel.table.rowCount() <= 2


def test_multiline_labels_get_full_height(own):
    page = page_with(own)
    page.select_tab(1)
    info = page.release_panel.item_info
    info.setText("첫 줄\n둘째 줄\n셋째 줄\n넷째 줄")
    until(lambda: info.height() >= info.heightForWidth(info.width()) > info.fontMetrics().height() * 3)
    assert all(not w.wordWrap() or w.sizePolicy().horizontalPolicy().name == "Ignored" for w in page.findChildren(QLabel))


def test_app_sidebar_has_single_data_menu_and_delete_moved_from_import(own):
    window = own(create_window())
    visible = [key for key, nav in window.nav.items() if nav.isVisible() or nav.isVisibleTo(window)]
    assert "data" in visible and "versions" not in visible and "management" not in visible
    assert window.navigate("data") and window.header.title.text() == "데이터 관리"
    page = window.pages["data"]
    assert page.unavailable.isVisible() or page.unavailable.isVisibleTo(window)  # 합성 예시에는 DB가 없다
    importer = window.pages["import"]
    assert not importer.tabs.isTabVisible(1) and page.deletion is importer.deletion
    page.select_tab(0)
    page.select_status(3)
    assert importer.deletion.isVisibleTo(window)
    assert window.navigate("versions") and window.navigate("management")  # 다른 화면의 내부 이동은 그대로
