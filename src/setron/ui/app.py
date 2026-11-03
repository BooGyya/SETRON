"""UI 시작점. 대시보드·히트맵·목록/상세는 같은 합성 관측을 쓰며 DB·Excel을 시작하지 않는다.

데이터 가져오기는 사용자가 불러오기를 누를 때만 실행 중인 Excel에 연결하고, AI는 예제 응답을 사용한다.
"""

import argparse
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from setron.ui.main_window import MainWindow
from setron.ui.dashboard import DashboardPage
from setron.ui.dashboard_data import DashboardSource
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.database_source import dashboard_source_from_env
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
from setron.ui.ai_data import facts_from_snapshot
from setron.ui.ai_page import AIPage
from setron.ui.import_demo import DemoExcel
from setron.ui.import_page import ImportPage, saver_from_env
from setron.ui.read_worker import BackgroundRead


def create_window(source: DashboardSource | None = None, *, version_loader=None, item_loader=None,
                  excel=None, extractor=None, import_saver=None, ai_parser=None, ai_analyzer=None) -> MainWindow:
    """공통 화면과 명시적으로 전달된 공급자를 연결한다.

    param
    - source: 대시보드 자료. None이면 합성 예시.
    - version_loader: 릴리즈 메모리 공급자 또는 BackgroundRead.
    - item_loader: Item 메모리 공급자 또는 BackgroundRead. 생략 시 릴리즈 공급자의 실행 방식을 따른다.
    - excel: 실행 중 Excel 읽기 객체 또는 기본 읽기 객체를 선택하는 None.
    - extractor: 추출 공급자. None이면 예제 응답.
    - import_saver: Import 저장 공급자. None이면 메모리 저장.
    - ai_parser: 질문 해석 공급자. None이면 예제 응답.
    - ai_analyzer: 분석 공급자. None이면 예제 응답.

    return
    - 실제 DB 연결을 자동 생성하지 않는 MainWindow.
    """
    demo = source is None
    source = source if source is not None else demo_dashboard_source()
    window = MainWindow(query_catalog=source.catalog)
    window.setWindowTitle("SETRON · 설비 현황 미리보기")
    window.notice.setText("작업 단위 7 · 목표 N 편집 · 합성 예시 데이터 · DB 미연결 · 임시 선택" if demo
                          else f"{getattr(source, 'notice', source.source_label)} · 목표 N 임시 선택")
    window.query_panel.set_target_describer(lambda targets: describe_targets(source, targets))
    window.query_panel.set_target_draft(initial_targets(source))
    window.query_panel.goal_button.setEnabled(True)
    window.query_panel.goal_button.setToolTip("모델·계열별 목표 N 임시 선택 · 새 계산은 조회 적용 후 · DB 저장 없음")
    window.query_panel.apply_button.setToolTip("선택한 위치·설비군·목표 N으로 합성 예시 결과를 함께 다시 계산합니다. DB 미연결." if demo
                                              else "선택한 위치·설비군·목표 N으로 저장된 자료를 다시 읽어 계산합니다.")
    window.target_dialog = None

    def open_targets():
        if window.target_dialog is not None and window.target_dialog.isVisible():
            window.target_dialog.raise_()
            window.target_dialog.activateWindow()
            return
        state = window.query_state
        generation = state.catalog_revision
        current = lambda: state.catalog_revision == generation and state.catalog == source.catalog
        if hasattr(source, "align_targets"):
            # DB 공급자는 조회 때마다 다시 읽으므로 새로 저장된 모델·계열을 목표 초안에 맞춘다.
            try:
                source.refresh()
                window.query_panel.set_target_draft(source.align_targets(state.target_draft))
            except RuntimeError as error:
                window.notice.setText(str(error))
                return
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
        if isinstance(version_loader, BackgroundRead):
            item_loader = BackgroundRead(item_loader)
    items = ItemsPage(item_loader)
    # 저장 서비스·회사 AI 연결 전: 각 화면의 기본값은 메모리 저장소와 예제 응답이며 출처를 표시한다.
    importer = ImportPage(excel, extractor, import_saver)

    def apply_ai_selection(selection):
        window.query_panel.set_draft(selection)
        return window.query_panel.apply_draft()

    def current_facts():
        snapshot = dashboard.snapshot
        if snapshot is None or not window.query_state.is_current(snapshot.query):
            return None
        return facts_from_snapshot(snapshot)

    ai = AIPage(window.query_state, ai_parser, ai_analyzer, apply_ai_selection, current_facts)
    window.register_page("dashboard", dashboard)
    window.register_page("heatmap", heatmap)
    window.register_page("versions", versions, uses_query=False)
    window.register_page("items", items, uses_query=False)
    window.register_page("import", importer, uses_query=False)
    window.register_page("ai", ai, uses_query=False)
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
        elif window.route == "import":
            window.notice.setText(f"데이터 가져오기 · 실행 중인 Excel 읽기 · AI 예제 응답 · 저장 위치: {importer.saver.target_label}")
            list_action.setEnabled(False)
        elif window.route == "ai":
            window.notice.setText("AI 분석 · 예제 응답 · 질문으로 DB를 바꾸지 않음")
            list_action.setEnabled(False)
        elif window.route == "versions":
            window.header.title.setText("릴리즈 버전 상세" if versions.mode == "detail" else "릴리즈 버전 목록")
            window.notice.setText("릴리즈 조회 전용 · 자료 출처와 조회 상태는 본문 참조")
            list_action.setEnabled(False)
        else:
            window.notice.setText(overview_notice)
            list_action.setEnabled(equipment.snapshot is not None and window.query_state.applied_is_current)
        if window.route == "equipment":
            window.header.title.setText("설비 상세" if equipment.mode == "detail" else "설비 목록")
            window.query_panel.setVisible(equipment.mode == "list")
            if equipment.item_request is not None:
                window.notice.setText("Item 적용 근거 · 같은 관측 기준일 · 공통 조회 조건 유지")
                list_action.setEnabled(False)
                window.nav["heatmap"].setChecked(False)
                window.nav["items"].setChecked(True)

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
        item_revision = items.revision
        if window.navigate(origin):
            if origin == "items":
                focus = items.distribution.tiles.get(items.selected_equipment_id) if items.distribution_view.currentData() == "heatmap" else items.equipment_table
                if focus is not None:
                    focus.setFocus(Qt.FocusReason.OtherFocusReason)
            def restore_scroll():
                if window.route == origin and (origin != "items" or items.revision == item_revision):
                    window.scroll.verticalScrollBar().setValue(origin_scroll.get(origin, 0))
            # Item 타일의 반응형 재배치와 포커스 스크롤 이후 복원한다.
            if origin == "items":
                QTimer.singleShot(0, lambda: QTimer.singleShot(0, restore_scroll))
            else:
                QTimer.singleShot(0, restore_scroll)

    def open_item_equipment(request):
        if window.route != "items" or not items.is_current_selection(request):
            return
        origin_scroll["items"] = window.scroll.verticalScrollBar().value()
        if equipment.open_item_detail(request, is_current=items.is_current_selection):
            window.navigate("equipment")
            window.scroll.verticalScrollBar().setValue(0)

    def invalidate_item_detail():
        if equipment.item_request is not None:
            equipment.close_item_detail()
            if window.route == "equipment":
                window.navigate("items")

    def leave_item_detail(*_):
        if window.route != "equipment" and equipment.item_request is not None:
            equipment.close_item_detail()

    items.equipment_selected.connect(open_item_equipment)
    items.selection_invalidated.connect(invalidate_item_detail)
    window.page_changed.connect(leave_item_detail)

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
    # 대시보드 계산 뒤에 연결해야 AI 화면이 같은 적용 조건의 결과를 읽는다.
    window.query_applied.connect(lambda *_: ai.refresh())
    window.query_panel.catalog_changed.connect(lambda *_: ai.refresh())
    window.query_panel.targets_changed.connect(lambda *_: ai.refresh())
    window.page_changed.connect(lambda key: ai.refresh() if key == "ai" else None)
    ai.navigate_requested.connect(window.navigate)
    importer.requery_requested.connect(lambda: window.navigate("dashboard"))
    window.query_panel.catalog_changed.connect(dashboard.invalidate)
    window.query_panel.catalog_changed.connect(heatmap.invalidate)
    window.query_panel.catalog_changed.connect(equipment.clear)
    window.query_panel.catalog_changed.connect(lambda *_: list_action.setEnabled(False))
    return window


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auto-close-ms", type=int, default=0, help="실행 검증용 자동 종료 시간")
    parser.add_argument("--demo-excel", action="store_true", help="실행 중인 Excel 대신 합성 통합문서로 가져오기 화면 확인")
    args = parser.parse_args(argv)
    if args.auto_close_ms < 0:
        parser.error("--auto-close-ms는 0 이상이어야 합니다.")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("SETRON")
    install_theme(app)
    # SETRON_DB_* 접속 설정이 있으면 조회 화면은 DB 자료를 읽고 가져오기 저장도 DB로 한다.
    source, problem = dashboard_source_from_env()
    window = create_window(source, excel=DemoExcel() if args.demo_excel else None,
                           import_saver=saver_from_env())
    if problem:
        window.notice.setText(problem)
    if screen := app.primaryScreen():
        area = screen.availableGeometry()
        window.resize(min(1440, area.width() - 32), min(960, area.height() - 48))
    window.show()
    if args.auto_close_ms:
        QTimer.singleShot(args.auto_close_ms, window.close)
    return app.exec()
