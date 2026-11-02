"""Excel이 없어도 검증할 수 있는 excel_reader의 순수 로직 테스트."""

from types import SimpleNamespace
from datetime import datetime
import warnings

import pytest

from setron.infrastructure import excel_reader
from setron.infrastructure.excel_reader import (
    AmbiguousWorkbookError,
    ExcelConnectionError,
    ExcelDiscoveryWarning,
    ExcelNotRunningError,
    ExcelProtectedViewError,
    ExcelSheetReadError,
    MergedRange,
    SheetData,
    SheetNotFoundError,
    WorkbookNotFoundError,
    WorkbookSelectionChangedError,
    cell_address,
    fill_merged,
    list_open_workbooks,
    read_sheet,
)


def fake_book(name, sheets=("Sheet1",)):
    """읽기 테스트용 통합문서를 구성한다.

    param
    - name: 테스트 파일명.
    - sheets: 시트명 또는 시트 객체 목록.

    return
    - 파일명·전체 이름·시트 목록을 제공하는 가짜 통합문서.
    """
    return SimpleNamespace(
        name=name,
        fullname=f"C:/data/{name}",
        sheets=[SimpleNamespace(name=s) if isinstance(s, str) else s for s in sheets],
    )


class FakeApps:
    """반복 열거와 프로세스 번호 선택을 제공하는 가짜 Excel 목록."""

    def __init__(self, windows):
        """프로세스별 창 목록을 보관한다.

        param
        - windows: 가짜 Excel 창 목록.
        """
        self.windows = windows

    def __iter__(self):
        """가짜 Excel 창을 열거한다.

        return
        - 창 목록의 반복자.
        """
        return iter(self.windows)

    def __len__(self):
        """열린 Excel 창 수를 반환한다.

        return
        - 창 목록의 길이.
        """
        return len(self.windows)

    def __getitem__(self, pid):
        """프로세스 번호로 선택한 창만 반환한다.

        param
        - pid: 조회할 가짜 Excel 프로세스 번호.

        return
        - 일치하는 가짜 Excel 창.

        raises
        - KeyError: 해당 번호의 창이 없는 경우.
        """
        for app in self.windows:
            if app.pid == pid:
                return app
        raise KeyError(pid)


def use_fake_excel(monkeypatch, apps):
    """xlwings 대신 프로세스별 가짜 Excel을 연결한다.

    param
    - monkeypatch: 테스트가 끝나면 모듈 변경을 복구하는 pytest fixture.
    - apps: 창마다 열린 통합문서 목록. 프로세스 번호는 1000부터 부여한다.

    return
    - 조회 뒤 파일 교체나 창 추가 상황을 재현할 수 있는 FakeApps.
    """
    windows = []
    for index, books in enumerate(apps):
        app = SimpleNamespace(pid=1000 + index, books=books)
        for book in books:
            book.app = app
        windows.append(app)
    collection = FakeApps(windows)
    monkeypatch.setattr(excel_reader, "xw", SimpleNamespace(apps=collection))
    return collection


class BrokenApps:
    """열거 중 COM 연결 실패를 재현하는 객체."""

    def __iter__(self):
        """목록 접근 시 연결 실패를 발생시킨다.

        raises
        - OSError: 가짜 RPC 연결 실패.
        """
        raise OSError("RPC 서버를 사용할 수 없습니다.")


def test_list_open_workbooks_returns_books_of_all_excel_windows(monkeypatch):
    use_fake_excel(monkeypatch, [[fake_book("화성.xlsx", ("Memory", "Foundry"))], [fake_book("C4.xlsx")]])

    books = list_open_workbooks()

    assert [(b.name, b.sheet_names) for b in books] == [("화성.xlsx", ["Memory", "Foundry"]), ("C4.xlsx", ["Sheet1"])]


@pytest.mark.parametrize("apps", [[], [[]]], ids=["Excel 미실행", "Excel만 실행, 파일 없음"])
def test_no_open_workbook_raises_not_running(monkeypatch, apps):
    use_fake_excel(monkeypatch, apps)

    with pytest.raises(ExcelNotRunningError, match="열려 있는 Excel 파일이 없습니다"):
        list_open_workbooks()
    with pytest.raises(ExcelNotRunningError if not apps else WorkbookNotFoundError):
        read_sheet("화성.xlsx", "Memory", app_pid=1000, full_name="C:/data/화성.xlsx")


def test_connection_failure_raises_connection_error(monkeypatch):
    monkeypatch.setattr(excel_reader, "xw", SimpleNamespace(apps=BrokenApps()))

    with pytest.raises(ExcelConnectionError, match="Excel에 연결하지 못했습니다"):
        list_open_workbooks()


def test_unknown_workbook_raises_not_found_when_other_books_are_open(monkeypatch):
    use_fake_excel(monkeypatch, [[fake_book("C4.xlsx")]])

    with pytest.raises(WorkbookNotFoundError):
        read_sheet("화성.xlsx", "Memory", app_pid=1000, full_name="C:/data/화성.xlsx")


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


class FakeComSheet:
    """병합 여부와 실제 병합 영역을 제공하는 가짜 COM 시트."""

    def __init__(self, merges=()):
        """테스트에 사용할 원본 병합 좌표를 보관한다.

        param
        - merges: MergedRange 목록.
        """
        self.merges = merges
        self.range_calls = 0

    def Cells(self, row, col):
        """좌표에 대응하는 셀과 병합 영역을 반환한다.

        param
        - row: 원본 행 번호.
        - col: 원본 열 번호.

        return
        - 행·열과 MergeArea를 제공하는 가짜 COM 셀.
        """
        merged = next((m for m in self.merges if m.contains(row, col)), None)
        area = merged or MergedRange(row, col, row, col)
        return SimpleNamespace(Row=row, Column=col, MergeArea=SimpleNamespace(
            Row=area.top, Column=area.left,
            Rows=SimpleNamespace(Count=area.bottom - area.top + 1),
            Columns=SimpleNamespace(Count=area.right - area.left + 1),
            MergeCells=merged is not None,
        ))

    def Range(self, start, end):
        """열 구간의 전체 병합·부분 병합·미병합을 구분한다.

        param
        - start: 구간의 첫 셀.
        - end: 구간의 마지막 셀.

        return
        - 실제 Excel과 같은 3가지 MergeCells 상태를 가진 객체.
        """
        self.range_calls += 1
        states = [any(m.contains(row, start.Column) for m in self.merges)
                  for row in range(start.Row, end.Row + 1)]
        state = True if all(states) else None if any(states) else False
        return SimpleNamespace(MergeCells=state)


class FakeUsedRange:
    """2차원 변환과 단계별 읽기 실패를 재현하는 사용 영역."""

    def __init__(self, values, row=1, col=1, merges=(), failure=None):
        """읽을 값과 사용 영역의 원본 좌표를 준비한다.

        param
        - values: 셀 값의 2차원 표. None이면 값이 없는 응답.
        - row: 사용 영역의 시작 행 번호.
        - col: 사용 영역의 시작 열 번호.
        - merges: 원본 좌표의 병합 범위 목록.
        - failure: 실패시킬 속성명. None이면 정상 응답.
        """
        self._values = values
        self.row = row
        self.column = col
        self._options = {}
        self.failure = failure
        self.api = SimpleNamespace(
            Worksheet=FakeComSheet(merges), Row=row, Column=col,
            Rows=SimpleNamespace(Count=len(values) if values else 1),
            Columns=SimpleNamespace(Count=len(values[0]) if values else 1),
        )

    def __getattribute__(self, name):
        """지정한 읽기 단계에서 COM 오류를 발생시킨다.

        param
        - name: 접근할 속성명.

        return
        - 실패 대상이 아닌 속성의 실제 값.

        raises
        - OSError: 테스트가 지정한 읽기 단계에 도달한 경우.
        """
        if name in ("row", "column", "api", "value") and name == object.__getattribute__(self, "failure"):
            raise OSError("읽는 중 Excel 연결이 끊겼습니다.")
        return object.__getattribute__(self, name)

    def options(self, **kwargs):
        """xlwings의 변환 옵션을 적용한다.

        param
        - kwargs: 차원과 오류 셀 변환 옵션.

        return
        - 변환할 가짜 범위 자신.
        """
        self._options = kwargs
        return self

    @property
    def value(self):
        """xlwings 기본 변환의 빈 셀·오류 셀 차이를 재현한다.

        return
        - 변환된 표. 오류 문자열 보존 옵션이 없으면 오류 셀은 None.
        """
        if self._values is None:
            return None
        result = [[None if v == "#N/A" and not self._options.get("err_to_str") else v for v in row]
                  for row in self._values]
        return result if self._options.get("ndim") == 2 else result[0][0]


def test_workbook_selection_includes_process_and_actual_file_name(monkeypatch):
    """목록의 선택 식별자와 저장 확인용 파일명이 실제 읽기 결과에 이어지는지 확인한다.

    param
    - monkeypatch: 외부 Excel을 가짜 창으로 교체하는 fixture.
    """
    use_fake_excel(monkeypatch, [[fake_book("Book1")], [fake_book("화성.xlsx")]])
    books = list_open_workbooks()
    assert [(b.name, b.full_name, b.app_pid) for b in books] == [
        ("Book1", "C:/data/Book1", 1000), ("화성.xlsx", "C:/data/화성.xlsx", 1001),
    ]


def test_read_sheet_preserves_values_file_sheet_and_offset_addresses(monkeypatch):
    """선택한 시트의 값과 오프셋 좌표·파일명을 원본 그대로 전달하는지 확인한다.

    param
    - monkeypatch: 외부 Excel을 가짜 창으로 교체하는 fixture.
    """
    observed = datetime(2026, 10, 27)
    values = [[" v4.56.7.3_b ", 0.0, False, None], [observed, "한글\n줄바꿈", "#N/A", "001"]]
    used = FakeUsedRange(values, row=5, col=27)
    selected = SimpleNamespace(name="Foundry 원본", used_range=used)
    use_fake_excel(monkeypatch, [[fake_book("원본.xlsx", ("Memory", selected))]])

    result = read_sheet("원본.xlsx", "Foundry 원본", app_pid=1000, full_name="C:/data/원본.xlsx")

    assert result.workbook_name == "원본.xlsx"
    assert result.sheet_name == "Foundry 원본" and result.app_pid == 1000
    assert result.values == values
    assert (result.first_row, result.first_col, result.row_count, result.col_count) == (5, 27, 2, 4)
    assert result.address_of(0, 0) == "AA5"
    assert result.address_of(1, 3) == "AD6"
    result.values[0][0] = "검토 중 수정"
    assert values[0][0] == " v4.56.7.3_b "


@pytest.mark.parametrize("values", [[["한 셀"]], [[None]], None])
def test_single_or_empty_cell_does_not_lose_shape_or_invent_values(monkeypatch, values):
    """한 셀과 빈 응답을 구분하고 실제 셀 위치를 유지하는지 확인한다.

    param
    - monkeypatch: 외부 Excel을 가짜 창으로 교체하는 fixture.
    - values: 한 셀 또는 빈 응답.
    """
    sheet = SimpleNamespace(name="원본", used_range=FakeUsedRange(values, row=3, col=2))
    use_fake_excel(monkeypatch, [[fake_book("Book1", (sheet,))]])
    result = read_sheet("Book1", "원본", app_pid=1000, full_name="C:/data/Book1")
    assert result.values == (values if values is not None else [])
    assert result.workbook_name == "Book1"
    if values is not None:
        assert result.address_of(0, 0) == "B3"


def test_duplicate_names_require_process_and_never_read_first_match(monkeypatch):
    """다른 창의 같은 파일명을 임의 선택하지 않고 선택한 프로세스만 읽는지 확인한다.

    param
    - monkeypatch: 외부 Excel을 가짜 창으로 교체하는 fixture.
    """
    first = SimpleNamespace(name="원본", used_range=FakeUsedRange([["첫 창"]]))
    second = SimpleNamespace(name="원본", used_range=FakeUsedRange([["선택한 창"]]))
    use_fake_excel(monkeypatch, [[fake_book("동일.xlsx", (first,))], [fake_book("동일.xlsx", (second,))]])
    assert read_sheet("동일.xlsx", "원본", app_pid=1001, full_name="C:/data/동일.xlsx").values == [["선택한 창"]]
    with pytest.raises(WorkbookNotFoundError, match="다시 선택"):
        read_sheet("동일.xlsx", "원본", app_pid=9999, full_name="C:/data/동일.xlsx")


def test_missing_sheet_is_distinct_from_read_failure(monkeypatch):
    """시트가 목록에 없을 때만 시트 없음 오류를 반환하는지 확인한다.

    param
    - monkeypatch: 외부 Excel을 가짜 창으로 교체하는 fixture.
    """
    use_fake_excel(monkeypatch, [[fake_book("원본.xlsx", ("Memory",))]])
    with pytest.raises(SheetNotFoundError, match="Foundry"):
        read_sheet("원본.xlsx", "Foundry", app_pid=1000, full_name="C:/data/원본.xlsx")


@pytest.mark.parametrize("failure", ["value", "row", "column", "api"])
def test_com_failure_during_read_reports_selected_file_and_sheet(monkeypatch, failure):
    """값·좌표·병합 읽기 실패를 원인 예외와 함께 전달하는지 확인한다.

    param
    - monkeypatch: 외부 Excel을 가짜 창으로 교체하는 fixture.
    - failure: COM 실패를 재현할 읽기 단계.
    """
    sheet = SimpleNamespace(name="Memory", used_range=FakeUsedRange([["원문"]], failure=failure))
    use_fake_excel(monkeypatch, [[fake_book("원본.xlsx", (sheet,))]])
    with pytest.raises(ExcelSheetReadError, match="원본.xlsx.*Memory") as caught:
        read_sheet("원본.xlsx", "Memory", app_pid=1000, full_name="C:/data/원본.xlsx")
    assert isinstance(caught.value.__cause__, OSError)


def test_sheet_list_failure_is_not_misreported_as_missing_sheet(monkeypatch):
    """시트 목록 COM 실패를 없는 시트로 잘못 알리지 않는지 확인한다.

    param
    - monkeypatch: 외부 Excel을 가짜 창으로 교체하는 fixture.
    """
    book = fake_book("원본.xlsx")
    book.sheets = BrokenApps()
    use_fake_excel(monkeypatch, [[book]])
    with pytest.raises(ExcelSheetReadError):
        read_sheet("원본.xlsx", "Memory", app_pid=1000, full_name="C:/data/원본.xlsx")
    with pytest.raises(ExcelConnectionError):
        list_open_workbooks()


def test_read_sheet_collects_original_merges_without_changing_raw_values(monkeypatch):
    """가로·세로 병합을 중복 없이 원본 위치로 전달하고 원본 빈칸을 보존하는지 확인한다.

    param
    - monkeypatch: 외부 Excel을 가짜 창으로 교체하는 fixture.
    """
    merges = [MergedRange(4, 3, 4, 4), MergedRange(5, 3, 7, 3), MergedRange(5, 4, 6, 4)]
    values = [["머리글", None, "구분"], ["v1", "PM", 0], [None, None, 1], [None, "다음", 2]]
    used = FakeUsedRange(values, row=4, col=3, merges=merges)
    use_fake_excel(monkeypatch, [[fake_book("원본.xlsx", (SimpleNamespace(name="원본", used_range=used),))]])
    result = read_sheet("원본.xlsx", "원본", app_pid=1000, full_name="C:/data/원본.xlsx")
    assert result.merged_ranges == merges
    assert [m.address for m in result.merged_ranges] == ["C4:D4", "C5:C7", "D5:D6"]
    assert result.values == values
    assert result.filled_values() == [["머리글", "머리글", "구분"], ["v1", "PM", 0],
                                      ["v1", "PM", 1], ["v1", "다음", 2]]
    assert result.values[2] == [None, None, 1]


def test_unmerged_columns_are_checked_without_per_cell_com_calls():
    """병합이 없는 큰 영역은 셀마다 COM을 호출하지 않는지 확인한다."""
    sheet = FakeComSheet()
    used = SimpleNamespace(Worksheet=sheet, Row=5, Column=3,
                           Rows=SimpleNamespace(Count=10000), Columns=SimpleNamespace(Count=4))
    assert excel_reader._find_merged_ranges(used) == []
    assert sheet.range_calls == 4


@pytest.mark.parametrize("i,j", [(-1, 0), (0, -1), (1, 0), (0, 1)])
def test_address_of_rejects_cells_not_read(i, j):
    """읽지 않은 셀에 대한 원본 주소를 만들어내지 않는지 확인한다.

    param
    - i: 표 범위 검사에 사용할 행 인덱스.
    - j: 표 범위 검사에 사용할 열 인덱스.
    """
    data = SheetData("원본.xlsx", "원본", 5, 3, [["원문"]])
    with pytest.raises(IndexError):
        data.address_of(i, j)


class ProtectedViewApp:
    """사용자가 보고한 제한된 보기 창의 중첩 예외를 재현하는 Excel 앱."""

    pid = 1000

    @property
    def books(self):
        """제한된 보기 객체의 속성 조회 실패 뒤 COM 오류를 발생시킨다.

        raises
        - OSError: 알 수 없는 이름 COM 오류를 모사한 예외. 원인 문맥을 포함한다.
        """
        try:
            raise AttributeError(
                "'<Microsoft Excel 16.0 Object Library.ProtectedViewWindow instance>' "
                "object has no attribute 'Application'"
            )
        except AttributeError:
            raise OSError(-2147352570, "알 수 없는 이름입니다.")


@pytest.mark.parametrize("operation", ["list", "read"])
def test_protected_view_connection_failure_has_specific_guidance(monkeypatch, operation):
    """제한된 보기 연결 실패를 미설치 오류와 구분해 안내하는지 확인한다.

    param
    - monkeypatch: 실제 Excel을 제한된 보기 모사 객체로 교체하는 fixture.
    - operation: 목록 조회 또는 시트 읽기 호출.
    """
    monkeypatch.setattr(excel_reader, "xw", SimpleNamespace(apps=FakeApps([ProtectedViewApp()])))
    with pytest.raises(ExcelProtectedViewError, match="제한된 보기.*편집 사용") as caught:
        if operation == "list":
            list_open_workbooks()
        else:
            read_sheet("원본.xlsx", "P1", app_pid=1000, full_name="C:/data/원본.xlsx")
    assert isinstance(caught.value, ExcelConnectionError)
    assert isinstance(caught.value.__cause__, OSError)
    assert isinstance(caught.value.__cause__.__context__, AttributeError)


def test_unknown_com_name_error_alone_does_not_imply_protected_view(monkeypatch):
    """같은 COM 오류 번호만으로 제한된 보기 상태를 추정하지 않는지 확인한다.

    param
    - monkeypatch: 가짜 목록 열거 동작을 교체하는 fixture.
    """
    def fail_to_iterate(self):
        """제한된 보기 근거가 없는 COM 오류를 재현한다.

        raises
        - OSError: 알 수 없는 이름 오류.
        """
        raise OSError(-2147352570, "알 수 없는 이름입니다.")

    monkeypatch.setattr(BrokenApps, "__iter__", fail_to_iterate)
    monkeypatch.setattr(excel_reader, "xw", SimpleNamespace(apps=BrokenApps()))
    with pytest.raises(ExcelConnectionError) as caught:
        list_open_workbooks()
    assert not isinstance(caught.value, ExcelProtectedViewError)


@pytest.mark.parametrize("failure", ["protected_view", "connection"])
@pytest.mark.parametrize("broken_first", [False, True])
def test_unrelated_broken_app_keeps_normal_selection_and_reading(monkeypatch, failure, broken_first):
    """다른 창에 제한된 보기나 연결 실패가 있어도 정상 파일을 선택하고 읽는지 확인한다.

    param
    - monkeypatch: 실제 Excel을 가짜 창 목록으로 교체하는 fixture.
    - failure: 다른 프로세스에서 재현할 실패 종류.
    - broken_first: 실패한 창이 목록의 앞에 있는지 여부.
    """
    sheet = SimpleNamespace(name="P1", used_range=FakeUsedRange([["정상 파일"]]))
    apps = use_fake_excel(monkeypatch, [[fake_book("원본.xlsx", (sheet,))]])
    broken = ProtectedViewApp() if failure == "protected_view" else SimpleNamespace(books=BrokenApps())
    broken.pid = 1001
    apps.windows.insert(0 if broken_first else 1, broken)

    with pytest.warns(ExcelDiscoveryWarning, match="일부 Excel 파일") as caught:
        choices = list_open_workbooks()
    assert len(caught) == 1
    assert [(b.name, b.app_pid) for b in choices] == [("원본.xlsx", 1000)]
    selected = choices[0]
    with warnings.catch_warnings(record=True) as read_warnings:
        warnings.simplefilter("always")
        result = read_sheet(selected.name, "P1", app_pid=selected.app_pid, full_name=selected.full_name)
    assert read_warnings == []
    assert result.values == [["정상 파일"]]


def test_bad_workbook_metadata_does_not_hide_other_books_in_same_app(monkeypatch):
    """한 파일의 시트 목록 조회 실패가 같은 창의 정상 파일까지 숨기지 않는지 확인한다.

    param
    - monkeypatch: 실제 Excel을 가짜 창 목록으로 교체하는 fixture.
    """
    broken = fake_book("닫히는중.xlsx")
    broken.sheets = BrokenApps()
    use_fake_excel(monkeypatch, [[broken, fake_book("정상.xlsx")]])
    with pytest.warns(ExcelDiscoveryWarning):
        choices = list_open_workbooks()
    assert [choice.name for choice in choices] == ["정상.xlsx"]


def test_replaced_same_name_file_is_rejected_before_reading_values(monkeypatch):
    """선택 후 동명 파일을 다른 폴더의 파일로 교체하면 값을 읽기 전에 차단하는지 확인한다.

    param
    - monkeypatch: 실제 Excel과 통합문서 목록을 교체하는 fixture.
    """
    original = fake_book("설비현황.xlsx", ("P1",))
    original.fullname = "C:/source-a/설비현황.xlsx"
    apps = use_fake_excel(monkeypatch, [[original]])
    selection = list_open_workbooks()[0]

    used = FakeUsedRange([["다른 파일"]], failure="value")
    replacement = fake_book("설비현황.xlsx", (SimpleNamespace(name="P1", used_range=used),))
    replacement.fullname = "C:/source-b/설비현황.xlsx"
    replacement.app = apps[selection.app_pid]
    replacement.app.books = [replacement]
    with pytest.raises(WorkbookSelectionChangedError, match="다시 선택"):
        read_sheet(selection.name, "P1", app_pid=selection.app_pid, full_name=selection.full_name)

    # 새 파일을 명시적으로 다시 선택했을 때만 해당 파일을 읽을 수 있다.
    used.failure = None
    reselected = list_open_workbooks()[0]
    result = read_sheet(reselected.name, "P1", app_pid=reselected.app_pid, full_name=reselected.full_name)
    assert result.values == [["다른 파일"]]


def test_path_changed_while_reading_is_not_returned_as_valid_data(monkeypatch):
    """읽는 도중 다른 이름으로 저장되어 경로가 바뀌면 읽기 결과를 반환하지 않는지 확인한다.

    param
    - monkeypatch: 셀 값을 읽을 때 경로 변경을 재현하는 fixture.
    """
    sheet = SimpleNamespace(name="P1", used_range=FakeUsedRange([["원문"]]))
    book = fake_book("원본.xlsx", (sheet,))
    use_fake_excel(monkeypatch, [[book]])
    selection = list_open_workbooks()[0]
    original_getter = FakeUsedRange.value.fget

    def read_and_move(used):
        """셀 값을 반환하면서 통합문서 경로 변경을 재현한다.

        param
        - used: 읽을 가짜 사용 영역.

        return
        - 경로가 변경되기 전에 읽은 가짜 셀 값.
        """
        values = original_getter(used)
        book.fullname = "C:/other/원본.xlsx"
        return values

    monkeypatch.setattr(FakeUsedRange, "value", property(read_and_move))
    with pytest.raises(WorkbookSelectionChangedError):
        read_sheet(selection.name, "P1", app_pid=selection.app_pid, full_name=selection.full_name)


@pytest.mark.parametrize("full_name", [r"c:\DATA\원본.xlsx", "C:/data/./원본.xlsx"])
def test_same_windows_path_spelling_is_accepted(monkeypatch, full_name):
    """같은 Windows 경로의 대소문자·구분자 차이 때문에 정상 선택을 거부하지 않는지 확인한다.

    param
    - monkeypatch: 실제 Excel을 가짜 창으로 교체하는 fixture.
    - full_name: 같은 경로를 다른 표기로 나타낸 선택 정보.
    """
    sheet = SimpleNamespace(name="P1", used_range=FakeUsedRange([["원문"]]))
    use_fake_excel(monkeypatch, [[fake_book("원본.xlsx", (sheet,))]])
    assert read_sheet("원본.xlsx", "P1", app_pid=1000, full_name=full_name).values == [["원문"]]


def test_unsaved_workbook_uses_excel_full_name_without_guessing_a_directory(monkeypatch):
    """저장 전 통합문서도 Excel이 제공한 전체 이름으로 선택을 검증하는지 확인한다.

    param
    - monkeypatch: 저장하지 않은 가짜 통합문서를 연결하는 fixture.
    """
    book = fake_book("Book1", (SimpleNamespace(name="P1", used_range=FakeUsedRange([["원문"]])),))
    book.fullname = "Book1"
    use_fake_excel(monkeypatch, [[book]])
    selection = list_open_workbooks()[0]
    assert read_sheet(selection.name, "P1", app_pid=selection.app_pid, full_name=selection.full_name).values == [["원문"]]


@pytest.mark.parametrize("kwargs", [{"app_pid": 1000}, {"full_name": "C:/data/원본.xlsx"}])
def test_process_and_full_name_are_both_required(kwargs):
    """프로세스나 전체 이름 검증을 생략한 읽기 호출을 허용하지 않는지 확인한다.

    param
    - kwargs: 필수 선택 정보가 하나 빠진 인자.
    """
    with pytest.raises(TypeError):
        read_sheet("원본.xlsx", "P1", **kwargs)


@pytest.mark.parametrize("full_name", [None, "", " ", 1])
def test_empty_or_invalid_selection_path_is_rejected(full_name):
    """빈 전체 이름으로 경로 검증을 우회할 수 없는지 확인한다.

    param
    - full_name: 잘못된 선택 전체 이름.
    """
    with pytest.raises(ValueError, match="전체 파일명"):
        read_sheet("원본.xlsx", "P1", app_pid=1000, full_name=full_name)
