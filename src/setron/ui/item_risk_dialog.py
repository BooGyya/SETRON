"""VER5 위험 Item 추가. 미리보기는 읽기 전용, 등록은 기존 관리 저장 경계에 위임한다."""

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLayout, QLineEdit, QMenu,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from setron.features.risk.item_scope import CATEGORIES
from setron.features.risk.schemas import RiskStatus
from setron.ui.combo_box import ComboBox
from setron.ui.components import button, label
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
        self.submitted = False
        self.request = ReadRequest(self)
        self.request.completed.connect(self._previewed)
        self.setWindowTitle("위험 아이템 추가")
        self.setMinimumSize(420, 320)
        self.setSizeGripEnabled(True)
        self.setStyleSheet("QDialog {background:white;} QLineEdit,QComboBox {border:1px solid #dee0e5;border-radius:8px;padding:6px 10px;}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(14)
        outer.addWidget(label("위험 아이템 추가", 18, medium=True))
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
        self.name.setAccessibleName("아이템 이름")
        layout.addWidget(field("아이템 이름", self.name))
        self.categories = QButtonGroup(self)
        self.categories.setExclusive(True)
        chips = []
        for i, text in enumerate(CATEGORIES):
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setChecked(i == 0)
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
        layout.addWidget(field("관련 릴리즈 노트 항목", _WrapRow([self.family, self.item], 240, fields=True)))
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
        layout.addWidget(self.verified)
        self.preview = label("영향 미리보기로 현재 관측에 미치는 영향을 확인하세요.", 12, color=PRIMARY, wrap=True)
        self.preview.setStyleSheet("QLabel {background:#f4f5f8;border-radius:6px;padding:10px;}")
        layout.addWidget(self.preview)
        layout.addStretch()
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)
        # 확인·취소·추가는 스크롤 밖에 두어 낮은 화면에서도 항상 접근할 수 있다.
        self.footer = QWidget()
        row = QHBoxLayout(self.footer)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.preview_button, self.cancel_button, self.save_button = button("영향 미리보기"), button("취소"), button("추가", primary=True)
        self.save_button.setEnabled(False)
        self.preview_button.clicked.connect(self.preview_impact)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self.save)
        row.addWidget(self.preview_button)
        row.addStretch()
        row.addWidget(self.cancel_button)
        row.addWidget(self.save_button)
        outer.addWidget(self.footer)
        self.family.currentIndexChanged.connect(self._family_changed)
        self.item.currentIndexChanged.connect(self._invalidate)
        self.site.currentIndexChanged.connect(self._site_changed)
        self.categories.idClicked.connect(self._category_changed)
        self.criterion.currentIndexChanged.connect(self._criterion_changed)
        for control in (self.start, self.end, self.line):
            control.currentIndexChanged.connect(self._invalidate)
        for control in (self.name, self.memo):
            control.textChanged.connect(self._invalidate)
        self.verified.toggled.connect(self._invalidate)
        self._family_changed()
        if item_id is not None and self.item.findData(item_id) >= 0:
            self.item.setCurrentIndex(self.item.findData(item_id))
            entry = next(e for e in data.versions.items if e.item.item_id == item_id)
            self.name.setText(entry.item.content[:250])
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
        self.item.clear()
        for entry in self.data.versions.items:
            if entry.item.item_id in item_ids:
                self.item.addItem(f"No {entry.item.item_no or '미확인'} · {entry.item.content[:65]}", entry.item.item_id)
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

    def _category_changed(self, index):
        # 분류와 판정 방식은 별개. 공정/기타의 의미를 임의로 새 위험 규칙으로 만들지 않는다.
        if index in (0, 1):
            self.criterion.setCurrentIndex(0 if index == 0 else 1)
        self._invalidate()

    def _criterion_changed(self, *_):
        key = self.criterion.currentData()
        self.version_fields.setVisible(key != "SAFETY_REQUIRED")
        self.end.setEnabled(key == "DEFECT_ITEM")
        self.item.setEnabled(key != "DIRECT_VERSION")
        self._invalidate()

    def values(self):
        key = self.criterion.currentData()
        values = dict(rule_type=key, family_id=self.family.currentData(), risk_reason=self.name.text().strip(),
                      mapping_status="VERIFIED" if self.verified.isChecked() else "UNRESOLVED",
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
        self.preview_values = None
        self.save_button.setEnabled(False)
        self.preview_button.setEnabled(True)
        self.preview.setText("입력 변경 · 영향 미리보기를 다시 확인하세요.")

    def preview_impact(self):
        values, context, gateway = deepcopy(self.values()), self.context_provider(), self.gateway
        self.preview_values = values
        self.preview.setText("영향 계산 중…")
        self.preview_button.setEnabled(False)
        self.request.start(BackgroundRead(lambda: gateway.preview_risk_rule(values, context)))

    def _previewed(self, result, error):
        self.preview_button.setEnabled(True)
        if error is not None or result is None:
            self.preview_values = None
            self.preview.setText("미리보기를 불러오지 못했습니다. 입력과 DB 연결을 확인해 주세요.")
            return
        if self.preview_values != self.values():
            return
        self.preview.setText(" · ".join(result.problems) if result.problems else
                             f"관측일 {result.base_date or '없음'} · 위험 후보 {result.count(RiskStatus.RISK)}대 · "
                             f"미확인 {result.count(RiskStatus.UNKNOWN)}대 · 새 위험 후보 {result.newly_risky}대 · HPA 제외 {result.hpa_excluded}대")
        self.save_button.setEnabled(result.saveable and self.verified.isChecked())

    def save(self):
        if self.submitted or not self.save_button.isEnabled() or self.preview_values != self.values():
            return False
        self.submitted = True
        if self.submit(deepcopy(self.preview_values)):
            self.accept()
            return True
        self.submitted = False
        self.preview.setText("다른 관리 작업이 진행 중입니다. 기존 작업을 완료한 뒤 다시 시도해 주세요.")
        return False

    def reject(self):
        self.request.cancel()
        super().reject()
