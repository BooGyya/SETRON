"""VER5 Item 관리: 등록 기준과 물리 설비별 적용·결함 상태를 같은 판정기로 표시한다."""

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLineEdit, QTabBar, QVBoxLayout, QWidget,
)

from setron.features.risk.schemas import MappingStatus, RiskStatus, RuleType
from setron.features.risk.item_scope import targets
from setron.features.risk.service import RiskAssessor
from setron.ui.components import Card, button, label
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.item_original_table import show_item_source
from setron.ui.item_risk_dialog import ItemRiskDialog, choice, field
from setron.ui.query_panel import _WrapRow
from setron.ui.theme import SECONDARY


def category(rule):
    return rule.category or {RuleType.SAFETY_REQUIRED: "환경안전 필수", RuleType.DEFECT_ITEM: "결함 버전",
                             RuleType.DIRECT_VERSION: "결함 버전"}[rule.rule_type]


class ItemRiskPage(QWidget):
    page_size = 15

    def __init__(self):
        super().__init__()
        self.data, self.gateway, self.context_provider, self.submit = None, None, None, None
        self.pending_rule_id = None
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
        self.add_button = button("+ 위험 기준 등록", primary=True)
        self.add_button.setFixedHeight(40)
        self.add_button.clicked.connect(self.open_add)
        self.filter_row = _WrapRow([field("Site", self.site), field("Line", self.line), field("설비군", self.group),
                                   field("위험 아이템", self.rule), field("검색", self.search)], 170, fields=True)
        filters.body.addWidget(self.filter_row)
        self.filters = filters
        card = Card(padding=24)
        card.body.setContentsMargins(24, 20, 24, 20)
        head = QHBoxLayout()
        head.addWidget(label("등록된 위험 기준", 17, medium=True))
        self.rule_count = label("", 12, color=SECONDARY, wrap=True)
        head.addWidget(self.rule_count, 1)
        head.addWidget(self.add_button)
        card.body.addLayout(head)
        self.rule_rows = QVBoxLayout()
        self.rule_rows.setSpacing(2)
        self.rule_buttons = {}
        card.body.addLayout(self.rule_rows)
        self.no_rules = label("", 13, color=SECONDARY, wrap=True)
        card.body.addWidget(self.no_rules)
        body.addWidget(card)
        card = Card(padding=24)
        self.detail_card = card
        card.body.setContentsMargins(24, 20, 24, 20)
        row = QHBoxLayout()
        self.expanded_id = None

        self.count = label("", 12, color=SECONDARY, wrap=True)
        self.condition = label("", 13, wrap=True)
        self.source_button = button("원본 행 보기")
        self.source_button.clicked.connect(self.open_source)
        self.status_filter = QTabBar()
        self.status_filter.setAccessibleName("설비 판정 결과")
        self.status_filter.setExpanding(False)
        self.status_filter.setDrawBase(False)
        self.status_filter.setFixedHeight(38)
        self.status_filter.setStyleSheet("QTabBar::tab {padding:6px 14px;margin-right:4px;border:1px solid transparent;border-radius:5px;}"
                                        "QTabBar::tab:selected {background:#edf0fd;color:#3652ca;}")
        self.states = ((RiskStatus.RISK, "위험 후보"), (RiskStatus.UNKNOWN, "판정 불가"), (RiskStatus.NO_RISK, "위험 없음"))
        for state, caption in self.states:
            self.status_filter.addTab(caption)
        self.status_filter.currentChanged.connect(self.filter_changed)
        self.displayed_rule = None
        row.addWidget(self.status_filter)
        row.addStretch()
        row.addWidget(self.count)
        card.body.addLayout(row)
        self.model = ReadTableModel(("설비명", "사업장 · 라인", "설비군", "관측 버전", "판정 근거"))
        self.table = read_table(self.model, "기준별 설비 판정 결과", (90, 120, 100, 150, 275))
        self.table.fit_to_viewport = True
        self.table.horizontalHeader().moveSection(4, 2)
        self.table.clicked.connect(self.toggle_equipment)
        self.table.activated.connect(lambda index: self.toggle_equipment(index) if self.expanded_id is None else None)
        card.body.addWidget(self.table)

        self.empty = label("", 13, color=SECONDARY, wrap=True)
        card.body.addWidget(self.empty)
        self.pager = QWidget()
        footer = QHBoxLayout(self.pager)
        footer.setContentsMargins(0, 0, 0, 0)
        self.previous, self.next = button("이전"), button("다음")
        self.page_label = label("", 12)
        self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next.clicked.connect(lambda: self.change_page(1))
        for control in (self.previous, self.page_label, self.next):
            footer.addWidget(control)
        footer.addStretch()
        card.body.addWidget(self.pager)
        tools_row = QHBoxLayout()
        self.info_toggle = button("기준 정보")
        self.filter_toggle = button("설비 필터")
        for control in (self.info_toggle, self.filter_toggle):
            control.setCheckable(True)
            tools_row.addWidget(control)
        tools_row.addStretch()
        card.body.addLayout(tools_row)
        self.info_panel = QWidget()
        info = QVBoxLayout(self.info_panel)
        info.setContentsMargins(0, 0, 0, 0)
        info.addWidget(self.condition)
        info.addWidget(self.source_button, alignment=Qt.AlignmentFlag.AlignLeft)
        card.body.addWidget(self.info_panel)
        card.body.addWidget(filters)
        self.info_panel.hide()
        filters.hide()
        self.info_toggle.toggled.connect(self.info_panel.setVisible)
        self.filter_toggle.toggled.connect(filters.setVisible)
        self.rule_rows.addWidget(card)
        card.hide()
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
        if data and self.pending_rule_id is not None:
            self.rule.setCurrentIndex(max(0, self.rule.findData(self.pending_rule_id)))
            self.pending_rule_id = None
        self.render_cards()
        self._site_changed()

    def _site_changed(self, *_):
        records = self.data.records if self.data else ()
        site = self.site.currentData()
        options = sorted({(r.site, r.line) for r in records if r.line and (site is None or r.site == site)})
        self.fill(self.line, "전체", ((pair, f"{pair[0]} · {pair[1]}" if site is None else pair[1]) for pair in options))
        self.filter_changed()

    def filter_changed(self, *_):
        self.page_index = 0
        self.expanded_id = None
        selected = self.rule.currentData() is not None
        rule_id = self.rule.currentData()
        if rule_id != self.displayed_rule:
            self.info_toggle.setChecked(False)
            self.filter_toggle.setChecked(False)
        for rid, control in self.rule_buttons.items():
            control.setChecked(rid == rule_id)
        anchor = self.rule_buttons.get(rule_id)
        if anchor and self.rule_rows.indexOf(self.detail_card) != self.rule_rows.indexOf(anchor) + 1:
            self.rule_rows.removeWidget(self.detail_card)
            self.rule_rows.insertWidget(self.rule_rows.indexOf(anchor) + 1, self.detail_card)
        self.detail_card.setVisible(selected)
        if selected:
            self.render()

    def change_page(self, delta):
        self.page_index += delta
        self.render()

    def selected_rules(self):
        selected = self.rule.currentData()
        return tuple(r for r in self.data.risk_rules if selected is None or r.risk_rule_id == selected) if self.data else ()

    def render_cards(self):
        for control in self.rule_buttons.values():
            self.rule_rows.removeWidget(control)
            control.hide()
            control.deleteLater()
        self.rule_buttons.clear()
        rules = self.data.risk_rules if self.data else ()
        catalog = self.data.catalog() if self.data else None
        for rule in rules:
            if not rule.is_enabled:
                outcome = "사용 중지"
            elif self.data.observations_connected:
                assessor = RiskAssessor(catalog.families, catalog, (rule,))
                statuses = [assessor.equipment_risk(r).status for r in self.data.records if not r.is_hpa and targets(rule, r)]
                outcome = f"위험 후보 {statuses.count(RiskStatus.RISK)}대 · 판정 불가 {statuses.count(RiskStatus.UNKNOWN)}대"
            else:
                outcome = "설비 관측 자료 미연결"
            title = rule.display_name or rule.risk_reason
            show = button(f"{title}    ·    {outcome}".replace("&", "&&"))
            show.setCheckable(True)
            show.setAccessibleName(f"{title} 판정 결과 보기")
            show.setToolTip(title)
            show.setMinimumWidth(0)
            show.setStyleSheet("QPushButton {text-align:left;min-height:42px;padding:0 14px;border:1px solid transparent;border-radius:5px;}"
                               "QPushButton:checked {background:#edf0fd;} QPushButton:hover {background:#f0f3f8;}")
            show.clicked.connect(lambda checked=False, rid=rule.risk_rule_id: self.select_rule(rid if checked else None))
            self.rule_rows.addWidget(show)
            self.rule_buttons[rule.risk_rule_id] = show
        self.rule_count.setText(f"{len(rules)}건")
        self.no_rules.setVisible(not rules)
        self.no_rules.setText("등록된 위험 기준이 없습니다. 위에서 첫 기준을 등록하세요." if self.data else "자료 조회 결과 미확인")

    def open_source(self):
        rules = self.selected_rules()
        if len(rules) == 1 and rules[0].item_id is not None:
            show_item_source(self, self.data, rules[0].item_id)

    def toggle_equipment(self, index):
        if index.isValid() and index.row() < len(self.model.ids):
            equipment_id = self.model.ids[index.row()]
            scrollbar = self.table.verticalScrollBar()
            offset = scrollbar.value()
            self.expanded_id = None if self.expanded_id == equipment_id else equipment_id
            self.render()
            self.table.selectRow(self.model.ids.index(equipment_id))
            scrollbar.setValue(offset)

    def select_rule(self, rule_id):
        with QSignalBlocker(self.site), QSignalBlocker(self.group), QSignalBlocker(self.search), QSignalBlocker(self.rule):
            self.site.setCurrentIndex(0)
            self.group.setCurrentIndex(0)
            self.search.clear()
            self.rule.setCurrentIndex(max(0, self.rule.findData(rule_id)))
        with QSignalBlocker(self.line):
            self.line.setCurrentIndex(0)
        self.displayed_rule = None
        self._site_changed()
        scroll = getattr(self.window(), "scroll", None)
        if hasattr(scroll, "ensureWidgetVisible") and self.detail_card.isVisible():
            QTimer.singleShot(0, self, lambda: scroll.ensureWidgetVisible(self.status_filter))

    def render(self):
        data = self.data
        rules = self.selected_rules()
        site, line, group, text = self.site.currentData(), self.line.currentData(), self.group.currentData(), self.search.text().casefold().strip()
        catalog = data.catalog() if data else None
        assessor = RiskAssessor(catalog.families, catalog, rules) if data else None
        records = []
        for record in data.records if data and rules and rules[0].is_enabled else ():
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
        self.count.setText(f"관측일 {data.base_date} · 대상 {len(records)}대" if data and data.observations_connected else "설비 관측 자료 미연결")
        selected_rule = rules[0] if rules else None
        if selected_rule:
            self.source_button.setVisible(selected_rule.item_id is not None)
            condition = {
                RuleType.SAFETY_REQUIRED: "대상 PM 중 하나라도 필수 아이템이 미적용이면 위험 후보입니다.",
                RuleType.DIRECT_VERSION: "대상 PM이 지정된 위험 버전을 사용하면 위험 후보입니다.",
                RuleType.DEFECT_ITEM: "지정한 버전 범위의 대상 PM에 결함 아이템이 포함되면 위험 후보입니다.",
            }[selected_rule.rule_type]
            family = next((p.family_name for p in data.versions.pairs if p.family_id == selected_rule.family_id), "미확인 계열")
            entry = next((e for e in data.versions.items if e.item.item_id == selected_rule.item_id), None)
            evidence = ""
            if entry:
                has_original = any(r.item_id == entry.item.item_id for r in data.versions.original_rows)
                self.source_button.setText("원본 행 보기" if has_original else "출처 정보 보기")
                inclusion = next((f for f in catalog.overview(entry.item, ()).families if f.family_id == selected_rule.family_id), None)
                first = inclusion.first_included if inclusion else None
                evidence = f"\n{entry.item.content} · 최초 확인 포함 {first.version_raw if first else '미확인'}"
            names = {p.model_id: p.model_name for p in data.versions.pairs}
            model_ids = selected_rule.target_model_ids or ((selected_rule.group_id,) if selected_rule.group_id is not None else ())
            scope = ", ".join(names.get(mid, str(mid)) for mid in model_ids) or "계열 전체"
            releases = {e.release.release_version_id: e.release.version_raw for e in data.versions.releases}
            if selected_rule.rule_type is RuleType.DIRECT_VERSION:
                evidence = f"\n지정 버전 {releases.get(selected_rule.release_version_id, '미확인')}"
            elif selected_rule.rule_type is RuleType.DEFECT_ITEM:
                evidence += f"\n결함 범위 {releases.get(selected_rule.start_release_version_id, '미확인')} ~ {releases.get(selected_rule.end_release_version_id, '해제 없음')}"
            self.condition.setText(f"{category(selected_rule)} · {condition}\n{family} · {scope} · {selected_rule.target_site or '전체 사업장'} · {selected_rule.target_line or '전체 라인'}{evidence}")
        counts = {state: sum(result.status is state for _, _, result in records) for state, _ in self.states}
        with QSignalBlocker(self.status_filter):
            for index, (state, caption) in enumerate(self.states):
                self.status_filter.setTabText(index, f"{caption} {counts[state]}대" if data and data.observations_connected else caption)
            if self.displayed_rule != self.rule.currentData():
                self.displayed_rule = self.rule.currentData()
                self.status_filter.setCurrentIndex(next((i for i, (state, _) in enumerate(self.states) if counts[state]), 0))
        selected_status = self.states[self.status_filter.currentIndex()][0]
        # 같은 조건에서는 빈 탭이나 긴 근거를 열어도 바깥 페이지의 높이가 변하지 않는다.
        table_height = 48 + 82 * min(5, max(1, len(records)))
        records = [v for v in records if v[2].status is selected_status]
        pages = max(1, (len(records) + self.page_size - 1) // self.page_size)
        self.page_index = min(max(self.page_index, 0), pages - 1)
        visible = records[self.page_index * self.page_size:(self.page_index + 1) * self.page_size]
        rows = []
        for record, relevant, result in visible:
            badges = []
            for rule in relevant:
                findings = [f for f in result.findings if f.rule.risk_rule_id == rule.risk_rule_id]
                # PM별 Item 상태는 한 설비로 합산하기 전에도 빠짐없이 표시한다.
                pms = [pm for pm in record.pms if pm.family_id == rule.family_id
                       and (rule.group_id is None or rule.group_id == pm.group_id)
                       and (not rule.target_model_ids or pm.group_id in rule.target_model_ids)]
                for pm in pms:
                    if rule.rule_type is RuleType.SAFETY_REQUIRED and rule.item_id is not None:
                        pm_state = catalog.pm_application(rule.item_id, pm)
                        caption = pm_state.label
                        if rule.mapping_status is not MappingStatus.VERIFIED:
                            caption = "미확인"
                    else:
                        pm_findings = [f for f in findings if f.pm == pm]
                        caption = "결함" if any(f.status is RiskStatus.RISK for f in pm_findings) else "미확인" if pm_findings else "해당 없음"
                    reason = (pm_state.basis_label if rule.rule_type is RuleType.SAFETY_REQUIRED and rule.item_id is not None and rule.mapping_status is MappingStatus.VERIFIED else
                              next((f.message for f in findings if f.pm == pm), caption))
                    badges.append((pm, caption, reason))
            status = "위험 기준 없음" if not rules else "대상 외" if not relevant else {RiskStatus.RISK: "위험 후보", RiskStatus.UNKNOWN: "판정 불가", RiskStatus.NO_RISK: "위험 없음"}[result.status]
            if badges and all(caption == "대상 외" for _, caption, _ in badges):
                status = "대상 외"
            if record.equipment_id != self.expanded_id and result.findings:
                affected = [finding.pm for finding in result.findings]
                badges = [badge for badge in badges if badge[0] in affected]
            versions, reasons = {}, {}
            for pm, caption, reason in badges:
                name = pm.pm_name or "공통 SW"
                versions.setdefault(pm.version_raw or "버전 없음", []).append(name)
                reasons.setdefault(reason, []).append(name)
            version_text = "\n".join(f"{', '.join(names)} · {version}" for version, names in versions.items())
            detail = record.equipment_id == self.expanded_id or len(reasons) > 1
            reason_text = "\n".join(f"{', '.join(names)} · {reason}" if detail else reason for reason, names in reasons.items())
            rows.append((record.equipment_code, f"{record.site} · {record.line or '라인 미지정'}", record.model_name,
                         version_text, reason_text or status))
        self.model.set_rows(rows, tuple(r.equipment_id for r, _, _ in visible))
        self.table.resizeRowsToContents()
        self.table.setFixedHeight(table_height)
        self.empty.setText(("조건에 맞는 설비가 없습니다." if data and data.observations_connected else "설비 관측 자료 미연결")
                           if not rows else "")
        self.empty.setVisible(not rows)
        self.pager.setVisible(pages > 1)
        self.previous.setEnabled(self.page_index > 0)
        self.next.setEnabled(self.page_index + 1 < pages)
        self.page_label.setText(f"{self.page_index + 1} / {pages}")

    def open_add(self, checked=False, *, item_id=None, family_id=None):
        if self.data is None or self.gateway is None or self.submit is None:
            return False
        dialog = ItemRiskDialog(self, self.data, self.gateway, self.context_provider, self.submit,
                                family_id=family_id, item_id=item_id)
        return bool(dialog.exec())
