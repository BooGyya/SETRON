"""UI 시작점. 대시보드·히트맵·목록/상세는 같은 관측 공급자를 쓴다.

`SETRON_DB_*` 설정이 없으면 합성 관측·메모리 저장을 쓰고 DB에 연결하지 않는다. 설정이 있으면 조회 화면은 DB 자료를
읽고, 가져오기 저장과 DB 전체 SQL 내보내기도 DB를 쓴다.
데이터 가져오기는 요청 시에만 실행 중인 Excel과 설정된 AI API에 연결한다. 데모 Excel은 예제 응답을 사용한다.
"""

import argparse
import sys
from dataclasses import replace
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
from setron.ui.equipment_drawer import EquipmentDrawer
from setron.ui.target_data import describe_targets, initial_targets, target_pairs, validate_targets
from setron.ui.query_state import QuerySelection, TargetN
from setron.ui.group_dialog import VersionGroupDialog
from setron.ui.target_dialog import TargetDialog
from setron.ui.version_filter import version_options
from setron.ui.version_filter_dialog import VersionFilterDialog
from setron.ui.theme import install_theme
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
from setron.ui.management_page import ManagementPage
from setron.ui.management_data import gateway_from_env
from setron.ui.management_integration import export_failure, report_filter
from setron.ui.settings_page import SettingsPage
from setron.ui.import_dialogs import ImportProgressDialog


def create_window(source: DashboardSource | None = None, *, version_loader=None, item_loader=None,
                  excel=None, extractor=None, import_saver=None, ai_parser=None, ai_analyzer=None,
                  db_exporter=None, sheet_extractor=None, management_gateway=None, data_exporter=None, ai_client=None,
                  rule_extractor=None, chat_enabled: bool = False, import_comparison_loader=None,
                  import_delete_gateway=None) -> MainWindow:
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
    window.query_panel.goal_button.setToolTip("모델·계열별 N 칸(N-2 ~ N+2) 입력·저장 · 새 계산은 조회 적용 후")
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

    window.open_single_targets = open_targets  # 목표 N 하나 임시 선택(기존 편집). N 그룹 저장 전 기본 목표에 쓴다.
    window.group_dialog = None

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
        dialog = VersionGroupDialog(source, targets=state.target_draft, group_key=state.draft.group_key, parent=window)
        window.group_dialog = dialog

        def finish(*_):
            if window.group_dialog is dialog:
                window.group_dialog = None
            dialog.deleteLater()

        dialog.finished.connect(finish)
        dialog.open()

    window.query_panel.goal_button.clicked.connect(open_groups)
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
                          comparison_loader=import_comparison_loader, delete_gateway=import_delete_gateway)
    def configure_import_choices():
        importer.configure_choices(dates=source.dates, sites=(s.label for s in source.catalog.sites),
                                   families=(family_name(source, key) for key in source.families))
    configure_import_choices()
    window.release_refresh_context = release_refresh_context
    def replay_targets():
        applied = window.query_state.applied
        return (applied.targets if applied is not None and applied.targets is not None else
                initial_targets(source))
    replay = ReplayPage(source, targets_provider=replay_targets)
    management = ManagementPage(management_gateway, context_provider=release_refresh_context)
    versions.configure(management_gateway, release_refresh_context, edit_allowed=lambda: (
        not management.busy and not management.locked and management.state not in {"review", "saving"}
        and not management.pending and not management.pending_applicability))
    versions.navigate_requested.connect(window.navigate)
    def import_from_version(kind):
        importer.request_kind(kind)      # 자료 종류 판단은 가져오기 화면이 한다 (앱 조립 파일은 업무 코드를 직접 부르지 않음)
        window.navigate("import")
    versions.import_requested.connect(import_from_version)
    item_risk_save_pending = False
    def submit_item_risk(values):
        nonlocal item_risk_save_pending
        if (management.busy or management.locked or management.state in {"review", "saving"}
                or management.pending or management.pending_applicability):
            return False
        try:
            if management._changes():
                return False
        except ValueError:
            return False
        item_risk_save_pending = True
        started = management.create_risk_rule(values)
        if not started:
            item_risk_save_pending = False
        return started
    items.manager.configure(management_gateway, release_refresh_context, submit_item_risk)
    window.management_gateway = management_gateway
    window.management_refresh = ReadRequest(window)
    window.management_refresh_query = None
    window.management_saving = False
    window.management_last_success = False
    window.management_progress = ImportProgressDialog(window)

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
    analysis_allowed = lambda: not (management.busy or management.locked or window.management_saving  # noqa: E731
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
    reports.title_changed.connect(lambda title: window.header.title.setText(title) if window.route == "reports" else None)
    if chat_enabled:
        ai.report_saved.connect(reports.add)
    window.register_page("reports", reports, uses_query=False)
    window.page_changed.connect(lambda key: reports.enter() if key == "reports" else None)
    window.register_page("management", management, uses_query=False)
    settings = SettingsPage(source)
    window.register_page("settings", settings, uses_query=False)
    settings.targets_requested.connect(open_groups)

    def settings_management(table):
        if management.busy or management.locked or management.state in {"review", "saving"} or management.pending or management.pending_applicability:
            settings.feedback.setText("관리 데이터에서 진행 중인 검토·저장을 완료한 뒤 다시 열어 주세요.")
            return
        management.kind.setCurrentIndex(management.kind.findData(table))
        window.navigate("management")
    settings.management_requested.connect(settings_management)

    def apply_personal(value):
        window.set_menu_order(value["order"])
        layout = dashboard.layout()
        layout.removeWidget(dashboard.kpi_grid)
        layout.removeWidget(dashboard.overview)
        position = layout.indexOf(dashboard.applied_row) + 1
        first, second = ((dashboard.overview, dashboard.kpi_grid) if value["home"] == "heatmap"
                         else (dashboard.kpi_grid, dashboard.overview))
        layout.insertWidget(position, first)
        layout.insertWidget(position + 1, second)
        catalog = source.catalog
        selected = replace(catalog,
            lines=tuple(o for o in catalog.lines if value["lines"] is None or o.key in value["lines"]),
            groups=tuple(o for o in catalog.groups if value["groups"] is None or o.key in value["groups"]))
        if selected != window.query_state.catalog:
            window.set_query_catalog(selected)
    settings.personal_changed.connect(apply_personal)
    apply_personal(settings.personal)

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
                raise ValueError("저장된 N 그룹이 우선 적용 중입니다. 기준·설정에서 그룹을 확인한 뒤 이동해 주세요.")
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
    window.register_auxiliary_page("equipment", equipment, parent_key="heatmap", title="설비 목록")
    drawer = window.equipment_drawer = EquipmentDrawer(window.centralWidget())
    window.equipment_edit_request = ReadRequest(window)
    drawer.closed.connect(window.equipment_edit_request.cancel)

    def close_drawer(*_):
        drawer.close_panel(restore_focus=False)

    window.page_changed.connect(lambda key: close_drawer() if key != "heatmap" else None)
    equipment.mode_changed.connect(lambda mode: close_drawer() if mode != "detail" else None)
    window.destroyed.connect(window.equipment_edit_request.cancel)

    def open_installed_version_editor(table, key):
        if (not drawer.isVisible() or window.route != "heatmap" or drawer.request is None
                or not window.query_state.is_current(drawer.request.query) or window.management_saving
                or window.management_refresh.busy):
            return
        if management.open_record(table, key):
            window.navigate("management")
        else:
            drawer.set_edit_loading(False, "관리 화면의 저장·검토를 마친 뒤 다시 시도해 주세요.")

    def request_installed_version_editor():
        request = drawer.request
        if (request is None or not window.query_state.is_current(request.query) or window.route != "heatmap"
                or not callable(getattr(management_gateway, "equipment_versions", None))):
            return
        if (management.busy or management.locked or management.state in {"review", "saving"}
                or window.management_saving or window.management_refresh.busy):
            drawer.set_edit_loading(False, "관리 화면의 조회·저장·검토를 마친 뒤 다시 시도해 주세요.")
            return
        observation_date = drawer.detail.record.base_date
        drawer.set_edit_loading(True)
        window.equipment_edit_request.start(BackgroundRead(lambda: management_gateway.equipment_versions(
            request.equipment_id, observation_date)))

    def installed_version_choices(choices, error):
        if (drawer.request is None or not drawer.isVisible() or window.route != "heatmap"
                or not window.query_state.is_current(drawer.request.query)):
            return
        if error is not None:
            drawer.set_edit_loading(False, "수정할 SW 기록을 읽지 못했습니다. DB 연결을 확인한 뒤 다시 시도해 주세요.")
        else:
            drawer.choose_edit_record(choices)

    drawer.edit_requested.connect(request_installed_version_editor)
    drawer.record_requested.connect(open_installed_version_editor)
    window.equipment_edit_request.completed.connect(installed_version_choices)
    list_action = window.header.actions["equipment_list"]
    list_action.setToolTip("현재 적용 조건의 전체 설비 목록")
    origin_scroll = {}
    overview_notice = window.notice.text()

    def sync_mode(*_):
        # 가져오기 화면은 자체 단계 배너를 쓰고, 대시보드·히트맵은 화면 단순화를 위해 공통 안내 배지를 숨긴다.
        quiet = ("import", "dashboard", "heatmap", "items", "replay", "versions", "reports", "settings") + (("ai",) if chat_enabled else ())
        window.notice.setVisible(window.route not in quiet or window.management_saving)
        window.header.toolbar.setVisible(window.route not in {"replay", "versions", "reports", "settings"})
        list_action.setVisible(window.route not in ("dashboard", "heatmap"))  # Figma 헤더에 없음. 대시보드는 카드의 "설비 목록 →"
        if window.management_saving:
            window.notice.setText("관리 데이터 저장 중 · 완료 후 관련 화면을 다시 읽습니다.")
            list_action.setEnabled(False)
            return
        if window.route == "items":
            window.header.title.setText("Item 상세" if items.mode == "detail" and items.tabs.currentIndex() == 0 else "아이템 매니저")
            window.notice.setText("Item 조회 전용 · 자료 출처와 관측 기준일은 본문 참조")
            list_action.setEnabled(False)
        elif window.route == "import":
            window.notice.setText(f"데이터 가져오기 · 실행 중인 Excel 읽기 · {importer.extraction_label} · 저장 위치: {importer.saver.target_label}")
            list_action.setEnabled(False)
        elif window.route == "replay":
            list_action.setEnabled(False)
        elif window.route == "ai":
            window.notice.setText(getattr(ai, "notice_text", "AI 분석 · 예제 응답 · 질문으로 DB를 바꾸지 않음"))
            list_action.setEnabled(False)
        elif window.route == "reports":
            list_action.setEnabled(False)
        elif window.route == "versions":
            window.header.title.setText("버전 관리")
            window.notice.setText("버전 관리 · 원본 보존 · 자료 출처와 조회 상태는 본문 참조")
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
        if window.route != "heatmap":
            return
        if equipment.open_detail(request, origin="heatmap"):
            detail = next(d for d in equipment.data.details if d.record.equipment_id == request.equipment_id)
            drawer.open_detail(request, detail, equipment.detail_model.rows, equipment.evidence_values,
                               equipment.snapshot.source_label, return_focus=heatmap.tile_buttons.get(request.equipment_id),
                               edit_available=callable(getattr(management_gateway, "equipment_versions", None)))

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
    importer.deletion.setProperty("backupAvailable", db_exporter is not None)
    importer.deletion.backup_button.setEnabled(db_exporter is not None)
    if db_exporter is not None:
        importer.deletion.backup_requested.connect(export_sql)
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
    sync_mode()  # 첫 화면(대시보드)에도 안내 배지 표시 규칙을 바로 적용한다.
    def ensure_loaded(key):
        # 등록 과정에서 취소된 초기 워커 조회는 해당 화면 진입 시 다시 시작한다.
        if key == "versions" and versions.catalog is None and not versions.read_request.busy:
            versions.reload()
        elif key == "items" and items.data is None and not items.read_request.busy:
            items.reload()
        elif key == "replay":
            replay.enter()
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
            heatmap.fail_query(query, getattr(dashboard, "failure_reason", None))
            equipment.clear("설비 결과 표시 실패 · 다시 적용해 주세요")

    window.query_applied.connect(apply_query)
    # 대시보드 계산 뒤에 연결해야 AI 화면이 같은 적용 조건의 결과를 읽는다.
    window.query_applied.connect(lambda *_: ai.refresh())
    window.query_panel.catalog_changed.connect(lambda *_: ai.refresh())
    window.query_panel.targets_changed.connect(lambda *_: ai.refresh())
    window.page_changed.connect(lambda key: ai.refresh() if key == "ai" else None)
    ai.navigate_requested.connect(window.navigate)
    importer.requery_requested.connect(lambda: window.navigate("dashboard"))
    importer.navigate_requested.connect(window.navigate)
    def show_saved_date(day):
        window.query_panel.set_draft(replace(window.query_state.draft, as_of=day))
        window.navigate("dashboard")
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

    def cancel_management_refresh():
        """저장 전 관리 재조회와 그 응답을 받을 조회 기준을 함께 폐기한다."""
        window.management_refresh.cancel()
        window.management_refresh_query = None
        if not window.management_saving:
            window.management_progress.set_operation(None)

    def begin_management_save():
        window.management_saving = True
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
        replay.setEnabled(True)
        replay.invalidate()
        versions.refresh_after_save()
        items.refresh_after_save()
        ai.invalidate_data()
        query = window.query_state.applied
        cancel_management_refresh()
        if reapplied:
            window.management_progress.set_operation(None)
            # 선택지가 바뀌어 같은 조건을 다시 적용했다. 적용 과정에서 화면을 이미 새로 읽었다.
            window.notice.setText("저장 완료 · 조회 선택지(Site·Line)를 새로 읽고 같은 조회 조건을 다시 적용했습니다.")
        elif query is not None and window.query_state.is_current(query):
            window.management_refresh_query = query
            window.management_progress.set_operation("refresh", "저장 결과 반영 중", "저장된 자료로 화면을 다시 조회하고 있습니다.")
            window.management_refresh.start(BackgroundRead(lambda: source.snapshot(query)))
        else:
            window.management_progress.set_operation(None)
            prefix = "저장 완료" if window.management_last_success else "저장 실패 · 기존값 유지"
            window.notice.setText(prefix + " · 조회 조건을 적용해 현재 저장된 현황을 확인해 주세요.")
        judgments = getattr(result, "refresh", None)
        if judgments and any(not target.valid for target in judgments.targets):
            management.notice.setText(management.notice.text() + "\n목표 N의 적용 근거가 바뀌었습니다. 목표 기준에서 재선택해 주세요.")

    def uncertain_management_save():
        window.management_saving = False
        window.management_progress.set_operation(None)
        ai.invalidate_data()
        window.notice.setText("저장 결과 미확인 · 같은 수정 묶음을 다시 저장하지 말고 DB 기록을 확인해 주세요.")

    management.save_started.connect(begin_management_save)
    management.saved.connect(after_management_save)
    management.save_uncertain.connect(uncertain_management_save)
    management.save_failed.connect(lambda: after_management_save(None))
    versions.save_started.connect(begin_management_save)
    versions.saved.connect(after_management_save)
    versions.save_failed.connect(lambda: after_management_save(None))
    versions.save_uncertain.connect(uncertain_management_save)
    versions.sql_requested.connect(lambda: export_sql())
    versions.history_panel.sql_button.setEnabled(db_exporter is not None)
    def item_save_notice(text, tone):
        nonlocal item_risk_save_pending
        if item_risk_save_pending:
            items.show_save_feedback(text, tone)
            item_risk_save_pending = False
    management.saved.connect(lambda *_: item_save_notice("저장 완료 · 현재 자료로 다시 조회합니다.", "success"))
    management.save_failed.connect(lambda: item_save_notice("저장 실패 · 기존 자료 유지 · 관리 데이터에서 오류를 확인해 주세요.", "danger"))
    management.save_uncertain.connect(lambda: item_save_notice("저장 결과 미확인 · 중복 등록하지 말고 관리 데이터와 DB 기록을 확인해 주세요.", "warning"))
    management.request.completed.connect(lambda *_: ai.refresh())
    window.management_refresh.completed.connect(finish_saved_refresh)
    window.after_management_save = after_management_save
    window.close_guard = lambda: (management.state != "saving" and not versions.saving
                                 and not settings.operation.busy
                                 and not reports.saving
                                 and not versions.history_panel.read_request.busy and importer.state != "saving"
                                 and importer.deletion.phase != "deleting")

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
        replay.invalidate()
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
    importer.saved.connect(lambda receipt: versions.record_import(
        receipt, importer.draft, persisted=is_database_import_saver(importer.saver)))
    importer.saved.connect(lambda *_: configure_import_choices())
    def after_import_delete(result):
        cancel_management_refresh()
        refresh_query_catalog()
        dashboard.invalidate()
        heatmap.invalidate()
        replay.invalidate()
        equipment.clear("저장 자료 삭제 · 조회 적용으로 다시 확인해 주세요.")
        ai.invalidate_data()
        versions.refresh_after_save()
        items.refresh_after_save()
        list_action.setEnabled(False)
        configure_import_choices()
    importer.deletion.deleted.connect(after_import_delete)
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
                           ai_client=ai.client,
                           # 실제 Excel이면 정해진 양식은 규칙으로 먼저 읽는다. 합성 통합문서(--demo-excel)는 예제 흐름 유지.
                           rule_extractor=None if args.demo_excel else rule_extractor,
                           import_delete_gateway=None if args.demo_excel else ImportDeleteGateway.from_env(),
                           chat_enabled=True)
    if problem or ai.problem:
        window.notice.setText(" / ".join(p for p in (problem, ai.problem) if p))
    if screen := app.primaryScreen():
        area = screen.availableGeometry()
        window.resize(min(1440, area.width() - 32), min(960, area.height() - 48))
    window.show()
    if args.auto_close_ms:
        QTimer.singleShot(args.auto_close_ms, window.close)
    return app.exec()
