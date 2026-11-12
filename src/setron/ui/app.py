"""UI 시작점. 대시보드·히트맵·목록/상세는 같은 관측 공급자를 쓴다.

`SETRON_DB_*` 설정이 없으면 합성 관측·메모리 저장을 쓰고 DB에 연결하지 않는다. 설정이 있으면 조회 화면은 DB 자료를
읽고, 가져오기 저장과 DB 전체 SQL 내보내기도 DB를 쓴다.
데이터 가져오기는 요청 시에만 실행 중인 Excel과 설정된 AI API에 연결한다. 데모 Excel은 예제 응답을 사용한다.
"""

import argparse
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox

from setron.ui.main_window import MainWindow
from setron.ui.dashboard import DashboardPage
from setron.ui.dashboard_data import DashboardDrilldown, DashboardSource, family_name, model_name
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.database_source import (
    dashboard_source_from_env, data_exporter_from_env, export_files_from_env, sql_exporter_from_env, sql_importer_from_env,
)
from setron.ui.heatmap import HeatmapPage
from setron.ui.heatmap_data import EquipmentSelection
from setron.ui.equipment import EquipmentPage
from setron.ui.equipment_drawer import EquipmentDrawer
from setron.ui.equipment_list_drawer import EquipmentListDrawer
from setron.ui.target_data import describe_targets, initial_targets, target_pairs, validate_targets
from setron.ui.query_state import QuerySelection, TargetN, single_selection
from setron.ui.group_dialog import VersionGroupDialog
from setron.ui.target_dialog import TargetDialog
from setron.ui.version_filter import version_options
from setron.ui.version_filter_dialog import VersionFilterDialog
from setron.ui.theme import icon, install_theme
from setron.ui.version_data import catalog_from_dashboard
from setron.ui.version_demo import demo_version_catalog
from setron.ui.version_management import VersionManagementPage
from setron.ui.item_data import ItemManagerData, item_data_from_source
from setron.ui.items import ItemsPage
from setron.ui.ai_data import FreshAnalysisData, facts_from_snapshot
from setron.ui.ai_page import AIPage
from setron.ui.analysis_data import analysis_hooks, choice_parser, snapshot_targets
from setron.ui.ai_connection import ai_providers_from_env
from setron.ui.import_demo import DemoExcel, example_extractor
from setron.ui.chat_data import ChatService
from setron.ui.chat_page import ChatPage
from setron.ui.import_delete_gateway import ImportDeleteGateway
from setron.ui.import_extraction import rule_extractor
from setron.ui.import_page import ImportPage, saver_from_env
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.release_refresh import capture_release_context, is_database_import_saver
from setron.ui.replay_page import ReplayPage
from setron.ui.reports_page import ReportsPage
from setron.ui.risk_rule_save import RiskRuleSaveController
from setron.ui.data_management_page import DataManagementPage
from setron.ui.data_export_page import DataExportPage
from setron.ui.management_data import gateway_from_env
from setron.ui.management_integration import export_failure, report_filter, save_numbered_export
from setron.ui.settings_page import SettingsPage
from setron.ui.import_dialogs import ImportProgressDialog, confirm_numbered_save


def create_window(source: DashboardSource | None = None, *, version_loader=None, item_loader=None,
                  excel=None, extractor=None, import_saver=None, ai_parser=None, ai_analyzer=None,
                  db_exporter=None, sheet_extractor=None, management_gateway=None, data_exporter=None, ai_client=None,
                  rule_extractor=None, chat_enabled: bool = False, import_comparison_loader=None,
                  import_delete_gateway=None, sql_importer=None, export_file_loader=None) -> MainWindow:
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
    - import_comparison_loader: 확인된 동일 범위의 직전 저장본 비교를 제공하는 선택적 읽기 공급자.
    - import_delete_gateway: 삭제 목록·원자적 삭제를 제공하는 선택적 서비스. 기본 미연결이며 UI에서 SQL을 실행하지 않음.
    - sql_importer: SETRON SQL 파일의 병합 미리보기·저장을 제공하는 선택적 서비스.
    - export_file_loader: 현재 관리값과 연결된 가져오기 파일 목록 공급자.

    return
    - 실제 DB 연결을 자동 생성하지 않는 MainWindow.
    """
    demo = source is None
    source = source if source is not None else demo_dashboard_source()
    window = MainWindow(query_catalog=source.catalog)
    window.notice.setText("작업 단위 7 · 목표 N 편집 · 합성 예시 데이터 · DB 미연결 · 임시 선택" if demo
                          else f"{getattr(source, 'notice', source.source_label)} · 목표 N 임시 선택")
    window.query_panel.set_target_describer(lambda targets: describe_targets(source, targets))
    window.query_panel.set_target_draft(initial_targets(source))
    window.query_panel.goal_button.setEnabled(True)
    window.query_panel.version_button.setEnabled(True)
    window.version_filter_dialog = None

    def open_version_filter():
        if window.version_filter_dialog is not None and window.version_filter_dialog.isVisible():
            window.version_filter_dialog.raise_()
            window.version_filter_dialog.activateWindow()
            return
        state = window.query_state
        original, revision = state.draft, state.catalog_revision
        options = version_options(source)
        dialog = VersionFilterDialog(options, original.versions,
                                     is_current=lambda: (state.catalog_revision == revision and state.draft == original
                                                         and version_options(source) == options), parent=window)
        window.version_filter_dialog = dialog

        def finish(result):
            if result == dialog.DialogCode.Accepted:
                window.query_panel.set_version_filter(dialog.result_filter)
            if window.version_filter_dialog is dialog:
                window.version_filter_dialog = None
            dialog.deleteLater()

        dialog.finished.connect(finish)
        dialog.open()

    window.query_panel.version_button.clicked.connect(open_version_filter)
    window.query_panel.goal_button.setToolTip("모델·계열별 기준 버전을 설정합니다. 저장 후 조회 버튼을 눌러 반영합니다.")
    window.target_dialog = None

    def open_targets():
        if window.target_dialog is not None and window.target_dialog.isVisible():
            window.target_dialog.raise_()
            window.target_dialog.activateWindow()
            return
        state = window.query_state
        generation = state.catalog_revision
        current = lambda: state.catalog_revision == generation and state.catalog.is_view_of(source.catalog)
        if hasattr(source, "align_targets"):
            # DB 공급자는 조회 때마다 다시 읽으므로 새로 저장된 모델·계열을 목표 초안에 맞춘다.
            try:
                source.refresh()
                window.query_panel.set_target_draft(source.align_targets(state.target_draft))
            except RuntimeError as error:
                window.notice.setText(str(error))
                return
        dialog = TargetDialog(source, state.target_draft, group_key=single_selection(state.draft.group_key),
                              is_current=current, parent=window)
        window.target_dialog = dialog

        def finish(result):
            if result == TargetDialog.DialogCode.Accepted and current() and dialog.result_targets is not None:
                window.query_panel.set_target_draft(dialog.result_targets)
            if window.target_dialog is dialog:
                window.target_dialog = None
            dialog.deleteLater()

        dialog.finished.connect(finish)
        dialog.open()

    window.open_single_targets = open_targets  # 목표 N 하나 임시 선택(기존 편집). N 그룹 저장 전 기본 목표에 쓴다.
    window.group_dialog = None
    last_group_pair = None

    def open_groups():
        if window.group_dialog is not None and window.group_dialog.isVisible():
            window.group_dialog.raise_()
            window.group_dialog.activateWindow()
            return
        state = window.query_state
        if hasattr(source, "refresh"):
            try:
                source.refresh()  # DB 공급자는 최신 릴리즈·저장 그룹으로 연다.
            except RuntimeError as error:
                window.notice.setText(str(error))
                return
        remembered_pair = last_group_pair or settings.store.version_group_pair()
        dialog = VersionGroupDialog(source, targets=state.target_draft, group_key=single_selection(state.draft.group_key),
                                    pair_key=remembered_pair, parent=window)
        window.group_dialog = dialog
        initial_pair = dialog.pair.key if dialog.pair is not None else None

        def finish(result):
            nonlocal last_group_pair
            if window.group_dialog is dialog:
                window.group_dialog = None
                selected = dialog.pair.key if dialog.pair is not None else None
                if selected is not None and selected != remembered_pair and (selected != initial_pair or dialog.saved_any):
                    last_group_pair = selected
                    try:
                        settings.store.remember_version_group_pair(selected)
                    except OSError:
                        pass  # 개인 설정을 저장하지 못해도 열린 창에서는 선택을 유지한다.
            dialog.deleteLater()

        dialog.finished.connect(finish)
        dialog.open()

    window.query_panel.goal_button.clicked.connect(open_groups)
    window.query_panel.catalog_changed.connect(
        lambda catalog: window.query_panel.goal_button.setEnabled(catalog.is_view_of(source.catalog))
    )
    dashboard = DashboardPage(window.query_state, source)
    window.query_panel.reset_button.clicked.connect(dashboard.reset_trend_period)

    def sync_dashboard_date_mode(*_):
        if window.route == "dashboard" and dashboard.tabs.currentIndex() == 3 and dashboard.snapshot is not None:
            start, end = dashboard.trend_start.date().toPython(), dashboard.trend_end.date().toPython()
            if start <= end:
                window.query_panel.enable_date_range(start, end)
        else:
            window.query_panel.disable_date_range()

    window.query_panel.date_range_selected.connect(dashboard.set_trend_period)
    dashboard.trend_period_changed.connect(sync_dashboard_date_mode)
    dashboard.tabs.currentChanged.connect(sync_dashboard_date_mode)
    window.page_changed.connect(sync_dashboard_date_mode)
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
    versions = VersionManagementPage(version_loader, source, targets_provider=lambda: (
        window.query_state.applied.targets if window.query_state.applied is not None else initial_targets(source)))
    if item_loader is None:
        # 외부 릴리즈 ID를 기본 합성 설비 ID와 임의로 연결하지 않는다.
        item_loader = (lambda: ItemManagerData(version_loader())) if custom_versions else (
            lambda: item_data_from_source(version_loader(), source))
        if not custom_versions and hasattr(source, "load_item_data"):
            item_loader = BackgroundRead(source.load_item_data)
        elif isinstance(version_loader, BackgroundRead):
            item_loader = BackgroundRead(item_loader)
    items = ItemsPage(item_loader, ai_client=ai_client)
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
                          sheet_extractor=sheet_extractor, rule_extractor=rule_extractor,
                          comparison_loader=import_comparison_loader, delete_gateway=import_delete_gateway,
                          sql_importer=sql_importer)
    def configure_import_choices():
        importer.configure_choices(dates=source.dates, sites=(s.label for s in source.catalog.sites),
                                   families=(family_name(source, key) for key in source.families))
    configure_import_choices()
    window.release_refresh_context = release_refresh_context
    def replay_targets():
        applied = window.query_state.applied
        return (applied.targets if applied is not None and applied.targets is not None else
                initial_targets(source))
    replay = ReplayPage(source, targets_provider=replay_targets, drawer_parent=window.centralWidget())
    replay.query_panel.goal_button.setEnabled(True)
    replay.query_panel.goal_button.setToolTip(window.query_panel.goal_button.toolTip())
    replay.query_panel.goal_button.clicked.connect(open_groups)
    risk_save = RiskRuleSaveController(management_gateway, context_provider=release_refresh_context, parent=window)
    # 데이터 관리 (11-10 개편): 설비현황 · 릴리즈노트 · 변경 이력. 고치기·더하기·지우기는 바로 DB에 반영하고
    # 같은 관리 저장 흐름(after_management_save)으로 조회 선택지와 화면을 다시 읽는다. 위험 지정은 아이템 매니저에서만 한다 (11-11).
    data_page = DataManagementPage(management_gateway, context_provider=release_refresh_context,
                                   drawer_parent=window.centralWidget())  # 변경 이력 세부 정보는 대시보드처럼 창 오른쪽에서
    # 데이터 가져오기의 '저장된 데이터 삭제'는 데이터 관리 › 설비현황 › 가져온 엑셀 삭제로 옮긴다 (같은 객체·연결 유지).
    data_page.attach_deletion(importer.deletion)
    importer.tabs.setTabVisible(1, False)
    versions.configure(management_gateway, release_refresh_context,
                       edit_allowed=lambda: not risk_save.busy and not risk_save.locked)
    versions.navigate_requested.connect(window.navigate)
    def import_from_version(kind):
        importer.request_kind(kind)      # 자료 종류 판단은 가져오기 화면이 한다 (앱 조립 파일은 업무 코드를 직접 부르지 않음)
        window.navigate("import")
    versions.import_requested.connect(import_from_version)
    item_risk_save_pending = False
    def submit_item_risk(values):
        nonlocal item_risk_save_pending
        if risk_save.busy or risk_save.locked or window.management_saving or window.management_refresh.busy:
            return False
        item_risk_save_pending = True
        started = risk_save.create_risk_rule(values)
        if not started:
            item_risk_save_pending = False
        return started
    items.manager.configure(management_gateway, release_refresh_context, submit_item_risk)
    window.management_gateway = management_gateway
    window.management_refresh = ReadRequest(window)
    catalog_refresh = ReadRequest(window)
    window.management_refresh_query = None
    window.management_saving = False
    window.management_last_success = False
    window.management_progress = ImportProgressDialog(window)

    def apply_ai_selection(selection):
        window.query_panel.set_draft(selection)
        return window.query_panel.apply_draft()

    def current_facts():
        if risk_save.busy or risk_save.locked or window.management_saving or window.management_refresh.busy:
            return None
        snapshot = dashboard.snapshot
        if snapshot is None or not window.query_state.is_current(snapshot.query):
            return None
        return facts_from_snapshot(snapshot)

    def fresh_analysis_loader():
        query = window.query_state.applied
        if (query is None or not window.query_state.is_current(query) or risk_save.busy
                or risk_save.locked or window.management_saving or window.management_refresh.busy):
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
    analysis_allowed = lambda: not (risk_save.busy or risk_save.locked or window.management_saving  # noqa: E731
                                     or window.management_refresh.busy or importer.state == "saving"
                                     or importer.deletion.phase == "deleting")
    if chat_enabled:
        # 대화형 AI 분석(VER4 10-1~10-5). 대시보드와 같은 자료·목표 N·저장한 N 그룹으로 답하고 조건은 바꾸지 않는다.
        def chat_targets():
            snapshot = dashboard.snapshot
            return snapshot_targets(snapshot) if snapshot is not None else dict(getattr(source, "targets", {}))
        ai = ChatPage(ChatService(source, chat_targets, ai_client), analysis_allowed=analysis_allowed)
    else:
        ai = AIPage(window.query_state, ai_parser, ai_analyzer, apply_ai_selection, current_facts,
                    fresh_loader=fresh_analysis_loader if hasattr(source, "refresh") else None,
                    analysis_allowed=analysis_allowed, **hooks)
    window.register_page("dashboard", dashboard)
    window.register_page("heatmap", heatmap)
    window.register_page("versions", versions, uses_query=False)
    window.register_page("data", data_page, uses_query=False)
    window.register_page("items", items, uses_query=False)
    window.register_page("import", importer, uses_query=False)
    window.register_page("replay", replay, uses_query=False)
    window.register_page("ai", ai, uses_query=False)
    def latest_report_answer():
        # 완료된 답만 복사한다. 보고서 생성에서 AI 요청이나 기존 초기 화면을 변경하지 않는다.
        return next((card.answer for card in reversed(getattr(ai, "cards", ()))
                     if not card.is_stale and not card.answer.issues and card.answer.request.understood), None)
    reports = ReportsPage(source, targets_provider=replay_targets, version_loader=version_loader,
                          ai_provider=latest_report_answer, generation_allowed=analysis_allowed)
    if chat_enabled:
        ai.report_saved.connect(reports.add)
    window.register_page("reports", reports, uses_query=False)
    window.page_changed.connect(lambda key: reports.enter() if key == "reports" else None)
    settings = SettingsPage(scroll_area=window.scroll, preview_background=window.canvas.set_preview_background)
    window.register_page("settings", settings, uses_query=False)
    window.page_changed.connect(lambda key: settings.enter() if key == "settings" else settings.leave())

    def navigate_item_answer(route, answer):
        if answer is None or answer is not items.answer or answer.data is not items.data:
            return False
        if route == "ai":
            name = next((p.family_name for p in answer.data.versions.pairs if p.family_id == answer.family_id), str(answer.family_id))
            draft = f"{name} · {answer.question}\n관련 Item: " + ", ".join(str(i) for i in answer.item_ids)
            if isinstance(ai, ChatPage):
                if not ai.prefill(draft):
                    items.answer_card.navigation_note.setText("AI 분석에서 진행 중인 질문을 완료한 뒤 다시 이동해 주세요.")
                    return False
            else:
                ai._prefill(draft)
            return window.navigate("ai")
        if route not in {"dashboard", "heatmap"}:
            return False
        try:
            family = source.families.get(answer.family_id)
            first = next((r for r in answer.versions if r.version_order is not None), None)
            if family is None or first is None or family.get(first.release_version_id) != first:
                raise ValueError("현재 조회 자료에서 포함 기준 버전을 확인할 수 없습니다. 다시 불러와 주세요.")
            pairs = tuple(p for p in target_pairs(source) if p.family_id == answer.family_id)
            if not pairs:
                raise ValueError("이 계열에 연결된 조회 대상 모델이 없습니다.")
            if any(p.key in getattr(source, "saved_groups", {}) for p in pairs):
                raise ValueError("저장된 N 그룹이 우선 적용 중입니다. 기준 버전 설정에서 그룹을 확인한 뒤 이동해 주세요.")
            for pair in pairs:
                if not any(o.release.release_version_id == first.release_version_id and o.unavailable_reason is None for o in pair.options):
                    raise ValueError("모델별 포함 기준 버전의 적용 근거·순서를 먼저 확인해 주세요.")
            applied = window.query_state.applied
            selected = applied.targets if window.query_state.applied_is_current and applied.targets is not None else initial_targets(source)
            targets = validate_targets(source, tuple(TargetN(t.model_id, t.family_id, first.release_version_id)
                if t.family_id == answer.family_id else t for t in selected))
            groups = {p.group_key for p in pairs}
            selection = QuerySelection(group_key=next(iter(groups)) if len(groups) == 1 else None,
                                       as_of=answer.data.base_date, item_family_id=answer.family_id)
            window.query_state.validate(selection)
        except ValueError as error:
            items.answer_card.navigation_note.setText(str(error))
            return False
        window.query_panel.set_target_draft(targets)
        window.query_panel.set_draft(selection)
        window.query_panel.apply_draft()
        return window.navigate(route)

    items.answer_navigation.connect(navigate_item_answer)
    window.navigate_item_answer = navigate_item_answer

    def show_item_risk_candidates(title, equipment_ids):
        """아이템 매니저의 위험 기준 판정 결과를 대시보드 위험 후보 목록으로 연다.

        대시보드 위험 후보는 현재 조회 조건과 활성 위험 기준 전체로 판정하므로, 두 결과에 모두 있는 설비만 연다.
        조회 미적용·범위 밖이면 이동하지 않고 이유를 안내한다(0대로 확정하지 않는다).
        """
        snapshot = dashboard.snapshot
        if snapshot is None or not window.query_state.is_current(snapshot.query):
            items.show_notice("대시보드 조회 조건을 먼저 적용해 주세요. 적용한 조회 결과의 위험 후보에 등록한 기준이 반영됩니다.")
            return False
        wanted = set(equipment_ids)
        shown = tuple(i for i in snapshot.risky_ids if i in wanted)
        if not shown:
            items.show_notice(f"현재 대시보드 조회 범위(기준일 {snapshot.base_date})의 위험 후보에는 이 기준의 위험 후보 설비가 없습니다. "
                              "Site·설비군·기준일 조건을 확인해 주세요.")
            return False
        window.navigate("dashboard")
        dashboard.drilldown_requested.emit(DashboardDrilldown(snapshot.query, snapshot.base_date, title, shown, True))
        return True

    items.risk_candidates_requested.connect(show_item_risk_candidates)
    window.show_item_risk_candidates = show_item_risk_candidates
    window.register_auxiliary_page("equipment", equipment, parent_key="heatmap", title="설비 목록")
    drawer = window.equipment_drawer = EquipmentDrawer(window.centralWidget())
    list_drawer = window.equipment_list_drawer = EquipmentListDrawer(window.centralWidget())

    def close_drawer(*_):
        drawer.close_panel(restore_focus=False)

    def close_inactive_drawers(key):
        if key != "heatmap":
            close_drawer()
        if key != "dashboard":
            list_drawer.close_panel(restore_focus=False)

    window.page_changed.connect(close_inactive_drawers)
    equipment.mode_changed.connect(lambda mode: close_drawer() if mode != "detail" else None)
    list_action = window.header.actions["equipment_list"]
    list_action.setToolTip("현재 적용 조건의 전체 설비 목록")
    origin_scroll = {}
    overview_notice = window.notice.text()

    def sync_back():
        action = None
        if window.route == "dashboard" and list_drawer.is_open:
            action = equipment.return_to_list if list_drawer.request is not None else list_drawer.close_panel
        elif window.route == "heatmap" and drawer.is_open:
            action = drawer.close_panel
        elif window.route == "replay" and replay.drawer.is_open:
            action = replay.drawer.close_panel
        elif window.route == "equipment" and equipment.mode == "detail":
            action = equipment.back_button.click
        elif window.route == "items" and items.mode == "detail":
            action = items.back_to_list
        elif window.route == "versions" and versions.active_tab == 1 and versions.mode == "detail":
            action = versions.back_to_list
        # 아이템 매니저는 헤더의 ← 버튼을 쓰지 않는다(상세는 같은 행을 다시 눌러 닫는다). 뒤로가기 단축키는 유지한다.
        window.set_page_back(action, subpage=action is not None and window.route not in ("dashboard", "heatmap", "replay", "items"))

    def sync_mode(*_):
        sync_back()
        # 가져오기 화면은 자체 단계 배너를 쓰고, 대시보드·히트맵은 화면 단순화를 위해 공통 안내 배지를 숨긴다.
        quiet = ("import", "export", "dashboard", "heatmap", "items", "replay", "versions", "data", "reports", "settings") + (("ai",) if chat_enabled else ())
        showing_equipment_result = (window.route == "equipment" and equipment.snapshot is not None
                                    and equipment.item_request is None)
        window.notice.setVisible(window.management_saving or (window.route not in quiet and not showing_equipment_result))
        # 데이터 가져오기 화면도 머리 줄 '설비 목록' 버튼을 숨긴다 (11-10 2차 3-1).
        window.header.toolbar.setVisible(window.route not in {"import", "export", "items", "replay", "versions", "data", "reports", "settings"})
        list_action.setVisible(window.route not in ("dashboard", "heatmap", "ai"))
        if window.management_saving:
            window.notice.setText("관리 데이터 저장 중 · 완료 후 관련 화면을 다시 읽습니다.")
            list_action.setEnabled(False)
            return
        if window.route == "items":
            window.header.title.setText("아이템 매니저")
            window.notice.setText("Item 조회 전용 · 자료 출처와 관측 기준일은 본문 참조")
            list_action.setEnabled(False)
        elif window.route == "import":
            window.notice.setText(f"데이터 가져오기 · 실행 중인 Excel 읽기 · {importer.extraction_label} · 저장 위치: {importer.saver.target_label}")
            list_action.setEnabled(False)
        elif window.route in ("replay", "export"):
            list_action.setEnabled(False)
        elif window.route == "ai":
            window.header.title.setText("AI 분석")
            window.notice.setText(getattr(ai, "notice_text", "AI 분석 · 예제 응답 · 질문으로 DB를 바꾸지 않음"))
            list_action.setEnabled(False)
        elif window.route == "reports":
            list_action.setEnabled(False)
        elif window.route == "data":
            window.header.title.setText("데이터 관리")
            list_action.setEnabled(False)
        elif window.route == "versions":
            window.header.title.setText("버전 관리")
            window.notice.setText("버전 관리 · 원본 보존 · 자료 출처와 조회 상태는 본문 참조")
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
        if window.route != "dashboard" or not equipment.open_scope(request, origin="dashboard"):
            return
        focus = QApplication.focusWidget()
        list_drawer.open_list(equipment, return_focus=focus if focus and dashboard.isAncestorOf(focus) else None)
        sync_back()

    def equipment_mode_changed(mode):
        if mode == "list":
            if equipment.snapshot is None:
                list_drawer.close_panel(restore_focus=False)
            elif list_drawer.is_open and list_drawer.request is not None:
                list_drawer.show_list()
        elif window.route == "dashboard" and list_drawer.is_open and equipment.detail_back == "list":
            detail = next(d for d in equipment.data.details if d.record.equipment_id == equipment.selected_id)
            snapshot = equipment.snapshot
            request = EquipmentSelection(snapshot.query, snapshot.base_date, detail.record.equipment_id, detail.record.base_date)
            list_drawer.show_detail(request, detail, equipment.detail_model.rows)

    def open_detail(request):
        if window.route != "heatmap":
            return
        if equipment.open_detail(request, origin="heatmap"):
            detail = next(d for d in equipment.data.details if d.record.equipment_id == request.equipment_id)
            drawer.open_detail(request, detail, equipment.detail_model.rows,
                               return_focus=heatmap.tile_buttons.get(request.equipment_id))
            sync_back()

    export_action = window.header.actions["export"]
    if db_exporter is not None or management_gateway is not None or data_exporter is not None:
        export_action.setEnabled(True)
        export_action.setToolTip("DB 전체 SQL · 관리 자료 XLSX · 현재 조회 현황 XLSX 내보내기")

    def run_numbered_export(exporter, path, suffix, *, interactive):
        def write(destination):
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                return exporter(destination)
            finally:
                QApplication.restoreOverrideCursor()

        confirm = (lambda requested: confirm_numbered_save(window, requested)) if interactive else None
        return save_numbered_export(write, path, suffix, confirm_duplicate=confirm)

    def export_sql(path=None):
        """DB 전체를 .sql로 내보낸다. ``path``가 없으면 저장 위치를 묻고 결과를 알림창으로 알린다."""
        interactive = path is None
        if interactive:
            default = Path.home() / f"setron_db_{datetime.now():%Y%m%d_%H%M}.sql"
            path, _ = QFileDialog.getSaveFileName(window, "DB 전체 SQL 내보내기", str(default), "SQL 파일 (*.sql)",
                                                  options=QFileDialog.Option.DontConfirmOverwrite)
            if not path:
                return None
        result = run_numbered_export(db_exporter, path, ".sql", interactive=interactive)
        if result is None:
            return None
        text = (f"{result.path} 에 저장했습니다 ({result.message}).\n받은 사람은 빈 DB에서 이 파일을 실행하면 됩니다."
                if result.saved else f"내보내지 못했습니다. {result.message}")
        if interactive:
            (QMessageBox.information if result.saved else QMessageBox.warning)(window, "DB 전체 SQL 내보내기", text)
        return result

    window.export_sql = export_sql
    importer.deletion.setProperty("backupAvailable", db_exporter is not None)
    importer.deletion.backup_button.setEnabled(db_exporter is not None)
    if db_exporter is not None:
        importer.deletion.backup_requested.connect(export_sql)
    def export_data(path=None):
        """현재 관리값을 선택한 생산 영역의 요약 Excel 형식으로 내보낸다."""
        interactive = path is None
        scope = export_page.production_area.currentData() if window.route == "export" else None
        batch_ids = export_page.selected_batch_ids() if window.route == "export" else None
        if batch_ids == () or batch_ids is not None and window.export_files_request.busy:
            result = export_failure(Path(path) if path is not None else Path("setron_data.xlsx"),
                                    "파일 목록을 확인하고 내보낼 파일을 하나 이상 선택해 주세요.")
            if interactive:
                QMessageBox.warning(window, "관리 데이터 Excel 내보내기", result.message)
            return result
        if interactive:
            default = Path.home() / f"setron_data_{datetime.now():%Y%m%d_%H%M}.xlsx"
            path, _ = QFileDialog.getSaveFileName(window, "관리 데이터 Excel 내보내기", str(default),
                                                  "Excel 통합문서 (*.xlsx)",
                                                  options=QFileDialog.Option.DontConfirmOverwrite)
            if not path:
                return None
        options = {}
        if scope is not None:
            options["production_area"] = scope
        if batch_ids is not None:
            options["batch_ids"] = batch_ids
        exporter = data_exporter if not options else lambda destination: data_exporter(destination, **options)
        result = run_numbered_export(exporter, path, ".xlsx", interactive=interactive)
        if result is None:
            return None
        if interactive:
            text = (f"{result.path}에 저장했습니다. {result.message}" if result.saved
                    else f"내보내지 못했습니다. {result.message}")
            (QMessageBox.information if result.saved else QMessageBox.warning)(window, "관리 데이터 Excel 내보내기", text)
        return result

    window.export_data = export_data
    window.export_request = ReadRequest(window)
    window.export_title = None
    export_page = DataExportPage()
    export_page.set_file_support(export_file_loader is not None)
    window.register_page("export", export_page, uses_query=False)
    window.export_files_request = ReadRequest(window)

    def load_export_files():
        if export_file_loader is None or window.export_files_request.busy or window.management_saving:
            return
        export_page.set_files_loading()
        window.export_files_request.start(export_file_loader)

    def finish_export_files(files, error):
        export_page.set_files(tuple(files or ()), error=error is not None)

    window.export_files_request.completed.connect(finish_export_files)
    export_page.files_requested.connect(load_export_files)
    window.page_changed.connect(lambda route: load_export_files() if route == "export" and
                                export_page.selected_files_button.isChecked() else None)

    def sync_export_page(*_):
        if window.route != "export":
            return
        busy = ""
        if window.export_request.busy:
            busy = "내보내기 진행 중입니다."
        elif window.management_saving:
            busy = "관리 데이터 저장 완료 후 이용할 수 있습니다."
        export_page.set_availability({
            "sql": "DB 연결이 필요합니다." if db_exporter is None else "",
            "summary": "DB 연결이 필요합니다." if data_exporter is None else "",
        }, busy=busy)

    window.page_changed.connect(sync_export_page)

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
        if management_gateway is None or risk_save.busy or risk_save.locked or window.management_saving:
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
        window.notice.setText(title + " · 저장 중")
        window.export_request.start(BackgroundRead(load))
        sync_export_page()
        return None

    def finish_management_export(result, error):
        export_action.setEnabled(True)
        title, window.export_title = window.export_title, None
        sync_export_page()
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
    export_page.buttons["sql"].clicked.connect(lambda: export_sql())
    export_page.buttons["summary"].clicked.connect(lambda: export_data())

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
        restored_by_history = window.go_back_route(origin)
        if restored_by_history or window.navigate(origin, record_history=False):
            if origin == "items":
                focus = items.distribution.tiles.get(items.selected_equipment_id) if items.distribution_view.currentData() == "heatmap" else items.equipment_table
                if focus is not None:
                    focus.setFocus(Qt.FocusReason.OtherFocusReason)
            if restored_by_history:
                return
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
                if not window.go_back_route("items"):
                    window.navigate("items", record_history=False)

    def leave_item_detail(*_):
        if window.route != "equipment" and equipment.item_request is not None:
            equipment.close_item_detail()

    items.equipment_selected.connect(open_item_equipment)
    items.selection_invalidated.connect(invalidate_item_detail)
    window.page_changed.connect(leave_item_detail)

    dashboard.drilldown_requested.connect(open_scope)
    heatmap.equipment_selected.connect(open_detail)
    list_drawer.back_requested.connect(equipment.return_to_list)
    list_drawer.closed.connect(sync_back)
    drawer.closed.connect(sync_back)
    replay.drawer.closed.connect(sync_back)
    replay.drawer_opened.connect(sync_back)
    equipment.back_requested.connect(go_back)
    equipment.mode_changed.connect(equipment_mode_changed)
    equipment.mode_changed.connect(sync_mode)
    versions.mode_changed.connect(sync_mode)
    items.mode_changed.connect(sync_mode)
    window.page_changed.connect(sync_mode)
    sync_mode()  # 첫 화면(대시보드)에도 안내 배지 표시 규칙을 바로 적용한다.
    def ensure_loaded(key):
        # 등록 과정에서 취소된 초기 워커 조회는 해당 화면 진입 시 다시 시작한다.
        if key == "versions" and versions.catalog is None and not versions.read_request.busy:
            versions.reload()
        elif key == "items" and items.data is None and not items.read_request.busy:
            items.reload()
        elif key == "replay":
            replay.enter()
        elif key == "data":
            data_page.enter()
    window.page_changed.connect(ensure_loaded)
    window.header.action_requested.connect(open_all)

    defer_query_apply = False

    def apply_query(query):
        if defer_query_apply:
            return
        if not heatmap.begin_query(query):
            return
        equipment.clear(f"{getattr(source, 'result_label', '예시 결과')} 계산 중")
        list_action.setEnabled(False)
        if dashboard.apply_query(query):
            # 한 번 계산한 같은 불변 결과를 두 화면에서 공유한다.
            heatmap.accept_result(query, dashboard.snapshot)
            list_action.setEnabled(equipment.accept_result(query, dashboard.snapshot))
        else:
            heatmap.fail_query(query, getattr(dashboard, "failure_reason", None))
            equipment.clear("설비 결과 표시 실패 · 다시 적용해 주세요")

    window.query_applied.connect(apply_query)
    # 대시보드 계산 뒤에 연결해야 AI 화면이 같은 적용 조건의 결과를 읽는다.
    window.query_applied.connect(lambda *_: ai.refresh())
    window.query_panel.catalog_changed.connect(lambda *_: ai.refresh())
    window.query_panel.targets_changed.connect(lambda *_: ai.refresh())
    window.page_changed.connect(lambda key: ai.refresh() if key == "ai" else None)
    def ai_navigate(route):
        # AI 분석의 이동 버튼. "reports:<종류>:<시작>:<끝>"이면 보고서 메뉴를 열고 종류·기간을 채운다 (11/11).
        key, _, rest = str(route).partition(":")
        window.navigate(key)
        if key == "reports" and rest:
            kind, start, end = (rest.split(":") + ["", ""])[:3]
            try:
                reports.open_period(kind, date.fromisoformat(start), date.fromisoformat(end))
            except ValueError:
                pass
    ai.navigate_requested.connect(ai_navigate)
    importer.requery_requested.connect(lambda: window.navigate("dashboard"))

    def show_import_result(day, site, route):
        key = next((o.key for o in window.query_state.catalog.sites if o.label == site), None)
        if key is None:
            importer.result_label.setText(f"{site} 사업장 조회 조건을 불러오지 못했습니다. DB 연결을 확인해 주세요.")
            return
        window.query_panel.set_draft(QuerySelection(as_of=day, site_key=key))
        window.navigate(route)
        window.query_panel.apply_draft()

    importer.saved_scope_requested.connect(show_import_result)

    def show_import_history():
        window.navigate("versions")
        versions.select_tab(4)

    importer.saved_history_requested.connect(show_import_history)
    importer.navigate_requested.connect(window.navigate)
    def show_saved_date(day):
        window.query_panel.set_draft(replace(window.query_state.draft, as_of=day))
        window.navigate("dashboard")
        window.query_panel.apply_draft()
    importer.date_selected.connect(show_saved_date)
    window.query_panel.catalog_changed.connect(dashboard.invalidate)
    window.query_panel.catalog_changed.connect(heatmap.invalidate)
    window.query_panel.catalog_changed.connect(equipment.clear)
    window.query_panel.catalog_changed.connect(replay.invalidate)
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

    def cancel_management_refresh(*, hide_progress=True):
        """저장 전 관리 재조회와 그 응답을 받을 조회 기준을 함께 폐기한다."""
        window.management_refresh.cancel()
        window.management_refresh_query = None
        if hide_progress and not window.management_saving:
            window.management_progress.set_operation(None)

    def begin_management_save():
        window.management_saving = True
        sync_export_page()
        if not versions.progress.isVisible():
            window.management_progress.set_operation("save", "변경 사항 저장 중", "검토한 변경 사항을 저장하고 있습니다. 잠시만 기다려 주세요.")
        cancel_management_refresh()
        dashboard.invalidate()
        heatmap.invalidate()
        replay.invalidate()
        equipment.clear("관리 데이터 저장 중 · 현재 자료 확인 대기")
        list_action.setEnabled(False)
        versions.setEnabled(False)
        items.setEnabled(False)
        replay.setEnabled(False)
        ai.invalidate_data()
        sync_mode()

    def finish_saved_refresh(snapshot, error):
        window.management_progress.set_operation(None)
        query = window.management_refresh_query
        if query is None:
            return
        if not window.query_state.is_current(query):
            ai.refresh()
            return
        if error is not None:
            prefix = "저장은 완료됐지만" if window.management_last_success else "기존값을 유지했지만"
            window.notice.setText(prefix + " 화면을 다시 읽지 못했습니다. 조회 조건을 다시 선택해 주세요.")
        elif snapshot is not None and query is not None and window.query_state.is_current(query):
            accept_current_snapshot(snapshot)
            prefix = "저장 완료" if window.management_last_success else "저장 실패 · 기존값 유지"
            window.notice.setText(prefix + " · 같은 조회 조건으로 집계·위험 결과를 다시 읽었습니다. 목표 N 선택은 유지했습니다.")
        ai.refresh()

    catalog_ready = None

    def finish_catalog_reload(catalog, error):
        nonlocal catalog_ready
        ready, catalog_ready = catalog_ready, None
        if error is not None:
            window.notice.setText(str(error))
        if ready is not None:
            ready(catalog)

    catalog_refresh.completed.connect(finish_catalog_reload)

    def read_saved_catalog(ready, *, background):
        nonlocal catalog_ready
        loader = getattr(source, "reload_catalog", None)
        if loader is None:
            ready(None)
            return
        catalog_refresh.cancel()
        catalog_ready = ready
        if background:
            window.management_progress.set_operation("catalog", "저장 결과 반영 중", "저장 자료와 조회 선택지를 다시 읽고 있습니다.")
        catalog_refresh.start(BackgroundRead(loader) if background else loader)

    def apply_saved_catalog(catalog, *, reapply=False):
        """유효한 같은 조회 조건은 다시 적용하고, 결과는 이어지는 워커에서 읽는다."""
        nonlocal defer_query_apply
        if catalog is None or catalog == window.query_state.catalog:
            return False
        state = window.query_state
        applied = state.applied
        same = (applied is not None and state.applied_is_current and state.draft == applied.selection
                and state.target_draft == applied.targets)
        window.query_panel.set_catalog(catalog)
        if not (reapply and same and state.draft == applied.selection):
            return False
        defer_query_apply = True
        try:
            window.query_panel.apply_draft()
        finally:
            defer_query_apply = False
        return True

    def after_management_save(result):
        preserve_detail = item_risk_save_pending
        if result is None:
            finish_management_save(result, None, preserve_detail)
        else:
            read_saved_catalog(lambda catalog: finish_management_save(result, catalog, preserve_detail), background=True)

    def finish_management_save(result, catalog, preserve_detail):
        reapplied = apply_saved_catalog(catalog, reapply=True)
        if catalog is not None:
            configure_import_choices()  # 데이터 관리에서 추가 · 이름 변경한 Site를 가져오기 선택 목록에도 (11-11 6차)
        window.management_saving = False
        sync_export_page()
        window.management_last_success = result is not None
        versions.setEnabled(True)
        items.setEnabled(True)
        replay.setEnabled(True)
        replay.invalidate()
        versions.refresh_after_save()
        if preserve_detail and result:
            items.last_saved_rule_id = next((r.key["risk_rule_id"] for r in result.records if r.table == "risk_rule"), None)
        items.refresh_after_save(preserve_detail=preserve_detail)
        if preserve_detail and result:
            item_save_notice("저장 완료 · 현재 자료로 다시 조회합니다.", "success")
        ai.invalidate_data()
        query = window.query_state.applied
        cancel_management_refresh(hide_progress=False)
        if query is not None and window.query_state.is_current(query):
            window.management_refresh_query = query
            window.management_progress.set_operation("refresh", "저장 결과 반영 중", "저장된 자료로 화면을 다시 조회하고 있습니다.")
            window.management_refresh.start(BackgroundRead(lambda: source.snapshot(query)))
            if reapplied:
                window.notice.setText("저장 완료 · 새 조회 선택지에서 같은 조회 조건을 다시 적용했습니다.")
        else:
            window.management_progress.set_operation(None)
            prefix = "저장 완료" if window.management_last_success else "저장 실패 · 기존값 유지"
            window.notice.setText(prefix + " · 조회 조건을 적용해 현재 저장된 현황을 확인해 주세요.")
        judgments = getattr(result, "refresh", None)
        if judgments and any(not target.valid for target in judgments.targets):
            window.notice.setText(window.notice.text() + "\n목표 N의 적용 근거가 바뀌었습니다. 목표 기준에서 재선택해 주세요.")

    def uncertain_management_save():
        window.management_saving = False
        sync_export_page()
        window.management_progress.set_operation(None)
        ai.invalidate_data()
        window.notice.setText("저장 결과 미확인 · 같은 수정 묶음을 다시 저장하지 말고 DB 기록을 확인해 주세요.")

    risk_save.save_started.connect(begin_management_save)
    data_page.save_started.connect(begin_management_save)
    data_page.saved.connect(after_management_save)
    data_page.save_failed.connect(lambda: after_management_save(None))
    risk_save.saved.connect(data_page.invalidate)
    importer.saved.connect(data_page.invalidate)
    importer.deletion.deleted.connect(data_page.invalidate)
    importer.sql_page.saved.connect(data_page.invalidate)
    risk_save.saved.connect(after_management_save)
    risk_save.save_uncertain.connect(uncertain_management_save)
    risk_save.save_failed.connect(lambda: after_management_save(None))
    versions.save_started.connect(begin_management_save)
    versions.saved.connect(after_management_save)
    versions.save_failed.connect(lambda: after_management_save(None))
    versions.save_uncertain.connect(uncertain_management_save)
    versions.sql_requested.connect(lambda: export_sql())
    versions.history_panel.sql_button.setEnabled(db_exporter is not None)
    def item_save_notice(text, tone):
        nonlocal item_risk_save_pending
        if item_risk_save_pending:
            if tone != "success":  # 성공 칩·안내는 띄우지 않고, 실패·결과 미확인만 알린다.
                items.show_save_feedback(text, tone)
            item_risk_save_pending = False
    risk_save.save_failed.connect(lambda: item_save_notice("저장 실패 · 기존 자료 유지 · DB 연결과 입력값을 확인해 주세요.", "danger"))
    risk_save.save_uncertain.connect(lambda: item_save_notice("저장 결과 미확인 · 중복 등록하지 말고 데이터 관리와 DB 기록을 확인해 주세요.", "warning"))
    risk_save.request.completed.connect(lambda *_: ai.refresh())
    window.management_refresh.completed.connect(finish_saved_refresh)
    window.after_management_save = after_management_save
    window.close_guard = lambda: (risk_save.state != "saving" and not versions.saving and not data_page.saving
                                 and not settings.operation.busy
                                 and not reports.saving
                                 and not versions.history_panel.read_request.busy and importer.state != "saving"
                                 and importer.deletion.phase != "deleting" and not importer.sql_page.busy)

    def invalidate_saved_results(receipt):
        """DB 저장 후 이전 판정·대기 중 AI 요청을 폐기하고 N 재확인을 안내한다.

        param
        - receipt: commit 성공 후에만 전달되는 저장 결과와 관련 판정.
        """
        if not is_database_import_saver(importer.saver):
            return
        cancel_management_refresh()
        dashboard.invalidate()
        ai.invalidate_data()
        heatmap.invalidate()
        replay.invalidate()
        equipment.clear("저장 자료 변경 · 조회 조건을 다시 선택해 주세요.")
        list_action.setEnabled(False)
        read_saved_catalog(finish_import_catalog, background=importer.progress_dialog.isVisible())
        judgments = getattr(receipt, "refresh", None)
        invalid = tuple(target for target in judgments.targets if not target.valid) if judgments else ()
        if invalid:
            details = " / ".join(f"{model_name(source, target.model_id)}·계열 {family_name(source, target.family_id)}: "
                                 f"{target.reason}" for target in invalid)
            # 저장 뒤 가져오기 화면은 1단계로 돌아가므로, 확인할 점은 토스트로 알린다 (11-10 2차 1-2).
            importer.show_toast("목표 N 재선택·적용 근거 확인 필요: " + details, "warning", 10000)
        unresolved = judgments.unresolved_risk_ids if judgments else ()
        if unresolved:
            identifiers = ", ".join(str(identifier) for identifier in unresolved)
            importer.show_toast(
                f"수동 위험 지정 {len(unresolved)}건이 미확인으로 유지되었습니다 (지정 번호: {identifiers}). "
                "대상 버전·Item과 적용 범위를 확인해 주세요. 위험이 해제된 상태가 아닙니다.", "warning", 10000)
        window.notice.setText(f"DB 저장 완료 · 입력 작업 {receipt.batch_id} · {receipt.row_count}행")

    importer.saved.connect(invalidate_saved_results)
    importer.saved.connect(lambda receipt: versions.record_import(
        receipt, importer.draft, persisted=is_database_import_saver(importer.saver)))
    def update_import_choices_for_memory(*_):
        if not is_database_import_saver(importer.saver):
            configure_import_choices()

    importer.saved.connect(update_import_choices_for_memory)
    def finish_import_catalog(catalog):
        apply_saved_catalog(catalog)
        versions.refresh_after_save()
        items.refresh_after_save()
        configure_import_choices()
        window.management_progress.set_operation(None)

    def refresh_after_data_change(message, *, background):
        cancel_management_refresh()
        dashboard.invalidate()
        heatmap.invalidate()
        replay.invalidate()
        equipment.clear(message)
        ai.invalidate_data()
        list_action.setEnabled(False)
        read_saved_catalog(finish_import_catalog, background=background)

    def after_import_delete(_result):
        refresh_after_data_change("저장 자료 삭제 · 조회 조건을 다시 선택해 주세요.",
                                  background=importer.progress_dialog.isVisible())

    def after_sql_import(_result):
        refresh_after_data_change("SQL 자료 저장 완료 · 조회 조건을 다시 선택해 주세요.", background=True)

    importer.deletion.deleted.connect(after_import_delete)
    importer.sql_page.saved.connect(after_sql_import)
    return window


def startup_warning(db_problem: str | None, ai_problem: str | None, *, demo_without_db: bool = False) -> str:
    """설정된 DB를 읽지 못한 경우와 DB 설정 없이 시작한 예시 모드를 구분한다."""
    if db_problem:
        return "DB에 연결하지 못했습니다. MySQL 실행 상태와 접속 설정을 확인해주세요."
    parts = []
    if demo_without_db:
        parts.append("DB 접속 설정이 없어 합성 예시를 표시합니다. setron.env를 확인해주세요.")
    if ai_problem:
        parts.append("AI 예제 응답 · " + ai_problem)
    return " / ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auto-close-ms", type=int, default=0, help="실행 검증용 자동 종료 시간")
    parser.add_argument("--demo-excel", action="store_true", help="실행 중인 Excel 대신 합성 통합문서로 가져오기 화면 확인")
    args = parser.parse_args(argv)
    if args.auto_close_ms < 0:
        parser.error("--auto-close-ms는 0 이상이어야 합니다.")
    app = QApplication.instance()
    if app is None:
        if sys.platform == "win32":
            from ctypes import windll

            # Windows가 개발 실행도 Python과 별개의 작업 표시줄 앱으로 인식하게 한다.
            windll.shell32.SetCurrentProcessExplicitAppUserModelID("SETRON.Desktop")
        app = QApplication(sys.argv[:1])
    app.setApplicationName("SETRON")
    app.setWindowIcon(icon("setron-window-icon.svg"))
    install_theme(app)
    # SETRON_DB_* 접속 설정이 있으면 조회 화면은 DB 자료를 읽고 가져오기 저장도 DB로 한다.
    source, problem = dashboard_source_from_env()
    ai = ai_providers_from_env()
    app.aboutToQuit.connect(ai.close)

    def build_window(source, problem):
        current = create_window(source, excel=DemoExcel() if args.demo_excel else None,
                                import_saver=saver_from_env(), db_exporter=sql_exporter_from_env(),
                                sql_importer=sql_importer_from_env() if source is not None else None,
                                management_gateway=gateway_from_env() if source is not None else None,
                                data_exporter=data_exporter_from_env(), export_file_loader=export_files_from_env(),
                                extractor=ai.extractor,
                                ai_client=ai.client,
                                rule_extractor=None if args.demo_excel else rule_extractor,
                                import_delete_gateway=None if args.demo_excel else ImportDeleteGateway.from_env(),
                                chat_enabled=True)
        if problem or ai.problem:
            current.notice.setText(" / ".join(p for p in (problem, ai.problem) if p))
        current.set_data_warning(
            startup_warning(problem, ai.problem, demo_without_db=source is None and problem is None),
            reconnect=bool(problem),
        )
        if source is not None and not source.dates:
            current.notice.setText("저장된 설비 현황이 없습니다. ‘데이터 가져오기’에서 첫 현황표를 저장해 주세요."
                                   + (f" · {ai.problem}" if ai.problem else ""))
        else:
            if source is not None:
                current.query_panel.set_as_of(max(source.analysis_dates or source.dates))
            QTimer.singleShot(0, current, current.query_panel.apply_draft)
        return current

    window = build_window(source, problem)
    if problem:
        reconnect = ReadRequest(window)

        def retry_database():
            if reconnect.start(BackgroundRead(dashboard_source_from_env)):
                window.db_reconnect_button.setEnabled(False)
                window.db_reconnect_button.setText("DB 연결 중…")

        def finish_reconnect(result, error):
            nonlocal window
            if not window.isVisible():
                return
            window.db_reconnect_button.setEnabled(True)
            window.db_reconnect_button.setText("DB 재연결")
            if error is not None or result[0] is None:
                window.set_data_warning("DB에 다시 연결하지 못했습니다. MySQL 실행 상태와 접속 설정을 확인해주세요.",
                                        reconnect=True)
                return
            if not window.close_guard():
                window.set_data_warning("데이터 처리 중입니다. 완료 후 DB 재연결을 눌러 주세요.", reconnect=True)
                return
            # DB를 쓰는 모든 화면과 저장 서비스가 같은 공급자로 시작하도록 창을 다시 구성한다.
            replacement = build_window(result[0], None)
            replacement.restoreGeometry(window.saveGeometry())
            replacement.show()
            window.close()
            window.deleteLater()
            window = replacement

        window.db_reconnect_button.clicked.connect(retry_database)
        reconnect.completed.connect(finish_reconnect)
    if screen := app.primaryScreen():
        area = screen.availableGeometry()
        window.resize(min(1440, area.width() - 32), min(960, area.height() - 48))
    window.show()
    if args.auto_close_ms:
        QTimer.singleShot(args.auto_close_ms, lambda: window.close())
    return app.exec()
