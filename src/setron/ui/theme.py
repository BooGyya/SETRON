"""VER5 Light 공통 토큰·폰트·원본 아이콘. 업무 상태 계산은 포함하지 않는다."""

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QTabBar


ASSETS = Path(__file__).parent / "assets"
INK = "#142033"
SECONDARY = "#5b6880"
PRIMARY = "#3c5ce9"
CANVAS = "#f5f6f8"
# VER5 (Figma VER5 - 황민정) 공통 변수: --line, --soft, --accentsoft
LINE = "#d5d7da"
SOFT = "#f9fafb"
ACCENT_SOFT = "#e8ebfc"
TONES = {
    "neutral": ("#f0f3f8", SECONDARY, "#e5eaf3"),
    "info": ("#e8ebfc", PRIMARY, "#e8ebfc"),
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


class WheelGuard(QObject):
    """콤보·숫자 입력·탭이 마우스 휠로 값이 바뀌지 않게 하고, 휠은 바깥 스크롤로 넘긴다."""

    TARGETS = (QComboBox, QAbstractSpinBox, QTabBar)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Wheel and isinstance(watched, self.TARGETS):
            parent = watched.parentWidget()
            while parent is not None:
                if isinstance(parent, QAbstractScrollArea) and parent.verticalScrollBar().maximum() > 0:
                    QApplication.sendEvent(parent.verticalScrollBar(), event)
                    break
                parent = parent.parentWidget()
            return True
        return False


def install_theme(app: QApplication) -> None:
    # 휠로 조회 조건·보기 탭이 바뀌지 않도록 애플리케이션 전체에 한 번만 건다.
    if app.findChild(WheelGuard) is None:
        app.installEventFilter(WheelGuard(app))
    # 여러 창을 만들더라도 폰트는 애플리케이션마다 한 번만 등록한다.
    if not app.property("setronFontsLoaded"):
        for name in ("IBMPlexSansKR-Regular.ttf", "IBMPlexSansKR-Medium.ttf", "Inter.ttf"):
            QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / name))
        app.setProperty("setronFontsLoaded", True)
    app.setStyle("Fusion")
    app.setFont(font())
    app.setStyleSheet("""
        QWidget { color: #142033; }
        QMainWindow, QWidget#canvas { background: #f5f6f8; }
        QFrame#sidebar { background: white; border: none; border-right: 1px solid #d5d7da; }
        QWidget#sidebarContent { background: white; }
        QScrollArea { border: none; background: transparent; }
        QScrollArea > QWidget { background: transparent; }
        QFrame#card { background: white; border: 1px solid white; border-radius: 10px; }
        QFrame#card[outlined="true"] { border-color: #d5d7da; border-radius: 8px; }
        QFrame#card[metricTone="achievement"] { border-color: #d8defb;
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #edf0fd,stop:0.72 white); }
        QFrame#card[metricTone="risk"] { border-color: #e58a9b;
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #fce2e7,stop:0.72 white); }
        QWidget#dashboardMatrix { background: white; }
        QTabBar::tab { background: #f7f9fc; color: #5b6880; padding: 9px 20px;
                       border: 1px solid #e5eaf3; border-radius: 8px; margin-right: 8px; }
        QTabBar::tab:selected { background: #edf0fd; color: #3c5ce9; border-color: #3c5ce9; }
        QPushButton[dashboardRisk="true"] { background: #fce2e7; color: #ad344d; }
        QPushButton[dashboardRiskBadge="true"] { background: #c9485e; color: white; border: 1px solid #c9485e;
                                                border-radius: 4px; padding: 0 6px; min-height: 18px; }
        QLabel { background: transparent; }
        QPushButton { background: #f9fafb; border: 1px solid #d5d7da; border-radius: 10px;
                      padding: 8px 18px; min-height: 20px; }
        QPushButton:hover { background: #edf0fd; border-color: #3c5ce9; }
        QPushButton:pressed { background: #d8defb; }
        QPushButton:focus { border: 2px solid #3c5ce9; }
        QPushButton:disabled { color: #8b95a8; background: #f9fafb; border-color: #e9eef6; }
        QPushButton[primary="true"] { border-color: #3c5ce9; color: white;
            background: qlineargradient(x1:0,y1:0,x2:0.3,y2:1,stop:0 #3c5ce9,stop:0.714 #3652ca); }
        QPushButton[soft="true"] { background: #e8ebfc; border-color: #a8b4fa; color: #3c5ce9; }
        QPushButton[soft="true"]:hover { background: #dfe4fb; border-color: #3c5ce9; }
        QPushButton[primary="true"]:hover { background: #304dc9; }
        QPushButton[primary="true"]:disabled { background: #d8defb; border-color: #d8defb; color: #5b6880; }
        QPushButton[nav="true"] { padding: 0; min-height: 44px; max-height: 44px; border: 1px solid transparent;
                                  border-radius: 12px; background: transparent; }
        QPushButton[nav="true"]:checked { background: #e8ebfc; border-radius: 6px; }
        QPushButton[nav="true"]:focus { border-color: #a8b4fa; }
        QPushButton[nav="true"]:hover:!checked { background: #f5f6f8; }
        QPushButton[queryChoice="true"] { padding: 0 8px; min-height: 38px; max-height: 38px; }
        QPushButton[queryChoice="true"][compactChoice="true"] { min-height: 34px; max-height: 34px; }
        QPushButton[queryChoice="true"]:checked { background: #edf0fd; border-color: #3c5ce9; color: #3c5ce9; }
        QPushButton[queryChoice="true"]:focus { border: 2px solid #3c5ce9; min-height: 36px; max-height: 36px; }
        QPushButton[queryChoice="true"][compactChoice="true"]:focus { min-height: 32px; max-height: 32px; }
        QComboBox[queryField="true"] { background: #f9fafb; border: 1px solid #d5d7da;
                                      border-radius: 10px; padding: 0 12px; }
        QComboBox[queryField="true"]:focus { border: 2px solid #3c5ce9; }
        QComboBox[queryField="true"]::drop-down { border: none; width: 28px; background: transparent; }
        QComboBox[queryField="true"] QAbstractItemView { background: white; color: #142033;
                                                       selection-background-color: #edf0fd;
                                                       selection-color: #3c5ce9; padding: 4px; }
        QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
        QScrollBar::handle:vertical { background: #cbd3e1; border-radius: 5px; min-height: 32px; }
        QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
        QScrollBar::handle:horizontal { background: #cbd3e1; border-radius: 5px; min-width: 32px; }
        QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
        QToolTip { background: #142033; color: white; border: none; padding: 8px; }
        QMessageBox { background: #f5f6f8; }
    """)
