"""실제 Windows 창 클래스에 SETRON 아이콘을 등록하는지 검증한다."""

import ctypes
import sys
from ctypes import wintypes

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from setron.ui.main_window import MainWindow


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 창 클래스 아이콘 검사")
def test_native_taskbar_icons_are_set_before_the_window_is_visible():
    app = QApplication.instance() or QApplication([])
    if app.platformName() != "windows":
        pytest.skip("실제 Windows Qt 플랫폼이 필요합니다.")

    window = MainWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    try:
        window.show()
        app.processEvents()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetClassLongPtrW.restype = ctypes.c_size_t
        hwnd = int(window.winId())
        registered = tuple(user32.GetClassLongPtrW(hwnd, index) for index in (-14, -34))
        assert all(registered)
        assert registered == app._setron_class_icons

        # DB 재연결 등으로 주 창을 다시 만들어도 같은 유효한 아이콘을 사용한다.
        replacement = MainWindow()
        replacement.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        try:
            replacement.show()
            app.processEvents()
            assert tuple(user32.GetClassLongPtrW(int(replacement.winId()), index)
                         for index in (-14, -34)) == registered
        finally:
            replacement.close()
            replacement.deleteLater()
    finally:
        window.close()
        window.deleteLater()
