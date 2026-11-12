"""VER5 릴리즈·Item 입력과 선택 관측 정정. 검토 전에는 어떤 저장도 실행하지 않는다."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from setron.ui.components import button, label
from setron.ui.import_dialogs import ImportDialog
from setron.ui.import_widgets import PeriodDateField
from setron.ui.query_panel import _WrapRow
from setron.ui.version_management_data import make_item, make_release, release_values
from setron.ui.version_management_history import FIELD_LABELS
from setron.ui.version_management_widgets import actions, card, columns, combo, note, set_rows, table, text_field
from setron.ui.versions import _field


def fit_dialog(dialog):
    """긴 입력만 스크롤하고 제목·취소·확인 버튼은 화면 안에 남긴다."""
    content = QWidget()
    content.setObjectName("versionDialogFields")
    content.setStyleSheet("QWidget#versionDialogFields {background:white;}")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 8, 0)
    layout.setSpacing(14)
    layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    while dialog.body.count() > 2:
        item = dialog.body.takeAt(1)
        if item.widget() is not None:
            layout.addWidget(item.widget())
        elif item.layout() is not None:
            layout.addLayout(item.layout())
    scroll = QScrollArea()
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setStyleSheet("QScrollArea {background:transparent;border:0;}")
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    scroll.setFixedHeight(max(140, min(layout.sizeHint().height() + 8, dialog.screen().availableGeometry().height() - 220)))
    dialog.body.insertWidget(1, scroll)
    dialog.field_scroll = scroll
    dialog.adjustSize()


def integer(value, caption, *, positive=False):
    if not value.strip():
        return None
    try:
        result = int(value)
    except ValueError:
        raise ValueError(caption + "은 정수로 입력해 주세요.") from None
    if positive and result < 1:
        raise ValueError(caption + "은 1 이상이어야 합니다.")
    return result


class ReleaseForm(QWidget):
    submitted = Signal(object, object, str)
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        self.form = card()
        self.title = label("새 버전 등록", 20, medium=True)
        self.form.body.addWidget(self.title)
        self.context = note()
        self.form.body.addWidget(self.context)
        self.raw = text_field("버전 원문")
        self.released = PeriodDateField()
        self.released.setFixedHeight(38)
        self.released.setAccessibleName("릴리즈일 · 미기재 허용")
        self.order = text_field("릴리즈 순서 · 확인값만 입력")
        self.order.setPlaceholderText("미확인")
        self.basis = text_field("순서 확인 근거")
        self.scope = text_field("확인된 적용 대상")
        self.reason = text_field("등록·수정 사유")
        for title, field in (("버전 원문 *", self.raw), ("릴리즈일 · 미기재 허용", self.released),
                             ("릴리즈 순서 · 확인값만 입력", self.order), ("순서 확인 근거", self.basis),
                             ("확인된 적용 대상", self.scope), ("등록·수정 사유 *", self.reason)):
            self.form.body.addWidget(_field(title, field))
        self.form.body.addWidget(note("순서가 미확인이어도 원문은 등록할 수 있습니다. 순서를 추정해 N 판정에 사용하지 않습니다."))
        review = card("변경 전후 확인")
        review.body.addWidget(note("원본은 보존하고 관리값만 수정합니다. 다른 SW 계열에는 적용하지 않습니다."))
        self.preview_model, self.preview_table = table(("항목", "현재 값", "저장할 값"), "릴리즈 변경 전후", (150, 220, 220))
        review.body.addWidget(self.preview_table)
        self.error = note()
        review.body.addWidget(self.error)
        self.review = button("변경분 확인", primary=True)
        self.save = button("작업본 저장", primary=True)
        self.cancel = button("취소·돌아가기")
        self.save.hide()
        self.review.clicked.connect(self.validate)
        self.save.clicked.connect(self._submit)
        self.cancel.clicked.connect(self.cancelled)
        review.body.addWidget(actions(self.review, self.save, self.cancel))
        body.addWidget(columns(self.form, review))
        body.addWidget(note("직접 등록은 원본 파일 없음으로 기록합니다. Excel 자료는 기존 가져오기 검토 흐름을 이용합니다."))
        for field in (self.raw, self.released, self.order, self.basis, self.scope, self.reason):
            field.textChanged.connect(self.invalidate)
        self.prepared = None

    def open_entry(self, catalog, model_id, family_id, metadata, entry=None):
        self.catalog, self.model_id, self.family_id, self.entry = catalog, model_id, family_id, entry
        self.before = release_values(entry, metadata) if entry else {}
        values = self.before
        self.title.setText("릴리즈 정보 수정" if entry else "새 버전 등록")
        pair = next(p for p in catalog.pairs if (p.model_id, p.family_id) == (model_id, family_id))
        self.context.setText(f"{pair.model_name} · {pair.family_name}")
        for field, key in ((self.raw, "version_raw"), (self.order, "version_order"),
                           (self.basis, "order_basis"), (self.scope, "applicability")):
            field.setText(str(values.get(key) if values.get(key) is not None else ""))
        self.released.setText(str(values.get("released_at") or ""))
        self.reason.clear()
        self.error.clear()
        self.invalidate()

    def invalidate(self, *_):
        self.prepared = None
        self.save.hide()
        self.review.show()
        set_rows(self.preview_model, self.preview_table, ())

    def validate(self):
        from setron.features.excel_import.service import parse_date
        try:
            values = dict(version_raw=self.raw.text(), released_at=parse_date(self.released.text()) if self.released.text().strip("- ") else None,
                          version_order=integer(self.order.text(), "릴리즈 순서"), order_basis=self.basis.text(), applicability=self.scope.text())
            entry = make_release(self.catalog, self.model_id, self.family_id, values, entry=self.entry)
            if not self.reason.text().strip():
                raise ValueError("등록·수정 사유를 입력해 주세요.")
            rows = tuple((FIELD_LABELS.get(key, key), str(self.before.get(key) if self.before.get(key) is not None else "미기재"), str(value if value is not None else "미기재"))
                         for key, value in values.items() if self.before.get(key) != value)
            if not rows:
                raise ValueError("변경된 항목이 없습니다.")
            self.prepared = entry, values, self.reason.text().strip()
            set_rows(self.preview_model, self.preview_table, rows)
            self.error.setText("검토한 변경만 반영합니다. 목표 N·설비 설치 기록은 유지됩니다.")
            self.save.show()
            self.review.hide()
            return True
        except ValueError as error:
            self.error.setText(str(error))
            return False

    def _submit(self):
        if self.prepared is not None:
            self.submitted.emit(*self.prepared)


class ItemForm(ImportDialog):
    submitted = Signal(object, object, str)

    def __init__(self, catalog, release_id, *, entry=None, metadata=None, parent=None):
        super().__init__("Item 수정" if entry else "Item 추가", parent)
        self.catalog, self.release_id, self.entry = catalog, release_id, entry
        metadata = metadata or {}
        existing = next((row for row in catalog.version_items if row.release_version_id == release_id
                         and entry is not None and row.item_id == entry.item.item_id), None)
        self.number = text_field("Item 번호", str(entry.item.item_no or "") if entry else "")
        self.category = text_field("항목 · 분류", metadata.get("category", ""))
        self.content = text_field("변경 내용", entry.item.content if entry else "")
        self.state = combo("이 릴리즈 포함 상태", (("UNKNOWN", "미확인"), ("APPLIED", "포함 확인"), ("NOT_APPLIED", "미포함")))
        self.scope = combo("모델 적용 범위", (("UNKNOWN", "적용 대상 확인 필요"), ("ALL", "계열 전체"), ("LISTED", "지정 모델")))
        self.models = {}
        choices = QWidget()
        row = QHBoxLayout(choices)
        row.setContentsMargins(0, 0, 0, 0)
        for pair in catalog.pairs:
            if pair.model_id in catalog.entry(release_id).model_ids and pair.family_id == catalog.entry(release_id).release.family_id:
                control = QCheckBox(pair.model_name)
                self.models[pair.model_id] = control
                row.addWidget(control)
        self.jira = text_field("JIRA · 미기재는 유지", entry.item.jira_code or metadata.get("jira", "") if entry else metadata.get("jira", ""))
        self.remark = text_field("비고 · REMARK", metadata.get("remark", ""))
        self.reason = text_field("등록·수정 사유")
        for title, field in (("Item 번호", self.number), ("항목 · 분류", self.category), ("변경 내용 *", self.content),
                             ("이 릴리즈 포함 상태", self.state), ("모델 적용 범위", self.scope),
                             ("적용 모델", choices), ("JIRA · 미기재는 유지", self.jira), ("비고 · REMARK", self.remark),
                             ("등록·수정 사유 *", self.reason)):
            self.body.addWidget(_field(title, field))
        if existing:
            self.state.setCurrentIndex(self.state.findData(existing.state.value))
            self.scope.setCurrentIndex(self.scope.findData(existing.target_scope.value))
            for mid, box in self.models.items():
                box.setChecked(mid in existing.target_group_ids)
        self.error = note("현재 릴리즈의 연결만 수정하며 다른 릴리즈의 명시 상태를 덮어쓰지 않습니다.")
        self.body.addWidget(self.error)
        self.cancel = self.add_action("취소", self.reject, default=True)
        self.review = self.add_action("변경분 확인", self._review, primary=True)
        self.confirm = self.add_action("작업본 저장", self._submit, primary=True)
        self.confirm.hide()
        self.finish_layout()
        self.prepared = None
        for field in (self.number, self.category, self.content, self.jira, self.remark, self.reason):
            field.textChanged.connect(self.invalidate)
        for field in (self.state, self.scope):
            field.currentIndexChanged.connect(self.invalidate)
        for field in self.models.values():
            field.toggled.connect(self.invalidate)
        fit_dialog(self)

    def invalidate(self, *_):
        self.prepared = None
        self.confirm.hide()
        self.review.show()

    def _review(self):
        try:
            values = dict(item_no=integer(self.number.text(), "Item 번호", positive=True), content=self.content.text(),
                          item_state=self.state.currentData(), target_scope=self.scope.currentData(),
                          target_group_ids=tuple(mid for mid, box in self.models.items() if box.isChecked()),
                          category=self.category.text(), jira=self.jira.text(), remark=self.remark.text())
            result = make_item(self.catalog, self.release_id, values, entry=self.entry)
            if not self.reason.text().strip():
                raise ValueError("등록·수정 사유를 입력해 주세요.")
            self.prepared = result, values, self.reason.text().strip()
            before = self.entry.item.content if self.entry else "미등록"
            self.error.setText(f"변경 내용: {before}\n→ {values['content']}\n포함 상태: {self.state.currentText()} · 범위: {self.scope.currentText()}\n작업본만 저장합니다. DB·실제 설비 상태는 변경하지 않습니다.")
            self.confirm.show()
            self.review.hide()
        except ValueError as error:
            self.error.setText(str(error))

    def _submit(self):
        if self.prepared:
            self.submitted.emit(*self.prepared)


class SoftwareDialog(ImportDialog):
    submitted = Signal(object, object, str)

    def __init__(self, record, source, drafts, parent=None):
        super().__init__("설비 버전 정보 수정", parent)
        self.saving = False
        self.record, self.source, self.drafts = record, source, drafts
        self.body.addWidget(note(f"{record.equipment_code} · {record.site} · {record.line or '원본 구역 미확인'} · 관측일 {record.base_date}"))
        self.original = note("정정 대상 원문 · 원본 파일·시트·셀은 연결된 저장 근거가 있을 때만 표시합니다. 관측 기준일은 보존됩니다.")
        self.body.addWidget(self.original)
        location = source.locations.get(record.equipment_id)
        fab = next((value.label for value in source.catalog.fabs if location and value.key == location.fab_key), "미지정")
        line = next((value.label for value in source.catalog.lines if location and value.key == location.line_key), "미지정")
        self.body.addWidget(_WrapRow([_field(title, text_field("정정 대상 " + title, value, readonly=True))
                                     for title, value in (("Site", record.site), ("Fab", fab), ("Line", line))], 140, fields=True))
        self.target = combo("수정할 관측 기록", tuple((index, f"{pm.pm_name or 'SW 관측 ' + str(index + 1)} · 계열 {pm.family_id}") for index, pm in enumerate(record.pms)))
        self.day = text_field("관측 기준일 · 읽기 전용", str(record.base_date), readonly=True)
        self.current = text_field("현재 기록된 버전", readonly=True)
        self.value = text_field("정정할 버전")
        self.reason = text_field("수정 사유")
        self.body.addWidget(_WrapRow([_field("수정 대상", self.target), _field("관측 기준일", self.day)], 220, fields=True))
        self.body.addWidget(_WrapRow([_field("현재 기록된 버전", self.current), _field("정정할 버전 *", self.value)], 220, fields=True))
        self.body.addWidget(_field("수정 사유 *", self.reason))
        self.preview_model, self.preview_table = table(("관측 기록", "현재 값", "저장할 값"), "설비 기록 변경 전후", (200, 180, 180))
        self.body.addWidget(self.preview_table)
        self.error = note("선택한 관측 기록만 정정합니다. 관측일·원본·목표 N·다른 SW·실제 설비 프로그램은 바꾸지 않습니다.")
        self.body.addWidget(self.error)
        self.cancel = self.add_action("취소", self.cancel_current, default=True)
        self.review = self.add_action("변경분 확인", self._review, primary=True)
        self.confirm = self.add_action("변경 확인 후 저장", self._submit, primary=True)
        self.confirm.hide()
        self.finish_layout()
        self.index, self.prepared, self._filling = None, None, False
        self.target.currentIndexChanged.connect(self._change_target)
        self.value.textChanged.connect(self._remember)
        self.reason.textChanged.connect(self._remember)
        self._change_target()
        fit_dialog(self)

    def reject(self):
        if not self.saving:
            super().reject()

    def closeEvent(self, event):
        if self.saving:
            event.ignore()
        else:
            super().closeEvent(event)

    def key(self, index=None):
        return self.record.equipment_id, self.record.base_date, self.index if index is None else index

    def _change_target(self, *_):
        self._filling = True
        self.index = self.target.currentData()
        if self.index is not None:
            pm = self.record.pms[self.index]
            self.current.setText(pm.version_raw or "미입력")
            value, reason = self.drafts.get(self.key(), ("", ""))
            self.value.setText(value)
            self.reason.setText(reason)
        self._filling = False
        self._invalidate()

    def _remember(self, *_):
        if not self._filling and self.index is not None:
            self.drafts[self.key()] = self.value.text(), self.reason.text()
        self._invalidate()

    def _invalidate(self):
        self.prepared = None
        self.review.show()
        self.confirm.hide()
        set_rows(self.preview_model, self.preview_table, ())

    def cancel_current(self):
        if self.index is not None:
            self.drafts.pop(self.key(), None)
        self.reject()

    def _review(self):
        if self.index is None:
            self.error.setText("수정할 관측 SW가 없습니다.")
            return False
        pm = self.record.pms[self.index]
        if not self.value.text().strip() or not self.reason.text().strip():
            self.error.setText("정정할 버전과 수정 사유를 입력해 주세요.")
            return False
        if self.value.text() == pm.version_raw:
            self.error.setText("현재 기록과 같은 값입니다.")
            return False
        rows = tuple((p.pm_name or f"SW 관측 {i+1}", p.version_raw or "미입력",
                      self.value.text() if i == self.index else "변경 없음") for i, p in enumerate(self.record.pms))
        set_rows(self.preview_model, self.preview_table, rows)
        self.prepared = self.index, self.value.text(), self.reason.text().strip()
        self.review.hide()
        self.confirm.show()
        return True

    def _submit(self):
        if self.prepared:
            self.submitted.emit(*self.prepared)
