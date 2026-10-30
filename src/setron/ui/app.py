"""UI 시작점. 대시보드는 합성 예시로 확인하며 DB·Excel을 시작하지 않는다."""

import argparse
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from setron.ui.main_window import MainWindow
from setron.ui.dashboard import DashboardPage
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.theme import install_theme


def create_window() -> MainWindow:
    source = demo_dashboard_source()
    window = MainWindow(query_catalog=source.catalog)
    window.setWindowTitle("SETRON · 대시보드 미리보기")
    window.notice.setText("작업 단위 4 · 대시보드 · 합성 예시 데이터 · DB 미연결")
    dashboard = DashboardPage(window.query_state, source)
    window.register_page("dashboard", dashboard)
    window.query_applied.connect(dashboard.apply_query)
    window.query_panel.catalog_changed.connect(dashboard.invalidate)
    return window


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auto-close-ms", type=int, default=0, help="실행 검증용 자동 종료 시간")
    args = parser.parse_args(argv)
    if args.auto_close_ms < 0:
        parser.error("--auto-close-ms는 0 이상이어야 합니다.")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("SETRON")
    install_theme(app)
    window = create_window()
    if screen := app.primaryScreen():
        area = screen.availableGeometry()
        window.resize(min(1440, area.width() - 32), min(960, area.height() - 48))
    window.show()
    if args.auto_close_ms:
        QTimer.singleShot(args.auto_close_ms, window.close)
    return app.exec()
