"""UI 시작점. 대시보드·히트맵은 같은 합성 예시 결과를 쓰며 DB·Excel을 시작하지 않는다."""

import argparse
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from setron.ui.main_window import MainWindow
from setron.ui.dashboard import DashboardPage
from setron.ui.dashboard_data import DashboardSource
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.heatmap import HeatmapPage
from setron.ui.theme import install_theme


def create_window(source: DashboardSource | None = None) -> MainWindow:
    source = source if source is not None else demo_dashboard_source()
    window = MainWindow(query_catalog=source.catalog)
    window.setWindowTitle("SETRON · 대시보드·히트맵 미리보기")
    window.notice.setText("작업 단위 5 · 히트맵 · 합성 예시 데이터 · DB 미연결")
    dashboard = DashboardPage(window.query_state, source)
    heatmap = HeatmapPage(window.query_state, source)
    window.register_page("dashboard", dashboard)
    window.register_page("heatmap", heatmap)

    def apply_query(query):
        if not heatmap.begin_query(query):
            return
        if dashboard.apply_query(query):
            # 한 번 계산한 같은 불변 결과를 두 화면에서 공유한다.
            heatmap.accept_result(query, dashboard.snapshot)
        else:
            heatmap.fail_query(query)

    window.query_applied.connect(apply_query)
    window.query_panel.catalog_changed.connect(dashboard.invalidate)
    window.query_panel.catalog_changed.connect(heatmap.invalidate)
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
