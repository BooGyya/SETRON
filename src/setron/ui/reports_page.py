"""기간과 유형을 선택하고 Word·PDF 보고서와 히트맵 Excel을 내려받는 화면."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QSettings, QSignalBlocker, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QStyledItemDelegate, QVBoxLayout, QWidget

from setron.ui.chat_report_library import ChatReportLibrary
from setron.ui.chat_report_preview import PdfPreviewDialog
from setron.ui.appearance import readable_color, set_color_style
from setron.ui.components import button, label
from setron.ui.dashboard import DashboardGrid
from setron.ui.query_panel import QueryPanel
from setron.ui.query_state import QueryState
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.heatmap_grouping import GROUPING_MODES
from setron.ui.report_data import COMMON_CONTENTS, REPORT_TYPES, ReportRequest, generate_report, unavailable_report_contents
from setron.ui.report_export import pdf_document, save_generated_report
from setron.ui.report_heatmap_export import has_report_heatmaps, save_report_heatmaps
from setron.ui.report_widgets import GenerationDialog, ReportFormatDialog, ReportNoticeDialog, report_pictures
from setron.ui.import_dialogs import confirm_numbered_save
from setron.ui.management_integration import reserve_numbered_export, write_reserved_export
from setron.ui.theme import ASSETS, LINE, SECONDARY, font, set_widget_font
from setron.ui.version_management_widgets import actions, card, combo, note, table


_TYPE_DESCRIPTIONS = {
    "status": "선택 범위의 표준화 및 적용 현황",
    "upgrade": "업그레이드 필요 설비, 우선순위, 부족 단계, 판정 근거",
    "item": "선택 Item의 설비별 적용 결과, 판정 근거, 원본 문서 위치",
    "change": "관측일 사이 설비와 PM별 이전 버전 및 다음 버전",
    "ai": "최근 완료된 AI 답변의 요약, 원인 분석, 표, 주의사항",
}


def _content_check(caption):
    control = QCheckBox(caption)
    control.setAccessibleName(caption)
    caption_font = font(14)
    caption_font.setBold(True)
    set_widget_font(control, caption_font)
    tick = (ASSETS / "checkbox-tick.svg").as_posix()
    set_color_style(control, lambda t: (
        f"QCheckBox {{color:{readable_color(t['ink'], t['surface'])};background:transparent;spacing:7px;"
        "padding:2px;border:1px solid transparent;border-radius:4px;}"
        f"QCheckBox:focus {{color:{readable_color(t['accent'], t['surface'])};}}"
        f"QCheckBox:disabled {{color:{readable_color(t['muted'], t['surface'])};}}"
        f"QCheckBox::indicator {{width:16px;height:16px;border:1px solid {t['line']};border-radius:4px;background:{t['soft']};}}"
        f"QCheckBox::indicator:checked {{background:#3655c7;border-color:#3655c7;image:url({tick});}}"
        f"QCheckBox::indicator:disabled {{background:{t['soft']};border-color:{t['line']};}}"))
    return control


class SavedReportDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.column() == 0:
            # 줄바꿈 기회는 화면에만 넣어 파일명과 툴팁의 원문을 유지한다.
            option.text = option.text.replace("_", "_\u200b").replace(".", "\u200b.")
            option.textElideMode = Qt.TextElideMode.ElideMiddle
        elif index.column() == 1:
            option.displayAlignment = Qt.AlignmentFlag.AlignCenter


class ReportsPage(QWidget):
    def __init__(self, source, *, targets_provider=lambda: None, version_loader=None,
                 ai_provider=lambda: None, generation_allowed=lambda: True, settings=None, ai_settings=None):
        super().__init__()
        self.source, self.targets_provider = source, targets_provider
        self.version_loader, self.ai_provider = version_loader, ai_provider
        self.generation_allowed = generation_allowed
        self._controls_ready = False
        self.result = None
        self._generation_query = None
        self.pictures = None
        self.generation_dialog = self.preview_dialog = None
        self.preparing_dialog = None
        self.format_dialog = self.alert_dialog = None
        self._choosing_excel = False
        self._excel_report = None
        self._item_load_error = ""
        self.request = ReadRequest(self)
        self.request.completed.connect(self._generated)
        self.item_request = ReadRequest(self)
        self.item_request.completed.connect(self._items_loaded)
        self.excel_request = ReadRequest(self)
        self.excel_request.completed.connect(self._excel_saved)
        self.settings = settings if settings is not None else QSettings("SETRON", "Reports")
        self.ai_library = ChatReportLibrary(settings=ai_settings)
        self.ai_library.setParent(self)
        self.ai_library.hide()
        self.saved_entries = self._read_saved()

        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.state = QueryState(source.catalog)
        self.query_panel = QueryPanel(self.state, period=True, show_group=True)
        self.query_panel.actions_row.hide()
        self.query_panel.reset_button.clicked.connect(self.reset_conditions)
        self.query_panel.search_button.clicked.connect(self.search)
        self.start, self.end = self.query_panel.start_date, self.query_panel.end_date
        body.addWidget(self.query_panel)

        config = self.config_card = card()
        header = QWidget()
        header_body = QHBoxLayout(header)
        header_body.setContentsMargins(0, 0, 0, 0)
        header_body.setSpacing(12)
        self.contents_title = label("보고서 포함 내용", 18, medium=True, wrap=True)
        header_body.addWidget(self.contents_title, 1, Qt.AlignmentFlag.AlignVCenter)
        self.select_all_button = button("전체 선택")
        self.select_all_button.clicked.connect(lambda: self.select_contents(not self._all_contents_checked()))
        header_body.addWidget(self.select_all_button, 0, Qt.AlignmentFlag.AlignVCenter)
        config.body.addWidget(header)
        contents = QWidget()
        contents_body = QVBoxLayout(contents)
        contents_body.setContentsMargins(0, 4, 0, 0)
        contents_body.setSpacing(8)

        def divider(*, vertical=False):
            line = QFrame()
            line.setObjectName("reportContentsDivider")
            if vertical:
                line.setFixedWidth(1)
            else:
                line.setFixedHeight(1)
            line.setStyleSheet(f"QFrame#reportContentsDivider {{background:{LINE};border:0;}}")
            return line

        contents_body.addWidget(divider())
        contents_columns = QHBoxLayout()
        contents_columns.setSpacing(16)
        common = QWidget()
        common.setMinimumWidth(0)
        common_body = QVBoxLayout(common)
        common_body.setContentsMargins(0, 0, 0, 0)
        common_body.setSpacing(8)
        common_body.addWidget(label("공통 내용", 15, medium=True))
        self.content_checks = {}

        def option(layout, caption, description):
            row = QWidget()
            row_body = QHBoxLayout(row)
            row_body.setContentsMargins(0, 0, 0, 0)
            row_body.setSpacing(8)
            control = _content_check(caption)
            control.setAccessibleDescription(description)
            control.toggled.connect(self._kind_changed)
            row_body.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
            row_body.addWidget(label("|", 13, color=SECONDARY), 0, Qt.AlignmentFlag.AlignVCenter)
            text = label(description, 12, color=SECONDARY, wrap=True)
            text.setMinimumWidth(0)
            row_body.addWidget(text, 1)
            layout.addWidget(row)
            return control, text

        for key, caption, description in COMMON_CONTENTS:
            if key == "heatmap":
                self.heatmap_options = QWidget()
                map_body = QVBoxLayout(self.heatmap_options)
                map_body.setContentsMargins(0, 0, 0, 0)
                map_body.setSpacing(6)
                self.heatmap_title = label("히트맵", 14)
                title_font = font(14)
                title_font.setBold(True)
                set_widget_font(self.heatmap_title, title_font)
                map_body.addWidget(self.heatmap_title)
                self.heatmap_checks = {mode: _content_check(title) for mode, title in GROUPING_MODES}
                for mode, control in self.heatmap_checks.items():
                    control.setAccessibleName(f"{control.text()}별 히트맵")
                    control.setChecked(mode == "group")
                    control.toggled.connect(self._kind_changed)
                choices = QWidget()
                choices_body = QVBoxLayout(choices)
                choices_body.setContentsMargins(24, 0, 0, 0)
                choices_body.addWidget(DashboardGrid(list(self.heatmap_checks.values()),
                                                    minimum_width=100, maximum_columns=5, spacing=6))
                map_body.addWidget(choices)
                common_body.addWidget(self.heatmap_options)
                continue
            control, _ = option(common_body, caption, description)
            control.setChecked(True)
            self.content_checks[key] = control
        common_body.addStretch()
        contents_columns.addWidget(common, 1)
        self.contents_divider = divider(vertical=True)
        contents_columns.addWidget(self.contents_divider)
        specific = QWidget()
        specific.setMinimumWidth(0)
        specific_body = QVBoxLayout(specific)
        specific_body.setContentsMargins(0, 0, 0, 0)
        specific_body.setSpacing(8)
        specific_body.addWidget(label("유형별 추가 내용", 15, medium=True))
        self.type_buttons = {}
        for key, caption, *_ in REPORT_TYPES:
            control, text = option(specific_body, caption, _TYPE_DESCRIPTIONS[key])
            self.type_buttons[key] = control
            if key == "item":
                self.item = combo("조회할 Item", ((None, "Item 선택"),))
                self.item.setStyleSheet(self.item.styleSheet() +
                    "QComboBox:disabled {color:#8b95a8;background:#f1f3f7;border:1px solid #e5eaf3;}")
                self.item_label = note("조회할 Item")
                self.item_label.setBuddy(self.item)
                self.item.currentIndexChanged.connect(lambda: self.item.setToolTip(self.item.currentText()))
                specific_body.addWidget(self.item_label)
                specific_body.addWidget(self.item)
            if key == "ai":
                self.ai_note = text
        self.type_buttons["status"].setChecked(True)
        specific_body.addStretch()
        contents_columns.addWidget(specific, 1)
        contents_body.addLayout(contents_columns)
        config.body.addWidget(contents)
        self.generation_status = note()
        self.generation_status.hide()
        config.body.addWidget(self.generation_status)
        self.generate_button = button("보고서 생성", primary=True)
        self.generate_button.clicked.connect(self.generate)
        config.body.addWidget(actions(None, self.generate_button))
        body.addWidget(config)

        library = card("저장한 보고서")
        self.library_model, self.library_table = table(
            ("보고서명", "유형", "조건", "생성일시", "열기"), "저장한 보고서", (360, 132, 210, 152, 92))
        self.library_table.fit_to_viewport = True
        self.library_table.setItemDelegate(SavedReportDelegate(self.library_table))
        self.library_table.activated.connect(lambda index: self.open_saved(index.row()))
        library.body.addWidget(self.library_table)
        self.library_note = note()
        library.body.addWidget(self.library_note)
        body.addWidget(library)
        self._controls_ready = True
        self.reset_conditions()
        self.enter()

    @property
    def saving(self):
        return (self.preview_dialog is not None or self.preparing_dialog is not None or self.format_dialog is not None
                or self._choosing_excel or self.excel_request.busy)

    @property
    def kind(self):
        return next(iter(self.selected_types), "status")

    @property
    def selected_types(self):
        return tuple(key for key, control in self.type_buttons.items() if control.isChecked())

    @property
    def selected_contents(self):
        return tuple(key for key, *_ in COMMON_CONTENTS
                     if (any(c.isChecked() for c in self.heatmap_checks.values()) if key == "heatmap"
                         else self.content_checks[key].isChecked()))

    def _all_contents_checked(self):
        return all(c.isChecked() for c in (*self.content_checks.values(), *self.type_buttons.values(),
                                           *self.heatmap_checks.values()))

    def select_contents(self, checked):
        """선택 상태만 바꾼다. 저장된 파일이나 DB를 삭제하지 않는다."""
        for control in (*self.content_checks.values(), *self.type_buttons.values(), *self.heatmap_checks.values()):
            with QSignalBlocker(control):
                control.setChecked(checked)
        self._kind_changed()

    def open_period(self, kind: str, start, end) -> bool:
        """다른 화면(AI 분석)에서 '이 종류 · 이 기간으로' 보고서 메뉴를 채운다. 만들기는 사용자가 누른다 (11/11).

        param
        - kind: REPORT_TYPES의 키 (예: "status", "change").
        - start, end: 기간 (datetime.date).

        return
        - 채웠으면 True. 저장 중이거나 모르는 종류면 False (지금 조건을 그대로 둔다).
        """
        if self.saving or kind not in self.type_buttons:
            return False
        for key, control in self.type_buttons.items():
            with QSignalBlocker(control):
                control.setChecked(key == kind)
        with QSignalBlocker(self.start), QSignalBlocker(self.end):
            self.start.setDate(QDate(start.year, start.month, start.day))
            self.end.setDate(QDate(end.year, end.month, end.day))
        self._kind_changed()
        self._notice(f"AI 분석에서 요청한 기간 {start.isoformat()} ~ {end.isoformat()}으로 채웠어요. 형식을 고르고 만들어 주세요.")
        return True

    def enter(self):
        if self.state.catalog != self.source.catalog:
            self.query_panel.set_catalog(self.source.catalog)
        self._kind_changed()
        self._render_library()

    def invalidate_catalog(self):
        """계층이 바뀌면 이전 조건으로 만드는 중인 보고서와 미저장 미리보기를 폐기한다."""
        self.request.cancel()
        self._generation_query = None
        self._close_generation()
        self.result, self.pictures = None, None
        if self.state.catalog != self.source.catalog:
            self.query_panel.set_catalog(self.source.catalog)
        for name in ("format_dialog", "preview_dialog", "preparing_dialog"):
            dialog = getattr(self, name, None)
            if dialog is not None:
                dialog.reject()
        self._notice(self.state.selection_error or "조회 선택지가 변경되었습니다. 조건을 확인한 뒤 다시 생성해 주세요.")

    def reset_conditions(self):
        days = self.source.analysis_dates or self.source.dates
        with QSignalBlocker(self.start), QSignalBlocker(self.end):
            self.start.setDate(QDate.fromString(str(min(days, default=date.today())), "yyyy-MM-dd"))
            self.end.setDate(QDate.fromString(str(max(days, default=date.today())), "yyyy-MM-dd"))
        self.query_panel.sync_period_range()
        self.query_panel.reset_draft()
        self._notice("")

    def _notice(self, text):
        self.generation_status.setText(text)
        self.generation_status.setVisible(bool(text))

    def _alert(self, text):
        self._notice("")
        if self.alert_dialog is not None:
            return
        dialog = self.alert_dialog = ReportNoticeDialog(text, self.window())

        def finished(_):
            self.alert_dialog = None
            dialog.deleteLater()

        dialog.finished.connect(finished)
        dialog.open()

    def _kind_changed(self, *_):
        # 생성 중의 초기 체크 상태 신호에는 아직 다른 컨트롤이 없다.
        if not self._controls_ready:
            return
        self._update_bulk_button()
        item = "item" in self.selected_types
        self.item.setEnabled(item)
        self.item_label.setEnabled(item)
        if item and self.version_loader and self.item.count() == 1 and not self.item_request.busy:
            self._item_load_error = ""
            self.item_request.start(BackgroundRead(self.version_loader))

    def _update_bulk_button(self):
        caption = "전체 해제" if self._all_contents_checked() else "전체 선택"
        self.select_all_button.setText(caption)
        self.select_all_button.setAccessibleName(caption)

    def _items_loaded(self, result, error):
        if error is not None or result is None:
            self._item_load_error = "Item 목록을 읽지 못했습니다. Item 배포를 다시 선택해 목록을 불러와 주세요."
            return
        self._item_load_error = ""
        previous = self.item.currentData()
        self.item.clear()
        self.item.addItem("Item 선택", None)
        for entry in result.items:
            item = entry.item
            caption = f"Item {item.item_no or item.item_id} · {item.content}"
            self.item.addItem(caption, item.item_id)
            self.item.setItemData(self.item.count() - 1, caption, Qt.ItemDataRole.ToolTipRole)
        self.item.setCurrentIndex(max(0, self.item.findData(previous)))

    def _apply_scope(self):
        start, end = self.start.date().toPython(), self.end.date().toPython()
        if start > end:
            raise ValueError("시작 날짜는 종료 날짜와 같거나 앞서야 합니다.")
        if self.state.catalog != self.source.catalog:
            self.query_panel.set_catalog(self.source.catalog)
        days = self.source.analysis_dates or self.source.dates
        base = max((day for day in days if start <= day <= end), default=end)
        self.state.set_as_of(base)
        self.state.set_target_draft(self.targets_provider())
        query = self.query_panel.apply_draft()
        return (query, start, end, base) if query is not None else None

    def search(self):
        """상단 조회 조건만 적용하고 공통 내용은 기본 선택한다. 문서 생성은 별도 동작이다."""
        if self.request.busy or self.saving or self.alert_dialog is not None:
            return False
        if not self.generation_allowed():
            self._alert("다른 화면의 저장·조회가 완료된 뒤 검색해 주세요.")
            return False
        for control in (*self.content_checks.values(), *self.heatmap_checks.values()):
            with QSignalBlocker(control):
                control.setChecked(True)
        for control in self.type_buttons.values():
            with QSignalBlocker(control):
                control.setChecked(False)
        self._kind_changed()
        try:
            if self._apply_scope() is None:
                return False
        except ValueError as error:
            self._alert(str(error))
            return False
        self._notice("")
        return True

    def generate(self):
        if self.request.busy or self.saving or self.alert_dialog is not None:
            return False
        if not self.generation_allowed():
            self._alert("다른 화면의 저장·조회가 완료된 뒤 생성해 주세요.")
            return False
        try:
            scope = self._apply_scope()
            if scope is None:
                return False
            query, start, end, base = scope
            kinds = self.selected_types
            kind = self.kind
            if "item" in kinds and self.item_request.busy:
                raise ValueError("Item 목록을 불러오는 중입니다. 완료 후 다시 생성해 주세요.")
            if "item" in kinds and self._item_load_error:
                raise ValueError(self._item_load_error)
            if "item" in kinds and self.item.currentData() is None:
                raise ValueError("조회할 Item을 선택해 주세요.")
            ai_answer = deepcopy(self.ai_provider()) if "ai" in kinds else None
            if "ai" in kinds and ai_answer is None:
                raise ValueError("완료된 AI 답변이 없습니다. AI 분석에서 질문을 완료하거나 AI 분석 항목을 해제해 주세요.")
            caption = (self.type_buttons[kind].text().replace("·", "_").replace(" ", "_")
                       if len(kinds) == 1 else "맞춤")
            request = ReportRequest(kind, query, base, start, end, "docx", f"SETRON_{caption}_보고서_{end}",
                                    item_id=self.item.currentData(), ai_answer=ai_answer, numeric_only=ai_answer is None,
                                    contents=self.selected_contents,
                                    types=kinds, heatmap_modes=tuple(key for key, c in self.heatmap_checks.items() if c.isChecked()))
        except ValueError as error:
            self._alert(str(error))
            return False

        self.generate_button.setEnabled(False)
        self._notice("")
        dialog = self.generation_dialog = GenerationDialog(self.window())
        dialog.cancelled.connect(self.cancel_generation)
        dialog.open()
        source, loader = self.source, self.version_loader

        def load():
            versions = loader() if "item" in request.selected_types and loader else None
            return generate_report(source, request, versions)

        self._generation_query = query
        return self.request.start(BackgroundRead(load))

    def cancel_generation(self):
        self.request.cancel()
        self._generation_query = None
        self._close_generation()
        self._notice("보고서 생성을 취소했습니다.")

    def _close_generation(self):
        dialog, self.generation_dialog = self.generation_dialog, None
        if dialog is not None:
            dialog.accept()
            dialog.deleteLater()
        self.generate_button.setEnabled(not self._choosing_excel and not self.excel_request.busy)

    def _generated(self, result, error):
        self._close_generation()
        if self.state.catalog != self.source.catalog:
            self.invalidate_catalog()
            return
        query, self._generation_query = self._generation_query, None
        if query is not None and not self.state.is_current(query):
            self._notice(self.state.selection_error or "조회 조건이 변경되었습니다. 선택을 다시 확인한 뒤 보고서를 생성해 주세요.")
            return
        if error is not None:
            self._alert("보고서를 생성하지 못했습니다.\n" + str(error))
            return
        missing = unavailable_report_contents(result)
        if missing:
            self._alert("선택한 범위에 다음 내용이 없습니다.\n\n" + "\n".join(f"- {name}" for name in missing)
                        + "\n\n선택 항목이나 조회 조건을 변경한 뒤 다시 생성해 주세요.")
            return
        self.result, self.pictures = result, None
        self._show_formats()

    def _show_formats(self):
        dialog = self.format_dialog = ReportFormatDialog(self.window(), heatmaps=has_report_heatmaps(self.result))
        result = self.result

        def finished(_):
            self.format_dialog = None
            format_ = dialog.selected_format
            dialog.deleteLater()
            if format_:
                action = self.export_heatmaps if format_ == "xlsx" else lambda: self.open_preview(format_)
                QTimer.singleShot(0, self, lambda: action() if self.result is result else None)

        dialog.finished.connect(finished)
        dialog.open()

    def open_preview(self, format_):
        if format_ == "xlsx":
            return self.export_heatmaps()
        if format_ not in {"docx", "pdf"}:
            return None
        if self.result is None or self.request.busy or self.saving or self.alert_dialog is not None:
            return None
        if self.pictures is None:
            dialog = self.preparing_dialog = GenerationDialog(self.window())
            dialog.heading.setText("보고서 미리보기 준비 중")
            cancelled = []
            dialog.cancelled.connect(lambda: cancelled.append(True))
            dialog.open()

            def progress():
                QApplication.processEvents()
                if cancelled:
                    raise RuntimeError("보고서 미리보기 준비를 취소했습니다.")

            try:
                progress()
                self.pictures = report_pictures(self.result, progress=progress)
            except Exception as error:
                self._notice(str(error))
                return None
            finally:
                self.preparing_dialog = None
                dialog.accept()
                dialog.deleteLater()
        pictures = self.pictures
        report = replace(self.result, request=replace(self.result.request, format=format_))
        pdf = replace(report, request=replace(report.request, format="pdf"))
        dialog = self.preview_dialog = PdfPreviewDialog(
            report, filename=report.filename, parent=self.window(),
            preview_writer=lambda _, path: save_generated_report(pdf, path, pictures),
            document_factory=lambda _: pdf_document(pdf, pictures),
            download_writer=lambda path: save_generated_report(report, path, pictures),
        )

        def finished(_):
            self.preview_dialog = None
            if dialog.result is not None and dialog.result.saved:
                self._saved(dialog.result.path, report)
            dialog.deleteLater()

        dialog.finished.connect(finished)
        dialog.open()
        return dialog

    def export_heatmaps(self):
        """생성 당시 히트맵을 편집 가능한 Excel로 저장한다. 위젯은 워커에서 접근하지 않는다."""
        if self.result is None or self.request.busy or self.saving or self.alert_dialog is not None:
            return False
        report = self.result
        if not has_report_heatmaps(report):
            self._alert("Excel로 내보낼 히트맵이 없습니다. 히트맵을 선택한 뒤 다시 생성해 주세요.")
            return False
        self._choosing_excel = True
        self.generate_button.setEnabled(False)
        try:
            path, _ = QFileDialog.getSaveFileName(
                self, "히트맵 Excel 저장", f"{report.request.filename}_히트맵.xlsx", "Excel (*.xlsx)",
                options=QFileDialog.Option.DontConfirmOverwrite)
            if not path:
                return False
            if self.result is not report or self.state.catalog != self.source.catalog:
                self._notice("조회 선택지가 변경되었습니다. 보고서를 다시 생성한 뒤 저장해 주세요.")
                return False
            destination = reserve_numbered_export(
                path, ".xlsx", confirm_duplicate=lambda requested: confirm_numbered_save(self, requested))
            if not isinstance(destination, Path):
                if destination is not None:
                    self._notice(destination.message)
                return False
            if self.result is not report or self.state.catalog != self.source.catalog:
                destination.unlink(missing_ok=True)
                self._notice("조회 선택지가 변경되었습니다. 보고서를 다시 생성한 뒤 저장해 주세요.")
                return False
            self._excel_report = report

            def write():
                return write_reserved_export(lambda output: save_report_heatmaps(report, output), destination)

            started = self.excel_request.start(BackgroundRead(write))
            if not started:
                self._excel_report = None
                destination.unlink(missing_ok=True)
                return False
            self._notice("히트맵을 Excel로 저장하고 있습니다…")
            return True
        finally:
            self._choosing_excel = False
            self.generate_button.setEnabled(not self.excel_request.busy)

    def _excel_saved(self, result, error):
        report, self._excel_report = self._excel_report, None
        self.generate_button.setEnabled(True)
        if error is not None or result is None:
            self._notice("히트맵 Excel을 저장하지 못했습니다. " + (str(error) if error is not None else "다시 시도해 주세요."))
            return
        if not result.saved:
            self._notice(result.message)
            return
        self._saved(result.path, report)
        self._notice(f"히트맵을 Excel로 저장했습니다. {result.path}")

    def _saved(self, path, report):
        entry = {"path": str(path), "created": report.created.strftime("%Y-%m-%d %H:%M:%S"),
                 "kind": report.title, "scope": report.scope}
        self.saved_entries = [entry, *(e for e in self.saved_entries if e["path"] != str(path))]
        self.settings.setValue("files", json.dumps(self.saved_entries, ensure_ascii=False))
        self.settings.sync()
        self._render_library()
        if self.settings.status() != QSettings.Status.NoError:
            self.library_note.setText("파일은 저장됐지만 최근 보고서 목록 보관에 실패했습니다. " + str(path))

    def add(self, paths):
        self.ai_library.add(paths)
        self._render_library()

    def _read_saved(self):
        try:
            values = json.loads(self.settings.value("files", "[]"))
            return [entry for entry in values if isinstance(entry, dict) and isinstance(entry.get("path"), str)
                    and isinstance(entry.get("created"), str)
                    and Path(entry["path"]).suffix.lower() in {".docx", ".pdf", ".xlsx"}] if isinstance(values, list) else []
        except (ValueError, TypeError):
            return []

    def _render_library(self):
        known = {e["path"]: e for e in (*self.ai_library.entries, *self.saved_entries)}
        self.library_entries = sorted(known.values(), key=lambda entry: entry["created"], reverse=True)
        rows = []
        for entry in self.library_entries:
            path = Path(entry["path"])
            rows.append((path.name, entry.get("kind", "AI 분석"), entry.get("scope", "파일에서 확인"),
                         entry["created"], "열기" if path.is_file() else "파일 없음"))
        self.library_model.set_rows(rows)
        for index, row in enumerate(rows):
            control = button(row[-1])
            control.setEnabled(row[-1] == "열기")
            control.setAccessibleName(f"{row[0]} {row[-1]}")
            control.clicked.connect(lambda checked=False, i=index: self.open_saved(i))
            self.library_table.setIndexWidget(self.library_model.index(index, 4), control)
        self.library_table.resizeRowsToContents()
        height = (self.library_table.horizontalHeader().height() + self.library_table.verticalHeader().length()
                  + 2 * self.library_table.frameWidth())
        self.library_table.setFixedHeight(max(140, min(320, height)))
        self.library_note.setText("" if rows else "저장한 보고서가 없습니다.")

    def open_saved(self, row):
        if not 0 <= row < len(self.library_entries):
            return False
        path = Path(self.library_entries[row]["path"])
        if not path.is_file() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.library_note.setText("파일을 열지 못했습니다. 저장 위치를 확인해 주세요.")
            return False
        return True
