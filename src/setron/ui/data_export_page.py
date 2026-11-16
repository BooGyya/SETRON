"""출력 형식·범위를 선택하고 해당 카드에서 저장하는 내보내기 화면."""

from PySide6.QtCore import QEvent, QItemSelection, QItemSelectionModel, QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QBoxLayout, QButtonGroup, QFrame, QHeaderView, QHBoxLayout,
    QScrollArea, QSizePolicy, QStyle, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from setron.ui.appearance import set_color_style
from setron.ui.components import Card, button, filter_checkbox, label
from setron.ui.combo_box import ComboBox
from setron.ui.import_widgets import ImportTabs, import_combo_style
from setron.ui.theme import SECONDARY, font, set_widget_font


def _separator():
    line = QFrame()
    line.setFixedHeight(1)
    set_color_style(line, lambda colors: f"background:{colors['line']};border:0;")
    return line


class DataExportPage(QWidget):
    files_requested = Signal()

    def __init__(self):
        super().__init__()
        self._files_available = False
        self._files_loading = False
        self._files_error = False
        self._reasons = {"sql": "DB 연결이 필요합니다.", "summary": "DB 연결이 필요합니다."}
        self._busy = ""
        self.active_format = "summary"
        self.file_checks = []
        self._file_viewport = None
        self._file_resize_timer = QTimer(self)
        self._file_resize_timer.setSingleShot(True)
        self._file_resize_timer.timeout.connect(self._resize_file_list)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self.panels, self.details = {}, {}
        self.tab_strip = QWidget()
        tab_row = QHBoxLayout(self.tab_strip)
        tab_row.setContentsMargins(0, 0, 0, 0)
        self.tabs = ImportTabs()
        self.tabs.setAccessibleName("데이터 내보내기 형식 선택")
        for key, title in (("summary", "Excel"), ("sql", "SQL")):
            index = self.tabs.addTab(title)
            self.tabs.setTabData(index, key)
        tab_row.addWidget(self.tabs)
        tab_row.addStretch()
        layout.addWidget(self.tab_strip)
        self.panels["summary"] = self._excel_panel()
        self.panels["sql"] = self._sql_panel()
        for panel in self.panels.values():
            layout.addWidget(panel)
        self._add_actions()
        layout.addStretch()
        self.selected_files_button.toggled.connect(self._change_mode)
        self.production_area.currentIndexChanged.connect(self._sync_enabled)
        self.tabs.currentChanged.connect(lambda index: self._show_format(self.tabs.tabData(index)))
        self._show_format("summary")

    def _excel_panel(self):
        card = Card(padding=20)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        card.setProperty("outlined", True)
        card.body.setSpacing(14)
        heading = QVBoxLayout()
        heading.setSpacing(5)
        heading.addWidget(label("현재 관리 데이터 내보내기", 15, medium=True))
        self.details["summary"] = label(
            "설비현황표와 릴리즈노트를 기본 양식의 Excel 파일 하나로 저장합니다.",
            13, color=SECONDARY, wrap=True,
        )
        heading.addWidget(self.details["summary"])
        card.body.addLayout(heading)
        card.body.addWidget(_separator())
        self.filter_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.filter_layout.setSpacing(24)
        scope = self.scope_field = QWidget()
        scope.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        choices = QVBoxLayout(scope)
        choices.setContentsMargins(0, 0, 0, 0)
        choices.setSpacing(8)
        choices.addWidget(label("데이터 범위", 13, medium=True))
        modes = QHBoxLayout()
        modes.setSpacing(8)
        self.mode_group = QButtonGroup(self)
        self.all_files_button = button("전체 데이터")
        self.selected_files_button = button("가져온 파일로 선택")
        for mode in (self.all_files_button, self.selected_files_button):
            mode.setCheckable(True)
            mode.setProperty("queryChoice", True)
            self.mode_group.addButton(mode)
            modes.addWidget(mode)
        self.all_files_button.setChecked(True)
        choices.addLayout(modes)
        self.filter_layout.addWidget(scope)
        area = self.area_field = QWidget()
        area.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        filters = QVBoxLayout(area)
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(8)
        filters.addWidget(label("생산 영역", 13, medium=True))
        self.production_area = ComboBox()
        self.production_area.setProperty("queryField", True)
        self.production_area.setMinimumHeight(42)
        self.production_area.setStyleSheet(import_combo_style())
        set_widget_font(self.production_area, font(14))
        self.production_area.setAccessibleName("내보낼 생산 영역")
        self.production_area.setToolTip("내보낼 생산 영역")
        for caption, value in (("전체", None), ("Memory", "MEMORY"), ("Foundry", "FOUNDRY")):
            self.production_area.addItem(caption, value)
        self.production_area.setMinimumWidth(180)
        filters.addWidget(self.production_area)
        self.filter_layout.addWidget(area)
        self.filter_layout.addStretch()
        card.body.addLayout(self.filter_layout)
        self._add_file_list(card)
        return card

    def _sql_panel(self):
        card = Card(padding=20)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        card.setProperty("outlined", True)
        card.body.addWidget(label("SETRON 관리 DB 전체 백업", 15, medium=True))
        card.body.addWidget(_separator())
        for caption, value in (
            ("데이터 범위", "SETRON 관리 테이블 전체"),
            ("저장 내용", "테이블 구조 · 누적 데이터"),
            ("파일 형식", "SQL 파일 (.sql)"),
            ("복원 방법", "SETRON의 SQL 가져오기 또는 빈 DB에서 실행"),
        ):
            row = QHBoxLayout()
            row.setSpacing(20)
            name = label(caption, 13, color=SECONDARY)
            name.setMinimumWidth(name.fontMetrics().horizontalAdvance("데이터 범위") + 24)
            row.addWidget(name)
            row.addWidget(label(value, 13, wrap=True), 1)
            card.body.addLayout(row)
        return card

    def _add_actions(self):
        self.action_bar = QWidget(self)
        self.action_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        body = QVBoxLayout(self.action_bar)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        self.action_hint = label("", 12, color=SECONDARY, wrap=True)
        set_color_style(self.action_hint, lambda colors: (
            f"color:{colors['muted'] if not self.action_hint.property('selectionError') else colors['dashboard_risk_marker']};"))
        body.addWidget(self.action_hint)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch()
        self.buttons = {}
        for key, text in (("sql", "SQL 내보내기"), ("summary", "Excel 내보내기")):
            action = button(text, primary=True)
            action.setAccessibleName(text)
            action.setEnabled(False)
            actions.addWidget(action)
            self.buttons[key] = action
        body.addLayout(actions)

    def _show_format(self, kind):
        if self._busy and kind != self.active_format:
            with QSignalBlocker(self.tabs):
                self.tabs.setCurrentIndex(0 if self.active_format == "summary" else 1)
            return
        self.active_format = kind
        self.tabs.setCurrentIndex(0 if kind == "summary" else 1)
        panel = self.panels[kind]
        previous = self.action_bar.parentWidget()
        if previous is not panel:
            if previous in self.panels.values():
                previous.body.removeWidget(self.action_bar)
            panel.body.addWidget(self.action_bar)
        self.action_bar.show()
        for key, panel in self.panels.items():
            panel.setVisible(key == kind)
            self.buttons[key].setVisible(key == kind)
        self._sync_enabled()
        self._file_resize_timer.start(0)

    def _reflow(self):
        filter_width = (self.scope_field.minimumSizeHint().width() + self.area_field.minimumSizeHint().width()
                        + self.filter_layout.spacing() + 40)
        direction = QBoxLayout.Direction.TopToBottom if self.width() < filter_width else QBoxLayout.Direction.LeftToRight
        self.filter_layout.setDirection(direction)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def _add_file_list(self, card):
        self.file_panel = QWidget()
        self.file_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        body = QVBoxLayout(self.file_panel)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        toolbar = QHBoxLayout()
        self.file_count = label("0개 선택 / 전체 0개", 13)
        toolbar.addWidget(self.file_count, 1)
        self.select_all_files = button("전체 선택")
        self.select_all_files.setCheckable(True)
        self.select_all_files.setToolTip("목록의 모든 파일을 선택하거나 선택을 해제합니다.")
        self.reload_files = button("새로고침")
        self.reload_files.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.reload_files.setToolTip("가져온 파일 목록을 다시 불러옵니다.")
        for action in (self.select_all_files, self.reload_files):
            action.setProperty("queryChoice", True)
            set_widget_font(action, font(13, medium=True))
            toolbar.addWidget(action)
        body.addLayout(toolbar)
        self.file_status = label("파일 목록을 불러와 주세요.", 13, color=SECONDARY, wrap=True)
        body.addWidget(self.file_status)
        self.file_table = QTableWidget(0, 4)
        self.file_table.setAccessibleName("내보낼 가져온 파일 선택")
        self.file_table.setHorizontalHeaderLabels(("선택", "파일명", "자료 종류", "가져온 날짜"))
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.file_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.file_table.setAlternatingRowColors(False)
        self.file_table.setShowGrid(False)
        set_color_style(self.file_table, lambda colors: (
            "QTableWidget {"
            f"background:{colors['surface']};alternate-background-color:{colors['soft']};"
            f"color:{colors['ink']};border:1px solid {colors['line']};}}"
            "QTableWidget::item {"
            f"border-bottom:1px solid {colors['line']};padding:0 8px;}}"
            "QTableWidget::item:selected {"
            f"background:{colors['selected']};color:{colors['ink']};}}"
            "QHeaderView::section {"
            f"background:{colors['soft']};color:{colors['muted']};"
            f"border:0;border-bottom:1px solid {colors['line']};padding:8px;}}"
        ))
        self.file_table.verticalHeader().hide()
        self.file_table.verticalHeader().setDefaultSectionSize(48)
        set_widget_font(self.file_table, font(13))
        self.file_table.installEventFilter(self)
        self.file_table.viewport().installEventFilter(self)
        header = self.file_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.file_table.setColumnWidth(0, 52)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        body.addWidget(self.file_table)
        self.file_message = label("선택한 파일에서 가져온 현재 데이터와 연결 자료를 포함합니다.",
                                  12, color=SECONDARY, wrap=True)
        body.addWidget(self.file_message)
        card.body.addWidget(self.file_panel)
        self.file_panel.hide()
        self.select_all_files.clicked.connect(self._toggle_all_files)
        self.reload_files.clicked.connect(self.files_requested)

    def _change_mode(self, selected):
        self.file_panel.setVisible(selected)
        self._resize_file_list()
        self._sync_enabled()
        if selected and self._files_available:
            self.files_requested.emit()

    def selected_batch_ids(self):
        if self.all_files_button.isChecked():
            return None
        return tuple(self.file_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                     for row in range(self.file_table.rowCount())
                     if self.file_checks[row].isChecked())

    def set_file_support(self, available):
        self._files_available = available
        self._sync_enabled()

    def set_files_loading(self):
        self._files_loading = True
        self._files_error = False
        self.file_status.setText("가져온 파일 목록을 불러오는 중입니다…")
        self.file_status.show()
        self._sync_enabled()

    def set_area_choices(self, areas):
        selected = self.production_area.currentData()
        self.production_area.blockSignals(True)
        try:
            self.production_area.clear()
            self.production_area.addItem("전체", None)
            for code, text in areas.items():
                self.production_area.addItem(text, code)
            self.production_area.setCurrentIndex(max(0, self.production_area.findData(selected)))
        finally:
            self.production_area.blockSignals(False)
        self._sync_enabled()

    def set_files(self, files, *, error=False):
        self._files_loading = False
        self._files_error = error
        self.file_status.setText("파일 목록을 불러오지 못했습니다. 새로고침해 주세요." if error else
                                 "" if files else "현재 저장된 자료와 연결된 가져온 파일이 없습니다.")
        self.file_status.setVisible(bool(self.file_status.text()))
        if error:
            # 목록을 다시 확인하기 전에는 이전 선택으로 내보내지 않는다.
            files = ()
        selected = set(self.selected_batch_ids() or ())
        self.file_table.blockSignals(True)
        try:
            self.file_checks = []
            self.file_table.clearContents()
            self.file_table.setRowCount(len(files))
            for row, value in enumerate(files):
                identity = QTableWidgetItem()
                identity.setFlags(Qt.ItemFlag.ItemIsEnabled)
                identity.setData(Qt.ItemDataRole.UserRole, value.batch_id)
                self.file_table.setItem(row, 0, identity)
                check = filter_checkbox("")
                check.setStyleSheet(check.styleSheet() + "\nQCheckBox {spacing:0;padding:0;}")
                check.setAccessibleName(f"{value.filename} 선택")
                check.setChecked(value.batch_id in selected)
                check.toggled.connect(self._sync_enabled)
                self.file_checks.append(check)
                holder = QWidget()
                centered = QHBoxLayout(holder)
                centered.setContentsMargins(0, 0, 0, 0)
                centered.addWidget(check, alignment=Qt.AlignmentFlag.AlignCenter)
                self.file_table.setCellWidget(row, 0, holder)
                date = value.imported_at.strftime("%Y-%m-%d %H:%M:%S") if value.imported_at else "날짜 미기록"
                for column, text in enumerate((value.filename, value.kind, date), start=1):
                    item = QTableWidgetItem(text)
                    if column == 3:
                        item.setData(Qt.ItemDataRole.UserRole, date)
                    item.setToolTip(f"{value.filename}\n{value.kind}\n{date}"
                                    + (f"\n시트: {', '.join(value.sheets)}" if value.sheets else ""))
                    self.file_table.setItem(row, column, item)
        finally:
            self.file_table.blockSignals(False)
        self._resize_file_list()
        self._sync_enabled()

    def showEvent(self, event):
        super().showEvent(event)
        self._file_resize_timer.start(0)

    def eventFilter(self, watched, event):
        table = getattr(self, "file_table", None)
        if table is not None and watched is table.viewport() and event.type() == QEvent.Type.Resize:
            self._resize_file_columns(event.size().width())
        if ((watched is self._file_viewport and event.type() == QEvent.Type.Resize)
                or (table is not None and watched is table.viewport() and event.type() == QEvent.Type.Resize)
                or (watched is table and event.type() == QEvent.Type.FontChange)):
            self._file_resize_timer.start(0)
        return super().eventFilter(watched, event)

    def _resize_file_list(self):
        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        viewport = parent.viewport() if parent is not None else self.window()
        if viewport is not self._file_viewport:
            if self._file_viewport is not None:
                self._file_viewport.removeEventFilter(self)
            self._file_viewport = viewport
            viewport.installEventFilter(self)
        row_height = max(48, self.file_table.fontMetrics().height() + 20)
        self.file_table.verticalHeader().setDefaultSectionSize(row_height)
        header_height = self.file_table.horizontalHeader().sizeHint().height()
        # 목록은 화면의 약 35%만 사용하고 3~8개 행을 보여 준다. 나머지는 표 안에서 스크롤한다.
        limit = max(3, min(8, int((viewport.height() * .35 - header_height) / row_height)))
        visible = min(self.file_table.rowCount() or 3, limit)
        self.file_table.setFixedHeight(header_height + visible * row_height + self.file_table.frameWidth() * 2)
        self._resize_file_columns(self.file_table.viewport().width())
        self._reflow()

    def _resize_file_columns(self, width):
        compact = width < 580
        self.file_table.setColumnHidden(2, compact)
        metrics = self.file_table.fontMetrics()
        date_width = metrics.horizontalAdvance("2026-11-16" if compact else "2026-11-16 12:00:00") + 24
        kind_width = 0 if compact else metrics.horizontalAdvance("설비현황표") + 30
        available = max(0, width - self.file_table.columnWidth(0))
        self.file_table.setColumnWidth(1, max(0, available - kind_width - date_width))
        self.file_table.setColumnWidth(2, kind_width)
        self.file_table.setColumnWidth(3, date_width)
        for row in range(self.file_table.rowCount()):
            item = self.file_table.item(row, 3)
            if item is None:
                continue
            date = item.data(Qt.ItemDataRole.UserRole) or item.text()
            item.setText(date.replace(" ", "\n") if compact and date != "날짜 미기록" else date)

    def _toggle_all_files(self):
        count = self.file_table.rowCount()
        all_checked = count > 0 and all(check.isChecked() for check in self.file_checks)
        self._check_files(not all_checked)

    def _check_files(self, checked):
        for check in self.file_checks:
            with QSignalBlocker(check):
                check.setChecked(checked)
        self._sync_enabled()

    def _sync_enabled(self):
        allowed = not self._reasons["summary"] and not self._busy
        self.all_files_button.setEnabled(allowed)
        self.selected_files_button.setEnabled(allowed and self._files_available)
        self.production_area.setEnabled(allowed)
        self.reload_files.setEnabled(allowed and self._files_available and not self._files_loading)
        for control in (self.file_table, self.select_all_files):
            control.setEnabled(allowed and not self._files_loading)
        selected = self.selected_batch_ids()
        count = self.file_table.rowCount()
        checked = sum(check.isChecked() for check in self.file_checks)
        all_checked = count > 0 and checked == count
        self.select_all_files.setText("전체 선택 해제" if all_checked else "전체 선택")
        self.select_all_files.setChecked(all_checked)
        self.select_all_files.setMinimumWidth(self.select_all_files.fontMetrics().horizontalAdvance("전체 선택 해제") + 40)
        self.select_all_files.setEnabled(allowed and not self._files_loading and count > 0)
        self.file_count.setText(f"{checked}개 선택 / 전체 {count}개")
        selection = QItemSelection()
        for row, check in enumerate(self.file_checks):
            if check.isChecked():
                selection.select(self.file_table.model().index(row, 0), self.file_table.model().index(row, 3))
        self.file_table.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        selected_ready = self._files_available and bool(selected) and not self._files_loading
        self.buttons["summary"].setEnabled(allowed and (selected is None or selected_ready))
        self.buttons["sql"].setEnabled(not self._reasons["sql"] and not self._busy)
        self.tabs.setEnabled(not self._busy)
        reason = self._reasons[self.active_format] or self._busy
        if not reason and self.active_format == "summary" and selected is not None:
            if not self._files_available:
                reason = "파일 선택을 사용할 수 없습니다."
            elif self._files_loading:
                reason = "파일 목록을 불러오는 중입니다…"
            elif self._files_error:
                reason = "파일 목록을 새로고침해 주세요."
            elif not selected:
                reason = "내보낼 파일을 하나 이상 선택해 주세요."
        self.action_hint.setProperty("selectionError", bool(reason) and not self._busy)
        set_color_style(self.action_hint, self.action_hint._setron_color_style)
        self.action_hint.setText(reason)
        self.action_hint.setVisible(bool(reason))
        self._reflow()

    def set_availability(self, reasons, *, busy=""):
        self._reasons, self._busy = reasons, busy
        self._sync_enabled()
