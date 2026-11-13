"""Item 한 건의 위험 기준별 설비 판정 팝업. 판정은 RiskAssessor를 그대로 쓰고 여기서는 모아서 보여 주기만 한다.

기준별 '기준 해제'·'다시 사용'은 확인 창에서 고르기만 하고, 실제 저장은 팝업을 닫은 뒤 화면이 기존 저장 경로로 한다.
"""

from dataclasses import replace

from PySide6.QtCore import QRectF, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QSizePolicy, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from setron.features.risk.impact import preview_rule_impact
from setron.features.risk.item_scope import targets
from setron.features.risk.schemas import RiskStatus, RuleType, criterion_label
from setron.features.risk.service import RiskAssessor
from setron.ui.appearance import ui_color
from setron.ui.components import StatusBadge, button, label
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.item_original_table import show_item_source
from setron.ui.item_risk_dialog import FIRST_HINT, PopupDialog, choice, field, warning_icon
from setron.ui.theme import SECONDARY, TONES, font, paint_font
from setron.ui.version_data import family_display_name

STATUS_CAPTIONS = {RiskStatus.RISK: "위험 후보", RiskStatus.UNKNOWN: "미확인", RiskStatus.NO_RISK: "위험 없음"}
STATUS_TONES = {RiskStatus.RISK: "danger", RiskStatus.UNKNOWN: "warning", RiskStatus.NO_RISK: "success"}
UNKNOWN_HINT = "버전·관측 자료가 없어 위험 여부를 판단할 수 없음"
IMPACT_UNKNOWN_HINT = "설비 관측 자료가 없거나 판정을 계산할 수 없어 대수를 세지 못했습니다. 저장은 그대로 할 수 있습니다."
DISABLED_CAPTION = "해제됨"
CONDITIONS = {
    RuleType.SAFETY_REQUIRED: "대상 PM 중 하나라도 필수 아이템이 미적용이면 위험 후보입니다.",
    RuleType.DIRECT_VERSION: "대상 PM이 지정된 위험 버전을 사용하면 위험 후보입니다.",
    RuleType.DEFECT_ITEM: "지정한 버전 범위의 대상 PM에 결함 아이템이 포함되면 위험 후보입니다.",
}


def category(rule):
    return criterion_label(rule.rule_type)  # 판정 기준 이름 2종(사용 시 위험 / 미적용 시 위험)으로 보인다.


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
    """기준을 해제(또는 다시 사용)하면 위험 후보에서 빠지는(더해지는) 설비 수. 계산할 수 없으면 None.

    등록 영향 미리보기(preview_rule_impact)를 그대로 쓴다. 이 기준을 뺀 나머지 기준을 '기존', 이 기준을
    켠 상태를 '초안'으로 두면, 초안 때문에 새로 위험 후보가 되는 설비가 곧 해제 시 빠지는 설비다.
    다른 기준으로도 위험 후보인 설비는 세지 않는다. 관측 자료가 없거나 계산에 실패하면 0대로 쓰지 않는다.
    """
    if data is None or rule is None or not data.observations_connected or not data.records:
        return None
    try:
        catalog = data.catalog()
        others = tuple(r for r in data.risk_rules if r.risk_rule_id != rule.risk_rule_id)
        impact = preview_rule_impact(replace(rule, is_enabled=True), catalog.families, catalog, others,
                                     data.records, base_date=data.base_date)
    except Exception:  # noqa: BLE001 - 확인 창은 계산 실패를 '확인 불가'로만 알린다
        return None
    return None if impact.problems else impact.newly_risky


def toggle_message(enabled, count):
    """확인 창의 영향 문구. ``enabled``는 바꾼 뒤 상태(True=다시 사용)다."""
    verb = "다시 사용하면" if enabled else "해제하면"
    if count is None:
        return f"{verb} 바뀌는 위험 후보 수: 확인 불가"
    if count == 0:
        return f"{verb} 위험 후보 설비 수는 바뀌지 않습니다 (0대)."
    return f"{verb} 위험 후보 {count}대가 {'추가됩니다' if enabled else '빠집니다'}."


class RuleToggleConfirmDialog(PopupDialog):
    """위험 기준 해제·다시 사용 확인 창. 자동으로 닫히지 않고, 고르기만 할 뿐 DB를 바꾸지 않는다."""

    def __init__(self, parent, rule, enabled, count):
        name = rule.display_name or rule.risk_reason
        super().__init__(parent, "위험 기준을 다시 사용할까요?" if enabled else "위험 기준을 해제할까요?")
        self.setWindowTitle("위험 기준 다시 사용 확인" if enabled else "위험 기준 해제 확인")
        self.count = count
        self.rule_label = label(name, 13, color=SECONDARY, wrap=True)
        self.message = label(toggle_message(enabled, count), 14, medium=True, wrap=True)
        if count is None:
            self.message.setStyleSheet(f"color:{TONES['warning'][1]};")
        self.unknown = label(IMPACT_UNKNOWN_HINT, 12, color=SECONDARY, wrap=True)
        self.unknown.setVisible(count is None)
        hint = ("다시 사용하면 대시보드 위험 후보·히트맵·아이템 목록 배지에 다시 반영됩니다." if enabled else
                "기준은 지우지 않고 '해제됨'으로 남습니다. 위험 기준 상세에서 언제든 다시 사용할 수 있습니다.")
        self.hint = label(hint, 12, color=SECONDARY, wrap=True)
        for widget in (self.rule_label, self.message, self.unknown, self.hint):
            self.body.addWidget(widget)
        row = QHBoxLayout()
        row.addStretch()
        self.cancel_button = button("취소")
        self.confirm_button = button("다시 사용" if enabled else "해제", primary=True)
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
        rect = QRectF(option.rect.left() + 8, option.rect.center().y() - 12, width, 24)
        painter.setBrush(ui_color(background))
        painter.setPen(ui_color(border))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(ui_color(ink))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(100, 44)


class _Tile(QFrame):
    """판정별 대수를 보여 주고 누르면 그 판정만 거르는 켜고 끄는 타일."""

    clicked = Signal(bool)

    def __init__(self, accessible_name):
        super().__init__()
        self.checked = False
        self.setObjectName("verdictTile")
        self.setAccessibleName(accessible_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_tone(self, background, border):
        self.tone = (background, border)
        self._style()

    def _style(self):
        background, border = self.tone
        self.setStyleSheet(
            "QFrame#verdictTile {background:" + (background if self.checked else "white") + ";border:" +
            (f"2px solid {border}" if self.checked else "1px solid #e5eaf3") + ";border-radius:8px;}"
            f"QFrame#verdictTile:hover {{background:{background};}}")

    def isChecked(self):
        return self.checked

    def setChecked(self, checked):
        self.checked = bool(checked)
        self._style()

    def click(self):
        self.setChecked(not self.checked)
        self.clicked.emit(self.checked)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.click()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
        else:
            super().keyPressEvent(event)


class _VerdictModel(ReadTableModel):
    def __init__(self):
        super().__init__(("설비 호기", "위치", "판정", "근거"))
        self.statuses = ()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role == Qt.ItemDataRole.UserRole + 1:
            return self.statuses[index.row()] if index.column() == 2 else None
        if index.isValid() and index.column() == 2 and role == Qt.ItemDataRole.ToolTipRole and \
                self.statuses[index.row()] is RiskStatus.UNKNOWN:
            return UNKNOWN_HINT
        return super().data(index, role)


class ItemRuleVerdictDialog(PopupDialog):
    """Item의 위험 기준 판정 팝업: 기준 선택 → 조건 → 판정별 대수 → 설비 필터 → 설비별 판정 표."""

    def __init__(self, parent, data, item_id, rule_id=None, *, toggle_allowed=False):
        entry = next((e for e in data.versions.items if e.item.item_id == item_id), None)
        super().__init__(parent, entry.item.content if entry else "Item", title_lines=2)
        self.content = entry.item.content if entry else ""
        self.data, self.item_id = data, item_id
        # 해제된 기준도 '해제됨'으로 남겨 다시 사용할 수 있게 한다. 사용 중인 기준을 앞에 둔다.
        self.rules = sorted((r for r in data.risk_rules if r.item_id == item_id), key=lambda r: not r.is_enabled)
        # 확인 창에서 고른 (risk_rule_id, 바꿀 is_enabled). 저장은 팝업을 닫은 뒤 화면이 한다.
        self.toggle_request = None
        self.toggle_allowed = toggle_allowed
        self.rule = next((r for r in self.rules if r.risk_rule_id == rule_id), self.rules[0] if self.rules else None)
        self.status_filter = None
        self.verdicts = []
        self.setWindowTitle("위험 기준 판정")
        outer = self.body
        outer.setSpacing(12)
        family_id = self.rule.family_id if self.rule else None
        family = next((family_display_name(p.family_name) for p in data.versions.pairs if p.family_id == family_id), "계열 미확인")
        overview = data.catalog().overview(entry.item, ()) if entry else None
        first = next((f.first_included for f in overview.families if f.family_id == family_id), None) if overview else None
        meta = QHBoxLayout()
        meta.setSpacing(6)
        meta.addWidget(StatusBadge(family))
        first_chip = StatusBadge(f"최초 포함 버전 {first.version_raw if first else '미확인'}")
        first_chip.setToolTip("" if first else FIRST_HINT)
        meta.addWidget(first_chip)
        meta.addStretch()
        outer.addLayout(meta)
        self.selector = choice("위험 기준 선택")
        self.selector.setFixedHeight(34)
        for rule in self.rules:
            self.selector.addItem((rule.display_name or rule.risk_reason) + ("" if rule.is_enabled else f" ({DISABLED_CAPTION})"),
                                  rule.risk_rule_id)
        self.selector.setCurrentIndex(max(0, self.selector.findData(rule_id)))
        self.rule_name = label("", 13, color=SECONDARY, wrap=True)
        self.selector.setVisible(len(self.rules) > 1)
        outer.addWidget(self.selector)
        outer.addWidget(self.rule_name)
        # 조건 설명: 종류 칩 하나와 한두 문장만 회색 박스에 둔다.
        self.condition_box = QFrame()
        self.condition_box.setObjectName("ruleCondition")
        self.condition_box.setStyleSheet("QFrame#ruleCondition {background:#f7f9fc;border:1px solid #e5eaf3;border-radius:8px;}")
        condition = QVBoxLayout(self.condition_box)
        condition.setContentsMargins(14, 10, 14, 10)
        condition.setSpacing(6)
        self.kind_chip = StatusBadge("", "info")
        self.state_chip = StatusBadge(DISABLED_CAPTION)
        self.state_chip.setToolTip("해제한 기준은 위험 후보 판정에 쓰지 않습니다. '다시 사용'으로 되돌릴 수 있습니다.")
        self.toggle_button = button("기준 해제")
        self.toggle_button.setFixedHeight(30)
        self.toggle_button.clicked.connect(self._toggle_clicked)
        self.condition_text = label("", 13, wrap=True)
        self.scope_text = label("", 12, color=SECONDARY, wrap=True)
        top = QHBoxLayout()
        top.addWidget(self.kind_chip)
        top.addWidget(self.state_chip)
        top.addStretch()
        top.addWidget(self.toggle_button)
        condition.addLayout(top)
        condition.addWidget(self.condition_text)
        condition.addWidget(self.scope_text)
        outer.addWidget(self.condition_box)
        # 판정별 대수: 누르면 그 판정만 거른다. 한 번 더 누르면 전체로 돌아간다.
        self.tiles = QWidget()
        tiles = QHBoxLayout(self.tiles)
        tiles.setContentsMargins(0, 0, 0, 0)
        tiles.setSpacing(8)
        self.tile_buttons, self.tile_values = {}, {}
        for status, caption in STATUS_CAPTIONS.items():
            tile = _Tile(f"{caption} 설비만 보기")
            background, ink, border = TONES[STATUS_TONES[status]]
            tile.set_tone(background, border)
            column = QVBoxLayout(tile)
            column.setContentsMargins(14, 8, 14, 8)
            column.setSpacing(0)
            value = label("0", 18, medium=True)
            value.setStyleSheet(f"color:{ink};")
            name = label(caption, 12, color=SECONDARY)
            for widget in (value, name):
                widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                column.addWidget(widget)
            if status is RiskStatus.UNKNOWN:
                tile.setToolTip(UNKNOWN_HINT)
            tile.clicked.connect(lambda checked=False, s=status: self._toggle_status(s if checked else None))
            tiles.addWidget(tile, 1)
            self.tile_buttons[status], self.tile_values[status] = tile, value
        outer.addWidget(self.tiles)
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
        self.table = read_table(self.model, "기준별 설비 판정 결과", (110, 150, 100, 360))
        self.table.fit_to_viewport = True
        self.table.setMinimumHeight(180)
        self.table.verticalHeader().setMinimumSectionSize(40)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setItemDelegateForColumn(2, _VerdictDelegate(self.table))
        outer.addWidget(self.table, 1)
        # 자료가 없으면 표와 필터 대신 안내 한 블록만 둔다. 조건 박스(실선·회색 배경)와 달리 점선 테두리·가운데 정렬로 구분한다.
        self.notice = QFrame()
        self.notice.setObjectName("verdictNotice")
        self.notice.setStyleSheet("QFrame#verdictNotice {background:white;border:1px dashed #c9d1e0;border-radius:8px;}")
        notice = QVBoxLayout(self.notice)
        notice.setContentsMargins(16, 22, 16, 22)
        notice.setSpacing(6)
        self.notice_icon = QLabel()
        self.notice_icon.setPixmap(warning_icon("#b8c1d3", 22).pixmap(22, 22))
        self.notice_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice_text = label("", 13, color=SECONDARY, wrap=True)
        self.notice_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        notice.addWidget(self.notice_icon)
        notice.addWidget(self.notice_text)
        outer.addWidget(self.notice)
        outer.addStretch(0)
        footer = QHBoxLayout()
        has_original = any(r.item_id == item_id for r in data.versions.original_rows)
        self.source_button = button("원본 행 보기" if has_original else "출처 정보 보기")
        self.source_button.setEnabled(entry is not None)
        self.source_button.clicked.connect(lambda: show_item_source(self, data, item_id))
        self.close_button = button("닫기", primary=True)
        self.close_button.clicked.connect(self.accept)
        # 등록 결과를 대시보드 위험 후보 목록과 잇는다. 이동 여부는 닫힌 뒤 화면이 dashboard_ids로 판단한다.
        self.dashboard_ids = None
        self.dashboard_button = button("대시보드 위험 후보 보기")
        self.dashboard_button.setToolTip("이 기준의 위험 후보 중 현재 대시보드 조회 범위에 표시되는 설비를 목록으로 엽니다.")
        self.dashboard_button.clicked.connect(self._open_dashboard)
        footer.addWidget(self.source_button)
        footer.addWidget(self.dashboard_button)
        footer.addStretch()
        footer.addWidget(self.close_button)
        outer.addLayout(footer)
        self.selector.currentIndexChanged.connect(self._rule_changed)
        for combo in (self.line, self.group):
            combo.currentIndexChanged.connect(self._render)
        self.site.currentIndexChanged.connect(self._site_changed)
        self._rule_changed()
        has_table = not self.table.isHidden()
        # 화면이 낮아도 하단 버튼 줄이 잘리지 않도록 높이를 화면에 맞추고, 남는 높이는 표가 흡수한다.
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry().height() - 60 if screen else 700
        self.resize(780, max(self.minimumSizeHint().height(), min(700 if has_table else 440, available)))

    def _rule_changed(self, *_):
        self.rule = next((r for r in self.rules if r.risk_rule_id == self.selector.currentData()), self.rule)
        rule, data = self.rule, self.data
        self.status_filter = None
        for tile in self.tile_buttons.values():
            tile.setChecked(False)
        name = (rule.display_name or rule.risk_reason) if rule else ""
        self.rule_name.setText(name)
        self.rule_name.setVisible(len(self.rules) == 1 and not repeats_item(name, self.content))
        self.verdicts = rule_verdicts(data, rule) if rule else []
        if rule:
            self.state_chip.setVisible(not rule.is_enabled)
            self.toggle_button.setText("기준 해제" if rule.is_enabled else "다시 사용")
            self.toggle_button.setAccessibleName(("위험 기준 해제 · " if rule.is_enabled else "위험 기준 다시 사용 · ") + name)
            self.toggle_button.setEnabled(self.toggle_allowed)
            self.toggle_button.setToolTip("" if self.toggle_allowed else "DB에 연결된 경우에만 기준을 해제·다시 사용할 수 있습니다.")
            self.kind_chip.setText(category(rule))
            self.condition_text.setText(CONDITIONS[rule.rule_type])
            names = {p.model_id: p.model_name for p in data.versions.pairs}
            model_ids = rule.target_model_ids or ((rule.group_id,) if rule.group_id is not None else ())
            family = next((family_display_name(p.family_name) for p in data.versions.pairs if p.family_id == rule.family_id), "미확인 계열")
            self.scope_text.setText(" · ".join((family, ", ".join(names.get(m, str(m)) for m in model_ids) or "계열 전체",
                                                rule.target_site or "전체 Site", rule.target_line or "전체 Line")))
        has_rows = bool(self.verdicts)
        if rule is None:
            message = "이 Item에 등록된 위험 기준이 없습니다."
        elif not rule.is_enabled:
            message = "해제된 위험 기준이라 판정하지 않습니다. '다시 사용'을 누르면 위험 후보 판정에 다시 반영됩니다."
        elif not data.observations_connected:
            message = "설비 관측 자료가 없어 판정할 설비가 없습니다."
        elif not has_rows:
            message = "이 위험 기준이 적용되는 설비가 없습니다."
        else:
            message = ""
        self.notice_text.setText(message)
        self.notice.setVisible(bool(message))
        for widget in (self.tiles, self.filters, self.table):
            widget.setVisible(has_rows)
        self.dashboard_button.setEnabled(any(v[1].status is RiskStatus.RISK for v in self.verdicts))
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

    def _toggle_clicked(self):
        """확인 창에서 '해제'·'다시 사용'을 고른 경우에만 요청을 남기고 닫는다. 취소·창 닫기는 아무것도 바꾸지 않는다."""
        rule = self.rule
        if rule is None or not self.toggle_allowed or self.toggle_request is not None:
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

    def _open_dashboard(self):
        self.dashboard_ids = tuple(dict.fromkeys(r.equipment_id for r, res, _ in self.verdicts if res.status is RiskStatus.RISK))
        self.accept()

    def _site_changed(self, *_):
        site = self.site.currentData()
        with QSignalBlocker(self.line):
            self.line.clear()
            self.line.addItem("전체 Line", None)
            for pair in sorted({(r.site, r.line) for r, _, _ in self.verdicts if r.line and (site is None or r.site == site)}):
                self.line.addItem(pair[1] if site is not None else f"{pair[0]} · {pair[1]}", pair)
        self._render()

    def _toggle_status(self, status):
        self.status_filter = status
        for key, tile in self.tile_buttons.items():
            with QSignalBlocker(tile):
                tile.setChecked(key is status)
        self._render()

    def _render(self, *_):
        site, line, group = self.site.currentData(), self.line.currentData(), self.group.currentData()
        scoped = [v for v in self.verdicts
                  if (site is None or v[0].site == site) and (line is None or (v[0].site, v[0].line) == line)
                  and (group is None or any(pm.group_id == group for pm in v[0].pms))]
        for status, value in self.tile_values.items():
            value.setText(f"{sum(v[1].status is status for v in scoped)}대")
        shown = [v for v in scoped if self.status_filter is None or v[1].status is self.status_filter]
        self.model.statuses = tuple(v[1].status for v in shown)
        self.model.set_rows(
            [(r.equipment_code, f"{r.site} · {r.line or '라인 미지정'}", STATUS_CAPTIONS[res.status], reason) for r, res, reason in shown],
            tuple(r.equipment_id for r, _, _ in shown))
