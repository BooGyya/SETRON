"""VER5 위험 Item 추가. 미리보기는 읽기 전용, 등록은 기존 관리 저장 경계에 위임한다."""

from copy import deepcopy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLayout, QLineEdit, QMenu,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from setron.features.risk.item_scope import CATEGORIES
from setron.features.risk.schemas import FindingBasis, RiskStatus
from setron.ui.combo_box import ComboBox
from setron.ui.components import button, label
from setron.ui.item_original_table import show_item_source
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.query_panel import _WrapRow
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.theme import PRIMARY, SECONDARY


def choice(name):
    widget = ComboBox()
    widget.setAccessibleName(name)
    widget.setProperty("queryField", True)
    widget.setMinimumWidth(0)
    widget.setMinimumContentsLength(8)
    widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    widget.currentTextChanged.connect(widget.setToolTip)
    widget.setFixedHeight(40)
    return widget


def field(name, control):
    holder = QWidget()
    body = QVBoxLayout(holder)
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(6)
    body.addWidget(label(name, 12, medium=True, color=SECONDARY))
    body.addWidget(control)
    return holder


class ItemRiskDialog(QDialog):
    def __init__(self, parent, data, gateway, context_provider, submit, *, family_id=None, item_id=None):
        super().__init__(parent)
        self.data, self.gateway, self.context_provider, self.submit = data, gateway, context_provider, submit
        self.preview_values = None
        self.preview_valid = False
        self.suggested_name = ""
        self.submitted = False
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(350)
        self.preview_timer.timeout.connect(self.preview_impact)
        self.request = ReadRequest(self)
        self.request.completed.connect(self._previewed)
        self.setWindowTitle("위험 기준 등록")
        self.setMinimumSize(420, 320)
        self.setSizeGripEnabled(True)
        self.setStyleSheet("QDialog {background:white;} QLineEdit,QComboBox {border:1px solid #dee0e5;border-radius:8px;padding:6px 10px;}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(14)
        outer.addWidget(label("위험 기준 등록", 18, medium=True))
        outer.addWidget(label("저장 후 설비별 판정과 대시보드·히트맵 위험 집계에 반영됩니다.", 12, color=SECONDARY, wrap=True))
        self.scroll = QScrollArea()
        self.scroll.setAccessibleName("위험 아이템 입력 영역")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("itemRiskForm")
        content.setStyleSheet("QWidget#itemRiskForm {background:white;}")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.name = QLineEdit()
        self.name.setMaxLength(250)
        self.name.setFixedHeight(40)
        self.name.setAccessibleName("기준명")
        layout.addWidget(field("기준명", self.name))
        self.categories = QButtonGroup(self)
        self.categories.setExclusive(True)
        chips = []
        for i, text in enumerate(CATEGORIES):
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setChecked(text == "기타")
            chip.setStyleSheet(f"QPushButton {{border:1px solid #dee0e5;border-radius:16px;padding:7px 12px;}}"
                              f"QPushButton:checked {{color:{PRIMARY};background:#ebeffd;border-color:{PRIMARY};}}")
            self.categories.addButton(chip, i)
            chips.append(chip)
        layout.addWidget(field("구분", _WrapRow(chips, 115)))
        self.family, self.item = choice("관련 릴리즈 계열"), choice("관련 릴리즈노트 Item")
        families = {p.family_id: p.family_name for p in data.versions.pairs}
        for fid, name in sorted(families.items()):
            self.family.addItem(name, fid)
        if family_id in families:
            self.family.setCurrentIndex(self.family.findData(family_id))
        self.item_search = QLineEdit()
        self.item_search.setPlaceholderText("관련 아이템 검색")
        self.item_search.setClearButtonEnabled(True)
        self.item_note = label("", 14, medium=True, wrap=True)
        self.source_button = button("원본 행 보기")
        self.source_button.clicked.connect(lambda: show_item_source(self, data, self.item.currentData()))
        self.change_item = button("아이템 변경")
        self.change_item.setCheckable(True)
        self.change_item.setChecked(item_id is None)
        self.item_editor = QWidget()
        editor = QVBoxLayout(self.item_editor)
        editor.setContentsMargins(0, 0, 0, 0)
        editor.addWidget(self.item_search)
        editor.addWidget(_WrapRow([self.family, self.item], 240, fields=True))
        layout.insertWidget(0, field("관련 릴리즈노트 아이템", self.item_note))
        item_actions = QHBoxLayout()
        item_actions.addWidget(self.change_item)
        item_actions.addWidget(self.source_button)
        item_actions.addStretch()
        layout.insertLayout(1, item_actions)
        layout.insertWidget(2, self.item_editor)
        self.change_item.toggled.connect(self._update_editor)
        self.criterion = choice("적용 판정 기준")
        self.criterion.addItem("필수 Item 미적용 시 위험 · 포함 버전 이상 누적 적용", "SAFETY_REQUIRED")
        self.criterion.addItem("지정 버전 사용 시 위험", "DIRECT_VERSION")
        self.criterion.addItem("결함 Item의 시작~해제 범위가 위험", "DEFECT_ITEM")
        self.models = QPushButton("대상 설비군 선택")
        self.models.setFixedHeight(40)
        self.model_menu = QMenu(self.models)
        self.models.setMenu(self.model_menu)
        self.model_actions = []
        layout.addWidget(_WrapRow([field("적용 판정 기준", self.criterion), field("대상 설비군 · 복수 선택", self.models)], 240, fields=True))
        self.start, self.end = choice("대상·시작 버전"), choice("해제 버전")
        self.version_fields = _WrapRow([field("대상·시작 버전", self.start), field("해제 버전 (선택)", self.end)], 240, fields=True)
        layout.addWidget(self.version_fields)
        self.site, self.line = choice("위험 대상 Site"), choice("위험 대상 Line")
        self.site.addItem("전체 Site", None)
        for site in sorted({r.site for r in data.records}):
            self.site.addItem(site, site)
        layout.addWidget(field("대상 범위", _WrapRow([self.site, self.line], 240, fields=True)))
        self.memo = QLineEdit()
        self.memo.setMaxLength(4000)
        self.memo.setPlaceholderText("예) 원본 확인일 · 대상 공정")
        self.memo.setFixedHeight(40)
        layout.addWidget(field("메모 (선택)", self.memo))
        self.verified = QCheckBox("대상 Item·버전·설비군을 원본에서 확인했습니다")

        self.preview = label("영향 미리보기로 현재 관측에 미치는 영향을 확인하세요.", 12, color=PRIMARY, wrap=True)
        self.preview.setStyleSheet("QLabel {background:#f4f5f8;border-radius:6px;padding:10px;}")
        layout.insertWidget(3, field("영향 미리보기", self.preview))
        self.impact_model = ReadTableModel(("설비 · 위치", "판정 결과", "해당 PM · 버전 및 근거"))
        self.impact_table = read_table(self.impact_model, "영향 미리보기 설비", (105, 108, 198))
        layout.insertWidget(4, self.impact_table)
        layout.addStretch()
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)
        # 확인·취소·추가는 스크롤 밖에 두어 낮은 화면에서도 항상 접근할 수 있다.
        self.footer = QWidget()
        footer = QVBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self.verified)
        row = QHBoxLayout()
        footer.addLayout(row)
        row.setSpacing(12)
        self.preview_button, self.cancel_button, self.save_button = button("다시 계산"), button("취소"), button("위험 기준 저장", primary=True)
        self.save_button.setEnabled(False)
        self.preview_button.clicked.connect(self.preview_impact)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self.save)
        self.next_step = label("", 12, wrap=True)
        self.next_step.setAccessibleName("저장 안내")
        self.next_step.setMinimumWidth(0)
        row.addWidget(self.next_step, 1)
        row.addWidget(self.preview_button)
        row.addWidget(self.cancel_button)
        row.addWidget(self.save_button)
        outer.addWidget(self.footer)
        self.family.currentIndexChanged.connect(self._family_changed)
        self.item.currentIndexChanged.connect(self._item_changed)
        self.item_search.textChanged.connect(self._filter_items)
        self.site.currentIndexChanged.connect(self._site_changed)
        self.categories.idClicked.connect(self._invalidate)
        self.criterion.currentIndexChanged.connect(self._criterion_changed)
        for control in (self.start, self.end, self.line):
            control.currentIndexChanged.connect(self._invalidate)
        for control in (self.name, self.memo):
            control.textChanged.connect(self._invalidate)
        self.verified.toggled.connect(self._update_save_state)
        self._family_changed()
        if item_id is not None and self.item.findData(item_id) >= 0:
            self.item.setCurrentIndex(self.item.findData(item_id))
            entry = next(e for e in data.versions.items if e.item.item_id == item_id)
            self.name.setText(entry.item.content[:250])
        self.change_item.setChecked(item_id is None or self.item.currentData() != item_id)
        self._site_changed()
        self._criterion_changed()
        width, height = 640, 760
        if screen := self.screen():
            available = screen.availableGeometry()
            width, height = min(width, available.width() - 32), min(height, available.height() - 32)
        self.resize(width, height)

    def _family_changed(self, *_):
        fid = self.family.currentData()
        releases = [e.release for e in self.data.versions.releases if e.release.family_id == fid]
        ids = {r.release_version_id for r in releases}
        item_ids = {r.item_id for r in self.data.versions.version_items if r.release_version_id in ids}
        self.family_item_ids = item_ids
        self._filter_items()
        for combo in (self.start, self.end):
            combo.clear()
        self.end.addItem("해제 버전 없음", None)
        for release in sorted(releases, key=lambda r: (r.version_order is None, r.version_order or 0, r.release_version_id)):
            for combo in (self.start, self.end):
                combo.addItem(f"{release.version_raw} · 순서 {release.version_order if release.version_order is not None else '미확인'}", release.release_version_id)
        self.model_menu.clear()
        self.model_actions = []
        models = {p.model_id: p.model_name for p in self.data.versions.pairs if p.family_id == fid}
        for mid, name in models.items():
            action = self.model_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(True)
            action.setData(mid)
            action.toggled.connect(self._models_changed)
            self.model_actions.append(action)
        self._models_changed()
        self._invalidate()

    def _filter_items(self, *_):
        previous = self.item.currentData()
        self.item.clear()
        for entry in self.data.search(self.item_search.text()):
            if entry.item.item_id in self.family_item_ids:
                self.item.addItem(f"No {entry.item.item_no or '미확인'} · {entry.item.content[:65]}", entry.item.item_id)
        if self.item.findData(previous) >= 0:
            self.item.setCurrentIndex(self.item.findData(previous))

    def _item_changed(self, *_):
        entry = next((e for e in self.data.versions.items if e.item.item_id == self.item.currentData()), None)
        suggestion = entry.item.content[:250] if entry else ""
        if not self.name.text() or self.name.text() == self.suggested_name:
            self.name.setText(suggestion)
        overview = self.data.catalog().overview(entry.item, ()) if entry else None
        first = next((f.first_included for f in overview.families if f.family_id == self.family.currentData()), None) if overview else None
        has_row = entry and any(row.item_id == entry.item.item_id for row in self.data.versions.original_rows)
        evidence = "보관된 원본 행 있음" if has_row else "원본 행 미보관 · 원본 파일에서 확인 필요"
        self.source_button.setText("원본 행 보기" if has_row else "출처 정보 보기")
        self.source_button.setEnabled(entry is not None)
        self.item_note.setText(f"{self.family.currentText()} · {entry.item.content} · 최초 확인 포함 {first.version_raw if first else '미확인'}\n{evidence}"
                               if entry else "선택한 계열에 일치하는 아이템이 없습니다.")
        self.suggested_name = suggestion
        self._invalidate()

    def _models_changed(self, *_):
        self.models.setText(" · ".join(a.text() for a in self.model_actions if a.isChecked()) or "대상 설비군 선택")
        self._invalidate()

    def _site_changed(self, *_):
        site = self.site.currentData()
        self.line.clear()
        self.line.addItem("전체 Line", None)
        self.line.setEnabled(site is not None)
        for line in sorted({r.line for r in self.data.records if r.site == site and r.line}):
            self.line.addItem(line, line)
        self._invalidate()

    def _update_editor(self, *_):
        direct = self.criterion.currentData() == "DIRECT_VERSION"
        editing = self.change_item.isChecked()
        self.change_item.setVisible(not direct)
        self.change_item.setText("선택 닫기" if editing else "아이템 변경")
        self.item_editor.setVisible(direct or editing)
        self.item.setVisible(not direct)
        self.item_search.setVisible(not direct and editing)

    def _criterion_changed(self, *_):
        key = self.criterion.currentData()
        self.version_fields.setVisible(key != "SAFETY_REQUIRED")
        self.end.setEnabled(key == "DEFECT_ITEM")
        self.item.setEnabled(key != "DIRECT_VERSION")
        self.source_button.setVisible(key != "DIRECT_VERSION")
        self._update_editor()
        if key == "DIRECT_VERSION":
            self.item_note.setText(self.family.currentText())
        else:
            self._item_changed()
        if key == "DIRECT_VERSION" and self.name.text() == self.suggested_name:
            self.name.clear()
        elif key != "DIRECT_VERSION" and not self.name.text():
            self.name.setText(self.suggested_name)
        self._invalidate()

    def values(self):
        key = self.criterion.currentData()
        values = dict(rule_type=key, family_id=self.family.currentData(), risk_reason=self.name.text().strip(),
                      mapping_status="VERIFIED",
                      item_manager=dict(schema=1, name=self.name.text().strip(), category=CATEGORIES[self.categories.checkedId()],
                                        memo=self.memo.text().strip(), model_ids=[a.data() for a in self.model_actions if a.isChecked()],
                                        site=self.site.currentData(), line=self.line.currentData()))
        if key == "DIRECT_VERSION":
            values["release_version_id"] = self.start.currentData()
        else:
            values["item_id"] = self.item.currentData()
            if key == "DEFECT_ITEM":
                values.update(start_release_version_id=self.start.currentData(), end_release_version_id=self.end.currentData())
        return values

    def _invalidate(self, *_):
        self.request.cancel()
        self.preview_valid = False
        self.verified.setChecked(False)
        self.impact_table.hide()
        self.preview_values = None
        self.save_button.setEnabled(False)
        self.preview_button.hide()
        self.preview.setText("현재 입력값으로 영향을 확인하고 있습니다.")
        self.next_step.setText("영향을 계산한 뒤 원본 확인을 완료하세요.")
        if self.isVisible():
            self.preview_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.preview_timer.start(350)

    def hideEvent(self, event):
        self.preview_timer.stop()
        self.request.cancel()
        super().hideEvent(event)

    def _update_save_state(self, *_):
        ready = self.preview_valid and self.verified.isChecked() and not self.submitted
        self.save_button.setEnabled(ready)
        if self.preview_valid:
            self.next_step.setText("저장할 수 있습니다." if ready else "원본 확인 후 위 확인란을 선택하세요.")


    def preview_impact(self):
        self.preview_timer.stop()
        if self.request.busy or self.submitted:
            return
        values = deepcopy(self.values())
        presentation = values["item_manager"]
        problem = ""
        if not presentation["name"]:
            problem = "기준명을 입력하세요."
        elif not presentation["model_ids"]:
            problem = "대상 설비군을 선택하세요."
        elif values["rule_type"] != "DIRECT_VERSION" and not values.get("item_id"):
            problem = "관련 아이템을 선택하세요."
        elif values["rule_type"] == "DIRECT_VERSION" and not values.get("release_version_id"):
            problem = "대상 버전을 선택하세요."
        if problem:
            self.preview.setText(problem)
            self.next_step.setText(problem)
            return
        context, gateway = self.context_provider(), self.gateway
        self.preview_valid = False
        self.save_button.setEnabled(False)
        self.preview_values = values
        self.preview.setText("영향 계산 중…")
        self.preview_button.setEnabled(False)
        self.request.start(BackgroundRead(lambda: gateway.preview_risk_rule(values, context)))

    def _previewed(self, result, error):
        self.preview_button.setEnabled(True)
        if error is not None or result is None:
            self.preview_values = None
            self.preview.setText("미리보기를 불러오지 못했습니다.")
            self.preview_button.show()
            self.next_step.setText("연결을 확인한 뒤 다시 계산하세요.")
            return
        if self.preview_values != self.values():
            return
        self.preview.setText(" · ".join(result.problems) if result.problems else
                             f"관측일 {result.base_date or '없음'} · 위험 후보 {result.count(RiskStatus.RISK)}대 · "
                             f"미확인 {result.count(RiskStatus.UNKNOWN)}대 · 새 위험 후보 {result.newly_risky}대 · HPA 제외 {result.hpa_excluded}대")
        rows = []
        for impact in getattr(result, "equipment", ()):
            findings = impact.findings
            reasons = {}
            for finding in findings:
                reason = ("필수 아이템 미적용" if finding.basis is FindingBasis.SAFETY_NOT_APPLIED else finding.message)
                version = (finding.pm.version_raw or "버전 미확인") if finding.pm else ""
                pms = reasons.setdefault((version, reason), [])
                if finding.pm:
                    pms.append(finding.pm.pm_name or "공통 SW")
            details = [" · ".join(filter(None, (", ".join(pms), version, reason)))
                       for (version, reason), pms in reasons.items()]
            record = impact.record
            location = " · ".join(filter(None, (record.site, record.line)))
            status = ("판정 불가" if impact.status is RiskStatus.UNKNOWN else
                      "기존 위험 후보" if impact.before is RiskStatus.RISK else "신규 위험 후보")
            rows.append((f"{record.equipment_code}\n{location}", status, "\n".join(details)))
        self.impact_model.set_rows(rows)
        self.impact_table.resizeRowsToContents()
        self.impact_table.setFixedHeight(min(280, max(110, 50 + sum(self.impact_table.rowHeight(i) for i in range(min(6, len(rows)))))))
        self.impact_table.setVisible(bool(rows))
        if result.saveable:
            self.preview.setText(f"{self.family.currentText()} · {self.site.currentText()} · {self.line.currentText()}\n" +
                                 (self.preview.text() if result.base_date else "설비 관측 자료 미연결"))
        if result.saveable and result.base_date and hasattr(result, "target_count"):
            self.preview.setText(self.preview.text() + f"\n대상 {result.target_count}대 · 위험 판정 없음 {result.target_count - len(result.equipment)}대 · 기존 위험 후보 {result.already_risky}대")
        self.preview_valid = result.saveable
        if result.problems:
            self.next_step.setText(result.problems[0])
        self._update_save_state()

    def save(self):
        if self.submitted or not self.verified.isChecked() or not self.save_button.isEnabled() or self.preview_values != self.values():
            return False
        self.submitted = True
        if self.submit(deepcopy(self.preview_values)):
            self.accept()
            return True
        self.submitted = False
        self.next_step.setText("진행 중인 관리 작업을 마친 뒤 저장하세요.")
        return False

    def reject(self):
        self.preview_timer.stop()
        self.request.cancel()
        super().reject()
