"""Excel이 없어도 검증할 수 있는 excel_reader의 순수 로직 테스트."""

import pytest

from setron.infrastructure.excel_reader import MergedRange, SheetData, cell_address, fill_merged


@pytest.mark.parametrize(
    ("row", "col", "expected"),
    [(1, 1, "A1"), (5, 3, "C5"), (10, 26, "Z10"), (2, 27, "AA2"), (3, 34, "AH3"), (1, 702, "ZZ1"), (1, 703, "AAA1")],
)
def test_cell_address(row, col, expected):
    assert cell_address(row, col) == expected


def test_cell_address_rejects_zero():
    with pytest.raises(ValueError):
        cell_address(0, 1)


def test_fill_merged_fills_vertical_merge_like_release_list():
    # 릴리즈 리스트처럼 A열 버전명이 3행에 걸쳐 병합된 경우
    values = [
        ["v4.56.7.3_b (2026-02-26)", 1, "UI"],
        [None, 2, "ALL"],
        [None, 3, "CTC"],
        ["v4.56.8.1_a (2026-03-03)", 1, "UI"],
    ]
    merged = [MergedRange(top=3, left=1, bottom=5, right=1)]

    filled = fill_merged(values, merged, first_row=3, first_col=1)

    assert [row[0] for row in filled] == [
        "v4.56.7.3_b (2026-02-26)",
        "v4.56.7.3_b (2026-02-26)",
        "v4.56.7.3_b (2026-02-26)",
        "v4.56.8.1_a (2026-03-03)",
    ]


def test_fill_merged_fills_horizontal_header_and_keeps_original():
    # 화성 양식처럼 2행 머리글 'Version'이 두 열에 걸쳐 병합된 경우
    values = [["Version", None], ["SW", "Engine"]]
    merged = [MergedRange(top=2, left=8, bottom=2, right=9)]

    filled = fill_merged(values, merged, first_row=2, first_col=8)

    assert filled[0] == ["Version", "Version"]
    assert values[0] == ["Version", None]


def test_fill_merged_ignores_range_outside_used_area():
    values = [["a", "b"]]
    merged = [MergedRange(top=10, left=10, bottom=11, right=11)]

    assert fill_merged(values, merged) == [["a", "b"]]


def test_sheet_data_address_and_size():
    data = SheetData(
        workbook_name="book.xlsx",
        sheet_name="Memory",
        first_row=2,
        first_col=1,
        values=[["설비군", "Line"], ["Poly", "13L"]],
        merged_ranges=[MergedRange(2, 1, 3, 1)],
    )

    assert (data.row_count, data.col_count) == (2, 2)
    assert data.address_of(1, 1) == "B3"
    assert data.merged_ranges[0].address == "A2:A3"
    assert data.filled_values()[1][0] == "설비군"
