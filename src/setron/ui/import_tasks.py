"""열린 Excel 호출을 워커 안에서 완료하고 plain 데이터만 반환한다."""

import sys
import warnings
from importlib import import_module
from threading import Lock

from setron.infrastructure.excel_reader import (
    EXCEL_BUSY_MESSAGE, ExcelBusyError, ExcelDiscoveryWarning, is_excel_busy_error,
)
from setron.features.excel_import.service import snapshot_sheet


_COM_LOCK = Lock()
MAX_COM_ATTEMPTS = 20


def _com_backend():
    return import_module("xlwings._xlwindows")


def excel_task(call, *, com=False):
    """COM은 호출하는 스레드에서 초기화/정리한다. Excel 객체를 GUI로 전달하지 않는다."""
    pythoncom = None
    if com and sys.platform == "win32":
        import pythoncom
        pythoncom.CoInitialize()
    try:
        if pythoncom is None:
            return call()
        # xlwings 기본값 0은 busy 오류를 무한 재시도한다. 전역 설정이므로 실제
        # Excel 작업을 직렬화하고 이 호출이 끝나면 원래 값을 반드시 복구한다.
        with _COM_LOCK:
            backend = _com_backend()
            previous = backend.N_COM_ATTEMPTS
            backend.N_COM_ATTEMPTS = min(previous, MAX_COM_ATTEMPTS) if previous > 0 else MAX_COM_ATTEMPTS
            try:
                return call()
            except ExcelBusyError:
                raise
            except Exception as error:
                if is_excel_busy_error(error):
                    raise ExcelBusyError(EXCEL_BUSY_MESSAGE) from error
                raise
            finally:
                backend.N_COM_ATTEMPTS = previous
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
