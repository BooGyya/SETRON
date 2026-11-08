"""확인된 위치 연결의 프론트엔드 작업 공간. 원본 관측/회사 DB에는 쓰지 않는다."""

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from setron.ui.components import button, label
from setron.ui.import_dialogs import ImportConfirmDialog, ImportDialog
from setron.ui.query_panel import _WrapRow
from setron.ui.version_management_widgets import actions, card, combo, note, set_rows, table, text_field


class AddReferenceDialog(ImportDialog):
    def __init__(self, kind, existing, parent=None):
        super().__init__(f"{kind} 추가", parent)
        self.existing = existing
        self.name = text_field(f"새 {kind} 이름")
        self.name.setPlaceholderText(f"{kind} 이름 입력")
        self.body.addWidget(self.name)
        self.feedback = note("현재 화면의 선택지에 추가합니다. 회사 DB에는 저장하지 않습니다.")
        self.body.addWidget(self.feedback)
        self.add_action("취소", self.reject, default=True)
        self.add_action("확인", self._accept, primary=True)
        self.finish_layout()

    def _accept(self):
        value = self.name.text().strip()
        if not value or len(value) > 80:
            self.feedback.setText("이름을 1~80자로 입력하세요.")
        elif value.casefold() in {str(v).casefold() for v in self.existing}:
            self.feedback.setText("이미 등록된 이름입니다.")
        else:
            self.accept()


class LocationPanel(QWidget):
    back_requested = Signal()

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.catalog = source.catalog
        self.mapping_drafts = {}
        self.equipment_drafts = {}
        self.added_sites, self.added_groups = [], []
        self.selected = None
        self.generation = 0
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        heading = QHBoxLayout()
        heading.addWidget(label("위치 관리", 20, medium=True))
        heading.addStretch()
        self.add_site, self.add_group = button("+ Site 추가"), button("+ 설비군 추가")
        heading.addWidget(self.add_site)
        heading.addWidget(self.add_group)
        body.addLayout(heading)
        self.add_site.clicked.connect(lambda: self.add_reference("Site"))
        self.add_group.clicked.connect(lambda: self.add_reference("설비군"))
        mapping = card("위치 정보 연결", outlined=False)
        mapping.body.addWidget(note("원본 구역명과 실제 Fab·Line을 구분합니다."))
        self.site = combo("위치 연결 Site", tuple((o.key, o.label) for o in self.catalog.sites))
        self.fab, self.line = combo("위치 연결 Fab"), combo("위치 연결 Line")
        mapping.body.addWidget(_WrapRow([self._field("Site", self.site), self._field("Fab", self.fab), self._field("Line", self.line)], 210, fields=True))
        self.model, self.view = table(("원본 구역명", "Site", "Fab", "Line", "처리 상태"), "원본 구역과 위치 연결", (230, 140, 180, 180, 300))
        mapping.body.addWidget(self.view)
        self.preview_button, back = button("입력 후 영향 확인", primary=True), button("계열 설정으로 돌아가기")
        back.clicked.connect(self.back_requested.emit)
        self.preview_button.clicked.connect(self.preview_mapping)
        mapping.body.addWidget(actions(self.preview_button, back))
        self.feedback = note("위치 편집은 현재 세션에만 적용됩니다. 원본·DB·기존 판정은 바꾸지 않습니다.")
        mapping.body.addWidget(self.feedback)
        self.added_references = note()
        mapping.body.addWidget(self.added_references)
        body.addWidget(mapping)
        equipment = card("설비 위치 수정", outlined=False)
        latest = {r.equipment_id: r for r in sorted(source.records, key=lambda r: r.base_date)}
        self.equipment = combo("설비호기", tuple((key, r.equipment_code) for key, r in latest.items()))
        self.raw = text_field("기록된 원본 구역", readonly=True)
        self.equipment_fab, self.equipment_line = combo("설비 Fab"), combo("설비 Line")
        equipment.body.addWidget(_WrapRow([self._field("설비호기", self.equipment), self._field("기록된 원본 구역", self.raw),
            self._field("Fab", self.equipment_fab), self._field("Line", self.equipment_line)], 200, fields=True))
        self.reason = text_field("위치 수정 사유")
        self.reason.setPlaceholderText("위치 수정 사유 입력")
        equipment.body.addWidget(self._field("수정 사유", self.reason))
        self.equipment_preview = button("영향 미리보기", primary=True)
        self.equipment_cancel = button("취소 · 저장값 복원")
        self.equipment_preview.clicked.connect(self.preview_equipment)
        self.equipment_cancel.clicked.connect(self._equipment_changed)
        equipment.body.addWidget(actions(self.equipment_preview, self.equipment_cancel))
        self.audit_model, self.audit_view = table(("대상", "이전 값", "변경 값", "사유"), "위치 변경 내역 · 현재 세션", (220, 220, 220, 250))
        equipment.body.addWidget(self.audit_view)
        self.audit = []
        body.addWidget(equipment)
        self.site.currentIndexChanged.connect(self._site_changed)
        self.fab.currentIndexChanged.connect(self._fab_changed)
        self.line.currentIndexChanged.connect(self._invalidate)
        self.equipment.currentIndexChanged.connect(self._equipment_changed)
        self.equipment_fab.currentIndexChanged.connect(self._equipment_fab_changed)
        self.equipment_line.currentIndexChanged.connect(self._invalidate)
        self.reason.textChanged.connect(self._invalidate)
        self.view.clicked.connect(self._select_row)
        self._site_changed()
        self._equipment_changed()

    @staticmethod
    def _field(title, widget):
        host = QWidget()
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        caption = note(title)
        caption.setBuddy(widget)
        body.addWidget(caption)
        body.addWidget(widget)
        return host

    def _name(self, key, options):
        return next((o.label for o in options if o.key == key), "미지정" if key is None else str(key))

    def _fill(self, field, choices, selected=None):
        field.blockSignals(True)
        field.clear()
        field.addItem("미지정", None)
        for key, name in choices:
            field.addItem(name, key)
        field.setCurrentIndex(max(0, field.findData(selected)))
        field.blockSignals(False)

    def _invalidate(self, *_):
        self.generation += 1

    def _site_changed(self):
        self.selected = None
        self._fill(self.fab, ((o.key, o.label) for o in self.catalog.fabs if o.site_key == self.site.currentData()))
        self._fab_changed()
        self._render_rows()

    def _fab_changed(self):
        self._fill(self.line, ((o.key, o.label) for o in self.catalog.lines
            if o.site_key == self.site.currentData() and o.fab_key == self.fab.currentData()))
        self._invalidate()

    def _render_rows(self):
        self.row_keys = tuple(sorted({(loc.site_key, loc.raw_area) for loc in self.source.locations.values()
                                    if loc.site_key == self.site.currentData()}, key=lambda key: key[1] or ""))
        rows = []
        for key in self.row_keys:
            matching = [loc for loc in self.source.locations.values() if (loc.site_key, loc.raw_area) == key]
            locations = {(loc.fab_key, loc.line_key) for loc in matching}
            fab, line = self.mapping_drafts.get(key, next(iter(locations)) if len(locations) == 1 else (None, None))
            rows.append((key[1] or "미확인", self._name(key[0], self.catalog.sites), self._name(fab, self.catalog.fabs),
                self._name(line, self.catalog.lines), "화면 내 연결 확인" if key in self.mapping_drafts else "원본 검토 후 연결"))
        set_rows(self.model, self.view, tuple(rows))
        self.preview_button.setEnabled(bool(self.row_keys))

    def _select_row(self, index):
        if index.isValid() and index.row() < len(self.row_keys):
            self.selected = self.row_keys[index.row()]
            locations = {(loc.fab_key, loc.line_key) for loc in self.source.locations.values()
                         if (loc.site_key, loc.raw_area) == self.selected}
            current = self.mapping_drafts.get(self.selected, next(iter(locations)) if len(locations) == 1 else (None, None))
            if current:
                self.fab.setCurrentIndex(max(0, self.fab.findData(current[0])))
                self.line.setCurrentIndex(max(0, self.line.findData(current[1])))
            self._invalidate()

    def _equipment_changed(self, *_):
        key = self.equipment.currentData()
        loc = self.source.locations.get(key)
        current = self.equipment_drafts.get(key, loc)
        self.raw.setText((self._name(loc.site_key, self.catalog.sites) + " · " + (loc.raw_area or "미확인")) if loc else "")
        self._fill(self.equipment_fab, ((o.key, o.label) for o in self.catalog.fabs if loc and o.site_key == loc.site_key), current.fab_key if current else None)
        self._equipment_fab_changed()
        if current:
            self.equipment_line.setCurrentIndex(max(0, self.equipment_line.findData(current.line_key)))
        self.reason.clear()
        self.equipment_preview.setEnabled(loc is not None)
        self._invalidate()

    def _equipment_fab_changed(self):
        loc = self.source.locations.get(self.equipment.currentData())
        self._fill(self.equipment_line, ((o.key, o.label) for o in self.catalog.lines
            if loc and o.site_key == loc.site_key and o.fab_key == self.equipment_fab.currentData()))
        self._invalidate()

    def preview_mapping(self):
        if self.selected is None:
            self.feedback.setText("연결할 원본 구역 행을 먼저 선택하세요.")
            return
        key, fab, line = self.selected, self.fab.currentData(), self.line.currentData()
        count = sum((loc.site_key, loc.raw_area) == key for loc in self.source.locations.values())
        description = f"원본 구역: {key[1] or '미확인'}\nSite: {self._name(key[0], self.catalog.sites)}\nFab: {self._name(fab, self.catalog.fabs)}\nLine: {self._name(line, self.catalog.lines)}\n영향 범위: {count}대\n현재 세션의 위치 연결만 적용합니다. 원본과 DB 기록은 유지합니다."
        self._confirm(description, lambda: self._save_mapping(key, fab, line))

    def _save_mapping(self, key, fab, line):
        self.mapping_drafts[key] = (fab, line)
        for identifier, loc in self.source.locations.items():
            if (loc.site_key, loc.raw_area) == key:
                self.equipment_drafts[identifier] = replace(loc, fab_key=fab, line_key=line)
        self._render_rows()
        self._equipment_changed()
        self.feedback.setText("위치 연결 확인 완료 · 현재 세션에 적용 · 원본·DB 유지")

    def preview_equipment(self):
        key = self.equipment.currentData()
        loc = self.source.locations.get(key)
        if loc is None:
            return
        reason = self.reason.text().strip()
        if not reason:
            self.feedback.setText("위치 수정 사유를 입력하세요.")
            return
        previous = self.equipment_drafts.get(key, loc)
        target = replace(previous, fab_key=self.equipment_fab.currentData(), line_key=self.equipment_line.currentData())
        if target == previous:
            self.feedback.setText("변경된 위치가 없습니다.")
            return
        before = f"{self._name(previous.fab_key, self.catalog.fabs)} / {self._name(previous.line_key, self.catalog.lines)}"
        after = f"{self._name(target.fab_key, self.catalog.fabs)} / {self._name(target.line_key, self.catalog.lines)}"
        def save():
            self.equipment_drafts[key] = target
            self.audit.append((self.equipment.currentText(), before, after, reason))
            set_rows(self.audit_model, self.audit_view, tuple(self.audit))
            self.feedback.setText("설비 위치 확인 완료 · 현재 세션에 적용 · 원본·DB 유지")
        self._confirm(f"설비: {self.equipment.currentText()}\n이전: {before}\n변경: {after}\n사유: {reason}\n현재 세션에만 적용합니다. 원본과 DB 기록은 유지합니다.", save)

    def _confirm(self, message, save):
        generation = self.generation
        dialog = ImportConfirmDialog(message, self, title="위치 연결 영향 확인", action_text="확인")
        self.confirm_dialog = dialog
        def finished(result):
            if result == dialog.DialogCode.Accepted:
                if generation != self.generation:
                    self.feedback.setText("선택이 변경되었습니다. 영향을 다시 확인하세요.")
                else:
                    save()
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()

    def add_reference(self, kind):
        existing = ([s.label for s in self.catalog.sites] + self.added_sites if kind == "Site" else
                    [g.label for g in self.catalog.groups] + self.added_groups)
        dialog = AddReferenceDialog(kind, existing, self)
        self.add_dialog = dialog
        def finished(result):
            if result == dialog.DialogCode.Accepted:
                value = dialog.name.text().strip()
                (self.added_sites if kind == "Site" else self.added_groups).append(value)
                if kind == "Site":
                    self.site.addItem(value, "local:" + value)
                    self.site.setCurrentIndex(self.site.count() - 1)
                self.added_references.setText("추가한 Site: " + (", ".join(self.added_sites) or "없음")
                    + "\n추가한 설비군: " + (", ".join(self.added_groups) or "없음"))
                self.feedback.setText(f"{kind} '{value}' 추가 · 현재 화면 선택지 · DB에는 저장하지 않았습니다.")
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()
