"""C-1~C-3 확인 전용 합성 통합문서와 예제 AI 추출. 실제 Excel 파일·회사 자료·실제 AI 응답이 아니다."""

import json
from datetime import datetime

from setron.features.ai.examples import EXTRACT_REPLY
from setron.features.ai.schemas import ExtractKind
from setron.features.ai.service import extract_sheet
from setron.infrastructure.ai_client import ExampleChatClient
from setron.infrastructure.excel_reader import (
    MergedRange, SheetData, SheetNotFoundError, WorkbookInfo, WorkbookNotFoundError,
)

EQUIPMENT_BOOK = "DEMO_설비현황(합성).xlsx"
RELEASE_BOOK = "DEMO_릴리즈노트(합성).xlsx"

# 예제 응답(features.ai.examples.EXTRACT_REPLY)의 셀 주소에 맞춘 합성 시트. 기준일은 일부러 비워 둔다.
_EQUIPMENT_SHEET = (
    ["화성", None, None, None, None, None],
    ["합성 예시 · 실제 회사 자료 아님", None, None, None, None, None],
    [None, None, None, None, None, None],
    ["No", "Line", "호기", "모델", "SW 계열", "SW Ver"],
    [1.0, "P1D", "ETCH-001", "M-C4", "C4", "v4.56.6.43"],
)
_RELEASE_SHEET = (
    ["Version", "Date", "No", "Content", "Model"],
    ["v1.0.3", datetime(2026, 9, 1), 1.0, "Recipe 승인 상태 확인", "C4"],
    ["v1.0.3", datetime(2026, 9, 1), 2.0, "환경안전 Interlock 추가", "C4, C4 Plus"],
)
_BOOKS = {
    EQUIPMENT_BOOK: {"Memory": (_EQUIPMENT_SHEET, (MergedRange(1, 1, 1, 2), MergedRange(2, 1, 2, 6)))},
    RELEASE_BOOK: {"Release": (_RELEASE_SHEET, ())},
}

RELEASE_EXTRACT_REPLY = json.dumps(
    {
        "rows": [
            {
                "values": {"version_raw": "v1.0.3", "release_date": "2026-09-01", "item_no": str(no),
                           "content": content, "target_models": models},
                "sources": {"version_raw": f"A{row}", "release_date": f"B{row}", "item_no": f"C{row}",
                            "content": f"D{row}", "target_models": f"E{row}"},
            }
            for row, no, content, models in ((2, 1, "Recipe 승인 상태 확인", "C4"),
                                              (3, 2, "환경안전 Interlock 추가", "C4, C4 Plus"))
        ],
        "review_notes": [{"reason": "대상 모델에 파생형 표기가 섞여 있어 적용 범위 확인이 필요합니다.",
                          "field": "target_models", "address": "E3"}],
    },
    ensure_ascii=False,
)


class DemoExcel:
    """``excel_reader``와 같은 함수 형식으로 합성 통합문서를 돌려준다."""

    def list_open_workbooks(self):
        return [WorkbookInfo(name, f"(합성) {name}", list(sheets)) for name, sheets in _BOOKS.items()]

    def read_sheet(self, workbook_name, sheet_name):
        if workbook_name not in _BOOKS:
            raise WorkbookNotFoundError(f"열려 있는 통합문서 중 '{workbook_name}'이(가) 없습니다.")
        if sheet_name not in _BOOKS[workbook_name]:
            raise SheetNotFoundError(f"'{workbook_name}'에 '{sheet_name}' 시트가 없습니다.")
        values, merged = _BOOKS[workbook_name][sheet_name]
        return SheetData(workbook_name, sheet_name, 1, 1, [list(row) for row in values], list(merged))


def example_extractor(kind, sheet_name, cells):
    """실제 회사 AI 연결 전 예제 응답. 시트 내용과 관계없이 종류별 고정 응답을 EXAMPLE 출처로 돌려준다."""
    reply = EXTRACT_REPLY if kind is ExtractKind.EQUIPMENT_STATUS else RELEASE_EXTRACT_REPLY
    return extract_sheet(ExampleChatClient([reply]), kind, sheet_name, cells)
