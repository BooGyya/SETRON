"""데이터 관리 화면(11-10 개편, 11-11 2차 수정) 프론트 검증. DB 대신 가짜 게이트웨이를 쓴다.

11-11: 고치기·더하기·지우기는 확인 단계 없이 바로 DB에 반영(행 삭제만 한 번 묻는다), 현황표 행 삭제,
설비군 개조형도 한 줄씩, 설비 표는 모델·Site·Line, Site 등록은 DB, 릴리즈노트 위험 지정 제거, 변경 이력 열 정리.
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
GRID = SheetGrid(7, "현황 2026-11-01", "마스터.xlsx",
                 {"A1": None, "B1": None, "A2": "No", "B2": "Line", "C2": "호기", "D2": "SW Ver",
                  "A3": 1, "B3": "13L", "C3": "EC1", "D3": "v1.2", "A4": 2, "B4": "15L", "C4": "EC2", "D4": "v1.3"},
                 {"B3": CellBinding("equipment_snapshot", {"snapshot_id": 11}, "line", "13L", True, "EC1 · Line"),
                  "C3": CellBinding("equipment", {"equipment_id": 5}, "equipment_code", "EC1", True, "EC1 · 호기"),
                  "D3": CellBinding("equipment_version", {"equipment_version_id": 21}, "version_raw", "v1.2", True, "EC1 · SW Ver"),
                  "D4": CellBinding("equipment_version", {"equipment_version_id": 22}, "version_raw", "v1.3", True, "EC2 · SW Ver")},
                 {"B3": 11, "C3": 11, "D3": 11, "D4": 12},
                 {"A5": "삭제됨 · EC3", "D5": "삭제됨 · EC3"})
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
                        None, None, None),
           HistoryEntry(38, datetime(2026, 1, 1, 8, 0), "삭제", "equipment_snapshot", {"snapshot_id": 13}, "EC3",
                        "현황표 행", "화성 · 17L · 2026-11-01", "삭제", "마스터.xlsx", "현황 2026-11-01", "A5~D5"))


class FakeGateway:
    def __init__(self):
        self.calls, self.saved, self.created, self.deleted, self.sites = [], [], [], [], ["기흥"]

    def data_admin(self, method, *args):
        self.calls.append(method)
        if method == "create_group":
            self.created.append(args)
            return GroupRow(9, args[0], args[1], args[2], None, 0)
        if method == "delete_rows":
            self.deleted.append(args)
            return len(args[1])
        if method == "add_site":
            self.sites.append(args[0])
            return args[0]
        if method == "sites":
            return tuple(self.sites)
        return {"status_sheets": (SHEET,), "sheet_grid": GRID, "equipment_overview": (GROUPS, EQUIPMENT),
                "release_families": FAMILIES, "release_rows": ROWS, "history": HISTORY}[method]

    def get(self, table, key):
        return SimpleNamespace(revision=f"rev-{table}-{sorted(key.items())}")

    def save(self, edits, context=None):
        self.saved.append((edits, context))
        return SimpleNamespace(records=(), audit_batch_id=41)


def page_with(own, gateway=None, **kwargs):
    page = own(DataManagementPage(gateway or FakeGateway(), context_provider=lambda: {"n": 1}, **kwargs))
    page.resize(1400, 900)
    page.show()
    return page


def settle(page):
    until(lambda: not page.saving and not any(p.request.busy for p in page.panels))


def answer(monkeypatch, reply=QMessageBox.StandardButton.Yes):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[2]) or reply))
    return asked


def test_three_fixed_tabs_and_status_subtabs_load_lazily(own):
    gateway = FakeGateway()
    page = page_with(own, gateway)
    assert [page.tabs.tabText(i) for i in range(3)] == ["설비현황", "릴리즈노트", "변경 이력"]
    assert [b.text() for b in page.status_buttons] == ["현황표", "설비", "위치", "가져온 엑셀 삭제"]
    page.enter()
    settle(page)
    assert gateway.calls == ["status_sheets", "sheet_grid"]  # 처음에는 보이는 현황표만 읽는다
    page.select_tab(2)
    settle(page)
    assert "history" in gateway.calls and len(page.history_panel.entries) == 3


def test_sheet_grid_hides_empty_rows_marks_header_and_deleted_rows(own):
    page = page_with(own)
    page.enter()
    settle(page)
    sheet = page.sheet_panel
    assert "마스터.xlsx" in sheet.source.text() and sheet.sheet_combo.currentText().startswith("2026-11-01 · 화성")
    assert [sheet.table.horizontalHeaderItem(c).text() for c in range(4)] == ["A", "B", "C", "D"]
    # 엑셀 1행처럼 값이 하나도 없는 줄은 빼고, 행 번호는 엑셀 그대로 둔다
    assert [sheet.table.verticalHeaderItem(r).text() for r in range(sheet.table.rowCount())] == ["2", "3", "4", "5"]
    assert sheet.table.item(0, 0).font().bold()  # 머리글 행
    assert set(sheet.originals) == {"B3", "C3", "D3", "D4"}  # 연결된 칸만 고칠 수 있다
    deleted = sheet.items["D5"]
    assert deleted.font().strikeOut() and "삭제됨 · EC3" in deleted.toolTip()
    assert sum(sheet.table.columnWidth(c) for c in range(4)) >= sheet.table.viewport().width() - 4  # 표 폭을 채운다
    assert not sheet.delete_button.isEnabled()


def test_editing_a_cell_saves_right_away_without_any_question(own, monkeypatch):
    asked = answer(monkeypatch)
    gateway = FakeGateway()
    saved = []
    page = page_with(own, gateway)
    page.saved.connect(saved.append)
    page.enter()
    settle(page)
    sheet = page.sheet_panel
    sheet.table.item(1, 3).setText("v1.3a")   # D3 SW Ver → 바로 저장
    assert page.saving
    sheet.table.item(1, 1).setText("15L")     # 저장 중에 고친 B3는 앞 저장이 끝나면 이어서 저장
    settle(page)
    assert asked == [] and len(gateway.saved) == 2  # '저장할까요' · '버릴까요' 같은 질문이 없다
    (first, context), (second, _) = gateway.saved
    assert isinstance(first, tuple) and all(isinstance(e, RecordEdit) for e in first) and context == {"n": 1}
    assert first[0].changes == {"version_raw": "v1.3a", "release_version_id": None}  # 릴리즈 연결은 다시 판정
    assert second[0].table == "equipment_snapshot" and second[0].changes == {"line": "15L"}
    assert second[0].revision.startswith("rev-equipment_snapshot")
    assert sheet.originals["D3"] == "v1.3a" and not sheet.edits
    assert sheet.items["D3"].background().color() == module.SAVED and "저장됨" in sheet.items["D3"].toolTip()
    assert len(saved) == 2 and "저장했습니다" in sheet.notice.text() and not page.history_panel.loaded
    sheet.table.item(1, 3).setText("v1.3a")   # 같은 값은 다시 저장하지 않는다
    settle(page)
    assert len(gateway.saved) == 2


def test_failed_save_restores_db_value_and_reports_reason(own, monkeypatch):
    gateway = FakeGateway()
    gateway.save = lambda edits, context=None: (_ for _ in ()).throw(ValueError("검토 후 자료가 바뀌었습니다"))
    failed = []
    page = page_with(own, gateway)
    page.save_failed.connect(lambda: failed.append(True))
    page.enter()
    settle(page)
    page.sheet_panel.table.item(1, 3).setText("v9")
    settle(page)
    sheet = page.sheet_panel
    assert failed and "바뀌었습니다" in sheet.notice.text() and not sheet.edits
    assert sheet.items["D3"].text() == "v1.2" and sheet.originals["D3"] == "v1.2"


def test_delete_selected_rows_asks_once_then_deletes_owner_snapshots(own, monkeypatch):
    asked = answer(monkeypatch, QMessageBox.StandardButton.No)
    gateway = FakeGateway()
    saved = []
    page = page_with(own, gateway)
    page.saved.connect(saved.append)
    page.enter()
    settle(page)
    sheet = page.sheet_panel
    sheet.table.selectRow(1)          # 3행 (EC1)
    assert sheet.selected_rows() == {11: "EC1"} and sheet.delete_button.isEnabled()
    assert not sheet.delete_selected() and not gateway.deleted and "EC1" in asked[0]  # 아니요면 지우지 않는다
    answer(monkeypatch)
    sheet.table.clearSelection()
    sheet.table.item(1, 0).setSelected(True)   # 연결 없는 칸(No)만 고르면 지울 행이 없다
    assert not sheet.delete_button.isEnabled()
    sheet.table.selectRow(1)
    sheet.table.item(2, 3).setSelected(True)   # 4행 (EC2)도 함께
    assert sheet.delete_selected()
    settle(page)
    assert gateway.deleted == [(7, (11, 12))] and saved and saved[0].value == 2
    assert "설비 2대의 행을 지웠습니다" in sheet.notice.text()


def test_equipment_groups_one_row_each_and_list_shows_model_site_line(own, monkeypatch):
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_status(1)
    settle(page)
    panel = page.equipment_panel
    names = [panel.group_table.item(r, 1).text().strip() for r in range(panel.group_table.rowCount())]
    assert names == ["M-C4", "↳ M-C4 DFB", "M-O2"]  # 개조형도 상위 설비군 바로 아래 한 줄
    assert panel.group_table.item(1, 2).text() == "M-C4" and panel.group_table.item(0, 3).text() == "2대"
    headers = [panel.equipment_table.horizontalHeaderItem(c).text() for c in range(panel.equipment_table.columnCount())]
    assert headers == ["설비 ID", "모델", "Site", "Line", "설치 기록", "최근 기준일"]
    panel.group_table.selectRow(1)
    assert panel.equipment_table.rowCount() == 1 and panel.equipment_table.item(0, 0).text() == "EC2"
    panel.group_table.clearSelection()
    rows = {panel.equipment_table.item(r, 0).text(): (panel.equipment_table.item(r, 2).text(), panel.equipment_table.item(r, 3).text())
            for r in range(panel.equipment_table.rowCount())}
    assert rows["EC1"] == ("화성", "13L") and rows["OLD"] == ("관측 없음", "")

    class Accept:
        def __init__(self, *_):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return "M-C7", "POLY", None

    monkeypatch.setattr(module, "AddGroupDialog", Accept)
    added, saved = [], []
    page.group_added.connect(added.append)
    page.saved.connect(saved.append)
    assert panel.add_group()
    settle(page)
    assert gateway.created == [("M-C7", "POLY", None)] and added and added[0].name == "M-C7"
    assert saved and "대시보드·히트맵" in panel.notice.text()  # 저장 알림으로 앱이 조회 선택지를 다시 읽는다


def test_add_group_dialog_requires_same_category_for_variant(own):
    dialog = own(module.AddGroupDialog(None, GROUPS))
    dialog.name.setText("M-C4 X")
    dialog.category.setCurrentIndex(dialog.category.findData("OXIDE"))
    dialog.parent_combo.setCurrentIndex(dialog.parent_combo.findData(1))
    dialog._accept()
    assert "같은 분류" in dialog.error.text() and dialog.result() != QDialog.DialogCode.Accepted


def test_location_tree_shows_registered_sites_and_adds_site_to_db(own, monkeypatch):
    gateway = FakeGateway()
    saved = []
    page = page_with(own, gateway)
    page.saved.connect(saved.append)
    page.select_status(2)
    settle(page)
    tree = page.location_panel.tree
    sites = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert sites == ["기흥  (등록 · 설비 없음)", "평택", "화성"]  # 관측이 없는 설비(OLD)는 위치에 넣지 않는다
    monkeypatch.setattr(module.QInputDialog, "getText", staticmethod(lambda *a, **k: ("용인", True)))
    assert page.location_panel.add_site()
    settle(page)
    assert gateway.sites[-1] == "용인" and saved and "Site 선택지" in page.location_panel.notice.text()
    assert any(tree.topLevelItem(i).text(0).startswith("용인") for i in range(tree.topLevelItemCount()))
    monkeypatch.setattr(module.QInputDialog, "getText", staticmethod(lambda *a, **k: ("화성", True)))
    assert not page.location_panel.add_site() and "이미 있는" in page.location_panel.notice.text()


def test_release_grid_has_no_risk_and_saves_each_edit_right_away(own, monkeypatch):
    asked = answer(monkeypatch)
    gateway = FakeGateway()
    page = page_with(own, gateway)
    page.select_tab(1)
    settle(page)
    panel = page.release_panel
    assert not hasattr(panel, "risk_button")  # 위험 지정은 아이템 매니저에서만
    assert [panel.table.horizontalHeaderItem(c).text() for c in range(panel.table.columnCount())] == list(module.RELEASE_COLUMNS)
    assert "위험" not in module.RELEASE_COLUMNS
    assert panel.tree.topLevelItem(0).child(0).text(0).startswith("↳ M-C4 DFB")  # 개조형은 상위 계열 아래
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0).child(0))
    settle(page)
    assert panel.family_title.text() == "C4 · M-C4 DFB" and panel.table.rowCount() == 3
    assert panel.table.item(0, 0).text() == "▼ v4.60.1.1" and panel.table.item(1, 0).text() == ""
    assert panel.table.item(1, 3).font().bold() and not panel.table.item(0, 3).font().bold()  # 개조형 적용 Item 강조
    panel._toggle_version(0, 0)
    assert panel.table.isRowHidden(1) and panel.table.item(0, 0).text() == "▶ v4.60.1.1"
    panel._toggle_version(0, 0)
    assert not panel.table.isRowHidden(1)
    panel.table.item(1, 3).setText("보호동작 오류 수정")
    settle(page)
    panel.table.item(1, 4).setText("M-C4")
    settle(page)
    panel.table.item(0, 1).setText("2026-04-07")
    settle(page)
    assert asked == [] and len(gateway.saved) == 3
    changes = [edits[0] for edits, _ in gateway.saved]
    assert changes[0].table == "item" and changes[0].changes == {"content": "보호동작 오류 수정"}
    assert changes[1].changes == {"target_scope": "LISTED", "target_group_ids": [1]}
    assert changes[2].changes == {"released_at": date(2026, 4, 7)}
    assert panel.table.item(1, 3).background().color() == module.SAVED


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


def test_history_shows_time_file_cell_and_before_after_in_one_row(own):
    page = page_with(own)
    page.select_tab(2)
    settle(page)
    panel = page.history_panel
    assert [panel.table.horizontalHeaderItem(c).text() for c in range(8)] == list(panel.COLUMNS)
    assert panel.table.rowCount() == 3 and panel.count.text() == "3건"
    assert [panel.table.item(0, c).text() for c in range(8)] == [
        "2026-11-10 14:02", "수정", "마스터.xlsx", "현황 2026-11-01 · D3", "EC1", "SW Ver", "v1.1", "v1.2"]
    assert panel.table.item(1, 2).text() == "—" and panel.table.item(1, 1).text() == "등록"  # 파일 없이 등록한 기록
    assert [panel.table.item(2, c).text() for c in (1, 3, 7)] == ["삭제", "현황 2026-11-01 · A5~D5", "삭제"]
    assert panel.table.item(0, 7).font().bold() and panel.table.item(0, 6).toolTip() == "v1.1"
    panel.kind.setCurrentIndex(panel.kind.findText("설비군"))
    assert panel.table.rowCount() == 1 and panel.table.item(0, 4).text() == "M-C7"
    panel.kind.setCurrentIndex(0)
    panel.period.setCurrentIndex(0)  # 최근 7일
    assert panel.table.rowCount() <= 3


def test_multiline_labels_get_full_height(own):
    page = page_with(own)
    page.select_tab(1)
    info = page.release_panel.notice
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
