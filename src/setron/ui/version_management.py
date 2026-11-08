"""VER5 / 7 버전관리. 고정 탭 아래 설치 기록·릴리즈·작업본·이력을 분리한다."""

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from setron.features.aging.service import pm_age
from setron.features.management.schemas import ManagedRecord, ManagementResult, RecordEdit
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseConnectionError, DatabaseSaveError
from setron.ui.components import button, label
from setron.ui.dashboard_data import family_name
from setron.ui.import_dialogs import ImportConfirmDialog, ImportDialog, ImportProgressDialog
from setron.ui.import_widgets import PeriodDateField, import_combo_style
from setron.ui.query_panel import _WrapRow
from setron.ui.query_state import QuerySelection, QueryState, UNASSIGNED
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.replay_data import freeze_source
from setron.ui.version_management_data import VersionWorkspace
from setron.ui.version_management_forms import ItemForm, ReleaseForm, SoftwareDialog, integer
from setron.ui.version_management_history import FIELD_LABELS, HistoryPanel, ReviewPanel
from setron.ui.version_management_widgets import actions, card, columns, combo, note, set_rows, table, tabs, text_field
from setron.ui.versions import VersionsPage, _field, _fit


class VersionManagementPage(VersionsPage):
    """기존 릴리즈 읽기 계약을 유지하고 VER5 관리 화면을 구성한다."""

    navigate_requested = Signal(str)
    import_requested = Signal(str)
    sql_requested = Signal()
    save_started = Signal()
    saved = Signal(object)
    save_failed = Signal()
    save_uncertain = Signal()

    def __init__(self, loader, source, *, targets_provider=lambda: None):
        self.workspace = VersionWorkspace()
        self.source, self.targets_provider = source, targets_provider
        self.gateway, self.context_provider, self.edit_allowed = None, lambda: None, lambda: True
        self._ready, self._tab_history, self._navigating = False, [], False
        self._operation, self.locked, self.pending_change = None, False, None
        super().__init__(loader)
        self.operation_request = ReadRequest(self)
        self.operation_request.completed.connect(self._operation_done)
        self.installed_request = ReadRequest(self)
        self.installed_request.completed.connect(self._installed_loaded)
        self.progress = ImportProgressDialog(self)
        self.installed_source, self.installed_records = None, ()
        self._installed_epoch, self._installed_dirty = 0, True
        self.software_dialog = self.item_dialog = None
        self.status.hide()
        body = self.layout()
        # 기존 목록/상세는 동일한 읽기·비동기·페이지 상태를 유지한 채 두 번째 탭으로 이동한다.
        body.removeWidget(self.list_view)
        body.removeWidget(self.detail_view)
        self.release_views = QWidget()
        release_body = QVBoxLayout(self.release_views)
        release_body.setContentsMargins(0, 0, 0, 0)
        release_body.setSpacing(20)
        release_body.addWidget(self.list_view)
        release_body.addWidget(self.detail_view)
        self.tabs = tabs(("설비 설치 버전", "릴리즈 · Item", "설비 정보 관리", "변경 검토", "변경·보관 이력"), "버전관리 탭")
        self.back_button = button("← 뒤로")
        self.back_button.clicked.connect(self.go_back)
        body.insertWidget(0, actions(self.tabs, None, self.back_button))
        self.session_notice = note("신규 등록·설비 정보·보관은 현재 앱 세션의 작업본입니다. 기존 원본·목표 N은 유지됩니다.")
        body.insertWidget(1, self.session_notice)
        self.installed_view = self._installed_panel()
        self.release_form = ReleaseForm()
        self.release_form.submitted.connect(self._release_submitted)
        self.release_form.cancelled.connect(self._release_cancelled)
        self.equipment_view = self._equipment_panel()
        self.review_panel = ReviewPanel(self.workspace)
        self.history_panel = HistoryPanel(self.workspace)
        self.history_panel.sql_requested.connect(self.sql_requested)
        self.review_panel.archive_created.connect(self._open_archives)
        self.review_panel.history_requested.connect(self._open_archives)
        self.views = (self.installed_view, self.release_views, self.equipment_view, self.review_panel, self.history_panel)
        for view in self.views:
            body.addWidget(view)
        release_body.addWidget(self.release_form)
        self.release_form.hide()
        self._decorate_releases()
        self.tabs.currentChanged.connect(self._tab_changed)
        self._ready = True
        self._fill_quick_pairs()
        self._tab_changed(0)
        self.render_list()

    def configure(self, gateway, context_provider, *, edit_allowed=lambda: True):
        self.gateway, self.context_provider, self.edit_allowed = gateway, context_provider, edit_allowed

    @property
    def busy(self):
        return self.operation_request.busy if hasattr(self, "operation_request") else False

    @property
    def saving(self):
        return self.busy and self._operation == "save"

    def _tab_changed(self, index):
        if not self._ready:
            return
        if not self._navigating:
            previous = getattr(self, "active_tab", 0)
            if previous != index:
                self._tab_history.append(previous)
        self.active_tab = index
        for i, view in enumerate(self.views):
            view.setVisible(index == i)
        self.notice.setVisible(index == 1)
        self.back_button.setEnabled(bool(self._tab_history) or (index == 1 and self.mode != "list"))
        if index == 0 and self.installed_source is None and not self.installed_request.busy:
            self.load_installed()
        elif index == 3:
            self.review_panel.refresh()
        elif index == 4:
            self.history_panel.refresh()
        self.mode_changed.emit(self.mode)

    def select_tab(self, index):
        self.tabs.setCurrentIndex(index)

    def go_back(self):
        if self.active_tab == 1 and self.release_form.isVisible():
            self._release_cancelled()
        elif self.active_tab == 1 and self.mode == "detail":
            self.back_to_list()
        elif self.active_tab == 0 and self.comparison_view.isVisible():
            self.comparison_view.hide()
            self.installed_listing.show()
        elif self.active_tab == 4 and self.history_panel.archived:
            self.history_panel.show_mode(False)
        elif self._tab_history:
            self._navigating = True
            self.tabs.setCurrentIndex(self._tab_history.pop())
            self._navigating = False
            self.back_button.setEnabled(bool(self._tab_history))

    def _decorate_releases(self):
        for field in (self.model_filter, self.family_filter, self.order):
            field.setStyleSheet(import_combo_style())
        toolbar = actions(button("변경 이력"), button("릴리즈노트 가져오기"), button("새 버전 등록", primary=True))
        toolbar.layout().itemAt(0).widget().clicked.connect(lambda: self.select_tab(4))
        toolbar.layout().itemAt(1).widget().clicked.connect(lambda: self.import_requested.emit("release"))
        self.new_release_button = toolbar.layout().itemAt(2).widget()
        self.new_release_button.clicked.connect(lambda: self.open_release_form())
        self.list_view.layout().insertWidget(0, toolbar)
        filters = self.list_view.layout().itemAt(1).widget()
        self.list_view.layout().removeWidget(toolbar)
        heading = filters.body.itemAt(0).widget()
        heading.setText("조회 조건")
        filters.body.removeWidget(heading)
        controls = tuple(toolbar.layout().itemAt(i).widget() for i in range(3))
        filters.body.insertWidget(0, actions(heading, None, *controls))
        toolbar.hide()
        toolbar.deleteLater()
        self.release_query, self.release_reset = button("조회", primary=True), button("초기화")
        self.release_query.clicked.connect(self.render_list)
        self.release_reset.clicked.connect(self.reset_release_filters)
        filters.body.addWidget(actions(self.release_query, self.release_reset, None))
        self.quick_pairs = QWidget()
        self.quick_pairs_layout = QHBoxLayout(self.quick_pairs)
        self.quick_pairs_layout.setContentsMargins(0, 0, 0, 0)
        self.quick_pairs_layout.setSpacing(12)
        filters.body.addWidget(self.quick_pairs)
        self.edit_button.setText("릴리즈 정보 수정")
        self.edit_button.clicked.disconnect()
        self.edit_button.clicked.connect(lambda: self.open_release_form(self.catalog.entry(self.selected_id)) if self.catalog and self.selected_id else None)
        # 상세의 Item 표와 선택 Item 정보는 좌우 패널로 표시한다.
        self.item_detail = card("선택 Item 상세")
        self.item_info = {}
        for key, title in (("number", "Item 번호"), ("category", "항목 · 분류"), ("content", "변경 내용"),
                           ("state", "이 릴리즈 포함 상태"), ("scope", "모델 적용 범위"), ("jira", "JIRA · 미기재는 유지"), ("remark", "비고 · REMARK")):
            field = text_field("선택 Item " + title, readonly=True)
            self.item_info[key] = field
            self.item_detail.body.addWidget(_field(title, field))
        self.add_item_button = button("Item 추가", primary=True)
        self.edit_item_button = button("Item 수정", primary=True)
        self.add_item_button.clicked.connect(lambda: self.open_item_form())
        self.edit_item_button.clicked.connect(lambda: self.open_item_form(self.selected_item))
        self.item_detail.body.addWidget(actions(self.add_item_button, self.edit_item_button))
        self.item_table.clicked.connect(lambda index: self.select_item(index.row()))
        self.item_table.activated.connect(lambda index: self.select_item(index.row()))
        self.selected_item = None
        item_card = self.item_table.parentWidget()
        item_card.body.itemAt(0).widget().setText("이 릴리즈의 Item")
        detail = self.detail_view.layout()
        detail.removeWidget(item_card)
        detail.insertWidget(2, columns(item_card, self.item_detail))
        self.source_card = self.source_table.parentWidget()
        self.source_card.hide()
        self.info_table.hide()
        self.release_metadata = {}
        metadata_fields = []
        for key, title in (("released_at", "릴리즈일"), ("order", "릴리즈 순서"), ("models", "적용 대상")):
            field = text_field("릴리즈 " + title, readonly=True)
            self.release_metadata[key] = field
            metadata_fields.append(_field(title, field))
        self.overview.body.addWidget(_WrapRow(metadata_fields, 200, fields=True))
        self.overview.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.source_button = button("계열 원본 보기")
        self.source_button.clicked.connect(self.show_release_sources)
        detail_history = button("변경 이력")
        detail_history.clicked.connect(lambda: self.select_tab(4))
        self.read_only.setText("선택 릴리즈의 Item 연결을 관리합니다. 설비 설치 상태·목표 N은 변경하지 않습니다.")
        self.edit_button.setParent(self.overview)
        self.overview.body.addWidget(actions(self.edit_button, self.source_button, detail_history, None))

    def _loaded(self, catalog, error):
        self.base_catalog = catalog if error is None else None
        try:
            effective = self.workspace.catalog(catalog) if catalog is not None and error is None else catalog
        except ValueError:
            # 다른 입력 작업으로 모델·계열이 사라진 작업본은 새 DB 자료에 강제로 붙이지 않는다.
            effective = catalog
        super()._loaded(effective, error)
        if self._ready:
            self.status.hide()
            self._fill_quick_pairs()

    def _fill_quick_pairs(self):
        while self.quick_pairs_layout.count():
            item = self.quick_pairs_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for pair in (self.catalog.pairs[:2] if self.catalog is not None else ()):
            control = button(f"{pair.model_name} · {pair.family_name}")
            control.clicked.connect(lambda checked=False, p=pair: self._quick_pair(p.model_id, p.family_id))
            self.quick_pairs_layout.addWidget(control)
        self.quick_pairs_layout.addStretch()

    def _quick_pair(self, model_id, family_id):
        self.model_filter.setCurrentIndex(self.model_filter.findData(model_id))
        self.family_filter.setCurrentIndex(self.family_filter.findData(family_id))

    def render_list(self):
        if hasattr(self, "list_model"):
            self.list_model.headings = ("버전 원문", "출시일", "확인된 순서", "포함 Item", "출처 문서")
        super().render_list()
        if self._ready:
            self.new_release_button.setEnabled(self.catalog is not None and bool(self.catalog.pairs))
            self.list_model.headings = ("버전 원문", "릴리즈일", "순서", "주요 Item", "등록 구분", "관리")
            rows = []
            for entry in self.visible_entries:
                release = entry.release
                items = self.catalog.item_rows(release.release_version_id, self.model_filter.currentData())
                included = self.catalog.included_count(release.release_version_id)
                mapping = f"{included}건" if entry.items_verified else f"확인된 {included}건 · 매핑 미완료"
                rows.append((release.version_raw, str(release.released_at or "미기재"), self.catalog.order_label(release),
                             mapping + "\n" + ("\n".join(item.item.content for item, state, *_ in items if state.startswith("포함")) or "포함 Item 미확인"),
                             "직접 등록 · 작업본" if release.release_version_id in self.workspace.release_overlays else
                             "Excel · 원문 보존" if any(source.document for source in entry.sources) else "출처 미확인", ""))
            self.list_model.set_rows(rows, tuple(e.release.release_version_id for e in self.visible_entries))
            widths = (190, 120, 190, 260, 180, 210)
            self.table.base_widths = widths
            ratio = max(1, self.table.viewport().width() / sum(widths))
            for i, width in enumerate(widths):
                self.table.setColumnWidth(i, int(width * ratio))
            self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            for i, entry in enumerate(self.visible_entries):
                detail, edit = button("상세·Item"), button("수정")
                for control in (detail, edit):
                    control.setFixedHeight(32)
                    control.setStyleSheet("QPushButton {padding:4px 8px;min-height:20px;}")
                detail.clicked.connect(lambda checked=False, row=i: self.open_row(row))
                edit.clicked.connect(lambda checked=False, row=i: self.edit_release_row(row))
                self.table.setIndexWidget(self.list_model.index(i, 5), actions(detail, edit))
            _fit(self.table)

    def reset_release_filters(self):
        self.search.clear()
        self.order.setCurrentIndex(0)
        self.model_filter.setCurrentIndex(0)
        self.page_index = 0
        self.render_list()

    def edit_release_row(self, row):
        if self.open_row(row):
            self.open_release_form(self.catalog.entry(self.selected_id))

    def _set_mode(self, mode):
        super()._set_mode(mode)
        if self._ready:
            self.release_form.hide()
            self.back_button.setEnabled(mode == "detail" or bool(self._tab_history))

    def open_row(self, row):
        if self._ready:
            self.select_tab(1)
        opened = super().open_row(row)
        if opened and self._ready:
            self.source_card.hide()
            self.detail_item_rows = self.catalog.item_rows(self.selected_id, self.model_filter.currentData())
            entry = self.catalog.entry(self.selected_id)
            self.release_metadata["released_at"].setText(str(entry.release.released_at or "미기재"))
            self.release_metadata["order"].setText(self.catalog.order_label(entry.release))
            self.release_metadata["models"].setText(", ".join(dict.fromkeys(p.model_name for p in self.catalog.pairs if p.model_id in entry.model_ids)))
            self.select_item(0)
        return opened

    def select_item(self, row):
        values = getattr(self, "detail_item_rows", ())
        self.selected_item = values[row][0] if 0 <= row < len(values) else None
        entry = self.selected_item
        metadata = self.workspace.metadata.get(("item", entry.item.item_id), {}) if entry else {}
        data = dict(number=str(entry.item.item_no or "미기재") if entry else "미기재", content=entry.item.content if entry else "Item을 선택하세요.",
                    state=values[row][1] if entry else "미확인", scope=values[row][2] if entry else "미확인", category=metadata.get("category", "미기재"),
                    jira=metadata.get("jira", "미기재"), remark=metadata.get("remark", "미기재"))
        for key, field in self.item_info.items():
            field.setText(data[key])
        self.edit_item_button.setEnabled(entry is not None)

    def open_release_form(self, entry=None):
        if self.catalog is None or self.model_filter.currentData() is None or self.family_filter.currentData() is None:
            self.notice.setText("모델·SW 계열을 먼저 선택해 주세요.")
            return False
        self.select_tab(1)
        self.release_form.open_entry(self.catalog, self.model_filter.currentData(), self.family_filter.currentData(),
                                     self.workspace.metadata.get(("release", entry.release.release_version_id), {}) if entry else {}, entry)
        self.list_view.hide()
        self.detail_view.hide()
        self.release_form.show()
        self.back_button.setEnabled(True)
        return True

    def _release_cancelled(self):
        self.release_form.hide()
        self._set_mode("detail" if self.selected_id and self.catalog.entry(self.selected_id) else "list")

    def _release_submitted(self, entry, values, reason):
        if self.release_form.catalog is not self.catalog:
            self.release_form.error.setText("입력 중 자료가 변경되었습니다. 최신 릴리즈를 다시 조회한 뒤 검토해 주세요.")
            return
        old = self.release_form.entry
        rid = entry.release.release_version_id
        change = self.workspace.stage("릴리즈", "수정" if old else "등록", entry.release.version_raw,
                                      self.release_form.before, values, reason, identity=f"release:{rid}",
                                      sources=old.sources if old else ())
        self.workspace.release_overlays[rid] = entry
        self.workspace.metadata["release", rid] = deepcopy(values)
        self.workspace.finish((change.identity,))
        self.catalog = self.workspace.catalog(self.base_catalog)
        self.release_form.hide()
        self._set_mode("list")
        self.render_list()
        self.notice.setText("릴리즈 작업본을 저장했습니다. DB·원본·목표 N·설비 설치 기록은 변경하지 않았습니다.")

    def open_item_form(self, entry=None):
        if self.catalog is None or self.selected_id is None:
            return
        self.item_dialog = ItemForm(self.catalog, self.selected_id, entry=entry,
                                   metadata=self.workspace.metadata.get(("item", entry.item.item_id), {}) if entry else {}, parent=self)
        self.item_dialog.submitted.connect(lambda result, values, reason: self._item_submitted(result, values, reason, entry))
        self.item_dialog.open()

    def _item_submitted(self, result, values, reason, old):
        if self.item_dialog is None or self.item_dialog.catalog is not self.catalog or self.item_dialog.release_id != self.selected_id:
            if self.item_dialog is not None:
                self.item_dialog.error.setText("입력 중 릴리즈 자료가 변경되었습니다. 다시 조회한 뒤 검토해 주세요.")
            return
        entry, row = result
        iid = entry.item.item_id
        before = dict(item_no=old.item.item_no, content=old.item.content) if old else {}
        existing = next((v for v in self.catalog.version_items if v.item_id == iid and v.release_version_id == self.selected_id), None)
        if existing:
            before.update(item_state=existing.state.value, target_scope=existing.target_scope.value, target_group_ids=existing.target_group_ids)
        before.update({key: self.workspace.metadata.get(("item", iid), {}).get(key, "")
                       for key in ("category", "jira", "remark")})
        try:
            change = self.workspace.stage("Item", "수정" if old else "등록", f"{self.catalog.entry(self.selected_id).release.version_raw} · Item {iid}",
                                          before, values, reason, identity=f"item:{self.selected_id}:{iid}", sources=old.sources if old else ())
        except ValueError as error:
            self.notice.setText(str(error))
            return
        self.workspace.item_overlays[self.selected_id, iid] = entry, row
        self.workspace.metadata["item", iid] = {key: values[key] for key in ("category", "jira", "remark")}
        self.workspace.finish((change.identity,))
        self.catalog = self.workspace.catalog(self.base_catalog)
        self.item_dialog.accept()
        selected = self.selected_id
        self._set_mode("list")
        self.render_list()
        index = next((i for i, e in enumerate(self.visible_entries) if e.release.release_version_id == selected), None)
        if index is not None:
            self.open_row(index)
        self.notice.setText("Item 작업본을 저장했습니다. 명시된 릴리즈 연결만 수정했으며 DB·설비 설치 상태는 유지됩니다.")

    def show_release_sources(self):
        dialog = ImportDialog("계열 원본 · 입력 근거", self)
        rows = self.source_model.rows
        dialog.add_message("\n\n".join(" · ".join(row) for row in rows) or "원본 파일·시트·셀 위치를 전달받지 못했습니다.")
        dialog.add_action("닫기", dialog.reject, default=True)
        dialog.finish_layout()
        self.source_dialog = dialog
        dialog.open()

    def _installed_panel(self):
        root = QWidget()
        body = QVBoxLayout(root)
        body.setContentsMargins(0, 0, 0, 0)
        self.installed_listing = QWidget()
        listing = QVBoxLayout(self.installed_listing)
        listing.setContentsMargins(0, 0, 0, 0)
        filters = card("설비에 기록된 설치 버전 관리", outlined=False)
        self.installed_search = text_field("설비명 검색")
        self.installed_search.setPlaceholderText("설비호기 검색")
        self.installed_date = PeriodDateField()
        self.installed_date.setFixedHeight(38)
        self.installed_date.setAccessibleName("설치 기록 관측 기준일")
        if self.source.dates:
            self.installed_date.setText(str(self.source.dates[-1]))
        self.installed_site = combo("설치 기록 Site", ((None, "전체"), *((s.key, s.label) for s in self.source.catalog.sites)))
        self.installed_fab = combo("설치 기록 Fab")
        self.installed_line = combo("설치 기록 Line")
        self.installed_site.currentIndexChanged.connect(self._fill_locations)
        self.installed_fab.currentIndexChanged.connect(self._fill_lines)
        self.query_button = button("조회", primary=True)
        self.query_button.clicked.connect(self.load_installed)
        query_host = QWidget()
        query_row = QVBoxLayout(query_host)
        query_row.setContentsMargins(0, 0, 0, 0)
        query_row.addStretch()
        self.query_button.setFixedHeight(38)
        query_row.addWidget(self.query_button)
        filters.body.addWidget(_WrapRow([_field("설비명 검색", self.installed_search), _field("Site / 원본 구역", self.installed_site),
                                         _field("관측 기준일", self.installed_date), query_host], 220, fields=True))
        filters.body.addWidget(_WrapRow([_field("Fab", self.installed_fab), _field("Line", self.installed_line)], 220, fields=True))
        get_excel, history, compare = button("Excel 현황 가져오기"), button("변경 이력"), button("버전 비교")
        get_excel.clicked.connect(lambda: self.import_requested.emit("equipment"))
        history.clicked.connect(lambda: self.select_tab(4))
        compare.clicked.connect(self.show_comparison)
        filters.body.addWidget(actions(get_excel, history, compare, None))
        filters.body.addWidget(note("현재 버전 기록의 정정은 목표 기준 N을 변경하지 않습니다."))
        self.installed_notice = note()
        filters.body.addWidget(self.installed_notice)
        listing.addWidget(filters)
        records = card("설비별 설치 버전", outlined=False)
        self.installed_model, self.installed_table = table(("설비호기", "Site / 원본 구역", "버전 계열", "현재 작업 버전", "관측 기준일", "관리"),
                                                         "설비별 설치 버전", (140, 180, 180, 260, 150, 120))
        self.installed_table.clicked.connect(self._installed_click)
        self.installed_table.activated.connect(lambda index: self.open_software(index.row()))
        records.body.addWidget(self.installed_table)
        self.correct_button = button("선택 설비 버전 정정")
        self.correct_button.clicked.connect(lambda: self.open_software(self.installed_table.currentIndex().row()))
        records.body.addWidget(actions(None, self.correct_button))
        listing.addWidget(records)
        body.addWidget(self.installed_listing)
        self.comparison_view = self._comparison_panel()
        body.addWidget(self.comparison_view)
        self.comparison_view.hide()
        self._fill_locations()
        for field in (self.installed_search, self.installed_date):
            field.textChanged.connect(self._installed_changed)
        for field in (self.installed_site, self.installed_fab, self.installed_line):
            field.currentIndexChanged.connect(self._installed_changed)
        return root

    def _installed_changed(self, *_):
        self._installed_epoch += 1
        self._installed_dirty = True
        self.correct_button.setEnabled(False)
        self.installed_notice.setText("조회 조건이 변경되었습니다. 조회를 눌러 적용해 주세요.")

    def _fill_locations(self, *_):
        catalog = self.source.catalog
        site = self.installed_site.currentData()
        with QSignalBlocker(self.installed_fab):
            self.installed_fab.clear()
            for key, caption in ((None, "전체 · 미지정 포함"), (UNASSIGNED, "미지정"), *((f.key, f.label) for f in catalog.fabs if site is None or f.site_key == site)):
                self.installed_fab.addItem(caption, key)
        self._fill_lines()

    def _fill_lines(self, *_):
        catalog = self.source.catalog
        site, fab = self.installed_site.currentData(), self.installed_fab.currentData()
        with QSignalBlocker(self.installed_line):
            self.installed_line.clear()
            for key, caption in ((None, "전체 · 미지정 포함"), (UNASSIGNED, "미지정"), *((l.key, l.label) for l in catalog.lines
                    if (site is None or l.site_key == site) and (fab is None or (l.fab_key is None if fab is UNASSIGNED else l.fab_key == fab)))):
                self.installed_line.addItem(caption, key)

    def load_installed(self):
        from setron.features.excel_import.service import parse_date
        if self.installed_request.busy or self.saving:
            return False
        try:
            day = parse_date(self.installed_date.text())
            selected = QuerySelection(site_key=self.installed_site.currentData(), fab_key=self.installed_fab.currentData(),
                                      line_key=self.installed_line.currentData(), as_of=day)
            state = QueryState(self.source.catalog)
            state.set_draft(selected)
            query = state.apply()
        except ValueError:
            self.installed_notice.setText("관측 기준일과 조회 범위를 확인해 주세요.")
            return False
        keyword = self.installed_search.text().strip().casefold()
        epoch = self._installed_epoch
        def read():
            with getattr(self.source, "_lock", nullcontext()):
                if hasattr(self.source, "refresh"):
                    self.source.refresh()
                frozen = freeze_source(self.source)
            records = tuple(r for r in frozen._scope_records(tuple(r for r in frozen.records if r.base_date == day), query)
                            if not keyword or keyword in r.equipment_code.casefold())
            return frozen, records, day, epoch
        self.query_button.setEnabled(False)
        self.correct_button.setEnabled(False)
        self._installed_dirty = True
        self.installed_notice.setText("저장된 관측 기록을 읽고 있습니다…")
        set_rows(self.installed_model, self.installed_table, ())
        return self.installed_request.start(BackgroundRead(read))

    def _installed_loaded(self, value, error):
        self.query_button.setEnabled(True)
        if error is not None:
            self.installed_source, self.installed_records = None, ()
            self.installed_notice.setText("관측 기록을 읽지 못했습니다. 다시 조회해 주세요.")
            return
        frozen, records, day, epoch = value
        if epoch != self._installed_epoch:
            self.installed_source, self.installed_records = None, ()
            self.installed_notice.setText("조회 중 조건이 변경되었습니다. 다시 조회해 주세요.")
            set_rows(self.installed_model, self.installed_table, ())
            return
        self.installed_source, self.installed_records, self.installed_day = frozen, records, day
        self._installed_dirty = False
        self._render_installed()
        self._fill_equipment_choices()

    def _render_installed(self):
        records = self.installed_records
        rows = []
        for record in records:
            versions = []
            for i, pm in enumerate(record.pms):
                overlay = self.workspace.software_drafts.get((record.equipment_id, record.base_date, i))
                raw = overlay["version_raw"] if overlay else pm.version_raw
                versions.append(f"{pm.pm_name or 'SW 관측 ' + str(i+1)} · {raw or '미입력'}")
            rows.append((record.equipment_code, f"{record.site} · {record.line or '원본 구역 미확인'}",
                         " / ".join(dict.fromkeys(family_name(self.installed_source, p.family_id) for p in record.pms)) or "SW 미등록",
                         "\n".join(versions) or "미입력", str(record.base_date), "버전 정정"))
        set_rows(self.installed_model, self.installed_table, rows, tuple(r.equipment_id for r in records))
        self.correct_button.setEnabled(bool(records) and not self.locked)
        self.installed_notice.setText(f"관측 기준일 {self.installed_day} · {len(records)}대" if records else
                                      f"{self.installed_day}에 저장된 관측 스냅샷이 없습니다. 다른 날짜로 자동 대체하지 않습니다.")

    def _installed_click(self, index):
        if index.column() == 5:
            self.open_software(index.row())

    def open_software(self, row):
        if not 0 <= row < len(self.installed_records) or self.busy or self.installed_request.busy or self.locked or self._installed_dirty:
            return False
        record = self.installed_records[row]
        if self.gateway is None:
            record = replace(record, pms=tuple(replace(pm, version_raw=self.workspace.software_drafts[
                record.equipment_id, record.base_date, i]["version_raw"], release_version_id=None)
                if (record.equipment_id, record.base_date, i) in self.workspace.software_drafts else pm
                for i, pm in enumerate(record.pms)))
        dialog = SoftwareDialog(record, self.installed_source, self.workspace.metadata.setdefault("software_inputs", {}), self)
        self.software_dialog = dialog
        dialog.submitted.connect(lambda index, raw, reason: self._software_submitted(record, index, raw, reason))
        dialog.open()
        return True

    def _software_submitted(self, record, index, raw, reason):
        if self.busy or self.locked or not self.edit_allowed():
            self.software_dialog.error.setText("다른 관리 작업의 조회·검토·저장을 마친 뒤 다시 시도해 주세요.")
            return
        pm = record.pms[index]
        change = self.workspace.stage("설비 기록", "정정", f"{record.equipment_code} · {pm.pm_name or 'SW 관측 ' + str(index+1)} · {record.base_date}",
                                      {"version_raw": pm.version_raw}, {"version_raw": raw}, reason,
                                      identity=f"software:{record.equipment_id}:{record.base_date}:{index}")
        self.pending_change = change
        self._software_target = record, index, raw
        if self.gateway is None:
            self.workspace.software_drafts[record.equipment_id, record.base_date, index] = {"version_raw": raw}
            self.workspace.finish((change.identity,))
            self.workspace.metadata.get("software_inputs", {}).pop((record.equipment_id, record.base_date, index), None)
            self.software_dialog.accept()
            self._render_installed()
            self.installed_notice.setText("설치 기록 작업본을 저장했습니다. DB·원본·목표 N·다른 SW는 변경하지 않았습니다.")
            return
        self._operation = "resolve"
        self.software_dialog.setEnabled(False)
        self.progress.set_operation("resolve", "정정 대상 확인 중", "같은 설비·관측일·SW 위치의 저장 기록을 확인하고 있습니다.")
        def resolve():
            choices = self.gateway.equipment_versions(record.equipment_id, record.base_date)
            candidates = [c for c in choices if c[2].endswith(f" · 계열 ID {pm.family_id}")
                          and c[2].startswith((pm.pm_name + " · ") if pm.pm_name else "공통 SW · ")]
            if pm.pm_name is None:
                candidates = [c for c in candidates if f"공통 SW · {pm.version_raw or '미입력'} ·" in c[2]]
            if len(candidates) != 1:
                raise ValueError("정확한 SW 관측 위치를 확인하지 못했습니다. 다시 조회해 주세요.")
            table_name, key, _ = candidates[0]
            value = self.gateway.get(table_name, key)
            if value.current.get("version_raw") != pm.version_raw or value.current.get("family_id") != pm.family_id:
                raise ValueError("조회 후 기록이 변경되었습니다. 다시 조회해 주세요.")
            return value
        self.operation_request.start(BackgroundRead(resolve))

    def _operation_done(self, value, error):
        operation, self._operation = self._operation, None
        self.progress.set_operation(None)
        if self.software_dialog is not None:
            self.software_dialog.saving = False
            self.software_dialog.setEnabled(True)
        if operation == "resolve" and (self.software_dialog is None or not self.software_dialog.isVisible()):
            if self.pending_change:
                self.workspace.discard(self.pending_change.identity)
            return
        if error is not None:
            unknown = operation == "save" and (isinstance(error, DatabaseCommitUnknownError) or not isinstance(error, (ValueError, LookupError, DatabaseConnectionError, DatabaseSaveError)))
            self.locked = unknown
            text = "저장 결과 미확인 · 중복 저장하지 말고 저장된 기록을 확인해 주세요." if unknown else (
                "기록이 달라졌거나 저장을 완료하지 못했습니다. 입력을 유지했습니다. 다시 조회해 주세요.")
            if self.software_dialog:
                self.software_dialog.error.setText(text)
                self.software_dialog.confirm.setEnabled(not unknown)
            if operation == "save":
                self.save_uncertain.emit() if unknown else self.save_failed.emit()
            return
        if operation == "resolve":
            if not isinstance(value, ManagedRecord):
                return
            self.resolved_record = value
            sources = tuple(f"{cell.field} · 입력 작업 {cell.batch_id} · {cell.sheet}!{cell.address} · "
                            + (f"원문: {cell.value}" if cell.available else "원문 미제공") for cell in value.original_cells)
            self.software_dialog.original.setText("\n".join(sources) or "원본 파일·시트·셀 위치를 전달받지 못했습니다. 원본은 변경하지 않습니다.")
            self.pending_change = replace(self.pending_change, sources=sources)
            self.workspace.pending[self.pending_change.identity] = self.pending_change
            dialog = ImportConfirmDialog("선택한 SW 관측 기록만 정정합니다. 원본·관측일·목표 N·다른 SW·실제 설비 프로그램은 유지됩니다.\n\n"
                                         + f"{self.pending_change.target}\n{dict(self.pending_change.before)['version_raw']} → {dict(self.pending_change.after)['version_raw']}\n사유: {self.pending_change.reason}",
                                         self, title="설치 기록 정정 저장 확인", action_text="저장")
            self.save_confirm = dialog
            dialog.accepted.connect(self._save_software)
            dialog.open()
        elif operation == "save":
            if not isinstance(value, ManagementResult):
                self.locked = True
                self.software_dialog.error.setText("저장 결과 미확인 · 중복 저장하지 마세요.")
                self.save_uncertain.emit()
                return
            self.workspace.finish((self.pending_change.identity,), persisted=True)
            record, index, _ = self._software_target
            self.workspace.metadata.get("software_inputs", {}).pop((record.equipment_id, record.base_date, index), None)
            self.software_dialog.accept()
            self.saved.emit(value)
            self.load_installed()

    def _save_software(self):
        if self.busy or self.locked or not self.edit_allowed():
            return
        record = self.resolved_record
        raw = dict(self.pending_change.after)["version_raw"]
        edit = RecordEdit(record.table, deepcopy(record.key), record.revision, {"version_raw": raw, "release_version_id": None})
        try:
            context = deepcopy(self.context_provider())
        except Exception:
            self.software_dialog.error.setText("현재 저장 조건을 확인하지 못했습니다. 입력을 유지했습니다. 다시 조회해 주세요.")
            return
        self._operation = "save"
        self.software_dialog.saving = True
        self.software_dialog.setEnabled(False)
        self.progress.set_operation("save", "저장하고 있습니다", "완료될 때까지 기다려 주세요. 입력 내용은 유지됩니다.")
        self.save_started.emit()
        self.operation_request.start(BackgroundRead(lambda: self.gateway.save((edit,), context)))

    def _equipment_panel(self):
        root = QWidget()
        body = QVBoxLayout(root)
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(label("설비 정보 관리 · 작업본", 20, medium=True))
        self.equipment_modes = tabs(("기존 설비 수정", "신규 설비 등록", "삭제 검토 요청"), "설비 정보 관리 작업 종류")
        body.addWidget(self.equipment_modes)
        left, right = card("저장된 작업본"), card()
        self.equipment_choice = combo("작업 대상 설비")
        left.body.addWidget(self.equipment_choice)
        self.equipment_summary = note("기본정보만 관리합니다. 모델·모듈·PM·SW 연결은 확인된 자료로 별도 관리합니다.")
        left.body.addWidget(self.equipment_summary)
        review = button("저장한 변경 검토", primary=True)
        installed = button("설치 버전 정정은 별도")
        review.clicked.connect(lambda: self.select_tab(3))
        installed.clicked.connect(lambda: self.select_tab(0))
        left.body.addWidget(actions(review, installed))
        self.equipment_title = label("기존 설비 기본정보 수정", 18, medium=True)
        right.body.addWidget(self.equipment_title)
        self.equipment_fields = {}
        for key, title in (("code", "설비호기 · 전 사업장 고유"), ("site", "Site"), ("fab", "Fab"),
                           ("line", "Line"), ("geometry", "형상 · 기본정보")):
            field = text_field("설비 정보 " + title)
            self.equipment_fields[key] = field
        right.body.addWidget(_field("설비호기 · 전 사업장 고유", self.equipment_fields["code"]))
        right.body.addWidget(_WrapRow([_field(title, self.equipment_fields[key]) for key, title in
                                      (("site", "Site"), ("fab", "Fab"), ("line", "Line"))], 130, fields=True))
        self.equipment_model_info = text_field("모델·SW 연결 상태", readonly=True)
        right.body.addWidget(_field("모델·SW 연결 상태", self.equipment_model_info))
        right.body.addWidget(_field("형상 · 기본정보", self.equipment_fields["geometry"]))
        self.equipment_reason = text_field("설비 변경·등록·삭제 검토 사유")
        right.body.addWidget(_field("변경·등록·삭제 검토 사유 *", self.equipment_reason))
        self.equipment_preview_model, self.equipment_preview = table(("항목", "현재 값", "변경 값"), "설비 작업본 변경 전후", (140, 200, 200))
        right.body.addWidget(self.equipment_preview)
        self.equipment_error = note()
        right.body.addWidget(self.equipment_error)
        self.equipment_review = button("변경분 확인", primary=True)
        self.equipment_save = button("작업본 저장", primary=True)
        self.equipment_cancel = button("입력 취소")
        self.equipment_review.clicked.connect(self.review_equipment)
        self.equipment_save.clicked.connect(self.save_equipment)
        self.equipment_cancel.clicked.connect(self.cancel_equipment)
        self.equipment_save.hide()
        right.body.addWidget(actions(self.equipment_review, self.equipment_save, self.equipment_cancel))
        body.addWidget(columns(left, right))
        self.equipment_modes.currentChanged.connect(self.fill_equipment)
        self.equipment_choice.currentIndexChanged.connect(self.fill_equipment)
        for field in (*self.equipment_fields.values(), self.equipment_reason):
            field.textChanged.connect(self.invalidate_equipment_review)
        self.equipment_prepared = None
        return root

    def _fill_equipment_choices(self):
        records = {r.equipment_id: r for r in self.installed_records}
        self.equipment_records = records
        previous = self.equipment_choice.currentData()
        with QSignalBlocker(self.equipment_choice):
            self.equipment_choice.clear()
            for key, record in records.items():
                self.equipment_choice.addItem(record.equipment_code, key)
            index = self.equipment_choice.findData(previous)
            if index >= 0:
                self.equipment_choice.setCurrentIndex(index)
        self.fill_equipment()

    def fill_equipment(self, *_):
        previous_key = getattr(self, "_equipment_input_key", None)
        inputs = self.workspace.metadata.setdefault("equipment_inputs", {})
        if previous_key is not None and not getattr(self, "_reset_equipment", False):
            inputs[previous_key] = ({key: field.text() for key, field in self.equipment_fields.items()}, self.equipment_reason.text())
        mode = self.equipment_modes.currentIndex()
        self.equipment_title.setText(("기존 설비 기본정보 수정", "신규 설비 등록", "삭제 검토 요청")[mode])
        record = getattr(self, "equipment_records", {}).get(self.equipment_choice.currentData())
        values = {"code": record.equipment_code, "site": record.site, "line": record.line or "", "fab": "", "geometry": ""} if record and mode != 1 else {}
        identity = f"equipment:{record.equipment_id}" if record and mode != 1 else ""
        if identity in self.workspace.equipment_drafts:
            values = deepcopy(self.workspace.equipment_drafts[identity])
        self.equipment_before = deepcopy(values)
        self._equipment_input_key = (mode, record.equipment_id if record and mode != 1 else "new")
        values, reason = inputs.get(self._equipment_input_key, (values, ""))
        for key, field in self.equipment_fields.items():
            field.setReadOnly(mode == 2)
            field.setText(str(values.get(key, "")))
        self.equipment_reason.setText(reason)
        self.equipment_model_info.setText(f"{record.model_name} · SW 연결 유지" if record and mode != 1 else "연결 전 · PM·SW는 확인된 자료로 별도 관리")
        self.equipment_error.setText("삭제 검토 요청은 작업본에만 기록되며 DB·원본·관측 이력을 삭제하지 않습니다." if mode == 2 else
                                     "기본정보 작업본입니다. 실제 DB의 설비·위치·PM·SW 연결은 변경하지 않습니다.")
        self.invalidate_equipment_review()

    def cancel_equipment(self):
        key = getattr(self, "_equipment_input_key", None)
        self.workspace.metadata.setdefault("equipment_inputs", {}).pop(key, None)
        self._reset_equipment = True
        self.fill_equipment()
        self._reset_equipment = False

    def invalidate_equipment_review(self, *_):
        previous = getattr(self, "equipment_prepared", None)
        if previous is not None:
            self.workspace.discard(previous.identity)
        self.equipment_prepared = None
        self.equipment_review.show()
        self.equipment_save.hide()
        set_rows(self.equipment_preview_model, self.equipment_preview, ())

    def review_equipment(self):
        try:
            values = {key: field.text() for key, field in self.equipment_fields.items()}
            mode = self.equipment_modes.currentIndex()
            if not values["code"].strip() or not values["site"].strip():
                raise ValueError("설비호기와 Site를 입력해 주세요.")
            record = getattr(self, "equipment_records", {}).get(self.equipment_choice.currentData())
            if mode != 1 and record is None:
                raise ValueError("수정할 설비를 선택해 주세요.")
            known_codes = {r.equipment_code for r in self.source.records}
            known_codes.update(v.get("code") for v in self.workspace.equipment_drafts.values())
            if values["code"] in known_codes and (mode == 1 or values["code"] != self.equipment_before.get("code")):
                raise ValueError("이미 사용 중인 설비호기입니다. 전 사업장에서 고유해야 합니다.")
            if mode == 2:
                values["검토 상태"] = "삭제 검토 요청 · 실제 삭제 안 함"
            action = ("수정", "등록", "삭제 검토 요청")[mode]
            change = self.workspace.stage("설비 정보", action, values["code"], self.equipment_before, values,
                                          self.equipment_reason.text(), identity=f"equipment:{record.equipment_id if record and mode != 1 else values['code']}")
            self.equipment_prepared = change
            set_rows(self.equipment_preview_model, self.equipment_preview, tuple((FIELD_LABELS.get(key, key), str(old or "미기재"), str(new or "미기재")) for key, old, new in change.differences))
            self.equipment_review.hide()
            self.equipment_save.show()
            return True
        except ValueError as error:
            self.equipment_error.setText(str(error))
            return False

    def save_equipment(self):
        change = self.equipment_prepared
        if change is None:
            return False
        self.workspace.equipment_drafts[change.identity] = dict(change.after)
        self.workspace.finish((change.identity,))
        self.workspace.metadata.setdefault("equipment_inputs", {}).pop(self._equipment_input_key, None)
        self.equipment_summary.setText("\n".join(f"{v.get('code')} · {v.get('site')} · Line {v.get('line') or '미지정'}" for v in self.workspace.equipment_drafts.values()))
        self.equipment_error.setText("설비 정보 작업본을 저장했습니다. 실제 DB·설비 설치 기록은 변경하지 않았습니다.")
        self.equipment_prepared = None
        self.equipment_save.hide()
        self.equipment_review.show()
        return True

    def _open_archives(self):
        self.select_tab(4)
        self.history_panel.show_mode(True)

    def record_import(self, receipt, draft=None, *, persisted=False):
        from setron.features.excel_import.schemas import SaveReceipt
        if not isinstance(receipt, SaveReceipt):
            return
        identity = f"import:{receipt.target_label}:{receipt.batch_id}"
        if any(change.identity == identity for change in self.workspace.history):
            return
        filename = getattr(draft, "workbook_name", None) or f"입력 작업 {receipt.batch_id}"
        sheet = getattr(getattr(draft, "sheet", None), "sheet_name", None) or "시트 미제공"
        change = self.workspace.stage("Excel", "가져오기", filename, {},
                                      {"입력 작업": receipt.batch_id, "저장 요약·행 수": receipt.row_count,
                                       "저장 위치": receipt.target_label, "저장 일시": str(receipt.saved_at)},
                                      "가져오기 화면에서 검토·저장한 입력 작업", identity=identity,
                                      sources=(f"파일: {filename}", f"시트: {sheet}", "행별 변경 전후는 저장 공급자의 비교 자료가 있을 때 확인합니다."))
        self.workspace.finish((change.identity,), persisted=persisted)

    def _comparison_panel(self):
        root = card("등록 SW의 버전 격차", outlined=False)
        self.compare_modes = tabs(("Relative Version", "Actual Version"), "버전 비교 표시")
        self.compare_family = combo("비교 계열")
        self.compare_target = combo("비교 목표 기준 N")
        root.body.addWidget(self.compare_modes)
        root.body.addWidget(_WrapRow([_field("비교 계열", self.compare_family), _field("목표 기준 N · 비교 전용", self.compare_target)], 220, fields=True))
        self.compare_model, self.compare_table = table(("설비", "상대 버전", "목표 판정", "안전·결함 후보"), "등록 SW 버전 비교", (160, 260, 180, 200))
        root.body.addWidget(self.compare_table)
        root.body.addWidget(label("릴리즈 경과 기간", 20, medium=True))
        self.age_model, self.age_table = table(("설비", "릴리즈일", "경과 기간", "노후 지표"), "릴리즈 경과 기간", (160, 180, 180, 180))
        root.body.addWidget(self.age_table)
        self.age_warning, self.age_danger = text_field("주의 기간 · 개월", "12"), text_field("노후 기간 · 개월", "24")
        self.age_apply = button("경과 기준 설정 · 화면 내")
        self.age_apply.clicked.connect(self.render_comparison)
        root.body.addWidget(_WrapRow([_field("주의 · 개월", self.age_warning), _field("노후 · 개월", self.age_danger), self.age_apply], 180, fields=True))
        self.compare_notice = note("목표 N·위험 기준과 독립적인 화면 내 비교입니다.")
        root.body.addWidget(self.compare_notice)
        back = button("설치 버전 목록으로")
        back.clicked.connect(lambda: (root.hide(), self.installed_listing.show()))
        root.body.addWidget(back)
        self.compare_modes.currentChanged.connect(self.render_comparison)
        self.compare_family.currentIndexChanged.connect(self._compare_family_changed)
        self.compare_target.currentIndexChanged.connect(self.render_comparison)
        return root

    def show_comparison(self):
        if self.installed_source is None or self._installed_dirty:
            self.installed_notice.setText("관측 기록을 먼저 조회해 주세요.")
            return
        with QSignalBlocker(self.compare_family):
            self.compare_family.clear()
            for fid in sorted({p.family_id for r in self.installed_records if not r.is_hpa for p in r.pms}):
                self.compare_family.addItem(family_name(self.installed_source, fid), fid)
        self._compare_family_changed()
        self.installed_listing.hide()
        self.comparison_view.show()
        self.back_button.setEnabled(True)

    def _compare_family_changed(self, *_):
        source = self.installed_source
        fid = self.compare_family.currentData()
        with QSignalBlocker(self.compare_target):
            self.compare_target.clear()
            self.compare_target.addItem("목표 미지정", None)
            if source and fid in source.families:
                for r in source.families[fid].releases:
                    self.compare_target.addItem(r.version_raw, r.release_version_id)
            targets = self.targets_provider() or ()
            selected = {target.release_id for target in targets if target.family_id == fid and target.release_id is not None}
            if len(selected) == 1:
                index = self.compare_target.findData(next(iter(selected)))
                if index >= 0:
                    self.compare_target.setCurrentIndex(index)
        self.render_comparison()

    def render_comparison(self, *_):
        if self.installed_source is None:
            return
        from setron.features.version.service import compare_version
        from setron.features.version.service import FamilyReleases
        from setron.features.version.schemas import CompareStatus
        from setron.features.risk.schemas import RISK_LABELS
        from setron.ui.target_data import target_pairs
        from setron.features.aging.service import parse_thresholds
        try:
            thresholds = parse_thresholds({"warning": integer(self.age_warning.text(), "주의 기간", positive=True),
                                            "danger": integer(self.age_danger.text(), "노후 기간", positive=True), "unit": "MONTH"})
        except ValueError as error:
            self.compare_notice.setText(str(error))
            return
        fid, target = self.compare_family.currentData(), self.compare_target.currentData()
        family = self.installed_source.families.get(fid) or FamilyReleases(fid or 1, ())
        eligibility = {(pair.model_id, pair.family_id): {option.release.release_version_id for option in pair.options if option.unavailable_reason is None}
                       for pair in target_pairs(self.installed_source)} if hasattr(self.installed_source, "applicability") else None
        rows, ages, invalid = [], [], 0
        for record in self.installed_records:
            if record.is_hpa:
                continue
            for pm in record.pms:
                if pm.family_id != fid:
                    continue
                # 기존 계열 비교·노후도 서비스만 사용. 문자열로 순서나 모델 범위를 추정하지 않는다.
                valid_target = target
                if eligibility is not None and target is not None and target not in eligibility.get((pm.group_id, fid), set()):
                    valid_target = None
                    invalid += 1
                comparison = compare_version(family, pm.version_raw, valid_target, release_version_id=pm.release_version_id)
                age = pm_age(pm, self.installed_source.families, thresholds, record.base_date).age
                raw = pm.version_raw or "미입력"
                relative = ("N" if comparison.release is not None and comparison.target is not None
                            and comparison.release.release_version_id == comparison.target.release_version_id else comparison.label)
                display = relative if self.compare_modes.currentIndex() == 0 else raw
                rows.append((record.equipment_code + (f" · {pm.pm_name}" if pm.pm_name else ""), display,
                             {CompareStatus.AT_OR_ABOVE: "달성", CompareStatus.BELOW: "미달", CompareStatus.UNDETERMINED: "미확인"}[comparison.status],
                             RISK_LABELS[self.installed_source.risk_status(record)]))
                ages.append((record.equipment_code, str(age.released_at or "미기재"), age.elapsed_label or "알 수 없음", age.label))
        self.compare_model.headings = ("설비", "상대 버전" if self.compare_modes.currentIndex() == 0 else "실제 버전", "목표 판정", "안전·결함 후보")
        set_rows(self.compare_model, self.compare_table, rows)
        set_rows(self.age_model, self.age_table, ages)
        self.compare_notice.setText(f"주의 {thresholds.warning}개월 / 노후 {thresholds.danger}개월 · 화면 내 비교 · 공통 목표 N·위험 기준은 유지됩니다.")
        if invalid:
            self.compare_notice.setText(self.compare_notice.text() + f"\n모델별 목표 적용 근거 미확인 {invalid}건은 비교 불가입니다.")

    def refresh_after_save(self):
        self.installed_request.cancel()
        self.installed_source = None
        unconfirmed = self.pending_change is not None and self.pending_change.identity in self.workspace.pending
        if self.software_dialog and not self.saving and not unconfirmed:
            self.software_dialog.reject()
        super().refresh_after_save()
        if self._ready and self.active_tab == 0 and not self.saving:
            self.load_installed()
