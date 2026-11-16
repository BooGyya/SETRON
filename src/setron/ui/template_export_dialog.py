"""사용자가 선택한 Excel 양식의 입력 영역과 항목 연결을 검토한다."""

from dataclasses import asdict
import json
from pathlib import Path
import re

from PySide6.QtCore import QSettings, QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QMessageBox, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from setron.features.export.template import (
    TemplateColumn, TemplateConfig, TemplateRow, inspect_template, inspect_template_sheet, preview_template,
)
from setron.features.export.template_workbook import (
    SheetExportConfig, WorkbookTemplateConfig, preview_workbook_template,
)
from openpyxl.utils import column_index_from_string, get_column_letter
from setron.features.management_format import EQUIPMENT_HEADERS, RELEASE_HEADERS
from setron.infrastructure.database import DatabaseError
from setron.ui.combo_box import ComboBox
from setron.ui.components import button, filter_checkbox, label
from setron.ui.read_worker import BackgroundRead, ReadRequest


TITLES = {"equipment": "설비현황표", "release": "릴리즈노트"}


def _decode_config(payload):
    value = json.loads(payload) if isinstance(payload, str) else payload
    value = dict(value)
    if "sheets" in value:
        path = Path(value["path"])
        if not isinstance(value["sheets"], (list, tuple)) or not 1 <= len(value["sheets"]) <= 100:
            raise ValueError("저장된 시트별 조건을 확인할 수 없습니다.")
        sheets = tuple(SheetExportConfig(_decode_config(item["template"]), item.get("line"),
                                         item.get("production_area"), item.get("site")) for item in value["sheets"])
        if (any(not isinstance(item.template, TemplateConfig) for item in sheets)
                or value.get("empty_policy", "clear") not in {"clear", "keep", "error"}
                or len({item.template.sheet_name for item in sheets}) != len(sheets)
                or any(item.template.path.resolve() != path.resolve() for item in sheets)
                or any(part is not None and (not isinstance(part, str) or not part.strip())
                       for item in sheets for part in (item.line, item.production_area, item.site))):
            raise ValueError("저장된 시트별 조건을 확인할 수 없습니다.")
        return WorkbookTemplateConfig(path, sheets, value.get("empty_policy", "clear"))
    value["path"] = Path(value["path"])
    value["columns"] = tuple(TemplateColumn(**column) for column in value["columns"])
    value["rows"] = tuple(TemplateRow(**row) for row in value.get("rows", ()))
    config = TemplateConfig(**value)
    if (config.kind not in TITLES or config.mode not in {"rows", "columns", "lists"}
            or config.mode == "lists" and config.kind != "release"):
        raise ValueError("저장된 양식의 자료 종류와 배치를 확인할 수 없습니다.")
    headers = EQUIPMENT_HEADERS if config.kind == "equipment" else RELEASE_HEADERS
    transposed = config.orientation == "columns"
    mappings = config.rows if transposed else config.columns
    coordinates = [item.row if transposed else item.column for item in mappings]
    axis_values = ((config.header_column, config.start_column, config.end_column) if transposed else
                   (config.header_row, config.start_row, config.end_row))
    axis_limit, mapping_limit = (16_384, 1_048_576) if transposed else (1_048_576, 16_384)
    if (not isinstance(config.sheet_name, str) or not config.sheet_name
            or not isinstance(config.fingerprint, str) or not config.fingerprint
            or config.orientation not in {"rows", "columns"}
            or any(type(item) is not int for item in axis_values)
            or not 1 <= axis_values[0] < axis_values[1] <= axis_values[2] <= axis_limit
            or not mappings or len(set(coordinates)) != len(coordinates)
            or any(type(coordinate) is not int or not 1 <= coordinate <= mapping_limit
                   or item.field not in headers or not isinstance(item.qualifier, str)
                   for coordinate, item in zip(coordinates, mappings))
            or config.site is not None and (config.kind != "equipment" or not isinstance(config.site, str) or not config.site)):
        raise ValueError("저장된 양식 연결을 확인할 수 없습니다. 양식을 다시 선택해 주세요.")
    return config


class ColumnSpinBox(QSpinBox):
    def textFromValue(self, value):
        return get_column_letter(max(1, value))

    def valueFromText(self, text):
        return int(text) if text.isdecimal() else column_index_from_string(text.strip().upper())

    def validate(self, text, position):
        if not text:
            return QValidator.State.Intermediate, text, position
        try:
            valid = bool(re.fullmatch(r"[A-Za-z]{1,3}|[0-9]{1,5}", text)) and 1 <= self.valueFromText(text) <= 16_384
        except ValueError:
            valid = False
        return (QValidator.State.Acceptable if valid else QValidator.State.Invalid), text, position


class TemplateExportDialog(QDialog):
    def __init__(self, parent=None, *, records_loader, initial=None, settings=None, workbook_records_loader=None):
        super().__init__(parent)
        self.setWindowTitle("업로드한 Excel 양식에 내보내기")
        self.resize(1000, 780)
        self.settings = settings if settings is not None else QSettings("SETRON", "ExcelTemplates")
        self.records_loader = records_loader
        self.workbook_records_loader = workbook_records_loader
        self.records = self.metadata = self.sheet_metadata = None
        self.path = self.accepted_config = self.reviewed_config = None
        self.initial = None
        self.sheet_plans = {}
        self.dirty_sheets = set()
        self.reviewed_workbook = None
        self._initial_sheet = None
        self._restore_site = self._restore_line = self._restore_area = None
        self._initial_profile = initial
        self._preview_phase = "sheet"
        self._closed = False
        self._template_changed = False
        self.mapping_controls = []
        self._rendering = False
        self.records_request, self.file_request, self.preview_request = (
            ReadRequest(self), ReadRequest(self), ReadRequest(self))
        self.records_request.completed.connect(self._records_completed)
        self.file_request.completed.connect(self._file_completed)
        self.preview_request.completed.connect(self._preview_completed)
        self._file_phase = None
        self.header_timer = QTimer(self)
        self.header_timer.setSingleShot(True)
        self.header_timer.setInterval(250)
        self.header_timer.timeout.connect(self._read_sheet)

        outer = QVBoxLayout(self)
        outer.addWidget(label("사용자 Excel 양식", 20, medium=True))
        outer.addWidget(label("양식을 선택하고 입력할 영역과 항목을 연결하세요. 원본 파일은 유지하고 새 파일로 저장합니다.", wrap=True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(8, 8, 8, 8)
        file_row = QHBoxLayout()
        self.file_button = button("양식 업로드…")
        self.file_button.clicked.connect(self._choose_file)
        file_row.addWidget(self.file_button)
        self.file_label = label(".xlsx 파일을 선택해 주세요.", wrap=True)
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        file_row.addWidget(self.file_label, 1)
        body.addLayout(file_row)

        form = self.form = QFormLayout()
        self.kind_combo, self.sheet_combo, self.mode_combo, self.site_combo = ComboBox(), ComboBox(), ComboBox(), ComboBox()
        self.line_combo, self.area_combo, self.orientation_combo = ComboBox(), ComboBox(), ComboBox()
        self.site_combo.addItem("전체 사업장", None)
        self.site_combo.setEditable(True)
        self.line_combo.setEditable(True)
        self.line_combo.addItem("전체 라인", None)
        self.area_combo.addItem("전체 생산 영역", None)
        self.orientation_combo.addItem("항목이 가로 · 데이터는 아래로", "rows")
        self.orientation_combo.addItem("항목이 세로 · 데이터는 오른쪽으로", "columns")
        for key, title in TITLES.items():
            self.kind_combo.addItem(title, key)
        self.header_row, self.start_row, self.end_row = QSpinBox(), QSpinBox(), QSpinBox()
        for control in (self.header_row, self.start_row, self.end_row):
            control.setRange(1, 1_048_576)
            control.setGroupSeparatorShown(True)
        self.start_row.setValue(2)
        self.end_row.setValue(2)
        self.header_column, self.start_column, self.end_column = ColumnSpinBox(), ColumnSpinBox(), ColumnSpinBox()
        for control in (self.header_column, self.start_column, self.end_column):
            control.setRange(1, 16_384)
        self.start_column.setValue(2)
        self.end_column.setValue(2)
        for title, control in (("채울 시트", self.sheet_combo), ("자료 종류", self.kind_combo), ("사업장", self.site_combo),
                               ("라인", self.line_combo), ("생산 영역", self.area_combo), ("입력 방향", self.orientation_combo),
                               ("머리글 행", self.header_row), ("입력 시작 행", self.start_row),
                               ("입력 마지막 행", self.end_row), ("항목명이 있는 열", self.header_column),
                               ("입력 시작 열", self.start_column), ("입력 마지막 열", self.end_column), ("배치 방식", self.mode_combo)):
            form.addRow(title, control)
            control.setAccessibleName(title)
        body.addLayout(form)
        self.mode_help = label("", wrap=True)
        body.addWidget(self.mode_help)
        body.addWidget(label("시트 이름으로 범위를 추측하지 않습니다. 조건은 시트마다 직접 지정하세요. 입력 영역의 연결된 셀만 교체하고 다른 내용·수식은 유지합니다.", wrap=True))
        body.addWidget(label("내보내기 화면에서 선택한 파일·생산 영역 범위 안에서 시트 조건을 적용합니다. 릴리즈노트의 사업장·라인 조건은 해당 위치 설비의 SW 계열을 기준으로 적용합니다.", wrap=True))

        self.mapping_table = QTableWidget(0, 4)
        self.mapping_table.setAccessibleName("양식 열과 저장 항목 연결")
        self.mapping_table.setHorizontalHeaderLabels(("양식 열", "원본 머리글", "채울 항목", "PM / 버전 구분"))
        self.mapping_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mapping_table.verticalHeader().hide()
        self.mapping_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for index, width in ((0, 80), (2, 200), (3, 190)):
            self.mapping_table.setColumnWidth(index, width)
        self.mapping_table.setMinimumHeight(235)
        body.addWidget(self.mapping_table)
        self.preview_button = button("채워질 결과 미리보기")
        self.preview_button.clicked.connect(self._preview)
        body.addWidget(self.preview_button)
        self.preview_table = QTableWidget()
        self.preview_table.setAccessibleName("양식에 채워질 데이터 미리보기")
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.preview_table.setMinimumHeight(175)
        body.addWidget(self.preview_table)
        self.status = label("내보낼 현재 자료를 읽고 있습니다…", wrap=True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.status)
        self.region_confirm = filter_checkbox("미리보기와 입력 영역·항목 연결을 확인했습니다.")
        self.region_confirm.toggled.connect(self._sync)
        body.addWidget(self.region_confirm)
        plan_actions = QHBoxLayout()
        self.add_sheet_button, self.remove_sheet_button = button("이 시트 설정 추가·갱신"), button("선택 시트 설정 제외")
        self.preview_all_button = button("설정한 시트 전체 미리보기")
        self.add_sheet_button.clicked.connect(self._add_sheet)
        self.remove_sheet_button.clicked.connect(self._remove_sheet)
        self.preview_all_button.clicked.connect(self._preview_all)
        for action in (self.add_sheet_button, self.remove_sheet_button, self.preview_all_button):
            plan_actions.addWidget(action)
        body.addLayout(plan_actions)
        self.plans_table = QTableWidget(0, 4)
        self.plans_table.setAccessibleName("한 파일에 채울 시트별 설정")
        self.plans_table.setHorizontalHeaderLabels(("시트", "자료·방향", "시트별 조건", "상태"))
        self.plans_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.plans_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.plans_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.plans_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.plans_table.setMinimumHeight(135)
        self.plans_table.cellDoubleClicked.connect(self._open_plan)
        body.addWidget(label("여러 시트는 각각 설정을 추가한 뒤 전체 미리보기를 확인하세요. 데이터가 없는 시트는 연결된 입력 셀을 비우고 저장 결과에 안내합니다.", wrap=True))
        body.addWidget(self.plans_table)
        self.remember = filter_checkbox("이 양식의 설정을 다음에도 사용")
        self.remember.setChecked(True)
        body.addWidget(self.remember)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = button("취소")
        cancel.clicked.connect(self.reject)
        cancel.setDefault(True)
        self.use_button = button("이 양식 사용", primary=True)
        self.use_button.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(self.use_button)
        outer.addLayout(actions)

        self.kind_combo.currentIndexChanged.connect(self._kind_changed)
        self.sheet_combo.currentIndexChanged.connect(self._sheet_changed)
        self.orientation_combo.currentIndexChanged.connect(self._orientation_changed)
        self.header_row.valueChanged.connect(self._header_changed)
        self.header_column.valueChanged.connect(self._header_changed)
        for control in (self.start_row, self.end_row, self.start_column, self.end_column):
            control.valueChanged.connect(self._invalidate)
        self.mode_combo.currentIndexChanged.connect(lambda _: self._mode_changed(recommend=True))
        self.site_combo.currentTextChanged.connect(self._site_changed)
        self.line_combo.currentTextChanged.connect(self._invalidate)
        self.area_combo.currentIndexChanged.connect(self._invalidate)
        self._kind_changed()
        self._show_orientation()
        self._sync()
        QTimer.singleShot(0, self, self._start)

    @property
    def busy(self):
        return self.records_request.busy or self.file_request.busy or self.preview_request.busy

    def _start(self):
        if self._closed:
            return
        self.records_request.start(BackgroundRead(self.records_loader))
        profile = self._initial_profile
        if profile is None:
            try:
                profile = _decode_config(self.settings.value("last", "null"))
            except (ValueError, TypeError, KeyError):
                profile = None
        self._apply_profile(profile)
        if self.initial is not None:
            self._load_file(self.initial.path)
        self._sync()

    def _apply_profile(self, profile):
        self.sheet_plans = ({item.template.sheet_name: item for item in profile.sheets}
                            if isinstance(profile, WorkbookTemplateConfig) else {})
        self.dirty_sheets.clear()
        self.reviewed_workbook = None
        self._initial_sheet = next(iter(self.sheet_plans.values()), None)
        self.initial = self._initial_sheet.template if self._initial_sheet else profile
        self._restore_site = ((self._initial_sheet.site or self.initial.site) if self._initial_sheet else
                              self.initial.site if self.initial else None)
        self._restore_line = self._initial_sheet.line if self._initial_sheet else None
        self._restore_area = self._initial_sheet.production_area if self._initial_sheet else None
        self._render_plans()

    def _records_completed(self, records, error):
        if error is not None:
            self._error(error)
        elif not isinstance(records, dict):
            self._error(ValueError("내보낼 현재 자료의 형식을 확인할 수 없습니다."))
        else:
            self.records = records
            selected_site = self._restore_site or self._choice(self.site_combo)
            choices = records.get("_scope_choices", {})
            self.site_combo.blockSignals(True)
            self.site_combo.clear()
            self.site_combo.addItem("전체 사업장", None)
            sites = choices.get("sites") or {row["사업장"] for row in records.get("설비현황표", ()) if row.get("사업장")}
            for site in sorted(sites):
                self.site_combo.addItem(site, site)
            self._select_site(selected_site)
            self.site_combo.blockSignals(False)
            self._set_line_choices(self._restore_line or self._choice(self.line_combo))
            with QSignalBlocker(self.area_combo):
                selected_area = self._restore_area or self._choice(self.area_combo)
                self.area_combo.clear()
                self.area_combo.addItem("전체 생산 영역", None)
                for code, text in choices.get("production_areas", {}).items():
                    self.area_combo.addItem(text, code)
                self._select_choice(self.area_combo, selected_area)
            self.status.setText("양식을 업로드하고 연결할 항목을 확인해 주세요.")
            self._build_mapping(preserve=True)
        self._sync()

    def _choose_file(self):
        selected, _ = QFileDialog.getOpenFileName(self, "내보내기에 사용할 Excel 양식", "", "Excel 양식 (*.xlsx)")
        if selected:
            try:
                profiles = json.loads(self.settings.value("profiles", "{}"))
                profile = _decode_config(profiles[str(Path(selected).resolve())])
            except (ValueError, TypeError, KeyError):
                profile = None
            self._apply_profile(profile)
            self._load_file(Path(selected))

    def _load_file(self, path):
        if self.file_request.busy:
            return
        self._invalidate(mark_plan=False)
        self.path = Path(path)
        self.metadata = self.sheet_metadata = None
        self.mapping_controls = []
        self.mapping_table.setRowCount(0)
        self.file_label.setText(str(self.path))
        self._file_phase = "book"
        self.file_request.start(BackgroundRead(lambda path=self.path: inspect_template(path)))
        self.status.setText("양식의 시트와 머리글을 확인하고 있습니다…")
        self._sync()

    def _file_completed(self, metadata, error):
        if error is not None:
            self._error(error)
            return
        if self._file_phase == "book":
            self.metadata = metadata
            self._template_changed = self.initial is not None and self.initial.fingerprint != metadata["fingerprint"]
            if self._template_changed:
                self.initial = None
                self.sheet_plans.clear()
                self.dirty_sheets.clear()
                self._render_plans()
            self._rendering = True
            self.sheet_combo.clear()
            for sheet in metadata["sheets"]:
                self.sheet_combo.addItem(sheet["name"], sheet)
            if self.initial is not None:
                index = self.sheet_combo.findText(self.initial.sheet_name)
                if index >= 0:
                    self.sheet_combo.setCurrentIndex(index)
                self.kind_combo.setCurrentIndex(self.kind_combo.findData(self.initial.kind))
                self._restore_editor(self.initial, self._initial_sheet)
            sheet = self.sheet_combo.currentData() or {}
            if self.initial is None:
                self.header_row.setValue(sheet.get("header_row", 1))
                self.header_column.setValue(sheet.get("header_column", 1))
            self._rendering = False
            self._read_sheet()
        else:
            self.sheet_metadata = metadata
            self._rendering = True
            self.start_row.setValue(self.initial.start_row if self.initial else self.header_row.value() + 1)
            self.end_row.setValue(self.initial.end_row if self.initial else max(self.start_row.value(), metadata["max_row"]))
            self.start_column.setValue(self.initial.start_column if self.initial else self.header_column.value() + 1)
            self.end_column.setValue(self.initial.end_column if self.initial else max(self.start_column.value(), metadata["max_column"]))
            self._rendering = False
            self._build_mapping()
            self.status.setText(("파일이 바뀌어 이전 연결을 초기화했습니다. " if self._template_changed else "")
                                + "추천한 항목과 PM·버전 구분을 확인한 뒤 미리보기를 실행해 주세요.")
            self.initial = None
            self._initial_sheet = None
            self._sync()

    def _restore_editor(self, config, plan=None):
        self.kind_combo.setCurrentIndex(self.kind_combo.findData(config.kind))
        self.orientation_combo.setCurrentIndex(self.orientation_combo.findData(config.orientation))
        self.header_row.setValue(config.header_row)
        self.header_column.setValue(config.header_column)
        self._select_site((plan.site if plan else None) or config.site)
        self._set_line_choices(plan.line if plan else None)
        self._select_choice(self.area_combo, plan.production_area if plan else None)
        self._set_mode_labels()
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(config.mode))
        self._show_orientation()

    def _sheet_changed(self, *_):
        if self._rendering:
            return
        plan = self.sheet_plans.get(self.sheet_combo.currentText())
        self.initial, self._initial_sheet = (plan.template, plan) if plan else (None, None)
        self._rendering = True
        if plan:
            self._restore_editor(plan.template, plan)
        else:
            metadata = self.sheet_combo.currentData() or {}
            self.header_row.setValue(metadata.get("header_row", 1))
            self.header_column.setValue(metadata.get("header_column", 1))
        self._rendering = False
        self._read_sheet()

    def _orientation_changed(self, *_):
        self._show_orientation()
        if not self._rendering:
            self._invalidate()
            self._read_sheet()

    def _show_orientation(self):
        transposed = self.orientation_combo.currentData() == "columns"
        for control in (self.header_row, self.start_row, self.end_row):
            self.form.setRowVisible(control, not transposed)
        for control in (self.header_column, self.start_column, self.end_column):
            self.form.setRowVisible(control, transposed)
        self.mapping_table.setHorizontalHeaderLabels(("양식 행" if transposed else "양식 열", "원본 항목명", "채울 항목", "PM / 버전 구분"))
        self.mapping_table.setAccessibleName("양식 행과 저장 항목 연결" if transposed else "양식 열과 저장 항목 연결")

    def _read_sheet(self, *_):
        if self._rendering or self.path is None or self.metadata is None or self.file_request.busy:
            return
        self._invalidate(mark_plan=False)
        self.sheet_metadata = None
        path, sheet, header = self.path, self.sheet_combo.currentText(), self.header_row.value()
        orientation, header_column = self.orientation_combo.currentData(), self.header_column.value()
        self._file_phase = "sheet"
        self.file_request.start(BackgroundRead(lambda: inspect_template_sheet(
            path, sheet, header, orientation=orientation, header_column=header_column)))
        self.status.setText("선택한 시트의 항목명을 읽고 있습니다…")
        self._sync()

    def _select_site(self, site):
        self._select_choice(self.site_combo, site)

    @staticmethod
    def _choice(combo):
        index = combo.currentIndex()
        return combo.currentData() if index >= 0 and combo.currentText() == combo.itemText(index) else combo.currentText()

    @staticmethod
    def _select_choice(combo, value):
        with QSignalBlocker(combo):
            index = combo.findData(value)
            if value is not None and index < 0:
                combo.addItem(f"{value} (현재 범위에 자료 없음)", value)
                index = combo.count() - 1
            combo.setCurrentIndex(max(0, index))

    def _set_line_choices(self, selected=None):
        choices = (self.records or {}).get("_scope_choices", {})
        site = self._choice(self.site_combo)
        lines = choices.get("lines_by_site", {}).get(site, ()) if site else choices.get("lines", ())
        if not lines:
            lines = sorted({row["라인"] for row in (self.records or {}).get("설비현황표", ())
                            if row.get("라인") and (site is None or row.get("사업장") == site)})
        with QSignalBlocker(self.line_combo):
            self.line_combo.clear()
            self.line_combo.addItem("전체 라인", None)
            for line in lines:
                self.line_combo.addItem(line, line)
            self._select_choice(self.line_combo, selected)

    def _site_changed(self, *_):
        if not self._rendering:
            self._set_line_choices(self._choice(self.line_combo))
            self._invalidate()

    def _header_changed(self):
        if not self._rendering:
            self._invalidate()
            self.header_timer.start()
            self._sync()

    def _set_mode_labels(self):
        previous = self.mode_combo.currentData() or "rows"
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        transposed = self.orientation_combo.currentData() == "columns"
        record_axis, field_axis = ("열", "행") if transposed else ("행", "열")
        if self.kind_combo.currentData() == "equipment":
            choices = ((f"PM마다 한 {record_axis}", "rows"),
                       (f"설비마다 한 {record_axis} · PM별 {field_axis}", "columns"))
        else:
            choices = ((f"버전·Item마다 한 {record_axis}", "rows"),
                       (f"Item마다 한 {record_axis} · 버전별 {field_axis}", "columns"),
                       (f"버전마다 Item 내용 목록을 별도 {field_axis}로", "lists"))
        for title, value in choices:
            self.mode_combo.addItem(title, value)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(previous)))
        self.mode_combo.blockSignals(False)

    def _kind_changed(self):
        if self._rendering:
            return
        self._set_mode_labels()
        self._build_mapping()
        self._mode_changed()

    def _mode_changed(self, *, recommend=False, mark_plan=True):
        if self._rendering:
            return
        self._invalidate(mark_plan=mark_plan)
        mode = self.mode_combo.currentData()
        wide = mode != "rows"
        transposed = self.orientation_combo.currentData() == "columns"
        if self.kind_combo.currentData() == "equipment":
            example = ("예: 설비 A | PM1 버전 v1 | PM2 버전 v2처럼 한 줄에 채웁니다. 각 버전 열에서 해당 PM을 선택하세요."
                       if wide else "예: ‘설비 A / PM1 / v1’, ‘설비 A / PM2 / v2’처럼 PM마다 서로 다른 줄에 채웁니다.")
        elif mode == "lists":
            example = "예: v1 열에는 v1의 Item 내용 목록, v2 열에는 v2의 목록을 각각 위에서부터 채웁니다. 열마다 해당 버전을 선택하세요."
            if recommend:
                for _, field, qualifier in self.mapping_controls:
                    if qualifier.currentText() != qualifier.itemText(0) and field.currentData() in {None, "적용 상태", "버전(원문)"}:
                        field.setCurrentIndex(field.findData("내용"))
        else:
            example = ("예: Item 내용 | v1 적용 상태 | v2 적용 상태처럼 버전별 열을 채웁니다. 각 열에서 해당 버전을 선택하세요."
                       if wide else "예: ‘v1 / Item 1 / 내용’, ‘v2 / Item 1 / 내용’처럼 버전과 Item마다 서로 다른 줄에 채웁니다.")
        if transposed:
            if self.kind_combo.currentData() == "equipment":
                example = ("설비마다 오른쪽의 새 열에 채우고, PM별 값은 연결한 행에 나누어 넣습니다. 각 행에서 해당 PM을 선택하세요."
                           if wide else "설비·PM 자료마다 오른쪽의 새 열에 채웁니다. 연결한 각 행에는 선택한 항목을 넣습니다.")
            elif mode == "lists":
                example = "버전마다 연결한 행에 Item 내용 목록을 왼쪽에서 오른쪽으로 채웁니다. 각 행에서 해당 버전을 선택하세요."
            else:
                example = ("Item마다 오른쪽의 새 열에 채우고, 버전별 값은 연결한 행에 나누어 넣습니다. 각 행에서 해당 버전을 선택하세요."
                           if wide else "버전·Item 자료마다 오른쪽의 새 열에 채웁니다. 연결한 각 행에는 선택한 항목을 넣습니다.")
        self.mode_help.setText(example + (" 모든 PM·버전에서 같은 값인 항목은 ‘PM·버전 공통’을 사용합니다." if wide else ""))
        for _, _, qualifier in self.mapping_controls:
            qualifier.setEnabled(wide and not self.busy)

    def _build_mapping(self, *, preserve=False):
        if self.sheet_metadata is None:
            return
        transposed = self.orientation_combo.currentData() == "columns"
        mapping_type = TemplateRow if transposed else TemplateColumn
        existing = {column: mapping_type(column, field.currentData(),
                    (qualifier.currentData() if qualifier.currentIndex() >= 0
                     and qualifier.currentText() == qualifier.itemText(qualifier.currentIndex())
                     else qualifier.currentText()) or "")
                    for column, field, qualifier in self.mapping_controls} if preserve else {}
        self._rendering = True
        self.mapping_controls = []
        columns = self.sheet_metadata.get("rows", ()) if transposed else self.sheet_metadata["columns"]
        self.mapping_table.setRowCount(len(columns))
        kind = self.kind_combo.currentData()
        headers = EQUIPMENT_HEADERS if kind == "equipment" else RELEASE_HEADERS
        records = (self.records or {}).get(TITLES[kind], ())
        qualifier_field = "PM" if kind == "equipment" else "버전(원문)"
        qualifiers = sorted({str(row[qualifier_field]) for row in records if row.get(qualifier_field) not in (None, "")})
        saved_mappings = (self.initial.rows if transposed else self.initial.columns) if self.initial else ()
        initial = ({item.row if transposed else item.column: item for item in saved_mappings} if self.initial else existing)
        for index, column in enumerate(columns):
            coordinate = column["row"] if transposed else column["column"]
            address = str(column["row"]) if transposed else column["letter"]
            self.mapping_table.setItem(index, 0, QTableWidgetItem(address))
            self.mapping_table.setItem(index, 1, QTableWidgetItem(column["title"]))
            field, qualifier = ComboBox(), ComboBox()
            field.addItem("채우지 않음", None)
            for header in headers:
                field.addItem(header, header)
            remembered = initial.get(coordinate)
            suggested = remembered.field if remembered else column.get("suggested_field")
            if kind == "release" and suggested == "SW 버전(원문)":
                suggested = "버전(원문)"
            elif kind == "equipment" and suggested == "버전(원문)":
                suggested = "SW 버전(원문)"
            field.setCurrentIndex(max(0, field.findData(suggested)))
            qualifier.setEditable(True)
            qualifier.addItem("PM·버전 공통", "")
            for value in qualifiers:
                qualifier.addItem(value, value)
            chosen = remembered.qualifier if remembered else column.get("qualifier", "")
            if chosen:
                found = qualifier.findData(chosen)
                if found >= 0:
                    qualifier.setCurrentIndex(found)
                else:
                    qualifier.setEditText(chosen)
            axis_label = address + ("행" if transposed else "열")
            field.setAccessibleName(f"{axis_label}에 채울 항목")
            qualifier.setAccessibleName(f"{axis_label}의 PM 또는 버전")
            self.mapping_table.setCellWidget(index, 2, field)
            self.mapping_table.setCellWidget(index, 3, qualifier)
            self.mapping_controls.append((coordinate, field, qualifier))
            field.currentIndexChanged.connect(self._invalidate)
            qualifier.currentTextChanged.connect(self._invalidate)
        self._rendering = False
        self._set_mode_labels()
        self._mode_changed(mark_plan=False)
        self._sync()

    def _config(self):
        columns = []
        transposed = self.orientation_combo.currentData() == "columns"
        mapping_type = TemplateRow if transposed else TemplateColumn
        for column, field, qualifier in self.mapping_controls:
            if field.currentData() is None:
                continue
            qualifier_value = (qualifier.currentData() if qualifier.currentIndex() >= 0
                               and qualifier.currentText() == qualifier.itemText(qualifier.currentIndex())
                               else qualifier.currentText())
            columns.append(mapping_type(column, field.currentData(),
                                          (qualifier_value or "") if self.mode_combo.currentData() != "rows" else ""))
        return TemplateConfig(self.path, self.sheet_combo.currentText(), self.kind_combo.currentData(),
                              self.header_row.value(), self.start_row.value(), self.end_row.value(),
                              self.mode_combo.currentData(), () if transposed else tuple(columns), self.sheet_metadata["fingerprint"],
                              self._choice(self.site_combo) if self.kind_combo.currentData() == "equipment" else None,
                              self.orientation_combo.currentData(), self.header_column.value(), self.start_column.value(),
                              self.end_column.value(), tuple(columns) if transposed else ())

    def _invalidate(self, *_, mark_plan=True):
        if self._rendering:
            return
        self.reviewed_config = None
        self.reviewed_workbook = None
        if mark_plan and self.sheet_combo.currentText() in self.sheet_plans:
            self.dirty_sheets.add(self.sheet_combo.currentText())
            self._render_plans()
        self.region_confirm.setChecked(False)
        self.preview_table.clear()
        self.preview_table.setRowCount(0)
        self._sync()

    def _preview(self):
        if self.busy or self.records is None or self.sheet_metadata is None:
            return
        config, records = self._config(), self.records
        self.reviewed_config = None
        self._preview_config = config
        self._preview_sheet = self._current_sheet_config(config)
        self._preview_phase = "sheet"
        if self.workbook_records_loader is not None or self._has_sheet_filters():
            workbook = WorkbookTemplateConfig(self.path, (self._preview_sheet,), "clear")
            self.preview_request.start(BackgroundRead(lambda: preview_workbook_template(self._load_scoped_records(workbook), workbook)))
        else:
            self.preview_request.start(BackgroundRead(lambda: preview_template(records, config)))
        self.status.setText("채워질 값과 병합 셀·수식·입력 영역을 확인하고 있습니다…")
        self._sync()

    def _preview_completed(self, result, error):
        if error is not None:
            self._error(error)
            return
        if self._preview_phase == "workbook":
            self.reviewed_workbook = self._preview_workbook
            summaries = result["sheets"]
            self._display_preview({"columns": ("시트", "출력 건수", "입력 범위", "처리"),
                                   "rows": tuple((item.get("sheet_name", item.get("sheet", "")), item["row_count"],
                                                  item["range"], "입력 영역 비움" if item["row_count"] == 0 else "채우기")
                                                 for item in summaries)})
            empty = result.get("empty_sheets", ())
            self.status.setText(f"{len(summaries)}개 시트 · 총 {result.get('record_count', 0):,}건\n"
                                + ("데이터 없음 · 입력 영역 비움: " + ", ".join(empty) if empty else "시트별 조건과 입력 범위를 확인해 주세요."))
            self._render_plans({item.get("sheet_name", item.get("sheet", "")): item for item in summaries})
            self._sync()
            return
        if "sheets" in result:
            result = result["sheets"][0]
        self.reviewed_config = self._preview_config
        self._reviewed_sheet = self._preview_sheet
        self._display_preview(result)
        if self.reviewed_config.kind == "equipment":
            scope = self._preview_sheet.site or self.reviewed_config.site or "전체 사업장"
            if self.reviewed_config.site is None and result.get("site_counts"):
                scope += " (" + " / ".join(result["site_counts"]) + ")"
        else:
            scope = self._preview_sheet.site or "선택 파일·생산 영역의 릴리즈노트"
        scope += (f" · 라인={self._preview_sheet.line}" if self._preview_sheet.line is not None else "")
        scope += (f" · 생산 영역={self._preview_sheet.production_area}" if self._preview_sheet.production_area is not None else "")
        count = result.get("record_count", result["row_count"])
        empty_note = "\n데이터 없음 · 이 시트의 연결된 입력 영역을 비웁니다." if count == 0 else ""
        self.status.setText(f"{scope} · 총 {count:,}건 · {result['range']} · 처음 5건 미리보기\n원본 양식은 변경하지 않습니다.{empty_note}")
        self._sync()

    def _display_preview(self, result):
        self.preview_table.setColumnCount(len(result["columns"]))
        self.preview_table.setHorizontalHeaderLabels(tuple(result["columns"]))
        self.preview_table.setRowCount(len(result["rows"]))
        for row_index, row in enumerate(result["rows"]):
            for column_index, value in enumerate(row):
                item = QTableWidgetItem("" if value is None else str(value))
                item.setToolTip(item.text())
                self.preview_table.setItem(row_index, column_index, item)
        labels = result.get("row_labels")
        self.preview_table.setVerticalHeaderLabels(tuple(labels) if labels else tuple(str(i + 1) for i in range(len(result["rows"]))))

    def _current_sheet_config(self, config=None):
        return SheetExportConfig(config or self._config(), self._choice(self.line_combo),
                                 self._choice(self.area_combo), self._choice(self.site_combo))

    def _has_sheet_filters(self):
        return (self._choice(self.line_combo) is not None or self._choice(self.area_combo) is not None
                or self.kind_combo.currentData() == "release" and self._choice(self.site_combo) is not None)

    def _load_scoped_records(self, workbook):
        if self.workbook_records_loader is not None:
            return self.workbook_records_loader(workbook)
        if any(item.line is not None or item.production_area is not None
               or item.template.kind == "release" and item.site is not None for item in workbook.sheets):
            raise ValueError("시트별 라인·생산 영역 미리보기에 필요한 DB 연결을 확인해 주세요.")
        return {item.template.sheet_name: self.records for item in workbook.sheets}

    def _workbook_config(self):
        return WorkbookTemplateConfig(self.path, tuple(self.sheet_plans.values()), "clear")

    def _add_sheet(self):
        if self.busy or self.reviewed_config is None or not self.region_confirm.isChecked():
            return
        plan = self._current_sheet_config()
        if self.reviewed_config != plan.template or self._reviewed_sheet != plan:
            self._invalidate()
            return
        self.sheet_plans[plan.template.sheet_name] = plan
        self.dirty_sheets.discard(plan.template.sheet_name)
        self.reviewed_config = self.reviewed_workbook = None
        self.region_confirm.setChecked(False)
        self._render_plans()
        self.status.setText(f"{plan.template.sheet_name} 설정을 추가했습니다. 다른 시트도 설정하거나 ‘설정한 시트 전체 미리보기’를 실행해 주세요.")
        self._sync()

    def _remove_sheet(self):
        if self.busy or not self.sheet_plans:
            return
        row = self.plans_table.currentRow()
        name = self.plans_table.item(row, 0).text() if row >= 0 else self.sheet_combo.currentText()
        self.sheet_plans.pop(name, None)
        self.dirty_sheets.discard(name)
        self.reviewed_workbook = None
        self.region_confirm.setChecked(False)
        self._render_plans()
        self._sync()

    def _open_plan(self, row, *_):
        if not self.busy and row >= 0:
            self.sheet_combo.setCurrentIndex(self.sheet_combo.findText(self.plans_table.item(row, 0).text()))

    def _render_plans(self, summaries=None):
        if not hasattr(self, "plans_table"):
            return
        self.plans_table.setRowCount(len(self.sheet_plans))
        for row, (name, plan) in enumerate(self.sheet_plans.items()):
            direction = "오른쪽으로" if plan.template.orientation == "columns" else "아래로"
            scope = " · ".join(value for value in (
                plan.site or plan.template.site or "전체 사업장",
                f"라인={plan.line}" if plan.line is not None else None,
                f"생산 영역={plan.production_area}" if plan.production_area is not None else None) if value)
            status = "수정됨 · 추가·갱신 필요" if name in self.dirty_sheets else "전체 미리보기 대기"
            if summaries and name in summaries:
                count = summaries[name]["row_count"]
                status = f"{count:,}건 채우기" if count else "데이터 없음 · 입력 영역 비움"
            for column, text in enumerate((name, TITLES[plan.template.kind] + " · " + direction, scope, status)):
                self.plans_table.setItem(row, column, QTableWidgetItem(text))
        self.region_confirm.setText("설정한 시트의 조건·입력 영역·미리보기를 확인했습니다." if self.sheet_plans
                                    else "미리보기와 입력 영역·항목 연결을 확인했습니다.")

    def _preview_all(self):
        if self.busy or not self.sheet_plans or self.dirty_sheets:
            return
        workbook = self._workbook_config()
        self.reviewed_config = self.reviewed_workbook = None
        self.region_confirm.setChecked(False)
        self._preview_workbook = workbook
        self._preview_phase = "workbook"
        self.preview_request.start(BackgroundRead(lambda: preview_workbook_template(self._load_scoped_records(workbook), workbook)))
        self.status.setText("설정한 모든 시트의 범위·데이터·수식 보호를 확인하고 있습니다…")
        self._sync()

    def _error(self, error):
        self.reviewed_config = None
        self.reviewed_workbook = None
        self.status.setText(str(error) if isinstance(error, (ValueError, OSError, DatabaseError))
                            else "양식 또는 현재 자료를 읽지 못했습니다. 파일과 DB 연결을 확인해 주세요.")
        self._sync()

    def _sync(self, *_):
        if not hasattr(self, "use_button"):
            return
        ready = not self.busy
        self.file_button.setEnabled(ready)
        for control in (self.kind_combo, self.sheet_combo, self.header_row, self.start_row, self.end_row, self.mode_combo,
                        self.orientation_combo, self.header_column, self.start_column, self.end_column,
                        self.site_combo, self.line_combo, self.area_combo):
            control.setEnabled(ready)
        for _, field, qualifier in self.mapping_controls:
            field.setEnabled(ready)
            qualifier.setEnabled(ready and self.mode_combo.currentData() != "rows")
        can_preview = ready and not self.header_timer.isActive() and self.records is not None and self.sheet_metadata is not None
        self.preview_button.setEnabled(can_preview)
        current_reviewed = self.reviewed_config is not None
        all_reviewed = self.reviewed_workbook is not None and not self.dirty_sheets
        self.region_confirm.setEnabled(ready and (current_reviewed or all_reviewed))
        self.add_sheet_button.setEnabled(ready and current_reviewed and self.region_confirm.isChecked())
        self.remove_sheet_button.setEnabled(ready and bool(self.sheet_plans))
        self.preview_all_button.setEnabled(ready and bool(self.sheet_plans) and not self.dirty_sheets)
        self.plans_table.setEnabled(ready)
        reviewed = all_reviewed if self.sheet_plans else current_reviewed
        self.use_button.setEnabled(ready and reviewed and self.region_confirm.isChecked())

    def accept(self):
        if self.busy or not self.region_confirm.isChecked():
            return
        if self.sheet_plans:
            if self.dirty_sheets or self.reviewed_workbook is None or self.reviewed_workbook != self._workbook_config():
                return
            self.accepted_config = self.reviewed_workbook
        else:
            if self.reviewed_config is None or self.reviewed_config != self._config() or self._reviewed_sheet != self._current_sheet_config():
                self._invalidate()
                return
            self.accepted_config = (WorkbookTemplateConfig(self.path, (self._reviewed_sheet,), "clear")
                                    if self.workbook_records_loader is not None or self._has_sheet_filters() else self.reviewed_config)
        if self.remember.isChecked():
            payload = json.loads(json.dumps(asdict(self.accepted_config), ensure_ascii=False, default=str))
            self.settings.setValue("last", json.dumps(payload, ensure_ascii=False))
            try:
                profiles = json.loads(self.settings.value("profiles", "{}"))
                if not isinstance(profiles, dict):
                    profiles = {}
            except (ValueError, TypeError):
                profiles = {}
            profile_key = str(self.accepted_config.path.resolve())
            profiles.pop(profile_key, None)
            profiles[profile_key] = payload
            profiles = dict(list(profiles.items())[-20:])
            self.settings.setValue("profiles", json.dumps(profiles, ensure_ascii=False))
            self.settings.sync()
        super().accept()

    def done(self, result):
        self._closed = True
        self.header_timer.stop()
        for request in (self.records_request, self.file_request, self.preview_request):
            request.cancel()
        super().done(result)


def choose_template_export(parent, exporter, *, production_area=None, batch_ids=None, initial=None):
    loader = getattr(exporter, "read_records", None)
    workbook_loader = getattr(exporter, "read_workbook_records", None)
    if not callable(loader):
        QMessageBox.warning(parent, "Excel 양식 선택", "양식 미리보기에 필요한 DB 연결을 확인해 주세요.")
        return None
    dialog = TemplateExportDialog(
        parent, records_loader=lambda: loader(production_area=production_area, batch_ids=batch_ids), initial=initial,
        workbook_records_loader=(lambda config: workbook_loader(
            config, production_area=production_area, batch_ids=batch_ids)) if callable(workbook_loader) else None)
    try:
        return dialog.accepted_config if dialog.exec() == QDialog.DialogCode.Accepted else None
    finally:
        dialog.deleteLater()
