"""데이터 관리 현황표에서 여러 칸을 한 번에 바꾸고, 자동으로 정해지는 칸을 회색으로 구분한다 (11-17 요청).

- 데이터 가져오기 검토 표처럼 같은 열의 여러 칸을 골라 입력 · Ctrl+V · Delete · [일괄 변경]으로 한 번에 바꾼다.
- 한 번에 바꾼 것은 되돌리기 한 번으로 모두 돌아가고, [저장]을 눌러야 DB에 들어간다.
- 기준일 · Site · 대분류 · 모델명 · UI 구분처럼 직접 고치지 않는 칸은 회색이고 열 제목에 '(자동)'이 붙는다.
"""

from setron.ui import data_management_page as module
from test_ui_data_management import FakeGateway, answer, cell, codes, settle, sheet_page
from test_ui_import_ver5 import own, until  # noqa: F401

C = module.COLUMN


def select(sheet, cells):
    table = sheet.table
    table.clearSelection()
    model = table.selectionModel()
    model.setCurrentIndex(table.model().index(*cells[0]), model.SelectionFlag.NoUpdate)
    for r, c in cells:
        model.select(table.model().index(r, c), model.SelectionFlag.Select)


def test_cells_are_selected_one_by_one_and_fixed_cells_are_gray(own):
    page, sheet = sheet_page(own)
    assert sheet.table.selectionBehavior() == module.QAbstractItemView.SelectionBehavior.SelectItems
    for field in ("base_date", "site", "category", "model_name", "ui_type"):
        assert cell(sheet, 0, C[field]).background().color() == module.FIXED_CELL, field
        assert "(자동)" in sheet.table.horizontalHeaderItem(C[field]).text()
    for field in ("line", "equipment_code", "version_raw"):
        assert cell(sheet, 0, C[field]).background().style() == module.Qt.BrushStyle.NoBrush, field
        assert "(자동)" not in sheet.table.horizontalHeaderItem(C[field]).text()
    assert sheet.replace_button.isEnabled()


def test_typing_into_one_of_several_selected_cells_fills_them_all_and_undo_is_one_step(own):
    page, sheet = sheet_page(own)
    column = C["line"]
    select(sheet, [(0, column), (1, column), (2, column)])
    index = sheet.table.model().index(0, column)
    delegate = sheet.table.itemDelegateForIndex(index)
    editor = delegate.createEditor(sheet.table.viewport(), module.QStyleOptionViewItem(), index)
    assert "3칸" in editor.toolTip()
    editor.setEditText("20L")
    assert [cell(sheet, r, column).text() for r in (1, 2)] == ["20L", "20L"]    # 입력하는 동안 다른 칸에도 보인다
    delegate.setModelData(editor, sheet.table.model(), index)
    until(lambda: all(cell(sheet, r, column).text() == "20L" for r in range(3)) and sheet.pending)
    assert len(sheet.edits) == 3 and sheet.save_button.text() == "저장 (3건)"
    assert cell(sheet, 0, column).background().color() == module.SAVING
    assert sheet.undo()                                             # 한 번에 바꾼 것은 한 번에 돌아간다
    assert not sheet.pending and [cell(sheet, r, column).text() for r in range(3)] == ["13L", "15L", "17L"]


def test_preview_is_restored_when_editing_is_cancelled(own):
    page, sheet = sheet_page(own)
    column = C["process_name"]
    sheet.bulk_preview([(1, column), (2, column)], "ETCH")
    assert cell(sheet, 1, column).text() == "ETCH"
    sheet.bulk_preview_end()
    assert cell(sheet, 1, column).text() == "" and not sheet.pending


def test_paste_fills_from_the_selected_cell_and_skips_gray_cells(own):
    page, sheet = sheet_page(own)
    select(sheet, [(0, C["equipment_code"])])
    assert sheet.paste_cells("EQ1\tv9.1\nEQ2\tv9.2")                 # 호기 · PM 열부터 두 줄 (PM 칸은 고칠 수 없음)
    assert codes(sheet) == ["EQ1", "EQ2", "EC4"]
    select(sheet, [(0, C["category"]), (1, C["category"])])         # 회색(자동) 칸은 바뀌지 않는다
    assert not sheet.paste_cells("OXIDE")
    assert cell(sheet, 0, C["category"]).text() == "POLY"
    assert "건너뛰었습니다" in sheet.last_note


def test_one_value_pasted_to_several_cells_and_blank_code_is_refused(own):
    page, sheet = sheet_page(own)
    select(sheet, [(0, C["geometry_type"]), (2, C["geometry_type"])])
    assert sheet.paste_cells("Twin")
    assert [cell(sheet, r, C["geometry_type"]).text() for r in range(3)] == ["Twin", "", "Twin"]
    select(sheet, [(0, C["equipment_code"]), (1, C["equipment_code"])])
    assert not sheet.clear_cells()                                   # 호기는 비울 수 없다
    assert codes(sheet)[:2] == ["EC1", "EC2"] and sheet.notice.isVisible() and "비울 수 없습니다" in sheet.notice.text()


def test_find_replace_changes_codes_and_saves_them_together(own, monkeypatch):
    gateway = FakeGateway()
    page, sheet = sheet_page(own, gateway)
    assert ("category", "대분류") not in sheet.replace_fields() and ("equipment_code", "설비 호기") in sheet.replace_fields()
    assert sheet.find_replace("equipment_code", "EC", "EQ") == 3
    assert codes(sheet) == ["EQ1", "EQ2", "EQ4"]
    assert sheet.find_replace("equipment_code", "없음", "X") == 0 and "찾지 못했습니다" in sheet.notice.text()
    assert sheet.find_replace("line", "15L", "16L", True) == 1      # 칸 전체가 같을 때만
    assert [cell(sheet, r, C["line"]).text() for r in range(3)] == ["13L", "16L", "17L"]
    answer(monkeypatch)
    assert sheet.save()
    settle(page)
    saved = [e for e in gateway.saved[0][0] if e.table == "equipment"]
    assert sorted(e.changes["equipment_code"] for e in saved) == ["EQ1", "EQ2", "EQ4"]


def test_find_replace_only_in_selected_rows(own):
    page, sheet = sheet_page(own)
    select(sheet, [(1, C["equipment_code"])])
    assert sheet.find_replace("equipment_code", "EC", "EQ", False, True, rows=[1]) == 1
    assert codes(sheet) == ["EC1", "EQ2", "EC4"]
