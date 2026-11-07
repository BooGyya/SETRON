"""열린 Excel 호출을 워커 안에서 완료하고 plain 데이터만 반환한다."""

import sys
import warnings

from setron.infrastructure.excel_reader import ExcelDiscoveryWarning
from setron.features.excel_import.service import snapshot_sheet


def excel_task(call, *, com=False):
    """COM은 호출하는 스레드에서 초기화/정리한다. Excel 객체를 GUI로 전달하지 않는다."""
    pythoncom = None
    if com and sys.platform == "win32":
        import pythoncom
        pythoncom.CoInitialize()
    try:
        return call()
    finally:
        if pythoncom is not None:
            pythoncom.CoUninitialize()


def discover_workbooks(excel, *, com=False):
    def discover():
        with warnings.catch_warnings(record=True) as notices:
            warnings.simplefilter("always", ExcelDiscoveryWarning)
            books = tuple(excel.list_open_workbooks())
        messages = tuple(str(notice.message) for notice in notices if issubclass(notice.category, ExcelDiscoveryWarning))
        return books, messages
    return excel_task(discover, com=com)


def read_workbook(excel, workbook, sheet, *, com=False):
    def read():
        data = excel.read_sheet(workbook.name, sheet, app_pid=workbook.app_pid, full_name=workbook.full_name)
        return data.workbook_name, snapshot_sheet(data)
    return excel_task(read, com=com)
