"""관리 화면의 위험 기준 새로 등록 창 (BE-015).

이미 읽어 둔 관리 참조 자료(계열·릴리즈·Item·버전별 Item 연결)로 선택지를 만들고, 입력값만 돌려준다.
연결 검사(같은 계열·확인된 순서·Item 연결)와 저장은 ``ManagementService.create_risk_rule``이 한다.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QPlainTextEdit, QVBoxLayout,
)

from setron.ui.combo_box import ComboBox

RULE_TYPES = (
    ("DIRECT_VERSION", "위험 버전 직접 지정 (이 버전을 쓰는 설비가 위험)"),
    ("DEFECT_ITEM", "결함 Item (시작 버전부터 위험, 해제 버전부터 제외)"),
    ("SAFETY_REQUIRED", "환경·안전 필수 Item (적용 대상인데 미적용이면 위험)"),
)
# 유형별로 보이는 연결 칸 (서비스 검사와 같은 필수 항목)
FIELDS_BY_TYPE = {
    "DIRECT_VERSION": ("release_version_id",),
    "DEFECT_ITEM": ("item_id", "start_release_version_id", "end_release_version_id"),
    "SAFETY_REQUIRED": ("item_id",),
}


def risk_rule_options(references, family_id):
    """계열의 릴리즈(순서 오름차순)와, 그 릴리즈에 연결된 Item 선택지."""
    releases = sorted((r for r in references.get("release_version", ()) if r["family_id"] == family_id),
                      key=lambda r: (r["version_order"] is None, r["version_order"] or 0, r["release_version_id"]))
    release_ids = {r["release_version_id"] for r in releases}
    linked = {link["item_id"] for link in references.get("release_version_item", ()) if link["release_version_id"] in release_ids}
    items = sorted((i for i in references.get("item", ()) if i["item_id"] in linked),
                   key=lambda i: (i.get("item_no") is None, i.get("item_no") or 0, i["item_id"]))
    release_options = tuple((r["release_version_id"], f"{r['version_raw']} · 순서 {r['version_order'] if r['version_order'] is not None else '미확인'}")
                            for r in releases)
    item_options = tuple((i["item_id"], f"{i.get('item_no') if i.get('item_no') is not None else '번호 없음'} · {str(i['content'])[:60]}")
                         for i in items)
    return release_options, item_options


class RiskRuleDialog(QDialog):
    """입력값을 ``values()``로 돌려준다. 저장은 호출하는 화면이 작업 스레드에서 한다."""

    def __init__(self, parent, references, family_id=None):
        super().__init__(parent)
        self.setWindowTitle("위험 기준 새로 등록")
        self.references = references
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("저장하면 현재 저장된 현황으로 위험을 다시 판정합니다. 연결 확인 전이면 '미확인'으로 남습니다."))
        form = QFormLayout()
        self.rule_type = ComboBox()
        for key, text in RULE_TYPES:
            self.rule_type.addItem(text, key)
        self.family = ComboBox()
        for row in references.get("version_family", ()):
            self.family.addItem(row["family_name"], row["family_id"])
        if family_id is not None and self.family.findData(family_id) >= 0:
            self.family.setCurrentIndex(self.family.findData(family_id))
        self.combos = {name: ComboBox() for name in ("item_id", "release_version_id",
                                                      "start_release_version_id", "end_release_version_id")}
        self.labels = {"item_id": "대상 Item", "release_version_id": "위험 버전",
                       "start_release_version_id": "결함 시작 버전", "end_release_version_id": "해제 버전 (선택)"}
        self.reason = QPlainTextEdit()
        self.reason.setPlaceholderText("위험 사유 (예: Interlock 누락으로 공정 정지 위험)")
        self.reason.setFixedHeight(70)
        self.verified = QCheckBox("대상 버전·Item 연결을 원본에서 확인함 (확인 전에는 미확인으로 판정)")
        form.addRow("유형", self.rule_type)
        form.addRow("버전 계열", self.family)
        self.rows = {}
        for name, combo in self.combos.items():
            caption = QLabel(self.labels[name])
            form.addRow(caption, combo)
            self.rows[name] = (caption, combo)
        form.addRow("사유", self.reason)
        layout.addLayout(form)
        layout.addWidget(self.verified)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("등록")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.rule_type.currentIndexChanged.connect(self._refresh)
        self.family.currentIndexChanged.connect(self._refresh)
        self.reason.textChanged.connect(self._refresh_ok)
        self._refresh()

    def _refresh(self, *_):
        releases, items = risk_rule_options(self.references, self.family.currentData())
        for name, combo in self.combos.items():
            combo.clear()
            if name == "end_release_version_id":
                combo.addItem("해제 버전 없음", None)
            for key, text in items if name == "item_id" else releases:
                combo.addItem(text, key)
        visible = FIELDS_BY_TYPE[self.rule_type.currentData()]
        for name, (caption, combo) in self.rows.items():
            caption.setVisible(name in visible)
            combo.setVisible(name in visible)
        self._refresh_ok()

    def _refresh_ok(self):
        required = [n for n in FIELDS_BY_TYPE[self.rule_type.currentData()] if n != "end_release_version_id"]
        ready = (self.family.currentData() is not None and bool(self.reason.toPlainText().strip())
                 and all(self.combos[n].currentData() is not None for n in required))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ready)

    def values(self):
        rule_type = self.rule_type.currentData()
        values = {"rule_type": rule_type, "family_id": self.family.currentData(),
                  "risk_reason": self.reason.toPlainText().strip(),
                  "mapping_status": "VERIFIED" if self.verified.isChecked() else "UNRESOLVED"}
        for name in FIELDS_BY_TYPE[rule_type]:
            if self.combos[name].currentData() is not None:
                values[name] = self.combos[name].currentData()
        return values
