"""보관 원본과 현재 관리값을 구분하는 직접 수정·검토·묶음 저장 화면."""

from copy import deepcopy
from datetime import date
import json

from PySide6.QtCore import QDate, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from setron.features.aging.service import parse_thresholds
from setron.features.management import ApplicabilityEdit, RecordEdit
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.targets.schemas import CandidateConfirmation
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseConnectionError, DatabaseSaveError
from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.date_range import DatePickerEdit
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.management_data import (
    ENUMS, FIELD_NAMES, KINDS, OPTIONAL_IDS, OPTIONAL_TEXT, display_value,
    record_title, reference_options, safe_message,
)
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.theme import SECONDARY


def _identity(table, key):
    return table, json.dumps(key, ensure_ascii=False, sort_keys=True)


def _candidate_basis(candidate):
    locations = []
    for values in (candidate.source_reference or {}).values():
        for location in values if isinstance(values, list) else [values]:
            locations.append(location["sheet"] + "!" + ",".join(location["cells"]))
    source = candidate.source_text or "; ".join(locations) or "원본 출처 미확인"
    return f"적용: {candidate.applicability_basis}\n순서: {candidate.order_basis}\n출처: {source}"


class ValueEditor(QWidget):
    """일반 화면에 JSON·식별자를 노출하지 않는 관리 항목별 입력기."""

    def __init__(self, table, field, current, references, family_id=None):
        super().__init__()
        self.field, self.empty, self.kind = field, None, "text"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        if field == "setting_value":
            self.kind = "thresholds"
            self.warning, self.danger, self.unit = QSpinBox(), QSpinBox(), ComboBox()
            for number in (self.warning, self.danger):
                number.setRange(-2147483648, 2147483647)
            for value, text in (("YEAR", "년"), ("MONTH", "개월"), ("DAY", "일")):
                self.unit.addItem(text, value)
            values = current if isinstance(current, dict) else {}
            self.warning.setValue(values.get("warning", 1) if type(values.get("warning", 1)) is int else 1)
            self.danger.setValue(values.get("danger", 2) if type(values.get("danger", 2)) is int else 2)
            self.unit.setCurrentIndex(self.unit.findData(values.get("unit", "YEAR")))
            form = QFormLayout()
            form.addRow("주의 기간", self.warning)
            form.addRow("위험 기간", self.danger)
            form.addRow("기간 단위", self.unit)
            layout.addLayout(form)
        elif field == "target_group_ids":
            self.kind, self.editor = "groups", QListWidget()
            self.editor.setMaximumHeight(150)
            for key, text in reference_options(field, references):
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if key in (current or ()) else Qt.CheckState.Unchecked)
                self.editor.addItem(item)
            layout.addWidget(self.editor)
        elif field == "is_enabled":
            self.kind, self.editor = "boolean", QCheckBox("이 위험 지정을 사용")
            self.editor.setChecked(bool(current))
            layout.addWidget(self.editor)
        elif field in ENUMS or field in {"group_id", "family_id", "item_id"} or field.endswith("release_version_id"):
            self.kind, self.editor = "choice", ComboBox()
            self.editor.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.editor.setMinimumContentsLength(12)
            self.editor.setMinimumWidth(0)
            self.editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            options = ENUMS.get(field) or reference_options(field, references, family_id=family_id)
            optional = field in OPTIONAL_IDS and not (field == "group_id" and table == "equipment_pm")
            if optional and all(key is not None for key, _ in options):
                self.editor.addItem("미확인 / 값 없음", None)
            for key, text in options:
                self.editor.addItem(text, key)
            index = self.editor.findData(current)
            if index < 0:
                self.editor.addItem("현재 값 · 등록 자료 재확인 필요", current)
                index = self.editor.count() - 1
            self.editor.setCurrentIndex(index)
            layout.addWidget(self.editor)
        elif field in {"released_at", "version_order", "item_no"}:
            self.empty = QCheckBox("값 없음")
            self.empty.setChecked(current is None)
            layout.addWidget(self.empty)
            if field == "released_at":
                self.kind, self.editor = "date", DatePickerEdit()
                self.editor.setDateRange(QDate(1000, 1, 1), QDate(9999, 12, 31))
                self.editor.setCalendarPopup(True)
                self.editor.setDisplayFormat("yyyy-MM-dd")
                self.editor.setDate(QDate(current.year, current.month, current.day) if isinstance(current, date) else QDate.currentDate())
            else:
                self.kind, self.editor = "integer", QSpinBox()
                self.editor.setRange(-2147483648, 2147483647)
                self.editor.setValue(current if type(current) is int else 0)
            self.empty.toggled.connect(self.editor.setDisabled)
            self.editor.setDisabled(self.empty.isChecked())
            layout.addWidget(self.editor)
        else:
            if field in OPTIONAL_TEXT:
                self.empty = QCheckBox("값 없음")
                self.empty.setChecked(current is None)
                layout.addWidget(self.empty)
            if field in {"content", "risk_reason"}:
                self.kind, self.editor = "longtext", QPlainTextEdit(current or "")
                self.editor.setMinimumHeight(82)
                self.editor.setMaximumHeight(150)
            else:
                self.editor = QLineEdit(current or "")
                self.editor.setMaxLength(2147483647)
                self.editor.setMinimumHeight(36)
            if self.empty is not None:
                self.empty.toggled.connect(self.editor.setDisabled)
                self.editor.setDisabled(self.empty.isChecked())
            layout.addWidget(self.editor)
        self.setAccessibleName(FIELD_NAMES.get(field, "관리 항목") + " 수정값")

    def value(self):
        if self.empty is not None and self.empty.isChecked():
            return None
        if self.kind == "thresholds":
            return {"warning": self.warning.value(), "danger": self.danger.value(), "unit": self.unit.currentData()}
        if self.kind == "groups":
            return [self.editor.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.editor.count())
                    if self.editor.item(i).checkState() == Qt.CheckState.Checked] or None
        if self.kind == "choice":
            return self.editor.currentData()
        if self.kind == "boolean":
            return self.editor.isChecked()
        if self.kind == "date":
            value = self.editor.date()
            return date(value.year(), value.month(), value.day())
        if self.kind == "integer":
            return self.editor.value()
        return self.editor.toPlainText() if self.kind == "longtext" else self.editor.text()


class ManagementPage(QWidget):
    """모든 DB 작업은 워커에서 실행하고 검토한 수정 묶음만 저장한다."""

    save_started = Signal()
    saved = Signal(object)
    save_failed = Signal()
    save_uncertain = Signal()

    def __init__(self, gateway=None, *, context_provider=None, parent=None):
        super().__init__(parent)
        self.gateway, self.context_provider = gateway, context_provider or (lambda: RefreshContext())
        self.records, self.references, self.record = (), {}, None
        self.offset, self.has_more, self.pending, self.pending_applicability = 0, False, {}, {}
        self._applicability_before = {}
        self.state, self.locked, self.inputs = "edit", False, {}
        self._operation, self._target, self._loaded_once, self._reviewed = None, None, False, None
        self.request = ReadRequest(self)
        self.request.completed.connect(self._completed)
        self.operation_error = None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        self.status = StatusBadge("관리 데이터", "info")
        body.addWidget(self.status)
        self.notice = label("원본은 보존하고 현재 관리값만 수정합니다. 사업장·기준일·PM 위치는 고정입니다.", 13, color=SECONDARY, wrap=True)
        body.addWidget(self.notice)
        selector = QHBoxLayout()
        self.kind = ComboBox()
        for table, title in KINDS:
            self.kind.addItem(title, table)
        self.reload_button, self.previous, self.next = button("새로 조회"), button("이전"), button("다음")
        selector.addWidget(self.kind, 1)
        for widget in (self.reload_button, self.previous, self.next):
            selector.addWidget(widget)
        body.addLayout(selector)
        self.record_model = ReadTableModel(("관리 기록", "편집 상태"))
        self.record_table = read_table(self.record_model, "관리 기록 선택", (650, 200))
        self.record_table.setMinimumHeight(0)
        header = self.record_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        body.addWidget(self.record_table)
        self.title = label("수정할 기록을 선택해 주세요.", 18, medium=True, wrap=True)
        body.addWidget(self.title)
        self.original_model = ReadTableModel(("항목", "원본 위치", "보관 원본값"))
        self.original_notice = label("", 13, color=SECONDARY, wrap=True)
        body.addWidget(self.original_notice)
        self.original_table = read_table(self.original_model, "보관 원본 셀", (180, 230, 420))
        self.original_table.setMinimumHeight(0)
        body.addWidget(self.original_table)
        self.form = Card()
        self.form.body.addWidget(label("현재 관리값 · 수정값", 18, medium=True))
        self.fields_widget = QWidget()
        self.fields_layout = QFormLayout(self.fields_widget)
        self.fields_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.form.body.addWidget(self.fields_widget)
        self.add_button = button("수정 목록에 추가")
        self.form.body.addWidget(self.add_button)
        body.addWidget(self.form)
        self.applicability_card = self._applicability_panel()
        body.addWidget(self.applicability_card)
        self.queue_label = label("수정 목록 0건", medium=True)
        body.addWidget(self.queue_label)
        self.review_model = ReadTableModel(("기록", "항목", "현재 관리값", "수정값"))
        self.review_table = read_table(self.review_model, "수정 묶음 검토", (310, 160, 240, 240))
        self.review_table.setMinimumHeight(0)
        body.addWidget(self.review_table)
        actions = QHBoxLayout()
        self.clear_button, self.edit_button, self.review_button, self.save_button = (
            button("수정 목록 비우기"), button("계속 수정"), button("수정 묶음 검토"), button("검토한 묶음 저장", primary=True))
        for widget in (self.clear_button, self.edit_button, self.review_button, self.save_button):
            actions.addWidget(widget)
        body.addLayout(actions)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.reload_button.clicked.connect(self.refresh)
        self.previous.clicked.connect(lambda: self._page(-100))
        self.next.clicked.connect(lambda: self._page(100))
        self.record_table.clicked.connect(lambda index: self._select(index.row()))
        self.record_table.activated.connect(lambda index: self._select(index.row()))
        self.add_button.clicked.connect(self.add_current)
        self.clear_button.clicked.connect(self.clear_pending)
        self.review_button.clicked.connect(self.review)
        self.edit_button.clicked.connect(self.continue_editing)
        self.save_button.clicked.connect(self.save)
        self._render()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 공통 표의 폭 재배치가 끝난 뒤 실제 행 높이에 맞춰 관리 화면의 빈 공간만 줄인다.
        QTimer.singleShot(0, self._fit_tables)

    def _fit_tables(self):
        for table in (self.record_table, self.original_table, self.review_table):
            table.resizeRowsToContents()
            contents = table.horizontalHeader().height() + table.verticalHeader().length() + 4
            if table.horizontalHeader().length() > table.viewport().width():
                contents += table.horizontalScrollBar().sizeHint().height()
            height = max(100, min(340, contents))
            if table.minimumHeight() != height or table.maximumHeight() != height:
                table.setFixedHeight(height)
        self.original_table.setVisible(self.original_model.rowCount() > 0)
        self.review_table.setVisible(self.review_model.rowCount() > 0 and self.state in {"review", "saving", "unknown"})

    @property
    def busy(self):
        return self.request.busy

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded_once and self.gateway is not None:
            self.refresh()

    def refresh(self):
        if self.busy or self.locked or self.gateway is None:
            return False
        if not self._stage_current():
            return False
        table, offset = self.kind.currentData(), self.offset
        if self.record is not None and self.record.table == table and self._target is None:
            self._target = (table, deepcopy(self.record.key))
        return self._start("load", lambda: self.gateway.load(table, offset=offset))

    def open_record(self, table, key):
        if self.locked or self.state in {"review", "saving"} or self.gateway is None:
            return False
        if self.busy:
            if self._operation not in {"load", "get", "applicability"}:
                return False
            # 조회 워커는 종료시키지 않고 이전 응답 수락만 취소한다. 저장은 취소하지 않는다.
            self.request.cancel()
            self._operation = None
        if not self._stage_current():
            return False
        index = self.kind.findData(table)
        if index < 0:
            return False
        self.kind.blockSignals(True)
        self.kind.setCurrentIndex(index)
        self.kind.blockSignals(False)
        self._target, self.offset = (table, deepcopy(key)), 0
        self.record = None
        return self.refresh()

    def _start(self, operation, loader):
        self._operation = operation
        self.operation_error = None
        started = self.request.start(BackgroundRead(loader))
        self._render()
        return started

    def _completed(self, value, error):
        operation, self._operation = self._operation, None
        if error is not None:
            self.operation_error = error
            known_failure = isinstance(error, (ValueError, LookupError, DatabaseConnectionError, DatabaseSaveError))
            if operation == "save" and (isinstance(error, DatabaseCommitUnknownError) or not known_failure):
                self.state, self.locked = "unknown", True
                self.save_uncertain.emit()
                self.notice.setText("저장 결과를 확인하지 못했습니다. 다시 저장하지 말고 저장된 값을 확인한 뒤 앱을 다시 열어 주세요.")
            else:
                self.state = "failed" if operation == "save" else "edit"
                self.notice.setText(safe_message(error))
                if operation == "save":
                    self.save_failed.emit()
            self._render()
            return
        if operation == "load":
            self._loaded_once = True
            self.records, self.references, self.has_more = value.records, value.references, value.has_more
            self.record_model.set_rows(tuple((record_title(r, self.references), "편집 가능" if r.editable_fields else "위치별 편집 또는 전용 설정 사용")
                                            for r in self.records))
            self.record_table.resizeRowsToContents()
            self._fill_applicability_choices()
            if self._target is not None:
                table, key = self._target
                self._target = None
                self._start("get", lambda: self.gateway.get(table, key))
                return
            self.record = None
            if self.records:
                self._select(0)
            else:
                self._show_record(None)
            self.notice.setText("원본은 보존하고 선택한 기록의 현재 관리값만 수정합니다.")
        elif operation == "get":
            self._show_record(value)
        elif operation == "save":
            from setron.features.management import ManagementResult
            if not isinstance(value, ManagementResult):
                self.state, self.locked = "unknown", True
                self.save_uncertain.emit()
                self.notice.setText("저장 결과를 확인하지 못했습니다. 같은 요청을 다시 저장하지 마세요.")
                self._render()
                return
            self.pending.clear()
            self.pending_applicability.clear()
            self._applicability_before.clear()
            if self.record is not None:
                self._target = (self.record.table, deepcopy(self.record.key))
            self.record = None
            self.state, self._reviewed = "edit", None
            self.saved.emit(value)
            self.notice.setText("수정 묶음을 저장했습니다. 최신값을 다시 조회합니다.")
            self.refresh()
        elif operation == "orders":
            self._stage_order_candidates(value)
        elif operation == "applicability":
            self.applicability_view = value
            self.candidate_list.clear()
            pair = value.value
            candidates = {c.release_id: c.confirmation for c in pair.candidates}
            for key, text in reference_options("release_version_id", self.references, family_id=pair.family_id):
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setData(Qt.ItemDataRole.UserRole + 1, candidates.get(key))
                if key in candidates:
                    item.setToolTip(_candidate_basis(candidates[key]))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if key in candidates else Qt.CheckState.Unchecked)
                self.candidate_list.addItem(item)
            self.applicability_notice.setText("기존 근거는 유지됩니다. 선택·근거를 바꾸려면 세 근거 항목을 입력한 뒤 수정 목록에 추가하세요.")
        self._render()

    def _kind_changed(self):
        if self.busy or self.locked:
            return
        if not self._stage_current():
            if self.record is not None:
                self.kind.blockSignals(True)
                self.kind.setCurrentIndex(self.kind.findData(self.record.table))
                self.kind.blockSignals(False)
            return
        self.offset, self.record = 0, None
        self._show_record(None)
        self.refresh()

    def _page(self, delta):
        if self.busy or self.locked or not self._stage_current():
            return
        self.offset = max(0, self.offset + delta)
        self.record = None
        self._show_record(None)
        self.refresh()

    def _select(self, row):
        if self.busy or self.locked or self.state == "review" or not 0 <= row < len(self.records):
            return
        if not self._stage_current():
            return
        self._show_record(self.records[row])

    def _show_record(self, record):
        self.record = record
        self.inputs = {}
        while self.fields_layout.rowCount():
            self.fields_layout.removeRow(0)
        if record is None:
            self.title.setText("조회된 기록이 없습니다.")
            self.original_model.set_rows(())
            self.original_notice.setText("")
            self._render()
            return
        self.title.setText(record_title(record, self.references))
        self.original_model.set_rows(tuple((FIELD_NAMES.get(c.field, "원본 항목"), f"{c.sheet}!{c.address}",
                                           ("(빈 셀)" if c.value is None else str(c.value)) if c.available else "원본 셀 미확인") for c in record.original_cells))
        self.original_table.resizeRowsToContents()
        self.original_notice.setText("보관 원본 셀은 수정하지 않습니다." if record.original_cells else "보관 원본 셀 정보가 없습니다.")
        pending = self.pending.get(_identity(record.table, record.key))
        values = {**record.current, **(pending[1] if pending else {})}
        if not record.editable_fields:
            self.fields_layout.addRow(label("공유 SW는 PM 구성 · SW에서 수정할 위치를 선택하세요.", wrap=True))
        for field in record.editable_fields:
            editor = ValueEditor(record.table, field, values[field], self.references, values.get("family_id"))
            self.inputs[field] = editor
            row = QWidget()
            layout = QVBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(label("현재 관리값: " + display_value(field, record.current[field], self.references), 13, color=SECONDARY, wrap=True))
            layout.addWidget(editor)
            self.fields_layout.addRow(FIELD_NAMES[field], row)
        if "target_scope" in self.inputs and "target_group_ids" in self.inputs:
            scope = self.inputs["target_scope"].editor
            scope.currentIndexChanged.connect(lambda: self.inputs["target_group_ids"].setEnabled(scope.currentData() == "LISTED"))
            self.inputs["target_group_ids"].setEnabled(scope.currentData() == "LISTED")
        if "family_id" in self.inputs:
            self.inputs["family_id"].editor.currentIndexChanged.connect(self._family_changed)
        self._render()

    def _family_changed(self):
        """선택한 계열의 실제 릴리즈만 연결 선택지로 표시한다."""
        family_id = self.inputs["family_id"].value()
        for field, editor in self.inputs.items():
            if not field.endswith("release_version_id"):
                continue
            previous = editor.value()
            combo = editor.editor
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("미확인 / 값 없음", None)
            for key, text in reference_options(field, self.references, family_id=family_id):
                combo.addItem(text, key)
            combo.setCurrentIndex(max(0, combo.findData(previous)))
            combo.blockSignals(False)

    def _changes(self):
        if self.record is None:
            return {}
        values = {field: editor.value() for field, editor in self.inputs.items()}
        if "target_group_ids" in values and values.get("target_scope") != "LISTED":
            values["target_group_ids"] = None
        for field in ("equipment_code", "model_name", "version_raw", "content", "risk_reason"):
            if field in values and (not isinstance(values[field], str) or not values[field].strip()):
                raise ValueError(FIELD_NAMES[field] + ": 필수값을 입력해 주세요.")
        if "setting_value" in values:
            parse_thresholds(values["setting_value"])
        return {field: deepcopy(value) for field, value in values.items() if value != self.record.current[field]}

    def _stage_current(self):
        if self.record is None or self.state in {"review", "unknown"}:
            return True
        try:
            changes = self._changes()
        except ValueError as error:
            self.notice.setText(str(error))
            return False
        key = _identity(self.record.table, self.record.key)
        if changes:
            self.pending[key] = (self.record, changes)
        else:
            self.pending.pop(key, None)
        self._reviewed = None
        return True

    def add_current(self):
        if self.busy or self.locked or self.state == "review":
            return False
        if not self._stage_current():
            return False
        self.notice.setText("수정 목록에 반영했습니다. 묶음 검토 후 저장해 주세요.")
        self._render()
        return True

    def clear_pending(self):
        if self.busy or self.locked:
            return
        self.pending.clear()
        self.pending_applicability.clear()
        self._applicability_before.clear()
        self.state, self._reviewed = "edit", None
        self._show_record(self.record)

    def review(self):
        if self.busy or self.locked or not self._stage_current() or not (self.pending or self.pending_applicability):
            return False
        edits = tuple(RecordEdit(record.table, deepcopy(record.key), record.revision, deepcopy(changes))
                      for record, changes in self.pending.values())
        applicability = tuple(self.pending_applicability.values())
        rows = [(record_title(record, self.references), FIELD_NAMES[field],
                 display_value(field, record.current[field], self.references), display_value(field, value, self.references))
                for record, changes in self.pending.values() for field, value in changes.items()]
        for change in applicability:
            pair = (change.model_id, change.family_id)
            title = (display_value("group_id", change.model_id, self.references) + " · "
                     + display_value("family_id", change.family_id, self.references))
            previous = self._applicability_before[pair].value
            previous_candidates = {candidate.release_id: candidate.confirmation for candidate in previous.candidates}
            before = ", ".join(candidate.release.version_raw for candidate in previous.candidates) or "확인 후보 없음"
            after = ", ".join(display_value("release_version_id", candidate.release_id, self.references)
                              for candidate in change.candidates) or "모든 후보 제외"
            rows.append((title, "적용 가능한 릴리즈", before, after))
            for candidate in change.candidates:
                release = display_value("release_version_id", candidate.release_id, self.references)
                old = previous_candidates.get(candidate.release_id)
                rows.append((title, release + " 근거", _candidate_basis(old) if old is not None else "신규 후보",
                             _candidate_basis(candidate)))
        self.review_model.set_rows(tuple(rows))
        self.review_table.resizeRowsToContents()
        self._reviewed = deepcopy((edits, applicability))
        self.state = "review"
        self.notice.setText("묶음 전체를 확인하세요. 한 항목이라도 검증에 실패하면 모두 저장하지 않습니다.")
        self._render()
        return True

    def continue_editing(self):
        if self.state == "review" and not self.busy:
            self.state, self._reviewed = "edit", None
            self._render()

    def open_risk_dialog(self, dialog_factory=None):
        """위험 기준 등록 창을 열고, 등록을 누르면 저장한다. 수정 목록과 섞지 않고 바로 한 건 저장한다."""
        if self.busy or self.locked or self.gateway is None or self.state in {"review", "saving"} or not self.references:
            self.notice.setText("관리 자료를 불러온 뒤, 검토 중인 수정 묶음이 없을 때 위험 기준을 등록할 수 있습니다.")
            return False
        from setron.ui.risk_rule_dialog import RiskRuleDialog
        dialog = (dialog_factory or RiskRuleDialog)(self, self.references, self.family_choice.currentData())
        if not dialog.exec():
            return False
        return self.create_risk_rule(dialog.values())

    def create_risk_rule(self, values):
        if self.busy or self.locked or self.gateway is None:
            return False
        try:
            context = deepcopy(self.context_provider())
        except Exception:
            self.notice.setText("현재 조회 조건을 확인하지 못했습니다. 조회 조건을 다시 적용해 주세요.")
            return False
        values = deepcopy(values)
        self.state = "saving"
        self.save_started.emit()
        # 저장 완료·실패 처리는 묶음 저장과 같은 경로(saved·save_failed 신호)를 쓴다.
        return self._start("save", lambda: self.gateway.create_risk_rule(values, context))

    def save(self):
        if self.busy or self.locked or self.state != "review" or self._reviewed is None or self.gateway is None:
            return False
        try:
            context = deepcopy(self.context_provider())
        except Exception:
            self.notice.setText("현재 조회 조건을 확인하지 못했습니다. 조회 조건을 다시 적용해 주세요.")
            return False
        edits, applicability = deepcopy(self._reviewed)
        self.state = "saving"
        self.save_started.emit()
        return self._start("save", lambda: self.gateway.save(edits, context, applicability))

    def _applicability_panel(self):
        card = Card()
        card.body.addWidget(label("모델별 적용 가능한 릴리즈 확인", 18, medium=True))
        card.body.addWidget(label("목표 N은 자동으로 바꾸지 않습니다. 실제 적용 표식·분기·순서와 출처를 확인한 후보만 지정하세요.", 13, wrap=True))
        choices = QHBoxLayout()
        self.model_choice, self.family_choice = ComboBox(), ComboBox()
        self.load_candidates_button = button("적용 후보 조회")
        choices.addWidget(self.model_choice, 1)
        choices.addWidget(self.family_choice, 1)
        choices.addWidget(self.load_candidates_button)
        card.body.addLayout(choices)
        self.candidate_list = QListWidget()
        self.candidate_list.setMaximumHeight(160)
        card.body.addWidget(self.candidate_list)
        self.applicability_basis, self.order_basis, self.source_text = QLineEdit(), QLineEdit(), QLineEdit()
        form = QFormLayout()
        form.addRow("모델 적용 표식 근거", self.applicability_basis)
        form.addRow("버전 분기 · 순서 근거", self.order_basis)
        form.addRow("확인한 원본 출처", self.source_text)
        card.body.addLayout(form)
        self.applicability_notice = label("모델·계열을 선택한 뒤 현재 적용 후보를 조회하세요.", 13, color=SECONDARY, wrap=True)
        card.body.addWidget(self.applicability_notice)
        self.add_candidates_button = button("적용 후보를 수정 목록에 추가")
        card.body.addWidget(self.add_candidates_button)
        self.order_candidates_button = button("선택 계열의 버전 순서 후보를 수정 목록에 추가")
        self.order_candidates_button.setToolTip("릴리즈노트에 나열된 순서로 10, 20, 30 …을 제안합니다(데이터 파트 규칙). "
                                                "순서가 확인된 릴리즈가 있는 계열은 제안하지 않습니다.")
        card.body.addWidget(self.order_candidates_button)
        self.order_candidates_button.clicked.connect(self._load_order_candidates)
        self.risk_create_button = button("위험 기준 새로 등록")
        self.risk_create_button.setToolTip("환경·안전 필수 Item, 결함 Item, 위험 버전을 새로 지정합니다. 저장하면 위험을 다시 판정합니다.")
        card.body.addWidget(self.risk_create_button)
        self.risk_create_button.clicked.connect(lambda: self.open_risk_dialog())
        self.load_candidates_button.clicked.connect(self._load_applicability)
        self.add_candidates_button.clicked.connect(self._stage_applicability)
        self.applicability_view = None
        self.model_choice.currentIndexChanged.connect(self._invalidate_applicability)
        self.family_choice.currentIndexChanged.connect(self._invalidate_applicability)
        return card

    def _fill_applicability_choices(self):
        for field, combo in (("group_id", self.model_choice), ("family_id", self.family_choice)):
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for key, text in reference_options(field, self.references):
                combo.addItem(text, key)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)
        self._invalidate_applicability()

    def _invalidate_applicability(self):
        self.applicability_view = None
        self.candidate_list.clear()
        self.applicability_basis.clear()
        self.order_basis.clear()
        self.source_text.clear()

    def _load_applicability(self):
        if self.busy or self.locked or self.gateway is None:
            return
        model, family = self.model_choice.currentData(), self.family_choice.currentData()
        if model is not None and family is not None:
            self._start("applicability", lambda: self.gateway.get_applicability(model, family))

    def _load_order_candidates(self):
        if self.busy or self.locked or self.state == "review" or self.gateway is None:
            return False
        family = self.family_choice.currentData()
        if family is None:
            return False
        return self._start("orders", lambda: self.gateway.version_order_candidates(family))

    def _stage_order_candidates(self, value):
        records, suggestion = value
        if suggestion.reason:
            self.applicability_notice.setText(suggestion.reason)
            return
        by_id = {record.key["release_version_id"]: record for record in records}
        for candidate in suggestion.candidates:
            record = by_id[candidate.release_version_id]
            self.pending[_identity(record.table, record.key)] = (record, {"version_order": candidate.version_order})
        self._reviewed = None
        self.applicability_notice.setText(
            f"버전 순서 후보 {len(suggestion.candidates)}건을 수정 목록에 추가했습니다. 묶음 검토에서 확인한 뒤 저장하세요."
            + "".join(f"\n· {warning}" for warning in suggestion.warnings))

    def _stage_applicability(self):
        if self.busy or self.locked or self.state == "review" or self.applicability_view is None:
            return False
        basis, order, source = self.applicability_basis.text(), self.order_basis.text(), self.source_text.text()
        replace_basis = any(text.strip() for text in (basis, order, source))
        selected = []
        for number in range(self.candidate_list.count()):
            item = self.candidate_list.item(number)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            previous = item.data(Qt.ItemDataRole.UserRole + 1)
            if replace_basis or previous is None:
                if not all(text.strip() for text in (basis, order, source)):
                    self.applicability_notice.setText("새 후보나 근거를 바꿀 때는 적용 표식·순서·원본 출처를 모두 입력하세요.")
                    return False
                previous = CandidateConfirmation(item.data(Qt.ItemDataRole.UserRole), basis, order, source)
            selected.append(previous)
        pair = self.applicability_view.value
        self.pending_applicability[(pair.model_id, pair.family_id)] = ApplicabilityEdit(
            pair.model_id, pair.family_id, self.applicability_view.revision, tuple(selected))
        self._applicability_before[(pair.model_id, pair.family_id)] = self.applicability_view
        self._reviewed = None
        self.applicability_notice.setText("적용 후보 변경을 수정 목록에 추가했습니다. 묶음 검토 후 저장하세요.")
        self._render()
        return True

    def _render(self):
        editable = self.gateway is not None and not self.busy and not self.locked and self.state != "review"
        for widget in (self.kind, self.reload_button, self.record_table, self.form, self.applicability_card):
            widget.setEnabled(editable)
        self.previous.setEnabled(editable and self.offset > 0)
        self.next.setEnabled(editable and self.has_more)
        self.add_button.setEnabled(editable and self.record is not None and bool(self.record.editable_fields))
        self.clear_button.setEnabled(not self.busy and not self.locked and bool(self.pending or self.pending_applicability))
        self.review_button.setEnabled(editable and bool(self.pending or self.pending_applicability))
        self.edit_button.setEnabled(self.state == "review" and not self.busy)
        self.save_button.setEnabled(self.state == "review" and self.gateway is not None and not self.busy and not self.locked)
        self.queue_label.setText(f"수정 목록 {len(self.pending) + len(self.pending_applicability)}건")
        text = {"edit": "관리 데이터", "review": "수정 묶음 검토", "saving": "저장 중", "failed": "저장 실패 · 초안 유지",
                "unknown": "저장 결과 미확인"}[self.state]
        self.status.setText("조회 중" if self.busy and self.state != "saving" else text)
        self.status.set_tone("warning" if self.state in {"failed", "unknown"} else "info")
        if self.gateway is None:
            self.notice.setText("DB 연결 설정이 없어 관리값 수정·저장을 사용할 수 없습니다.")
        self._fit_tables()
