"""선택 Item에 등록된 위험 기준을 먼저 고르는 목록 팝업."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout

from setron.features.risk.schemas import RuleType
from setron.ui.components import button, label
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.item_risk_dialog import PopupDialog
from setron.ui.item_risk_verdicts import resize_rule_dialog, rule_reference
from setron.ui.theme import SECONDARY


_CRITERIA = {
    RuleType.VERSION_BELOW: "버전 미만",
    RuleType.DIRECT_VERSION: "버전 일치",
    RuleType.VERSION_ABOVE: "버전 초과",
    RuleType.SAFETY_REQUIRED: "필수 Item 미적용",
    RuleType.DEFECT_ITEM: "범위 내 사용",
}


class ItemRuleListDialog(PopupDialog):
    """기준 수와 관계없이 목록에서 고른 한 건만 상세 화면으로 전달한다."""

    def __init__(self, parent, data, item_id, *, family_id=None):
        super().__init__(parent, "위험 기준 목록", show_close=False)
        self.data, self.item_id, self.family_id = data, item_id, family_id
        self.selected_rule_id = None
        self.rules = tuple(sorted(
            (rule for rule in getattr(data, "risk_rules", ())
             if rule.item_id == item_id and (family_id is None or rule.family_id == family_id)),
            key=lambda rule: (not rule.is_enabled, rule.risk_rule_id)))
        active = sum(rule.is_enabled for rule in self.rules)
        self.count = label(f"등록된 위험 기준 {len(self.rules)}건 · 적용됨 {active}건",
                           13, color=SECONDARY, wrap=True)
        self.count.setAccessibleName("등록된 위험 기준 수")
        self.body.addWidget(self.count)
        self.model = ReadTableModel(("위험 기준명", "기준 버전", "판정 기준", "상태"), centered=True)
        self.table = read_table(self.model, "상세를 볼 위험 기준 목록", (270, 190, 140, 100))
        self.table.fit_to_viewport = True
        self.table.minimum_column_widths = {0: 160, 1: 140, 2: 96, 3: 80}
        self.table.setMinimumHeight(96)
        self.table.verticalHeader().setMinimumSectionSize(44)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setStyleSheet(self.table.styleSheet().replace("text-align:left", "text-align:center"))
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setCursor(Qt.CursorShape.PointingHandCursor)
        self.table.clicked.connect(self._select_rule)
        self.table.activated.connect(self._select_rule)
        rows, tooltips, tones = [], {}, {}
        for index, rule in enumerate(self.rules):
            name = rule.display_name or rule.risk_reason
            rows.append((" ".join(name.split()), rule_reference(data, rule), _CRITERIA[rule.rule_type],
                         "적용됨" if rule.is_enabled else "해제됨"))
            tooltips[index, 0] = name
            tones[index, 3] = "success" if rule.is_enabled else "neutral"
        self.model.set_rows(rows, tuple(rule.risk_rule_id for rule in self.rules),
                            tooltips=tooltips, cell_tones=tones)
        self.body.addWidget(self.table, 1)
        self.table.setVisible(bool(self.rules))
        empty = ("선택한 계열에 등록된 위험 기준이 없습니다." if family_id is not None else
                 "이 Item에 등록된 위험 기준이 없습니다.")
        self.notice = label(empty, 14, color=SECONDARY, wrap=True)
        self.notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice.setMinimumHeight(96)
        self.body.addWidget(self.notice)
        self.notice.setVisible(not self.rules)
        footer = QHBoxLayout()
        footer.addStretch()
        self.close_button = button("닫기", primary=True)
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.close_button)
        self.body.addLayout(footer)
        resize_rule_dialog(self)
        if self.rules:
            self.table.selectRow(0)
            self.table.setFocus()

    def _select_rule(self, index):
        if self.selected_rule_id is not None or not index.isValid():
            return
        self.selected_rule_id = self.model.ids[index.row()]
        self.accept()
