"""UI 시작점. 대시보드·히트맵·목록/상세는 같은 합성 관측을 쓰며 DB·Excel을 시작하지 않는다."""

import argparse
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from setron.ui.main_window import MainWindow
from setron.ui.dashboard import DashboardPage
from setron.ui.dashboard_data import DashboardSource
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.heatmap import HeatmapPage
from setron.ui.equipment import EquipmentPage
from setron.ui.target_data import describe_targets, initial_targets
from setron.ui.target_dialog import TargetDialog
from setron.ui.theme import install_theme
from setron.ui.version_data import catalog_from_dashboard
from setron.ui.version_demo import demo_version_catalog
from setron.ui.versions import VersionsPage
from setron.ui.item_data import ItemManagerData, item_data_from_source
from setron.ui.items import ItemsPage


def create_window(source: DashboardSource | None = None, *, version_loader=None, item_loader=None) -> MainWindow:
    demo = source is None
    source = source if source is not None else demo_dashboard_source()
    window = MainWindow(query_catalog=source.catalog)
    window.setWindowTitle("SETRON · 설비 현황 미리보기")
    window.notice.setText("작업 단위 7 · 목표 N 편집 · 합성 예시 데이터 · DB 미연결 · 임시 선택")
    window.query_panel.set_target_describer(lambda targets: describe_targets(source, targets))
    window.query_panel.set_target_draft(initial_targets(source))
    window.query_panel.goal_button.setEnabled(True)
    window.query_panel.goal_button.setToolTip("모델·계열별 목표 N 임시 선택 · 새 계산은 조회 적용 후 · DB 저장 없음")
    window.query_panel.apply_button.setToolTip("선택한 위치·설비군·목표 N으로 합성 예시 결과를 함께 다시 계산합니다. DB 미연결.")
    window.target_dialog = None

    def open_targets():
        if window.target_dialog is not None and window.target_dialog.isVisible():
            window.target_dialog.raise_()
            window.target_dialog.activateWindow()
            return
        state = window.query_state
        generation = state.catalog_revision
        current = lambda: state.catalog_revision == generation and state.catalog == source.catalog
        dialog = TargetDialog(source, state.target_draft, group_key=state.draft.group_key, is_current=current, parent=window)
        window.target_dialog = dialog

        def finish(result):
            if result == TargetDialog.DialogCode.Accepted and current() and dialog.result_targets is not None:
                window.query_panel.set_target_draft(dialog.result_targets)
            if window.target_dialog is dialog:
                window.target_dialog = None
            dialog.deleteLater()

        dialog.finished.connect(finish)
        dialog.open()

    window.query_panel.goal_button.clicked.connect(open_targets)
    window.query_panel.catalog_changed.connect(lambda *_: window.query_panel.goal_button.setEnabled(window.query_state.catalog == source.catalog))
    dashboard = DashboardPage(window.query_state, source, drilldown_connected=True)
    heatmap = HeatmapPage(window.query_state, source, detail_connected=True)
    equipment = EquipmentPage(window.query_state, source)
    custom_versions = version_loader is not None
    if version_loader is None:
        version_loader = lambda: demo_version_catalog(source) if demo else catalog_from_dashboard(source)
    versions = VersionsPage(version_loader)
    if item_loader is None:
        # 외부 릴리즈 ID를 기본 합성 설비 ID와 임의로 연결하지 않는다.
        item_loader = (lambda: ItemManagerData(version_loader())) if custom_versions else (
            lambda: item_data_from_source(version_loader(), source))
    items = ItemsPage(item_loader)
    window.register_page("dashboard", dashboard)
    window.register_page("heatmap", heatmap)
    window.register_page("versions", versions, uses_query=False)
    window.register_page("items", items, uses_query=False)
    window.register_auxiliary_page("equipment", equipment, parent_key="heatmap", title="설비 목록")
    list_action = window.header.actions["equipment_list"]
    list_action.setToolTip("현재 적용 조건의 전체 설비 목록")
    origin_scroll = {}
    overview_notice = window.notice.text()

    def sync_mode(*_):
        if window.route == "items":
            window.header.title.setText("Item 상세" if items.mode == "detail" else "Item 매니저")
            window.notice.setText("Item 조회 전용 · 자료 출처와 관측 기준일은 본문 참조")
            list_action.setEnabled(False)
        elif window.route == "versions":
            window.header.title.setText("릴리즈 버전 상세" if versions.mode == "detail" else "릴리즈 버전 목록")
            window.notice.setText("릴리즈 조회 전용 · 예시 자료 · DB 미연결")
            list_action.setEnabled(False)
        else:
            window.notice.setText(overview_notice)
            list_action.setEnabled(equipment.snapshot is not None and window.query_state.applied_is_current)
        if window.route == "equipment":
            window.header.title.setText("설비 상세" if equipment.mode == "detail" else "설비 목록")
            window.query_panel.setVisible(equipment.mode == "list")

    def remember_origin():
        if window.route in ("dashboard", "heatmap"):
            origin_scroll[window.route] = window.scroll.verticalScrollBar().value()

    def open_scope(request):
        remember_origin()
        if equipment.open_scope(request, origin="dashboard"):
            window.navigate("equipment")

    def open_detail(request):
        remember_origin()
        if equipment.open_detail(request, origin="heatmap"):
            window.navigate("equipment")

    def open_all(action):
        if action == "equipment_list":
            remember_origin()
            if equipment.open_all(window.route):
                window.navigate("equipment")

    def go_back(origin):
        if window.navigate(origin):
            def restore_scroll():
                if window.route == origin:
                    window.scroll.verticalScrollBar().setValue(origin_scroll.get(origin, 0))
            QTimer.singleShot(0, restore_scroll)

    dashboard.drilldown_requested.connect(open_scope)
    heatmap.equipment_selected.connect(open_detail)
    equipment.back_requested.connect(go_back)
    equipment.mode_changed.connect(sync_mode)
    versions.mode_changed.connect(sync_mode)
    items.mode_changed.connect(sync_mode)
    window.page_changed.connect(sync_mode)
    window.header.action_requested.connect(open_all)

    def apply_query(query):
        if not heatmap.begin_query(query):
            return
        equipment.clear("예시 결과 계산 중")
        list_action.setEnabled(False)
        if dashboard.apply_query(query):
            # 한 번 계산한 같은 불변 결과를 두 화면에서 공유한다.
            heatmap.accept_result(query, dashboard.snapshot)
            list_action.setEnabled(equipment.accept_result(query, dashboard.snapshot))
        else:
            heatmap.fail_query(query)
            equipment.clear("설비 결과 표시 실패 · 다시 적용해 주세요")

    window.query_applied.connect(apply_query)
    window.query_panel.catalog_changed.connect(dashboard.invalidate)
    window.query_panel.catalog_changed.connect(heatmap.invalidate)
    window.query_panel.catalog_changed.connect(equipment.clear)
    window.query_panel.catalog_changed.connect(lambda *_: list_action.setEnabled(False))
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
