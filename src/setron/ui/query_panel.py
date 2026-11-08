"""VER3 공통 조회 패널. 모든 조작은 초안만 바꾸며 조회 적용 시에만 신호를 보낸다."""

from dataclasses import replace
from datetime import date

from PySide6.QtCore import QDate, QSize, Qt, Signal
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLayout, QSizePolicy, QVBoxLayout, QWidget

from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.calendar_widgets import DateEdit
from setron.ui.query_state import UNASSIGNED, QueryCatalog, QuerySelection, QueryState, version_filter_caption
from setron.ui.theme import INK, SECONDARY


class _WrapRow(QWidget):
    """좁은 창에서 필드/버튼을 줄바꿈한다. 숨겨진 복제 화면을 만들지 않는다."""

    def __init__(self, widgets, minimum_width, *, fields=False):
        super().__init__()
        self.widgets = widgets
        self.minimum_width = minimum_width
        self.fields = fields
        self.columns = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._arrange(len(widgets))

    def minimumSizeHint(self):
        # 현재 한 줄의 너비를 부모 최소 너비로 강제하면, 좁아져야 발생하는 줄바꿈이 막힌다.
        return QSize(0, self.grid.minimumSize().height())

    def _arrange(self, columns):
        if self.columns == columns:
            return
        for index in range(max(self.columns, columns) + 1):
            self.grid.setColumnStretch(index, 0)
        for widget in self.widgets:
            self.grid.removeWidget(widget)
        for index, widget in enumerate(self.widgets):
            self.grid.addWidget(widget, index // columns, index % columns)
        if self.fields:
            # 고정 너비 항목(기준 날짜·버튼 묶음)은 늘리지 않고 콤보 칸만 남는 폭을 나눠 쓴다.
            for index in range(columns):
                fixed = all(w.maximumWidth() < 10000 for w in self.widgets[index::columns])
                self.grid.setColumnStretch(index, 0 if fixed else 1)
        else:
            self.grid.setColumnStretch(columns, 1)
        self.columns = columns
        self.updateGeometry()

    def resizeEvent(self, event):
        columns = max(1, min(len(self.widgets), (event.size().width() + 12) // (self.minimum_width + 12)))
        if self.fields:
            needed = sum(max(w.minimumWidth(), self.minimum_width) if w.maximumWidth() >= 10000 else w.maximumWidth()
                         for w in self.widgets) + 12 * (len(self.widgets) - 1)
            columns = len(self.widgets) if event.size().width() >= needed else 2 if columns >= 2 else 1
        self._arrange(columns)
        super().resizeEvent(event)


class QueryPanel(QWidget):
    applied = Signal(object)  # AppliedQuery: 공통 조건 계약, 데이터 조회 완료 신호가 아님
    draft_changed = Signal(object)  # QuerySelection
    catalog_changed = Signal(object)  # QueryCatalog
    targets_changed = Signal(object)  # 목표 초안 변경. 조회 적용과 별개

    def __init__(self, state: QueryState):
        super().__init__()
        self.state = state
        self.target_describer = None
        self.combos: dict[str, QComboBox] = {}
        self.quick_buttons = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        self.card = Card()
        self.card.setProperty("outlined", True)
        self.card.body.setContentsMargins(20, 16, 20, 16)
        self.card.body.setSpacing(12)
        outer.addWidget(self.card)
        # VER5 조회 패널에는 공정·개인 설정 줄이 없다. 개인 설정 연결 전까지 숨겨 둔다.
        self.heading = QWidget()
        self.heading.hide()
        heading = QHBoxLayout(self.heading)
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(12)
        process_label = label("Etch", 14, medium=True)
        process_label.setFixedWidth(110)
        heading.addWidget(process_label)
        self.preferences_button = button("라인 · 설비군 설정")
        self.preferences_button.setEnabled(False)
        self.preferences_button.setFixedSize(200, 36)
        self.preferences_button.setToolTip("개인별 기본 선택지 설정은 별도 작업입니다.")
        heading.addWidget(self.preferences_button)
        heading.addStretch()
        self.card.body.addWidget(self.heading)
        # 선택 중 요약은 상태 계산·접근성용으로 유지하고 VER5 화면에서는 적용 줄만 보인다.
        self.draft_summary = label("", 13, color=SECONDARY, wrap=True)
        self.draft_summary.hide()
        self.card.body.addWidget(self.draft_summary)
        self.source_label = label("", 12, color=SECONDARY, wrap=True)
        fields = [self._date_field()]
        for key, caption in (("site_key", "Site"), ("fab_key", "Fab"),
                             ("line_key", "Line"), ("group_key", "설비군")):
            field = QWidget()
            layout = QVBoxLayout(field)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            title = label(caption, medium=True, color=INK)
            combo = ComboBox()
            combo.setObjectName("query_" + key)
            combo.setProperty("queryField", True)
            combo.setAccessibleName(caption)
            combo.setFixedHeight(38)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            title.setBuddy(combo)
            combo.currentIndexChanged.connect(lambda index, name=key: self._choose(name, index))
            self.combos[key] = combo
            layout.addWidget(title)
            layout.addWidget(combo)
            fields.append(field)
        self.apply_button = button("조회 적용", primary=True)
        self.apply_button.setFixedSize(90, 38)
        self.apply_button.setToolTip("선택한 조건을 확정합니다.")
        # 조건 초기화는 아이콘 버튼. Site~설비군은 전체로, 기준 날짜는 오늘로 되돌린다.
        self.reset_button = button("", asset="ver5/reset.svg")
        self.reset_button.setAccessibleName("조건 초기화")
        self.reset_button.setProperty("soft", True)
        self.reset_button.setFixedSize(38, 38)
        self.reset_button.setToolTip("조건 초기화")
        actions = QWidget()
        actions_body = QHBoxLayout(actions)
        actions_body.setContentsMargins(0, 0, 0, 0)
        actions_body.setSpacing(12)
        actions_body.addWidget(self.reset_button, 0, Qt.AlignmentFlag.AlignBottom)
        actions_body.addWidget(self.apply_button, 0, Qt.AlignmentFlag.AlignBottom)
        actions.setFixedWidth(38 + 12 + 90)
        fields.append(actions)
        self.fields_row = _WrapRow(fields, 150, fields=True)
        self.card.body.addWidget(self.fields_row)
        self.quick_rows = []
        self._rebuild_quick_rows()
        self.goal_button = button("버전(N) 기준 입력")
        self.goal_button.setFixedSize(170, 38)
        self.goal_button.setEnabled(False)
        self.goal_button.setToolTip("목표 N 편집은 별도 작업입니다.")
        self.version_button = button("조회 버전 · 전체")
        self.version_button.setMinimumSize(190, 38)
        self.version_button.setEnabled(False)
        self.version_button.setToolTip("버전 다중 선택 조회 · 목표 N과 별개 · 선택 후 조회 적용")
        self.actions_row = _WrapRow([self.goal_button, self.version_button], 190)
        self.card.body.addWidget(self.actions_row)
        self.version_pending = label("버전 선택 변경 · 조회 적용 필요", 12, color=SECONDARY)
        self.card.body.addWidget(self.version_pending)
        self.hint = label("선택 변경·초기화는 자동 적용되지 않습니다. ‘조회 적용’을 눌러 확정하세요.",
                          13, color=SECONDARY, wrap=True)
        self.card.body.addWidget(self.hint)
        self.card.body.addWidget(self.source_label)
        applied = QHBoxLayout()
        applied.setContentsMargins(0, 0, 0, 0)
        applied.setSpacing(12)
        self.status = StatusBadge("미적용", "info")
        applied.addWidget(self.status, 0, Qt.AlignmentFlag.AlignTop)
        self.applied_summary = label("", 13, color=SECONDARY, wrap=True)
        self.applied_summary.setAccessibleName("실제 적용 조건")
        self.applied_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        applied.addWidget(self.applied_summary, 1)
        outer.addLayout(applied)
        # 화면 단순화: 안내 문구·선택지 출처·적용 상태 줄은 표시하지 않는다(값은 내부·접근성용으로 유지).
        for hidden in (self.hint, self.source_label, self.status, self.applied_summary):
            hidden.hide()
        self.apply_button.clicked.connect(self.apply_draft)
        self.reset_button.clicked.connect(self.reset_draft)
        self._render()

    def _rebuild_quick_rows(self):
        for row in self.quick_rows:
            self.card.body.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.quick_rows.clear()
        self.quick_buttons.clear()
        catalog = self.state.catalog
        groups = [
            ([("site_key", None, "Site 전체")] + [("site_key", o.key, o.label) for o in catalog.sites], 145),
            ([("fab_key", None, "Fab 전체"), ("fab_key", UNASSIGNED, "Fab 미지정"),
              ("line_key", None, "Line 전체"), ("line_key", UNASSIGNED, "Line 미지정")], 145),
            ([("group_key", None, "설비군 전체")] + [("group_key", o.key, o.label)
                                                    for o in catalog.groups if o.category != "KCTC"], 135),
            ([("group_key", o.key, o.label) for o in catalog.groups if o.category == "KCTC"], 160),
        ]
        for entries, width in groups:
            if not entries:
                continue
            widgets = []
            for field, value, caption in entries:
                item = button(caption)
                item.setProperty("queryChoice", True)
                item.setProperty("compactChoice", width == 160)
                item.setCheckable(True)
                item.setFixedSize(width, 36 if width == 160 else 40)
                dimension = dict(site_key="Site", fab_key="Fab", line_key="Line", group_key="설비군")[field]
                item.setAccessibleName((caption if value is None or value is UNASSIGNED else f"{dimension} {caption}") + " 선택")
                item.clicked.connect(lambda checked=False, name=field, key=value: self.select(name, key))
                self.quick_buttons[field, value] = item
                widgets.append(item)
            row = _WrapRow(widgets, width)
            row.setVisible(False)  # 빠른 선택 줄은 VER5 화면에서 쓰지 않는다(콤보로 선택).
            self.quick_rows.append(row)
            self.card.body.insertWidget(self.card.body.indexOf(self.fields_row) + len(self.quick_rows), row)

    def _date_field(self):
        """기준일 선택. 비워 두면(오늘) 최신 관측, 과거 날짜면 그날 이전 최신 관측으로 계산한다."""
        field = QWidget()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title = label("기준 날짜", medium=True, color=INK)
        self.date_edit = DateEdit()
        self.date_edit.setObjectName("query_as_of")
        self.date_edit.setProperty("queryField", True)
        self.date_edit.setAccessibleName("기준 날짜")
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.date_edit.setFixedHeight(38)
        self.date_edit.setToolTip("선택한 날짜까지 저장된 관측 중 가장 최근 자료로 계산합니다.")
        self.date_edit.dateChanged.connect(self._choose_date)
        title.setBuddy(self.date_edit)
        layout.addWidget(title)
        layout.addWidget(self.date_edit)
        field.setFixedWidth(160)
        return field

    def _choose_date(self, value):
        self.set_as_of(value.toPython())

    def set_today(self):
        self.set_as_of(None)

    def set_as_of(self, value):
        previous = self.state.draft
        self.state.set_as_of(value)
        self._render()
        if previous != self.state.draft:
            self.draft_changed.emit(self.state.draft)

    def _choose(self, field, index):
        if index >= 0:
            self.select(field, self.combos[field].itemData(index))

    def select(self, field, value):
        previous = self.state.draft
        self.state.select(field, value)
        self._render()
        if previous != self.state.draft:
            self.draft_changed.emit(self.state.draft)

    def set_draft(self, selection: QuerySelection):
        previous = self.state.draft
        self.state.set_draft(selection)
        self._render()
        if previous != selection:
            self.draft_changed.emit(selection)

    def reset_draft(self):
        self.set_draft(QuerySelection())

    def set_version_filter(self, value):
        self.set_draft(replace(self.state.draft, versions=value))

    def set_target_describer(self, describer):
        self.target_describer = describer
        self._render()

    def set_target_draft(self, targets):
        previous = self.state.target_draft
        self.state.set_target_draft(targets)
        self._render()
        if previous != self.state.target_draft:
            self.targets_changed.emit(self.state.target_draft)

    def apply_draft(self):
        query = self.state.apply()
        self._render()
        self.applied.emit(query)
        return query

    def set_catalog(self, catalog: QueryCatalog):
        previous = self.state.draft
        self.state.replace_catalog(catalog)
        self._rebuild_quick_rows()
        self._render()
        self.catalog_changed.emit(catalog)
        if previous != self.state.draft:
            self.draft_changed.emit(self.state.draft)

    def _render(self):
        self.version_button.setText(version_filter_caption(self.state.draft.versions))
        self.version_button.setAccessibleName("버전 다중 선택 조회 · " + self.version_button.text())
        applied_versions = self.state.applied.selection.versions if self.state.applied is not None else None
        self.version_pending.setVisible(self.state.draft.versions != applied_versions)
        for field, options, caption in (
            ("site_key", self.state.catalog.sites, "Site"),
            ("fab_key", self.state.available_fabs(), "Fab"),
            ("line_key", self.state.available_lines(), "Line"),
            ("group_key", self.state.catalog.groups, "설비군"),
        ):
            combo = self.combos[field]
            previous_block = combo.blockSignals(True)
            combo.clear()
            combo.addItem("전체", None)
            if field in ("fab_key", "line_key"):
                combo.addItem("미지정", UNASSIGNED)
            for option in options:
                combo.addItem(option.label, option.key)
            value = getattr(self.state.draft, field)
            combo.setCurrentIndex(next(i for i in range(combo.count()) if combo.itemData(i) == value))
            combo.blockSignals(previous_block)
        today = date.today()
        previous_block = self.date_edit.blockSignals(True)
        self.date_edit.setMaximumDate(QDate(today.year, today.month, today.day))
        shown = self.state.draft.as_of or today
        self.date_edit.setDate(QDate(shown.year, shown.month, shown.day))
        self.date_edit.blockSignals(previous_block)
        for (field, value), item in self.quick_buttons.items():
            item.setChecked(getattr(self.state.draft, field) == value)
        self.draft_summary.setText("선택 중 · " + self.state.describe(self.state.draft))
        if self.target_describer is not None:
            self.draft_summary.setText(self.draft_summary.text() + "\n선택 중 목표 N · " + self.target_describer(self.state.target_draft))
        self.source_label.setText(self.state.catalog.source_label)
        applied = self.state.applied
        if applied is None:
            status, tone = "미적용", "info"
            summary = "아직 적용된 조건이 없습니다. 선택 후 조회 적용을 눌러 주세요."
        else:
            if not self.state.applied_is_current:
                status, tone = "선택지 변경 · 재적용 필요", "warning"
            elif self.state.has_pending_changes:
                status, tone = "선택 변경 · 재적용 필요", "warning"
                if self.state.target_draft != applied.targets:
                    status = "선택·목표 변경 · 재적용 필요" if self.state.draft != applied.selection else "목표 변경 · 재적용 필요"
            else:
                status, tone = "조건 적용됨", "info"
            summary = f"적용 #{applied.revision} · {applied.description}"
            if self.target_describer is not None:
                summary += "\n적용 목표 N · " + self.target_describer(applied.targets)
        self.status.setText(status)
        self.status.set_tone(tone)
        self.applied_summary.setText(summary)
