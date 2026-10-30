"""VER3 Light 공통 토큰·폰트·원본 아이콘. 업무 상태 계산은 포함하지 않는다."""

from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


ASSETS = Path(__file__).parent / "assets"
INK = "#18243c"
SECONDARY = "#5b6880"
PRIMARY = "#3c5ce9"
CANVAS = "#f4f7fc"
TONES = {
    "neutral": ("#f0f3f8", SECONDARY, "#e5eaf3"),
    "info": ("#edf0fd", PRIMARY, "#d8defb"),
    "success": ("#d4ebdf", "#246b4a", "#208457"),
    "warning": ("#fbe5a3", "#856009", "#d5b660"),
    "danger": ("#fce2e7", "#ad344d", "#e58a9b"),
}


def icon(filename: str) -> QIcon:
    return QIcon(str(ASSETS / filename))


def svg_size(filename: str) -> QSize:
    """원본 루트 크기를 유지한다. 18px 슬롯 안에서도 SVG 자체를 확대하지 않는다."""
    return QSvgRenderer(str(ASSETS / filename)).defaultSize()


def font(size: int = 14, *, medium: bool = False, numeric: bool = False) -> QFont:
    result = QFont("Inter" if numeric else "IBM Plex Sans KR")
    result.setPixelSize(size)
    result.setWeight(QFont.Weight.Medium if medium else QFont.Weight.Normal)
    return result


def install_theme(app: QApplication) -> None:
    # 여러 창을 만들더라도 폰트는 애플리케이션마다 한 번만 등록한다.
    if not app.property("setronFontsLoaded"):
        for name in ("IBMPlexSansKR-Regular.ttf", "IBMPlexSansKR-Medium.ttf", "Inter.ttf"):
            QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / name))
        app.setProperty("setronFontsLoaded", True)
    app.setStyle("Fusion")
    app.setFont(font())
    app.setStyleSheet("""
        QWidget { color: #18243c; }
        QMainWindow, QWidget#canvas { background: #f4f7fc; }
        QFrame#sidebar, QWidget#sidebarContent { background: white; }
        QScrollArea { border: none; background: transparent; }
        QScrollArea > QWidget { background: transparent; }
        QFrame#card { background: white; border: 1px solid #e9eef6; border-radius: 16px; }
        QLabel { background: transparent; }
        QPushButton { background: #f7f9fc; border: 1px solid #e5eaf3; border-radius: 10px;
                      padding: 8px 18px; min-height: 20px; }
        QPushButton:hover { background: #edf0fd; border-color: #3c5ce9; }
        QPushButton:pressed { background: #d8defb; }
        QPushButton:focus { border: 2px solid #3c5ce9; }
        QPushButton:disabled { color: #8b95a8; background: #f7f9fc; border-color: #e9eef6; }
        QPushButton[primary="true"] { background: #3c5ce9; border-color: #3c5ce9; color: white; }
        QPushButton[primary="true"]:hover { background: #304dc9; }
        QPushButton[primary="true"]:disabled { background: #d8defb; border-color: #d8defb; color: #5b6880; }
        QPushButton[nav="true"] { padding: 0; min-height: 44px; max-height: 44px; border: 1px solid transparent;
                                  border-radius: 12px; background: transparent; }
        QPushButton[nav="true"]:checked { background: #3c5ce9; }
        QPushButton[nav="true"]:focus { border-color: #a8b4fa; }
        QPushButton[nav="true"]:hover:!checked { background: #edf0fd; }
        QPushButton[queryChoice="true"] { padding: 0 8px; min-height: 38px; max-height: 38px; }
        QPushButton[queryChoice="true"][compactChoice="true"] { min-height: 34px; max-height: 34px; }
        QPushButton[queryChoice="true"]:checked { background: #edf0fd; border-color: #3c5ce9; color: #3c5ce9; }
        QPushButton[queryChoice="true"]:focus { border: 2px solid #3c5ce9; min-height: 36px; max-height: 36px; }
        QPushButton[queryChoice="true"][compactChoice="true"]:focus { min-height: 32px; max-height: 32px; }
        QComboBox[queryField="true"] { background: #f7f9fc; border: 1px solid #e5eaf3;
                                      border-radius: 10px; padding: 0 12px; }
        QComboBox[queryField="true"]:focus { border: 2px solid #3c5ce9; }
        QComboBox[queryField="true"] QAbstractItemView { background: white; color: #18243c;
                                                       selection-background-color: #edf0fd;
                                                       selection-color: #3c5ce9; padding: 4px; }
        QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
        QScrollBar::handle:vertical { background: #cbd3e1; border-radius: 5px; min-height: 32px; }
        QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
        QScrollBar::handle:horizontal { background: #cbd3e1; border-radius: 5px; min-width: 32px; }
        QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
        QToolTip { background: #18243c; color: white; border: none; padding: 8px; }
        QMessageBox { background: #f4f7fc; }
    """)
