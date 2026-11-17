"""Windows 작업 표시줄이 사용하는 창 클래스 아이콘을 설정한다."""

import sys

from PySide6.QtWidgets import QApplication

from setron.ui.theme import ASSETS


def set_taskbar_icon(window):
    if sys.platform != "win32" or QApplication.platformName() != "windows":
        return

    import ctypes
    from ctypes import wintypes

    app = QApplication.instance()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL
    user32.SetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetClassLongPtrW.restype = ctypes.c_size_t

    if not hasattr(app, "_setron_class_icons"):
        handles = []
        # Qt가 창을 다시 만들거나 닫아도 클래스의 아이콘 핸들은 앱 종료까지 보관한다.
        try:
            for width_metric, height_metric in ((11, 12), (49, 50)):
                handle = user32.LoadImageW(None, str(ASSETS / "setron-window-icon.ico"), 1,
                                           user32.GetSystemMetrics(width_metric),
                                           user32.GetSystemMetrics(height_metric), 0x10)
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                handles.append(handle)
        except OSError:
            for handle in handles:
                user32.DestroyIcon(handle)
            raise

        app._setron_class_icons = tuple(handles)

        def release_icons():
            for handle in handles:
                user32.DestroyIcon(handle)

        app.aboutToQuit.connect(release_icons)

    hwnd = int(window.winId())
    for index, handle in zip((-14, -34), app._setron_class_icons):
        ctypes.set_last_error(0)
        previous = user32.SetClassLongPtrW(hwnd, index, handle)
        if not previous and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
