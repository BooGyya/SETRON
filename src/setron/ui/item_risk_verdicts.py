"""Item 한 건의 위험 기준별 설비 판정 팝업. 판정은 RiskAssessor를 그대로 쓰고 여기서는 모아서 보여 주기만 한다.

기준별 '기준 해제'·'다시 사용'은 확인 창에서 고르기만 하고, 실제 저장은 팝업을 닫은 뒤 화면이 기존 저장 경로로 한다.
"""

from dataclasses import replace

from PySide6.QtCore import QRectF, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from setron.features.risk.impact import preview_rule_impact
from setron.features.risk.item_scope import targets
from setron.features.risk.schemas import RiskStatus, RuleType
from setron.features.risk.service import RiskAssessor
from setron.ui.appearance import ui_color
from setron.ui.components import button, label
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.item_risk_dialog import PopupDialog, choice, field, warning_icon
from setron.ui.import_dialogs import ImportConfirmDialog
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.theme import INK, LINE, SECONDARY, TONES, font, paint_font, set_widget_font

STATUS_CAPTIONS = {RiskStatus.RISK: "위험 후보", RiskStatus.UNKNOWN: "미확인", RiskStatus.NO_RISK: "위험 없음"}
STATUS_TONES = {RiskStatus.RISK: "danger", RiskStatus.UNKNOWN: "warning", RiskStatus.NO_RISK: "success"}
UNKNOWN_HINT = "버전·관측 자료가 없어 위험 여부를 판단할 수 없음"
IMPACT_UNKNOWN_HINT = "관측 자료가 부족해 적용 대상 수를 확인할 수 없습니다."
DISABLED_CAPTION = "해제됨"
CONDITIONS = {
    RuleType.SAFETY_REQUIRED: "대상 PM에 필수 Item이 적용되지 않으면 위험 후보로 판정합니다.",
    RuleType.DIRECT_VERSION: "버전 일치",
    RuleType.VERSION_BELOW: "버전 미만",
    RuleType.VERSION_ABOVE: "버전 초과",
    RuleType.DEFECT_ITEM: "시작 버전부터 해제 버전 전까지 결함 Item이 포함된 SW를 사용하면 위험 후보로 판정합니다.",
}


def category(rule):
    return rule.category or {RuleType.SAFETY_REQUIRED: "환경안전 필수", RuleType.DEFECT_ITEM: "결함 버전",
                             RuleType.VERSION_BELOW: "버전 미만 위험", RuleType.VERSION_ABOVE: "버전 초과 위험",
                             RuleType.DIRECT_VERSION: "결함 버전"}[rule.rule_type]


def rule_condition(data, rule):
    return CONDITIONS[rule.rule_type]


def rule_reference(data, rule):
    releases = {entry.release.release_version_id: entry.release.version_raw
                for entry in data.versions.releases if entry.release.family_id == rule.family_id}
    if rule.rule_type is RuleType.SAFETY_REQUIRED:
        return "별도 기준 버전 없음"
    if rule.rule_type is RuleType.DEFECT_ITEM:
        start = releases.get(rule.start_release_version_id, "미확인")
        end = releases.get(rule.end_release_version_id, "미확인") if rule.end_release_version_id is not None else "없음"
        return f"시작 {start} · 해제 {end}"
    return releases.get(rule.release_version_id, "미확인")


def resize_rule_dialog(dialog):
    """기준 목록과 상세 창에 같은 크기와 화면 여백을 적용한다."""
    screen = dialog.screen() or QGuiApplication.primaryScreen()
    width, height = 780, 700
    if screen is not None:
        available = screen.availableGeometry()
        width = min(width, available.width() - 32)
        height = min(height, available.height() - 60)
    dialog.resize(width, max(dialog.minimumSizeHint().height(), height))


def software_versions(record, rule):
    """위험 기준의 계열·설비군에 해당하는 관측 SW 버전만 원문대로 표시한다."""
    versions = dict.fromkeys(pm.version_raw if pm.version_raw and pm.version_raw.strip() else "버전 미입력"
                             for pm in record.pms if pm.family_id == rule.family_id
                             and (rule.group_id is None or pm.group_id == rule.group_id)
                             and (not rule.target_model_ids or pm.group_id in rule.target_model_ids))
    return " / ".join(versions) or "버전 미입력"


def _action_style(widget, *, danger=False, emphasis=False, secondary=False):
    ink = TONES["danger"][1] if danger else SECONDARY if secondary else INK
    background = "#f1f3f6" if emphasis else "white"
    hover = "#e7ebf0" if emphasis else "#f5f6f8"
    set_widget_font(widget, font(14, medium=True))
    widget.setMinimumSize(84, 40)
    widget.setAutoDefault(False)
    widget.setStyleSheet(
        f"QPushButton {{background:{background};color:{ink};border:1px solid {LINE};"
        "border-radius:10px;padding:8px 18px;}"
        f"QPushButton:hover {{background:{hover};border-color:#aab2bf;}}"
        "QPushButton:pressed {background:#e7ebf0;}"
        "QPushButton:focus {border:2px solid #8b95a8;padding:7px 17px;}"
        "QPushButton:disabled {background:#f9fafb;color:#8b95a8;border-color:#e9eef6;}")


def repeats_item(name, content):
    """기준 이름이 Item 제목과 같거나 Item 내용의 앞부분이면 True. 같은 문장을 화면에 되풀이하지 않기 위한 판단이다."""
    shown = " ".join((name or "").split()).rstrip("…").strip()
    return not shown or " ".join((content or "").split()).startswith(shown)


def rule_verdicts(data, rule):
    """기준 한 건을 설비마다 판정한다. (설비, 판정 결과, 근거 문구) 목록을 위험 후보 → 미확인 → 위험 없음 순으로 돌려준다."""
    if data is None or not rule.is_enabled or not data.observations_connected:
        return []
    catalog = data.catalog()
    assessor = RiskAssessor(catalog.families, catalog, (rule,))
    rank = {RiskStatus.RISK: 0, RiskStatus.UNKNOWN: 1, RiskStatus.NO_RISK: 2}
    rows = []
    for record in data.records:
        if record.is_hpa or not targets(rule, record):
            continue
        result = assessor.equipment_risk(record)
        reasons = list(dict.fromkeys(f.message for f in result.findings if f.rule.risk_rule_id == rule.risk_rule_id))
        rows.append((record, result, " / ".join(reasons) or "위험 조건에 해당하지 않습니다."))
    rows.sort(key=lambda v: (rank[v[1].status], v[0].equipment_code, v[0].equipment_id))
    return rows


def toggle_impact(data, rule):
    """현재 기준에 해당하는 전체 설비 수. 다른 기준과 겹치는 대상도 포함하고 확인 불가는 None이다."""
    if data is None or rule is None or not data.observations_connected or not data.records:
        return None
    try:
        catalog = data.catalog()
        others = tuple(r for r in data.risk_rules if r.risk_rule_id != rule.risk_rule_id)
        impact = preview_rule_impact(replace(rule, is_enabled=True), catalog.families, catalog, others,
                                     data.records, base_date=data.base_date)
    except Exception:  # noqa: BLE001 - 확인 창은 계산 실패를 '확인 불가'로만 알린다
        return None
    return None if impact.problems else impact.count(RiskStatus.RISK)


def toggle_message(enabled, count):
    """상태 변경 방향과 관계없이 선택한 기준의 적용 대상 수를 표시한다."""
    if count is None:
        return "적용 대상 확인 불가"
    return f"적용 대상 {count}건"


class RuleToggleConfirmDialog(PopupDialog):
    """위험 기준 해제·다시 사용 확인 창. 자동으로 닫히지 않고, 고르기만 할 뿐 DB를 바꾸지 않는다."""

    def __init__(self, parent, rule, enabled, count):
        name = rule.display_name or rule.risk_reason
        super().__init__(parent, "위험 기준을 다시 사용할까요?" if enabled else "위험 기준을 해제할까요?",
                         show_close=False)
        self.setWindowTitle("위험 기준 다시 사용 확인" if enabled else "위험 기준 해제 확인")
        self.count = count
        self.rule_label = label(" ".join(name.split()), 14, medium=True, color=INK, wrap=True)
        self.message = label(toggle_message(enabled, count), 14, medium=True, wrap=True)
        self.unknown = label(IMPACT_UNKNOWN_HINT, 13, color=SECONDARY, wrap=True)
        self.unknown.setVisible(count is None)
        hint = ("다시 사용하면 대시보드와 히트맵의 위험 판정에 반영됩니다." if enabled else
                "해제한 기준은 삭제되지 않습니다. 상세 화면에서 언제든 다시 사용할 수 있습니다.")
        self.hint = label(hint, 14, color=INK, wrap=True)
        for widget in (self.rule_label, self.hint, self.unknown):
            self.body.addWidget(widget)
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.message, alignment=Qt.AlignmentFlag.AlignVCenter)
        row.addStretch()
        self.cancel_button = button("취소")
        self.confirm_button = button("다시 사용" if enabled else "해제")
        for action in (self.cancel_button, self.confirm_button):
            _action_style(action, emphasis=action is self.confirm_button)
        self.cancel_button.setDefault(True)
        self.confirm_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self.accept)
        row.addWidget(self.cancel_button)
        row.addWidget(self.confirm_button)
        self.body.addLayout(row)
        # 줄바꿈 문구의 높이가 폭에 맞게 계산되도록 폭을 먼저 고정한다.
        self.setFixedWidth(500)
        self.adjustSize()


class _VerdictDelegate(QStyledItemDelegate):
    """판정 열을 상태 색 칩으로 그린다. 색은 기존 배지 토큰을 쓴다."""

    def paint(self, painter, option, index):
        status = index.data(Qt.ItemDataRole.UserRole + 1)
        if status is None:
            return super().paint(painter, option, index)
        background, ink, border = TONES[STATUS_TONES[status]]
        painter.save()
        painter.setPen(ui_color("#e9eef6"))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(paint_font(12, medium=True))
        text = STATUS_CAPTIONS[status]
        width = painter.fontMetrics().horizontalAdvance(text) + 20
        rect = QRectF(option.rect.center().x() - width / 2, option.rect.center().y() - 12, width, 24)
        painter.setBrush(ui_color(background))
        painter.setPen(ui_color(border))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(ui_color(ink))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(100, 44)


class _VerdictModel(ReadTableModel):
    def __init__(self):
        super().__init__(("설비", "위치", "판정", "SW 버전"), centered=True)
        self.statuses = ()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role == Qt.ItemDataRole.UserRole + 1:
            return self.statuses[index.row()] if index.column() == 2 else None
        if role == Qt.ItemDataRole.FontRole and index.isValid():
            return paint_font(13, medium=index.column() == 0)
        if index.isValid() and index.column() == 2 and role == Qt.ItemDataRole.ToolTipRole and \
                self.statuses[index.row()] is RiskStatus.UNKNOWN:
            return UNKNOWN_HINT
        return super().data(index, role)


class ItemRuleVerdictDialog(PopupDialog):
    """위험 기준 설명과 적용 대상 수, 설비별 SW 버전을 표시한다."""

    def __init__(self, parent, data, item_id, rule_id=None, *, manager=None, toggle_allowed=False):
        entry = next((e for e in data.versions.items if e.item.item_id == item_id), None)
        super().__init__(parent, entry.item.content if entry else "Item", title_lines=2, show_close=False)
        self.content = entry.item.content if entry else ""
        self.data, self.item_id = data, item_id
        self.manager = manager
        self.mutation_request = ReadRequest(self)
        self.mutation_request.completed.connect(self._record_loaded)
        self._mutation_target = None
        self.rules = sorted((r for r in data.risk_rules if r.item_id == item_id), key=lambda r: not r.is_enabled)
        self.toggle_request = None
        self.toggle_allowed = toggle_allowed
        self.rule = next((r for r in self.rules if r.risk_rule_id == rule_id), self.rules[0] if self.rules else None)
        self.verdicts = []
        self.setWindowTitle("위험 기준 판정")
        outer = self.body
        outer.setSpacing(12)
        # 이름·버전·판정 설명을 한 블록에 모으고 작업 버튼은 하단에 둔다.
        self.condition_box = QFrame()
        self.condition_box.setObjectName("ruleCondition")
        self.condition_box.setStyleSheet("QFrame#ruleCondition {background:#f7f9fc;border:1px solid #e5eaf3;border-radius:8px;}")
        condition = QGridLayout(self.condition_box)
        condition.setContentsMargins(16, 14, 16, 14)
        condition.setHorizontalSpacing(16)
        condition.setVerticalSpacing(10)
        condition.setColumnStretch(1, 1)
        self.rule_name = label("", 13, medium=True, wrap=True)
        self.reference_text = label("", 13, wrap=True)
        self.condition_text = label("", 13, wrap=True)
        self.toggle_button = button("해제")
        self.toggle_button.clicked.connect(self._toggle_clicked)
        for row, (caption, value) in enumerate((("위험 기준명", self.rule_name), ("기준 버전", self.reference_text),
                                               ("판정 기준", self.condition_text))):
            condition.addWidget(label(caption, 12, medium=True, color=SECONDARY), row, 0,
                                Qt.AlignmentFlag.AlignTop)
            value.setMinimumWidth(0)
            condition.addWidget(value, row, 1)
        self.edit_button, self.delete_button = button("수정"), button("삭제")
        self.edit_button.setToolTip("현재 선택한 위험 기준을 수정합니다.")
        self.delete_button.setToolTip("현재 선택한 위험 기준 한 건만 삭제합니다.")
        self.edit_button.clicked.connect(lambda: self._load_rule("edit"))
        self.delete_button.clicked.connect(lambda: self._load_rule("delete"))
        outer.addWidget(self.condition_box)
        self.mutation_notice = label("", 12, color=SECONDARY, wrap=True)
        self.mutation_notice.hide()
        outer.addWidget(self.mutation_notice)
        self.target_count = label("위험 적용 대상 0건", 15, medium=True)
        self.target_count.setAccessibleName("위험 적용 대상 수")
        outer.addWidget(self.target_count)
        self.site, self.line, self.group = (choice("판정 Site"), choice("판정 Line"), choice("판정 설비군"))
        for combo in (self.site, self.line, self.group):
            combo.setFixedHeight(32)
        self.filters = QWidget()
        filters = QHBoxLayout(self.filters)
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(8)
        for name, combo in (("Site", self.site), ("Line", self.line), ("설비군", self.group)):
            filters.addWidget(field(name, combo), 1)
        outer.addWidget(self.filters)
        self.model = _VerdictModel()
        self.table = read_table(self.model, "위험 기준별 설비 SW 버전", (130, 150, 110, 290))
        self.table.fit_to_viewport = True
        self.table.setMinimumHeight(180)
        self.table.verticalHeader().setMinimumSectionSize(40)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setStyleSheet(self.table.styleSheet().replace("text-align:left", "text-align:center"))
        self.table.setItemDelegateForColumn(2, _VerdictDelegate(self.table))
        outer.addWidget(self.table, 1)
        # 판정 자료가 없거나 해제된 경우에는 테두리 없이 간결한 안내만 표시한다.
        self.notice = QFrame()
        self.notice.setObjectName("verdictNotice")
        self.notice.setStyleSheet("QFrame#verdictNotice {background:transparent;border:0;}")
        notice = QVBoxLayout(self.notice)
        notice.setContentsMargins(16, 22, 16, 22)
        notice.setSpacing(6)
        self.notice_icon = QLabel()
        self.notice_icon.setPixmap(warning_icon("#b8c1d3", 22).pixmap(22, 22))
        self.notice_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice_text = label("", 14, color=INK, wrap=True)
        self.notice_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        notice.addWidget(self.notice_icon)
        notice.addWidget(self.notice_text)
        outer.addWidget(self.notice)
        outer.addStretch(0)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 8, 0, 0)
        footer.setSpacing(8)
        self.close_button = button("닫기")
        self.close_button.clicked.connect(self.accept)
        for action in (self.toggle_button, self.edit_button, self.delete_button, self.close_button):
            _action_style(action, danger=action is self.delete_button, emphasis=action is self.edit_button,
                          secondary=action is self.close_button)
        footer.addWidget(self.toggle_button)
        footer.addWidget(self.edit_button)
        footer.addSpacing(4)
        footer.addWidget(self.delete_button)
        footer.addStretch()
        footer.addWidget(self.close_button)
        outer.addLayout(footer)
        for combo in (self.line, self.group):
            combo.currentIndexChanged.connect(self._render)
        self.site.currentIndexChanged.connect(self._site_changed)
        self._rule_changed()
        resize_rule_dialog(self)

    def _rule_changed(self, *_):
        self.mutation_request.cancel()
        self._mutation_target = None
        self.mutation_notice.hide()
        rule, data = self.rule, self.data
        self._update_mutation_buttons()
        name = (rule.display_name or rule.risk_reason) if rule else ""
        self.rule_name.setText(" ".join(name.split()))
        self.verdicts = rule_verdicts(data, rule) if rule else []
        if rule:
            self.toggle_button.setText("해제" if rule.is_enabled else "다시 사용")
            self.toggle_button.setAccessibleName(("위험 기준 해제 · " if rule.is_enabled else "위험 기준 다시 사용 · ") + name)
            self.toggle_button.setEnabled(self.toggle_allowed)
            self.toggle_button.setToolTip("" if self.toggle_allowed else "DB에 연결된 경우에만 기준을 해제·다시 사용할 수 있습니다.")
            self.reference_text.setText(rule_reference(data, rule))
            self.condition_text.setText(rule_condition(data, rule))
        has_rows = bool(self.verdicts)
        if rule is None:
            message = "이 Item에 등록된 위험 기준이 없습니다."
        elif not rule.is_enabled:
            message = "해제된 기준은 위험 판정에 반영되지 않습니다.\n다시 적용하려면 하단의 ‘다시 사용’을 눌러 주세요."
        elif not data.observations_connected:
            message = "설비 관측 자료가 없어 판정할 설비가 없습니다."
        elif not has_rows:
            message = "이 위험 기준이 적용되는 설비가 없습니다."
        else:
            message = ""
        self.notice_text.setText(message)
        self.notice.setVisible(bool(message))
        for widget in (self.filters, self.table):
            widget.setVisible(has_rows)
        self.condition_box.setVisible(rule is not None)
        records = [v[0] for v in self.verdicts]
        models = {p.model_id: p.model_name for p in data.versions.pairs}
        with QSignalBlocker(self.site), QSignalBlocker(self.group):
            for combo, title in ((self.site, "전체 Site"), (self.group, "전체 설비군")):
                combo.clear()
                combo.addItem(title, None)
            for site in sorted({r.site for r in records}):
                self.site.addItem(site, site)
            for model_id in sorted({pm.group_id for r in records for pm in r.pms if pm.group_id in models}):
                self.group.addItem(models[model_id], model_id)
        self._site_changed()

    def _update_mutation_buttons(self):
        available = self.rule is not None and self.manager is not None and self.manager.gateway is not None
        self.edit_button.setEnabled(bool(available and self.manager.submit_edit is not None and not self.mutation_request.busy))
        self.delete_button.setEnabled(bool(available and self.manager.submit_delete is not None and not self.mutation_request.busy))
        self.toggle_button.setEnabled(bool(self.rule is not None and self.toggle_allowed and
                                           self.toggle_request is None and not self.mutation_request.busy))

    def _load_rule(self, action):
        if self.mutation_request.busy or self.rule is None or self.manager is None or self.manager.gateway is None:
            return
        rule_id, gateway = self.rule.risk_rule_id, self.manager.gateway
        self._mutation_target = action, rule_id
        self.mutation_notice.setText("선택한 위험 기준의 현재 저장값을 확인하고 있습니다.")
        self.mutation_notice.show()
        self.mutation_request.start(BackgroundRead(lambda: gateway.get("risk_rule", {"risk_rule_id": rule_id})))
        self._update_mutation_buttons()

    def _record_loaded(self, record, error):
        target, self._mutation_target = self._mutation_target, None
        self._update_mutation_buttons()
        if target is None or not self.isVisible() or self.rule is None or self.rule.risk_rule_id != target[1]:
            return
        if error is not None or record is None:
            self.mutation_notice.setText("위험 기준을 읽지 못했습니다. 다시 조회한 뒤 시도해 주세요.")
            return
        if record.key.get("risk_rule_id") != target[1] or record.current.get("item_id") != self.item_id:
            self.mutation_notice.setText("위험 기준의 연결이 변경됐습니다. Item 자료를 다시 조회해 주세요.")
            return
        self.mutation_notice.hide()
        if target[0] == "edit":
            if self.manager.open_edit(self, self.data, record):
                self.accept()
            return
        presentation = (record.current.get("target_reference") or {}).get("item_manager") or {}
        name = presentation.get("name") or record.current.get("risk_reason") or f"위험 기준 {target[1]}"
        confirmation = ImportConfirmDialog(
            f"위험 기준 '{name}'을(를) 삭제할까요?\n이 기준 한 건만 삭제하며, 같은 Item의 다른 기준은 유지됩니다.",
            self, title="위험 기준 삭제", action_text="삭제")
        if confirmation.exec() != QDialog.DialogCode.Accepted:
            return
        if self.manager.submit_delete(record):
            self.accept()
        else:
            self.mutation_notice.setText("진행 중인 관리 작업을 마친 뒤 다시 시도해 주세요.")
            self.mutation_notice.show()

    def hideEvent(self, event):
        self.mutation_request.cancel()
        self._mutation_target = None
        super().hideEvent(event)

    def _toggle_clicked(self):
        """해제·다시 사용을 확인한 뒤 기존 저장 경계에서 처리하도록 요청만 남긴다."""
        rule = self.rule
        if rule is None or not self.toggle_allowed or self.toggle_request is not None or self.mutation_request.busy:
            return False
        enabled = not rule.is_enabled
        confirm = RuleToggleConfirmDialog(self, rule, enabled, toggle_impact(self.data, rule))
        self.confirm_dialog = confirm
        if confirm.exec() != QDialog.DialogCode.Accepted or self.toggle_request is not None:
            return False
        self.toggle_request = (rule.risk_rule_id, enabled)
        self.toggle_button.setEnabled(False)
        self.accept()
        return True

    def _site_changed(self, *_):
        site = self.site.currentData()
        with QSignalBlocker(self.line):
            self.line.clear()
            self.line.addItem("전체 Line", None)
            for pair in sorted({(r.site, r.line) for r, _, _ in self.verdicts if r.line and (site is None or r.site == site)}):
                self.line.addItem(pair[1] if site is not None else f"{pair[0]} · {pair[1]}", pair)
        self._render()

    def _render(self, *_):
        site, line, group = self.site.currentData(), self.line.currentData(), self.group.currentData()
        scoped = [v for v in self.verdicts
                  if (site is None or v[0].site == site) and (line is None or (v[0].site, v[0].line) == line)
                  and (group is None or any(pm.group_id == group for pm in v[0].pms))]
        self.target_count.setText(f"위험 적용 대상 {sum(v[1].status is RiskStatus.RISK for v in scoped)}건")
        self.model.statuses = tuple(v[1].status for v in scoped)
        self.model.set_rows(
            [(r.equipment_code, f"{r.site} · {r.line or '라인 미지정'}", STATUS_CAPTIONS[res.status],
              software_versions(r, self.rule)) for r, res, _ in scoped],
            tuple(r.equipment_id for r, _, _ in scoped))
