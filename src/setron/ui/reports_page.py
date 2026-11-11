"""기간과 유형을 선택하고 Word·PDF 보고서를 내려받는 화면."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QSettings, QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QButtonGroup, QStyledItemDelegate, QVBoxLayout, QWidget

from setron.ui.chat_report_library import ChatReportLibrary
from setron.ui.chat_report_preview import PdfPreviewDialog
from setron.ui.components import button, label
from setron.ui.dashboard import DashboardGrid
from setron.ui.query_panel import QueryPanel
from setron.ui.query_state import QueryState
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.report_data import REPORT_TYPES, ReportRequest, generate_report
from setron.ui.report_export import pdf_document, save_generated_report
from setron.ui.report_widgets import GenerationDialog, report_pictures
from setron.ui.version_management_widgets import actions, card, combo, note, table


_TYPE_DESCRIPTIONS = {key: description for key, _, _, description, _ in REPORT_TYPES}


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
        self.result = None
        self.pictures = None
        self.generation_dialog = self.preview_dialog = None
        self.request = ReadRequest(self)
        self.request.completed.connect(self._generated)
        self.item_request = ReadRequest(self)
        self.item_request.completed.connect(self._items_loaded)
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
        self.query_panel = QueryPanel(self.state, period=True, show_group=False)
        self.query_panel.actions_row.hide()
        self.query_panel.reset_button.clicked.connect(self.reset_conditions)
        self.query_panel.search_button.clicked.connect(self.generate)
        self.start, self.end = self.query_panel.start_date, self.query_panel.end_date
        body.addWidget(self.query_panel)

        config = card("보고서 유형")
        types = QButtonGroup(self)
        self.type_buttons = {}
        for key, caption, *_ in REPORT_TYPES:
            choice = button(caption)
            choice.setCheckable(True)
            choice.setProperty("settingsOption", True)
            choice.clicked.connect(self._kind_changed)
            types.addButton(choice)
            self.type_buttons[key] = choice
        self.type_buttons["status"].setChecked(True)
        config.body.addWidget(DashboardGrid(list(self.type_buttons.values()), minimum_width=120, maximum_columns=5, spacing=8))
        self.type_note = note()
        config.body.addWidget(self.type_note)
        self.item = combo("조회할 Item", ((None, "Item 선택"),))
        self.item_label = note("조회할 Item")
        self.item_label.setBuddy(self.item)
        self.item.currentIndexChanged.connect(lambda: self.item.setToolTip(self.item.currentText()))
        config.body.addWidget(self.item_label)
        config.body.addWidget(self.item)
        self.generation_status = note()
        self.generation_status.hide()
        config.body.addWidget(self.generation_status)
        self.generate_button = button("보고서 생성", primary=True)
        self.generate_button.clicked.connect(self.generate)
        config.body.addWidget(actions(None, self.generate_button))
        body.addWidget(config)

        self.result_card = card("생성된 보고서")
        self.result_title = label("", 16, medium=True, wrap=True)
        self.result_scope = note()
        self.result_card.body.addWidget(self.result_title)
        self.result_card.body.addWidget(self.result_scope)
        self.download_buttons = {}
        for fmt, title in (("docx", "Word 다운로드"), ("pdf", "PDF 다운로드")):
            control = button(title, primary=fmt == "docx")
            control.clicked.connect(lambda checked=False, format_=fmt: self.open_preview(format_))
            self.download_buttons[fmt] = control
        self.result_card.body.addWidget(actions(None, *self.download_buttons.values()))
        self.result_card.hide()
        body.addWidget(self.result_card)

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
        self.reset_conditions()
        self.enter()

    @property
    def saving(self):
        return self.preview_dialog is not None

    @property
    def kind(self):
        return next(key for key, control in self.type_buttons.items() if control.isChecked())

    def enter(self):
        if self.state.catalog != self.source.catalog:
            self.query_panel.set_catalog(self.source.catalog)
        self._kind_changed()
        self._render_library()

    def reset_conditions(self):
        days = self.source.analysis_dates or self.source.dates
        with QSignalBlocker(self.start), QSignalBlocker(self.end):
            self.start.setDate(QDate.fromString(str(min(days, default=date.today())), "yyyy-MM-dd"))
            self.end.setDate(QDate.fromString(str(max(days, default=date.today())), "yyyy-MM-dd"))
        self.query_panel.reset_draft()
        self._notice("")

    def _notice(self, text):
        self.generation_status.setText(text)
        self.generation_status.setVisible(bool(text))

    def _kind_changed(self, *_):
        kind = self.kind
        description = _TYPE_DESCRIPTIONS[kind]
        if kind == "ai" and self.ai_provider() is None:
            description += " · 답변이 없으면 수치표만 포함"
        self.type_note.setText(description)
        item = kind == "item"
        self.item.setVisible(item)
        self.item_label.setVisible(item)
        if item and self.version_loader and self.item.count() == 1 and not self.item_request.busy:
            self.item_request.start(BackgroundRead(self.version_loader))

    def _items_loaded(self, result, error):
        if error is not None or result is None:
            self._notice("Item 목록을 읽지 못했습니다. 보고서 유형을 다시 선택해 주세요.")
            return
        previous = self.item.currentData()
        self.item.clear()
        self.item.addItem("Item 선택", None)
        for entry in result.items:
            item = entry.item
            caption = f"Item {item.item_no or item.item_id} · {item.content}"
            self.item.addItem(caption, item.item_id)
            self.item.setItemData(self.item.count() - 1, caption, Qt.ItemDataRole.ToolTipRole)
        self.item.setCurrentIndex(max(0, self.item.findData(previous)))

    def generate(self):
        if self.request.busy or self.saving:
            return False
        if not self.generation_allowed():
            self._notice("다른 화면의 저장·조회가 완료된 뒤 생성해 주세요.")
            return False
        try:
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
            kind = self.kind
            ai_answer = deepcopy(self.ai_provider()) if kind == "ai" else None
            caption = self.type_buttons[kind].text().replace("·", "_").replace(" ", "_")
            request = ReportRequest(kind, query, base, start, end, "docx", f"SETRON_{caption}_보고서_{end}",
                                    item_id=self.item.currentData(), ai_answer=ai_answer, numeric_only=ai_answer is None)
        except ValueError as error:
            self._notice(str(error))
            return False

        self.generate_button.setEnabled(False)
        self._notice("")
        dialog = self.generation_dialog = GenerationDialog(self.window())
        dialog.cancelled.connect(self.cancel_generation)
        dialog.open()
        source, loader = self.source, self.version_loader

        def load():
            versions = loader() if request.kind == "item" and loader else None
            return generate_report(source, request, versions)

        return self.request.start(BackgroundRead(load))

    def cancel_generation(self):
        self.request.cancel()
        self._close_generation()
        self._notice("보고서 생성을 취소했습니다.")

    def _close_generation(self):
        dialog, self.generation_dialog = self.generation_dialog, None
        if dialog is not None:
            dialog.accept()
            dialog.deleteLater()
        self.generate_button.setEnabled(True)

    def _generated(self, result, error):
        self._close_generation()
        if error is not None:
            self._notice("생성 실패 · " + str(error))
            return
        self.result, self.pictures = result, None
        self.result_title.setText(result.title)
        self.result_scope.setText(f"{result.request.start} ~ {result.request.end} · 관측 기준일 {result.request.base_date}\n"
                                  + result.scope)
        self.result_card.show()

    def open_preview(self, format_):
        if self.result is None or self.request.busy or self.saving:
            return None
        if self.pictures is None:
            self.pictures = report_pictures(self.result)
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
                    and Path(entry["path"]).suffix.lower() in {".xlsx", ".docx", ".pdf"}] if isinstance(values, list) else []
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
