"""UI 시작점. 대시보드·히트맵·목록/상세는 같은 관측 공급자를 쓴다.

`SETRON_DB_*` 설정이 없으면 합성 관측·메모리 저장을 쓰고 DB에 연결하지 않는다. 설정이 있으면 조회 화면은 DB 자료를
읽고, 가져오기 저장과 DB 전체 SQL 내보내기도 DB를 쓴다.
데이터 가져오기는 요청 시에만 실행 중인 Excel과 설정된 AI API에 연결한다. 데모 Excel은 예제 응답을 사용한다.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox

from setron.ui.main_window import MainWindow
from setron.ui.dashboard import DashboardPage
from setron.ui.dashboard_data import DashboardSource, family_name, model_name
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.database_source import dashboard_source_from_env, sql_exporter_from_env, data_exporter_from_env
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
from setron.ui.ai_data import FreshAnalysisData, facts_from_snapshot
from setron.ui.ai_page import AIPage
from setron.ui.analysis_data import analysis_hooks, choice_parser
from setron.ui.ai_connection import ai_providers_from_env
from setron.ui.import_demo import DemoExcel, example_extractor
from setron.ui.import_page import ImportPage, saver_from_env
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.release_refresh import capture_release_context, is_database_import_saver
from setron.ui.management_page import ManagementPage
from setron.ui.management_data import gateway_from_env
from setron.ui.management_integration import export_failure, report_filter


def create_window(source: DashboardSource | None = None, *, version_loader=None, item_loader=None,
                  excel=None, extractor=None, import_saver=None, ai_parser=None, ai_analyzer=None,
                  db_exporter=None, sheet_extractor=None, management_gateway=None, data_exporter=None, ai_client=None) -> MainWindow:
    """공통 화면과 명시적으로 전달된 공급자를 연결한다.

    param
    - source: 대시보드 자료. None이면 합성 예시.
    - version_loader: 릴리즈 메모리 공급자 또는 BackgroundRead.
    - item_loader: Item 메모리 공급자 또는 BackgroundRead. 생략 시 릴리즈 공급자의 실행 방식을 따른다.
    - excel: 실행 중 Excel 읽기 객체 또는 기본 읽기 객체를 선택하는 None.
    - extractor: 기존 형식의 추출 공급자. 결과도 원본 대조를 거침.
    - sheet_extractor: SheetData 추출 공급자. 생략하면 설정된 실제 AI를 사용하며 DemoExcel만 예제 응답.
    - import_saver: Import 저장 공급자. None이면 메모리 저장.
    - ai_parser: 질문 해석 공급자. None이면 예제 응답.
    - ai_analyzer: 분석 공급자. None이면 예제 응답.
    - ai_client: 회사 AI 클라이언트. 있으면 질문 해석에 화면 선택지를 함께 보내고, 원인 설명·예측 설명에 쓴다.
    - db_exporter: ``path``를 받아 DB 전체를 .sql로 저장하고 ExportResult를 돌려주는 함수. None이면 내보내기 비활성.
    - data_exporter: 현재 관리값을 읽기 쉬운 Excel로 내보내는 공급자.
    - management_gateway: 워커에서 관리 기록 조회·묶음 저장·XLSX 출력을 실행하는 공급자.

    return
    - 실제 DB 연결을 자동 생성하지 않는 MainWindow.
    """
    demo = source is None
    source = source if source is not None else demo_dashboard_source()
    window = MainWindow(query_catalog=source.catalog)
    if management_gateway is not None:
        window.connection_label.setText("관리 데이터 연결")
        window.connection_detail.setText("검토 후 묶음 저장")
    elif hasattr(source, "refresh"):
        window.connection_label.setText("저장 자료 조회 연결")
        window.connection_detail.setText("조회 요청마다 최신값 확인")
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
        def version_loader():
            """기본 DB 화면은 저장 뒤나 수동 재조회 때 현재 자료를 읽는다."""
            if demo:
                return demo_version_catalog(source)
            if hasattr(source, "load_version_catalog"):
                return source.load_version_catalog()
            if hasattr(source, "refresh"):
                source.refresh()
            return catalog_from_dashboard(source)
        if hasattr(source, "load_version_catalog"):
            version_loader = BackgroundRead(version_loader)
    versions = VersionsPage(version_loader)
    if item_loader is None:
        # 외부 릴리즈 ID를 기본 합성 설비 ID와 임의로 연결하지 않는다.
        item_loader = (lambda: ItemManagerData(version_loader())) if custom_versions else (
            lambda: item_data_from_source(version_loader(), source))
        if not custom_versions and hasattr(source, "load_item_data"):
            item_loader = BackgroundRead(source.load_item_data)
        elif isinstance(version_loader, BackgroundRead):
            item_loader = BackgroundRead(item_loader)
    items = ItemsPage(item_loader)
    # 저장 서비스·회사 AI 연결 전: 각 화면의 기본값은 메모리 저장소와 예제 응답이며 출처를 표시한다.
    def release_refresh_context():
        """GUI 스레드에서 현재 적용 N을 불변 저장 요청용 조건으로 복사한다.

        return
        - DB 모델의 전체 선택 N. 초안만 바뀌었다면 기존 적용 N을 사용하고 자동 변경하지 않음.
        """
        return capture_release_context(source, window.query_state)

    if extractor is None and sheet_extractor is None and isinstance(excel, DemoExcel):
        extractor = example_extractor
    importer = ImportPage(excel, extractor, import_saver, refresh_context_provider=release_refresh_context,
                          sheet_extractor=sheet_extractor)
    window.release_refresh_context = release_refresh_context
    management = ManagementPage(management_gateway, context_provider=release_refresh_context)
    window.management_gateway = management_gateway
    window.management_refresh = ReadRequest(window)
    window.management_refresh_query = None
    window.management_saving = False
    window.management_last_success = False

    def open_management_release(release_id):
        management.open_record("release_version", {"release_version_id": release_id})
        window.navigate("management")

    if management_gateway is not None:
        versions.editor_opener = open_management_release

    def apply_ai_selection(selection):
        window.query_panel.set_draft(selection)
        return window.query_panel.apply_draft()

    def current_facts():
        if management.busy or management.locked or window.management_saving or window.management_refresh.busy:
            return None
        snapshot = dashboard.snapshot
        if snapshot is None or not window.query_state.is_current(snapshot.query):
            return None
        return facts_from_snapshot(snapshot)

    def fresh_analysis_loader():
        query = window.query_state.applied
        if (query is None or not window.query_state.is_current(query) or management.busy
                or management.locked or window.management_saving or window.management_refresh.busy):
            return None
        def load():
            snapshot = source.snapshot(query)
            return FreshAnalysisData(facts_from_snapshot(snapshot), snapshot)
        return BackgroundRead(load)

    def current_snapshot():
        snapshot = dashboard.snapshot
        return snapshot if snapshot is not None and window.query_state.is_current(snapshot.query) else None

    if ai_parser is None and ai_client is not None:
        ai_parser = choice_parser(ai_client, lambda: window.query_state.catalog)
    hooks = analysis_hooks(source, current_snapshot, ai_client)
    if ai_analyzer is not None:
        hooks["explainer"] = None  # 분석 공급자를 직접 넘기면 그 공급자로 분석한다
    ai = AIPage(window.query_state, ai_parser, ai_analyzer, apply_ai_selection, current_facts,
                fresh_loader=fresh_analysis_loader if hasattr(source, "refresh") else None,
                analysis_allowed=lambda: not (management.busy or management.locked or window.management_saving
                                               or window.management_refresh.busy),
                **hooks)
    window.register_page("dashboard", dashboard)
    window.register_page("heatmap", heatmap)
    window.register_page("versions", versions, uses_query=False)
    window.register_page("items", items, uses_query=False)
    window.register_page("import", importer, uses_query=False)
    window.register_page("ai", ai, uses_query=False)
    window.register_page("management", management, uses_query=False)
    window.register_auxiliary_page("equipment", equipment, parent_key="heatmap", title="설비 목록")
    list_action = window.header.actions["equipment_list"]
    list_action.setToolTip("현재 적용 조건의 전체 설비 목록")
    origin_scroll = {}
    overview_notice = window.notice.text()

    def sync_mode(*_):
        # VER5 가져오기 화면은 자체 단계 배너·결과 안내를 쓰므로 공통 안내 배지를 겹쳐 보이지 않는다.
        window.notice.setVisible(window.route != "import" or window.management_saving)
        if window.management_saving:
            window.notice.setText("관리 데이터 저장 중 · 완료 후 관련 화면을 다시 읽습니다.")
            list_action.setEnabled(False)
            return
        if window.route == "items":
            window.header.title.setText("Item 상세" if items.mode == "detail" else "아이템 매니저")
            window.notice.setText("Item 조회 전용 · 자료 출처와 관측 기준일은 본문 참조")
            list_action.setEnabled(False)
        elif window.route == "import":
            window.notice.setText(f"데이터 가져오기 · 실행 중인 Excel 읽기 · {importer.extraction_label} · 저장 위치: {importer.saver.target_label}")
            list_action.setEnabled(False)
        elif window.route == "ai":
            window.notice.setText("AI 분석 · 예제 응답 · 질문으로 DB를 바꾸지 않음")
            list_action.setEnabled(False)
        elif window.route == "versions":
            window.header.title.setText("릴리즈 버전 상세" if versions.mode == "detail" else "릴리즈 버전 목록")
            window.notice.setText("릴리즈 조회 전용 · 자료 출처와 조회 상태는 본문 참조")
            list_action.setEnabled(False)
        elif window.route == "management":
            window.notice.setText("관리 데이터 · 원본 보존 · 검토한 수정 묶음 저장")
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

    export_action = window.header.actions["export"]
    if db_exporter is not None or management_gateway is not None or data_exporter is not None:
        export_action.setEnabled(True)
        export_action.setToolTip("DB 전체 SQL · 관리 자료 XLSX · 현재 조회 현황 XLSX 내보내기")

    def export_sql(path=None):
        """DB 전체를 .sql로 내보낸다. ``path``가 없으면 저장 위치를 묻고 결과를 알림창으로 알린다."""
        interactive = path is None
        if interactive:
            default = Path.home() / f"setron_db_{datetime.now():%Y%m%d_%H%M}.sql"
            path, _ = QFileDialog.getSaveFileName(window, "DB 전체 SQL 내보내기", str(default), "SQL 파일 (*.sql)")
            if not path:
                return None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = db_exporter(path)
        finally:
            QApplication.restoreOverrideCursor()
        text = (f"{result.path} 에 저장했습니다 ({result.message}).\n받은 사람은 빈 DB에서 이 파일을 실행하면 됩니다."
                if result.saved else f"내보내지 못했습니다. {result.message}")
        if interactive:
            (QMessageBox.information if result.saved else QMessageBox.warning)(window, "DB 전체 SQL 내보내기", text)
        return result

    window.export_sql = export_sql
    def export_data(path=None):
        """현재 관리값을 원격 develop의 요약 Excel 형식으로 내보낸다."""
        interactive = path is None
        if interactive:
            default = Path.home() / f"setron_data_{datetime.now():%Y%m%d_%H%M}.xlsx"
            path, _ = QFileDialog.getSaveFileName(window, "관리 데이터 Excel 내보내기", str(default), "Excel 통합문서 (*.xlsx)")
            if not path:
                return None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = data_exporter(path)
        finally:
            QApplication.restoreOverrideCursor()
        if interactive:
            text = (f"{result.path}에 저장했습니다. {result.message}" if result.saved
                    else f"내보내지 못했습니다. {result.message}")
            (QMessageBox.information if result.saved else QMessageBox.warning)(window, "관리 데이터 Excel 내보내기", text)
        return result

    window.export_data = export_data
    window.export_request = ReadRequest(window)
    window.export_title = None

    def management_export(kind, path=None):
        interactive = path is None
        title = "관리 자료 XLSX 내보내기" if kind == "management" else "현재 조회 현황 XLSX 내보내기"
        if interactive:
            filename = f"setron_{kind}_{datetime.now():%Y%m%d_%H%M}.xlsx"
            path, _ = QFileDialog.getSaveFileName(window, title, str(Path.home() / filename), "Excel 파일 (*.xlsx)")
            if not path:
                return None
        path = Path(path)
        def reject(message):
            result = export_failure(path, message)
            if interactive:
                QMessageBox.warning(window, title, message)
            return result
        if management_gateway is None or management.busy or management.locked or window.management_saving:
            return reject("DB 연결과 관리 저장 상태를 확인해 주세요.")
        if window.export_request.busy:
            return reject("다른 내보내기가 진행 중입니다.")
        query = window.query_state.applied
        if kind == "status" and (query is None or not window.query_state.is_current(query)):
            return reject("조회 조건을 먼저 적용해 주세요.")
        context = release_refresh_context()
        def load():
            if kind == "management":
                return management_gateway.export_management(path)
            snapshot = source.snapshot(query)
            return management_gateway.export_status(path, snapshot.base_date, context, report_filter(snapshot, source))
        if not interactive:
            return load()
        window.export_title = title
        export_action.setEnabled(False)
        management.setEnabled(False)
        window.notice.setText(title + " · 저장 중")
        window.export_request.start(BackgroundRead(load))
        return None

    def finish_management_export(result, error):
        export_action.setEnabled(True)
        management.setEnabled(True)
        title, window.export_title = window.export_title, None
        if error is not None or result is None:
            QMessageBox.warning(window, title or "내보내기", "자료를 내보내지 못했습니다. DB 연결과 파일 저장 권한을 확인해 주세요.")
        elif result.saved:
            QMessageBox.information(window, title, f"{result.path}에 저장했습니다.")
        else:
            QMessageBox.warning(window, title, result.message or "자료를 내보내지 못했습니다.")
        sync_mode()

    window.export_request.completed.connect(finish_management_export)
    window.export_management_xlsx = lambda path=None: management_export("management", path)
    window.export_current_xlsx = lambda path=None: management_export("status", path)

    def choose_export():
        if management_gateway is None and db_exporter is None and data_exporter is not None:
            return export_data()
        menu = QMenu(window)
        sql = menu.addAction("DB 전체 SQL")
        sql.setEnabled(db_exporter is not None)
        summary = menu.addAction("관리값 요약 Excel") if data_exporter is not None else None
        managed = menu.addAction("관리 자료 XLSX")
        managed.setEnabled(management_gateway is not None)
        current = menu.addAction("현재 조회 현황 XLSX")
        current.setEnabled(management_gateway is not None and window.query_state.applied_is_current)
        selected = menu.exec(export_action.mapToGlobal(export_action.rect().bottomLeft()))
        if selected is sql:
            export_sql()
        elif summary is not None and selected is summary:
            export_data()
        elif selected is managed:
            window.export_management_xlsx()
        elif selected is current:
            window.export_current_xlsx()

    def open_all(action):
        if action == "export" and (management_gateway is not None or data_exporter is not None):
            choose_export()
            return
        if action == "export" and db_exporter is not None:
            export_sql()
            return
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
    def ensure_loaded(key):
        # 등록 과정에서 취소된 초기 워커 조회는 해당 화면 진입 시 다시 시작한다.
        if key == "versions" and versions.catalog is None and not versions.read_request.busy:
            versions.reload()
        elif key == "items" and items.data is None and not items.read_request.busy:
            items.reload()
    window.page_changed.connect(ensure_loaded)
    window.header.action_requested.connect(open_all)

    def apply_query(query):
        if not heatmap.begin_query(query):
            return
        equipment.clear(f"{getattr(source, 'result_label', '예시 결과')} 계산 중")
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

    def accept_current_snapshot(snapshot):
        query = snapshot.query
        if not window.query_state.is_current(query):
            return False
        if dashboard.accept_result(query, snapshot):
            heatmap.begin_query(query)
            heatmap.accept_result(query, snapshot)
            list_action.setEnabled(equipment.accept_result(query, snapshot))
            return True
        return False

    ai.fresh_data_ready.connect(lambda data: accept_current_snapshot(data.snapshot))

    def cancel_management_refresh():
        """저장 전 관리 재조회와 그 응답을 받을 조회 기준을 함께 폐기한다."""
        window.management_refresh.cancel()
        window.management_refresh_query = None

    def begin_management_save():
        window.management_saving = True
        cancel_management_refresh()
        dashboard.invalidate()
        heatmap.invalidate()
        equipment.clear("관리 데이터 저장 중 · 현재 자료 확인 대기")
        list_action.setEnabled(False)
        versions.setEnabled(False)
        items.setEnabled(False)
        ai.invalidate_data()
        sync_mode()

    def finish_saved_refresh(snapshot, error):
        query = window.management_refresh_query
        if query is None:
            return
        if not window.query_state.is_current(query):
            ai.refresh()
            return
        if error is not None:
            prefix = "저장은 완료됐지만" if window.management_last_success else "기존값을 유지했지만"
            window.notice.setText(prefix + " 화면을 다시 읽지 못했습니다. 조회 적용으로 재시도해 주세요.")
        elif snapshot is not None and query is not None and window.query_state.is_current(query):
            accept_current_snapshot(snapshot)
            prefix = "저장 완료" if window.management_last_success else "저장 실패 · 기존값 유지"
            window.notice.setText(prefix + " · 같은 조회 조건으로 집계·위험 결과를 다시 읽었습니다. 목표 N 선택은 유지했습니다.")
        ai.refresh()

    def refresh_query_catalog(*, reapply=False):
        """저장 뒤 DB의 새 Site·Line을 조회 선택지에 반영한다. 선택지가 같으면 아무것도 바꾸지 않는다.

        선택지가 바뀌면 적용 조건이 무효가 된다. ``reapply``이고 적용 조건·목표 N이 초안과 같으며
        새 선택지에서도 유효하면 같은 조건을 다시 적용하고 True를 돌려준다.
        """
        if not hasattr(source, "reload_catalog"):
            return False
        try:
            catalog = source.reload_catalog()
        except RuntimeError as error:
            window.notice.setText(str(error))
            return False
        if catalog == window.query_state.catalog:
            return False
        state = window.query_state
        applied = state.applied
        same = (applied is not None and state.applied_is_current and state.draft == applied.selection
                and state.target_draft == applied.targets)
        window.query_panel.set_catalog(catalog)
        if not (reapply and same and state.draft == applied.selection):
            return False
        window.query_panel.apply_draft()
        return True

    def after_management_save(result):
        window.management_saving = False
        reapplied = result is not None and refresh_query_catalog(reapply=True)
        window.management_last_success = result is not None
        versions.setEnabled(True)
        items.setEnabled(True)
        versions.refresh_after_save()
        items.refresh_after_save()
        ai.invalidate_data()
        query = window.query_state.applied
        cancel_management_refresh()
        if reapplied:
            # 선택지가 바뀌어 같은 조건을 다시 적용했다. 적용 과정에서 화면을 이미 새로 읽었다.
            window.notice.setText("저장 완료 · 조회 선택지(Site·Line)를 새로 읽고 같은 조회 조건을 다시 적용했습니다.")
        elif query is not None and window.query_state.is_current(query):
            window.management_refresh_query = query
            window.management_refresh.start(BackgroundRead(lambda: source.snapshot(query)))
        else:
            prefix = "저장 완료" if window.management_last_success else "저장 실패 · 기존값 유지"
            window.notice.setText(prefix + " · 조회 조건을 적용해 현재 저장된 현황을 확인해 주세요.")
        judgments = getattr(result, "refresh", None)
        if judgments and any(not target.valid for target in judgments.targets):
            management.notice.setText(management.notice.text() + "\n목표 N의 적용 근거가 바뀌었습니다. 목표 기준에서 재선택해 주세요.")

    def uncertain_management_save():
        window.management_saving = False
        ai.invalidate_data()
        window.notice.setText("저장 결과 미확인 · 같은 수정 묶음을 다시 저장하지 말고 DB 기록을 확인해 주세요.")

    management.save_started.connect(begin_management_save)
    management.saved.connect(after_management_save)
    management.save_uncertain.connect(uncertain_management_save)
    management.save_failed.connect(lambda: after_management_save(None))
    management.request.completed.connect(lambda *_: ai.refresh())
    window.management_refresh.completed.connect(finish_saved_refresh)
    window.after_management_save = after_management_save
    window.close_guard = lambda: management.state != "saving"

    def invalidate_saved_results(receipt):
        """DB 저장 후 이전 판정·대기 중 AI 요청을 폐기하고 N 재확인을 안내한다.

        param
        - receipt: commit 성공 후에만 전달되는 저장 결과와 관련 판정.
        """
        if not is_database_import_saver(importer.saver):
            return
        cancel_management_refresh()
        refresh_query_catalog()
        dashboard.invalidate()
        ai.invalidate_data()
        heatmap.invalidate()
        equipment.clear("저장 자료 변경 · 조회 적용으로 다시 확인해 주세요.")
        list_action.setEnabled(False)
        versions.refresh_after_save()
        items.refresh_after_save()
        judgments = getattr(receipt, "refresh", None)
        invalid = tuple(target for target in judgments.targets if not target.valid) if judgments else ()
        if invalid:
            details = " / ".join(f"{model_name(source, target.model_id)}·계열 {family_name(source, target.family_id)}: "
                                 f"{target.reason}" for target in invalid)
            importer.result_label.setText(importer.result_label.text() + "\n목표 N 재선택·적용 근거 확인 필요: " + details)
        unresolved = judgments.unresolved_risk_ids if judgments else ()
        if unresolved:
            identifiers = ", ".join(str(identifier) for identifier in unresolved)
            importer.result_label.setText(importer.result_label.text() +
                f"\n수동 위험 지정 {len(unresolved)}건이 미확인으로 유지되었습니다 (지정 번호: {identifiers}). "
                "대상 버전·Item과 적용 범위를 확인해 주세요. 위험이 해제된 상태가 아닙니다.")
        window.notice.setText("DB 저장 완료 · 기존 조회 결과 갱신 필요 · 목표 N 선택은 유지했습니다.")

    importer.saved.connect(invalidate_saved_results)
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
    ai = ai_providers_from_env()
    app.aboutToQuit.connect(ai.close)
    window = create_window(source, excel=DemoExcel() if args.demo_excel else None,
                           import_saver=saver_from_env(), db_exporter=sql_exporter_from_env(),
                           management_gateway=gateway_from_env() if source is not None else None,
                           data_exporter=data_exporter_from_env(), extractor=ai.extractor,
                           ai_client=ai.client)
    if problem or ai.problem:
        window.notice.setText(" / ".join(p for p in (problem, ai.problem) if p))
    if screen := app.primaryScreen():
        area = screen.availableGeometry()
        window.resize(min(1440, area.width() - 32), min(960, area.height() - 48))
    window.show()
    if args.auto_close_ms:
        QTimer.singleShot(args.auto_close_ms, window.close)
    return app.exec()
