"""기존 내보내기 작업을 선택하는 독립 화면."""

from PySide6.QtCore import QEvent, QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QHeaderView, QHBoxLayout, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from setron.ui.components import Card, button, filter_checkbox, label
from setron.ui.combo_box import ComboBox
from setron.ui.theme import SECONDARY, font, set_widget_font


EXPORT_OPTIONS = (
    ("sql", "DB 전체 SQL", "데이터베이스 전체를 복원 가능한 SQL 파일로 저장합니다."),
    ("summary", "관리값 요약 Excel", "설비현황표와 릴리즈노트 두 시트를 Excel로 저장합니다."),
)


class DataExportPage(QWidget):
    files_requested = Signal()

    def __init__(self):
        super().__init__()
        self._files_available = False
        self._files_loading = False
        self._reasons = {"sql": "DB 연결이 필요합니다.", "summary": "DB 연결이 필요합니다."}
        self._busy = ""
        self.file_checks = []
        self._file_viewport = None
        self._file_resize_timer = QTimer(self)
        self._file_resize_timer.setSingleShot(True)
        self._file_resize_timer.timeout.connect(self._resize_file_list)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.buttons, self.details = {}, {}
        for key, title, description in EXPORT_OPTIONS:
            card = Card(padding=18)
            row = QHBoxLayout()
            row.setSpacing(16)
            captions = QVBoxLayout()
            captions.setSpacing(5)
            captions.addWidget(label(title, 16, medium=True, wrap=True))
            detail = label(description, 13, color=SECONDARY, wrap=True)
            captions.addWidget(detail)
            if key == "summary":
                choices = QHBoxLayout()
                self.mode_group = QButtonGroup(self)
                self.all_files_button = button("전체 내보내기")
                self.selected_files_button = button("가져온 파일 선택")
                for mode in (self.all_files_button, self.selected_files_button):
                    mode.setCheckable(True)
                    mode.setProperty("queryChoice", True)
                    self.mode_group.addButton(mode)
                    choices.addWidget(mode)
                self.all_files_button.setChecked(True)
                choices.addStretch()
                captions.addLayout(choices)
                filters = QHBoxLayout()
                filters.addWidget(label("분류", 13, color=SECONDARY))
                self.production_area = ComboBox()
                self.production_area.setProperty("queryField", True)
                self.production_area.setFixedHeight(38)
                set_widget_font(self.production_area, font(14))
                self.production_area.setAccessibleName("내보낼 Memory/Foundry")
                for caption, value in (("전체", None), ("Memory", "MEMORY"), ("Foundry", "FOUNDRY")):
                    self.production_area.addItem(caption, value)
                self.production_area.setMinimumWidth(180)
                filters.addWidget(self.production_area)
                filters.addStretch()
                captions.addLayout(filters)
            row.addLayout(captions, 1)
            action = button("내보내기", primary=True)
            action.setAccessibleName(f"{title} 내보내기")
            action.setEnabled(False)
            row.addWidget(action, alignment=Qt.AlignmentFlag.AlignVCenter)
            card.body.addLayout(row)
            if key == "summary":
                self._add_file_list(card)
            layout.addWidget(card)
            self.buttons[key], self.details[key] = action, detail
        self.selected_files_button.toggled.connect(self._change_mode)
        self._sync_enabled()

    def _add_file_list(self, card):
        self.file_panel = QWidget()
        body = QVBoxLayout(self.file_panel)
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(label("선택한 파일에서 가져온 자료의 현재 저장값을 출력합니다.", 13, color=SECONDARY, wrap=True))
        toolbar = QHBoxLayout()
        self.file_count = label("선택 0개", 13, color=SECONDARY)
        toolbar.addWidget(self.file_count, 1)
        self.select_all_files = button("전체 선택")
        self.select_all_files.setCheckable(True)
        self.select_all_files.setProperty("queryChoice", True)
        self.reload_files = button("새로고침")
        for action in (self.select_all_files, self.reload_files):
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
        self.file_table.setAlternatingRowColors(True)
        self.file_table.verticalHeader().hide()
        self.file_table.verticalHeader().setDefaultSectionSize(48)
        set_widget_font(self.file_table, font(13))
        self.file_table.installEventFilter(self)
        self.file_table.viewport().installEventFilter(self)
        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.file_table.setColumnWidth(0, 52)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        body.addWidget(self.file_table)
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
        self.file_status.setText("가져온 파일 목록을 불러오는 중입니다…")
        self.file_status.show()
        self._sync_enabled()

    def set_files(self, files, *, error=False):
        self._files_loading = False
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
        available = max(0, self.file_table.viewport().width() - self.file_table.columnWidth(0))
        filename_width, kind_width = int(available * .45), int(available * .20)
        for column, width in enumerate((filename_width, kind_width, available - filename_width - kind_width), start=1):
            self.file_table.setColumnWidth(column, width)

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
        self.file_count.setText(f"선택 {checked}개 / 전체 {count}개")
        self.buttons["summary"].setEnabled(allowed and (selected is None or bool(selected) and not self._files_loading))

    def set_availability(self, reasons, *, busy=""):
        self._reasons, self._busy = reasons, busy
        for key, _, description in EXPORT_OPTIONS:
            reason = reasons[key] or busy
            self.buttons[key].setEnabled(not reason)
            self.details[key].setText(f"{description} {reason}" if reason else description)
        self._sync_enabled()
