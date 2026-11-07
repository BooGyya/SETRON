"""VER5 Item 관리: 등록 기준과 물리 설비별 적용·결함 상태를 같은 판정기로 표시한다."""

from PySide6.QtCore import QRectF, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLineEdit, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QVBoxLayout, QWidget,
)

from setron.features.item.schemas import Application
from setron.features.risk.schemas import MappingStatus, RiskStatus, RuleType
from setron.features.risk.service import RiskAssessor
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.equipment import FlowGrid, ReadTableModel, read_table
from setron.ui.item_risk_dialog import ItemRiskDialog, choice, field
from setron.ui.query_panel import _WrapRow
from setron.ui.theme import SECONDARY, TONES, font


class ItemStatusDelegate(QStyledItemDelegate):
    """상태 글자를 한 번만 그린다. 폰트 실측으로 배지·행·열 크기를 보장한다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tones = ()

    @staticmethod
    def required_size(text):
        metrics = QFontMetrics(font(12, medium=True))
        # 배지: 좌우 8px/상하 3px + 테두리. 셀: 좌우 8px/상하 4px.
        return QSize(metrics.horizontalAdvance(text) + 34, metrics.height() + 16)

    def sizeHint(self, option, index):
        return self.required_size(str(index.data() or ""))

    def paint(self, painter, option, index):
        base = QStyleOptionViewItem(option)
        self.initStyleOption(base, index)
        base.text = ""  # 기본 셀 텍스트와 배지 텍스트를 중복 출력하지 않는다.
        style = base.widget.style() if base.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, base, painter, base.widget)
        text = str(index.data() or "")
        if not text:
            return
        tone = self.tones[index.row()] if index.row() < len(self.tones) else "neutral"
        background, ink, border = TONES[tone]
        painter.save()
        painter.setFont(font(12, medium=True))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        metrics = painter.fontMetrics()
        available = option.rect.adjusted(8, 4, -8, -4)
        if available.width() <= 0 or available.height() <= 0:
            painter.restore()
            return
        shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, available.width() - 18))
        height = min(available.height(), metrics.height() + 8)
        badge = QRectF(available.left(), available.top() + (available.height() - height) / 2,
                       min(available.width(), metrics.horizontalAdvance(shown) + 18), height)
        painter.setPen(QColor(border))
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(badge.adjusted(.5, .5, -.5, -.5), 4, 4)
        painter.setPen(QColor(ink))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, shown)
        painter.restore()


def category(rule):
    return rule.category or {RuleType.SAFETY_REQUIRED: "환경안전 필수", RuleType.DEFECT_ITEM: "결함 버전",
                             RuleType.DIRECT_VERSION: "결함 버전"}[rule.rule_type]


def targets(rule, record):
    return ((rule.target_site is None or record.site == rule.target_site)
            and (rule.target_line is None or record.line == rule.target_line)
            and any(pm.family_id == rule.family_id and (rule.group_id is None or pm.group_id == rule.group_id)
                    and (not rule.target_model_ids or pm.group_id in rule.target_model_ids) for pm in record.pms))


class ItemRiskPage(QWidget):
    page_size = 15

    def __init__(self):
        super().__init__()
        self.data, self.gateway, self.context_provider, self.submit = None, None, None, None
        self.page_index = 0
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        filters = Card(padding=24)
        filters.body.setContentsMargins(24, 20, 24, 20)
        self.site, self.line, self.group, self.rule = [choice(name) for name in ("Item 관리 Site", "Item 관리 Line", "Item 관리 설비군", "위험 아이템")]
        self.search = QLineEdit()
        self.search.setAccessibleName("설비명·아이템 검색")
        self.search.setPlaceholderText("설비명 · 아이템 검색")
        self.search.setFixedHeight(40)
        self.add_button = button("+ 위험 아이템 추가", primary=True)
        self.add_button.setFixedHeight(40)
        self.add_button.clicked.connect(self.open_add)
        self.add_host = QWidget()
        action = QVBoxLayout(self.add_host)
        action.setContentsMargins(0, 0, 0, 0)
        action.setSpacing(0)
        action.addStretch()
        action.addWidget(self.add_button)
        self.filter_row = _WrapRow([field("Site", self.site), field("Line", self.line), field("설비군", self.group),
                                   field("위험 아이템", self.rule), field("검색", self.search), self.add_host], 170, fields=True)
        filters.body.addWidget(self.filter_row)
        body.addWidget(filters)
        card = Card(padding=24)
        card.body.setContentsMargins(24, 20, 24, 20)
        head = QHBoxLayout()
        head.addWidget(label("등록된 위험 아이템", 17, medium=True))
        self.rule_count = label("", 12, color=SECONDARY, wrap=True)
        head.addWidget(self.rule_count, 1)
        card.body.addLayout(head)
        self.cards = FlowGrid(300, max_columns=3, fill=False)
        card.body.addWidget(self.cards)
        self.no_rules = label("", 13, color=SECONDARY, wrap=True)
        card.body.addWidget(self.no_rules)
        body.addWidget(card)
        card = Card(padding=24)
        card.body.setContentsMargins(24, 20, 24, 20)
        row = QHBoxLayout()
        row.addWidget(label("설비별 현재 적용 상태", 17, medium=True))
        self.count = label("", 12, color=SECONDARY, wrap=True)
        row.addWidget(self.count, 1)
        self.risk_badge = StatusBadge("미적용·결함 0대", "danger")
        row.addWidget(self.risk_badge)
        card.body.addLayout(row)
        self.model = ReadTableModel(("설비명", "Site · Line", "설비군", "현재 버전", "위험 아이템 적용 현황", "상태"))
        self.table = read_table(self.model, "Item 관리 설비별 현재 적용 상태", (120, 150, 150, 220, 390, 120))
        self.status_delegate = ItemStatusDelegate(self.table)
        self.table.setItemDelegateForColumn(5, self.status_delegate)
        self.table.minimum_column_widths = {5: self.status_delegate.required_size("위험 기준 없음").width()}
        card.body.addWidget(self.table)
        card.body.addWidget(label("적용됨 · 미적용 · 결함 버전 해당 · 미확인 · 대상 외는 구분합니다. HPA는 분석에서 제외합니다.", 12, color=SECONDARY, wrap=True))
        self.empty = label("", 13, color=SECONDARY, wrap=True)
        card.body.addWidget(self.empty)
        footer = QHBoxLayout()
        self.previous, self.next = button("이전"), button("다음")
        self.page_label = label("", 12)
        self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next.clicked.connect(lambda: self.change_page(1))
        for control in (self.previous, self.page_label, self.next):
            footer.addWidget(control)
        footer.addStretch()
        card.body.addLayout(footer)
        body.addWidget(card)
        self.site.currentIndexChanged.connect(self._site_changed)
        for control in (self.line, self.group, self.rule):
            control.currentIndexChanged.connect(self.filter_changed)
        self.search.textChanged.connect(self.filter_changed)
        self.set_data(None)

    def configure(self, gateway, context_provider, submit):
        self.gateway, self.context_provider, self.submit = gateway, context_provider, submit
        self.add_button.setEnabled(self.data is not None and gateway is not None)
        self.add_button.setToolTip("" if gateway else "DB 저장 경계가 연결되지 않았습니다. 예제 화면은 읽기 전용입니다.")

    @staticmethod
    def fill(combo, title, options):
        current = combo.currentData()
        blocker = QSignalBlocker(combo)
        combo.clear()
        combo.addItem(title, None)
        for key, text in options:
            combo.addItem(text, key)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        del blocker

    def set_data(self, data):
        self.data = data
        self._needs_render = True
        if not self.isVisible():
            return
        self._load_data()

    def showEvent(self, event):
        if getattr(self, "_needs_render", False):
            self._load_data()
        super().showEvent(event)

    def _load_data(self):
        self._needs_render = False
        data = self.data
        records, rules = (data.records, data.risk_rules) if data else ((), ())
        self.fill(self.site, "전체", ((s, s) for s in sorted({r.site for r in records})))
        models = {p.model_id: p.model_name for p in data.versions.pairs} if data else {}
        self.fill(self.group, "전체", sorted(models.items()))
        self.fill(self.rule, f"전체 {len(rules)}개", ((r.risk_rule_id, r.display_name or r.risk_reason) for r in rules))
        self.add_button.setEnabled(data is not None and self.gateway is not None)
        self._site_changed()
        self.render_cards()

    def _site_changed(self, *_):
        records = self.data.records if self.data else ()
        site = self.site.currentData()
        options = sorted({(r.site, r.line) for r in records if r.line and (site is None or r.site == site)})
        self.fill(self.line, "전체", ((pair, f"{pair[0]} · {pair[1]}" if site is None else pair[1]) for pair in options))
        self.filter_changed()

    def filter_changed(self, *_):
        self.page_index = 0
        self.render_cards()
        self.render()

    def change_page(self, delta):
        self.page_index += delta
        self.render()

    def selected_rules(self):
        selected = self.rule.currentData()
        return tuple(r for r in self.data.risk_rules if selected is None or r.risk_rule_id == selected) if self.data else ()

    def render_cards(self):
        widgets = []
        names = {p.model_id: p.model_name for p in self.data.versions.pairs} if self.data else {}
        releases = {e.release.release_version_id: e.release.version_raw for e in self.data.versions.releases} if self.data else {}
        rules = self.selected_rules()
        for rule in rules:
            widget = QWidget()
            widget.setObjectName("riskItemChip")
            widget.setStyleSheet("QWidget#riskItemChip {background:#f4f5f8;border-radius:10px;}")
            layout = QVBoxLayout(widget)
            layout.setContentsMargins(12, 8, 12, 8)
            layout.setSpacing(5)
            row = QHBoxLayout()
            row.addWidget(StatusBadge(category(rule), "warning" if category(rule) == "환경안전 필수" else "danger" if category(rule) == "결함 버전" else "neutral"))
            title = label(rule.display_name or rule.risk_reason, 13, medium=True, wrap=True)
            title.setMinimumWidth(0)
            row.addWidget(title, 1)
            layout.addLayout(row)
            scope = " · ".join(names.get(mid, f"모델 {mid}") for mid in rule.target_model_ids) if rule.target_model_ids else names.get(rule.group_id, "해당 계열 전체")
            basis = ("필수 Item · 누적 적용" if rule.rule_type is RuleType.SAFETY_REQUIRED else
                     f"위험 버전 {releases.get(rule.release_version_id, '미확인')}" if rule.rule_type is RuleType.DIRECT_VERSION else
                     f"결함 {releases.get(rule.start_release_version_id, '미확인')} ~ {releases.get(rule.end_release_version_id, '해제 없음')}")
            layout.addWidget(label(f"{basis} · 대상 {scope}\n{rule.target_site or '전체 Site'} · {rule.target_line or '전체 Line'}", 11, color=SECONDARY, wrap=True))
            widget.setToolTip(rule.risk_reason + ("\n메모: " + rule.memo if rule.memo else ""))
            widgets.append(widget)
        self.cards.set_widgets(widgets)
        self.rule_count.setText(f"{len(rules)}개 · 이 기준으로 설비별 적용 여부를 판정합니다")
        self.no_rules.setVisible(not rules)
        self.no_rules.setText("등록된 위험 기준 없음 · 안전 판정이 아닙니다." if self.data else "자료 조회 결과 미확인")

    def render(self):
        data = self.data
        rules = self.selected_rules()
        site, line, group, text = self.site.currentData(), self.line.currentData(), self.group.currentData(), self.search.text().casefold().strip()
        catalog = data.catalog() if data else None
        assessor = RiskAssessor(catalog.families, catalog, rules) if data else None
        records = []
        for record in data.records if data else ():
            if record.is_hpa or site is not None and record.site != site or line is not None and (record.site, record.line) != line:
                continue
            if group is not None and not any(pm.group_id == group for pm in record.pms):
                continue
            relevant = tuple(r for r in rules if targets(r, record))
            if self.rule.currentData() is not None and not relevant:
                continue
            if text and text not in (record.equipment_code + " " + record.model_name + " " + " ".join((r.display_name or r.risk_reason) for r in relevant)).casefold():
                continue
            result = assessor.equipment_risk(record)
            records.append((record, relevant, result))
        rank = {RiskStatus.RISK: 0, RiskStatus.UNKNOWN: 1, RiskStatus.NO_RISK: 2}
        records.sort(key=lambda value: (rank[value[2].status], value[0].equipment_code, value[0].equipment_id))
        risk_count = sum(result.status is RiskStatus.RISK for _, _, result in records)
        self.risk_badge.setText(f"미적용·결함 해당 {risk_count}대")
        self.count.setText(f"관측일 {data.base_date if data else '미확인'} · {len(records)}대 · HPA 제외 {sum(r.is_hpa for r in data.records) if data else 0}대")
        pages = max(1, (len(records) + self.page_size - 1) // self.page_size)
        self.page_index = min(max(self.page_index, 0), pages - 1)
        visible = records[self.page_index * self.page_size:(self.page_index + 1) * self.page_size]
        rows, chips, statuses = [], [], []
        for record, relevant, result in visible:
            badges = []
            for rule in relevant:
                findings = [f for f in result.findings if f.rule.risk_rule_id == rule.risk_rule_id]
                # PM별 Item 상태는 한 설비로 합산하기 전에도 빠짐없이 표시한다.
                pms = [pm for pm in record.pms if pm.family_id == rule.family_id
                       and (rule.group_id is None or rule.group_id == pm.group_id)
                       and (not rule.target_model_ids or pm.group_id in rule.target_model_ids)]
                for pm in pms:
                    name = (rule.display_name or rule.risk_reason)[:35]
                    if rule.rule_type is RuleType.SAFETY_REQUIRED and rule.item_id is not None:
                        application = catalog.pm_application(rule.item_id, pm).application
                        caption, tone = {Application.APPLIED: ("적용", "success"), Application.NOT_APPLIED: ("미적용", "warning"),
                                         Application.UNKNOWN: ("미확인", "neutral"), Application.NOT_TARGET: ("대상 외", "neutral")}[application]
                        if rule.mapping_status is not MappingStatus.VERIFIED:
                            caption, tone = "미확인", "neutral"
                    else:
                        pm_findings = [f for f in findings if f.pm == pm]
                        caption, tone = ("결함", "danger") if any(f.status is RiskStatus.RISK for f in pm_findings) else ("미확인", "neutral") if pm_findings else ("해당 없음", "success")
                    badges.append((f"{caption} {name} · {pm.pm_name or '공통 SW'}", tone))
            status = "위험 기준 없음" if not rules else "대상 외" if not relevant else {RiskStatus.RISK: "위험 후보", RiskStatus.UNKNOWN: "미확인", RiskStatus.NO_RISK: "적용 완료"}[result.status]
            if badges and all(text.startswith("대상 외") for text, _ in badges):
                status = "대상 외"
            rows.append((record.equipment_code, f"{record.site} · {record.line or 'Line 미지정'}", record.model_name,
                         "\n".join(f"{pm.pm_name or '공통 SW'} · {pm.version_raw or '버전 없음'}" for pm in record.pms),
                         "\n".join(b[0] for b in badges) or status, status))
            chips.append(badges)
            statuses.append((status, "danger" if status == "위험 후보" else "success" if status == "적용 완료" else "neutral"))
        self.status_delegate.tones = tuple(tone for _, tone in statuses)
        self.model.set_rows(rows, tuple(r.equipment_id for r, _, _ in visible))
        self.table.resizeRowsToContents()
        for index, badges in enumerate(chips):
            if badges:
                host = _WrapRow([StatusBadge(text, tone) for text, tone in badges], 170)
                self._opaque_cell(host)
                self.table.setIndexWidget(self.model.index(index, 4), host)
                self.table.setRowHeight(index, max(self.table.rowHeight(index), host.sizeHint().height() + 16))
        self.table.setFixedHeight(max(100, min(720, 44 + sum(self.table.rowHeight(i) for i in range(len(rows))))))
        self.empty.setVisible(not rows)
        self.empty.setText("조건에 맞는 설비가 없습니다." if data and data.observations_connected else "설비 관측 자료 미연결 · 0대/안전 확정 아님")
        self.previous.setEnabled(self.page_index > 0)
        self.next.setEnabled(self.page_index + 1 < pages)
        self.page_label.setText(f"{self.page_index + 1} / {pages}")

    @staticmethod
    def _opaque_cell(widget):
        # indexWidget 아래의 기본 셀 텍스트가 겹쳐 보이지 않게 셀 배경을 명시한다.
        widget.setObjectName("itemRiskCell")
        palette = widget.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("white"))
        widget.setPalette(palette)
        widget.setAutoFillBackground(True)

    def open_add(self):
        if self.data is None or self.gateway is None or self.submit is None:
            return False
        parent = self.parentWidget()
        item_id = getattr(parent, "selected_id", None)
        answer = getattr(parent, "answer", None)
        if item_id is None and answer is not None and len(answer.item_ids) == 1:
            item_id = answer.item_ids[0]
        dialog = ItemRiskDialog(self, self.data, self.gateway, self.context_provider, self.submit,
                                family_id=getattr(parent, "family_id", None), item_id=item_id)
        return bool(dialog.exec())
