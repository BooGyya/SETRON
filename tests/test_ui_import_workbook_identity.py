"""열린 Excel의 경로·프로세스 구분이 검토·저장 요청까지 유지된다."""

from PySide6.QtTest import QTest

from setron.features.excel_import.service import build_save_request
from setron.features.ai.schemas import AIResult, AIStatus, AITask, ExtractedRow, ExtractResult
from setron.infrastructure.excel_reader import WorkbookInfo
from setron.ui.import_page import ImportPage, _workbook_key
from test_ui_import import app, identity_reader  # noqa: F401
from test_import_validation import row


def test_same_named_workbooks_keep_distinct_identity_in_review_and_save_request(app):
    books = [WorkbookInfo("현황.xlsx", "C:/화성/현황.xlsx", ["Memory"], 7101),
             WorkbookInfo("현황.xlsx", "C:/평택/현황.xlsx", ["Memory"], 7102)]
    def extract(kind, sheet, cells):
        value = row()
        return AIResult(AITask.EXCEL_EXTRACT, AIStatus.OK, "검토 예제",
                        ExtractResult(kind, sheet, (ExtractedRow(value.original, value.sources),)))
    page = ImportPage(excel=identity_reader(books), extractor=extract)
    try:
        page.load_workbooks()
        ids = []
        for index, book in enumerate(books):
            page.workbook_combo.setCurrentIndex(index)
            assert page.read_selected()
            QTest.qWait(10)
            assert page.extract()
            QTest.qWait(10)
            assert page.draft is not None
            assert page.draft.workbook_name == "현황.xlsx"
            assert page.draft.workbook_id == _workbook_key(book)
            request = build_save_request(page.draft, f"identity-{index}")
            assert request.workbook_id == _workbook_key(book)
            assert "workbook_id" not in request.raw_data
            ids.append(request.workbook_id)
        assert ids[0] != ids[1]
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()
