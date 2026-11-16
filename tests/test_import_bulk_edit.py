"""최종 멘토링(11-16) — 데이터 가져오기에서 설비명 등을 한 번에 바꾸기.

- 여러 칸을 고른 뒤 값을 입력하면 고른 칸이 모두 바뀐다.
- Ctrl+V: Excel에서 복사한 열을 고른 칸부터 붙여 넣는다. 값 하나는 고른 칸 모두에.
- Ctrl+C 복사, Delete 비우기, [찾아 바꾸기].
- 원본 추출값은 그대로 두고 수정값으로 기록한다. 제외한 행은 바꾸지 않는다.
"""

import pytest
from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from setron.features.excel_import.bulk_edit import apply_edits, parse_clipboard, paste_plan, replace_plan
from setron.features.excel_import.service import set_excluded
from test_import_validation import draft, row
from test_ui_import_ver5 import import_page, own  # noqa: F401


def three():
    return draft(row(1, equipment_code="ESPG01", model_name="C4(DFB)"), row(2, equipment_code="ESPG02", model_name="C4(DFB)"),
                 row(3, equipment_code="ESOX03", model_name="M-O2"))


# --- 계산 (features) -------------------------------------------------------------------------------------
def test_apply_edits_records_edits_once_and_keeps_originals():
    source = three()
    changed, count = apply_edits(source, [(1, "model_name", "M-C4 DFB"), (2, "model_name", "M-C4 DFB"),
                                          (3, "model_name", "M-O2")])          # 3행은 값이 같아 세지 않는다
    assert count == 2
    assert [r.value("model_name") for r in changed.rows] == ["M-C4 DFB", "M-C4 DFB", "M-O2"]
    assert [r.original["model_name"] for r in changed.rows] == ["C4(DFB)", "C4(DFB)", "M-O2"]
    assert "model_name" not in changed.row(3).edits


def test_excluded_rows_are_not_changed():
    source = set_excluded(three(), 2, True)
    changed, count = apply_edits(source, [(1, "site", "평택"), (2, "site", "평택")])
    assert count == 1 and changed.row(2).value("site") == "화성"
    assert [c[0] for c in replace_plan(source, "site", "화성", "평택")] == [1, 3]


def test_replace_plan_partial_whole_cell_and_selected_rows():
    source = three()
    assert replace_plan(source, "equipment_code", "ESPG", "ESPH") == [(1, "equipment_code", "ESPH01"),
                                                                      (2, "equipment_code", "ESPH02")]
    assert replace_plan(source, "model_name", "C4(DFB)", "M-C4 DFB", whole_cell=True) == [
        (1, "model_name", "M-C4 DFB"), (2, "model_name", "M-C4 DFB")]
    assert replace_plan(source, "model_name", "C4", "X", whole_cell=True) == []   # 일부만 같으면 안 바꾼다
    assert replace_plan(source, "equipment_code", "ESPG", "ESPH", row_ids=[2]) == [(2, "equipment_code", "ESPH02")]
    assert replace_plan(source, "없는 항목", "a", "b") == []


def test_clipboard_and_paste_plan():
    assert parse_clipboard("A1\tB1\r\nA2\tB2\r\n") == [["A1", "B1"], ["A2", "B2"]]
    ids, columns = [10, 20, 30], ["site", None, "equipment_code", "model_name"]
    # 열 하나를 2번째 줄부터: 표 끝에서 멈춘다
    assert paste_plan([["E2"], ["E3"], ["E4"]], 1, 2, ids, columns) == [(20, "equipment_code", "E2"),
                                                                         (30, "equipment_code", "E3")]
    # 두 열: 고칠 수 없는 열(None)은 건너뛴다
    assert paste_plan([["S", "x", "E"]], 0, 0, ids, columns) == [(10, "site", "S"), (10, "equipment_code", "E")]
    # 값 하나를 여러 칸에: 고른 칸 모두
    assert paste_plan([["M"]], 0, 3, ids, columns, [(0, 3), (2, 3)]) == [(10, "model_name", "M"), (30, "model_name", "M")]


# --- 화면 ------------------------------------------------------------------------------------------------
def _page(own):  # noqa: F811
    page = import_page(own)
    page.draft = three()
    page._render()
    page.select_row(1)
    return page


def _select(page, cells):
    selection = QItemSelection()
    for r, c in cells:
        index = page.row_model.index(r, c)
        selection.select(index, index)
    page.row_table.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    first = page.row_model.index(*cells[0])
    page.row_table.selectionModel().setCurrentIndex(first, QItemSelectionModel.SelectionFlag.NoUpdate)


def _column(page, name):
    return 2 + page.draft.fields.index(name)


def test_table_allows_selecting_many_cells(own):  # noqa: F811
    page = _page(own)
    assert page.row_table.selectionMode() == page.row_table.SelectionMode.ExtendedSelection
    assert page.replace_button.isVisibleTo(page) and page.replace_button.isEnabled()


def test_typing_into_one_of_many_selected_cells_changes_them_all(own):  # noqa: F811
    page = _page(own)
    column = _column(page, "model_name")
    _select(page, [(0, column), (1, column)])
    assert page.row_model.setData(page.row_model.index(0, column), "M-C4 DFB")
    QTest.qWait(20)
    assert [r.value("model_name") for r in page.draft.rows] == ["M-C4 DFB", "M-C4 DFB", "M-O2"]
    assert all(r.original["model_name"] in ("C4(DFB)", "M-O2") for r in page.draft.rows)


def test_editing_one_cell_of_a_selected_row_changes_only_that_cell(own):  # noqa: F811
    page = _page(own)
    page.row_table.selectRow(0)                                      # 행 전체를 고른 상태
    column = _column(page, "model_name")
    assert page.row_model.setData(page.row_model.index(0, column), "M-C4 DFB")
    QTest.qWait(20)
    assert page.draft.row(1).edited_fields == ("model_name",)        # 다른 열은 그대로


def test_paste_a_column_and_one_value_into_many(own):  # noqa: F811
    page = _page(own)
    column = _column(page, "equipment_code")
    _select(page, [(0, column)])
    assert page.paste_cells("NEW01\nNEW02\nNEW03\n")                  # Excel에서 복사한 열
    assert [r.value("equipment_code") for r in page.draft.rows] == ["NEW01", "NEW02", "NEW03"]
    site = _column(page, "site")
    _select(page, [(0, site), (1, site), (2, site)])
    assert page.paste_cells("평택")                                   # 값 하나 → 고른 칸 모두
    assert {r.value("site") for r in page.draft.rows} == {"평택"}


def test_ctrl_v_copy_and_delete_keys(own):  # noqa: F811
    page = _page(own)
    column = _column(page, "model_name")
    _select(page, [(0, column), (1, column)])
    page.row_table.setFocus()
    QTest.keyClick(page.row_table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == "C4(DFB)\nC4(DFB)"
    QApplication.clipboard().setText("M-C4 DFB")
    _select(page, [(0, column), (1, column)])
    QTest.keyClick(page.row_table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert [r.value("model_name") for r in page.draft.rows][:2] == ["M-C4 DFB", "M-C4 DFB"]
    _select(page, [(0, column), (1, column)])
    QTest.keyClick(page.row_table, Qt.Key.Key_Delete)
    assert [r.value("model_name") for r in page.draft.rows][:2] == [None, None]
    assert all("model_name" in r.edits for r in page.draft.rows[:2])          # 직접 비운 값으로 기록


def test_find_replace_changes_equipment_codes_at_once(own):  # noqa: F811
    page = _page(own)
    assert page.find_replace("equipment_code", "ESPG", "ESPH")
    assert [r.value("equipment_code") for r in page.draft.rows] == ["ESPH01", "ESPH02", "ESOX03"]
    assert not page.find_replace("equipment_code", "없는글자", "X")
    assert page.find_replace("model_name", "C4(DFB)", "M-C4 DFB", True)
    assert [r.value("model_name") for r in page.draft.rows] == ["M-C4 DFB", "M-C4 DFB", "M-O2"]


def test_revert_and_exclude_apply_to_all_selected_rows(own):  # noqa: F811
    page = _page(own)
    page.find_replace("equipment_code", "ES", "XX")
    column = _column(page, "equipment_code")
    _select(page, [(0, column), (1, column)])
    page.select_row(1)
    _select(page, [(0, column), (1, column)])
    assert page.revert_selected()
    assert [r.value("equipment_code") for r in page.draft.rows] == ["ESPG01", "ESPG02", "XXOX03"]
    _select(page, [(0, column), (1, column)])
    assert page.toggle_excluded()
    assert [r.excluded for r in page.draft.rows] == [True, True, False]


@pytest.mark.parametrize("locked_state", ["saving", "saved"])
def test_nothing_changes_when_the_draft_is_locked(own, locked_state):  # noqa: F811
    page = _page(own)
    page.state = locked_state
    before = page.draft
    assert not page.find_replace("equipment_code", "ESPG", "ESPH")
    assert not page.paste_cells("X")
    assert page.draft is before


def test_undo_and_redo_icons_step_through_review_edits(own):  # noqa: F811
    page = _page(own)
    page._changed(initial=True)
    assert not page.undo_button.isEnabled() and page.undo_button.text() == ""      # 글자 없는 아이콘 버튼
    assert page.find_replace("equipment_code", "ESPG", "ESPH")
    page.edit_cell("site", "평택", 1)
    assert page.undo_button.isEnabled() and not page.redo_button.isEnabled()
    assert page.undo()
    assert page.draft.row(1).value("site") == "화성" and page.draft.row(1).value("equipment_code") == "ESPH01"
    assert page.undo()
    assert [r.value("equipment_code") for r in page.draft.rows] == ["ESPG01", "ESPG02", "ESOX03"]
    assert not page.undo() and page.redo_button.isEnabled()
    assert page.redo() and page.redo()
    assert page.draft.row(1).value("site") == "평택" and page.draft.row(1).value("equipment_code") == "ESPH01"
    page.undo()
    page.edit_cell("line", "새 라인", 2)                                   # 새로 고치면 다시 실행 목록은 비운다
    assert not page.redo_button.isEnabled()


def test_ctrl_z_and_ctrl_y_on_the_table(own):  # noqa: F811
    page = _page(own)
    page._changed(initial=True)
    page.find_replace("equipment_code", "ESPG", "ESPH")
    page.row_table.setFocus()
    QTest.keyClick(page.row_table, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert page.draft.row(1).value("equipment_code") == "ESPG01"
    QTest.keyClick(page.row_table, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert page.draft.row(1).value("equipment_code") == "ESPH01"


def test_many_cell_edit_previews_other_cells_and_keeps_them_selected(own):  # noqa: F811
    page = _page(own)
    column = _column(page, "model_name")
    _select(page, [(0, column), (1, column)])
    delegate = page.row_table.itemDelegate()
    editor = delegate.createEditor(page.row_table.viewport(), QStyleOptionViewItem(), page.row_model.index(0, column))
    assert "고른 2칸에 함께 입력" in editor.placeholderText()
    editor.textEdited.emit("M-C4 DFB")                                    # 입력하는 동안
    assert page.row_model.data(page.row_model.index(1, column)) == "M-C4 DFB"   # 다른 칸에도 미리 보인다
    editor.deleteLater()
    assert page.row_model.setData(page.row_model.index(0, column), "M-C4 DFB")
    QTest.qWait(30)
    selected = {(i.row(), i.column()) for i in page.row_table.selectionModel().selectedIndexes()}
    assert selected == {(0, column), (1, column)}                         # 바꾼 칸을 그대로 고른 상태
    assert not page.row_model.preview
